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
Dedicated web crawler for jsj.zhangzhou.gov.cn (漳州市住房和城乡建设局).

Crawls multiple content sections (工作动态, 通知公告, 政务公开), extracts
article content from detail pages, converts to Markdown, and uploads the
result to a RAGFlow knowledge base.

Article URL pattern:
  /cms/html/zzszfhcxjsj/YYYY-MM-DD/<digits>.html

Standard listing page structure (<ul id="resources">):
  <ul id="resources">
    <li><a href="...">TITLE</a><span>YYYY-MM-DD</span></li>
    ...

政务公开 (zwgk) uses tab panels:
  <div id="tabstripBody_...">
    <li><a href="...">TITLE</a><span>YYYY-MM-DD</span></li>
    ...

Content container:
  <div class="content" id="Content"><p>...</p></div>

Usage (typically spawned by task_executor):
    python jsj_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://jsj.zhangzhou.gov.cn/cms/html/zzszfhcxjsj/index.html \
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
from urllib.parse import urljoin, urlparse

import requests  # fallback only
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


# ---------------------------------------------------------------------------
# Known sections on jsj.zhangzhou.gov.cn
# ---------------------------------------------------------------------------
SECTIONS = {
    "gzdt": "工作动态",
    "tzgg3": "通知公告",
    "zwgk": "政务公开",
}

# ---------------------------------------------------------------------------
# ZWGN tab IDs for article extraction
# ---------------------------------------------------------------------------
# Each tab ID corresponds to a <div id="tabstripBody_{tab_id}_{index}">
ZWGK_TABS = {
    "1604150666": "建设动态/通知公告/建设文件",
    "1403827949": "财政资金/规划信息/规划计划",
    "1304611683": "政策法规/政策解读/资质管理",
    "71504435": "城市建设/城市管理/安全生产",
    "594588935": "科技教育/数据建设/统计信息",
    "908226803": "重点项目/电子监察",
}


def parse_args():
    parser = argparse.ArgumentParser(description="JSJ crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL to crawl")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None, help="Comma-separated list of sections to crawl (default: all)")
    # Kept for backward compat — unused
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


def _fetch(url, timeout=30, client: PlaywrightHttpClient = None):
    try:
        if client is not None:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            raw = resp.content
        else:
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
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Listing-page parsing
# ---------------------------------------------------------------------------

_SITE_PATH = "zzszfhcxjsj"


def _get_listing_url(base_url, section_path):
    """Build the listing URL for a section."""
    parsed = urlparse(base_url)
    site_root = f"{parsed.scheme}://{parsed.netloc}"
    return f"{site_root}/cms/html/{_SITE_PATH}/{section_path}/index.html"


