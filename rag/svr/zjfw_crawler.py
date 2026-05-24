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
Dedicated web crawler for zjfw.zhangzhou.gov.cn (漳州市工程项目中介服务平台).

This is a JavaScript-heavy SPA. All data is loaded via a signed API:
  POST /imng/api-v2/{action}/{method}?s={sig}&t={ts}
  Content-Type: application/json

The signature mechanism involves:
  1. Fetching the homepage to extract a __signature token from the HTML.
  2. Generating a 6-char key by using each character of __signature as an
     index into the string "0123456789abcdef".
  3. Building a timestamp string: "{rand8}_{key}_{epoch_ms}".
  4. Passing the original __signature as ?s and the timestamp as ?t.

Content sections (user-selected):
  - tzgg (通知公告 / Policy Notices) — IndexCmd.getPoliciesRegulationsList
      Listing returns rows with {id, title, publish_date, ...}.
      Detail via PoliciesRegulationsCmd.getPoliciesRegulationsDetail → CONTENT (HTML).
  - cggg (采购公告 / Procurement Notices) — BidNoticeCmd.queryBidNoticeList
      Listing returns rows with {bid_id, bid_title, publish_date, ...}.
      Detail via BidNoticeCmd.getBidsInfo → BID_CONTENT (text/html).
  - zcwj (政策文件 / Service Guide) — PolicyCmd.getList with type=ZCWJ
      Listing returns rows with {ID, TITLE, DISPLAYTIME}.
      Detail via product...PoliciesRegulationsCmd.getPoliciesRegulationsDetail
      → CONTENT + DOC_ID/ATTACHMENT_NAME for file download.

Usage (typically spawned by task_executor):
    python zjfw_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://zjfw.zhangzhou.gov.cn/imng/zjfw \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import string
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# Known sections — each entry is:
#   ( (list_action, list_method), (detail_action, detail_method) or None,
#     display_label, id_field, date_field )
# detail_api = None means listing data is used directly as content.
# ---------------------------------------------------------------------------
SECTIONS = {
    "tzgg": (
        ("fujian.zhangzhougaoxin.app.icity.policy.PolicyCmd", "getList"),
        ("fujian.zhangzhougaoxin.app.icity.policy.PoliciesRegulationsCmd", "getPoliciesRegulationsDetail"),
        "通知公告", "ID", "DISPLAYTIME",
    ),
    "cggg": (
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "queryBidNoticeList"),
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "getBidsInfo"),
        "采购公告", "ID", "CREATDATE",
    ),
    "zxgg": (
        ("fujian.zhangzhougaoxin.app.icity.browse.dealInfo.DealInfoCmd", "queryDealInfoNoticeList"),
        None,
        "中选公告", "ID", "CONFIRM_TIME",
    ),
    "zzgg": (
        ("fujian.zhangzhou.app.icity.notificationAnnounce.NotificationAnnounceCmd", "getNotificationAnnounceList"),
        None,
        "终止公告", "ID", "CREATE_DATE",
    ),
    "cqgg": (
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "queryClarifyList"),
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "getBidsInfo"),
        "澄清公告", "ID", "CREATDATE",
    ),
    "cfgg": (
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "queryResendList"),
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "getBidsInfo"),
        "重发公告", "ID", "CREATDATE",
    ),
    "lbgg": (
        ("fujian.zhangzhou.app.icity.notificationAnnounce.NotificationAnnounceCmd", "getNotificationAnnounceListlb"),
        None,
        "流标公告", "BID_ID", "CREATE_DATE",
    ),
    "zxbggg": (
        ("fujian.zhangzhougaoxin.app.icity.browse.dealInfo.DealInfoCmd", "getBidChangeInfo"),
        None,
        "中选变更公告", "ID", "CONFIRM_TIME",
    ),
    "zcwj": (
        ("fujian.zhangzhougaoxin.app.icity.policy.PolicyCmd", "getList"),
        ("product.app.icity.policiesRegulations.PoliciesRegulationsCmd", "getPoliciesRegulationsDetail"),
        "政策文件", "ID", "DISPLAYTIME",
    ),
}

# Section-specific listing API parameters merged into the base {start, limit}.
LISTING_PARAMS = {
    "cggg": {"notice_type": "1", "reg_code": "350600000000"},
    "zcwj": {"type": "ZCWJ"},
}

