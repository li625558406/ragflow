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
Dedicated web crawler for easy-prt.com (工采通电子招投标交易平台).

This site is a Vue.js SPA with SM4-encrypted API communication.  Without
login most project/bidding endpoints are inaccessible;  only the CMS
policy/regulation section is public (27 articles, no file attachments).

Usage (typically spawned by task_executor):
    python easy_prt_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://easy-prt.com/ \
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

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient

try:
    from gmssl.sm4 import CryptSM4, SM4_ENCRYPT, SM4_DECRYPT
except ImportError:
    logging.error("gmssl package is required. Run: uv add gmssl or pip install gmssl")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Site configuration
# ---------------------------------------------------------------------------
_API_BASE = "https://easy-prt.com/gykjbid"
_SM4_KEY = "90bdd291004611ef87fc52540023e781"

# ---- CMS policy/regulation articles ----
_CMS_LIST_URL = "/jeecg-system/cms/content/portal/list"
_CMS_DETAIL_URL = "/jeecg-system/cms/content/portal/detail"

# ---- Site / venue listing ----
_SITE_LIST_URL = "/gykj-bid/bid/portal/sitePageList"

# ---- Platform config ----
_PLATFORM_CONFIG_URL = "/jeecg-system/sys/platform/getPortalConfigByPlatformId"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Platform-Id": "gct",
}


# ---------------------------------------------------------------------------
# SM4 helpers
# ---------------------------------------------------------------------------

def _sm4_encrypt(data):
    cipher = CryptSM4()
    cipher.set_key(bytes.fromhex(_SM4_KEY), SM4_ENCRYPT)
    return cipher.crypt_ecb(json.dumps(data, separators=(",", ":")).encode("utf-8")).hex()


def _sm4_decrypt(hex_data):
    cipher = CryptSM4()
    cipher.set_key(bytes.fromhex(_SM4_KEY), SM4_DECRYPT)
    decrypted = cipher.crypt_ecb(bytes.fromhex(hex_data)).decode("utf-8")
    return json.loads(decrypted)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_post(path, params, retries=3, client=None):
    """POST a JSON body encrypted with SM4, decrypt and return the response."""
    url = _API_BASE + path
    headers = {
        "Content-Type": "application/json",
        "X-Platform-Id": "gct",
        "User-Agent": _HEADERS["User-Agent"],
    }
    encrypted = _sm4_encrypt(params)
    for attempt in range(1, retries + 1):
        try:
            if client:
                resp = client.post(url, data=encrypted, headers=headers, timeout=30)
            else:
                resp = requests.post(url, data=encrypted, headers=headers, timeout=30,
                                     proxies={"http": "", "https": ""})
            if resp.status_code == 200:
                return _sm4_decrypt(resp.text.strip())
            logging.warning("POST %s returned HTTP %s (attempt %d/%d)",
                           path, resp.status_code, attempt, retries)
            import time
            time.sleep(2 ** attempt)
        except json.JSONDecodeError as e:
            logging.error("POST %s decrypt failed: %s", path, e)
            return None
        except Exception as e:
            logging.warning("POST %s failed: %s (attempt %d/%d)",
                           path, e, attempt, retries)
            import time
            time.sleep(2 ** attempt)
    return None


def _api_get(path, params, retries=3, client=None):
    """GET with encrypted params as query string, decrypt response."""
    url = _API_BASE + path
    headers = {
        "X-Platform-Id": "gct",
        "User-Agent": _HEADERS["User-Agent"],
    }
    encrypted = _sm4_encrypt(params)
    full_url = f"{url}?encryptParams={encrypted}"
    for attempt in range(1, retries + 1):
        try:
            if client:
                resp = client.fetch_get(full_url, headers=headers, timeout=30)
            else:
                resp = requests.get(url, params={"encryptParams": encrypted},
                                    headers=headers, timeout=30,
                                    proxies={"http": "", "https": ""})
            if resp.status_code == 200:
                return _sm4_decrypt(resp.text.strip())
            logging.warning("GET %s returned HTTP %s (attempt %d/%d)",
                           path, resp.status_code, attempt, retries)
            import time
            time.sleep(2 ** attempt)
        except json.JSONDecodeError as e:
            logging.error("GET %s decrypt failed: %s", path, e)
            return None
        except Exception as e:
            logging.warning("GET %s failed: %s (attempt %d/%d)",
                           path, e, attempt, retries)
            import time
            time.sleep(2 ** attempt)
    return None


def _download_file(url, timeout=120, client=None):
    """Download a binary file."""
    try:
        if client:
            resp = client.get(url, timeout=timeout)
            return resp.text.encode("utf-8"), resp.headers.get("Content-Type", "") if hasattr(resp, 'headers') else ""
        else:
            resp = requests.get(url, timeout=timeout, proxies={"http": "", "https": ""})
            return resp.content, resp.headers.get("Content-Type", "")
    except Exception as e:
        logging.error("Failed to download %s: %s", url, e)
        return None, None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try common date formats; return datetime or None."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y年%m月%d日"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# CMS content
