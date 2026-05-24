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
Dedicated web crawler for https://zyjy.as.xm.gov.cn/zwgk/zcfg/ (政策法规).

Crawls ALL paginated articles from the Xiamen Public Resources Trading Center
policy & regulations portal.  This is a TRS (拓尔思) CMS site with server-
rendered static HTML pages — no JavaScript rendering or API calls required.

Site characteristics
────────────────────
  • Listing  →  /zwgk/zcfg/index.htm (page 1), index_1.htm … index_89.htm
                 TRS CMS with Avalon.js pagebar (client-side pagination).
                 Each page has 10 articles.  Total: ~894 articles / 90 pages.
  • Details  →  Relative URLs like ./gcjs/202603/t20260331_1142720.htm
                 Server-rendered HTML with meta tags and TRS_Editor content.
  • Files    →  Embedded images (scanned documents) and occasional DOC/PDF
                 download links inside article_content.
  • Auth     →  None required.  Public read-only access.
  • Anti-CW  →  Random delays 1.5-3.0s between pages, 0.5-1.5s between articles.

Usage (typically spawned by task_executor):
    python xmzyjy_zcfg_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://zyjy.as.xm.gov.cn/zwgk/zcfg/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import random
import re
import ssl
import sys
import time
import urllib.request
import warnings
import zipfile
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests as _requests
from bs4 import BeautifulSoup

# Suppress SSL warnings from urllib3
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://zyjy.as.xm.gov.cn"
_LISTING_PATH = "/zwgk/zcfg"
_LISTING_PAGE1 = "/zwgk/zcfg/index.htm"
_LISTING_PAGE_PATTERN = "/zwgk/zcfg/index_{}.htm"  # {} = page-1 for N>=2

_PAGE_DELAY = (1.5, 3.0)
_ARTICLE_DELAY = (0.5, 1.5)

_STATE_FILENAME = "_crawler_state.json"
BATCH_SIZE = 10

_DEFAULT_SECTION = "zcfg"
_DEFAULT_MODULE = "政策法规"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_ATTACH_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"}

# TRS CMS date badge month mapping
_TRS_MONTHS_CN = {
    1: "\u4e00", 2: "\u4e8c", 3: "\u4e09", 4: "\u56db",
    5: "\u4e94", 6: "\u516d", 7: "\u4e03", 8: "\u516b",
    9: "\u4e5d", 10: "\u5341", 11: "\u5341\u4e00", 12: "\u5341\u4e8c",
}

# SSL contexts
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

