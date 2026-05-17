#!/usr/bin/env python3
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
Dedicated web crawler for www.ccgp.gov.cn (中国政府采购网).

Crawls multiple content sections (news, procurement notices, policies, etc.)
with pagination, extracts article content from detail pages, converts to
Markdown, and uploads the result to a RAGFlow knowledge base.

Article URL pattern:
  /<section>/<YYYYMM>/t<YYYYMMDD>_<id>.htm

Pagination pattern:
  /<section>/index.htm     (page 1)
  /<section>/index_2.htm   (page 2)
  /<section>/index_3.htm   (page 3)

Usage (typically spawned by task_executor):
    python ccgp_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url http://www.ccgp.gov.cn/index.shtml \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests  # noqa: F401 — kept for reference; PlaywrightHttpClient is used instead
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# Known sections on ccgp.gov.cn (path → display label)
# ---------------------------------------------------------------------------
# Each entry is a directory under http://www.ccgp.gov.cn/ that contains
# listing pages (index.htm, index_2.htm, ...) with <ul class="c_list">.
SECTIONS = {
    "news": "\u65b0\u95fb",
    "zcdt": "\u653f\u5e9c\u91c7\u8d2d\u52a8\u6001",
    "zcfg": "\u653f\u91c7\u6cd5\u89c4",
    "gpsr": "\u8d2d\u4e70\u670d\u52a1",
    "jdjc": "\u76d1\u7763\u68c0\u67e5",
    "xxgg": "\u4fe1\u606f\u516c\u544a",
    "wtogpa": "\u56fd\u9645\u4e13\u680f",
    "cggg/zygg/gkzb": "\u4e2d\u592e\u91c7\u8d2d-\u516c\u5f00\u62db\u6807",
    "cggg/zygg/zbgg": "\u4e2d\u592e\u91c7\u8d2d-\u4e2d\u6807\u516c\u544a",
    "cggg/zygg/gzgg": "\u4e2d\u592e\u91c7\u8d2d-\u66f4\u6b63\u516c\u544a",
    "cggg/zygg/xjgg": "\u4e2d\u592e\u91c7\u8d2d-\u8be2\u4ef7\u516c\u544a",
    "cggg/zygg/cjgg": "\u4e2d\u592e\u91c7\u8d2d-\u6210\u4ea4\u516c\u544a",
    "cggg/zygg/jzxcs": "\u4e2d\u592e\u91c7\u8d2d-\u7ade\u4e89\u6027\u78cb\u5546",
    "cggg/zygg/yqzbgg": "\u4e2d\u592e\u91c7\u8d2d-\u9080\u8bf7\u62db\u6807",
    "cggg/zygg/jzxtpgg": "\u4e2d\u592e\u91c7\u8d2d-\u7ade\u4e89\u6027\u8c08\u5224",
    "cggg/dfgg/gkzb": "\u5730\u65b9\u91c7\u8d2d-\u516c\u5f00\u62db\u6807",
    "cggg/dfgg/zbgg": "\u5730\u65b9\u91c7\u8d2d-\u4e2d\u6807\u516c\u544a",
    "cggg/dfgg/gzgg": "\u5730\u65b9\u91c7\u8d2d-\u66f4\u6b63\u516c\u544a",
    "cggg/dfgg/xjgg": "\u5730\u65b9\u91c7\u8d2d-\u8be2\u4ef7\u516c\u544a",
    "cggg/dfgg/cjgg": "\u5730\u65b9\u91c7\u8d2d-\u6210\u4ea4\u516c\u544a",
    "cggg/dfgg/jzxcs": "\u5730\u65b9\u91c7\u8d2d-\u7ade\u4e89\u6027\u78cb\u5546",
    "cggg/dfgg/yqzbgg": "\u5730\u65b9\u91c7\u8d2d-\u9080\u8bf7\u62db\u6807",
    "cggg/dfgg/jzxtpgg": "\u5730\u65b9\u91c7\u8d2d-\u7ade\u4e89\u6027\u8c08\u5224",
}


