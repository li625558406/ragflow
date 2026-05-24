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
Crawler for ygcg.fjcqjy.com — 工程公告 detail with time fields + attachments.

Target:
  - List:   https://ygcg.fjcqjy.com/trade/项目信息/工程公告
            (Vue SPA → API: Web.GZCG_GetJiaoYiList, BIG_TYPE=A)
  - Detail: https://qtgcztb.enjoy5191.com/views/ebid/combine/v1/
            entp-view.html?type=tp&tpId={id}

The list page is a Vue SPA backed by a .NET ASHX handler at
www.enjoy5191.com/api/GetDataHandler.ashx.  Detail pages are hosted at
the enjoy5191 bidding subdomain (qtgcztb) and use Playwright for full
content extraction including specific time fields and attachment downloads.

API requests are routed through a SOCKS5 proxy (sing-box on host) because
the server's IP is blacklisted by the enjoy5191 API.

Key detail fields:
  - 开标时间 / 开标倒计时
  - 标书售卖截止时间 / 答疑截止时间
  - 保证金缴纳截止时间 / 质疑截止时间

Checkpoint/resume: articles are processed in batches of 10. After each batch,
state is saved and content is uploaded to KB.

Usage:
    python ygcg_engineering_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ygcg.fjcqjy.com/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import base64
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright (required for detail pages)
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://ygcg.fjcqjy.com"
_LIST_URL = "{}/trade/%E9%A1%B9%E7%9B%AE%E4%BF%A1%E6%81%AF/%E5%B7%A5%E7%A8%8B%E5%85%AC%E5%91%8A".format(_SITE_ROOT)

# API (routed through SOCKS5 proxy to avoid server IP blacklist)
_API_BASE = "https://www.enjoy5191.com/api/GetDataHandler.ashx"
_API_METHOD = "Web.GZCG_GetJiaoYiList"
_API_BIG_TYPE = "A"

# SOCKS5 proxy (sing-box on host, accessible from Docker at 172.18.0.1:1080)
_PROXY_URL = "socks5h://172.18.0.1:1080"
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL}

# Detail
_DETAIL_BASE = "https://qtgcztb.enjoy5191.com"
_DETAIL_URL_FMT = (
    "{base}/views/ebid/combine/v1/entp-view.html"
    "?type=tp&tpId={tp_id}&flag=true&name=LogOn_GUOZI"
)

_SECTION_LABEL = "工程公告"
_SECTION_KEY = "ygcg_engineering"

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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Anti-crawling
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

# Batch checkpoint
BATCH_SIZE = 10

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_ATTACHMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".csv", ".rtf",
}

_EXT_LAWS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}

# Time field keywords for detail page extraction
_TIME_KEYWORDS = {
    "开标时间": ["开标时间", "开标日期"],
    "开标倒计时": ["开标倒计时"],
    "标书售卖截止时间": ["标书售卖截止时间", "标书售卖截止", "售卖截止时间", "标书获取截止时间", "标书获取截止"],
    "答疑截止时间": ["答疑截止时间", "答疑截止", "澄清截止时间", "澄清截止"],
    "保证金缴纳截止时间": ["保证金缴纳截止时间", "保证金缴纳截止", "保证金截止时间", "保证金截止", "投标保证金截止"],
    "质疑截止时间": ["质疑截止时间", "质疑截止", "异议截止时间", "异议截止"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _request_delay():
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name)
    name = name.strip("._ ")
    return (name or "untitled")[:max_len]


def _parse_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
        "%Y年%m月%d日",
    ):
        try:
            return datetime.strptime(text, fmt)
        except (ValueError, AttributeError):
            continue
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ===================================================================
# Listing — API via requests with SOCKS5 proxy
# ===================================================================

def _fetch_listing(page=1, page_size=20):
    """Fetch one page of 工程公告 listing via requests + SOCKS5 proxy."""
    params = {
        "method": _API_METHOD,
        "BIG_TYPE": _API_BIG_TYPE,
        "pageindex": page,
        "pagesize": page_size,
    }
    query = "&".join("{}={}".format(k, v) for k, v in params.items())
    url = "{}?{}".format(_API_BASE, query)

    api_headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://ygcg.fjcqjy.com/",
    }

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=api_headers,
                              proxies=_PROXIES, timeout=30)
            data = resp.json()
            if data.get("res") == "1":
                return data.get("data", [])
            logging.warning("API res=%s msg=%s (attempt %d)",
                          data.get("res"), data.get("msg", ""), attempt + 1)
        except Exception as e:
            logging.warning("API fetch page %d failed (attempt %d): %s",
                          page, attempt + 1, e)
        if attempt < 2:
            time.sleep((2 ** attempt) + random.uniform(1, 3))
    return None


