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
Dedicated web crawler for zfcg.czt.fujian.gov.cn 政策法规 (ZCFG) section.

Site characteristics
────────────────────
This site is a Vue.js SPA (gpcms-center-web framework) backed by a Java REST API.
All content is loaded asynchronously — the REST API requires an ``nsssjss``
signature header added by the website's own axios interceptors.

To satisfy this requirement, the crawler uses Playwright (Chromium) to load the
SPA and call the REST APIs through the website's Vue 2 ``$http`` instance.

Target section
──────────────
  政策法规 (ZCFG) — https://zfcg.czt.fujian.gov.cn/maincms-web/zcfg
    • channel:  268f9bf1-c144-4526-b3cd-0ace493d31d8
    • dictName: 政策法规

API endpoints (called via Vue $http)
────────────────────────────────────
  • Listing:  /gpcms/rest/web/v2/info/selectInfoForIndex
  • Detail:   /gpcms/rest/web/v2/info/getInfoById

Usage (typically spawned by task_executor):
    python zfcg_zcfg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zfcg.czt.fujian.gov.cn/maincms-web/zcfg \
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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ── Playwright (required) ─────────────────────────────────────────────────
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_URL = "https://zfcg.czt.fujian.gov.cn"
_API_BASE = "/gpcms/rest/web/v2"
_SITE_ID = "d36a6e8b-4363-4b52-a00b-79ca47033923"
_TARGET_CHANNEL = "268f9bf1-c144-4526-b3cd-0ace493d31d8"

_FRONTEND_BASE = "https://zfcg.czt.fujian.gov.cn"

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── JavaScript: call an API through the website's Vue 2 $http ────────────
# The website's axios interceptors automatically add the required ``nsssjss``
# signature header which cannot be replicated without the server-side key.

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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="zfcg.czt.fujian.gov.cn 政策法规 crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="ZCFG page URL (e.g. https://zfcg.czt.fujian.gov.cn/maincms-web/zcfg)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=0,
                        help="Max age in days for articles (0 = no limit)")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Max pages to crawl (0 = unlimited/all)")
    parser.add_argument("--page-size", type=int, default=20,
                        help="Page size for API calls (default: 20)")
    parser.add_argument("--channel", default=None,
                        help="Override channel ID (default: zcfg policy channel)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _find_chrome():
    """Return the first existing Chrome executable path, or None."""
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _vue_http_get(page, url, params=None, response_type=None):
    """Call an API through the website's Vue 2 $http (axios with interceptors)."""
    opts = {"params": params or {}}
    if response_type:
        opts["responseType"] = response_type
    return page.evaluate(_VUE_HTTP_JS, [url, opts])


def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
        "%Y年%m月%d日",
    ):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _html_to_markdown(html: str) -> str:
    """Convert the article's HTML ``content`` field to Markdown text."""
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    lines = []
    for el in soup.find_all([
        "p", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "blockquote", "pre", "img", "div",
        "span", "section", "table", "tr", "td", "th",
    ]):
        tn = el.name

        # ── Images ──────────────────────────────────────────────────
        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                if src.startswith("/"):
                    src = f"{_FRONTEND_BASE}{src}"
                alt_text = f" ({alt})" if alt else ""
                lines.append(f"![{alt_text}]({src})")
            continue

        # Skip inline text elements that are inside already-processed blocks
        if tn in ("span",) and el.find_parent(["p", "h1", "h2", "h3", "li"]):
            continue

        # Get direct text (avoid double-counting nested content)
        texts = []
        for child in el.children:
            if child.name is None:
                t = (child.string or "").strip()
                if t:
                    texts.append(t)
        if not texts:
            text = el.get_text(strip=True)
        else:
            text = " ".join(texts)
        if not text:
            continue

        # ── Block formatting ────────────────────────────────────────
        if tn == "h1":
            lines.append(f"\n# {text}\n")
        elif tn == "h2":
            lines.append(f"\n## {text}\n")
        elif tn == "h3":
            lines.append(f"\n### {text}\n")
        elif tn in ("h4", "h5", "h6"):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        elif tn in ("td", "th"):
            lines.append(f"| {text} |")
        elif tn == "p":
            lines.append(text)
        elif tn == "div" and not el.find_parent(["td", "th"]):
            lines.append(text)
        elif tn == "section":
            lines.append(text)

    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# API operations (via Vue $http)
