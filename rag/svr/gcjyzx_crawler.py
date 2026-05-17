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
Dedicated web crawler for gcjyzx.zhangzhou.gov.cn (漳州市工程项目交易中心).

Traditional server-rendered JSP site with jQuery pagination. Contains policy
documents, notices, violation reports, and service guides suitable for RAG.

Content sections:
  - 政策法规 (Policy Documents)        — /zcfg/moreinfo.html  (439 items, 22 pages)
  - 通知公告 (Platform Notices)        — /tzgg/moreinfo.html  (131 items,  7 pages)
  - 违规通报 (Violation Notices)       — /wgtb/moreinfo.html  (~11 items,  1 page)
  - 办事指南 (Service Guides)          — /bszn/moreinfo.html  (~10 items,  1 page)

Listing page structure:
  <li class="list-item">
    <a href="/{section}/{YYYYMMDD}/{UUID}.html">
      <p class="name text-overflow" title="TITLE">TITLE</p>
      <span class="date">YYYY-MM-DD</span>
    </a>
  </li>

Pagination: /{section}/{pageNum}.html  (pageNum starts at 1, page 1 = moreinfo.html)

Detail page structure:
  <div class="title">TITLE</div>
  <div class="toolbar">来源：SOURCE  发布时间：DATE</div>
  <div class="content-main clearfix">... article HTML ...</div>

Usage (typically spawned by task_executor):
    python gcjyzx_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://gcjyzx.zhangzhou.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

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
# Known sections
# ---------------------------------------------------------------------------
SECTIONS = {
    "zcfg": ("/zcfg/moreinfo.html", "政策法规"),
    "tzgg": ("/tzgg/moreinfo.html", "通知公告"),
    "wgtb": ("/wgtb/moreinfo.html", "违规通报"),
    "bszn": ("/bszn/moreinfo.html", "办事指南"),
}

_SITE_ROOT = "https://gcjyzx.zhangzhou.gov.cn"


def parse_args():
    parser = argparse.ArgumentParser(description="Zhangzhou GCJYXZ crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL (e.g. https://gcjyzx.zhangzhou.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None, help="Comma-separated list of section labels to crawl (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=365,
                        help="Max age in days for articles to crawl (default: 365)")
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
    )
}


def _fetch(url, timeout=30, client=None):
    try:
        if client:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
        else:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        if enc.upper() in ("EUC-JP", "EUC-KR", "SHIFT_JIS", "ISO-8859-1"):
            enc = getattr(resp, "apparent_encoding", None) or "utf-8"
        if not enc or enc.upper() in ("ASCII", "ISO-8859-1"):
            enc = "utf-8"
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return raw.decode("gbk", errors="replace")
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _abs_url(href, base=None):
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if base:
        return urljoin(base, href)
    return urljoin(_SITE_ROOT, href)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y—%m—%d", "%Y年%m月%d日",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Listing parsing
# ---------------------------------------------------------------------------

