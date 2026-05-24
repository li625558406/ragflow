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
Dedicated web crawler for https://zjk.ggzyfw.fujian.gov.cn/zffg/notice.html
(福建省综合评标专家库 政府法规).

Crawls ALL paginated articles from the Fujian Comprehensive Evaluation
Expert Database government regulations section.  Same Epoint WebBuilder
CMS as /tzgg/notice.html — static HTML pages, no captcha required.

Site characteristics
────────────────────
  • Listing  →  /zffg/notice.html (page 1), /zffg/2.html (page 2).
                 Epoint WebBuilder CMS with mricode.pagination.js.
                 10 articles per page.  Total: 17 articles / 2 pages.
  • Details  →  Server-rendered HTML.  Content in .detail-content div.
                 Title in preceding sibling, date in 「发布时间：...」 line.
  • Files    →  Attachment downloads via /tspframe/... AttachGuid parameter.
                 Many articles are just attachment links (published PDFs).
  • Auth     →  None required.  Public read-only access.
  • Anti-CW  →  Random delays 0.5-1.5s between articles.

Usage (typically spawned by task_executor):
    python zjk_zffg_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://zjk.ggzyfw.fujian.gov.cn/zffg/notice.html \\
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
_LISTING_PATH = "/zffg"
_LISTING_PAGE1 = "/zffg/notice.html"
_LISTING_PAGE_PATTERN = "/zffg/{}.html"  # {} = page number for 2+

_PAGE_DELAY = (1.0, 2.0)
_ARTICLE_DELAY = (0.5, 1.5)

_STATE_FILENAME = "_crawler_state.json"
BATCH_SIZE = 10

_MODULE_NAME = "政府法规"
_COLUMN_NAME = "福建省综合评标专家库"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_ATTACH_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"}

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

    for li in soup.find_all("li"):
        a_tag = li.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag.get("href", "").strip()
        if "/zffg/" not in href:
            continue

        title = a_tag.get_text(strip=True)
        if not title:
            title = a_tag.get("title", "").strip()
        if not title:
            continue

        detail_url = urljoin(_SITE_ROOT, href)
        art_id = href.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": "",
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
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail_page(html, detail_url):
    """Parse a server-rendered detail page. Returns dict with parsed fields."""
    result = {
        "title": "",
        "date_str": "",
        "source": "",
        "content_text": "",
        "attachment_urls": [],
    }

    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    # Content div
    content_div = soup.find(class_="detail-content")
    if not content_div:
        content_div = soup.find(class_=re.compile(r"content|article", re.I))

    # Title: extract from sibling element before .detail-content
    if content_div and content_div.parent:
        for child in content_div.parent.children:
            if hasattr(child, "get_text"):
                txt = child.get_text(strip=True)
                skip_words = ("发布时间", "当前位置", "首页", "政府法规", "通知公告",
                               "来源", "字体", "字号", "打印", "关闭",
                               "福建省综合评标专家库")
                if txt and len(txt) > 5 and len(txt) < 250:
                    if not any(w in txt for w in skip_words):
                        result["title"] = txt
                        break

    # Fallback: regex
    if not result["title"]:
        text = soup.get_text(" ", strip=True)
        m = re.search(r'(?:政府法规|通知公告)\s+(.+?)\s*发布时间', text)
        if m:
            result["title"] = m.group(1).strip()[:200]

    # Date
    text = soup.get_text()
    m_date = re.search(r'发布时间[：:]\s*(\d{4}-\d{2}-\d{2})', text)
    if m_date:
        result["date_str"] = m_date.group(1)

    # Source
    m_source = re.search(r'来源[：:]\s*([^\s\n]+)', text)
    if m_source:
        src = m_source.group(1).strip()
        if src not in ("发布时间", "政府法规", "通知公告", "当前位置", "首页", "字体"):
            result["source"] = src

    # Content extraction
    if content_div:
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
            elif elem.name in ("p", "div", "br", "li",
                               "h1", "h2", "h3", "h4", "h5", "h6"):
                lines.append("\n")

        result["content_text"] = re.sub(r'\n{3,}', '\n\n', "\n".join(lines)).strip()

        # Extract attachment links
        for a_tag in content_div.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith("javascript:") or href.startswith("#"):
                continue
            if "AttachGuid" in href:
                abs_url = urljoin(detail_url, href)
                result["attachment_urls"].append(abs_url)
            else:
                lower = href.lower()
                if any(lower.endswith(ext) for ext in _ATTACH_EXTS):
                    abs_url = urljoin(detail_url, href)
                    result["attachment_urls"].append(abs_url)

    # Also check whole page for attachments (many zffg articles embed pdf links)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        if "AttachGuid" in href:
            abs_url = urljoin(detail_url, href)
            if abs_url not in result["attachment_urls"]:
                result["attachment_urls"].append(abs_url)

    # If content references an attachment file but has no AttachGuid link,
    # the attachment might be the actual content
    if not result["content_text"] or len(result["content_text"]) < 50:
        # Check for file references like "XXX.pdf" in content
        if result["attachment_urls"]:
            result["content_text"] = result["content_text"] or ""
            # Content IS the attachment in many cases

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
            disp = resp.headers.get("Content-Disposition", "")
            if "filename=" in disp:
                fname_match = re.search(r'filename[^*]=["\']?([^"\';]+)', disp)
                if fname_match:
                    raw_fname = fname_match.group(1).strip()
                    try:
                        filename = raw_fname.encode("latin-1").decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        filename = raw_fname
    except Exception as e:
        logging.warning("Download attachment failed: %s: %s", url, e)
        return ""

    if not filename or filename == "attachment":
        filename = os.path.basename(urlparse(url).path) or "attachment"

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, _sanitize_filename(filename))
    with open(filepath, "wb") as f:
        f.write(data)

    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _parse_pdf(filepath)
    elif lower.endswith((".doc", ".docx")):
        return _parse_doc(filepath)
    elif lower.endswith((".xls", ".xlsx")):
        return _parse_xls(filepath)
    elif lower.endswith((".ppt", ".pptx")):
        return _parse_ppt(filepath)
    elif lower.endswith(".txt"):
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return data.decode("gbk", errors="replace")
    elif lower.endswith(".zip"):
        return _parse_zip(filepath, output_dir)
    elif lower.endswith((".rar", ".7z")):
        return "[附件: {}]".format(filename)
    return "[附件: {}]".format(filename)


