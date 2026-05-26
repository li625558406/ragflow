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
Dedicated web crawler for www.ccgp.gov.cn/zjlwbcbz/ (中国政府采购网).

  各地区政府采购评审专家劳务报酬标准

Site characteristics
────────────────────
Server-rendered HTML (not an SPA), GBK encoding.  No API signing, no
JavaScript requirement — plain ``requests`` + ``BeautifulSoup`` are sufficient.

Two sections on the page
────────────────────────
  1. **政策文件** (Policy Documents)
     Three links to national-level MOF policy notices under ``/zcfg/mof/…``.
     These are the overarching regulations on expert management and fee standards.

  2. **标准一览** (Standards Table)
     A 32-row table listing each province/city's expert labour remuneration
     standard, with columns: 地区 (region), 标准 (standard description),
     文件依据 (document basis → detail page link).

Detail pages
────────────
Plain HTML with title, date, source, and body content.  Some include
file attachments (PDF/DOC).

Usage (typically spawned by task_executor):
    python ccgp_zjlwbcbz_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://www.ccgp.gov.cn/zjlwbcbz/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

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

_SITE_URL = "http://www.ccgp.gov.cn"
_TARGET_URL = "http://www.ccgp.gov.cn/zjlwbcbz/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

# ── Anti-crawling: random delays between requests ───────────────────────
_REQUEST_DELAY_MIN = 1.0   # minimum seconds between HTTP requests
_REQUEST_DELAY_MAX = 2.5   # maximum seconds between HTTP requests

# Columns in the 标准一览 table
_STANDARD_TABLE_COLUMNS = ("region", "standard", "document")

# ── 政策文件 ── These are national-level policy docs linked from the page.
# We also match them dynamically from the page; these paths are used for
# identifying policy links.
_POLICY_LINK_PATTERN = re.compile(r"^/(zcfg/mof|zjlwbcbz)/.*\.(?:htm|shtml)$")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="www.ccgp.gov.cn/zjlwbcbz/ 各地区政府采购评审专家劳务报酬标准 crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Page URL (http://www.ccgp.gov.cn/zjlwbcbz/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--section", default=None,
                        help="Comma-separated sections: policy,standards (default: both)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
    if not text:
        return None
    text = text.strip()
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
        "%Y年%m月%d日", "%Y年%m月%d日 %H:%M",
        "%Y年%m月%d日  %H:%M", "%Y年%m月%d日  %H:%M:%S",
        "%Y年%m月%d日%H:%M", "%Y年%m月%d日%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _init_session():
    """Create a requests Session with browser-like headers and cookies.

    The session automatically handles cookies (JSESSIONID etc.) and
    connection reuse, which is both more efficient and less suspicious
    than creating a new connection for each request.
    """
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    # Warm up the session with a visit to the main site
    try:
        sess.get("http://www.ccgp.gov.cn/", timeout=30)
    except Exception as e:
        logging.warning("Session warm-up request failed: %s", e)
    return sess


def _fetch(url, sess, encoding=None):
    """Fetch a URL with retries and random delays, return BeautifulSoup or None.

    Uses a shared requests Session for cookie persistence and connection reuse.
    Auto-detects encoding from HTTP headers → HTML meta charset → apparent_encoding
    → GBK fallback, avoiding garbled Chinese text.
    """
    # Set Referer based on the URL to look like normal site navigation
    headers = dict(_HEADERS)
    parsed = urlparse(url)
    headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    if "zjlwbcbz" in url:
        headers["Referer"] = "http://www.ccgp.gov.cn/zjlwbcbz/"
    elif "zcfg" in url:
        headers["Referer"] = "http://www.ccgp.gov.cn/zcfg/"

    for attempt in range(3):
        try:
            resp = sess.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                # ── Encoding detection (avoids garbled Chinese) ──────────
                if encoding:
                    resp.encoding = encoding
                else:
                    _detect_encoding(resp)
                return BeautifulSoup(resp.text, "lxml")
            if resp.status_code in (403, 429):
                # Rate-limited or blocked — back off longer
                wait = 10 + attempt * 10
                logging.warning("HTTP %d for %s, backing off %ds (attempt %d)",
                              resp.status_code, url, wait, attempt + 1)
                time.sleep(wait)
                continue
            logging.warning("HTTP %d for %s (attempt %d)", resp.status_code, url, attempt + 1)
        except Exception as e:
            logging.warning("Fetch error for %s (attempt %d): %s", url, attempt + 1, e)
        time.sleep(2 + attempt * 2)
    return None


