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
Crawler for www.enjoy5191.com — public trade listings with attachments.

Target:
  - List:   https://www.enjoy5191.com/views/public/trade.html
  - Detail: https://qycg.enjoy5191.com/platform/entp-purchase/combine/v1/
            entp-view.html?project_id=<ID>&utype=guest&type=<TYPE>&method=<METHOD>

The listing page is a Vue.js SPA that fetches trade data via POST to
/api/GetDataHandler.ashx.  This crawler uses Playwright to:
  1. Navigate to trade.html and intercept API requests to discover the
     listing method and available parameters.
  2. Use the discovered API method for paginated listing retrieval.
  3. Apply date filter: only items with ``开标日期`` >= today.
  4. For each item, navigate to the qycg.enjoy5191.com detail page,
     extract content, and download attachments (doc, pdf, zip, etc.).
     ZIP archives are auto-extracted.

Checkpoint/resume: articles are processed in batches of 10. After each batch,
state is saved and content is uploaded to KB. If the 3600s task timeout kills
the run, the next trigger resumes from where it left off.

Usage:
    python enjoy5191_trade_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.enjoy5191.com/ \
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
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://www.enjoy5191.com"
_LIST_URL = "{}/views/public/trade.html".format(_SITE_ROOT)
_API_BASE = "{}/api/GetDataHandler.ashx".format(_SITE_ROOT)

# qycg detail subdomain
_QYCG_BASE = "https://qycg.enjoy5191.com"
def _build_detail_url(project_id, type_param="%E9%87%87%E8%B4%AD%E5%85%AC%E5%91%8A", method_param="66030108"):
    """Build a qycg.enjoy5191.com detail page URL."""
    return ("{}/platform/entp-purchase/combine/v1/entp-view.html"
            "?project_id={}&utype=guest&type={}&method={}").format(
        _QYCG_BASE, project_id, type_param, method_param)

_SECTION_LABEL = "交易公告"
_SECTION_KEY = "enjoy5191_trade"

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

# Known API methods to probe (order matters — first match wins)
_PROBE_METHODS = [
    "Web.GetTradeList",
    "Web.GetJiaoYiList",
    "Web.GetTop8ByType",
    "Web.GetProjectList",
    "Web.GetBidList",
]

# Attachment file extensions
_ATTACHMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".csv", ".rtf",
}

_EXT_LAWS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}

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
    if not name:
        name = "untitled"
    return name[:max_len]


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
    logging.info("State saved (%d IDs, completed=%s)",
                 len(state.get("processed_ids", [])),
                 state.get("completed", False))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ===================================================================
# API discovery — intercept XHR responses on the listing page
# ===================================================================

