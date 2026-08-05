"""
中华人民共和国水利部 — 政策解读 采集 (custom_runner)
======================================================

Target: http://www.mwr.gov.cn/zw/zcjd/

Site characteristics
────────────────────
Static HTML (TRS CMS). No SPA, no encryption, no OAuth.
Listing = single page (25 items) across 5 `<ul class="slnewsconlist">` blocks.

List item structure:
    <ul class="slnewsconlist">
      <li>
        <span>YYYY-MM-DD</span>
        <a href="...">标题</a>
      </li>

Two detail-page URL flavours (mixed on the same listing):
  ① relative  ./YYYYMM/tYYYYMMDD_NNNNNNN.html
     → http://www.mwr.gov.cn/zw/zcjd/YYYYMM/t...html
     Real policy interpretation docs (TRS CMS templates).
  ② absolute  http://www.mwr.gov.cn/hd/zxft/.../index.html
     Press-conference mini-sites aggregated onto the 政策解读 column.

Detail page metadata uses TRS CMS `<meta>` tags:
    ArticleTitle / PubDate / ContentSource / ColumnName / ArticleAuthor

Body container fallback chain:
    div.TRS_UEDITOR.trs_paper_default.trs_word   (older template)
    → div.xlcontainer                            (newer template)
    → div.newsfbh1art                            (press-conference)
    → div.newscontain (text fallback)

Attachments:
    div.fujian block (TRS standard) + whole-page anchors ending in
    pdf/doc/docx/xls/xlsx/zip/rar/wps/ofd. ZIPs auto-extracted by
    AttachmentHandler (no manual extraction in this script).

Anti-crawler level: 🟢 L1 (open HTTP, UA + Referer sufficient).

Call entry: unified_crawler.py dispatches via custom_runner → run().
"""

import gzip
import logging
import os
import random
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict  # noqa: E402

logger = logging.getLogger("mwr_zcjd_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "mwr_zcjd"
SITE_DISPLAY = "国家水利部-政策解读 mwr.gov.cn"
CATEGORY = "policy"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"

LISTING_URL = "http://www.mwr.gov.cn/zw/zcjd/"
SITE_ROOT = "http://www.mwr.gov.cn"
ISSUING_AUTHORITY = "中华人民共和国水利部"
TOPIC_CATEGORY = "政策解读"
SECTION_NAME = "国家水利部-政策解读"  # 前端"类型"列显示字段
PARSER_ID = "general"

_TAG = "[MWR-ZCJD]"

_REQUEST_DELAY_MIN = 0.8
_REQUEST_DELAY_MAX = 2.0
_MAX_RETRIES = 3
_HTTP_TIMEOUT = 30
_DETAIL_TIMEOUT = 45

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": LISTING_URL,
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 中文文号正则：水利部规章 / 联合发文常见格式
# 例：水资管〔2026〕46号、水政法〔2025〕17号、国汛办〔2024〕3号
_DOC_NUMBER_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,12}[〔\[（(]\d{4}[〕\]）)]\s*\d+\s*号)"
)

# 附件文件后缀（AttachmentHandler 白名单内）
_ATT_EXT_RE = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|wps|et|ofd|ppt|pptx)(?:$|\?)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg: str) -> None:
    print(msg, flush=True)


def _request_delay() -> None:
    time_sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def time_sleep(seconds: float) -> None:
    import time as _t
    _t.sleep(seconds)