# ---------------------------------------------------------------------------

def _fetch_cms_articles(max_days=30, client=None):
    """Fetch all CMS policy/regulation articles within max_days."""
    cutoff = datetime.now() - timedelta(days=max_days)
    result = _api_post(_CMS_LIST_URL, {"pageNo": 1, "pageSize": 50}, client=client)
    if not result:
        logging.error("CMS list API returned no result")
        _safe_print("[EASYPRT]   ERROR: CMS list API returned no result")
        return []
    if not result.get("result"):
        logging.warning("CMS list API result is empty: %s", json.dumps(result, ensure_ascii=False)[:200])
        _safe_print(f"[EASYPRT]   ERROR: CMS list API error: code={result.get('code')} msg={result.get('message','')}")
        return []

    records = result["result"].get("records", [])
    articles = []
    for art in records:
        date_str = (art.get("releaseTime") or "")[:10]
        dt = _parse_date(date_str) if date_str else None
        if dt and dt < cutoff:
            continue
        articles.append({
            "id": art["id"],
            "title": art.get("title", "").strip(),
            "date_str": date_str,
            "date": dt,
        })
    return articles


def _fetch_article_detail(article_id, client=None):
    """Fetch article detail; return (html_content, metadata_dict)."""
    result = _api_get(_CMS_DETAIL_URL, {"id": article_id}, client=client)
    if not result or not result.get("result"):
        return "", {}
    art = result["result"]
    return art.get("content", "") or "", {
        "title": art.get("title", ""),
        "releaseTime": art.get("releaseTime", ""),
    }


# ---------------------------------------------------------------------------
# Site / venue listing
# ---------------------------------------------------------------------------

