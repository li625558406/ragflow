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
Dedicated web crawler for zycg.gov.cn 制度法规 (zdfg) section.

Site characteristics
────────────────────
FreeCMS static-HTML site for "中央国家机关政府采购中心 制度法规".
No JavaScript rendering required — all content is in server-rendered HTML.

  • Listing  →  /freecms/site/zygjjgzfcgzx/zdfg/index.html (page 1)
                 /freecms/site/zygjjgzfcgzx/zdfg/index_N.html (page N+1)
  • Details  →  /freecms/site/zygjjgzfcgzx/zdfg/info/YYYY/XXXXX.html
  • Files    →  /freecms/upload/{uuid}/{date}/{filename}.{doc|docx|pdf|...}

Pagination is driven by pageMe.js on the client side, but the server
simply serves static index_N.html files.  The crawler enumerates pages
until a 404 or empty page is returned.

Attachments are direct-download links embedded in the detail page HTML
(UEditor / FreeCMS attachment markup).  ZIP archives are auto-extracted.

Usage (typically spawned by task_executor):
    python zycg_zdfg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.zycg.gov.cn/ \
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
import urllib.request
import zipfile
from datetime import datetime
from urllib.parse import urljoin

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
_SITE_ROOT = "https://www.zycg.gov.cn"
_LISTING_BASE = "/freecms/site/zygjjgzfcgzx/zdfg"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

# Anti-crawling: random delays between requests
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

_STATE_FILENAME = "_crawler_state.json"

# Attachment extensions to download
_ATTACH_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".7z",
    ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}

# Extensions that RAGFlow can parse with "laws" parser
_LAWS_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    """Random delay between requests to avoid rate limiting."""
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(text, max_len=150):
    """Clean a string for use as a filesystem file/folder name."""
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', " ", name)
    name = name.strip("._ ")
    if not name:
        name = "untitled"
    return name[:max_len]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch_html(url, timeout=30):
    """Fetch a URL and return decoded HTML, or None on failure."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            for enc in ("gbk", "gb2312", "gb18030", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _download_file(url, timeout=60):
    """Download a binary file, return (bytes, filename_from_url) or (None, None)."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read(), url
    except Exception as e:
        logging.error("Download failed %s: %s", url, e)
        return None, None


# ---------------------------------------------------------------------------
# Listing extraction
# ---------------------------------------------------------------------------

def _fetch_listing_page(page_num):
    """Fetch a single listing page and extract article entries.

    Args:
        page_num: 0 for index.html, >=1 for index_N.html

    Returns list[dict]: {id, title, url, date_str}.
    Returns empty list if page not found or no articles.
    """
    if page_num == 0:
        url = f"{_SITE_ROOT}{_LISTING_BASE}/index.html"
    else:
        url = f"{_SITE_ROOT}{_LISTING_BASE}/index_{page_num}.html"

    html = _fetch_html(url)
    if not html:
        return []

    # Quick check for 404 / empty page
    if len(html) < 500:
        return []

    soup = BeautifulSoup(html, "lxml")
    articles = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/zdfg/info/" not in href or not href.endswith(".html"):
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        # Make absolute URL
        detail_url = urljoin(_SITE_ROOT, href)

        # Derive an ID from the URL path (e.g. "2023/733410")
        art_id = re.sub(r"^.*/info/", "", href)
        art_id = re.sub(r"\.html$", "", art_id)

        # Try to extract date from surrounding elements
        date_str = ""
        parent = a.parent
        if parent:
            time_el = parent.find("span", class_=re.compile(r"time|date", re.I))
            if time_el:
                date_str = time_el.get_text(strip=True)
            else:
                parent_text = parent.get_text(strip=True)
                date_m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", parent_text)
                if date_m:
                    date_str = date_m.group(1)

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": date_str,
        })

    # Deduplicate by URL within the page
    seen = set()
    unique = []
    for art in articles:
        if art["url"] not in seen:
            seen.add(art["url"])
            unique.append(art)
    return unique


