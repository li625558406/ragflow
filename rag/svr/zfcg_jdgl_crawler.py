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
Crawler for zfcg.czt.fujian.gov.cn — supervision management (监督管理+曝光台).

Channel: 47bbf186-5544-4537-bef5-158d0c0923fa (曝光台)
List page:  /maincms-web/jdgl?channel=...&dictName=监督管理
Detail:     /maincms-web/articleDetail?type=manual&id=<ID>&channel=...

No captcha — uses selectInfoMoreChannel for listings and selectPAndNInfoById
for details via the Vue $http (axios) wrapper.

Crawls ALL pages (all historical data).

Usage:
    python zfcg_jdgl_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zfcg.czt.fujian.gov.cn/ \
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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_URL = "https://zfcg.czt.fujian.gov.cn"
_API_BASE = "/gpcms/rest/web/v2"
_SITE_ID = "d36a6e8b-4363-4b52-a00b-79ca47033923"
_LIST_CHANNEL_ID = "47bbf186-5544-4537-bef5-158d0c0923fa"
_DETAIL_CHANNEL_ID = "8d90e7b4-3ada-4fc3-ae6d-fc3ddd0bf36e"

# The actual jdgl page — must load this specific URL so Vue app context is correct
_JDGL_PAGE_URL = (
    "https://zfcg.czt.fujian.gov.cn/maincms-web/jdgl"
    "?channel=47bbf186-5544-4537-bef5-158d0c0923fa"
    "&dictName=%E7%9B%91%E7%9D%A3%E7%AE%A1%E7%90%86"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_VUE_HTTP_JS = """
([url, opts]) => {
    return new Promise((resolve) => {
        const vm = document.querySelector('#app').__vue__;
        const http = vm.$http;
        http.get(url, opts).then(resp => {
            if (opts.responseType === 'arraybuffer') {
                let bytes = new Uint8Array(resp.data);
                let binary = '';
                bytes.forEach(b => binary += String.fromCharCode(b));
                resolve({b64: btoa(binary)});
            } else {
                resolve(resp.data);
            }
        }).catch(err => {
            resolve({error: err.message});
        });
    });
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Vue $http wrapper
# ---------------------------------------------------------------------------

def _vue_http_get(page, url, params=None, response_type=None):
    opts = {"params": params or {}}
    if response_type:
        opts["responseType"] = response_type
    return page.evaluate(_VUE_HTTP_JS, [url, opts])


# ---------------------------------------------------------------------------
# Listing & detail API (no captcha)
# ---------------------------------------------------------------------------

def _search_listings(page, page_num=1, page_size=20, api_path=None, base_params=None):
    """Call the listing API via Vue $http."""
    t = int(time.time() * 1000)

    if api_path is None:
        api_path = "/info/selectInfoForIndex"
    if base_params is None:
        base_params = {}

    params = dict(base_params)
    params["currPage"] = str(page_num)
    params["pageSize"] = str(page_size)
    params["_t"] = str(t)

    data = _vue_http_get(page, "{}{}".format(_API_BASE, api_path), params=params)
    if isinstance(data, dict) and data.get("error"):
        _safe_print("[JDGL]   API error: {}".format(data["error"]))
        return None
    if data.get("code") == "200":
        return data.get("data", {}).get("rows", [])
    _safe_print("[JDGL]   API error: code={}, msg={}".format(
        data.get("code"), data.get("msg")))
    return None


def _get_detail(page, record):
    """Fetch full detail via getInfoById API (same as zfcg_crawler/xmgg)."""
    t = int(time.time() * 1000)
    data = _vue_http_get(page, "{}/info/getInfoById".format(_API_BASE), params={
        "id": record.get("id", ""),
        "planId": record.get("planId", ""),
        "channel": record.get("channel", ""),
        "siteId": _SITE_ID,
        "_t": str(t),
    })
    if isinstance(data, dict) and data.get("error"):
        logging.warning("Detail API error for %s: %s", record.get("id"), data["error"])
        return None
    if data.get("code") == "200":
        return data.get("data", {})
    logging.warning("Detail API for %s: code=%s, msg=%s",
                   record.get("id"), data.get("code"), data.get("msg"))
    return None


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
    return {"processed_ids": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d processed IDs)", len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


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
        description="zfcg.czt.fujian.gov.cn supervision management crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://zfcg.czt.fujian.gov.cn/",
                   help="Site URL (kept for task_executor compatibility)")
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
    _safe_print("[JDGL] zfcg supervision management crawler (all data, no captcha)")
    _safe_print("[JDGL] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== JDGL crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[JDGL] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[JDGL] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print("[JDGL] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Playwright ----------------------------------------------------------
    with sync_playwright() as pw:
        chrome_path = _find_chrome()
        launch_opts = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox"],
        }
        if chrome_path:
            launch_opts["executable_path"] = chrome_path
        browser = pw.chromium.launch(**launch_opts)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        # Step 1: Load jdgl page -> Vue initialises axios interceptors
        _safe_print("[JDGL] Step 1/3: Loading jdgl page and probing API...")
        sys.stdout.flush()
        page.goto(_JDGL_PAGE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        # ── Probe: try known API endpoints via Vue $http, pick the one that works ──
        api_path = None
        base_params = None

        probe_candidates = [
            # (api_path, description, params)
            ("/info/selectInfoForIndex", "selectInfoForIndex (zcfg-style)", {
                "siteId": _SITE_ID,
                "channel": _LIST_CHANNEL_ID,
                "title": "",
                "noticeType": "",
                "operationStartTime": "",
                "operationEndTime": "",
                "selectTimeName": "",
            }),
            ("/info/selectInfoMoreChannel", "selectInfoMoreChannel (jdgl-original)", {
                "siteId": _SITE_ID,
                "channel": _LIST_CHANNEL_ID,
                "noticeType": "00101",
                "regionCode": "350001",
                "operationStartTime": "2020-01-01 00:00:00",
                "operationEndTime": "2030-01-01 00:00:00",
                "selectTimeName": "openTenderTime",
            }),
            ("/info/selectInfoMoreChannel", "selectInfoMoreChannel (no filters)", {
                "siteId": _SITE_ID,
                "channel": _LIST_CHANNEL_ID,
                "noticeType": "",
                "regionCode": "350001",
                "operationStartTime": "",
                "operationEndTime": "",
                "selectTimeName": "",
            }),
        ]

        for probe_path, probe_desc, probe_params in probe_candidates:
            _safe_print("[JDGL]   Trying {}...".format(probe_desc))
            sys.stdout.flush()
            rows = _search_listings(page, page_num=1, page_size=5,
                                    api_path=probe_path, base_params=probe_params)
            if rows is not None and len(rows) > 0:
                api_path = probe_path
                base_params = probe_params
                _safe_print("[JDGL]   SUCCESS: {} returned {} rows".format(probe_path, len(rows)))
                sys.stdout.flush()
                break
            _safe_print("[JDGL]   No rows from {}".format(probe_path))
            sys.stdout.flush()

        if api_path is None:
            _safe_print("[JDGL] ERROR: No API endpoint returned data. Exiting.")
            sys.stdout.flush()
            browser.close()
            return

        # Step 2: Fetch all listings (paginated, no captcha)
        _safe_print("[JDGL] Step 2/3: Fetching all pages via {}...".format(api_path))
        sys.stdout.flush()

        all_records = []
        page_size = 20
        for pg in range(1, 201):
            rows = _search_listings(page, page_num=pg, page_size=page_size,
                                    api_path=api_path, base_params=base_params)
            if rows is None:
                _safe_print("[JDGL]   API returned error, stopping.")
                break
            if not rows:
                break
            all_records.extend(rows)
            _safe_print("[JDGL]   Page {}: {} records (total: {})".format(
                pg, len(rows), len(all_records)))
            if len(rows) < page_size:
                break

        _safe_print("[JDGL]   Total: {} records".format(len(all_records)))
        sys.stdout.flush()

        if not all_records:
            _safe_print("[JDGL] No records found.")
            browser.close()
            return

        # Filter already processed
        new_records = [r for r in all_records
                       if r.get("id") and r["id"] not in processed_ids]
        _safe_print("[JDGL]   {} new (skipped {} already processed)".format(
            len(new_records), len(all_records) - len(new_records)))
        sys.stdout.flush()

        if not new_records:
            _safe_print("[JDGL] Nothing new.")
            browser.close()
            return

        # Step 3: Fetch details & build markdown
        _safe_print("[JDGL] Step 3/3: Fetching details for {} articles...".format(
            len(new_records)))
        sys.stdout.flush()

        md_parts = []
        article_data = []
        total_fail = 0

        for idx, record in enumerate(new_records, 1):
            record_id = record.get("id", "")
            title = record.get("title", "\u65e0\u6807\u9898")
            _safe_print("[JDGL]   [{}/{}] {}".format(
                idx, len(new_records), title[:60]))
            sys.stdout.flush()

            detail = _get_detail(page, record)

            content_parts = []
            if detail:
                desc = detail.get("content") or detail.get("description") or ""
                if desc:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(desc, "html.parser")
                        content_parts.append(soup.get_text(separator="\n", strip=True))
                    except Exception:
                        content_parts.append(desc)

            if not content_parts:
                total_fail += 1
                for key in ("purchaser", "agency", "budget", "purchaseManner"):
                    val = record.get(key, "")
                    if val:
                        label_map = {
                            "purchaser": "\u91c7\u8d2d\u4eba",
                            "agency": "\u4ee3\u7406\u673a\u6784",
                            "budget": "\u9884\u7b97\u91d1\u989d",
                            "purchaseManner": "\u91c7\u8d2d\u65b9\u5f0f",
                        }
                        content_parts.append("{}: {}".format(label_map.get(key, key), val))

            content = "\n".join(content_parts) if content_parts else "\u65e0\u8be6\u7ec6\u5185\u5bb9"

            release_time = (record.get("releaseTime") or "")[:19]
            open_time = (record.get("openTenderTime") or "")[:19]
            region = record.get("regionName", "")
            detail_url = "{}/maincms-web/articleDetail?type=manual&id={}&planId=&channel={}".format(
                _SITE_URL, record_id, _DETAIL_CHANNEL_ID)

            md = "# {}\n**\u680f\u76ee:** \u76d1\u7763\u7ba1\u7406\n**\u53d1\u5e03\u65f6\u95f4:** {}\n**\u5f00\u6807\u65f6\u95f4:** {}\n**\u533a\u57df:** {}\n**URL:** {}\n\n{}\n".format(
                title, release_time, open_time, region, detail_url, content
            )
            _save_markdown(md, output_dir, record_id)
            md_parts.append(md)
            article_data.append({"id": record_id, "md": md})

            time.sleep(0.3)

        browser.close()

    # -- Save & upload -------------------------------------------------------
    if md_parts:
        combined_path = os.path.join(output_dir, "articles_combined.md")
        with open(combined_path, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))

        new_ids = [a["id"] for a in article_data]
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

        if args.kb_id:
            _safe_print("[JDGL]   Uploading to KB {}...".format(args.kb_id))
            sys.stdout.flush()
            try:
                _upload_to_kb(combined_path, args.kb_id, args.tenant_id)
                _safe_print(" done!")
            except Exception as e:
                _safe_print(" failed: {}".format(e))
                logging.error("Upload failed: %s", e)

    _safe_print("\n" + "=" * 60)
    _safe_print("[JDGL] Done: {} articles, {} no-detail".format(
        len(article_data), total_fail))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== JDGL crawler finished: %d success, %d no-detail ===",
                 len(article_data), total_fail)


if __name__ == "__main__":
    CONSUMER_NAME = "zfcg_jdgl_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
