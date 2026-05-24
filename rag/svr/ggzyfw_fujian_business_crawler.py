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
Dedicated web crawler for ggzyfw.fujian.gov.cn/business/list
(福建省公共资源交易电子公共服务平台 - 交易信息).

This is a Vue.js SPA with a signed (MD5 + app secret) and AES-256-CBC encrypted API.
Listing data fetched via POST /FwPortalApi/Trade/TradeInfo (paginated).
Detail pages are rendered via Playwright at /business/detail?cid={M_ID}&type={KIND}
because the detail API requires additional parameters not available from the listing.

GGTYPE codes: 1=招标公告, 2=变更公告, 3=资格预审公告, 4=中标候选人公示, 5=中标结果公告

Usage (typically spawned by task_executor):
    python ggzyfw_fujian_business_crawler.py \
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
# API crypto constants (same as ggzyfw_fujian_crawler — shared platform)
# ---------------------------------------------------------------------------
APP_SECRET = "B3978D054A72A7002063637CCDF6B2E5"
AES_KEY = "EB444973714E4A40876CE66BE45D5930"  # 32 bytes → AES-256
AES_IV = "B5A8904209931867"                   # 16 bytes

# ---------------------------------------------------------------------------
# API base & frontend
# ---------------------------------------------------------------------------
_API_BASE = "https://ggzyfw.fj.gov.cn/FwPortalApi"
_FRONTEND_BASE = "https://ggzyfw.fujian.gov.cn"

# ---------------------------------------------------------------------------
# GGTYPE display names
# ---------------------------------------------------------------------------
GGTYPE_MAP = {
    "1": "招标公告",
    "2": "变更公告",
    "3": "资格预审公告",
    "4": "中标候选人公示",
    "5": "中标结果公告",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fujian GGZYFW business/trade info crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://ggzyfw.fujian.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=90,
                        help="Max age in days for articles to crawl (default: 90)")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="Max pages to crawl (default: 5)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _init():
    settings.init_settings()
    logging.info("Project settings initialised")


# ---------------------------------------------------------------------------
# API helpers — signing & AES decryption (shared with ggzyfw_fujian_crawler)
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
# Listing — TradeInfo pagination
# ---------------------------------------------------------------------------

def _fetch_trade_list(max_articles: int = 0, max_days: int = 90,
                      max_pages: int = 5):
    """Fetch trade info list via TradeInfo API with pagination.

    Uses requests.post (not Playwright) — the signed API doesn't need a browser.
    Returns list[dict] with keys: m_id, title, kind, ggtype, name, tm, date.
    Filters out articles older than max_days.
    """
    articles = []
    seen_mids = set()  # same M_ID can appear with different GGTYPE
    page_index = 1
    page_size = 20
    cutoff = datetime.now() - timedelta(days=max_days)

    while True:
        body = {"pageSize": page_size, "pageIndex": page_index}
        result = _api_post("/Trade/TradeInfo", body)

        table = result.get("Table") or []
        for row in table:
            m_id = str(row.get("M_ID", ""))
            title = (row.get("TITLE") or "").strip()
            kind = (row.get("KIND") or "").strip()
            ggtype = str(row.get("GGTYPE", ""))
            name = (row.get("NAME") or "").strip()
            tm_str = (row.get("TM") or "").strip()

            if not m_id or not title:
                continue

            # Deduplicate: same M_ID may appear with different GGTYPE
            unique_key = f"{m_id}_{ggtype}"
            if unique_key in seen_mids:
                continue
            seen_mids.add(unique_key)

            # Parse date for filtering
            art_date = _parse_date(tm_str)
            if art_date and art_date < cutoff:
                logging.debug(
                    "Hit date cutoff at article %s (%s), stopping pagination",
                    m_id, tm_str,
                )
                return articles

            articles.append({
                "m_id": m_id,
                "title": title,
                "kind": kind,
                "ggtype": ggtype,
                "name": name,
                "tm": tm_str,
                "date": art_date,
                "ggtype_label": GGTYPE_MAP.get(ggtype, ggtype),
            })

            if max_articles and len(articles) >= max_articles:
                return articles

        total = result.get("Total", 0)
        page_total = result.get("PageTotal", 0)
        logging.debug(
            "TradeInfo page=%d: got %d items (total=%d, pages=%d)",
            page_index, len(table), total, page_total,
        )

        if page_index >= page_total:
            break
        if max_pages and page_index >= max_pages:
            logging.info("Reached max_pages=%d limit, stopping pagination", max_pages)
            break
        page_index += 1

        # Small delay between pages
        time.sleep(0.3)

    return articles


# ---------------------------------------------------------------------------
# Detail page — Playwright-rendered
# ---------------------------------------------------------------------------

def _fetch_detail_html(m_id: str, kind: str,
                       client: PlaywrightHttpClient) -> str:
    """Render the detail page via Playwright and return the HTML."""
    url = f"{_FRONTEND_BASE}/business/detail?cid={m_id}&type={kind}"
    resp = client.get(url)
    return resp.text


def _html_to_markdown(html: str, m_id: str) -> str:
    """Convert the rendered detail page HTML to Markdown."""
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Strip unwanted tags
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Try to find the main content area
    content_el = (
        soup.find("div", class_=re.compile(r"detail|content|article", re.I))
        or soup.find("div", class_="el-main")
        or soup.find("main")
        or soup.find("body")
    )
    if content_el is None:
        content_el = soup

    lines = []
    for el in content_el.find_all(
        ["p", "h1", "h2", "h3", "h4", "h5", "h6",
         "li", "blockquote", "pre", "img", "div",
         "span", "section", "table", "tr", "td", "th"]
    ):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                if src.startswith("/"):
                    src = f"https://ggzyfw.fj.gov.cn{src}"
                alt_text = f" ({alt})" if alt else ""
                lines.append(f"![{alt_text}]({src})")
            continue

        if tn in ("span",) and el.find_parent(["p", "h1", "h2", "h3", "li"]):
            continue

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
    logging.info("Crawler state saved (%d processed URLs)",
                 len(state.get("processed_urls", [])))


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


