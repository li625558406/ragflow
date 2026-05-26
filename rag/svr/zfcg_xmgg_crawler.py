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
Crawler for zfcg.czt.fujian.gov.cn — announcement channel (公告信息).

Channel: f582600e-065d-4f35-8966-48a33fa93863 (采购信息/公告信息)
List page:  /maincms-web/xmgg?channel=...&dictName=公告信息
Detail page: /maincms-web/articleDetail?type=notice&id=...

Site uses Vue SPA + axios interceptors that add a required ``nsssjss``
signature header.  Direct HTTP requests fail; Playwright + Vue $http
is required.

Flow:
  1. Playwright loads homepage -> Vue app initialises axios interceptors
  2. Fetch captcha image via Vue $http, OCR with ddddocr
  3. Call selectInfoForIndex API with verifyCode (past 24 hours)
  4. For each article, fetch detail via getInfoById API
  5. Save markdown & upload to KB

Usage:
    python zfcg_xmgg_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://zfcg.czt.fujian.gov.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# ddddocr
# ---------------------------------------------------------------------------
try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_URL = "https://zfcg.czt.fujian.gov.cn"
_API_BASE = "/gpcms/rest/web/v2"
_SITE_ID = "d36a6e8b-4363-4b52-a00b-79ca47033923"
_CHANNEL_ID = "f582600e-065d-4f35-8966-48a33fa93863"
_REGION_CODE = "350001"

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

# JS snippet to call the website's Vue 2 $http (axios with interceptors)
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


def _parse_date(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text.strip()[:19], fmt)
        except (ValueError, AttributeError):
            continue
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
# Captcha
# ---------------------------------------------------------------------------

def _solve_captcha(page, ocr, site_id, channel_id, max_retries=15):
    """Fetch captcha, OCR it, verify against API. Returns code string or None."""
    for attempt in range(1, max_retries + 1):
        cap = _vue_http_get(
            page,
            "{}/index/getVerify".format(_API_BASE),
            params={"_t": str(int(time.time() * 1000))},
            response_type="arraybuffer",
        )
        if cap.get("error") or not cap.get("b64"):
            logging.warning("Captcha fetch failed (attempt %d)", attempt)
            time.sleep(0.3)
            continue

        img_bytes = base64.b64decode(cap["b64"])
        code_val = ocr.classification(img_bytes).strip()

        # Verify against the search API
        check = _vue_http_get(page, "{}/info/selectInfoForIndex".format(_API_BASE), params={
            "siteId": site_id,
            "channel": channel_id,
            "currPage": "1", "pageSize": "1",
            "noticeType": "00101",
            "regionCode": _REGION_CODE,
            "operationStartTime": "2020-01-01 00:00:00",
            "operationEndTime": "2030-01-01 00:00:00",
            "verifyCode": code_val,
            "selectTimeName": "openTenderTime",
        })
        if check.get("code") == "200":
            return code_val
        time.sleep(0.3)

    return None


# ---------------------------------------------------------------------------
# Listing & detail API
# ---------------------------------------------------------------------------

def _search_listings(page, verify_code, start_dt, end_dt,
                     site_id, channel_id, page_num=1, page_size=20):
    """Call selectInfoForIndex API via Vue $http (no server-side time filter).

    Time filtering is done client-side on ``releaseTime`` after fetching all
    records, because the API's ``selectTimeName`` parameter rejects
    ``releaseTime`` and only accepts ``openTenderTime``.
    """
    params = {
        "siteId": site_id,
        "channel": channel_id,
        "currPage": str(page_num),
        "pageSize": str(page_size),
        "noticeType": "00101",
        "regionCode": _REGION_CODE,
        "cityOrArea": "",
        "purchaseManner": "",
        "openTenderCode": "",
        "purchaser": "",
        "agency": "",
        "purchaseNature": "",
        "verifyCode": verify_code,
        "selectTimeName": "",
        "operationStartTime": "",
        "operationEndTime": "",
    }
    data = _vue_http_get(page, "{}/info/selectInfoForIndex".format(_API_BASE), params=params)
    if data.get("code") == "200":
        return data.get("data", {}).get("rows", [])
    _safe_print("[XMGG]   API error: code={}, msg={}".format(
        data.get("code"), data.get("msg")))
    return None


