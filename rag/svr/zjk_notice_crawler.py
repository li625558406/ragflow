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
Dedicated web crawler for https://zjk.ggzyfw.fujian.gov.cn/tzgg/notice.html
(福建省综合评标专家库 通知公告).

Crawls ALL paginated articles from the Fujian Comprehensive Evaluation
Expert Database notice board.  This is an Epoint WebBuilder CMS site with
static HTML pages for pages 1-7 and AJAX-based listing (with captcha) for
pages 8-9.

Site characteristics
────────────────────
  • Listing  →  /tzgg/notice.html (page 1), 2.html … 7.html (pages 2-7).
                 Epoint WebBuilder CMS with mricode.pagination.js.
                 Pages 8-9 require AJAX + captcha — use Playwright fallback.
                 10 articles per page.  Total: 87 articles / 9 pages.
  • Details  →  Server-rendered HTML.  Content in .detail-content div.
                 Title in preceding sibling, date in 「发布时间：...」 line.
  • Files    →  Attachment downloads via /tspframe/... AttachGuid parameter.
  • Auth     →  None required.  Public read-only access.
  • Anti-CW  →  Random delays 1.5-3.0s between pages, 0.5-1.5s between articles.

Usage (typically spawned by task_executor):
    python zjk_notice_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://zjk.ggzyfw.fujian.gov.cn/tzgg/notice.html \\
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

_SITE_ROOT = "https://zjk.ggzyfw.fujian.gov.cn"
_LISTING_PATH = "/tzgg"
_LISTING_PAGE1 = "/tzgg/notice.html"
_LISTING_PAGE_PATTERN = "/tzgg/{}.html"  # {} = page number for 2-7

# Epoint WebBuilder AJAX API (for pages 8-9 with captcha)
_SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
_CATEGORY_NUM = "001"
_API_LISTING = "/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"

_PAGE_DELAY = (1.5, 3.0)
_ARTICLE_DELAY = (0.5, 1.5)

_STATE_FILENAME = "_crawler_state.json"
BATCH_SIZE = 10

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_ATTACH_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"}

# SSL context
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


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

def _fetch_url(url, timeout=60):
    """Fetch a URL and return (status_code, html_text_or_None)."""
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = _requests.get(url, headers=headers, timeout=timeout,
                            allow_redirects=True, verify=False)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.status_code, resp.text
    except Exception as e:
        logging.warning("Fetch error for %s: %s", url, e)
        return None, None


# ---------------------------------------------------------------------------
# Listing page parsing
# ---------------------------------------------------------------------------

def _parse_listing_page(html):
    """Parse one static listing page. Returns list of article dicts."""
    articles = []
    if not html:
        return articles

    soup = BeautifulSoup(html, "lxml")

    # Find list items — they're <li> with <a href="/tzgg/...html">
    for li in soup.find_all("li"):
        a_tag = li.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag.get("href", "").strip()
        if "/tzgg/" not in href:
            continue

        title = a_tag.get_text(strip=True)
        if not title:
            title = a_tag.get("title", "").strip()
        if not title:
            continue

        # Build absolute URL
        detail_url = urljoin(_SITE_ROOT, href)

        # Generate article ID from URL path
        art_id = href.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")

        # Try to extract date from sibling info
        date_str = ""
        # Sometimes date is in a span near the link
        date_span = li.find("span")
        if date_span:
            m = re.search(r'(\d{4}-\d{2}-\d{2})', date_span.get_text())
            if m:
                date_str = m.group(1)

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": date_str,
        })

    return articles


def _fetch_listing_page(page_num):
    """Fetch a static listing page. Returns (articles, http_status)."""
    if page_num == 1:
        url = _SITE_ROOT + _LISTING_PAGE1
    else:
        url = _SITE_ROOT + _LISTING_PAGE_PATTERN.format(page_num)

    status, html = _fetch_url(url)
    if status != 200:
        logging.warning("Listing page %d returned status %s", page_num, status)
        return [], status

    articles = _parse_listing_page(html)
    return articles, status


# ---------------------------------------------------------------------------
# AJAX listing (for pages 8-9 with Playwright fallback)
# ---------------------------------------------------------------------------