def _discover_api(page):
    """Navigate to trade.html, intercept API responses, discover the listing method.

    Returns dict: {method, params_template, row_fields, date_field}
    or None if discovery fails.
    """
    captured_responses = []

    def on_response(resp):
        if resp.status != 200:
            return
        if "GetDataHandler.ashx" not in resp.url:
            return
        try:
            ct = resp.headers.get("content-type", "")
            if "json" not in ct and "text/" not in ct:
                return
            data = resp.json()
            if data.get("res") == "1" and data.get("data"):
                captured_responses.append(data)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        page.goto(_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
    except Exception as e:
        logging.warning("Page load for API discovery failed: %s", e)
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    if not captured_responses:
        return None

    # Find the response with the most rows — that's likely the listing
    best = max(captured_responses, key=lambda d: len(d.get("data", [])))

    # Try to extract method from the request — look at page's form data
    # Since we only captured responses, we infer from data structure
    rows = best.get("data", [])
    if not rows:
        return None

    # Gather row field names
    row0 = rows[0]
    row_fields = list(row0.keys()) if isinstance(row0, dict) else []

    # Heuristic: find the date field that looks like 开标日期
    date_candidates = [f for f in row_fields if f and any(
        kw in f.upper() for kw in ("开标", "BID", "OPEN", "DATE", "TIME", "KBRQ", "KBSJ")
    )]
    if not date_candidates:
        date_candidates = [f for f in row_fields if "TM" in f.upper() or "DATE" in f.upper() or "TIME" in f.upper()]

    date_field = date_candidates[0] if date_candidates else "TM"

    # Heuristic: find project ID field
    id_candidates = [f for f in row_fields if f and any(
        kw in f.upper() for kw in ("PROJECT", "ID", "PRJID", "XMBH")
    )]
    id_field = id_candidates[0] if id_candidates else (row_fields[0] if row_fields else "ID")

    # Heuristic: find title field
    title_candidates = [f for f in row_fields if f and any(
        kw in f.upper() for kw in ("TITLE", "NAME", "XMMC", "GCMC", "BT")
    )]
    title_field = title_candidates[0] if title_candidates else "TITLE"

    return {
        "row_fields": row_fields,
        "date_field": date_field,
        "id_field": id_field,
        "title_field": title_field,
        "sample_row": row0,
        "sample_count": len(rows),
    }


# ===================================================================
# API listing — probe methods + paginated fetch
# ===================================================================

def _api_post(page, method, params=None, retries=3):
    """POST to GetDataHandler via Playwright fetch, return data list or None."""
    body = {"method": method}
    if params:
        body.update(params)

    js_code = """
    async ([url, body]) => {
        try {
            const formBody = new URLSearchParams();
            for (const [k, v] of Object.entries(body)) {
                formBody.append(k, String(v));
            }
            const resp = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: formBody.toString(),
            });
            const text = await resp.text();
            return text;
        } catch (e) {
            return JSON.stringify({ error: e.message });
        }
    }
    """

    for attempt in range(1, retries + 1):
        try:
            result = page.evaluate(js_code, [_API_BASE, body])
            data = json.loads(result)
            if data.get("res") == "1":
                return data.get("data", [])
            logging.warning("API %s -> res=%s msg=%s", method,
                          data.get("res"), data.get("msg", ""))
            if attempt < retries:
                time.sleep(2 ** attempt)
        except Exception as e:
            logging.warning("API %s failed (attempt %d/%d): %s", method, attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def _probe_listing_method(page, discovery):
    """Try known methods to find one that returns trade data matching our sample."""
    sample = discovery.get("sample_row", {})
    sample_keys = set(sample.keys()) if sample else set()

    for method in _PROBE_METHODS:
        _safe_print("[ETRADE]   Trying method: {}...".format(method))
        sys.stdout.flush()
        data = _api_post(page, method, {"page": 1, "rows": 5})
        if data and len(data) > 0:
            # Check if the data structure matches our discovery
            row_keys = set(data[0].keys()) if isinstance(data[0], dict) else set()
            overlap = len(sample_keys & row_keys) if sample_keys else 0
            if overlap >= 3 or not sample_keys:
                _safe_print("[ETRADE]   SUCCESS: {} returned {} rows".format(method, len(data)))
                sys.stdout.flush()
                return method
            else:
                _safe_print("[ETRADE]   Data mismatch (overlap={}), trying next...".format(overlap))
                sys.stdout.flush()
        else:
            _safe_print("[ETRADE]   No data from {}".format(method))
            sys.stdout.flush()

    return None


# ===================================================================
# Detail page extraction (qycg.enjoy5191.com)
# ===================================================================

def _extract_detail_from_page(page):
    """Extract content and attachment links from a qycg detail page.

    Returns dict: {title, date_str, content_text, attachments: [{url, filename, ext}]}
    """
    try:
        result = page.evaluate("""() => {
            const res = {
                title: '',
                date_str: '',
                content_text: '',
                attachments: [],
            };

            // Title
            const titleEl = document.querySelector(
                'h1, h2, h3, .title, [class*="title"], .detail-title, ' +
                '.project-name, .bid-name, [class*="bt"]'
            );
            if (titleEl) res.title = (titleEl.textContent || '').trim();

            // Date
            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(
                /(\\\\d{4}[-/年]\\\\d{1,2}[-/月]\\\\d{1,2})/
            );
            if (dateMatch) res.date_str = dateMatch[1];

            // Content — try selectors, fall back to body
            const contentSelectors = [
                '.detail-content', '.article-content', '.project-detail',
                '.bid-content', '.content', '[class*="content"]',
                '.detail-body', '.main-content', 'main', 'article',
                '.el-main', '.page-content',
            ];
            for (const sel of contentSelectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    res.content_text = el.textContent.trim();
                    break;
                }
            }
            if (!res.content_text) {
                const remove = document.querySelectorAll(
                    'nav, header, footer, .footer, .header, ' +
                    '.el-menu, .sidebar, .nav, .menu'
                );
                remove.forEach(el => el.remove());
                res.content_text = document.body.textContent.trim();
            }

            // Attachments
            const allLinks = document.querySelectorAll(
                'a[href*=".pdf"], a[href*=".doc"], a[href*=".zip"], ' +
                'a[href*=".xls"], a[href*=".rar"], a[href*=".7z"], ' +
                'a[href*="upload"], a[href*="download"], a[href*="file"], ' +
                'a[href*="attachment"]'
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
        return {"title": "", "date_str": "", "content_text": "", "attachments": []}


# ===================================================================
# Attachment download
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
            logging.warning("Download empty/error for %s: %s", att_url, parsed.get("error", ""))
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


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="enjoy5191.com public trade listings crawler with attachments"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://www.enjoy5191.com/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-pages", type=int, default=50,
                   help="Max pages to crawl (default: 50)")
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
    _safe_print("[ETRADE] enjoy5191 public trade crawler with attachments")
    _safe_print("[ETRADE] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ETRADE crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[ETRADE] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[ETRADE] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[ETRADE] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[ETRADE] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Date filter: 开标日期 >= today --------------------------------------
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _safe_print("[ETRADE] Date filter: 开标日期 >= {}".format(today.strftime("%Y-%m-%d")))
    sys.stdout.flush()

    # -- Playwright ----------------------------------------------------------
    with sync_playwright() as pw:
        chrome_path = _find_chrome()
        launch_opts = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox"],
        }
        if chrome_path:
            launch_opts["executable_path"] = chrome_path
        browser = pw.chromium.launch(**launch_opts)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            extra_http_headers={
                "Accept": _HEADERS["Accept"],
                "Accept-Language": _HEADERS["Accept-Language"],
                "Accept-Encoding": _HEADERS["Accept-Encoding"],
                "Cache-Control": _HEADERS["Cache-Control"],
            },
        )

        # ===================================================================
        # Step 1: Discover API by navigating to trade.html
        # ===================================================================
        _safe_print("[ETRADE] Step 1/4: Discovering listing API...")
        sys.stdout.flush()

        discovery_page = context.new_page()
        discovery_page.set_default_timeout(60000)

        discovery = _discover_api(discovery_page)

        if discovery:
            _safe_print("[ETRADE]   Found {} rows in initial load".format(
                discovery["sample_count"]))
            _safe_print("[ETRADE]   Row fields ({}): {}".format(
                len(discovery["row_fields"]),
                ", ".join(discovery["row_fields"][:15]),
            ))
            _safe_print("[ETRADE]   Date field: {}".format(discovery["date_field"]))
            _safe_print("[ETRADE]   ID field: {}".format(discovery["id_field"]))
            _safe_print("[ETRADE]   Title field: {}".format(discovery["title_field"]))
            sys.stdout.flush()

            # Probe for the correct API method
            api_method = _probe_listing_method(discovery_page, discovery)

            if not api_method:
                _safe_print("[ETRADE]   WARNING: Could not find API method via probing.")
                _safe_print("[ETRADE]   Falling back to DOM-based extraction.")
                sys.stdout.flush()
        else:
            _safe_print("[ETRADE]   WARNING: API discovery failed, using DOM extraction.")
            sys.stdout.flush()
            discovery = {}
            api_method = None

        discovery_page.close()

        # ===================================================================
        # Step 2: Fetch all listings (API or DOM fallback)
        # ===================================================================
        _safe_print("[ETRADE] Step 2/4: Fetching all listings...")
        sys.stdout.flush()

        all_articles = []
        id_field = (discovery.get("id_field") or "ID")
        title_field = (discovery.get("title_field") or "TITLE")
        date_field = (discovery.get("date_field") or "TM")

        if api_method:
            # ── API-based paginated listing ──
            list_page = context.new_page()
            list_page.set_default_timeout(60000)
            # Must navigate to the origin so JS fetch works for API calls
            list_page.goto(_LIST_URL, wait_until="domcontentloaded", timeout=60000)
            list_page.wait_for_timeout(3000)

            page_num = 1
            page_size = 20
            no_new_streak = 0

            while page_num <= args.max_pages:
                rows = _api_post(list_page, api_method, {
                    "page": page_num, "rows": page_size
                })
                if not rows:
                    _safe_print("[ETRADE]   Page {} empty, stopping.")
                    break

                new_on_page = 0
                for row in rows:
                    art_id = str(row.get(id_field, ""))
                    if not art_id:
                        continue
                    title = (row.get(title_field) or "").strip()
                    if not title:
                        continue

                    # Date filter: 开标日期 >= today
                    date_str = (row.get(date_field) or "").strip()
                    if date_str:
                        dt = _parse_date(date_str)
                        if dt and dt < today:
                            continue

                    all_articles.append({
                        "id": art_id,
                        "title": title,
                        "date_str": date_str,
                        "raw": row,
                    })
                    new_on_page += 1

                _safe_print("[ETRADE]   Page {}: {} new (total: {})".format(
                    page_num, new_on_page, len(all_articles)))

                if new_on_page == 0:
                    no_new_streak += 1
                else:
                    no_new_streak = 0

                if no_new_streak >= 3:
                    _safe_print("[ETRADE]   3 pages with no new data, stopping.")
                    break

                if len(rows) < page_size:
                    break

                page_num += 1
                _request_delay()

            list_page.close()

        else:
            # ── DOM-based fallback ──
            dom_page = context.new_page()
            dom_page.set_default_timeout(60000)
            dom_page.goto(_LIST_URL, wait_until="domcontentloaded", timeout=60000)
            dom_page.wait_for_timeout(5000)

            # Try to extract rows from table
            page_num = 1
            seen_ids = set()

            while page_num <= args.max_pages:
                rows = dom_page.evaluate("""() => {
                    const results = [];
                    // Try common table patterns
                    const tables = document.querySelectorAll(
                        'table tbody tr, .el-table__body tr, ' +
                        '.table tbody tr, [class*="table"] tbody tr, ' +
                        '.list-item, .trade-item, [class*="row"]'
                    );
                    for (const row of tables) {
                        const cells = row.querySelectorAll('td, th, .cell');
                        const texts = Array.from(cells).map(c => c.textContent.trim()).filter(Boolean);
                        if (texts.length >= 3) {
                            // Find links for detail URL
                            const links = row.querySelectorAll('a[href]');
                            let detailUrl = '';
                            for (const a of links) {
                                const h = a.href || '';
                                if (h.includes('project_id=') || h.includes('entp-view')) {
                                    detailUrl = h;
                                    break;
                                }
                            }
                            results.push({ texts: texts, detailUrl: detailUrl });
                        }
                    }
                    return results;
                }""")

                for row_data in (rows or []):
                    texts = row_data.get("texts", [])
                    detail_url = row_data.get("detailUrl", "")
                    if not texts:
                        continue

                    # Extract project_id from detail URL
                    art_id = ""
                    if detail_url:
                        m = re.search(r'project_id=(\d+)', detail_url)
                        if m:
                            art_id = m.group(1)

                    if not art_id or art_id in seen_ids:
                        continue
                    seen_ids.add(art_id)

                    # Heuristic: first text is title, look for date
                    title = texts[0] if texts else ""
                    date_str = ""
                    for t in texts:
                        dt = _parse_date(t)
                        if dt:
                            date_str = t
                            if dt < today:
                                break  # skip old
                            break
                    else:
                        if date_str:
                            dt = _parse_date(date_str)
                            if dt and dt < today:
                                continue  # old, skip
                        # If no date found, include anyway
                        pass

                    all_articles.append({
                        "id": art_id,
                        "title": title,
                        "date_str": date_str,
                        "detail_url": detail_url,
                        "raw": {"texts": texts},
                    })

                _safe_print("[ETRADE]   Page {}: {} total".format(page_num, len(all_articles)))
                sys.stdout.flush()

                # Try pagination click
                next_clicked = False
                try:
                    btn = dom_page.query_selector(
                        ".el-pagination .btn-next:not(.disabled), "
                        ".pagination .next:not(.disabled), "
                        "button.btn-next:not([disabled]), "
                        "li.next:not(.disabled)"
                    )
                    if btn:
                        btn.click()
                        dom_page.wait_for_timeout(2500)
                        next_clicked = True
                except Exception:
                    pass

                if not next_clicked:
                    break
                page_num += 1
                _request_delay()

            dom_page.close()

        _safe_print("[ETRADE]   Total articles after date filter: {}".format(len(all_articles)))
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ETRADE] No articles found. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            browser.close()
            return

        # Filter already processed
        new_articles = [a for a in all_articles
                       if a["id"] not in processed_ids]
        _safe_print("[ETRADE]   {} new (skipped {} already processed)".format(
            len(new_articles), len(all_articles) - len(new_articles)))
        sys.stdout.flush()

        if not new_articles:
            _safe_print("[ETRADE] Nothing new. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            browser.close()
            return

        # ===================================================================
        # Step 3: Fetch details + attachments (batch of 10)
        # ===================================================================
        _safe_print("[ETRADE] Step 3/4: Fetching {} articles in batches of {}...".format(
            len(new_articles), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_articles)
        success_count = 0
        fail_count = 0
        batch_num = 0
        etrade_start = time.time()

        for batch_start in range(0, total, BATCH_SIZE):
            # ── Runtime check for graceful stop ──
            elapsed = time.time() - etrade_start
            if elapsed > args.max_runtime - 120:
                _safe_print("[ETRADE]   Runtime {:.0f}s, stopping early. Next run will resume.".format(elapsed))
                break

            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                title_preview = art["title"][:60] if art["title"] else "(no title)"
                _safe_print("[ETRADE]   [{}/{}] {}".format(
                    global_idx, total, title_preview))
                sys.stdout.flush()

                # Build detail URL
                project_id = art["id"]
                raw = art.get("raw", {})
                type_param = raw.get("TYPE") or raw.get("type") or "%E9%87%87%E8%B4%AD%E5%85%AC%E5%91%8A"
                method_param = raw.get("METHOD") or raw.get("method") or "66030108"
                detail_url = _build_detail_url(project_id, str(type_param), str(method_param))

                # ---- Navigate to detail page ----
                detail_page = context.new_page()
                detail_page.set_default_timeout(60000)
                content_text = ""
                title = art["title"]
                attachments = []
                local_att_files = []

                for detail_attempt in range(3):
                    try:
                        detail_page.goto(detail_url,
                                        wait_until="domcontentloaded",
                                        timeout=60000)
                        # Wait for content to render
                        try:
                            detail_page.wait_for_selector(
                                "h1, h2, .detail-content, .project-detail, "
                                ".content, article, main, .el-main",
                                timeout=15000,
                            )
                        except Exception:
                            pass
                        detail_page.wait_for_timeout(2000)

                        detail = _extract_detail_from_page(detail_page)
                        content_text = detail.get("content_text", "")
                        if detail.get("title"):
                            title = detail["title"]
                        if detail.get("date_str"):
                            art["date_str"] = detail["date_str"]
                        attachments = detail.get("attachments", [])
                        break
                    except Exception as e:
                        logging.warning("Detail fetch failed for %s (attempt %d): %s",
                                      art["id"], detail_attempt + 1, e)
                        if detail_attempt < 2:
                            wait = (2 ** detail_attempt) + random.uniform(1, 3)
                            time.sleep(wait)

                # Download attachments while page is open
                if attachments:
                    att_dir = os.path.join(output_dir, "attachments", art["id"])
                    for att in attachments:
                        att_url = att.get("url", "")
                        if not att_url:
                            continue
                        if att_url.startswith("/"):
                            att_url = _QYCG_BASE + att_url
                        elif not att_url.startswith("http"):
                            from urllib.parse import urljoin
                            att_url = urljoin(_QYCG_BASE, att_url)

                        _safe_print("      [dl] {}".format(att.get("filename", "")[:50]))
                        sys.stdout.flush()

                        fp = _download_attachment(
                            detail_page, att_url, att_dir,
                            att.get("filename", "unknown"),
                        )
                        if fp:
                            local_att_files.append(fp)
                            # Check + extract ZIP
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

                # Close detail page
                try:
                    detail_page.close()
                except Exception:
                    pass

                if not content_text:
                    fail_count += 1
                    content_text = "标题: {}\n日期: {}\nURL: {}".format(
                        title, art.get("date_str", ""), detail_url)

                # ---- Build markdown ----
                date_str = art.get("date_str", "")
                lines = [
                    "# {}".format(title),
                    "**栏目:** {}".format(_SECTION_LABEL),
                    "**日期:** {}".format(date_str),
                    "**URL:** {}".format(detail_url),
                    "",
                    "## 正文",
                    "",
                    content_text,
                    "",
                ]

                if attachments:
                    lines.append("## 附件列表")
                    lines.append("")
                    for i, att in enumerate(attachments, 1):
                        lines.append("{}. **{}** — [{}]({})".format(
                            i, att.get("filename", "unknown"),
                            att.get("ext", "").upper().lstrip("."),
                            att.get("url", ""),
                        ))
                    lines.append("")

                md_content = "\n".join(lines)
                _save_markdown(md_content, output_dir, art["id"])
                md_parts.append(md_content)
                batch_ids.append(art["id"])

                # Collect files for upload
                article_md_path = os.path.join(output_dir, "articles", "{}.md".format(art["id"]))
                batch_files.append((article_md_path, "general"))
                for att_path in local_att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in _EXT_LAWS else "general"
                    batch_files.append((att_path, pid))

                success_count += 1
                _request_delay()

            # ── Checkpoint: save batch + upload + update state ──
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
                        _safe_print("[ETRADE]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload failed for batch %d: %s", batch_num, e)

                _safe_print("[ETRADE]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

        browser.close()

    # -- Mark complete -------------------------------------------------------
    state["completed"] = True
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[ETRADE] Done: {} articles processed ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[ETRADE] Total articles found: {}".format(len(all_articles)))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== ETRADE crawler finished: %d articles ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "enjoy5191_trade_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
