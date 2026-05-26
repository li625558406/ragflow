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
Crawler for fujian.etrading.cn (福建电子交易平台) — 采购公告 active bidding.

Site uses an ElasticSearch search API for listings and static HTML pages for
detail content.  No Playwright needed — plain requests works for everything.

Filter logic:
  - categorynum = 002003001 (采购公告)
  - youxiaodate > today  (equivalent to "正在报名" — the site JS computes
    "正在报名" client-side from this exact condition)

Checkpoint/resume: articles are processed in batches of 10.  After each batch
markdown is saved to KB and state is persisted.  The 3600s task timeout is
handled via --max-runtime (default 3300s).

Usage:
    python etrading_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://fujian.etrading.cn/ \
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
_SITE_ROOT = "https://fujian.etrading.cn"
_LISTING_API = "/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew"
_SITE_GUID = "0c486005-4998-475c-8147-891741aeefb3"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_BATCH_SIZE = 10

# Anti-crawling: gentle delay between requests (site has zero anti-crawling)
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
# API: listing (ES search)
# ---------------------------------------------------------------------------

def _fetch_all_listings():
    """Fetch all active bidding articles via the ES search API.

    Filters: categorynum=002003001 AND youxiaodate > now.

    Returns list of article dicts with fields:
      infoid, title, categorynum, city, youxiaodate, webdate, infodate,
      linkurl, content (truncated)
    """
    today_str = datetime.now().strftime("%Y-%m-%d 00:00:00")
    # Use a far-future end date to include all upcoming
    end_str = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d 23:59:59")

    payload_template = {
        "pn": 0,
        "rn": 20,
        "condition": [{
            "equal": "002003001",
            "fieldName": "categorynum",
            "isLike": "true",
            "likeType": "2",
        }],
        "time": [{
            "fieldName": "youxiaodate",
            "startTime": today_str,
            "endTime": end_str,
        }],
    }

    api_url = _SITE_ROOT + _LISTING_API
    headers = {
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json; charset=utf-8",
    }

    all_records = []
    page_size = 20
    page = 0

    while True:
        payload = dict(payload_template)
        payload["pn"] = page * page_size
        payload["rn"] = page_size

        try:
            resp = requests.post(api_url, json=payload, headers=headers,
                               timeout=30, proxies={"http": "", "https": ""})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error("Listing API page %d failed: %s", page, e)
            break

        result = data.get("result", data)
        records = result.get("records", [])
        total = result.get("totalcount", 0)

        if not records:
            break

        all_records.extend(records)
        page += 1

        # Safety limit — should never hit this
        if page > 50:
            break

    return all_records


# ---------------------------------------------------------------------------
# Detail page: static HTML
# ---------------------------------------------------------------------------

def _fetch_detail_page(linkurl):
    """Fetch the static HTML detail page for an article.

    Returns (content_text, attachments_list).
    attachments_list: [{"name": str, "url": str}, ...]
    """
    url = _SITE_ROOT + linkurl
    headers = {"User-Agent": _USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=30,
                          proxies={"http": "", "https": ""})
        resp.raise_for_status()
        # The site uses GBK-like encoding sometimes
        if resp.encoding and resp.encoding.lower() != "utf-8":
            resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logging.warning("Detail page fetch failed for %s: %s", linkurl, e)
        return "", []

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Extract content ──
    # Try common content containers
    content_text = ""
    for sel in ["#infoContent", "#content", ".info-content", ".article-content",
                ".main-content", "#main"]:
        el = soup.select_one(sel)
        if el:
            content_text = el.get_text(separator="\n", strip=True)
            break

    # Fallback: use entire body minus headers/nav
    if not content_text:
        # Remove script, style, nav, header, footer
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        body = soup.find("body")
        if body:
            content_text = body.get_text(separator="\n", strip=True)
        else:
            content_text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    content_text = re.sub(r"\n{3,}", "\n\n", content_text)

    # ── Extract attachments ──
    attachments = []
    attach_div = soup.select_one("#attach, .annex-box")
    if attach_div:
        for a in attach_div.find_all("a", href=True):
            att_url = a["href"]
            att_name = a.get("title") or a.get_text(strip=True)
            if not att_name:
                att_name = os.path.basename(att_url.split("?")[0])
            # Make absolute
            if att_url.startswith("/"):
                att_url = _SITE_ROOT + att_url
            elif not att_url.startswith("http"):
                att_url = _SITE_ROOT + "/" + att_url.lstrip("/")
            attachments.append({"name": att_name, "url": att_url})

    return content_text, attachments


