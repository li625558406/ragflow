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
Dedicated web crawler for zjt.fujian.gov.cn (福建省住房和城乡建设厅).

Crawls multiple content sections (新闻动态, 通知公告, 最新文件),
extracts article content from detail pages, converts to Markdown, and uploads
the result to a RAGFlow knowledge base.

This site runs on a Knockout.js (Avalon) based CMS with server-side static
fallback rendering. Articles in listing pages are rendered in the HTML even
though the framework uses client-side bindings (ms-controller, ms-visible).

Listing page structure (all sections):
  <div class="gl_list1" ms-controller="list" role="viewlist">
    <div ms-visible="showStatic">
      <ul ms-visible="$showStatic(N)">
        <li>
          <span>YYYY-MM-DD</span>
          <a href="..." target="_blank" title="TITLE">TITLE</a>
        </li>
        ...

Article URL pattern:
  /xxgk/{section}/{YYYYMM}/t{YYYYMMDD}_{digits}.htm

Content container:
  <div class="xl_con1" id="detailCont">
    <div class="TRS_Editor"><p>...</p></div>
  </div>

Usage (typically spawned by task_executor):
    python zjt_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://zjt.fujian.gov.cn/ \
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
# Known sections on zjt.fujian.gov.cn
# ---------------------------------------------------------------------------
# Key: section identifier
# Value: (URL_path_suffix, display_label)
# gzdt uses bmdt/ sub-directory for the full article listing page.
SECTIONS = {
    "gzdt": ("xxgk/gzdt/bmdt/", "新闻动态"),
    "tzgg": ("xxgk/tzgg/", "通知公告"),
    "zxwj": ("xxgk/zxwj/", "最新文件"),
}

_SITE_ROOT = "https://zjt.fujian.gov.cn"


def parse_args():
    parser = argparse.ArgumentParser(description="ZJT crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL (e.g. https://zjt.fujian.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None, help="Comma-separated list of section labels to crawl (default: all)")
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


def _fetch(url, client, timeout=30):
    try:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        if enc.upper() in ("EUC-JP", "EUC-KR", "SHIFT_JIS", "ISO-8859-1"):
            enc = "utf-8"
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
# Listing — gl_list1 static fallback parsing
# ---------------------------------------------------------------------------

def _extract_articles_from_gl_list1(html, section_label, listing_url):
    """Extract article entries from a Knockout.js gl_list1 static listing page.

    All zjt sections use the same pattern inside <div class="gl_list1">:
      <ul ms-visible="$showStatic(N)">
        <li>
          <span>YYYY-MM-DD</span>
          <a href="..." target="_blank" title="TITLE">TITLE</a>
        </li>
        ...

    Args:
        html: Listing page HTML.
        section_label: Display name for the section.
        listing_url: The URL of the listing page (resolves relative URLs).

    Returns list[dict] with keys: title, url, date (datetime or None).
    Articles published before 2023 are filtered out.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    gl_list1 = soup.find("div", class_="gl_list1")
    if not gl_list1:
        # Also try gl_list as fallback
        gl_list1 = soup.find("div", class_="gl_list")
    if not gl_list1:
        logging.warning("No <div class='gl_list1'> or <div class='gl_list'> in listing page")
        return articles

    for ul in gl_list1.find_all("ul", recursive=True):
        for li in ul.find_all("li", recursive=True):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            title = (a.get("title") or a.text or "").strip()
            if not title or len(title) < 2:
                continue

            url = _abs_url(href, listing_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Date from <span>
            dt = None
            span = li.find("span")
            if span:
                dt = _parse_date(span.get_text(strip=True))

            # Fallback: regex in full text
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


# ---------------------------------------------------------------------------
# Article detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(url, client):
    """Fetch and parse a zjt.fujian.gov.cn article detail page.

    Metadata from <meta> tags:
      <meta name="ArticleTitle" content="...">
      <meta name="PubDate" content="YYYY-MM-DD HH:MM">
      <meta name="ContentSource" content="...">

    Title fallback: <h1 class="xl_tit">
    Content: <div class="xl_con1" id="detailCont"><div class="TRS_Editor"><p>...</p></div></div>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url, client)
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

    # Fallback: <h1 class="xl_tit"> for title
    if not meta_title:
        h1 = soup.find("h1", class_="xl_tit")
        if h1:
            meta_title = h1.get_text(strip=True)

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Collect resources (file attachments) --
    resources = []
    seen_srcs = set()
    fjxz_div = soup.find("div", class_="fjxz")
    if fjxz_div:
        for a in fjxz_div.find_all("a", href=True):
            href = a["href"].strip()
            if href and href not in seen_srcs:
                seen_srcs.add(href)
                resources.append({
                    "type": "file",
                    "url": _abs_url(href, url),
                    "name": a.get_text(strip=True) or os.path.basename(href),
                })

    # -- Parse main content from <div class="xl_con1" id="detailCont"> --
    content_div = soup.find("div", class_="xl_con1", id="detailCont")
    if not content_div:
        content_div = soup.find("div", id="detailCont")
    if not content_div:
        logging.warning("No detailCont div found in %s", url)
        return "", resources, metadata

    # TRS_Editor is the actual rich-text container within xl_con1
    trs = content_div.find("div", class_="TRS_Editor")
    if trs:
        markdown_text = _content_to_markdown(trs)
    else:
        markdown_text = _content_to_markdown(content_div)

    return markdown_text, resources, metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text.

    zjt.fujian.gov.cn uses <p> tags with CSS text-indent for paragraphs.
    """
    lines = []
    for el in content_div.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                     "li", "blockquote", "pre", "img"]):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
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
    _safe_print(f"[ZJT] Starting Fujian Housing & Construction crawler")
    _safe_print(f"[ZJT] Target URL: {args.target_url}")
    _safe_print(f"[ZJT] Task name: {args.task_name}")
    _safe_print(f"[ZJT] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== ZJT crawler started for %s ===", args.target_url)

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
                _safe_print(f"[ZJT] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[ZJT] Sections to crawl: {len(selected)}")
        for label, (path, display) in selected.items():
            _safe_print(f"         - {display} ({path})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[ZJT] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[ZJT] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # 1/5 + 2/5: Crawl listings via gl_list1 static fallback
        _safe_print(f"[ZJT] Step 1/5: Crawling listing pages...")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for section_id, (url_path, display_label) in selected.items():
            list_url = f"{_SITE_ROOT}/{url_path}"
            _safe_print(f"[ZJT]   Section '{display_label}': {list_url}")
            sys.stdout.flush()

            html = _fetch(list_url, client)
            if not html:
                logging.warning("Failed to fetch listing page %s", list_url)
                continue

            arts = _extract_articles_from_gl_list1(html, display_label, list_url)
            logging.info("Section %s: %d articles", display_label, len(arts))
            section_stats[display_label] = len(arts)
            all_articles.extend(arts)

        _safe_print(f"[ZJT] Step 1/5: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[ZJT] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"[ZJT] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            _safe_print(f"\n[ZJT] Skipping {len(all_articles) - len(new_articles)} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[ZJT] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # 3/5 + 4/5: Fetch detail pages
        _safe_print(f"\n[ZJT] Step 2/5: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[ZJT] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"], client)
            if not content:
                _safe_print(f"[ZJT]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            n_files = sum(1 for r in resources if r.get("type") == "file")
            _safe_print(f"[ZJT]   -> {len(content)} chars, {n_files} files")
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
            _safe_print(f"[ZJT] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[ZJT] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # 5/5: Save + upload
        _safe_print(f"[ZJT] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[ZJT] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        _safe_print(f"[ZJT] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[ZJT] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[ZJT] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