_SUBCATEGORY_MAP = {
    "gcjs": ("工程建设", "51288"),
    "zfcg": ("政府采购", "51289"),
    "xzsyzczyjy": ("行政事业资产资源交易", "51290"),
    "tdsyqhgyqcr": ("土地使用权和矿业权出让", "51291"),
    "gycqjy": ("国有产权交易", "51292"),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay(min_s=0.5, max_s=1.5):
    time.sleep(random.uniform(min_s, max_s))


def _random_ua():
    return random.choice(_USER_AGENTS)


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch_url(url, timeout=60, allow_redirects=True):
    """Fetch a URL and return (status_code, html_text_or_None)."""
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = _requests.get(url, headers=headers, timeout=timeout,
                            allow_redirects=allow_redirects, verify=False)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.status_code, resp.text
    except Exception as e:
        logging.warning("Fetch error for %s: %s", url, e)
        return None, None


# ---------------------------------------------------------------------------
# Listing page parsing
# ---------------------------------------------------------------------------

def _resolve_relative_url(current_dir, relative_href):
    """Resolve a relative href like './gcjs/202603/t20260331_1142720.htm'
    against the listing directory /zwgk/zcfg/."""
    base = _SITE_ROOT + _LISTING_PATH + "/"
    return urljoin(base, relative_href)


def _detect_subcategory(href):
    """Detect sub-category from href path. Returns (key, display_name) or None."""
    for key, (name, _) in _SUBCATEGORY_MAP.items():
        if "/" + key + "/" in href:
            return key, name
    return None, "其他"


def _parse_listing_page(html, listing_dir):
    """Parse one TRS CMS listing page. Returns (articles, total_pages, total_records)."""
    articles = []
    if not html:
        return articles, 0, 0

    soup = BeautifulSoup(html, "lxml")

    # Extract total pages from pagebar initialization
    # recordCount: '894', pageCount: '90'
    total_pages = 0
    total_records = 0
    m_page = re.search(r"pageCount:\s*'?(\d+)'?", html)
    if m_page:
        total_pages = int(m_page.group(1))
    m_rec = re.search(r"recordCount:\s*'?(\d+)'?", html)
    if m_rec:
        total_records = int(m_rec.group(1))

    # Find the list container
    list_div = soup.find("div", class_="ggzy_list")
    if not list_div:
        logging.warning("Listing: .ggzy_list not found")
        return articles

    for li in list_div.find_all("li"):
        a_tag = li.find("a", href=True)
        if not a_tag:
            continue

        href = a_tag.get("href", "").strip()
        title = a_tag.get("title", "").strip()

        # Also try to get title from inner p tag
        if not title:
            p_title = a_tag.find("p", class_="w_space_np_02")
            if p_title:
                title = p_title.get_text(strip=True)

        if not href or not title:
            continue

        # Extract date from badge: <h1>day</h1><p>year-month</p>
        date_str = ""
        date_div = a_tag.find("div", class_="ggzy_list_l")
        if date_div:
            h1 = date_div.find("h1")
            p_date = date_div.find("p")
            day = h1.get_text(strip=True) if h1 else ""
            ym = p_date.get_text(strip=True) if p_date else ""
            if day and ym:
                date_str = "{}-{}".format(ym, day.zfill(2))

        # Build absolute URL
        detail_url = _resolve_relative_url(listing_dir, href)

        # Generate unique ID from URL
        art_id = href.rstrip(".htm").rsplit("/", 1)[-1] if "/" in href else href

        subcat_key, subcat_name = _detect_subcategory(href)

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": date_str,
            "subcategory": subcat_name,
            "subcat_key": subcat_key or "",
        })

    return articles, total_pages, total_records


