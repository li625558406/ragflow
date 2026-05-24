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
Crawler for ggzy.longyan.gov.cn/lyztb/ — 龙岩市公共资源交易中心 交易信息.

Target: https://ggzy.longyan.gov.cn/lyztb/

Modules covered:
  工程建设 (gcjs) with sub-types:
    房屋建筑工程, 市政基础设施工程, 交通工程, 水利工程,
    信息化工程, 工业项目, 其他工程
  政府采购 (zqcg)
  企业采购 (zqcg/008005)
  产权交易 (tdky/011006)
  土地矿业 (tdky/011007)

Each module has tab pages (招标公告, 中标候选人, 中标结果, etc.)

Site characteristics
────────────────────
Epoint WebBuilder platform with OAuth 2.0 anonymous token flow.
Listing API (getPageInfoListNew) requires captcha verification, so we use
server-rendered listing pages (moreinfo.html / secondpage.html) instead.

Detail pages are server-rendered HTML with content in #mainContent div.
Attachments use downloadztbattach gateway (same as Ningde).

Usage
─────
    python longyan_crawler.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>

    # Optional:
        --max-runtime 3300    # Max runtime before graceful stop (default: 3300)
        --full                # Ignore saved state, re-crawl all
        --date YYYY-MM-DD     # Filter by date (default: today)
        --modules MOD1,MOD2   # Comma-separated module keys (default: all)