def _detect_encoding(resp):
    """Set resp.encoding from HTML meta charset or chardet, with GBK fallback.

    Checks the HTML ``Content-Type`` meta tag in raw bytes first (avoiding
    the HTTP header which may be missing or misleading).  Falls back to
    chardet/apparent_encoding, then GBK if everything else fails.
    """
    # ── Step 1: try HTML <meta> charset in raw bytes (first 8 KB) ──────
    # The original regex might not match content="text/html; charset=..." format,
    # so we also search for charset= in any part of the meta tag.
    raw = resp.content[:8192]
    match = re.search(
        rb'<meta[^>]+charset[=]["\']?([^"\';\s>]+)',
        raw, re.IGNORECASE,
    )
    if not match:
        # Broader search: look for charset= anywhere in the header area
        match = re.search(
            rb'charset[=]["\']?([a-zA-Z0-9_-]+)',
            raw, re.IGNORECASE,
        )
    if match:
        detected = match.group(1).decode("ascii", errors="replace").lower()
        if detected in ("gb2312", "gb18030"):
            resp.encoding = "gbk"
        elif detected:
            resp.encoding = detected
        return
    # ── Step 2: fall back to chardet / apparent_encoding ───────────────
    if hasattr(resp, "apparent_encoding") and resp.apparent_encoding:
        resp.encoding = resp.apparent_encoding
    else:
        resp.encoding = "gbk"


def _request_delay():
    """Sleep for a random interval to avoid triggering rate limits."""
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _extract_article_id(url):
    """Extract a stable article ID from the file name in the URL.

    Examples:
        /zcfg/mof/201702/t20170208_7908707.htm  →  7908707
        201704/t20170406_8083831.htm             →  8083831
    """
    m = re.search(r"_(\d+)\.(?:htm|shtml)", url)
    return m.group(1) if m else None


def _normalise_url(href):
    """Build an absolute URL from a possibly-relative href on the main page."""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return urljoin(_SITE_URL, href)
    # Relative to the zjlwbcbz/ directory
    return urljoin(_TARGET_URL, href)


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _clean_body_md(text, article_title=""):
    """Remove CCGP site artifacts from the generated Markdown."""
    # Remove breadcrumb navigation
    text = re.sub(r"当前位置[：:]\s*[^\n]*?[»＞]\s*\n?", "", text)
    # Remove CSS class name artifacts
    text = re.sub(r"\bvF_deail_maincontent\b", "", text)
    text = re.sub(r"\bvF_detail_main\b", "", text)
    text = re.sub(r"\brh_container\b", "", text)
    # Remove template markers
    text = re.sub(r"\{.*?文件头部.*?\}", "", text)
    # Remove standalone "rh" noise (CSS class artifacts)
    text = re.sub(r"^\s*rh\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*rh\s+rh\s*$", "", text, flags=re.MULTILINE)
    # Remove inline CSS/HTML fragments
    text = re.sub(r"\btable width='\d+%' border='\d+'\b", "", text)
    text = re.sub(r"\bcellspacing='\d+'\b", "", text)
    text = re.sub(r"\bcellpadding='\d+'\b", "", text)
    # Remove duplicated date line at the start (e.g. "2016年11月28日  10:12来源：")
    text = re.sub(r"^\d{4}\D{1,3}\d{1,2}\D{1,3}\d{1,2}\s+\d{1,2}:\d{2}[^\n]*?\n", "", text, count=1)
    # Collapse consecutive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _html_to_markdown(soup, base_url):
    """Convert the detail page HTML body to plain Markdown."""
    lines = []

    # Find the main content — prefer .rh_content (actual article body)
    content_area = None
    # First try the article body wrapper
    content_area = soup.select_one(".rh_content")
    if not content_area:
        # Fall back to broader wrappers
        for cls in (".TRS_Editor", ".vF_deail_maincontent", ".vF_detail_main",
                     ".con_text", ".con", "#con", "article", ".content", ".main"):
            content_area = soup.select_one(cls)
            if content_area:
                break

    if not content_area:
        content_area = soup.find("body")

    if content_area:
        for tag in content_area.find_all(["script", "style", "noscript"]):
            tag.decompose()
        # Remove HTML comments (site template markers)
        from bs4 import Comment
        for comment in content_area.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        for el in content_area.find_all([
            "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "li", "blockquote", "pre", "img", "div",
            "span", "section", "table", "tr",
        ]):
            tn = el.name

            # Images
            if tn == "img":
                src = el.get("src", "")
                alt = el.get("alt", "")
                if src:
                    src = urljoin(base_url, src) if not src.startswith("http") else src
                    alt_text = f" ({alt})" if alt else ""
                    lines.append(f"![{alt_text}]({src})")
                continue

            # Skip inline elements nested inside block elements
            if tn in ("span",) and el.find_parent(["p", "h1", "h2", "h3", "h4", "li"]):
                continue

            # Get text
            texts = []
            for child in el.children:
                if child.name is None:
                    t = (child.string or "").strip()
                    if t:
                        texts.append(t)
            if not texts:
                text = el.get_text(strip=True)
            else:
                text = " ".join(texts)
            if not text:
                continue

            if tn == "h1":
                lines.append(f"\n# {text}\n")
            elif tn == "h2":
                lines.append(f"\n## {text}\n")
            elif tn in ("h3", "h4", "h5", "h6"):
                lines.append(f"\n**{text}**\n")
            elif tn == "blockquote":
                lines.append(f"> {text}")
            elif tn == "li":
                lines.append(f"- {text}")
            elif tn == "pre":
                lines.append(f"```\n{text}\n```")
            elif tn == "p":
                lines.append(text)
            elif tn == "div":
                if not el.find_parent(["td", "th"]):
                    lines.append(text)
            elif tn == "section":
                lines.append(text)
            elif tn in ("td", "th"):
                lines.append(f"| {text} |")
            elif tn == "table":
                lines.append("")

    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Page scraping
