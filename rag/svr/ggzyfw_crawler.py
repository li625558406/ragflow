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
Dedicated web crawler for ggzyfw.fujian.gov.cn (福建省公共资源交易网).

Site characteristics
────────────────────
The site is a Vue 2.6 SPA with no SSR alternative:
  • Homepage: SPA shell (<div id=app>) — all content loaded dynamically.
  • API base: https://ggzyfw.fujian.gov.cn/FwPortalApi
  • All POST requests require a `portal-sign` header:
       portal-sign = MD5(secret_key + flattened_params)
       secret_key = B3978D054A72A7002063637CCDF6B2E5
  • API responses are AES-128-CBC encrypted — the key/IV live in a dynamically
    loaded webpack chunk (module a078), making pure-requests reverse engineering
    fragile.
  • Playwright renders the SPA faithfully, executing all JS incl. decryption.
  • Detail pages use Vue Router hash navigation — NOT SSR.

Strategy
────────
  1. Playwright (headless Chromium) — load homepage, extract article links
     from the rendered DOM.
  2. Same Playwright session — navigate to each article detail page and extract
     full text content from the rendered page.
  3. Save markdown & upload to KB with parser_id="laws".

Usage
─────
    python ggzyfw_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ggzyfw.fujian.gov.cn/web/index.html \
        --kb-id <KB_ID> \
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
from urllib.parse import urljoin

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright (optional dependency)
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HOMEPAGE_URL = "https://ggzyfw.fujian.gov.cn/web/index.html"
_SITE_BASE = "https://ggzyfw.fujian.gov.cn"

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
                "%Y年%m月%d日"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _article_id(url):
    """Stable ID from a URL (safe for Windows filenames)."""
    u = url.rstrip("/").rstrip(".html")
    parts = u.rsplit("/", 1)
    candidate = parts[-1] if len(parts) > 1 else parts[0]
    # Strip query string and hash fragment
    candidate = candidate.split("?")[0].split("#")[0]
    # For hash-based routing like index.html#/detail/123
    if "#" in parts[-1]:
        candidate = parts[-1].split("#")[-1].split("?")[0]
    candidate = candidate.strip().replace("/", "_").replace("?", "_").replace("#", "_")
    if candidate and candidate not in ("index.html", ""):
        return candidate[:16]
    return get_uuid()[:12]


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _normalize_url(href, base_url):
    """Make an absolute URL from a possibly-relative href."""
    if not href:
        return ""
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return urljoin(_SITE_BASE, href)
    if href.startswith("http"):
        return href
    # Relative URL
    return urljoin(base_url, href)


# ---------------------------------------------------------------------------
# Playwright-based extraction
# ---------------------------------------------------------------------------

def _extract_articles_from_homepage(page, timeout=60):
    """Extract article links from the rendered homepage.

    Returns list of dicts: [{title, url, date_str}]
    """
    articles = []

    # Wait for Vue to render article content
    selectors = [
        ".article-list a",
        ".news-list a",
        ".list-item a",
        "[class*=article] a",
        "[class*=news] a",
        "[class*=list] a",
        "a[href*='detail']",
        "a[href*='article']",
        "a[href*='info']",
        "a[href*='notice']",
        "a[href*='#/']",  # Vue hash router links
    ]

    found = False
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=15000)
            found = True
            break
        except Exception:
            continue

    if not found:
        _safe_print("[GGFW] No article selector matched — waiting 10s for JS...")
        page.wait_for_timeout(10000)

    # Extra settle time
    page.wait_for_timeout(2000)

    # Extract article links from DOM
    try:
        articles = page.evaluate("""() => {
            const results = [];
            const links = document.querySelectorAll('a');
            const seen = new Set();
            for (const a of links) {
                const href = a.href || '';
                const text = (a.textContent || '').trim();
                if (!href || !text) continue;
                if (href === '#' || href.startsWith('javascript:')) continue;
                if (text.length < 4) continue;

                // Dedup
                const key = href.split('?')[0];
                if (seen.has(key)) continue;
                seen.add(key);

                // Try to find nearby date
                let dateStr = '';
                let el = a.parentElement;
                for (let i = 0; i < 5 && el; i++) {
                    const t = el.textContent || '';
                    const m = t.match(/(\\d{4})[-/](\\d{1,2})[-/](\\d{1,2})/);
                    if (m) { dateStr = m[0]; break; }
                    el = el.parentElement;
                }
                // Also check siblings
                if (!dateStr && a.nextSibling) {
                    const t = a.nextSibling.textContent || '';
                    const m = t.match(/(\\d{4})[-/](\\d{1,2})[-/](\\d{1,2})/);
                    if (m) dateStr = m[0];
                }

                // Detect category from link context
                let category = '';
                let p = a.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const tag = p.tagName || '';
                    const cls = p.className || '';
                    const prev = p.previousElementSibling;
                    if (prev) {
                        const pt = (prev.textContent || '').trim();
                        if (pt.length < 20 && pt.length > 0) category = pt;
                    }
                    p = p.parentElement;
                }

                results.push({
                    title: text,
                    url: href,
                    date_str: dateStr,
                    category: category,
                });
            }
            return results;
        }""")
    except Exception as e:
        _safe_print(f"[GGFW] DOM extraction error: {e}")

    return articles