def _extract_articles_from_listing(html, section_label, listing_url):
    """Extract article entries from a gcjyzx listing page.

    Structure:
      <li class="list-item">
        <a href="/zcfg/20260212/...html">
          <p class="name text-overflow" title="TITLE">TITLE</p>
          <span class="date">YYYY-MM-DD</span>
        </a>
      </li>

    Args:
        html: Listing page HTML.
        section_label: Display name for the section.
        listing_url: URL of the listing page (for resolving relative URLs).

    Returns list[dict] with keys: title, url, date (datetime or None).
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    for li in soup.find_all("li", class_="list-item"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        title = ""
        # Title from <p class="name text-overflow" title="...">
        p = a.find("p", class_="name")
        if p and p.get("title"):
            title = p["title"].strip()
        if not title:
            title = (a.get("title") or a.text or "").strip()
        if not title or len(title) < 2:
            continue

        url = _abs_url(href, listing_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Date from <span class="date">
        dt = None
        span = li.find("span", class_="date")
        if span:
            dt = _parse_date(span.get_text(strip=True))

        # Fallback: regex in full text
        if not dt:
            all_text = li.get_text()
            m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", all_text)
            if m:
                dt = _parse_date(m.group(1))

        articles.append({
            "title": title,
            "url": url,
            "date": dt,
            "section": section_label,
        })

    return articles


# ---------------------------------------------------------------------------
# Article detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(url, client=None):
    """Fetch and parse a gcjyzx article detail page.

    Structure:
      <div class="title">TITLE</div>
      <div class="toolbar">来源：SOURCE  发布时间：DATE</div>
      <div class="content-main clearfix">... HTML ...</div>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url, client=client)
    if not html:
        return "", [], {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract metadata --
    meta_title = ""
    title_div = soup.find("div", class_="title")
    if title_div:
        meta_title = title_div.get_text(strip=True)
    if not meta_title:
        # Fallback: <title> tag
        t = soup.find("title")
        if t:
            meta_title = t.get_text(strip=True)

    meta_date = ""
    meta_source = ""
    toolbar = soup.find("div", class_="toolbar")
    if toolbar:
        tb_text = toolbar.get_text()
        src_m = re.search(r"来源[：:]\s*(.+?)\s*发布时间", tb_text, re.DOTALL)
        if src_m:
            meta_source = src_m.group(1).strip()
        date_m = re.search(r"发布时间[：:]\s*(\S+)", tb_text)
        if date_m:
            meta_date = date_m.group(1).strip()

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Parse main content --
    content_div = soup.find("div", class_="content-main")
    if not content_div:
        content_div = soup.find("div", class_="content")
    if not content_div:
        logging.warning("No content-main div found in %s", url)
        return "", [], metadata

    resources = []
    markdown_text = _content_to_markdown(content_div)

    return markdown_text, resources, metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text."""
    _TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "img",
             "div", "section", "table",
             "span", "strong", "font", "em", "b", "i", "u", "a"}

    lines = []
    seen_texts = set()

    for el in content_div.find_all(list(_TAGS)):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                src = _abs_url(src)
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
    return {"processed_urls": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed URLs)", len(state.get("processed_urls", [])))


def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
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
            logging.error("Failed to queue parsing for document %s: %s", doc["id"], e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _paginated_url(section_id, url_path, page_num):
    """Build the listing URL for a given page number."""
    if page_num <= 1:
        return f"{_SITE_ROOT}{url_path}"
    return f"{_SITE_ROOT}/{section_id}/{page_num}.html"


def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GCJYXZ] Starting Zhangzhou Engineering Project Trading Center crawler")
    _safe_print(f"[GCJYXZ] Target URL: {args.target_url}")
    _safe_print(f"[GCJYXZ] Task name: {args.task_name}")
    _safe_print(f"[GCJYXZ] Target KB: {args.kb_id}")
    _safe_print(f"[GCJYXZ] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[GCJYXZ] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== GCJYXZ crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        if args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in SECTIONS:
                    selected[label] = SECTIONS[label]
            if not selected:
                _safe_print(f"[GCJYXZ] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[GCJYXZ] Sections to crawl: {len(selected)}")
        for label, (url_path, display) in selected.items():
            _safe_print(f"         - {display} ({url_path})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[GCJYXZ] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[GCJYXZ] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # Step 1: Crawl listing pages
        _safe_print(f"[GCJYXZ] Step 1/4: Crawling listing pages...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}
        cutoff = datetime.now() - timedelta(days=args.max_days)

        for section_id, (url_path, display_label) in selected.items():
            _safe_print(f"[GCJYXZ]   Section '{display_label}':")
            sys.stdout.flush()

            section_articles = []
            page_num = 1

            while True:
                if args.max_articles and len(section_articles) >= args.max_articles:
                    break

                list_url = _paginated_url(section_id, url_path, page_num)
                _safe_print(f"[GCJYXZ]     Page {page_num}: {list_url}")
                sys.stdout.flush()

                html = _fetch(list_url, client=client)
                if not html:
                    logging.warning("Failed to fetch listing page %s", list_url)
                    break

                arts = _extract_articles_from_listing(html, display_label, list_url)
                if not arts:
                    _safe_print(f"[GCJYXZ]     -> No more articles found, stopping")
                    sys.stdout.flush()
                    break

                # Filter by date and max_articles
                date_cutoff_hit = False
                for art in arts:
                    if args.max_articles and len(section_articles) >= args.max_articles:
                        break
                    if art.get("date") and art["date"] < cutoff:
                        date_cutoff_hit = True
                        break
                    section_articles.append(art)

                if date_cutoff_hit:
                    _safe_print(f"[GCJYXZ]     -> Hit date cutoff, stopping pagination")
                    sys.stdout.flush()
                    break

                # Check if this was the last page (fewer items than expected)
                if len(arts) < 20:
                    _safe_print(f"[GCJYXZ]     -> Last page ({len(arts)} items)")
                    sys.stdout.flush()
                    break

                page_num += 1
                time.sleep(0.3)

            count = len(section_articles)
            section_stats[display_label] = count
            for art in section_articles:
                all_articles.append(art)

            _safe_print(f"[GCJYXZ]     -> {count} articles\n")
            sys.stdout.flush()

        _safe_print(f"[GCJYXZ] Step 1/4: Collected {len(all_articles)} total articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[GCJYXZ] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"[GCJYXZ] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[GCJYXZ] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[GCJYXZ] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # Step 2: Fetch detail pages
        _safe_print(f"\n[GCJYXZ] Step 2/4: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[GCJYXZ] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"], client=client)
            if not content:
                _safe_print(f"[GCJYXZ]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[GCJYXZ]   -> {len(content)} chars")
            sys.stdout.flush()

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
                f"**URL:** {art['url']}",
            ]
            if source_line:
                lines.append(source_line)
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            md_parts.append("\n".join(lines))
            success_count += 1

        if not md_parts:
            _safe_print(f"[GCJYXZ] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[GCJYXZ] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # Step 3: Save markdown
        _safe_print(f"[GCJYXZ] Step 3/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[GCJYXZ] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        # Step 4: Upload to KB
        _safe_print(f"[GCJYXZ] Step 4/4: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[GCJYXZ] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[GCJYXZ] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