def _gather_all_listings(today):
    """Paginate through the listing API, filter by today's date."""
    articles = []
    seen_ids = set()
    page = 1
    no_new_streak = 0

    while True:
        rows = _fetch_listing(page=page, page_size=20)
        if not rows:
            break

        new_on_page = 0
        for row in rows:
            source_id = str(row.get("SOURCE_ID", "")).strip()
            if not source_id or source_id in seen_ids:
                continue

            date_str = (row.get("PUBLISHED_TIME") or "").strip()[:10]
            dt = _parse_date(date_str)
            if not dt or dt < today:
                continue

            seen_ids.add(source_id)
            title = (row.get("NAME") or "").strip()
            if not title:
                continue

            # SOURCE_ID is the tpId; API provides the full detail URL
            tp_id = source_id
            raw_url = (row.get("URL") or "").strip()
            if raw_url and raw_url.startswith("http"):
                detail_url = raw_url
            else:
                detail_url = _DETAIL_URL_FMT.format(base=_DETAIL_BASE, tp_id=tp_id)

            articles.append({
                "id": source_id,
                "tp_id": tp_id,
                "title": title,
                "date_str": date_str,
                "detail_url": detail_url,
                "open_time": (row.get("OPEN_TIME") or "").strip(),
                "area": (row.get("AREANAME") or "").strip(),
                "tenderer": (row.get("TENDERER_NAME") or "").strip(),
                "agent": (row.get("UNIT_NAME") or "").strip(),
                "status": (row.get("STATUS_TXT") or "").strip(),
                "type_text": (row.get("TYPE_TEXT") or "").strip(),
            })
            new_on_page += 1

        _safe_print("[YGCG-E]   Page {}: {} new articles (total: {})".format(
            page, new_on_page, len(articles)))
        sys.stdout.flush()

        if new_on_page == 0:
            no_new_streak += 1
            if no_new_streak >= 2:
                break
        else:
            no_new_streak = 0

        if len(rows) < 20:
            break

        page += 1
        _request_delay()

    return articles


# ===================================================================
# Detail page extraction (Playwright browser)
# ===================================================================

def _extract_detail_from_page(page):
    """Extract content, time fields, and attachments from a detail page.

    Returns dict: {title, time_fields: {label: value}, content_text,
                   attachments: [{url, filename, ext}]}
    """
    try:
        result = page.evaluate("""() => {
            const res = {
                title: '',
                time_fields: {},
                content_text: '',
                attachments: [],
            };

            // Title
            const titleEl = document.querySelector(
                'h1, h2, h3, .project-name, .bid-name, ' +
                '.el-dialog__title, [class*="title"], [class*="bt"]'
            );
            if (titleEl) res.title = (titleEl.textContent || '').trim();

            // Body text — scan for time fields
            const bodyText = document.body.innerText || '';
            const lines = bodyText.split('\\n');

            const timeKeywords = {
                "开标时间": ["开标时间", "开标日期"],
                "开标倒计时": ["开标倒计时"],
                "标书售卖截止时间": ["标书售卖截止时间", "标书售卖截止", "售卖截止", "标书获取截止"],
                "答疑截止时间": ["答疑截止时间", "答疑截止", "澄清截止"],
                "保证金缴纳截止时间": ["保证金缴纳截止时间", "保证金缴纳截止", "保证金截止", "投标保证金截止"],
                "质疑截止时间": ["质疑截止时间", "质疑截止", "异议截止"],
            };

            for (const [label, keywords] of Object.entries(timeKeywords)) {
                for (const line of lines) {
                    // Split on first colon (： or :)
                    const colonIdx = line.indexOf('\\uff1a');
                    const semiIdx = line.indexOf(':');
                    const sepIdx = colonIdx >= 0 ? colonIdx : semiIdx;
                    if (sepIdx < 0) continue;
                    const labelPart = line.substring(0, sepIdx).trim();
                    const valuePart = line.substring(sepIdx + 1).trim();
                    let matched = false;
                    for (const kw of keywords) {
                        if (labelPart.includes(kw)) {
                            if (valuePart && valuePart.length > 1) {
                                res.time_fields[label] = valuePart;
                            }
                            matched = true;
                            break;
                        }
                    }
                    if (matched) break;
                }
            }

            // Full page text as content
            // Try content selectors first
            const contentSelectors = [
                '.detail-content', '.article-content', '.project-detail',
                '.bid-content', '.el-main', '[class*="content"]',
                '.detail-body', '.main-content', 'main', 'article',
                '.page-content', '.el-tab-pane', '.el-tabs__content',
            ];
            for (const sel of contentSelectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    res.content_text = el.textContent.trim();
                    break;
                }
            }
            if (!res.content_text) {
                // Remove noisy elements and use body text
                res.content_text = bodyText;
            }
            // Truncate very long content for KB
            if (res.content_text.length > 50000) {
                res.content_text = res.content_text.substring(0, 50000) +
                    '\\n\\n（内容过长，已截断）';
            }

            // Attachments
            const allLinks = document.querySelectorAll(
                'a[href*=".pdf"], a[href*=".doc"], a[href*=".zip"], ' +
                'a[href*=".xls"], a[href*=".rar"], a[href*=".7z"], ' +
                'a[href*="upload"], a[href*="download"], a[href*="file"], ' +
                'a[href*="attachment"], a[href*=".pptx"], a[href*=".ppt"]'
            );
            const seenUrls = new Set();
            for (const a of allLinks) {
                const href = (a.href || '').trim();
                if (!href || href.startsWith('javascript:') || href.startsWith('#')) continue;
                if (seenUrls.has(href)) continue;
                seenUrls.add(href);

                const knownExts = ['.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx',
                                   '.zip','.rar','.7z','.txt','.jpg','.jpeg','.png',
                                   '.tif','.tiff','.csv','.rtf'];
                let matchedExt = '';
                const pathPart = href.split('?')[0].split('#')[0].toLowerCase();
                for (const ext of knownExts) {
                    if (pathPart.endsWith(ext)) { matchedExt = ext; break; }
                }

                const fn = (a.textContent || '').trim() ||
                    decodeURIComponent(href.split('/').pop().split('?')[0]) ||
                    ('attachment' + (matchedExt || ''));

                res.attachments.push({
                    url: href,
                    filename: fn,
                    ext: matchedExt,
                });
            }

            return res;
        }""")
        return result
    except Exception as e:
        logging.warning("Detail extraction failed: %s", e)
        return {"title": "", "time_fields": {}, "content_text": "", "attachments": []}


