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
Crawler for bulletin.cebpubservice.com (中国招标投标公共服务平台) — listing only.

Site characteristics
────────────────────
  • Listing page is SSR-rendered at /xxfbcmses/search/bulletin.html.
    Plain HTTP requests work — no JavaScript or Playwright needed.
  • Detail pages are on ctbpsp.com (different domain) and are protected by
    Alibaba Cloud WAF with slider CAPTCHA + NetEase Dun + VAPTCHA.
    Detail pages embed content as PDF via pdfjs-dist viewer.
    This crawler does NOT fetch detail pages — it saves listing data only.

Filter
──────
  Only bulletins published TODAY (searchDate = current date, dates = 1).

Checkpoint/resume
─────────────────
  Bulletins are processed in batches of 10.  After each batch markdown is
  uploaded to KB and state is saved.  The 3600s task timeout is handled
  via --max-runtime (default 3300s).

Usage
─────
    python cebpubservice_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://bulletin.cebpubservice.com/ \
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
from datetime import datetime

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_LISTING_URL = (
    "https://bulletin.cebpubservice.com/xxfbcmses/search/bulletin.html"
)
_DETAIL_URL_TEMPLATE = (
    "https://ctbpsp.com/#/bulletinDetail?uuid={id}&inpvalue=&dataSource=0&tenderAgency="
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_BATCH_SIZE = 10

# Anti-crawling: gentle delay (SSR endpoint is not CAPTCHA-protected)
_REQUEST_DELAY_MIN = 0.5
_REQUEST_DELAY_MAX = 1.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    import random
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


# ---------------------------------------------------------------------------
# Listing page: SSR HTML parser
# ---------------------------------------------------------------------------

def _fetch_listing_page(page, search_date):
    """Fetch one page of bulletin listing. Returns (items, has_more)."""
    params = {
        "searchDate": search_date,
        "dates": "1",
        "categoryId": "88",
        "page": str(page),
        "showStatus": "1",
    }
    headers = {"User-Agent": _USER_AGENT}

    try:
        resp = requests.get(_LISTING_URL, params=params, headers=headers,
                          timeout=30, proxies={"http": "", "https": ""})
        resp.raise_for_status()
    except Exception as e:
        logging.error("Listing page %d failed: %s", page, e)
        return [], False

    html = resp.text

    # Parse rows
    soup = BeautifulSoup(html, "html.parser")
    table_rows = soup.select("table tr")
    data_rows = [tr for tr in table_rows if tr.find("a", href=True)]

    if not data_rows:
        return [], False

    items = []
    for tr in data_rows:
        try:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue

            a_tag = tr.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            id_match = re.search(r"urlOpen\('([a-f0-9]{32})'\)", href)
            if not id_match:
                continue
            bulletin_id = id_match.group(1)
            title = a_tag.get("title") or a_tag.get_text(strip=True)

            # Industry & region from span titles
            spans = tr.find_all("span", title=True)
            industry = spans[0].get("title", "") if len(spans) > 0 else ""
            region = spans[1].get("title", "") if len(spans) > 1 else ""

            # Source channel (4th td)
            source = tds[3].get_text(strip=True) if len(tds) >= 4 else ""

            # Publish datetime from first td id attribute
            publish_datetime = tds[0].get("id", "") or ""
            publish_date = publish_datetime[:10] if publish_datetime else ""

            # Publish date display (5th td)
            date_display = tds[4].get_text(strip=True) if len(tds) >= 5 else ""

            # Bid opening time from last td id attribute
            bid_opening = ""
            if len(tds) >= 6:
                bid_opening = tds[5].get("id", "") or tds[5].get_text(strip=True)
                if bid_opening == "加载中...":
                    bid_opening = ""

            items.append({
                "id": bulletin_id,
                "title": title,
                "industry": industry,
                "region": region,
                "source": source,
                "publish_date": publish_date or date_display,
                "publish_datetime": publish_datetime or date_display,
                "bid_opening": bid_opening,
                "detail_url": _DETAIL_URL_TEMPLATE.format(id=bulletin_id),
            })
        except Exception as e:
            logging.warning("Row parse error: %s", e)
            continue

    # Pagination: check total pages
    total_match = re.search(r'共<label>(\d+)</label>页', html)
    has_more = True
    if total_match:
        has_more = page < int(total_match.group(1))
    elif len(items) < 20:
        has_more = False

    return items, has_more


def _fetch_all_listings(search_date):
    """Fetch all bulletin listings for the given date."""
    all_items = []
    page = 1

    while True:
        items, has_more = _fetch_listing_page(page, search_date)
        if not items:
            break

        # Verify items still belong to search date
        date_matches = sum(
            1 for it in items
            if (it.get("publish_date") or "") == search_date
        )
        if date_matches == 0:
            break

        all_items.extend(items)
        _safe_print("  Page {}: {} items ({} matching date)".format(
            page, len(items), date_matches))
        sys.stdout.flush()

        if not has_more:
            break

        page += 1
        _request_delay()

        if page > 100:
            break

    return all_items


# ---------------------------------------------------------------------------
# Persistence & state
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
    return {"processed_ids": [], "completed": False}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d processed IDs)", len(state.get("processed_ids", [])))


