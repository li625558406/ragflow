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
Dedicated web crawler for www.chinabidding.com.cn (中国采购与招标网).

Site characteristics
────────────────────
The site is a Nuxt.js SPA split across two domains:
  • www.chinabidding.com.cn  – portal / news (partial SSR for homepage + detail)
  • www.chinabidding.cn      – bidding content (Alibaba Cloud WAF, login-gated)

Key findings (verified 2026-05):
  1. Listing pages (e.g. /notice/1.html) return 404 or empty Nuxt shells even
     with Selenium — the Vue app cannot reach its APIs from a headless browser.
  2. The homepage SSR (622 KB) contains ~40 article links across all categories
     — this is the ONLY reliable article-discovery mechanism.
  3. Detail pages on .com.cn  (/pageInfoSsr/{catId}/{articleId}) are SSR and
     fully accessible via simple requests — free content with full text.
  4. Detail pages on .cn (e.g. /zbgg/xxx.html) are behind an Alibaba Cloud WAF
     AND a login wall — the user must have a paid account to view full content
     or download bidding documents.
  5. No downloadable PDFs/DOCs were found on any freely accessible page.
  6. undetected_chromedriver bypasses the Alibaba Cloud WAF for .cn pages.
  7. Even with valid login cookies, .cn bidding details may still be gated
     behind a paid membership tier ("对会员开放，需注册/登录").

Strategy
────────
  1. Selenium Chrome        — load the homepage, extract all article links.
  2. requests               — fetch .com.cn SSR detail pages (fast, full text).
  3. undetected_chromedriver — bypass WAF on .cn detail pages, optionally
                               inject auth cookies from CLI.
  4. Save markdown & upload to KB with parser_id="laws".

Usage
─────
    # Without cookies (public content only)
    python chinabidding_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://www.chinabidding.com.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>

    # With auth cookies for .cn pages
    python chinabidding_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://www.chinabidding.com.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME> \\
        --cookie "acw_tc=xxx; loginUserName=xxx; token=xxx"

Docker: Chrome 121 + ChromeDriver are pre-installed in the RAGFlow image.
        undetected_chromedriver requires pip install (included in pyproject.toml).
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

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR_TOP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_SCRIPT_DIR_TOP, "..", "..")))
from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient

# ---------------------------------------------------------------------------
# Selenium (optional)
# ---------------------------------------------------------------------------
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ---------------------------------------------------------------------------
# undetected-chromedriver (for .cn WAF-bypass)
# ---------------------------------------------------------------------------
try:
    import undetected_chromedriver as uc
    UC_AVAILABLE = True
except ImportError:
    UC_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HOMEPAGE_URL = "https://www.chinabidding.com.cn"
_CN_BASE = "https://www.chinabidding.cn"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_CHROME_PATHS = [
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]
_DRIVER_PATHS = [
    "/usr/local/bin/chromedriver",
    "/usr/bin/chromedriver",
    "/usr/bin/chromium-chromedriver",
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


def _normalise_url(href):
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return urljoin(_HOMEPAGE_URL, href)
    return href


def _article_id(url):
    """Unique stable ID for a URL."""
    u = url.rstrip("/").rstrip(".html")
    parts = u.rsplit("/", 1)
    return parts[-1] if len(parts) > 1 else get_uuid()[:12]


# ---------------------------------------------------------------------------
# Selenium driver
# ---------------------------------------------------------------------------

def _create_driver():
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("selenium not installed — pip install selenium")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"user-agent={_USER_AGENT}")
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            opts.binary_location = p
            break
    svc = None
    for p in _DRIVER_PATHS:
        if os.path.exists(p):
            svc = Service(p)
            break
    kwargs = {"options": opts}
    if svc:
        kwargs["service"] = svc
    d = webdriver.Chrome(**kwargs)
    d.set_page_load_timeout(30)
    return d


