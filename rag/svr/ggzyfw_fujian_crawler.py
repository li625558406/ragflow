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
Dedicated web crawler for ggzyfw.fujian.gov.cn (福建省公共资源交易电子公共服务平台).

This is a Vue.js SPA with a signed (MD5 + app secret) and AES-256-CBC encrypted API.
All listing and article data is fetched via API calls (POST /FwPortalApi/Article/PageList
and POST /FwPortalApi/Article/Detail), then decrypted client-side.

Three content sections:
  - 新闻动态 (type=11) — ~738 articles
  - 通知公告 (type=12) — ~893 articles
  - 行业动态 (type=13) — ~1033 articles

Usage (typically spawned by task_executor):
    python ggzyfw_fujian_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ggzyfw.fujian.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from base64 import b64decode
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests  # fallback only
from bs4 import BeautifulSoup
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# API crypto constants (reverse-engineered from JS module a078)
# ---------------------------------------------------------------------------
APP_SECRET = "B3978D054A72A7002063637CCDF6B2E5"
AES_KEY = "EB444973714E4A40876CE66BE45D5930"  # 32 bytes → AES-256
AES_IV = "B5A8904209931867"                   # 16 bytes

# ---------------------------------------------------------------------------
# API base
# ---------------------------------------------------------------------------
_API_BASE = "https://ggzyfw.fj.gov.cn/FwPortalApi"

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
SECTIONS = {
    "xwdt":   (11, "新闻动态"),
    "tzgg":   (12, "通知公告"),
    "hydt":   (13, "行业动态"),
}

_FRONTEND_BASE = "https://ggzyfw.fujian.gov.cn"


