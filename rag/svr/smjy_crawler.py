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
Dedicated web crawler for smggzy.sm.gov.cn/smwz/jyxx/ (三明市公共资源交易网 交易信息).

Targets 5 main categories with 19 sub-modules under 交易信息:
  建设工程 (022001): 招标计划, 招标公告, 答疑公告, 候选人公示, 中标结果公告, 中标合同, 开标记录表
  政府采购 (022002): 招标公告, 答疑公告, 中标结果公示
  土地矿产 (022003): 交易公告, 交易结果公示
  产权交易 (022004): 选拍公告, 交易公告, 交易结果公示, 县区项目
  其他交易 (022006): 交易公告, 答疑公告, 中标结果公示

Site characteristics
--------------------
  - Epoint WebBuilder CMS variant (ewb-* CSS classes)
  - SSR HTML, jQuery-based, not SPA
  - Listing pages: 20 items/page, AJAX pagination via ?pageing={N}
  - Detail pages: /smwz/InfoDetail/?InfoID={UUID}&CategoryNum={code}
  - Attachments: external links to download.bqpoint.com (standard bid platform)
  - Anti-crawling: random delays, full browser headers, session management

Pagination mechanism
--------------------
  Page 1: SSR in the initial HTML
  Page 2+: $.get(url + "?pageing=" + N) → HTML fragment injected into page
  Both accessible via plain GET requests.

Checkpoint/resume
-----------------
  Each sub-module is processed independently. State tracks:
  - processed_ids: set of article UUIDs already processed
  - module_progress: {module_key: {"page": N, "done": bool}}

  If the 3600s timeout kills the run, next trigger resumes from where it left off.

Usage:
    python smjy_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://smggzy.sm.gov.cn/smwz/jyxx/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin, unquote

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://smggzy.sm.gov.cn"
_SITE_NAME = "三明市公共资源交易网"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

# All 19 sub-modules
_MODULES = [
    # 建设工程 (022001)
    {"key": "gcjy_zbjh", "name": "建设工程-招标计划",
     "list_url": "/smwz/jyxx/022001/022001008/", "parent": "建设工程"},
    {"key": "gcjy_zbgg", "name": "建设工程-招标公告",
     "list_url": "/smwz/jyxx/022001/022001001/", "parent": "建设工程"},
    {"key": "gcjy_dygg", "name": "建设工程-答疑公告",
     "list_url": "/smwz/jyxx/022001/022001002/", "parent": "建设工程"},
    {"key": "gcjy_hxrgs", "name": "建设工程-候选人公示",
     "list_url": "/smwz/jyxx/022001/022001004/", "parent": "建设工程"},
    {"key": "gcjy_zbjg", "name": "建设工程-中标结果公告",
     "list_url": "/smwz/jyxx/022001/022001005/", "parent": "建设工程"},
    {"key": "gcjy_zbht", "name": "建设工程-中标合同",
     "list_url": "/smwz/jyxx/022001/022001006/", "parent": "建设工程"},
    {"key": "gcjy_kbjlb", "name": "建设工程-开标记录表",
     "list_url": "/smwz/jyxx/022001/022001007/", "parent": "建设工程"},
    # 政府采购 (022002)
    {"key": "zfcg_zbgg", "name": "政府采购-招标公告",
     "list_url": "/smwz/jyxx/022002/022002001/", "parent": "政府采购"},
    {"key": "zfcg_dygg", "name": "政府采购-答疑公告",
     "list_url": "/smwz/jyxx/022002/022002002/", "parent": "政府采购"},
    {"key": "zfcg_zbjg", "name": "政府采购-中标结果公示",
     "list_url": "/smwz/jyxx/022002/022002005/", "parent": "政府采购"},
    # 土地矿产 (022003)
    {"key": "tdkc_jygg", "name": "土地矿产-交易公告",
     "list_url": "/smwz/jyxx/022003/022003001/", "parent": "土地矿产"},
    {"key": "tdkc_jyjg", "name": "土地矿产-交易结果公示",
     "list_url": "/smwz/jyxx/022003/022003005/", "parent": "土地矿产"},
    # 产权交易 (022004)
    {"key": "cqjy_xpgg", "name": "产权交易-选拍公告",
     "list_url": "/smwz/jyxx/022004/022004004/", "parent": "产权交易"},
    {"key": "cqjy_jygg", "name": "产权交易-交易公告",
     "list_url": "/smwz/jyxx/022004/022004001/", "parent": "产权交易"},
    {"key": "cqjy_jyjg", "name": "产权交易-交易结果公示",
     "list_url": "/smwz/jyxx/022004/022004003/", "parent": "产权交易"},
    {"key": "cqjy_xqxm", "name": "产权交易-县区项目",
     "list_url": "/smwz/jyxx/022004/022004005/", "parent": "产权交易"},
    # 其他交易 (022006)
    {"key": "qtjy_jygg", "name": "其他交易-交易公告",
     "list_url": "/smwz/jyxx/022006/022006001/", "parent": "其他交易"},
    {"key": "qtjy_dygg", "name": "其他交易-答疑公告",
     "list_url": "/smwz/jyxx/022006/022006002/", "parent": "其他交易"},
    {"key": "qtjy_zbjg", "name": "其他交易-中标结果公示",
     "list_url": "/smwz/jyxx/022006/022006005/", "parent": "其他交易"},
]