# ---------------------------------------------------------------------------

def _scrape_main_page(target_url, sess):
    """Scrape the main zjlwbcbz page.

    Returns (policy_articles, standard_articles), where each is a list of dicts:
        {title, url, section, section_label, article_id, standard_desc, region}
    """
    soup = _fetch(target_url, sess)
    if not soup:
        return [], []

    policy_articles = []
    standard_articles = []

    # ── 1. 政策文件 ──
    # Policy links point to /zcfg/mof/...
    seen_policy = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/zcfg/mof/" not in href:
            continue
        if ".htm" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        norm_url = _normalise_url(href)
        art_id = _extract_article_id(norm_url)
        if art_id in seen_policy:
            continue
        seen_policy.add(art_id)
        policy_articles.append({
            "title": title,
            "url": norm_url,
            "section": "policy",
            "section_label": "政策文件",
            "article_id": art_id,
        })

    # ── 2. 标准一览 table ──
    seen_standard = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 10:   # The big table has 30+ rows
            continue

        header_cells = rows[0].find_all(["td", "th"])
        if len(header_cells) < 3:
            continue

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            region = cells[0].get_text(strip=True)
            standard_desc = cells[1].get_text(strip=True)
            doc_cell = cells[2]

            # Extract link from the 文件依据 column
            link = doc_cell.find("a", href=True)
            if not link:
                # Some rows have no link (standards not yet published)
                continue

            href = link["href"].strip()
            doc_title = link.get_text(strip=True)
            if not doc_title:
                doc_title = doc_cell.get_text(strip=True)

            norm_url = _normalise_url(href)
            art_id = _extract_article_id(norm_url)
            if not art_id or art_id in seen_standard:
                continue
            seen_standard.add(art_id)

            standard_articles.append({
                "title": doc_title,
                "url": norm_url,
                "section": "standards",
                "section_label": "标准一览",
                "article_id": art_id,
                "region": region,
                "standard_desc": standard_desc,
            })

    return policy_articles, standard_articles


