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
Dedicated web crawler for zz.fycbid.cn (福易采电子交易平台漳州分区).

This site is a BladeX (Spring Boot) + Vue.js SPA.  All public content
(notices / bulletins / roll notices) is accessible without login via
plain REST APIs — no SM4 or other encryption.

Usage (typically spawned by task_executor):
    python zz_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zz.fycbid.cn/ \
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

import requests  # fallback only
from bs4 import BeautifulSoup

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
_API_BASE = "https://zz.fycbid.cn"

# ---- Notice list & detail ----
_NOTICE_LIST_URL = "/fyc-cms/index/home/notice"
_ANNOUNCE_INFO_URL = "/fyc-statistics/home/announceInfo"
_BULLETIN_INFO_URL = "/fyc-statistics/home/bulletinInfo"

# ---- Other bulletins (内容直出) ----
_OTHER_BULLETIN_URL = "/fyc-cms/index/home/otherBulletin"

# ---- Roll notices (首页滚动通知) ----
_INDEX_URL = "/fyc-cms/index/index"

# ---- File download ----
_FILE_DOWNLOAD_URL = "/fyc-file/load"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_post(path, params, retries=3, client=None):
    """POST JSON, return decoded dict."""
    url = _API_BASE + path
    for attempt in range(1, retries + 1):
        try:
            if client:
                resp = client.post(url, json_body=params, headers=_HEADERS, timeout=30)
            else:
                resp = requests.post(url, json=params, headers=_HEADERS, timeout=30,
                                     proxies={"http": "", "https": ""})
            data = resp.json()
            if data.get("code") == 200 and data.get("success"):
                return data
            logging.warning("POST %s -> code=%s success=%s (attempt %d/%d)",
                            path, data.get("code"), data.get("success"), attempt, retries)
            time.sleep(2 ** attempt)
        except Exception as e:
            logging.warning("POST %s failed: %s (attempt %d/%d)", path, e, attempt, retries)
            time.sleep(2 ** attempt)
    return None


def _api_get(path, params, retries=3, client=None):
    """GET with query params, return decoded dict."""
    url = _API_BASE + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    for attempt in range(1, retries + 1):
        try:
            if client:
                resp = client.fetch_get(url, headers=_HEADERS, timeout=30)
            else:
                resp = requests.get(url, params=params, headers=_HEADERS, timeout=30,
                                    proxies={"http": "", "https": ""})
            data = resp.json()
            if data.get("code") == 200 and data.get("success"):
                return data
            logging.warning("GET %s -> code=%s success=%s (attempt %d/%d)",
                            path, data.get("code"), data.get("success"), attempt, retries)
            time.sleep(2 ** attempt)
        except Exception as e:
            logging.warning("GET %s failed: %s (attempt %d/%d)", path, e, attempt, retries)
            time.sleep(2 ** attempt)
    return None


def _download_file(file_id, timeout=120, client=None):
    """Download a file by its fileId."""
    try:
        url = f"{_API_BASE}{_FILE_DOWNLOAD_URL}?fileId={file_id}"
        if client:
            resp = client.fetch_get(url, headers=_HEADERS, timeout=timeout)
        else:
            resp = requests.get(
                _API_BASE + _FILE_DOWNLOAD_URL,
                params={"fileId": file_id},
                headers=_HEADERS, timeout=timeout,
                proxies={"http": "", "https": ""},
            )
        ct = resp.headers.get("Content-Type", "")
        cd = resp.headers.get("Content-Disposition", "")
        filename = ""
        if cd:
            import re
            m = re.search(r'filename="?([^";\n]+)"?', cd)
            if m:
                filename = m.group(1)
        return resp.content, ct, filename
    except Exception as e:
        logging.error("Failed to download fileId %s: %s", file_id, e)
        return None, "", ""


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
# Content fetching
# ---------------------------------------------------------------------------

def _fetch_notice_list(max_days=30, client=None):
    """Fetch all public notice announcements, returning items with projId."""
    cutoff = datetime.now() - timedelta(days=max_days)
    all_items = []
    page_no = 1

    while True:
        result = _api_post(_NOTICE_LIST_URL, {
            "pageNo": page_no, "pageSize": 50,
            "projName": "", "bultype": "", "projType": "",
        }, client=client)
        if not result:
            break
        rows = result.get("data", {}).get("rows", [])
        if not rows:
            break

        for item in rows:
            date_str = (item.get("releaseTime") or "")[:10]
            dt = _parse_date(date_str)
            if dt and dt < cutoff:
                return all_items
            all_items.append({
                "id": item["id"],
                "projId": item.get("projId", ""),
                "title": item.get("title", "").strip(),
                "date_str": date_str,
                "date": dt,
                "announce_type": item.get("announceType", ""),
                "bultype": item.get("bultype", ""),
            })

        page_no += 1
        time.sleep(0.3)

    return all_items


