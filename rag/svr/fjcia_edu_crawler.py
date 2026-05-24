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
Dedicated web crawler for fjcia.org/education (福建省建筑业协会 教育培训).

Crawls the 招投标业务培训 page which lists all training/education articles
with full content embedded in the Nuxt SSR payload.

Site characteristics
────────────────────
  • Nuxt.js SSR — all content in __NUXT_DATA__ JSON payload.
  • Single page — 3 items, no pagination, no separate detail pages.
  • Full article HTML embedded in newsContent field of each list item.
  • Attachments: contentPdf field (PDF URL), contentWord field (DOC URL).
  • No WAF/captcha — standard HTTP requests with proper User-Agent.

Usage (typically spawned by task_executor):
    python fjcia_edu_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url http://www.fjcia.org/education \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import datetime
import html as html_lib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.request
import zipfile
from urllib.parse import urljoin, urlparse

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
_BASE = "http://www.fjcia.org"
_SITE_NAME = "福建省建筑业协会"
_EDU_URL = f"{_BASE}/education"

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

# Anti-crawling delays (seconds)
_PAGE_DELAY = (1.0, 2.5)
_ARTICLE_DELAY = (0.3, 1.0)

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


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session():
    sess = _requests.Session()
    sess.headers.update(_HTML_HEADERS)
    sess.verify = False
    try:
        sess.get(_BASE, timeout=30, verify=False)
        logging.info("Session initialized")
    except Exception as e:
        logging.warning("Failed to init session: %s", e)
    return sess


# ---------------------------------------------------------------------------
# Nuxt payload extraction
# ---------------------------------------------------------------------------

def _extract_nuxt_payload(html):
    """Extract and parse the __NUXT_DATA__ payload from Nuxt SSR HTML."""
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logging.error("Failed to parse __NUXT_DATA__: %s", e)
        return None


def _resolve_nuxt_value(payload, ref, depth=0, seen=None):
    """Safely resolve a reference in the Nuxt payload.

    Integer values are indices into the payload array.
    Lists starting with "Reactive"/"NuxtError" are special markers.
    """
    if seen is None:
        seen = set()
    if depth > 30:
        return None
    if isinstance(ref, int):
        if ref < 0 or ref >= len(payload):
            return None
        if ref in seen:
            return None
        seen.add(ref)
        return _resolve_nuxt_value(payload, payload[ref], depth + 1, seen)
    elif isinstance(ref, list):
        # Skip markers like ["Reactive", N], ["NuxtError", ...], ["ShallowRef", N]
        if ref and isinstance(ref[0], str) and ref[0] in ('Reactive', 'NuxtError', 'ShallowRef'):
            target = ref[1] if len(ref) > 1 else None
            return _resolve_nuxt_value(payload, target, depth + 1, seen)
        return [_resolve_nuxt_value(payload, item, depth + 1, seen) for item in ref]
    elif isinstance(ref, dict):
        return {k: _resolve_nuxt_value(payload, v, depth + 1, seen) for k, v in ref.items()}
    return ref


