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
Dedicated web crawler for Fujian Finance Dept — 解读回应 (czt.fujian.gov.cn/jdhy/).

Crawls all content modules from the jdhy page, filtering items published
today by the date column in each listing.

Site characteristics
────────────────────
  • SSR HTML — all listing and detail pages are server-rendered.
  • 3 modules: 回应关切, 省财政厅政策文件解读, 其他相关解读
  • 政策文件解读 (zcjd) has 3 sub-category tabs: bmzcwjjd, spjd, tpjd
  • Listing → /jdhy/<module>/  — <li> items within list_base / viewlist divs.
  • Detail  → TRS CMS .htm pages with <meta name="PubDate">.
  • Date    → YYYY-MM-DD in li text, confirmed via meta PubDate on detail.
  • Auth    → none (public). Simple Session with User-Agent header.
  • SSL     → verify=False needed for some .gov.cn certificates.

Usage (typically spawned by task_executor):
    python czt_jdhy_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://czt.fujian.gov.cn/jdhy/ \\
        --kb-id <KB_ID> \\
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
_SITE_ROOT = "https://czt.fujian.gov.cn"
_MAIN_URL = f"{_SITE_ROOT}/jdhy/"
_SITE_NAME = "福建省财政厅"

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

_CZT_JDHY_MODULES = [
    ("回应关切", "/jdhy/hygq/"),
    ("省财政厅政策文件解读", "/jdhy/zcjd/"),
    ("其他相关解读", "/jdhy/qtzcwjjd/"),
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
# Module discovery
# ---------------------------------------------------------------------------

def _discover_modules(sess):
    """Discover content modules from the jdhy main page.

    Returns list of (module_name, listing_url) tuples.
    Falls back to _CZT_JDHY_MODULES if discovery fails.
    """
    modules = []

    try:
        r = sess.get(_MAIN_URL, timeout=30, verify=False)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        logging.warning("Module discovery failed: %s", e)
        return [(n, urljoin(_SITE_ROOT, p)) for n, p in _CZT_JDHY_MODULES]

    # Find module links on the jdhy page from sidebar nav
    for a in soup.find_all('a', href=re.compile(r'/jdhy/')):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 20:
            continue

        # Match known module patterns
        m = re.match(r'^(?:\.?/jdhy)?/(hygq|zcjd|qtzcwjjd)/?$', href)
        if not m:
            continue

        abs_url = urljoin(_MAIN_URL, href)
        known_urls = {url for _, url in modules}
        if abs_url not in known_urls:
            modules.append((text, abs_url))

    if not modules:
        logging.warning("No modules discovered, using fallback list")
        return [(n, urljoin(_SITE_ROOT, p)) for n, p in _CZT_JDHY_MODULES]

    logging.info("Discovered %d modules", len(modules))
    return modules


# ---------------------------------------------------------------------------
# Sub-category discovery (for modules with tabs, e.g. zcjd)
# ---------------------------------------------------------------------------

def _discover_subcategories(sess, module_url):
    """Discover sub-category listing URLs from a module page.

    The zcjd module has tabs (bmzcwjjd, spjd, tpjd) where each
    sub-category is a separate listing page.

    Returns list of (sub_name, sub_url) tuples.
    If no sub-categories found, returns [(None, module_url)] to use main page.
    """
    try:
        r = sess.get(module_url, timeout=60, verify=False)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        logging.warning("Sub-category discovery failed for %s: %s", module_url, e)
        return [(None, module_url)]

    subs = []
    seen = set()
    module_path = urlparse(module_url).path.rstrip('/')

    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        abs_href = urljoin(module_url, href)
        # Match: .../<module_path>/<sub_dir>/YYYYMM/tYYYYMMDD_NNNNNNN.htm
        m = re.search(
            rf'{re.escape(module_path)}/([a-z][a-z0-9_]{{2,}}/)\d{{6}}/t\d{{8}}_\d+\.htm',
            abs_href
        )
        if not m:
            continue
        sub_dir = m.group(1)
        if sub_dir in seen:
            continue
        seen.add(sub_dir)

        sub_url = urljoin(module_url, './' + sub_dir)
        sub_name = sub_dir.rstrip('/')
        subs.append((sub_name, sub_url))

    if not subs:
        return [(None, module_url)]

    logging.info("Discovered %d sub-categories for %s", len(subs), module_url)
    return [(None, module_url)] + subs


# ---------------------------------------------------------------------------
# Listing parsing
# ---------------------------------------------------------------------------

def _parse_listing(html, listing_url):
    """Parse a module listing page, return items with title, date, href.

    Returns list of dicts: {title, date (YYYY-MM-DD), href (absolute URL),
                            is_attachment (bool)}.
    """
    soup = BeautifulSoup(html, 'lxml')
    items = []

    list_div = soup.find('div', class_=re.compile(r'list_base|list_box|news_list|info_list'))
    if not list_div:
        list_div = soup.find('div', role='viewlist')
    if not list_div:
        list_div = soup.find('div', class_=re.compile(r'main|content_area|list_wrap|right_content'))

    lis = []
    if list_div:
        lis = list_div.find_all('li')
    if not lis:
        for ul in soup.find_all(['ul', 'ol']):
            ul_lis = ul.find_all('li', recursive=False)
            if len(ul_lis) > len(lis):
                lis = ul_lis

    for li in lis:
        a = li.find('a', href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get('href', '')
        if not title or len(title) < 4:
            continue
        if '更多' in title:
            continue
        if href.startswith('#') or href.startswith('javascript'):
            continue

        # Extract date from li text
        date_str = ''
        full_text = li.get_text(strip=True)

        # YYYY-MM-DD
        m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', full_text)
        if m:
            date_str = m.group(1)

        # Chinese date: "2026年5月20日"
        if not date_str:
            m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', full_text)
            if m:
                date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        # URL-embedded: /tYYYYMMDD_
        if not date_str:
            m = re.search(r'/t(\d{8})_', href)
            if m:
                d = m.group(1)
                date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

        # URL path segment: /YYYYMM/
        if not date_str:
            m = re.search(r'/(\d{4})(\d{2})/', href)
            if m and 2020 <= int(m.group(1)) <= 2030:
                date_str = f"{m.group(1)}-{m.group(2)}"

        abs_url = urljoin(listing_url, href)

        # Skip external links
        parsed = urlparse(abs_url)
        if parsed.netloc and parsed.netloc != urlparse(_SITE_ROOT).netloc:
            continue

        items.append({
            'title': title,
            'date': date_str,
            'href': abs_url,
            'is_attachment': _is_attach_url(abs_url),
        })

    return items


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail(html, detail_url):
    """Extract content and attachments from a detail page.

    Returns dict: {title, pub_date, content_text, attachments: [(name, url)]}.
    """
    soup = BeautifulSoup(html, 'lxml')
    result = {'title': '', 'pub_date': '', 'content_text': '', 'attachments': []}

    title_el = soup.find('title')
    if title_el:
        raw_title = title_el.get_text(strip=True)
        result['title'] = raw_title.split('_')[0].strip()

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

    content_div = None
    for selector in [
        {'class_': re.compile(r'TRS_Editor|Custom_UnionStyle|TRS_PreAppend')},
        {'class_': re.compile(r'article_con|article_content|article', re.I)},
        {'id': re.compile(r'article|content|detail|text|zoom', re.I)},
        {'class_': re.compile(r'content|text_con|body_con|detail_con', re.I)},
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

    seen_urls = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        abs_url = urljoin(detail_url, href)
        if _is_attach_url(abs_url) and abs_url not in seen_urls:
            seen_urls.add(abs_url)
            att_name = a.get_text(strip=True) or os.path.basename(urlparse(abs_url).path)
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
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(module_name, sub_name, item, detail, attachments_dir):
    lines = [
        f"# {detail.get('title', item['title'])}",
        "",
        f"**来源**: {_SITE_NAME}",
        f"**栏目**: {module_name}",
    ]
    if sub_name:
        lines.append(f"**子栏目**: {sub_name}")
    if detail.get('pub_date'):
        lines.append(f"**发布日期**: {detail['pub_date']}")
    lines.append(f"**原文链接**: {item['href']}")

    lines.append("")
    lines.append("---")
    lines.append("")

    if detail.get('content_text'):
        lines.append(detail['content_text'])
    elif item.get('is_attachment'):
        fname = os.path.basename(urlparse(item['href']).path)
        lines.append(f"(附件文件: {fname} — 正文请查看原文)")
    else:
        lines.append("(无法提取正文内容)")

    if attachments_dir and os.path.isdir(attachments_dir):
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")
        for fname in sorted(os.listdir(attachments_dir)):
            fpath = os.path.join(attachments_dir, fname)
            fsize = os.path.getsize(fpath)
            lines.append(f"- **{fname}** ({fsize:,} bytes)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single item processing
# ---------------------------------------------------------------------------

def _process_item(sess, module_name, sub_name, item, output_dir, kb_id, tenant_id):
    """Fetch detail or download direct attachment, save markdown, upload to KB."""
    item_id = item['href']
    _safe_print(f"\n  [{module_name}] {item['title'][:80]}")
    _safe_print(f"  {item['href'][:150]}")

    attachments_dir = None

    if item['is_attachment']:
        _safe_print("  Type: direct attachment")
        data = _download_file(sess, item['href'])
        if not data:
            _safe_print("  Download FAILED")
            return False

        fname = os.path.basename(urlparse(item['href']).path) or "attachment"
        safe_key = _sanitize_filename(item_id, 100)
        attachments_dir = os.path.join(output_dir, "attachments", safe_key)
        os.makedirs(attachments_dir, exist_ok=True)
        fpath = os.path.join(attachments_dir, fname)
        with open(fpath, 'wb') as f:
            f.write(data)
        _safe_print(f"  Downloaded: {fname} ({len(data):,} bytes)")

        ext_text = _extract_file_text(fpath)
        _safe_print(f"  Extracted: {len(ext_text)} chars")

        if fname.lower().endswith('.zip'):
            _extract_zip(fpath, attachments_dir)
            for extracted_file in sorted(os.listdir(attachments_dir)):
                epath = os.path.join(attachments_dir, extracted_file)
                if os.path.isfile(epath) and epath != fpath:
                    extracted_text = _extract_file_text(epath)
                    if extracted_text:
                        ext_text += f"\n\n--- {extracted_file} ---\n\n{extracted_text}"

        detail = {
            'title': item['title'],
            'pub_date': item['date'],
            'content_text': ext_text or f"附件: {fname}",
            'attachments': [(fname, item['href'])],
        }
    else:
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
        _safe_print(f"  Date: {detail.get('pub_date', item['date'])} | "
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
                    fpath = os.path.join(attachments_dir, fname)
                    with open(fpath, 'wb') as f:
                        f.write(data)
                    _safe_print(f"    OK ({len(data):,} bytes)")
                    if fname.lower().endswith('.zip'):
                        _extract_zip(fpath, attachments_dir)

    md_content = _build_markdown(module_name, sub_name, item, detail, attachments_dir)
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
    _safe_print(f"CZT JDHY Crawler | Today: {today_str}")
    _safe_print(f"Target: {target_url}")
    _safe_print("=" * 60)

    os.makedirs(output_dir, exist_ok=True)
    state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))

    sess = _init_session()

    _safe_print("\n--- Discovering modules ---")
    modules = _discover_modules(sess)
    _safe_print(f"Modules to crawl: {len(modules)}")
    for name, url in modules:
        _safe_print(f"  - {name}: {url}")

    total_processed = 0
    for mod_name, mod_url in modules:
        remaining = max_runtime - (time.time() - start_time)
        if remaining < 120:
            _safe_print(f"\nTimeout approaching ({remaining:.0f}s left) — graceful stop")
            break

        _safe_print(f"\n{'='*50}")
        _safe_print(f"Module: {mod_name}  ->  {mod_url}")

        # Discover sub-categories (tabs) for this module
        sub_categories = _discover_subcategories(sess, mod_url)
        if len(sub_categories) > 1:
            _safe_print(f"  Sub-categories: {len(sub_categories)}")
            for sname, surl in sub_categories:
                label = sname or "(主页面)"
                _safe_print(f"    - {label}: {surl}")

        for sub_name, sub_url in sub_categories:
            remaining = max_runtime - (time.time() - start_time)
            if remaining < 120:
                _safe_print(f"Timeout ({remaining:.0f}s) — stopping")
                break

            if sub_name:
                _safe_print(f"\n  Sub: {sub_name}  ->  {sub_url}")

            try:
                r = sess.get(sub_url, timeout=60, verify=False)
                if r.status_code != 200:
                    _safe_print(f"    HTTP {r.status_code} — skipping")
                    continue
                r.encoding = 'utf-8'
                html = r.text
            except Exception as e:
                _safe_print(f"    Fetch error: {e} — skipping")
                continue

            items = _parse_listing(html, sub_url)
            _safe_print(f"    Page items: {len(items)}")

            today_items = [it for it in items if it['date'] and it['date'] == today_str]
            _safe_print(f"    Today ({today_str}): {len(today_items)}")

            if not today_items:
                dates = sorted(set(it['date'] for it in items if it['date']), reverse=True)
                if dates:
                    _safe_print(f"    Dates on page: {dates[:5]}")
                continue

            for item in today_items:
                remaining = max_runtime - (time.time() - start_time)
                if remaining < 120:
                    _safe_print(f"Timeout ({remaining:.0f}s) — stopping")
                    break

                if item['href'] in processed_ids:
                    _safe_print(f"    [skip] {item['title'][:60]}")
                    continue

                success = _process_item(sess, mod_name, sub_name, item,
                                        output_dir, kb_id, tenant_id)
                if success:
                    processed_ids.add(item['href'])
                    state["processed_ids"] = list(processed_ids)
                    total_processed += 1
                    if total_processed % 10 == 0:
                        _save_state(output_dir, state)

                _request_delay(*_ARTICLE_DELAY)

            _request_delay(*_PAGE_DELAY)

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
        description="CZT JDHY Crawler — Fujian Finance Dept 解读回应"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--target-url", default=_MAIN_URL,
                        help="Target URL (default: %(default)s)")
    parser.add_argument("--kb-id", default=None, help="Knowledge base ID")
    parser.add_argument("--task-name", default="czt_jdhy_crawler", help="Task name")
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

    init_root_logger("czt_jdhy_crawler")
    logging.info("CZT JDHY Crawler | task=%s | output=%s", args.task_name, output_dir)

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
    main()
