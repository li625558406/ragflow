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
Dedicated web crawler for three ccgp.gov.cn sections.

    /jdjc/jdcf/   \u76d1\u7763\u5904\u7f5a (supervision & penalties)
    /gg/           \u516c\u544a (announcements)
    /search/cr/    \u4e25\u91cd\u8fdd\u6cd5\u5931\u4fe1 (serious violations — table data via /cr/list)

Site characteristics
--------------------
* jdcf — standard ``ul.c_list_tat`` listing, 3 pages, detail pages with TRS_Editor.
* gg   — custom ``ul#pageContent`` inside ``div.inforcon2``, 1 page only,
         detail pages with ``vF_detail_main``.
* cr   — data in an iframe at ``/cr/list``; jQuery EasyUI table ``#tableInfo``.
         All data is inline (10 columns per row), no separate detail pages.

Usage (typically spawned by task_executor):
    python ccgp_jdcf_gg_cr_crawler.py \\
        --tenant-id <TENANT_ID> \\
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
from collections import defaultdict
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
SECTIONS = {
    "jdcf": {
        "path": "/jdjc/jdcf/",
        "label": "\u76d1\u7763\u5904\u7f5a",
        "list_type": "c_list_tat",
    },
    "gg": {
        "path": "/gg/",
        "label": "\u516c\u544a",
        "list_type": "pageContent",
    },
    "cr": {
        "path": "/search/cr/",
        "label": "\u4e25\u91cd\u8fdd\u6cd5\u5931\u4fe1",
        "list_type": "tableInfo",
    },
}

SITE_ROOT = "http://www.ccgp.gov.cn"
_REQUEST_DELAY = 1.5

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="CCGP jdcf/gg/cr crawler for scheduled tasks"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated section keys: jdcf,gg,cr (default: all)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles per section (0=unlimited)")
    parser.add_argument("--year-cutoff", type=int, default=2023,
                        help="Skip articles before this year (default: 2023)")
    # Legacy compat for task_executor
    parser.add_argument("--target-url", default=None, help="Unused (legacy)")
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

