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
Crawler for ggzyjy.quanzhou.gov.cn — 工程建设模块 (7 sub-modules).

Target: https://ggzyjy.quanzhou.gov.cn/project/projectList.do?centerId=-1

Modules
───────
  1. 招标计划        — biddingPlan (2989 records, 299 pages)
  2. 招标文件预公告   — tenderNotice (518 records, 52 pages)
  3. 招标信息        — projectList (8828 records, 883 pages)
  4. 答疑纪要        — anQuestion (4695 records, 470 pages)
  5. 中标信息        — winBulletin (13437 records, 1344 pages)
  6. 立项信息        — projApproval (7822 records, 783 pages)
  7. 建材供求        — building (4301 records, 431 pages)

Site characteristics
────────────────────
Traditional Java/JSP application with jQuery AJAX.  All data is loaded via
POST requests to ``*.do`` endpoints with a pseudo-JSON body format
(keys are unquoted, e.g. ``{pageIndex:1,pageSize:10,keyword:"",centerId:0}``).

Listing API
   POST each module's paging endpoint → JSON ``{result, data: {totalPage,
   totalRecord, dataList: [...]}}``

Detail content (modules 3-5, projectInfo-based)
   POST /project/getProjBulletin_project.do → fileTitle, fileContent, fileUrl, files
   POST /project/getProjAttachList_project.do → attachment list

Detail content (modules 1, 2, 7)
   Detail pages load content via JavaScript.  Playwright is used to render
   these pages and extract content + attachment links from DOM.

Module 6 (立项信息) has no detail page — listing data is sufficient.

Data flow
─────────
  1. For each module: POST listing API page by page
  2. For each new article: fetch detail content via API or Playwright
  3. Download attachments (PDF/DOC/RAR/ZIP) via direct HTTP
  4. Extract text from downloaded files, decompress ZIP/RAR
  5. Build markdown → save locally → upload to KB
  6. Save state after each batch → resume on next run

Usage
─────
    python quanzhou_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>

    # Optional:
        --max-runtime 3300    # Max runtime before graceful stop
        --full                # Ignore saved state, re-crawl
        --playwright-detail   # Use Playwright for ALL detail pages (slower but more reliable)
