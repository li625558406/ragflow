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
Crawler for ggzyfw.fujian.gov.cn — business trading info (交易信息) section.

The site is a Vue 2.6 SPA.  All API calls go to /FwPortalApi/* endpoints with
AES-256-CBC encrypted responses and portal-sign MD5 request signing.

Data flow
─────────
  1. **Listing**: POST /FwPortalApi/Trade/TradeInfo
     → Encrypted response with PageTotal/Table fields.
     → Each row has M_ID, NAME, TITLE, AREANAME, PLATFORM_NAME, TM, etc.
  2. **Detail content**: POST /FwPortalApi/Trade/TradeInfoContent
     → {type: 1, m_id: str(M_ID)}
     → Returns encrypted HTML with inline CSS styling.
  3. No separate attachment API — the content HTML is plain styled text.

Authentication
──────────────
  - portal-sign header: MD5(SECRET + sorted_key_value_concat)
  - ts (epoch ms) included in sign calculation and request body
  - AES-256-CBC decryption with PKCS7 padding

Date filtering
──────────────
  Uses BeginTime/EndTime parameters for the past 30 days (近一个月).

Checkpoint/resume: state saved every 5 pages.  Time-bounded check
(default 3300s) stops gracefully before the 3600s task-timeout window.

Usage
-----
    python ggzyfw_business_crawler.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>

    # Optional:
        --max-articles 100       # Limit records (0=unlimited)
        --full                   # Ignore saved state, re-crawl
        --max-runtime 3300       # Max runtime before graceful stop
        --max-days 30            # Days to look back (default: 30)
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# Crypto (for AES decryption)
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://ggzyfw.fujian.gov.cn"
_API_BASE = _SITE_ROOT + "/FwPortalApi"
_LISTING_PATH = "/Trade/TradeInfo"
_CONTENT_PATH = "/Trade/TradeInfoContent"
_SECTION_LABEL = "交易信息"

# AES-256-CBC keys (CryptoJS.enc.Utf8.parse of these strings → 32/16 byte keys)
_AES_KEY = b"EB444973714E4A40876CE66BE45D5930"      # 32 bytes → AES-256
_AES_IV = b"B5A8904209931867"                        # 16 bytes → AES IV
_SIGN_SECRET = "B3978D054A72A7002063637CCDF6B2E5"    # MD5 signing secret

_PAGE_SIZE = 20

# Checkpoint batch size (pages)
_BATCH_PAGES = 5

# Default max runtime (55 min, 5 min margin)
_MAX_RUNTIME_DEFAULT = 3300

# Anti-crawling delays
_REQUEST_DELAY_MIN = 0.5
_REQUEST_DELAY_MAX = 1.5

# State filename
_STATE_FILENAME = "_crawler_state.json"

# Project kind (GCJS = 工程建设)
_DEFAULT_KIND = "GCJS"

# Default lookback days
_DEFAULT_MAX_DAYS = 30

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _sanitize_filename(text, max_len=120):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name).strip("._ ")
    return (name or "untitled")[:max_len]


# ---------------------------------------------------------------------------
# AES decryption
# ---------------------------------------------------------------------------

def _decrypt(encrypted_b64):
    """Decrypt AES-256-CBC encrypted base64 string from API response."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("pycryptodome not installed (pip install pycryptodome)")
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    encrypted_bytes = base64.b64decode(encrypted_b64)
    decrypted = cipher.decrypt(encrypted_bytes)
    return unpad(decrypted, AES.block_size).decode("utf-8")


# ---------------------------------------------------------------------------
# portal-sign calculation
# ---------------------------------------------------------------------------

def _get_sign(params):
    """Calculate portal-sign header value.

    Mirrors the JS getSign() function:
      1. Remove empty/undefined params
      2. Sort keys case-insensitively (toUpperCase comparison)
      3. Concat: SECRET + key1+val1 + key2+val2 + ...
      4. For object/array values, use JSON.stringify (no spaces)
      5. MD5 → lowercase hex
    """
    clean = {}
    for k, v in params.items():
        if v == "" or v is None:
            continue
        clean[k] = v

    sorted_keys = sorted(clean.keys(), key=lambda x: x.upper())
    parts = []
    for k in sorted_keys:
        v = clean[k]
        if isinstance(v, (dict, list)):
            parts.append(k + json.dumps(v, separators=(",", ":"), ensure_ascii=False))
        else:
            parts.append(k + str(v))

    concat = _SIGN_SECRET + "".join(parts)
    return hashlib.md5(concat.encode("utf-8")).hexdigest().lower()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _call_api(endpoint, params, timeout=30):
    """POST to /FwPortalApi/endpoint with signing and decryption.

    Returns decoded Python object, or None on failure.
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("pycryptodome not installed")

    ts = int(time.time() * 1000)
    params["ts"] = ts
    sign = _get_sign(params)

    url = _API_BASE + endpoint
    body = json.dumps(params, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("Content-Type", "application/json;charset=UTF-8")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("portal-sign", sign)
    req.add_header("Referer", _SITE_ROOT + "/business/list/")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as e:
        logging.error("API call %s failed: %s", endpoint, e)
        return None

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        logging.error("JSON parse error for %s: %s", endpoint, e)
        return None

    if data.get("State") == "200" and data.get("Data"):
        try:
            return json.loads(_decrypt(data["Data"]))
        except Exception as e:
            logging.error("Decrypt error for %s: %s", endpoint, e)
            return None

    logging.error("API error for %s: State=%s, Msg=%s",
                   endpoint, data.get("State"), data.get("Msg", ""))
    return None


# ---------------------------------------------------------------------------
# Listing fetcher
# ---------------------------------------------------------------------------

def _fetch_listing_page(page_no, kind, begin_time, end_time):
    """Fetch one page of the listing API.

    Returns dict with keys: PageTotal, PageNo, Total, PageSize, Table (list).
    """
    params = {
        "pageSize": _PAGE_SIZE,
        "pageNo": page_no,
        "AREACODE": "",
        "M_PROJECT_TYPE": "",
        "KIND": kind,
        "GGTYPE": "",
        "PROTYPE": "",
        "BeginTime": begin_time,
        "EndTime": end_time,
        "NAME": "",
    }
    return _call_api(_LISTING_PATH, params)


# ---------------------------------------------------------------------------
# Detail content fetcher
# ---------------------------------------------------------------------------

def _fetch_content(m_id):
    """Fetch detail page HTML content for a given M_ID.

    Returns dict with: M_ID, Type, Contents (HTML string).
    """
    params = {
        "type": 1,
        "m_id": str(m_id),
    }
    return _call_api(_CONTENT_PATH, params)


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(row, content_html):
    """Build a Markdown document from listing data + detail HTML content."""
    title = (row.get("NAME") or row.get("TITLE") or "").strip()
    title_type = (row.get("TITLE") or "").strip()
    area = (row.get("AREANAME") or "").strip()
    platform = (row.get("PLATFORM_NAME") or "").strip()
    protype = (row.get("PROTYPE_TEXT") or "").strip()
    pub_date = (row.get("TM1") or row.get("TM") or "").strip()
    m_id = row.get("M_ID", "")

    lines = [
        f"# {title}",
        "",
        f"**数据来源:** 福建省公共资源交易电子公共服务平台 — {_SECTION_LABEL}",
        f"**页面地址:** {_SITE_ROOT}/business/detail?name=&cid={m_id}&type=GCJS",
        f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if title_type:
        lines.append(f"**信息类型:** {title_type}")
    if pub_date:
        lines.append(f"**发布时间:** {pub_date}")
    if area:
        lines.append(f"**所属地区:** {area}")
    if platform:
        lines.append(f"**交易平台:** {platform}")
    if protype:
        lines.append(f"**行业类型:** {protype}")
    lines.append("")

    # Detail content
    if content_html:
        lines.append("---")
        lines.append("")
        lines.append("## 详细内容")
        lines.append("")

        # Convert HTML to plain text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content_html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        # Compact multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


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
    return {"processed_ids": [], "completed": False, "last_page": 1}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="ggzyfw.fujian.gov.cn business crawler — 交易信息"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://ggzyfw.fujian.gov.cn/business/list")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds (default: 3300)")
    p.add_argument("--max-articles", type=int, default=0,
                   help="Max records to fetch (0 = unlimited)")
    p.add_argument("--max-days", type=int, default=_DEFAULT_MAX_DAYS,
                   help="Days to look back (default: 30)")
    # Legacy/unused args (accepted for task executor compatibility)
    for opt in ("--section", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[GGZY-BIZ] 福建省公共资源交易平台 — {} crawler".format(_SECTION_LABEL))
    _safe_print("[GGZY-BIZ] Target: {}".format(args.target_url))
    _safe_print("[GGZY-BIZ] KB: {}".format(args.kb_id))
    _safe_print("[GGZY-BIZ] Task: {}".format(args.task_name))
    _safe_print("[GGZY-BIZ] Max articles: {}".format(
        args.max_articles if args.max_articles else "unlimited"))
    _safe_print("[GGZY-BIZ] Max runtime: {}s".format(args.max_runtime))
    _safe_print("[GGZY-BIZ] Lookback days: {}".format(args.max_days))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    if not CRYPTO_AVAILABLE:
        _safe_print("[GGZY-BIZ] ERROR: pycryptodome not installed.")
        _safe_print("[GGZY-BIZ]   pip install pycryptodome")
        sys.stdout.flush()
        return

    settings.init_settings()
    logging.info("=== GGZY-BIZ crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[GGZY-BIZ] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False, "last_page": 1,
    }
    processed_ids = set(state.get("processed_ids", []))

    if state.get("completed"):
        _safe_print("[GGZY-BIZ] Already completed, nothing to do.")
        sys.stdout.flush()
        return

    _safe_print("[GGZY-BIZ] Already processed: {} record(s)".format(len(processed_ids)))
    sys.stdout.flush()

    # ── Date range ─────────────────────────────────────────────────────
    end_time = datetime.now().strftime("%Y-%m-%d 23:59:59")
    begin_time = (datetime.now() - timedelta(days=args.max_days)).strftime("%Y-%m-%d 00:00:00")
    _safe_print("[GGZY-BIZ] Date range: {} ~ {}".format(begin_time, end_time))
    sys.stdout.flush()

    crawl_start = time.time()

    # ── Step 1: Get total count from first page ─────────────────────────
    _safe_print("[GGZY-BIZ] Step 1/3: Fetching first page to get total...")
    sys.stdout.flush()

    first_page = _fetch_listing_page(1, _DEFAULT_KIND, begin_time, end_time)
    if not first_page:
        _safe_print("[GGZY-BIZ] Failed to fetch listing, exiting.")
        sys.stdout.flush()
        return

    total_records = first_page.get("Total", 0)
    total_pages = first_page.get("PageTotal", 0)
    _safe_print("[GGZY-BIZ] Total: {} records across {} pages".format(
        total_records, total_pages))
    sys.stdout.flush()

    if total_records == 0:
        _safe_print("[GGZY-BIZ] No data returned, exiting.")
        sys.stdout.flush()
        return

    max_records = args.max_articles if args.max_articles else total_records

    # ── Step 2: Paginate through all listing pages ─────────────────────
    _safe_print("\n[GGZY-BIZ] Step 2/3: Paginating through listing...")
    sys.stdout.flush()

    start_page = state.get("last_page", 1)
    if start_page > 1:
        _safe_print("[GGZY-BIZ] Resuming from page {}".format(start_page))
        sys.stdout.flush()

    all_rows = []
    stopped_early = False

    for page_no in range(start_page, total_pages + 1):
        # ── Time-bounded check ─────────────────────────────────────
        elapsed = time.time() - crawl_start
        if elapsed > args.max_runtime - 300:
            _safe_print(
                "\n[GGZY-BIZ] Runtime {:.0f}s approaching limit ({}s), "
                "stopping pagination. {} rows collected. "
                "Next run will resume from page {}.".format(
                    elapsed, args.max_runtime, len(all_rows), page_no))
            sys.stdout.flush()
            stopped_early = True
            break

        if page_no == 1:
            rows = first_page.get("Table", [])
        else:
            page_data = _fetch_listing_page(page_no, _DEFAULT_KIND, begin_time, end_time)
            if not page_data:
                logging.warning("Failed to fetch page %d, skipping", page_no)
                continue
            rows = page_data.get("Table", [])

        all_rows.extend(rows)

        if page_no % 10 == 0 or page_no == total_pages:
            _safe_print("[GGZY-BIZ]   Page {} of {} ({} rows collected)".format(
                page_no, total_pages, len(all_rows)))
            sys.stdout.flush()

        # Checkpoint every batch of pages
        if page_no % _BATCH_PAGES == 0:
            state["last_page"] = page_no + 1
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

        # Check max articles limit
        if args.max_articles and len(all_rows) >= max_records:
            all_rows = all_rows[:max_records]
            break

        time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

    _safe_print("[GGZY-BIZ] Collected {} rows total.\n".format(len(all_rows)))
    sys.stdout.flush()

    if not all_rows:
        _safe_print("[GGZY-BIZ] No rows collected, exiting.")
        sys.stdout.flush()
        return

    # ── Filter already-processed ───────────────────────────────────────
    new_rows = [r for r in all_rows if str(r.get("M_ID", "")) not in processed_ids]
    skipped = len(all_rows) - len(new_rows)
    if skipped:
        _safe_print("[GGZY-BIZ] {} already processed, {} new".format(skipped, len(new_rows)))
        sys.stdout.flush()

    if not new_rows:
        _safe_print("[GGZY-BIZ] All rows already processed.")
        sys.stdout.flush()
        if not stopped_early:
            state["completed"] = True
            _save_state(output_dir, state)
        return

    # ── Step 3: Process each record (fetch content, build MD, upload) ──
    _safe_print("\n[GGZY-BIZ] Step 3/3: Processing {} new record(s)...\n".format(
        len(new_rows)))
    sys.stdout.flush()

    processed_count = 0

    for idx, row in enumerate(new_rows, 1):
        # ── Time-bounded check ─────────────────────────────────────────
        elapsed = time.time() - crawl_start
        if elapsed > args.max_runtime - 120:
            _safe_print(
                "\n[GGZY-BIZ] Runtime {:.0f}s approaching limit ({}s), "
                "stopping gracefully. {} processed. "
                "Next run will resume.".format(
                    elapsed, args.max_runtime, processed_count))
            sys.stdout.flush()
            stopped_early = True
            break

        m_id = row.get("M_ID", "")
        name = (row.get("NAME") or row.get("TITLE") or "").strip()
        pub_date = (row.get("TM1") or row.get("TM") or "").strip()

        _safe_print("[GGZY-BIZ] [{}/{}] {}...".format(idx, len(new_rows), name[:50]))
        sys.stdout.flush()

        # Fetch detail content
        content = _fetch_content(m_id)
        content_html = ""
        if content and isinstance(content, dict):
            content_html = content.get("Contents", "")

        # Build markdown
        md_content = _build_markdown(row, content_html)

        # Save markdown locally
        folder_name = _sanitize_filename(
            "{}_{}_{}".format(pub_date[:10] if pub_date else "nodate",
                              str(m_id)[:12], name[:40]),
            max_len=120)
        md_path = os.path.join(output_dir, f"{folder_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        _safe_print("[GGZY-BIZ]   Saved ({} chars)".format(len(md_content)))
        sys.stdout.flush()

        # Upload to KB
        if args.kb_id:
            try:
                _upload_to_kb(md_path, args.kb_id, args.tenant_id)
                _safe_print("[GGZY-BIZ]   Uploaded to KB {}".format(args.kb_id))
                sys.stdout.flush()
            except Exception as e:
                logging.error("KB upload failed: %s", e)
                _safe_print("[GGZY-BIZ]   Upload error: {}".format(e))
                sys.stdout.flush()

        processed_ids.add(str(m_id))
        processed_count += 1

        # Checkpoint every batch
        if processed_count % (_BATCH_PAGES * _PAGE_SIZE) == 0:
            state["last_page"] = state.get("last_page", 1)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)
            _safe_print("[GGZY-BIZ]   Checkpoint ({} processed)".format(processed_count))
            sys.stdout.flush()

        # Anti-crawling delay
        time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

    # ── Final state ────────────────────────────────────────────────────
    if not stopped_early:
        state["completed"] = True
        state["last_page"] = 1
    state["processed_ids"] = list(processed_ids)
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[GGZY-BIZ] Crawl complete — {} new record(s)".format(processed_count))
    if stopped_early:
        _safe_print("[GGZY-BIZ] Stopped early, will resume next run")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== GGZY-BIZ crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "ggzyfw_business_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
