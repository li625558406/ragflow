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
Crawler for ggzyfw.fujian.gov.cn — guide + policies sections.

Targets:
  1. /guide/index — three tabs:
       - 示范文本  (type=53)
       - 行业规范  (type=54)
       - 培训资料  (type=59)
  2. /policies/list?type=X — seven policy types:
       92, 93, 94, 96, 97, 98, 922

Site is a Vue 2.6 SPA with AES-128-CBC encrypted API responses.
Playwright is required to render content from the decrypted DOM.

Checkpoint/resume: each section is processed independently (list → details
→ upload → save state).  If the 3600s task timeout kills the run mid-way,
the next trigger resumes from the next incomplete section.

Usage:
    python ggzyfw_guide_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ggzyfw.fujian.gov.cn/ \
        --kb-id <KB_ID> \
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
_SITE_ROOT = "https://ggzyfw.fujian.gov.cn"

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

# Anti-crawling: random delays between requests
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

# Guide tabs: (tab_id, label)
GUIDE_TABS = [
    ("53", "\u793a\u8303\u6587\u672c"),
    ("54", "\u884c\u4e1a\u89c4\u8303"),
    ("59", "\u57f9\u8bad\u8d44\u6599"),
]

# Policy types: (type_id, label)
POLICY_TYPES = [
    ("92", "\u653f\u7b56\u6cd5\u89c4-92"),
    ("93", "\u653f\u7b56\u6cd5\u89c4-93"),
    ("94", "\u653f\u7b56\u6cd5\u89c4-94"),
    ("96", "\u653f\u7b56\u6cd5\u89c4-96"),
    ("97", "\u653f\u7b56\u6cd5\u89c4-97"),
    ("98", "\u653f\u7b56\u6cd5\u89c4-98"),
    ("922", "\u653f\u7b56\u6cd5\u89c4-922"),
]

# Detail page: wait for content element instead of blind timeout
_DETAIL_CONTENT_SELECTORS = [
    ".detail-content", ".article-content", ".page-main",
    ".content", "[class*=\"content\"]", "main", "article",
    "h1", ".detail-title",
]

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


# ===================================================================
# Playwright: extraction helpers
# ===================================================================

def _wait_for_list(page, timeout=30):
    """Wait for a list to render (a.title inside div.list-list)."""
    try:
        page.wait_for_selector("div.list-list a.title", timeout=timeout * 1000)
        return True
    except Exception:
        pass
    try:
        page.wait_for_selector("div.list-item a", timeout=10000)
        return True
    except Exception:
        page.wait_for_timeout(5000)
        return True


def _extract_list_items(page):
    """Extract items from a rendered list page.

    Returns list[dict]: {id, title, url, date_str}.
    """
    try:
        items = page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll(
                'div.list-list a.title, div.list-item a[class*="title"]'
            );
            for (const a of links) {
                const href = a.href || '';
                const text = (a.textContent || '').trim();
                if (!href || !text || text.length < 2) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                let dateStr = '';
                const parent = a.closest('.list-item') || a.parentElement;
                if (parent) {
                    const timeEl = parent.querySelector('span.time, label.time, .time');
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
            return results;
        }""")
        return items or []
    except Exception as e:
        logging.warning("List item extraction failed: %s", e)
        return []


def _click_next_page(page):
    """Click Element UI pagination next button."""
    try:
        btn_next = page.query_selector(".el-pagination .btn-next")
        if not btn_next:
            return False
        cls = btn_next.get_attribute("class") or ""
        if "disabled" in cls:
            return False
        btn_next.click()
        page.wait_for_timeout(2000)
        return True
    except Exception:
        return False


def _extract_detail_content(page):
    """Extract content from a rendered detail page."""
    try:
        result = page.evaluate("""() => {
            const res = {title: '', date: '', content_text: ''};

            const titleEl = document.querySelector(
                'h1, .detail-title, [class*="title"], [class*="bt"]'
            );
            if (titleEl) res.title = titleEl.textContent.trim();

            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(/(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})/);
            if (dateMatch) res.date = dateMatch[1];

            const selectors = [
                '.detail-content', '.article-content', '.page-main',
                '.content', '[class*="content"]', '[class*="con"]',
                'main', 'article',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    const text = el.textContent.trim();
                    if (text.length < bodyText.length * 0.3) continue;
                    res.content_text = text;
                    break;
                }
            }
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
        return {"title": "", "date": "", "content_text": ""}


# ===================================================================
# Section crawlers — return list[dict] of article info
# ===================================================================