# ===================================================================
# Attachment download (in-browser fetch)
# ===================================================================

def _download_attachment(page, att_url, dest_dir, filename, timeout=120):
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(filename, max_len=150)
    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    js_code = """
    async ([url, t]) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), t * 1000);
        try {
            const resp = await fetch(url, {
                method: 'GET',
                signal: controller.signal,
                headers: { 'Accept': '*/*' },
            });
            clearTimeout(timer);
            if (!resp.ok) return JSON.stringify({ error: 'HTTP ' + resp.status });
            const blob = await resp.blob();
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const b64 = reader.result.split(',')[1];
                    resolve(JSON.stringify({ b64: b64, size: blob.size }));
                };
                reader.onerror = () => resolve(JSON.stringify({ error: 'read error' }));
                reader.readAsDataURL(blob);
            });
        } catch (e) {
            clearTimeout(timer);
            return JSON.stringify({ error: e.message });
        }
    }
    """

    try:
        result = page.evaluate(js_code, [att_url, timeout])
        parsed = json.loads(result)
        if parsed.get("b64") and parsed.get("size", 0) > 100:
            blob = base64.b64decode(parsed["b64"])
            with open(dest_path, "wb") as f:
                f.write(blob)
            return dest_path
        else:
            logging.warning("Download empty/error for %s: %s",
                          att_url, parsed.get("error", ""))
            return None
    except Exception as e:
        logging.warning("Download failed for %s: %s", att_url, e)
        return None


# ===================================================================
# ZIP extraction
# ===================================================================

def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(os.path.basename(name), max_len=150)
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, 'wb') as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
                _safe_print("      [extract] {}".format(safe_name))
        os.remove(zip_path)
    except zipfile.BadZipFile:
        logging.warning("Not a valid ZIP: %s", zip_path)
    except Exception as e:
        logging.warning("ZIP extract error for %s: %s", os.path.basename(zip_path), e)
    return extracted


