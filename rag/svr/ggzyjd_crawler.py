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
Dedicated web crawler for ggzyjd.fj.gov.cn (福建省工程领域招投标在线监管平台).

Targets two sections:
  1. /dissentResult/list/  \u5f02\u8bae\u7ed3\u679c — last 7 days only
     Detail: /dissentResult/detail?id=<ID>
  2. /case/index/          \u6709\u5173\u6848\u4f8b — all data
     Detail: /case/detail?MGUID=<MGUID>

Site characteristics
────────────────────
Vue 2.6 SPA (Element UI) backed by ASP.NET REST API.  All API responses
are AES-128-CBC encrypted — the client-side CryptoJS decrypts before Vue
renders content.  Direct HTTP requests fail; Playwright is required to
render the SPA and extract content from the decrypted DOM.

Internal API endpoints (called by Vue, AES-encrypted):
  POST /JdPortalApi/Home/ObjectionResult           → \u5f02\u8bae\u7ed3\u679c\u5217\u8868
  POST /JdPortalApi/Home/ObjectionResultDetail      → \u5f02\u8bae\u7ed3\u679c\u8be6\u60c5
  POST /JdPortalApi/JdSysRelevantCases/ChuZhangExampleSearch → \u6848\u4f8b\u5217\u8868
  POST /JdPortalApi/JdSysRelevantCases/ChuZhangDetail        → \u6848\u4f8b\u8be6\u60c5

Strategy
────────
  1. Playwright renders each listing page; Vue decrypts and renders content.
  2. Extract article rows from the rendered el-table DOM.
  3. Click through pagination until all pages are collected.
  4. For each new article (dedup by ID/MGUID), navigate to detail page
     and extract full content from the rendered DOM.
  5. Save as markdown & upload to KB.

Usage:
    python ggzyjd_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://ggzyjd.fj.gov.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta

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
_SITE_ROOT = "https://ggzyjd.fj.gov.cn"

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


