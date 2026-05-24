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
Crawler for ggzyjy.xgw.ningde.gov.cn — 工程建设 (Engineering Construction).

Target: https://ggzyjy.xgw.ningde.gov.cn/gcjs/engineeringConstruction2.html

Categories (8 sub-modules):
  002008 — 招标计划
  002001 — 招标公告
  002002 — 变更公告
  002003 — 中标候选人公示
  002004 — 中标结果公示
  002005 — 开标情况一览
  002009 — 异议回复
  002006 — 合同签订公示

Site characteristics
────────────────────
Epoint WebBuilder platform with OAuth 2.0 anonymous token flow.

Listing API (no auth required)
   POST /EpointWebBuilder/rest/frontAppCustomAction/getPageInfoListNew2
   Body: params=<JSON> with siteGuid, categoryNum, startDate, endDate,
         pageIndex, pageSize, kw, order, diqu, xmlx, zblx

Detail API (Bearer token required)
   POST /EpointWebBuilder/rest/GgSearchAction/getDetails
   Body: params=<JSON> with siteGuid, infoid, categorynum
   Returns list of stage entries with visiturl, title, etc.

Content
   GET the visiturl (relative HTML page) — full text content with
   embedded attachment links.

Attachments
   Attachment links use a download gateway:
     /EpointWebBuilder/webbuildermis/attach/downloadztbattach?attachGuid=...
   The actual file comes from:
     GET /EpointWebBuilder/webbuildermis/attach/ztbAttachDownloadAction.action
         ?cmd=getContent&attachGuid=...&appUrlFlag=TP001&siteGuid=...

Usage
─────
    python ningde_gcjs_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>

    # Optional:
        --max-runtime 3300    # Max runtime before graceful stop (default: 3300)
        --full                # Ignore saved state, re-crawl all
        --date YYYY-MM-DD     # Filter by date (default: today)
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
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://ggzyjy.xgw.ningde.gov.cn"
_TAG_PREFIX = "[ND-GCJS]"

_PAGE_SIZE = 30
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
_APP_URL_FLAG = "TP001"

# API endpoints
_API_LISTING = _SITE_ROOT + _PROJECT_NAME + "/rest/frontAppCustomAction/getPageInfoListNew2"
_API_TOKEN = _SITE_ROOT + _PROJECT_NAME + "/rest/getOauthInfoAction/getNoUserAccessToken"
_API_DETAIL = _SITE_ROOT + _PROJECT_NAME + "/rest/GgSearchAction/getDetails"
_API_ATTACH = _SITE_ROOT + _PROJECT_NAME + "/webbuildermis/attach/ztbAttachDownloadAction.action"

# Category mapping
_CATEGORY_MAP = {
    "002008": "招标计划",
    "002001": "招标公告",
    "002002": "变更公告",
    "002003": "中标候选人公示",
    "002004": "中标结果公示",
    "002005": "开标情况一览",
    "002009": "异议回复",
    "002006": "合同签订公示",
}

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

