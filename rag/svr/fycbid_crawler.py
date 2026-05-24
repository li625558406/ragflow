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
Crawler for www.fycbid.cn (福易采电子交易平台).

Site characteristics
────────────────────
  • Vue.js + Element UI SPA with hash routing.
  • All data is served via REST JSON APIs — no Playwright or browser needed.
  • Plain requests with proper headers work for everything.

API endpoints
─────────────
  Listing:  POST /fyc/fyc3/uip/mh/project/query
  Detail:   POST /fyc/fyc3/uip/mh/attachment/query
  Download: GET  https://v3.fycbid.cn/ebidding/api/base/file/withoutPermission/download?fileId=...

Filter
──────
  Bulletins published in the last 3 days (dateType=fbDate).

Time fields extracted from detail
─────────────────────────────────
  • 售标截止时间 (saleFileEndTime)
  • 提问截止时间 (clarifyEndTime)
  • 开标开始时间 (bidOpenTime)
  • 投标截止时间 (parsed from content HTML, fallback: bidOpenTime)

Attachments
───────────
  PDF/DOC/ZIP files downloaded from v3.fycbid.cn, ZIP archives are decompressed
  and uploaded as separate files linked to the parent article.

Checkpoint/resume
─────────────────
  Articles are processed in batches of 10.  After each batch markdown is
  uploaded to KB and state is saved.  The 3600s task timeout is handled
  via --max-runtime (default 3300s).

Usage
─────
    python fycbid_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.fycbid.cn/ \
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
import zipfile
from datetime import datetime, timedelta

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
_SITE_ROOT = "https://www.fycbid.cn"
_LISTING_API = "/fyc/fyc3/uip/mh/project/query"
_DETAIL_API = "/fyc/fyc3/uip/mh/attachment/query"
_DETAIL_URL_TEMPLATE = (
    "https://www.fycbid.cn/#/notice/detail-upgrade"
    "?bidId={bid_id}&bulletinId={bulletin_id}&noticeType=notice"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Content-Type": "application/json",
    "Referer": "https://www.fycbid.cn/",
}

_BATCH_SIZE = 10
_LISTING_PAGE_SIZE = 50

# Anti-crawling: gentle delay between requests
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 0.8


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
# Listing API
# ---------------------------------------------------------------------------

def _fetch_all_listings():
    """Fetch all bulletin listings from the last 3 days via API.

    Returns list of listing dicts with keys:
      bidId, bulletinId, bidName, bidOpenTime, bidType, city, province,
      clarifyEndTime, pushTime, saleFileBeginTime, saleFileEndTime,
      signBeginTime, signEndTime, title, purchaseModeName, tenderName, ...
    """
    end_date = datetime.now().strftime("%Y-%m-%d") + " 23:59:59"
    start_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d") + " 00:00:00"

    base_payload = {
        "site": "00",
        "bidName": "",
        "bidType": "",
        "dateType": "fbDate",
        "startDate": start_date,
        "endDate": end_date,
        "isProvince": "",
        "page": {"current": 1, "size": _LISTING_PAGE_SIZE},
        "address": "",
        "isEffective": 1,
        "purchaseMode": "",
        "title": "",
    }

    api_url = _SITE_ROOT + _LISTING_API
    all_records = []
    page = 1

    while True:
        payload = dict(base_payload)
        payload["page"] = {"current": page, "size": _LISTING_PAGE_SIZE}

        try:
            resp = requests.post(api_url, json=payload, headers=_DEFAULT_HEADERS,
                               timeout=30, proxies={"http": "", "https": ""})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error("Listing API page %d failed: %s", page, e)
            break

        if data.get("code") != "200" and data.get("code") != 200:
            logging.error("Listing API error: code=%s, msg=%s",
                        data.get("code"), data.get("msg"))
            break

        result = data.get("data", {})
        records = result.get("records", [])
        if not records:
            break

        all_records.extend(records)
        total_pages = result.get("pages", 0)

        if page >= total_pages:
            break

        page += 1
        _request_delay()

        if page > 500:
            break

    return all_records


