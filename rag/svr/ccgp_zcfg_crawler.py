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
Dedicated web crawler for www.ccgp.gov.cn/zcfg/* (中国政府采购网-政策法规).

Covers 7 sub-sections of the 政策法规 (laws & regulations) area:

    /zcfg/mofgz/   财政部规章
    /zcfg/mof/     财政部文件
    /zcfg/gwywj/   国务院文件
    /zcfg/bwfile/  部门文件
    /zcfg/dffg/    地方法规 (two-level: province directory → province articles)
    /zcfg/gjfg/    国家法规
    /zcfg/guojifg/ 国际法规

Site characteristics
────────────────────
Listing pages use ``<ul class="c_list_zcfg">`` (or ``c_list_tat`` for guojifg),
with ``<li><a href="..."><span>date</span></a></li>`` per article.  Pagination
is driven by a JavaScript ``Pager`` widget:

    Pager({size:N, current:M, prefix:'index', suffix:'htm'});

Page URLs:  index.htm (page 1), index_1.htm (page 2), index_2.htm (page 3), …

Detail pages use the standard ccgp.gov.cn layout: ``<div class="TRS_Editor">``
for content plus ``<meta name="ArticleTitle">`` / ``PubDate`` / ``ContentSource``.

The dffg (地方法规) section is a two-level directory: the top-level page lists
provinces; each province sub-directory has its own article listing.

Anti-detection measures
───────────────────────
- Playwright headless Chromium for all detail-page requests (real browser
  fingerprint, TLS, JS environment).
- Fixed ``_REQUEST_DELAY`` between requests to avoid rate limiting.
- Full ``User-Agent`` header emulating Chrome 120 on Windows.

Usage (typically spawned by task_executor):
    python ccgp_zcfg_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url http://www.ccgp.gov.cn/zcfg/ \\
        --kb-id <KB_ID> \\
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
from urllib.parse import urljoin, urlparse

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
# Section definitions
# ---------------------------------------------------------------------------
# Each entry: (relative_path, display_label, ul_class, is_two_level)
# - relative_path:  path under /zcfg/ (e.g. "mofgz")
# - display_label:  human-readable Chinese name
# - ul_class:       CSS class of the <ul> that holds article <li> items
# - is_two_level:   True if the top-level page is a directory of sub-sections
SECTIONS = {
    "mofgz":   ("/zcfg/mofgz/",   "\u8d22\u653f\u90e8\u89c4\u7ae0",   "c_list_zcfg", False),
    "mof":     ("/zcfg/mof/",     "\u8d22\u653f\u90e8\u6587\u4ef6",   "c_list_zcfg", False),
    "gwywj":   ("/zcfg/gwywj/",   "\u56fd\u52a1\u9662\u6587\u4ef6",   "c_list_zcfg", False),
    "bwfile":  ("/zcfg/bwfile/",  "\u90e8\u95e8\u6587\u4ef6",         "c_list_zcfg", False),
    "dffg":    ("/zcfg/dffg/",    "\u5730\u65b9\u6cd5\u89c4",         "c_list_zcfg", True),
    "gjfg":    ("/zcfg/gjfg/",    "\u56fd\u5bb6\u6cd5\u89c4",         "c_list_zcfg", False),
    "guojifg": ("/zcfg/guojifg/", "\u56fd\u9645\u6cd5\u89c4",         "c_list_tat",  False),
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="CCGP \u653f\u7b56\u6cd5\u89c4 (zcfg) crawler for scheduled tasks"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url",
                        default="http://www.ccgp.gov.cn/zcfg/",
                        help="Base URL (default: http://www.ccgp.gov.cn/zcfg/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated section keys: mofgz,mof,gwywj,bwfile,dffg,gjfg,guojifg")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Max listing pages per (sub-)section (0=auto-detect from Pager)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to process overall (0=unlimited)")
    parser.add_argument("--year-cutoff", type=int, default=2023,
                        help="Skip articles published before this year (default: 2023)")
    # Legacy compat
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
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

_REQUEST_DELAY = 1.5  # seconds between requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch(url, client=None, timeout=30):
    """Fetch a URL.  Playwright first, requests as fallback."""
    time.sleep(_REQUEST_DELAY)
    if client is not None:
        try:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logging.warning("Playwright failed for %s: %s, trying requests fallback", url, e)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        if enc.upper() in ("EUC-JP", "EUC-KR", "SHIFT_JIS", "ISO-8859-1"):
            enc = resp.apparent_encoding or "utf-8"
        if not enc or enc.upper() in ("ASCII", "ISO-8859-1"):
            enc = "utf-8"
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return raw.decode("gbk", errors="replace")
    except Exception as e:
        logging.error("requests fallback also failed for %s: %s", url, e)
    return None


def _abs_url(href, base):
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return urljoin(base, href)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y\u5e74%m\u6708%d\u65e5",
]


def _parse_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _extract_date_from_url(url):
    """Extract date from ccgp article URL pattern tYYYYMMDD_xxxxx.htm."""
    m = re.search(r"/t(\d{4})(\d{2})(\d{2})_\d+\.htm", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def _parse_pager(html):
    """Extract total page count from ``Pager({size:N, …})`` in the HTML.

    Returns (total_pages, current_page_0based).
    """
    m = re.search(r"Pager\(\{([^}]+)\}\)", html)
    if not m:
        return 1, 0
    body = m.group(1)
    size = 1
    current = 0
    for part in body.split(","):
        part = part.strip()
        if ":" in part:
            key, _, val = part.partition(":")
            key = key.strip()
            try:
                v = int(val.strip())
            except ValueError:
                continue
            if key == "size":
                size = v
            elif key == "current":
                current = v
    return max(size, 1), current


def _build_listing_urls(site_root, section_path, total_pages):
    """Generate listing page URLs for a zcfg (sub-)section.

    Page 1:  http://www.ccgp.gov.cn/zcfg/mof/index.htm
    Page 2:  http://www.ccgp.gov.cn/zcfg/mof/index_1.htm
    Page 3:  http://www.ccgp.gov.cn/zcfg/mof/index_2.htm
    …
    """
    section_path = section_path.strip("/")
    urls = []
    for page_num in range(1, total_pages + 1):
        if page_num == 1:
            filename = "index.htm"
        else:
            # Pager is 0-based: current=1 → index_1.htm
            filename = f"index_{page_num - 1}.htm"
        urls.append(f"{site_root}/{section_path}/{filename}")
    return urls


# ---------------------------------------------------------------------------
# Article extraction from listing pages
# ---------------------------------------------------------------------------

def _extract_articles_from_listing(html, section_label, listing_url, ul_class, year_cutoff):
    """Extract article entries from a zcfg listing page.

    Handles two ``<ul>`` class variants:
      - ``c_list_zcfg``  (most zcfg sub-sections)
      - ``c_list_tat``   (guojifg)

    Returns list[dict] with keys: title, url, date (datetime or None).
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    base_for_url = listing_url or "http://www.ccgp.gov.cn"

    # Look for the article list <ul>
    for ul in soup.find_all("ul", class_=ul_class):
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            if not href.endswith(".htm"):
                continue

            title = (a.get("title") or a.get_text(strip=True) or "").strip()
            if not title or len(title) < 2:
                continue

            url = _abs_url(href, base_for_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract date: URL pattern first, then <span> text
            dt = _extract_date_from_url(url)
            if not dt:
                span = li.find("span")
                if span:
                    dt = _parse_date(span.get_text(strip=True))
            if not dt:
                all_text = li.get_text()
                m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", all_text)
                if m:
                    dt = _parse_date(m.group(1))

            if dt and dt.year < year_cutoff:
                logging.debug("Skipped %s (date: %s)", title[:60], dt.date())
                continue

            articles.append({
                "title": title,
                "url": url,
                "date": dt,
                "section": section_label,
            })

    return articles


# ---------------------------------------------------------------------------
# Province directory extraction (dffg two-level)
# ---------------------------------------------------------------------------

def _extract_provinces_from_directory(html, listing_url):
    """Extract province sub-directory links from the dffg top-level page.

    Each province is listed as:
        <li><a href="./guangdong/">广东</a></li>

    Returns list[dict] with keys: name, url.
    """
    soup = BeautifulSoup(html, "lxml")
    provinces = []
    base_for_url = listing_url or "http://www.ccgp.gov.cn"

    for ul in soup.find_all("ul", class_="c_list_zcfg"):
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            if href.endswith(".htm"):
                continue  # skip regular articles that happen to be in this ul

            name = (a.get("title") or a.get_text(strip=True) or "").strip()
            if not name:
                continue

            url = _abs_url(href, base_for_url)
            provinces.append({"name": name, "url": url})

    return provinces


# ---------------------------------------------------------------------------
# Article content from detail page
# ---------------------------------------------------------------------------

def _fetch_article_content(url, client=None):
    """Fetch and parse a ccgp.gov.cn zcfg article detail page.

    Returns (markdown_text, metadata_dict).
    Returns ("", {}) on failure.
    """
    html = _fetch(url, client=client)
    if not html:
        return "", {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract metadata from <meta> tags --
    meta_title = ""
    meta_date = ""
    meta_source = ""
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").strip()
        content = (meta.get("content") or "").strip()
        if name == "ArticleTitle":
            meta_title = content
        elif name == "PubDate":
            meta_date = content
        elif name == "ContentSource":
            meta_source = content

    # Fallback: <h2> for title
    if not meta_title:
        h2 = soup.find("h2", class_="tc")
        if h2:
            meta_title = h2.get_text(strip=True)

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # -- Extract main content from TRS_Editor --
    detail_content = soup.find("div", class_="vF_detail_main")
    if not detail_content:
        detail_content = soup.find("div", class_="TRS_Editor")
    if not detail_content:
        # Try vF_detail_content (used in some page variants)
        detail_content = soup.find("div", class_="vF_detail_content")

    content_md = ""
    if detail_content:
        content_md = _content_to_markdown(detail_content)

    # -- Extract appendix section (附件) --
    appendix = soup.find("div", class_="appendix")
    if appendix:
        app_lines = []
        for a in appendix.find_all("a", href=True):
            href = _abs_url(a["href"], url)
            text = a.get_text(strip=True) or os.path.basename(href)
            app_lines.append(f"- [{text}]({href})")
        if app_lines:
            content_md += "\n\n**\u9644\u4ef6**\n\n" + "\n".join(app_lines)

    return content_md, metadata


def _content_to_markdown(detail_soup):
    """Convert a detail content <div> to Markdown."""
    lines = []
    for el in detail_soup.find_all(
        ["p", "h1", "h2", "h3", "h4", "h5", "h6",
         "li", "blockquote", "pre", "div", "table"]
    ):
        if el.name == "table":
            md_table = _table_to_markdown(el)
            if md_table:
                lines.append("")
                lines.append(md_table)
                lines.append("")
            continue

        text = el.get_text(strip=True)
        if not text:
            continue

        tn = el.name
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
        elif tn == "div":
            if len(text) > 100:
                lines.append(text)
        else:
            # <p> and others
            lines.append(text)

    return "\n\n".join(lines)


def _table_to_markdown(table):
    """Convert an HTML <table> to a simple Markdown table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(cell.get_text(strip=True))
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    n_cols = max(len(r) for r in rows)
    md = []
    md.append("| " + " | ".join(r.ljust(15) for r in rows[0]) + " |")
    md.append("| " + " | ".join(["---"] * n_cols) + " |")
    for row in rows[1:]:
        padded = list(row) + [""] * (n_cols - len(row))
        md.append("| " + " | ".join(r.ljust(15) for r in padded) + " |")
    return "\n".join(md)


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
    return {"processed_urls": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info(
        "Crawler state saved (%d processed URLs)",
        len(state.get("processed_urls", [])),
    )


def _save_markdown(content, output_dir, section_key, file_index=""):
    """Save a Markdown file under output_dir/section_key/."""
    section_dir = os.path.join(output_dir, section_key)
    os.makedirs(section_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{file_index}" if file_index else ""
    path = os.path.join(section_dir, f"{ts}{suffix}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="naive"):
    """Upload a Markdown file to KB and queue parsing."""
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
        doc_id = doc["id"]
        logging.info("Document %s uploaded to KB %s", doc_id, kb_id)
        try:
            DocumentService.update_by_id(doc_id, {"parser_id": parser_id})
        except Exception as e:
            logging.error("Failed to update parser_id for %s: %s", doc_id, e)
        try:
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Crawl a single listing section (non-dffg)
# ---------------------------------------------------------------------------

def _crawl_section(section_key, section_info, args, site_root, client,
                   processed_urls, year_cutoff):
    """Crawl a standard (non-two-level) zcfg section.

    Returns list of new articles (filtered against processed_urls).
    """
    section_path, section_label, ul_class, _ = section_info

    max_pages = args.max_pages if args.max_pages else 999

    # Determine total pages from page 1's Pager widget
    first_url = f"{site_root}{section_path}index.htm"
    html = _fetch(first_url, client=client)
    if not html:
        logging.warning("Failed to fetch listing %s", first_url)
        return [], [], []
    total_pages_from_pager, _ = _parse_pager(html)
    total_pages = min(max_pages, total_pages_from_pager)
    logging.info(
        "Section %s: Pager reports %d pages, using %d",
        section_key, total_pages_from_pager, total_pages,
    )

    listing_urls = _build_listing_urls(site_root, section_path, total_pages)

    # Collect all articles from all pages
    all_page_articles = []
    for page_idx, list_url in enumerate(listing_urls, 1):
        page_html = html if page_idx == 1 else _fetch(list_url, client=client)
        if not page_html:
            logging.warning("Failed to fetch listing page %s", list_url)
            continue
        arts = _extract_articles_from_listing(
            page_html, section_label, list_url, ul_class, year_cutoff,
        )
        all_page_articles.extend(arts)
        logging.info("  Page %d/%d: %d articles", page_idx, total_pages, len(arts))
        if len(arts) == 0:
            break

    # Deduplicate
    new_arts = [a for a in all_page_articles if a["url"] not in processed_urls]
    logging.info(
        "  Total on pages: %d, new: %d",
        len(all_page_articles), len(new_arts),
    )
    return new_arts


# ---------------------------------------------------------------------------
# Crawl a dffg province sub-section
# ---------------------------------------------------------------------------

def _crawl_dffg_province(province, site_root, client, processed_urls,
                         max_pages, year_cutoff):
    """Crawl one province's article listing.  Returns list of new articles."""
    province_url = province["url"]
    province_name = province["name"]

    html = _fetch(province_url, client=client)
    if not html:
        logging.warning("Failed to fetch province listing %s", province_url)
        return []

    total_pages_from_pager, _ = _parse_pager(html)
    total_pages = min(max_pages or 999, total_pages_from_pager)

    # Determine the section path from the URL for building listing URLs
    parsed = urlparse(province_url)
    section_path = parsed.path.strip("/")

    listing_urls = _build_listing_urls(site_root, section_path, total_pages)

    all_articles = []
    for page_idx, list_url in enumerate(listing_urls, 1):
        page_html = html if page_idx == 1 else _fetch(list_url, client=client)
        if not page_html:
            continue
        arts = _extract_articles_from_listing(
            page_html,
            f"\u5730\u65b9\u6cd5\u89c4-{province_name}",
            list_url,
            "c_list_zcfg",
            year_cutoff,
        )
        all_articles.extend(arts)
        if len(arts) == 0:
            break

    new_arts = [a for a in all_articles if a["url"] not in processed_urls]
    logging.info(
        "  Province %s: %d total, %d new",
        province_name, len(all_articles), len(new_arts),
    )
    return new_arts


# ---------------------------------------------------------------------------
# Process article detail: fetch + build markdown
# ---------------------------------------------------------------------------

def _process_article(art, client):
    """Fetch detail page and build markdown for one article.

    Returns markdown string, or "" on failure.
    """
    content_md, metadata = _fetch_article_content(art["url"], client=client)
    if not content_md:
        return ""

    article_date_str = ""
    if art.get("date"):
        article_date_str = art["date"].strftime("%Y-%m-%d")
    elif metadata.get("date"):
        article_date_str = metadata["date"]

    source_str = metadata.get("source", "")
    source_line = f"**\u6765\u6e90:** {source_str}" if source_str else ""

    lines = [
        f"# {art['title']}",
        f"**\u680f\u76ee:** {art['section']}",
        f"**\u65e5\u671f:** {article_date_str}",
        f"**URL:** {art['url']}",
    ]
    if source_line:
        lines.append(source_line)
    lines.append("")
    lines.append(content_md)
    lines.append("")
    lines.append("---")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Safe print (handles Windows GBK encoding issues)
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print("[ZCFG] Starting CCGP \u653f\u7b56\u6cd5\u89c4 crawler")
    _safe_print(f"[ZCFG] Base URL: {args.target_url}")
    _safe_print(f"[ZCFG] Task name: {args.task_name}")
    _safe_print(f"[ZCFG] Target KB: {args.kb_id}")
    _safe_print(f"[ZCFG] Year cutoff: {args.year_cutoff}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== ZCFG crawler started for %s ===", args.target_url)

    client = None
    try:
        # Start Playwright browser
        try:
            client = PlaywrightHttpClient()
            client.start()
            _safe_print("[ZCFG] Playwright browser ready")
            sys.stdout.flush()
        except Exception as e:
            _safe_print(f"[ZCFG] Playwright unavailable ({e}), using requests only")
            sys.stdout.flush()
            client = None

        # Determine which sections to crawl
        if args.section:
            selected = {k: v for k, v in SECTIONS.items()
                        if k in [s.strip() for s in args.section.split(",")]}
            if not selected:
                _safe_print(f"[ZCFG] WARNING: No matching sections for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[ZCFG] Sections to crawl: {len(selected)}")
        for key, (path, label, _, is_two) in selected.items():
            lvl = " (two-level)" if is_two else ""
            _safe_print(f"         - {label} ({key}){lvl}  ->  {path}")
        sys.stdout.flush()

        # Output directory
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[ZCFG] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Site root
        p = urlparse(args.target_url)
        site_root = f"{p.scheme}://{p.netloc}"

        # Incremental state (dedup by URL)
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[ZCFG] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # -------------------------------------------------------------------
        # Phase 1: Collect all new article references
        # -------------------------------------------------------------------
        _safe_print("[ZCFG] Phase 1/3: Crawling listing pages ...\n")
        sys.stdout.flush()

        all_new_articles = []
        max_articles = args.max_articles or 0

        for sec_idx, (section_key, section_info) in enumerate(selected.items(), 1):
            section_path, section_label, ul_class, is_two_level = section_info

            _safe_print(f"[ZCFG] [{sec_idx}/{len(selected)}] {section_label} ({section_key})")
            sys.stdout.flush()

            if is_two_level:
                # dffg: province directory → province articles
                dir_url = f"{site_root}{section_path}index.htm"
                dir_html = _fetch(dir_url, client=client)
                if not dir_html:
                    logging.warning("Failed to fetch dffg directory %s", dir_url)
                    continue

                provinces = _extract_provinces_from_directory(dir_html, dir_url)
                _safe_print(f"[ZCFG]   {len(provinces)} province directories found")
                sys.stdout.flush()

                for prov in provinces:
                    if max_articles and len(all_new_articles) >= max_articles:
                        break

                    remaining = max_articles - len(all_new_articles) if max_articles else 0
                    prov_arts = _crawl_dffg_province(
                        prov, site_root, client, processed_urls,
                        args.max_pages, args.year_cutoff,
                    )

                    # Apply per-province article limit if max_articles is set
                    if remaining and len(prov_arts) > remaining:
                        prov_arts = prov_arts[:remaining]

                    all_new_articles.extend(prov_arts)
                    _safe_print(f"[ZCFG]     {prov['name']}: {len(prov_arts)} new articles (cumulative: {len(all_new_articles)})")
                    sys.stdout.flush()
            else:
                # Standard single-level section
                new_arts = _crawl_section(
                    section_key, section_info, args, site_root, client,
                    processed_urls, args.year_cutoff,
                )
                if max_articles:
                    remaining = max_articles - len(all_new_articles)
                    if remaining <= 0:
                        continue
                    if len(new_arts) > remaining:
                        new_arts = new_arts[:remaining]

                all_new_articles.extend(new_arts)
                _safe_print(f"[ZCFG]   {len(new_arts)} new articles (cumulative: {len(all_new_articles)})")
                sys.stdout.flush()

            if max_articles and len(all_new_articles) >= max_articles:
                _safe_print(f"[ZCFG]   Reached --max-articles limit ({max_articles}), stopping collection")
                sys.stdout.flush()
                break

        if not all_new_articles:
            _safe_print("\n[ZCFG] No new articles to process.")
            sys.stdout.flush()
            logging.info("=== ZCFG crawler finished: no new articles ===")
            return

        _safe_print(f"\n[ZCFG] {len(all_new_articles)} new articles to fetch\n")
        sys.stdout.flush()

        # -------------------------------------------------------------------
        # Phase 2: Fetch detail pages and build markdown
        # -------------------------------------------------------------------
        _safe_print(f"[ZCFG] Phase 2/3: Fetching detail pages ...\n")
        sys.stdout.flush()

        total_success = 0
        total_fail = 0

        # Group articles by section for per-section markdown files
        from collections import defaultdict
        section_md = defaultdict(list)

        for idx, art in enumerate(all_new_articles, 1):
            _safe_print(f"[ZCFG]   [{idx}/{len(all_new_articles)}] {art['title'][:70]}")
            sys.stdout.flush()

            md = _process_article(art, client)
            if not md:
                _safe_print(f"[ZCFG]     -> Empty content, skipped")
                sys.stdout.flush()
                total_fail += 1
                continue

            # Determine section key from article section label
            sec_key = "unknown"
            for sk, (sp, sl, uc, _) in SECTIONS.items():
                if art["section"].startswith(sl):
                    sec_key = sk
                    break
                # Also match dffg province labels
                if art["section"].startswith("\u5730\u65b9\u6cd5\u89c4-"):
                    sec_key = "dffg"
                    break

            section_md[sec_key].append(md)
            total_success += 1
            _safe_print(f"[ZCFG]     -> {len(md)} chars")
            sys.stdout.flush()

        # -------------------------------------------------------------------
        # Phase 3: Save markdown, update state, upload to KB
        # -------------------------------------------------------------------
        _safe_print(f"\n[ZCFG] Phase 3/3: Saving & uploading ...\n")
        sys.stdout.flush()

        uploaded = 0
        for sec_key, md_parts in section_md.items():
            if not md_parts:
                continue

            # Save per-section combined markdown
            combined = "\n".join(md_parts)
            filepath = _save_markdown(combined, output_dir, sec_key)

            # Update state
            new_urls = [a["url"] for a in all_new_articles
                        if a.get("url")]
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

            # Upload to KB
            _safe_print(f"[ZCFG]   Uploading '{sec_key}' ({len(md_parts)} articles) to KB {args.kb_id}...")
            sys.stdout.flush()
            try:
                _upload_to_kb(filepath, args.kb_id, args.tenant_id, parser_id="naive")
                _safe_print(f"[ZCFG]   Upload OK!")
                sys.stdout.flush()
                uploaded += 1
            except Exception as e:
                _safe_print(f"[ZCFG]   ERROR: Upload failed: {e}")
                sys.stdout.flush()
                logging.error("Upload failed for %s: %s", sec_key, e)

        _safe_print(f"\n{'='*60}")
        _safe_print(f"[ZCFG] Done! {total_success} articles fetched, "
                     f"{total_fail} failed, {uploaded} sections uploaded")
        _safe_print(f"{'='*60}")
        sys.stdout.flush()
        logging.info(
            "=== ZCFG crawler finished: %d success, %d fail ===",
            total_success, total_fail,
        )
    finally:
        if client is not None:
            client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "ccgp_zcfg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
