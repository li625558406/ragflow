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
Dedicated web crawler for www.mwr.gov.cn (中华人民共和国水利部).

Covers two sections:
  A) http://www.mwr.gov.cn/zwgk/index.html
     - 政策法规(规章库): /zwgk/gzk/bmgz/ (6 pages, table layout, doc/pdf downloads)
     - 政府信息公开制度: /zwgk/gkzd/ (1 page, div layout)

  B) http://www.mwr.gov.cn/zw/zcfg/ (policy & law section)
     - 政策解读: /zw/zcjd/ (8 pages)
     - 法律: /zw/zcfg/fl/ (1 page)
     - 行政法规和法规性文件: /zw/zcfg/xzfghfgxwj/ (1 page)
     - 部门规章: /zw/zcfg/bmgz/ (3 pages)
     - 规范性文件: /zw/zcfg/gfxwj/ (16 pages)

Site characteristics
--------------------
  - Pure SSR HTML — no JavaScript/AJAX required, requests + BeautifulSoup only
  - TRS WCM CMS: detail URLs follow /{YYYYMM}/t{YYYYMMDD}_{id}.html pattern
  - Pagination: index_{N}.html (page 0 = index.html)
  - Two detail page layouts:
    1. 规章库: .gzk-content with .download section for doc/pdf
    2. 政策法规/解读: .view.TRS_UEDITOR for content, meta tags for title/date
  - Anti-crawling: normal gov site, moderate delays + browser UA suffice
  - No Playwright required

Usage:
    python mwr_crawler.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>

    python mwr_crawler.py \
        --tenant-id <ID> --kb-id <ID> --task-name <NAME> \
        --section zcfg_gzk,zcfg_zcjd
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from datetime import datetime
from urllib.parse import urljoin

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
_SITE_ROOT = "http://www.mwr.gov.cn"
_SITE_NAME = "中华人民共和国水利部"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Anti-crawling delays (seconds)
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.0

_STATE_FILENAME = "_crawler_state.json"

# ── Module definitions ────────────────────────────────────────────────────
# Each module is independent with its own listing URL, pagination, and detail format.

_MODULES = [
    # -- Group A: 政府信息公开 (zwgk/) --
    {
        "key": "zcfg_gzk",
        "name": "政策法规(规章库)",
        "list_url": "/zwgk/gzk/bmgz/index.html",
        "page_base": "/zwgk/gzk/bmgz/index",
        "detail_type": "gzk",   # .gzk-content layout with downloads
    },
    {
        "key": "zfxxgkzd",
        "name": "政府信息公开制度",
        "list_url": "/zwgk/gkzd/",
        "page_base": None,       # single page, no pagination
        "detail_type": "trs",    # TRS CMS detail
    },
    # -- Group B: 政策法规 (zw/zcfg/) + 政策解读 --
    {
        "key": "zcjd",
        "name": "政策解读",
        "list_url": "/zw/zcjd/index.html",
        "page_base": "/zw/zcjd/index",
        "detail_type": "trs",
    },
    {
        "key": "fl",
        "name": "法律",
        "list_url": "/zw/zcfg/fl/index.html",
        "page_base": "/zw/zcfg/fl/index",
        "detail_type": "trs",
    },
    {
        "key": "xzfghfgxwj",
        "name": "行政法规和法规性文件",
        "list_url": "/zw/zcfg/xzfghfgxwj/index.html",
        "page_base": "/zw/zcfg/xzfghfgxwj/index",
        "detail_type": "trs",
    },
    {
        "key": "bmgz",
        "name": "部门规章",
        "list_url": "/zw/zcfg/bmgz/index.html",
        "page_base": "/zw/zcfg/bmgz/index",
        "detail_type": "trs",
    },
    {
        "key": "gfxwj",
        "name": "规范性文件",
        "list_url": "/zw/zcfg/gfxwj/index.html",
        "page_base": "/zw/zcfg/gfxwj/index",
        "detail_type": "trs",
    },
]