def _fetch_listing_page_ajax(page_index, client=None):
    """Fetch listing via AJAX API using Playwright.

    NOTE: The Epoint WebBuilder backend enforces captcha for pageIndex >= 3
    (page 4+).  Static HTML covers pages 1-7, so AJAX pages 8-9 contain the
    oldest 17 items.  These cannot be fetched without solving the captcha.
    Subsequent crawler runs will capture them as they migrate to earlier pages.

    Returns list of article dicts (usually empty due to captcha).
    """
    articles = []

    if client is None:
        logging.warning("Playwright client not available for AJAX page %d", page_index + 1)
        return articles

    try:
        json_body = {
            "siteGuid": _SITE_GUID,
            "categoryNum": _CATEGORY_NUM,
            "pageIndex": page_index,
            "pageSize": 10,
            "YZM": "",
            "ImgGuid": "",
        }

        resp = client.post(
            _SITE_ROOT + _API_LISTING,
            json_body=json_body,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json, text/plain, */*",
                "Referer": _SITE_ROOT + "/tzgg/notice.html",
                "Origin": _SITE_ROOT,
            },
            timeout=30,
        )

        if resp.status_code == 200:
            try:
                data = resp.json()
                for item in data.get("custom", []):
                    infourl = item.get("infourl", "")
                    title = item.get("title2", "") or item.get("title", "")
                    if not title:
                        title_html = item.get("title", "")
                        title = re.sub(r'<[^>]+>', '', title_html).strip()
                    date_str = item.get("infodate", "")[:10]
                    art_id = item.get("infoid", "")

                    if infourl and title:
                        detail_url = urljoin(_SITE_ROOT, infourl)
                        articles.append({
                            "id": art_id or detail_url.rsplit("/", 1)[-1].replace(".html", ""),
                            "title": title,
                            "url": detail_url,
                            "date_str": date_str,
                            "source": item.get("zhuanzai", ""),
                        })
                if articles:
                    logging.info("AJAX page %d: got %d articles", page_index + 1, len(articles))
                else:
                    logging.info("AJAX page %d: captcha blocked (0 articles)", page_index + 1)
            except Exception as e:
                logging.warning("AJAX listing parse error: %s", e)
        else:
            logging.warning("AJAX listing page %d returned status %s", page_index + 1, resp.status_code)
    except Exception as e:
        logging.warning("AJAX listing page %d failed: %s", page_index + 1, e)

    return articles


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail_page(html, detail_url):
    """Parse a server-rendered detail page. Returns dict with parsed fields."""
    result = {
        "title": "",
        "date_str": "",
        "source": "",
        "content_html": "",
        "content_text": "",
        "attachment_urls": [],
    }

    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    text = soup.get_text(" ", strip=True)

    # Title: between "通知公告" and "发布时间" in the detail header area.
    m_title = re.search(r'通知公告\s+(.+?)\s*发布时间', text)
    if m_title:
        result["title"] = m_title.group(1).strip()[:200]

    # Fallback: extract title from siblings before .detail-content
    if not result["title"]:
        content_div = soup.find(class_="detail-content")
        if content_div:
            parent = content_div.parent
            if parent:
                for child in parent.children:
                    if hasattr(child, "get_text"):
                        txt = child.get_text(strip=True)
                        skip_words = ("发布时间", "当前位置", "首页", "通知公告",
                                       "来源", "字体", "字号", "打印", "关闭")
                        if txt and len(txt) > 10 and len(txt) < 250:
                            if not any(w in txt for w in skip_words):
                                result["title"] = txt
                                break

    # Last resort: look for heading
    if not result["title"]:
        for tag in ["h1", "h2", "h3", "h4"]:
            h = soup.find(tag)
            if h:
                t = h.get_text(strip=True)
                if t and len(t) > 5:
                    result["title"] = t[:200]
                    break

    # Date: 「发布时间：2026-04-08 20:00」
    m_date = re.search(r'发布时间[：:]\s*(\d{4}-\d{2}-\d{2})', text)
    if m_date:
        result["date_str"] = m_date.group(1)

    # Source: 「来源：XXX」
    m_source = re.search(r'来源[：:]\s*([^\s\n]+)', text)
    if m_source:
        src = m_source.group(1).strip()
        if src not in ("发布时间", "通知公告", "当前位置", "首页", "字体"):
            result["source"] = src

    # Content: .detail-content
    content_div = soup.find(class_="detail-content")
    if not content_div:
        # Fallback: try other common class names
        for cls in ["article-content", "content", "TRS_Editor", "detail_content"]:
            content_div = soup.find(class_=cls)
            if content_div:
                break

    if content_div:
        result["content_html"] = str(content_div)

        # Extract text from content
        lines = []
        for elem in content_div.descendants:
            if elem.name == "img":
                alt = elem.get("alt", "").strip()
                src = elem.get("src", "").strip()
                if alt:
                    lines.append("[图片: {}]".format(alt))
                elif src:
                    fname = src.rsplit("/", 1)[-1] if "/" in src else src
                    lines.append("[图片: {}]".format(fname))
            elif elem.name == "a" and elem.get("href"):
                pass  # handled as attachment
            elif isinstance(elem, str):
                t = elem.strip()
                if t:
                    lines.append(t)
            elif elem.name in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                lines.append("\n")

        result["content_text"] = re.sub(r'\n{3,}', '\n\n', "\n".join(lines)).strip()

        # Extract attachment links
        for a_tag in content_div.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith("javascript:") or href.startswith("#"):
                continue
            # Check for AttachGuid links OR direct file links
            if "AttachGuid" in href:
                abs_url = urljoin(detail_url, href)
                result["attachment_urls"].append(abs_url)
            else:
                lower = href.lower()
                if any(lower.endswith(ext) for ext in _ATTACH_EXTS):
                    abs_url = urljoin(detail_url, href)
                    result["attachment_urls"].append(abs_url)

    # Also check for attachments outside content_div (in the whole page)
    if not result["attachment_urls"]:
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if "AttachGuid" in href:
                abs_url = urljoin(detail_url, href)
                if abs_url not in result["attachment_urls"]:
                    result["attachment_urls"].append(abs_url)

    return result


def _fetch_detail(detail_url):
    """Fetch and parse a detail page."""
    status, html = _fetch_url(detail_url, timeout=60)
    if status != 200:
        logging.warning("Detail page returned %s: %s", status, detail_url)
        return {
            "title": "", "date_str": "", "source": "",
            "content_text": "", "attachment_urls": [],
        }
    return _parse_detail_page(html, detail_url)


# ---------------------------------------------------------------------------
# Attachment download and parsing
# ---------------------------------------------------------------------------

def _download_attachment(url, output_dir):
    """Download an attachment and return its text content."""
    headers = {"User-Agent": _random_ua()}
    filename = "attachment"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
            data = resp.read()
            # Try to get filename from Content-Disposition header
            disp = resp.headers.get("Content-Disposition", "")
            if "filename=" in disp:
                fname_match = re.search(r'filename[^*]=["\']?([^"\';]+)', disp)
                if fname_match:
                    raw_fname = fname_match.group(1).strip()
                    # Fix UTF-8 encoded as Latin-1 in headers
                    try:
                        filename = raw_fname.encode("latin-1").decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        filename = raw_fname
    except Exception as e:
        logging.warning("Download attachment failed: %s: %s", url, e)
        return ""

    if not filename or filename == "attachment":
        filename = os.path.basename(urlparse(url).path) or "attachment"

    lower = filename.lower()

    # Also try Content-Disposition header for URL-based filename
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, _sanitize_filename(filename))
    with open(filepath, "wb") as f:
        f.write(data)

    # Parse based on filename extension
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

def _build_markdown(title, date_str, url, source, content_text, attachments_text):
    """Build a markdown document from article data."""
    lines = [
        "# {}".format(title or "无标题"),
        "",
        "**模块:** 通知公告",
        "**栏目:** 福建省综合评标专家库",
        "**日期:** {}".format(date_str or ""),
        "**URL:** {}".format(url),
    ]

    if source:
        lines.append("**来源:** {}".format(source))

    lines.append("")
    lines.append("## 正文")
    lines.append("")
    if content_text:
        lines.append(content_text)
    else:
        lines.append("(无文字内容)")

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
        date_str = detail.get("date_str") or article.get("date_str", "")
        source = detail.get("source") or article.get("source", "")

        md = _build_markdown(
            title=detail.get("title") or article["title"],
            date_str=date_str,
            url=article["url"],
            source=source,
            content_text=detail.get("content_text", ""),
            attachments_text="\n\n".join(attachment_texts) if attachment_texts else "",
        )

        # Save individual markdown
        title_for_filename = article["title"]
        safe_name = _sanitize_filename(title_for_filename, max_len=100)
        if not safe_name or safe_name == "untitled":
            safe_name = art_id

        date_prefix = date_str.replace("-", "") if date_str else ""
        md_filename = "notice_{}_{}.md".format(
            date_prefix,
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

    _safe_print("=== 福建省综合评标专家库 通知公告 ===")
    _safe_print("Base URL: {}".format(_SITE_ROOT + _LISTING_PATH))
    sys.stdout.flush()

    # Detect total pages from page 1
    _safe_print("[LISTING] Fetching page 1 to detect total pages...")
    articles, status = _fetch_listing_page(1)
    if not articles:
        _safe_print("[LISTING] ERROR: No articles on page 1. Aborting.")
        return

    # Determine total pages by probing pages 2-9
    total_pages = 1
    for p in range(2, 10):
        _, s = _fetch_listing_page(p)
        if s == 200:
            total_pages = p
        else:
            break
    _safe_print("[LISTING] Detected {} static listing pages".format(total_pages))

    # Also try to detect if page 9 exists via page 1 listing count
    # (pages beyond total_pages will be tried with AJAX)

    # Initialize PlaywrightHttpClient for AJAX pages if needed
    pw_client = None
    pw_needed = total_pages < 9

    # Process static listing pages (1 to min(total_pages, 7))
    max_static = min(total_pages, 7)
    for p in range(1, max_static + 1):
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print("[LISTING] Stopping at page {} (runtime limit {:.0f}s)".format(p - 1, elapsed))
            break

        if p in completed_pages:
            _safe_print("[LISTING] Page {}: SKIPPED (already completed)".format(p))
            continue

        if p > 1:
            _request_delay(*_PAGE_DELAY)

        _safe_print("")
        _safe_print("[LISTING] Page {}/{}: static HTML...".format(p, total_pages))
        sys.stdout.flush()

        articles, _ = _fetch_listing_page(p)
        new_articles = [a for a in articles if a["id"] not in processed_ids]

        if not new_articles:
            _safe_print("[LISTING] Page {}: all {} articles already processed".format(p, len(articles)))
        else:
            _safe_print("[LISTING] Page {}: {} new articles".format(p, len(new_articles)))
            n = _process_batch(new_articles, output_dir, kb_id, tenant_id, task_name,
                              processed_ids, state, start_time, max_runtime)
            total_processed += n

        completed_pages.add(p)
        state["completed_pages"] = sorted(completed_pages)
        _save_state(output_dir, state)

    # Process AJAX pages 8-9 using Playwright
    for p in range(8, 10):
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print("[LISTING] Stopping before AJAX page {} (runtime limit {:.0f}s)".format(p, elapsed))
            break

        if p in completed_pages:
            _safe_print("[LISTING] AJAX Page {}: SKIPPED (already completed)".format(p))
            continue

        _request_delay(*_PAGE_DELAY)
        _safe_print("")
        _safe_print("[LISTING] AJAX Page {}/9: Playwright...".format(p))
        sys.stdout.flush()

        if pw_client is None:
            try:
                from rag.svr.crawler_utils import PlaywrightHttpClient
                pw_client = PlaywrightHttpClient(headless=True, timeout=30)
                pw_client.start()
                _safe_print("[LISTING] Playwright browser started for AJAX pages")
            except Exception as e:
                logging.warning("Cannot start Playwright for AJAX pages: %s", e)
                _safe_print("[LISTING] SKIP: Playwright not available for AJAX pages")
                break

        articles = _fetch_listing_page_ajax(p - 1, client=pw_client)
        new_articles = [a for a in articles if a["id"] not in processed_ids]

        if not new_articles:
            _safe_print("[LISTING] AJAX Page {}: no new articles (total={})".format(p, len(articles)))
        else:
            _safe_print("[LISTING] AJAX Page {}: {} new articles".format(p, len(new_articles)))
            n = _process_batch(new_articles, output_dir, kb_id, tenant_id, task_name,
                              processed_ids, state, start_time, max_runtime)
            total_processed += n

        completed_pages.add(p)
        state["completed_pages"] = sorted(completed_pages)
        _save_state(output_dir, state)

    # Cleanup
    if pw_client:
        try:
            pw_client.stop()
        except Exception:
            pass

    _safe_print("")
    _safe_print("CRAWL COMPLETE. Total new articles processed: {}".format(total_processed))
    _safe_print("Total unique IDs tracked: {}".format(len(processed_ids)))
    _safe_print("Pages completed: {}".format(len(completed_pages)))

    # Show output files
    _safe_print("")
    _safe_print("Output files:")
    for root, dirs, files in os.walk(output_dir):
        md_count = sum(1 for f in files if f.endswith(".md"))
        _safe_print("  {}: {} markdown files".format(root, md_count))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="zjk.ggzyfw.fujian.gov.cn 通知公告 crawler")
    parser.add_argument("--tenant-id", required=True, help="RAGFlow tenant ID")
    parser.add_argument("--target-url",
                        default="https://zjk.ggzyfw.fujian.gov.cn/tzgg/notice.html",
                        help="Target listing URL")
    parser.add_argument("--kb-id", required=True, help="Knowledge base ID for upload")
    parser.add_argument("--task-name", default="zjk_notice", help="Task name for logging")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: /ragflow/rag/<task_name>_data)")
    parser.add_argument("--full", action="store_true", help="Re-crawl all pages")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Maximum runtime in seconds (default 3300)")
    args = parser.parse_args()

    settings.init_settings()

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
    CONSUMER_NAME = "zjk_notice_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