def _parse_date(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y\u5e74%m\u6708%d\u65e5"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _retry_page_goto(page, url, wait_until="load",
                     timeout=60, max_retries=3, backoff_base=5):
    """Load a page with retry + exponential backoff on timeout."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            effective_timeout = timeout * (1 + attempt * 0.5)
            page.set_default_timeout(effective_timeout * 1000)
            page.goto(url, wait_until=wait_until,
                      timeout=effective_timeout * 1000)
            return True
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                wait = backoff_base * attempt
                _safe_print(
                    f"[GGJD]   Page load attempt {attempt} failed "
                    f"({type(e).__name__}), retrying in {wait}s..."
                )
                sys.stdout.flush()
                time.sleep(wait)
    raise last_exc


# ===================================================================
# Playwright: page-level extraction helpers
# ===================================================================

def _wait_for_table(page, timeout=30):
    """Wait for an Element UI table to render."""
    try:
        page.wait_for_selector(".el-table__body-wrapper tbody tr",
                               timeout=timeout * 1000)
        return True
    except Exception:
        pass
    # Fallback: wait for any list content
    try:
        page.wait_for_selector(".list-item, .case-item, [class*=list]",
                               timeout=10000)
        return True
    except Exception:
        return False


def _wait_for_list(page, timeout=30):
    """Wait for a list-based page (case/index) to render."""
    try:
        page.wait_for_selector(".list-item, .case-list a, [class*=item]",
                               timeout=timeout * 1000)
        return True
    except Exception:
        # Try waiting for any content
        page.wait_for_timeout(5000)
        return True


def _extract_table_rows(page):
    """Extract rows from a rendered el-table.

    Returns list[dict] with col_0..col_N plus _url and _id if available.
    """
    try:
        rows = page.evaluate("""() => {
            const results = [];
            const trs = document.querySelectorAll(
                '.el-table__body-wrapper tbody tr'
            );
            for (const tr of trs) {
                const tds = tr.querySelectorAll('td .cell');
                const row = {};
                tds.forEach((td, idx) => {
                    const text = (td.textContent || '').trim();
                    if (text) row['col_' + idx] = text;
                });
                // Check for detail link
                const link = tr.querySelector('a[href*="detail"]');
                if (link) {
                    row['_url'] = link.href;
                }
                if (Object.keys(row).length > 0) {
                    results.push(row);
                }
            }
            return results;
        }""")
        return rows or []
    except Exception as e:
        logging.warning("Table row extraction failed: %s", e)
        return []


def _extract_list_items(page):
    """Extract list items from a rendered list page (case/index).

    Returns list[dict] with title, url, date_str, id.
    """
    try:
        items = page.evaluate("""() => {
            const results = [];
            // case/index uses router-link with list-item class
            const links = document.querySelectorAll('a.list-item, .case-list a, a[href*="case/detail"]');
            const seen = new Set();
            for (const a of links) {
                const href = a.href || '';
                const text = (a.textContent || '').trim();
                if (!href || !text) continue;
                if (text.length < 2) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                // Extract date from <em> or nearby text
                let dateStr = '';
                const em = a.querySelector('em, .time, [class*=time]');
                if (em) {
                    dateStr = (em.textContent || '').trim();
                }
                if (!dateStr) {
                    const m = text.match(/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}/);
                    if (m) dateStr = m[0];
                }

                // Extract MGUID from URL
                let mguid = '';
                try {
                    const url = new URL(href);
                    mguid = url.searchParams.get('MGUID') || '';
                } catch(e) {}

                results.push({
                    title: text.replace(/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}/g, '').trim(),
                    url: href,
                    date_str: dateStr,
                    id: mguid || href.split('=').pop() || '',
                });
            }
            return results;
        }""")
        return items or []
    except Exception as e:
        logging.warning("List item extraction failed: %s", e)
        return []


def _click_next_page(page):
    """Click the 'next page' button in Element UI pagination.

    Returns True if click succeeded (more pages), False if on last page.
    """
    try:
        btn_next = page.query_selector(".el-pagination .btn-next")
        if not btn_next:
            return False
        # Check if disabled
        cls = btn_next.get_attribute("class") or ""
        if "disabled" in cls:
            return False
        btn_next.click()
        page.wait_for_timeout(2000)
        return True
    except Exception:
        return False


def _extract_detail_content(page):
    """Extract full content from a rendered detail page.

    Returns {title, date, content_html, content_text}.
    """
    try:
        result = page.evaluate("""() => {
            const res = {title: '', date: '', content_html: '', content_text: ''};

            // Title
            const titleEl = document.querySelector('h1, .detail-title, [class*=title], [class*=bt]');
            if (titleEl) res.title = titleEl.textContent.trim();

            // Date
            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(/(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})/);
            if (dateMatch) res.date = dateMatch[1];

            // Content area — try common selectors
            const selectors = [
                '.detail-content', '.article-content', '.page-main',
                '.content', '[class*=content]', '[class*=con]',
                'main', 'article',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    // Avoid selecting the entire page layout
                    const text = el.textContent.trim();
                    if (text.length < bodyText.length * 0.3) continue;
                    res.content_html = el.innerHTML;
                    res.content_text = text;
                    break;
                }
            }
            // Fallback: body minus nav/footer/header
            if (!res.content_text) {
                const remove = document.querySelectorAll(
                    'nav, header, footer, .footer, .top-header, .menu, .el-menu, .header'
                );
                remove.forEach(el => el.remove());
                res.content_text = document.body.textContent.trim();
            }
            return res;
        }""")
        return result
    except Exception as e:
        logging.warning("Detail extraction failed: %s", e)
        return {"title": "", "date": "", "content_text": "", "content_html": ""}


# ===================================================================
# HTML -> Markdown
# ===================================================================

def _content_to_markdown(html):
    if not html or not html.strip():
        return ""
    try:
        from markdownify import markdownify as md
        return md(html, heading_style="ATX", strip=["script", "style", "noscript"])
    except ImportError:
        pass
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


# ===================================================================
# Section crawlers
# ===================================================================

def _crawl_dissent_result(context, max_days=7):
    """Crawl /dissentResult/list/ — extract articles from last `max_days` days.

    Returns list[dict]: {id, title, url, date_str, date, section}.
    """
    cutoff = datetime.now() - timedelta(days=max_days)
    articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        _safe_print("[GGJD]   Loading /dissentResult/list/ ...")
        sys.stdout.flush()
        _retry_page_goto(page, f"{_SITE_ROOT}/dissentResult/list/",
                         wait_until="load", timeout=60, max_retries=3)

        if not _wait_for_table(page, timeout=30):
            _safe_print("[GGJD]   WARNING: table not rendered, waiting 10s...")
            sys.stdout.flush()
            page.wait_for_timeout(10000)

        # Collect all pages
        page_num = 1
        while True:
            rows = _extract_table_rows(page)
            _safe_print(f"[GGJD]   Page {page_num}: {len(rows)} rows")
            sys.stdout.flush()

            if not rows:
                break

            page_has_recent = False
            for row in rows:
                # Find date and ID in row data
                date_str = ""
                for k, v in row.items():
                    if k.startswith("col_"):
                        m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", str(v))
                        if m:
                            date_str = m.group(1)
                            break

                dt = _parse_date(date_str)
                if dt and dt < cutoff:
                    continue  # skip old entries
                if dt:
                    page_has_recent = True

                # Build ID from detail link or title
                detail_url = row.get("_url", "")
                art_id = ""
                if detail_url:
                    m = re.search(r"[?&]id=(\d+)", detail_url)
                    if m:
                        art_id = m.group(1)
                if not art_id:
                    # Try to find ID in row cells
                    for k, v in row.items():
                        if k.startswith("col_") and re.match(r"^\d+$", str(v).strip()):
                            art_id = v.strip()
                            break

                if not art_id or art_id in seen_ids:
                    continue
                seen_ids.add(art_id)

                # Find title (usually col_1 or col_2)
                title = row.get("col_1", "") or row.get("col_2", "") or ""
                if not title:
                    title = row.get("col_0", "")

                if detail_url and not detail_url.startswith("http"):
                    detail_url = f"{_SITE_ROOT}{detail_url}"

                articles.append({
                    "id": art_id,
                    "title": title.strip(),
                    "url": detail_url,
                    "date_str": date_str,
                    "date": dt,
                    "section": "\u5f02\u8bae\u7ed3\u679c",
                })

            # Stop paginating if no recent items on this page
            if not page_has_recent:
                break

            # Try next page
            if not _click_next_page(page):
                break
            page_num += 1
            time.sleep(0.5)

    except Exception as e:
        logging.error("dissentResult listing failed: %s", e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return articles


def _crawl_case_index(context):
    """Crawl /case/index/ — extract ALL case articles.

    Returns list[dict]: {id(MGUID), title, url, date_str, date, section}.
    """
    articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        _safe_print("[GGJD]   Loading /case/index/ ...")
        sys.stdout.flush()
        _retry_page_goto(page, f"{_SITE_ROOT}/case/index/",
                         wait_until="load", timeout=60, max_retries=3)

        if not _wait_for_list(page, timeout=30):
            _safe_print("[GGJD]   WARNING: list not rendered, waiting 10s...")
            sys.stdout.flush()
            page.wait_for_timeout(10000)

        # Collect all pages
        page_num = 1
        while True:
            items = _extract_list_items(page)
            _safe_print(f"[GGJD]   Page {page_num}: {len(items)} items")
            sys.stdout.flush()

            if not items:
                break

            new_on_page = 0
            for item in items:
                mguid = item.get("id", "")
                if not mguid or mguid in seen_ids:
                    continue
                seen_ids.add(mguid)

                dt = _parse_date(item.get("date_str", ""))
                url = item.get("url", "")
                if url and not url.startswith("http"):
                    url = f"{_SITE_ROOT}{url}"

                articles.append({
                    "id": mguid,
                    "title": item.get("title", "").strip(),
                    "url": url,
                    "date_str": item.get("date_str", ""),
                    "date": dt,
                    "section": "\u6709\u5173\u6848\u4f8b",
                })
                new_on_page += 1

            # Try next page
            if not _click_next_page(page):
                break
            page_num += 1
            time.sleep(0.5)

    except Exception as e:
        logging.error("case/index listing failed: %s", e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return articles


# ===================================================================
# Detail page fetching
# ===================================================================

def _fetch_detail(context, url, retries=2):
    """Load a detail page and extract content.  Returns markdown string."""
    page = context.new_page()
    page.set_default_timeout(60000)
    try:
        _retry_page_goto(page, url, wait_until="load",
                         timeout=60, max_retries=3)
        page.wait_for_timeout(3000)

        detail = _extract_detail_content(page)
        if detail.get("content_html"):
            return _content_to_markdown(detail["content_html"])
        if detail.get("content_text"):
            return detail["content_text"]
        return ""
    except Exception as e:
        logging.warning("Detail fetch failed for %s: %s", url, e)
        return ""
    finally:
        try:
            page.close()
        except Exception:
            pass


# ===================================================================
# Persistence & state
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
    return {"processed_ids": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d processed IDs)", len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ===================================================================
# KB upload
# ===================================================================

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="naive"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"Knowledge base {kb_id} not found")

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


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="ggzyjd.fj.gov.cn crawler (dissentResult + case)"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url", default="https://ggzyjd.fj.gov.cn/",
                   help="Site root (default: https://ggzyjd.fj.gov.cn/)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--section", default=None,
                   help="Comma-separated: dissent,case (default: both)")
    p.add_argument("--max-days", type=int, default=7,
                   help="Max days for dissentResult (default: 7)")
    p.add_argument("--max-articles", type=int, default=0,
                   help="Max articles per section (0=unlimited)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ===================================================================
# Main
# ===================================================================

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[GGJD] \u798f\u5efa\u7701\u5de5\u7a0b\u9886\u57df\u62db\u6295\u6807\u76d1\u7ba1\u5e73\u53f0 crawler")
    _safe_print(f"[GGJD] KB: {args.kb_id} | dissent max-days: {args.max_days}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== GGJD crawler started ===")

    # Prerequisites
    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[GGJD] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[GGJD] Output: {output_dir}\n")
    sys.stdout.flush()

    # State
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[GGJD] Previously processed: {len(processed_ids)}\n")
    sys.stdout.flush()

    # Determine sections
    do_dissent = True
    do_case = True
    if args.section:
        keys = [s.strip() for s in args.section.split(",")]
        do_dissent = "dissent" in keys
        do_case = "case" in keys

    all_new_articles = []

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
        )

        # -----------------------------------------------------------------
        # Section 1: dissentResult (异议结果) — last N days
        # -----------------------------------------------------------------
        if do_dissent:
            _safe_print("[GGJD] === Section 1/2: \u5f02\u8bae\u7ed3\u679c (last %d days) ===" % args.max_days)
            sys.stdout.flush()

            dissent_articles = _crawl_dissent_result(context, max_days=args.max_days)
            new_dissent = [a for a in dissent_articles if a["id"] not in processed_ids]
            _safe_print(f"[GGJD]   {len(dissent_articles)} on pages, {len(new_dissent)} new")
            sys.stdout.flush()
            all_new_articles.extend(new_dissent)

        # -----------------------------------------------------------------
        # Section 2: case/index (有关案例) — all data
        # -----------------------------------------------------------------
        if do_case:
            _safe_print("[GGJD] === Section 2/2: \u6709\u5173\u6848\u4f8b (all) ===")
            sys.stdout.flush()

            case_articles = _crawl_case_index(context)
            new_case = [a for a in case_articles if a["id"] not in processed_ids]
            _safe_print(f"[GGJD]   {len(case_articles)} on pages, {len(new_case)} new")
            sys.stdout.flush()
            all_new_articles.extend(new_case)

        # -----------------------------------------------------------------
        # Fetch detail pages for all new articles
        # -----------------------------------------------------------------
        if not all_new_articles:
            _safe_print("\n[GGJD] No new articles to process.")
            sys.stdout.flush()
            browser.close()
            logging.info("=== GGJD crawler finished: no new articles ===")
            return

        # Apply max_articles limit
        if args.max_articles and len(all_new_articles) > args.max_articles:
            all_new_articles = all_new_articles[:args.max_articles]

        _safe_print(f"\n[GGJD] Fetching details for {len(all_new_articles)} articles...\n")
        sys.stdout.flush()

        article_data = []
        total_fail = 0
        for idx, art in enumerate(all_new_articles, 1):
            _safe_print(f"[GGJD]   [{idx}/{len(all_new_articles)}] {art['title'][:60]}")
            sys.stdout.flush()

            url = art.get("url", "")
            content = ""
            if url:
                content = _fetch_detail(context, url)

            if not content:
                _safe_print("[GGJD]     -> no detail content, using list excerpt")
                sys.stdout.flush()
                total_fail += 1
                # Build a minimal record from listing data
                content = "\u6807\u9898: {}\n\u65e5\u671f: {}\nURL: {}".format(
                    art['title'], art.get('date_str', ''), url)

            art["content"] = content
            article_data.append(art)
            time.sleep(0.5)

        browser.close()

    # -----------------------------------------------------------------
    # Save & upload
    # -----------------------------------------------------------------
    _safe_print(f"\n[GGJD] Saving & uploading {len(article_data)} articles...\n")
    sys.stdout.flush()

    md_parts = []
    for art in article_data:
        title = art.get('title', '\u65e0\u6807\u9898')
        section = art.get('section', '')
        date_str = art.get('date_str', '')
        art_url = art.get('url', '')
        md = "# {}\n**\u680f\u76ee:** {}\n**\u65e5\u671f:** {}\n**URL:** {}\n\n{}\n".format(
            title, section, date_str, art_url, art['content']
        )
        _save_markdown(md, output_dir, art["id"])
        md_parts.append(md)

    if md_parts:
        combined_path = os.path.join(output_dir, "articles_combined.md")
        with open(combined_path, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))

        # Update state
        new_ids = [a["id"] for a in article_data]
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # Upload
        if args.kb_id:
            _safe_print(f"[GGJD]   Uploading to KB {args.kb_id}...")
            sys.stdout.flush()
            try:
                _upload_to_kb(combined_path, args.kb_id, args.tenant_id)
                _safe_print(" done!")
                sys.stdout.flush()
            except Exception as e:
                _safe_print(f" failed: {e}")
                sys.stdout.flush()
                logging.error("Upload failed: %s", e)

    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GGJD] Done: {len(article_data)} articles, {total_fail} no-detail")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== GGJD crawler finished: %d success, %d no-detail ===",
                 len(article_data), total_fail)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyjd_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