def _create_cn_driver(cookie_str=None, version_main=147):
    """Create an undetected_chromedriver for .cn (WAF-protected) pages.

    Args:
        cookie_str: Optional raw cookie string to inject after WAF passes.
        version_main: Chrome major version (default 147 for user's env).
    """
    if not UC_AVAILABLE:
        _safe_print("  undetected_chromedriver not installed — falling back to vanilla Selenium")
        return _create_driver()

    _safe_print("  Launching undetected_chromedriver for .cn pages...")
    sys.stdout.flush()

    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"user-agent={_USER_AGENT}")
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            opts.binary_location = p
            break

    try:
        driver = uc.Chrome(
            options=opts,
            version_main=version_main,
        )
        driver.set_page_load_timeout(30)
    except Exception as e:
        logging.warning("uc.Chrome init failed (%s), trying vanilla", e)
        return _create_driver()

    # Inject auth cookies if provided
    if cookie_str:
        _safe_print("  Injecting auth cookies for chinabidding.cn...")
        try:
            driver.get("https://www.chinabidding.cn")
            time.sleep(5)  # let WAF challenge complete
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                name, value = pair.split("=", 1)
                driver.add_cookie({"name": name.strip(), "value": value.strip(),
                                    "domain": ".chinabidding.cn"})
            _safe_print("  Cookies injected.")
        except Exception as e:
            logging.warning("Cookie injection failed: %s", e)
        sys.stdout.flush()

    return driver


# ---------------------------------------------------------------------------
# Step 1: Discover articles from the homepage (Selenium)
# ---------------------------------------------------------------------------

def discover_articles_from_homepage(driver):
    """Extract all article links from the rendered homepage.

    Returns list of dicts: [{url, title, date_str, category}]
    """
    _safe_print("  Loading homepage...")
    driver.get(_HOMEPAGE_URL)
    time.sleep(3)
    src_len = len(driver.page_source)
    _safe_print(f"  Homepage loaded: {src_len} bytes")

    links = driver.find_elements(By.TAG_NAME, "a")
    seen = set()
    articles = []

    for a in links:
        try:
            href = (a.get_attribute("href") or "").strip()
            title = a.get_attribute("title") or a.text.strip()
            if not href or not title or len(title) < 5:
                continue
        except Exception:
            continue

        # Only keep relevant URLs
        if not any(p in href for p in (
            "/pageInfoSsr/",
            "chinabidding.cn/zbgg/",
            "chinabidding.cn/zfcg/",
            "chinabidding.cn/xmxx/",
        )):
            continue

        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)

        # Categorise by URL pattern
        if "/pageInfoSsr/" in href:
            cat = "资讯"
            # Extract catId from URL for more specific categorisation
            m = re.search(r"/pageInfoSsr/(\d+)", href)
            if m:
                _CAT_MAP = {
                    "3000000016366": "综合要闻",
                    "3000000000466": "新闻",
                    "3000000016066": "招标公告",
                    "3000000017166": "政策法规",
                    "3000000016766": "实务问答",
                    "3000000016666": "曝光台",
                    "3000000009866": "综合要闻",
                    "3000000009966": "行业动态",
                    "3000000010066": "实务问答",
                    "3000000010166": "专家观点",
                    "3000000010266": "曝光台",
                    "3000000010366": "企业动态",
                    "3000000010466": "会展信息",
                    "3000000010566": "专题论坛",
                }
                cat = _CAT_MAP.get(m.group(1), "资讯")
        elif "zbgg" in href:
            cat = "招标公告"
        elif "zfcg" in href:
            cat = "采购公告"
        elif "xmxx" in href:
            cat = "项目信息"
        else:
            cat = "其他"

        # Try to find a nearby date element
        date_str = ""
        try:
            parent = a.find_element(By.XPATH, "..")
            date_el = parent.find_element(By.CLASS_NAME, "item-right")
            date_str = date_el.text.strip()[:10]
        except Exception:
            pass

        articles.append({
            "url": href,
            "title": title[:200],
            "date_str": date_str,
            "category": cat,
        })

    return articles


