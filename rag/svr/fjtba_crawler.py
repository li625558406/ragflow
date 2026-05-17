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
Dedicated web crawler for fjtba.com (福建省建筑业协会).

Crawls http://www.fjtba.com/ — a traditional ASP.NET website.
Extracts article listings from .mid_content sections, fetches each
article's content via the internal AJAX API, converts to Markdown,
and uploads the result to a RAGFlow knowledge base.  Images and file
links embedded in the content are kept as Markdown references and
will be processed by the KB's native document parser.

Usage (typically spawned by task_executor):
    python fjtba_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url <URL> \\
        --kb-id <KB_ID> \\
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

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


def parse_args():
    parser = argparse.ArgumentParser(description="FJTBA crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True, help="Homepage URL to crawl")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    # Vision model for image OCR
    parser.add_argument("--llm-id", default=None, help="LLM factory name (e.g. OpenAI)")
    parser.add_argument("--llm-model", default=None, help="Vision model name for image OCR (e.g. gpt-4o)")
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


def _abs_url(href, base):
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return urljoin(base, href)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y年%m月%d日",
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


# ---------------------------------------------------------------------------
# Article extraction from fjtba.com homepage
# ---------------------------------------------------------------------------
#
# fjtba.com is an ASP.NET website whose homepage lists articles under
# several category sections (<div class="mid_content"> …).  Each section
# contains <div class="top_new"> article cards; each holds an <a> linking
# to the detail page and a <span class="date"> with the date.
#
# Detail-page URL pattern:
#   /PortalPage/ISDInfo.aspx?type=<CATEGORY>&id=<ARTICLE_ID>&isContent=1
#
# IMPORTANT: The actual article content is NOT in the ASP.NET page HTML.
# Instead it is loaded via an AJAX POST to DataHandler.ashx with
# OPtype=GetArticleContent&id=<ARTICLE_ID>.  See fetch_article_content_via_api().
# ---------------------------------------------------------------------------

def _extract_article_id(url):
    """Extract the numeric article ID from a fjtba.com detail URL."""
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def extract_fjtba_articles(html, base_url):
    """Extract article entries from fjtba.com homepage HTML.

    Returns list[dict] with keys: title, url, article_id, date, etc.
    Articles published before 2023 are filtered out.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen_urls = set()

    mid_sections = soup.find_all("div", class_="mid_content")

    print(f"[FJTBA] Found {len(mid_sections)} .mid_content sections")
    sys.stdout.flush()

    for section in mid_sections:
        # Category heading lives in previous sibling <div class="tab_top img_jddt">
        prev = section.find_previous_sibling()
        category = ""
        if prev:
            raw = prev.get_text(strip=True)
            category = re.sub(r"更多[>》].*$", "", raw).strip()

        for article_div in section.find_all("div", class_="top_new"):
            a = article_div.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            title = (a.get("title") or a.text or "").strip()
            title = title.lstrip("(")
            if not title or len(title) < 2:
                continue

            url = _abs_url(href, base_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Date in <span class="date">
            date_text = ""
            date_span = article_div.find("span", class_=re.compile(r"date|time", re.I))
            if date_span:
                date_text = date_span.get_text(strip=True)
            else:
                all_text = article_div.get_text()
                m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", all_text)
                if m:
                    date_text = m.group(1)

            dt = _parse_date(date_text)

            if dt and dt.year < 2023:
                logging.info("Skipped %s (date: %s)", title[:60], dt.date())
                continue

            article_id = _extract_article_id(url)

            articles.append({
                "title": title,
                "url": url,
                "article_id": article_id,
                "date": dt,
                "date_str": date_text,
                "category": category,
            })

    logging.info("Found %d unique articles on fjtba.com", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Article content via AJAX API (DataHandler.ashx)
# ---------------------------------------------------------------------------
#
# fjtba.com loads article content asynchronously via a POST to:
#   /PortalPage/AjaxHandler/DataHandler.ashx
# with form-encoded body: OPtype=GetArticleContent&id=<ARTICLE_ID>
#
# The response is a JSON array where the first element contains:
#   TITLE, CONTENTS (HTML), SOURCES, TM, READ_NUM
# ---------------------------------------------------------------------------

_API_URL = "http://www.fjtba.com/PortalPage/AjaxHandler/DataHandler.ashx"
_API_HEADERS = {
    "User-Agent": _HEADERS["User-Agent"],
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def fetch_article_content_via_api(article_id, base_url, client=None):
    """Fetch article content from the fjtba.com internal API.

    Images embedded in the article are OCR'd using RAGFlow's built-in
    local engine and the extracted text is inserted alongside the image
    reference in a ``> `` blockquote.

    Returns (markdown_text, list_of_resource_dicts).
    Each resource has keys: type ("image"|"file"), src, caption.
    Returns ("", []) on failure.
    """
    try:
        if client:
            resp = client.post(
                _API_URL,
                data={"OPtype": "GetArticleContent", "id": article_id},
                headers=_API_HEADERS,
                timeout=30,
            )
        else:
            resp = requests.post(
                _API_URL,
                data={"OPtype": "GetArticleContent", "id": article_id},
                headers=_API_HEADERS,
                timeout=30,
            )
        resp.raise_for_status()
    except Exception as e:
        logging.error("API fetch failed for article %s: %s", article_id, e)
        return "", []

    try:
        import json
        items = json.loads(resp.text)
        if not items or not isinstance(items, list):
            return "", []
        item = items[0]
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        logging.error("API response parse failed for article %s: %s", article_id, e)
        return "", []

    content_html = item.get("CONTENTS", "")
    if not content_html or not content_html.strip():
        return "", []

    content, resources = _html_to_markdown(content_html, base_url)

    # Enrich content with image references + OCR text
    if resources:
        extra = []
        for r in resources:
            if r["type"] == "image":
                cap = f" *({r['caption']})*" if r.get("caption") else ""
                img_line = f"![]({r['src']}){cap}"
                extra.append(img_line)
                ocr_text = _ocr_image(r["src"], client=client)
                if ocr_text:
                    r["ocr_text"] = ocr_text
                    extra.append(f"\n> {ocr_text}\n")
            elif r["type"] == "file":
                cap = f" ({r['caption']})" if r.get("caption") else ""
                extra.append(f"[{r['caption']}]({r['src']}){cap}")
        if extra:
            content += "\n\n" + "\n\n".join(extra)

    return content, resources


# ---------------------------------------------------------------------------
# HTML → Markdown conversion (shared logic)
# ---------------------------------------------------------------------------

def _html_to_markdown(html_content, base_url):
    """Convert HTML to a rough Markdown string.

    Returns (markdown_text, list_of_resources) where each resource is a dict:
        {"type": "image"|"file", "src": str, "caption": str}
    Image and file URLs are kept as Markdown references so the KB's native
    document parser can process them (OCR, layout analysis, chunking, etc.).
    """
    if not html_content or not html_content.strip():
        return "", []

    soup = BeautifulSoup(html_content, "lxml")

    # Strip clutter
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Collect embedded resources: images + downloadable files
    resources = []
    seen_srcs = set()

    _FILE_EXT = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv)", re.I)

    # Images
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        src = _abs_url(src, base_url)
        if not src.startswith(("http://", "https://")):
            continue
        if src in seen_srcs:
            continue
        seen_srcs.add(src)
        parent = img.find_parent(["figure", "a"])
        caption = ""
        if parent:
            cap_tag = parent.find(["figcaption", "span", "em"])
            if cap_tag:
                caption = cap_tag.get_text(strip=True)
        resources.append({"type": "image", "src": src, "caption": caption})

    # Downloadable file links embedded in <a> tags
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not _FILE_EXT.search(href):
            continue
        src = _abs_url(href, base_url)
        if src in seen_srcs:
            continue
        seen_srcs.add(src)
        caption = a.get_text(strip=True) or os.path.basename(href)
        resources.append({"type": "file", "src": src, "caption": caption})

    # Convert block elements to rough markdown
    lines = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"]):
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
        else:
            lines.append(text)

    # Also grab <table> elements (common in fjtba.com government notices)
    for table in soup.find_all("table"):
        md_table = _table_to_markdown(table)
        if md_table:
            lines.append("")
            lines.append(md_table)
            lines.append("")

    return "\n\n".join(lines), resources


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

    # Determine column count
    n_cols = max(len(r) for r in rows)

    # Build markdown table
    md = []
    # Header row
    md.append("| " + " | ".join(r.ljust(15) for r in rows[0]) + " |")
    # Separator
    md.append("| " + " | ".join(["---"] * n_cols) + " |")
    # Data rows
    for row in rows[1:]:
        padded = list(row) + [""] * (n_cols - len(row))
        md.append("| " + " | ".join(r.ljust(15) for r in padded) + " |")

    return "\n".join(md)


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
# Local OCR (built-in RAGFlow engine, no external API needed)
# ---------------------------------------------------------------------------

def _ocr_image(image_url, client=None):
    """Download an image and run RAGFlow's built-in local OCR on it.

    Uses the same engine as the ``picture`` parser mode — works entirely
    offline, no API calls needed.  Returns extracted text (str) or None
    if the engine produced no output or an error occurred.
    """
    try:
        if client:
            resp = client.get(image_url, headers=_HEADERS, timeout=30)
        else:
            resp = requests.get(image_url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        image_bytes = resp.content
    except Exception as e:
        logging.warning("Failed to download image %s: %s", image_url, e)
        return None

    try:
        import io
        import numpy as np
        from PIL import Image
        from deepdoc.vision import OCR

        ocr_engine = OCR()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        bxs = ocr_engine(np.array(img))
        txt = "\n".join([t[0] for _, t in bxs if t[0]]).strip()
        if not txt:
            return None
        return txt
    except Exception as e:
        logging.warning("Local OCR failed for %s: %s", image_url, e)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    print(f"\n{'='*60}")
    print(f"[FJTBA] Starting FJTBA crawler")
    print(f"[FJTBA] Target URL: {args.target_url}")
    print(f"[FJTBA] Task name: {args.task_name}")
    print(f"[FJTBA] Target KB: {args.kb_id}")
    print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== FJTBA crawler started for %s ===", args.target_url)
    print(f"[FJTBA] Local OCR engine active — images will be OCR'd for text content")
    sys.stdout.flush()

    client = PlaywrightHttpClient()
    client.start()
    try:

        # 1/4: Fetch homepage
        print(f"[FJTBA] Step 1/4: Fetching homepage...")
        sys.stdout.flush()
        html = _fetch(args.target_url, client=client)
        if not html:
            print(f"[FJTBA] ERROR: Homepage fetch failed, exiting")
            sys.stdout.flush()
            logging.error("Homepage fetch failed, exiting")
            sys.exit(1)
        print(f"[FJTBA] Step 1/4: Homepage fetched successfully ({len(html)} bytes)\n")
        sys.stdout.flush()

        # 2/4: Extract articles (filters < 2023)
        print(f"[FJTBA] Step 2/4: Extracting articles from .mid_content sections...")
        sys.stdout.flush()
        articles = extract_fjtba_articles(html, args.target_url)
        print(f"[FJTBA] Step 2/4: Found {len(articles)} articles after filtering\n")
        sys.stdout.flush()
        if not articles:
            print(f"[FJTBA] No articles found, exiting")
            sys.stdout.flush()
            logging.warning("No articles found, exiting")
            sys.exit(0)

        # Output directory
        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT,
            "rag",
            args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        print(f"[FJTBA] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # Incremental state
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        if processed_urls:
            new_articles = [a for a in articles if a["url"] not in processed_urls]
            skipped = len(articles) - len(new_articles)
            print(f"[FJTBA] Skipping {skipped} already-processed article(s)\n")
            sys.stdout.flush()
            articles = new_articles

        # 3/4: Process each article (API → markdown with images & file links)
        print(f"[FJTBA] Step 3/4: Processing {len(articles)} articles...\n")
        sys.stdout.flush()
        md_parts = []
        for idx, art in enumerate(articles, 1):
            print(f"[FJTBA] Article [{idx}/{len(articles)}]: {art['title']}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s", idx, len(articles), art["title"])

            if not art.get("article_id"):
                print(f"[FJTBA]   -> No article ID found in URL, skipped")
                sys.stdout.flush()
                logging.warning("No article ID for %s, skipped", art["title"])
                continue

            content, resources = fetch_article_content_via_api(
                art["article_id"], art["url"], client=client,
            )
            if not content:
                print(f"[FJTBA]   -> Empty content, skipped")
                sys.stdout.flush()
                logging.warning("Empty content for %s, skipped", art["title"])
                continue

            n_images = sum(1 for r in resources if r.get("type") == "image")
            n_files = sum(1 for r in resources if r.get("type") == "file")
            n_ocr = sum(1 for r in resources if r.get("type") == "image" and r.get("ocr_text"))
            print(f"[FJTBA]   -> Content: {len(content)} chars, {n_images} images ({n_ocr} OCR'd), {n_files} files")
            sys.stdout.flush()

            # Build article markdown section
            article_date_str = art["date"].strftime("%Y-%m-%d") if art.get("date") else art.get("date_str", "")
            category_str = f" [{art['category']}]" if art.get("category") else ""
            lines = [
                f"# {art['title']}{category_str}",
                f"**Date:** {article_date_str}",
                f"**Source:** {art['url']}",
                "",
                content,
            ]

            lines.append("")
            lines.append("---")
            md_parts.append("\n".join(lines))

        if not md_parts:
            print(f"[FJTBA] No articles processed successfully, exiting")
            sys.stdout.flush()
            logging.warning("No articles processed successfully")
            sys.exit(0)

        # 4/4: Save combined markdown + upload to KB
        print(f"[FJTBA] Step 4/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        print(f"[FJTBA] Step 4/4: Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Save state for incremental crawling
        new_urls = [a["url"] for a in articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        # Upload to KB
        print(f"[FJTBA] Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s …", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            print(f"[FJTBA] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            print(f"[FJTBA] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

        print(f"{'='*60}")
        print(f"[FJTBA] All done! Task completed successfully.")
        print(f"{'='*60}")
        sys.stdout.flush()
        logging.info("=== FJTBA crawler finished successfully ===")

    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "fjtba_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