# ---------------------------------------------------------------------------

def _fetch_article_list(page, channel, max_pages=0, max_days=365, page_size=20):
    """Fetch article list via selectInfoForIndex API with pagination.

    Returns list[dict] with keys: id, title, channel, author, noticeTime,
    publishTime, regionName, dataSource, lawCategory, lawCategoryName, etc.
    Filters out articles older than max_days.
    """
    articles = []
    page_index = 1
    cutoff = datetime.now() - timedelta(days=max_days) if max_days > 0 else None

    while True:
        t = int(time.time() * 1000)
        params = {
            "title": "",
            "region": "",
            "siteId": _SITE_ID,
            "channel": channel,
            "currPage": str(page_index),
            "pageSize": str(page_size),
            "noticeType": "",
            "cityOrArea": "",
            "purchaseManner": "",
            "openTenderCode": "",
            "purchaser": "",
            "agency": "",
            "purchaseNature": "",
            "operationStartTime": "",
            "operationEndTime": "",
            "verifyCode": "",
            "selectTimeName": "",
            "lawCategory": "",
            "_t": str(t),
        }

        data = _vue_http_get(
            page,
            f"{_API_BASE}/info/selectInfoForIndex",
            params=params,
        )

        if isinstance(data, dict) and data.get("error"):
            logging.error("API error on page %d: %s", page_index, data["error"])
            break

        code = data.get("code", "")
        if code != "200":
            logging.error("API returned code=%s, msg=%s on page %d",
                          code, data.get("msg", ""), page_index)
            break

        result = data.get("data") or {}
        rows = result.get("rows", [])
        total = result.get("total", 0)

        if not rows:
            break

        for row in rows:
            art_id = str(row.get("id", ""))
            title = (row.get("title") or "").strip()
            if not art_id or not title:
                continue

            # Date filter (use noticeTime or publishTime)
            date_str = (row.get("noticeTime") or row.get("publishTime") or "").strip()
            art_date = _parse_date(date_str)
            if art_date and cutoff and art_date < cutoff:
                continue

            articles.append({
                "id": art_id,
                "title": title,
                "channel": row.get("channel", channel),
                "author": (row.get("author") or "").strip(),
                "noticeTime": (row.get("noticeTime") or "").strip(),
                "publishTime": (row.get("publishTime") or "").strip(),
                "regionName": (row.get("regionName") or "").strip(),
                "dataSource": (row.get("dataSource") or "").strip(),
                "lawCategoryName": (row.get("lawCategoryName") or "").strip(),
                "publishNumber": (row.get("publishNumber") or "").strip(),
                "planId": (row.get("planId") or "").strip(),
                "date": art_date or _parse_date(date_str),
            })

        page_count = len(rows)
        logging.info(
            "Page %d: %d items (total=%d)",
            page_index, page_count, total,
        )

        # Stop conditions
        if page_count < page_size:
            break
        if max_pages and page_index >= max_pages:
            logging.info("Reached max_pages=%d limit, stopping pagination", max_pages)
            break
        page_index += 1

        # Small delay between pages
        time.sleep(0.5)

    return articles


