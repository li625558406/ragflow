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
Dedicated web crawler for mohurd.gov.cn (中华人民共和国住房和城乡建设部).

Crawls content from the government information disclosure portal using the
TRS WCM unitbuild API backend.

Site characteristics
--------------------
  • TRS WCM CMS — list content loaded via AJAX from unitbuild API.
  • API endpoint: /api-gateway/jpaas-publish-server/front/page/build/unit
  • Pagination via paramJson={"pageNo","pageSize","loadEnabled","search"}
  • Detail pages: standard HTML with <meta name="PubDate"> and TRS_Editor divs.
  • Attachments served via API gateway download endpoint.
  • 5 modules across 4 URLs (see _MODULES below).

Modules
-------
  1. 行政规范性文件 (zc/xzgfxwjk) — ALL ~510 items, full pagination
  2. 文件库 (zc/wjk) — today only (from ~18000 total)
  3. 文告 (zc/wgk) — today only (from ~39 total)
  4. 国务院及有关部门 (gkgd/gwyjygbm) — ALL items
  5. 住房和城乡建设部 (gkgd/zfhcxjsb) — ALL items

Usage (typically spawned by task_executor):
    python mohurd_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.mohurd.gov.cn/gongkai/zc/index.html \
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
import urllib.request
import zipfile
from urllib.parse import unquote, urljoin, urlparse

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
_SITE_ROOT = "https://www.mohurd.gov.cn"
_SITE_NAME = "住房和城乡建设部"

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

# TRS unitbuild API constants
_API_URL = f"{_SITE_ROOT}/api-gateway/jpaas-publish-server/front/page/build/unit"
_WEB_ID = "86ca573ec4df405db627fdc2493677f3"
_TPLSET_ID = "fc259c381af3496d85e61997ea7771cb"

# Module definitions
# (label, listing_url, col_id, tag_id, crawl_mode)
# crawl_mode: "all" = paginate all items; "today" = stop when no more today items
_MODULES = [
    # 1. 行政规范性文件
    ("行政规范性文件",
     f"{_SITE_ROOT}/gongkai/zc/xzgfxwjk/index.html",
     "CddoJMk2fUTffhM06m29m", "内容1",
     "all"),

    # 2. 文件库 — today only
    ("文件库",
     f"{_SITE_ROOT}/gongkai/zc/wjk/index.html",
     "vhiC3JxmPC8o7Lqg4Jw0E", "内容1",
     "today"),

    # 3. 文告 — today only
    ("文告",
     f"{_SITE_ROOT}/gongkai/zc/wgk/index.html",
     "2b9996f111a0454ebc4a278e9ae92571", "内容1",
     "today"),

    # 4. 国务院及有关部门政府信息公开制度
    ("国务院及有关部门政府信息公开制度",
     f"{_SITE_ROOT}/gongkai/gkgd/gwyjygbm/index.html",
     "e4a041cb9eba4fed8ffa5440289dc0bc", "信息公开制度-list",
     "all"),

    # 5. 住房和城乡建设部政府信息公开制度
    ("住房和城乡建设部政府信息公开制度",
     f"{_SITE_ROOT}/gongkai/gkgd/zfhcxjsb/index.html",
     "f633695139cc4f19a1d78ec9d6a425d8", "信息公开制度-list",
     "all"),
]

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


def _is_download_url(url):
    return "/api-gateway/" in url and "/document/download" in url


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session():
    sess = _requests.Session()
    sess.headers.update(_HTML_HEADERS)
    sess.verify = False
    try:
        sess.get(_SITE_ROOT, timeout=30, verify=False)
        logging.info("Session initialized")
    except Exception as e:
        logging.warning("Failed to init session: %s", e)
    return sess


# ---------------------------------------------------------------------------
# API-based listing
# ---------------------------------------------------------------------------

def _call_list_api(sess, col_id, tag_id, page_no, page_size):
    """Call the TRS unitbuild API for a column listing page.

    Returns (BeautifulSoup of HTML fragment, total_count).
    """
    params = {
        'webId': _WEB_ID,
        'pageId': col_id,
        'parseType': 'bulidstatic',
        'pageType': 'column',
        'tagId': tag_id,
        'tplSetId': _TPLSET_ID,
        'unitUrl': _API_URL,
        'editType': 'null',
        'paramJson': json.dumps({
            'pageNo': page_no,
            'pageSize': page_size,
            'loadEnabled': True,
            'search': '{}',
        }),
    }
    try:
        r = sess.get(_API_URL, params=params, timeout=60, verify=False)
        data = r.json()
        html = data.get('data', {}).get('html', '')
        if not html:
            return None, 0
        soup = BeautifulSoup(html, 'lxml')
        total = 0
        pag_div = soup.find('div', id=lambda x: x and 'pagination' in x)
        if pag_div:
            qd = pag_div.get('querydata', '')
            m = re.search(r"'count'\s*:\s*'(\d+)'", qd)
            if m:
                total = int(m.group(1))
        if not total:
            # Use page items count as fallback
            items = _parse_list_items(soup, "")
            total = page_no * page_size + (1 if len(items) == page_size else 0)
        return soup, total
    except Exception as e:
        logging.error("API call failed for col=%s page=%d: %s", col_id, page_no, e)
        return None, 0