def _fetch_listing_page(page_num):
    """Fetch a listing page and return articles."""
    if page_num == 1:
        url = _SITE_ROOT + _LISTING_PAGE1
    else:
        url = _SITE_ROOT + _LISTING_PAGE_PATTERN.format(page_num - 1)

    status, html = _fetch_url(url)
    if status != 200:
        logging.warning("Listing page %d returned status %s", page_num, status)
        return [], 0, 0

    articles, total_pages, total_records = _parse_listing_page(html, _LISTING_PATH)
    return articles, total_pages, total_records


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail_page(html, detail_url):
    """Parse a TRS CMS detail page. Returns dict with title, date, content, etc."""
    result = {
        "title": "",
        "date_str": "",
        "source": "",
        "column": "",
        "content_html": "",
        "content_text": "",
        "attachment_urls": [],
        "image_urls": [],
    }

    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    # Title: try <meta name="ArticleTitle"> first, then <h1>
    meta_title = soup.find("meta", attrs={"name": "ArticleTitle"})
    if meta_title:
        result["title"] = meta_title.get("content", "").strip()

    if not result["title"]:
        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)

    # Date: <meta name="PubDate">
    meta_date = soup.find("meta", attrs={"name": "PubDate"})
    if meta_date:
        result["date_str"] = meta_date.get("content", "").strip()[:10]

    # Also try article_time span
    if not result["date_str"]:
        time_span = soup.find("span", class_="article_time")
        if time_span:
            m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', time_span.get_text())
            if m:
                result["date_str"] = m.group(1)

    # Source: <meta name="ContentSource">
    meta_source = soup.find("meta", attrs={"name": "ContentSource"})
    if meta_source:
        result["source"] = meta_source.get("content", "").strip()

    # Column: <meta name="ColumnType"> or breadcrumb
    meta_col = soup.find("meta", attrs={"name": "ColumnType"})
    if meta_col:
        result["column"] = meta_col.get("content", "").strip()

    # Content: .article_content or .TRS_Editor
    content_div = soup.find("div", class_="article_content")
    if not content_div:
        content_div = soup.find("div", class_="TRS_Editor")

    if content_div:
        result["content_html"] = str(content_div)

        # Extract text
        text_parts = []
        for elem in content_div.descendants:
            if elem.name == "img":
                alt = elem.get("alt", "").strip()
                src = elem.get("src", "").strip()
                if alt:
                    text_parts.append("[图片: {}]".format(alt))
                elif src:
                    # Scanned document page without alt text
                    fname = src.rsplit("/", 1)[-1] if "/" in src else src
                    text_parts.append("[图片: {}]".format(fname))
            elif elem.name == "a" and elem.get("href"):
                pass  # handled separately
            elif isinstance(elem, str):
                t = elem.strip()
                if t:
                    text_parts.append(t)
            elif elem.name in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                text_parts.append("\n")

        result["content_text"] = re.sub(r'\n{3,}', '\n\n', "\n".join(text_parts)).strip()
        if not result["content_text"] and result["image_urls"]:
            result["content_text"] = "（本文档为扫描件，共{}页图片）".format(len(result["image_urls"]))

        # Extract attachments (download links)
        for a_tag in content_div.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith("javascript:") or href.startswith("#"):
                continue
            lower = href.lower()
            if any(lower.endswith(ext) for ext in _ATTACH_EXTS):
                abs_url = urljoin(detail_url, href)
                result["attachment_urls"].append(abs_url)

        # Extract image URLs
        for img in content_div.find_all("img", src=True):
            src = img.get("src", "").strip()
            if src:
                abs_src = urljoin(detail_url, src)
                result["image_urls"].append(abs_src)

    return result


def _fetch_detail(detail_url):
    """Fetch and parse a detail page."""
    status, html = _fetch_url(detail_url, timeout=60)
    if status != 200:
        logging.warning("Detail page returned %s: %s", status, detail_url)
        return {"title": "", "date_str": "", "content_text": "", "attachment_urls": [], "image_urls": []}

    return _parse_detail_page(html, detail_url)


# ---------------------------------------------------------------------------
# Attachment download and parsing
# ---------------------------------------------------------------------------

def _download_attachment(url, output_dir):
    """Download an attachment and return its text content."""
    headers = {"User-Agent": _random_ua()}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
            data = resp.read()
    except Exception as e:
        logging.warning("Download attachment failed: %s: %s", url, e)
        return ""

    lower = url.lower()
    filename = os.path.basename(urlparse(url).path) or "attachment"

    # Save the file
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, _sanitize_filename(filename))
    with open(filepath, "wb") as f:
        f.write(data)

    # Parse based on type
    text = ""
    if lower.endswith(".pdf"):
        text = _parse_pdf(filepath)
    elif lower.endswith((".doc", ".docx")):
        text = _parse_doc(filepath)
    elif lower.endswith((".xls", ".xlsx")):
        text = _parse_xls(filepath)
    elif lower.endswith((".ppt", ".pptx")):
        text = _parse_ppt(filepath)
    elif lower.endswith(".txt"):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("gbk", errors="replace")
    elif lower.endswith(".zip"):
        text = _parse_zip(filepath, output_dir)
    elif lower.endswith((".rar", ".7z")):
        logging.info("Skipping unparseable archive: %s", filename)
        text = "[附件: {}]".format(filename)
    else:
        text = "[附件: {}]".format(filename)

    return text


def _parse_pdf(filepath):
    """Parse PDF file to text."""
    try:
        from common import file_utils
        text = file_utils.extract_text_from_pdf(filepath)
        return text or ""
    except Exception as e:
        logging.warning("PDF parsing failed: %s: %s", filepath, e)
        return "[PDF: {}]".format(os.path.basename(filepath))


