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
Dedicated web crawler for ygcg.fjcqjy.com (福建省国资采购平台).

This site is a Vue.js SPA backed by a .NET ASHX handler at
www.enjoy5191.com.  Procurement data is served via 4 JSON APIs
that return list metadata (no full-text content).  Detail pages
require a browser — this crawler collects the publicly accessible
list metadata with direct detail-page links.

Usage (typically spawned by task_executor):
    python ygcg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://ygcg.fjcqjy.com/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient

# ---------------------------------------------------------------------------
# Site configuration
# ---------------------------------------------------------------------------
_API_BASE = "https://www.enjoy5191.com/api/GetDataHandler.ashx"

# The SPA's axios interceptor automatically adds SITES_CONFIG.SITES (value: 4)
# to every request.  Without it the API returns "res": "0".
_SITES = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# The 4 procurement API types exposed on the homepage
_TRADE_TYPES = [
    {
        "name": "工程公告",
        "method": "Web.GZCG_GetJiaoYiList",
        "in_BIG_TYPE": "A",
        "id_field": "SOURCE_ID",
    },
    {
        "name": "采购公告",
        "method": "Web.GZCG_GetJiaoYiList",
        "in_BIG_TYPE": "D,Q",
        "id_field": "SOURCE_ID",
    },
    {
        "name": "采购补充通知",
        "method": "Web.GZCG_GetSUPPLYNOTICE",
        "in_BIG_TYPE": "D,Q",
        "id_field": "UNQ_ID",
    },
    {
        "name": "采购结果",
        "method": "Web.GZCG_GetBIDDERPUBLIC",
        "in_BIG_TYPE": "D,Q",
        "id_field": "UNQ_ID",
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch_list(method, in_big_type, page=1, pagesize=100, retries=3, client=None):
    """GET list data from the ASHX handler."""
    params = {
        "method": method,
        "in_BIG_TYPE": in_big_type,
        "pageindex": page,
        "pagesize": pagesize,
        "SITES": _SITES,
    }
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_API_BASE}?{param_str}"
    for attempt in range(1, retries + 1):
        try:
            if client:
                resp = client.get(url, headers=_HEADERS, timeout=60)
            else:
                resp = requests.get(
                    _API_BASE, params=params, headers=_HEADERS,
                    timeout=60, proxies={"http": "", "https": ""},
                )
            result = resp.json()
            if result.get("res") == "1":
                return result
            logging.warning("%s/%s page %d -> res=%s (attempt %d/%d)",
                            method, in_big_type, page,
                            result.get("res"), attempt, retries)
        except Exception as e:
            logging.warning("%s/%s page %d failed: %s (attempt %d/%d)",
                            method, in_big_type, page, e, attempt, retries)
        time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try common date formats; return datetime or None."""
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


# ---------------------------------------------------------------------------
# List fetching
# ---------------------------------------------------------------------------

def _fetch_trade_list(trade_type, max_days=30, max_items=0, client=None):
    """Fetch articles for one trade type, filtered by age.

    Args:
        trade_type: Trade type configuration dict.
        max_days: Max article age in days.
        max_items: Max items to return (0 = unlimited).
        client: Optional PlaywrightHttpClient instance.
    """
    cutoff = datetime.now() - timedelta(days=max_days)
    items = []
    page = 1
    pagesize = 100

    while True:
        result = _fetch_list(trade_type["method"], trade_type["in_BIG_TYPE"],
                             page=page, pagesize=pagesize, client=client)
        if not result:
            break
        rows = result.get("data", [])
        if not rows:
            break

        for item in rows:
            date_str = (item.get("PUBLISHED_TIME") or "")[:10]
            dt = _parse_date(date_str)
            if dt and dt < cutoff:
                # Items sorted desc by date; once past cutoff, skip the rest
                # of this page but continue to next pages in case of mixed dates
                continue

            item_id = item.get(trade_type["id_field"], "")
            if not item_id:
                continue

            items.append({
                "id": item_id,
                "title": item.get("NAME", "").strip(),
                "date_str": date_str,
                "date": dt,
                "category": trade_type["name"],
                "type_text": item.get("TYPE_TEXT", ""),
                "big_type_text": item.get("BIG_TYPE_TEXT", ""),
                "areaname": item.get("AREANAME", ""),
                "sen": item.get("SEN", ""),
                "tenderer": item.get("TENDERER_NAME", ""),
                "unit_name": item.get("UNIT_NAME", ""),
                "tender_way": item.get("TENDER_PROJECT_WAY", ""),
                "status_txt": item.get("STATUS_TXT", ""),
                "open_time": item.get("OPEN_TIME", ""),
                "url": item.get("URL", ""),
            })

            if max_items > 0 and len(items) >= max_items:
                return items[:max_items]

        page += 1
        time.sleep(0.3)

        if len(rows) < pagesize:
            break

    return items



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


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="naive"):
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
    parser = argparse.ArgumentParser(
        description="ygcg.fjcqjy.com (福建省国资采购平台) crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://ygcg.fjcqjy.com/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated trade types: 工程公告,采购公告,采购补充通知,采购结果")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to process (0 = unlimited)")
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
    _safe_print("[YGCG] Starting 福建省国资采购平台 crawler")
    _safe_print(f"[YGCG] Target URL: {args.target_url}")
    _safe_print(f"[YGCG] Task name: {args.task_name}")
    _safe_print(f"[YGCG] Target KB: {args.kb_id}")
    _safe_print(f"[YGCG] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[YGCG] Max articles: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== YGCG crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[YGCG] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[YGCG] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # Determine trade types to crawl
        if args.section:
            selected = [s.strip() for s in args.section.split(",")]
            trade_types = [tt for tt in _TRADE_TYPES if tt["name"] in selected]
        else:
            trade_types = _TRADE_TYPES

        _safe_print(f"[YGCG] Trade types to crawl: {len(trade_types)}")
        for tt in trade_types:
            _safe_print(f"       - {tt['name']} ({tt['method']}, {tt['in_BIG_TYPE']})")
        _safe_print("")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 1: Fetch articles from all trade types
        # -----------------------------------------------------------------------
        _safe_print("[YGCG] Step 1/3: Fetching article lists...")
        sys.stdout.flush()

        all_articles = []
        remaining = args.max_articles or 0
        for tt in trade_types:
            _safe_print(f"  [{tt['name']}] Fetching...")
            sys.stdout.flush()
            articles = _fetch_trade_list(tt, max_days=args.max_days,
                                         max_items=remaining, client=client)
            _safe_print(f"  [{tt['name']}] {len(articles)} article(s)")
            sys.stdout.flush()
            all_articles.extend(articles)
            if remaining:
                remaining -= len(articles)
                if remaining <= 0:
                    break

        # Sort by date descending
        all_articles.sort(key=lambda a: a.get("date_str", ""), reverse=True)

        # Apply max_articles limit
        if args.max_articles and len(all_articles) > args.max_articles:
            all_articles = all_articles[:args.max_articles]

        # Deduplicate by ID (same article can appear in multiple trade types)
        seen_ids = set()
        deduped = []
        for a in all_articles:
            if a["id"] not in seen_ids:
                seen_ids.add(a["id"])
                deduped.append(a)
        if len(deduped) < len(all_articles):
            all_articles = deduped

        # Filter already processed
        if processed_ids:
            new_articles = [a for a in all_articles if a["id"] not in processed_ids]
            skipped = len(all_articles) - len(new_articles)
            if skipped:
                _safe_print(f"\n[YGCG]   Skipping {skipped} already-processed article(s)")
                sys.stdout.flush()
            all_articles = new_articles

        _safe_print(f"\n[YGCG] Total to process: {len(all_articles)} new article(s)\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[YGCG] No new articles to process.\n")
            sys.stdout.flush()
        else:
            # -------------------------------------------------------------------
            # Step 2: Save individual articles
            # -------------------------------------------------------------------
            _safe_print(f"[YGCG] Step 2/3: Saving {len(all_articles)} article(s)...\n")
            sys.stdout.flush()

            md_parts = []
            for idx, art in enumerate(all_articles, 1):
                _safe_print(f"[YGCG]   [{idx}/{len(all_articles)}] {art['title'][:60]}")
                sys.stdout.flush()

                md = (
                    f"# {art['title']}\n"
                    f"**Date:** {art['date_str']}\n"
                    f"**Category:** {art['category']}\n"
                    f"**Type:** {art['type_text']}\n"
                    f"**Region:** {art['areaname']}\n"
                    f"**Tenderer:** {art['tenderer']}\n"
                    f"**Agent:** {art['unit_name']}\n"
                    f"**Bidding Method:** {art['tender_way']}\n"
                    f"**Status:** {art['status_txt']}\n"
                    f"**Open Time:** {art['open_time']}\n"
                    f"**Source:** {art['url']}\n\n"
                    f"详细内容请访问原文链接。\n"
                )

                _save_markdown(md, output_dir, art["id"])
                md_parts.append(md)
                sys.stdout.flush()

            _safe_print("")
            sys.stdout.flush()

            # -------------------------------------------------------------------
            # Step 3: Save combined & upload
            # -------------------------------------------------------------------
            _safe_print("[YGCG] Step 3/3: Saving combined file and uploading...\n")
            sys.stdout.flush()

            if md_parts:
                combined_path = os.path.join(output_dir, "articles_combined.md")
                with open(combined_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_parts))
                _safe_print(f"[YGCG]   Combined markdown: {combined_path}")
                sys.stdout.flush()

                # Update state
                new_ids = [art["id"] for art in all_articles]
                if new_ids:
                    processed_ids.update(new_ids)
                    _save_state(output_dir, {"processed_ids": list(processed_ids)})

                # Upload to KB
                if args.kb_id:
                    _safe_print(f"[YGCG]   Uploading to KB {args.kb_id}...")
                    sys.stdout.flush()
                    try:
                        _upload_to_kb(combined_path, args.kb_id, args.tenant_id, parser_id="naive")
                        _safe_print(" done!")
                        sys.stdout.flush()
                    except Exception as e:
                        _safe_print(f" failed: {e}")
                        sys.stdout.flush()
                        logging.error("Markdown upload failed: %s", e)

        _safe_print(f"\n[YGCG] {'='*60}")
        _safe_print(f"[YGCG] Crawl finished: {len(all_articles)} new articles")
        _safe_print(f"[YGCG] {'='*60}\n")
        sys.stdout.flush()
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "ygcg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