def _parse_list_items(soup, listing_url):
    """Extract list items from an API-rendered listing page.

    Handles two formats:
      1. Table rows (xzgfxwjk, wjk) — <tr> with <td> cells
      2. List items (wgk, gkgd) — <li> with <a> tags

    Returns list of dicts: {title, date (YYYY-MM-DD), href (absolute URL)}.
    """
    items = []

    # Format 1: Table rows
    for tr in soup.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 2:
            continue
        a = tr.find('a', href=True)
        if not a:
            continue
        href = a.get('href', '')
        if not href or 'art/' not in href:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        date_str = ''
        for td in reversed(tds):
            text = td.get_text(strip=True)
            m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', text)
            if m:
                date_str = m.group(1)
                break
        if not date_str:
            m = re.search(r'/art/(\d{4})/', href)
            if m:
                date_str = m.group(1)

        abs_url = urljoin(listing_url, href)
        items.append({'title': title, 'date': date_str, 'href': abs_url})

    # Format 2: List items (for wgk and gkgd)
    if not items:
        for li in soup.find_all('li'):
            a = li.find('a', href=True)
            if not a:
                continue
            href = a.get('href', '')
            if not href or 'art/' not in href:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue

            full_text = li.get_text(strip=True)
            date_str = ''
            m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', full_text)
            if m:
                date_str = m.group(1)
            if not date_str:
                m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', full_text)
                if m:
                    date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if not date_str:
                m = re.search(r'/art/(\d{4})/', href)
                if m:
                    date_str = m.group(1)

            abs_url = urljoin(listing_url, href)
            items.append({'title': title, 'date': date_str, 'href': abs_url})

    return items


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail(html, detail_url):
    """Extract content and attachments from a TRS CMS detail page.

    Returns dict: {title, pub_date, content_text, attachments: [(name, url)]}.
    """
    soup = BeautifulSoup(html, 'lxml')
    result = {'title': '', 'pub_date': '', 'content_text': '', 'attachments': []}

    # Title
    meta_title = soup.find('meta', attrs={'name': 'ArticleTitle'})
    if meta_title and meta_title.get('content'):
        result['title'] = meta_title['content'].strip()
    if not result['title']:
        title_el = soup.find('title')
        if title_el:
            raw_title = title_el.get_text(strip=True)
            result['title'] = raw_title.split('_')[0].strip()

    # PubDate
    for meta_name in ('PubDate', 'publishdate', 'articledate', 'dc.date'):
        meta = soup.find('meta', attrs={'name': meta_name})
        if meta and meta.get('content'):
            result['pub_date'] = meta['content'].strip()[:10]
            break

    if not result['pub_date']:
        for el in soup.find_all(['span', 'div', 'p'],
                                string=re.compile(r'\d{4}-\d{2}-\d{2}')):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', el.get_text())
            if m:
                result['pub_date'] = m.group(1)
                break

    # Content extraction
    content_div = None
    for selector in [
        {'class_': re.compile(r'TRS_Editor|Custom_UnionStyle|TRS_PreAppend|'
                              r'article_con|article_content')},
        {'id': re.compile(r'article|content|detail|text|zoom|con', re.I)},
        {'class_': re.compile(r'content|text_con|body_con|detail_con|'
                              r'pages_content', re.I)},
    ]:
        content_div = soup.find('div', **selector)
        if content_div:
            break

    if not content_div:
        max_len = 0
        for div in soup.find_all('div'):
            if div.find_parent(['header', 'nav', 'footer', 'script', 'style']):
                continue
            text = div.get_text(strip=True)
            if 200 < len(text) < 50000 and len(text) > max_len:
                content_div = div
                max_len = len(text)

    if content_div:
        for tag in content_div.find_all(['script', 'style']):
            tag.decompose()
        text = content_div.get_text(separator='\n', strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        result['content_text'] = text

    # Attachments
    seen_urls = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        abs_url = urljoin(detail_url, href)

        if _is_download_url(abs_url) and abs_url not in seen_urls:
            seen_urls.add(abs_url)
            att_name = a.get_text(strip=True)
            if not att_name:
                m = re.search(r'fileName=([^&]+)', href)
                if m:
                    att_name = unquote(m.group(1))
                else:
                    att_name = os.path.basename(urlparse(href).path) or "attachment"
            result['attachments'].append((att_name, abs_url))
        elif _is_attach_url(abs_url) and abs_url not in seen_urls:
            seen_urls.add(abs_url)
            att_name = a.get_text(strip=True) or \
                       os.path.basename(urlparse(abs_url).path)
            result['attachments'].append((att_name, abs_url))

    return result


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=120):
    """Download a binary file, returning bytes or None."""
    parsed = urlparse(file_url)
    main_parsed = urlparse(_SITE_ROOT)

    if parsed.netloc == main_parsed.netloc or not parsed.netloc:
        try:
            resp = sess.get(file_url, timeout=timeout, stream=True, verify=False)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            logging.error("Download error %s: %s", file_url, e)
        return None

    req = urllib.request.Request(file_url, headers=_HTML_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if len(data) > 100:
            return data
    except Exception as e:
        logging.error("Download error (external) %s: %s", file_url, e)
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
    """Extract text from PDF/DOC/DOCX/XLS/XLSX files."""
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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(module_name, item, detail, attachments_dir):
    lines = [
        f"# {detail.get('title', item['title'])}",
        "",
        f"**来源**: {_SITE_NAME}",
        f"**栏目**: {module_name}",
    ]
    pub_date = detail.get('pub_date') or item.get('date')
    if pub_date:
        lines.append(f"**发布日期**: {pub_date}")
    lines.append(f"**原文链接**: {item['href']}")

    lines.append("")
    lines.append("---")
    lines.append("")

    if detail.get('content_text'):
        lines.append(detail['content_text'])
    else:
        lines.append("(无法提取正文内容)")

    if attachments_dir and os.path.isdir(attachments_dir):
        files = [f for f in os.listdir(attachments_dir)
                 if os.path.isfile(os.path.join(attachments_dir, f))]
        if files:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## 附件")
            lines.append("")
            for fname in sorted(files):
                fpath = os.path.join(attachments_dir, fname)
                fsize = os.path.getsize(fpath)
                lines.append(f"- **{fname}** ({fsize:,} bytes)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single item processing
# ---------------------------------------------------------------------------

def _process_item(sess, module_name, item, output_dir, kb_id, tenant_id):
    """Fetch detail page, download attachments, save markdown, upload to KB."""
    item_id = item['href']
    _safe_print(f"\n  [{module_name}] {item['title'][:80]}")
    _safe_print(f"  {item['href'][:150]}")

    attachments_dir = None

    try:
        r = sess.get(item['href'], timeout=60, verify=False)
        if r.status_code != 200:
            _safe_print(f"  HTTP {r.status_code} — skipped")
            return False
        r.encoding = 'utf-8'
        html = r.text
    except Exception as e:
        _safe_print(f"  Fetch error: {e}")
        return False

    detail = _parse_detail(html, item['href'])
    _safe_print(f"  Date: {detail.get('pub_date', item.get('date', 'N/A'))} | "
                f"Content: {len(detail['content_text'])} chars")

    if detail['attachments']:
        safe_key = _sanitize_filename(item_id, 100)
        attachments_dir = os.path.join(output_dir, "attachments", safe_key)
        os.makedirs(attachments_dir, exist_ok=True)
        for att_name, att_url in detail['attachments']:
            _safe_print(f"  Downloading: {att_name[:60]}")
            data = _download_file(sess, att_url)
            if data:
                fname = _sanitize_filename(att_name, 100)
                if '.' not in fname:
                    m2 = re.search(r'fileName=([^&]+)', att_url)
                    if m2:
                        url_fname = unquote(m2.group(1))
                        ext = os.path.splitext(url_fname)[1]
                        if ext:
                            fname += ext
                fpath = os.path.join(attachments_dir, fname)
                with open(fpath, 'wb') as f:
                    f.write(data)
                _safe_print(f"    OK ({len(data):,} bytes)")
                if fname.lower().endswith('.zip'):
                    _extract_zip(fpath, attachments_dir)
            else:
                _safe_print(f"    FAILED")

    if not detail['pub_date'] and item.get('date'):
        detail['pub_date'] = item['date']

    md_content = _build_markdown(module_name, item, detail, attachments_dir)
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

def crawl(target_url, output_dir, kb_id, tenant_id, max_runtime=3300):
    start_time = time.time()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    _safe_print("=" * 60)
    _safe_print(f"MOHURD Crawler | Today: {today_str}")
    _safe_print(f"Target: {target_url}")
    _safe_print(f"Modules: {len(_MODULES)}")
    for mod_name, mod_url, col_id, tag_id, mode in _MODULES:
        _safe_print(f"  - {mod_name} [{mode}]")
    _safe_print("=" * 60)

    os.makedirs(output_dir, exist_ok=True)
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))

    sess = _init_session()
    total_processed = 0

    for mod_name, mod_url, col_id, tag_id, mode in _MODULES:
        remaining = max_runtime - (time.time() - start_time)
        grace = min(120, max_runtime * 0.05)
        if remaining < grace:
            _safe_print(f"\nTimeout approaching ({remaining:.0f}s left) — stopping")
            break

        _safe_print(f"\n{'='*50}")
        _safe_print(f"Module: {mod_name} [{mode}]")

        page_no = 1
        page_size = 10
        total_for_mod = 0
        consecutive_empty = 0

        while True:
            remaining = max_runtime - (time.time() - start_time)
            if remaining < grace:
                _safe_print(f"  Timeout ({remaining:.0f}s) — stopping module")
                break

            soup, total = _call_list_api(sess, col_id, tag_id, page_no, page_size)
            if soup is None:
                _safe_print(f"  API failed on page {page_no}")
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    _safe_print(f"  Too many failures — skipping module")
                    break
                page_no += 1
                continue

            items = _parse_list_items(soup, mod_url)
            if not items:
                _safe_print(f"  Page {page_no}: no items — end of list")
                break

            consecutive_empty = 0
            _safe_print(f"  Page {page_no}: {len(items)} items (total={total})")

            # For "today" mode: stop when latest date < today
            no_today_count = 0
            if mode == "today":
                dates = sorted(set(it['date'] for it in items if it['date']),
                               reverse=True)
                latest = dates[0] if dates else 'N/A'
                has_today = today_str in dates
                if not has_today:
                    no_today_count += 1
                    _safe_print(f"    No today items on page {page_no}. "
                                f"Latest: {latest}")
                    if latest != 'N/A' and latest < today_str:
                        _safe_print(f"    Past today — stopping")
                        break
                    if no_today_count >= 5:
                        _safe_print(f"    No today items for 5 pages — stopping")
                        break
                else:
                    no_today_count = 0

            for item in items:
                remaining = max_runtime - (time.time() - start_time)
                if remaining < grace:
                    _safe_print(f"  Timeout — stopping")
                    break

                if mode == "today" and item['date'] != today_str:
                    continue

                if item['href'] in processed_ids:
                    continue

                success = _process_item(sess, mod_name, item, output_dir,
                                        kb_id, tenant_id)
                if success:
                    processed_ids.add(item['href'])
                    state["processed_ids"] = list(processed_ids)
                    total_processed += 1
                    total_for_mod += 1
                    if total_processed % 5 == 0:
                        _save_state(output_dir, state)

                _request_delay(*_ARTICLE_DELAY)

            if total > 0 and page_no * page_size >= total:
                _safe_print(f"    Reached total ({total}) — end of list")
                break

            page_no += 1
            _request_delay(*_PAGE_DELAY)

        _safe_print(f"  Module processed: {total_for_mod}")

    _save_state(output_dir, state)

    elapsed = time.time() - start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Crawl complete. {total_processed} new items in {elapsed:.0f}s")
    _safe_print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MOHURD Crawler — 住房和城乡建设部 政府信息公开"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--target-url",
                        default=f"{_SITE_ROOT}/gongkai/zc/index.html",
                        help="Target URL")
    parser.add_argument("--kb-id", default=None, help="Knowledge base ID")
    parser.add_argument("--task-name", default="mohurd_crawler", help="Task name")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Maximum runtime in seconds (default: 3300)")
    parser.add_argument("--project-root", default=None, help="Project root")
    parser.add_argument("--modules", default=None,
                        help="Comma-separated 1-based module indexes (e.g. '1,2')")

    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, args.project_root)
        os.chdir(args.project_root)

    output_dir = args.output_dir or os.path.join(_SCRIPT_DIR, args.task_name)
    os.makedirs(output_dir, exist_ok=True)

    init_root_logger("mohurd_crawler")
    logging.info("MOHURD Crawler | task=%s | output=%s", args.task_name, output_dir)

    global _MODULES
    if args.modules:
        indices = [int(x.strip()) - 1 for x in args.modules.split(",")]
        _MODULES = [_MODULES[i] for i in indices if 0 <= i < len(_MODULES)]
        logging.info("Filtered modules: %s", [m[0] for m in _MODULES])

    try:
        crawl(
            target_url=args.target_url,
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
    CONSUMER_NAME = "mohurd_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