def _parse_doc(filepath):
    """Parse DOC/DOCX file to text."""
    try:
        from common import file_utils
        text = file_utils.extract_text_from_doc(filepath)
        return text or ""
    except Exception as e:
        logging.warning("DOC parsing failed: %s: %s", filepath, e)
        return "[DOC: {}]".format(os.path.basename(filepath))


def _parse_xls(filepath):
    """Parse XLS/XLSX file to text."""
    try:
        from common import file_utils
        text = file_utils.extract_text_from_xls(filepath)
        return text or ""
    except Exception as e:
        logging.warning("XLS parsing failed: %s: %s", filepath, e)
        return "[XLS: {}]".format(os.path.basename(filepath))


def _parse_ppt(filepath):
    """Parse PPT/PPTX file to text."""
    try:
        from common import file_utils
        text = file_utils.extract_text_from_ppt(filepath)
        return text or ""
    except Exception as e:
        logging.warning("PPT parsing failed: %s: %s", filepath, e)
        return "[PPT: {}]".format(os.path.basename(filepath))


def _parse_zip(filepath, output_dir):
    """Extract ZIP and parse contained files."""
    texts = []
    try:
        extract_dir = os.path.join(output_dir, "_zip_" + os.path.basename(filepath)[:50])
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                if name.endswith("/") or name.startswith("__MACOSX"):
                    continue
                try:
                    zf.extract(name, extract_dir)
                    extracted_path = os.path.join(extract_dir, name)
                    lower_name = name.lower()
                    if lower_name.endswith(".pdf"):
                        texts.append(_parse_pdf(extracted_path))
                    elif lower_name.endswith((".doc", ".docx")):
                        texts.append(_parse_doc(extracted_path))
                    elif lower_name.endswith((".xls", ".xlsx")):
                        texts.append(_parse_xls(extracted_path))
                    elif lower_name.endswith((".ppt", ".pptx")):
                        texts.append(_parse_ppt(extracted_path))
                    elif lower_name.endswith(".txt"):
                        with open(extracted_path, "r", encoding="utf-8", errors="replace") as f:
                            texts.append(f.read())
                except Exception as e:
                    logging.warning("ZIP extract failed: %s: %s", name, e)
    except Exception as e:
        logging.warning("ZIP parsing failed: %s: %s", filepath, e)
        return "[ZIP: {}]".format(os.path.basename(filepath))

    return "\n\n".join(texts)


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------

def _build_markdown(title, section, module_name, date_str, url, content_text, attachments_text, image_urls):
    """Build a markdown document from article data."""
    lines = [
        "# {}".format(title or "无标题"),
        "",
        "**模块:** {}".format(module_name),
        "**栏目:** {}".format(section),
        "**日期:** {}".format(date_str or ""),
        "**URL:** {}".format(url),
        "",
    ]

    lines.append("## 正文")
    lines.append("")
    if content_text:
        lines.append(content_text)
    else:
        lines.append("(无文字内容)")

    if image_urls:
        lines.append("")
        lines.append("## 图片附件")
        lines.append("")
        for i, img_url in enumerate(image_urls[:20], 1):
            lines.append("{}. ![]({})".format(i, img_url))

    if attachments_text:
        lines.append("")
        lines.append("## 下载附件内容")
        lines.append("")
        lines.append(attachments_text)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    state_path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_ids": [], "completed_pages": [], "total_articles": 0}


def _save_state(output_dir, state):
    state_path = os.path.join(output_dir, _STATE_FILENAME)
    # Limit stored IDs
    if len(state.get("processed_ids", [])) > 50000:
        state["processed_ids"] = state["processed_ids"][-50000:]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Batch processing and KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(file_paths, kb_id, tenant_id, task_name, start_time, max_runtime):
    """Upload markdown files to RAGFlow knowledge base. Returns uploaded doc IDs."""
    from common.file_service import FileService
    from common.document_service import DocumentService
    from common.constants import FILE_TYPE_MARKDOWN

    uploaded_ids = []
    for filepath in file_paths:
        if max_runtime and time.time() - start_time > max_runtime - 120:
            break
        filename = os.path.basename(filepath)
        try:
            file_id = FileService.upload_document(
                tenant_id=tenant_id,
                kb_id=kb_id,
                file_path=filepath,
                file_type=FILE_TYPE_MARKDOWN,
                task_name=task_name,
            )
            if file_id:
                doc_id = DocumentService.create_document(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    file_id=file_id,
                    doc_name=filename,
                    doc_type=FILE_TYPE_MARKDOWN,
                )
                if doc_id:
                    uploaded_ids.append(doc_id)
        except Exception as e:
            logging.warning("Upload failed for %s: %s", filename, e)
    return uploaded_ids