# Anti-crawling: random delays between requests
_REQUEST_DELAY_MIN = 0.8
_REQUEST_DELAY_MAX = 2.0

_STATE_FILENAME = "_crawler_state.json"

# -- Regex patterns -----------------------------------------------------------
_INFOID_RE = re.compile(r"InfoID=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_CATEGORYNUM_RE = re.compile(r"CategoryNum=(\d+)", re.I)
_TOTAL_PAGES_RE = re.compile(r"var\s+totalPageNums\s*=\s*(\d+)")
_PAGING_RE = re.compile(r"pageing=(\d+)")

# Try to import Playwright for attachment downloads if needed
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Try to import ddddocr for captcha
try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _init_session():
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get(_SITE_ROOT + "/", timeout=30)
        time.sleep(1)
    except Exception:
        pass
    return sess


def _fetch_page(sess, url, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, timeout=60)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp.text
            if attempt < max_retries:
                time.sleep(2 * attempt)
        except Exception as e:
            logging.warning("Fetch %s failed (attempt %d): %s", url, attempt, e)
            if attempt < max_retries:
                time.sleep(2 * attempt)
    return ""


# ---------------------------------------------------------------------------
# List extraction
# ---------------------------------------------------------------------------

def _extract_list_items(html):
    """Extract article links from listing HTML (SSR page 1 or AJAX page N)."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True)
        if not text or len(text) < 4:
            continue
        m = _INFOID_RE.search(href)
        if not m:
            continue
        art_id = m.group(1)
        if art_id in seen:
            continue
        seen.add(art_id)
        cm = _CATEGORYNUM_RE.search(href)
        cat_num = cm.group(1) if cm else ""
        articles.append({
            "id": art_id,
            "title": text,
            "url": urljoin(_SITE_ROOT, href),
            "category_num": cat_num,
        })
    return articles


def _get_total_pages(sess, list_url):
    """Extract totalPageNums from the first page's JavaScript."""
    html = _fetch_page(sess, list_url)
    if not html:
        return 1
    m = _TOTAL_PAGES_RE.search(html)
    if m:
        return int(m.group(1))
    return 1


def _fetch_list_page(sess, list_url, page_num):
    """Fetch a listing page (SSR for page 1, AJAX for page 2+)."""
    if page_num == 1:
        return _fetch_page(sess, list_url)
    else:
        url = list_url.rstrip("/") + "?pageing={}".format(page_num)
        return _fetch_page(sess, url)


# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------

def _extract_detail(html, detail_url):
    result = {"title": "", "date": "", "content_text": "", "attachments": []}
    soup = BeautifulSoup(html, "html.parser")

    # Remove script/style tags early
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    body_text = soup.get_text()

    # Date: 信息时间：YYYY-MM-DD
    date_m = re.search(r"信息时间[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", body_text)
    if date_m:
        result["date"] = date_m.group(1)
    else:
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
        if date_m:
            result["date"] = date_m.group(1)

    # Title: extract from body text right before "信息时间"
    # Pattern: text appears between breadcrumb end and 信息时间, usually the last
    # meaningful non-nav line before the date metadata
    body_lines = [l.strip() for l in body_text.split("\n")]
    info_time_idx = -1
    for i, line in enumerate(body_lines):
        if "信息时间" in line:
            info_time_idx = i
            break

    if info_time_idx > 0:
        # Scan backwards from info_time to find the title line
        # Skip empty lines and short nav-like lines
        nav_pattern = re.compile(
            r"^(当前位置|首页|交易信息|建设工程|政府采购|土地矿产|产权交易|"
            r"中介机构|其他交易|政策法规|服务指引|学习园地|政务公开|"
            r"中心介绍|行政事务|中心动态|政民互动|下载中心|企业信息库|"
            r"数字证书|区块链|存证|【|阅读次数|我要打印|我要关闭)$"
        )
        for j in range(info_time_idx - 1, max(0, info_time_idx - 20), -1):
            line = body_lines[j]
            if not line or len(line) < 4:
                continue
            if nav_pattern.match(line):
                continue
            # Found candidate title
            if len(line) < 300:
                result["title"] = line
                break

    # Title fallback: from breadcrumb (.ewb-route)
    if not result["title"]:
        route_el = soup.select_one(".ewb-route")
        if route_el:
            parts = route_el.get_text(strip=True).split(">")
            for part in reversed(parts):
                part = part.strip()
                if part and len(part) > 3 and "首页" not in part and "交易信息" not in part:
                    result["title"] = part
                    break

    # Title fallback: h1, h2, h3
    if not result["title"]:
        for sel in ("h1", "h2", "h3", ".bt", "[class*='title']"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) > 2 and len(text) < 200:
                    result["title"] = text
                    break

    # Content: body text with navigation/footer stripped
    content_text = soup.get_text(separator="\n", strip=True)
    # Cut off footer
    cutoff_markers = ["设为首页", "主办：", "备案序号：", "技术支持：",
                      "访问统计", "闽公网安备", "网站标识码"]
    for marker in cutoff_markers:
        idx = content_text.find(marker)
        if idx > 200:
            content_text = content_text[:idx]
            break

    if len(content_text) > 50:
        result["content_text"] = content_text

    # Attachments: extract download links
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        # Standard bid attachment download links
        if "download.bqpoint.com" in href or "downloaddetail" in href:
            result["attachments"].append({
                "name": text or "招标文件下载",
                "download_url": urljoin(_SITE_ROOT, href),
                "source": "bqpoint",
            })
        # Direct file links
        elif re.search(r'\.(pdf|doc|docx|xls|xlsx|rar|zip|7z)(\b|[?#])', href, re.I):
            result["attachments"].append({
                "name": text or os.path.basename(href.split("?")[0]),
                "download_url": urljoin(_SITE_ROOT, href),
                "source": "direct",
            })

    return result


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachment(sess, att, dest_dir):
    """Download an attachment. Uses Playwright for bqpoint links if needed."""
    url = att.get("download_url", "")
    name = att.get("name", "unknown")
    source = att.get("source", "direct")

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) if name != "招标文件下载" else "attachment"
    # Ensure extension
    if not re.search(r'\.\w{2,5}$', safe_name):
        safe_name += ".html"

    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    try:
        _request_delay()

        if source == "bqpoint":
            # bqpoint is a download gateway page, not a direct download
            # Save the gateway page for reference
            resp = sess.get(url, timeout=60)
            if resp.status_code == 200:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                _safe_print("      Saved bqpoint page: {} ({} bytes)".format(
                    safe_name, len(resp.content)))
                return dest_path
        else:
            resp = sess.get(url, timeout=120, stream=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                # Get filename from Content-Disposition if available
                cd = resp.headers.get("Content-Disposition", "")
                fn_match = re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
                if fn_match:
                    safe_name = re.sub(r'[\\/:*?"<>|]', "_", fn_match.group(1))
                    dest_path = os.path.join(dest_dir, safe_name)

                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                _safe_print("      Downloaded: {} ({} bytes)".format(
                    safe_name, len(resp.content)))
                return dest_path
    except Exception as e:
        logging.warning("Download error for %s: %s", name, e)

    return None


# ---------------------------------------------------------------------------
# File extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("__MACOSX") or name.startswith("."):
                    continue
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, "wb") as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
                _safe_print("      Extracted: {}".format(safe_name))
        os.remove(zip_path)
    except Exception as e:
        logging.warning("ZIP extract error for %s: %s",
                        os.path.basename(zip_path), e)
    return extracted