def _extract_articles_from_listing(html, section_label, listing_url=None):
    """Extract article entries from a standard <ul id="resources"> listing page.

    Structure:
      <ul id="resources">
        <li><a href="..." target="_blank">TITLE</a><span>YYYY-MM-DD</span></li>
        ...

    Args:
        html: Listing page HTML.
        section_label: Display name for the section.
        listing_url: The URL of the listing page (used as base for resolving
            relative article URLs).

    Returns list[dict] with keys: title, url, date (datetime or None).
    Articles published before 2023 are filtered out.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    base_for_url = listing_url or f"https://jsj.zhangzhou.gov.cn"

    # Find the <ul id="resources"> container
    resources_ul = soup.find("ul", id="resources")
    if not resources_ul:
        resources_ul = soup.find("ul", id=re.compile(r"resources", re.I))
    if not resources_ul:
        logging.warning("No <ul id='resources'> found in listing page")
        return articles

    for li in resources_ul.find_all("li", recursive=True):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        title = (a.get("title") or a.text or "").strip()
        if not title or len(title) < 2:
            continue
        url = _abs_url(href, base_for_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Date from <span> sibling / child
        dt = None
        span = li.find("span")
        if span:
            dt = _parse_date(span.get_text(strip=True))

        # Fallback: try to extract date from text content
        if not dt:
            all_text = li.get_text()
            m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", all_text)
            if m:
                dt = _parse_date(m.group(1))

        # Filter pre-2023
        if dt and dt.year < 2023:
            logging.debug("Skipped %s (date: %s)", title[:60], dt.date())
            continue

        articles.append({
            "title": title,
            "url": url,
            "date": dt,
            "section": section_label,
        })

    return articles


def _extract_articles_from_zwgk(html, section_label, listing_url=None):
    """Extract articles from 政务公开 tab panels.

    Structure:
      <div id="tabstripBody_{tab_id}_0">
        <li><a href="..." target="_blank">TITLE</a><span>YYYY-MM-DD</span></li>
        ...
      <div id="tabstripBody_{tab_id}_1">...

    Args:
        html: ZWGK listing page HTML.
        section_label: Display name for the section.
        listing_url: The URL of the listing page (used as base for resolving
            relative article URLs).

    Returns list[dict] with keys: title, url, date (datetime or None).
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    base_for_url = listing_url or f"https://jsj.zhangzhou.gov.cn"

    # Find all tabstripBody divs
    for tab_id in ZWGK_TABS:
        for i in range(20):  # up to 20 tab pages per group
            div = soup.find("div", id=f"tabstripBody_{tab_id}_{i}")
            if not div:
                break
            for li in div.find_all("li", recursive=True):
                a = li.find("a", href=True)
                if not a:
                    continue
                href = a["href"].strip()
                title = (a.get("title") or a.text or "").strip()
                if not title or len(title) < 2:
                    continue
                url = _abs_url(href, base_for_url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Date from <span>
                dt = None
                span = li.find("span")
                if span:
                    dt = _parse_date(span.get_text(strip=True))
                if not dt:
                    all_text = li.get_text()
                    m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", all_text)
                    if m:
                        dt = _parse_date(m.group(1))

                if dt and dt.year < 2023:
                    continue

                articles.append({
                    "title": title,
                    "url": url,
                    "date": dt,
                    "section": f"{section_label}/{ZWGK_TABS[tab_id]}",
                })

    return articles


# ---------------------------------------------------------------------------
# Article detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(url, client: PlaywrightHttpClient = None):
    """Fetch and parse a jsj.zhangzhou.gov.cn article detail page.

    Title:  <div class="top-title1">TITLE</div>
    Date:   <span class="rq">日期：YYYY-MM-DD</span>
    Source: <span class="ly">来源：xxx</span>
    Content: <div class="content" id="Content"><p>...</p></div>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url, client=client)
    if not html:
        return "", [], {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract title --
    title = ""
    title_div = soup.find("div", class_="top-title1")
    if title_div:
        title = title_div.get_text(strip=True)

    # Fallback: meta tags
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

    # -- Extract date from span.rq --
    date_text = ""
    rq_span = soup.find("span", class_="rq")
    if rq_span:
        raw = rq_span.get_text(strip=True)
        # Strip prefix like "日期：" or "发布时间："
        raw = re.sub(r"^(日期|发布时间|发布日期)[：:]", "", raw).strip()
        date_text = raw
        if not meta_date:
            meta_date = raw

    # -- Extract source from span.ly --
    source_text = ""
    ly_span = soup.find("span", class_="ly")
    if ly_span:
        raw = ly_span.get_text(strip=True)
        raw = re.sub(r"^(来源)[：:]", "", raw).strip()
        source_text = raw
        if not meta_source:
            meta_source = raw

    metadata = {
        "title": title or meta_title,
        "date": date_text or meta_date,
        "source": source_text or meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Collect resources (file attachments) --
    resources = []
    seen_srcs = set()
    attach_span = soup.find("span", id="attachments")
    if attach_span:
        for a in attach_span.find_all("a", href=True):
            href = a["href"].strip()
            if href and href not in seen_srcs:
                seen_srcs.add(href)
                resources.append({
                    "type": "file",
                    "url": _abs_url(href, url),
                    "name": a.get_text(strip=True) or os.path.basename(href),
                })

    # -- Parse main content from <div id="Content"> --
    content_div = soup.find("div", class_="content", id="Content")
    if not content_div:
        content_div = soup.find("div", id="Content")
    if not content_div:
        logging.warning("No content div found in %s", url)
        return "", resources, metadata

    markdown_text = _content_to_markdown(content_div)

    return markdown_text, resources, metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text.

    Content consists of <p> tags with plain text (no tables, no images).
    """
    lines = []
    for el in content_div.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                     "li", "blockquote", "pre"]):
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
        elif tn == "p":
            lines.append(text)

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
    """Print with GBK fallback for Windows terminals."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def main():
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[JSJ] Starting Zhangzhou Housing & Construction crawler")
    _safe_print(f"[JSJ] Target URL: {args.target_url}")
    _safe_print(f"[JSJ] Task name: {args.task_name}")
    _safe_print(f"[JSJ] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== JSJ crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        # Determine which sections to crawl
        if args.section:
            selected = {k: v for k, v in SECTIONS.items() if k in args.section.split(",")}
            if not selected:
                _safe_print(f"[JSJ] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[JSJ] Sections to crawl: {len(selected)}")
        for path, label in selected.items():
            _safe_print(f"         - {label} ({path})")
        sys.stdout.flush()

        # Output directory
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[JSJ] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Incremental state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[JSJ] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # Steps:
        # 1/5: Crawl listing pages for each section
        # 2/5: Collect all article URLs (filter pre-2023)
        # 3/5: Filter against state
        # 4/5: Fetch detail pages, convert to markdown
        # 5/5: Save + upload

        # 1/5 + 2/5: Crawl listings and collect articles
        _safe_print(f"[JSJ] Step 1/5: Crawling listing pages...")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for section_path, section_label in selected.items():
            list_url = _get_listing_url(args.target_url, section_path)
            _safe_print(f"[JSJ]   Section '{section_label}': {list_url}")
            sys.stdout.flush()

            html = _fetch(list_url, client=client)
            if not html:
                logging.warning("Failed to fetch listing page %s", list_url)
                continue

            if section_path == "zwgk":
                arts = _extract_articles_from_zwgk(html, section_label, list_url)
            else:
                arts = _extract_articles_from_listing(html, section_label, list_url)

            logging.info("Section %s: %d articles", section_label, len(arts))
            section_stats[section_label] = len(arts)
            all_articles.extend(arts)

        _safe_print(f"[JSJ] Step 1/5: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[JSJ] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        # Print section stats
        _safe_print(f"[JSJ] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Filter already-processed
        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            _safe_print(f"\n[JSJ] Skipping {len(all_articles) - len(new_articles)} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[JSJ] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # 3/5 + 4/5: Fetch detail pages
        _safe_print(f"\n[JSJ] Step 2/5: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[JSJ] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"], client=client)
            if not content:
                _safe_print(f"[JSJ]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            n_files = sum(1 for r in resources if r.get("type") == "file")
            _safe_print(f"[JSJ]   -> {len(content)} chars, {n_files} files")
            sys.stdout.flush()

            # Build article markdown section
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
            _safe_print(f"[JSJ] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[JSJ] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # 5/5: Save + upload
        _safe_print(f"[JSJ] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[JSJ] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Save state
        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        # Upload to KB
        _safe_print(f"[JSJ] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[JSJ] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[JSJ] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