def _fetch_article_detail(page, art):
    """Fetch article detail via getInfoById API.

    Returns dict with keys: content (HTML), attchList (attachments), and all listing fields.
    """
    t = int(time.time() * 1000)
    params = {
        "id": art["id"],
        "planId": art.get("planId", ""),
        "channel": art.get("channel", ""),
        "type": art.get("dataSource", ""),
        "siteId": _SITE_ID,
        "_t": str(t),
    }
    data = _vue_http_get(page, f"{_API_BASE}/info/getInfoById", params=params)

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Detail API error for {art['id']}: {data['error']}")

    code = data.get("code", "")
    if code != "200":
        raise RuntimeError(
            f"Detail API error for {art['id']}: code={code}, msg={data.get('msg', '')}"
        )

    return data.get("data") or {}


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachments(page, attch_list, dest_dir, cookie_headers):
    """Download attachments from attchList to dest_dir.

    Returns list of local file paths.
    """
    downloaded = []
    if not attch_list:
        return downloaded

    os.makedirs(dest_dir, exist_ok=True)

    for att in attch_list:
        file_url = att.get("fileUrl", "")
        file_name = att.get("fileName", "attachment")
        file_ext = att.get("fileExt", "")

        if not file_url:
            continue

        # Clean filename
        file_name = re.sub(r'[\\/:*?"<>|]', "_", file_name)
        if file_ext and not file_name.lower().endswith(file_ext.lower()):
            file_name = file_name + file_ext

        dest_path = os.path.join(dest_dir, file_name)
        if os.path.exists(dest_path):
            downloaded.append(dest_path)
            continue

        # Build full download URL
        parsed = urlparse(file_url)
        clean_url = parsed.path
        if parsed.query:
            clean_url += "?" + parsed.query

        # Strip gateway prefixes
        for prefix in (
            "/freecms/download/gateway/gpx-document-zc/common/v3/base/download/",
            "/freecms/download/",
        ):
            if clean_url.startswith(prefix):
                clean_url = clean_url[len(prefix):]
                break

        if not clean_url.startswith("/"):
            clean_url = "/" + clean_url

        clean_url = re.sub(r"^/?downloadPublicFile(\?)", r"/gpx-public-file\1", clean_url)
        download_url = f"{_FRONTEND_BASE}{clean_url}"

        # Strategy 1: Vue $http arraybuffer
        try:
            result = _vue_http_get(
                page,
                clean_url,
                params={},
                response_type="arraybuffer",
            )
            if result.get("b64"):
                import base64
                blob = base64.b64decode(result["b64"])
                if len(blob) >= 100:
                    with open(dest_path, "wb") as f:
                        f.write(blob)
                    downloaded.append(dest_path)
                    logging.info("Downloaded: %s (%d bytes)", file_name, len(blob))
                    continue
        except Exception as e:
            logging.warning("Vue $http download failed for %s: %s", file_name, e)

        # Strategy 2: requests with session cookies
        try:
            resp = requests.get(
                download_url,
                headers={**cookie_headers, "User-Agent": _USER_AGENT,
                         "Referer": f"{_FRONTEND_BASE}/"},
                timeout=60,
            )
            if resp.ok and len(resp.content) >= 100 and b"<html" not in resp.content[:20]:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                downloaded.append(dest_path)
                logging.info("Downloaded: %s (%d bytes)", file_name, len(resp.content))
            else:
                logging.warning(
                    "Download failed for %s: HTTP %d, %d bytes",
                    file_name, resp.status_code, len(resp.content),
                )
        except Exception as e:
            logging.warning("Download failed for %s: %s", file_name, e)

    return downloaded


# ---------------------------------------------------------------------------
# State management
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


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_article_to_kb(kb, tenant_id, kb_parent, article, markdown_text, att_files, output_dir):
    """Upload a single article (MD + attachments) to KB as a per-article folder."""
    from api.db.services.file_service import FileService
    from api.db import FileType

    # Create per-article folder in KB
    title_clean = re.sub(r'[\\/:*?"<>|]', "_", article.get("title", "untitled"))[:80]
    date_str = (article.get("noticeTime") or article.get("publishTime") or "")[:10]
    folder_name = f"{date_str}_{title_clean}" if date_str else title_clean
    folder_name = folder_name[:120]

    article_folder = FileService.new_a_file_from_kb(
        tenant_id, folder_name, kb_parent["id"], ty=FileType.FOLDER.value,
    )

    # Save MD and upload
    staging = os.path.join(output_dir, "staging", folder_name)
    os.makedirs(staging, exist_ok=True)

    md_path = os.path.join(staging, f"{folder_name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    _upload_file_to_kb_folder(md_path, kb, tenant_id, article_folder["id"], parser_id="general")

    # Upload attachments
    ext_laws = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}
    for fp in att_files:
        ext = os.path.splitext(fp)[1].lower()
        pid = "laws" if ext in ext_laws else "general"
        try:
            _upload_file_to_kb_folder(fp, kb, tenant_id, article_folder["id"], parser_id=pid)
        except Exception as e:
            logging.warning("Attachment upload error for %s: %s", os.path.basename(fp), e)

    return article_folder["id"]