def _http_post(url, params_dict, extra_headers=None):
    hdrs = dict(_HEADERS_JSON)
    if extra_headers:
        hdrs.update(extra_headers)
    body_str = "params=" + json.dumps(params_dict, ensure_ascii=False)
    data_bytes = body_str.encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("POST %s failed: %s", url, e)
        return None


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
    """Get or refresh anonymous OAuth token."""
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
        _token_cache["expires"] = now + 1800  # 30 min cache
    return token


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _crawl_listing(target_date):
    """Crawl listing pages for the given date.

    Returns list[dict]: deduplicated items by infoid.
    """
    all_items = []
    seen_infoids = set()
    page_index = 0

    while True:
        params = {
            "siteGuid": _SITE_GUID,
            "categoryNum": "",
            "startDate": target_date,
            "endDate": target_date,
            "kw": "",
            "order": "",
            "diqu": "",
            "xmlx": "",
            "zblx": "",
            "pageIndex": page_index,
            "pageSize": _PAGE_SIZE,
        }
        text = _http_post(_API_LISTING, params,
                         extra_headers={"Referer": _SITE_ROOT + "/gcjs/engineeringConstruction2.html"})
        if not text:
            break

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logging.warning("JSON decode error for listing page %d", page_index)
            break

        custom = data.get("custom", {})
        items = custom.get("infodata", [])
        total = custom.get("count", 0)

        if not items:
            break

        # Deduplicate by infoid
        new_count = 0
        for item in items:
            infoid = item.get("infoid", "")
            if infoid and infoid not in seen_infoids:
                seen_infoids.add(infoid)
                all_items.append(item)
                new_count += 1

        _safe_print("[{}]   Page {} — {} items ({} new, {} unique total / {} total records)".format(
            _TAG_PREFIX, page_index + 1, len(items), new_count, len(all_items), total))
        sys.stdout.flush()

        if len(items) < _PAGE_SIZE:
            break

        page_index += 1
        _request_delay()

    return all_items


# ---------------------------------------------------------------------------
# Detail API
# ---------------------------------------------------------------------------

def _fetch_details(infoid, categorynum):
    """Fetch all detail stages for one infoid.

    Returns list[dict]: detail entries with keys:
        visiturl, categorynum, infoid, title, infodate, categoryname, zhuanzai
    """
    token = _get_token()
    if not token:
        logging.warning("No token available for detail API")
        return []

    pre_category = categorynum[:3] if categorynum else "002"
    params = {
        "siteGuid": _SITE_GUID,
        "infoid": infoid,
        "categorynum": pre_category,
    }
    referer = _SITE_ROOT + "/projectDetail.html?categorynum={}&infoid={}".format(
        categorynum, infoid)

    text = _http_post(_API_DETAIL, params,
                     extra_headers={
                         "Authorization": "Bearer " + token,
                         "Referer": referer,
                     })
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning("JSON decode error for detail infoid=%s", infoid)
        return []

    if isinstance(data, list):
        return data
    return []


def _fetch_visiturl_content(visiturl):
    """Fetch the content page HTML from a visiturl.

    Returns (content_text, attachments) tuple.
    """
    if not visiturl:
        return "", []

    if not visiturl.startswith("http"):
        url = _SITE_ROOT + visiturl
    else:
        url = visiturl

    html = _http_get_html(url, referer=_SITE_ROOT + "/projectDetail.html")
    if not html:
        return "", []

    content_text = _extract_visiturl_text(html)
    attachments = _extract_attachments_from_html(html, url)

    return content_text, attachments