def _get_detail(page, record_id, plan_id, channel_id, site_id):
    """Fetch full detail via getInfoById API."""
    data = _vue_http_get(page, "{}/info/getInfoById".format(_API_BASE), params={
        "id": record_id,
        "planId": plan_id or "",
        "channel": channel_id,
        "siteId": site_id,
    })
    if data.get("code") == "200":
        return data.get("data", {})
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
        description="zfcg.czt.fujian.gov.cn announcement crawler (today + yesterday)"
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
    p.add_argument("--hours", type=int, default=0,
                   help="Override lookback in hours (0 = use today+yesterday date range)")
    for opt in ("--section", "--max-articles", "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[XMGG] zfcg announcement crawler (today + yesterday)")
    _safe_print("[XMGG] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== XMGG crawler started ===")

    # -- Prerequisites -------------------------------------------------------
    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[XMGG] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)
    if not DDDDOCR_AVAILABLE:
        _safe_print("[XMGG] ERROR: ddddocr not installed (pip install ddddocr).")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[XMGG] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print("[XMGG] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Date range: today + yesterday (calendar-based) -----------------------
    now = datetime.now()
    if args.hours > 0:
        start_dt = now - timedelta(hours=args.hours)
    else:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = today_start - timedelta(days=1)  # yesterday 00:00:00
    end_dt = now
    _safe_print("[XMGG] Time range: {} ~ {}".format(
        start_dt.strftime("%Y-%m-%d %H:%M"), end_dt.strftime("%Y-%m-%d %H:%M")))
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

        # Step 1: Load homepage -> Vue initialises axios interceptors
        _safe_print("[XMGG] Step 1/4: Loading homepage...")
        sys.stdout.flush()
        page.goto(_SITE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # Step 2: Solve captcha
        _safe_print("[XMGG] Step 2/4: Solving captcha...")
        sys.stdout.flush()
        ocr = ddddocr.DdddOcr(show_ad=False)
        verify_code = _solve_captcha(page, ocr, _SITE_ID, _CHANNEL_ID)
        if not verify_code:
            _safe_print("[XMGG] ERROR: Could not solve captcha.")
            browser.close()
            return
        _safe_print("[XMGG]   Captcha verified: {}".format(verify_code))
        sys.stdout.flush()

        # Step 3: Fetch listings (paginated)
        _safe_print("[XMGG] Step 3/4: Fetching listings...")
        sys.stdout.flush()

        all_records = []
        page_size = 20
        for pg in range(1, 101):
            rows = _search_listings(
                page, verify_code, start_dt, end_dt,
                _SITE_ID, _CHANNEL_ID,
                page_num=pg, page_size=page_size,
            )
            if rows is None:
                # verifyCode may have expired -> re-solve
                _safe_print("[XMGG]   verifyCode expired, re-solving...")
                verify_code = _solve_captcha(page, ocr, _SITE_ID, _CHANNEL_ID)
                if not verify_code:
                    _safe_print("[XMGG]   Re-solve failed, stopping.")
                    break
                rows = _search_listings(
                    page, verify_code, start_dt, end_dt,
                    _SITE_ID, _CHANNEL_ID,
                    page_num=pg, page_size=page_size,
                )
            if not rows:
                break
            all_records.extend(rows)
            _safe_print("[XMGG]   Page {}: {} records (total: {})".format(
                pg, len(rows), len(all_records)))
            if len(rows) < page_size:
                break

        _safe_print("[XMGG]   Total: {} records (before date filter)".format(len(all_records)))
        sys.stdout.flush()

        # ── Client-side date filter by releaseTime ──────────────────────
        # releaseTime is date-only (YYYY-mm-DD), not datetime.
        if start_dt and end_dt:
            start_date = start_dt.date()
            end_date = end_dt.date()
            filtered = []
            for r in all_records:
                rt = (r.get("releaseTime") or r.get("noticeTime") or "").strip()[:10]
                if rt:
                    try:
                        rt_date = datetime.strptime(rt, "%Y-%m-%d").date()
                        if start_date <= rt_date <= end_date:
                            filtered.append(r)
                    except ValueError:
                        pass  # drop unparseable dates
                # Records without a releaseTime are dropped (not announcement data)
            all_records = filtered
            _safe_print("[XMGG]   After date filter: {} records".format(len(all_records)))
            sys.stdout.flush()

        if not all_records:
            _safe_print("[XMGG] No records found.")
            browser.close()
            return

        # Filter already processed
        new_records = [r for r in all_records
                       if r.get("id") and r["id"] not in processed_ids]
        _safe_print("[XMGG]   {} new (skipped {} already processed)".format(
            len(new_records), len(all_records) - len(new_records)))
        sys.stdout.flush()

        if not new_records:
            _safe_print("[XMGG] Nothing new.")
            browser.close()
            return

        # Step 4: Fetch details & build markdown
        _safe_print("[XMGG] Step 4/4: Fetching details for {} articles...".format(
            len(new_records)))
        sys.stdout.flush()

        md_parts = []
        article_data = []
        total_fail = 0

        for idx, record in enumerate(new_records, 1):
            record_id = record.get("id", "")
            title = record.get("title", "\u65e0\u6807\u9898")
            _safe_print("[XMGG]   [{}/{}] {}".format(idx, len(new_records), title[:60]))
            sys.stdout.flush()

            # Fetch detail
            detail = _get_detail(
                page, record_id,
                record.get("planId", ""),
                _CHANNEL_ID, _SITE_ID,
            )

            # Build content
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

            # List-level metadata as content fallback
            if not content_parts:
                total_fail += 1
                for key in ("purchaser", "agency", "budget",
                            "purchaseManner", "openTenderCode"):
                    val = record.get(key, "")
                    if val:
                        label_map = {
                            "purchaser": "\u91c7\u8d2d\u4eba",
                            "agency": "\u4ee3\u7406\u673a\u6784",
                            "budget": "\u9884\u7b97\u91d1\u989d",
                            "purchaseManner": "\u91c7\u8d2d\u65b9\u5f0f",
                            "openTenderCode": "\u9879\u76ee\u7f16\u53f7",
                        }
                        content_parts.append("{}: {}".format(label_map.get(key, key), val))

            content = "\n".join(content_parts) if content_parts else "\u65e0\u8be6\u7ec6\u5185\u5bb9"

            release_time = (record.get("releaseTime") or "")[:19]
            open_time = (record.get("openTenderTime") or "")[:19]
            region = record.get("regionName", "")
            detail_url = "{}/maincms-web/articleDetail?type=notice&id={}&channel={}".format(
                _SITE_URL, record_id, _CHANNEL_ID)

            md = "# {}\n**\u680f\u76ee:** \u516c\u544a\u4fe1\u606f\n**\u53d1\u5e03\u65f6\u95f4:** {}\n**\u5f00\u6807\u65f6\u95f4:** {}\n**\u533a\u57df:** {}\n**URL:** {}\n\n{}\n".format(
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
            _safe_print("[XMGG]   Uploading to KB {}...".format(args.kb_id))
            sys.stdout.flush()
            try:
                _upload_to_kb(combined_path, args.kb_id, args.tenant_id)
                _safe_print(" done!")
            except Exception as e:
                _safe_print(" failed: {}".format(e))
                logging.error("Upload failed: %s", e)

    _safe_print("\n" + "=" * 60)
    _safe_print("[XMGG] Done: {} articles, {} no-detail".format(
        len(article_data), total_fail))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== XMGG crawler finished: %d success, %d no-detail ===",
                 len(article_data), total_fail)


if __name__ == "__main__":
    CONSUMER_NAME = "zfcg_xmgg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