def _parse_pdf(filepath):
    try:
        from common import file_utils
        text = file_utils.extract_text_from_pdf(filepath)
        return text or ""
    except Exception as e:
        logging.warning("PDF parsing failed: %s: %s", filepath, e)
        return "[PDF: {}]".format(os.path.basename(filepath))


def _parse_doc(filepath):
    try:
        from common import file_utils
        text = file_utils.extract_text_from_doc(filepath)
        return text or ""
    except Exception as e:
        logging.warning("DOC parsing failed: %s: %s", filepath, e)
        return "[DOC: {}]".format(os.path.basename(filepath))


def _parse_xls(filepath):
    try:
        from common import file_utils
        text = file_utils.extract_text_from_xls(filepath)
        return text or ""
    except Exception as e:
        logging.warning("XLS parsing failed: %s: %s", filepath, e)
        return "[XLS: {}]".format(os.path.basename(filepath))


def _parse_ppt(filepath):
    try:
        from common import file_utils
        text = file_utils.extract_text_from_ppt(filepath)
        return text or ""
    except Exception as e:
        logging.warning("PPT parsing failed: %s: %s", filepath, e)
        return "[PPT: {}]".format(os.path.basename(filepath))


def _parse_zip(filepath, output_dir):
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
    lines = [
        "# {}".format(title or "无标题"),
        "",
        "**模块:** {}".format(_MODULE_NAME),
        "**栏目:** {}".format(_COLUMN_NAME),
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
        lines.append("(详见附件)")

    if attachments_text:
        lines.append("")
        lines.append("## 附件内容")
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
                tenant_id=tenant_id, kb_id=kb_id,
                file_path=filepath, file_type=FILE_TYPE_MARKDOWN,
                task_name=task_name,
            )
            if file_id:
                doc_id = DocumentService.create_document(
                    tenant_id=tenant_id, kb_id=kb_id,
                    file_id=file_id, doc_name=filename,
                    doc_type=FILE_TYPE_MARKDOWN,
                )
                if doc_id:
                    uploaded_ids.append(doc_id)
        except Exception as e:
            logging.warning("Upload failed for %s: %s", filename, e)
    return uploaded_ids