def _fetch_announce_detail(proj_id, client=None):
    """Fetch full announcement detail with HTML content by projId."""
    result = _api_get(_ANNOUNCE_INFO_URL, {"projId": proj_id}, client=client)
    if not result:
        return "", {}
    rows = result.get("data", {}).get("rows", [])
    if not rows:
        return "", {}
    item = rows[0]
    return item.get("content", "") or "", {
        "title": item.get("title", ""),
        "releaseTime": item.get("releaseTime", ""),
        "announceType": item.get("announceType", ""),
        "attachNo": item.get("attachNo", ""),
    }


def _fetch_other_bulletins(max_days=30, client=None):
    """Fetch 'other bulletins' which have content directly included."""
    cutoff = datetime.now() - timedelta(days=max_days)
    result = _api_post(_OTHER_BULLETIN_URL, {"pageNo": 1, "pageSize": 50}, client=client)
    if not result:
        return []
    rows = result.get("data", {}).get("rows", [])
    items = []
    for item in rows:
        date_str = (item.get("createTime") or "")[:10]
        dt = _parse_date(date_str)
        if dt and dt < cutoff:
            continue
        items.append({
            "id": item["id"],
            "title": item.get("title", "").strip(),
            "date_str": date_str,
            "date": dt,
            "content": item.get("content", "") or "",
        })
    return items