_SITE_ROOT = "http://zjfw.zhangzhou.gov.cn"
_API_BASE = _SITE_ROOT + "/imng/api-v2"

# Detail page URL templates for Playwright-rendered sections.
# {FIELD} placeholders are replaced from the listing row's raw data.
DETAIL_URL_TEMPLATES = {
    "zxgg": "/imng/zjfw/browse/dealInfo/dealInfoDetail?id={ID}&bcid={BCID}",
    "zzgg": "/imng/zjfw/index/notification/notificationDetail_view?id={ID}",
    "lbgg": "/imng/zjfw/index/notification/notificationDetail_view_lb?id={BID_ID}",
    "zxbggg": "/imng/zjfw/browse/bid_change_info/dealInfoDetail?id={BGID}",
}


def parse_args():
    parser = argparse.ArgumentParser(description="ZJFW crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. http://zjfw.zhangzhou.gov.cn/imng/zjfw)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated list of section labels (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=365,
                        help="Max age in days for articles (default: 365)")
    parser.add_argument("--tzgg-only", action="store_true",
                        help="Only crawl tzgg section (convenience)")
    parser.add_argument("--cggg-only", action="store_true",
                        help="Only crawl cggg section (convenience)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds before graceful stop (default: 3300 = 55 min)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _init():
    settings.init_settings()
    logging.info("Project settings initialised")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
}

def _fetch(url, client, timeout=30):
    """Fetch a URL and return decoded text."""
    try:
        resp = client.get(url)
        resp.raise_for_status()
        # Playwright get() already returns decoded text from the browser;
        # avoid re-encoding to bytes + chardet misdetection (e.g. 'charmap').
        return resp.text
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Signature generation (reverse-engineered from LEx.Command JS)
# ---------------------------------------------------------------------------

_SIG_CHARS = "0123456789abcdef"


