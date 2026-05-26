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
Dedicated web crawler for hwdms.mot.gov.cn (全国公路建设市场监督管理系统 — 政策法规).

Crawls the policy/regulation listing page and detail pages with attachment
download and parsing.

Site characteristics
────────────────────
  • Java Struts/Spring MVC web app — .do action URLs with AJAX APIs.
  • Session-based: requires JSESSIONID cookie from initial page visit.
  • List data loaded via POST AJAX: getPolicyList.do (paginated, max 15/page).
  • Detail content embedded in list API response (content field in HTML).
  • Attachments (fileList) only populated in detail API: getPolicy.do.
  • File download via separate REST service on port 80 base path.
  • No WAF/captcha — standard HTTP requests with proper session + headers.

Usage (typically spawned by task_executor):
    python hwdms_policy_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://hwdms.mot.gov.cn/BMWebSite/Policy/index.do?type=0 \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import datetime
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from urllib.parse import urljoin, urlparse, parse_qs

import requests as _requests
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE = "https://hwdms.mot.gov.cn"
_SITE_NAME = "全国公路建设市场监督管理系统"
_POLICY_INDEX = f"{_BASE}/BMWebSite/Policy/index.do"
_POLICY_LIST_API = f"{_BASE}/BMWebSite/Policy/getPolicyList.do"
_POLICY_DETAIL_API = f"{_BASE}/BMWebSite/Policy/getPolicy.do"
_DETAIL_PAGE = f"{_BASE}/BMWebSite/Policy/info.do"
_FILE_DOWNLOAD_BASE = "https://hwdms.mot.gov.cn/otcredit-webapp/rest/companyfile/BMWebSiteDownload"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HTML_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_AJAX_HEADERS = {
    **_HTML_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}

_PAGE_SIZE = 15

# Anti-crawling delays (seconds)
_PAGE_DELAY = (1.0, 2.5)
_ITEM_DELAY = (0.3, 1.0)

_STATE_FILENAME = "_crawler_state.json"

_ATTACH_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".7z",
    ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay(min_s, max_s):
    time.sleep(random.uniform(min_s, max_s))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


def _is_attach_url(url):
    lower = url.lower().split("?")[0]
    return any(lower.endswith(ext) for ext in _ATTACH_EXTENSIONS)


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session(target_url):
    """Create a requests.Session with JSESSIONID cookie from the index page."""
    sess = _requests.Session()
    sess.headers.update(_HTML_HEADERS)
    sess.verify = False

    try:
        r = sess.get(target_url, timeout=30, verify=False)
        r.encoding = 'utf-8'
        if 'JSESSIONID' in sess.cookies.get_dict():
            logging.info("Session initialized (JSESSIONID obtained)")
        else:
            logging.warning("No JSESSIONID cookie — API calls may fail")
    except Exception as e:
        logging.warning("Failed to init session: %s", e)

    return sess


# ---------------------------------------------------------------------------
# List API
# ---------------------------------------------------------------------------

def _extract_type_param(target_url):
    """Extract 'type' parameter from the target URL (0=施工, 1=监理)."""
    parsed = urlparse(target_url)
    params = parse_qs(parsed.query)
    return params.get("type", ["0"])[0]


def _fetch_list_page(sess, page, page_size, type_filter, name_filter=""):
    """Call the list AJAX API and return parsed JSON."""
    data = {
        "page": str(page),
        "rows": str(page_size),
        "type": str(type_filter),
        "name": str(name_filter),
    }
    headers = {**_AJAX_HEADERS, "Referer": f"{_POLICY_INDEX}?type={type_filter}"}

    try:
        r = sess.post(_POLICY_LIST_API, data=data, headers=headers, timeout=60, verify=False)
        r.encoding = 'utf-8'
        return r.json()
    except Exception as e:
        logging.error("List API error (page %d): %s", page, e)
        return None


# ---------------------------------------------------------------------------
# Detail API (for fileList)
# ---------------------------------------------------------------------------