def parse_args():
    parser = argparse.ArgumentParser(description="CCGP crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL to crawl")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--max-pages", type=int, default=5, help="Max listing pages per section (default: 5)")
    parser.add_argument("--section", default=None, help="Comma-separated list of sections to crawl (default: all)")
    # Kept for backward compat — unused
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _init():
    settings.init_settings()
    logging.info("Project settings initialised")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch(url, client=None, timeout=30):
    try:
        if client is not None:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
        else:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        if enc.upper() in ("EUC-JP", "EUC-KR", "SHIFT_JIS", "ISO-8859-1"):
            enc = resp.apparent_encoding or "utf-8"
        if not enc or enc.upper() in ("ASCII", "ISO-8859-1"):
            enc = "utf-8"
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return raw.decode("gbk", errors="replace")
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _abs_url(href, base):
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return urljoin(base, href)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y\u5e74%m\u6708%d\u65e5",
]


def _parse_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _extract_date_from_url(url):
    """Extract date from ccgp article URL pattern tYYYYMMDD_xxxxx.htm."""
    m = re.search(r"/t(\d{4})(\d{2})(\d{2})_\d+\.htm", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Section listing page discovery
# ---------------------------------------------------------------------------

def _get_section_listing_urls(base_url, section_path, max_pages):
    """Generate listing URLs for a given section.

    Page 1: http://www.ccgp.gov.cn/<section>/index.htm
    Page 2: http://www.ccgp.gov.cn/<section>/index_2.htm
    ...
    """
    p = urlparse(base_url)
    site_root = f"{p.scheme}://{p.netloc}"
    # Strip leading/trailing slashes
    section_path = section_path.strip("/")
    urls = []
    for i in range(1, max_pages + 1):
        if i == 1:
            page_path = f"/{section_path}/index.htm"
        else:
            page_path = f"/{section_path}/index_{i}.htm"
        urls.append(f"{site_root}{page_path}")
    return urls


# ---------------------------------------------------------------------------
# Article extraction from listing page
# ---------------------------------------------------------------------------

def _extract_articles_from_listing(html, section_label, listing_url=None):
    """Extract article entries from a ccgp.gov.cn listing page.

    Looks for <ul class="c_list"> containing <li><a href="..." title="...">.

    Args:
        html: Listing page HTML.
        section_label: Display name for the section.
        listing_url: The URL of the listing page (used as base for resolving
            relative article URLs).  If None, falls back to the site root.

    Returns list[dict] with keys: title, url, date (datetime or None).
    Articles published before 2023 are filtered out.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    base_for_url = listing_url or "http://www.ccgp.gov.cn"

    # Find all <ul class="c_list"> — each contains article links
    for ul in soup.find_all("ul", class_=re.compile(r"c_list")):
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            title = (a.get("title") or a.text or "").strip()
            if not title or len(title) < 2:
                continue
            url = _abs_url(href, base_for_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract date: try URL pattern first, then meta, then text
            dt = _extract_date_from_url(url)

            # Also check for date text in parent elements
            if not dt:
                all_text = li.get_text()
                m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", all_text)
                if m:
                    dt = _parse_date(m.group(1))

            if dt and dt.year < 2023:
                logging.debug("Skipped %s (date: %s)", title[:60], dt.date())
                continue

            articles.append({
                "title": title,
                "url": url,
                "date": dt,
                "section": section_label,
            })

    return articles


# ---------------------------------------------------------------------------
# Article content from detail page
# ---------------------------------------------------------------------------

def _fetch_article_content(url, client=None):
    """Fetch and parse a ccgp.gov.cn article detail page.

    Two layout types are handled:
    1. News/articles:  <div class="vF_detail_content"><div class="TRS_Editor">
    2. Notices:        <div class='table'><table>...</table></div> + content

    Images are OCR'd using RAGFlow's built-in local engine.

    Returns (markdown_text, list_of_resource_dicts, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url, client=client)
    if not html:
        return "", [], {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract metadata from <meta> tags --
    meta_title = ""
    meta_date = ""
    meta_source = ""
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").strip()
        content = (meta.get("content") or "").strip()
        if name == "ArticleTitle":
            meta_title = content
        elif name == "PubDate":
            meta_date = content
        elif name == "ContentSource":
            meta_source = content

    # Fallback: <h2 class="tc"> for title
    if not meta_title:
        h2 = soup.find("h2", class_="tc")
        if h2:
            meta_title = h2.get_text(strip=True)

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # -- Collect resources (images, files) --
    resources = []
    seen_srcs = set()
    _FILE_EXT = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv)", re.I)

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        src = _abs_url(src, url)
        if not src.startswith(("http://", "https://")):
            continue
        if src in seen_srcs:
            continue
        seen_srcs.add(src)
        parent = img.find_parent(["figure", "a"])
        caption = ""
        if parent:
            cap_tag = parent.find(["figcaption", "span", "em"])
            if cap_tag:
                caption = cap_tag.get_text(strip=True)
        resources.append({"type": "image", "src": src, "caption": caption})

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not _FILE_EXT.search(href):
            continue
        src = _abs_url(href, url)
        if src in seen_srcs:
            continue
        seen_srcs.add(src)
        caption = a.get_text(strip=True) or os.path.basename(href)
        resources.append({"type": "file", "src": src, "caption": caption})

    # -- Extract main content area --
    # Procurement notices have structured data in <div class='table'>
    content_parts = []

    # 1. Structured notice table (<div class='table'>)
    notice_table = soup.find("div", class_="table")
    if notice_table:
        table = notice_table.find("table")
        if table:
            md_table_content = _notice_table_to_markdown(table)
            if md_table_content:
                content_parts.append(md_table_content)

    # 2. Free-text content
    detail_content = soup.find("div", class_="vF_detail_content")
    if not detail_content:
        detail_content = soup.find("div", class_="TRS_Editor")

    if detail_content:
        md_text = _content_to_markdown(detail_content, url)
        if md_text:
            content_parts.append(md_text)

    content = "\n\n".join(content_parts)

    # -- Enrich content with image references + OCR text --
    if resources:
        extra = []
        for r in resources:
            if r["type"] == "image":
                cap = f" *({r['caption']})*" if r.get("caption") else ""
                img_line = f"![]({r['src']}){cap}"
                extra.append(img_line)
                ocr_text = _ocr_image(r["src"], client=client)
                if ocr_text:
                    r["ocr_text"] = ocr_text
                    extra.append(f"\n> {ocr_text}\n")
            elif r["type"] == "file":
                cap = f" ({r['caption']})" if r.get("caption") else ""
                extra.append(f"[{r['caption']}]({r['src']}){cap}")
        if extra:
            content += "\n\n" + "\n\n".join(extra)

    return content, resources, metadata


def _notice_table_to_markdown(table):
    """Convert a procurement notice <table> to a readable key-value Markdown block.

    The table layout is:
      <td class='title'>Label</td><td colspan='3'>Value</td>
    with section headers like: <td colspan='4'><b>Section Title</b></td>
    """
    lines = []
    current_section = ""
    for tr in table.find_all("tr"):
        # Section header row
        th = tr.find("td", colspan="4")
        if th:
            section_text = th.get_text(strip=True)
            if section_text:
                current_section = section_text
                lines.append(f"\n**{section_text}**")
            continue

        # Key-value row
        title_td = tr.find("td", class_="title")
        if title_td:
            key = title_td.get_text(strip=True)
            # Value is in the next td(s) — collect all remaining tds
            value_tds = title_td.find_next_siblings("td")
            values = []
            for v in value_tds:
                # <p> inside <td> means multiple values
                for p in v.find_all("p"):
                    p_text = p.get_text(strip=True)
                    if p_text:
                        values.append(p_text)
                # Also grab direct text
                direct_text = v.get_text(strip=True)
                if direct_text and direct_text not in values:
                    values.append(direct_text)
            value_str = " | ".join(v for v in values if v)
            if key and value_str:
                lines.append(f"- **{key}**: {value_str}")

    return "\n".join(lines)


def _content_to_markdown(detail_soup, base_url):
    """Convert the detail content area to rough Markdown.

    Handles <p>, headings, lists, <table>, <blockquote>, <pre>, images.
    """
    lines = []
    for el in detail_soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                     "li", "blockquote", "pre", "div"]):
        # Skip container divs
        if el.name == "div" and not el.get_text(strip=True):
            continue

        text = el.get_text(strip=True)
        if not text:
            continue

        tn = el.name
        if tn == "h1":
            lines.append(f"\n# {text}\n")
        elif tn == "h2":
            lines.append(f"\n## {text}\n")
        elif tn == "h3":
            lines.append(f"\n### {text}\n")
        elif tn in ("h4", "h5", "h6"):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        elif tn == "p":
            lines.append(text)
        elif tn == "div":
            # Only include divs with substantial text not already covered
            if len(text) > 100:
                lines.append(text)

    # Tables inside the content area
    for table in detail_soup.find_all("table"):
        md_table = _table_to_markdown(table)
        if md_table:
            lines.append("")
            lines.append(md_table)
            lines.append("")

    return "\n\n".join(lines)