def _extract_file_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            try:
                import pdfplumber
                parts = []
                with pdfplumber.open(filepath) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            parts.append(text)
                return "\n\n".join(parts)
            except ImportError:
                pass
            try:
                import fitz
                doc = fitz.open(filepath)
                parts = []
                for page in doc:
                    text = page.get_text()
                    if text:
                        parts.append(text)
                doc.close()
                return "\n\n".join(parts)
            except ImportError:
                return "(PDF file, no parser available)"
        elif ext in (".doc", ".docx"):
            if ext == ".docx":
                try:
                    import docx
                    doc = docx.Document(filepath)
                    return "\n".join(
                        p.text for p in doc.paragraphs if p.text.strip())
                except ImportError:
                    pass
            return "(DOC file, text extraction limited)"
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(
                        " | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------

def _build_markdown(art, detail, attachment_texts):
    title = detail.get("title") or art.get("title", "无标题")
    lines = [
        "# {}".format(title),
        "",
        "**栏目:** {}".format(art.get("section", "")),
    ]
    date_str = detail.get("date") or art.get("date_str", "")
    if date_str:
        lines.append("**日期:** {}".format(date_str))
    lines.append("**URL:** {}".format(art.get("url", "")))
    lines.append("")

    content = detail.get("content_text", "")
    if content:
        if len(content) > 50000:
            content = content[:50000] + "\n\n（内容过长，已截断）"
        lines.append("## 详细内容")
        lines.append("")
        lines.append(content)
        lines.append("")

    if attachment_texts:
        lines.append("## 附件内容")
        lines.append("")
        for fname, ftext in attachment_texts:
            lines.append("### {}".format(fname))
            lines.append("")
            if ftext:
                if len(ftext) > 50000:
                    ftext = ftext[:50000] + "\n\n（附件内容过长，已截断）"
                lines.append(ftext)
            else:
                lines.append("（无法提取文本内容）")
            lines.append("")

    return "\n".join(lines)


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
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": [], "completed_modules": [],
            "module_progress": {}}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d modules done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_modules", [])))


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="general"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FileObj(os.path.basename(filepath), blob)
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            DocumentService.update_by_id(did, {"parser_id": parser_id})
        except Exception:
            pass
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Module-level processing
# ---------------------------------------------------------------------------