def _fetch_all_items(sess):
    """Parse the education page and extract all article items.

    The Nuxt SSR page embeds all data in the __NUXT_DATA__ payload.
    We walk the reference chain: data[2] → infolist → list → items.

    Returns list of dicts: {news_code, title, date, content_html, pdf_url, ...}
    """
    _safe_print(f"Fetching: {_EDU_URL}")
    r = sess.get(_EDU_URL, timeout=60, verify=False)
    r.encoding = 'utf-8'

    payload = _extract_nuxt_payload(r.text)
    if not payload:
        _safe_print("ERROR: Could not extract __NUXT_DATA__")
        return []

    # The payload structure:
    #   payload[2] = page component data dict
    #   payload[2]['infolist'] = int ref → payload[N] = {'list': ref, 'total': ref}
    #   payload[N]['list'] = int ref → payload[M] = list of item refs

    page_data = payload[2]
    infolist_ref = page_data.get('infolist')
    if not infolist_ref or not isinstance(infolist_ref, int):
        _safe_print("ERROR: Could not find infolist in payload")
        return []

    infolist = _resolve_nuxt_value(payload, infolist_ref)
    if not isinstance(infolist, dict):
        _safe_print(f"ERROR: infolist is {type(infolist).__name__}, expected dict")
        return []

    items_list = infolist.get('list', [])
    total = infolist.get('total', 0)

    _safe_print(f"  Found {len(items_list)} items (total={total})")

    articles = []
    for item_data in items_list:
        if not isinstance(item_data, dict):
            continue

        news_code = item_data.get('newsCode', '')
        title = item_data.get('newsTitle', '')
        content_html = item_data.get('newsContent', '')
        pub_date = item_data.get('publishTimeWeb', '')
        pdf_url = item_data.get('contentPdf', '')
        # Some items may have Word attachments in a different field
        word_url = item_data.get('contentWord', '')
        column_code = item_data.get('columnCode', '')

        if not title:
            continue

        # Collect attachment URLs
        attach_urls = []
        if pdf_url and isinstance(pdf_url, str) and pdf_url.strip():
            attach_urls.append(('pdf_attachment', pdf_url))
        if word_url and isinstance(word_url, str) and word_url.strip():
            attach_urls.append(('word_attachment', word_url))

        # Also scan content HTML for embedded attachment links
        if content_html:
            soup = BeautifulSoup(content_html, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a.get('href', '').strip()
                if not href:
                    continue
                abs_url = urljoin(_EDU_URL, href)
                if _is_attach_url(abs_url):
                    att_name = a.get_text(strip=True) or os.path.basename(urlparse(abs_url).path)
                    attach_urls.append((att_name, abs_url))

        articles.append({
            'news_code': news_code,
            'title': title,
            'date': pub_date,
            'content_html': content_html or '',
            'column_code': column_code,
            'attach_urls': attach_urls,
        })

    return articles


# ---------------------------------------------------------------------------
# HTML to text conversion
# ---------------------------------------------------------------------------

def _html_to_text(html_content):
    """Convert HTML content to plain text with basic formatting."""
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, 'lxml')

    # Remove scripts and styles
    for tag in soup.find_all(['script', 'style']):
        tag.decompose()

    # Replace common block elements with newlines
    for tag_name in ['p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr']:
        for tag in soup.find_all(tag_name):
            tag.insert_after(soup.new_string('\n'))

    text = soup.get_text(separator=' ', strip=True)
    # Clean up extra whitespace
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\n +', '\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=120):
    """Download a file. Handles URLs with non-ASCII characters via proper encoding."""
    from urllib.parse import quote as _url_quote

    # Properly encode URL with non-ASCII characters
    encoded_url = _url_quote(file_url, safe=":/?&=#%")

    parsed = urlparse(file_url)
    main_parsed = urlparse(_BASE)

    if parsed.netloc == main_parsed.netloc or not parsed.netloc:
        try:
            resp = sess.get(encoded_url, timeout=timeout, stream=True, verify=False)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            logging.error("Download error %s: %s", encoded_url, e)
        return None

    try:
        resp = sess.get(encoded_url, timeout=timeout, stream=True, verify=False)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        logging.error("Download error (external) %s: %s", encoded_url, e)
    return None


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
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs)", len(state.get("processed_ids", [])))


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
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(article, detail):
    lines = [
        f"# {article['title']}",
        "",
        f"**来源**: {_SITE_NAME} — 招投标业务培训",
    ]
    if detail.get('pub_date'):
        lines.append(f"**发布日期**: {detail['pub_date']}")
    elif article.get('date'):
        lines.append(f"**发布日期**: {article['date']}")
    lines.append(f"**原文链接**: {_EDU_URL}")
    if article.get('news_code'):
        lines.append(f"**文章ID**: {article['news_code']}")

    lines.append("")
    lines.append("---")
    lines.append("")

    if detail.get('content_text'):
        lines.append(detail['content_text'])
    else:
        lines.append("(无法提取正文内容)")

    if detail.get('attach_texts'):
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附件内容")
        lines.append("")
        for att_name, att_text in detail['attach_texts']:
            if att_text:
                lines.append(f"### {att_name}")
                lines.append("")
                lines.append(att_text)
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single article processing
# ---------------------------------------------------------------------------