def _extract_visiturl_text(html_str):
    """Extract main content text from a visiturl HTML page.

    The visiturl page has a .notice-content div containing the actual
    article (often as a nested HTML document with table layout).
    """
    if not html_str:
        return ""

    # 1. Try to extract just the .notice-content section
    notice_match = re.search(
        r'<div[^>]*class="notice-content"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>',
        html_str, re.DOTALL | re.I
    )
    if notice_match:
        content_html = notice_match.group(1)
    else:
        # Fallback: use the whole body
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html_str, re.DOTALL | re.I)
        if body_match:
            content_html = body_match.group(1)
        else:
            content_html = html_str

    # 2. Extract metadata table (deadlines, etc.)
    meta_lines = []
    tb_match = re.search(r'<table[^>]*class="tb"[^>]*>(.*?)</table>', html_str, re.DOTALL | re.I)
    if tb_match:
        tb_html = tb_match.group(1)
        for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', tb_html, re.DOTALL | re.I):
            cells = re.findall(r'<td[^>]*>(.*?)</td>', tr.group(1), re.DOTALL | re.I)
            clean_cells = []
            for c in cells:
                c = re.sub(r'<[^>]+>', '', c).strip()
                if c:
                    clean_cells.append(c)
            if clean_cells:
                meta_lines.append(" | ".join(clean_cells))

    # 3. Clean the content HTML
    if tb_match:
        content_html = content_html.replace(tb_match.group(0), "")

    # Remove script/style tags
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content_html, flags=re.DOTALL | re.I)

    # Remove the "附件：" label
    text = re.sub(r'<p[^>]*class="notice-hd1"[^>]*>[^<]*</p>', '', text, flags=re.I)

    # Remove nested HTML/head/title tags (report wrapper)
    text = re.sub(r'<!DOCTYPE[^>]*>', '', text, flags=re.I)
    text = re.sub(r'</?(?:html|head|meta|title|body)[^>]*>', '', text, flags=re.I)

    # Replace block elements with newlines
    text = re.sub(r'</?(?:div|p|tr|li|h[1-6]|table|hr|section|article|header|footer)[^>]*>',
                  '\n', text, flags=re.I)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    # td closings
    text = re.sub(r'</td>', ' ', text, flags=re.I)
    text = re.sub(r'</tr>', '\n', text, flags=re.I)

    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode HTML entities
    text = html_mod.unescape(text)

    # Replace special spaces
    text = text.replace('\xa0', ' ')
    text = text.replace('\u3000', ' ')

    # Remove the "Report" title line artifact
    text = re.sub(r'\n\s*Report\s*\n', '\n', text)

    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # Prepend metadata if available
    if meta_lines:
        meta_text = "\n".join(meta_lines)
        text = "**标书信息:**\n" + meta_text + "\n\n---\n\n" + text

    return text


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _html_to_text(html_str):
    """Strip HTML tags and decode entities, return clean text."""
    if not html_str:
        return ""

    # Remove script/style tags
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.I)

    # Replace common block elements with newlines
    text = re.sub(r'</?(?:div|p|tr|li|h[1-6]|table|hr|section|article|header|footer)[^>]*>',
                  '\n', text, flags=re.I)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)

    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode HTML entities
    text = html_mod.unescape(text)

    # Replace non-breaking spaces
    text = text.replace('\xa0', ' ')
    text = text.replace('\u3000', ' ')

    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


def _extract_attachments_from_html(html_str, page_url):
    """Extract attachment links from content page HTML.

    Returns list[dict]: [{filename, url}]
    """
    if not html_str:
        return []

    attachments = []
    seen_urls = set()

    # 1. Match the downloadztbattach links from content pages
    for m in re.finditer(
        r'href=["\']([^"\']*downloadztbattach\?attachGuid=([^"\'&]+)[^"\']*)["\']',
        html_str, re.I
    ):
        full_url = m.group(1).strip()
        attach_guid = m.group(2).strip()

        if not attach_guid or attach_guid in seen_urls:
            continue
        seen_urls.add(attach_guid)

        # Build real download URL
        dl_url = _API_ATTACH + "?cmd=getContent&attachGuid={}&appUrlFlag={}&siteGuid={}".format(
            attach_guid, _APP_URL_FLAG, _SITE_GUID)

        fname = attach_guid  # Will be updated from Content-Disposition on download
        attachments.append({"filename": fname, "url": dl_url, "guid": attach_guid})

    # 2. Also match direct file links (.pdf, .doc, .xls, .zip, .rar)
    for m in re.finditer(
        r'<a[^>]*href=["\']([^"\']*(?:\.pdf|\.doc[x]?|\.xls[x]?|\.rar|\.zip|\.ppt[x]?)'
        r'[^"\']*)["\'][^>]*>([^<]*)</a>',
        html_str, re.I
    ):
        href = m.group(1).strip()
        link_text = m.group(2).strip()
        link_text = html_mod.unescape(link_text)

        if not href:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = _SITE_ROOT + href
            elif href.startswith("//"):
                href = "https:" + href
            else:
                base = page_url.rsplit("/", 1)[0]
                href = base + "/" + href

        if href in seen_urls:
            continue
        seen_urls.add(href)

        fname = link_text if link_text else os.path.basename(
            urllib.parse.urlparse(href).path.split("?")[0]
        )
        if not fname:
            fname = "attachment"

        attachments.append({"filename": fname, "url": href})

    return attachments


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachments(attachments, download_dir):
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

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        data = _http_download(url, referer=_SITE_ROOT + "/projectDetail.html")
        if data:
            # Check Content-Disposition for real filename (for downloadztbattach URLs)
            # If the data starts with HTML, it might be the gateway page, not the file
            if data[:15].lower().startswith(b"<!doctype") or data[:6].lower().startswith(b"<html"):
                # Try extracting real download URL from gateway page
                gateway = data.decode("utf-8", errors="replace")
                real_url_match = re.search(
                    r'action\s*=\s*["\']([^"\']*ztbAttachDownloadAction[^"\']*)["\']',
                    gateway, re.I
                )
                if real_url_match:
                    action = real_url_match.group(1)
                    if not action.startswith("http"):
                        action = _SITE_ROOT + _PROJECT_NAME + "/webbuildermis/attach/" + action.lstrip("/")
                    data = _http_download(action, referer=url)
                    if not data:
                        continue

            if not data:
                continue

            # Try to get filename from URL after download
            if data[:4] == b'%PDF' and not fname.lower().endswith('.pdf'):
                fname += '.pdf'
            elif data[:4] == b'PK\x03\x04' and not fname.lower().endswith('.zip'):
                if not any(fname.lower().endswith(e) for e in ['.docx', '.xlsx', '.pptx']):
                    fname += '.zip'
                # Will be handled by ZIP extraction

            filepath = os.path.join(download_dir, fname)
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
            _request_delay()

    return local_files