def _process_module(sess, output_dir, kb_id, tenant_id,
                    module_info, processed_ids, state):
    module_key = module_info["key"]
    module_name = module_info["name"]
    list_url = urljoin(_SITE_ROOT, module_info["list_url"])

    if module_key in state.get("completed_modules", []):
        _safe_print("[{}]   Already completed, skipping.".format(module_name))
        sys.stdout.flush()
        return 0

    progress = state.get("module_progress", {}).get(module_key, {"page": 1, "done": False})

    _safe_print("[{}]   Listing URL: {}".format(module_name, list_url))
    sys.stdout.flush()

    # Get total pages from first page
    total_pages = _get_total_pages(sess, list_url)
    _safe_print("[{}]   Total pages: {} (resuming from page {})".format(
        module_name, total_pages, progress["page"]))
    sys.stdout.flush()

    if total_pages == 0:
        # Empty module
        state.setdefault("completed_modules", []).append(module_key)
        _save_state(output_dir, state)
        return 0

    BATCH_SIZE = 10
    total_processed = 0
    downloads_dir = os.path.join(output_dir, "downloads")
    reached_already_processed = False

    for page_num in range(progress["page"], total_pages + 1):
        page_html = _fetch_list_page(sess, list_url, page_num)
        if not page_html:
            _safe_print("[{}]   ERROR: Failed to fetch page {}".format(
                module_name, page_num))
            break

        articles = _extract_list_items(page_html)
        # Filter already processed articles
        new_articles = [a for a in articles if a["id"] not in processed_ids]

        if len(new_articles) < len(articles):
            # Some articles on this page were already processed
            if len(new_articles) == 0:
                # Entire page is duplicate - subsequent pages likely also processed
                if not reached_already_processed:
                    reached_already_processed = True
                    _safe_print("[{}]   Page {}: all {} items already processed, "
                                "checking next page...".format(
                                    module_name, page_num, len(articles)))
                # Continue to next page but mark module as potentially done
                if page_num >= total_pages:
                    break
                continue
            else:
                reached_already_processed = False

        _safe_print("[{}]   Page {}/{}: {} items ({} new)".format(
            module_name, page_num, total_pages,
            len(articles), len(new_articles)))
        sys.stdout.flush()

        for a in new_articles:
            a["section"] = module_name

        # Process in batches
        for batch_start in range(0, len(new_articles), BATCH_SIZE):
            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num = (batch_start // BATCH_SIZE) + 1
            md_parts = []
            batch_ids = []

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                _safe_print("[{}]   [{}/{}] {}".format(
                    module_name, global_idx, len(new_articles),
                    art["title"][:60]))
                sys.stdout.flush()

                detail_html = _fetch_page(sess, art["url"])
                if detail_html:
                    detail = _extract_detail(detail_html, art["url"])
                else:
                    detail = {
                        "title": art["title"],
                        "date": "",
                        "content_text": "标题: {}\nURL: {}".format(
                            art["title"], art["url"]),
                        "attachments": [],
                    }

                # Download attachments
                attachment_texts = []
                for att in detail.get("attachments", []):
                    att_name = att.get("name", "unknown")
                    dest_dir = os.path.join(downloads_dir, module_key, art["id"])
                    fp = _download_attachment(sess, att, dest_dir)
                    if fp:
                        is_zip = (
                                fp.lower().endswith((".zip", ".rar")) or
                                (os.path.getsize(fp) >= 4 and
                                 open(fp, "rb").read(4) == b"PK\x03\x04")
                        )
                        if is_zip:
                            extracted = _extract_zip(fp)
                            for ext_fp in extracted:
                                text = _extract_file_text(ext_fp)
                                attachment_texts.append(
                                    (os.path.basename(ext_fp), text))
                        else:
                            text = _extract_file_text(fp)
                            attachment_texts.append((att_name, text))

                md = _build_markdown(art, detail, attachment_texts)

                articles_dir = os.path.join(output_dir, "articles", module_key)
                os.makedirs(articles_dir, exist_ok=True)
                md_path = os.path.join(articles_dir, "{}.md".format(art["id"]))
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md)

                md_parts.append(md)
                batch_ids.append(art["id"])

                _request_delay()

            # Checkpoint after each batch
            if md_parts:
                batch_path = os.path.join(
                    output_dir, "{}_{:03d}_{:03d}.md".format(
                        module_key, page_num, batch_num))
                with open(batch_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_parts))

                processed_ids.update(batch_ids)
                state["processed_ids"] = list(processed_ids)
                state.setdefault("module_progress", {})[module_key] = {
                    "page": page_num, "done": False}
                _save_state(output_dir, state)

                if kb_id:
                    try:
                        _upload_to_kb(batch_path, kb_id, tenant_id)
                    except Exception as e:
                        _safe_print("[{}]   batch upload failed: {}".format(
                            module_name, e))
                        logging.error("Upload failed for %s: %s", module_name, e)

                total_processed += len(md_parts)
                _safe_print("[{}]   p{}b{} uploaded ({} total)".format(
                    module_name, page_num, batch_num, total_processed))
                sys.stdout.flush()

        # Update page progress after completing all batches on this page
        state.setdefault("module_progress", {})[module_key] = {
            "page": page_num + 1, "done": False}
        _save_state(output_dir, state)

    # Module complete
    state.setdefault("completed_modules", []).append(module_key)
    state.setdefault("module_progress", {})[module_key] = {
        "page": total_pages, "done": True}
    _save_state(output_dir, state)

    _safe_print("[{}]   Done: {} processed\n".format(module_name, total_processed))
    sys.stdout.flush()
    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="smjy crawler - 三明市公共资源交易网 交易信息"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://smggzy.sm.gov.cn/smwz/jyxx/")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Comma-separated module keys (e.g. gcjy_zbgg,zfcg_zbgg)")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds (default: 3300s = 55min)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[SMJY] 三明市公共资源交易网 crawler")
    _safe_print("[SMJY] KB: {}".format(args.kb_id))
    _safe_print("[SMJY] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== SMJY crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[SMJY] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_modules": [], "module_progress": {}
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_modules = set(state.get("completed_modules", []))
    _safe_print("[SMJY] Previously processed: {}, completed modules: {}\n".format(
        len(processed_ids), len(completed_modules)))
    sys.stdout.flush()

    if args.section:
        selected = set(args.section.split(","))
        active_modules = [m for m in _MODULES if m["key"] in selected]
    else:
        active_modules = [m for m in _MODULES if m["key"] not in completed_modules]

    _safe_print("[SMJY] Active modules: {}".format(
        ", ".join(m["name"] for m in active_modules)))
    sys.stdout.flush()

    sess = _init_session()
    _safe_print("[SMJY] Session initialized\n")
    sys.stdout.flush()

    try:
        run_start = time.time()
        total_processed = 0
        stopped_early = False

        for mod_info in active_modules:
            elapsed = time.time() - run_start
            remaining = args.max_runtime - elapsed
            grace = min(120, args.max_runtime * 0.05)
            if remaining < grace:
                _safe_print(
                    "\n[SMJY] Runtime {:.0f}s, remaining {:.0f}s < "
                    "grace {:.0f}s, stopping early.".format(
                        elapsed, remaining, grace))
                sys.stdout.flush()
                stopped_early = True
                break

            n = _process_module(sess, output_dir, args.kb_id,
                                args.tenant_id, mod_info, processed_ids, state)
            total_processed += n

        _safe_print("\n" + "=" * 60)
        if stopped_early:
            _safe_print("[SMJY] Partial run: {} articles. "
                        "Will resume from checkpoint.".format(total_processed))
        else:
            _safe_print("[SMJY] Done: {} articles processed.".format(
                total_processed))
        _safe_print("=" * 60 + "\n")
        sys.stdout.flush()
        logging.info("=== SMJY crawler finished: %d articles ===",
                     total_processed)
    finally:
        sess.close()


if __name__ == "__main__":
    CONSUMER_NAME = "smjy_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