def _fetch(url, client=None, timeout=30):
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
        logging.error("requests fallback failed for %s: %s", url, e)
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
    "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y",
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
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _extract_date_from_url(url):
    m = re.search(r"/t(\d{4})(\d{2})(\d{2})_\d+\.htm", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Pagination helpers (for jdcf)
# ---------------------------------------------------------------------------

def _parse_pager(html):
    m = re.search(r"Pager\(\{([^}]+)\}\)", html)
    if not m:
        return 1
    body = m.group(1)
    for part in body.split(","):
        if ":" in part:
            key, _, val = part.partition(":")
            if key.strip() == "size":
                try:
                    return max(int(val.strip()), 1)
                except ValueError:
                    pass
    return 1


def _build_listing_urls(section_path, total_pages):
    section_path = section_path.strip("/")
    urls = []
    for page_num in range(1, total_pages + 1):
        if page_num == 1:
            filename = "index.htm"
        else:
            filename = f"index_{page_num - 1}.htm"
        urls.append(f"{SITE_ROOT}/{section_path}/{filename}")
    return urls


# ===================================================================
# Crawler: JDCF (\u76d1\u7763\u5904\u7f5a)
# ===================================================================

def _crawl_jdcf_listing(client, year_cutoff):
    """Extract articles from c_list_tat listing with pagination."""
    first_url = f"{SITE_ROOT}/jdjc/jdcf/index.htm"
    html = _fetch(first_url, client=client)
    if not html:
        return []

    total_pages = _parse_pager(html)
    logging.info("jdcf: %d pages from Pager", total_pages)
    listing_urls = _build_listing_urls("/jdjc/jdcf", total_pages)

    all_articles = []
    for page_idx, list_url in enumerate(listing_urls, 1):
        page_html = html if page_idx == 1 else _fetch(list_url, client=client)
        if not page_html:
            continue
        soup = BeautifulSoup(page_html, "lxml")
        seen = set()
        for ul in soup.find_all("ul", class_="c_list_tat"):
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
                url = _abs_url(href, list_url)
                if url in seen:
                    continue
                seen.add(url)

                dt = _extract_date_from_url(url)
                if not dt:
                    span = li.find("span")
                    if span:
                        dt = _parse_date(span.get_text(strip=True))
                if dt and dt.year < year_cutoff:
                    continue

                all_articles.append({
                    "title": title,
                    "url": url,
                    "date": dt,
                    "section": "\u76d1\u7763\u5904\u7f5a",
                })
        logging.info("  jdcf page %d/%d: %d articles", page_idx, total_pages, len(seen))
        if len(seen) == 0:
            break
    return all_articles


# ===================================================================
# Crawler: GG (\u516c\u544a)
# ===================================================================

def _crawl_gg_listing(client, year_cutoff):
    """Extract articles from ul#pageContent listing (1 page)."""
    url = f"{SITE_ROOT}/gg/"
    html = _fetch(url, client=client)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    page_content = soup.find("ul", id="pageContent")
    if not page_content:
        logging.warning("gg: ul#pageContent not found")
        return []

    articles = []
    seen = set()
    for li in page_content.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href.endswith(".htm"):
            continue
        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if not title or len(title) < 2:
            continue
        article_url = _abs_url(href, url)
        if article_url in seen:
            continue
        seen.add(article_url)

        dt = _extract_date_from_url(article_url)
        if not dt:
            span = li.find("span")
            if span:
                dt = _parse_date(span.get_text(strip=True))
        if dt and dt.year < year_cutoff:
            continue

        articles.append({
            "title": title,
            "url": article_url,
            "date": dt,
            "section": "\u516c\u544a",
        })

    logging.info("gg: %d articles on page", len(articles))
    return articles


# ===================================================================
# Crawler: CR (\u4e25\u91cd\u8fdd\u6cd5\u5931\u4fe1) — table-based
# ===================================================================

def _crawl_cr_table(client):
    """Parse /cr/list iframe content — jQuery EasyUI table.

    Table columns: #, company_name, tax_id, address, violation_type,
    penalty, legal_basis, penalty_date, entry_time, authority.

    Returns list[dict] with all fields populated from the row.
    """
    url = f"{SITE_ROOT}/cr/list"
    html = _fetch(url, client=client)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="tableInfo")
    if not table:
        logging.warning("cr: table#tableInfo not found")
        return []

    records = []
    # Skip header row(s) — first tr with <th> is header
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue  # skip header / empty rows
        cells = [td.get_text(strip=True) for td in tds]
        if not cells[1] or cells[1] == "\u4f9b\u5e94\u5546\u540d\u79f0":
            # header row or empty company name
            continue

        records.append({
            "seq": cells[0] if len(cells) > 0 else "",
            "company": cells[1] if len(cells) > 1 else "",
            "tax_id": cells[2] if len(cells) > 2 else "",
            "address": cells[3] if len(cells) > 3 else "",
            "violation": cells[4] if len(cells) > 4 else "",
            "penalty": cells[5] if len(cells) > 5 else "",
            "legal_basis": cells[6] if len(cells) > 6 else "",
            "penalty_date": cells[7] if len(cells) > 7 else "",
            "entry_time": cells[8] if len(cells) > 8 else "",
            "authority": cells[9] if len(cells) > 9 else "",
        })

    logging.info("cr: %d records from table", len(records))
    return records


# ===================================================================
# Detail page fetchers (jdcf + gg)
# ===================================================================

def _fetch_article_detail(url, client=None):
    """Fetch and parse a standard ccgp detail page.

    Returns (markdown_text, metadata_dict).
    """
    html = _fetch(url, client=client)
    if not html:
        return "", {}

    soup = BeautifulSoup(html, "lxml")

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

    if not meta_title:
        h2 = soup.find("h2", class_="tc")
        if h2:
            meta_title = h2.get_text(strip=True)

    metadata = {"title": meta_title, "date": meta_date, "source": meta_source}

    # Strip clutter
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Extract content
    detail = (soup.find("div", class_="vF_detail_main") or
              soup.find("div", class_="TRS_Editor") or
              soup.find("div", class_="vF_detail_content"))

    content_md = _content_to_markdown(detail) if detail else ""

    # Appendices
    appendix = soup.find("div", class_="appendix")
    if appendix:
        app_lines = []
        for a_tag in appendix.find_all("a", href=True):
            app_href = _abs_url(a_tag["href"], url)
            app_text = a_tag.get_text(strip=True) or os.path.basename(app_href)
            app_lines.append(f"- [{app_text}]({app_href})")
        if app_lines:
            content_md += "\n\n**\u9644\u4ef6**\n\n" + "\n".join(app_lines)

    return content_md, metadata


