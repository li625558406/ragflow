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
Crawler for zjfw.zhangzhou.gov.cn — 评分排序 (score_sort) section.

This is a listing-only section (~498 pages, ~4976 records).  No detail pages
exist — all content comes directly from the listing API response.

API: POST /imng/api-v2/.../AgencyinfoCmd/queryInterSort
      { item_id, agencyname, sort, start, limit }

Each row contains: company name, item name/code, year/quarter/service scores,
deduction, and total score.

Signature-based authentication (same mechanism as zjfw_crawler.py):
  1. Fetch homepage → extract __signature
  2. Generate 6-char key from signature
  3. Build timestamp param
  4. Pass ?s={sig}&t={ts} on every API call

Checkpoint/resume: rows saved in batches of 50 pages, state persisted after
each batch.  Time-bounded check (default 3300s) stops gracefully before the
3600s task-timeout window.

Usage:
    python zjfw_score_sort_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://zjfw.zhangzhou.gov.cn/imng/zjfw \
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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "http://zjfw.zhangzhou.gov.cn"
_API_BASE = _SITE_ROOT + "/imng/api-v2"

# Listing API
_LIST_ACTION = "fujian.zhangzhougaoxin.app.icity.browse.agencyinfo.AgencyinfoCmd"
_LIST_METHOD = "queryInterSort"

# Default sort: 1 (综合排序)
_DEFAULT_SORT = "1"

# Pagination
_PAGE_SIZE = 10

# Checkpoint batch size (in pages)
_BATCH_PAGES = 50

# Default max runtime (55 min, 5 min margin)
_MAX_RUNTIME_DEFAULT = 3300

# Anti-crawling delays
_REQUEST_DELAY_MIN = 0.5
_REQUEST_DELAY_MAX = 1.5

# State filename
_STATE_FILENAME = "_crawler_state.json"

# Section display label
_SECTION_LABEL = "评分排序"

# Row field mappings: API key → Chinese label
_ROW_FIELDS = [
    ("CONAME", "公司名称"),
    ("ITEM_NAME", "项目名称"),
    ("ITEM_CODE", "项目编号"),
    ("YEAR_SCORE", "年度评分"),
    ("QUARTER_SCORE", "季度评分"),
    ("SERVICE_SCORE", "服务评分"),
    ("SCORE_OTHER", "其他评分"),
    ("REDUCE_SCORE", "扣分"),
    ("SCORE_SUM", "总分"),
]


# ---------------------------------------------------------------------------
# Print helper
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


# ---------------------------------------------------------------------------
# Signature helpers (same as zjfw_crawler.py)
# ---------------------------------------------------------------------------

_SIG_CHARS = "0123456789abcdef"


