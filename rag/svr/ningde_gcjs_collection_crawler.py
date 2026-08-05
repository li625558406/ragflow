"""
宁德市公共资源交易中心 — 工程建设采集 (custom_runner)
====================================================

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

API flow:
  1. Listing API (no auth):
     POST /EpointWebBuilder/rest/frontAppCustomAction/getPageInfoListNew2
     Body: params=<JSON> with siteGuid, categoryNum, startDate, endDate,
           pageIndex, pageSize, kw, order, diqu, xmlx, zblx

  2. Token API (anonymous):
     POST /EpointWebBuilder/rest/getOauthInfoAction/getNoUserAccessToken
     Returns Bearer token valid ~30 min.

  3. Detail API (Bearer token required):
     POST /EpointWebBuilder/rest/GgSearchAction/getDetails
     Body: params=<JSON> with siteGuid, infoid, categorynum
     Returns list of stage entries with visiturl, title, etc.

  4. Content:
     GET the visiturl (relative HTML page) — full text content with
     embedded attachment links.

  5. Attachments:
     /EpointWebBuilder/webbuildermis/attach/downloadztbattach?attachGuid=...
     Actual file from:
     GET /EpointWebBuilder/webbuildermis/attach/ztbAttachDownloadAction.action
         ?cmd=getContent&attachGuid=...&appUrlFlag=TP001&siteGuid=...

Call entry: unified_crawler.py dispatches via custom_runner → run()
"""

import html as html_mod
import json
import logging
import os
import random
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

logger = logging.getLogger("ningde_gcjs_collection_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "ningde_gcjs"
SITE_DISPLAY = "宁德市-工程建设"
CATEGORY = "bid"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"

_SITE_ROOT = "https://ggzyjy.xgw.ningde.gov.cn"
_TAG_PREFIX = "[ND-GCJS]"

_PAGE_SIZE = 30
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.2

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

# Category mapping (8 sub-categories)
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

# Global token cache: {"token": str|None, "expires": float}
_token_cache: Dict[str, Any] = {"token": None, "expires": 0}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay() -> None:
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    if not name:
        return "unnamed"
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("._ ")
    if len(safe) > max_len:
        base, ext = os.path.splitext(safe)
        safe = base[: max_len - len(ext)] + (ext or "")
    return safe or "unnamed"


def _normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", str(date_str))
    return m.group(1) if m else str(date_str)[:10]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_post(url: str, params_dict: dict, extra_headers: Optional[dict] = None) -> Optional[str]:
    """POST with params=<JSON> form body. Returns response text or None."""
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


def _http_post_raw(url: str, body_str: str, extra_headers: Optional[dict] = None) -> Optional[str]:
    """POST with raw body string. Returns response text or None."""
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


def _http_get_html(url: str, referer: Optional[str] = None) -> Optional[str]:
    """GET HTML page. Returns response text or None."""
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


def _http_download(url: str, referer: Optional[str] = None) -> Optional[bytes]:
    """GET binary download. Returns bytes or None."""
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
# OAuth2 token
# ---------------------------------------------------------------------------

def _get_token() -> Optional[str]:
    """Get or refresh anonymous OAuth token (30 min cache)."""
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

def _crawl_listing(target_date: str) -> List[dict]:
    """Crawl listing pages for the given date.

    Uses categoryNum="" (empty string) to fetch ALL sub-categories.
    Returns list[dict]: deduplicated items by infoid.
    """
    all_items: List[dict] = []
    seen_infoids: set = set()
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
        text = _http_post(
            _API_LISTING,
            params,
            extra_headers={"Referer": _SITE_ROOT + "/gcjs/engineeringConstruction2.html"},
        )
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

        _safe_print(
            "{}   Page {} — {} items ({} new, {} unique total / {} total records)".format(
                _TAG_PREFIX, page_index + 1, len(items), new_count, len(all_items), total
            )
        )
        sys.stdout.flush()

        if len(items) < _PAGE_SIZE:
            break

        page_index += 1
        _request_delay()

    return all_items


# ---------------------------------------------------------------------------
# Detail API
# ---------------------------------------------------------------------------

def _fetch_details(infoid: str, categorynum: str) -> List[dict]:
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
        categorynum, infoid
    )

    text = _http_post(
        _API_DETAIL,
        params,
        extra_headers={
            "Authorization": "Bearer " + token,
            "Referer": referer,
        },
    )
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


