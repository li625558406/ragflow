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
Crawler for jtyst.fujian.gov.cn — 福建省交通运输厅 解读回应 (ALL data).

Covers three URL targets:
  1. /jdhy/hygq/     — 回应关切 listing (paginated, ~293 records)
  2. /jdhy/zcjd/     — 政策解读 main page (3 sub-sections, each links to full listing)
  3. /jdhy/          — 解读回应 main page (carousel + policy list)

Gets ALL list data (no date filtering). Cross-section dedup by article_id.
Server-rendered HTML with avalon.js framework — no JS rendering needed.

Data flow
---------
  1. Listing: GET each listing URL -> parse <li> entries for title, URL, date
  2. Pagination: try index_{N}.html for sections with wasdoc pagination
  3. Detail: GET each .htm detail page -> parse <h2> title, meta tags,
     .TRS_Editor content, .myzj_xl_list attachment links
  4. Attachments: direct download (relative or absolute URLs)
  5. ZIP auto-extraction with content parsing (pdfplumber, python-docx, openpyxl)

Checkpoint/resume: state saved per batch. Time-bounded check
(default 3300s) stops gracefully before the 3600s task-timeout window.

Usage
-----
    python jtyst_jdhy_crawler.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>

    # Optional:
        --section hygq        # Only 回应关切
        --section zcjd        # Only 政策解读 (incl. sub-pages)
        --section jdhy        # Only main 解读回应 page
        --section all         # All three (default)
        --max-runtime 3300    # Max runtime before graceful stop
        --full                # Ignore saved state, re-crawl
"""

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://jtyst.fujian.gov.cn"
_SECTION_LABEL = "福建省交通运输厅-解读回应"

# Listing URLs
_LISTING_URLS = {
    "jdhy": "/jdhy/",
    "hygq": "/jdhy/hygq/",
    "zcjd": "/jdhy/zcjd/",
}

# zcjd sub-pages (each has its own listing with pagination)
_ZCJD_SUB_PAGES = {
    "zctj": "/jdhy/zcjd/zctj/",
    "wzjd": "/jdhy/zcjd/wzjd/",
    "sjjd": "/jdhy/zcjd/sjjd/",
}

# Checkpoint batch size (articles)
_BATCH_SIZE = 3

# Default max runtime (55 min, 5 min margin)
_MAX_RUNTIME_DEFAULT = 3300

# Anti-crawling delays (seconds)
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.0

# State filename
_STATE_FILENAME = "_crawler_state.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Max pagination pages to try per section
_MAX_PAGES = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _sanitize_filename(text, max_len=120):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name).strip("._ ")
    return (name or "untitled")[:max_len]


def _extract_article_id(url):
    """Extract article ID from URL.

    Patterns:
      .../t20260522_7151059.htm  -> 7151059
      .../P020260109398721339121.pdf -> P020260109398721339121
    """
    m = re.search(r'/t\d{8}_(\d+)\.htm', url)
    if m:
        return m.group(1)
    m = re.search(r'/([A-Z]\d{15,})\.(?:pdf|doc|docx|xlsx|xls|zip|rar)', url)
    if m:
        return m.group(1)
    # Fallback: hash the URL path
    path = urllib.parse.urlparse(url).path
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _extract_date_from_url(url):
    """Extract date from URL path.

    Patterns:
      .../202605/t20260522_... -> 2026-05-22
      .../202605/...           -> 2026-05 (month precision fallback)
    """
    m = re.search(r'/t(\d{4})(\d{2})(\d{2})_', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'/(\d{4})(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _resolve_url(href, base_url):
    """Resolve a relative href against a base URL."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return urllib.parse.urljoin(base_url, href)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url, referer=None, timeout=30):
    """GET request returning bytes or None."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if referer:
        req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logging.error("GET %s failed: %s", url, e)
        return None


def _download_binary(url, referer=None, timeout=60):
    """Download binary content; returns bytes or None."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if referer:
        req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logging.error("Download failed: %s — %s", url[:120], e)
        return None


# ---------------------------------------------------------------------------
# Generic listing parser for <ul class="jtt-gl_list"> pages
# ---------------------------------------------------------------------------