def _fetch_detail(sess, item_id, type_filter):
    """Call the detail AJAX API to get fileList with attachment info."""
    headers = {**_AJAX_HEADERS, "Referer": f"{_DETAIL_PAGE}?id={item_id}"}

    try:
        r = sess.post(f"{_POLICY_DETAIL_API}?id={item_id}", headers=headers,
                      timeout=60, verify=False)
        r.encoding = 'utf-8'
        resp = r.json()
        if resp.get("code") == 0:
            return resp.get("data", {})
        return None
    except Exception as e:
        logging.error("Detail API error (%s): %s", item_id, e)
        return None


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=120):
    """Download a file (attachment) from the server."""
    from urllib.parse import quote as _url_quote

    encoded_url = _url_quote(file_url, safe=":/?&=#%")

    try:
        resp = sess.get(encoded_url, timeout=timeout, stream=True, verify=False)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        logging.error("Download error %s: %s", encoded_url, e)
    return None


def _build_file_download_url(file_id):
    """Build the file download URL from a file ID."""
    return f"{_FILE_DOWNLOAD_BASE}?linkId={file_id}"


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path, dest_dir):
    extracted = []
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, 'wb') as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
                _safe_print(f"           Extracted: {safe_name}")
    except Exception as e:
        _safe_print(f"           ZIP extract error: {e}")
    return extracted


# ---------------------------------------------------------------------------
# Text extraction from attachments
# ---------------------------------------------------------------------------

def _extract_file_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            import fitz
            with fitz.open(filepath) as doc:
                for page in doc:
                    text += page.get_text() + "\n"
        elif ext == '.docx':
            from docx import Document
            doc = Document(filepath)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext in ('.xls', '.xlsx'):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    line = '\t'.join(str(c) if c is not None else '' for c in row)
                    text += line + "\n"
                text += "\n"
        elif ext == '.txt':
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)

    return text.strip()


# ---------------------------------------------------------------------------
# HTML to text conversion
# ---------------------------------------------------------------------------

def _html_to_text(html_content):
    """Convert HTML content to plain text with basic formatting."""
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, 'lxml')

    for tag in soup.find_all(['script', 'style']):
        tag.decompose()

    for tag_name in ['p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr']:
        for tag in soup.find_all(tag_name):
            tag.insert_after(soup.new_string('\n'))

    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\n +', '\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load state: %s", e)
    return {"processed_ids": []}