def _extract_detail_text(page):
    """Extract article detail text from a rendered detail page.

    Returns dict: {title, date_str, content}
    """
    result = {"title": "", "date_str": "", "content": ""}

    # Wait for content to render
    page.wait_for_timeout(3000)

    # Try common detail page content selectors
    content_selectors = [
        ".article-content",
        ".detail-content",
        ".content-body",
        ".main-content",
        ".news-content",
        "[class*=content]",
        "[class*=detail]",
        "[class*=article]",
        "#content",
        ".rich-text",
        ".text-content",
    ]

    content_text = ""
    for sel in content_selectors:
        try:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text()
                if len(txt) > 50:
                    content_text = txt
                    break
        except Exception:
            continue

    # Fallback: full page text
    if not content_text:
        try:
            content_text = page.evaluate("() => document.body.innerText")
        except Exception:
            content_text = ""

    result["content"] = content_text

    # Extract title
    try:
        title = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            if (h1) return h1.textContent.trim();
            const title = document.querySelector('title');
            return title ? title.textContent.trim() : '';
        }""")
        result["title"] = title
    except Exception:
        pass

    # Extract date
    try:
        date_str = page.evaluate("""() => {
            const text = document.body.innerText;
            const m = text.match(/(\\d{4})[-/](\\d{1,2})[-/](\\d{1,2})/);
            return m ? m[0] : '';
        }""")
        result["date_str"] = date_str
    except Exception:
        pass

    return result


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
        except Exception as e:
            logging.error("set parser_id: %s", e)
        try:
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("queue parse: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="ggzyfw.fujian.gov.cn (福建省公共资源交易网) crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Not used (SPA renders all content on homepage)")
    p.add_argument("--max-articles", type=int, default=0)
    p.add_argument("--max-days", type=int, default=7,
                   help="Max article age in days (default: 7)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[GGFW] 福建省公共资源交易网 crawler")
    _safe_print(f"[GGFW] Target URL: {args.target_url}")
    _safe_print(f"[GGFW] KB: {args.kb_id} | max-days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[GGFW] Max articles: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ggzyfw crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[GGFW] Output: {output_dir}\n")
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[GGFW] Previously processed: {len(processed_ids)}\n")
    sys.stdout.flush()

    # -- Chrome check --------------------------------------------------------
    chrome_path = _find_chrome()
    if not chrome_path:
        _safe_print("[GGFW] ERROR: Chrome not found. Check CHROME_PATHS.")
        sys.exit(1)
    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[GGFW] ERROR: playwright not installed.")
        _safe_print("    pip install playwright && playwright install chromium")
        sys.exit(1)

    # ====================================================================
    # Step 1: Load SPA homepage and extract article links
    # ====================================================================
    _safe_print("[GGFW] Step 1/3: Loading SPA homepage with Playwright...\n")
    sys.stdout.flush()

    all_articles = []
    browser = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
            )
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()
            page.set_default_timeout(120000)

            _safe_print(f"[GGFW] Loading {_HOMEPAGE_URL}...")
            try:
                page.goto(_HOMEPAGE_URL, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                _safe_print(f"[GGFW] Page load warning: {e}")

            # Extract article links from homepage
            all_articles = _extract_articles_from_homepage(page)
            _safe_print(f"[GGFW] Found {len(all_articles)} article link(s)\n")
            sys.stdout.flush()

            if not all_articles:
                _safe_print("[GGFW] No articles found. Aborting.\n")
                return

            # -- Filter & process articles -----------------------------------
            cutoff = datetime.now() - timedelta(days=args.max_days)

            # Filter by date
            for art in all_articles:
                if art.get("date_str"):
                    art["date"] = _parse_date(art["date_str"])
                else:
                    art["date"] = None

            before = len(all_articles)
            all_articles = [a for a in all_articles
                            if a["date"] is None or a["date"] >= cutoff]
            _safe_print(f"[GGFW] After date filter: {len(all_articles)} article(s) "
                        f"({before - len(all_articles)} too old)")
            sys.stdout.flush()

            # Filter processed
            if processed_ids:
                new_articles = [a for a in all_articles
                                if _article_id(a["url"]) not in processed_ids]
                _safe_print(f"[GGFW] Skipping {len(all_articles) - len(new_articles)} "
                            f"already processed")
                sys.stdout.flush()
                all_articles = new_articles

            # Limit
            if args.max_articles and len(all_articles) > args.max_articles:
                all_articles = all_articles[:args.max_articles]

            _safe_print(f"[GGFW] To process: {len(all_articles)} article(s)\n")
            sys.stdout.flush()

            if not all_articles:
                _safe_print("[GGFW] Nothing new.\n")
                return

            # ================================================================
            # Step 2: Navigate to each detail page & extract content
            # ================================================================
            _safe_print("[GGFW] Step 2/3: Fetching detail pages...\n")
            sys.stdout.flush()

            article_data = []
            for idx, art in enumerate(all_articles, 1):
                url = art.get("url", "")
                if not url:
                    continue

                # Ensure absolute URL
                url = _normalize_url(url, _HOMEPAGE_URL)
                art["url"] = url

                title_short = art.get("title", "")[:60]
                _safe_print(f"  [{idx}/{len(all_articles)}] {title_short}")
                sys.stdout.flush()

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    detail = _extract_detail_text(page)

                    art["title"] = detail.get("title") or art.get("title", "")
                    if detail.get("date_str"):
                        art["date_str"] = detail["date_str"]
                    content = detail.get("content", "")
                    art["content"] = content
                    _safe_print(f"    content: {len(content)} chars")
                except Exception as e:
                    _safe_print(f"    -> failed: {e}")
                    art["content"] = f"（内容获取失败: {e}）"
                    logging.warning("Detail page failed for %s: %s", url, e)

                article_data.append(art)
                sys.stdout.flush()
                time.sleep(1.0)  # be gentle between navigations

            browser.close()

    except Exception as e:
        _safe_print(f"[GGFW] Fatal error: {e}")
        logging.error("Fatal: %s", e)
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        sys.exit(1)

    # ====================================================================
    # Step 3: Save & upload
    # ====================================================================
    _safe_print(f"\n[GGFW] Step 3/3: Saving & uploading ({len(article_data)} articles)...\n")
    sys.stdout.flush()

    md_parts = []
    for art in article_data:
        content = art.get("content", "") or "（无内容）"
        # Strip very long content for display
        md = (
            f"# {art['title']}\n"
            f"**日期:** {art.get('date_str', '')}\n"
            f"**来源:** {art.get('url', '')}\n\n"
            f"{content}\n"
        )
        _save_markdown(md, output_dir, _article_id(art["url"]))
        md_parts.append(md)

    if md_parts:
        combined = os.path.join(output_dir, "articles_combined.md")
        with open(combined, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))
        _safe_print(f"  Saved: {combined}")

        new_ids = [_article_id(a["url"]) for a in article_data]
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

        if args.kb_id:
            _safe_print(f"  Uploading to KB {args.kb_id}...")
            sys.stdout.flush()
            try:
                _upload_to_kb(combined, args.kb_id, args.tenant_id, parser_id="laws")
                _safe_print("  Upload OK")
            except Exception as e:
                _safe_print(f"  Upload failed: {e}")
                logging.error("Upload: %s", e)

    _safe_print(f"\n[GGFW] {'='*60}")
    _safe_print(f"[GGFW] Done: {len(article_data)} articles")
    _safe_print(f"[GGFW] {'='*60}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
