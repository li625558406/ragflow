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
Crawler for ggzyjd.fj.gov.cn/policies/list — 政策法规.

Crawls 4 sections:
  1. 国家政策法规
  2. 福建省政策法规
  3. 政策解读
  4. 接口规范

Site is an AES-128-CBC encrypted Vue 2.6 SPA.  Direct HTTP requests return
encrypted payloads; Playwright is required to render and extract from DOM.

Internal API:
  POST /JdPortalApi/Article/PageTable   → listing (paginated)
  POST /JdPortalApi/Article/Detail      → detail

Listing DOM:  a.title (router-link) + span.time  inside div.list-list
Detail URL:   /policies/newDetail?id=<ID>

Usage:
    python ggzyjd_policies_crawler.py \\
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

# 4 policy sections:  (type_name, label)
SECTIONS = [
    ("\u56fd\u5bb6\u653f\u7b56\u6cd5\u89c4", "\u56fd\u5bb6\u653f\u7b56\u6cd5\u89c4"),
    ("\u798f\u5efa\u7701\u653f\u7b56\u6cd5\u89c4", "\u798f\u5efa\u7701\u653f\u7b56\u6cd5\u89c4"),
    ("\u653f\u7b56\u89e3\u8bfb", "\u653f\u7b56\u89e3\u8bfb"),
    ("\u63a5\u53e3\u89c4\u8303", "\u63a5\u53e3\u89c4\u8303"),
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


# ===================================================================
# Playwright: page-level extraction helpers
# ===================================================================

def _wait_for_list(page, timeout=30):
    """Wait for the policies list to render (a.title inside div.list-list)."""
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
    """Extract listing items from a rendered policies page.

    Returns list[dict] with {id, title, url, date_str}.
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

                // Extract date from sibling span.time
                let dateStr = '';
                const parent = a.closest('.list-item') || a.parentElement;
                if (parent) {
                    const timeEl = parent.querySelector('span.time, .time');
                    if (timeEl) dateStr = (timeEl.textContent || '').trim();
                }
                if (!dateStr) {
                    const m = text.match(/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}/);
                    if (m) dateStr = m[0];
                }

                // Extract id from URL query param
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
    """Click the 'next page' button in Element UI pagination."""
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

            // Title
            const titleEl = document.querySelector(
                'h1, .detail-title, [class*="title"], [class*="bt"]'
            );
            if (titleEl) res.title = titleEl.textContent.trim();

            // Date
            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(/(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})/);
            if (dateMatch) res.date = dateMatch[1];

            // Content — try specific selectors first
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
# Section crawler
# ===================================================================

def _crawl_section(context, type_name, label):
    """Crawl one policies section — all pages.

    Returns list[dict]: {id, title, url, date_str, section}.
    """
    articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        url = "{}/policies/list?type={}&title=".format(_SITE_ROOT, type_name)
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
                    "section": label,
                })

            if not _click_next_page(page):
                break
            page_num += 1
            time.sleep(0.5)

    except Exception as e:
        logging.error("Section '%s' listing failed: %s", label, e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return articles


# ===================================================================
# Detail page fetching
# ===================================================================

def _fetch_detail_on_page(page, url):
    """Load a detail page and extract content.  Reuses an existing page."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        detail = _extract_detail_content(page)
        return detail.get("content_text", "")
    except Exception as e:
        logging.warning("Detail fetch failed for %s: %s", url, e)
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
    path = os.path.join(articles_dir, "{}.md".format(article_id))
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
        description="ggzyjd.fj.gov.cn policies crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://ggzyjd.fj.gov.cn/",
                   help="Site root (unused, kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--section", default=None,
                   help="Section index (1-4), default: all")
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
    _safe_print("[Policies] ggzyjd.fj.gov.cn policies crawler")
    _safe_print("[Policies] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Policies crawler started ===")

    # Prerequisites
    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[Policies] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[Policies] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # State
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print("[Policies] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # Determine which sections to crawl
    sections = SECTIONS
    if args.section:
        idx = int(args.section) - 1
        if 0 <= idx < len(SECTIONS):
            sections = [SECTIONS[idx]]
        else:
            _safe_print("[Policies] Invalid section index: {}".format(args.section))
            sys.exit(1)

    article_data = []
    total_fail = 0

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

        for sec_idx, (type_name, label) in enumerate(sections, 1):
            _safe_print("[Policies] === Section {}/{}: {} ===".format(
                sec_idx, len(sections), label))
            sys.stdout.flush()

            sec_articles = _crawl_section(context, type_name, label)
            new_sec = [a for a in sec_articles if a["id"] not in processed_ids]
            _safe_print("[Policies]   {} on pages, {} new".format(
                len(sec_articles), len(new_sec)))
            sys.stdout.flush()

            if not new_sec:
                continue

            if args.max_articles:
                remaining = args.max_articles - len(article_data)
                if remaining <= 0:
                    break
                if len(new_sec) > remaining:
                    new_sec = new_sec[:remaining]

            # Fetch detail pages for THIS section immediately
            _safe_print("[Policies]   Fetching {} details...".format(len(new_sec)))
            sys.stdout.flush()

            sec_data = []
            detail_page = context.new_page()
            detail_page.set_default_timeout(60000)
            try:
                for i, art in enumerate(new_sec, 1):
                    title_preview = art["title"][:60]
                    _safe_print("[Policies]     [{}/{}] {}".format(
                        i, len(new_sec), title_preview))
                    sys.stdout.flush()

                    url = art.get("url", "")
                    content = ""
                    if url:
                        content = _fetch_detail_on_page(detail_page, url)

                    if not content:
                        _safe_print("[Policies]       -> no detail, using list data")
                        sys.stdout.flush()
                        total_fail += 1
                        content = "\u6807\u9898: {}\n\u65e5\u671f: {}\nURL: {}".format(
                            art["title"], art.get("date_str", ""), url)

                    art["content"] = content
                    sec_data.append(art)
                    article_data.append(art)
                    time.sleep(0.3)
            finally:
                try:
                    detail_page.close()
                except Exception:
                    pass

            # Save this section to disk immediately
            if sec_data:
                sec_md_path = os.path.join(output_dir, "section_{}_articles.md".format(sec_idx))
                sec_md_parts = []
                for art in sec_data:
                    title = art.get("title", "\u65e0\u6807\u9898")
                    section = art.get("section", "")
                    date_str = art.get("date_str", "")
                    art_url = art.get("url", "")
                    md = "# {}\n**\u680f\u76ee:** {}\n**\u65e5\u671f:** {}\n**URL:** {}\n\n{}\n".format(
                        title, section, date_str, art_url, art["content"]
                    )
                    _save_markdown(md, output_dir, art["id"])
                    sec_md_parts.append(md)

                with open(sec_md_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(sec_md_parts))

                # Update state incrementally
                for art in sec_data:
                    processed_ids.add(art["id"])
                _save_state(output_dir, {"processed_ids": list(processed_ids)})

                _safe_print("[Policies]   Saved {} articles from section {}".format(
                    len(sec_data), sec_idx))
                sys.stdout.flush()

        browser.close()

    # -----------------------------------------------------------------
    # Final combined save & upload
    # -----------------------------------------------------------------
    if not article_data:
        _safe_print("\n[Policies] No new articles to process.")
        sys.stdout.flush()
        logging.info("=== Policies crawler finished: no new articles ===")
        return

    _safe_print("\n[Policies] Building combined markdown for {} articles...".format(
        len(article_data)))
    sys.stdout.flush()

    md_parts = []
    for art in article_data:
        title = art.get("title", "\u65e0\u6807\u9898")
        section = art.get("section", "")
        date_str = art.get("date_str", "")
        art_url = art.get("url", "")
        md = "# {}\n**\u680f\u76ee:** {}\n**\u65e5\u671f:** {}\n**URL:** {}\n\n{}\n".format(
            title, section, date_str, art_url, art["content"]
        )
        md_parts.append(md)

    combined_path = os.path.join(output_dir, "articles_combined.md")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(md_parts))

    # Upload to KB
    if args.kb_id:
        _safe_print("[Policies]   Uploading to KB {}...".format(args.kb_id))
        sys.stdout.flush()
        try:
            _upload_to_kb(combined_path, args.kb_id, args.tenant_id)
            _safe_print(" done!")
            sys.stdout.flush()
        except Exception as e:
            _safe_print(" failed: {}".format(e))
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)

    _safe_print("\n" + "=" * 60)
    _safe_print("[Policies] Done: {} articles, {} no-detail".format(
        len(article_data), total_fail))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== Policies crawler finished: %d success, %d no-detail ===",
                 len(article_data), total_fail)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyjd_policies_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