# ---------------------------------------------------------------------------
# Attachment download & ZIP handling
# ---------------------------------------------------------------------------

def _download_attachment(url):
    """Download attachment, return (bytes, filename) or (None, None)."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        content = resp.content

        # Parse filename from Content-Disposition
        filename = ""
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename[^;=\n]*=(([\"']).*?\2|[^;\n]*)", cd)
        if m:
            raw = m.group(1).strip("\"'")
            # Fix garbled GBK/ISO-8859-1 filenames
            try:
                filename = raw.encode("iso-8859-1").decode("gbk")
            except (UnicodeDecodeError, UnicodeEncodeError):
                try:
                    filename = raw.encode("iso-8859-1").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    filename = raw

        if not filename:
            filename = os.path.basename(url.split("?")[0])
        if not filename:
            filename = "attachment"

        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        return content, filename
    except Exception as e:
        logging.warning("Attachment download failed: %s — %s", url, e)
        return None, None


def _process_attachment_bytes(file_bytes, filename):
    """Process attachment bytes.  If ZIP, decompress and return list of
    (name, bytes).  Otherwise return [(filename, file_bytes)].
    """
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
            logging.warning("ZIP extraction failed for '%s': %s", filename, e)
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
    path = os.path.join(articles_dir, "{}.md".format(article_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def _html_to_markdown(html_text):
    """Convert HTML or plain text to clean markdown text."""
    if not html_text or not html_text.strip():
        return ""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        pass
    # Plain text: just clean up
    text = html_text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def _process_batch(batch_articles, output_dir, kb_id, tenant_id,
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

    for idx, art in enumerate(batch_articles, 1):
        # ── Time-bounded check ──
        elapsed = time.time() - crawl_start
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print("\n[ETRADING] Runtime {:.0f}s approaching limit ({}s), "
                       "stopping early".format(elapsed, max_runtime))
            sys.stdout.flush()
            return len(batch_ids), True

        global_idx = (batch_num - 1) * _BATCH_SIZE + idx
        title = art.get("title", "无标题")
        _safe_print("[ETRADING] [{}/{}] {}".format(
            global_idx, total_new, title[:70]))
        sys.stdout.flush()

        infoid = art.get("infoid", "")
        linkurl = art.get("linkurl", "")

        # Fetch detail page for full content + attachments
        content_text, attachments = "", []
        if linkurl:
            content_text, attachments = _fetch_detail_page(linkurl)
        if not content_text:
            # Fallback to API content (truncated)
            content_text = art.get("content", "")

        content_md = _html_to_markdown(content_text)
        if not content_md:
            content_md = content_text or "（无内容）"
            fail_count += 1

        # Build article markdown
        youxiaodate = (art.get("youxiaodate") or "")[:19]
        webdate = (art.get("webdate") or "")[:10]
        city = art.get("city", "")
        categorynum = art.get("categorynum", "")
        detail_url = _SITE_ROOT + linkurl if linkurl else ""

        md_lines = [
            "# {}".format(title),
            "**栏目:** 采购公告 (002003001)",
            "**开标/投标截止时间:** {}".format(youxiaodate),
            "**发布日期:** {}".format(webdate),
            "**地区:** {}".format(city),
            "**分类编号:** {}".format(categorynum),
            "**URL:** {}".format(detail_url),
            "",
            content_md,
            "",
        ]

        # ── Attachment processing ──
        if attachments:
            md_lines.append("---")
            md_lines.append("## 附件列表")
            md_lines.append("")

            for att_idx, att in enumerate(attachments, 1):
                att_url = att["url"]
                att_name = att.get("name", "附件{}".format(att_idx))

                md_lines.append("### {}. {}".format(att_idx, att_name))
                md_lines.append("**下载链接:** {}".format(att_url))
                md_lines.append("")

                _safe_print("[ETRADING]     Downloading: {}".format(att_name[:60]))
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
        batch_ids.append(infoid)

        # Save per-article markdown for crash safety
        _save_article_markdown(md_part, output_dir, infoid[:12])

        _request_delay()

    # ── Save batch markdown & update state ──
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
                _safe_print("[ETRADING] Batch {} upload failed: {}".format(
                    batch_num, e))
                logging.error("Upload batch %d: %s", batch_num, e)

        _safe_print("[ETRADING] Batch {} uploaded: {} articles, {} attachments "
                   "(fail: {})".format(batch_num, len(md_parts),
                                       total_attachments, fail_count))
        sys.stdout.flush()

    return len(batch_ids), False


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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="fujian.etrading.cn (福建电子交易平台) crawler — 采购公告"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://fujian.etrading.cn/",
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

    _safe_print("\n" + "=" * 60)
    _safe_print("[ETRADING] fujian.etrading.cn 采购公告 active-bidding crawler")
    _safe_print("[ETRADING] Filter: categorynum=002003001, youxiaodate > today")
    _safe_print("[ETRADING] KB: {} | max-runtime: {}s".format(
        args.kb_id, args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== etrading crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[ETRADING] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    crawl_start = time.time()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False}
    processed_ids = set(state.get("processed_ids", []))

    if state.get("completed"):
        _safe_print("[ETRADING] Already completed. Use --full to re-crawl.\n")
        sys.stdout.flush()
        return

    _safe_print("[ETRADING] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # ====================================================================
    # Step 1: Fetch all active listings via ES API
    # ====================================================================
    _safe_print("[ETRADING] Step 1/2: Fetching active bidding listings...")
    sys.stdout.flush()

    all_records = _fetch_all_listings()
    _safe_print("[ETRADING]   API returned {} records\n".format(len(all_records)))
    sys.stdout.flush()

    if not all_records:
        _safe_print("[ETRADING] No records found — aborting.\n")
        sys.stdout.flush()
        return

    # -- Filter already processed -------------------------------------------
    new_records = [r for r in all_records
                   if r.get("infoid") and r["infoid"] not in processed_ids]
    _safe_print("[ETRADING]   {} new (skipped {} already processed)\n".format(
        len(new_records), len(all_records) - len(new_records)))
    sys.stdout.flush()

    if not new_records:
        _safe_print("[ETRADING] Nothing new.\n")
        sys.stdout.flush()
        return

    # ====================================================================
    # Step 2: Process in batches (detail fetch + attachments + upload)
    # ====================================================================
    _safe_print("[ETRADING] Step 2/2: Processing {} articles in batches of {}...\n".format(
        len(new_records), _BATCH_SIZE))
    sys.stdout.flush()

    total_processed = 0
    stopped_early = False

    for batch_num in range(1, 999):
        start = (batch_num - 1) * _BATCH_SIZE
        end = start + _BATCH_SIZE
        batch = new_records[start:end]
        if not batch:
            break

        n, early = _process_batch(
            batch, output_dir, args.kb_id, args.tenant_id,
            processed_ids, state, batch_num, len(new_records),
            args.max_runtime, crawl_start,
        )
        total_processed += n
        if early:
            stopped_early = True
            break

    # -- Mark completed if finished all ------------------------------------
    if not stopped_early:
        state["completed"] = True
        _save_state(output_dir, state)

    # ====================================================================
    # Done
    # ====================================================================
    _safe_print("\n" + "=" * 60)
    _safe_print("[ETRADING] Done: {} articles processed{}".format(
        total_processed, " (stopped early)" if stopped_early else ""))
    _safe_print("[ETRADING] State: {}".format(
        "completed" if not stopped_early else "partial"))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== etrading crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "etrading_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