def _fetch_roll_notices(client=None):
    """Fetch roll notices from the site index."""
    result = _api_get(_INDEX_URL, {}, client=client)
    if not result:
        return []
    data = result.get("data", {})
    roll = data.get("rollNotice", {}).get("list", {}).get("records", [])
    items = []
    for item in roll:
        items.append({
            "id": item.get("id", ""),
            "title": item.get("articleTitle", "").strip(),
            "date_str": (item.get("createTime") or "")[:10],
            "content": item.get("articleContent", "") or "",
        })
    return items


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def _content_to_markdown(html):
    """Convert HTML content to plain markdown text."""
    from markdownify import markdownify as md
    return md(html, heading_style="ATX", strip=["script", "style", "noscript"])


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
    parser = argparse.ArgumentParser(
        description="zz.fycbid.cn (福易采电子交易平台漳州分区) crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://zz.fycbid.cn/)")
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
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
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
    _safe_print("[ZZ] Starting 福易采电子交易平台漳州分区 crawler")
    _safe_print(f"[ZZ] Target URL: {args.target_url}")
    _safe_print(f"[ZZ] Task name: {args.task_name}")
    _safe_print(f"[ZZ] Target KB: {args.kb_id}")
    _safe_print(f"[ZZ] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[ZZ] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZZ crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[ZZ] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[ZZ] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        all_data = []  # accumulated markdown parts
        total_new = 0

        # ===================================================================
        # Section 1: Roll notices (首页滚动通知)
        # ===================================================================
        _safe_print("[ZZ] Section 1/4: Fetching roll notices...")
        sys.stdout.flush()
        roll_notices = _fetch_roll_notices(client=client)
        _safe_print(f"[ZZ]   Found {len(roll_notices)} roll notice(s)\n")
        sys.stdout.flush()

        new_roll = 0
        for item in roll_notices:
            if item["id"] in processed_ids:
                continue
            if not item.get("content"):
                continue
            md = (
                f"# {item['title']}\n"
                f"**Date:** {item['date_str']}\n"
                f"**Source:** {args.target_url}\n"
                f"**Type:** 滚动通知\n\n"
                f"{_content_to_markdown(item['content'])}\n"
            )
            _save_markdown(md, output_dir, f"roll_{item['id']}")
            all_data.append(md)
            new_roll += 1
        _safe_print(f"[ZZ]   New roll notices: {new_roll}\n")
        sys.stdout.flush()

        # ===================================================================
        # Section 2: Notice list (公告列表)
        # ===================================================================
        _safe_print("[ZZ] Section 2/4: Fetching notice list...")
        sys.stdout.flush()
        notices = _fetch_notice_list(max_days=args.max_days, client=client)
        if args.max_articles:
            notices = notices[:args.max_articles]

        # Filter processed
        if processed_ids:
            new_notices = [n for n in notices if n["id"] not in processed_ids]
            skipped = len(notices) - len(new_notices)
            if skipped:
                _safe_print(f"[ZZ]   Skipping {skipped} already-processed notice(s)")
                sys.stdout.flush()
            notices = new_notices

        _safe_print(f"[ZZ]   Found {len(notices)} new notice(s)\n")
        sys.stdout.flush()

        notice_count = 0
        for idx, notice in enumerate(notices, 1):
            _safe_print(f"[ZZ]   [{idx}/{len(notices)}] {notice['title'][:60]}")
            sys.stdout.flush()

            # Get detail via announceInfo
            content, meta = _fetch_announce_detail(notice["projId"], client=client)
            if content:
                md = (
                    f"# {notice['title']}\n"
                    f"**Date:** {notice['date_str']}\n"
                    f"**Source:** {args.target_url}\n"
                    f"**Type:** 公告\n"
                    f"**Project ID:** {notice['projId']}\n\n"
                    f"{_content_to_markdown(content)}\n"
                )

                # Check for attachments
                if meta.get("attachNo"):
                    _safe_print("      -> has attachments")
                    sys.stdout.flush()
                    parts = meta["attachNo"].split(",")
                    for i, fid in enumerate(parts):
                        fid = fid.strip()
                        if fid:
                            blob, ct, fname = _download_file(fid, client=client)
                            if blob and len(blob) > 0:
                                save_name = fname or f"attach_{notice['projId']}_{i}"
                                _save_download(blob, output_dir, save_name)

                _save_markdown(md, output_dir, notice["id"])
                all_data.append(md)
                notice_count += 1
            else:
                _safe_print("      -> no content, skipping")
                sys.stdout.flush()

            time.sleep(0.3)

        total_new += notice_count
        _safe_print(f"[ZZ]   New notices with content: {notice_count}\n")
        sys.stdout.flush()

        # ===================================================================
        # Section 3: Other bulletins (其他公告, 内容直出)
        # ===================================================================
        _safe_print("[ZZ] Section 3/4: Fetching other bulletins...")
        sys.stdout.flush()
        bulletins = _fetch_other_bulletins(max_days=args.max_days, client=client)
        # Filter processed
        if processed_ids:
            new_bulletins = [b for b in bulletins if b["id"] not in processed_ids]
            skipped = len(bulletins) - len(new_bulletins)
            if skipped:
                _safe_print(f"[ZZ]   Skipping {skipped} already-processed bulletin(s)")
                sys.stdout.flush()
            bulletins = new_bulletins

        _safe_print(f"[ZZ]   Found {len(bulletins)} new other bulletin(s)\n")
        sys.stdout.flush()

        bulletin_count = 0
        for item in bulletins:
            if not item.get("content"):
                continue
            md = (
                f"# {item['title']}\n"
                f"**Date:** {item['date_str']}\n"
                f"**Source:** {args.target_url}\n"
                f"**Type:** 其他公告\n\n"
                f"{_content_to_markdown(item['content'])}\n"
            )
            _save_markdown(md, output_dir, f"other_{item['id']}")
            all_data.append(md)
            bulletin_count += 1

        total_new += bulletin_count
        _safe_print(f"[ZZ]   New bulletins: {bulletin_count}\n")
        sys.stdout.flush()

        # ===================================================================
        # Section 4: Save combined & upload
        # ===================================================================
        _safe_print("[ZZ] Section 4/4: Saving and uploading...\n")
        sys.stdout.flush()

        if all_data:
            combined_path = os.path.join(output_dir, "articles_combined.md")
            with open(combined_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(all_data))
            _safe_print(f"[ZZ]   Combined markdown: {combined_path}")
            sys.stdout.flush()

            # Update state with all newly processed IDs
            new_ids = []
            for notice in notices:
                new_ids.append(notice["id"])
            for item in roll_notices:
                new_ids.append(item["id"])
            for item in bulletins:
                new_ids.append(item["id"])
            if new_ids:
                processed_ids.update(new_ids)
                _save_state(output_dir, {"processed_ids": list(processed_ids)})

            # Upload to KB
            if args.kb_id:
                _safe_print(f"[ZZ]   Uploading combined markdown to KB {args.kb_id}...")
                sys.stdout.flush()
                try:
                    _upload_to_kb(combined_path, args.kb_id, args.tenant_id, parser_id="naive")
                    _safe_print(" done!")
                    sys.stdout.flush()
                except Exception as e:
                    _safe_print(f" failed: {e}")
                    sys.stdout.flush()
                    logging.error("Markdown upload failed: %s", e)
        else:
            _safe_print("[ZZ]   No new content to save.\n")
            sys.stdout.flush()

        _safe_print(f"\n[ZZ] {'='*60}")
        _safe_print(f"[ZZ] Crawl finished: {total_new} new articles")
        _safe_print(f"[ZZ] {'='*60}\n")
        sys.stdout.flush()
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "zz_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
