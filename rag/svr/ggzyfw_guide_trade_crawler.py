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
Crawler for ggzyfw.fujian.gov.cn — guide 交易流程 (type=54) with attachments.

Target:
  - List:   https://ggzyfw.fujian.gov.cn/guide/list?type=54
  - Detail: https://ggzyfw.fujian.gov.cn/guide/detail?id=<ID>

The site is a Vue.js SPA. This crawler uses Playwright to render the SPA,
extract list/detail content from the DOM, and download attachments (doc, pdf,
zip, etc.). ZIP archives are auto-extracted and their contents are uploaded
alongside the article detail.

Checkpoint/resume: articles are processed in batches of 10. After each batch,
state is saved and content is uploaded to KB. If the 3600s task timeout kills
the run, the next trigger resumes from where it left off.

Usage:
    python ggzyfw_guide_trade_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ggzyfw.fujian.gov.cn/ \
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
from urllib.parse import urljoin, urlparse

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
_SITE_ROOT = "https://ggzyfw.fujian.gov.cn"
_LIST_URL = "{}/guide/list?type=54".format(_SITE_ROOT)
_DETAIL_URL_FMT = "{}/guide/detail?id={{}}".format(_SITE_ROOT)

_SECTION_LABEL = "交易流程"
_SECTION_KEY = "guide_trade"

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

# Anti-crawling — random delays between requests
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

# Batch checkpoint — per 10 articles
BATCH_SIZE = 10

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

# Detail page content selectors (tried in order)
_DETAIL_CONTENT_SELECTORS = [
    ".detail-content", ".article-content", ".page-main",
    ".content", "[class*=\"content\"]", "main", "article",
    ".detail-body", ".guide-detail",
]

# Attachment file extensions
_ATTACHMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".csv", ".rtf",
}

# Parser mapping for upload
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
    """Random delay between requests to avoid rate limiting."""
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(text, max_len=120):
    """Clean a string for use as a filesystem name."""
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name)
    name = name.strip("._ ")
    if not name:
        name = "untitled"
    return name[:max_len]


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
# Playwright DOM extraction
# ===================================================================

def _wait_for_list(page, timeout=30):
    """Wait for the list to render in the Vue SPA."""
    selectors = [
        "div.list-list a.title",
        "div.list-item a[class*='title']",
        ".list-item a",
        "a[class*='title']",
        ".list-list a",
    ]
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout * 1000)
            return True
        except Exception:
            continue
    try:
        page.wait_for_timeout(5000)
        return True
    except Exception:
        return False