# ---------------------------------------------------------------------------
# Detail API
# ---------------------------------------------------------------------------

def _fetch_detail(bulletin_id):
    """Fetch bulletin detail via attachment/query API.

    Returns dict with keys:
      content, title, bidName, bidOpenTime, clarifyEndTime,
      saleFileBeginTime, saleFileEndTime, pushTime, fileUrls, ...
    """
    api_url = _SITE_ROOT + _DETAIL_API
    payload = {"site": "00", "bulletinId": bulletin_id}

    try:
        resp = requests.post(api_url, json=payload, headers=_DEFAULT_HEADERS,
                           timeout=30, proxies={"http": "", "https": ""})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.warning("Detail API failed for %s: %s", bulletin_id, e)
        return None

    if data.get("code") not in ("200", 200):
        logging.warning("Detail API error for %s: code=%s, msg=%s",
                      bulletin_id, data.get("code"), data.get("msg"))
        return None

    return data.get("data", {})


def _extract_bid_end_time(content_html):
    """Parse 投标截止时间 from content HTML. Returns '' if not found."""
    if not content_html:
        return ""
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    patterns = [
        r"投标截止时间[：:]\s*(.{10,40}?)(?:\n|$)",
        r"投标文件递交[的截止]*时间[：:]\s*(.{10,40}?)(?:\n|$)",
        r"投标截止时间[：:]\s*(\d{4}[-年]\d{1,2}[-月]\d{1,2}[日]?\s*\d{1,2}:\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Attachment download & ZIP handling
# ---------------------------------------------------------------------------

def _download_attachment(file_url):
    """Download attachment file, return (bytes, filename) or (None, None)."""
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.fycbid.cn/"}
    try:
        resp = requests.get(file_url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        content = resp.content

        # Parse filename from Content-Disposition
        filename = ""
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename[^;=\n]*=(([\"']).*?\2|[^;\n]*)", cd)
        if m:
            raw = m.group(1).strip("\"'")
            try:
                filename = raw.encode("iso-8859-1").decode("gbk")
            except (UnicodeDecodeError, UnicodeEncodeError):
                try:
                    filename = raw.encode("iso-8859-1").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    filename = raw

        if not filename:
            filename = file_url.split("?")[0].split("/")[-1] or "attachment"
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

        return content, filename
    except Exception as e:
        logging.warning("Attachment download failed: %s — %s", file_url, e)
        return None, None


def _process_attachment_bytes(file_bytes, filename):
    """Process attachment bytes. If ZIP, decompress. Returns [(name, bytes)]."""
    if filename.lower().endswith(".zip"):
        files = []
        try:
            import io as io_mod
            z = zipfile.ZipFile(io_mod.BytesIO(file_bytes))
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner_name = os.path.basename(info.filename)
                if not inner_name:
                    continue
                inner_bytes = z.read(info)
                files.append((inner_name, inner_bytes))
            z.close()
            logging.info("ZIP '%s' extracted: %d files", filename, len(files))
            return files
        except (zipfile.BadZipFile, Exception) as e:
            logging.warning("ZIP extraction failed: %s", e)
            return [(filename, file_bytes)]
    else:
        return [(filename, file_bytes)]


def _save_attachment_file(content_bytes, filename, output_dir):
    """Save attachment bytes to disk, return filepath."""
    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    path = os.path.join(downloads_dir, filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(path):
        path = os.path.join(downloads_dir, "{}_{:d}{}".format(base, counter, ext))
        counter += 1
    with open(path, "wb") as f:
        f.write(content_bytes)
    return path


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

def _html_to_text(html_text):
    """Convert HTML content to clean text."""
    if not html_text:
        return ""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        text = html_text.strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


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
    """Process one batch: fetch detail → build markdown → attachments →
    save → upload → checkpoint.

    Returns: (articles_processed, should_stop_early)
    """
    md_parts = []
    batch_ids = []
    total_attachments = 0
    fail_count = 0

    for idx, item in enumerate(batch_items, 1):
        # ── Time-bounded check ──
        elapsed = time.time() - crawl_start
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print("\n[FYCBID] Runtime {:.0f}s approaching limit ({}s), "
                       "stopping early".format(elapsed, max_runtime))
            sys.stdout.flush()
            return len(batch_ids), True

        global_idx = (batch_num - 1) * _BATCH_SIZE + idx
        bid_name = item.get("bidName", "") or item.get("title", "无标题")
        _safe_print("[FYCBID] [{}/{}] {}".format(
            global_idx, total_new, bid_name[:70]))
        sys.stdout.flush()

        bulletin_id = item.get("bulletinId", "")
        bid_id = item.get("bidId", "")

        # Fetch detail
        detail = _fetch_detail(bulletin_id)
        _request_delay()

        # Extract content and time fields
        content_html = ""
        title = bid_name
        bid_open_time = ""
        clarify_end_time = ""
        sale_file_begin = ""
        sale_file_end = ""
        bid_end_time = ""
        push_time = ""
        file_urls = []
        if detail:
            content_html = detail.get("content", "") or ""
            title = detail.get("title") or detail.get("bidName") or bid_name
            bid_open_time = (detail.get("bidOpenTime") or "")[:19]
            clarify_end_time = (detail.get("clarifyEndTime") or "")[:19]
            sale_file_begin = (detail.get("saleFileBeginTime") or "")[:19]
            sale_file_end = (detail.get("saleFileEndTime") or "")[:19]
            push_time = (detail.get("pushTime") or "")[:19]
            # Parse 投标截止时间 from content
            bid_end_time = _extract_bid_end_time(content_html)
            if not bid_end_time:
                bid_end_time = bid_open_time  # often same as bidOpenTime
            # Parse attachment URLs
            file_urls_raw = detail.get("fileUrls", "")
            if isinstance(file_urls_raw, str) and file_urls_raw:
                try:
                    file_urls = json.loads(file_urls_raw)
                except json.JSONDecodeError:
                    pass
            elif isinstance(file_urls_raw, list):
                file_urls = file_urls_raw

        # Fallback to listing data
        if not bid_open_time:
            bid_open_time = (item.get("bidOpenTime") or "")[:19]
        if not clarify_end_time:
            clarify_end_time = (item.get("clarifyEndTime") or "")[:19]
        if not sale_file_begin:
            sale_file_begin = (item.get("saleFileBeginTime") or "")[:19]
        if not sale_file_end:
            sale_file_end = (item.get("saleFileEndTime") or "")[:19]
        if not push_time:
            push_time = (item.get("pushTime") or "")[:19]

        content_text = _html_to_text(content_html)
        if not content_text:
            content_text = "(未能获取详细内容)"
            fail_count += 1

        # Build markdown
        detail_url = _DETAIL_URL_TEMPLATE.format(
            bid_id=bid_id, bulletin_id=bulletin_id)
        city = item.get("city", "")
        province = item.get("province", "")
        purchase_mode = item.get("purchaseModeName", "")
        tender_name = item.get("tenderName", "")
        bid_type = item.get("bidType", "")
        bid_code = item.get("bidCode", "")

        md_lines = [
            "# {}".format(title),
            "",
            "| 字段 | 内容 |",
            "|------|------|",
            "| **项目编号** | {} |".format(bid_code),
            "| **招标人** | {} |".format(tender_name),
            "| **采购方式** | {} |".format(purchase_mode),
            "| **项目类型** | {} |".format(bid_type),
            "| **地区** | {} {} |".format(province, city),
            "| **发布时间** | {} |".format(push_time),
            "| **售标截止时间** | {} |".format(sale_file_end),
            "| **提问截止时间** | {} |".format(clarify_end_time),
            "| **开标开始时间** | {} |".format(bid_open_time),
            "| **投标截止时间** | {} |".format(bid_end_time),
            "",
            "**详情页:** {}".format(detail_url),
            "",
            "---",
            "",
            content_text,
            "",
        ]

        # ── Attachment processing ──
        if file_urls:
            md_lines.append("---")
            md_lines.append("## 附件列表")
            md_lines.append("")

            for att_idx, att in enumerate(file_urls, 1):
                att_url = att.get("fileUrl", "")
                att_name = att.get("fileName", "附件{}".format(att_idx))

                md_lines.append("### {}. {}".format(att_idx, att_name))
                md_lines.append("**下载链接:** {}".format(att_url))
                md_lines.append("")

                _safe_print("[FYCBID]     Downloading: {}".format(att_name[:60]))
                sys.stdout.flush()

                file_bytes, filename = _download_attachment(att_url)
                if file_bytes:
                    processed_files = _process_attachment_bytes(
                        file_bytes, filename)
                    for pf_name, pf_bytes in processed_files:
                        saved_path = _save_attachment_file(
                            pf_bytes, pf_name, output_dir)
                        md_lines.append("**附件文件:** {}".format(pf_name))
                        md_lines.append("")

                        try:
                            _upload_to_kb(saved_path, kb_id, tenant_id,
                                        parser_id="naive")
                            total_attachments += 1
                        except Exception as e:
                            logging.error("Upload attachment '%s': %s", pf_name, e)
                else:
                    md_lines.append("*(下载失败)*")
                    md_lines.append("")

        md_part = "\n".join(md_lines)
        md_parts.append(md_part)
        batch_ids.append(bulletin_id)

        # Save per-article markdown for crash safety
        _save_article_markdown(md_part, output_dir, bulletin_id)

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
                _safe_print("[FYCBID] Batch {} upload failed: {}".format(
                    batch_num, e))
                logging.error("Upload batch %d: %s", batch_num, e)

        _safe_print("[FYCBID] Batch {} uploaded: {} articles, {} attachments "
                   "(fail: {})".format(batch_num, len(md_parts),
                                       total_attachments, fail_count))
        sys.stdout.flush()

    return len(batch_ids), False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="www.fycbid.cn (福易采电子交易平台) crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://www.fycbid.cn/",
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

    start_str = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    end_str = datetime.now().strftime("%Y-%m-%d")

    _safe_print("\n" + "=" * 60)
    _safe_print("[FYCBID] www.fycbid.cn (福易采电子交易平台) crawler")
    _safe_print("[FYCBID] Date filter: {} ~ {} (last 3 days)".format(
        start_str, end_str))
    _safe_print("[FYCBID] KB: {} | max-runtime: {}s".format(
        args.kb_id, args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== fycbid crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[FYCBID] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    crawl_start = time.time()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False}
    processed_ids = set(state.get("processed_ids", []))

    if state.get("completed"):
        _safe_print("[FYCBID] Already completed. Use --full to re-crawl.\n")
        sys.stdout.flush()
        return

    _safe_print("[FYCBID] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # ====================================================================
    # Step 1: Fetch all listings
    # ====================================================================
    _safe_print("[FYCBID] Step 1/2: Fetching listings (last 3 days)...")
    sys.stdout.flush()

    all_items = _fetch_all_listings()
    _safe_print("[FYCBID]   Total: {} items\n".format(len(all_items)))
    sys.stdout.flush()

    if not all_items:
        _safe_print("[FYCBID] No bulletins found.\n")
        sys.stdout.flush()
        return

    # -- Filter already processed -------------------------------------------
    new_items = [it for it in all_items
                 if it.get("bulletinId") and it["bulletinId"] not in processed_ids]
    _safe_print("[FYCBID]   {} new (skipped {} already processed)\n".format(
        len(new_items), len(all_items) - len(new_items)))
    sys.stdout.flush()

    if not new_items:
        _safe_print("[FYCBID] Nothing new.\n")
        sys.stdout.flush()
        return

    # ====================================================================
    # Step 2: Process in batches
    # ====================================================================
    _safe_print("[FYCBID] Step 2/2: Processing {} items in batches of {}...\n".format(
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
    _safe_print("[FYCBID] Done: {} articles processed{}".format(
        total_processed, " (stopped early)" if stopped_early else ""))
    _safe_print("[FYCBID] State: {}".format(
        "completed" if not stopped_early else "partial"))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== fycbid crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "fycbid_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