def _crawl_all_listings():
    """Fetch all listing pages until empty.

    Returns list[dict]: all deduplicated articles from all pages.
    """
    all_articles = []
    seen_urls = set()

    for page_num in range(0, 200):  # safety cap
        articles = _fetch_listing_page(page_num)
        if not articles:
            if page_num == 0:
                _safe_print("[ZDFG] ERROR: page 1 (index.html) returned no articles!")
                sys.stdout.flush()
                return []
            _safe_print(f"[ZDFG]   Page {page_num + 1}: empty, stopping pagination")
            sys.stdout.flush()
            break

        new_count = 0
        for art in articles:
            if art["url"] not in seen_urls:
                seen_urls.add(art["url"])
                all_articles.append(art)
                new_count += 1

        _safe_print(f"[ZDFG]   Page {page_num + 1}: {len(articles)} items ({new_count} new)")
        sys.stdout.flush()

        if len(articles) < 10:  # likely last page
            break

        _request_delay()

    _safe_print(f"[ZDFG]   Total articles found: {len(all_articles)}")
    sys.stdout.flush()
    return all_articles


# ---------------------------------------------------------------------------
# Detail page extraction
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(\s+\d{1,2}:\d{1,2}(:\d{1,2})?)?$")


def _extract_detail(html, detail_url):
    """Parse a zycg zdfg detail page and return (content_md, attachments_list, metadata).

    Returns:
        content_md: Markdown-formatted article content (str)
        attachments: list of {filename, url} dicts
        metadata: dict with title, date_str
    """
    soup = BeautifulSoup(html, "lxml")

    # ── Strip clutter ──
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript", "iframe"]):
        tag.decompose()

    # ── Title ──
    title = ""
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = h.get_text(strip=True)
        if not text or len(text) < 10 or len(text) > 200:
            continue
        if _DATE_RE.match(text):
            continue
        title = text
        break
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

    # ── Date ──
    date_str = ""
    body_text = soup.body.get_text(separator="\n", strip=True) if soup.body else html
    date_m = re.search(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})", body_text)
    if date_m:
        date_str = date_m.group(1)
    else:
        date_m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", body_text)
        if date_m:
            date_str = date_m.group(1)

    # ── Content ──
    content_div = None
    for cls in ("info-content", "article-content", "content", "info-text", "article"):
        content_div = soup.find("div", class_=re.compile(cls, re.I))
        if content_div and len(content_div.get_text(strip=True)) > 100:
            break
    if not content_div:
        content_div = soup.body

    # ── Attachments ──
    attachments = []
    if content_div:
        for a in content_div.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            ext = os.path.splitext(href.split("?")[0].split("#")[0])[1].lower()
            if ext not in _ATTACH_EXTENSIONS:
                if not any(kw in href.lower() for kw in ("download", "attach", "upload")):
                    continue
            file_url = urljoin(_SITE_ROOT, href)
            fname = a.get_text(strip=True) or os.path.basename(href.split("?")[0])
            if fname:
                attachments.append({"filename": fname, "url": file_url})

    # ── Build Markdown ──
    lines = [f"# {title}", ""]
    if date_str:
        lines.append(f"**日期:** {date_str}")
    lines.append(f"**来源:** {detail_url}")
    lines.append(f"**网站:** 中央国家机关政府采购中心 - 制度法规")
    lines.append("")

    if content_div:
        for el in content_div.find_all(
            ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"]
        ):
            tag = el.name
            text = el.get_text(strip=True)
            if not text:
                continue
            if any(kw in text for kw in ("当前位置", "版权所有", "ICP备", "公网安备")):
                continue

            if tag.startswith("h") and len(tag) == 2:
                level = int(tag[1])
                prefix = "#" * level
                lines.append(f"\n{prefix} {text}\n")
            elif tag == "li":
                lines.append(f"- {text}")
            elif tag == "blockquote":
                lines.append(f"> {text}")
            elif tag == "pre":
                lines.append(f"```\n{text}\n```")
            else:
                lines.append(text)
                lines.append("")

    # If no structured content captured, fall back to text extraction
    if len(lines) < 8 and content_div:
        text = content_div.get_text(separator="\n", strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        lines.append(text)

    content_md = "\n".join(lines)
    metadata = {"title": title, "date_str": date_str}
    return content_md, attachments, metadata


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path, dest_dir):
    """Extract a ZIP file, return list of extracted file paths. Original ZIP kept."""
    extracted = []
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, 'wb') as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
                _safe_print(f"         Extracted: {safe_name}")
    except Exception as e:
        _safe_print(f"         ZIP extract error: {e}")
    return extracted


