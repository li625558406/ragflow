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
Dedicated web crawler for zfcg.czt.fujian.gov.cn (福建省政府采购网).

Site characteristics
────────────────────
This site is built on the gpcms-center-web framework — a Vue.js SPA backed
by a Java / Spring Boot REST API.  All content is loaded asynchronously:

  • Homepage  →  Vue shell + JS bundles; no SSR.
  • Listings  →  REST calls to /gpcms/rest/web/v2/info/selectInfoMoreChannel
  • Details   →  REST calls to /gpcms/rest/web/v2/info/selectPAndNInfoById

The REST API requires a browser-like User-Agent and the cookies that are set
by the JavaScript on the homepage (regionCode, regionRemark, etc.).

This crawler uses Playwright (Chromium) to render the SPA and capture the
API responses, since direct curl requests fail with "nsssjss is null".

Modes
─────
  * Default mode: crawls news / announcement articles from channel pages.
  * KBDT mode (--kbdt): crawls the 开标大厅 (bid opening hall) via direct
    REST API (selectInfoForIndex + getInfoById).  Captcha solved via OCR.
    Attachments downloaded with Playwright session cookies.
    ZIP attachments are auto-extracted.

Architecture
────────────
  API base:     https://zfcg.czt.fujian.gov.cn/gpcms/rest/web/v2/
  Site ID:      d36a6e8b-4363-4b52-a00b-79ca47033923  (Fujian province)
  Channels:     88 channels including:
                  - 通知公告 (a8f44520...)
                  - 采购信息 (f582600e...)
                  - 政府采购项目公告 (6edf8f9a...)
                  - 省直预算项目公告 (9ca0d87d...)
  Article data: { id, title, channel, description (rich text), releaseTime, … }

Usage (typically spawned by task_executor):
    # Default news mode
    python zfcg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zfcg.czt.fujian.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME>

    # Bid opening hall (开标大厅) mode
    python zfcg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zfcg.czt.fujian.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME> \
        --kbdt \
        --region-name "福建省本级"

Requirements:
    playwright  (pip install playwright && playwright install chromium)
    ddddocr     (pip install ddddocr)
    Chrome installed at C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright (required)
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# ddddocr (optional — only needed in --kbdt mode)
# ---------------------------------------------------------------------------
try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_URL = "https://zfcg.czt.fujian.gov.cn"
_API_BASE = "/gpcms/rest/web/v2"

# Chrome executable paths (Windows first, then Linux fallbacks)
_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_CHANNEL_SECTIONS = {
    # These are the key procurement-related channels on Fujian's site.
    # The full channel list is fetched dynamically, but these are hard-coded
    # as known-good channels for the "文件类" (procurement documents) focus.
    "a8f44520-bad8-4f13-92f0-3765eacc6f71": "通知公告",
    "f582600e-065d-4f35-8966-48a33fa93863": "采购信息",
    "6edf8f9a-9c0e-4bcf-b7b9-ecf6826ed919": "政府采购项目公告",
    "9ca0d87d-48a9-4048-ba74-2b158989626b": "省直预算项目公告",
    "1def3926-6d73-47fc-9f92-aed723ef7178": "图片新闻",
    "8d90e7b4-3ada-4fc3-ae6d-fc3ddd0bf36e": "监督警示",
    "47bbf186-5544-4537-bef5-158d0c0923fa": "曝光台",
    "82087740-d05e-4fa6-a668-c8c224756a44": "采购指引",
    "0064f5d9-5be4-4cbb-8cf3-2cd473c738ca": "学习培训",
    "4cf1aede-fc8f-4742-8001-e32d872948b4": "采购信息公示",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _sanitize_filename(text, max_len=100):
    """Clean a string for use as a filesystem folder/file name."""
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name)
    name = name.strip("._ ")
    if not name:
        name = "untitled"
    return name[:max_len]


def _download_attachments_from_html(html_content, dest_dir, base_url=_SITE_URL):
    """Parse HTML description and download file attachments (PDF, DOC, etc.)."""
    if not html_content:
        return []
    downloaded = []
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href:
                continue
            ext = os.path.splitext(href.split("?")[0].split("#")[0])[1].lower()
            if ext not in (
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
            ):
                continue
            file_url = urljoin(base_url, href)
            fname = a_tag.get_text(strip=True) or os.path.basename(href.split("?")[0])
            if not fname or "." not in fname:
                fname = f"attachment_{len(downloaded)+1}{ext}"
            fname = re.sub(r'[\\/:*?"<>|]', "_", fname)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, fname)
            if os.path.exists(dest_path):
                downloaded.append(dest_path)
                continue
            try:
                resp = requests.get(file_url, headers=_HEADERS, timeout=60, stream=True)
                if resp.status_code == 200:
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                    downloaded.append(dest_path)
                else:
                    logging.warning("Download failed: %s HTTP %s", file_url, resp.status_code)
            except Exception as e:
                logging.warning("Download error: %s: %s", file_url, e)
    except Exception as e:
        logging.warning("HTML parse error: %s", e)
    return downloaded


def _upload_file_to_kb_folder(filepath, kb, tenant_id, parent_folder_id, parser_id="laws"):
    """Upload a local file to a specific KB folder and queue parsing.

    Creates a Document record, stores the blob in object storage, links it
    under *parent_folder_id* in the KB file tree, then enqueues parsing.
    """
    from api.db.services.document_service import DocumentService
    from api.db.services.file_service import FileService
    from api.utils.file_utils import filename_type
    import xxhash
    from pathlib import Path

    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        blob = f.read()
    doc_id = get_uuid()
    ftype = filename_type(filename)
    location = f"{parent_folder_id}/{doc_id}_{filename}"
    while settings.STORAGE_IMPL.obj_exist(kb.id, location):
        location += "_"
    settings.STORAGE_IMPL.put(kb.id, location, blob)

    doc = {
        "id": doc_id,
        "kb_id": kb.id,
        "parser_id": parser_id,
        "parser_config": kb.parser_config,
        "created_by": tenant_id,
        "type": ftype,
        "name": filename,
        "source_type": "local",
        "suffix": Path(filename).suffix.lstrip(".").lower(),
        "location": location,
        "size": len(blob),
        "content_hash": xxhash.xxh128(blob).hexdigest(),
    }
    DocumentService.insert(doc)
    FileService.add_file_from_kb(doc, parent_folder_id, tenant_id)

    if not os.environ.get("SKIP_PARSE", "").strip():
        try:
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)

    return doc_id