def _scrape_detail_page(article, sess):
    """Scrape a detail page; add extracted fields to the article dict.

    Mutates article in place, adding: page_date, body_md, source, attachments.
    """
    soup = _fetch(article["url"], sess)
    if not soup:
        return

    # ── Title (from meta or structured h2) ────────────────────────────────
    title_el = soup.find("h2", id="redt")
    if title_el:
        t = title_el.get_text(strip=True)
        if t:
            article["title"] = t

    if not article.get("title"):
        for meta_name in ("ColumnName", "ArticleTitle", "title"):
            meta = soup.find("meta", attrs={"name": meta_name})
            if meta and meta.get("content", "").strip():
                article["title"] = meta["content"].strip()
                break

    if not article.get("title"):
        title_tag = soup.find("title")
        if title_tag:
            t = title_tag.get_text(strip=True)
            t = re.sub(r"_中国政府采购网.*", "", t).strip()
            if t:
                article["title"] = t

    # ── Date (from structured span) ──────────────────────────────────────
    date_str = ""
    pub_time = soup.find("span", id="pubTime")
    if pub_time:
        date_str = pub_time.get_text(strip=True)

    if not date_str:
        for selector in (
            ".con_time", ".time", ".date", ".pub_date", ".info_time",
            "span.time", "div.time",
        ):
            el = soup.select_one(selector)
            if el:
                date_str = el.get_text(strip=True)
                break

    if not date_str:
        text = soup.get_text()
        m = re.search(
            r"(\d{4}\D{1,3}\d{1,2}\D{1,3}\d{1,2}\D{0,3}\s*\d{1,2}:\d{2}(?::\d{2})?)",
            text,
        )
        if not m:
            m = re.search(r"(\d{4}\D{1,3}\d{1,2}\D{1,3}\d{1,2})", text)
        if m:
            date_str = m.group(1).strip()

    article["page_date"] = date_str
    article["date"] = _parse_date(date_str)

    # ── Source ───────────────────────────────────────────────────────────
    source = ""
    for selector in (".con_source", ".source", ".pub_source"):
        el = soup.select_one(selector)
        if el:
            source = el.get_text(strip=True)
            source = re.sub(r"^(来源|来源：|来源:)\s*", "", source)
            break
    article["source"] = source

    # ── Body content → Markdown ─────────────────────────────────────────
    body_md = _html_to_markdown(soup, article["url"])

    # Clean up common CCGP site artifacts
    body_md = _clean_body_md(body_md, article.get("title", ""))

    article["body_md"] = body_md.strip()

    # ── Attachments ─────────────────────────────────────────────────────
    attachments = []
    for a in soup.select(
        ".vF_deail_maincontent a[href], .vF_detail_main a[href], "
        ".con_text a[href], .con a[href], .content a[href]"
    ):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if not href or not text:
            continue
        ext_match = re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)$", href.lower())
        if ext_match or "附件" in text:
            file_url = urljoin(article["url"], href)
            attachments.append({
                "name": text,
                "url": file_url,
                "ext": ext_match.group(1) if ext_match else "",
            })
    article["attachments"] = attachments


# ---------------------------------------------------------------------------
# State management
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


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_article_to_kb(kb, tenant_id, kb_parent, article, markdown_text,
                          output_dir, sess=None):
    """Upload a single article (MD + attachments) to KB as a per-article folder.

    Folder and file names use only ASCII characters (article_id + ISO date)
    to avoid MinIO S3 XMinioInvalidObjectName errors with Chinese characters.
    """
    from api.db.services.file_service import FileService
    from api.db import FileType

    # Build ASCII-safe folder name — MinIO S3 rejects Chinese/Unicode in object names
    article_id = article.get("article_id", "")
    iso_date = ""
    dt = article.get("date")
    if dt:
        iso_date = dt.strftime("%Y-%m-%d")
    elif article.get("page_date"):
        parsed = _parse_date(article["page_date"])
        if parsed:
            iso_date = parsed.strftime("%Y-%m-%d")

    if iso_date and article_id:
        folder_name = f"{iso_date}_{article_id}"
    elif article_id:
        folder_name = article_id
    else:
        folder_name = get_uuid()
    folder_name = folder_name[:120]

    article_folder = FileService.new_a_file_from_kb(
        tenant_id, folder_name, kb_parent["id"], ty=FileType.FOLDER.value,
    )

    staging = os.path.join(output_dir, "staging", folder_name)
    os.makedirs(staging, exist_ok=True)

    md_path = os.path.join(staging, f"{folder_name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    _upload_file_to_kb_folder(md_path, kb, tenant_id, article_folder["id"],
                              parser_id="general")

    # Download and upload attachments — rename to ASCII-safe names for MinIO
    for idx, att in enumerate(article.get("attachments", [])):
        att_path = _download_attachment(att, staging, sess)
        if att_path:
            ext = os.path.splitext(att_path)[1].lower()
            safe_path = os.path.join(staging, f"att_{idx:02d}{ext}")
            if att_path != safe_path:
                if os.path.exists(safe_path):
                    os.remove(safe_path)
                os.rename(att_path, safe_path)
                att_path = safe_path
            pid = "laws" if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"} else "general"
            try:
                _upload_file_to_kb_folder(att_path, kb, tenant_id,
                                          article_folder["id"], parser_id=pid)
            except Exception as e:
                logging.warning("Attachment upload error %s: %s", att.get("name"), e)

    return article_folder["id"]


