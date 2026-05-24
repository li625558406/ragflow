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
Crawler for easy-prt.com (工采通电子招投标交易平台) — bid-price + enquiry sections.

Covers two sections:
  1. 竞价公告 (/bid-price) — last 90 days, ~163 items
  2. 正在询价 (/enquiry) — bidStatus=02, ~40 items

All APIs use SM4-ECB encryption.  Communication is pure HTTP (no Playwright
required — urllib with SM4 encrypted payloads is sufficient).

Detail pages return project metadata, HTML content, and file annexes.
Attachments (doc/pdf/zip) are downloaded directly from the files.easy-prt.com
CDN; ZIP archives are extracted before upload.

Checkpoint/resume: articles processed in batches of 10, state saved after each
batch.  A 3600 s time-bounded check stops processing early on long runs.

Usage:
    python easy_prt_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://easy-prt.com/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import io
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

try:
    from gmssl.sm4 import CryptSM4, SM4_ENCRYPT, SM4_DECRYPT
except ImportError:
    logging.error("gmssl package is required. Run: pip install gmssl")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_BASE = "https://easy-prt.com/gykjbid"
_SM4_KEY = "90bdd291004611ef87fc52540023e781"

_SITE_ROOT = "https://easy-prt.com"

# ---- Bid-price (竞价公告) ----
_SECTION_BIDPRICE = "竞价公告"
_BIDPRICE_LIST_PATH = "/gykj-bidding/bidding/portal/project/list"
_BIDPRICE_DETAIL_PATH = "/gykj-bidding/bidding/portal/project/detail"
_BIDPRICE_ANNOUNCE_PATH = "/gykj-bidding/bidding/portal/project/announcement/list"

# ---- Enquiry (正在询价) ----
_SECTION_ENQUIRY = "正在询价"
_ENQUIRY_LIST_PATH = "/gykj-bid/bid/portal/getProjectList"
_ENQUIRY_DETAIL_PATH = "/gykj-bid/bid/portal/getProjectDetail"
_ENQUIRY_CONTENT_PATH = "/gykj-bid/bid/portal/getProjectContent"
_ENQUIRY_ANNOUNCE_PATH = "/gykj-bid/bid/portal/getProjectAnnouncementList"

# Bid-price listing filter: last 90 days
_IN_RECENT_DAYS = 90

# Enquiry listing filter: "正在询价" status
_ENQUIRY_BID_STATUS = "02"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "Content-Type": "application/json",
    "X-Platform-Id": "gct",
    "User-Agent": _USER_AGENT,
}

_GET_HEADERS = {
    "X-Platform-Id": "gct",
    "User-Agent": _USER_AGENT,
}

# Anti-crawling: random delays between requests (seconds)
_REQUEST_DELAY_MIN = 1.5
_REQUEST_DELAY_MAX = 3.0

# Batch checkpoint
BATCH_SIZE = 10

# Max runtime before graceful stop (seconds)
_MAX_RUNTIME_DEFAULT = 3300

# File types for attachment handling
_ARCHIVE_EXTS = {".zip", ".rar"}

# ---------------------------------------------------------------------------
# SM4 helpers
# ---------------------------------------------------------------------------


def _sm4_encrypt(data):
    """SM4-ECB encrypt dict -> hex string."""
    cipher = CryptSM4()
    cipher.set_key(bytes.fromhex(_SM4_KEY), SM4_ENCRYPT)
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return cipher.crypt_ecb(payload).hex()


