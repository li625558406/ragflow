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
Crawler for www.ccgp.gov.cn — 政府采购代理机构名单 (agency list).

Site:  http://www.ccgp.gov.cn/agency/md/
Data:  Iframe → pubListIndex.regx → getPubList.regx (EasyUI datagrid JSON API)
Total: ~60,000+ records across ~2,000 pages (30 per page).

Crawls the listing data only (no detail pages).  Dedup by ``uuid`` field.

Usage:
    python ccgp_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://www.ccgp.gov.cn/agency/md/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_API_INDEX_URL = "http://jczy.ccgp.gov.cn/gs1/gs1agentreg/pubListIndex.regx"
_API_LIST_URL = "http://jczy.ccgp.gov.cn/gs1/gs1agentreg/getPubList.regx"
_PAGE_SIZE = 30

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "http://jczy.ccgp.gov.cn/gs1/gs1agentreg/pubListIndex.regx",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "X-Requested-With": "XMLHttpRequest",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

_REQUEST_DELAY_MIN = 1.0   # minimum seconds between API calls
_REQUEST_DELAY_MAX = 2.5   # maximum seconds between API calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _init_session():
    """Create a requests Session with JSESSIONID cookie and browser-like headers."""
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get(_API_INDEX_URL, timeout=30)
    except Exception as e:
        logging.warning("Session init request failed: %s", e)
    return sess


def _reset_session(sess):
    """Re-initialize session cookies if the old session expired."""
    sess.cookies.clear()
    try:
        sess.get(_API_INDEX_URL, timeout=30)
    except Exception as e:
        logging.warning("Session reset request failed: %s", e)


def _fetch_page(sess, page_num, page_size=_PAGE_SIZE):
    """Fetch one page from the agency list API.  Returns (rows, total) or (None, 0)."""
    for attempt in range(4):
        try:
            resp = sess.get(_API_LIST_URL, params={
                "provinceCode": "",
                "page": page_num,
                "rows": page_size,
            }, timeout=60)
            if resp.status_code == 429:
                # Rate limited — exponential backoff
                wait = (2 ** attempt) + random.uniform(1, 3)
                logging.warning("Page %d rate limited (429), waiting %.1fs...",
                                page_num, wait)
                time.sleep(wait)
                _reset_session(sess)
                continue
            if resp.status_code != 200:
                logging.warning("Page %d HTTP %d, resetting session...",
                                page_num, resp.status_code)
                _reset_session(sess)
                time.sleep(2 + attempt * 2)
                continue
            data = resp.json()
            rows = data.get("rows") or []
            total = int(data.get("total", 0))
            return rows, total
        except Exception as e:
            logging.warning("Page %d attempt %d failed: %s", page_num, attempt + 1, e)
            _reset_session(sess)
            time.sleep(2 + attempt * 3)
    return None, 0


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
    return {"processed_ids": [], "last_page": 0}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------