def _fetch_signature(home_url, client):
    """Fetch homepage and extract __signature value."""
    try:
        resp = client.get(home_url)
        resp.raise_for_status()
        html = resp.text
        m = re.search(r'__signature\s*=\s*["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        logging.warning("__signature not found in homepage HTML")
        return None
    except Exception as e:
        logging.error("Failed to fetch signature: %s", e)
        return None


def _generate_key(sig):
    """Generate a 6-character key from the signature."""
    key = ""
    key_index = -1
    for i in range(6):
        c = sig[key_index + 1]
        key += c
        key_index = _SIG_CHARS.index(c)
        if key_index < 0 or key_index >= len(sig):
            key_index = i
    return key


def _gen_timestamp(key):
    """Build timestamp: {rand8}_{key}_{epoch_ms}."""
    rand8 = str(random.randint(10000000, 99999999))
    epoch_ms = int(time.time() * 1000)
    return f"{rand8}_{key}_{epoch_ms}"


def _call_api(action, method, params, sig_cache, client, timeout=30):
    """POST to the signed API endpoint."""
    if not sig_cache.get("sig") or not sig_cache.get("key"):
        sig = _fetch_signature(sig_cache["home_url"], client)
        if not sig:
            return None
        sig_cache["sig"] = sig
        sig_cache["key"] = _generate_key(sig)

    ts = _gen_timestamp(sig_cache["key"])
    url = f"{_API_BASE}/{action}/{method}?s={sig_cache['sig']}&t={ts}"

    try:
        headers = {"Referer": sig_cache.get("home_url", _SITE_ROOT + "/imng/zjfw")}
        resp = client.post(url, json_body=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.error("API call %s/%s failed: %s", action, method, e)
        return None


# ---------------------------------------------------------------------------
# Listing fetcher
# ---------------------------------------------------------------------------

def _fetch_score_sort_page(sig_cache, client, start=0, limit=_PAGE_SIZE, sort=_DEFAULT_SORT):
    """Fetch one page of the score_sort listing.

    Returns (total, rows) where rows is list[dict].
    """
    params = {
        "item_id": "",
        "agencyname": "",
        "sort": sort,
        "start": start,
        "limit": limit,
    }
    data = _call_api(_LIST_ACTION, _LIST_METHOD, params, sig_cache, client)
    if not data:
        return 0, []

    total = data.get("total", 0)
    rows = data.get("data", []) or []
    return total, rows


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {
        "processed_keys": [],
        "completed": False,
        "last_start": 0,
    }


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(rows):
    """Build markdown table from score_sort listing rows."""
    lines = [
        "# {} — 漳州市工程项目中介服务平台".format(_SECTION_LABEL),
        "",
        "**数据来源:** {}/imng/zjfw/browse/score_sort".format(_SITE_ROOT),
        "**抓取时间:** {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "**记录数:** {}".format(len(rows)),
        "",
        "---",
        "",
    ]

    if not rows:
        lines.append("*无数据*")
        return "\n".join(lines)

    # Table header
    headers = ["序号"] + [label for _, label in _ROW_FIELDS]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")

    for idx, row in enumerate(rows, 1):
        vals = [str(idx)]
        for field, _ in _ROW_FIELDS:
            val = (row.get(field) or "").strip()
            vals.append(val if val else "-")
        lines.append("| " + " | ".join(vals) + " |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------

def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"score_sort_{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


def _make_row_key(row):
    """Generate a stable dedup key from a listing row.

    Uses INTER_ID (unique service record ID) primary; falls back to
    ITEM_CODE + CONAME combination.
    """
    inter_id = (row.get("INTER_ID") or "").strip()
    if inter_id:
        return inter_id
    item_code = (row.get("ITEM_CODE") or "").strip()
    coname = (row.get("CONAME") or "").strip()
    return f"{item_code}|{coname}"


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="ZJFW score_sort crawler — listing-only section"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url", default="http://zjfw.zhangzhou.gov.cn/imng/zjfw",
                   help="ZJFW homepage URL")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds before graceful stop (default: 3300)")
    p.add_argument("--max-articles", type=int, default=0,
                   help="Max records to fetch (0 = unlimited)")
    for opt in ("--section", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[ZJFW-SCORE] 评分排序 crawler — listing-only")
    _safe_print("[ZJFW-SCORE] Target: {}".format(args.target_url))
    _safe_print("[ZJFW-SCORE] KB: {}".format(args.kb_id))
    _safe_print("[ZJFW-SCORE] Task: {}".format(args.task_name))
    _safe_print("[ZJFW-SCORE] Max articles: {}".format(
        args.max_articles if args.max_articles else "unlimited"))
    _safe_print("[ZJFW-SCORE] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZJFW score_sort crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[ZJFW-SCORE] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    client = PlaywrightHttpClient()
    client.start()
    crawl_start = time.time()

    try:
        # Load state
        state = _load_state(output_dir) if not args.full else {
            "processed_keys": [], "completed": False, "last_start": 0,
        }
        processed_keys = set(state.get("processed_keys", []))

        if state.get("completed"):
            _safe_print("[ZJFW-SCORE] Already completed, nothing to do.")
            sys.stdout.flush()
            return

        _safe_print("[ZJFW-SCORE] Already processed: {} record(s)".format(
            len(processed_keys)))
        sys.stdout.flush()

        sig_cache = {"sig": None, "key": None, "home_url": args.target_url.rstrip("/")}

        # Fetch first page to get total
        _safe_print("[ZJFW-SCORE] Fetching first page to get total count...")
        sys.stdout.flush()

        total, first_page = _fetch_score_sort_page(sig_cache, client, start=0)
        if total == 0:
            _safe_print("[ZJFW-SCORE] No data returned, exiting.")
            sys.stdout.flush()
            return

        max_records = args.max_articles if args.max_articles else total
        to_fetch = min(total, max_records)
        _safe_print("[ZJFW-SCORE] Total: {}, will fetch up to: {}".format(
            total, to_fetch))
        sys.stdout.flush()

        # Determine starting offset from state
        start_offset = state.get("last_start", 0)
        if start_offset > 0:
            _safe_print("[ZJFW-SCORE] Resuming from offset {}".format(start_offset))
            sys.stdout.flush()

        all_rows = []
        stopped_early = False
        batch_num = 0
        offset = start_offset

        while offset < to_fetch:
            # ── Time-bounded check before each batch ──
            elapsed = time.time() - crawl_start
            if elapsed > args.max_runtime - 120:
                _safe_print(
                    "\n[ZJFW-SCORE] Runtime {:.0f}s approaching limit ({}s), "
                    "stopping gracefully. {} records fetched. "
                    "Next run will resume from offset {}.".format(
                        elapsed, args.max_runtime, len(all_rows), offset))
                sys.stdout.flush()
                stopped_early = True
                break

            page = (offset // _PAGE_SIZE) + 1
            _safe_print("[ZJFW-SCORE]   Page {} (offset {}, {:.0f}%)".format(
                page, offset, offset / to_fetch * 100 if to_fetch else 0))
            sys.stdout.flush()

            _, rows = _fetch_score_sort_page(sig_cache, client, start=offset)

            if not rows:
                logging.warning("Empty page at offset %d, continuing", offset)
                offset += _PAGE_SIZE
                continue

            all_rows.extend(rows)
            offset += len(rows)

            # Checkpoint every BATCH_PAGES worth of records
            batch_num += 1
            if batch_num % _BATCH_PAGES == 0 or offset >= to_fetch:
                # Save state
                state["last_start"] = offset
                state["processed_keys"] = list(processed_keys)  # placeholder
                _save_state(output_dir, state)

                _safe_print("[ZJFW-SCORE]   checkpoint saved at offset {} "
                            "({} records so far)".format(offset, len(all_rows)))
                sys.stdout.flush()

            # Stop if we fetched enough
            if len(all_rows) >= to_fetch:
                all_rows = all_rows[:to_fetch]
                break

            time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

        _safe_print("\n[ZJFW-SCORE] Fetched {} rows total.".format(len(all_rows)))
        sys.stdout.flush()

        if not all_rows:
            _safe_print("[ZJFW-SCORE] No rows fetched, exiting.")
            sys.stdout.flush()
            return

        # Dedup
        new_rows = []
        for row in all_rows:
            key = _make_row_key(row)
            if key not in processed_keys:
                new_rows.append(row)
                processed_keys.add(key)

        skipped = len(all_rows) - len(new_rows)
        _safe_print("[ZJFW-SCORE] {} new ({} skipped by dedup)".format(
            len(new_rows), skipped))
        sys.stdout.flush()

        if not new_rows:
            _safe_print("[ZJFW-SCORE] All rows already processed, nothing to do.")
            sys.stdout.flush()
            if not stopped_early:
                state["completed"] = True
                _save_state(output_dir, state)
            return

        # Build markdown & save
        _safe_print("[ZJFW-SCORE] Building markdown...")
        sys.stdout.flush()

        md = _build_markdown(new_rows)
        filepath = _save_markdown(md, output_dir)
        _safe_print("[ZJFW-SCORE] Saved to {} ({} chars)".format(
            filepath, len(md)))
        sys.stdout.flush()

        # Upload to KB
        if args.kb_id:
            _safe_print("[ZJFW-SCORE] Uploading to KB {}...".format(args.kb_id))
            sys.stdout.flush()
            try:
                _upload_to_kb(filepath, args.kb_id, args.tenant_id)
                _safe_print("[ZJFW-SCORE] Upload complete!")
                sys.stdout.flush()
            except Exception as e:
                logging.error("Upload failed: %s", e)
                _safe_print("[ZJFW-SCORE] Upload error: {}".format(e))
                sys.stdout.flush()

        # Final state
        state["last_start"] = offset
        state["processed_keys"] = list(processed_keys)
        if not stopped_early:
            state["completed"] = True
        _save_state(output_dir, state)

    finally:
        client.stop()

    _safe_print("\n" + "=" * 60)
    _safe_print("[ZJFW-SCORE] Crawl complete — {} records".format(
        len(state.get("processed_keys", []))))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== ZJFW score_sort crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "zjfw_score_sort_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