def _content_to_markdown(detail_soup):
    """Convert content <div> to Markdown."""
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
            lines.append(text)
    return "\n\n".join(lines)


def _table_to_markdown(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["th", "td"])]
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


# ===================================================================
# Build markdown for jdcf/gg articles
# ===================================================================

def _build_article_md(art, client):
    """Fetch detail and build a markdown document for one article."""
    content_md, metadata = _fetch_article_detail(art["url"], client=client)
    if not content_md:
        return ""

    date_str = ""
    if art.get("date"):
        date_str = art["date"].strftime("%Y-%m-%d")
    elif metadata.get("date"):
        date_str = metadata["date"]

    source = metadata.get("source", "")

    lines = [
        f"# {art['title']}",
        f"**\u680f\u76ee:** {art['section']}",
        f"**\u65e5\u671f:** {date_str}",
        f"**URL:** {art['url']}",
    ]
    if source:
        lines.append(f"**\u6765\u6e90:** {source}")
    lines.append("")
    lines.append(content_md)
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


# ===================================================================
# Build markdown for CR table records
# ===================================================================

def _build_cr_record_md(rec):
    """Build a markdown block for one CR table row."""
    lines = [
        f"## {rec['company']}",
        "",
        f"| \u5b57\u6bb5 | \u5185\u5bb9 |",
        f"| --- | --- |",
        f"| \u5e8f\u53f7 | {rec['seq']} |",
        f"| \u4f9b\u5e94\u5546\u540d\u79f0 | {rec['company']} |",
        f"| \u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801 | {rec['tax_id']} |",
        f"| \u5730\u5740 | {rec['address']} |",
        f"| \u8fdd\u6cd5\u884c\u4e3a | {rec['violation']} |",
        f"| \u5904\u7f5a\u7ed3\u679c | {rec['penalty']} |",
        f"| \u5904\u7f5a\u4f9d\u636e | {rec['legal_basis']} |",
        f"| \u5904\u7f5a\u65e5\u671f | {rec['penalty_date']} |",
        f"| \u5f55\u5165\u65f6\u95f4 | {rec['entry_time']} |",
        f"| \u6267\u6cd5\u5355\u4f4d | {rec['authority']} |",
        "",
        "---",
    ]
    return "\n".join(lines)


# ===================================================================
# Persistence & state
# ===================================================================

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
    logging.info("State saved (%d processed URLs)", len(state.get("processed_urls", [])))


