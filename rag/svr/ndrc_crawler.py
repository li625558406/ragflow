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
Dedicated web crawler for www.ndrc.gov.cn (国家发展和改革委员会).

Crawls multiple content sections (新闻发布, 通知公告, 发展改革工作,
委属单位话发改), extracts article content from detail pages, converts to
Markdown, and uploads the result to a RAGFlow knowledge base.

Article URL pattern:
  /{section}/{YYYYMM}/t{YYYYMMDD}_{digits}.html

Listing page structure (25 articles/page):
  <li>
    <a href="./202604/t20260427_1404911.html">TITLE</a>
    <span>2026/04/27</span>
  </li>

Content container:
  <div class="TRS_Editor"><div class="Custom_UnionStyle"><div>...</div></div></div>

Usage (typically spawned by task_executor):
    python ndrc_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.ndrc.gov.cn/xwdt/xwfb/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import re
import sys
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
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# Known sections on www.ndrc.gov.cn
# ---------------------------------------------------------------------------
# Key: URL sub-path
# Value: display label
SECTIONS = {
    "xwdt/xwfb/": "新闻发布",
    "xwdt/tzgg/": "通知公告",
    "fggz/": "发展改革工作",
    "wsdwhfz/": "委属单位话发改",
}


def parse_args():
    parser = argparse.ArgumentParser(description="NDRC crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL (e.g. https://www.ndrc.gov.cn/xwdt/xwfb/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None, help="Comma-separated list of sections to crawl (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
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
        if client is not None:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text if resp.text else None
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
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _abs_url(href, base):
    """Resolve a (possibly relative) href against base."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base, href)


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
# Listing-page parsing
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://www.ndrc.gov.cn"


def _get_listing_url(base_url, section_path):
    """Build the listing URL for a section."""
    if base_url.endswith("/"):
        base_url = base_url.rstrip("/")
    # Extract scheme+host from base_url, then append section path
    m = re.match(r"(https?://[^/]+)", base_url)
    site_root = m.group(1) if m else _SITE_ROOT
    return f"{site_root}/{section_path}"


def _extract_articles_from_listing(html, section_label, listing_url=None):
    """Extract article entries from a listing page.

    Structure:
      <li>
        <a href="./202604/t20260427_1404911.html">TITLE</a>
        <span>2026/04/27</span>
      </li>

    Args:
        html: Listing page HTML.
        section_label: Display name for the section.
        listing_url: The URL of the listing page.

    Returns list[dict] with keys: title, url, date (datetime or None).
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    base_for_url = listing_url or _SITE_ROOT
    # Ensure base_for_url ends with / so urljoin works correctly with ./ relative links
    if not base_for_url.endswith("/"):
        base_for_url += "/"

    # Find all li items with article links
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        # NDRC article URLs look like: ./YYYYMM/tYYYYMMDD_NNNNNNN.html
        if "t20" not in href or ".html" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        url = _abs_url(href, base_for_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Date from <span> sibling
        dt = None
        span = li.find("span")
        if span:
            dt = _parse_date(span.get_text(strip=True))

        # Fallback: extract date from URL path (YYYYMM)
        if not dt:
            m = re.search(r"/(\d{4})(\d{2})/t\d{14}", href)
            if m:
                try:
                    dt = datetime(int(m.group(1)), int(m.group(2)), 1)
                except (ValueError, OverflowError):
                    pass

        # Filter pre-2023
        if dt and dt.year < 2023:
            continue

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
    """Fetch and parse an ndrc.gov.cn article detail page.

    Metadata from <meta> tags:
      <meta name="ArticleTitle" content="...">
      <meta name="PubDate" content="YYYY-MM-DD HH:MM:SS">
      <meta name="ContentSource" content="...">

    Title fallback: <h2>TITLE</h2>
    Date fallback: <div class="time">发布时间：YYYY/MM/DD</div>
    Content: <div class="TRS_Editor"><div>...</div></div>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url, client=client)
    if not html:
        return "", [], {}

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
        h2 = soup.find("h2")
        if h2:
            meta_title = h2.get_text(strip=True)

    # Fallback: <div class="time"> for date
    if not meta_date:
        time_div = soup.find("div", class_="time")
        if time_div:
            raw = time_div.get_text(strip=True)
            raw = re.sub(r"^(发布时间|发布日期)[：:]", "", raw).strip()
            meta_date = raw

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Parse main content from <div class="TRS_Editor"> --
    content_div = soup.find("div", class_="TRS_Editor")
    if not content_div:
        logging.warning("No TRS_Editor div found in %s", url)
        return "", [], metadata

    markdown_text = _content_to_markdown(content_div)

    return markdown_text, [], metadata


def _content_to_markdown(content_div):
    """Convert the NDRC content div to Markdown text.

    NDRC uses <div> tags (not <p>) with Chinese-space indentation.
    Also handles <p> tags and embedded <img> elements.
    """
    lines = []

    for el in content_div.find_all(["div", "p", "h1", "h2", "h3", "h4",
                                     "li", "blockquote", "pre", "img"]):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                # Skip pixel/icon images
                if "r75" in src or "s_r" in src:
                    continue
                alt_text = f" ({alt})" if alt else ""
                lines.append(f"![{alt_text}]({src})")
            continue

        text = el.get_text(strip=True)
        if not text:
            continue

        if tn == "h1":
            lines.append(f"\n# {text}\n")
        elif tn == "h2":
            lines.append(f"\n## {text}\n")
        elif tn == "h3":
            lines.append(f"\n### {text}\n")
        elif tn in ("h4",):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        elif tn in ("div", "p"):
            lines.append(text)

    # Remove leading empty lines
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


def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[NDRC] Starting National Development & Reform Commission crawler")
    _safe_print(f"[NDRC] Target URL: {args.target_url}")
    _safe_print(f"[NDRC] Task name: {args.task_name}")
    _safe_print(f"[NDRC] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== NDRC crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        if args.section:
            selected = {k: v for k, v in SECTIONS.items() if k in args.section.split(",")}
            if not selected:
                _safe_print(f"[NDRC] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[NDRC] Sections to crawl: {len(selected)}")
        for path, label in selected.items():
            _safe_print(f"         - {label} ({path})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[NDRC] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[NDRC] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # 1/5 + 2/5: Crawl listings and collect articles
        _safe_print(f"[NDRC] Step 1/5: Crawling listing pages...")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for section_path, section_label in selected.items():
            list_url = _get_listing_url(args.target_url, section_path)
            _safe_print(f"[NDRC]   Section '{section_label}': {list_url}")
            sys.stdout.flush()

            html = _fetch(list_url, client=client)
            if not html:
                logging.warning("Failed to fetch listing page %s", list_url)
                continue

            arts = _extract_articles_from_listing(html, section_label, list_url)
            logging.info("Section %s: %d articles", section_label, len(arts))
            section_stats[section_label] = len(arts)
            all_articles.extend(arts)

        _safe_print(f"[NDRC] Step 1/5: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[NDRC] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"[NDRC] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            _safe_print(f"\n[NDRC] Skipping {len(all_articles) - len(new_articles)} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[NDRC] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # 3/5 + 4/5: Fetch detail pages
        _safe_print(f"\n[NDRC] Step 2/5: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[NDRC] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"], client=client)
            if not content:
                _safe_print(f"[NDRC]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[NDRC]   -> {len(content)} chars")
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
            _safe_print(f"[NDRC] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[NDRC] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # 5/5: Save + upload
        _safe_print(f"[NDRC] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[NDRC] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        _safe_print(f"[NDRC] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[NDRC] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[NDRC] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)
    finally:
        client.stop()


if __name__ == "__main__":
    main()