def _parse_date(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y年%m月%d日"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _find_chrome():
    """Return the first existing Chrome executable path, or None."""
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _retry_page_goto(page, url, wait_until="domcontentloaded",
                     timeout=60, max_retries=3, backoff_base=5):
    """Load a page with retry + exponential backoff on timeout.

    Does NOT change the original logic — if all retries fail the caller
    still gets the exception so downstream fallbacks (text extraction, etc.)
    work exactly as before.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            effective_timeout = timeout * (1 + attempt * 0.5)  # grow: 60→90→120
            page.set_default_timeout(effective_timeout * 1000)
            page.goto(url, wait_until=wait_until,
                      timeout=effective_timeout * 1000)
            return True
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                wait = backoff_base * attempt
                _safe_print(
                    f"[ZFCG]   Page load attempt {attempt} failed "
                    f"({type(e).__name__}), retrying in {wait}s..."
                )
                sys.stdout.flush()
                time.sleep(wait)
    # All retries exhausted — re-raise so caller's except handles it
    raise last_exc


# ---------------------------------------------------------------------------
# Playwright-based SPA rendering & API capture
# ---------------------------------------------------------------------------

def _capture_api_data(url=None, timeout=60):
    """Launch headless Chromium, load the homepage, capture API responses.

    Returns a dict with keys:
        site_id     – the deployment site UUID
        channels    – list of {id, name, pagemark, ...}
        articles    – dict of {channel_id: [article_dict, ...]}
    """
    result = {"site_id": "", "channels": [], "articles": {}}

    chrome_path = _find_chrome()
    if not chrome_path:
        _safe_print("[ZFCG] ERROR: Chrome not found. Check CHROME_PATHS.")
        return result

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[ZFCG] ERROR: playwright not installed. pip install playwright && playwright install chromium")
        return result

    # Clean up stale Chrome profile directories that can block launch
    import glob as _glob_zfcg
    import shutil as _shutil_zfcg
    for _d in _glob_zfcg.glob("/tmp/playwright_chromiumdev_profile-*"):
        try:
            _shutil_zfcg.rmtree(_d)
        except Exception:
            pass
    for _d in _glob_zfcg.glob("/tmp/.org.chromium.*"):
        try:
            _shutil_zfcg.rmtree(_d)
        except Exception:
            pass

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                timeout=60000,
            )
        except Exception:
            time.sleep(3)
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                timeout=60000,
            )
        page = browser.new_page()
        page.set_default_timeout(timeout * 1000)

        # Intercept API responses
        captured = {"site_id": "", "channels": None, "articles": {}}

        def on_response(resp):
            resp_url = resp.url
            if resp.status != 200:
                return

            try:
                ct = resp.headers.get("content-type", "")
                if "json" not in ct and "rest" not in resp_url:
                    return
                data = resp.json()
            except Exception:
                return

            code = data.get("code")
            msg = data.get("msg", "")
            if code != "200":
                return

            # 1) Site deployment info
            if "getDeploymentSiteId" in resp_url:
                site_data = data.get("data")
                if site_data and isinstance(site_data, dict):
                    captured["site_id"] = site_data.get("id", "")

            # 2) Channel list
            if "index/list" in resp_url and "site=" in resp_url:
                chs = data.get("data", [])
                if isinstance(chs, list):
                    captured["channels"] = [
                        {"id": c["id"], "name": c["name"], "pagemark": c.get("pagemark", "")}
                        for c in chs if c.get("id") and c.get("name")
                    ]

            # 3) Article listings
            if "selectInfoMoreChannel" in resp_url:
                rows = data.get("data", {}).get("rows", [])
                if isinstance(rows, list) and rows:
                    # Extract channel ID from URL
                    import urllib.parse
                    qs = urllib.parse.urlparse(resp_url).query
                    qp = urllib.parse.parse_qs(qs)
                    ch_id = qp.get("channel", [""])[0]
                    if ch_id:
                        if ch_id not in captured["articles"]:
                            captured["articles"][ch_id] = []
                        captured["articles"][ch_id].extend(rows)

        page.on("response", on_response)

        # Load the SPA with retry (Chinese gov sites can be slow)
        target_url = url or _SITE_URL
        _safe_print(f"[ZFCG] Loading SPA at {target_url}...")
        sys.stdout.flush()
        try:
            _retry_page_goto(page, target_url, wait_until="domcontentloaded",
                             timeout=timeout, max_retries=3)
            # Wait for Vue app to initialise and make API calls
            _safe_print("[ZFCG] Waiting for SPA to render...")
            sys.stdout.flush()
            page.wait_for_timeout(8000)  # generous wait for all API calls
        except Exception as e:
            logging.warning("SPA page load may be incomplete: %s", e)
            page.wait_for_timeout(5000)

        # Also grab visible page text for articles not fully captured via API
        try:
            page_text = page.evaluate("() => document.body.innerText")
            result["page_text"] = page_text
        except Exception:
            result["page_text"] = ""

        browser.close()

    result["site_id"] = captured["site_id"]
    result["channels"] = captured["channels"] or []
    result["articles"] = captured["articles"]
    return result


# ---------------------------------------------------------------------------
# Fallback: extract articles from rendered page text
# ---------------------------------------------------------------------------

def _extract_articles_from_text(page_text, section_label):
    """Simple regex-based extraction from rendered page text.

    The homepage renders article titles and dates as plain text.
    This is a best-effort fallback when the API capture is incomplete.
    """
    articles = []
    # Pattern: look for date + title combinations
    # Typical: "2026-04-13  关于2026年全省政府采购..."
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
    i = 0
    while i < len(lines):
        m = date_pattern.search(lines[i])
        if m:
            date_str = m.group(1)
            title = lines[i][m.end():].strip()
            # Next line might be continuation of title
            if not title and i + 1 < len(lines):
                title = lines[i + 1].strip()
                i += 1
            if title and len(title) > 4:
                articles.append({
                    "title": title,
                    "date_str": date_str,
                    "section": section_label,
                })
        i += 1
    return articles


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------

def _format_article_md(article, section_label, source_url):
    """Format an article dict as Markdown."""
    title = article.get("title", "").strip()
    if not title:
        return ""

    date_str = article.get("releaseTime", "") or article.get("date_str", "") or ""
    if date_str and len(date_str) > 10:
        date_str = date_str[:10]

    # Description is the main content body (rich text)
    desc = article.get("description", "") or ""

    # Build markdown
    lines = [
        f"# {title}",
        f"**Section:** {section_label}",
    ]
    if date_str:
        lines.append(f"**Date:** {date_str}")
    lines.append(f"**Source:** {source_url}")
    if article.get("author"):
        lines.append(f"**Author:** {article['author']}")
    lines.append("")

    if desc:
        lines.append(desc)
    else:
        lines.append("（暂无详细内容）")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed IDs)",
                 len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    """Upload a file to KB and queue parsing."""
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
# KBDT (开标大厅) mode
# ---------------------------------------------------------------------------

_KBDT_DEFAULT_SITE_ID = "d36a6e8b-4363-4b52-a00b-79ca47033923"
_KBDT_DEFAULT_CHANNEL_ID = "f582600e-065d-4f35-8966-48a33fa93863"

# JavaScript snippet that calls the website's own Vue 2 $http (axios).
# The website's axios interceptors add the required ``nsssjss`` signature
# header which cannot be replicated without the server-side AES key.
_VUE_HTTP_JS = """
([url, opts]) => {
    return new Promise((resolve) => {
        const vm = document.querySelector('#app').__vue__;
        const http = vm.$http;
        http.get(url, opts).then(resp => {
            if (opts.responseType === 'arraybuffer') {
                let bytes = new Uint8Array(resp.data);
                let binary = '';
                bytes.forEach(b => binary += String.fromCharCode(b));
                resolve({b64: btoa(binary)});
            } else {
                resolve(resp.data);
            }
        }).catch(err => {
            resolve({error: err.message});
        });
    });
}
"""


def _vue_http_get(page, url, params=None, response_type=None):
    """Call an API through the website's Vue 2 $http (axios with interceptors)."""
    opts = {"params": params or {}}
    if response_type:
        opts["responseType"] = response_type
    return page.evaluate(_VUE_HTTP_JS, [url, opts])


def _kbdt_solve_captcha(page, ocr, site_id, channel_id, max_retries=15):
    """Fetch captcha via Vue 2 $http, OCR it, and verify against the API.

    Returns the verified captcha code string, or None on failure.
    """
    import base64

    for attempt in range(1, max_retries + 1):
        cap = _vue_http_get(
            page,
            f"{_API_BASE}/index/getVerify",
            params={"_t": str(int(time.time() * 1000))},
            response_type="arraybuffer",
        )
        if cap.get("error") or not cap.get("b64"):
            logging.warning("Captcha fetch failed (attempt %d): %s", attempt, cap.get("error"))
            time.sleep(0.3)
            continue

        img_bytes = base64.b64decode(cap["b64"])
        code_val = ocr.classification(img_bytes).strip()

        # Quick verification against the search API
        check = _vue_http_get(page, f"{_API_BASE}/info/selectInfoForIndex", params={
            "siteId": site_id,
            "channel": channel_id,
            "currPage": "1", "pageSize": "1",
            "noticeType": "00101", "regionCode": "350001",
            "operationStartTime": "2020-01-01 00:00:00",
            "operationEndTime": "2030-01-01 00:00:00",
            "verifyCode": code_val,
            "selectTimeName": "openTenderTime",
        })
        if check.get("code") == "200":
            return code_val

        time.sleep(0.3)

    return None


def _kbdt_get_detail(page, record, site_id, channel_id):
    """Fetch full detail for a bid record via getInfoById API.

    Returns the detail dict or None.
    """
    try:
        data = _vue_http_get(page, f"{_API_BASE}/info/getInfoById", params={
            "id": record.get("id", ""),
            "planId": record.get("planId", ""),
            "channel": record.get("channel", channel_id),
            "siteId": site_id,
        })
        if data.get("code") == "200":
            return data.get("data", {})
        _safe_print(f"[KBDT]   Detail API error: {data.get('msg')}")
    except Exception as e:
        logging.warning("Detail API failed for %s: %s", record.get("id"), e)
    return None


def _extract_text_from_file(filepath):
    """Extract plain text from a local file (PDF, DOCX, XLSX, TXT, etc.).

    Returns the text string, or None on failure.
    """
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber
            parts = []
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        parts.append(text)
            return "\n\n".join(parts)
        elif ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append(f"### {ws.title}\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)
    return None


def _kbdt_format_content(record, detail, local_files=None):
    """Build a Markdown document from a bid record, its detail, and attachments."""
    title = record.get("title", "无标题")
    lines = [f"# {title}", ""]

    meta = [
        ("开标时间", record.get("openTenderTime", "")),
        ("区域", record.get("regionName", "")),
        ("采购人", record.get("purchaser", "")),
        ("代理机构", record.get("agency", "")),
        ("项目编号", record.get("openTenderCode", "")),
        ("预算金额", record.get("budget", "")),
        ("采购方式", record.get("purchaseManner", "")),
        ("采购品目", record.get("catalogueNameList", "")),
    ]
    for k, v in meta:
        if v:
            if k == "采购方式":
                v = {"1": "公开招标", "2": "邀请招标", "3": "竞争性谈判",
                     "4": "竞争性磋商", "5": "询价", "6": "单一来源"}.get(v, v)
            lines.append(f"**{k}：** {v}")
    lines.append("")

    content_html = detail.get("content") or record.get("description") or ""
    if content_html:
        lines.append("## 详细内容")
        lines.append("")
        try:
            soup = BeautifulSoup(content_html, "html.parser")
            lines.append(soup.get_text(separator="\n", strip=True))
        except Exception:
            lines.append(content_html)
    else:
        lines.append("（暂无详细内容）")
    lines.append("")

    # Embed attachment contents into the MD
    if local_files:
        lines.append("## 附件内容")
        lines.append("")
        for fp in local_files:
            fname = os.path.basename(fp)
            text = _extract_text_from_file(fp)
            if text and text.strip():
                lines.append(f"### {fname}")
                lines.append("")
                # Truncate very long attachments to avoid oversized chunks
                if len(text) > 50000:
                    text = text[:50000] + "\n\n（内容过长，已截断）"
                lines.append(text)
                lines.append("")
            else:
                lines.append(f"### {fname}")
                lines.append("")
                lines.append("（无法提取文本内容）")
                lines.append("")

    return "\n".join(lines)


def _kbdt_download_file(page, file_info, dest_dir, download_base_url):
    """Download a single attachment and return local path, or None.

    *download_base_url* is the base URL for file downloads (always the parent
    domain ``https://zfcg.czt.fujian.gov.cn`` because sub-domains do not
    host the file download service).
    """
    import base64
    import requests as _requests

    file_url = file_info.get("fileUrl", "")
    file_ext = file_info.get("fileExt", "")
    file_name = re.sub(
        r'[\\/:*?"<>|]', "_",
        file_info.get("fileName") or file_info.get("name") or "unknown",
    )
    # Append file extension from API if file_name lacks one
    if file_ext and not file_name.lower().endswith(file_ext.lower()):
        file_name = file_name + file_ext
    if not file_url:
        return None

    # Normalize download URL: strip any origin and /freecms/download/ prefix
    from urllib.parse import urlparse
    # Strip any origin (http or https, any host)
    parsed = urlparse(file_url)
    file_url = parsed.path
    if parsed.query:
        file_url = file_url + "?" + parsed.query

    for prefix in (
        "/freecms/download/gateway/gpx-document-zc/common/v3/base/download/",
        "/freecms/download/",
    ):
        if file_url.startswith(prefix):
            file_url = file_url[len(prefix):]
            break

    # downloadPublicFile path also doesn't work; only gpx-public-file does
    file_url = re.sub(r"^/?downloadPublicFile(\?)", r"/gpx-public-file\1", file_url)

    # Ensure leading slash after prefix stripping
    if not file_url.startswith("/"):
        file_url = "/" + file_url

    # Always use the parent domain for file downloads
    file_url = f"{download_base_url}{file_url}"

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, file_name)

    if os.path.exists(dest_path):
        return dest_path

    # Strategy 1: $http (works when download_base_url == page origin, no CORS)
    try:
        result = page.evaluate("""
            ([url]) => {
                return new Promise((resolve) => {
                    const vm = document.querySelector('#app').__vue__;
                    const http = vm.$http;
                    http.get(url, {
                        responseType: 'arraybuffer',
                        timeout: 120000
                    }).then(resp => {
                        let bytes = new Uint8Array(resp.data);
                        let binary = '';
                        bytes.forEach(b => binary += String.fromCharCode(b));
                        resolve({b64: btoa(binary), size: bytes.length});
                    }).catch(err => {
                        resolve({error: err.message});
                    });
                });
            }
        """, [file_url])

        if result.get("b64"):
            blob = base64.b64decode(result["b64"])
            if len(blob) >= 100:
                with open(dest_path, "wb") as f:
                    f.write(blob)
                _safe_print(f"[KBDT]   Downloaded: {file_name}")
                return dest_path
    except Exception:
        pass

    # Strategy 2: requests (handles CORS / cross-domain downloads)
    try:
        context = page.context
        cookies = context.cookies()
        sess = _requests.Session()
        sess.cookies.update({ck["name"]: ck["value"] for ck in cookies})
        sess.headers.update({"User-Agent": _USER_AGENT, "Referer": f"{download_base_url}/"})
        r = sess.get(file_url, timeout=120)
        if r.ok and len(r.content) >= 100 and b"<html" not in r.content[:20]:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            _safe_print(f"[KBDT]   Downloaded: {file_name}")
            return dest_path
        else:
            _safe_print(f"[KBDT]   Download error for {file_name}: HTTP {r.status_code}, {len(r.content)} bytes")
    except Exception as e:
        _safe_print(f"[KBDT]   Download failed for {file_name}: {e}")

    return None


def _kbdt_extract_zip(zip_path):
    """Extract a ZIP file alongside it, return list of extracted file paths.

    The original ZIP is removed after extraction.
    """
    import zipfile
    extracted = []
    dest_dir = os.path.dirname(zip_path)
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
                _safe_print(f"[KBDT]   Extracted: {safe_name}")
        os.remove(zip_path)
    except Exception as e:
        _safe_print(f"[KBDT]   ZIP extract error for {os.path.basename(zip_path)}: {e}")
    return extracted


def _kbdt_search_listings(page, verify_code, start_date, end_date,
                          site_id, channel_id, region_code="350001",
                          page_num=1, page_size=20):
    """Call selectInfoForIndex API via the website's Vue 2 $http."""
    data = _vue_http_get(page, f"{_API_BASE}/info/selectInfoForIndex", params={
        "siteId": site_id,
        "channel": channel_id,
        "currPage": str(page_num),
        "pageSize": str(page_size),
        "noticeType": "00101",
        "regionCode": region_code,
        "cityOrArea": "",
        "purchaseManner": "",
        "openTenderCode": "",
        "purchaser": "",
        "agency": "",
        "purchaseNature": "",
        "operationStartTime": start_date,
        "operationEndTime": end_date,
        "verifyCode": verify_code,
        "selectTimeName": "openTenderTime",
    })
    if data.get("code") == "200":
        rows = data.get("data", {}).get("rows", [])
        if not rows and page_num == 1:
            total = data.get("data", {}).get("total", "?")
            _safe_print(f"[KBDT]   Search API returned 0 rows (total={total}), params: siteId={site_id}, channel={channel_id}, regionCode={region_code}")
        return rows
    _safe_print(f"[KBDT]   API error: code={data.get('code')}, msg={data.get('msg')}, full={data}")
    return None


def _kbdt_upload_file(kb, tenant_id, filepath, parent_folder_id, parser_id="laws"):
    """Store a file into KB object storage, create a Document record,
    link it under *parent_folder_id*, then enqueue parsing."""
    doc_id = _upload_file_to_kb_folder(
        filepath, kb, tenant_id, parent_folder_id, parser_id=parser_id,
    )
    _safe_print(f"[KBDT]   Uploaded: {os.path.basename(filepath)}")
    return doc_id


def _kbdt_upload_record(kb, tenant_id, kb_folder, folder_name, content_md,
                         local_files, output_dir, record=None, detail=None):
    """
    Create a per-record sub-folder in the KB file tree and upload:
      1. {folder_name}.md (general parser) — metadata + detail content
      2. Attachment files (laws parser) — original PDF/DOCX/etc.
      3. {folder_name}-全文.md (general parser) — merged MD with attachment text
    """
    from api.db.services.file_service import FileService
    from api.db import FileType

    record_folder = FileService.new_a_file_from_kb(
        tenant_id, folder_name, kb_folder["id"], ty=FileType.FOLDER.value,
    )

    staging = os.path.join(output_dir, "kbdt_staging", folder_name)
    os.makedirs(staging, exist_ok=True)

    # 1. Upload MD (metadata + detail only)
    md_filename = f"{folder_name}.md"
    content_path = os.path.join(staging, md_filename)
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(content_md)
    _kbdt_upload_file(kb, tenant_id, content_path, record_folder["id"], parser_id="general")

    # 2. Upload original attachments with RAGFlow parsers
    ext_laws = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".txt"}
    for fp in local_files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in (".zip", ".rar", ".7z"):
            continue  # archives are already extracted; skip uploading them
        pid = "laws" if ext in ext_laws else "general"
        try:
            _kbdt_upload_file(kb, tenant_id, fp, record_folder["id"], parser_id=pid)
        except Exception as e:
            _safe_print(f"[KBDT]   Upload error for {os.path.basename(fp)}: {e}")