# -- URL helpers ------------------------------------------------------------
_DETAIL_URL_RE = re.compile(r"/(\d{6})/t(\d{8})_(\d+)\.html")
_DATE_URL_RE = re.compile(r"/(\d{4})(\d{2})/")


def _make_art_id(prefix, url_or_text):
    """Generate a safe article ID from a URL or text string.

    1. Try matching the standard TRS pattern: /YYYYMM/tYYYYMMDD_id.html
    2. Fall back to hashing the input string.
    """
    m = _DETAIL_URL_RE.search(url_or_text)
    if m:
        return "{}_{}".format(prefix, m.group(3))
    # Fallback: use a hash of the URL to avoid bad filename characters
    import hashlib
    h = hashlib.md5(url_or_text.encode()).hexdigest()[:12]
    return "{}_{}".format(prefix, h)


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


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Session init
# ---------------------------------------------------------------------------

def _init_session():
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return sess


# ---------------------------------------------------------------------------
# Listing extraction
# ---------------------------------------------------------------------------

def _extract_gzk_listing(html, base_url):
    """Extract articles from 规章库 (gzk/bmgz) table format.

    Each article is a table.zcfg-item row with:
    - .ixh span: sequence number
    - .ititle a: detail link, title embedded in showTitle() JS call
    - .zcfg-down a: download links (.doc / .pdf)
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for table in soup.select("table.zcfg-item"):
        title_el = table.select_one(".ititle a")
        if not title_el:
            continue
        href = title_el.get("href", "").strip()
        if not href:
            continue

        # Title is inside showTitle('...') script tag inside the anchor
        title = ""
        script_el = title_el.select_one("script")
        if script_el and script_el.string:
            m = re.search(r"showTitle\s*\(\s*['\"](.+?)['\"]", script_el.string)
            if m:
                title = m.group(1).strip()
        if not title:
            # Fallback: try direct text (in case JS isn't used)
            title = title_el.get_text(strip=True)
        if not title:
            continue

        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        art_id = _make_art_id("gzk", abs_url)

        # Extract date from URL path
        date_str = ""
        date_m = _DATE_URL_RE.search(abs_url)
        if date_m:
            date_str = "{}-{}-{}".format(
                date_m.group(1), date_m.group(2), "01")

        # Check for download links in the same row
        download_links = []
        for dl in table.select(".zcfg-down a, a[href]"):
            dl_href = dl.get("href", "")
            if dl_href and re.search(r'\.(doc|docx|pdf)\b', dl_href, re.I):
                download_links.append({
                    "name": dl.get_text(strip=True),
                    "href": urljoin(abs_url, dl_href),
                })

        articles.append({
            "id": art_id,
            "title": title,
            "url": abs_url,
            "date_str": date_str,
            "prefetch_attachments": download_links,
        })

    return articles


def _extract_trs_listing(html, base_url):
    """Extract articles from TRS CMS listing (slnewsconlist > li format).

    Used by 政策法规 tabs and 政策解读.
    <ul class="slnewsconlist">
      <li><span>YYYY-MM-DD</span><a href="./.../t...html">Title</a></li>
    </ul>
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for ul in soup.select("ul.slnewsconlist"):
        for li in ul.select("li"):
            a_tag = li.select_one("a[href]")
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(strip=True)
            if not href or not title:
                continue

            abs_url = urljoin(base_url, href)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            # Date from <span>
            span = li.select_one("span")
            date_str = span.get_text(strip=True) if span else ""
            if date_str and re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                pass
            else:
                date_str = ""

            art_id = _make_art_id("trs", abs_url)

            articles.append({
                "id": art_id,
                "title": title,
                "url": abs_url,
                "date_str": date_str,
            })

    return articles


