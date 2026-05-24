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

Targets:
  1. Homepage news (web/index.html) — recent articles
  2. /guide/list?type=53 — 交易指南:办事指引 (5 pages)
  3. /guide/list?type=54 — 交易指南:交易流程 (5 pages)

Site characteristics
────────────────────
Vue 2.6 SPA with AES-128-CBC encrypted API responses.  Some pages have SSR
(rendered HTML in initial response) but pagination requires JS.  Playwright
renders the SPA faithfully, executing all JS incl. decryption.

Guide list DOM:  div.list-list > div.list-item > a.title[href*="guide/detail?id=X"] + span.time
Guide detail:    /guide/detail?id=<ID>

Strategy
────────
  1. Playwright (headless Chromium) — load each section, extract article links
     from the rendered DOM, paginate via Element UI .btn-next.
  2. Same session — navigate to each article detail page and extract full text.
  3. Save markdown & upload to KB with parser_id="laws".

Usage
─────
    python ggzyfw_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://ggzyfw.fujian.gov.cn/web/index.html \\
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

# Guide sections: (type_id, label, max_pages)
GUIDE_SECTIONS = [
    ("53", "\u529e\u4e8b\u6307\u5f15", 5),
    ("54", "\u4ea4\u6613\u6d41\u7a0b", 5),
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
# Guide section crawler
# ---------------------------------------------------------------------------

def _wait_for_guide_list(page, timeout=30):
    """Wait for guide list to render."""
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


def _extract_guide_items(page):
    """Extract guide list items from rendered page.

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
                    const timeEl = parent.querySelector('span.time, .time');
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
        logging.warning("Guide item extraction failed: %s", e)
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


def _crawl_guide_section(context, type_id, label, max_pages=5):
    """Crawl one guide section — up to max_pages pages.

    Returns list[dict]: {id, title, url, date_str, section}.
    """
    articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        url = "{}/guide/list?type={}".format(_SITE_BASE, type_id)
        _safe_print("[GGFW]   Loading: {} ...".format(label))
        sys.stdout.flush()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if not _wait_for_guide_list(page, timeout=30):
            _safe_print("[GGFW]   WARNING: list not rendered, waiting 10s...")
            sys.stdout.flush()
            page.wait_for_timeout(10000)

        for page_num in range(1, max_pages + 1):
            items = _extract_guide_items(page)
            _safe_print("[GGFW]   Page {}: {} items".format(page_num, len(items)))
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
                    detail_url = _SITE_BASE + detail_url

                articles.append({
                    "id": art_id,
                    "title": item.get("title", "").strip(),
                    "url": detail_url,
                    "date_str": item.get("date_str", ""),
                    "section": label,
                })

            if page_num < max_pages:
                if not _click_next_page(page):
                    break
                time.sleep(0.5)

    except Exception as e:
        logging.error("Guide section '%s' listing failed: %s", label, e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return articles



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
    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[GGFW] ERROR: playwright not installed.")
        _safe_print("    pip install playwright && playwright install chromium")
        sys.exit(1)

    # ====================================================================
    # Step 1: Collect article links (homepage + guide sections)
    # ====================================================================
    _safe_print("[GGFW] Step 1/3: Collecting article links...\n")
    sys.stdout.flush()

    all_articles = []
    browser = None

    try:
        with sync_playwright() as pw:
            chrome_path = _find_chrome()
            launch_opts = {"headless": True}
            if chrome_path:
                launch_opts["executable_path"] = chrome_path
            browser = pw.chromium.launch(**launch_opts)
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )

            # --- Homepage articles ---
            _safe_print("[GGFW] === Section: Homepage ===")
            sys.stdout.flush()
            page = context.new_page()
            page.set_default_timeout(120000)

            _safe_print("[GGFW] Loading {}...".format(_HOMEPAGE_URL))
            try:
                page.goto(_HOMEPAGE_URL, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                _safe_print("[GGFW] Page load warning: {}".format(e))

            homepage_articles = _extract_articles_from_homepage(page)
            _safe_print("[GGFW] Found {} article link(s)".format(len(homepage_articles)))
            sys.stdout.flush()

            # Filter by date
            cutoff = datetime.now() - timedelta(days=args.max_days)
            for art in homepage_articles:
                art["date"] = _parse_date(art.get("date_str", ""))
                art["section"] = "\u9996\u9875\u65b0\u95fb"
            homepage_articles = [a for a in homepage_articles
                                 if a["date"] is None or a["date"] >= cutoff]
            all_articles.extend(homepage_articles)
            page.close()

            # --- Guide sections ---
            for type_id, label, max_pages in GUIDE_SECTIONS:
                _safe_print("[GGFW] === Section: {} ===".format(label))
                sys.stdout.flush()
                guide_articles = _crawl_guide_section(context, type_id, label, max_pages)
                _safe_print("[GGFW]   {} articles from {}".format(len(guide_articles), label))
                sys.stdout.flush()
                all_articles.extend(guide_articles)

            _safe_print("\n[GGFW] Total collected: {} articles".format(len(all_articles)))
            sys.stdout.flush()

            if not all_articles:
                _safe_print("[GGFW] No articles found.\n")
                browser.close()
                return

            # -- Dedup against processed -------------------------------------
            new_articles = [a for a in all_articles
                            if a.get("id") or a.get("url")]
            # Assign stable IDs
            for a in new_articles:
                if not a.get("id"):
                    a["id"] = _article_id(a.get("url", ""))

            if processed_ids:
                before = len(new_articles)
                new_articles = [a for a in new_articles
                                if a["id"] not in processed_ids]
                _safe_print("[GGFW] Skipping {} already processed".format(
                    before - len(new_articles)))
                sys.stdout.flush()
            all_articles = new_articles

            # Limit
            if args.max_articles and len(all_articles) > args.max_articles:
                all_articles = all_articles[:args.max_articles]

            _safe_print("[GGFW] To process: {} article(s)\n".format(len(all_articles)))
            sys.stdout.flush()

            if not all_articles:
                _safe_print("[GGFW] Nothing new.\n")
                browser.close()
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

                url = _normalize_url(url, _HOMEPAGE_URL)
                art["url"] = url

                title_short = art.get("title", "")[:60]
                _safe_print("  [{}/{}] {}".format(idx, len(all_articles), title_short))
                sys.stdout.flush()

                detail_page = context.new_page()
                try:
                    detail_page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    detail = _extract_detail_text(detail_page)

                    art["title"] = detail.get("title") or art.get("title", "")
                    if detail.get("date_str"):
                        art["date_str"] = detail["date_str"]
                    content = detail.get("content", "")
                    art["content"] = content
                    _safe_print("    content: {} chars".format(len(content)))
                except Exception as e:
                    _safe_print("    -> failed: {}".format(e))
                    art["content"] = "\u5185\u5bb9\u83b7\u53d6\u5931\u8d25: {}".format(e)
                    logging.warning("Detail page failed for %s: %s", url, e)
                finally:
                    try:
                        detail_page.close()
                    except Exception:
                        pass

                article_data.append(art)
                sys.stdout.flush()
                time.sleep(1.0)

            browser.close()

    except Exception as e:
        _safe_print("[GGFW] Fatal error: {}".format(e))
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
    _safe_print("\n[GGFW] Step 3/3: Saving & uploading ({} articles)...\n".format(
        len(article_data)))
    sys.stdout.flush()

    md_parts = []
    for art in article_data:
        content = art.get("content", "") or "\uff08\u65e0\u5185\u5bb9\uff09"
        title = art.get("title", "\u65e0\u6807\u9898")
        date_str = art.get("date_str", "")
        art_url = art.get("url", "")
        section = art.get("section", "")
        md = "# {}\n**\u680f\u76ee:** {}\n**\u65e5\u671f:** {}\n**URL:** {}\n\n{}\n".format(
            title, section, date_str, art_url, content
        )
        _save_markdown(md, output_dir, art.get("id", _article_id(art_url)))
        md_parts.append(md)

    if md_parts:
        combined = os.path.join(output_dir, "articles_combined.md")
        with open(combined, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))
        _safe_print("  Saved: {}".format(combined))

        new_ids = [a.get("id", _article_id(a.get("url", ""))) for a in article_data]
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

        if args.kb_id:
            _safe_print("  Uploading to KB {}...".format(args.kb_id))
            sys.stdout.flush()
            try:
                _upload_to_kb(combined, args.kb_id, args.tenant_id, parser_id="laws")
                _safe_print("  Upload OK")
            except Exception as e:
                _safe_print("  Upload failed: {}".format(e))
                logging.error("Upload: %s", e)

    _safe_print("\n[GGFW] " + "=" * 60)
    _safe_print("[GGFW] Done: {} articles".format(len(article_data)))
    _safe_print("[GGFW] " + "=" * 60 + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