def _save_markdown(content, output_dir, section_key):
    section_dir = os.path.join(output_dir, section_key)
    os.makedirs(section_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(section_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ===================================================================
# Knowledge-base upload
# ===================================================================

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="naive"):
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
            logging.error("Failed to update parser_id %s: %s", doc_id, e)
        try:
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ===================================================================
# Safe print
# ===================================================================

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


# ===================================================================
# Main
# ===================================================================

def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print("[JDC] Starting CCGP jdcf/gg/cr crawler")
    _safe_print(f"[JDC] Task name: {args.task_name}")
    _safe_print(f"[JDC] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== JDC crawler started ===")

    client = None
    try:
        try:
            client = PlaywrightHttpClient()
            client.start()
            _safe_print("[JDC] Playwright browser ready")
            sys.stdout.flush()
        except Exception as e:
            _safe_print(f"[JDC] Playwright unavailable ({e}), using requests only")
            sys.stdout.flush()
            client = None

        # Determine sections
        if args.section:
            selected = {k: v for k, v in SECTIONS.items()
                        if k in [s.strip() for s in args.section.split(",")]}
            if not selected:
                _safe_print(f"[JDC] WARNING: no matching sections, using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[JDC] Sections: {', '.join(selected.keys())}")
        sys.stdout.flush()

        # Output directory
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"[JDC] Output: {output_dir}\n")
        sys.stdout.flush()

        # Load state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[JDC] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        all_new_urls = []
        total_success = 0
        total_fail = 0

        for sec_key, sec_info in selected.items():
            label = sec_info["label"]
            list_type = sec_info["list_type"]

            _safe_print(f"[JDC] === Section: {label} ({sec_key}) ===")
            sys.stdout.flush()

            # ---- Phase 1: Collect article references ----
            if list_type == "c_list_tat":
                articles = _crawl_jdcf_listing(client, args.year_cutoff)
            elif list_type == "pageContent":
                articles = _crawl_gg_listing(client, args.year_cutoff)
            elif list_type == "tableInfo":
                # CR: table data, no filtering by processed_urls (each record is unique)
                records = _crawl_cr_table(client)
                articles = []  # not URL-based
            else:
                logging.warning("Unknown list_type: %s", list_type)
                continue

            # ---- Phase 2: Process ----
            if list_type == "tableInfo":
                # CR section: each row is complete, build markdown directly
                _safe_print(f"[JDC]   {len(records)} records in table")
                sys.stdout.flush()

                max_items = args.max_articles or len(records)
                records = records[:max_items]

                md_parts = []
                for idx, rec in enumerate(records, 1):
                    if idx % 20 == 0:
                        _safe_print(f"[JDC]   [{idx}/{len(records)}] processing...")
                        sys.stdout.flush()
                    md_parts.append(_build_cr_record_md(rec))

                if not md_parts:
                    _safe_print(f"[JDC]   No records to save")
                    sys.stdout.flush()
                    continue

                combined = "# \u4e25\u91cd\u8fdd\u6cd5\u5931\u4fe1\u884c\u4e3a\u8bb0\u5f55\n\n" + "\n".join(md_parts)
                filepath = _save_markdown(combined, output_dir, sec_key)
                _upload_to_kb(filepath, args.kb_id, args.tenant_id)
                total_success += len(md_parts)
                _safe_print(f"[JDC]   Uploaded {len(md_parts)} records")
                sys.stdout.flush()

            else:
                # jdcf / gg: URL-based articles with detail pages
                new_articles = [a for a in articles if a["url"] not in processed_urls]
                _safe_print(f"[JDC]   {len(articles)} on pages, {len(new_articles)} new")
                sys.stdout.flush()

                if args.max_articles and len(new_articles) > args.max_articles:
                    new_articles = new_articles[:args.max_articles]

                if not new_articles:
                    _safe_print(f"[JDC]   No new articles")
                    sys.stdout.flush()
                    continue

                md_parts = []
                sec_success = 0
                sec_fail = 0
                for idx, art in enumerate(new_articles, 1):
                    _safe_print(f"[JDC]   [{idx}/{len(new_articles)}] {art['title'][:70]}")
                    sys.stdout.flush()
                    md = _build_article_md(art, client)
                    if not md:
                        sec_fail += 1
                        continue
                    md_parts.append(md)
                    sec_success += 1

                if not md_parts:
                    total_fail += sec_fail
                    continue

                combined = "\n".join(md_parts)
                filepath = _save_markdown(combined, output_dir, sec_key)
                _upload_to_kb(filepath, args.kb_id, args.tenant_id)
                total_success += sec_success
                total_fail += sec_fail
                all_new_urls.extend(a["url"] for a in new_articles)
                _safe_print(f"[JDC]   {sec_success} done, {sec_fail} failed")
                sys.stdout.flush()

        # Save state (for URL-based sections)
        if all_new_urls:
            processed_urls.update(all_new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        _safe_print(f"\n{'='*60}")
        _safe_print(f"[JDC] Done! {total_success} articles, {total_fail} failed")
        _safe_print(f"{'='*60}")
        sys.stdout.flush()
        logging.info("=== JDC crawler finished: %d success, %d fail ===", total_success, total_fail)
    finally:
        if client is not None:
            client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "ccgp_jdcf_gg_cr_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