def parse_args():
    parser = argparse.ArgumentParser(description="Fujian GGZYFW crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL (e.g. https://ggzyfw.fujian.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None, help="Comma-separated list of section labels to crawl (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=90,
                        help="Max age in days for articles to crawl (default: 90)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _init():
    settings.init_settings()
    logging.info("Project settings initialised")


# ---------------------------------------------------------------------------
# API helpers — signing & AES decryption
# ---------------------------------------------------------------------------

def _sign(params: dict) -> str:
    """MD5 signing: MD5(APP_SECRET + sorted_key_value_string).lower()."""
    sorted_keys = sorted(params.keys(), key=str.upper)
    raw = APP_SECRET
    for k in sorted_keys:
        raw += f"{k}{params[k]}"
    return hashlib.md5(raw.encode()).hexdigest()


def _aes_decrypt(data_b64: str) -> dict:
    """AES-256-CBC decrypt the API response Data field."""
    cipher = AES.new(AES_KEY.encode(), AES.MODE_CBC, AES_IV.encode())
    decrypted = unpad(cipher.decrypt(b64decode(data_b64)), AES.block_size)
    return json.loads(decrypted.decode("utf-8"))


def _api_post(endpoint: str, body: dict, client: PlaywrightHttpClient = None) -> dict:
    """POST to the signed/encrypted API and return the decrypted response."""
    ts = int(time.time() * 1000)
    body["ts"] = ts
    sig = _sign(body)

    headers = {
        "portal-sign": sig,
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Origin": "https://ggzyfw.fujian.gov.cn",
        "Referer": "https://ggzyfw.fujian.gov.cn/",
    }

    url = f"{_API_BASE}{endpoint}"
    if client is not None:
        resp = client.post(url, json_body=body, headers=headers, timeout=30)
    else:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("Success") and data.get("Data"):
        return _aes_decrypt(data["Data"])
    raise RuntimeError(f"API error: {data}")


# ---------------------------------------------------------------------------
# Listing — API-based pagination
# ---------------------------------------------------------------------------

def _fetch_article_list(api_type: int, max_articles: int = 0,
                        max_days: int = 90, client: PlaywrightHttpClient = None):
    """Fetch article list for a given type via PageList API with pagination.

    Returns list[dict] with keys: id, title, type, tm, section_label.
    Filters out articles older than max_days.
    """
    articles = []
    page_index = 1
    page_size = 50
    cutoff = datetime.now() - timedelta(days=max_days)

    while True:
        body = {"pageSize": page_size, "type": api_type, "pageIndex": page_index}
        result = _api_post("/Article/PageList", body, client=client)

        table = result.get("Table") or []
        for row in table:
            art_id = str(row.get("ID", ""))
            title = (row.get("TITLE") or "").strip()
            tm_str = (row.get("TM") or "").strip()

            if not art_id or not title:
                continue

            # Parse date for filtering
            art_date = _parse_date(tm_str)
            if art_date and art_date < cutoff:
                # List is sorted newest-first; once we hit old articles, stop entirely
                logging.debug(
                    "Hit date cutoff at article %s (%s), stopping pagination",
                    art_id, tm_str,
                )
                # Return what we have — no need to fetch older pages
                return articles

            articles.append({
                "id": art_id,
                "title": title,
                "type": str(api_type),
                "tm": tm_str,
                "date": art_date,
            })

            if max_articles and len(articles) >= max_articles:
                return articles

        total = result.get("Total", 0)
        page_total = result.get("PageTotal", 0)
        logging.debug(
            "PageList type=%s page=%d: got %d items (total=%d, pages=%d)",
            api_type, page_index, len(table), total, page_total,
        )

        if page_index >= page_total:
            break
        page_index += 1

        # Small delay between pages
        time.sleep(0.3)

    return articles


# ---------------------------------------------------------------------------
# Article detail — API-based
# ---------------------------------------------------------------------------

def _fetch_article_detail(art_id: str, api_type: str,
                          client: PlaywrightHttpClient = None):
    """Fetch article content via Article/Detail API.

    Returns (markdown_text, resources_list).
    """
    body = {"id": int(art_id), "type": int(api_type)}
    result = _api_post("/Article/Detail", body, client=client)

    html_content = result.get("CONTENTS") or ""

    resources = []

    # Convert HTML to Markdown
    markdown_text = _html_to_markdown(html_content, art_id)

    return markdown_text, resources


def _html_to_markdown(html: str, art_id: str) -> str:
    """Convert UEditor HTML body to plain Markdown text.

    UEditor generates HTML with inline styles and <img> tags.
    We extract text from block elements and convert images to markdown.
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Strip unwanted tags
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    lines = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                               "li", "blockquote", "pre", "img", "div",
                               "span", "section", "table", "tr", "td", "th"]):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                # Make relative URLs absolute (images hosted on API domain)
                if src.startswith("/"):
                    src = f"https://ggzyfw.fj.gov.cn{src}"
                alt_text = f" ({alt})" if alt else ""
                lines.append(f"![{alt_text}]({src})")
            continue

        # Skip elements that are children of block-level parents already processed
        # (avoid double-text from nested <span> inside <p>)
        if tn in ("span",) and el.find_parent(["p", "h1", "h2", "h3", "li"]):
            continue

        # Only process direct text children to avoid duplication
        texts = []
        for child in el.children:
            if child.name is None:
                t = (child.string or "").strip()
                if t:
                    texts.append(t)

        # For leaf elements, get all text
        if not texts:
            text = el.get_text(strip=True)
        else:
            text = " ".join(texts)

        if not text:
            continue

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

    # Remove leading empty lines
    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y—%m—%d", "%Y年%m月%d日",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Markdown persistence & incremental state
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
    return {"processed_urls": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed URLs)", len(state.get("processed_urls", [])))


def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
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
        logging.info("Document %s uploaded to KB %s", doc["id"], kb_id)
        try:
            DocumentService.begin2parse(doc["id"])
            DocumentService.run(tenant_id, doc, {})
            logging.info("Parsing task queued for document %s", doc["id"])
        except Exception as e:
            logging.error("Failed to queue parsing for document %s: %s", doc["id"], e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _build_article_url(art_id: str) -> str:
    """Build a human-friendly frontend URL for an article."""
    return f"{_FRONTEND_BASE}/index/new/detail?id={art_id}"


def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GGZYFW] Starting Fujian Public Resources Trading crawler")
    _safe_print(f"[GGZYFW] Target URL: {args.target_url}")
    _safe_print(f"[GGZYFW] Task name: {args.task_name}")
    _safe_print(f"[GGZYFW] Target KB: {args.kb_id}")
    _safe_print(f"[GGZYFW] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[GGZYFW] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== GGZYFW Fujian crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        if args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in SECTIONS:
                    selected[label] = SECTIONS[label]
            if not selected:
                _safe_print(f"[GGZYFW] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[GGZYFW] Sections to crawl: {len(selected)}")
        for label, (api_type, display) in selected.items():
            _safe_print(f"         - {display} (type={api_type})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[GGZYFW] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Load state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_keys = set(state.get("processed_urls", []))
        _safe_print(f"[GGZYFW] Already processed: {len(processed_keys)} articles\n")
        sys.stdout.flush()

        # Step 1: Fetch article listings
        _safe_print(f"[GGZYFW] Step 1/4: Fetching article listings (signed API)...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for label, (api_type, display) in selected.items():
            _safe_print(f"[GGZYFW]   Section '{display}' (type={api_type}): fetching...")
            sys.stdout.flush()

            try:
                arts = _fetch_article_list(
                    api_type,
                    max_articles=args.max_articles,
                    max_days=args.max_days,
                    client=client,
                )
            except Exception as e:
                logging.error("Failed to fetch listing for type=%s: %s", api_type, e)
                _safe_print(f"[GGZYFW]   ERROR: {e}")
                sys.stdout.flush()
                continue

            count = len(arts)
            section_stats[display] = count
            for art in arts:
                art["section_label"] = display
                all_articles.append(art)

            _safe_print(f"[GGZYFW]   -> {count} articles")
            sys.stdout.flush()

        _safe_print(f"\n[GGZYFW] Step 1/4: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[GGZYFW] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"[GGZYFW] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Filter already-processed
        if processed_keys:
            new_articles = [
                a for a in all_articles
                if a["id"] not in processed_keys
            ]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[GGZYFW] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[GGZYFW] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # Step 2: Fetch detail pages
        _safe_print(f"\n[GGZYFW] Step 2/4: Fetching {len(all_articles)} article details (signed API)...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            title_preview = art["title"][:70]
            _safe_print(f"[GGZYFW] [{idx}/{total}] [{art['section_label']}] {title_preview}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section_label"], art["title"])

            try:
                content, resources = _fetch_article_detail(art["id"], art["type"], client=client)
            except Exception as e:
                _safe_print(f"[GGZYFW]   -> ERROR: {e}")
                sys.stdout.flush()
                logging.error("Failed to fetch detail for article %s: %s", art["id"], e)
                fail_count += 1
                continue

            if not content:
                _safe_print(f"[GGZYFW]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[GGZYFW]   -> {len(content)} chars")
            sys.stdout.flush()

            article_date_str = art["date"].strftime("%Y-%m-%d") if art.get("date") else art.get("tm", "")
            article_url = _build_article_url(art["id"])

            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section_label']}",
                f"**Date:** {article_date_str}",
                f"**URL:** {article_url}",
                "",
                content,
                "",
                "---",
            ]
            md_parts.append("\n".join(lines))
            success_count += 1

            # Small delay between API calls
            time.sleep(0.2)

        if not md_parts:
            _safe_print(f"[GGZYFW] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[GGZYFW] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # Step 3: Save markdown
        _safe_print(f"[GGZYFW] Step 3/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[GGZYFW] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Update state
        new_ids = [a["id"] for a in all_articles]
        if new_ids:
            processed_keys.update(new_ids)
            _save_state(output_dir, {"processed_urls": list(processed_keys)})

        # Step 4: Upload to KB
        _safe_print(f"[GGZYFW] Step 4/4: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[GGZYFW] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[GGZYFW] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
