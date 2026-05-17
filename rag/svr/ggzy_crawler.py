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
Dedicated web crawler for www.ggzy.gov.cn (全国公共资源交易平台).

This is a traditional JSP server-rendered portal.  Listing pages use
static HTML with ?pageNum=N pagination.  Article details are served via
POST to /SIC/web/details.po with an id parameter.

Content sections (user-selected):
  - 政策法规文件 (Policy Documents)   — /SIC/web/policyFileList.po
  - 政策法规解读 (Policy Interpretations) — /SIC/web/policyReadList.po

Listing structure:
  <UL class="ul" id="contextId">
    <LI>
      <A href="details.po;jsessionid=XXX?id=UUID">TITLE</A>
      <SPAN>YYYY-MM-DD</SPAN>
    </LI>
    ...
  </UL>

Pagination: ?pageNum=N  (20 items per page)

Detail page (POST /SIC/web/details.po, form body: id=UUID):
  <H4 id="txtCaption">TITLE</H4>
  <SPAN id="txtPublishTime">YYYY-MM-DD</SPAN>
  <DIV id="divContent">... article HTML (p/span/strong) ...</DIV>

Usage (typically spawned by task_executor):
    python ggzy_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.ggzy.gov.cn/ \
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
    "policyFile": ("/SIC/web/policyFileList.po", "政策法规文件"),
    "policyRead": ("/SIC/web/policyReadList.po", "政策法规解读"),
}

_SITE_ROOT = "https://www.ggzy.gov.cn"
_DETAIL_POST_URL = _SITE_ROOT + "/SIC/web/details.po"