def _extract_gkzd_listing(html, base_url):
    """Extract articles from 政府信息公开制度 page.

    Two sections, each with .gkzd_title heading and .item_con > .itemz > a items.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for item_con in soup.select(".item_con"):
        for itemz in item_con.select(".itemz"):
            a_tag = itemz.select_one("a[href]")
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(strip=True)
            if not href or not title:
                continue

            abs_url = urljoin(base_url, href)
            if abs_url in seen:
                continue
            seen.add(abs_url)

            # Extract date from URL or from the text after &nbsp;
            date_str = ""
            date_m = _DATE_URL_RE.search(abs_url)
            # Try to get date from text: "...&nbsp;&nbsp;YYYY-MM-DD"
            text_m = re.search(
                r'(\d{4}-\d{2}-\d{2})\s*$', a_tag.get_text())
            if text_m:
                date_str = text_m.group(1)
            elif date_m:
                date_str = "{}-{}-{}".format(
                    date_m.group(1), date_m.group(2), "01")

            art_id = _make_art_id("gkzd", abs_url)

            articles.append({
                "id": art_id,
                "title": title,
                "url": abs_url,
                "date_str": date_str,
            })

    return articles


def _get_pagination(html):
    """Extract countPage and currentPage from TRS pagination JS."""
    cp_m = re.search(r'var countPage\s*=\s*(\d+)', html)
    pp_m = re.search(r'var currentPage\s*=\s*(\d+)', html)
    if cp_m:
        return {
            "total_pages": int(cp_m.group(1)),
            "current_page": int(pp_m.group(1)) if pp_m else 0,
        }
    return {"total_pages": 1, "current_page": 0}


# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------

def _extract_gzk_detail(html, detail_url):
    """Extract detail from 规章库 detail page (.gzk-content layout).

    Title: <meta name="ArticleTitle"> or .gzk-title
    Date: <meta name="PubDate">
    Content: .gzk-content-m
    Attachments: .download a[href]
    """
    result = {"title": "", "date": "", "content_text": "", "attachments": []}
    soup = BeautifulSoup(html, "html.parser")

    # Title from meta
    meta_title = soup.select_one('meta[name="ArticleTitle"]')
    if meta_title and meta_title.get("content"):
        result["title"] = meta_title["content"].strip()

    if not result["title"]:
        title_el = soup.select_one(".gzk-title")
        if title_el:
            result["title"] = title_el.get_text(strip=True)

    # Date from meta
    meta_date = soup.select_one('meta[name="PubDate"]')
    if meta_date and meta_date.get("content"):
        dt = meta_date["content"].strip()
        date_m = re.match(r'(\d{4}-\d{2}-\d{2})', dt)
        if date_m:
            result["date"] = date_m.group(1)

    # Content
    content_el = soup.select_one(".gzk-content-m")
    if content_el:
        result["content_text"] = content_el.get_text("\n", strip=True)
        if len(result["content_text"]) > 50000:
            result["content_text"] = result["content_text"][:50000] + \
                "\n\n（内容过长，已截断）"

    # Attachments from .download section
    download_el = soup.select_one(".download")
    if download_el:
        for a in download_el.select("a[href]"):
            href = a.get("href", "").strip()
            name = a.get_text(strip=True)
            if href and re.search(r'\.(doc|docx|pdf)\b', href, re.I):
                abs_url = urljoin(detail_url, href)
                result["attachments"].append({
                    "name": name or os.path.basename(href),
                    "download_url": abs_url,
                })

    # Also check for inline download scripts
    for script in soup.select("script"):
        text = script.string or ""
        if "rel_appendix" in text:
            # var rel_appendix = '<a href="./P0202....doc">下载文字版</a>|...'
            for m in re.finditer(
                    r"href\s*=\s*[\"'](\.\/[^\"']+\.(?:doc|docx|pdf))[\"']",
                    text, re.I):
                href = m.group(1)
                abs_url = urljoin(detail_url, href)
                # Check if already added
                if not any(a["download_url"] == abs_url
                          for a in result["attachments"]):
                    result["attachments"].append({
                        "name": os.path.basename(href),
                        "download_url": abs_url,
                    })

    return result


def _extract_trs_detail(html, detail_url):
    """Extract detail from TRS CMS detail page (政策法规/解读).

    Title: <meta name="ArticleTitle">
    Date: <meta name="PubDate">
    Content: .view.TRS_UEDITOR or .slnewscon
    Attachments: links to .doc/.pdf in content area
    """
    result = {"title": "", "date": "", "content_text": "", "attachments": []}
    soup = BeautifulSoup(html, "html.parser")

    # Title from meta
    meta_title = soup.select_one('meta[name="ArticleTitle"]')
    if meta_title and meta_title.get("content"):
        result["title"] = meta_title["content"].strip()

    # Date from meta
    meta_date = soup.select_one('meta[name="PubDate"]')
    if meta_date and meta_date.get("content"):
        dt = meta_date["content"].strip()
        date_m = re.match(r'(\d{4}-\d{2}-\d{2})', dt)
        if date_m:
            result["date"] = date_m.group(1)

    # Content
    content_selectors = [
        ".view.TRS_UEDITOR", ".slnewscon .view",
        "#slywxl2 .view", "[class*='TRS_UEDITOR']",
    ]
    content_el = None
    for sel in content_selectors:
        content_el = soup.select_one(sel)
        if content_el:
            break

    if not content_el:
        # Fallback: body text minus nav/footer
        body = soup.body
        if body:
            result["content_text"] = body.get_text("\n", strip=True)
            cutoff = result["content_text"].find("水利部总机")
            if cutoff > 200:
                result["content_text"] = result["content_text"][:cutoff]
    else:
        result["content_text"] = content_el.get_text("\n", strip=True)

    if len(result["content_text"]) > 50000:
        result["content_text"] = result["content_text"][:50000] + \
            "\n\n（内容过长，已截断）"

    # Attachments: look for links to doc/pdf/zip in content
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        name = a.get_text(strip=True)
        if not href:
            continue
        if re.search(r'\.(doc|docx|pdf|xls|xlsx|zip|rar|7z)\b', href, re.I):
            abs_url = urljoin(detail_url, href)
            if abs_url not in seen:
                seen.add(abs_url)
                result["attachments"].append({
                    "name": name or os.path.basename(href),
                    "download_url": abs_url,
                })

    return result


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachment(sess, att, dest_dir):
    url = att.get("download_url", "")
    name = att.get("name", "unknown")

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)
    if not re.search(r'\.\w{2,5}$', safe_name):
        ext_m = re.search(r'\.(\w{2,5})$', url)
        if ext_m:
            safe_name += ext_m.group(0)
        else:
            safe_name += ".bin"

    dest_path = os.path.join(dest_dir, safe_name)
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    try:
        _request_delay()
        resp = sess.get(url, timeout=120, stream=True)
        if resp.status_code == 200 and len(resp.content) > 100:
            # Check Content-Disposition for real filename
            cd = resp.headers.get("Content-Disposition", "")
            fn_match = re.search(
                r'filename\*?[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
            if fn_match:
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", fn_match.group(1))
                dest_path = os.path.join(dest_dir, safe_name)

            with open(dest_path, "wb") as f:
                f.write(resp.content)
            _safe_print("      Downloaded: {} ({} bytes)".format(
                safe_name, len(resp.content)))
            return dest_path
        else:
            _safe_print("      Download failed: status={}, size={}".format(
                resp.status_code, len(resp.content)))
    except Exception as e:
        logging.warning("Download error for %s: %s", name, e)

    return None


# ---------------------------------------------------------------------------
# File extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if member.startswith("__MACOSX") or member.startswith("."):
                    continue
                safe_name = re.sub(r'[\\/:*?"<>|]', "_",
                                   os.path.basename(member))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, "wb") as f:
                    f.write(zf.read(member))
                extracted.append(dest_path)
                _safe_print("      Extracted: {}".format(safe_name))
        os.remove(zip_path)
    except Exception as e:
        logging.warning("ZIP extract error for %s: %s",
                        os.path.basename(zip_path), e)
    return extracted


def _extract_file_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            try:
                import pdfplumber
                parts = []
                with pdfplumber.open(filepath) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            parts.append(text)
                return "\n\n".join(parts) if parts else None
            except ImportError:
                pass
            try:
                import fitz
                doc = fitz.open(filepath)
                parts = []
                for page in doc:
                    text = page.get_text()
                    if text:
                        parts.append(text)
                doc.close()
                return "\n\n".join(parts) if parts else None
            except ImportError:
                return "(PDF file, no parser available)"
        elif ext in (".doc", ".docx"):
            if ext == ".docx":
                try:
                    import docx
                    doc = docx.Document(filepath)
                    return "\n".join(
                        p.text for p in doc.paragraphs if p.text.strip())
                except ImportError:
                    pass
            return "(DOC file, text extraction limited)"
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(
                        " | ".join(str(c) if c is not None else ""
                                   for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) +
                                 "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------

def _build_markdown(art, detail, attachment_texts):
    title = detail.get("title") or art.get("title", "无标题")
    lines = [
        "# {}".format(title),
        "",
        "**栏目:** {}".format(art.get("section", "")),
    ]
    date_str = detail.get("date") or art.get("date_str", "")
    if date_str:
        lines.append("**日期:** {}".format(date_str))
    lines.append("**URL:** {}".format(art.get("url", "")))
    lines.append("")

    content = detail.get("content_text", "")
    if content:
        if len(content) > 50000:
            content = content[:50000] + "\n\n（内容过长，已截断）"
        lines.append("## 详细内容")
        lines.append("")
        lines.append(content)
        lines.append("")

    if attachment_texts:
        lines.append("## 附件内容")
        lines.append("")
        for fname, ftext in attachment_texts:
            lines.append("### {}".format(fname))
            lines.append("")
            if ftext:
                if len(ftext) > 50000:
                    ftext = ftext[:50000] + "\n\n（附件内容过长，已截断）"
                lines.append(ftext)
            else:
                lines.append("（无法提取文本内容）")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": [], "completed_modules": [],
            "module_progress": {}}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d modules done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_modules", [])))


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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Module-level processing
# ---------------------------------------------------------------------------

def _process_module(sess, output_dir, kb_id, tenant_id,
                    module_info, processed_ids, state):
    module_key = module_info["key"]
    module_name = module_info["name"]
    detail_type = module_info.get("detail_type", "trs")
    list_url = urljoin(_SITE_ROOT, module_info["list_url"])
    page_base = module_info.get("page_base")
    # Derive list_base_dir from list_url for constructing page URLs
    list_base_dir = list_url.rsplit("/", 1)[0] if list_url.endswith(".html") \
        else list_url.rstrip("/")

    if module_key in state.get("completed_modules", []):
        _safe_print("[{}] Already completed, skipping.".format(module_name))
        sys.stdout.flush()
        return 0

    progress = state.get("module_progress", {}).get(
        module_key, {"page": 0, "done": False}
    )

    _safe_print("[{}] Listing URL: {}".format(module_name, list_url))
    sys.stdout.flush()

    # Extract function per module type
    if module_key == "zcfg_gzk":
        extract_list = _extract_gzk_listing
    elif module_key == "zfxxgkzd":
        extract_list = _extract_gkzd_listing
    else:
        extract_list = _extract_trs_listing

    if detail_type == "gzk":
        extract_detail = _extract_gzk_detail
    else:
        extract_detail = _extract_trs_detail

    BATCH_SIZE = 10
    total_processed = 0
    downloads_dir = os.path.join(output_dir, "downloads")

    try:
        # Load first page to get pagination info
        _request_delay()
        resp = sess.get(list_url, timeout=30)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            _safe_print("[{}] Failed to load listing: {}".format(
                module_name, resp.status_code))
            return 0

        paging = _get_pagination(resp.text)
        total_pages = paging["total_pages"]
        start_page = progress.get("page", 0)

        _safe_print("[{}] Total pages: {} (resuming from page {})".format(
            module_name, total_pages, start_page))
        sys.stdout.flush()

        if total_pages == 0:
            total_pages = 1

        stop_early = False

        for page_num in range(start_page, total_pages):
            html = resp.text if page_num == start_page else None

            if page_num > start_page:
                if page_base:
                    if page_num == 0:
                        page_url = list_url
                    else:
                        page_url = urljoin(
                            _SITE_ROOT,
                            "{}_{}.html".format(page_base, page_num))
                else:
                    # No pagination (gkzd style)
                    break

                _request_delay()
                resp = sess.get(page_url, timeout=30)
                resp.encoding = "utf-8"
                if resp.status_code != 200:
                    _safe_print("[{}] Failed to load page {}: {}".format(
                        module_name, page_num, resp.status_code))
                    break
                html = resp.text

            if not html:
                continue

            # Extract articles
            articles = extract_list(html, list_url)

            # Filter out already processed
            new_articles = [a for a in articles if a["id"] not in processed_ids]

            if len(new_articles) == 0:
                _safe_print("[{}] Page {}: all {} items already processed".format(
                    module_name, page_num, len(articles)))
                if page_num - start_page > 10:
                    break  # don't scan too many empty pages
                continue

            _safe_print("[{}] Page {}/{}: {} items ({} new)".format(
                module_name, page_num, total_pages,
                len(articles), len(new_articles)))
            sys.stdout.flush()

            for a in new_articles:
                a["section"] = module_name

            # Process in batches
            for batch_start in range(0, len(new_articles), BATCH_SIZE):
                batch = new_articles[batch_start:batch_start + BATCH_SIZE]
                batch_num = (batch_start // BATCH_SIZE) + 1
                md_parts = []
                batch_ids = []

                for idx, art in enumerate(batch, 1):
                    global_idx = batch_start + idx
                    _safe_print("[{}] [{}/{}] {}".format(
                        module_name, global_idx, len(new_articles),
                        art["title"][:60]))
                    sys.stdout.flush()

                    # Fetch detail page
                    _request_delay()
                    try:
                        d_resp = sess.get(art["url"], timeout=30)
                        d_resp.encoding = "utf-8"
                        if d_resp.status_code != 200:
                            logging.warning(
                                "Detail page failed: %s (%d)",
                                art["url"], d_resp.status_code)
                            continue
                        detail = extract_detail(d_resp.text, art["url"])
                    except Exception as e:
                        logging.warning(
                            "Detail page error %s: %s", art["url"], e)
                        continue

                    # Merge prefetched attachments (for gzk modules)
                    prefetch = art.pop("prefetch_attachments", [])
                    if prefetch and not detail.get("attachments"):
                        detail["attachments"] = prefetch

                    # Download attachments
                    attachment_texts = []
                    for att in detail.get("attachments", []):
                        att_name = att.get("name", "unknown")
                        dest_dir = os.path.join(
                            downloads_dir, module_key, art["id"])
                        fp = _download_attachment(sess, att, dest_dir)
                        if fp:
                            is_zip = (
                                fp.lower().endswith((".zip", ".rar", ".7z")) or
                                (os.path.getsize(fp) >= 4 and
                                 open(fp, "rb").read(4) == b"PK\x03\x04")
                            )
                            if is_zip:
                                extracted = _extract_zip(fp)
                            else:
                                extracted = []

                            if extracted:
                                for ext_fp in extracted:
                                    text = _extract_file_text(ext_fp)
                                    attachment_texts.append(
                                        (os.path.basename(ext_fp), text))
                            else:
                                text = _extract_file_text(fp)
                                attachment_texts.append((att_name, text))

                    md = _build_markdown(art, detail, attachment_texts)

                    articles_dir = os.path.join(
                        output_dir, "articles", module_key)
                    os.makedirs(articles_dir, exist_ok=True)
                    md_path = os.path.join(
                        articles_dir, "{}.md".format(art["id"]))
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(md)

                    md_parts.append(md)
                    batch_ids.append(art["id"])

                # Checkpoint after each batch
                if md_parts:
                    batch_path = os.path.join(
                        output_dir, "{}_{:03d}_{:03d}.md".format(
                            module_key, page_num, batch_num))
                    with open(batch_path, "w", encoding="utf-8") as f:
                        f.write("\n\n---\n\n".join(md_parts))

                    processed_ids.update(batch_ids)
                    state["processed_ids"] = list(processed_ids)
                    state.setdefault("module_progress", {})[module_key] = {
                        "page": page_num, "done": False}
                    _save_state(output_dir, state)

                    if kb_id:
                        try:
                            _upload_to_kb(batch_path, kb_id, tenant_id)
                        except Exception as e:
                            _safe_print(
                                "[{}] batch upload failed: {}".format(
                                    module_name, e))
                            logging.error("Upload failed: %s", e)

                    total_processed += len(md_parts)
                    _safe_print("[{}] p{}b{} uploaded ({} total)".format(
                        module_name, page_num, batch_num, total_processed))
                    sys.stdout.flush()

            # Update page progress
            state.setdefault("module_progress", {})[module_key] = {
                "page": page_num + 1, "done": False}
            _save_state(output_dir, state)

        # Module complete
        state.setdefault("completed_modules", []).append(module_key)
        state.setdefault("module_progress", {})[module_key] = {
            "page": total_pages, "done": True}
        _save_state(output_dir, state)

    except Exception as e:
        logging.error("[%s] Module error: %s", module_name, e)
        _safe_print("[{}] ERROR: {}".format(module_name, e))
        sys.stdout.flush()

    _safe_print("[{}] Done: {} processed\n".format(
        module_name, total_processed))
    sys.stdout.flush()
    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="mwr crawler - 中华人民共和国水利部"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="http://www.mwr.gov.cn/zwgk/index.html")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Comma-separated module keys (e.g. zcfg_gzk,zcjd,fl)")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds (default: 3300s = 55min)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[MWR] 中华人民共和国水利部 crawler")
    _safe_print("[MWR] KB: {}".format(args.kb_id))
    _safe_print("[MWR] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== MWR crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[MWR] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_modules": [], "module_progress": {}
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_modules = set(state.get("completed_modules", []))
    _safe_print("[MWR] Previously processed: {}, completed modules: {}\n".format(
        len(processed_ids), len(completed_modules)))
    sys.stdout.flush()

    if args.section:
        selected = set(args.section.split(","))
        active_modules = [m for m in _MODULES if m["key"] in selected]
    else:
        active_modules = [m for m in _MODULES
                         if m["key"] not in completed_modules]

    _safe_print("[MWR] Active modules: {}".format(
        ", ".join(m["name"] for m in active_modules)))
    sys.stdout.flush()

    sess = _init_session()
    _safe_print("[MWR] Session initialized\n")
    sys.stdout.flush()

    run_start = time.time()
    total_processed = 0
    stopped_early = False

    for mod_info in active_modules:
        elapsed = time.time() - run_start
        remaining = args.max_runtime - elapsed
        grace = min(120, args.max_runtime * 0.05)
        if remaining < grace:
            _safe_print(
                "\n[MWR] Runtime {:.0f}s, remaining {:.0f}s < "
                "grace {:.0f}s, stopping early.".format(
                    elapsed, remaining, grace))
            sys.stdout.flush()
            stopped_early = True
            break

        n = _process_module(sess, output_dir, args.kb_id,
                            args.tenant_id, mod_info,
                            processed_ids, state)
        total_processed += n

    _safe_print("\n" + "=" * 60)
    if stopped_early:
        _safe_print("[MWR] Partial run: {} articles. "
                    "Will resume from checkpoint.".format(total_processed))
    else:
        _safe_print("[MWR] Done: {} articles processed.".format(
            total_processed))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== MWR crawler finished: %d articles ===",
                 total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "mwr_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