def _fetch_signature(home_url, client):
    """Fetch the homepage and extract the __signature value.

    The signature is embedded somewhere in the HTML as a JavaScript variable:
      var __signature = "29e63b961c88e08197c81248600dd50b";

    Returns the signature string, or None if not found.
    """
    html = _fetch(home_url, client)
    if not html:
        return None
    m = re.search(r'__signature\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    logging.warning("__signature not found in homepage HTML")
    return None


def _generate_key(sig):
    """Generate a 6-character key from the signature.

    Reverse-engineered from LEx.Command JS:
      var key = "";
      var keyIndex = -1;
      for (var i = 0; i < 6; i++) {
        var c = sig.charAt(keyIndex + 1);
        key += c;
        keyIndex = chars.indexOf(c);
        if (keyIndex < 0 || keyIndex >= sig.length) keyIndex = i;
      }

    Each iteration picks the char at (prev_index + 1), then uses that
    char's position in "0123456789abcdef" as the next index.
    """
    key = ""
    key_index = -1
    for i in range(6):
        c = sig[key_index + 1]
        key += c
        key_index = _SIG_CHARS.index(c)
        if key_index < 0 or key_index >= len(sig):
            key_index = i
    return key


def _gen_timestamp(key):
    """Build the timestamp parameter: "{rand8}_{key}_{epoch_ms}".

    Matches the JS: parseInt(Math.random() * 90000000 + 10000000)
    The rand8 is 8 random decimal digits (10000000-99999999).
    """
    rand8 = str(random.randint(10000000, 99999999))
    epoch_ms = int(time.time() * 1000)
    ts = f"{rand8}_{key}_{epoch_ms}"
    return ts.replace("+", "_")


def _ensure_signature(sig_cache, client):
    """Ensure we have a cached signature; fetch if needed.

    sig_cache is a dict: {"sig": str or None, "key": str or None, "home_url": str}
    Returns True if signature is available.
    """
    if sig_cache.get("sig") and sig_cache.get("key"):
        return True
    sig = _fetch_signature(sig_cache["home_url"], client)
    if not sig:
        return False
    sig_cache["sig"] = sig
    sig_cache["key"] = _generate_key(sig)
    return True


def _call_api(action, method, params, sig_cache, client, timeout=30):
    """Call a signed API endpoint.

    POST to /imng/api-v2/{action}/{method}?s={sig}&t={ts}
    with JSON body = params.

    The Referer header is set dynamically so the nginx WAF can validate
    the request source.

    Returns parsed JSON dict, or None on failure.
    """
    if not _ensure_signature(sig_cache, client):
        logging.error("Cannot sign API call: no signature available")
        return None

    ts = _gen_timestamp(sig_cache["key"])
    url = f"{_API_BASE}/{action}/{method}?s={sig_cache['sig']}&t={ts}"

    try:
        headers = {"Referer": sig_cache.get("home_url", _SITE_ROOT + "/imng/zjfw")}
        resp = client.post(url, json_body=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        logging.error("API call %s/%s failed: %s", action, method, e)
        return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
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
# Listing parsing — generic
# ---------------------------------------------------------------------------

def _extract_articles(api_data, section_label, id_field="ID", date_field="CREATDATE"):
    """Extract articles from any listing API response.

    Returns list[dict] with keys: id, title, date, section, raw.
    """
    articles = []
    rows = []
    if isinstance(api_data, dict):
        rows = api_data.get("data") or api_data.get("rows") or []
    elif isinstance(api_data, list):
        rows = api_data

    for row in rows:
        if not isinstance(row, dict):
            continue
        article_id = (row.get(id_field) or row.get(id_field.upper()) or
                      row.get(id_field.lower()) or "")
        if not article_id:
            continue
        title = (row.get("TITLE") or row.get("title") or "").strip()
        if not title or len(title) < 2:
            continue

        # Try configured date_field first, then common alternatives
        date_str = ""
        for df in [date_field, "CREATDATE", "DISPLAYTIME", "CONFIRM_TIME", "CREATE_DATE"]:
            val = (row.get(df) or row.get(df.upper()) or "").strip()
            if val:
                date_str = val
                break
        dt = _parse_date(date_str) if date_str else None

        articles.append({
            "id": str(article_id),
            "title": title,
            "date": dt,
            "section": section_label,
            "raw": row,
        })

    return articles


# ---------------------------------------------------------------------------
# Article detail — BidNoticeCmd sections (cggg, cqgg, cfgg)
# ---------------------------------------------------------------------------

def _fetch_bid_notice_detail(article_id, sig_cache, client):
    """Fetch article detail via BidNoticeCmd/getBidsInfo.

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    data = _call_api(
        "fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd",
        "getBidsInfo",
        {"bid_id": article_id}, sig_cache, client,
    )
    if not data:
        return "", [], {}

    rows = []
    if isinstance(data, dict):
        rows = data.get("data") or data.get("rows") or []
    elif isinstance(data, list):
        rows = data

    if not rows or not isinstance(rows, list):
        return "", [], {}

    detail = rows[0] if isinstance(rows[0], dict) else {}

    meta_title = (detail.get("TITLE") or detail.get("title") or "").strip()
    meta_date = (detail.get("CREATDATE") or detail.get("DISPLAYTIME") or "").strip()
    meta_source = (detail.get("SOURCE") or detail.get("source") or "").strip()

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    content_text = (detail.get("BID_CONTENT") or detail.get("bid_content") or "").strip()
    if not content_text:
        return "", [], metadata

    # Extract attachment links before converting to markdown
    attachments = _extract_attachments_from_html(content_text) if "<" in content_text else []

    if "<" in content_text and ">" in content_text:
        markdown_text = _html_to_markdown(content_text)
    else:
        markdown_text = content_text

    return markdown_text, attachments, metadata


# ---------------------------------------------------------------------------
# Article detail — PolicyCmd sections (tzgg)
# ---------------------------------------------------------------------------

def _fetch_policy_detail(article_id, sig_cache, client, action=None, method=None):
    """Fetch article detail via a PoliciesRegulationsCmd API.

    Returns (markdown_text, attachments_list, metadata_dict).

    If *action* / *method* are given they override the default tzgg endpoint;
    this is used by the zcwj section (product.app.icity.policiesRegulations).
    """
    if action is None:
        action = "fujian.zhangzhougaoxin.app.icity.policy.PoliciesRegulationsCmd"
    if method is None:
        method = "getPoliciesRegulationsDetail"

    data = _call_api(action, method, {"id": article_id}, sig_cache, client)
    if not data:
        return "", [], {}

    detail = data if isinstance(data, dict) else {}
    if "data" in detail and isinstance(detail["data"], dict):
        detail = detail["data"]
    elif "data" in detail and isinstance(detail["data"], list) and detail["data"]:
        detail = detail["data"][0]

    meta_title = (detail.get("TITLE") or detail.get("title") or "").strip()
    meta_date = (detail.get("DISPLAYTIME") or detail.get("CREATDATE") or "").strip()
    meta_source = (detail.get("SOURCE") or detail.get("source") or "").strip()
    metadata = {"title": meta_title, "date": meta_date, "source": meta_source}

    content_text = (detail.get("CONTENT") or detail.get("content") or "").strip()

    # Extract attachments: HTML-based (tzgg) or DOC_ID-based (zcwj)
    attachments = []
    if "<" in content_text and ">" in content_text:
        attachments = _extract_attachments_from_html(content_text)

    # DOC_ID-based attachments (product API: zcwj section)
    doc_id = (detail.get("DOC_ID") or "").strip()
    attach_name = (detail.get("ATTACHMENT_NAME") or "").strip()
    if doc_id and attach_name:
        attach_url = _SITE_ROOT + "/imng/bsp/uploadify?action=downloadagent4wp&path=" + doc_id + "&name=" + quote(attach_name, safe="")
        attachments.append({"url": attach_url, "name": attach_name})

    if not content_text and not attachments:
        return "", [], metadata

    # For zcwj: if CONTENT is just the title, use the attachment name as supplement
    if not content_text or content_text == meta_title:
        if attach_name:
            content_text = meta_title + "\n\n附件: " + attach_name
        elif not content_text:
            return "", [], metadata

    if "<" in content_text and ">" in content_text:
        markdown_text = _html_to_markdown(content_text)
    else:
        markdown_text = content_text

    return markdown_text, attachments, metadata


# ---------------------------------------------------------------------------
# Article detail — Playwright page rendering
# ---------------------------------------------------------------------------

def _fetch_detail_by_page(template_url, art_raw, client):
    """Render a detail page via Playwright and extract content + attachments.

    Parameters
    ----------
    template_url : str
        URL template with {FIELD} placeholders from DETAIL_URL_TEMPLATES.
    art_raw : dict
        The raw listing row data used to fill placeholders.

    Returns (content_md, attachments_list).
    """
    # Replace {FIELD} placeholders with values from raw listing data
    try:
        resolved = template_url.format(**{k: v for k, v in art_raw.items() if isinstance(v, (str, int))})
    except KeyError as e:
        logging.warning("Missing field %s in listing data for URL template", e)
        return "", []

    full_url = _SITE_ROOT + resolved
    logging.info("Rendering detail page: %s", full_url)

    html = _fetch(full_url, client)
    if not html:
        return "", []

    # Extract attachments before content conversion
    attachments = _extract_attachments_from_html(html)

    # Convert full page HTML to markdown
    content = _html_to_markdown(html)
    return content, attachments


# ---------------------------------------------------------------------------
# Attachment extraction from HTML
# ---------------------------------------------------------------------------

_ATTACHMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar",
                          ".ppt", ".pptx", ".txt", ".csv", ".jpg", ".png")


def _extract_attachments_from_html(html_text, base_url=_SITE_ROOT):
    """Extract attachment download links from HTML content.

    Returns list[dict] with keys: url, name.
    """
    soup = BeautifulSoup(html_text, "lxml")
    attachments = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue

        is_attachment = (
            "uploadify" in href
            or "download" in href.lower()
            or any(href.lower().endswith(ext) for ext in _ATTACHMENT_EXTENSIONS)
        )
        if not is_attachment:
            continue

        # Resolve relative URLs
        if not href.startswith("http"):
            href = base_url + (href if href.startswith("/") else "/" + href)

        if href in seen_urls:
            continue
        seen_urls.add(href)

        name = a.get_text(strip=True) or "attachment"
        attachments.append({"url": href, "name": name})

    return attachments


# ---------------------------------------------------------------------------
# Attachment download & KB upload
# ---------------------------------------------------------------------------

def _download_and_upload_attachments(attachments, output_dir, kb_id, tenant_id, client):
    """Download attachment files and upload each to the knowledge base.

    Returns count of successfully uploaded attachments.
    """
    download_dir = os.path.join(output_dir, "downloads")
    os.makedirs(download_dir, exist_ok=True)
    uploaded = 0

    for att in attachments:
        att_url = att["url"]
        att_name = att.get("name", "attachment")

        _safe_print(f"[ZJFW]   Downloading attachment: {att_name}")
        sys.stdout.flush()

        file_bytes, content_type, filename = client.download(att_url)
        if not file_bytes:
            logging.warning("Failed to download attachment: %s", att_url)
            continue

        if not filename:
            filename = att_name
        # Sanitise filename
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

        # Ensure unique filenames by appending a short hash if needed
        base, ext = os.path.splitext(filename)
        if not ext and content_type:
            ext_map = {
                "application/pdf": ".pdf",
                "application/msword": ".doc",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.ms-excel": ".xls",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/zip": ".zip",
                "application/x-rar-compressed": ".rar",
            }
            ext = ext_map.get(content_type, ".bin")
        filename = base + ext

        # Avoid overwriting
        tmp_path = os.path.join(download_dir, filename)
        if os.path.exists(tmp_path):
            tmp_path = os.path.join(download_dir, f"{base}_{hashlib.md5(att_url.encode()).hexdigest()[:8]}{ext}")

        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        _safe_print(f"[ZJFW]   -> Saved {len(file_bytes)} bytes, uploading to KB...")
        sys.stdout.flush()

        try:
            _upload_to_kb(tmp_path, kb_id, tenant_id)
            uploaded += 1
        except Exception as e:
            logging.error("Failed to upload attachment %s: %s", filename, e)

    return uploaded


# ---------------------------------------------------------------------------
# Article detail — from listing data (no separate detail API)
# ---------------------------------------------------------------------------

_CONTENT_FIELDS = [
    ("REASON", "原因"),
    ("CROP_NAME", "委托单位"),
    ("NAME", "所属区县"),
    ("MAIN_DEP_NAME", "主管部门"),
    ("CONTACT", "联系方式"),
    ("TYPE", "类型"),
]


def _build_content_from_listing(art):
    """Build markdown content from listing data when no detail API is available."""
    raw = art.get("raw", {})
    lines = []
    for field, label in _CONTENT_FIELDS:
        val = (raw.get(field) or "").strip()
        if val and val != "-":
            lines.append(f"**{label}:** {val}")
    return "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _html_to_markdown(html_text):
    """Convert an HTML fragment to Markdown text."""
    soup = BeautifulSoup(html_text, "lxml")

    # Strip clutter
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    _TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "img",
             "div", "section", "table",
             "span", "strong", "font", "em", "b", "i", "u", "a"}

    lines = []
    seen_texts = set()

    for el in soup.find_all(list(_TAGS)):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                alt_text = f" ({alt})" if alt else ""
                lines.append(f"![{alt_text}]({src})")
            continue

        text = el.get_text(strip=True)
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)

        if tn == "h1":
            lines.append(f"\n# {text}\n")
        elif tn == "h2":
            lines.append(f"\n## {text}\n")
        elif tn == "h3":
            lines.append(f"\n### {text}\n")
        elif tn in ("h4", "h5", "h6"):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        elif tn == "p":
            lines.append(text)
        elif tn == "div" and not el.find_parent(["td", "th"]):
            lines.append(text)
        elif tn == "section":
            lines.append(text)
        elif tn in ("span", "strong", "font", "em", "b", "i", "u", "a"):
            lines.append(text)

    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown persistence & incremental state
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


def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


def _save_article_markdown(content, output_dir, article_id):
    """Save a single article's markdown for incremental crash safety."""
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
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
        logging.info("Document %s uploaded to KB %s", doc["id"], kb_id)
        try:
            DocumentService.begin2parse(doc["id"])
            DocumentService.run(tenant_id, doc, {})
            logging.info("Parsing task queued for document %s", doc["id"])
        except Exception as e:
            logging.error("Failed to queue parsing for document %s: %s",
                          doc["id"], e)
    return doc_pairs


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
    _safe_print("[ZJFW] Starting Zhangzhou Intermediary Service Platform crawler")
    _safe_print(f"[ZJFW] Target URL: {args.target_url}")
    _safe_print(f"[ZJFW] Task name: {args.task_name}")
    _safe_print(f"[ZJFW] Target KB: {args.kb_id}")
    _safe_print(f"[ZJFW] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[ZJFW] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== ZJFW crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        # Determine which sections to crawl
        if args.tzgg_only:
            selected = {"tzgg": SECTIONS["tzgg"]}
        elif args.cggg_only:
            selected = {"cggg": SECTIONS["cggg"]}
        elif args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in SECTIONS:
                    selected[label] = SECTIONS[label]
            if not selected:
                _safe_print(f"[ZJFW] No matching sections for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[ZJFW] Sections to crawl: {len(selected)}")
        for label, cfg in selected.items():
            _safe_print(f"         - {cfg[2]}")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[ZJFW] Output directory: {output_dir}\n")
        sys.stdout.flush()

        crawl_start = time.time()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[ZJFW] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # Initialise signature cache (fetches homepage on first use)
        sig_cache = {"sig": None, "key": None, "home_url": args.target_url.rstrip("/")}

        # -----------------------------------------------------------------------
        # Step 1: Crawl listing pages
        # -----------------------------------------------------------------------
        _safe_print("[ZJFW] Step 1/4: Crawling listing pages...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}
        cutoff = datetime.now() - timedelta(days=args.max_days)

        for section_id, section_cfg in selected.items():
            (list_action, list_method) = section_cfg[0]
            detail_api = section_cfg[1]
            display_label = section_cfg[2]
            id_field = section_cfg[3]
            date_field = section_cfg[4]

            _safe_print(f"[ZJFW]   Section '{display_label}':")
            sys.stdout.flush()

            # Use --max-days for policy sections; deadline-driven sections use today only
            if section_id in ("tzgg", "zcwj", "cggg"):
                section_cutoff = cutoff
            else:
                section_cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            section_articles = []
            page_size = 50
            start = 0

            while True:
                if args.max_articles and len(section_articles) >= args.max_articles:
                    break

                params = {"start": start, "limit": page_size}
                extra = LISTING_PARAMS.get(section_id, {})
                if extra:
                    params.update(extra)

                _safe_print(f"[ZJFW]     Fetching offset {start} (limit {page_size})")
                sys.stdout.flush()

                data = _call_api(list_action, list_method, params, sig_cache, client)
                if not data:
                    logging.warning("Failed to fetch listing for %s at offset %d",
                                    section_id, start)
                    break

                # Extract articles
                arts = _extract_articles(data, display_label, id_field, date_field)

                # Tag each article with its section_id for detail routing
                for a in arts:
                    a["section_id"] = section_id

                if not arts:
                    _safe_print("[ZJFW]     -> No more articles found, stopping")
                    sys.stdout.flush()
                    break

                # Filter by date and max_articles
                date_cutoff_hit = False
                for art in arts:
                    if args.max_articles and len(section_articles) >= args.max_articles:
                        break
                    if art.get("date") and art["date"] < section_cutoff:
                        date_cutoff_hit = True
                        break
                    section_articles.append(art)

                # Check if fewer results returned than page_size (last page)
                if len(arts) < page_size:
                    _safe_print(f"[ZJFW]     -> Last page ({len(arts)} items)")
                    sys.stdout.flush()
                    break

                if date_cutoff_hit:
                    _safe_print("[ZJFW]     -> Hit date cutoff, stopping pagination")
                    sys.stdout.flush()
                    break

                start += page_size
                time.sleep(0.3)

            count = len(section_articles)
            section_stats[display_label] = count
            all_articles.extend(section_articles)

            _safe_print(f"[ZJFW]     -> {count} articles\n")
            sys.stdout.flush()

        _safe_print(f"[ZJFW] Collected {len(all_articles)} total articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ZJFW] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print("[ZJFW] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Deduplicate with state (by article ID)
        if processed_ids:
            new_articles = [a for a in all_articles if a["id"] not in processed_ids]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[ZJFW] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print("[ZJFW] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # -----------------------------------------------------------------------
        # Step 2: Fetch detail pages
        # -----------------------------------------------------------------------
        _safe_print(f"\n[ZJFW] Step 2/5: Fetching {len(all_articles)} article details...\n")
        sys.stdout.flush()

        # Build section_id → detail_api lookup from selected sections
        section_detail_api = {}
        for sid, scfg in selected.items():
            section_detail_api[sid] = scfg[1]  # detail_api tuple or None

        md_parts = []
        all_attachments = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        stopped_early = False

        for idx, art in enumerate(all_articles, 1):
            elapsed = time.time() - crawl_start
            remaining = args.max_runtime - elapsed
            if remaining < 120:
                _safe_print(
                    f"\n[ZJFW] Runtime {elapsed:.0f}s approaching limit "
                    f"({args.max_runtime}s), stopping early "
                    f"({total - idx + 1} article(s) remaining)"
                )
                sys.stdout.flush()
                stopped_early = True
                break

            _safe_print(f"[ZJFW] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            sid = art.get("section_id", "")
            detail_api = section_detail_api.get(sid)
            content = ""
            attachments = []
            metadata = {}

            if detail_api is not None:
                # API-based detail
                if "BidNotice" in detail_api[0]:
                    content, attachments, metadata = _fetch_bid_notice_detail(
                        art["id"], sig_cache, client
                    )
                    # Fallback to PARENT_BID_ID for cqgg/cfgg if empty
                    if not content and art.get("raw", {}).get("PARENT_BID_ID"):
                        content, attachments, metadata = _fetch_bid_notice_detail(
                            art["raw"]["PARENT_BID_ID"], sig_cache, client
                        )
                elif "PoliciesRegulations" in detail_api[0]:
                    content, attachments, metadata = _fetch_policy_detail(
                        art["id"], sig_cache, client,
                        action=detail_api[0], method=detail_api[1],
                    )
                else:
                    content, attachments, metadata = _fetch_bid_notice_detail(
                        art["id"], sig_cache, client
                    )

            elif sid in DETAIL_URL_TEMPLATES:
                # Playwright page-rendered detail
                content, attachments = _fetch_detail_by_page(
                    DETAIL_URL_TEMPLATES[sid], art.get("raw", {}), client
                )
                metadata = {"title": art["title"], "date": "", "source": ""}
                if art.get("date"):
                    metadata["date"] = art["date"].strftime("%Y-%m-%d")

            else:
                # Fallback: use listing data
                content = _build_content_from_listing(art)
                metadata = {"title": art["title"], "date": "", "source": ""}
                if art.get("date"):
                    metadata["date"] = art["date"].strftime("%Y-%m-%d")

            if not content:
                _safe_print("[ZJFW]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            att_info = f" + {len(attachments)} attachment(s)" if attachments else ""
            _safe_print(f"[ZJFW]   -> {len(content)} chars{att_info}")
            sys.stdout.flush()

            if attachments:
                art["attachments"] = attachments
                all_attachments.extend(attachments)

            article_date_str = ""
            if art.get("date"):
                article_date_str = art["date"].strftime("%Y-%m-%d")
            elif metadata.get("date"):
                article_date_str = metadata["date"]

            source_str = metadata.get("source", "")
            source_line = f"**Source:** {source_str}" if source_str else ""

            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section']}",
                f"**Date:** {article_date_str}",
            ]
            if source_line:
                lines.append(source_line)
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            md_part = "\n".join(lines)
            md_parts.append(md_part)
            success_count += 1

            # Incremental save for crash safety & time-bounded resilience
            _save_article_markdown(md_part, output_dir, art["id"])
            processed_ids.add(art["id"])
            _save_state(output_dir, {"processed_ids": list(processed_ids)})

            time.sleep(0.3)

        if not md_parts:
            _safe_print("[ZJFW] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[ZJFW] Details: {success_count} success, {fail_count} failed")
        _safe_print(f"[ZJFW] Total attachments found: {len(all_attachments)}\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 3: Save markdown (state already saved incrementally above)
        # -----------------------------------------------------------------------
        if stopped_early:
            _safe_print(
                f"[ZJFW] Step 3/5: Saving partial markdown "
                f"({success_count} success, {fail_count} failed)..."
            )
        else:
            _safe_print("[ZJFW] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[ZJFW] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 4: Download & upload attachments
        # -----------------------------------------------------------------------
        if all_attachments:
            _safe_print(f"[ZJFW] Step 4/5: Downloading {len(all_attachments)} attachment(s)...")
            sys.stdout.flush()
            att_uploaded = _download_and_upload_attachments(
                all_attachments, output_dir, args.kb_id, args.tenant_id, client
            )
            _safe_print(f"[ZJFW] Uploaded {att_uploaded}/{len(all_attachments)} attachment(s) to KB\n")
            sys.stdout.flush()
        else:
            _safe_print("[ZJFW] Step 4/5: No attachments found, skipping\n")
            sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 5: Upload markdown to KB
        # -----------------------------------------------------------------------
        _safe_print(f"[ZJFW] Step 5/5: Uploading markdown to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print("[ZJFW] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[ZJFW] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