def _extract_list_items(page):
    """Extract items from a rendered list page.

    Returns list[dict]: {id, title, url, date_str}.
    """
    try:
        items = page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // Try multiple selector patterns
            const selectors = [
                'div.list-list a.title',
                'div.list-item a[class*="title"]',
                '.list-item a[href*="detail"]',
                'a[href*="guide/detail"]',
                '.list a[href*="detail"]',
            ];

            for (const sel of selectors) {
                const links = document.querySelectorAll(sel);
                for (const a of links) {
                    const href = a.href || '';
                    const text = (a.textContent || '').trim();
                    if (!href || !text || text.length < 2) continue;
                    if (seen.has(href)) continue;
                    seen.add(href);

                    let dateStr = '';
                    const parent = a.closest('.list-item') || a.closest('li') || a.parentElement;
                    if (parent) {
                        const timeEl = parent.querySelector(
                            'span.time, label.time, .time, span.date, .date, [class*="time"]'
                        );
                        if (timeEl) dateStr = (timeEl.textContent || '').trim();
                    }

                    let artId = '';
                    try {
                        const url = new URL(href);
                        artId = url.searchParams.get('id') || '';
                    } catch(e) {}

                    results.push({
                        title: text,
                        url: href,
                        date_str: dateStr,
                        id: artId || '',
                    });
                }
                if (results.length > 0) break;
            }
            return results;
        }""")
        return items or []
    except Exception as e:
        logging.warning("List item extraction failed: %s", e)
        return []


def _click_next_page(page):
    """Click pagination next button (Element UI style). Returns False if no more pages."""
    try:
        btn_next = page.query_selector(
            ".el-pagination .btn-next, .pagination .next, "
            ".el-pager + button, button.btn-next, "
            "li.next:not(.disabled), .pagination-next:not(.disabled)"
        )
        if not btn_next:
            return False

        # Check Element UI disabled state
        cls = (btn_next.get_attribute("class") or "").lower()
        if "disabled" in cls or "is-disabled" in cls:
            return False

        # Check if the button is actually disabled
        disabled = btn_next.get_attribute("disabled")
        if disabled is not None:
            return False

        btn_next.click()
        page.wait_for_timeout(2500)
        return True
    except Exception as e:
        logging.debug("Pagination click failed: %s", e)
        return False


def _wait_for_detail(page, timeout=15):
    """Wait for detail content to render."""
    for sel in _DETAIL_CONTENT_SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=timeout * 1000)
            return True
        except Exception:
            continue
    page.wait_for_timeout(3000)
    return True


def _extract_detail(page):
    """Extract title, date, content, and attachment links from a detail page.

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
                'h1, .detail-title, [class*="title"], [class*="bt"], h2, h3'
            );
            if (titleEl) res.title = (titleEl.textContent || '').trim();

            // Date from regex
            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(
                /(\\d{4}[-/年]\\d{1,2}[-/月]\\d{1,2})/
            );
            if (dateMatch) res.date_str = dateMatch[1];

            // Content — try selective extraction first
            const contentSelectors = [
                '.detail-content', '.article-content', '.page-main',
                '.content', '[class*="content"]', '[class*="con"]',
                '.detail-body', '.guide-detail', 'main', 'article',
            ];
            for (const sel of contentSelectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    const text = el.textContent.trim();
                    if (text.length < bodyText.length * 0.3) continue;
                    res.content_text = text;
                    break;
                }
            }

            // Fallback: remove nav/header/footer noise
            if (!res.content_text) {
                const remove = document.querySelectorAll(
                    'nav, header, footer, .footer, .top-header, ' +
                    '.menu, .el-menu, .header, .sidebar, .nav'
                );
                remove.forEach(el => el.remove());
                res.content_text = document.body.textContent.trim();
            }

            // Attachments — find all <a> links to downloadable files
            const allLinks = document.querySelectorAll('a[href]');
            const seenUrls = new Set();
            for (const a of allLinks) {
                const href = (a.href || '').trim();
                if (!href || href.startsWith('javascript:') || href.startsWith('#')) continue;
                if (seenUrls.has(href)) continue;
                seenUrls.add(href);

                const lower = href.toLowerCase();
                const knownExts = [
                    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                    '.zip', '.rar', '.7z', '.txt', '.jpg', '.jpeg', '.png',
                    '.tif', '.tiff', '.csv', '.rtf',
                ];
                let matchedExt = '';
                for (const ext of knownExts) {
                    // Check before query string / hash
                    const pathPart = href.split('?')[0].split('#')[0].toLowerCase();
                    if (pathPart.endsWith(ext)) {
                        matchedExt = ext;
                        break;
                    }
                }
                if (!matchedExt) {
                    // Also check if link text suggests an attachment
                    const linkText = (a.textContent || '').trim();
                    if (!linkText) continue;
                    const hasExt = knownExts.some(e => linkText.toLowerCase().endsWith(e));
                    if (!hasExt) continue;
                    // Guess extension from text
                    for (const ext of knownExts) {
                        if (linkText.toLowerCase().endsWith(ext)) {
                            matchedExt = ext;
                            break;
                        }
                    }
                }

                const fn = (a.textContent || '').trim() ||
                    decodeURIComponent(href.split('/').pop().split('?')[0]) ||
                    ('attachment' + matchedExt);

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
# Attachment download (via Playwright fetch)
# ===================================================================

def _download_attachment(page, att_url, dest_dir, filename, timeout=120):
    """Download a single attachment via the browser's fetch API.

    Returns the local file path, or None on failure.
    """
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
    """Extract a ZIP file into its parent directory.

    Removes the ZIP after extraction. Returns list of extracted file paths.
    """
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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


def _upload_attachment(filepath, kb_id, tenant_id):
    """Upload an individual attachment with appropriate parser."""
    ext = os.path.splitext(filepath)[1].lower()
    parser = "laws" if ext in _EXT_LAWS else "general"
    _upload_to_kb(filepath, kb_id, tenant_id, parser_id=parser)


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="ggzyfw.fujian.gov.cn guide trade (type=54) crawler with attachments"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://ggzyfw.fujian.gov.cn/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-pages", type=int, default=50,
                   help="Max pages to crawl (default: 50)")
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
    _safe_print("[GTRADE] ggzyfw guide trade (type=54) crawler with attachments")
    _safe_print("[GTRADE] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== GTRADE crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[GTRADE] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[GTRADE] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[GTRADE] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[GTRADE] Previously processed: {}\n".format(len(processed_ids)))
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
        # Step 1: Fetch all listing pages
        # ===================================================================
        _safe_print("[GTRADE] Step 1/3: Fetching listing pages...")
        sys.stdout.flush()

        list_page = context.new_page()
        list_page.set_default_timeout(60000)

        # Navigate to list URL with retry
        for attempt in range(3):
            try:
                list_page.goto(_LIST_URL, wait_until="domcontentloaded",
                              timeout=60000)
                break
            except Exception as e:
                if attempt < 2:
                    _safe_print("[GTRADE]   Page load attempt {} failed: {}, retrying...".format(
                        attempt + 1, e))
                    time.sleep(5)
                else:
                    raise

        _wait_for_list(list_page, timeout=30)

        all_articles = []
        seen_ids = set()
        page_num = 1

        while True:
            items = _extract_list_items(list_page)
            new_on_page = 0
            for item in items:
                art_id = item.get("id", "")
                if not art_id:
                    # Try to extract ID from URL
                    url = item.get("url", "")
                    m = re.search(r'[?&]id=(\d+)', url)
                    if m:
                        art_id = m.group(1)
                if not art_id or art_id in seen_ids:
                    continue
                seen_ids.add(art_id)

                detail_url = item.get("url", "")
                if detail_url and not detail_url.startswith("http"):
                    detail_url = urljoin(_SITE_ROOT, detail_url)
                if not detail_url:
                    detail_url = _DETAIL_URL_FMT.format(art_id)

                all_articles.append({
                    "id": art_id,
                    "title": item.get("title", "").strip(),
                    "url": detail_url,
                    "date_str": item.get("date_str", ""),
                })
                new_on_page += 1

            _safe_print("[GTRADE]   Page {}: {} new (total: {})".format(
                page_num, new_on_page, len(all_articles)))
            sys.stdout.flush()

            # Check pagination
            if not new_on_page and page_num > 1:
                # Empty page after page 1 → stop
                _safe_print("[GTRADE]   No new items, stopping pagination.")
                break

            if page_num >= args.max_pages:
                _safe_print("[GTRADE]   Reached max_pages={}, stopping.".format(args.max_pages))
                break

            if not _click_next_page(list_page):
                _safe_print("[GTRADE]   No more pages.")
                break

            page_num += 1
            _request_delay()

        list_page.close()

        _safe_print("[GTRADE]   Total articles: {} (expect ~121)".format(len(all_articles)))
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[GTRADE] No articles found. Check page structure.")
            browser.close()
            return

        # Filter already processed
        new_articles = [a for a in all_articles
                       if a["id"] not in processed_ids]
        _safe_print("[GTRADE]   {} new (skipped {} already processed)".format(
            len(new_articles), len(all_articles) - len(new_articles)))
        sys.stdout.flush()

        if not new_articles:
            _safe_print("[GTRADE] Nothing new. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            browser.close()
            return

        # ===================================================================
        # Step 2: Fetch details + download attachments (batch of 10)
        # ===================================================================
        _safe_print("[GTRADE] Step 2/3: Fetching {} articles in batches of {}...".format(
            len(new_articles), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_articles)
        success_count = 0
        fail_count = 0
        batch_num = 0

        for batch_start in range(0, total, BATCH_SIZE):
            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []  # (filepath, parser_id)

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                title_preview = art["title"][:60] if art["title"] else "(no title)"
                _safe_print("[GTRADE]   [{}/{}] {}".format(
                    global_idx, total, title_preview))
                sys.stdout.flush()

                # ---- Navigate to detail page + extract + download attachments ----
                detail_page = context.new_page()
                detail_page.set_default_timeout(60000)
                content_text = ""
                title = art["title"]
                attachments = []
                local_att_files = []
                detail_ok = False

                for detail_attempt in range(3):
                    try:
                        detail_page.goto(art["url"],
                                        wait_until="domcontentloaded",
                                        timeout=60000)
                        _wait_for_detail(detail_page, timeout=15)

                        detail = _extract_detail(detail_page)
                        content_text = detail.get("content_text", "")
                        if detail.get("title"):
                            title = detail["title"]
                        if detail.get("date_str"):
                            art["date_str"] = detail["date_str"]
                        attachments = detail.get("attachments", [])
                        detail_ok = True
                        break
                    except Exception as e:
                        logging.warning("Detail fetch failed for %s (attempt %d): %s",
                                      art["id"], detail_attempt + 1, e)
                        if detail_attempt < 2:
                            wait = (2 ** detail_attempt) + random.uniform(1, 3)
                            time.sleep(wait)

                # Download attachments while page is still open
                if detail_ok and attachments:
                    att_dir = os.path.join(output_dir, "attachments", art["id"])
                    for att in attachments:
                        att_url = att.get("url", "")
                        if not att_url:
                            continue
                        if att_url.startswith("/"):
                            att_url = _SITE_ROOT + att_url
                        elif not att_url.startswith("http"):
                            att_url = urljoin(_SITE_ROOT, att_url)

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
                        title, art.get("date_str", ""), art["url"])

                # ---- Build markdown ----
                date_str = art.get("date_str", "")
                detail_url = art["url"]

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

                # Attachment references
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

                # Save individual article MD
                _save_markdown(md_content, output_dir, art["id"])
                md_parts.append(md_content)
                batch_ids.append(art["id"])

                # Collect all files for this article
                article_md_path = os.path.join(output_dir, "articles", "{}.md".format(art["id"]))
                batch_files.append((article_md_path, "general"))
                for att_path in local_att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in _EXT_LAWS else "general"
                    batch_files.append((att_path, pid))

                success_count += 1

                # Anti-crawling delay between articles
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
                        # Upload combined batch MD
                        _upload_to_kb(batch_path, args.kb_id, args.tenant_id)

                        # Upload individual article MDs + attachments
                        for fp, parser in batch_files:
                            if os.path.exists(fp):
                                _upload_to_kb(fp, args.kb_id, args.tenant_id,
                                             parser_id=parser)
                    except Exception as e:
                        _safe_print("[GTRADE]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload failed for batch %d: %s", batch_num, e)

                _safe_print("[GTRADE]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

        browser.close()

    # -- Mark complete -------------------------------------------------------
    state["completed"] = True
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[GTRADE] Done: {} articles processed ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[GTRADE] Total articles found: {}".format(len(all_articles)))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== GTRADE crawler finished: %d articles ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_guide_trade_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
