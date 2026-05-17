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
Dedicated web crawler for www.mohurd.gov.cn (住房和城乡建设部).

Crawls multiple content sections (建设要闻, 领导动态, 地方信息, 政策发布),
extracts article content from detail pages, converts to Markdown, and uploads
the result to a RAGFlow knowledge base.

This site uses a CMS with an internal API for listing pages (instead of
server-rendered HTML). Articles are loaded from:
  /api-gateway/jpaas-publish-server/front/page/build/unit

Article URL pattern:
  /{section}/art/{YYYY}/art_{hash}.html

Content container:
  <div class="editor-content"><p>...</p></div>

Usage (typically spawned by task_executor):
    python mohurd_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.mohurd.gov.cn/xinwen/gzdt/ \
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
# Known sections on www.mohurd.gov.cn
# ---------------------------------------------------------------------------
# Key: display label
# Value: (URL_path, CMS_pageId)
SECTIONS = {
    "建设要闻": ("xinwen/gzdt/", "919e942639b5477d96e4c97471c61d9f"),
    "领导动态": ("xinwen/jsyw/", "f317736c953f43b893310d52b48aadaa"),
    "地方信息": ("xinwen/dfxx/", "13f214f3a89147ea859e47aab5f60d72"),
    "政策发布": ("zhengcefabu/", "8soTiiRMg3k87m5e2CQit"),
}

_CMS_API = "https://www.mohurd.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit"
_SITE_ROOT = "https://www.mohurd.gov.cn"


def parse_args():
    parser = argparse.ArgumentParser(description="MOHURD crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL (e.g. https://www.mohurd.gov.cn/)")
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

_client: PlaywrightHttpClient | None = None


def _fetch(url, timeout=30):
    try:
        resp = _client.get(url, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
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
# Listing — via CMS internal API
# ---------------------------------------------------------------------------

def _fetch_listing(page_id):
    """Fetch article listing from the MOHURD CMS API.

    Returns list[dict] with keys: title, url, date (datetime or None).
    """
    params = {
        "parseType": "bulidstatic",
        "webId": "86ca573ec4df405db627fdc2493677f3",
        "tplSetId": "fc259c381af3496d85e61997ea7771cb",
        "pageType": "column",
        "editType": "null",
        "pageId": page_id,
    }

    try:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        api_url = f"{_CMS_API}?{qs}"
        resp = _client.fetch_get(
            api_url, timeout=15,
            headers={**_HEADERS, "Referer": "https://www.mohurd.gov.cn/"}
        )
        resp.raise_for_status()
        data = resp.json()
        html = data.get("data", {}).get("html", "")
    except Exception as e:
        logging.error("Failed to fetch CMS listing for pageId %s: %s", page_id, e)
        return []

    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        title = (a.get("title") or a.text or "").strip()
        if not title or len(title) < 4:
            continue
        url = _abs_url(href, _SITE_ROOT)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Date from <span class="date-info">
        dt = None
        span = li.find("span", class_="date-info")
        if span:
            dt = _parse_date(span.get_text(strip=True))

        if dt and dt.year < 2023:
            continue

        articles.append({
            "title": title,
            "url": url,
            "date": dt,
            "section": "",
        })

    return articles


# ---------------------------------------------------------------------------
# Article detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(url):
    """Fetch and parse a mohurd.gov.cn article detail page.

    Metadata from <meta> tags:
      <meta name="ArticleTitle" content="...">
      <meta name="PubDate" content="YYYY-MM-DD HH:MM">
      <meta name="ContentSource" content="...">

    Title fallback: <h3>TITLE</h3>
    Content: <div class="editor-content"><p>...</p></div>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _fetch(url)
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

    # Fallback: <h3> for title
    if not meta_title:
        h3 = soup.find("h3")
        if h3:
            meta_title = h3.get_text(strip=True)

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Parse main content from <div class="editor-content"> --
    content_div = soup.find("div", class_="editor-content")
    if not content_div:
        logging.warning("No editor-content div found in %s", url)
        return "", [], metadata

    markdown_text = _content_to_markdown(content_div)

    return markdown_text, [], metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text.

    MOHURD uses <p> tags with text-indent style for paragraphs.
    """
    lines = []
    for el in content_div.find_all(["p", "h1", "h2", "h3", "h4",
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
        elif tn in ("h4",):
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
    global _client
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[MOHURD] Starting Housing & Urban-Rural Development crawler")
    _safe_print(f"[MOHURD] Target URL: {args.target_url}")
    _safe_print(f"[MOHURD] Task name: {args.task_name}")
    _safe_print(f"[MOHURD] Target KB: {args.kb_id}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== MOHURD crawler started for %s ===", args.target_url)

    _client = PlaywrightHttpClient()
    _client.start()
    try:

        if args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in SECTIONS:
                    selected[label] = SECTIONS[label]
            if not selected:
                _safe_print(f"[MOHURD] WARNING: No matching sections found for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[MOHURD] Sections to crawl: {len(selected)}")
        for label, (path, _page_id) in selected.items():
            _safe_print(f"         - {label} ({path})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[MOHURD] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        _safe_print(f"[MOHURD] Already processed: {len(processed_urls)} URLs\n")
        sys.stdout.flush()

        # 1/5 + 2/5: Fetch listings via CMS API and collect articles
        _safe_print(f"[MOHURD] Step 1/5: Fetching listings via CMS API...")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}

        for label, (_path, page_id) in selected.items():
            _safe_print(f"[MOHURD]   Section '{label}': pageId={page_id}")
            sys.stdout.flush()

            arts = _fetch_listing(page_id)
            for a in arts:
                a["section"] = label
            logging.info("Section %s: %d articles", label, len(arts))
            section_stats[label] = len(arts)
            all_articles.extend(arts)

        _safe_print(f"[MOHURD] Step 1/5: Collected {len(all_articles)} total articles across {len(selected)} sections\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print(f"[MOHURD] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"[MOHURD] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        if processed_urls:
            new_articles = [a for a in all_articles if a["url"] not in processed_urls]
            _safe_print(f"\n[MOHURD] Skipping {len(all_articles) - len(new_articles)} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print(f"[MOHURD] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # 3/5 + 4/5: Fetch detail pages
        _safe_print(f"\n[MOHURD] Step 2/5: Fetching {len(all_articles)} article detail pages...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[MOHURD] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["url"])
            if not content:
                _safe_print(f"[MOHURD]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[MOHURD]   -> {len(content)} chars")
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
            _safe_print(f"[MOHURD] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[MOHURD] Detail pages fetched: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # 5/5: Save + upload
        _safe_print(f"[MOHURD] Step 3/5: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[MOHURD] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_urls = [a["url"] for a in all_articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        _safe_print(f"[MOHURD] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print(f"[MOHURD] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[MOHURD] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)
    finally:
        _client.stop()


if __name__ == "__main__":
    main()