"""

import argparse
import json
import logging
import os
import random
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Playwright (optional — only needed for modules 1, 2, 7 detail pages)
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://ggzyjy.quanzhou.gov.cn"
_TAG_PREFIX = "[QZ-GC]"

_PAGE_SIZE = 10
_BATCH_SIZE = 3
_MAX_RUNTIME_DEFAULT = 3300
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.0
_STATE_FILENAME = "_crawler_state.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

_HEADERS_HTML = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

# Module definitions
# (key, label, listing_url, api_url, api_params, detail_url_template, id_field, detail_file_type)
_MODULES = [
    {
        "key": "biddingPlan",
        "label": "招标计划",
        "listing_url": _SITE_ROOT + "/project/biddingPlanList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getBiddingjPage_project.do",
        "api_params": 'classId:0,centerId:0,projName:"",ownerDeptName:""',
        "detail_url_tpl": _SITE_ROOT + "/project/biddingPlanDetail.do?id={id}",
        "id_field": "id",
        "detail_type": "playwright",  # requires JS rendering
    },
    {
        "key": "tenderNotice",
        "label": "招标文件预公告",
        "listing_url": _SITE_ROOT + "/project/tenderNotice.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getTenderNotice.do",
        "api_params": 'classId:0,centerId:0,projName:""',
        "detail_url_tpl": _SITE_ROOT + "/project/getTenderNoticeDetail.do?projid={id}",
        "id_field": "id",
        "detail_type": "playwright",  # requires JS rendering
    },
    {
        "key": "projectList",
        "label": "招标信息",
        "listing_url": _SITE_ROOT + "/project/projectList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getProjPage_project.do",
        "api_params": 'classId:0,centerId:0,projNo:"",projName:"",ownerDeptName:""',
        "detail_url_tpl": _SITE_ROOT + "/project/projectInfo.do?projId={projId}",
        "id_field": "projId",
        "detail_type": "api",  # getProjBulletin_project.do
        "detail_file_type": "'F001'",
    },
    {
        "key": "anQuestion",
        "label": "答疑纪要",
        "listing_url": _SITE_ROOT + "/project/anQuestionList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getAnQuestionPage_project.do",
        "api_params": 'keyword:"",centerId:0',
        "detail_url_tpl": _SITE_ROOT + "/project/projectInfo.do?projId={projId}&leftIndex=2&bullId={aqId}",
        "id_field": "projId",
        "detail_type": "api",
        "detail_file_type": "'F002'",
    },
    {
        "key": "winBulletin",
        "label": "中标信息",
        "listing_url": _SITE_ROOT + "/project/winBulletinList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getwinBulletinPage_project.do",
        "api_params": 'keyword:"",centerId:0',
        "detail_url_tpl": _SITE_ROOT + "/project/projectInfo.do?projId={projId}&leftIndex=3&bullId={bltId}",
        "id_field": "projId",
        "detail_type": "api",
        "detail_file_type": "'F004'",
    },
    {
        "key": "projApproval",
        "label": "立项信息",
        "listing_url": _SITE_ROOT + "/project/projApprovalList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getProjApprovalPage.do",
        "api_params": 'keyword:"",centerId:0',
        "detail_url_tpl": None,  # No detail page
        "id_field": "projid",
        "detail_type": "none",
    },
    {
        "key": "building",
        "label": "建材供求",
        "listing_url": _SITE_ROOT + "/project/buildingList.do?centerId=-1",
        "api_url": _SITE_ROOT + "/project/getBuildingPage.do",
        "api_params": 'classId:0,centerId:0,projName:""',
        "detail_url_tpl": _SITE_ROOT + "/project/buildingDetail.do?projid={ProjID}",
        "id_field": "ProjID",
        "detail_type": "playwright",  # requires JS rendering
    },
]

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(name, max_len=120):
    if not name:
        return "unnamed"
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("._ ")
    if len(safe) > max_len:
        base, ext = os.path.splitext(safe)
        safe = base[:max_len - len(ext)] + (ext or "")
    return safe or "unnamed"


def _normalize_date(date_str):
    """Extract YYYY-MM-DD from various date formats."""
    if not date_str:
        return ""
    m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', str(date_str))
    return m.group(1) if m else str(date_str)[:10]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_post(url, body_str, referer=None):
    """POST with pseudo-JSON body, return decoded text."""
    hdrs = dict(_HEADERS)
    if referer:
        hdrs["Referer"] = referer
    data_bytes = body_str.encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("POST %s failed: %s", url, e)
        return None


def _http_get_html(url, referer=None):
    """GET an HTML page, return text."""
    hdrs = dict(_HEADERS_HTML)
    if referer:
        hdrs["Referer"] = referer
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("GET %s failed: %s", url, e)
        return None


def _http_download(url, referer=None):
    """Download a binary file, return bytes."""
    hdrs = dict(_HEADERS_HTML)
    if referer:
        hdrs["Referer"] = referer
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
            return resp.read()
    except Exception as e:
        logging.warning("Download %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _crawl_listing_module(mod_info):
    """Crawl all listing pages for one module.

    Returns list[dict]: raw article items from API.
    """
    all_items = []
    page_index = 1
    attempted_fallback = False

    referer = mod_info["listing_url"]
    api_params = mod_info["api_params"]

    while True:
        body = "{" + f"pageIndex:{page_index},pageSize:{_PAGE_SIZE},{api_params}" + "}"

        text = _http_post(mod_info["api_url"], body, referer=referer)
        if not text:
            break

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logging.warning("JSON decode error for %s page %d", mod_info["key"], page_index)
            break

        if not data.get("result"):
            logging.warning("API error for %s page %d: %s",
                          mod_info["key"], page_index, data.get("error", ""))
            break

        d = data.get("data", {})
        items = d.get("dataList", [])
        total_page = d.get("totalPage", 0)
        total_record = d.get("totalRecord", 0)

        if not items:
            # ── Fallback: trigger "search" with simpler params ─────────
            if not attempted_fallback and page_index == 1:
                attempted_fallback = True
                logging.info("Module %s page 1 empty — trying fallback params", mod_info["key"])
                # Try with minimal params
                fallback_params = 'classId:0,centerId:0,keyword:""'
                if mod_info["key"] == "biddingPlan":
                    fallback_params = 'classId:0,centerId:0,projName:"",ownerDeptName:""'
                elif mod_info["key"] in ("projectList",):
                    fallback_params = 'classId:0,centerId:0,projNo:"",projName:"",ownerDeptName:""'
                api_params = fallback_params
                continue
            break

        for item in items:
            item["_module_key"] = mod_info["key"]
            item["_module_label"] = mod_info["label"]
        all_items.extend(items)

        if page_index >= total_page:
            break

        page_index += 1
        _request_delay()

    return all_items


# ---------------------------------------------------------------------------
# Detail API for modules 3, 4, 5 (projectInfo-based)
# ---------------------------------------------------------------------------

def _normalize_att_url(url):
    """Ensure an attachment URL is absolute.  Most URLs from the API are
    already absolute; this handles the rare relative case."""
    if not url:
        return url
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    # Some URLs are relative paths under :8088 or :8883
    return "http://ggzyjy.quanzhou.gov.cn:8088" + url


def _extract_bulletin_content(d, download_dir):
    """Extract text content from a bulletin PDF/DOC by downloading it.

    Parameters
    ----------
    d : dict — bulletin item from getProjBulletin_project.do
    download_dir : str — directory to save downloaded files

    Returns
    -------
    (content_text, attachments) — extracted text and list of {filename, url}
    """
    content_text = ""
    attachments = []

    # Prefer fileUrl (direct HTTP), fall back to downPath (requires query params)
    file_url = d.get("fileUrl", "") or d.get("downPath", "") or d.get("sourceUrl", "")
    file_url = _normalize_att_url(file_url)
    file_name = d.get("fileName", "") or "bulletin_file"

    if file_url and file_name:
        attachments.append({"filename": file_name, "url": file_url})

        # Download and extract text
        os.makedirs(download_dir, exist_ok=True)
        safe_name = _sanitize_filename(file_name, max_len=120)
        local_path = os.path.join(download_dir, safe_name)

        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
            data = _http_download(file_url)
            if data:
                with open(local_path, "wb") as f:
                    f.write(data)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            content_text = _extract_text_from_file(local_path) or ""

    # Handle sub-files (bulletin may have embedded file list)
    sub_files = d.get("files", "")
    if sub_files:
        if isinstance(sub_files, list):
            for sf in sub_files:
                if isinstance(sf, dict):
                    sf_name = sf.get("srcfilename", "") or sf.get("fileName", "")
                    sf_url = sf.get("fileUrl", "") or sf.get("downPath", "") or sf.get("sourceUrl", "")
                    sf_url = _normalize_att_url(sf_url)
                    if sf_url and sf_name:
                        attachments.append({"filename": sf_name, "url": sf_url})
        elif isinstance(sub_files, str) and sub_files.strip():
            # Try parsing as JSON
            try:
                sf_list = json.loads(sub_files)
                if isinstance(sf_list, list):
                    for sf in sf_list:
                        if isinstance(sf, dict):
                            sf_name = sf.get("srcfilename", "") or sf.get("fileName", "")
                            sf_url = sf.get("fileUrl", "") or sf.get("downPath", "")
                            sf_url = _normalize_att_url(sf_url)
                            if sf_url and sf_name:
                                attachments.append({"filename": sf_name, "url": sf_url})
            except (json.JSONDecodeError, TypeError):
                pass

    return content_text, attachments


def _fetch_detail_api(mod_info, item):
    """Fetch detail content via getProjBulletin_project.do + getProjAttachList_project.do.

    The bulletin API returns metadata + a downloadable PDF/DOC that contains
    the actual content.  We download and extract text from that file.

    Returns dict: {title, content_text, pub_date, project_info, attachments, views}.
    """
    result = {
        "title": "",
        "content_text": "",
        "pub_date": "",
        "views": "",
        "project_info": {},
        "attachments": [],
    }

    proj_id = item.get("projId", "")
    if not proj_id:
        return result

    referer = _SITE_ROOT + "/project/projectList.do?centerId=-1"
    import tempfile
    dl_dir = os.path.join(tempfile.gettempdir(), "qz_bulletin", str(proj_id))

    # ── Bulletin content ────────────────────────────────────────────────
    file_type = mod_info.get("detail_file_type", "'F001'")
    body = "{" + f"projId:{proj_id},fileType:{file_type}" + "}"

    text = _http_post(
        _SITE_ROOT + "/project/getProjBulletin_project.do",
        body, referer=referer,
    )

    seen_urls = set()

    if text:
        try:
            data = json.loads(text)
            if data.get("result") and data.get("data"):
                d = data["data"]
                if isinstance(d, list) and len(d) > 0:
                    d = d[0]
                if isinstance(d, dict):
                    result["title"] = d.get("fileTitle", "") or d.get("title", "")
                    result["content_text"] = d.get("fileContent", "") or ""
                    if d.get("chkDate"):
                        result["pub_date"] = _normalize_date(d["chkDate"])

                    # Download bulletin file and extract content
                    if not result["content_text"]:
                        bulletin_text, bulletin_atts = _extract_bulletin_content(d, dl_dir)
                        if bulletin_text:
                            result["content_text"] = bulletin_text
                        for att in bulletin_atts:
                            if att["url"] not in seen_urls:
                                result["attachments"].append(att)
                                seen_urls.add(att["url"])
                    else:
                        # fileContent has text — still add the bulletin file as attachment
                        file_url = _normalize_att_url(
                            d.get("fileUrl", "") or d.get("downPath", "")
                        )
                        file_name = d.get("fileName", "")
                        if file_url and file_name and file_url not in seen_urls:
                            result["attachments"].append({
                                "filename": file_name, "url": file_url,
                            })
                            seen_urls.add(file_url)
        except json.JSONDecodeError:
            pass

    # ── Attachment list ──────────────────────────────────────────────────
    _request_delay()
    body2 = "{" + f"projId:{proj_id}" + "}"

    text2 = _http_post(
        _SITE_ROOT + "/project/getProjAttachList_project.do",
        body2, referer=referer,
    )

    if text2:
        try:
            data2 = json.loads(text2)
            if data2.get("result") and isinstance(data2.get("data"), list):
                for att in data2["data"]:
                    if not isinstance(att, dict):
                        continue
                    # Prefer direct fileUrl, then notneedloginurl (public access)
                    att_url = _normalize_att_url(
                        att.get("fileUrl", "") or
                        att.get("notneedloginurl", "") or
                        att.get("sourceUrl", "") or
                        att.get("downPath", "")
                    )
                    att_name = (
                        att.get("fileTitle", "") or
                        att.get("fileName", "") or
                        "attachment"
                    )
                    if att_url and att_url not in seen_urls:
                        result["attachments"].append({
                            "filename": att_name,
                            "url": att_url,
                        })
                        seen_urls.add(att_url)
        except json.JSONDecodeError:
            pass

    # ── Project detail metadata ──────────────────────────────────────────
    _request_delay()
    body3 = "{" + f"projId:{proj_id}" + "}"
    text3 = _http_post(
        _SITE_ROOT + "/project/getProjDetail_project.do",
        body3, referer=referer,
    )
    if text3:
        try:
            data3 = json.loads(text3)
            if data3.get("result") and data3.get("data"):
                d3 = data3["data"]
                if isinstance(d3, dict):
                    result["project_info"] = {
                        "projName": d3.get("projName", ""),
                        "projNo": d3.get("projNo", ""),
                        "buildArea": d3.get("buildArea", ""),
                        "fundSource": d3.get("fundSource", ""),
                        "totalInvest": d3.get("totalInvest", ""),
                        "ownerDeptName": d3.get("ownerdeptname", ""),
                        "agentDept": d3.get("agentDept", ""),
                        "projTradeType": d3.get("projTradeType", ""),
                    }
        except json.JSONDecodeError:
            pass

    # ── Fill title from listing data if empty ────────────────────────────
    if not result["title"]:
        result["title"] = (
            item.get("projName", "") or item.get("bltTitle", "") or
            item.get("aqTitle", "") or ""
        )
    if not result["pub_date"]:
        result["pub_date"] = _normalize_date(
            item.get("pubDate", "") or item.get("auditDate", "")
        )

    return result


# ---------------------------------------------------------------------------
# Detail via Playwright (for modules 1, 2, 7)
# ---------------------------------------------------------------------------

def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _fetch_detail_playwright(browser, mod_info, item):
    """Use Playwright to render a detail page and extract content + attachments.

    Returns dict: {title, content_text, pub_date, attachments, views}.
    """
    result = {
        "title": "",
        "content_text": "",
        "pub_date": "",
        "views": "",
        "attachments": [],
    }

    if not PLAYWRIGHT_AVAILABLE:
        return result

    id_value = item.get(mod_info["id_field"], "")
    if not id_value:
        return result

    detail_url = mod_info["detail_url_tpl"].format(**{mod_info["id_field"]: id_value})

    ctx = browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
    )
    page = ctx.new_page()
    page.set_default_timeout(30000)

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        # Extract content via JS evaluation
        extracted = page.evaluate("""() => {
            const res = {title: '', content_text: '', pub_date: '', attachments: []};

            // Try to find title
            const titleSelectors = [
                '.detail-title', '.article-title', 'h1', 'h2', 'h3',
                '.font22', '#ProjectName', '[class*="title"]'
            ];
            for (const sel of titleSelectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 3) {
                    res.title = el.textContent.trim();
                    break;
                }
            }

            // Try to find date
            const bodyText = document.body.innerText || '';
            const dateMatch = bodyText.match(/(\\d{4}[-/]\\d{1,2}[-/]\\d{1,2})/);
            if (dateMatch) res.pub_date = dateMatch[1];

            // Try to find content area
            const contentSelectors = [
                '#ProjectReleDetail', '.detail-content', '.article-content',
                '[class*="content"]', '[class*="detail"]', '.jsgc_nr',
                'main', 'article'
            ];
            for (const sel of contentSelectors) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim().length > 50) {
                    res.content_text = el.innerText.trim();
                    break;
                }
            }
            if (!res.content_text) {
                // Try to get body text minus header/footer
                const remove = document.querySelectorAll(
                    'nav, .top-nav, .header, .footer, .menu, .el-menu'
                );
                remove.forEach(el => el.remove());
                res.content_text = document.body.innerText.trim();
            }

            // Find attachment links
            const links = document.querySelectorAll('a[href]');
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                const text = (a.textContent || '').trim();
                if (/\\.(pdf|doc|docx|xls|xlsx|rar|zip|7z)$/i.test(href) ||
                    /filedown|download|uploadfile|attach/i.test(href)) {
                    res.attachments.push({
                        filename: text || href.split('/').pop().split('?')[0],
                        url: href
                    });
                }
            }

            return res;
        }""")

        result["title"] = extracted.get("title", "")
        result["content_text"] = extracted.get("content_text", "")
        result["pub_date"] = extracted.get("pub_date", "")
        for att in extracted.get("attachments", []):
            url = att.get("url", "")
            if url and not url.startswith("http"):
                url = urllib.parse.urljoin(detail_url, url)
            result["attachments"].append({
                "filename": att.get("filename", "attachment"),
                "url": url,
            })

        # Fallback title from listing
        if not result["title"]:
            result["title"] = item.get("PROJNAME", "") or item.get("ProjName", "") or item.get("projName", "")
        if not result["pub_date"]:
            result["pub_date"] = _normalize_date(
                item.get("AUDITTIME", "") or item.get("AUDIT_TIME", "") or
                item.get("bulletinIssueTime", "") or item.get("pubDate", "")
            )

    except Exception as e:
        logging.warning("Playwright detail failed for id=%s: %s", id_value, e)
    finally:
        try:
            ctx.close()
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Attachment download + processing
# ---------------------------------------------------------------------------

def _download_attachments(attachments, download_dir):
    """Download attachment files. Returns list of local file paths."""
    os.makedirs(download_dir, exist_ok=True)
    local_files = []

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue

        fname = _sanitize_filename(att.get("filename", "attachment"), max_len=120)
        ext = os.path.splitext(urllib.parse.urlparse(url).path.split("?")[0])[1].lower()
        if ext and not fname.lower().endswith(ext):
            fname += ext
        if not ext and "." not in fname:
            fname += ".pdf"

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        data = _http_download(url)
        if data:
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
            _request_delay()

    return local_files


def _extract_zip(filepath):
    """Extract ZIP file, return list of extracted file paths."""
    extracted = []
    extract_dir = os.path.splitext(filepath)[0] + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(name, max_len=120)
                out_path = os.path.join(extract_dir, safe_name)
                parent_dir = os.path.dirname(out_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except Exception as e:
        logging.warning("ZIP extraction failed for %s: %s", filepath, e)
    return extracted


# ---------------------------------------------------------------------------
# Text extraction from binary files
# ---------------------------------------------------------------------------

def _extract_text_from_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber
            parts = []
            with pdfplumber.open(filepath) as pdf:
                for pg in pdf.pages:
                    text = pg.extract_text()
                    if text:
                        parts.append(text)
            return "\n\n".join(parts)
        elif ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) + "\n".join(rows))
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return ""


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(title, pub_date, views, content_text, attachments,
                    download_dir, detail_url, module_label, project_info=None):
    lines = [
        "# {}".format(title or "无标题"),
        "",
        "**数据来源:** 泉州市公共资源交易信息网 — {}".format(module_label),
        "**页面地址:** {}".format(detail_url),
        "**抓取时间:** {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    if pub_date:
        lines.append("**发布时间:** {}".format(pub_date))
    if views:
        lines.append("**浏览量:** {}".format(views))

    # Project metadata
    if project_info:
        meta_lines = []
        if project_info.get("projNo"):
            meta_lines.append("**项目编号:** {}".format(project_info["projNo"]))
        if project_info.get("ownerDeptName"):
            meta_lines.append("**建设单位:** {}".format(project_info["ownerDeptName"]))
        if project_info.get("agentDept"):
            meta_lines.append("**招标代理:** {}".format(project_info["agentDept"]))
        if project_info.get("buildArea"):
            meta_lines.append("**建设地点:** {}".format(project_info["buildArea"]))
        if project_info.get("totalInvest"):
            meta_lines.append("**总投资额:** {} 万元".format(project_info["totalInvest"]))
        if project_info.get("fundSource"):
            meta_lines.append("**资金来源:** {}".format(project_info["fundSource"]))
        if meta_lines:
            lines.extend(meta_lines)
    lines.append("")

    if content_text:
        lines.append("---")
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
        if len(content_clean) > 100000:
            content_clean = content_clean[:100000] + "\n\n（内容过长，已截断）"
        lines.append(content_clean)
        lines.append("")

    if attachments:
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")
        for att in attachments:
            fname = att.get("filename", "unknown")
            att_url = att.get("url", "")
            lines.append("- [{}]({})".format(fname, att_url))
        lines.append("")

        if download_dir and os.path.isdir(download_dir):
            lines.append("### 附件内容")
            lines.append("")
            for att in attachments:
                fname = att.get("filename", "")
                local_path = os.path.join(download_dir, fname)
                safe_name = _sanitize_filename(fname, max_len=120)
                if not os.path.exists(local_path):
                    alt_path = os.path.join(download_dir, safe_name)
                    if os.path.exists(alt_path):
                        local_path = alt_path
                if not os.path.exists(local_path):
                    for root, _, files in os.walk(download_dir):
                        for fn in files:
                            if fn == safe_name or fn == fname:
                                local_path = os.path.join(root, fn)
                                break
                if not os.path.exists(local_path):
                    continue

                lines.append("#### {}".format(fname))
                lines.append("")
                extracted_text = _extract_text_from_file(local_path)
                if extracted_text and extracted_text.strip():
                    if len(extracted_text) > 50000:
                        extracted_text = extracted_text[:50000] + "\n\n（内容过长，已截断）"
                    lines.append(extracted_text)
                else:
                    lines.append("（无法提取文本内容）")
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
    return {"processed_ids": [], "module_pages": {}}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(md_content, attachment_files, kb_id, tenant_id, folder_name):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FO("{}.md".format(folder_name), md_content.encode("utf-8"))
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("MD upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)

    for fp in attachment_files:
        fname = os.path.basename(fp)
        with open(fp, "rb") as f:
            blob = f.read()
        fo2 = _FO(fname, blob)
        errs2, pairs2 = FileService.upload_document(kb, [fo2], tenant_id)
        if errs2:
            logging.warning("Attachment upload errors: %s", errs2)
        for doc, _ in pairs2:
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
        description="quanzhou_crawler — 泉州市公共资源交易信息网 工程建设"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds (default: 3300)")
    p.add_argument("--playwright-detail", action="store_true",
                   help="Use Playwright for ALL detail pages (slower but more reliable)")
    for opt in ("--max-days", "--hours", "--max-articles",
                "--llm-id", "--llm-model", "--access-token", "--target-url"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] 泉州市公共资源交易信息网 — 工程建设 crawler".format(_TAG_PREFIX))
    _safe_print("[{}] KB: {}".format(_TAG_PREFIX, args.kb_id))
    _safe_print("[{}] Task: {}".format(_TAG_PREFIX, args.task_name))
    _safe_print("[{}] Max runtime: {}s".format(_TAG_PREFIX, args.max_runtime))
    _safe_print("[{}] Modules: {}".format(_TAG_PREFIX, ", ".join(m["label"] for m in _MODULES)))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== QZ-GC crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[{}] Output: {}\n".format(_TAG_PREFIX, output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {"processed_ids": [], "module_pages": {}}
    processed_ids = set(state.get("processed_ids", []))
    module_pages = state.get("module_pages", {})

    _safe_print("[{}] Already processed: {} article(s)".format(_TAG_PREFIX, len(processed_ids)))
    sys.stdout.flush()

    crawl_start = time.time()
    downloads_dir = os.path.join(output_dir, "downloads")

    # ── Playwright (if needed) ──────────────────────────────────────────
    browser = None
    pw = None
    use_playwright = args.playwright_detail or any(
        m["detail_type"] == "playwright" for m in _MODULES
    )
    if use_playwright:
        if not PLAYWRIGHT_AVAILABLE:
            _safe_print("[{}] WARNING: playwright not installed. "
                        "Modules requiring JS rendering will be skipped.".format(_TAG_PREFIX))
            sys.stdout.flush()
            use_playwright = False
        else:
            chrome_path = _find_chrome()
            if not chrome_path:
                _safe_print("[{}] WARNING: Chrome not found.".format(_TAG_PREFIX))
                sys.stdout.flush()
                use_playwright = False
            else:
                pw = sync_playwright().start()
                browser = pw.chromium.launch(
                    headless=True,
                    executable_path=chrome_path,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-sandbox", "--disable-gpu"],
                )
                _safe_print("[{}] Playwright browser started".format(_TAG_PREFIX))
                sys.stdout.flush()

    try:
        processed_count = 0
        stopped_early = False

        for mod in _MODULES:
            mod_key = mod["key"]
            _safe_print("\n[{}] ── Module: {} ({}) ──".format(_TAG_PREFIX, mod["label"], mod_key))
            sys.stdout.flush()

            # ── Step 1: Crawl listing ─────────────────────────────────
            _safe_print("[{}]   Crawling listing...".format(_TAG_PREFIX))
            sys.stdout.flush()

            all_items = _crawl_listing_module(mod)
            _safe_print("[{}]   Found {} article(s)".format(_TAG_PREFIX, len(all_items)))
            sys.stdout.flush()

            if not all_items:
                continue

            # Filter already-processed
            new_items = []
            for item in all_items:
                item_id = "{}_{}".format(mod_key, item.get(mod["id_field"], ""))
                if item_id not in processed_ids:
                    new_items.append(item)

            skipped = len(all_items) - len(new_items)
            if skipped:
                _safe_print("[{}]   {} already processed, {} new".format(
                    _TAG_PREFIX, skipped, len(new_items)))
                sys.stdout.flush()

            if not new_items:
                continue

            # ── Step 2: Process each article ──────────────────────────
            for i, item in enumerate(new_items, 1):
                elapsed = time.time() - crawl_start
                remaining = args.max_runtime - elapsed
                if remaining < 120:
                    _safe_print(
                        "\n[{}] Runtime {:.0f}s, {:.0f}s remaining. "
                        "Stopping gracefully. {} processed.".format(
                            _TAG_PREFIX, elapsed, remaining, processed_count))
                    sys.stdout.flush()
                    stopped_early = True
                    break

                id_field = mod["id_field"]
                item_id_val = item.get(id_field, "")
                item_id = "{}_{}".format(mod_key, item_id_val)

                # Get listing-level metadata
                if mod_key in ("projectList",):
                    list_title = item.get("projName", "")
                    list_date = _normalize_date(item.get("auditDate", ""))
                elif mod_key in ("anQuestion",):
                    list_title = item.get("aqTitle", "")
                    list_date = _normalize_date(item.get("pubDate", ""))
                elif mod_key in ("winBulletin",):
                    list_title = item.get("bltTitle", "")
                    list_date = _normalize_date(item.get("pubDate", ""))
                elif mod_key in ("biddingPlan",):
                    list_title = item.get("PROJNAME", "")
                    list_date = _normalize_date(item.get("AUDITTIME", ""))
                elif mod_key in ("tenderNotice",):
                    list_title = item.get("PROJNAME", "")
                    list_date = _normalize_date(item.get("AUDIT_TIME", ""))
                elif mod_key in ("projApproval",):
                    list_title = item.get("projname", "")
                    list_date = ""
                elif mod_key in ("building",):
                    list_title = item.get("ProjName", "")
                    list_date = _normalize_date(item.get("bulletinIssueTime", ""))
                else:
                    list_title = ""
                    list_date = ""

                _safe_print("[{}] [{}/{}] {}: {}...".format(
                    _TAG_PREFIX, i, len(new_items), mod["label"],
                    list_title[:50] if list_title else "(no title)"))
                sys.stdout.flush()

                # ── Fetch detail ─────────────────────────────────────
                detail = {}
                dt = mod.get("detail_type", "none")

                if dt == "api" or args.playwright_detail:
                    if args.playwright_detail and browser:
                        detail = _fetch_detail_playwright(browser, mod, item)
                    else:
                        detail = _fetch_detail_api(mod, item)
                elif dt == "playwright":
                    if browser:
                        detail = _fetch_detail_playwright(browser, mod, item)
                elif dt == "none":
                    # Module 6 (立项信息) - no detail page
                    detail = {
                        "title": list_title,
                        "content_text": "项目名称: {}\n立项状态: {}\n项目编号: {}".format(
                            item.get("projname", ""),
                            "已立项" if item.get("isProjApproval") == 1 else "未立项",
                            item.get("projno", ""),
                        ),
                        "pub_date": list_date,
                        "views": "",
                        "attachments": [],
                    }

                # Build detail URL
                if mod["detail_url_tpl"]:
                    detail_url = mod["detail_url_tpl"]
                    for k, v in item.items():
                        if "{" + k + "}" in detail_url:
                            detail_url = detail_url.replace("{" + k + "}", str(v))
                else:
                    detail_url = mod["listing_url"]

                title = detail.get("title", "") or list_title
                pub_date = detail.get("pub_date", "") or list_date
                views = detail.get("views", "")
                content_text = detail.get("content_text", "")
                attachments = detail.get("attachments", [])
                project_info = detail.get("project_info", {})

                # ── Download attachments ────────────────────────────
                local_files = []
                article_dl_dir = ""
                if attachments:
                    dl_name = "{}_{}_{}".format(
                        mod_key,
                        str(item_id_val)[:20],
                        _sanitize_filename(title[:30], 40)
                    )
                    article_dl_dir = os.path.join(downloads_dir, dl_name)
                    local_files = _download_attachments(attachments, article_dl_dir)

                    # Check for ZIP files and extract
                    for fp in list(local_files):
                        is_zip = fp.lower().endswith(".zip")
                        if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                            with open(fp, "rb") as f:
                                is_zip = f.read(4) == b"PK\x03\x04"
                        if is_zip:
                            extracted = _extract_zip(fp)
                            local_files.remove(fp)
                            local_files.extend(extracted)

                        # Also handle RAR files (rename to trigger extraction if possible)
                        is_rar = fp.lower().endswith(".rar")
                        if is_rar:
                            logging.info("RAR file (no extraction): %s", fp)

                # ── Build markdown ──────────────────────────────────
                md_content = _build_markdown(
                    title, pub_date, views, content_text,
                    attachments, article_dl_dir, detail_url, mod["label"],
                    project_info=project_info,
                )

                # Save markdown locally
                date_for_name = pub_date or datetime.now().strftime("%Y-%m-%d")
                folder_name = _sanitize_filename(
                    "{}_{}_{}_{}".format(
                        date_for_name, mod_key, str(item_id_val)[:16], title[:40]
                    ), max_len=120
                )
                md_path = os.path.join(output_dir, "{}.md".format(folder_name))
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                _safe_print("[{}]   Saved ({} chars, {} attachments)".format(
                    _TAG_PREFIX, len(md_content), len(local_files)))
                sys.stdout.flush()

                # Upload to KB
                if args.kb_id:
                    try:
                        _upload_to_kb(md_content, local_files, args.kb_id,
                                      args.tenant_id, folder_name)
                    except Exception as e:
                        logging.error("KB upload failed: %s", e)
                        _save_state(output_dir, {
                            "processed_ids": list(processed_ids),
                            "module_pages": module_pages,
                        })
                        _safe_print("[{}]   Upload error: {}".format(_TAG_PREFIX, e))
                        sys.stdout.flush()

                processed_ids.add(item_id)
                processed_count += 1

                if processed_count % _BATCH_SIZE == 0:
                    _save_state(output_dir, {
                        "processed_ids": list(processed_ids),
                        "module_pages": module_pages,
                    })
                    _safe_print("[{}]   Checkpoint ({} processed)".format(
                        _TAG_PREFIX, processed_count))
                    sys.stdout.flush()

            if stopped_early:
                break

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    # ── Final state ────────────────────────────────────────────────────
    state = {"processed_ids": list(processed_ids), "module_pages": module_pages}
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] Crawl complete — {} new article(s)".format(_TAG_PREFIX, processed_count))
    if stopped_early:
        _safe_print("[{}] Stopped early, will resume next run".format(_TAG_PREFIX))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== QZ-GC crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "quanzhou_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