"""
import argparse
import html as html_mod
import json
import logging
import os
import random
import re
import ssl
import sys
import time
import urllib.parse
import zipfile
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://ggzy.longyan.gov.cn"
_TAG_PREFIX = "[LY]"

_PAGE_SIZE = 20
_BATCH_SIZE = 3
_MAX_RUNTIME_DEFAULT = 3300
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.2
_STATE_FILENAME = "_crawler_state.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
_PROJECT_NAME = "/EpointWebBuilder"
_APP_URL_FLAG = "ztb001"

# API endpoints
_API_TOKEN = _SITE_ROOT + _PROJECT_NAME + "/rest/getOauthInfoAction/getNoUserAccessToken"
_API_ATTACH = _SITE_ROOT + _PROJECT_NAME + "/webbuildermis/attach/ztbAttachDownloadAction.action"

# ---------------------------------------------------------------------------
# Module definitions
# Each module: (key, name, path_prefix, category_root, listing_pages)
# listing_pages: list of (category_path, tab_name)
# ---------------------------------------------------------------------------

_GCJS_CATEGORIES = {
    "007004": "房屋建筑工程",
    "007005": "市政基础设施工程",
    "007006": "交通工程",
    "007007": "水利工程",
    "007008": "信息化工程",
    "007009": "其他工程",
    "007010": "工业项目",
}

_GCJS_TABS = {
    "001": "招标公告信息",
    "002": "标段结果",
    "003": "中标候选人公示",
    "004": "中标结果公示",
    "005": "合同签订",
}

_ZQCG_CATEGORIES = {
    "008001": "政府采购(市级)",
    "008003": "政府采购(区县)",
}

_ZQCG_TABS = {
    "001": "预公告",
    "002": "招标公告",
    "003": "中标、成交结果",
}

_QYCG_TABS = {
    "001": "招标公告",
    "002": "变更公告",
    "003": "中标、成交结果",
    "004": "预公告",
}

_TDKY_CATEGORIES = {
    "011006": "产权交易",
    "011007": "土地矿业",
}


def _build_module_list():
    """Build the full list of modules with their listing page URLs."""
    modules = []

    # --- 工程建设 ---
    for cat_code, cat_name in _GCJS_CATEGORIES.items():
        for tab_code, tab_name in _GCJS_TABS.items():
            # Try both moreinfo.html and secondpage.html
            modules.append({
                "key": f"gcjs_{cat_code}_{tab_code}",
                "name": f"工程建设/{cat_name}/{tab_name}",
                "listing_urls": [
                    f"{_SITE_ROOT}/lyztb/gcjs/{cat_code}/{cat_code}{tab_code}/moreinfo.html",
                    f"{_SITE_ROOT}/lyztb/gcjs/{cat_code}/{cat_code}{tab_code}/secondpage.html",
                ],
                "path_prefix": f"/lyztb/gcjs/{cat_code}/{cat_code}{tab_code}/",
            })
        # Sub-tab pages under 招标公告信息 (001001, 001002)
        for sub_tab_code in ["001", "002"]:
            modules.append({
                "key": f"gcjs_{cat_code}_001_{sub_tab_code}",
                "name": f"工程建设/{cat_name}/招标公告信息/子标签{sub_tab_code}",
                "listing_urls": [
                    f"{_SITE_ROOT}/lyztb/gcjs/{cat_code}/{cat_code}001/{cat_code}001{sub_tab_code}/secondpage.html",
                ],
                "path_prefix": f"/lyztb/gcjs/{cat_code}/{cat_code}001/{cat_code}001{sub_tab_code}/",
            })

    # --- 政府采购 ---
    for cat_code, cat_name in _ZQCG_CATEGORIES.items():
        for tab_code, tab_name in _ZQCG_TABS.items():
            modules.append({
                "key": f"zqcg_{cat_code}_{tab_code}",
                "name": f"政府采购/{cat_name}/{tab_name}",
                "listing_urls": [
                    f"{_SITE_ROOT}/lyztb/zqcg/{cat_code}/{cat_code}{tab_code}/secondpage.html",
                ],
                "path_prefix": f"/lyztb/zqcg/{cat_code}/{cat_code}{tab_code}/",
            })

    # --- 企业采购 ---
    for tab_code, tab_name in _QYCG_TABS.items():
        modules.append({
            "key": f"qycg_{tab_code}",
            "name": f"企业采购/{tab_name}",
            "listing_urls": [
                f"{_SITE_ROOT}/lyztb/zqcg/008005/008005{tab_code}/secondpage.html",
            ],
            "path_prefix": f"/lyztb/zqcg/008005/008005{tab_code}/",
        })

    # --- 产权交易 (011006 — uses different URL pattern) ---
    modules.append({
        "key": "cqjy",
        "name": "产权交易",
        "listing_urls": [
            f"{_SITE_ROOT}/lyztb/tdky/011006/moreinfo.html",
        ],
        "path_prefix": "/lyztb/tdky/011006/",
    })

    # --- 土地矿业 (011007) ---
    modules.append({
        "key": "tdky",
        "name": "土地矿业",
        "listing_urls": [
            f"{_SITE_ROOT}/lyztb/tdky/011007/011007001/moreinfo.html",
            f"{_SITE_ROOT}/lyztb/tdky/011007/moreinfo.html",
        ],
        "path_prefix": "/lyztb/tdky/011007/",
    })

    return modules


_MODULES = _build_module_list()

_HEADERS_JSON = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

_HEADERS_HTML = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_HEADERS_DOWNLOAD = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Global token cache
_token_cache = {"token": None, "expires": 0}


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
    if not date_str:
        return ""
    m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', str(date_str))
    return m.group(1) if m else str(date_str)[:10]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_post_raw(url, body_str, extra_headers=None):
    hdrs = dict(_HEADERS_JSON)
    if extra_headers:
        hdrs.update(extra_headers)
    data_bytes = body_str.encode("utf-8") if body_str else b""
    req = urllib.request.Request(url, data=data_bytes, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("POST %s failed: %s", url, e)
        return None


def _http_get_html(url, referer=None):
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
    hdrs = dict(_HEADERS_DOWNLOAD)
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
# OAuth token
# ---------------------------------------------------------------------------

def _get_token():
    global _token_cache
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    text = _http_post_raw(_API_TOKEN, "")
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning("Token JSON decode error")
        return None

    token = data.get("custom", {}).get("access_token", "")
    if token:
        _token_cache["token"] = token
        _token_cache["expires"] = now + 1800
    return token


# ---------------------------------------------------------------------------
# Listing — parse server-rendered HTML
# ---------------------------------------------------------------------------

def _fetch_listing_page(url):
    """Fetch a server-rendered listing page and extract article links.

    Returns list of dicts: {guid, title, url, date}
    """
    html = _http_get_html(url)
    if not html:
        return []

    articles = []
    # Pattern: <a href="/lyztb/.../YYYYMMDD/GUID.html" ... title="TITLE">...</a>
    # followed by <span class="list-date">DATE</span>
    pattern = re.compile(
        r'<a\s+href="(/lyztb/[^"]+?/(\d{8})/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.html)"'
        r'[^>]*title="([^"]*)"',
        re.IGNORECASE,
    )
    for m in pattern.finditer(html):
        full_url = m.group(1)
        date_str = m.group(2)
        guid = m.group(3)
        title = html_mod.unescape(m.group(4)).strip()
        formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        articles.append({
            "guid": guid,
            "title": title,
            "url": _SITE_ROOT + full_url,
            "date": formatted_date,
            "path": full_url,
        })

    # Deduplicate by guid
    seen = set()
    unique = []
    for a in articles:
        if a["guid"] not in seen:
            seen.add(a["guid"])
            unique.append(a)
    return unique


def _crawl_listing(target_date):
    """Crawl all module listing pages for the given date.

    Returns list[dict]: all articles matching the target date, deduplicated by guid.
    """
    all_items = []
    seen_guids = set()

    for module in _MODULES:
        logging.info("Listing: %s", module["name"])
        for url in module["listing_urls"]:
            _request_delay()
            articles = _fetch_listing_page(url)
            logging.info("  %s -> %d articles", url.split("/")[-2], len(articles))
            for art in articles:
                if art["guid"] not in seen_guids:
                    # Filter by date if target_date specified
                    if target_date and art["date"] != target_date:
                        continue
                    seen_guids.add(art["guid"])
                    art["module"] = module
                    all_items.append(art)

    logging.info("Total unique articles for %s: %d",
                 target_date or "all", len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Detail page — extract content from server-rendered HTML
# ---------------------------------------------------------------------------

def _fetch_detail_content(detail_url):
    """Fetch and extract content from a detail page.

    Returns dict: {title, info_time, content_html, content_text, attachments}
    """
    html = _http_get_html(detail_url)
    if not html:
        return None

    result = {
        "title": "",
        "info_time": "",
        "content_html": "",
        "content_text": "",
        "attachments": [],
        "metadata": {},
    }

    # Title: <h3 class="bigtitle">
    m = re.search(r'<h3\s+class="bigtitle">(.+?)</h3>', html, re.DOTALL)
    if m:
        result["title"] = html_mod.unescape(m.group(1)).strip()

    # Info time
    m = re.search(r'信息时间：(\d{4}-\d{2}-\d{2})', html)
    if m:
        result["info_time"] = m.group(1)

    # Extract metadata from sub-cp area
    m = re.search(r'<p\s+class="sub-cp">(.*?)</p>', html, re.DOTALL)
    if m:
        meta_raw = m.group(1)
        # Extract source
        src_m = re.search(r'信息来源：<span[^>]*>([^<]*)</span>', meta_raw)
        if src_m:
            result["metadata"]["source"] = src_m.group(1).strip()
        # Extract reading count
        count_m = re.search(r'阅读次数：<span[^>]*>(\d*)</span>', meta_raw)
        if count_m:
            result["metadata"]["read_count"] = count_m.group(1)

    # Main content: div#mainContent
    # Find the start of mainContent
    idx = html.find('id="mainContent"')
    if idx >= 0:
        content_start = html.find('>', idx) + 1
        # Find the end — look for ewb-page-lookup, footrt, or subfooter
        end_markers = ['ewb-page-lookup', 'footrt', 'subfooter']
        content_end = len(html)
        for marker in end_markers:
            marker_idx = html.find(marker, content_start)
            if marker_idx > 0:
                # Go back to find the closing divs
                # Look backwards for the appropriate closing point
                content_end = marker_idx
                break
        content_html = html[content_start:content_end].strip()
        # Remove the blockchain verification div
        content_html = re.sub(
            r'<div\s+class="chain"[^>]*>.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>',
            '', content_html, flags=re.DOTALL)
        result["content_html"] = content_html
        result["content_text"] = _html_to_text(content_html)

    # Attachments
    result["attachments"] = _extract_attachments_from_html(html, detail_url)

    return result


def _html_to_text(html_str):
    """Convert HTML content to plain text."""
    if not html_str:
        return ""
    # Remove scripts and styles
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.I)
    # Convert block elements to newlines
    text = re.sub(r'</?(?:div|p|tr|h[1-6]|li|br)[^>]*>', '\n', text, flags=re.I)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode entities
    text = html_mod.unescape(text)
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def _extract_attachments_from_html(html_str, page_url):
    """Extract attachment download links from detail page HTML.

    Returns list of dicts: {name, guid, url, real_url}
    The 'url' is the gateway page; 'real_url' is the direct download URL.
    """
    attachments = []
    seen = set()

    # Pattern: ztbfjyz('/EpointWebBuilder/pages/.../downloadztbattach?attachGuid=...','1','1')
    for m in re.finditer(
        r"ztbfjyz\('([^']+/downloadztbattach\?attachGuid="
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r"[^']*)'",
        html_str, re.I,
    ):
        gateway_path = m.group(1)
        guid = m.group(2)
        if guid in seen:
            continue
        seen.add(guid)

        gateway_url = _SITE_ROOT + ("" if gateway_path.startswith("/") else "/") + gateway_path

        # Extract file name from the enclosing <a> tag
        # The onclick attribute is inside an <a> tag with title and link text AFTER it
        name = f"{guid}.unknown"
        post_context = html_str[m.end():m.end() + 500]
        # Try title attribute first
        name_m = re.search(r'title="([^"]+)"', post_context)
        if not name_m:
            # Try link text
            name_m = re.search(r'>([^<]{3,120})</a>', post_context)
        if name_m:
            name = name_m.group(1).strip()

        # Construct the direct download URL (bypassing gateway JS)
        real_url = (
            f"{_SITE_ROOT}{_PROJECT_NAME}/webbuildermis/attach/"
            f"ztbAttachDownloadAction.action?cmd=getContent"
            f"&attachGuid={guid}&appUrlFlag={_APP_URL_FLAG}&siteGuid={_SITE_GUID}"
        )

        attachments.append({
            "name": _sanitize_filename(name),
            "guid": guid,
            "url": gateway_url,
            "real_url": real_url,
        })

    return attachments


# ---------------------------------------------------------------------------
# Chrome discovery (for Playwright)
# ---------------------------------------------------------------------------

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachments(attachments, output_dir, detail_title):
    """Download attachments to output_dir.

    NOTE: Longyan site requires a visual captcha (4-char image) for ALL
    attachment downloads — see ztbfjyz() in detail.js.  The captcha dialog
    is shown via layer.open() and must be solved before the gateway page
    auto-submits its form and triggers the download.

    This function logs the attachment metadata and returns empty list.
    Attachment download URLs are included in the generated markdown.

    To implement captcha solving in the future:
      1. Navigate to the detail page in Playwright
      2. Click the attachment link (triggers ztbfjyz → captcha dialog)
      3. Get captcha image + verificationGuid from the pageVerify.html iframe
      4. OCR the 4-char captcha (78×26px, 1 interference line)
      5. Enter code, click confirm → gateway URL loads in #yzmhide iframe
      6. Catch the download event from the gateway page's form.submit()
    """
    downloaded = []
    os.makedirs(output_dir, exist_ok=True)

    for att in attachments:
        name = att.get("name", "unnamed")
        dl_url = att.get("real_url", att.get("url", ""))
        logging.info("  Attachment: %s", name)
        logging.info("    Download URL: %s", dl_url)
        logging.info("    (requires captcha — see markdown for link)")

    return downloaded


# ---------------------------------------------------------------------------
# Build markdown
# ---------------------------------------------------------------------------

def _build_markdown(detail_data, listing_item, module_info):
    """Build markdown document from detail data."""
    title = detail_data.get("title") or listing_item.get("title", "无标题")
    info_time = detail_data.get("info_time") or listing_item.get("date", "")
    content_text = detail_data.get("content_text", "")
    attachments = detail_data.get("attachments", [])
    metadata = detail_data.get("metadata", {})

    lines = []
    # Title
    lines.append(f"# {title}")
    lines.append("")
    # Metadata
    lines.append(f"**数据来源:** 龙岩市公共资源交易中心 — {module_info.get('name', '交易信息')}")
    lines.append(f"**页面地址:** {listing_item.get('url', '')}")
    lines.append(f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**信息时间:** {info_time}")
    if metadata.get("source"):
        lines.append(f"**信息来源:** {metadata['source']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Content
    if content_text:
        lines.append(content_text)
    else:
        lines.append("*(本文无文字内容)*")

    # Attachments
    if attachments:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")
        lines.append("*(附件下载需验证码，以下为下载链接)*")
        lines.append("")
        for att in attachments:
            name = att.get("name", "附件")
            dl_url = att.get("real_url", att.get("url", ""))
            lines.append(f"- [{name}]({dl_url})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state(state_dir):
    state_path = os.path.join(state_dir, _STATE_FILENAME)
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_ids": [], "last_run": None}


def _save_state(state_dir, state):
    state_path = os.path.join(state_dir, _STATE_FILENAME)
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("Failed to save state: %s", e)


# ---------------------------------------------------------------------------
# Knowledge base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
    """Upload a markdown file to RAGFlow knowledge base."""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.document_service import DocumentService
    from api.db.db_models import TaskTimerType

    try:
        kb = KnowledgebaseService.get_by_id(kb_id)
        if not kb:
            logging.error("KB not found: %s", kb_id)
            return False

        filename = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        doc_id = get_uuid()
        doc = {
            "id": doc_id,
            "name": filename,
            "kb_id": kb_id,
            "tenant_id": tenant_id,
            "location": filepath,
            "parser_id": "naive",
            "parser_config": {},
            "status": "0",
            "type": "doc",
            "source": "crawler",
            "created_by": kb.tenant_id,
            "size": len(content.encode("utf-8")),
        }
        DocumentService.save(**doc)
        logging.info("Uploaded to KB: %s -> %s", filename, doc_id)
        return True
    except Exception as e:
        logging.error("Upload failed: %s — %s", filepath, e)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Longyan Public Resources Crawler")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT)
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--modules", default=None, help="Comma-separated module keys")
    args = parser.parse_args()

    CONSUMER_NAME = "longyan_crawler"
    init_root_logger(CONSUMER_NAME)

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    logging.info("%s Starting Longyan crawler for date: %s", _TAG_PREFIX, target_date)

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(_SCRIPT_DIR, "output", "longyan", target_date)
    os.makedirs(output_dir, exist_ok=True)

    # Load state
    state_dir = os.path.join(_SCRIPT_DIR, "output", "longyan")
    os.makedirs(state_dir, exist_ok=True)
    state = _load_state(state_dir) if not args.full else {"processed_ids": [], "last_run": None}
    processed_ids = set(state.get("processed_ids", []))

    # Filter modules
    if args.modules:
        module_keys = set(args.modules.split(","))
        target_modules = [m for m in _MODULES if m["key"] in module_keys]
    else:
        target_modules = list(_MODULES)

    logging.info("Modules: %d", len(target_modules))
    for m in target_modules:
        logging.info("  - %s: %s", m["key"], m["name"])

    # Phase 1: crawl listing
    start_time = time.time()
    all_articles = _crawl_listing(target_date)
    logging.info("Phase 1 done: %d articles total", len(all_articles))

    # Filter out already processed
    new_articles = [a for a in all_articles if a["guid"] not in processed_ids]
    logging.info("New articles: %d (skipped %d processed)",
                 len(new_articles), len(all_articles) - len(new_articles))

    # Phase 2: fetch detail + download attachments
    success_count = 0
    batch_processed = []

    for i, article in enumerate(new_articles):
        elapsed = time.time() - start_time
        if elapsed > args.max_runtime:
            logging.info("Max runtime reached (%.0fs), stopping gracefully.", elapsed)
            break

        guid = article["guid"]
        logging.info("[%d/%d] %s: %s", i + 1, len(new_articles), guid,
                     article["title"][:80])

        _request_delay()

        # Fetch detail
        detail = _fetch_detail_content(article["url"])
        if not detail:
            logging.warning("  Failed to fetch detail: %s", article["url"])
            continue

        # Build markdown
        md_content = _build_markdown(detail, article, article.get("module", {}))

        # Save markdown
        safe_title = _sanitize_filename(detail.get("title") or guid, max_len=80)
        md_filename = f"{target_date}_{guid}_{safe_title}.md"
        md_path = os.path.join(output_dir, md_filename)

        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            logging.info("  Saved: %s", md_filename)
        except Exception as e:
            logging.warning("  Save failed: %s", e)
            continue

        # Download attachments
        attachments = detail.get("attachments", [])
        if attachments:
            att_dir = os.path.join(output_dir, f"_attachments_{guid}")
            _download_attachments(attachments, att_dir, detail.get("title", ""))

        # Upload to KB
        _upload_to_kb(md_path, args.kb_id, args.tenant_id)

        processed_ids.add(guid)
        batch_processed.append(guid)
        success_count += 1

        # Batch checkpoint
        if len(batch_processed) >= _BATCH_SIZE:
            state["processed_ids"] = list(processed_ids)
            _save_state(state_dir, state)
            batch_processed = []
            logging.info("  [checkpoint] %d IDs saved", len(processed_ids))

    # Final checkpoint
    state["processed_ids"] = list(processed_ids)
    _save_state(state_dir, state)

    elapsed = time.time() - start_time
    logging.info("%s Done. Processed %d/%d articles in %.0fs.",
                 _TAG_PREFIX, success_count, len(new_articles), elapsed)
    logging.info("State: %d total IDs persisted.", len(processed_ids))


if __name__ == "__main__":
    CONSUMER_NAME = "longyan_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