def _normalize_date(date_str: str) -> str:
    """Normalize 'YYYY-MM-DD' / 'YYYY/MM/DD' / 'YYYY.MM.DD' / 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DD'."""
    if not date_str:
        return ""
    s = date_str.strip()
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        try:
            return "{:04d}-{:02d}-{:02d}".format(int(y), int(mo), int(d))
        except ValueError:
            return s
    return s


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = _HTTP_TIMEOUT) -> Tuple[Optional[bytes], Optional[str]]:
    """HTTP GET with retries + gzip handling. Returns (raw_bytes, final_url) or (None, None)."""
    hdrs = dict(headers or _HEADERS)
    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs, method="GET")
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                final_url = resp.geturl() or url
                return data, final_url
        except Exception as e:
            last_exc = e
            logger.warning("HTTP GET %s failed (attempt %d/%d): %s",
                           url, attempt, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES:
                time_sleep(1 + attempt)  # 2s, 3s backoff
    _safe_print("{} WARNING: GET failed after {} attempts: {}".format(
        _TAG, _MAX_RETRIES, last_exc))
    return None, None


def _decode_html(data: bytes) -> str:
    """Decode bytes → HTML string. utf-8 first, GBK fallback."""
    if data is None:
        return ""
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_real_href(href: str) -> bool:
    """Filter out javascript:/mailto:/anchor hrefs."""
    if not href:
        return False
    h = href.strip().lower()
    if h.startswith(("javascript:", "mailto:", "tel:", "#")):
        return False
    return True


def _strip_url(url: str) -> str:
    """Normalize URL for dedup: strip fragment, strip whitespace."""
    if not url:
        return ""
    u = url.strip()
    # strip fragment
    if "#" in u:
        u = u.split("#", 1)[0]
    return u


# ---------------------------------------------------------------------------
# Listing parse
# ---------------------------------------------------------------------------

def _parse_listing(html_str: str) -> List[Dict[str, str]]:
    """Parse listing → [{title, url(absolute), date(YYYY-MM-DD)}, ...].

    Skips javascript:/mailto: anchors. De-duplicates by absolute URL.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _safe_print("{} ERROR: bs4 not available".format(_TAG))
        return []

    soup = BeautifulSoup(html_str, "html.parser")
    items: List[Dict[str, str]] = []
    seen: set = set()

    for ul in soup.select("ul.slnewsconlist"):
        for li in ul.find_all("li", recursive=False):
            a = li.find("a")
            span = li.find("span")
            if not a:
                continue
            href = (a.get("href") or "").strip()
            if not _is_real_href(href):
                continue
            abs_url = _strip_url(urllib.parse.urljoin(LISTING_URL, href))
            if not abs_url or abs_url in seen:
                continue
            title = (a.get_text(strip=True) or "").strip()
            if not title:
                continue
            date_raw = (span.get_text(strip=True) if span else "") or ""
            date = _normalize_date(date_raw)
            seen.add(abs_url)
            items.append({
                "title": title,
                "url": abs_url,
                "date": date,
                "list_date": date,
            })

    return items


def _filter_by_date(items: List[Dict[str, str]],
                    date_filter: str) -> List[Dict[str, str]]:
    """Filter list items by list <span> date.

    Used as a coarse first-pass filter so we don't fetch detail pages
    that obviously don't match date_filter. Detail-page PubDate will be
    the authoritative date stored.
    """
    if not date_filter:
        return items
    if date_filter == "today":
        target = datetime.now().strftime("%Y-%m-%d")
    else:
        target = _normalize_date(date_filter)
    return [it for it in items if it.get("date") == target]


# ---------------------------------------------------------------------------
# Detail page extraction
# ---------------------------------------------------------------------------

def _meta_content(html_str: str, name: str) -> str:
    """Get <meta name="..."> content attribute."""
    m = re.search(
        r'<meta\s+name=["\']' + re.escape(name) + r'["\']\s+content=["\']([^"\']*)["\']',
        html_str, re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _html_to_text(html_fragment: str) -> str:
    """Convert HTML fragment to plain text (preserve paragraph breaks)."""
    if not html_fragment:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # regex fallback
        t = re.sub(r"<(?:p|br|div|h[1-6]|li|tr)[^>]*>", "\n", html_fragment, flags=re.I)
        t = re.sub(r"<[^>]+>", "", t)
        return t.strip()
    soup = BeautifulSoup(html_fragment, "html.parser")
    # drop script/style
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # collapse blank lines
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _html_to_markdown_body(html_fragment: str) -> str:
    """Light HTML → markdown: keep paragraphs as blank-line separated text."""
    text = _html_to_text(html_fragment)
    if not text:
        return ""
    return text


def _select_body_html(html_str: str) -> str:
    """Select body content with fallback chain.

    Order:
      1. div.TRS_UEDITOR.trs_paper_default.trs_word (older)
      2. div.TRS_UEDITOR (any)
      3. div.xlcontainer (newer)
      4. div.newsfbh1art (press-conference)
      5. div.newscontain (last-ditch, includes header chrome — cleaned below)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    soup = BeautifulSoup(html_str, "html.parser")

    for selector in [
        "div.TRS_UEDITOR.trs_paper_default.trs_word",
        "div.TRS_UEDITOR",
        "div.xlcontainer",
        "div.newsfbh1art",
    ]:
        node = soup.select_one(selector)
        if node:
            # Drop nested script/style
            for t in node(["script", "style"]):
                t.decompose()
            inner = node.decode_contents()
            inner_text = _html_to_text(inner)
            if len(inner_text) >= 50:
                return inner

    # Fallback: newscontain minus the .slywxl4 chrome (font/share buttons)
    nc = soup.select_one("div.newscontain")
    if nc:
        for t in nc(["script", "style"]):
            t.decompose()
        # remove share/font-control divs
        for cls in ["slywxl4", "wx2wm", "slnewsconborder", "margin_top_20"]:
            for bad in nc.select("." + cls):
                bad.decompose()
        # remove h1 (title) so we don't duplicate
        h1 = nc.find("h1")
        if h1:
            h1.decompose()
        inner = nc.decode_contents()
        inner_text = _html_to_text(inner)
        if len(inner_text) >= 50:
            return inner

    return ""


def _extract_attachments(html_str: str, base_url: str) -> List[Dict[str, str]]:
    """Extract attachment list from detail page.

    Sources:
      1. div.fujian block anchors (TRS standard)
      2. Whole-page anchors whose href ends in supported file extension

    Returns: [{file_name, file_url, file_suffix}] (deduped by file_url)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html_str, "html.parser")
    found: List[Dict[str, str]] = []
    seen: set = set()

    def _add(text: str, href: str) -> None:
        if not _is_real_href(href):
            return
        abs_url = _strip_url(urllib.parse.urljoin(base_url, href))
        if not abs_url or abs_url in seen:
            return
        # only keep file-extension links
        if not _ATT_EXT_RE.search(abs_url):
            return
        seen.add(abs_url)
        # derive suffix
        m = _ATT_EXT_RE.search(abs_url)
        suffix = (m.group(1) if m else "").lower()
        if not suffix:
            return
        suffix = "." + suffix
        name = (text or "").strip()
        if not name:
            name = os.path.basename(urllib.parse.urlparse(abs_url).path) or "attachment"
        # ensure name has the suffix
        if not name.lower().endswith(suffix):
            name = name + suffix
        found.append({
            "file_name": name,
            "file_url": abs_url,
            "file_suffix": suffix,
        })

    # 1. div.fujian (TRS standard attachment block)
    for fj in soup.select("div.fujian"):
        for a in fj.find_all("a"):
            _add(a.get_text(strip=True), a.get("href", ""))

    # 2. whole-page file-extension anchors
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if _ATT_EXT_RE.search(href):
            _add(a.get_text(strip=True), href)

    return found


def _extract_doc_number(text: str) -> str:
    """Extract document number like '水资管〔2026〕46号' from title or body."""
    if not text:
        return ""
    m = _DOC_NUMBER_RE.search(text)
    return m.group(1).strip() if m else ""


def _extract_detail(listing_item: Dict[str, str]) -> Dict[str, Any]:
    """Fetch detail page and extract structured fields.

    Returns dict with keys:
        title, pub_date, content_source, column_name, body_html, body_text,
        doc_number, attachments, source_url (final after redirects), http_ok
    """
    detail_url = listing_item["url"]
    result: Dict[str, Any] = {
        "title": listing_item.get("title", ""),
        "pub_date": listing_item.get("date", ""),
        "content_source": "",
        "column_name": "",
        "body_html": "",
        "body_text": "",
        "doc_number": "",
        "attachments": [],
        "source_url": detail_url,
        "http_ok": False,
    }

    data, final_url = _http_get(detail_url, timeout=_DETAIL_TIMEOUT)
    if not data:
        return result
    html_str = _decode_html(data)
    result["http_ok"] = True
    result["source_url"] = _strip_url(final_url or detail_url)

    # meta tags (TRS CMS standard)
    meta_title = _meta_content(html_str, "ArticleTitle")
    meta_pub = _meta_content(html_str, "PubDate")
    meta_source = _meta_content(html_str, "ContentSource")
    meta_col = _meta_content(html_str, "ColumnName")

    if meta_title:
        result["title"] = meta_title
    if meta_pub:
        result["pub_date"] = _normalize_date(meta_pub)
    result["content_source"] = meta_source or "水利部网站"
    result["column_name"] = meta_col or TOPIC_CATEGORY

    # body
    body_html = _select_body_html(html_str)
    body_text = _html_to_text(body_html)
    result["body_html"] = body_html
    result["body_text"] = body_text

    # doc number — try title first, then body
    result["doc_number"] = (
        _extract_doc_number(result["title"]) or
        _extract_doc_number(body_text)
    )

    # attachments
    result["attachments"] = _extract_attachments(html_str, result["source_url"])

    return result


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(item: Dict[str, Any], detail: Dict[str, Any]) -> str:
    """Build KB-ready markdown for one item."""
    lines: List[str] = []
    lines.append("# {}".format(item.get("title", "Untitled")))
    lines.append("")

    pub = detail.get("pub_date") or item.get("date") or ""
    if pub:
        lines.append("**发布日期:** {}".format(pub))

    src = detail.get("content_source") or "水利部网站"
    lines.append("**来源:** {}".format(src))
    lines.append("**发文机构:** {}".format(ISSUING_AUTHORITY))
    lines.append("**主题分类:** {}".format(TOPIC_CATEGORY))

    doc_no = detail.get("doc_number") or ""
    if doc_no:
        lines.append("**文号:** {}".format(doc_no))

    lines.append("**原文链接:** {}".format(detail.get("source_url") or item.get("url")))
    lines.append("")

    body_md = _html_to_markdown_body(detail.get("body_html") or "")
    if body_md:
        lines.append("## 正文")
        lines.append("")
        lines.append(body_md)
    else:
        lines.append("## 正文")
        lines.append("")
        lines.append("> 该详情页为发布会专题页或正文以其他形式呈现，请访问原文链接查看完整内容。")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run(
    tenant_id: str = "",
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = CATEGORY,
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config: Any = None,
    output_dir: str = "",
) -> dict:
    """Custom runner entry point called by unified_crawler.py."""
    _kb_id = kb_id or KB_ID_DEFAULT

    _safe_print("=" * 60)
    _safe_print("{} 水利部政策解读采集 (custom_runner)".format(_TAG))
    _safe_print("{} Tenant: {}  KB: {}".format(_TAG, tenant_id, _kb_id))
    _safe_print("{} Date filter: {!r}  Category: {}".format(_TAG, date_filter, category))
    _safe_print("{} Full crawl: {}  Force: {}".format(_TAG, full_crawl, force_run))
    _safe_print("=" * 60)
    sys.stdout.flush()

    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline

    writer = CollectionWriter(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        date_filter=date_filter,
    )
    pipeline = StoragePipeline(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        site_id=SITE_ID,
        site_display=SITE_DISPLAY,
        task_name=task_name,
        output_dir=output_dir,
        writer_mode="collection",
        category=category,
        task_id=task_id,
        date_filter=date_filter,
    )

    total_items = 0
    total_new = 0
    total_updated = 0
    total_kb = 0
    total_att = 0
    errors: List[str] = []

    # ── Step 1: Fetch listing ─────────────────────────────────────────
    _safe_print("{} Fetching listing: {}".format(_TAG, LISTING_URL))
    sys.stdout.flush()
    list_data, _ = _http_get(LISTING_URL)
    if not list_data:
        msg = "Failed to fetch listing page"
        _safe_print("{} ERROR: {}".format(_TAG, msg))
        errors.append(msg)
        return {
            "status": "fail",
            "pages": 0, "items_found": 0, "items_new": 0, "items_updated": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": errors,
        }

    list_html = _decode_html(list_data)
    all_items = _parse_listing(list_html)
    _safe_print("{} Parsed {} list items".format(_TAG, len(all_items)))
    sys.stdout.flush()

    if not all_items:
        return {
            "status": "success",
            "pages": 1, "items_found": 0, "items_new": 0, "items_updated": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": [],
        }

    # ── Step 2: Coarse date filter on listing <span> ─────────────────
    filtered = _filter_by_date(all_items, date_filter)
    _safe_print("{} After date_filter {!r}: {} items (from {} total)".format(
        _TAG, date_filter or "none", len(filtered), len(all_items)))
    sys.stdout.flush()

    if not filtered and date_filter:
        _safe_print("{} No items matched date filter — done.".format(_TAG))
        return {
            "status": "success",
            "pages": 1,
            "items_found": len(all_items),
            "items_new": 0, "items_updated": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": [],
        }

    # ── Step 3: For each item, fetch detail + ingest ─────────────────
    for i, listing_item in enumerate(filtered, 1):
        title = listing_item["title"]
        url = listing_item["url"]
        _safe_print("{} [{}/{}] {}".format(_TAG, i, len(filtered), title[:70]))
        sys.stdout.flush()

        # Fetch detail page
        try:
            detail = _extract_detail(listing_item)
        except Exception as e:
            msg = "Detail fetch failed for {}: {}".format(title[:50], e)
            _safe_print("{}   WARNING: {}".format(_TAG, msg))
            logger.warning(msg)
            errors.append(msg)
            detail = {
                "title": title, "pub_date": listing_item.get("date", ""),
                "content_source": "水利部网站", "column_name": TOPIC_CATEGORY,
                "body_html": "", "body_text": "", "doc_number": "",
                "attachments": [], "source_url": url, "http_ok": False,
            }

        if not detail["http_ok"]:
            errors.append("Detail page unavailable: {}".format(url))

        # Build item dict for writer
        pub_date = detail.get("pub_date") or listing_item.get("date") or ""
        item_dict: Dict[str, Any] = {
            "title": detail.get("title") or title,
            "url": detail.get("source_url") or url,
            "date": pub_date,
            "publishDate": pub_date,
            "content": _build_markdown(
                {**listing_item, "title": detail.get("title") or title},
                detail,
            ),
            "doc_number": detail.get("doc_number") or "",
            "issuing_authority": ISSUING_AUTHORITY,
            "fawenjiguan": ISSUING_AUTHORITY,
            "topic_category": TOPIC_CATEGORY,
            "category": TOPIC_CATEGORY,
            "section_name": SECTION_NAME,
            "column_name": detail.get("column_name") or TOPIC_CATEGORY,
            "content_source": detail.get("content_source") or "水利部网站",
            "site_id": SITE_ID,
            "_category": category,
            "attachments": detail.get("attachments") or [],
        }

        # Write to crawler_result + collection_policy_ext
        try:
            result_id = writer.write_all(
                item=item_dict,
                site_id=SITE_ID,
                category=category,
                task_id=task_id,
                site_display=SITE_DISPLAY,
            )
        except Exception as e:
            msg = "CollectionWriter failed for {}: {}".format(title[:50], e)
            _safe_print("{}   ERROR: {}".format(_TAG, msg))
            logger.error(msg)
            errors.append(msg)
            continue

        if not result_id:
            # Filtered by writer (date/dedup) — skip KB upload too
            _request_delay()
            continue

        total_items += 1
        # Heuristic: distinguish "new" vs "updated" by checking if kb_doc_id was set
        # The writer does not return this flag, so we approximate:
        # the pipeline will upload KB; whether it is new vs updated is determined at KB level.
        # We count every successful result_id as one "processed"; for stats purposes,
        # treat the first run as "new" (we can't reliably tell without an extra query).
        total_new += 1

        # ── Upload content + attachments to KB ───────────────────────
        try:
            normalized = item_from_dict(item_dict, site_id=SITE_ID)
            store_result = pipeline.store(normalized)
            if store_result.get("doc_id"):
                total_kb += 1
            # attachments are processed inside pipeline.store via AttachmentHandler
            att_results = store_result.get("attachment_results") or []
            for ar in att_results:
                if isinstance(ar, dict) and (ar.get("kb_doc_id") or ar.get("doc_id")):
                    total_att += 1
                elif isinstance(ar, dict) and ar.get("status") == "ok":
                    total_att += 1
        except Exception as e:
            msg = "Pipeline store failed for {}: {}".format(title[:50], e)
            _safe_print("{}   WARNING: {}".format(_TAG, msg))
            logger.warning(msg)
            errors.append(msg)

        _request_delay()

    # ── Step 4: Summary ──────────────────────────────────────────────
    _safe_print(
        "{} Done: {} items processed, {} new, {} KB uploads, {} attachments".format(
            _TAG, total_items, total_new, total_kb, total_att
        )
    )
    sys.stdout.flush()

    return {
        "status": "success" if total_items > 0 or not errors else (
            "success" if not [e for e in errors if "listing" in e] else "fail"
        ),
        "pages": 1,
        "items_found": len(all_items),
        "items_new": total_new,
        "items_updated": total_updated,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors[:20],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    summary = run(
        tenant_id="",
        kb_id=KB_ID_DEFAULT,
        task_name="local_test_mwr_zcjd",
        task_id="",
        date_filter="",  # full backfill
    )
    print("Summary:", summary)