def _build_detail_url(m_id: str, kind: str) -> str:
    """Build a human-friendly frontend URL for a trade detail page."""
    return f"{_FRONTEND_BASE}/business/detail?cid={m_id}&type={kind}"


def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GGZYFW-BIZ] Starting Fujian Trade/Business crawler")
    _safe_print(f"[GGZYFW-BIZ] Target URL: {args.target_url}")
    _safe_print(f"[GGZYFW-BIZ] Task name: {args.task_name}")
    _safe_print(f"[GGZYFW-BIZ] Target KB: {args.kb_id}")
    _safe_print(f"[GGZYFW-BIZ] Max days: {args.max_days}")
    _safe_print(f"[GGZYFW-BIZ] Max pages: {args.max_pages}")
    if args.max_articles:
        _safe_print(f"[GGZYFW-BIZ] Max articles: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== GGZYFW Fujian Business crawler started for %s ===",
                 args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[GGZYFW-BIZ] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Load state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_keys = set(state.get("processed_urls", []))
        _safe_print(f"[GGZYFW-BIZ] Already processed: {len(processed_keys)} articles\n")
        sys.stdout.flush()

        # Step 1: Fetch trade listings
        _safe_print(f"[GGZYFW-BIZ] Step 1/4: Fetching trade listings (signed API)...\n")
        sys.stdout.flush()

        try:
            articles = _fetch_trade_list(
                max_articles=args.max_articles,
                max_days=args.max_days,
                max_pages=args.max_pages,
            )
        except Exception as e:
            logging.error("Failed to fetch trade listings: %s", e)
            _safe_print(f"[GGZYFW-BIZ] ERROR: {e}")
            sys.stdout.flush()
            sys.exit(1)

        _safe_print(f"[GGZYFW-BIZ] Step 1/4: Collected {len(articles)} trade items\n")
        sys.stdout.flush()

        if not articles:
            _safe_print(f"[GGZYFW-BIZ] No trade items found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        # Breakdown by GGTYPE
        ggtype_counts = {}
        for art in articles:
            label = art["ggtype_label"]
            ggtype_counts[label] = ggtype_counts.get(label, 0) + 1
        _safe_print(f"[GGZYFW-BIZ] Breakdown by type:")
        for label, count in sorted(ggtype_counts.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Filter already-processed
        if processed_keys:
            new_articles = [
                a for a in articles
                if f"{a['m_id']}_{a['ggtype']}" not in processed_keys
            ]
            skipped = len(articles) - len(new_articles)
            _safe_print(f"\n[GGZYFW-BIZ] Skipping {skipped} already-processed item(s)")
            sys.stdout.flush()
            articles = new_articles

        if not articles:
            _safe_print(f"[GGZYFW-BIZ] All items already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # Step 2: Fetch detail pages via Playwright
        _safe_print(f"\n[GGZYFW-BIZ] Step 2/4: Fetching {len(articles)} detail pages (Playwright)...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(articles)

        for idx, art in enumerate(articles, 1):
            title_preview = art["title"][:70]
            _safe_print(
                f"[GGZYFW-BIZ] [{idx}/{total}] [{art['ggtype_label']}] {title_preview}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total,
                         art["ggtype_label"], art["title"])

            try:
                html = _fetch_detail_html(art["m_id"], art["kind"], client)
                content = _html_to_markdown(html, art["m_id"])
            except Exception as e:
                _safe_print(f"[GGZYFW-BIZ]   -> ERROR: {e}")
                sys.stdout.flush()
                logging.error("Failed to fetch detail for %s: %s", art["m_id"], e)
                fail_count += 1
                continue

            if not content:
                _safe_print(f"[GGZYFW-BIZ]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[GGZYFW-BIZ]   -> {len(content)} chars")
            sys.stdout.flush()

            article_date_str = (
                art["date"].strftime("%Y-%m-%d")
                if art.get("date") else art.get("tm", "")
            )
            article_url = _build_detail_url(art["m_id"], art["kind"])

            lines = [
                f"# {art['title']}",
                f"**类型:** {art['ggtype_label']}",
                f"**项目名称:** {art.get('name', '')}",
                f"**日期:** {article_date_str}",
                f"**URL:** {article_url}",
                "",
                content,
                "",
                "---",
            ]
            md_parts.append("\n".join(lines))
            success_count += 1

            # Delay between page loads
            time.sleep(0.3)

        if not md_parts:
            _safe_print(f"[GGZYFW-BIZ] No items processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(
            f"\n[GGZYFW-BIZ] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # Step 3: Save markdown
        _safe_print(f"[GGZYFW-BIZ] Step 3/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[GGZYFW-BIZ] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Update state
        new_keys = [f"{a['m_id']}_{a['ggtype']}" for a in articles]
        if new_keys:
            processed_keys.update(new_keys)
            _save_state(output_dir, {"processed_urls": list(processed_keys)})

        # Step 4: Upload to KB
        _safe_print(f"[GGZYFW-BIZ] Step 4/4: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[GGZYFW-BIZ] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[GGZYFW-BIZ] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_fujian_business_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