def _fetch_visiturl_content(visiturl: str) -> tuple:
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


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _extract_visiturl_text(html_str: str) -> str:
    """Extract main content text from a visiturl HTML page.

    The visiturl page has a .notice-content div containing the actual
    article (often as a nested HTML document with table layout).
    """
    if not html_str:
        return ""

    # 1. Try to extract just the .notice-content section
    notice_match = re.search(
        r'<div[^>]*class="notice-content"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</div>\s*</div>',
        html_str,
        re.DOTALL | re.I,
    )
    if notice_match:
        content_html = notice_match.group(1)
    else:
        # Fallback: use the whole body
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html_str, re.DOTALL | re.I)
        if body_match:
            content_html = body_match.group(1)
        else:
            content_html = html_str

    # 2. Extract metadata table (deadlines, etc.)
    meta_lines: List[str] = []
    tb_match = re.search(r'<table[^>]*class="tb"[^>]*>(.*?)</table>', html_str, re.DOTALL | re.I)
    if tb_match:
        tb_html = tb_match.group(1)
        for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", tb_html, re.DOTALL | re.I):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr.group(1), re.DOTALL | re.I)
            clean_cells = []
            for c in cells:
                c = re.sub(r"<[^>]+>", "", c).strip()
                if c:
                    clean_cells.append(c)
            if clean_cells:
                meta_lines.append(" | ".join(clean_cells))

    # 3. Clean the content HTML
    if tb_match:
        content_html = content_html.replace(tb_match.group(0), "")

    # Remove script/style tags
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", content_html, flags=re.DOTALL | re.I)

    # Remove the "附件：" label
    text = re.sub(r'<p[^>]*class="notice-hd1"[^>]*>[^<]*</p>', "", text, flags=re.I)

    # Remove nested HTML/head/title tags (report wrapper)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    text = re.sub(r"</?(?:html|head|meta|title|body)[^>]*>", "", text, flags=re.I)

    # Replace block elements with newlines
    text = re.sub(
        r"</?(?:div|p|tr|li|h[1-6]|table|hr|section|article|header|footer)[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    # td closings
    text = re.sub(r"</td>", " ", text, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html_mod.unescape(text)

    # Replace special spaces
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")

    # Remove the "Report" title line artifact
    text = re.sub(r"\n\s*Report\s*\n", "\n", text)

    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # Prepend metadata if available
    if meta_lines:
        meta_text = "\n".join(meta_lines)
        text = "**标书信息:**\n" + meta_text + "\n\n---\n\n" + text

    return text


def _html_to_text(html_str: str) -> str:
    """Strip HTML tags and decode entities, return clean text."""
    if not html_str:
        return ""

    # Remove script/style tags
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_str, flags=re.DOTALL | re.I)

    # Replace common block elements with newlines
    text = re.sub(
        r"</?(?:div|p|tr|li|h[1-6]|table|hr|section|article|header|footer)[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html_mod.unescape(text)

    # Replace non-breaking spaces
    text = text.replace("\xa0", " ")
    text = text.replace("　", " ")

    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


def _extract_attachments_from_html(html_str: str, page_url: str) -> List[dict]:
    """Extract attachment links from content page HTML.

    Returns list[dict]: [{filename, url, guid?}]
    """
    if not html_str:
        return []

    attachments: List[dict] = []
    seen_urls: set = set()

    # 1. Match the downloadztbattach links from content pages
    for m in re.finditer(
        r'href=["\']([^"\']*downloadztbattach\?attachGuid=([^"\'&]+)[^"\']*)["\']',
        html_str,
        re.I,
    ):
        full_url = m.group(1).strip()
        attach_guid = m.group(2).strip()

        if not attach_guid or attach_guid in seen_urls:
            continue
        seen_urls.add(attach_guid)

        # Build real download URL
        dl_url = _API_ATTACH + "?cmd=getContent&attachGuid={}&appUrlFlag={}&siteGuid={}".format(
            attach_guid, _APP_URL_FLAG, _SITE_GUID
        )

        fname = attach_guid  # Will be updated from Content-Disposition on download
        attachments.append({"filename": fname, "url": dl_url, "guid": attach_guid})

    # 2. Also match direct file links (.pdf, .doc, .xls, .zip, .rar)
    for m in re.finditer(
        r'<a[^>]*href=["\']([^"\']*(?:\.pdf|\.doc[x]?|\.xls[x]?|\.rar|\.zip|\.ppt[x]?)'
        r'[^"\']*)["\'][^>]*>([^<]*)</a>',
        html_str,
        re.I,
    ):
        href = m.group(1).strip()
        link_text = m.group(2).strip()
        link_text = html_mod.unescape(link_text)

        if not href:
            continue

        if not href.startswith("http"):
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = _SITE_ROOT + href
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

def _download_attachments(attachments: List[dict], download_dir: str) -> List[str]:
    """Download attachments to local directory. Returns list of local file paths."""
    os.makedirs(download_dir, exist_ok=True)
    local_files: List[str] = []

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
            # If the data starts with HTML, it might be the gateway page, not the file
            if data[:15].lower().startswith(b"<!doctype") or data[:6].lower().startswith(b"<html"):
                # Try extracting real download URL from gateway page
                gateway = data.decode("utf-8", errors="replace")
                real_url_match = re.search(
                    r'action\s*=\s*["\']([^"\']*ztbAttachDownloadAction[^"\']*)["\']',
                    gateway,
                    re.I,
                )
                if real_url_match:
                    action = real_url_match.group(1)
                    if not action.startswith("http"):
                        action = (
                            _SITE_ROOT + _PROJECT_NAME + "/webbuildermis/attach/" + action.lstrip("/")
                        )
                    data = _http_download(action, referer=url)
                    if not data:
                        continue

            if not data:
                continue

            # Detect file type from magic bytes
            if data[:4] == b"%PDF" and not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            elif data[:4] == b"PK\x03\x04" and not fname.lower().endswith(".zip"):
                if not any(fname.lower().endswith(e) for e in [".docx", ".xlsx", ".pptx"]):
                    fname += ".zip"

            filepath = os.path.join(download_dir, fname)
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
            _request_delay()

    return local_files


def _extract_zip(filepath: str) -> List[str]:
    """Extract ZIP file and return list of extracted file paths."""
    extracted: List[str] = []
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


def _extract_text_from_file(filepath: str) -> str:
    """Extract text from PDF/DOCX/XLSX/TXT for markdown appendix."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber

            parts: List[str] = []
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
                rows: List[str] = []
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

def _build_markdown(all_contents: List[dict], listing_item: dict, detail_url: str) -> str:
    """Build markdown from all detail stage contents.

    Parameters
    ----------
    all_contents : list[dict]
        Each: {categoryname, categorynum, title, infodate, content_text, attachments}
    listing_item : dict
        Original listing item with infoid, title, etc.
    detail_url : str
        The projectDetail.html URL for this infoid.
    """
    main_title = listing_item.get("title", "") or "无标题"
    # Strip HTML tags from title
    main_title = re.sub(r"<[^>]+>", "", main_title)

    lines: List[str] = [
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
        stage_title = re.sub(r"<[^>]+>", "", stage_title)

        category_name = stage.get("categoryname", "") or _CATEGORY_MAP.get(
            stage.get("categorynum", ""), ""
        )
        date_str = stage.get("infodate", "")
        content_text = stage.get("content_text", "")
        attachments = stage.get("attachments", [])

        section_title = "## {}".format(category_name) if category_name else "## 详情"
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


def _build_attachment_appendix(attachments: List[dict], download_dir: str) -> str:
    """Build appendix markdown for extraction of attachment content."""
    if not attachments or not download_dir or not os.path.isdir(download_dir):
        return ""

    lines: List[str] = ["### 附件内容", ""]
    for att in attachments:
        fname = att.get("filename", "")
        safe_name = _sanitize_filename(fname, max_len=120)

        local_path = os.path.join(download_dir, safe_name)
        # Also check with actual filename (might have been renamed after download)
        if not os.path.exists(local_path):
            found = False
            for fn in os.listdir(download_dir):
                if fn.endswith(safe_name[-40:]) or safe_name.endswith(fn[-40:]):
                    local_path = os.path.join(download_dir, fn)
                    found = True
                    break
            if not found:
                continue
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
# run() — custom_runner entry point
# ---------------------------------------------------------------------------

def run(
    tenant_id: str = "",
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = CATEGORY,
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config: Any = None,
    output_dir: str = "",
) -> dict:
    """Custom runner entry point called by unified_crawler.py.

    Returns a flat summary dict consumed by _writeback_task_run_result().
    """
    _kb_id = kb_id or KB_ID_DEFAULT

    _safe_print("=" * 60)
    _safe_print("宁德市公共资源交易中心 — 工程建设采集 (custom_runner)")
    _safe_print("Tenant: {}  KB: {}".format(tenant_id, _kb_id))
    _safe_print("Date filter: {}  Category: {}".format(date_filter or "none", category))
    _safe_print("Full crawl: {}  Force run: {}".format(full_crawl, force_run))
    _safe_print("=" * 60)
    sys.stdout.flush()

    # Lazy-import writer services (available inside Docker container)
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline

    writer = CollectionWriter(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        date_filter=date_filter,
    )

    pipeline = StoragePipeline(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        site_id=SITE_ID,
        site_display=SITE_DISPLAY,
        task_name=task_name,
        output_dir=output_dir,
        writer_mode="collection",
        category=category,
        task_id=task_id,
        date_filter=date_filter,
    )

    # Stats
    total_items = 0
    total_new = 0
    total_kb = 0
    total_att = 0
    errors: List[str] = []

    # ── Step 1: Get anonymous token (fail early) ─────────────────────
    _safe_print("{} Getting anonymous token...".format(_TAG_PREFIX))
    sys.stdout.flush()
    token = _get_token()
    if not token:
        msg = "Failed to get OAuth token — cannot proceed"
        _safe_print("{} ERROR: {}".format(_TAG_PREFIX, msg))
        errors.append(msg)
        return {
            "status": "fail",
            "pages": 0,
            "items_found": 0,
            "items_new": 0,
            "kb_uploaded": 0,
            "attachments_uploaded": 0,
            "errors": errors,
        }
    _safe_print("{} Token obtained.".format(_TAG_PREFIX))
    sys.stdout.flush()

    # ── Step 2: Determine target date ────────────────────────────────
    if date_filter and date_filter != "today":
        target_date = _normalize_date(date_filter)
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")

    _safe_print("{} Target date: {}".format(_TAG_PREFIX, target_date))
    sys.stdout.flush()

    # ── Step 3: Crawl listing ────────────────────────────────────────
    _safe_print("{} Crawling listing for {}...".format(_TAG_PREFIX, target_date))
    sys.stdout.flush()

    all_items = _crawl_listing(target_date)
    pages_scanned = max(1, (len(all_items) + _PAGE_SIZE - 1) // _PAGE_SIZE)

    _safe_print("{} Total unique articles: {}".format(_TAG_PREFIX, len(all_items)))
    sys.stdout.flush()

    if not all_items:
        _safe_print("{} No articles found for {}.".format(_TAG_PREFIX, target_date))
        return {
            "status": "success",
            "pages": pages_scanned,
            "items_found": 0,
            "items_new": 0,
            "kb_uploaded": 0,
            "attachments_uploaded": 0,
            "errors": [],
        }

    # ── Step 4: Process each article ─────────────────────────────────
    for i, item in enumerate(all_items, 1):
        infoid = str(item.get("infoid", ""))
        categorynum = item.get("categorynum", "002001")
        list_title = re.sub(r"<[^>]+>", "", item.get("title", "") or "(no title)")

        _safe_print("{} [{}/{}] {}...".format(_TAG_PREFIX, i, len(all_items), list_title[:70]))
        sys.stdout.flush()

        _request_delay()

        tmp_dir = ""
        try:
            # ── Fetch detail stages ──────────────────────────────────
            stages = _fetch_details(infoid, categorynum)
            if not stages:
                _safe_print("{}   WARNING: No detail stages returned".format(_TAG_PREFIX))
                errors.append("No detail stages for infoid={}".format(infoid))
                continue

            # ── Fetch content for each stage ─────────────────────────
            all_contents: List[dict] = []
            all_attachments: List[dict] = []

            for stage in stages:
                visiturl = stage.get("visiturl", "")
                if not visiturl:
                    continue

                content_text, attachments = _fetch_visiturl_content(visiturl)
                all_contents.append(
                    {
                        "categoryname": stage.get("categoryname", ""),
                        "categorynum": stage.get("categorynum", ""),
                        "title": stage.get("title", ""),
                        "infodate": stage.get("infodate", ""),
                        "content_text": content_text,
                        "attachments": attachments,
                    }
                )
                all_attachments.extend(attachments)

            if not all_contents:
                _safe_print("{}   WARNING: No content extracted".format(_TAG_PREFIX))
                errors.append("No content for infoid={}".format(infoid))
                continue

            _safe_print(
                "{}   {} stage(s), {} attachment(s)".format(
                    _TAG_PREFIX, len(all_contents), len(all_attachments)
                )
            )
            sys.stdout.flush()

            # ── Download attachments to temp dir ─────────────────────
            local_files: List[str] = []
            if all_attachments:
                tmp_dir = tempfile.mkdtemp(prefix="ndgcjs_{}_".format(infoid[:12]))
                local_files = _download_attachments(all_attachments, tmp_dir)

                # Check for ZIP files and extract
                for fp in list(local_files):
                    is_zip = fp.lower().endswith(".zip")
                    if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                        with open(fp, "rb") as f:
                            is_zip = f.read(4) == b"PK\x03\x04"
                    if is_zip:
                        extracted = _extract_zip(fp)
                        if fp in local_files:
                            local_files.remove(fp)
                        local_files.extend(extracted)

            # ── Build markdown ───────────────────────────────────────
            detail_url = _SITE_ROOT + "/projectDetail.html?categorynum={}&infoid={}".format(
                categorynum, infoid
            )
            md_content = _build_markdown(all_contents, item, detail_url)

            # Append attachment text extraction
            if all_attachments and tmp_dir:
                appendix = _build_attachment_appendix(all_attachments, tmp_dir)
                if appendix:
                    md_content += "\n" + appendix + "\n"

            # ── Build item dict for writer ───────────────────────────
            item_title = re.sub(r"<[^>]+>", "", item.get("title", "") or "")
            item_date = item.get("infodate", "") or target_date
            category_name = _CATEGORY_MAP.get(categorynum, "")
            xmlx = item.get("xmlx", "")
            zblx = item.get("zblx", "")

            item_dict: Dict[str, Any] = {
                "title": item_title,
                "url": detail_url,
                "date": item_date,
                "content": md_content,
                "content_markdown": md_content,
                "site_id": SITE_ID,
                "site_display": SITE_DISPLAY,
                "section_name": SITE_DISPLAY,
                "category_name": category_name,
                "project_type": xmlx,
                "bid_type": zblx,
                "infoid": infoid,
                "categorynum": categorynum,
                "stage_count": len(all_contents),
                "attachment_count": len(local_files),
                "attachments": [
                    {
                        "file_name": att.get("filename", ""),
                        "file_url": att.get("url", ""),
                    }
                    for att in all_attachments
                ],
            }

            # ── Write to crawler_result via CollectionWriter ─────────
            result_id = writer.write_all(
                item=item_dict,
                site_id=SITE_ID,
                category=category,
                task_id=task_id,
                site_display=SITE_DISPLAY,
            )
            if not result_id:
                # Filtered out by date or dedup
                continue
            total_items += 1

            # ── KB upload via StoragePipeline ────────────────────────
            normalized = item_from_dict(item_dict, site_id=SITE_ID)
            try:
                store_result = pipeline.store(normalized)
                if store_result.get("doc_id"):
                    total_kb += 1
                attach_results = store_result.get("attachment_results", [])
                if attach_results:
                    total_att += sum(1 for a in attach_results if a.get("success"))
            except Exception as e:
                logger.warning("Pipeline store failed for %s: %s", detail_url[:80], e)

            # ── Upload local attachment files to KB ──────────────────
            # The pipeline handles URL-based attachments, but this site
            # requires custom gateway download. Upload local files directly.
            if local_files and _kb_id:
                try:
                    from rag.svr.crawler_engine.kb_uploader import KBUploader

                    kb_uploader = KBUploader(_kb_id, tenant_id)
                    for fp in local_files:
                        if os.path.exists(fp) and os.path.getsize(fp) > 0:
                            try:
                                doc_ids = kb_uploader.upload_file(fp)
                                if doc_ids:
                                    total_att += 1
                            except Exception as e:
                                logger.warning(
                                    "Attachment KB upload failed for %s: %s",
                                    os.path.basename(fp),
                                    e,
                                )
                except Exception as e:
                    logger.warning("KBUploader init failed: %s", e)

            _safe_print(
                "{}   OK ({} chars, {} files)".format(
                    _TAG_PREFIX, len(md_content), len(local_files)
                )
            )
            sys.stdout.flush()

        except Exception as e:
            msg = "Error processing infoid={}: {}".format(infoid, e)
            logger.exception(msg)
            errors.append(msg)
            _safe_print("{}   ERROR: {}".format(_TAG_PREFIX, e))
            sys.stdout.flush()
        finally:
            # ── Cleanup temp download dir ────────────────────────────
            if tmp_dir and os.path.isdir(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    # ── Pull writer stats ────────────────────────────────────────────
    wstats = writer.stats
    total_new = wstats.get("results_new", 0)

    _safe_print("\n" + "=" * 60)
    _safe_print(
        "DONE  pages={}  items={}  new={}  kb={}  att={}".format(
            pages_scanned, total_items, total_new, total_kb, total_att
        )
    )
    _safe_print("=" * 60)
    sys.stdout.flush()

    # Cleanup pipeline resources
    try:
        pipeline.cleanup()
    except Exception:
        pass

    return {
        "status": "success" if not errors else ("fail" if total_items == 0 else "partial"),
        "pages": pages_scanned,
        "items_found": total_items,
        "items_new": total_new,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors,
    }