def _upload_file_to_kb_folder(filepath, kb, tenant_id, parent_folder_id, parser_id="laws"):
    """Upload a local file to a KB folder and queue parsing."""
    from api.db.services.document_service import DocumentService
    from api.db.services.file_service import FileService
    from api.utils.file_utils import filename_type
    import xxhash
    from pathlib import Path

    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        blob = f.read()
    doc_id = get_uuid()
    ftype = filename_type(filename)
    location = f"{parent_folder_id}/{doc_id}_{filename}"
    while settings.STORAGE_IMPL.obj_exist(kb.id, location):
        location += "_"
    settings.STORAGE_IMPL.put(kb.id, location, blob)

    doc = {
        "id": doc_id,
        "kb_id": kb.id,
        "parser_id": parser_id,
        "parser_config": kb.parser_config,
        "created_by": tenant_id,
        "type": ftype,
        "name": filename,
        "source_type": "local",
        "suffix": Path(filename).suffix.lstrip(".").lower(),
        "location": location,
        "size": len(blob),
        "content_hash": xxhash.xxh128(blob).hexdigest(),
    }
    DocumentService.insert(doc)
    FileService.add_file_from_kb(doc, parent_folder_id, tenant_id)

    if not os.environ.get("SKIP_PARSE", "").strip():
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)

    return doc_id