def _process_batch(batch, output_dir, kb_id, tenant_id, task_name,
                   processed_ids, state, start_time, max_runtime):
    md_files = []
    for article in batch:
        art_id = article["id"]
        if art_id in processed_ids:
            continue

        _safe_print("  [{}] {}".format(art_id[:40], article["title"][:60]))
        sys.stdout.flush()

        detail = _fetch_detail(article["url"])
        _request_delay(*_ARTICLE_DELAY)

        attachment_texts = []
        att_dir = os.path.join(output_dir, "attachments")
        for att_url in detail.get("attachment_urls", [])[:5]:
            _safe_print("    Downloading attachment: {}".format(att_url[:80]))
            att_text = _download_attachment(att_url, att_dir)
            if att_text:
                attachment_texts.append(att_text)
            _request_delay(0.3, 0.8)

        date_str = detail.get("date_str") or article.get("date_str", "")
        source = detail.get("source", "")

        md = _build_markdown(
            title=detail.get("title") or article["title"],
            date_str=date_str,
            url=article["url"],
            source=source,
            content_text=detail.get("content_text", ""),
            attachments_text="\n\n".join(attachment_texts) if attachment_texts else "",
        )

        title_for_filename = article["title"]
        safe_name = _sanitize_filename(title_for_filename, max_len=100)
        if not safe_name or safe_name == "untitled":
            safe_name = art_id

        date_prefix = date_str.replace("-", "") if date_str else ""
        md_filename = "zffg_{}_{}.md".format(date_prefix, safe_name)
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

    if kb_id and kb_id not in ("test456", "test123", ""):
        uploaded = _upload_to_kb(
            [p for _, p in md_files], kb_id, tenant_id, task_name,
            start_time, max_runtime)
        if uploaded:
            _safe_print("  [OK] Uploaded {} documents to KB".format(len(uploaded)))
            state.setdefault("uploaded_ids", []).extend(uploaded)

    return len(md_files)


# ---------------------------------------------------------------------------
# Main crawler logic
# ---------------------------------------------------------------------------

def _crawl_all(start_time, max_runtime, output_dir, kb_id, tenant_id, task_name):
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))
    completed_pages = set(state.get("completed_pages", []))
    total_processed = 0

    _safe_print("=== 政府法规 - 福建省综合评标专家库 ===")
    _safe_print("Base URL: {}".format(_SITE_ROOT + _LISTING_PATH))
    sys.stdout.flush()

    # Probe to detect total pages
    _safe_print("[LISTING] Detecting available pages...")
    total_pages = 0
    for p in range(1, 11):
        if p == 1:
            url = _SITE_ROOT + _LISTING_PAGE1
        else:
            url = _SITE_ROOT + _LISTING_PAGE_PATTERN.format(p)
        status, _ = _fetch_url(url, timeout=30)
        if status == 200:
            total_pages = p
        else:
            break
    _safe_print("[LISTING] Detected {} static listing pages".format(total_pages))

    for p in range(1, total_pages + 1):
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print("[LISTING] Stopping at page {} (runtime limit {:.0f}s)".format(
                p - 1, elapsed))
            break

        if p in completed_pages:
            _safe_print("[LISTING] Page {}: SKIPPED (already completed)".format(p))
            continue

        if p > 1:
            _request_delay(*_PAGE_DELAY)

        _safe_print("")
        _safe_print("[LISTING] Page {}/{}...".format(p, total_pages))
        sys.stdout.flush()

        articles, _ = _fetch_listing_page(p)
        new_articles = [a for a in articles if a["id"] not in processed_ids]

        if not new_articles:
            _safe_print("[LISTING] Page {}: all {} articles already processed".format(
                p, len(articles)))
        else:
            _safe_print("[LISTING] Page {}: {} new articles".format(p, len(new_articles)))
            n = _process_batch(new_articles, output_dir, kb_id, tenant_id, task_name,
                              processed_ids, state, start_time, max_runtime)
            total_processed += n

        completed_pages.add(p)
        state["completed_pages"] = sorted(completed_pages)
        _save_state(output_dir, state)

    _safe_print("")
    _safe_print("CRAWL COMPLETE. Total new articles processed: {}".format(total_processed))
    _safe_print("Total unique IDs tracked: {}".format(len(processed_ids)))
    _safe_print("Pages completed: {}".format(len(completed_pages)))

    _safe_print("")
    _safe_print("Output files:")
    for root, dirs, files in os.walk(output_dir):
        md_count = sum(1 for f in files if f.endswith(".md"))
        _safe_print("  {}: {} markdown files".format(root, md_count))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="zjk.ggzyfw.fujian.gov.cn 政府法规 crawler")
    parser.add_argument("--tenant-id", required=True, help="RAGFlow tenant ID")
    parser.add_argument("--target-url",
                        default="https://zjk.ggzyfw.fujian.gov.cn/zffg/notice.html",
                        help="Target listing URL")
    parser.add_argument("--kb-id", required=True, help="Knowledge base ID for upload")
    parser.add_argument("--task-name", default="zjk_zffg", help="Task name for logging")
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
    _crawl_all(start_time, args.max_runtime, output_dir,
               args.kb_id, args.tenant_id, args.task_name)


if __name__ == "__main__":
    CONSUMER_NAME = "zjk_zffg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