def _save_state(output_dir, state):
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, _STATE_FILENAME), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    logging.info("State saved (%d IDs)", len(state.get("processed_ids", [])))


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(item, detail_data):
    """Build a markdown document from item + detail data."""
    lines = [
        f"# {item['name']}",
        "",
        f"**来源**: {_SITE_NAME} — 政策法规",
    ]

    if item.get('dispatchTime'):
        lines.append(f"**发文时间**: {item['dispatchTime']}")
    if item.get('dispatchNumber') and item['dispatchNumber'] != '要点':
        lines.append(f"**文号**: {item['dispatchNumber']}")
    if item.get('dispatchType'):
        lines.append(f"**分类**: {item['dispatchType']}")
    if item.get('recordUnit'):
        lines.append(f"**发文单位**: {item['recordUnit']}")
    if item.get('recordTime'):
        lines.append(f"**录入时间**: {item['recordTime']}")

    detail_url = f"{_DETAIL_PAGE}?id={item['id']}"
    lines.append(f"**原文链接**: {detail_url}")

    lines.append("")
    lines.append("---")
    lines.append("")

    if item.get('content_text'):
        lines.append(item['content_text'])
    else:
        lines.append("(无法提取正文内容)")

    if item.get('attach_texts'):
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附件内容")
        lines.append("")
        for att_name, att_text in item['attach_texts']:
            if att_text:
                lines.append(f"### {att_name}")
                lines.append("")
                lines.append(att_text)
                lines.append("")

    return "\n".join(lines)


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    safe_id = re.sub(r'[\\/:*?"<>|]', "_", article_id)
    path = os.path.join(articles_dir, f"{safe_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
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
        doc_id = doc["id"]
        logging.info("Document %s uploaded to KB %s", doc_id, kb_id)
        try:
            DocumentService.update_by_id(doc_id, {"parser_id": parser_id})
        except Exception as e:
            logging.error("Failed to update parser_id for %s: %s", doc_id, e)
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Single item processing
# ---------------------------------------------------------------------------

def _process_item(sess, item, output_dir, kb_id, tenant_id, type_filter):
    """Process one policy item: get detail, download attachments, save, upload."""
    item_id = item['id']
    item_name = item.get('name', '无标题')
    _safe_print(f"\n  [{item_id[:16]}...] {item_name[:80]}")
    _safe_print(f"  Date: {item.get('dispatchTime', 'N/A')}")

    # Convert embedded HTML content to plain text
    content_html = item.get('content', '')
    content_text = _html_to_text(content_html)
    _safe_print(f"  Content: {len(content_text)} chars")

    # Fetch detail to get fileList (attachments)
    _request_delay(*_ITEM_DELAY)
    detail_data = _fetch_detail(sess, item_id, type_filter)

    # Build item record with combined data
    item_record = {
        'id': item_id,
        'name': item_name,
        'dispatchTime': item.get('dispatchTime', ''),
        'dispatchNumber': item.get('dispatchNumber', ''),
        'dispatchType': item.get('dispatchType', ''),
        'recordUnit': item.get('recordUnit', ''),
        'recordTime': item.get('recordTime', ''),
        'content_text': content_text,
        'attach_texts': [],
    }

    # Download attachments
    file_list = []
    if detail_data:
        file_list = detail_data.get('fileList', [])
        # Also enrich from detail data
        if not item_record['dispatchNumber']:
            item_record['dispatchNumber'] = detail_data.get('dispatchNumber', '')
        if not item_record['dispatchType']:
            item_record['dispatchType'] = detail_data.get('dispatchType', '')
        if not item_record['recordUnit']:
            item_record['recordUnit'] = detail_data.get('recordUnit', '')

    if file_list:
        safe_key = _sanitize_filename(item_id, 80)
        attachments_dir = os.path.join(output_dir, "attachments", safe_key)
        os.makedirs(attachments_dir, exist_ok=True)

        for file_info in file_list:
            file_name = file_info.get('fileName', 'attachment')
            file_id = file_info.get('id', '')
            if not file_id:
                continue

            _safe_print(f"  Downloading: {file_name[:60]}")
            download_url = _build_file_download_url(file_id)
            data = _download_file(sess, download_url)

            if data:
                fname = _sanitize_filename(file_name, 100)
                fpath = os.path.join(attachments_dir, fname)
                with open(fpath, 'wb') as f:
                    f.write(data)
                _safe_print(f"    OK ({len(data):,} bytes)")

                ext_text = _extract_file_text(fpath)
                if ext_text:
                    item_record['attach_texts'].append((file_name, ext_text))
                    _safe_print(f"    Extracted: {len(ext_text)} chars")

                if fname.lower().endswith('.zip'):
                    extracted = _extract_zip(fpath, attachments_dir)
                    for ext_file in extracted:
                        if os.path.isfile(ext_file):
                            ext_text2 = _extract_file_text(ext_file)
                            if ext_text2:
                                basename = os.path.basename(ext_file)
                                item_record['attach_texts'].append((basename, ext_text2))
            else:
                _safe_print(f"    Download FAILED")

    md_content = _build_markdown(item_record, detail_data)
    md_path = _save_markdown(md_content, output_dir, item_id)
    _safe_print(f"  Markdown: {md_path}")

    if kb_id:
        try:
            _upload_to_kb(md_path, kb_id, tenant_id)
        except Exception as e:
            logging.error("Upload failed: %s", e)

    return True


# ---------------------------------------------------------------------------
# Main crawl logic
# ---------------------------------------------------------------------------

def crawl(output_dir, kb_id, tenant_id, target_url, max_runtime=3300):
    start_time = time.time()
    type_filter = _extract_type_param(target_url)

    type_label = {0: "施工政策法规", "0": "施工政策法规",
                  1: "监理政策法规", "1": "监理政策法规"}.get(type_filter, f"type={type_filter}")

    _safe_print("=" * 60)
    _safe_print(f"HWDMS Policy Crawler — {_SITE_NAME}")
    _safe_print(f"Category: {type_label} | Target: {target_url}")
    _safe_print(f"Start: {_now_str()}")
    _safe_print("=" * 60)

    os.makedirs(output_dir, exist_ok=True)
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))

    sess = _init_session(target_url)

    # ── Step 1: Fetch first page to get total count ──────────────────────
    _safe_print("\n--- Fetching list page 1 ---")
    first_page = _fetch_list_page(sess, 1, _PAGE_SIZE, type_filter)
    if not first_page or first_page.get("code") != 0:
        _safe_print("ERROR: Failed to fetch list page 1")
        return

    page_obj = first_page.get("pageObj", {})
    total_count = page_obj.get("countSize", 0)
    total_pages = page_obj.get("maxPage", 0)
    rows = first_page.get("rows", [])

    _safe_print(f"Total: {total_count} items | Pages: {total_pages} (size={_PAGE_SIZE})")

    # ── Step 2: Collect all items from all pages ─────────────────────────
    all_items = list(rows)
    if total_pages > 1:
        for page_num in range(2, total_pages + 1):
            _request_delay(*_PAGE_DELAY)
            _safe_print(f"  Fetching page {page_num}/{total_pages}...")
            page_data = _fetch_list_page(sess, page_num, _PAGE_SIZE, type_filter)
            if page_data and page_data.get("code") == 0:
                all_items.extend(page_data.get("rows", []))
                _safe_print(f"    Got {len(page_data.get('rows', []))} items")

    _safe_print(f"\nTotal collected: {len(all_items)} items")

    # ── Step 3: Deduplicate ─────────────────────────────────────────────
    new_items = [it for it in all_items if it.get('id') not in processed_ids]
    _safe_print(f"New: {len(new_items)} | Already processed: {len(all_items) - len(new_items)}")

    if not new_items:
        _safe_print("No new items to process — done.")
        return

    # ── Step 4: Process each item with timeout awareness ─────────────────
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Processing {len(new_items)} new items...")
    _safe_print(f"{'='*60}")

    total_processed = 0
    for idx, item in enumerate(new_items, 1):
        # ── Time-bounded check ─────────────────────────────────────────
        elapsed = time.time() - start_time
        remaining = max_runtime - elapsed
        grace = min(120, max_runtime * 0.05)
        if remaining < grace:
            _safe_print(
                f"\nTimeout approaching ({elapsed:.0f}s elapsed, "
                f"{remaining:.0f}s remaining < {grace:.0f}s grace) — "
                f"stopping. Processed {total_processed} items."
            )
            break

        _safe_print(f"\n[{idx}/{len(new_items)}] ({elapsed:.0f}s elapsed)")
        success = _process_item(sess, item, output_dir, kb_id, tenant_id, type_filter)

        if success:
            processed_ids.add(item['id'])
            state["processed_ids"] = list(processed_ids)
            total_processed += 1
            # Checkpoint every 10 items
            if total_processed % 10 == 0:
                _save_state(output_dir, state)

        _request_delay(*_ITEM_DELAY)

    # ── Final state save ─────────────────────────────────────────────────
    _save_state(output_dir, state)

    elapsed = time.time() - start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Crawl complete. {total_processed} new items in {elapsed:.0f}s")
    _safe_print(f"Total IDs tracked: {len(processed_ids)}")
    _safe_print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="HWDMS Policy Crawler — mot.gov.cn 政策法规"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--target-url",
                        default=f"{_POLICY_INDEX}?type=0",
                        help="Target URL (default: %(default)s)")
    parser.add_argument("--kb-id", default=None, help="Knowledge base ID")
    parser.add_argument("--task-name", default="hwdms_policy_crawler",
                        help="Task name")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Maximum runtime in seconds (default: 3300)")
    parser.add_argument("--project-root", default=None, help="Project root")

    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, args.project_root)
        os.chdir(args.project_root)

    output_dir = args.output_dir or os.path.join(_SCRIPT_DIR, args.task_name)
    os.makedirs(output_dir, exist_ok=True)

    init_root_logger("hwdms_policy_crawler")
    logging.info("HWDMS Policy Crawler | task=%s | output=%s", args.task_name, output_dir)

    try:
        crawl(
            output_dir=output_dir,
            kb_id=args.kb_id,
            tenant_id=args.tenant_id,
            target_url=args.target_url,
            max_runtime=args.max_runtime,
        )
    except KeyboardInterrupt:
        _safe_print("\nInterrupted by user")
        logging.info("Interrupted by user")
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        _safe_print(f"\nFATAL: {e}")
        raise


if __name__ == "__main__":
    CONSUMER_NAME = "hwdms_policy_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