def _save_article_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id[:12]))
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


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def _process_batch(batch_items, output_dir, kb_id, tenant_id,
                   processed_ids, state, batch_num, total_new,
                   max_runtime, crawl_start):
    """Process one batch: build markdown → save → upload → checkpoint.

    Returns: (articles_processed, should_stop_early)
    """
    md_parts = []
    batch_ids = []

    for idx, item in enumerate(batch_items, 1):
        elapsed = time.time() - crawl_start
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print("\n[CEB] Runtime {:.0f}s approaching limit ({}s), "
                       "stopping early".format(elapsed, max_runtime))
            sys.stdout.flush()
            return len(batch_ids), True

        global_idx = (batch_num - 1) * _BATCH_SIZE + idx
        title = item.get("title", "无标题")
        _safe_print("[CEB] [{}/{}] {}".format(
            global_idx, total_new, title[:70]))
        sys.stdout.flush()

        # Build markdown
        md_lines = [
            "# {}".format(title),
            "",
            "| 字段 | 内容 |",
            "|------|------|",
            "| **公告ID** | {} |".format(item.get("id", "")),
            "| **所属行业** | {} |".format(item.get("industry", "")),
            "| **所属地区** | {} |".format(item.get("region", "")),
            "| **来源渠道** | {} |".format(item.get("source", "")),
            "| **公告发布时间** | {} |".format(item.get("publish_datetime", "")),
            "| **开标时间** | {} |".format(item.get("bid_opening", "")),
            "",
            "**详情页:** {}".format(item.get("detail_url", "")),
            "",
            "*注：详情页(ctbpsp.com)有阿里云WAF防护，本数据仅包含列表页信息。*",
            "",
        ]

        md_part = "\n".join(md_lines)
        md_parts.append(md_part)
        batch_ids.append(item["id"])

        _save_article_markdown(md_part, output_dir, item["id"])

    # ── Save batch & checkpoint ──
    if md_parts:
        batch_path = os.path.join(
            output_dir, "batch_{:03d}.md".format(batch_num))
        with open(batch_path, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))

        processed_ids.update(batch_ids)
        state["processed_ids"] = list(processed_ids)
        _save_state(output_dir, state)

        if kb_id:
            try:
                _upload_to_kb(batch_path, kb_id, tenant_id, parser_id="laws")
            except Exception as e:
                _safe_print("[CEB] Batch {} upload failed: {}".format(batch_num, e))
                logging.error("Upload batch %d: %s", batch_num, e)

        _safe_print("[CEB] Batch {} uploaded: {} articles".format(
            batch_num, len(md_parts)))
        sys.stdout.flush()

    return len(batch_ids), False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="bulletin.cebpubservice.com listing crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://bulletin.cebpubservice.com/",
                   help="Site URL (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop (default: 3300 = 55 min)")
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    search_date = datetime.now().strftime("%Y-%m-%d")

    _safe_print("\n" + "=" * 60)
    _safe_print("[CEB] bulletin.cebpubservice.com listing crawler")
    _safe_print("[CEB] Date filter: {} (today only)".format(search_date))
    _safe_print("[CEB] Detail pages: SKIPPED (ctbpsp.com WAF-protected)")
    _safe_print("[CEB] KB: {} | max-runtime: {}s".format(
        args.kb_id, args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== cebpubservice crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[CEB] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    crawl_start = time.time()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False}
    processed_ids = set(state.get("processed_ids", []))

    if state.get("completed"):
        _safe_print("[CEB] Already completed. Use --full to re-crawl.\n")
        sys.stdout.flush()
        return

    _safe_print("[CEB] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # ====================================================================
    # Step 1: Fetch all listings
    # ====================================================================
    _safe_print("[CEB] Step 1/2: Fetching listings for {}...".format(search_date))
    sys.stdout.flush()

    all_items = _fetch_all_listings(search_date)
    _safe_print("[CEB]   Total: {} items\n".format(len(all_items)))
    sys.stdout.flush()

    if not all_items:
        _safe_print("[CEB] No bulletins found for {}.\n".format(search_date))
        sys.stdout.flush()
        return

    # -- Filter already processed -------------------------------------------
    new_items = [it for it in all_items
                 if it["id"] not in processed_ids]
    _safe_print("[CEB]   {} new (skipped {} already processed)\n".format(
        len(new_items), len(all_items) - len(new_items)))
    sys.stdout.flush()

    if not new_items:
        _safe_print("[CEB] Nothing new.\n")
        sys.stdout.flush()
        return

    # ====================================================================
    # Step 2: Process in batches
    # ====================================================================
    _safe_print("[CEB] Step 2/2: Processing {} items in batches of {}...\n".format(
        len(new_items), _BATCH_SIZE))
    sys.stdout.flush()

    total_processed = 0
    stopped_early = False

    for batch_num in range(1, 999):
        start = (batch_num - 1) * _BATCH_SIZE
        end = start + _BATCH_SIZE
        batch = new_items[start:end]
        if not batch:
            break

        n, early = _process_batch(
            batch, output_dir, args.kb_id, args.tenant_id,
            processed_ids, state, batch_num, len(new_items),
            args.max_runtime, crawl_start,
        )
        total_processed += n
        if early:
            stopped_early = True
            break

    # -- Mark completed -----------------------------------------------------
    if not stopped_early:
        state["completed"] = True
        _save_state(output_dir, state)

    # ====================================================================
    # Done
    # ====================================================================
    _safe_print("\n" + "=" * 60)
    _safe_print("[CEB] Done: {} articles processed{}".format(
        total_processed, " (stopped early)" if stopped_early else ""))
    _safe_print("[CEB] State: {}".format(
        "completed" if not stopped_early else "partial"))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== cebpubservice crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "cebpubservice_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