# ---------------------------------------------------------------------------
# Persistence & state
# ---------------------------------------------------------------------------

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
    logging.info("State saved (%d IDs)", len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id.replace('/', '_')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    """Upload a file to the knowledge base and queue parsing."""
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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Article processing (batch + checkpoint)
# ---------------------------------------------------------------------------

def _process_articles(articles, output_dir, kb_id, tenant_id,
                      processed_ids, state, start_time, max_runtime):
    """Process articles in batches of 10 with checkpoint after each batch.

    For each article:
      1. Fetch detail page + extract content + find attachments
      2. Download attachments (with ZIP extraction)
      3. Save markdown locally
      4. Upload batch to KB (markdown + attachments)

    Args:
        start_time: time.time() when the crawl started
        max_runtime: max seconds before graceful stop

    Returns:
        total_processed: number of articles successfully processed
    """
    new_articles = [a for a in articles if a["id"] not in processed_ids]
    if not new_articles:
        _safe_print("[ZDFG] All articles already processed, nothing to do.")
        sys.stdout.flush()
        return 0

    _safe_print(f"[ZDFG] {len(new_articles)} new article(s) to process\n")
    sys.stdout.flush()

    BATCH_SIZE = 10
    total_processed = 0
    fail_count = 0
    batch_num = 0

    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    for batch_start in range(0, len(new_articles), BATCH_SIZE):
        # ── Time-bounded check ──
        elapsed = time.time() - start_time
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                f"\n[ZDFG] Runtime {elapsed:.0f}s, "
                f"stopping early ({total_processed} saved). "
                f"Next run will resume."
            )
            sys.stdout.flush()
            break

        batch = new_articles[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        md_parts = []
        batch_ids = []
        all_attachment_files = []  # (local_path, article_title)

        _safe_print(f"[ZDFG] === Batch {batch_num} ({len(batch)} articles) ===")
        sys.stdout.flush()

        for idx, art in enumerate(batch, 1):
            global_idx = batch_start + idx
            title_short = art["title"][:70]
            _safe_print(f"[ZDFG]   [{global_idx}/{len(new_articles)}] {title_short}")
            sys.stdout.flush()

            # Step 1: Fetch detail page
            html = _fetch_html(art["url"])
            if not html:
                fail_count += 1
                logging.warning("Empty detail page: %s", art["url"])
                continue

            content_md, attachments, metadata = _extract_detail(html, art["url"])
            if not content_md:
                fail_count += 1
                continue

            # Update title/date from detail if listing had none
            art_title = metadata.get("title") or art["title"]
            art_date = metadata.get("date_str") or art.get("date_str", "")

            # Step 2: Download attachments
            local_files = []
            if attachments:
                _safe_print(f"         {len(attachments)} attachment(s)")
                sys.stdout.flush()
                for att in attachments:
                    fname = _sanitize_filename(att["filename"])
                    date_prefix = art_date[:10].replace("-", "") if art_date else ""
                    local_name = f"{date_prefix}_{art['id'].replace('/', '_')}_{fname}" if date_prefix else f"{art['id'].replace('/', '_')}_{fname}"
                    local_path = os.path.join(downloads_dir, local_name)

                    if os.path.exists(local_path):
                        _safe_print(f"         (cached) {fname}")
                        local_files.append(local_path)
                        continue

                    _safe_print(f"         downloading: {fname}")
                    sys.stdout.flush()
                    blob, _ = _download_file(att["url"])
                    if blob and len(blob) > 100:
                        with open(local_path, "wb") as f:
                            f.write(blob)
                        _safe_print(f"         saved: {local_name} ({len(blob)} bytes)")
                        local_files.append(local_path)

                        # ZIP extraction
                        if local_path.lower().endswith(".zip") or (
                            len(blob) >= 4 and blob[:4] == b"PK\x03\x04"
                        ):
                            extracted = _extract_zip(local_path, downloads_dir)
                            local_files.extend(extracted)
                    else:
                        _safe_print(f"         download failed for {fname}")

            # Add attachment links to markdown
            if attachments:
                content_md += "\n\n## 附件\n\n"
                for att in attachments:
                    content_md += f"- [{att['filename']}]({att['url']})\n"

            # Save individual markdown
            _save_markdown(content_md, output_dir, art["id"])

            md_parts.append(content_md)
            batch_ids.append(art["id"])
            for lf in local_files:
                all_attachment_files.append((lf, art_title))

            _request_delay()

        # ── Checkpoint: save batch markdown + state ──
        if md_parts:
            batch_md_path = os.path.join(output_dir,
                                         f"zdfg_batch_{batch_num:03d}.md")
            with open(batch_md_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            # Upload batch markdown to KB
            if kb_id:
                try:
                    _upload_to_kb(batch_md_path, kb_id, tenant_id, parser_id="laws")
                    _safe_print(f"[ZDFG]   Batch {batch_num} markdown uploaded to KB")
                except Exception as e:
                    _safe_print(f"[ZDFG]   Batch {batch_num} markdown upload failed: {e}")
                    logging.error("Upload failed for batch %d: %s", batch_num, e)

            total_processed += len(md_parts)
            _safe_print(f"[ZDFG]   Batch {batch_num} done ({total_processed}/{len(new_articles)})\n")
            sys.stdout.flush()

        # Upload attachments for this batch
        if all_attachment_files and kb_id:
            _safe_print(f"[ZDFG]   Uploading {len(all_attachment_files)} attachment(s)...")
            sys.stdout.flush()
            for local_path, _art_title in all_attachment_files:
                ext = os.path.splitext(local_path)[1].lower()
                pid = "laws" if ext in _LAWS_EXTENSIONS else "general"
                try:
                    _upload_to_kb(local_path, kb_id, tenant_id, parser_id=pid)
                except Exception as e:
                    _safe_print(f"         Upload error: {os.path.basename(local_path)}: {e}")

    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="zycg.gov.cn 制度法规 (zdfg) FreeCMS crawler"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. https://www.zycg.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl all articles")
    parser.add_argument("--section", default=None,
                        help="Unused (single-section crawler)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to process (0 = unlimited)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds before graceful stop (default: 3300 = 55 min)")
    # Legacy compatibility
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
    parser.add_argument("--max-days", type=int, default=365,
                        help="Max age in days for articles (default: 365)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[ZDFG] 中央国家机关政府采购中心 - 制度法规 crawler")
    _safe_print(f"[ZDFG] Target: {args.target_url}")
    _safe_print(f"[ZDFG] KB: {args.kb_id}")
    _safe_print(f"[ZDFG] Task: {args.task_name}")
    _safe_print(f"[ZDFG] Max runtime: {args.max_runtime}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZYCG-ZDFG crawler started ===")

    start_time = time.time()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZDFG] Output directory: {output_dir}\n")
    sys.stdout.flush()

    # ── State ──
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[ZDFG] Previously processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # ── Step 1: Crawl listing ──
    _safe_print("[ZDFG] Step 1/3: Crawling listing pages...")
    sys.stdout.flush()
    all_articles = _crawl_all_listings()

    if not all_articles:
        _safe_print("[ZDFG] No articles found, exiting.")
        sys.stdout.flush()
        return

    # Apply max-articles limit
    if args.max_articles and len(all_articles) > args.max_articles:
        all_articles = all_articles[:args.max_articles]
        _safe_print(f"[ZDFG] Limited to {args.max_articles} articles")
        sys.stdout.flush()

    # ── Step 2: Process articles (detail + attachments) ──
    _safe_print(f"\n[ZDFG] Step 2/3: Processing {len(all_articles)} articles...\n")
    sys.stdout.flush()

    total = _process_articles(
        all_articles, output_dir, args.kb_id, args.tenant_id,
        processed_ids, state, start_time, args.max_runtime
    )

    # ── Step 3: Final state save ──
    _save_state(output_dir, state)

    elapsed = time.time() - start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[ZDFG] Done: {total} article(s) processed in {elapsed:.0f}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== ZYCG-ZDFG crawler finished: %d articles ===", total)


if __name__ == "__main__":
    CONSUMER_NAME = "zycg_zdfg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