def _process_batch(batch, output_dir, kb_id, tenant_id, task_name,
                   processed_ids, state, start_time, max_runtime):
    """Process a batch of articles: fetch detail, build markdown, upload."""
    md_files = []
    for article in batch:
        art_id = article["id"]
        if art_id in processed_ids:
            continue

        _safe_print("  [{}] {}".format(art_id[:40], article["title"][:60]))
        sys.stdout.flush()

        # Fetch detail
        detail = _fetch_detail(article["url"])
        _request_delay(*_ARTICLE_DELAY)

        # Sub-category for section naming
        subcat = article.get("subcategory", "政策法规")
        section = "zcfg|{}".format(subcat)

        # Handle attachment downloads
        attachment_texts = []
        att_dir = os.path.join(output_dir, "attachments")
        for att_url in detail.get("attachment_urls", [])[:5]:
            _safe_print("    Downloading attachment: {}".format(att_url[:80]))
            att_text = _download_attachment(att_url, att_dir)
            if att_text:
                attachment_texts.append(att_text)
            _request_delay(0.3, 0.8)

        # Build markdown
        md = _build_markdown(
            title=detail.get("title") or article["title"],
            section=section,
            module_name="政策法规",
            date_str=detail.get("date_str") or article.get("date_str", ""),
            url=article["url"],
            content_text=detail.get("content_text", ""),
            attachments_text="\n\n".join(attachment_texts) if attachment_texts else "",
            image_urls=detail.get("image_urls", []),
        )

        # Save individual markdown
        safe_name = _sanitize_filename(article["title"], max_len=100)
        if not safe_name or safe_name == "untitled":
            safe_name = art_id
        md_filename = "{}_{}.md".format(
            article.get("subcat_key", "zcfg"),
            safe_name,
        )
        # Ensure unique filename
        base = md_filename
        counter = 1
        while md_filename in [m[0] for m in md_files]:
            md_filename = "{}__{}.md".format(base[:-3], counter)
            counter += 1

        md_path = os.path.join(output_dir, md_filename)
        os.makedirs(output_dir, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)

        md_files.append((md_filename, md_path))
        processed_ids.add(art_id)
        state["processed_ids"] = list(processed_ids)

    # Batch upload to KB
    if kb_id and kb_id not in ("test456", "test123", ""):
        uploaded = _upload_to_kb(
            [p for _, p in md_files], kb_id, tenant_id, task_name, start_time, max_runtime)
        if uploaded:
            _safe_print("  [OK] Uploaded {} documents to KB".format(len(uploaded)))
            state.setdefault("uploaded_ids", []).extend(uploaded)

    return len(md_files)


# ---------------------------------------------------------------------------
# Main crawler logic
# ---------------------------------------------------------------------------