def parse_args():
    parser = argparse.ArgumentParser(description="GGZY crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://www.ggzy.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Comma-separated list of section labels (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=365,
                        help="Max age in days for articles (default: 365)")
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
        return raw.decode(enc)
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _post(url, data, timeout=30):
    """POST form-encoded data and return decoded HTML string."""
    try:
        resp = _client.post(url, data=data, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        if not enc or enc.upper() in ("ASCII", "ISO-8859-1"):
            enc = "utf-8"
        return raw.decode(enc)
    except Exception as e:
        logging.error("Failed to POST %s: %s", url, e)
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
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y年%m月%d日"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Listing parsing
# ---------------------------------------------------------------------------

def _extract_article_id(href):
    """Extract the UUID id parameter from a details.po URL."""
    m = re.search(r"[?&]id=([^&\s]+)", href)
    return m.group(1) if m else None


def _extract_articles_from_listing(html, section_label, listing_url):
    """Extract article entries from a ggzy.gov.cn listing page.

    Structure:
      <UL class="ul" id="contextId">
        <LI>
          <A href="details.po;jsessionid=XXX?id=UUID">TITLE</A>
          <SPAN>YYYY-MM-DD</SPAN>
        </LI>
      </UL>

    Returns list[dict] with keys: title, id, date, section.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_ids = set()

    ul = soup.find("ul", class_="ul", id="contextId")
    if not ul:
        return articles

    for li in ul.find_all("li", recursive=True):
        a = li.find("a", href=True)
        if not a:
            continue

        href = a["href"].strip()
        article_id = _extract_article_id(href)
        if not article_id or article_id in seen_ids:
            continue
        seen_ids.add(article_id)

        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if not title or len(title) < 2:
            continue

        dt = None
        span = li.find("span")
        if span:
            dt = _parse_date(span.get_text(strip=True))

        articles.append({
            "title": title,
            "id": article_id,
            "date": dt,
            "section": section_label,
        })

    return articles


# ---------------------------------------------------------------------------
# Article detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(article_id):
    """Fetch and parse a ggzy.gov.cn article detail page via POST.

    Detail structure (POST /SIC/web/details.po, body: id=UUID):
      <H4 id="txtCaption">TITLE</H4>
      <SPAN id="txtPublishTime">YYYY-MM-DD</SPAN>
      <DIV id="divContent">... HTML ...</DIV>

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    html = _post(_DETAIL_POST_URL, {"id": article_id})
    if not html:
        return "", [], {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract metadata --
    meta_title = ""
    caption = soup.find("h4", id="txtCaption")
    if caption:
        meta_title = caption.get_text(strip=True)

    meta_date = ""
    time_span = soup.find("span", id="txtPublishTime")
    if time_span:
        meta_date = time_span.get_text(strip=True)

    metadata = {"title": meta_title, "date": meta_date}

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Parse main content --
    content_div = soup.find("div", id="divContent")
    if not content_div:
        logging.warning("No divContent found for article %s", article_id)
        return "", [], metadata

    resources = []
    markdown_text = _content_to_markdown(content_div)
    return markdown_text, resources, metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text."""
    _TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "img",
             "div", "section",
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
    return {"processed_ids": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed IDs)",
                 len(state.get("processed_ids", [])))


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
            logging.error("Failed to queue parsing for document %s: %s",
                          doc["id"], e)
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
    _safe_print("[GGZY] Starting National Public Resources Trading Platform crawler")
    _safe_print(f"[GGZY] Target URL: {args.target_url}")
    _safe_print(f"[GGZY] Task name: {args.task_name}")
    _safe_print(f"[GGZY] Target KB: {args.kb_id}")
    _safe_print(f"[GGZY] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[GGZY] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== GGZY crawler started for %s ===", args.target_url)

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
                _safe_print(f"[GGZY] No matching sections for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[GGZY] Sections to crawl: {len(selected)}")
        for label, (url_path, display) in selected.items():
            _safe_print(f"         - {display} ({url_path})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[GGZY] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[GGZY] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # Step 1: Crawl listing pages
        _safe_print("[GGZY] Step 1/4: Crawling listing pages...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}
        cutoff = datetime.now() - timedelta(days=args.max_days)

        for section_id, (url_path, display_label) in selected.items():
            _safe_print(f"[GGZY]   Section '{display_label}':")
            sys.stdout.flush()

            section_articles = []
            page_num = 1

            while True:
                if args.max_articles and len(section_articles) >= args.max_articles:
                    break

                list_url = f"{_SITE_ROOT}{url_path}?pageNum={page_num}"
                _safe_print(f"[GGZY]     Page {page_num}: {list_url}")
                sys.stdout.flush()

                html = _fetch(list_url)
                if not html:
                    logging.warning("Failed to fetch listing page %s", list_url)
                    break

                arts = _extract_articles_from_listing(html, display_label, list_url)
                if not arts:
                    _safe_print("[GGZY]     -> No more articles found, stopping")
                    sys.stdout.flush()
                    break

                date_cutoff_hit = False
                for art in arts:
                    if args.max_articles and len(section_articles) >= args.max_articles:
                        break
                    if art.get("date") and art["date"] < cutoff:
                        date_cutoff_hit = True
                        break
                    section_articles.append(art)

                if date_cutoff_hit:
                    _safe_print("[GGZY]     -> Hit date cutoff, stopping pagination")
                    sys.stdout.flush()
                    break

                if len(arts) < 20:
                    _safe_print(f"[GGZY]     -> Last page ({len(arts)} items)")
                    sys.stdout.flush()
                    break

                page_num += 1
                time.sleep(0.3)

            count = len(section_articles)
            section_stats[display_label] = count
            all_articles.extend(section_articles)

            _safe_print(f"[GGZY]     -> {count} articles\n")
            sys.stdout.flush()

        _safe_print(f"[GGZY] Collected {len(all_articles)} total articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[GGZY] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print("[GGZY] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Deduplicate with state (by article ID)
        if processed_ids:
            new_articles = [a for a in all_articles if a["id"] not in processed_ids]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[GGZY] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print("[GGZY] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # Step 2: Fetch detail pages
        _safe_print(f"\n[GGZY] Step 2/4: Fetching {len(all_articles)} article details...\n")
        sys.stdout.flush()

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[GGZY] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            content, resources, metadata = _fetch_article_content(art["id"])
            if not content:
                _safe_print("[GGZY]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[GGZY]   -> {len(content)} chars")
            sys.stdout.flush()

            article_date_str = ""
            if art.get("date"):
                article_date_str = art["date"].strftime("%Y-%m-%d")
            elif metadata.get("date"):
                article_date_str = metadata["date"]

            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section']}",
                f"**Date:** {article_date_str}",
                "",
                content,
                "",
                "---",
            ]
            md_parts.append("\n".join(lines))
            success_count += 1

        if not md_parts:
            _safe_print("[GGZY] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[GGZY] Details: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # Step 3: Save markdown
        _safe_print("[GGZY] Step 3/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[GGZY] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_ids = [a["id"] for a in all_articles]
        if new_ids:
            processed_ids.update(new_ids)
            _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # Step 4: Upload to KB
        _safe_print(f"[GGZY] Step 4/4: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print("[GGZY] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[GGZY] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)
    finally:
        _client.stop()


if __name__ == "__main__":
    main()