def _kbdt_crawl(args):
    """开标大厅 (bid opening hall) crawl.

    All API calls go through the website's own Vue 2 $http (axios) which
    automatically adds the required ``nsssjss`` signature header.

    Flow:
      1. Playwright: load homepage → Vue app initialises
      2. Solve captcha via Vue $http + ddddocr (verified against API)
      3. Call selectInfoForIndex API (paginated) via Vue $http
      4. For each record:
         a. getInfoById for full detail (attachments, content)
         b. Download attachments via Vue $http
         c. Extract ZIP attachments
         d. Build MD with metadata + detail
         e. Upload: MD + original attachments + merged full-text MD
    """
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService

    region_name = args.region_name or "福建省本级"
    site_url = args.target_url.rstrip("/")
    site_id = args.site_id or _KBDT_DEFAULT_SITE_ID
    channel_id = args.channel_id or _KBDT_DEFAULT_CHANNEL_ID
    region_code = args.region_code or "350001"
    # File download service is always on the parent domain, not sub-domains
    download_base_url = "https://zfcg.czt.fujian.gov.cn"
    _safe_print(f"[KBDT] Region: {region_name}")
    _safe_print(f"[KBDT] Site URL: {site_url}")
    _safe_print(f"[KBDT] Download base: {download_base_url}")
    _safe_print(f"[KBDT] Region Code: {region_code}")
    _safe_print(f"[KBDT] Site ID: {site_id}")
    _safe_print(f"[KBDT] Channel ID: {channel_id}")

    # ── Validate dependencies ──────────────────────────────────────────
    chrome_path = _find_chrome()
    if not chrome_path:
        _safe_print("[KBDT] ERROR: Chrome not found. Check CHROME_PATHS.")
        return
    if not DDDDOCR_AVAILABLE:
        _safe_print("[KBDT] ERROR: ddddocr not installed (pip install ddddocr).")
        return

    # ── KB setup ───────────────────────────────────────────────────────
    ok, kb = KnowledgebaseService.get_by_id(args.kb_id)
    if not ok:
        _safe_print(f"[KBDT] ERROR: Knowledge base {args.kb_id} not found.")
        return

    kb_root_folder = FileService.get_kb_folder(args.tenant_id)
    kb_folder = FileService.new_a_file_from_kb(
        args.tenant_id, kb.name, kb_root_folder["id"],
    )

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip(),
    )
    os.makedirs(output_dir, exist_ok=True)

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))

    # ── Date range: today 00:00 → +2 months ────────────────────────────
    now = datetime.now()
    start_date = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d 00:00:00")
    end_date = (now + timedelta(days=60)).strftime("%Y-%m-%d 00:00:00")
    _safe_print(f"[KBDT] Date range: {start_date} ~ {end_date}")

    # ── Overall timer (avoid 3600s task_executor timeout) ───────────────
    kbdt_start = time.time()

    # ── Playwright ─────────────────────────────────────────────────────
    # Clean up stale Chrome profile directories that can block launch
    import glob as _glob
    import shutil as _shutil
    for _d in _glob.glob("/tmp/playwright_chromiumdev_profile-*"):
        try:
            _shutil.rmtree(_d)
        except Exception:
            pass
    for _d in _glob.glob("/tmp/.org.chromium.*"):
        try:
            _shutil.rmtree(_d)
        except Exception:
            pass

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                timeout=60000,  # browser launch timeout
            )
        except Exception as e:
            _safe_print(f"[KBDT] ERROR: Browser launch failed: {e}")
            _safe_print("[KBDT]   Retrying after cleanup...")
            sys.stdout.flush()
            time.sleep(3)
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                timeout=60000,
            )

        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        # Step 1: Load homepage → Vue app initialises its axios interceptors
        _safe_print("[KBDT] Step 1/4: Loading homepage …")
        _retry_page_goto(page, site_url, wait_until="domcontentloaded",
                         timeout=60, max_retries=3)
        page.wait_for_timeout(5000)

        # Auto-discover site_id, region_code and region_name if not manually specified
        if not args.site_id or not args.region_code or not args.region_name:
            _safe_print("[KBDT]   Discovering site_id / region_code / region_name …")
            discovered_site_id = ""
            discovered_region_code = ""
            discovered_region_name = ""

            def _on_api_resp(resp):
                nonlocal discovered_site_id, discovered_region_code, discovered_region_name
                if resp.status != 200:
                    return
                try:
                    data = resp.json()
                except Exception:
                    return
                if data.get("code") != "200":
                    return
                url_path = resp.url
                # getDeploymentSiteId → site ID + region code + name
                if "getDeploymentSiteId" in url_path:
                    sd = data.get("data")
                    if isinstance(sd, dict):
                        if sd.get("id"):
                            discovered_site_id = sd["id"]
                        if sd.get("regionCode"):
                            discovered_region_code = sd["regionCode"]
                        if sd.get("name"):
                            discovered_region_name = sd["name"]

            page.on("response", _on_api_resp)
            try:
                _retry_page_goto(page, site_url, wait_until="domcontentloaded",
                                 timeout=60, max_retries=2)
            except Exception as e:
                logging.warning("KBDT auto-discovery reload failed: %s", e)
            page.wait_for_timeout(5000)
            page.remove_listener("response", _on_api_resp)

            if not args.site_id and discovered_site_id:
                site_id = discovered_site_id
                _safe_print(f"[KBDT]   Auto site_id: {site_id}")
            if not args.region_code and discovered_region_code:
                region_code = discovered_region_code
                _safe_print(f"[KBDT]   Auto region_code: {region_code}")
            if not args.region_name and discovered_region_name:
                region_name = discovered_region_name
                _safe_print(f"[KBDT]   Auto region_name: {region_name}")

        # Step 2: Solve captcha (with built-in verification)
        _safe_print("[KBDT] Step 2/4: Solving captcha …")
        ocr = ddddocr.DdddOcr(show_ad=False)
        verify_code = _kbdt_solve_captcha(page, ocr, site_id, channel_id)
        if not verify_code:
            _safe_print("[KBDT]   ERROR: Could not solve captcha after retries.")
            browser.close()
            return
        _safe_print(f"[KBDT]   Captcha verified: {verify_code}")

        # Step 3: Fetch all listings (paginated)
        _safe_print("[KBDT] Step 3/4: Fetching listings …")
        all_records = []
        page_size = 20
        for pg in range(1, 101):
            try:
                rows = _kbdt_search_listings(
                    page, verify_code, start_date, end_date,
                    site_id, channel_id, region_code,
                    page_num=pg, page_size=page_size,
                )
                if rows is None:
                    # verifyCode might have expired → re-solve
                    _safe_print("[KBDT]   verifyCode expired, re-solving …")
                    verify_code = _kbdt_solve_captcha(page, ocr, site_id, channel_id)
                    if not verify_code:
                        _safe_print("[KBDT]   Captcha re-solve failed, stopping.")
                        break
                    rows = _kbdt_search_listings(
                        page, verify_code, start_date, end_date,
                        site_id, channel_id, region_code,
                        page_num=pg, page_size=page_size,
                    )
                if not rows:
                    break
                all_records.extend(rows)
                _safe_print(f"[KBDT]   Page {pg}: {len(rows)} records (total: {len(all_records)})")
                if len(rows) < page_size:
                    break
            except Exception as e:
                _safe_print(f"[KBDT]   Page {pg} error: {e}")
                break

        total_records = len(all_records)
        _safe_print(f"[KBDT]   Total records: {total_records}")

        if not all_records:
            _safe_print("[KBDT]   No records found.")
            browser.close()
            return

        # Step 4: Process each record (with time-bounded execution)
        _safe_print(f"[KBDT] Step 4/4: Processing {total_records} records …")
        sys.stdout.flush()

        new_ids = []
        stopped_early = False
        for idx, record in enumerate(all_records, 1):
            # ── Time-bounded check ─────────────────────────────────────
            elapsed = time.time() - kbdt_start
            remaining = args.max_runtime - elapsed
            if remaining < 120:  # less than 2 min left, stop gracefully
                _safe_print(
                    f"\n[KBDT]   Runtime {elapsed:.0f}s, "
                    f"stopping early (saved {len(new_ids)} records). "
                    f"Next run will resume."
                )
                sys.stdout.flush()
                stopped_early = True
                break

            record_id = record.get("id", "")
            if not record_id or record_id in processed_ids:
                continue

            title = record.get("title", "无标题")
            open_time = (record.get("openTenderTime") or "")[:10]
            folder_name = re.sub(
                r'[\\/:*?"<>|]', "_",
                f"{region_name}-{title}-{open_time}",
            )[:200]

            _safe_print(f"[KBDT]   [{idx}/{total_records}] {title[:50]}…")
            sys.stdout.flush()

            # 4a. Get full detail
            detail = _kbdt_get_detail(page, record, site_id, channel_id)

            # 4b. Download attachments
            att_list = (detail or {}).get("attchList") or []
            local_files = []
            for att in att_list:
                dest_dir = os.path.join(output_dir, "kbdt_downloads", folder_name[:80])
                fp = _kbdt_download_file(page, att, dest_dir, download_base_url)
                if fp:
                    local_files.append(fp)
                    # 4c. Extract ZIP attachments
                    is_zip = (
                        fp.lower().endswith(".zip")
                        or att.get("fileExt", "").lower() == ".zip"
                        or (os.path.getsize(fp) >= 4 and open(fp, "rb").read(4) == b"PK\x03\x04")
                    )
                    if is_zip:
                        extracted = _kbdt_extract_zip(fp)
                        local_files.extend(extracted)

            # 4d. Build MD (metadata + detail, without attachment text)
            content_md = _kbdt_format_content(record, detail or {})

            # 4e. Upload to KB
            try:
                _kbdt_upload_record(
                    kb, args.tenant_id, kb_folder,
                    folder_name, content_md, local_files, output_dir,
                    record=record, detail=detail or {},
                )
                _safe_print("[KBDT]     Uploaded")
                new_ids.append(record_id)
            except Exception as e:
                _safe_print(f"[KBDT]     Upload error: {e}")
                logging.exception("KB upload error for %s", record_id)
            sys.stdout.flush()

            # ── Incremental state after every record ────────────────────
            if record_id:
                processed_ids.add(record_id)
                _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # ── Save state ──────────────────────────────────────────────────
        if new_ids:
            _save_state(output_dir, {"processed_ids": list(processed_ids)})
        browser.close()

    _safe_print(f"\n[KBDT] {'='*50}")
    if stopped_early:
        _safe_print(f"[KBDT] Partial run: {len(new_ids)} record(s). Resuming next time.")
    else:
        _safe_print(f"[KBDT] Done. Processed {len(new_ids)} new record(s).")
    _safe_print(f"{'='*50}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="zfcg.czt.fujian.gov.cn (福建省政府采购网) crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://zfcg.czt.fujian.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated channel names (default: all procurement-related)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=30,
                        help="Max age in days for articles (default: 30)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds before graceful stop (default: 3300 = 55 min)")
    parser.add_argument("--no-kbdt", action="store_true",
                        help="Disable 开标大厅 mode (use default news mode)")
    parser.add_argument("--region-name", default="福建省本级",
                        help="Region name for folder naming (default: 福建省本级)")
    parser.add_argument("--region-code", default=None,
                        help="Region code for search API (auto-discovered if omitted)")
    parser.add_argument("--site-id", default=None,
                        help="KBDT site ID (auto-discovered if omitted)")
    parser.add_argument("--channel-id", default=None,
                        help="KBDT channel ID (auto-discovered if omitted)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[ZFCG] 福建省政府采购网 crawler")
    _safe_print(f"[ZFCG] Target URL: {args.target_url}")
    _safe_print(f"[ZFCG] Task name: {args.task_name}")
    _safe_print(f"[ZFCG] Target KB: {args.kb_id}")
    _safe_print(f"[ZFCG] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[ZFCG] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()

    # ── Kbdt mode (default) ─────────────────────────────────────────────────
    if not args.no_kbdt:
        _kbdt_crawl(args)
        return

    logging.info("=== ZFCG crawler started for %s ===", args.target_url)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZFCG] Output directory: {output_dir}\n")
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[ZFCG] Already processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # ===================================================================
    # Step 1: Load SPA & capture API data
    # ===================================================================
    _safe_print("[ZFCG] Step 1/4: Loading SPA and capturing API data...")
    _safe_print("[ZFCG]   (This uses Playwright + Chrome to render the Vue app)")
    sys.stdout.flush()

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[ZFCG] ERROR: playwright not installed. Run:")
        _safe_print("    pip install playwright && playwright install chromium")
        sys.stdout.flush()
        sys.exit(1)

    cap = _capture_api_data(url=args.target_url, timeout=60)

    site_id = cap.get("site_id", "")
    channels = cap.get("channels", [])
    articles_by_channel = cap.get("articles", {})
    page_text = cap.get("page_text", "")

    if not site_id:
        _safe_print("[ZFCG] WARNING: Could not obtain site ID from API.")
        _safe_print("[ZFCG]   Falling back to text extraction from rendered page.\n")
        sys.stdout.flush()
    else:
        _safe_print(f"[ZFCG] Site ID: {site_id}")
        _safe_print(f"[ZFCG] Channels found: {len(channels)}")
        _safe_print(f"[ZFCG] Channels with articles: {len(articles_by_channel)}\n")
        sys.stdout.flush()

    # ===================================================================
    # Step 2: Collect articles from relevant channels
    # ===================================================================
    _safe_print("[ZFCG] Step 2/4: Collecting articles...\n")
    sys.stdout.flush()

    # Determine which channels to use
    if args.section:
        # User-specified channel names
        selected_chs = {}
        for name in args.section.split(","):
            name = name.strip()
            for ch_id, ch_name in _CHANNEL_SECTIONS.items():
                if ch_name == name:
                    selected_chs[ch_id] = ch_name
        if not selected_chs:
            _safe_print(f"[ZFCG] WARNING: No matching channels for '{args.section}', using all")
            sys.stdout.flush()
            selected_chs = dict(_CHANNEL_SECTIONS)
    else:
        selected_chs = dict(_CHANNEL_SECTIONS)

    # Map channel IDs from the live API to our section names
    live_channel_map = {c["id"]: c["name"] for c in channels}

    _safe_print(f"[ZFCG] Target channels: {len(selected_chs)}")
    for ch_id, ch_name in selected_chs.items():
        live_name = live_channel_map.get(ch_id, ch_name)
        _safe_print(f"         - {live_name} ({ch_id[:12]}...)")
    sys.stdout.flush()

    # Collect all articles
    all_articles = []  # list of dicts with keys: id, title, date_str, section, description, author
    cutoff = datetime.now() - timedelta(days=args.max_days)

    for ch_id, ch_name in selected_chs.items():
        # Try API-captured articles first
        raw_articles = articles_by_channel.get(ch_id, [])
        if not raw_articles:
            _safe_print(f"[ZFCG]   Channel '{ch_name}': no API data, skipping")
            sys.stdout.flush()
            continue

        ch_articles = []
        for art in raw_articles:
            art_id = art.get("id", "")
            if not art_id:
                continue

            # Date filter
            date_str = (art.get("releaseTime") or "")[:10]
            if not date_str:
                # Some articles carry date in a nested field
                date_str = (art.get("createDate") or "")[:10]
            if not date_str:
                date_str = (art.get("pubdate") or "")[:10]
            dt = _parse_date(date_str) if date_str else None
            if dt and dt < cutoff:
                continue
            if dt and dt.year and dt.year < 2023:
                continue

            ch_articles.append({
                "id": art_id,
                "title": art.get("title", "").strip(),
                "date_str": date_str,
                "section": ch_name,
                "description": art.get("description", "") or "",
                "author": art.get("author", "") or "",
                "releaseTime": art.get("releaseTime", "") or "",
            })

        _safe_print(f"[ZFCG]   Channel '{ch_name}': {len(ch_articles)} article(s)")
        sys.stdout.flush()

        # Limit per section
        if args.max_articles and len(ch_articles) > args.max_articles:
            ch_articles = ch_articles[:args.max_articles]

        all_articles.extend(ch_articles)

    # Fallback: if no articles via API, try text extraction from page
    if not all_articles and page_text:
        _safe_print("[ZFCG]   No API articles found, trying text extraction...")
        sys.stdout.flush()
        text_articles = _extract_articles_from_text(page_text, "采购信息")
        for art in text_articles:
            art["id"] = get_uuid()[:12]
            art["description"] = ""
            art["author"] = ""
        all_articles = text_articles
        _safe_print(f"[ZFCG]   Extracted {len(all_articles)} article(s) from page text\n")
        sys.stdout.flush()

    if not all_articles:
        _safe_print("[ZFCG] No articles found.\n")
        sys.stdout.flush()
        return

    # Filter already-processed
    if processed_ids:
        new_articles = [a for a in all_articles if a["id"] not in processed_ids]
        skipped = len(all_articles) - len(new_articles)
        if skipped:
            _safe_print(f"[ZFCG] Skipping {skipped} already-processed article(s)\n")
            sys.stdout.flush()
        all_articles = new_articles

    if not all_articles:
        _safe_print("[ZFCG] All articles already processed.\n")
        sys.stdout.flush()
        return

    _safe_print(f"[ZFCG] New articles to process: {len(all_articles)}\n")
    sys.stdout.flush()

    # ===================================================================
    # Step 3: Format as Markdown
    # ===================================================================
    _safe_print(f"[ZFCG] Step 3/4: Formatting {len(all_articles)} article(s)...\n")
    sys.stdout.flush()

    formatted = []
    for idx, art in enumerate(all_articles, 1):
        _safe_print(f"[ZFCG]   [{idx}/{len(all_articles)}] {art['title'][:60]}")
        sys.stdout.flush()

        md = _format_article_md(art, art["section"], args.target_url)
        if md:
            formatted.append((art, md))
            _save_markdown(md, output_dir, art["id"])

    _safe_print("")
    sys.stdout.flush()

    if not formatted:
        _safe_print("[ZFCG] No articles formatted.\n")
        sys.stdout.flush()
        return

    # ===================================================================
    # Step 4: Upload per-article folders to KB
    # ===================================================================
    _safe_print(f"[ZFCG] Step 4/4: Uploading {len(formatted)} article(s) to KB...\n")
    sys.stdout.flush()

    # Create KB parent folder once
    kb_parent = None
    if args.kb_id:
        from api.db.services.knowledgebase_service import KnowledgebaseService
        from api.db.services.file_service import FileService

        ok, kb = KnowledgebaseService.get_by_id(args.kb_id)
        if not ok:
            _safe_print(f"[ZFCG] ERROR: KB {args.kb_id} not found, skipping upload.")
            sys.stdout.flush()
            args.kb_id = None
        else:
            _safe_print(f"[ZFCG]   KB: {kb.name}")
            sys.stdout.flush()
            kb_root_folder = FileService.get_kb_folder(args.tenant_id)
            kb_parent = FileService.new_a_file_from_kb(
                args.tenant_id, kb.name, kb_root_folder["id"],
            )

    new_ids = []
    for idx, (art, md_content) in enumerate(formatted, 1):
        title_short = art.get("title", "无标题")[:50]
        _safe_print(f"[ZFCG]   [{idx}/{len(formatted)}] {title_short}")
        sys.stdout.flush()

        article_id = art.get("id", "")
        date_str = (art.get("releaseTime") or art.get("date_str") or "")[:10]
        folder_name = _sanitize_filename(f"{date_str}_{art['title']}", max_len=120)
        staging = os.path.join(output_dir, "articles", folder_name)
        os.makedirs(staging, exist_ok=True)

        if args.kb_id and kb_parent:
            try:
                # Step 4a: Download attachments from description HTML
                description_html = art.get("description", "")
                att_files = _download_attachments_from_html(
                    description_html, staging, args.target_url
                )
                if att_files:
                    _safe_print(f"      Attachments: {len(att_files)} file(s)")

                # Step 4b: Save MD file locally
                md_filename = f"{folder_name}.md"
                md_path = os.path.join(staging, md_filename)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)

                # Step 4c: Create per-article KB folder
                article_folder = FileService.new_a_file_from_kb(
                    args.tenant_id, folder_name, kb_parent["id"],
                )

                # Step 4d: Upload MD file (general parser) to the article folder
                _upload_file_to_kb_folder(
                    md_path, kb, args.tenant_id,
                    article_folder["id"], parser_id="general",
                )

                # Step 4e: Upload attachments (laws parser) to the same folder
                ext_laws = {".pdf", ".doc", ".docx", ".xls", ".xlsx",
                            ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"}
                for att_path in att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in ext_laws else "general"
                    try:
                        _upload_file_to_kb_folder(
                            att_path, kb, args.tenant_id,
                            article_folder["id"], parser_id=pid,
                        )
                    except Exception as e:
                        _safe_print(
                            f"      Attachment upload error: "
                            f"{os.path.basename(att_path)}: {e}"
                        )

                _safe_print("    -> KB upload OK")
            except Exception as e:
                _safe_print(f"    -> KB upload failed: {e}")
                logging.exception("Article KB upload failed for %s", article_id)
        else:
            _safe_print("    -> skipped (no kb-id)")
        sys.stdout.flush()

        if article_id:
            new_ids.append(article_id)

    # Save state
    if new_ids:
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

    _safe_print(f"\n[ZFCG] {'='*60}")
    _safe_print(f"[ZFCG] Crawl finished: {len(formatted)} new articles")
    _safe_print(f"[ZFCG] {'='*60}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    CONSUMER_NAME = "zfcg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