# ===================================================================
# State management
# ===================================================================

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": [], "completed": False}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _save_markdown(content, output_dir, item_id):
    d = os.path.join(output_dir, "articles")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "{}.md".format(item_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ===================================================================
# KB upload
# ===================================================================

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="general"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b
        def read(self):
            return self.blob

    fo = _FO(os.path.basename(filepath), blob)
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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="ygcg.fjcqjy.com 工程公告 detail crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://ygcg.fjcqjy.com/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop (default: 3300)")
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ===================================================================
# Main
# ===================================================================

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[YGCG-E] ygcg.fjcqjy.com 工程公告 detail crawler")
    _safe_print("[YGCG-E] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== YGCG-E crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[YGCG-E] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[YGCG-E] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[YGCG-E] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[YGCG-E] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Date filter: today -----------------------------------------------
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _safe_print("[YGCG-E] Date filter: 发布时间 >= {}".format(today.strftime("%Y-%m-%d")))
    sys.stdout.flush()

    # -- Playwright ----------------------------------------------------------
    chrome_path = _find_chrome()
    if not chrome_path:
        _safe_print("[YGCG-E] ERROR: Chrome not found.")
        sys.stdout.flush()
        sys.exit(1)

    # ===================================================================
    # Step 1: Fetch listings via API (requests + SOCKS5 proxy)
    # ===================================================================
    _safe_print("[YGCG-E] Step 1/3: Fetching listings via API...")
    sys.stdout.flush()

    all_articles = _gather_all_listings(today)
    _safe_print("[YGCG-E]   Total articles matching today+: {}".format(len(all_articles)))
    sys.stdout.flush()

    if not all_articles:
        _safe_print("[YGCG-E] No articles found for today or later.")
        return

    # Filter already processed
    new_articles = [a for a in all_articles if a["id"] not in processed_ids]
    _safe_print("[YGCG-E]   {} new (skipped {} already processed)".format(
        len(new_articles), len(all_articles) - len(new_articles)))
    sys.stdout.flush()

    if not new_articles:
        _safe_print("[YGCG-E] Nothing new. Marking complete.")
        state["completed"] = True
        _save_state(output_dir, state)
        return

    # Phase 2: Detail page extraction via sync_playwright (routed through proxy)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=chrome_path,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            proxy={"server": "socks5://172.18.0.1:1080"},
            extra_http_headers={
                "Accept": _HEADERS["Accept"],
                "Accept-Language": _HEADERS["Accept-Language"],
                "Accept-Encoding": _HEADERS["Accept-Encoding"],
                "Cache-Control": _HEADERS["Cache-Control"],
            },
        )

        # -- Timeout tracking ------------------------------------------------
        start_time = time.time()
        max_runtime = args.max_runtime

        # ===================================================================
        # Step 2: Fetch detail pages + attachments (batches)
        # ===================================================================
        _safe_print("[YGCG-E] Step 2/3: Fetching {} detail pages in batches of {}...".format(
            len(new_articles), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_articles)
        success_count = 0
        fail_count = 0
        batch_num = 0
        stopped_early = False

        for batch_start in range(0, total, BATCH_SIZE):
            # ── Time-bounded check ──
            elapsed = time.time() - start_time
            if elapsed > max_runtime - 120:
                _safe_print(
                    "\n[YGCG-E] Runtime {:.0f}s, stopping gracefully "
                    "({} / {} done). Next run will resume.".format(
                        elapsed, success_count, total))
                sys.stdout.flush()
                stopped_early = True
                break

            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                _safe_print("[YGCG-E]   [{}/{}] {}".format(
                    global_idx, total, art["title"][:60]))
                sys.stdout.flush()

                # ── Load detail page (shell → iframe → extract) ──
                # Time fields are on the shell page; full content is in the iframe.
                page = context.new_page()
                page.set_default_timeout(60000)

                detail_ok = False
                detail = {}
                time_fields = {}
                attachments = []
                for detail_attempt in range(3):
                    try:
                        # Load shell page
                        page.goto(art["detail_url"],
                                 wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3000)
                        # Extract time fields from shell page body text
                        # (they appear here, not in the iframe)
                        shell_detail = _extract_detail_from_page(page)
                        time_fields = shell_detail.get("time_fields", {})
                        # Check for iframe (full content is inside)
                        iframe_src = page.evaluate("""() => {
                            const f = document.querySelector('iframe');
                            return f ? f.src : null;
                        }""")
                        if iframe_src:
                            page.goto(iframe_src,
                                     wait_until="networkidle", timeout=60000)
                            page.wait_for_timeout(3000)
                            detail = _extract_detail_from_page(page)
                            attachments = detail.get("attachments", [])
                        else:
                            detail = shell_detail
                        # Content from iframe if available, else shell
                        content_text = detail.get("content_text", "") or shell_detail.get("content_text", "")
                        detail["content_text"] = content_text
                        if content_text and len(content_text) > 20:
                            detail_ok = True
                            break
                    except Exception as e:
                        logging.warning("Detail attempt %d for %s: %s",
                                      detail_attempt + 1, art["id"], e)
                    if detail_attempt < 2:
                        time.sleep((2 ** detail_attempt) + random.uniform(1, 3))

                if not detail_ok:
                    fail_count += 1

                title = detail.get("title") or art["title"]
                content_text = detail.get("content_text", "")
                if attachments:
                    _safe_print("      {} attachment(s)".format(len(attachments)))

                # ── Download attachments (page still open) ──
                local_att_files = []
                if attachments:
                    att_dir = os.path.join(output_dir, "attachments", art["id"])
                    for att in attachments:
                        _safe_print("      [dl] {}".format(
                            att.get("filename", "")[:50]))
                        sys.stdout.flush()

                        fp = _download_attachment(
                            page, att["url"], att_dir,
                            att.get("filename", "unknown"),
                            timeout=120,
                        )
                        if fp:
                            local_att_files.append(fp)
                            ext = os.path.splitext(fp)[1].lower()
                            is_zip = ext == ".zip"
                            if not is_zip:
                                try:
                                    with open(fp, "rb") as f:
                                        is_zip = f.read(4) == b"PK\x03\x04"
                                except Exception:
                                    pass
                            if is_zip:
                                extracted = _extract_zip(fp)
                                local_att_files.extend(extracted)

                try:
                    page.close()
                except Exception:
                    pass

                # ── Build markdown ──
                lines = [
                    "# {}".format(title),
                    "**栏目:** {}".format(_SECTION_LABEL),
                    "**日期:** {}".format(art["date_str"]),
                    "**地区:** {}".format(art.get("area", "")),
                    "**招标人:** {}".format(art.get("tenderer", "")),
                    "**代理机构:** {}".format(art.get("agent", "")),
                    "**状态:** {}".format(art.get("status", "")),
                    "**类型:** {}".format(art.get("type_text", "")),
                    "**URL:** {}".format(art["detail_url"]),
                    "",
                ]

                # Time fields
                if time_fields:
                    lines.append("## 时间信息")
                    lines.append("")
                    for label in [
                        "开标时间", "开标倒计时", "标书售卖截止时间",
                        "答疑截止时间", "保证金缴纳截止时间", "质疑截止时间",
                    ]:
                        val = time_fields.get(label, "")
                        if val:
                            lines.append("- **{}:** {}".format(label, val))
                    lines.append("")

                # Content
                lines.append("## 正文")
                lines.append("")
                if content_text:
                    lines.append(content_text)
                else:
                    lines.append("标题: {}".format(title))
                    lines.append("URL: {}".format(art["detail_url"]))
                lines.append("")

                # Attachment references
                if attachments:
                    lines.append("## 附件列表")
                    lines.append("")
                    for i, att in enumerate(attachments, 1):
                        ext_label = (att.get("ext") or "").upper().lstrip(".")
                        lines.append("{}. **{}** [{}]({})".format(
                            i, att.get("filename", "unknown"),
                            ext_label, att.get("url", ""),
                        ))
                    lines.append("")

                md_content = "\n".join(lines)
                _save_markdown(md_content, output_dir, art["id"])
                md_parts.append(md_content)
                batch_ids.append(art["id"])

                art_md_path = os.path.join(
                    output_dir, "articles", "{}.md".format(art["id"]))
                batch_files.append((art_md_path, "general"))
                for att_path in local_att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in _EXT_LAWS else "general"
                    batch_files.append((att_path, pid))

                success_count += 1
                _request_delay()

            # ── Checkpoint ──
            if md_parts:
                batch_path = os.path.join(output_dir,
                    "batch_{:03d}.md".format(batch_num))
                with open(batch_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_parts))

                processed_ids.update(batch_ids)
                state["processed_ids"] = list(processed_ids)
                _save_state(output_dir, state)

                if args.kb_id:
                    try:
                        _upload_to_kb(batch_path, args.kb_id, args.tenant_id)
                        for fp, parser in batch_files:
                            if os.path.exists(fp):
                                _upload_to_kb(fp, args.kb_id, args.tenant_id,
                                             parser_id=parser)
                    except Exception as e:
                        _safe_print("[YGCG-E]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload batch %d: %s", batch_num, e)

                _safe_print("[YGCG-E]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

        browser.close()

    # -- Mark complete if all done -------------------------------------------
    if not stopped_early:
        state["completed"] = True
        _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[YGCG-E] Done: {} articles ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[YGCG-E] Total found on listing: {}".format(len(all_articles)))
    if stopped_early:
        _safe_print("[YGCG-E] Stopped early (timeout), resume next run.")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== YGCG-E crawler finished: %d articles ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "ygcg_engineering_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