def _format_record_md(record):
    """Format a single agency record as Markdown."""
    title = record.get("agentNm", "").strip() or "\u65e0\u540d\u79f0"
    lines = [
        "# {}".format(title),
        "",
        "| \u5b57\u6bb5 | \u503c |",
        "|------|-----|",
        "| \u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801 | {} |".format(
            record.get("orgCode", "")),
        "| \u8054\u7cfb\u4eba | {} |".format(
            record.get("contactNm", "")),
        "| \u8054\u7cfb\u7535\u8bdd | {} |".format(
            record.get("corpTel", "")),
        "| \u6ce8\u518c\u5730\u5740 | {} |".format(
            record.get("regAddr", "")),
        "| \u767b\u8bb0\u65e5\u671f | {} |".format(
            record.get("regValidDateStr", "")),
        "| \u767b\u8bb0\u5730\u70b9 | {} |".format(
            record.get("auditPlace", "")),
        "| \u5907\u6ce8 | {} |".format(
            record.get("publicRemark", "")),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Batch upload helper
# ---------------------------------------------------------------------------

def _upload_batch(records, output_dir, processed_ids, state,
                  kb_id, tenant_id, batch_num):
    """Format a batch of records as markdown, save, and upload to KB."""
    md_parts = []
    for rec in records:
        md_parts.append(_format_record_md(rec))

    combined_path = os.path.join(output_dir, "agency_batch_{:04d}.md".format(batch_num))
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(md_parts))

    for rec in records:
        rid = rec.get("uuid", "")
        if rid:
            processed_ids.add(rid)

    state["processed_ids"] = list(processed_ids)
    _save_state(output_dir, state)

    if kb_id:
        try:
            _upload_to_kb(combined_path, kb_id, tenant_id)
        except Exception as e:
            logging.error("Upload batch %d failed: %s", batch_num, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="CCGP agency list crawler for scheduled tasks"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="http://www.ccgp.gov.cn/agency/md/",
                   help="Page URL (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[AGENCY] CCGP agency list crawler")
    _safe_print("[AGENCY] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Agency crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[AGENCY] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {"processed_ids": [], "last_page": 0}
    processed_ids = set(state.get("processed_ids", []))
    last_page_done = state.get("last_page", 0)
    _safe_print("[AGENCY] Previously processed: {}, last page done: {}\n".format(
        len(processed_ids), last_page_done))
    sys.stdout.flush()

    # -- Init session --------------------------------------------------------
    _safe_print("[AGENCY] Initializing session...")
    sys.stdout.flush()
    sess = _init_session()

    # -- Start page ----------------------------------------------------------
    start_page = last_page_done + 1
    _safe_print("[AGENCY] Fetching page {} to get total count...".format(start_page))
    sys.stdout.flush()
    rows, total = _fetch_page(sess, start_page)
    if not rows:
        _safe_print("[AGENCY] ERROR: Failed to fetch page {}.".format(start_page))
        return

    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    _safe_print("[AGENCY] Total: {} records, {} pages (resuming from page {})\n".format(
        total, total_pages, start_page))
    sys.stdout.flush()

    # -- Checkpoint constants -------------------------------------------------
    CHECKPOINT_PAGES = 100   # save state every N pages
    BATCH_SIZE = 500         # markdown format + upload batch size
    CONSECUTIVE_OLD_PAGES = 5

    # -- Fetch pages with early stop ------------------------------------------
    # Data is sorted by regValidDate desc (newest first).  When we hit
    # CONSECUTIVE_OLD_PAGES pages in a row with zero new records, stop.
    new_records = []
    all_scanned = 0
    stale_streak = 0
    stopped_early = False
    last_saved_batch = 0  # track which batch was last uploaded

    for pg in range(start_page, total_pages + 1):
        if pg > start_page:
            time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))
            rows, _ = _fetch_page(sess, pg)

        if not rows:
            _safe_print("[AGENCY]   Page {} empty, stopping.".format(pg))
            break

        all_scanned += len(rows)
        new_in_page = 0
        for r in rows:
            rid = r.get("uuid", "")
            if rid and rid not in processed_ids:
                new_records.append(r)
                new_in_page += 1

        if new_in_page == 0:
            stale_streak += 1
        else:
            stale_streak = 0

        # -- Checkpoint: save page progress + upload batches -------------------
        if pg % CHECKPOINT_PAGES == 0 or pg == start_page:
            _safe_print("[AGENCY]   Page {}: {} new / {} scanned (streak={})".format(
                pg, new_in_page, all_scanned, stale_streak))
            sys.stdout.flush()
            # Save page progress to state (so resume works after timeout)
            state["last_page"] = pg
            _save_state(output_dir, state)

            # Upload completed batches
            while len(new_records) - last_saved_batch >= BATCH_SIZE:
                batch_end = last_saved_batch + BATCH_SIZE
                _upload_batch(new_records[last_saved_batch:batch_end],
                              output_dir, processed_ids, state,
                              args.kb_id, args.tenant_id,
                              len(processed_ids) // BATCH_SIZE + 1)
                last_saved_batch = batch_end
                _safe_print("[AGENCY]     batch {} uploaded ({} records)".format(
                    len(processed_ids) // BATCH_SIZE, BATCH_SIZE))
                sys.stdout.flush()

        if stale_streak >= CONSECUTIVE_OLD_PAGES:
            _safe_print("[AGENCY]   {} consecutive pages with no new records, stopping early.".format(
                CONSECUTIVE_OLD_PAGES))
            stopped_early = True
            break

    # Final checkpoint
    state["last_page"] = 0  # reset for next run (which starts from page 1)
    _save_state(output_dir, state)

    _safe_print("[AGENCY] Scanned: {} pages ({} to {}), {} records, {} new ({})".format(
        pg, start_page, pg, all_scanned, len(new_records),
        "early stop" if stopped_early else "full scan"))
    sys.stdout.flush()

    # -- Upload remaining batches ---------------------------------------------
    while last_saved_batch < len(new_records):
        batch_end = min(last_saved_batch + BATCH_SIZE, len(new_records))
        _upload_batch(new_records[last_saved_batch:batch_end],
                      output_dir, processed_ids, state,
                      args.kb_id, args.tenant_id,
                      len(processed_ids) // BATCH_SIZE + 1)
        last_saved_batch = batch_end

    if not new_records:
        _safe_print("[AGENCY] Nothing new.")
        return

    _safe_print("\n" + "=" * 60)
    _safe_print("[AGENCY] Done: {} new records in {} batches".format(
        len(new_records), (len(new_records) + BATCH_SIZE - 1) // BATCH_SIZE))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== Agency crawler finished: %d records ===", len(new_records))


if __name__ == "__main__":
    CONSUMER_NAME = "ccgp_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