def _fetch_venues(client=None):
    """Fetch the bidding venue list."""
    result = _api_get(_SITE_LIST_URL, {"pageNo": 1, "pageSize": 50}, client=client)
    if not result or not result.get("result"):
        return []
    return result["result"].get("records", [])


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def _content_to_markdown(html):
    """Convert HTML content to plain markdown text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    _BLOCK_TAGS = {
        "p", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "blockquote", "pre", "div", "section",
    }
    _INLINE_TAGS = {"span", "strong", "b", "em", "i", "u", "a", "font"}

    lines = []
    seen = set()
    for el in soup.find_all(list(_BLOCK_TAGS | _INLINE_TAGS)):
        tn = el.name
        # Skip if inside a table (handled separately)
        if el.find_parent("table"):
            continue

        text = el.get_text(strip=True)
        if not text or text in seen:
            continue
        seen.add(text)

        if tn in ("h1",):
            lines.append(f"\n# {text}\n")
        elif tn in ("h2",):
            lines.append(f"\n## {text}\n")
        elif tn in ("h3",):
            lines.append(f"\n### {text}\n")
        elif tn in ("h4", "h5", "h6"):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        elif tn == "p":
            lines.append(text)
        elif tn in ("div", "section"):
            lines.append(text)
        elif tn in _INLINE_TAGS:
            lines.append(text)

    # Extract tables
    for table in soup.find_all("table"):
        md_table = _table_to_markdown(table)
        if md_table:
            lines.append("\n" + md_table + "\n")

    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n\n".join(lines)


def _table_to_markdown(table):
    """Convert an HTML table to a markdown table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for tag in ("th", "td"):
            for cell in tr.find_all(tag):
                cells.append(cell.get_text(strip=True))
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    # Ensure all rows have same column count
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * col_count) + " |"
    body_lines = [header, sep]
    for row in rows[1:]:
        body_lines.append("| " + " | ".join(row) + " |")
    return "\n".join(body_lines)


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
    """Save markdown under output_dir/articles/."""
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _save_download(content, output_dir, filename):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    """Upload a file to KB and queue parsing."""
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
        doc_id = doc["id"]
        logging.info("Document %s uploaded to KB %s", doc_id, kb_id)
        try:
            DocumentService.update_by_id(doc_id, {"parser_id": parser_id})
        except Exception as e:
            logging.error("Failed to update parser_id for %s: %s", doc_id, e)
        try:
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="easy-prt.com crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://easy-prt.com/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated section labels (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None,
                        help="Access token (not needed for public content)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=30,
                        help="Max age in days for articles (default: 30)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[EASYPRT] Starting 工采通电子招投标交易平台 crawler")
    _safe_print(f"[EASYPRT] Target URL: {args.target_url}")
    _safe_print(f"[EASYPRT] Task name: {args.task_name}")
    _safe_print(f"[EASYPRT] Target KB: {args.kb_id}")
    _safe_print(f"[EASYPRT] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[EASYPRT] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== EASYPRT crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[EASYPRT] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[EASYPRT] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 1: Fetch CMS policy articles
        # -----------------------------------------------------------------------
        _safe_print("[EASYPRT] Step 1/4: Fetching CMS policy articles...")
        sys.stdout.flush()

        articles = _fetch_cms_articles(max_days=args.max_days, client=client)
        if args.max_articles:
            articles = articles[:args.max_articles]

        # Filter already-processed
        if processed_ids:
            new_articles = [a for a in articles if a["id"] not in processed_ids]
            skipped = len(articles) - len(new_articles)
            if skipped:
                _safe_print(f"[EASYPRT]   Skipping {skipped} already-processed article(s)")
                sys.stdout.flush()
            articles = new_articles

        _safe_print(f"[EASYPRT]   Found {len(articles)} article(s) in date range\n")
        sys.stdout.flush()

        if not articles:
            _safe_print("[EASYPRT] No new articles to process, checking other content...")
            sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 2: Fetch article details + convert to markdown
        # -----------------------------------------------------------------------
        article_data = []
        if articles:
            _safe_print(f"[EASYPRT] Step 2/4: Fetching {len(articles)} article detail(s)...\n")
            sys.stdout.flush()

            for idx, art in enumerate(articles, 1):
                _safe_print(f"[EASYPRT]   [{idx}/{len(articles)}] {art['title'][:60]}")
                sys.stdout.flush()

                content, metadata = _fetch_article_detail(art["id"], client=client)
                if not content:
                    _safe_print("      -> No content, skipping")
                    sys.stdout.flush()
                    continue

                md = _content_to_markdown(content)
                _safe_print(f"      -> {len(md)} chars")
                sys.stdout.flush()

                article_data.append({
                    "id": art["id"],
                    "title": art["title"],
                    "date_str": art["date_str"],
                    "markdown": md,
                    "metadata": metadata,
                })
                time.sleep(0.3)

            _safe_print("")
            sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 3: Fetch venue listing (supplementary content)
        # -----------------------------------------------------------------------
        _safe_print("[EASYPRT] Step 3/4: Fetching venue/site listing...")
        sys.stdout.flush()

        venues = _fetch_venues(client=client)
        _safe_print(f"[EASYPRT]   Found {len(venues)} venue(s)\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 4: Save & upload
        # -----------------------------------------------------------------------
        _safe_print("[EASYPRT] Step 4/4: Saving and uploading...\n")
        sys.stdout.flush()

        md_parts = []

        # Save article markdown
        for art in article_data:
            md = (
                f"# {art['title']}\n"
                f"**Date:** {art['date_str']}\n"
                f"**Source:** {args.target_url}\n"
                f"**ID:** {art['id']}\n\n"
                f"{art['markdown']}\n"
            )
            _save_markdown(md, output_dir, art["id"])
            md_parts.append(md)

        # Save venue markdown
        if venues:
            venue_lines = ["# 场地列表\n", f"**更新日期:** {datetime.now().strftime('%Y-%m-%d')}\n"]
            for v in venues:
                name = v.get("siteName", "")
                addr = v.get("address", "")
                open_rooms = v.get("openRoomCount", 0)
                eval_rooms = v.get("evalRoomCount", 0)
                venue_lines.append(f"- **{name}** | 地址: {addr} | 开标室: {open_rooms} | 评标室: {eval_rooms}")
            venue_md = "\n".join(venue_lines)
            venue_path = os.path.join(output_dir, "venues.md")
            with open(venue_path, "w", encoding="utf-8") as f:
                f.write(venue_md)
            md_parts.append(venue_md)
            _safe_print(f"[EASYPRT]   Venues saved: {venue_path}")
            sys.stdout.flush()

        # Save combined markdown
        if md_parts:
            combined_path = os.path.join(output_dir, "articles_combined.md")
            with open(combined_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))
            _safe_print(f"[EASYPRT]   Combined markdown: {combined_path}")
            sys.stdout.flush()

            # Update state
            new_ids = [art["id"] for art in article_data]
            if new_ids:
                processed_ids.update(new_ids)
                _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # Upload to KB
        if md_parts and args.kb_id:
            _safe_print(f"[EASYPRT]   Uploading combined markdown to KB {args.kb_id}...")
            sys.stdout.flush()
            try:
                _upload_to_kb(combined_path, args.kb_id, args.tenant_id, parser_id="naive")
                _safe_print(" done!")
                sys.stdout.flush()
            except Exception as e:
                _safe_print(f" failed: {e}")
                sys.stdout.flush()
                logging.error("Markdown upload failed: %s", e)

        _safe_print(f"\n[EASYPRT] {'='*60}")
        _safe_print(f"[EASYPRT] Crawl finished: {len(article_data)} articles, {len(venues)} venues")
        _safe_print(f"[EASYPRT] {'='*60}\n")
        sys.stdout.flush()
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "easy_prt_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