def _process_article(sess, article, output_dir, kb_id, tenant_id):
    """Process one article: convert HTML to text, download attachments, save, upload."""
    _safe_print(f"\n  {article['title'][:80]}")
    _safe_print(f"  Date: {article.get('date', '')} | Code: {article.get('news_code', '')}")

    # Convert embedded HTML content to plain text
    content_text = _html_to_text(article.get('content_html', ''))
    _safe_print(f"  Content: {len(content_text)} chars")

    # Download attachments
    attach_texts = []
    attachments_dir = None

    if article.get('attach_urls'):
        safe_key = _sanitize_filename(article.get('news_code', article['title']), 80)
        attachments_dir = os.path.join(output_dir, "attachments", safe_key)
        os.makedirs(attachments_dir, exist_ok=True)

        for att_name, att_url in article['attach_urls']:
            _safe_print(f"  Downloading: {att_name[:60]}")
            data = _download_file(sess, att_url)
            if data:
                fname = _sanitize_filename(att_name, 100)
                fpath = os.path.join(attachments_dir, fname)
                with open(fpath, 'wb') as f:
                    f.write(data)
                _safe_print(f"    OK ({len(data):,} bytes)")

                # Extract text from attachment
                ext_text = _extract_file_text(fpath)
                if ext_text:
                    attach_texts.append((att_name, ext_text))
                    _safe_print(f"    Extracted: {len(ext_text)} chars")

                # Handle ZIP
                if fname.lower().endswith('.zip'):
                    extracted = _extract_zip(fpath, attachments_dir)
                    for ext_file in extracted:
                        if os.path.isfile(ext_file):
                            ext_text2 = _extract_file_text(ext_file)
                            if ext_text2:
                                basename = os.path.basename(ext_file)
                                attach_texts.append((basename, ext_text2))
            else:
                _safe_print(f"    Download FAILED")

    detail = {
        'pub_date': article.get('date', ''),
        'content_text': content_text,
        'attach_texts': attach_texts,
    }

    md_content = _build_markdown(article, detail)
    md_path = _save_markdown(md_content, output_dir, article.get('news_code', article['title']))
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

def crawl(output_dir, kb_id, tenant_id, max_runtime=3300):
    start_time = time.time()

    _safe_print("=" * 60)
    _safe_print(f"FJCIA Edu Crawler — {_SITE_NAME} 招投标业务培训")
    _safe_print(f"Target: {_EDU_URL}")
    _safe_print("=" * 60)

    os.makedirs(output_dir, exist_ok=True)
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))

    sess = _init_session()

    # Fetch all items from the Nuxt SSR page
    _safe_print("\n--- Parsing education page (Nuxt SSR) ---")
    articles = _fetch_all_items(sess)

    # Filter already processed
    new_articles = [a for a in articles if a['news_code'] not in processed_ids]
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Total articles: {len(articles)} | New: {len(new_articles)} | "
                f"Already processed: {len(articles) - len(new_articles)}")
    _safe_print(f"{'='*60}")

    total_processed = 0
    for article in new_articles:
        remaining = max_runtime - (time.time() - start_time)
        grace = min(120, max_runtime * 0.05)
        if remaining < grace:
            _safe_print(f"\nTimeout approaching ({remaining:.0f}s < {grace:.0f}s) — stopping")
            break

        success = _process_article(sess, article, output_dir, kb_id, tenant_id)
        if success:
            processed_ids.add(article['news_code'])
            state["processed_ids"] = list(processed_ids)
            total_processed += 1
            if total_processed % 10 == 0:
                _save_state(output_dir, state)

        _request_delay(*_ARTICLE_DELAY)

    _save_state(output_dir, state)

    elapsed = time.time() - start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Crawl complete. {total_processed} new articles in {elapsed:.0f}s")
    _safe_print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="FJCIA Edu Crawler — fjcia.org 招投标业务培训"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--target-url", default=_EDU_URL,
                        help="Target URL (default: %(default)s)")
    parser.add_argument("--kb-id", default=None, help="Knowledge base ID")
    parser.add_argument("--task-name", default="fjcia_edu_crawler",
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

    init_root_logger("fjcia_edu_crawler")
    logging.info("FJCIA Edu Crawler | task=%s | output=%s", args.task_name, output_dir)

    try:
        crawl(
            output_dir=output_dir,
            kb_id=args.kb_id,
            tenant_id=args.tenant_id,
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
    CONSUMER_NAME = "fjcia_edu_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
