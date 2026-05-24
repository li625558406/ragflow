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
Crawler for 福建省水利建设市场信用管理平台 — 通知公告 (notice/announcements).

Target: http://27.156.118.74:18001/#/regulation
Section: 通知公告, 69 articles across 6 pages.

Site characteristics
────────────────────
Vue.js SPA with hash router and Element UI components.  API responses are
AES-encrypted (base64), so we use Playwright to render pages and extract
content from the rendered DOM.

API endpoints (discovered via browser devtools)
───────────────────────────────────────────────
Listing:
    GET /tb/noticeInfo/page?current=1&size=12&title_name
    Returns encrypted JSON, decrypted by Axios response interceptor.

Detail:
    GET /tb/noticeInfo/getById/{id}
    Returns encrypted JSON.

Data flow
─────────
  1. Launch Playwright headless browser
  2. Navigate to /#/regulation → extract article list from DOM
  3. Paginate via .btn-next click → extract all 6 pages
  4. For each article → navigate to /#/notice?id={id} → extract detail DOM
  5. Download attachments (PDF) via PlaywrightHttpClient.download()
  6. Build markdown → save locally → upload to KB

Usage
─────
    python smzx_notice_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>

    # Optional:
        --max-runtime 3300    # Max runtime before graceful stop
        --full                # Ignore saved state, re-crawl
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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient

# ---------------------------------------------------------------------------
# Playwright direct
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_FRONT = "http://27.156.118.74:18001"
_SITE_BACKEND = "http://27.156.118.74:8002"
_LISTING_URL = _SITE_FRONT + "/#/regulation"

_TAG_PREFIX = "[FJSL-TZG]"
_SECTION_LABEL = "通知公告"
_SECTION_NAME = "FJSL_TZG"

_PAGE_SIZE = 12
_BATCH_SIZE = 3
_MAX_RUNTIME_DEFAULT = 3300
_REQUEST_DELAY_MIN = 0.2
_REQUEST_DELAY_MAX = 0.8
_STATE_FILENAME = "_crawler_state.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

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


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _sanitize_filename(name, max_len=120):
    if not name:
        return "unnamed"
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("._ ")
    if len(safe) > max_len:
        base, ext = os.path.splitext(safe)
        safe = base[:max_len - len(ext)] + (ext or "")
    return safe or "unnamed"


# ---------------------------------------------------------------------------
# Playwright page context
# ---------------------------------------------------------------------------

def _launch_browser():
    """Launch a Playwright Chromium browser."""
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        )
    chrome_path = _find_chrome()
    if not chrome_path:
        raise RuntimeError("Chrome not found.")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        executable_path=chrome_path,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )
    return pw, browser


def _new_page(browser):
    """Create a new page with proper viewport and user agent."""
    ctx = browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        bypass_csp=True,
    )
    page = ctx.new_page()
    page.set_default_timeout(60000)
    return ctx, page


# ---------------------------------------------------------------------------
# Listing page
# ---------------------------------------------------------------------------

def _extract_list_items(page):
    """Extract article items from the rendered listing page.

    Returns list[dict]: {id, title, date_str}.
    """
    try:
        items = page.evaluate("""() => {
            const results = [];
            const lis = document.querySelectorAll('li.title[data-id]');
            for (const li of lis) {
                const id = li.getAttribute('data-id') || '';
                const titleEl = li.querySelector('span.contents');
                const dateEl = li.querySelector('span.date');
                const title = titleEl ? titleEl.textContent.trim() : '';
                const date = dateEl ? dateEl.textContent.trim() : '';
                if (id && title) {
                    results.push({id: id, title: title, date_str: date});
                }
            }
            return results;
        }""")
        return items or []
    except Exception as e:
        logging.warning("List item extraction failed: %s", e)
        return []


def _get_total_count(page):
    """Get total article count from pagination."""
    try:
        text = page.evaluate("""() => {
            const el = document.querySelector('.el-pagination__total');
            return el ? el.textContent.trim() : '';
        }""")
        m = re.search(r'(\d+)', text)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _click_next_page(page):
    """Click Element UI pagination next button. Returns True if successful."""
    try:
        btn_next = page.query_selector(".el-pagination .btn-next")
        if not btn_next:
            return False
        cls = btn_next.get_attribute("class") or ""
        if "disabled" in cls:
            return False
        btn_next.click()
        page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