def _crawl_all(start_time, max_runtime, output_dir, kb_id, tenant_id, task_name):
    """Crawl all listing pages and process all articles."""
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))
    completed_pages = set(state.get("completed_pages", []))
    total_processed = 0

    _safe_print("=== 政策法规 - 厦门市公共资源交易网 ===")
    _safe_print("Base URL: {}".format(_SITE_ROOT + _LISTING_PATH))
    sys.stdout.flush()

    # First page to discover total
    _safe_print("[LISTING] Fetching page 1...")
    articles, total_pages, total_records = _fetch_listing_page(1)
    if not articles:
        _safe_print("[LISTING] ERROR: No articles on page 1. Aborting.")
        return

    _safe_print("[LISTING] Total: {} articles across {} pages".format(total_records, total_pages))

    # Process page 1
    new_articles = [a for a in articles if a["id"] not in processed_ids]
    if new_articles:
        _safe_print("[LISTING] Page 1: {} new articles, processing...".format(len(new_articles)))
        n = _process_batch(new_articles, output_dir, kb_id, tenant_id, task_name,
                          processed_ids, state, start_time, max_runtime)
        total_processed += n
    else:
        _safe_print("[LISTING] Page 1: all already processed")
    completed_pages.add(1)
    state["completed_pages"] = sorted(completed_pages)
    _save_state(output_dir, state)

    # Remaining pages
    for p in range(2, total_pages + 1):
        # Time check
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print("[LISTING] Stopping at page {} (runtime limit {:.0f}s)".format(p - 1, elapsed))
            break

        if p in completed_pages:
            _safe_print("[LISTING] Page {}: SKIPPED".format(p))
            continue

        _request_delay(*_PAGE_DELAY)
        _safe_print("")
        _safe_print("[LISTING] Fetching page {}...".format(p))
        sys.stdout.flush()

        articles, _, _ = _fetch_listing_page(p)
        new_articles = [a for a in articles if a["id"] not in processed_ids]

        if not new_articles:
            _safe_print("[LISTING] Page {}: no new articles".format(p))
        else:
            _safe_print("[LISTING] Page {}: {} new articles".format(p, len(new_articles)))
            n = _process_batch(new_articles, output_dir, kb_id, tenant_id, task_name,
                              processed_ids, state, start_time, max_runtime)
            total_processed += n

        completed_pages.add(p)
        state["completed_pages"] = sorted(completed_pages)
        _save_state(output_dir, state)

        # Small pages → fewer pages to crawl, stop if we've reached the end
        if p >= total_pages:
            break

    _safe_print("")
    _safe_print("CRAWL COMPLETE. Total new articles processed: {}".format(total_processed))
    _safe_print("Total unique IDs tracked: {}".format(len(processed_ids)))
    _safe_print("Pages completed: {}".format(len(completed_pages)))

    # Show output files
    _safe_print("")
    _safe_print("Output files:")
    for root, dirs, files in os.walk(output_dir):
        md_count = 0
        for f in sorted(files):
            if f.endswith(".md"):
                md_count += 1
        _safe_print("  {}: {} markdown files".format(root, md_count))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="xmzyjy.cn 政策法规 crawler")
    parser.add_argument("--tenant-id", required=True, help="RAGFlow tenant ID")
    parser.add_argument("--target-url", default="https://zyjy.as.xm.gov.cn/zwgk/zcfg/",
                        help="Target listing URL")
    parser.add_argument("--kb-id", required=True, help="Knowledge base ID for upload")
    parser.add_argument("--task-name", default="xmzyjy_zcfg", help="Task name for logging")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: /ragflow/rag/<task_name>_data)")
    parser.add_argument("--full", action="store_true", help="Re-crawl all pages")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Maximum runtime in seconds (default 3300)")
    parser.add_argument("--start-page", type=int, default=1, help="Starting page number")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to crawl (0=all)")
    args = parser.parse_args()

    settings.init_settings()
    init_root_logger()

    output_dir = args.output_dir or "/ragflow/rag/{}_data".format(args.task_name)

    if args.full:
        state_path = os.path.join(output_dir, _STATE_FILENAME)
        if os.path.exists(state_path):
            os.remove(state_path)
            print("Cleared previous state for full re-crawl.")

    _safe_print("Target: {}".format(args.target_url))
    _safe_print("Output dir: {}".format(output_dir))
    _safe_print("Task: {}".format(args.task_name))
    _safe_print("Max runtime: {}s".format(args.max_runtime))

    start_time = time.time()
    _crawl_all(start_time, args.max_runtime, output_dir, args.kb_id, args.tenant_id, args.task_name)


if __name__ == "__main__":
    main()