def _table_to_markdown(table):
    """Convert an HTML <table> to a simple Markdown table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(cell.get_text(strip=True))
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    n_cols = max(len(r) for r in rows)

    md = []
    md.append("| " + " | ".join(r.ljust(15) for r in rows[0]) + " |")
    md.append("| " + " | ".join(["---"] * n_cols) + " |")
    for row in rows[1:]:
        padded = list(row) + [""] * (n_cols - len(row))
        md.append("| " + " | ".join(r.ljust(15) for r in padded) + " |")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# Markdown persistence & incremental state
# ---------------------------------------------------------------------------

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_urls": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed URLs)", len(state.get("processed_urls", [])))


def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"Knowledge base {kb_id} not found")

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, filename, blob):
            self.id = get_uuid()
            self.filename = filename
            self.blob = blob

        def read(self):
            return self.blob

    file_obj = _FileObj(os.path.basename(filepath), blob)
    errs, doc_pairs = FileService.upload_document(kb, [file_obj], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)
    for doc, _ in doc_pairs:
        logging.info("Document %s uploaded to KB %s", doc["id"], kb_id)
        try:
            DocumentService.begin2parse(doc["id"])
            DocumentService.run(tenant_id, doc, {})
            logging.info("Parsing task queued for document %s", doc["id"])
        except Exception as e:
            logging.error("Failed to queue parsing for document %s: %s", doc["id"], e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Local OCR (built-in RAGFlow engine, no external API needed)
# ---------------------------------------------------------------------------
# The OCR engine is lazily initialised once and cached globally to avoid
# reloading the ONNX models (det.onnx / rec.onnx) on every single image.

_OCR_ENGINE = None


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from deepdoc.vision import OCR
        _OCR_ENGINE = OCR()
        logging.info("Local OCR engine initialised (models loaded)")
    return _OCR_ENGINE


def _ocr_image(image_url, client=None):
    """Download an image and run RAGFlow's built-in local OCR on it.

    Uses the same engine as the ``picture`` parser mode — works entirely
    offline, no API calls needed.  Returns extracted text (str) or None
    if the engine produced no output or an error occurred.
    """
    try:
        if client is not None:
            resp = client.get(image_url, headers=_HEADERS, timeout=30)
        else:
            resp = requests.get(image_url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        image_bytes = resp.content
    except Exception as e:
        logging.warning("Failed to download image %s: %s", image_url, e)
        return None

    try:
        import io
        import numpy as np
        from PIL import Image

        ocr_engine = _get_ocr_engine()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        bxs = ocr_engine(np.array(img))
        txt = "\n".join([t[0] for _, t in bxs if t[0]]).strip()
        if not txt:
            return None
        return txt
    except Exception as e:
        logging.warning("Local OCR failed for %s: %s", image_url, e)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    print(f"\n{'='*60}")
    print(f"[CCGP] Starting CCGP crawler")
    print(f"[CCGP] Target URL: {args.target_url}")
    print(f"[CCGP] Task name: {args.task_name}")
    print(f"[CCGP] Target KB: {args.kb_id}")
    print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== CCGP crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:
        print(f"[CCGP] Local OCR engine active — images will be OCR'd for text content")
        sys.stdout.flush()

        # Determine which sections to crawl
        if args.section:
            selected = {k: v for k, v in SECTIONS.items() if k in args.section.split(",")}
            if not selected:
                print(f"[CCGP] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        print(f"[CCGP] Sections to crawl: {len(selected)}")
        for path, label in selected.items():
            print(f"         - {label} ({path})")
        sys.stdout.flush()

        # Output directory
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        print(f"\n[CCGP] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Incremental state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        print(f"[CCGP] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # Steps:
        # 1/5: Crawl listing pages for each section
        # 2/5: Collect all article URLs (filter pre-2023)
        # 3/5: Filter against state
        # 4/5: Fetch detail pages, convert to markdown
        # 5/5: Save + upload

        # 1/5 + 2/5: Crawl listings and collect articles
        print(f"[CCGP] Step 1/5: Crawling listing pages...")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for section_path, section_label in selected.items():
            listing_urls = _get_section_listing_urls(
                args.target_url, section_path, args.max_pages
            )
            print(f"[CCGP]   Section '{section_label}': {len(listing_urls)} page(s)...")
            sys.stdout.flush()

            page_articles = []
            for page_idx, list_url in enumerate(listing_urls, 1):
                html = _fetch(list_url, client=client)
                if not html:
                    logging.warning("Failed to fetch listing page %s", list_url)
                    continue

                arts = _extract_articles_from_listing(html, section_label, list_url)
                page_articles.extend(arts)
                logging.info("Page %d of %s: %d articles", page_idx, section_label, len(arts))

                if len(arts) == 0:
                    # No more articles on this page -> stop pagination
                    break

            section_stats[section_label] = len(page_articles)
            all_articles.extend(page_articles)

        print(f"[CCGP] Step 1/5: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            print(f"[CCGP] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        # Print section stats
        print(f"[CCGP] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Filter already-processed
        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            print(f"\n[CCGP] Skipping {len(all_articles) - len(new_articles)} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            print(f"[CCGP] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # 3/5 + 4/5: Fetch detail pages
        print(f"\n[CCGP] Step 2/5: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            print(f"[CCGP] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s — %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"], client=client)
            if not content:
                print(f"[CCGP]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            n_images = sum(1 for r in resources if r.get("type") == "image")
            n_files = sum(1 for r in resources if r.get("type") == "file")
            n_ocr = sum(1 for r in resources if r.get("type") == "image" and r.get("ocr_text"))
            print(f"[CCGP]   -> {len(content)} chars, {n_images} images ({n_ocr} OCR'd), {n_files} files")
            sys.stdout.flush()

            # Build article markdown section
            article_date_str = ""
            if art.get("date"):
                article_date_str = art["date"].strftime("%Y-%m-%d")
            elif metadata.get("date"):
                article_date_str = metadata["date"]

            source_str = metadata.get("source", "")
            source_line = f"**Source:** {source_str}" if source_str else ""

            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section']}",
                f"**Date:** {article_date_str}",
                f"**URL:** {art['url']}",
            ]
            if source_line:
                lines.append(source_line)
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            md_parts.append("\n".join(lines))
            success_count += 1

        if not md_parts:
            print(f"[CCGP] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        print(f"\n[CCGP] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # 5/5: Save + upload
        print(f"[CCGP] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        print(f"[CCGP] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Save state
        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        # Upload to KB
        print(f"[CCGP] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s \u2026", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            print(f"[CCGP] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            print(f"[CCGP] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

        print(f"{'='*60}")
        print(f"[CCGP] All done! Task completed successfully.")
        print(f"[CCGP] Processed {success_count} new articles across {len(selected)} sections")
        print(f"{'='*60}")
        sys.stdout.flush()
        logging.info("=== CCGP crawler finished successfully ===")
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "ccgp_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