def _crawl_listing(browser):
    """Crawl all listing pages, return list of articles.

    Returns list[dict]: {id, title, date_str}.
    """
    all_articles = []
    seen_ids = set()

    ctx, page = _new_page(browser)

    try:
        _safe_print("[{}]  Loading listing page...".format(_TAG_PREFIX))
        sys.stdout.flush()
        page.goto(_LISTING_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        total = _get_total_count(page)
        _safe_print("[{}]  Total articles: {}".format(_TAG_PREFIX, total))
        sys.stdout.flush()

        page_num = 1
        while True:
            _safe_print("[{}]  Extracting page {}...".format(_TAG_PREFIX, page_num))
            sys.stdout.flush()

            items = _extract_list_items(page)
            new_count = 0
            for item in items:
                if item["id"] and item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    all_articles.append(item)
                    new_count += 1

            _safe_print("[{}]    Got {} items ({} new)".format(
                _TAG_PREFIX, len(items), new_count))
            sys.stdout.flush()

            if not _click_next_page(page):
                _safe_print("[{}]  No more pages.".format(_TAG_PREFIX))
                sys.stdout.flush()
                break

            page_num += 1
            _request_delay()

    finally:
        try:
            ctx.close()
        except Exception:
            pass

    return all_articles


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------

def _crawl_detail(browser, article_id):
    """Navigate to the detail page and extract content.

    Returns dict: {title, pub_date, views, content_text, attachments}.
    """
    url = "{}/#/notice?id={}".format(_SITE_FRONT, article_id)
    ctx, page = _new_page(browser)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        result = page.evaluate("""() => {
            const res = {
                title: '', pub_date: '', views: '',
                content_text: '', attachments: []
            };

            const titleEl = document.querySelector('.detail-title');
            if (titleEl) res.title = titleEl.textContent.trim();

            const infoSpans = document.querySelectorAll('.detail-info span');
            for (const span of infoSpans) {
                const text = span.textContent.trim();
                if (text.startsWith('发布时间：')) {
                    res.pub_date = text.replace('发布时间：', '').trim();
                } else if (text.startsWith('浏览次数：')) {
                    res.views = text.replace('浏览次数：', '').trim();
                }
            }

            const contentEl = document.querySelector('.detail-content');
            if (contentEl) {
                res.content_text = contentEl.innerText.trim();

                const links = contentEl.querySelectorAll('a[href]');
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    const text = (a.textContent || '').trim();
                    if (href) {
                        res.attachments.push({
                            filename: text || href.split('/').pop(),
                            url: href
                        });
                    }
                }
            }

            return res;
        }""")
        return result

    except Exception as e:
        logging.warning("Detail extraction failed for id=%s: %s", article_id, e)
        return {"title": "", "pub_date": "", "views": "", "content_text": "", "attachments": []}
    finally:
        try:
            ctx.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachments(attachments, download_dir, http_client):
    """Download attachments using PlaywrightHttpClient.

    Returns list of local file paths.
    """
    os.makedirs(download_dir, exist_ok=True)
    local_files = []

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue

        fname = _sanitize_filename(att.get("filename", "attachment"), max_len=120)
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext and not fname.lower().endswith(ext):
            fname += ext

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        _safe_print("[{}]    Downloading: {}...".format(_TAG_PREFIX, fname[:60]))
        sys.stdout.flush()

        data, ct, remote_fname = http_client.download(url)
        if data:
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
        else:
            logging.warning("Failed to download: %s", url)

        _request_delay()

    return local_files


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(filepath):
    """Extract ZIP file, return list of extracted file paths."""
    extracted = []
    extract_dir = os.path.splitext(filepath)[0] + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(name, max_len=120)
                out_path = os.path.join(extract_dir, safe_name)
                parent_dir = os.path.dirname(out_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except Exception as e:
        logging.warning("ZIP extraction failed for %s: %s", filepath, e)
    return extracted


# ---------------------------------------------------------------------------
# Text extraction from binary files
# ---------------------------------------------------------------------------

def _extract_text_from_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber
            parts = []
            with pdfplumber.open(filepath) as pdf:
                for pg in pdf.pages:
                    text = pg.extract_text()
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
                    parts.append("### {}\n".format(ws.title) + "\n".join(rows))
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return ""


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(title, pub_date, views, content_text, attachments, download_dir, detail_url):
    lines = [
        "# {}".format(title or "无标题"),
        "",
        "**数据来源:** 福建省水利建设市场信用管理平台 — {}".format(_SECTION_LABEL),
        "**页面地址:** {}".format(detail_url),
        "**抓取时间:** {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    if pub_date:
        lines.append("**发布时间:** {}".format(pub_date))
    if views:
        lines.append("**浏览次数:** {}".format(views))
    lines.append("")

    if content_text:
        lines.append("---")
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
        lines.append(content_clean)
        lines.append("")

    if attachments:
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")
        for att in attachments:
            fname = att.get("filename", "unknown")
            att_url = att.get("url", "")
            lines.append("- [{}]({})".format(fname, att_url))
        lines.append("")

        if download_dir and os.path.isdir(download_dir):
            lines.append("### 附件内容")
            lines.append("")
            for att in attachments:
                fname = att.get("filename", "")
                local_path = os.path.join(download_dir, fname)
                safe_name = _sanitize_filename(fname, max_len=120)
                if not os.path.exists(local_path):
                    alt_path = os.path.join(download_dir, safe_name)
                    if os.path.exists(alt_path):
                        local_path = alt_path
                if not os.path.exists(local_path):
                    for root, _, files in os.walk(download_dir):
                        for fn in files:
                            if fn == safe_name or fn == fname:
                                local_path = os.path.join(root, fn)
                                break
                if not os.path.exists(local_path):
                    continue

                lines.append("#### {}".format(fname))
                lines.append("")
                extracted_text = _extract_text_from_file(local_path)
                if extracted_text and extracted_text.strip():
                    if len(extracted_text) > 50000:
                        extracted_text = extracted_text[:50000] + "\n\n（内容过长，已截断）"
                    lines.append(extracted_text)
                else:
                    lines.append("（无法提取文本内容）")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(md_content, attachment_files, kb_id, tenant_id, folder_name):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FO("{}.md".format(folder_name), md_content.encode("utf-8"))
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("MD upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)

    for fp in attachment_files:
        fname = os.path.basename(fp)
        with open(fp, "rb") as f:
            blob = f.read()
        fo2 = _FO(fname, blob)
        errs2, pairs2 = FileService.upload_document(kb, [fo2], tenant_id)
        if errs2:
            logging.warning("Attachment upload errors: %s", errs2)
        for doc, _ in pairs2:
            did = doc["id"]
            try:
                DocumentService.begin2parse(did)
                DocumentService.run(tenant_id, doc, {})
            except Exception as e:
                logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="smzx_notice_crawler — 福建省水利建设市场信用管理平台 通知公告"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds (default: 3300)")
    for opt in ("--max-days", "--hours", "--max-articles",
                "--llm-id", "--llm-model", "--access-token", "--target-url"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] 福建省水利建设市场信用管理平台 — {} crawler".format(_TAG_PREFIX, _SECTION_LABEL))
    _safe_print("[{}] KB: {}".format(_TAG_PREFIX, args.kb_id))
    _safe_print("[{}] Task: {}".format(_TAG_PREFIX, args.task_name))
    _safe_print("[{}] Max runtime: {}s".format(_TAG_PREFIX, args.max_runtime))
    _safe_print("[{}] Target: {}".format(_TAG_PREFIX, _LISTING_URL))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== FJSL-TZG crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[{}] Output: {}\n".format(_TAG_PREFIX, output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))

    _safe_print("[{}] Already processed: {} article(s)".format(_TAG_PREFIX, len(processed_ids)))
    sys.stdout.flush()

    crawl_start = time.time()

    # ── Step 1: Launch browser + crawl listing ──────────────────────────
    _safe_print("\n[{}] Step 1/4: Launching browser and crawling listing...".format(_TAG_PREFIX))
    sys.stdout.flush()

    pw, browser = _launch_browser()

    try:
        all_articles = _crawl_listing(browser)
    finally:
        # We keep the browser open for detail page crawling
        pass

    _safe_print("[{}]   Total: {} article(s) from listing".format(_TAG_PREFIX, len(all_articles)))
    sys.stdout.flush()

    if not all_articles:
        _safe_print("[{}] No articles found, done.".format(_TAG_PREFIX))
        sys.stdout.flush()
        browser.close()
        pw.stop()
        return

    # Filter already-processed
    new_articles = [
        a for a in all_articles
        if a["id"] not in processed_ids
    ]
    skipped = len(all_articles) - len(new_articles)
    if skipped:
        _safe_print("[{}] {} already processed, {} new".format(_TAG_PREFIX, skipped, len(new_articles)))
        sys.stdout.flush()

    if not new_articles:
        _safe_print("[{}] All available articles already processed.".format(_TAG_PREFIX))
        sys.stdout.flush()
        browser.close()
        pw.stop()
        return

    # ── Step 2: Crawl detail pages ─────────────────────────────────────
    _safe_print("\n[{}] Step 2/4: Crawling {} detail page(s)...".format(_TAG_PREFIX, len(new_articles)))
    sys.stdout.flush()

    detail_data = {}
    for i, article in enumerate(new_articles, 1):
        elapsed = time.time() - crawl_start
        remaining = args.max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                "\n[{}] Runtime {:.0f}s, {:.0f}s remaining. Stopping list crawl.".format(
                    _TAG_PREFIX, elapsed, remaining))
            sys.stdout.flush()
            break

        art_id = article["id"]
        _safe_print("[{}] [{}/{}] Fetching detail for id={}: {}...".format(
            _TAG_PREFIX, i, len(new_articles), art_id, article["title"][:50]))
        sys.stdout.flush()

        detail = _crawl_detail(browser, art_id)
        detail_data[art_id] = detail
        _request_delay()

    # ── Step 3: Download attachments ───────────────────────────────────
    _safe_print("\n[{}] Step 3/4: Downloading attachments...".format(_TAG_PREFIX))
    sys.stdout.flush()

    http_client = PlaywrightHttpClient()
    http_client.start()
    downloads_dir = os.path.join(output_dir, "downloads")

    try:
        processed_count = 0
        stopped_early = False

        for i, article in enumerate(new_articles, 1):
            elapsed = time.time() - crawl_start
            remaining = args.max_runtime - elapsed
            if remaining < 120:
                _safe_print(
                    "\n[{}] Runtime {:.0f}s, {:.0f}s remaining (limit {}s), "
                    "stopping gracefully. {} processed. "
                    "Next run will resume.".format(
                        _TAG_PREFIX, elapsed, remaining, args.max_runtime, processed_count))
                sys.stdout.flush()
                stopped_early = True
                break

            art_id = article["id"]
            title = article["title"]
            date_str = article.get("date_str", "")

            detail = detail_data.get(art_id, {})
            if detail:
                detail_title = detail.get("title", "") or title
                detail_date = detail.get("pub_date", "") or date_str
                views = detail.get("views", "")
                content_text = detail.get("content_text", "")
                attachments = detail.get("attachments", [])
            else:
                detail_title = title
                detail_date = date_str
                views = ""
                content_text = ""
                attachments = []

            detail_url = "{}/#/notice?id={}".format(_SITE_FRONT, art_id)

            _safe_print("[{}] [{}/{}] Processing: {}...".format(
                _TAG_PREFIX, i, len(new_articles), detail_title[:60]))
            sys.stdout.flush()

            # Download attachments
            local_files = []
            article_dl_dir = ""
            if attachments:
                dl_name = "{}_{}".format(
                    art_id[:12],
                    _sanitize_filename(detail_title[:30], 40)
                )
                article_dl_dir = os.path.join(downloads_dir, dl_name)
                local_files = _download_attachments(attachments, article_dl_dir, http_client)
                for fp in list(local_files):
                    is_zip = fp.lower().endswith(".zip")
                    if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                        with open(fp, "rb") as f:
                            is_zip = f.read(4) == b"PK\x03\x04"
                    if is_zip:
                        extracted = _extract_zip(fp)
                        local_files.remove(fp)
                        local_files.extend(extracted)

            # Build markdown
            md_content = _build_markdown(
                detail_title, detail_date, views,
                content_text, attachments, article_dl_dir, detail_url
            )

            # Save markdown locally
            date_for_name = detail_date or datetime.now().strftime("%Y-%m-%d")
            folder_name = _sanitize_filename(
                "{}_{}_{}".format(date_for_name, art_id[:12], detail_title[:40]),
                max_len=120
            )
            md_path = os.path.join(output_dir, "{}.md".format(folder_name))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            _safe_print("[{}]   Saved ({} chars, {} attachments)".format(
                _TAG_PREFIX, len(md_content), len(local_files)))
            sys.stdout.flush()

            # Upload to KB
            if args.kb_id:
                try:
                    _upload_to_kb(md_content, local_files, args.kb_id,
                                  args.tenant_id, folder_name)
                except Exception as e:
                    logging.error("KB upload failed: %s", e)
                    _save_state(output_dir, {"processed_ids": list(processed_ids)})
                    _safe_print("[{}]   Upload error: {}".format(_TAG_PREFIX, e))
                    sys.stdout.flush()

            processed_ids.add(art_id)
            processed_count += 1

            if processed_count % _BATCH_SIZE == 0:
                _save_state(output_dir, {"processed_ids": list(processed_ids)})
                _safe_print("[{}]   Checkpoint ({} processed)".format(_TAG_PREFIX, processed_count))
                sys.stdout.flush()

    finally:
        http_client.stop()

    # ── Cleanup ────────────────────────────────────────────────────────
    try:
        browser.close()
        pw.stop()
    except Exception:
        pass

    # ── Final state ────────────────────────────────────────────────────
    state["processed_ids"] = list(processed_ids)
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] Crawl complete — {} new article(s)".format(_TAG_PREFIX, processed_count))
    if stopped_early:
        _safe_print("[{}] Stopped early, will resume next run".format(_TAG_PREFIX))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== FJSL-TZG crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "smzx_notice_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