def _download_attachment(att, dest_dir, sess=None):
    """Download a single attachment to dest_dir, return local path or None."""
    os.makedirs(dest_dir, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]', "_", att.get("name", "attachment"))
    dest = os.path.join(dest_dir, name)
    if os.path.exists(dest):
        return dest
    try:
        headers = dict(_HEADERS)
        headers["Referer"] = "http://www.ccgp.gov.cn/zjlwbcbz/"
        fetcher = sess.get if sess else requests.get
        resp = fetcher(att["url"], headers=headers, timeout=60)
        if resp.status_code == 200 and len(resp.content) >= 100:
            with open(dest, "wb") as f:
                f.write(resp.content)
            logging.info("Downloaded attachment: %s (%d bytes)", name, len(resp.content))
            return dest
    except Exception as e:
        logging.warning("Download error for %s: %s", att["url"], e)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[ZJLWBCBZ] 中国政府采购网 - 各地区政府采购评审专家劳务报酬标准 crawler")
    _safe_print(f"[ZJLWBCBZ] Target URL: {args.target_url}")
    _safe_print(f"[ZJLWBCBZ] Task name: {args.task_name}")
    _safe_print(f"[ZJLWBCBZ] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZJLWBCBZ crawler started for %s ===", args.target_url)

    # ── Determine sections to crawl ──────────────────────────────────────
    if args.section:
        crawl_policy = "policy" in args.section
        crawl_standards = "standards" in args.section
    else:
        crawl_policy = True
        crawl_standards = True

    # ── Output dir & state ───────────────────────────────────────────────
    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip(),
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZJLWBCBZ] Output directory: {output_dir}\n")
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[ZJLWBCBZ] Already processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # ── KB setup ─────────────────────────────────────────────────────────
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService

    ok, kb = KnowledgebaseService.get_by_id(args.kb_id)
    if not ok:
        _safe_print(f"[ZJLWBCBZ] ERROR: Knowledge base {args.kb_id} not found.")
        sys.stdout.flush()
        sys.exit(1)
    _safe_print(f"[ZJLWBCBZ] KB: {kb.name}")
    sys.stdout.flush()

    kb_root_folder = FileService.get_kb_folder(args.tenant_id)
    kb_parent = FileService.new_a_file_from_kb(
        args.tenant_id, kb.name, kb_root_folder["id"],
    )

    # ── Init session ──────────────────────────────────────────────────────
    _safe_print("[ZJLWBCBZ] Initializing HTTP session (anti-crawling)...")
    sys.stdout.flush()
    sess = _init_session()
    _safe_print("[ZJLWBCBZ] Session ready.\n")
    sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════════════
    # Step 1: Scrape main page → discover articles
    # ═══════════════════════════════════════════════════════════════════════
    _safe_print("[ZJLWBCBZ] Step 1/3: Scraping main page for article links...\n")
    sys.stdout.flush()

    policy_articles, standard_articles = _scrape_main_page(args.target_url, sess)

    all_articles = []
    if crawl_policy:
        all_articles.extend(policy_articles)
        _safe_print(f"[ZJLWBCBZ]   政策文件: {len(policy_articles)} article(s)")
    if crawl_standards:
        all_articles.extend(standard_articles)
        _safe_print(f"[ZJLWBCBZ]   标准一览: {len(standard_articles)} article(s)")

    _safe_print(f"[ZJLWBCBZ]   Total: {len(all_articles)} article(s)\n")
    sys.stdout.flush()

    if not all_articles:
        _safe_print("[ZJLWBCBZ] No articles found, exiting.")
        sys.stdout.flush()
        return

    # ── Filter already-processed ─────────────────────────────────────────
    if processed_ids:
        new_articles = [a for a in all_articles if a["article_id"] not in processed_ids]
        skipped = len(all_articles) - len(new_articles)
        if skipped:
            _safe_print(f"[ZJLWBCBZ] Skipping {skipped} already-processed article(s)\n")
            sys.stdout.flush()
        all_articles = new_articles

    if not all_articles:
        _safe_print("[ZJLWBCBZ] All articles already processed, nothing to do.")
        sys.stdout.flush()
        return

    _safe_print(f"[ZJLWBCBZ] New articles to process: {len(all_articles)}\n")
    sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════════════
    # Step 2: Fetch detail pages & format Markdown
    # ═══════════════════════════════════════════════════════════════════════
    _safe_print(f"[ZJLWBCBZ] Step 2/3: Fetching {len(all_articles)} detail pages...\n")
    sys.stdout.flush()

    new_ids = []
    success_count = 0
    fail_count = 0
    total = len(all_articles)

    for idx, art in enumerate(all_articles, 1):
        title_preview = art["title"][:70]
        _safe_print(f"[ZJLWBCBZ] [{idx}/{total}] [{art['section_label']}] {title_preview}")
        sys.stdout.flush()
        logging.info("[%d/%d] %s - %s", idx, total, art["section_label"], art["title"])

        try:
            _scrape_detail_page(art, sess)

            body = art.get("body_md", "")
            if not body:
                _safe_print(f"[ZJLWBCBZ]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[ZJLWBCBZ]   -> {len(body)} chars MD, {len(art.get('attachments', []))} attachment(s)")
            sys.stdout.flush()

            # ── Build Markdown ────────────────────────────────────────
            date_str = art.get("page_date", "")[:10]
            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section_label']}",
            ]
            if art.get("region"):
                lines.append(f"**Region:** {art['region']}")
            if date_str:
                lines.append(f"**Date:** {date_str}")
            if art.get("source"):
                lines.append(f"**Source:** {art['source']}")
            lines.append(f"**URL:** {art['url']}")

            # Include the standard description for 标准一览 entries
            if art.get("standard_desc"):
                lines.append("")
                lines.append("## 标准摘要")
                lines.append("")
                lines.append(art["standard_desc"])

            lines.append("")
            lines.append(body)
            lines.append("")
            lines.append("---")

            markdown_full = "\n".join(lines)

            # ── Upload to KB ─────────────────────────────────────────
            try:
                _upload_article_to_kb(
                    kb, args.tenant_id, kb_parent,
                    art, markdown_full, output_dir, sess,
                )
                success_count += 1
                new_ids.append(art["article_id"])
            except Exception as e:
                _safe_print(f"[ZJLWBCBZ]   -> KB upload ERROR: {e}")
                sys.stdout.flush()
                logging.error("KB upload failed for %s: %s", art["article_id"], e)
                fail_count += 1

        except Exception as e:
            _safe_print(f"[ZJLWBCBZ]   -> ERROR: {e}")
            sys.stdout.flush()
            logging.error("Failed to process article %s: %s", art.get("url"), e)
            fail_count += 1

        # Random delay to avoid rate limiting
        _request_delay()

    _safe_print(f"\n[ZJLWBCBZ] Detail pages: {success_count} success, {fail_count} failed\n")
    sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════════════
    # Step 3: Save state
    # ═══════════════════════════════════════════════════════════════════════
    if new_ids:
        processed_ids.update(new_ids)
        _save_state(output_dir, {"processed_ids": list(processed_ids)})

    _safe_print(f"\n[ZJLWBCBZ] {'='*50}")
    _safe_print(f"[ZJLWBCBZ] Done. Processed {len(new_ids)} new article(s).")
    _safe_print(f"[ZJLWBCBZ] {'='*50}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    CONSUMER_NAME = "ccgp_zjlwbcbz_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