def _sm4_decrypt(hex_data):
    """SM4-ECB decrypt hex string -> dict."""
    cipher = CryptSM4()
    cipher.set_key(bytes.fromhex(_SM4_KEY), SM4_DECRYPT)
    decrypted = cipher.crypt_ecb(bytes.fromhex(hex_data)).decode("utf-8")
    return json.loads(decrypted)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _request_delay():
    """Random delay between requests to avoid rate limiting."""
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _api_post(path, params, retries=3):
    """POST with SM4-encrypted body.  Returns decoded dict or None."""
    enc = _sm4_encrypt(params)
    # POST body is a JSON string wrapping the hex (matching browser behavior)
    body = json.dumps(enc).encode("utf-8")
    url = _API_BASE + path

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=_HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            stripped = raw.strip().strip('"')
            return _sm4_decrypt(stripped)
        except Exception as e:
            logging.warning("POST %s attempt %d/%d: %s", path, attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def _api_get(path, params, retries=3):
    """GET with SM4-encrypted query param.  Returns decoded dict or None."""
    enc = _sm4_encrypt(params)
    url = "{}?encryptParams={}".format(_API_BASE + path, enc)

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=_GET_HEADERS, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            stripped = raw.strip().strip('"')
            return _sm4_decrypt(stripped)
        except Exception as e:
            logging.warning("GET %s attempt %d/%d: %s", path, attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None


def _download_file(url, retries=2):
    """Download a binary file from a direct URL.  Returns (content_bytes, filename) or (None, None)."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = resp.read()
            filename = url.rstrip("/").split("/")[-1]
            if "?" in filename:
                filename = filename.split("?")[0]
            return content, filename
        except Exception as e:
            logging.warning("Download %s attempt %d/%d: %s", url, attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return None, None


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def _html_to_text(html):
    """Convert HTML content to plain text via BeautifulSoup."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _build_bidprice_markdown(item, detail, announcements):
    """Build markdown for a bid-price project."""
    name = detail.get("projectName") or item.get("projectName", "未命名")
    item_id = item.get("id", "")
    lines = [
        "# {}".format(name),
        "",
        "**栏目:** {}".format(_SECTION_BIDPRICE),
        "**链接:** {}/project-bidprice?id={}&type=01".format(_SITE_ROOT, item_id),
        "",
    ]

    meta_fields = [
        ("项目编号", detail.get("projectNum")),
        ("招标类型", detail.get("bidTypeText")),
        ("行业分类", detail.get("industrySegmentationText")),
        ("省/市/区", "{}/{}/{}".format(
            detail.get("provinceName", ""), detail.get("cityName", ""),
            detail.get("areaName", ""))),
        ("采购单位", detail.get("purchaseUnit") or detail.get("tendereeName")),
        ("招标代理", detail.get("enterpriseName") or detail.get("tendereeName")),
        ("联系人", detail.get("tendereeContactName")),
        ("联系电话", detail.get("tendereeContactPhone")),
        ("预算", "{} {}".format(detail.get("budget", ""),
         detail.get("startPriceUnit", "")) if detail.get("budget") else
         "{} {}".format(detail.get("startPrice", ""),
                         detail.get("startPriceUnit", ""))),
        ("报价方式", detail.get("quoteTypeText")),
        ("成交方式", detail.get("winBidTypeText")),
        ("资格审核", detail.get("qualificationAuditTypeText")),
        ("报名开始", detail.get("applicationBeginTime")),
        ("报名结束", detail.get("applicationEndTime")),
        ("竞价开始", detail.get("freeBiddingBeginTime")),
        ("竞价结束", detail.get("freeBiddingEndTime")),
        ("保证金", "{} 元".format(detail.get("bondAmount"))
         if detail.get("bondAmount") else ""),
        ("报名费", "{} 元".format(detail.get("applicationFee"))
         if detail.get("applicationFee") else ""),
    ]
    for label, value in meta_fields:
        if value:
            lines.append("- **{}:** {}".format(label, value))

    lines.append("")

    if detail.get("biddingRequirement"):
        lines.append("## 竞价要求")
        lines.append("")
        lines.append(str(detail["biddingRequirement"]))
        lines.append("")
    if detail.get("qualificationRequire") and detail.get("qualificationRequire") != "详见竞价文件":
        lines.append("## 资格要求")
        lines.append("")
        lines.append(str(detail["qualificationRequire"]))
        lines.append("")
    if detail.get("bidDesc"):
        lines.append("## 项目描述")
        lines.append("")
        lines.append(str(detail["bidDesc"]))
        lines.append("")
    if detail.get("additionHtml"):
        text = _html_to_text(detail["additionHtml"])
        if text.strip():
            lines.append("## 补充说明")
            lines.append("")
            lines.append(text)
            lines.append("")
    if announcements:
        lines.append("## 公告列表")
        lines.append("")
        for ann in announcements:
            lines.append("- **{}** ({}) — {}".format(
                ann.get("announcementTitle", ""),
                ann.get("announcementTypeText", ""),
                ann.get("releaseTime", ""),
            ))
        lines.append("")

    return "\n".join(lines)


def _build_enquiry_markdown(item, detail, content_html, announcements):
    """Build markdown for an enquiry project."""
    name = detail.get("projectName") or item.get("projectName", "未命名")
    item_id = item.get("id", "")
    lines = [
        "# {}".format(name),
        "",
        "**栏目:** {}".format(_SECTION_ENQUIRY),
        "**链接:** {}/project?id={}&type=01".format(_SITE_ROOT, item_id),
        "",
    ]

    meta_fields = [
        ("项目编号", detail.get("projectNum")),
        ("包号", detail.get("packageNum")),
        ("行业分类", detail.get("industrySegmentation")),
        ("省/市/区", "{}/{}/{}".format(
            detail.get("provinceName", ""), detail.get("cityName", ""),
            detail.get("areaName", ""))),
        ("采购单位", detail.get("tendereeName")),
        ("代理机构", detail.get("agencyName")),
        ("联系人", detail.get("tendereeContactName")),
        ("联系电话", detail.get("tendereeContactPhone")),
        ("预算", detail.get("budget")),
        ("包预算", detail.get("packageBudget")),
        ("招标文件获取开始", detail.get("bidSaleStartTime")),
        ("招标文件获取结束", detail.get("bidSaleEndTime")),
        ("投标开始", detail.get("bidStartTime")),
        ("投标结束", detail.get("bidEndTime")),
        ("开标时间", detail.get("bidOpenTime")),
        ("发布时间", detail.get("releaseTime")),
        ("平台服务费", "{} 元".format(detail.get("platformFee"))
         if detail.get("platformFee") else ""),
    ]
    for label, value in meta_fields:
        if value:
            lines.append("- **{}:** {}".format(label, value))

    lines.append("")

    if detail.get("preQualRequire"):
        lines.append("## 资格要求")
        lines.append("")
        lines.append(str(detail["preQualRequire"]))
        lines.append("")

    if content_html:
        text = _html_to_text(content_html)
        if text.strip():
            lines.append("## 采购内容")
            lines.append("")
            lines.append(text)
            lines.append("")

    if announcements:
        lines.append("## 公告列表")
        lines.append("")
        for ann in announcements:
            lines.append("- **{}** ({}) — {}".format(
                ann.get("announcementTitle", ""),
                ann.get("announcementTypeText", ""),
                ann.get("releaseTime", ""),
            ))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Listing fetchers
# ---------------------------------------------------------------------------


def _fetch_bidprice_listing(page_index=1, page_size=10):
    """Fetch one page of bid-price listing."""
    params = {
        "announcementType": "",
        "areaCode": "",
        "biddingStatus": "",
        "cityCode": "",
        "inRecentDays": _IN_RECENT_DAYS,
        "industrySegmentation": "",
        "keyword": "",
        "pageIndex": page_index,
        "pageSize": page_size,
        "provinceCode": "",
        "releaseBeginTime": "",
        "releaseEndTime": "",
        "t": int(time.time() * 1000),
    }
    return _api_post(_BIDPRICE_LIST_PATH, params)


def _fetch_enquiry_listing(page_no=1, page_size=10):
    """Fetch one page of enquiry listing (正在询价)."""
    params = {
        "announcementType": "",
        "areaCode": "",
        "bidStatus": _ENQUIRY_BID_STATUS,
        "cityCode": "",
        "industryCategories": "good_service",
        "industrySegmentation": "",
        "industrySubCategories": "",
        "keyword": "",
        "pageNo": page_no,
        "pageSize": page_size,
        "provinceCode": "",
        "quotationSubmitForm": "",
        "releaseBeginTime": "",
        "releaseEndTime": "",
        "showTop": 0,
        "tenderType": "06",
        "t": int(time.time() * 1000),
    }
    return _api_post(_ENQUIRY_LIST_PATH, params)


def _gather_all_listings(fetch_fn, page_size=10, max_records=0):
    """Fetch all pages for a listing and return flat list of records.

    If *max_records* > 0, stop early once that many records have been
    collected.  Combined with dedup this means subsequent runs only
    fetch (and pay for) the newest items.
    """
    all_items = []
    page = 1
    while True:
        result = fetch_fn(page, page_size)
        if not result or not result.get("success"):
            logging.error("Listing page %d failed: %s", page,
                          result.get("message") if result else "no response")
            break
        res = result.get("result", {})
        records = res.get("records", [])
        if not records:
            break
        all_items.extend(records)
        total = res.get("total", 0)
        if len(all_items) >= total:
            break
        if max_records > 0 and len(all_items) >= max_records:
            all_items = all_items[:max_records]
            break
        page += 1
        _request_delay()
    return all_items


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
    return {
        "processed_bidprice": [],
        "processed_enquiry": [],
        "completed_bidprice": False,
        "completed_enquiry": False,
    }


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


def _save_markdown(content, output_dir, item_id):
    d = os.path.join(output_dir, "articles")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "{}.md".format(item_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _save_attachment(content, output_dir, filename):
    d = os.path.join(output_dir, "attachments")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _extract_zip(content, output_dir, project_id):
    """Extract a ZIP archive, return list of extracted file paths."""
    extracted = []
    d = os.path.join(output_dir, "attachments", project_id)
    os.makedirs(d, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.endswith("/") or "__MACOSX" in name:
                    continue
                safe_name = os.path.basename(name)
                if not safe_name:
                    continue
                out_path = os.path.join(d, safe_name)
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except Exception as e:
        logging.warning("ZIP extraction failed: %s", e)
    return extracted


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------


def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="general"):
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
            DocumentService.update_by_id(did, {"parser_id": parser_id})
        except Exception:
            pass
        try:
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Section processors
# ---------------------------------------------------------------------------


def _process_bidprice(output_dir, kb_id, tenant_id, state, max_runtime, start_time,
                      max_articles=0):
    """Process 竞价公告 section with batching and timeout.

    Returns updated set of processed IDs.
    """
    processed_ids = set(state.get("processed_bidprice", []))
    if state.get("completed_bidprice"):
        _safe_print("[EASYPRT]  竞价公告 already completed, skip.\n")
        return processed_ids

    _safe_print("[EASYPRT]  竞价公告: fetching listing...")
    sys.stdout.flush()

    all_items = _gather_all_listings(_fetch_bidprice_listing, max_records=max_articles)
    _safe_print("[EASYPRT]  竞价公告: {} items total from listing".format(len(all_items)))
    sys.stdout.flush()

    new_items = [a for a in all_items if a.get("id") not in processed_ids]
    _safe_print("[EASYPRT]  竞价公告: {} new ({} skipped)".format(
        len(new_items), len(all_items) - len(new_items)))
    sys.stdout.flush()

    if not new_items:
        state["completed_bidprice"] = True
        _save_state(output_dir, state)
        return processed_ids

    total = len(new_items)
    success_count = 0
    batch_num = 0
    stopped_early = False

    for batch_start in range(0, total, BATCH_SIZE):
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print(
                "\n[EASYPRT] Runtime {:.0f}s, stopping gracefully "
                "({} / {} done).".format(elapsed, success_count, total))
            sys.stdout.flush()
            stopped_early = True
            break

        batch = new_items[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        batch_files = []
        batch_ids = []

        for idx, item in enumerate(batch, 1):
            global_idx = batch_start + idx
            item_id = item.get("id", "")
            name = item.get("projectName", "")[:50]
            _safe_print("[EASYPRT]   [{}/{}] {}".format(global_idx, total, name))
            sys.stdout.flush()

            # Fetch detail
            detail = _api_get(_BIDPRICE_DETAIL_PATH, {
                "id": item_id,
                "t": int(time.time() * 1000),
            })
            if not detail or not detail.get("success"):
                _safe_print("      -> detail fetch failed")
                sys.stdout.flush()
                continue

            detail_data = detail.get("result", {}) or {}

            # Fetch announcements
            ann_result = _api_get(_BIDPRICE_ANNOUNCE_PATH, {
                "projectId": item_id,
                "t": int(time.time() * 1000),
            })
            announcements = []
            if ann_result and ann_result.get("success"):
                announcements = ann_result.get("result", []) or []

            # Download attachments
            annexes = detail_data.get("annexes", []) or []
            attachment_paths = []
            for annex in annexes:
                file_url = annex.get("url", "")
                if not file_url:
                    continue
                fn = annex.get("fileName", "")
                _safe_print("        downloading: {}".format(fn))
                sys.stdout.flush()

                content, dl_filename = _download_file(file_url)
                if not content:
                    continue
                if not fn:
                    fn = dl_filename or "untitled"

                ext = os.path.splitext(fn)[1].lower()
                if ext in _ARCHIVE_EXTS:
                    extracted = _extract_zip(content, output_dir, item_id)
                    attachment_paths.extend(extracted)
                    _safe_print("        extracted {} files from {}".format(
                        len(extracted), fn))
                    sys.stdout.flush()
                else:
                    saved_path = _save_attachment(content, output_dir, fn)
                    attachment_paths.append(saved_path)

                _request_delay()

            # Build markdown
            md = _build_bidprice_markdown(item, detail_data, announcements)
            if attachment_paths:
                md += "\n## 附件\n\n"
                for ap in attachment_paths:
                    md += "- {}\n".format(os.path.basename(ap))

            _save_markdown(md, output_dir, item_id)
            batch_files.append(os.path.join(
                output_dir, "articles", "{}.md".format(item_id)))
            batch_ids.append(item_id)
            success_count += 1
            _request_delay()

        # Checkpoint
        if batch_ids:
            processed_ids.update(batch_ids)
            state["processed_bidprice"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                for fp in batch_files:
                    if os.path.exists(fp):
                        try:
                            _upload_to_kb(fp, kb_id, tenant_id)
                        except Exception as e:
                            logging.error("Upload %s: %s", fp, e)
                for item in batch:
                    att_dir = os.path.join(output_dir, "attachments",
                                           item.get("id", ""))
                    if os.path.isdir(att_dir):
                        for root, _, files in os.walk(att_dir):
                            for f in files:
                                fp = os.path.join(root, f)
                                try:
                                    _upload_to_kb(fp, kb_id, tenant_id)
                                except Exception as e:
                                    logging.error("Upload att %s: %s", fp, e)

            _safe_print("[EASYPRT]   batch {} done ({}/{} total)\n".format(
                batch_num, success_count, total))
            sys.stdout.flush()

    if not stopped_early:
        state["completed_bidprice"] = True
        _save_state(output_dir, state)

    return processed_ids


def _process_enquiry(output_dir, kb_id, tenant_id, state, max_runtime, start_time,
                     max_articles=0):
    """Process 正在询价 section with batching and timeout.

    Returns updated set of processed IDs.
    """
    processed_ids = set(state.get("processed_enquiry", []))
    if state.get("completed_enquiry"):
        _safe_print("[EASYPRT]  正在询价 already completed, skip.\n")
        return processed_ids

    _safe_print("[EASYPRT]  正在询价: fetching listing...")
    sys.stdout.flush()

    all_items = _gather_all_listings(_fetch_enquiry_listing, max_records=max_articles)
    _safe_print("[EASYPRT]  正在询价: {} items total from listing".format(len(all_items)))
    sys.stdout.flush()

    new_items = [a for a in all_items if a.get("id") not in processed_ids]
    _safe_print("[EASYPRT]  正在询价: {} new ({} skipped)".format(
        len(new_items), len(all_items) - len(new_items)))
    sys.stdout.flush()

    if not new_items:
        state["completed_enquiry"] = True
        _save_state(output_dir, state)
        return processed_ids

    total = len(new_items)
    success_count = 0
    batch_num = 0
    stopped_early = False

    for batch_start in range(0, total, BATCH_SIZE):
        elapsed = time.time() - start_time
        if elapsed > max_runtime - 120:
            _safe_print(
                "\n[EASYPRT] Runtime {:.0f}s, stopping gracefully "
                "({} / {} done).".format(elapsed, success_count, total))
            sys.stdout.flush()
            stopped_early = True
            break

        batch = new_items[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        batch_files = []
        batch_ids = []

        for idx, item in enumerate(batch, 1):
            global_idx = batch_start + idx
            item_id = item.get("id", "")
            name = item.get("projectName", "")[:50]
            _safe_print("[EASYPRT]   [{}/{}] {}".format(global_idx, total, name))
            sys.stdout.flush()

            # Fetch detail
            detail = _api_get(_ENQUIRY_DETAIL_PATH, {
                "projectPackageId": item_id,
            })
            if not detail or not detail.get("success"):
                _safe_print("      -> detail fetch failed")
                sys.stdout.flush()
                continue

            detail_data = detail.get("result", {}) or {}

            # Fetch content HTML
            content_html = ""
            content_annexes = []
            ann_list = item.get("announcementList", []) or []
            content_id = ann_list[0].get("contentId") if ann_list else None
            if content_id:
                content_result = _api_get(_ENQUIRY_CONTENT_PATH, {
                    "contentId": content_id,
                    "t": int(time.time() * 1000),
                })
                if content_result and content_result.get("success"):
                    content_data = content_result.get("result", {}) or {}
                    content_html = content_data.get("content", "") or ""
                    content_annexes = content_data.get("annexes", []) or []

            # Fetch announcements
            ann_result = _api_get(_ENQUIRY_ANNOUNCE_PATH, {
                "projectPackageId": item_id,
                "t": int(time.time() * 1000),
            })
            announcements = []
            if ann_result and ann_result.get("success"):
                announcements = ann_result.get("result", []) or []

            # Collect attachments from multiple sources
            pre_qual_annexes = detail_data.get("preQualAnnexes") or []
            all_annexes = list(pre_qual_annexes)
            for ann in announcements:
                all_annexes.extend(ann.get("annexes", []) or [])
            all_annexes.extend(content_annexes)

            attachment_paths = []
            seen_urls = set()
            for annex in all_annexes:
                file_url = annex.get("url", "") or annex.get("fileUrl", "")
                if not file_url or file_url in seen_urls:
                    continue
                seen_urls.add(file_url)
                fn = annex.get("fileName", "") or annex.get("name", "")
                _safe_print("        downloading: {}".format(fn))
                sys.stdout.flush()

                content, dl_filename = _download_file(file_url)
                if not content:
                    continue
                if not fn:
                    fn = dl_filename or "untitled"

                ext = os.path.splitext(fn)[1].lower()
                if ext in _ARCHIVE_EXTS:
                    extracted = _extract_zip(content, output_dir, item_id)
                    attachment_paths.extend(extracted)
                    _safe_print("        extracted {} files from {}".format(
                        len(extracted), fn))
                    sys.stdout.flush()
                else:
                    saved_path = _save_attachment(content, output_dir, fn)
                    attachment_paths.append(saved_path)

                _request_delay()

            # Build markdown
            md = _build_enquiry_markdown(item, detail_data, content_html,
                                         announcements)
            if attachment_paths:
                md += "\n## 附件\n\n"
                for ap in attachment_paths:
                    md += "- {}\n".format(os.path.basename(ap))

            _save_markdown(md, output_dir, item_id)
            batch_files.append(os.path.join(
                output_dir, "articles", "{}.md".format(item_id)))
            batch_ids.append(item_id)
            success_count += 1
            _request_delay()

        # Checkpoint
        if batch_ids:
            processed_ids.update(batch_ids)
            state["processed_enquiry"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                for fp in batch_files:
                    if os.path.exists(fp):
                        try:
                            _upload_to_kb(fp, kb_id, tenant_id)
                        except Exception as e:
                            logging.error("Upload %s: %s", fp, e)
                for item in batch:
                    att_dir = os.path.join(output_dir, "attachments",
                                           item.get("id", ""))
                    if os.path.isdir(att_dir):
                        for root, _, files in os.walk(att_dir):
                            for f in files:
                                fp = os.path.join(root, f)
                                try:
                                    _upload_to_kb(fp, kb_id, tenant_id)
                                except Exception as e:
                                    logging.error("Upload att %s: %s", fp, e)

            _safe_print("[EASYPRT]   batch {} done ({}/{} total)\n".format(
                batch_num, success_count, total))
            sys.stdout.flush()

    if not stopped_early:
        state["completed_enquiry"] = True
        _save_state(output_dir, state)

    return processed_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(
        description="easy-prt.com crawler — bid-price + enquiry sections"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url", default="https://easy-prt.com/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--section", default=None,
                   help="Comma-separated sections: '竞价公告,正在询价' (default: both)")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop (default: 3300)")
    p.add_argument("--max-articles", type=int, default=500,
                   help="Max records per section (0=unlimited, default: 500)")
    for opt in ("--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


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

    _safe_print("\n" + "=" * 60)
    _safe_print("[EASYPRT] easy-prt.com crawler — 竞价公告 + 正在询价")
    _safe_print("[EASYPRT] KB: {}".format(args.kb_id))
    _safe_print("[EASYPRT] Task: {}".format(args.task_name))
    _safe_print("[EASYPRT] Max articles: {}".format(args.max_articles))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== EASYPRT crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[EASYPRT] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # Determine which sections to run
    if args.section:
        section_names = [s.strip() for s in args.section.split(",")]
    else:
        section_names = [_SECTION_BIDPRICE, _SECTION_ENQUIRY]

    # State
    state = _load_state(output_dir) if not args.full else {
        "processed_bidprice": [], "processed_enquiry": [],
        "completed_bidprice": False, "completed_enquiry": False,
    }

    start_time = time.time()
    max_runtime = args.max_runtime

    # --- 竞价公告 ---
    if _SECTION_BIDPRICE in section_names:
        _safe_print("[EASYPRT] === Section: {} ===".format(_SECTION_BIDPRICE))
        sys.stdout.flush()
        try:
            _process_bidprice(output_dir, args.kb_id, args.tenant_id,
                              state, max_runtime, start_time,
                              max_articles=args.max_articles)
        except Exception as e:
            logging.exception("BidPrice section failed: %s", e)
            _safe_print("[EASYPRT] ERROR in 竞价公告: {}".format(e))
            sys.stdout.flush()

    # --- 正在询价 ---
    if _SECTION_ENQUIRY in section_names:
        _safe_print("\n[EASYPRT] === Section: {} ===".format(_SECTION_ENQUIRY))
        sys.stdout.flush()
        try:
            _process_enquiry(output_dir, args.kb_id, args.tenant_id,
                             state, max_runtime, start_time,
                             max_articles=args.max_articles)
        except Exception as e:
            logging.exception("Enquiry section failed: %s", e)
            _safe_print("[EASYPRT] ERROR in 正在询价: {}".format(e))
            sys.stdout.flush()

    _safe_print("\n" + "=" * 60)
    _safe_print("[EASYPRT] Crawl complete.")
    _safe_print("[EASYPRT] 竞价公告: {} processed".format(
        len(state.get("processed_bidprice", []))))
    _safe_print("[EASYPRT] 正在询价: {} processed".format(
        len(state.get("processed_enquiry", []))))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== EASYPRT crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "easy_prt_bidding_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