def _crawl_guide_tabs(context):
    """Crawl /guide/index — click each tab, extract all items (no pagination).

    Returns list[dict]: {id, title, url, date_str, section}.
    """
    all_articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        url = "{}/guide/index".format(_SITE_ROOT)
        _safe_print("[Guide]   Loading /guide/index ...")
        sys.stdout.flush()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if not _wait_for_list(page, timeout=30):
            _safe_print("[Guide]   WARNING: list not rendered, waiting 10s...")
            sys.stdout.flush()
            page.wait_for_timeout(10000)

        for tab_id, label in GUIDE_TABS:
            _safe_print("[Guide]   === Tab: {} ===".format(label))
            sys.stdout.flush()

            try:
                tab_pane = page.query_selector(
                    ".el-tabs__item[id='tab-{}']".format(tab_id)
                )
                if not tab_pane:
                    tab_pane = page.query_selector(
                        ".el-tabs__item >> text={}".format(label)
                    )
                if tab_pane:
                    tab_pane.click()
                    _request_delay()
                else:
                    _safe_print("[Guide]   WARNING: tab '{}' not found".format(label))
                    continue
            except Exception as e:
                _safe_print("[Guide]   WARNING: tab click failed: {}".format(e))
                continue

            items = _extract_list_items(page)
            count = 0
            for item in items:
                art_id = item.get("id", "")
                if not art_id or art_id in seen_ids:
                    continue
                seen_ids.add(art_id)

                detail_url = item.get("url", "")
                if detail_url and not detail_url.startswith("http"):
                    detail_url = _SITE_ROOT + detail_url

                all_articles.append({
                    "id": art_id,
                    "title": item.get("title", "").strip(),
                    "url": detail_url,
                    "date_str": item.get("date_str", ""),
                    "section": "\u4ea4\u6613\u6307\u5357-{}".format(label),
                })
                count += 1

            _safe_print("[Guide]   {} items".format(count))
            sys.stdout.flush()

    except Exception as e:
        logging.error("Guide index listing failed: %s", e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return all_articles


def _crawl_policies_section(context, type_id, label):
    """Crawl /policies/list?type=X — all pages.

    Returns list[dict]: {id, title, url, date_str, section}.
    """
    articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        url = "{}/policies/list?type={}".format(_SITE_ROOT, type_id)
        _safe_print("[Policies]   Loading: {} ...".format(label))
        sys.stdout.flush()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if not _wait_for_list(page, timeout=30):
            _safe_print("[Policies]   WARNING: list not rendered, waiting 10s...")
            sys.stdout.flush()
            page.wait_for_timeout(10000)

        page_num = 1
        while True:
            items = _extract_list_items(page)
            _safe_print("[Policies]   Page {}: {} items".format(page_num, len(items)))
            sys.stdout.flush()

            if not items:
                break

            for item in items:
                art_id = item.get("id", "")
                if not art_id or art_id in seen_ids:
                    continue
                seen_ids.add(art_id)

                detail_url = item.get("url", "")
                if detail_url and not detail_url.startswith("http"):
                    detail_url = _SITE_ROOT + detail_url

                articles.append({
                    "id": art_id,
                    "title": item.get("title", "").strip(),
                    "url": detail_url,
                    "date_str": item.get("date_str", ""),
                    "section": "\u653f\u7b56\u6cd5\u89c4-{}".format(label),
                })

            if not _click_next_page(page):
                break
            page_num += 1
            _request_delay()

    except Exception as e:
        logging.error("Policies section '%s' listing failed: %s", label, e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return articles


# ===================================================================
# Detail page fetching
# ===================================================================

def _fetch_detail(context, url):
    """Load a detail page and extract content with retry+backoff. Returns text string."""
    for attempt in range(3):
        page = context.new_page()
        page.set_default_timeout(60000)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Check for HTTP error / block page
            status = page.evaluate("() => document.title || ''")
            if status and any(kw in status for kw in ("403", "404", "502", "503")):
                raise RuntimeError("Blocked or error page: {}".format(status))

            # Wait for any content element to appear
            try:
                selector = ", ".join(_DETAIL_CONTENT_SELECTORS)
                page.wait_for_selector(selector, timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)

            detail = _extract_detail_content(page)
            content = detail.get("content_text", "")
            if content and len(content) > 20:
                return content
            if attempt < 2:
                logging.warning("Detail empty for %s, retry %d/3...", url, attempt + 2)
                _request_delay()
        except Exception as e:
            logging.warning("Detail fetch failed for %s (attempt %d/3): %s",
                          url, attempt + 1, e)
            if attempt < 2:
                wait = (2 ** attempt) + random.uniform(1, 3)
                time.sleep(wait)
        finally:
            try:
                page.close()
            except Exception:
                pass
    return ""


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
    return {"processed_ids": [], "completed_sections": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d sections done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_sections", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id))
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
# Section-level processing (list → details → upload → checkpoint)
# ===================================================================

def _process_section(context, output_dir, kb_id, tenant_id,
                     section_key, articles, processed_ids, state):
    """For one section: fetch details in batches of 10, upload per batch,
    checkpoint after each batch so resume is near-instant on timeout."""
    new_articles = [a for a in articles if a["id"] not in processed_ids]
    if not new_articles:
        _safe_print("[{}]   0 new articles, skipping.".format(section_key))
        sys.stdout.flush()
        # Mark section complete if all articles processed
        if section_key not in state.get("completed_sections", []):
            state.setdefault("completed_sections", []).append(section_key)
            _save_state(output_dir, state)
        return 0

    _safe_print("[{}]   {} new articles, fetching details (batches of 10)...".format(
        section_key, len(new_articles)))
    sys.stdout.flush()

    BATCH_SIZE = 10
    total_processed = 0
    fail_count = 0
    batch_num = 0

    for batch_start in range(0, len(new_articles), BATCH_SIZE):
        batch = new_articles[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        md_parts = []
        batch_ids = []

        for idx, art in enumerate(batch, 1):
            global_idx = batch_start + idx
            _safe_print("[{}]   [{}/{}] {}".format(
                section_key, global_idx, len(new_articles), art["title"][:60]))
            sys.stdout.flush()

            url = art.get("url", "")
            content = ""
            if url:
                content = _fetch_detail(context, url)

            if not content:
                fail_count += 1
                content = "\u6807\u9898: {}\n\u65e5\u671f: {}\nURL: {}".format(
                    art["title"], art.get("date_str", ""), url)

            title = art.get("title", "\u65e0\u6807\u9898")
            section_label = art.get("section", "")
            date_str = art.get("date_str", "")

            md = "# {}\n**\u680f\u76ee:** {}\n**\u65e5\u671f:** {}\n**URL:** {}\n\n{}\n".format(
                title, section_label, date_str, url, content
            )
            _save_markdown(md, output_dir, art["id"])
            md_parts.append(md)
            batch_ids.append(art["id"])

            # Anti-crawling delay
            _request_delay()

        # ── Checkpoint: upload batch + save state ──
        if md_parts:
            batch_path = os.path.join(output_dir,
                                      "{}_{:03d}.md".format(section_key, batch_num))
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                try:
                    _upload_to_kb(batch_path, kb_id, tenant_id)
                except Exception as e:
                    _safe_print("[{}]   batch {} upload failed: {}".format(
                        section_key, batch_num, e))
                    logging.error("Upload failed for %s batch %d: %s",
                                  section_key, batch_num, e)

            total_processed += len(md_parts)
            _safe_print("[{}]   batch {} uploaded ({}/{} done)".format(
                section_key, batch_num, total_processed, len(new_articles)))
            sys.stdout.flush()

    # Mark section complete
    if section_key not in state.get("completed_sections", []):
        state.setdefault("completed_sections", []).append(section_key)
        _save_state(output_dir, state)

    _safe_print("[{}]   done: {} articles, {} no-detail\n".format(
        section_key, total_processed, fail_count))
    sys.stdout.flush()

    return total_processed


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="ggzyfw.fujian.gov.cn guide+policies crawler"
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
    p.add_argument("--section", default=None,
                   help="guide | policies | guide,policies (default: both)")
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

    _safe_print("\n" + "=" * 60)
    _safe_print("[GGFW-GP] ggzyfw guide+policies crawler")
    _safe_print("[GGFW-GP] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== GGFW guide+policies crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[GGFW-GP] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[GGFW-GP] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # State
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_sections": []
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_sections = set(state.get("completed_sections", []))
    _safe_print("[GGFW-GP] Previously processed: {}, completed sections: {}\n".format(
        len(processed_ids), len(completed_sections)))
    sys.stdout.flush()

    do_guide = True
    do_policies = True
    if args.section:
        do_guide = "guide" in args.section
        do_policies = "policies" in args.section

    total_processed = 0

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

        # -----------------------------------------------------------------
        # Part 1: Guide tabs (示范文本, 行业规范, 培训资料)
        # -----------------------------------------------------------------
        if do_guide:
            section_key = "guide"
            if section_key in completed_sections:
                _safe_print("[GGFW-GP] === Part 1: Guide tabs (SKIPPED, already done) ===\n")
                sys.stdout.flush()
            else:
                _safe_print("[GGFW-GP] === Part 1: Guide tabs ===")
                sys.stdout.flush()
                guide_articles = _crawl_guide_tabs(context)
                n = _process_section(context, output_dir, args.kb_id,
                                     args.tenant_id, section_key,
                                     guide_articles, processed_ids, state)
                total_processed += n

        # -----------------------------------------------------------------
        # Part 2: Policies sections (type=92,93,94,96,97,98,922)
        # -----------------------------------------------------------------
        if do_policies:
            for sec_idx, (type_id, label) in enumerate(POLICY_TYPES, 1):
                section_key = "policies_{}".format(type_id)
                if section_key in completed_sections:
                    _safe_print("[GGFW-GP] === Part 2.{}/{}: {} (SKIPPED) ===\n".format(
                        sec_idx, len(POLICY_TYPES), label))
                    sys.stdout.flush()
                    continue

                _safe_print("[GGFW-GP] === Part 2.{}/{}: {} ===".format(
                    sec_idx, len(POLICY_TYPES), label))
                sys.stdout.flush()

                pol_articles = _crawl_policies_section(context, type_id, label)
                n = _process_section(context, output_dir, args.kb_id,
                                     args.tenant_id, section_key,
                                     pol_articles, processed_ids, state)
                total_processed += n

        browser.close()

    _safe_print("\n" + "=" * 60)
    _safe_print("[GGFW-GP] Done: {} articles processed this run".format(total_processed))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== GGFW-GP crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_guide_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