def _parse_generic_listing(html_bytes, base_url, section_label):
    """Parse a listing page with <ul class="jtt-gl_list"> items.

    Handles both single <ul> and multiple <ul> groups.
    Returns list of {"title", "url", "date_str", "article_id", "section"}.
    """
    if not html_bytes:
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for ul in soup.find_all("ul", class_="jtt-gl_list"):
        for li in ul.find_all("li"):
            span = li.find("span")
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag["href"].strip()
            title = (a_tag.get("title") or a_tag.get_text()).strip()
            if not title or len(title) < 2:
                continue

            date_str = span.get_text(strip=True) if span else ""

            article_id = _extract_article_id(href)
            if article_id in seen:
                continue
            seen.add(article_id)

            articles.append({
                "title": title,
                "url": _resolve_url(href, base_url),
                "date_str": date_str,
                "article_id": article_id,
                "section": section_label,
            })

    return articles


# ---------------------------------------------------------------------------
# hygq listing parser (回应关切)
# ---------------------------------------------------------------------------

def _parse_hygq_listing(html_bytes, base_url):
    """Parse the /jdhy/hygq/ page for ALL articles.

    hygq uses avalon.js with wasdoc backend. Static HTML shows first 15 items.
    We parse the static HTML and also try index_{N}.html pagination.
    """
    if not html_bytes:
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    # hygq articles are inside ms-visible="showStatic" block
    static_block = soup.find(attrs={"ms-visible": "showStatic"})
    if not static_block:
        # Fallback: find all jtt-gl_list uls
        static_block = soup

    for ul in static_block.find_all("ul", class_="jtt-gl_list"):
        for li in ul.find_all("li"):
            span = li.find("span")
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag["href"].strip()
            title = (a_tag.get("title") or a_tag.get_text()).strip()
            if not title or len(title) < 2:
                continue

            date_str = span.get_text(strip=True) if span else ""

            article_id = _extract_article_id(href)
            if article_id in seen:
                continue
            seen.add(article_id)

            articles.append({
                "title": title,
                "url": _resolve_url(href, base_url),
                "date_str": date_str,
                "article_id": article_id,
                "section": "回应关切",
            })

    return articles