def _extract_zip(filepath):
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

def _build_markdown(all_contents, listing_item, detail_url):
    """Build markdown from all detail stage contents.

    Parameters
    ----------
    all_contents : list[dict]
        Each: {categoryname, title, infodate, content_text, attachments}
    listing_item : dict
        Original listing item with infoid, title, etc.
    detail_url : str
        The projectDetail.html URL for this infoid.
    """
    main_title = listing_item.get("title", "") or "无标题"
    # Strip HTML tags from title
    main_title = re.sub(r'<[^>]+>', '', main_title)

    lines = [
        "# {}".format(main_title),
        "",
        "**数据来源:** 宁德市公共资源交易中心 — 工程建设",
        "**页面地址:** {}".format(detail_url),
        "**抓取时间:** {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]

    if listing_item.get("infodate"):
        lines.append("**信息时间:** {}".format(listing_item["infodate"]))

    # Project type metadata from listing
    xmlx = listing_item.get("xmlx", "")
    zblx = listing_item.get("zblx", "")
    if xmlx:
        lines.append("**项目类型:** {}".format(xmlx))
    if zblx:
        lines.append("**招标类型:** {}".format(zblx))

    lines.append("")

    # Add each stage as a section
    for stage in all_contents:
        stage_title = stage.get("title", "")
        # Strip HTML from title too
        stage_title = re.sub(r'<[^>]+>', '', stage_title)

        category = stage.get("categoryname", "") or _CATEGORY_MAP.get(
            stage.get("categorynum", ""), "")
        date_str = stage.get("infodate", "")
        content_text = stage.get("content_text", "")
        attachments = stage.get("attachments", [])

        section_title = "## {}".format(category) if category else "## 详情"
        if date_str:
            section_title += " ({})".format(date_str)

        lines.append(section_title)
        lines.append("")

        if content_text:
            content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
            if len(content_clean) > 100000:
                content_clean = content_clean[:100000] + "\n\n（内容过长，已截断）"
            lines.append(content_clean)
            lines.append("")

        if attachments:
            lines.append("**附件:**")
            for att in attachments:
                fname = att.get("filename", "unknown")
                att_url = att.get("url", "")
                lines.append("- [{}]({})".format(fname, att_url))
            lines.append("")

    return "\n".join(lines)


def _build_attachment_appendix(attachments, download_dir):
    """Build appendix markdown for extraction of attachment content."""
    if not attachments or not download_dir or not os.path.isdir(download_dir):
        return ""

    lines = ["### 附件内容", ""]
    for att in attachments:
        fname = att.get("filename", "")
        safe_name = _sanitize_filename(fname, max_len=120)

        local_path = os.path.join(download_dir, safe_name)
        # Also check with actual filename (might have been renamed after download)
        if not os.path.exists(local_path):
            for fn in os.listdir(download_dir):
                if fn.endswith(safe_name[-40:]) or safe_name.endswith(fn[-40:]):
                    local_path = os.path.join(download_dir, fn)
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
    return {"processed_ids": []}


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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)

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
                DocumentService.begin2parse(did)
                DocumentService.run(tenant_id, doc, {})
            except Exception as e:
                logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="ningde_gcjs_crawler — 宁德市公共资源交易中心 工程建设"
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
    p.add_argument("--date", default=None,
                   help="Filter date YYYY-MM-DD (default: today)")
    for opt in ("--max-days", "--hours", "--max-articles",
                "--llm-id", "--llm-model", "--access-token", "--target-url"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] 宁德市公共资源交易中心 — 工程建设 crawler".format(_TAG_PREFIX))
    _safe_print("[{}] Date: {}".format(_TAG_PREFIX, target_date))
    _safe_print("[{}] KB: {}".format(_TAG_PREFIX, args.kb_id))
    _safe_print("[{}] Task: {}".format(_TAG_PREFIX, args.task_name))
    _safe_print("[{}] Max runtime: {}s".format(_TAG_PREFIX, args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ND-GCJS crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[{}] Output: {}\n".format(_TAG_PREFIX, output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))

    _safe_print("[{}] Already processed: {} article(s)".format(_TAG_PREFIX, len(processed_ids)))
    sys.stdout.flush()

    crawl_start = time.time()
    downloads_dir = os.path.join(output_dir, "downloads")

    # ── Step 1: Get anonymous token ───────────────────────────────────
    _safe_print("[{}] Getting anonymous token...".format(_TAG_PREFIX))
    sys.stdout.flush()
    token = _get_token()
    if not token:
        _safe_print("[{}] ERROR: Failed to get OAuth token, exiting.".format(_TAG_PREFIX))
        return
    _safe_print("[{}] Token obtained.".format(_TAG_PREFIX))
    sys.stdout.flush()

    # ── Step 2: Crawl listing ────────────────────────────────────────
    _safe_print("[{}] Crawling listing for {}...".format(_TAG_PREFIX, target_date))
    sys.stdout.flush()

    all_items = _crawl_listing(target_date)
    _safe_print("[{}] Total unique articles: {}".format(_TAG_PREFIX, len(all_items)))
    sys.stdout.flush()

    if not all_items:
        _safe_print("[{}] No articles found for {}, exiting.".format(_TAG_PREFIX, target_date))
        return

    # Filter already-processed
    new_items = []
    for item in all_items:
        infoid = str(item.get("infoid", ""))
        if infoid not in processed_ids:
            new_items.append(item)

    skipped = len(all_items) - len(new_items)
    if skipped:
        _safe_print("[{}] {} already processed, {} new".format(
            _TAG_PREFIX, skipped, len(new_items)))
        sys.stdout.flush()

    if not new_items:
        _safe_print("[{}] No new articles to process.".format(_TAG_PREFIX))
        return

    # ── Step 3: Process each article ──────────────────────────────────
    processed_count = 0
    stopped_early = False

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

        infoid = str(item.get("infoid", ""))
        categorynum = item.get("categorynum", "002001")
        list_title = re.sub(r'<[^>]+>', '', item.get("title", "") or "(no title)")

        _safe_print("[{}] [{}/{}] {}...".format(
            _TAG_PREFIX, i, len(new_items), list_title[:70]))
        sys.stdout.flush()

        _request_delay()

        # ── Fetch detail stages ────────────────────────────────────
        stages = _fetch_details(infoid, categorynum)
        if not stages:
            _safe_print("[{}]   WARNING: No detail stages returned".format(_TAG_PREFIX))
            processed_ids.add(infoid)
            continue

        # ── Fetch content for each stage ───────────────────────────
        all_contents = []
        all_attachments = []

        for stage in stages:
            visiturl = stage.get("visiturl", "")
            if not visiturl:
                continue

            content_text, attachments = _fetch_visiturl_content(visiturl)
            all_contents.append({
                "categoryname": stage.get("categoryname", ""),
                "categorynum": stage.get("categorynum", ""),
                "title": stage.get("title", ""),
                "infodate": stage.get("infodate", ""),
                "content_text": content_text,
                "attachments": attachments,
            })
            all_attachments.extend(attachments)

        if not all_contents:
            _safe_print("[{}]   WARNING: No content extracted".format(_TAG_PREFIX))
            processed_ids.add(infoid)
            continue

        _safe_print("[{}]   {} stage(s), {} attachment(s)".format(
            _TAG_PREFIX, len(all_contents), len(all_attachments)))
        sys.stdout.flush()

        # ── Download attachments ──────────────────────────────────
        local_files = []
        article_dl_dir = ""
        if all_attachments:
            dl_name = "ndgcjs_{}_{}".format(
                infoid[:16], _sanitize_filename(list_title[:30], 40)
            )
            article_dl_dir = os.path.join(downloads_dir, dl_name)
            local_files = _download_attachments(all_attachments, article_dl_dir)

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

        # ── Build markdown ────────────────────────────────────────
        detail_url = _SITE_ROOT + "/projectDetail.html?categorynum={}&infoid={}".format(
            categorynum, infoid)
        md_content = _build_markdown(all_contents, item, detail_url)

        # Append attachment text
        if all_attachments and article_dl_dir:
            appendix = _build_attachment_appendix(all_attachments, article_dl_dir)
            if appendix:
                md_content += "\n" + appendix + "\n"

        # Save markdown locally
        pub_date = item.get("infodate", "") or datetime.now().strftime("%Y-%m-%d")
        date_for_name = _normalize_date(pub_date) or datetime.now().strftime("%Y-%m-%d")
        folder_name = _sanitize_filename(
            "ndgcjs_{}_{}_{}".format(
                date_for_name, infoid[:12], list_title[:40]
            ), max_len=120
        )
        md_path = os.path.join(output_dir, "{}.md".format(folder_name))
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        _safe_print("[{}]   Saved ({} chars, {} files)".format(
            _TAG_PREFIX, len(md_content), len(local_files)))
        sys.stdout.flush()

        # Upload to KB
        if args.kb_id:
            try:
                _upload_to_kb(md_content, local_files, args.kb_id,
                            args.tenant_id, folder_name)
            except Exception as e:
                logging.error("KB upload failed: %s", e)
                _save_state(output_dir, {"processed_ids": list(processed_ids)})
                _safe_print("[{}]   Upload error: {}".format(_TAG_PREFIX, e))
                sys.stdout.flush()

        processed_ids.add(infoid)
        processed_count += 1

        if processed_count % _BATCH_SIZE == 0:
            _save_state(output_dir, {"processed_ids": list(processed_ids)})
            _safe_print("[{}]   Checkpoint ({} processed)".format(
                _TAG_PREFIX, processed_count))
            sys.stdout.flush()

    # ── Final state ────────────────────────────────────────────────────
    _save_state(output_dir, {"processed_ids": list(processed_ids)})

    _safe_print("\n" + "=" * 60)
    _safe_print("[{}] Crawl complete — {} new article(s)".format(_TAG_PREFIX, processed_count))
    if stopped_early:
        _safe_print("[{}] Stopped early, will resume next run".format(_TAG_PREFIX))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== ND-GCJS crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "ningde_gcjs_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