# ---------------------------------------------------------------------------
# Step 2: Fetch detail content
# ---------------------------------------------------------------------------

def fetch_ssr_detail(url, client=None):
    """Fetch a .com.cn /pageInfoSsr/ page (full SSR content)."""
    try:
        if client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
        else:
            resp = requests.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=30
            )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logging.warning("SSR fetch failed %s: %s", url, e)
        return None


def _extract_meta_content(html, name):
    """Get a meta tag's content attribute by name."""
    m = re.search(
        rf'<meta[^>]+name=["\']{name}["\'][^>]*content=["\']([^"\']*)["\']',
        html, re.I
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*name=["\']{name}["\']',
        html, re.I
    )
    return m.group(1) if m else ""


def parse_ssr_article(html):
    """Parse SSR article from .com.cn /pageInfoSsr/ pages.

    SSR structure varies by article category — some have a full cc-article
    content div, others only expose a meta-description summary.

    Returns (title, date_str, content_text, file_urls).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    t = soup.find("title")
    if t:
        title = t.get_text(strip=True)

    # Date – meta tags
    date_str = ""
    for meta in soup.find_all("meta"):
        if (meta.get("name") or "").lower() in ("pubdate", "publishdate",
                                                  "dc.date"):
            date_str = (meta.get("content") or "")[:10]
            break
    if not date_str:
        m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", html[:2000])
        if m:
            date_str = m.group(1)

    # Content – multi-strategy
    content_text = ""
    file_urls = []

    # Strategy 1: cc-article div (full content)
    article_div = soup.find("div", class_="cc-article")
    if article_div:
        content_text = article_div.get_text("\n", strip=True)
        for a in article_div.find_all("a", href=True):
            h = a["href"]
            if re.search(r"\.(pdf|docx?|xlsx?|zip|rar)$", h, re.I):
                file_urls.append(_normalise_url(h))

    # Strategy 2: other known content containers
    if not content_text or len(content_text) < 50:
        for cls in ("article-content", "detail-content", "main-content"):
            div = soup.find("div", class_=cls)
            if div:
                txt = div.get_text("\n", strip=True)
                if len(txt) > len(content_text):
                    content_text = txt

    # Strategy 3: meta description (summary only)
    if not content_text or len(content_text) < 50:
        desc = _extract_meta_content(html, "description")
        if desc and len(desc) > len(content_text):
            content_text = desc

    # Strategy 4: Nuxt SSR body text (last resort – includes nav noise)
    if not content_text or len(content_text) < 50:
        body = soup.find("body")
        if body:
            txt = body.get_text("\n", strip=True)
            if len(txt) > len(content_text):
                content_text = txt[:5000]

    return title, date_str, content_text, file_urls


def fetch_cn_detail_selenium(driver, url):
    """Load a .cn bidding page with Selenium.

    These pages are behind a login wall — we collect what metadata is
    exposed before the gate.
    """
    logging.info("Selenium loading .cn detail: %s", url)
    try:
        driver.get(url)
        time.sleep(3)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        pass
    return driver.page_source


def parse_cn_detail(html):
    """Parse .cn bidding page (may be login-gated)."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    t = soup.find("title")
    if t:
        title = t.get_text(strip=True)

    # Try to find content — likely limited behind login
    content_text = ""
    for sel in ({"class_": "detail-content"}, {"class_": "article-content"},
                {"class_": "main-content"}, {"class_": "content"}, {"id": "content"}):
        div = soup.find("div", **sel)
        if div:
            content_text = div.get_text("\n", strip=True)[:3000]
            break
    if not content_text:
        body = soup.find("body")
        if body:
            content_text = body.get_text("\n", strip=True)[:3000]

    date_str = ""
    m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", html[:3000])
    if m:
        date_str = m.group(1)

    file_urls = []
    for a in soup.find_all("a", href=True):
        if re.search(r"\.(pdf|docx?|xlsx?|zip|rar)$", a["href"], re.I):
            file_urls.append(_normalise_url(a["href"]))

    return title, date_str, content_text, file_urls


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    p = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load state: %s", e)
    return {"processed_ids": []}


def _save_state(output_dir, state):
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, _STATE_FILENAME), "w",
              encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d processed)", len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    d = os.path.join(output_dir, "articles")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{article_id}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"KB {kb_id} not found")
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
    p = argparse.ArgumentParser(description="chinabidding.com.cn crawler")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Comma-separated: 招标公告,综合要闻,政策法规,...")
    p.add_argument("--max-articles", type=int, default=0)
    p.add_argument("--max-days", type=int, default=30)
    p.add_argument("--cookie", default=None,
                   help="Raw cookie string for chinabidding.cn authentication")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print("[CHINABIDDING] 中国采购与招标网 crawler")
    _safe_print(f"[CHINABIDDING] KB: {args.kb_id} | max-days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[CHINABIDDING] Max articles: {args.max_articles}")
    if args.cookie:
        _safe_print(f"[CHINABIDDING] Auth cookies provided for .cn pages")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== chinabidding crawler started ===")

    client = PlaywrightHttpClient()
    client.start()
    try:

        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
        output_dir = args.output_dir or os.path.join(
            project_root, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[CHINABIDDING] Output: {output_dir}\n")
        sys.stdout.flush()

        # -- Filter by section ---------------------------------------------------
        section_filter = None
        if args.section:
            section_filter = set(s.strip() for s in args.section.split(","))

        # -- State ---------------------------------------------------------------
        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[CHINABIDDING] Previously processed: {len(processed_ids)}\n")
        sys.stdout.flush()

        # ====================================================================
        # Step 1: Discover articles from homepage (Selenium required)
        # ====================================================================
        _safe_print("[CHINABIDDING] Step 1/4: Discovering articles from homepage...")
        sys.stdout.flush()

        if not SELENIUM_AVAILABLE:
            _safe_print("[CHINABIDDING] ERROR: selenium required — aborting.")
            sys.exit(1)

        driver = _create_driver()
        try:
            all_articles = discover_articles_from_homepage(driver)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

        _safe_print(f"[CHINABIDDING] Found {len(all_articles)} article(s) on homepage\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[CHINABIDDING] No articles found — aborting.\n")
            return

        # -- Filter by section
        if section_filter:
            all_articles = [a for a in all_articles if a["category"] in section_filter]
            _safe_print(f"[CHINABIDDING] After section filter: {len(all_articles)}\n")

        # -- Deduplicate by URL
        seen = set()
        deduped = []
        for a in all_articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                deduped.append(a)
        all_articles = deduped

        # -- Filter processed
        if processed_ids:
            new = [a for a in all_articles if _article_id(a["url"]) not in processed_ids]
            _safe_print(f"[CHINABIDDING] Skipping {len(all_articles) - len(new)} already processed\n")
            all_articles = new

        # -- Limit
        if args.max_articles and len(all_articles) > args.max_articles:
            all_articles = all_articles[:args.max_articles]

        _safe_print(f"[CHINABIDDING] To process: {len(all_articles)} articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[CHINABIDDING] Nothing new.\n")
            return

        # ====================================================================
        # Step 2: Fetch detail pages
        # ====================================================================
        _safe_print(f"[CHINABIDDING] Step 2/4: Fetching details...\n")
        sys.stdout.flush()

        cn_driver = None
        article_data = []
        try:
            for idx, art in enumerate(all_articles, 1):
                url = art["url"]
                _safe_print(f"  [{idx}/{len(all_articles)}] {art['title'][:60]}")
                sys.stdout.flush()

                is_cn = "chinabidding.cn" in url
                title, date_str, content, file_urls = "", "", "", []

                if is_cn:
                    if cn_driver is None:
                        cn_driver = _create_cn_driver(
                            cookie_str=args.cookie,
                            version_main=147,
                        )
                    html = fetch_cn_detail_selenium(cn_driver, url)
                    if html and len(html) > 5000:
                        title, date_str, content, file_urls = parse_cn_detail(html)
                    if not date_str:
                        date_str = art.get("date_str", "")
                    _safe_print(f"    .cn page — content: {len(content)} chars, "
                                f"files: {len(file_urls)}")
                else:
                    html = fetch_ssr_detail(url, client=client)
                    if html:
                        title, date_str, content, file_urls = parse_ssr_article(html)
                    if not date_str:
                        date_str = art.get("date_str", "")
                    _safe_print(f"    content: {len(content)} chars, "
                                f"files: {len(file_urls)}")

                sys.stdout.flush()

                article_data.append({
                    **art,
                    "title": title or art["title"],
                    "date_str": date_str,
                    "content": content,
                    "file_urls": file_urls,
                })
        finally:
            if cn_driver:
                try:
                    cn_driver.quit()
                except Exception:
                    pass

        # ====================================================================
        # Step 3: Download file attachments
        # ====================================================================
        _safe_print(f"\n[CHINABIDDING] Step 3/4: Downloading attachments...\n")
        sys.stdout.flush()

        dl_dir = os.path.join(output_dir, "downloads")
        downloaded = []
        for art in article_data:
            for fu in art.get("file_urls", []):
                try:
                    r = client.get(fu, headers={"User-Agent": _USER_AGENT}, timeout=60)
                    r.raise_for_status()
                    fname = os.path.basename(fu.split("?")[0])
                    if not fname or "." not in fname:
                        fname = f"file_{get_uuid()[:8]}"
                    os.makedirs(dl_dir, exist_ok=True)
                    fp = os.path.join(dl_dir, fname)
                    with open(fp, "wb") as f:
                        f.write(r.text.encode("utf-8"))
                    downloaded.append(fp)
                    _safe_print(f"  Downloaded: {fname}")
                except Exception as e:
                    logging.warning("Download failed %s: %s", fu, e)

        if downloaded:
            _safe_print(f"\n  Total files: {len(downloaded)}\n")
        else:
            _safe_print("  No downloadable files found (bidding documents require login).\n")
        sys.stdout.flush()

        # ====================================================================
        # Step 4: Save & upload
        # ====================================================================
        _safe_print("[CHINABIDDING] Step 4/4: Saving & uploading...\n")
        sys.stdout.flush()

        md_parts = []
        for art in article_data:
            # If no content was retrieved (login wall), note it
            body = art.get("content", "") or "（需登录查看完整内容）"
            md = (
                f"# {art['title']}\n"
                f"**日期:** {art.get('date_str', '')}\n"
                f"**分类:** {art.get('category', '')}\n"
                f"**来源:** {art.get('url', '')}\n\n"
                f"{body}\n"
            )
            if art.get("file_urls"):
                md += "\n**附件:**\n"
                for fu in art["file_urls"]:
                    md += f"- {fu}\n"

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

            # Upload
            if args.kb_id:
                _safe_print(f"  Uploading to KB {args.kb_id}...")
                sys.stdout.flush()
                try:
                    _upload_to_kb(combined, args.kb_id, args.tenant_id, parser_id="laws")
                    _safe_print("  Upload OK")
                except Exception as e:
                    _safe_print(f"  Upload failed: {e}")
                    logging.error("Upload: %s", e)

                for fp in downloaded:
                    try:
                        _upload_to_kb(fp, args.kb_id, args.tenant_id, parser_id="laws")
                        _safe_print(f"  Uploaded: {os.path.basename(fp)}")
                    except Exception as e:
                        _safe_print(f"  Upload failed: {os.path.basename(fp)}: {e}")

        _safe_print(f"\n[CHINABIDDING] {'='*60}")
        _safe_print(f"[CHINABIDDING] Done: {len(article_data)} articles, "
                    f"{len(downloaded)} files")
        _safe_print(f"[CHINABIDDING] {'='*60}\n")
        sys.stdout.flush()
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "chinabidding_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