def _try_paginated_pages(base_path, base_label, referer, seen_ids, max_pages=_MAX_PAGES):
    """Try fetching index_{N}.html pages for TRS-style pagination.

    Only tries a few pages to detect the pattern. Stops on first 404
    or when no new articles are found (same static HTML).

    Returns list of NEW articles from additional pages.
    """
    all_articles = []
    for page_num in range(1, min(max_pages + 1, 6)):  # Try at most 5 pages
        page_url = _SITE_ROOT + base_path.rstrip("/") + f"/index_{page_num}.html"

        time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))
        html_bytes = _http_get(page_url, referer=referer)
        if not html_bytes:
            # First 404 means pattern doesn't apply — stop immediately
            if page_num == 1:
                break
            # Otherwise keep trying next page
            continue

        articles = _parse_generic_listing(html_bytes, page_url, base_label)
        if not articles:
            break

        # Check if these are actually new articles (not duplicate static HTML)
        new_count = 0
        for a in articles:
            aid = a.get("article_id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                all_articles.append(a)
                new_count += 1

        if new_count == 0:
            # Same static HTML as page 1 — pattern doesn't work
            break

    return all_articles


# ---------------------------------------------------------------------------
# zcjd listing parser (政策解读 main page + sub-pages)
# ---------------------------------------------------------------------------

def _parse_zcjd_main(html_bytes, base_url):
    """Parse the /jdhy/zcjd/ main page.

    Has 3 sub-sections (.gl_tit1 + .jtt-gl_list):
      - 政策图解 (zctj/)
      - 文字解读 (wzjd/)
      - 上级解读 (sjjd/)
    Each sub-section shows 5 items. "更多>" links to full listing sub-page.
    """
    if not html_bytes:
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for gl_div in soup.find_all("div", class_="jtt-gl_list"):
        # Find which sub-section this belongs to
        sub_section = "政策解读"
        prev_tit = gl_div.find_previous("div", class_="gl_tit1")
        if prev_tit:
            prev_a = prev_tit.find("a", href=True)
            if prev_a:
                sub_text = prev_a.get_text(strip=True)
                if sub_text and "更多" not in sub_text:
                    sub_section = f"政策解读-{sub_text}"

        for ul in gl_div.find_all("ul"):
            for li in ul.find_all("li"):
                span = li.find("span")
                a_tag = li.find("a", href=True)
                if not a_tag:
                    continue

                href = a_tag["href"].strip()
                title = (a_tag.get("title") or a_tag.get_text()).strip()
                if not title or len(title) < 2:
                    continue

                date_str = span.get_text(strip=True) if span else ""

                article_id = _extract_article_id(href)
                if article_id in seen:
                    continue
                seen.add(article_id)

                articles.append({
                    "title": title,
                    "url": _resolve_url(href, base_url),
                    "date_str": date_str,
                    "article_id": article_id,
                    "section": sub_section,
                })

    return articles


# ---------------------------------------------------------------------------
# jdhy main page parser (解读回应)
# ---------------------------------------------------------------------------

def _parse_jdhy_main(html_bytes, base_url):
    """Parse the /jdhy/ main page.

    Two content areas:
      1. 回应关切 carousel (.hygq-bt + .jdhy_list with .leftscroll <li> items)
      2. 政策解读 section (.xw-list-1 text list items)
    """
    if not html_bytes:
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    # ── 回应关切 carousel ──────────────────────────────────────────────
    carousel = soup.find("div", class_="jdhy_list")
    if carousel:
        for li in carousel.find_all("li"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag["href"].strip()
            title = a_tag.get("title", "").strip()
            if not title:
                em = a_tag.find("em")
                if em:
                    title = em.get_text(strip=True)
            if not title or len(title) < 2:
                continue

            # Date from <p> tag
            p_tag = li.find("p")
            date_str = p_tag.get_text(strip=True) if p_tag else ""

            article_id = _extract_article_id(href)
            if article_id in seen:
                continue
            seen.add(article_id)

            articles.append({
                "title": title,
                "url": _resolve_url(href, base_url),
                "date_str": date_str,
                "article_id": article_id,
                "section": "解读回应-回应关切",
            })

    # ── 政策解读 text list ─────────────────────────────────────────────
    for ul in soup.find_all("ul", class_="xw-list-1"):
        for li in ul.find_all("li"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag["href"].strip()
            title = (a_tag.get("title") or a_tag.get_text()).strip()
            if not title or len(title) < 2:
                continue

            span = li.find("span")
            date_str = span.get_text(strip=True) if span else ""

            article_id = _extract_article_id(href)
            if article_id in seen:
                continue
            seen.add(article_id)

            articles.append({
                "title": title,
                "url": _resolve_url(href, base_url),
                "date_str": date_str,
                "article_id": article_id,
                "section": "解读回应-政策解读",
            })

    return articles


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail_html(html_bytes, detail_url):
    """Parse a .htm article detail page.

    Metadata from <meta> tags: ArticleTitle, PubDate, ContentSource.
    Content from .TRS_Editor inside .smgb-article / .detailCont.
    Attachments from .myzj_xl_list links.
    """
    if not html_bytes:
        return None

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    info = {
        "title": "",
        "pub_date": "",
        "info_source": "",
        "article_id": _extract_article_id(detail_url),
        "content_html": "",
        "content_text": "",
        "attachments": [],
    }

    # Title: <h2> inside .smgb-xl-tit
    tit_div = soup.select_one(".smgb-xl-tit")
    if tit_div:
        h2 = tit_div.find("h2")
        if h2:
            info["title"] = h2.get_text(strip=True)

    # Fallback: meta ArticleTitle
    if not info["title"]:
        meta_title = soup.find("meta", attrs={"name": "ArticleTitle"})
        if meta_title and meta_title.get("content"):
            info["title"] = meta_title["content"].strip()

    # Metadata from meta tags
    meta_pubdate = soup.find("meta", attrs={"name": "PubDate"})
    if meta_pubdate and meta_pubdate.get("content"):
        info["pub_date"] = meta_pubdate["content"].strip()

    meta_source = soup.find("meta", attrs={"name": "ContentSource"})
    if meta_source and meta_source.get("content"):
        info["info_source"] = meta_source["content"].strip()

    # Also try .smgb-lips spans
    lips_div = soup.select_one(".smgb-lips")
    if lips_div:
        for sp in lips_div.find_all("span"):
            text = sp.get_text(strip=True)
            if "来源" in text:
                src = re.sub(r'^来源[：:]\s*', '', text).strip()
                if src:
                    info["info_source"] = src
            elif re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', text):
                if not info["pub_date"]:
                    info["pub_date"] = text.strip()

    # Content: .TRS_Editor inside .smgb-article / .detailCont
    content_div = soup.select_one(".detailCont") or soup.select_one(".smgb-article")
    if content_div:
        trs = content_div.find("div", class_="TRS_Editor")
        if trs:
            for tag in trs.find_all(["script", "style"]):
                tag.decompose()
            info["content_html"] = trs.decode_contents()
            info["content_text"] = trs.get_text(separator="\n", strip=True)
        else:
            for tag in content_div.find_all(["script", "style"]):
                tag.decompose()
            info["content_html"] = content_div.decode_contents()
            info["content_text"] = content_div.get_text(separator="\n", strip=True)

    # Attachments: .myzj_xl_list links
    for att_ul in soup.find_all("ul", class_=re.compile(r"myzj_xl_list")):
        for li in att_ul.find_all("li"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"].strip()
            text = (a_tag.get_text() or "").strip()
            att_url = _resolve_url(href, detail_url)

            info["attachments"].append({
                "filename": text or os.path.basename(urllib.parse.urlparse(att_url).path),
                "url": att_url,
                "type": "download",
            })

    return info


# ---------------------------------------------------------------------------
# Attachment download + processing
# ---------------------------------------------------------------------------

def _download_attachments(attachments, download_dir):
    """Download attachments to local directory. Returns list of local file paths."""
    os.makedirs(download_dir, exist_ok=True)
    local_files = []

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue
        fname = _sanitize_filename(att.get("filename", "attachment"), max_len=120)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext and not fname.lower().endswith(ext):
            fname += ext

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        data = _download_binary(url)
        if data:
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
            time.sleep(random.uniform(0.1, 0.3))

    return local_files


def _extract_zip(filepath):
    """Extract ZIP file to same directory. Returns list of extracted file paths."""
    extracted = []
    extract_dir = os.path.splitext(filepath)[0] + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(name, max_len=120)
                out_path = os.path.join(extract_dir, safe_name)
                os.makedirs(os.path.dirname(out_path) or extract_dir, exist_ok=True)
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
    """Extract plain text from PDF / DOCX / XLSX / TXT files."""
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
                    parts.append(f"### {ws.title}\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(detail, download_dir, source_url):
    """Build a Markdown document from article detail + attachments."""
    info = detail or {}
    title = info.get("title", "无标题")
    pub_date = info.get("pub_date", "")
    info_source = info.get("info_source", "")

    lines = [
        f"# {title}",
        "",
        f"**数据来源:** 福建省交通运输厅",
        f"**页面地址:** {source_url}",
        f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if pub_date:
        lines.append(f"**发布时间:** {pub_date}")
    if info_source:
        lines.append(f"**信息来源:** {info_source}")
    lines.append("")

    # Main content
    content_text = info.get("content_text", "")
    if content_text:
        lines.append("---")
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
        lines.append(content_clean)
        lines.append("")

    # Attachments
    attachments = info.get("attachments", [])
    if attachments:
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")

        for att in attachments:
            fname = att.get("filename", "unknown")
            att_url = att.get("url", "")
            lines.append(f"- [{fname}]({att_url})")
        lines.append("")

        # Embed extracted attachment text
        if download_dir and os.path.isdir(download_dir):
            lines.append("### 附件内容")
            lines.append("")
            for att in attachments:
                if att.get("type") != "download":
                    continue
                fname = att.get("filename", "")
                local_path = os.path.join(download_dir, fname)
                safe_name = _sanitize_filename(fname, max_len=120)
                alt_path = os.path.join(download_dir, safe_name)
                if not os.path.exists(local_path) and os.path.exists(alt_path):
                    local_path = alt_path
                if not os.path.exists(local_path):
                    for root, _, files in os.walk(download_dir):
                        for fn in files:
                            if fn == safe_name or fn == fname:
                                local_path = os.path.join(root, fn)
                                break
                if not os.path.exists(local_path):
                    continue

                lines.append(f"#### {fname}")
                lines.append("")
                text = _extract_text_from_file(local_path)
                if text and text.strip():
                    if len(text) > 50000:
                        text = text[:50000] + "\n\n（内容过长，已截断）"
                    lines.append(text)
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
    return {"processed_ids": [], "completed_sections": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(md_content, attachment_files, kb_id, tenant_id, folder_name):
    """Upload markdown + attachment files to knowledge base."""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"Knowledge base {kb_id} not found")

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FO(f"{folder_name}.md", md_content.encode("utf-8"))
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
        description="jtyst.fujian.gov.cn jdhy crawler — 福建省交通运输厅 解读回应 (all data)"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--target-url", default=None,
                   help="Target URL — auto-detects section")
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--section", default="all",
                   choices=["all", "jdhy", "hygq", "zcjd"],
                   help="Section to crawl (default: all)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds (default: 3300)")
    # Legacy/unused args
    for opt in ("--max-days", "--hours", "--max-articles",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


def _detect_section_from_url(url):
    """Detect which section a URL belongs to."""
    if not url:
        return None
    url_lower = url.lower()
    if "/jdhy/hygq" in url_lower:
        return "hygq"
    if "/jdhy/zcjd" in url_lower:
        return "zcjd"
    if "/jdhy" in url_lower:
        return "jdhy"
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[JTYST-JDHY] 福建省交通运输厅 — 解读回应 crawler")
    _safe_print("[JTYST-JDHY] Section: {}".format(args.section))
    _safe_print("[JTYST-JDHY] KB: {}".format(args.kb_id))
    _safe_print("[JTYST-JDHY] Task: {}".format(args.task_name))
    _safe_print("[JTYST-JDHY] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== JTYST-JDHY crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[JTYST-JDHY] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_sections": [],
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_sections = set(state.get("completed_sections", []))

    _safe_print("[JTYST-JDHY] Already processed: {} article(s)".format(len(processed_ids)))
    sys.stdout.flush()

    crawl_start = time.time()

    # ── Determine which sections to crawl ──────────────────────────────
    if args.target_url:
        detected = _detect_section_from_url(args.target_url)
        if detected:
            sections_to_crawl = [detected]
            _safe_print("[JTYST-JDHY] Section auto-detected from URL: {}".format(detected))
            sys.stdout.flush()
        else:
            sections_to_crawl = ["jdhy", "hygq", "zcjd"] if args.section == "all" else [args.section]
    else:
        sections_to_crawl = ["jdhy", "hygq", "zcjd"] if args.section == "all" else [args.section]
    sections_to_crawl = [s for s in sections_to_crawl if s not in completed_sections]

    if not sections_to_crawl:
        _safe_print("[JTYST-JDHY] All sections already completed, nothing to do.")
        sys.stdout.flush()
        return

    # ── Step 1: Crawl listing pages ────────────────────────────────────
    _safe_print("[JTYST-JDHY] Step 1/3: Crawling listing pages...")
    sys.stdout.flush()

    all_articles = []
    referer = _SITE_ROOT + "/jdhy/"

    for section_name in sections_to_crawl:
        if section_name == "hygq":
            # ── hygq: main listing + pagination ─────────────────────────
            listing_url = _SITE_ROOT + _LISTING_URLS["hygq"]
            _safe_print("[JTYST-JDHY]   Fetching hygq: {}".format(listing_url))
            sys.stdout.flush()

            html_bytes = _http_get(listing_url, referer=referer)
            if html_bytes:
                articles = _parse_hygq_listing(html_bytes, listing_url)
                _safe_print("[JTYST-JDHY]   -> {} article(s) from hygq page 1".format(len(articles)))
                sys.stdout.flush()
                # Track seen IDs for pagination dedup
                _seen = {a["article_id"] for a in articles if a.get("article_id")}
                all_articles.extend(articles)

                # Try pagination
                paginated = _try_paginated_pages(
                    _LISTING_URLS["hygq"], "回应关切", referer, _seen
                )
                if paginated:
                    _safe_print("[JTYST-JDHY]   -> {} article(s) from hygq pagination".format(len(paginated)))
                    sys.stdout.flush()
                    all_articles.extend(paginated)
            else:
                _safe_print("[JTYST-JDHY]   Failed to fetch hygq, skipping.")
                sys.stdout.flush()
            time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

        elif section_name == "zcjd":
            # ── zcjd: main page + sub-pages ─────────────────────────────
            listing_url = _SITE_ROOT + _LISTING_URLS["zcjd"]
            _safe_print("[JTYST-JDHY]   Fetching zcjd main: {}".format(listing_url))
            sys.stdout.flush()

            html_bytes = _http_get(listing_url, referer=referer)
            if html_bytes:
                articles = _parse_zcjd_main(html_bytes, listing_url)
                _safe_print("[JTYST-JDHY]   -> {} article(s) from zcjd main".format(len(articles)))
                sys.stdout.flush()
                all_articles.extend(articles)
            else:
                _safe_print("[JTYST-JDHY]   Failed to fetch zcjd main, skipping.")
                sys.stdout.flush()
            time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

            # Crawl each sub-page
            for sub_key, sub_path in _ZCJD_SUB_PAGES.items():
                sub_url = _SITE_ROOT + sub_path
                sub_label = {
                    "zctj": "政策解读-政策图解",
                    "wzjd": "政策解读-文字解读",
                    "sjjd": "政策解读-上级解读",
                }.get(sub_key, f"政策解读-{sub_key}")

                _safe_print("[JTYST-JDHY]   Fetching zcjd sub-page {}: {}".format(sub_key, sub_url))
                sys.stdout.flush()

                sub_html = _http_get(sub_url, referer=listing_url)
                if sub_html:
                    sub_articles = _parse_generic_listing(sub_html, sub_url, sub_label)
                    _safe_print("[JTYST-JDHY]   -> {} article(s) from {}".format(len(sub_articles), sub_key))
                    sys.stdout.flush()
                    _sub_seen = {a["article_id"] for a in sub_articles if a.get("article_id")}
                    all_articles.extend(sub_articles)

                    # Try pagination for sub-page
                    sub_paginated = _try_paginated_pages(sub_path, sub_label, sub_url, _sub_seen)
                    if sub_paginated:
                        _safe_print("[JTYST-JDHY]   -> {} article(s) from {} pagination".format(
                            len(sub_paginated), sub_key))
                        sys.stdout.flush()
                        all_articles.extend(sub_paginated)
                else:
                    _safe_print("[JTYST-JDHY]   Failed to fetch {}, skipping.".format(sub_key))
                    sys.stdout.flush()
                time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

        elif section_name == "jdhy":
            # ── jdhy main page ──────────────────────────────────────────
            listing_url = _SITE_ROOT + _LISTING_URLS["jdhy"]
            _safe_print("[JTYST-JDHY]   Fetching jdhy main: {}".format(listing_url))
            sys.stdout.flush()

            html_bytes = _http_get(listing_url, referer=_SITE_ROOT + "/")
            if html_bytes:
                articles = _parse_jdhy_main(html_bytes, listing_url)
                _safe_print("[JTYST-JDHY]   -> {} article(s) from jdhy main".format(len(articles)))
                sys.stdout.flush()
                all_articles.extend(articles)
            else:
                _safe_print("[JTYST-JDHY]   Failed to fetch jdhy main, skipping.")
                sys.stdout.flush()
            time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

    # ── Deduplicate across sections ────────────────────────────────────
    seen_ids = set()
    unique_articles = []
    for a in all_articles:
        aid = a.get("article_id")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            unique_articles.append(a)

    _safe_print("[JTYST-JDHY] Total unique articles across all sections: {}".format(len(unique_articles)))
    sys.stdout.flush()

    if not unique_articles:
        _safe_print("[JTYST-JDHY] No articles found, marking sections complete.")
        sys.stdout.flush()
        for s in sections_to_crawl:
            completed_sections.add(s)
        state["completed_sections"] = list(completed_sections)
        _save_state(output_dir, state)
        return

    # Filter already-processed
    new_articles = [
        a for a in unique_articles
        if a.get("article_id") and a["article_id"] not in processed_ids
    ]
    skipped = len(unique_articles) - len(new_articles)
    if skipped:
        _safe_print("[JTYST-JDHY] {} already processed, {} new".format(skipped, len(new_articles)))
        sys.stdout.flush()

    if not new_articles:
        _safe_print("[JTYST-JDHY] All articles already processed.")
        sys.stdout.flush()
        for s in sections_to_crawl:
            completed_sections.add(s)
        state["completed_sections"] = list(completed_sections)
        _save_state(output_dir, state)
        return

    # ── Step 2: Process each article ───────────────────────────────────
    _safe_print("\n[JTYST-JDHY] Step 2/3: Processing {} article(s)...\n".format(len(new_articles)))
    sys.stdout.flush()

    processed_count = 0
    stopped_early = False
    downloads_dir = os.path.join(output_dir, "downloads")

    for idx, article in enumerate(new_articles, 1):
        # ── Time-bounded check ─────────────────────────────────────────
        elapsed = time.time() - crawl_start
        remaining = args.max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                "\n[JTYST-JDHY] Runtime {:.0f}s, {:.0f}s remaining (limit {}s), "
                "stopping gracefully. {} processed. "
                "Next run will resume.".format(elapsed, remaining, args.max_runtime, processed_count))
            sys.stdout.flush()
            stopped_early = True
            break

        article_id = article["article_id"]
        title = article["title"]
        detail_url = article["url"]
        date_str = article.get("date_str", "")
        section_label = article.get("section", "")

        _safe_print("[JTYST-JDHY] [{}/{}] [{}] {}...".format(
            idx, len(new_articles), section_label, title[:50]))
        sys.stdout.flush()

        # Fetch detail page
        html_bytes = _http_get(detail_url, referer=referer)
        if not html_bytes:
            logging.warning("Failed to fetch detail: %s", detail_url)
            processed_ids.add(article_id)
            continue

        detail = _parse_detail_html(html_bytes, detail_url)
        if not detail:
            logging.warning("Failed to parse detail: %s", detail_url)
            processed_ids.add(article_id)
            continue

        # Prefer detail page title if longer
        if detail.get("title") and len(detail["title"]) > len(title):
            title = detail["title"]

        # Download attachments
        attachments = detail.get("attachments", [])
        local_files = []
        article_dl_dir = ""
        if attachments:
            dl_name = "{}_{}".format(article_id[:12], _sanitize_filename(title[:30], 40))
            article_dl_dir = os.path.join(downloads_dir, dl_name)
            local_files = _download_attachments(attachments, article_dl_dir)
            # Extract ZIPs
            for fp in list(local_files):
                is_zip = fp.lower().endswith(".zip")
                if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                    with open(fp, "rb") as f:
                        is_zip = f.read(4) == b"PK\x03\x04"
                if is_zip:
                    extracted = _extract_zip(fp)
                    local_files.remove(fp)
                    local_files.extend(extracted)

        # Build markdown
        md_content = _build_markdown(detail, article_dl_dir, detail_url)

        # Save markdown locally
        date_for_name = date_str or datetime.now().strftime("%Y-%m-%d")
        folder_name = _sanitize_filename(
            "{}_{}_{}".format(date_for_name, article_id[:12], title[:40]), max_len=120)
        md_path = os.path.join(output_dir, f"{folder_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        _safe_print("[JTYST-JDHY]   Saved ({} chars, {} attachments)".format(
            len(md_content), len(local_files)))
        sys.stdout.flush()

        # Upload to KB
        if args.kb_id:
            try:
                _upload_to_kb(md_content, local_files, args.kb_id,
                             args.tenant_id, folder_name)
                _safe_print("[JTYST-JDHY]   Uploaded to KB {}".format(args.kb_id))
                sys.stdout.flush()
            except Exception as e:
                logging.error("KB upload failed: %s", e)
                _safe_print("[JTYST-JDHY]   Upload error: {}".format(e))
                sys.stdout.flush()

        processed_ids.add(article_id)
        processed_count += 1

        # Checkpoint every batch
        if processed_count % _BATCH_SIZE == 0:
            _save_state(output_dir, {
                "processed_ids": list(processed_ids),
                "completed_sections": list(completed_sections),
            })
            _safe_print("[JTYST-JDHY]   Checkpoint ({} processed)".format(processed_count))
            sys.stdout.flush()

        # Anti-crawling delay
        time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

    # ── Final state ────────────────────────────────────────────────────
    if not stopped_early:
        for s in sections_to_crawl:
            completed_sections.add(s)
    state["processed_ids"] = list(processed_ids)
    state["completed_sections"] = list(completed_sections)
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[JTYST-JDHY] Crawl complete — {} new article(s)".format(processed_count))
    if stopped_early:
        _safe_print("[JTYST-JDHY] Stopped early, will resume next run")
    _safe_print("[JTYST-JDHY] Sections: {}".format(", ".join(sorted(completed_sections))))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== JTYST-JDHY crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "jtyst_jdhy_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
