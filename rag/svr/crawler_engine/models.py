"""
Normalized data models for the unified crawler framework.

These dataclasses are the transfer objects between the three layers:
  - Crawl Layer produces NormalizedItem
  - Dedup Layer filters NormalizedItem
  - Storage Layer consumes NormalizedItem

All field names are normalized from the various source formats
(JSON API keys, HTML selectors, DOM extraction) into a single schema.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AttachmentMeta:
    """Attachment metadata — used by the storage layer to download and upload."""
    file_name: str = ""
    file_url: str = ""          # absolute download URL
    file_suffix: str = ""       # .pdf, .doc, .docx, .xls, .xlsx, .zip, .rar
    file_size: int = 0          # bytes (0 = unknown)


@dataclass
class NormalizedItem:
    """Standardized crawl item — the data transfer unit between layers.

    Fields are normalized from all source formats:
    - JSON API responses (various field names)
    - HTML extraction (CSS selectors, BeautifulSoup)
    - DOM extraction (Playwright page text)
    """
    # --- Core identity ---
    item_id: str = ""            # unique ID (URL hash / API id / title+date hash)
    title: str = ""              # article title
    url: str = ""                # absolute source URL
    date: str = ""               # publish date (YYYY-MM-DD)

    # --- Content ---
    content: str = ""            # body plain text
    content_html: str = ""       # body HTML (preserves formatting)

    # --- Source tracking ---
    source_site: str = ""        # crawler site_id from config
    section: str = ""            # section label (for multi-section sites)

    # --- Structured fields (for bid_* tables) ---
    news_type: str = ""          # category / news type
    region_code: str = ""        # province/city code

    # --- Attachments ---
    attachments: List[AttachmentMeta] = field(default_factory=list)

    # --- Raw data (original fields for debugging / bid table write) ---
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def has_content(self) -> bool:
        return bool(self.content and len(self.content.strip()) > 20)

    def has_attachments(self) -> bool:
        return len(self.attachments) > 0


def item_from_dict(data: Dict[str, Any], site_id: str = "",
                   section: str = "") -> NormalizedItem:
    """Convert a raw crawled dict into a NormalizedItem.

    Handles the various field names from 77 old scripts:
    - JSON API: uuid/id, title, publishDate/publishTime/date, content/text
    - HTML extraction: title, url/href, date, content/detail/text
    """
    item = NormalizedItem()

    # --- Item ID (priority: url > id/uuid > title+date hash) ---
    url_val = (data.get("url") or data.get("href") or
               data.get("link") or data.get("sourceUrl") or "")
    id_val = (data.get("id") or data.get("uuid") or data.get("article_id") or
              data.get("infoid") or data.get("noticenumber") or
              data.get("bulletinID") or data.get("guid") or data.get("_id") or "")
    title_val = data.get("title") or data.get("name") or data.get("text") or ""
    date_val = (data.get("date") or data.get("publishDate") or
                data.get("publishTime") or data.get("pubDate") or
                data.get("pub_date") or "")

    if url_val:
        item.item_id = str(url_val)
    elif id_val:
        item.item_id = str(id_val)
    elif title_val:
        item.item_id = f"{title_val}|{date_val}"

    # --- Core fields ---
    item.title = str(title_val or "Untitled")
    item.url = str(url_val)
    item.source_site = site_id
    item.section = section

    # --- Date normalization ---
    item.date = _normalize_date(date_val)

    # --- Content ---
    content_val = (data.get("content") or data.get("detail") or
                   data.get("text") or data.get("body") or
                   data.get("description") or "")
    item.content = str(content_val) if content_val else ""

    content_html_val = (data.get("content_html") or data.get("detail_html") or
                        data.get("html") or "")
    item.content_html = str(content_html_val) if content_html_val else ""

    # --- Structured fields ---
    item.news_type = str(data.get("news_type") or data.get("newsTypeID") or
                         data.get("type") or "")
    item.region_code = str(data.get("region_code") or data.get("proviceCode") or "")

    # --- Attachments ---
    item.attachments = _extract_attachments(data)

    # --- Raw data (preserve original for bid table writes) ---
    item.raw_data = data

    return item


def _normalize_date(val: Any) -> str:
    """Normalize various date formats to YYYY-MM-DD."""
    if not val:
        return ""
    from .content_converter import parse_date  # noqa: F811
    dt = parse_date(val)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return str(val).strip()[:20]


def _extract_attachments(data: Dict[str, Any]) -> List[AttachmentMeta]:
    """Extract AttachmentMeta list from various formats in crawled data."""
    attachments = []

    # List fields: attachments, files, fileList, appendixList
    for key in ("attachments", "files", "fileList", "appendixList"):
        val = data.get(key)
        if isinstance(val, list):
            for f in val:
                if isinstance(f, dict):
                    att = _dict_to_attachment(f)
                    if att.file_url:
                        attachments.append(att)

    # Single file dict
    if not attachments:
        for key in ("file", "attachment"):
            val = data.get(key)
            if isinstance(val, dict):
                att = _dict_to_attachment(val)
                if att.file_url:
                    attachments.append(att)

    # Flat file fields
    if not attachments:
        file_url = (data.get("file_url") or data.get("fileUrl") or
                    data.get("download_url") or "")
        if file_url:
            attachments.append(AttachmentMeta(
                file_name=data.get("file_name") or data.get("fileName") or "attachment",
                file_url=file_url,
                file_suffix=data.get("file_suffix") or data.get("fileSuffix") or
                            _guess_suffix(file_url),
                file_size=int(data.get("file_size") or data.get("fileSize") or 0),
            ))

    return attachments


def _dict_to_attachment(f: Dict[str, Any]) -> AttachmentMeta:
    """Convert a file dict to AttachmentMeta."""
    file_url = (f.get("url") or f.get("fileUrl") or f.get("file_url") or
                f.get("downloadUrl") or "")
    file_name = (f.get("name") or f.get("fileName") or f.get("file_name") or
                 "attachment")
    file_suffix = (f.get("suffix") or f.get("fileSuffix") or
                   f.get("file_suffix") or _guess_suffix(file_url))
    file_size = int(f.get("size") or f.get("fileSize") or f.get("file_size") or 0)
    return AttachmentMeta(
        file_name=file_name,
        file_url=file_url,
        file_suffix=file_suffix,
        file_size=file_size,
    )


def _guess_suffix(url: str) -> str:
    """Guess file suffix from URL path."""
    if not url:
        return ""
    # Extract from URL path
    from urllib.parse import urlparse
    path = urlparse(url).path
    # Find last extension
    parts = path.rsplit(".", 1)
    if len(parts) == 2 and len(parts[1]) <= 8:
        return "." + parts[1].lower()
    return ""