def _upload_file_to_kb_folder(filepath, kb, tenant_id, parent_folder_id, parser_id="laws"):
    """Upload a local file to a specific KB folder and queue parsing."""
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[ZCFG] 福建省政府采购网 - 政策法规 crawler")
    _safe_print(f"[ZCFG] Target URL: {args.target_url}")
    _safe_print(f"[ZCFG] Task name: {args.task_name}")
    _safe_print(f"[ZCFG] Target KB: {args.kb_id}")
    _safe_print(f"[ZCFG] Max days: {args.max_days}")
    if args.max_pages:
        _safe_print(f"[ZCFG] Max pages: {args.max_pages}")
    if args.max_articles:
        _safe_print(f"[ZCFG] Max articles: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZCFG policy crawler started for %s ===", args.target_url)

    # ── Resolve channel ─────────────────────────────────────────────────
    channel = args.channel or _TARGET_CHANNEL

    # ── Chrome check ────────────────────────────────────────────────────
    chrome_path = _find_chrome()
    if not chrome_path:
        _safe_print("[ZCFG] ERROR: Chrome not found. Check CHROME_PATHS.")
        sys.stdout.flush()
        sys.exit(1)

    # ── Output dir & state ──────────────────────────────────────────────
    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip(),
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZCFG] Output directory: {output_dir}\n")
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[ZCFG] Already processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # ── KB setup ────────────────────────────────────────────────────────
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService

    ok, kb = KnowledgebaseService.get_by_id(args.kb_id)
    if not ok:
        _safe_print(f"[ZCFG] ERROR: Knowledge base {args.kb_id} not found.")
        sys.stdout.flush()
        sys.exit(1)
    _safe_print(f"[ZCFG] KB: {kb.name}")
    sys.stdout.flush()

    kb_root_folder = FileService.get_kb_folder(args.tenant_id)
    kb_parent = FileService.new_a_file_from_kb(
        args.tenant_id, kb.name, kb_root_folder["id"],
    )

    # ═════════════════════════════════════════════════════════════════════
    # Step 1: Launch Playwright, load SPA, capture API data
    # ═════════════════════════════════════════════════════════════════════
    _safe_print("[ZCFG] Step 1/4: Loading SPA and initializing Vue app...\n")
    sys.stdout.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome_path)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        # Load the zcfg page to initialize the Vue app
        page.goto(args.target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        # ── Extract cookies for attachment downloads ─────────────────
        cookies = context.cookies()
        cookie_headers = {
            "Cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
        }

        try:

            # ═══════════════════════════════════════════════════════════
            # Step 2: Fetch article listing
            # ═══════════════════════════════════════════════════════════
            _safe_print("[ZCFG] Step 2/4: Fetching article listing via signed API...\n")
            sys.stdout.flush()

            all_articles = _fetch_article_list(
                page,
                channel=channel,
                max_pages=args.max_pages,
                max_days=args.max_days,
                page_size=args.page_size,
            )

            _safe_print(f"[ZCFG] Fetched {len(all_articles)} total articles\n")
            sys.stdout.flush()

            if not all_articles:
                _safe_print("[ZCFG] No articles found, exiting.")
                sys.stdout.flush()
                return

            # ── Limit max articles ──────────────────────────────────
            if args.max_articles and len(all_articles) > args.max_articles:
                all_articles = all_articles[:args.max_articles]
                _safe_print(f"[ZCFG] Limited to {args.max_articles} article(s)\n")
                sys.stdout.flush()

            # ── Filter already-processed ────────────────────────────
            if processed_ids:
                new_articles = [
                    a for a in all_articles
                    if a["id"] not in processed_ids
                ]
                skipped = len(all_articles) - len(new_articles)
                if skipped:
                    _safe_print(f"[ZCFG] Skipping {skipped} already-processed article(s)\n")
                    sys.stdout.flush()
                all_articles = new_articles

            if not all_articles:
                _safe_print("[ZCFG] All articles already processed, nothing to do.")
                sys.stdout.flush()
                return

            _safe_print(f"[ZCFG] New articles to process: {len(all_articles)}\n")
            sys.stdout.flush()

            # ═══════════════════════════════════════════════════════════
            # Step 3: Fetch details & format Markdown
            # ═══════════════════════════════════════════════════════════
            _safe_print(f"[ZCFG] Step 3/4: Fetching {len(all_articles)} article details...\n")
            sys.stdout.flush()

            success_count = 0
            fail_count = 0
            total = len(all_articles)
            new_ids = []

            for idx, art in enumerate(all_articles, 1):
                title_preview = art["title"][:70]
                _safe_print(f"[ZCFG] [{idx}/{total}] {title_preview}")
                sys.stdout.flush()
                logging.info("[%d/%d] %s", idx, total, art["title"])

                try:
                    detail = _fetch_article_detail(page, art)

                    html_content = detail.get("content") or ""
                    markdown_text = _html_to_markdown(html_content)
                    attch_list = detail.get("attchList") or []

                    # Download attachments
                    att_files = []
                    if attch_list:
                        dest_dir = os.path.join(
                            output_dir, "downloads",
                            re.sub(r'[\\/:*?"<>|]', "_", art["id"])[:80],
                        )
                        att_files = _download_attachments(
                            page, attch_list, dest_dir, cookie_headers,
                        )
                except Exception as e:
                    _safe_print(f"[ZCFG]   -> ERROR: {e}")
                    sys.stdout.flush()
                    logging.error("Failed to fetch detail for %s: %s", art["id"], e)
                    fail_count += 1
                    continue

                # ── Build Markdown ──────────────────────────────────
                date_str = (art.get("noticeTime") or art.get("publishTime") or "")[:10]
                article_url = (
                    f"{_FRONTEND_BASE}/maincms-web/articleDetail"
                    f"?type={art.get('dataSource', 'manual')}"
                    f"&id={art['id']}"
                    f"&planId={art.get('planId', '')}"
                    f"&channel={art.get('channel', '')}"
                )

                lines = [
                    f"# {art['title']}",
                    f"**Section:** 政策法规",
                ]
                if art.get("lawCategoryName"):
                    lines.append(f"**Category:** {art['lawCategoryName']}")
                if art.get("author"):
                    lines.append(f"**Author:** {art['author']}")
                if art.get("publishNumber"):
                    lines.append(f"**Document No:** {art['publishNumber']}")
                if date_str:
                    lines.append(f"**Date:** {date_str}")
                lines.append(f"**URL:** {article_url}")
                lines.append("")

                if markdown_text:
                    lines.append(markdown_text)
                else:
                    lines.append("（暂无详细内容）")

                if att_files:
                    lines.append("")
                    lines.append("## 附件")
                    lines.append("")
                    for fp in att_files:
                        fname = os.path.basename(fp)
                        lines.append(f"- [{fname}]({fname})")

                lines.append("")
                lines.append("---")

                markdown_full = "\n".join(lines)

                # ── Upload to KB ────────────────────────────────────
                try:
                    folder_id = _upload_article_to_kb(
                        kb, args.tenant_id, kb_parent,
                        art, markdown_full, att_files, output_dir,
                    )
                    _safe_print(f"[ZCFG]   -> {len(markdown_text)} chars, {len(att_files)} attachment(s)")
                    sys.stdout.flush()
                    success_count += 1
                    new_ids.append(art["id"])
                except Exception as e:
                    _safe_print(f"[ZCFG]   -> KB upload ERROR: {e}")
                    sys.stdout.flush()
                    logging.error("KB upload failed for %s: %s", art["id"], e)
                    fail_count += 1

                # Small delay between API calls
                time.sleep(0.3)

            _safe_print(
                f"\n[ZCFG] Detail pages: {success_count} success, {fail_count} failed\n"
            )
            sys.stdout.flush()

            # ═══════════════════════════════════════════════════════════
            # Step 4: Save state
            # ═══════════════════════════════════════════════════════════
            if new_ids:
                processed_ids.update(new_ids)
                _save_state(output_dir, {"processed_ids": list(processed_ids)})

            _safe_print(f"\n[ZCFG] {'='*50}")
            _safe_print(f"[ZCFG] Done. Processed {len(new_ids)} new article(s).")
            _safe_print(f"[ZCFG] {'='*50}\n")
            sys.stdout.flush()

        finally:
            browser.close()


if __name__ == "__main__":
    CONSUMER_NAME = "zfcg_zcfg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
