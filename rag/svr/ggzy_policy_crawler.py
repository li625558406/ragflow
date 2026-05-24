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
Dedicated web crawler for ggzy.gov.cn 政策法规文件 (policyFileList.po).

Crawls the policy document repository of the National Public Resource Trading
Platform — all articles across all pagination pages.

Site characteristics
────────────────────
  • Listing  →  /SIC/web/policyFileList.po?pageNum=N
                 Java Servlet `.po` site with JSESSIONID cookie.
                 Server-rendered HTML, no JavaScript rendering required.
  • Details  →  /SIC/web/details.po;jsessionid=XXX?id=UUID
                 Single-page content with inline text and download links.
  • Files    →  Direct download links (often external domains like ndrc.gov.cn).
                 Formats: PDF, DOC, DOCX, XLS, XLSX, ZIP, RAR, 7Z.
  • Auth     →  JSESSIONID cookie from first page visit.
                 No access_token or Playwright required.

Pagination: server-rendered, total pages extracted from getPageHtml(1, N) JS call.
Each page has 20 articles.  Stop when page returns zero articles.

Checkpoint/resume: articles processed in batches of 10 with state persistence
after each batch.  3600s task timeout adaptation via max_runtime with graceful
stop when < 120s remaining.

Usage (typically spawned by task_executor):
    python ggzy_policy_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://www.ggzy.gov.cn/ \\
        --kb-id <KB_ID> \\
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
from urllib.parse import urljoin

import requests as _requests
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
_SITE_ROOT = "https://www.ggzy.gov.cn"
_LISTING_PATH = "/SIC/web/policyFileList.po"
_DETAIL_PATH = "/SIC/web/details.po"

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
}

# Anti-crawling delays (seconds)
_PAGE_DELAY = (1.5, 3.0)     # between listing pages
_ARTICLE_DELAY = (0.5, 1.5)   # between articles within batch

_STATE_FILENAME = "_crawler_state.json"

# Attachment extensions
_ATTACH_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".7z",
    ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}
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


def _request_delay(min_s, max_s):
    time.sleep(random.uniform(min_s, max_s))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(\s+\d{1,2}:\d{1,2}(:\d{1,2})?)?$")


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session():
    sess = _requests.Session()
    sess.headers.update(_HEADERS)
    # Visit listing page to obtain JSESSIONID
    try:
        sess.get(f"{_SITE_ROOT}{_LISTING_PATH}", timeout=30)
    except Exception as e:
        logging.warning("Failed to get JSESSIONID: %s", e)
    return sess


# ---------------------------------------------------------------------------
# Listing page parsing
# ---------------------------------------------------------------------------

def _fetch_listing_page(sess, page_num=1):
    """Fetch one listing page, parse articles.

    Returns (articles: list[dict], total_pages: int).
    """
    url = f"{_SITE_ROOT}{_LISTING_PATH}?pageNum={page_num}"
    try:
        resp = sess.get(url, timeout=60)
        html = resp.text
    except Exception as e:
        logging.error("Listing page %d error: %s", page_num, e)
        return [], 0

    soup = BeautifulSoup(html, "lxml")

    # Extract total pages from JS: getPageHtml(current, total)
    total_pages = 1
    m = re.search(r'getPageHtml\(\s*(\d+)\s*,\s*(\d+)\s*\)', html)
    if m:
        total_pages = int(m.group(2))

    # Also try the paging div text
    if total_pages <= 1:
        paging = soup.find("div", id="paging")
        if paging:
            nums = re.findall(r'pageNum=(\d+)', str(paging))
            if nums:
                total_pages = max(int(n) for n in nums)

    # Parse article list
    articles = []
    context_ul = soup.find("ul", id="contextId")
    if not context_ul:
        logging.warning("Listing page %d: #contextId not found", page_num)
        return [], total_pages

    for li in context_ul.find_all("li"):
        a = li.find("a", href=re.compile(r"details\.po"))
        if not a:
            continue
        href = a.get("href", "").strip()
        if not href:
            continue

        title = a.get_text(strip=True)
        if not title:
            continue

        # Build absolute detail URL
        # href format: details.po;jsessionid=XXX?id=UUID or details.po?id=UUID
        if href.startswith("http"):
            detail_url = href
        elif href.startswith("/"):
            detail_url = urljoin(_SITE_ROOT, href)
        else:
            # Relative href — resolve against the listing directory
            base_dir = f"{_SITE_ROOT}{_LISTING_PATH.rsplit('/', 1)[0]}/"
            detail_url = urljoin(base_dir, href)

        # Date from sibling <span>
        date_str = ""
        span = li.find("span")
        if span:
            date_str = span.get_text(strip=True)

        # Extract article UUID from id= parameter
        art_id = ""
        m_id = re.search(r'[?&]id=([^&\s]+)', href)
        if m_id:
            art_id = m_id.group(1)

        if not art_id:
            art_id = title  # fallback

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": date_str,
        })

    return articles, total_pages


def _crawl_all_listing(sess, start_time=None, max_runtime=3300):
    """Crawl all listing pages and collect all articles."""
    all_articles = []
    seen_ids = set()

    # First page to get total
    articles, total_pages = _fetch_listing_page(sess, 1)
    if not articles:
        _safe_print("[LISTING] No articles on page 1.")
        return []

    for a in articles:
        if a["id"] not in seen_ids:
            seen_ids.add(a["id"])
            all_articles.append(a)

    _safe_print(f"[LISTING] Page 1/{total_pages}: {len(articles)} items, total pages={total_pages}")
    sys.stdout.flush()

    if total_pages <= 1:
        _safe_print(f"[LISTING] Collected {len(all_articles)} articles (single page)")
        return all_articles

    # Remaining pages
    for p in range(2, total_pages + 1):
        # Time-bounded check
        if start_time and (time.time() - start_time) > (max_runtime - 120):
            _safe_print(f"[LISTING] Stopping early (runtime limit), got {len(all_articles)} articles")
            break

        _request_delay(*_PAGE_DELAY)

        articles, _ = _fetch_listing_page(sess, p)
        new_count = 0
        for a in articles:
            if a["id"] not in seen_ids:
                seen_ids.add(a["id"])
                all_articles.append(a)
                new_count += 1

        _safe_print(f"[LISTING] Page {p}/{total_pages}: {len(articles)} items ({new_count} new, total: {len(all_articles)})")
        sys.stdout.flush()

        if not articles:
            break

    _safe_print(f"[LISTING] Collected {len(all_articles)} articles from {total_pages} pages")
    return all_articles


# ---------------------------------------------------------------------------
# Detail page extraction
# ---------------------------------------------------------------------------

def _extract_detail(html, detail_url):
    """Parse a policy detail page.

    Returns (content_md, attachments, metadata).
    """
    soup = BeautifulSoup(html, "lxml")

    # ── Strip clutter ──
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # ── Title ──
    title = ""
    h4 = soup.find("h4", id="txtCaption")
    if h4:
        title = h4.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

    # ── Date ──
    date_str = ""
    date_span = soup.find("span", id="txtPublishTime")
    if date_span:
        date_str = date_span.get_text(strip=True)

    # ── Content ──
    content_div = soup.find("div", id="divContent")
    if not content_div:
        content_div = soup.find("div", class_="article_con")

    lines = []
    attachments = []

    if content_div:
        for el in content_div.find_all(
            ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "span", "div"]
        ):
            tag = el.name
            text = el.get_text(strip=True)
            if not text:
                continue
            # Skip breadcrumb/clutter
            if any(kw in text for kw in ("当前位置", "版权所有", "ICP备", "公网安备", "政府网站工作年度报表")):
                continue

            if tag.startswith("h") and len(tag) == 2:
                level = int(tag[1])
                lines.append(f"\n{'#' * level} {text}\n")
            elif tag == "li":
                lines.append(f"- {text}")
            elif tag == "blockquote":
                lines.append(f"> {text}")
            elif tag == "pre":
                lines.append(f"```\n{text}\n```")
            elif tag in ("div", "span"):
                if len(text) > 50:
                    lines.append(text)
                    lines.append("")
            else:
                lines.append(text)
                lines.append("")

    # ── Attachments: from download links in and after content_div ──
    # Search in the entire "ultimately" container or article_con
    seen_urls = set()
    for container_class in ("ultimately", "article_con", "cmsdiv"):
        container = soup.find("div", class_=container_class)
        if not container:
            continue
        for a in container.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("javascript:") or href.startswith("#"):
                continue
            abs_url = urljoin(_SITE_ROOT, href)
            if abs_url in seen_urls:
                continue
            ext = os.path.splitext(href.split("?")[0].split("#")[0])[1].lower()
            text = a.get_text(strip=True)
            if ext in _ATTACH_EXTENSIONS:
                fname = text or os.path.basename(href.split("?")[0])
                attachments.append({"filename": fname, "url": abs_url})
                seen_urls.add(abs_url)
            # Also catch links with download/file keywords
            elif any(kw in href.lower() for kw in ("download", "attach", "file", "upload")):
                fname = text or os.path.basename(href.split("?")[0])
                if fname:
                    attachments.append({"filename": fname, "url": abs_url})
                    seen_urls.add(abs_url)

    # ── Fallback: body text ──
    if len(lines) < 8:
        body = soup.body
        if body:
            text = body.get_text(separator="\n", strip=True)
            text = re.sub(r'\n{3,}', '\n\n', text)
            lines.append(text)

    # ── Build markdown ──
    header_lines = [f"# {title}", ""]
    if date_str:
        header_lines.append(f"**发布时间:** {date_str}")
    header_lines.append(f"**来源:** {detail_url}")
    header_lines.append(f"**网站:** 全国公共资源交易平台 — 政策法规文件")
    header_lines.append("")

    content_md = "\n".join(header_lines + lines)

    if attachments:
        content_md += "\n\n## 附件\n\n"
        for att in attachments:
            content_md += f"- [{att['filename']}]({att['url']})\n"

    metadata = {"title": title, "date_str": date_str}
    return content_md, attachments, metadata


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=120):
    """Download a binary file. Use session for same-domain, urllib for external."""
    if file_url.startswith("http://"):
        file_url = file_url.replace("http://", "https://", 1)

    # Use session for ggzy.gov.cn URLs
    if "ggzy.gov.cn" in file_url:
        try:
            resp = sess.get(file_url, timeout=timeout, stream=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            logging.error("Download error %s: %s", file_url, e)
        return None

    # External URLs: use urllib
    req = urllib.request.Request(file_url, headers=_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if len(data) > 100:
            return data
    except Exception as e:
        logging.error("Download error (external) %s: %s", file_url, e)
    return None


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path, dest_dir):
    """Extract ZIP file, return list of extracted file paths. ZIP kept."""
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
                _safe_print(f"           Extracted: {safe_name}")
    except Exception as e:
        _safe_print(f"           ZIP extract error: {e}")
    return extracted


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load state: %s", e)
    return {"processed_ids": []}


def _save_state(output_dir, state):
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, _STATE_FILENAME), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs)", len(state.get("processed_ids", [])))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    safe_id = article_id.replace("/", "_").replace("\\", "_")
    path = os.path.join(articles_dir, f"{safe_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
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
# Article processing (batch of 10 with checkpoint)
# ---------------------------------------------------------------------------

def _process_articles(articles, output_dir, kb_id, tenant_id,
                       processed_ids, state, start_time, max_runtime, http_sess):
    """Process articles in batches of 10 with checkpoint after each batch."""
    new_articles = [a for a in articles if a["id"] not in processed_ids]
    if not new_articles:
        _safe_print("[PROCESS] All articles already processed.")
        sys.stdout.flush()
        return 0

    _safe_print(f"[PROCESS] {len(new_articles)} new article(s) to process\n")
    sys.stdout.flush()

    BATCH_SIZE = 10
    total_processed = 0
    batch_num = 0

    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    for batch_start in range(0, len(new_articles), BATCH_SIZE):
        # Time-bounded check
        elapsed = time.time() - start_time
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                f"\n[PROCESS] Runtime {elapsed:.0f}s, "
                f"stopping early ({total_processed} saved). "
                f"Next run will resume."
            )
            sys.stdout.flush()
            break

        batch = new_articles[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        md_parts = []
        batch_ids = []
        all_attachment_files = []

        _safe_print(f"[PROCESS] === Batch {batch_num} ({len(batch)} articles) ===")
        sys.stdout.flush()

        for idx, art in enumerate(batch, 1):
            global_idx = batch_start + idx
            _safe_print(f"[PROCESS]   [{global_idx}/{len(new_articles)}] {art['title'][:80]}")
            sys.stdout.flush()

            if not art.get("url"):
                batch_ids.append(art["id"])
                _safe_print(f"           No detail URL, skipping")
                continue

            # Step 1: Fetch detail page
            try:
                resp = http_sess.get(art["url"], timeout=60)
                html = resp.text
            except Exception as e:
                logging.error("Detail page error for %s: %s", art["url"], e)
                batch_ids.append(art["id"])
                _safe_print(f"           Failed to fetch detail page")
                continue

            if not html:
                batch_ids.append(art["id"])
                continue

            # Step 2: Extract content + attachments
            content_md, attachments, metadata = _extract_detail(html, art["url"])
            if not content_md:
                batch_ids.append(art["id"])
                _safe_print(f"           Empty content")
                continue

            art_title = metadata.get("title") or art["title"]
            art_date = metadata.get("date_str") or art.get("date_str", "")

            # Step 3: Download attachments
            local_files = []
            if attachments:
                _safe_print(f"           {len(attachments)} attachment(s)")
                sys.stdout.flush()
                for att in attachments:
                    fname = _sanitize_filename(att["filename"])
                    date_prefix = art_date[:10].replace("-", "") if art_date else ""
                    safe_id = art["id"].replace("/", "_").replace("\\", "_")[:60]
                    local_name = f"{date_prefix}_{safe_id}_{fname}" if date_prefix else f"{safe_id}_{fname}"
                    local_path = os.path.join(downloads_dir, local_name)

                    if os.path.exists(local_path) and os.path.getsize(local_path) > 100:
                        _safe_print(f"           (cached) {fname}")
                        local_files.append(local_path)
                        continue

                    _safe_print(f"           downloading: {fname[:80]}")
                    sys.stdout.flush()

                    blob = _download_file(http_sess, att["url"])
                    if blob and len(blob) > 100:
                        with open(local_path, "wb") as f:
                            f.write(blob)
                        _safe_print(f"           saved: {local_name[:80]} ({len(blob)} bytes)")
                        local_files.append(local_path)

                        # ZIP extraction
                        if local_path.lower().endswith(".zip") or (
                            len(blob) >= 4 and blob[:4] == b"PK\x03\x04"
                        ):
                            extracted = _extract_zip(local_path, downloads_dir)
                            local_files.extend(extracted)
                    else:
                        _safe_print(f"           download failed for {fname[:60]}")

            # Save individual markdown
            _save_markdown(content_md, output_dir, art["id"])
            md_parts.append(content_md)
            batch_ids.append(art["id"])
            for lf in local_files:
                all_attachment_files.append((lf, art_title))

            _request_delay(*_ARTICLE_DELAY)

        # ── Batch checkpoint ──
        if md_parts:
            batch_path = os.path.join(output_dir, f"policy_batch_{batch_num:04d}.md")
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                try:
                    _upload_to_kb(batch_path, kb_id, tenant_id, parser_id="laws")
                    _safe_print(f"[PROCESS]   Batch {batch_num} markdown uploaded to KB")
                except Exception as e:
                    _safe_print(f"[PROCESS]   Batch {batch_num} upload failed: {e}")

            total_processed += len(md_parts)
            _safe_print(f"[PROCESS]   Batch {batch_num} done ({total_processed}/{len(new_articles)})\n")
            sys.stdout.flush()

        # Upload attachments
        if all_attachment_files and kb_id:
            _safe_print(f"[PROCESS]   Uploading {len(all_attachment_files)} attachment(s)...")
            sys.stdout.flush()
            for local_path, _art_title in all_attachment_files:
                ext = os.path.splitext(local_path)[1].lower()
                pid = "laws" if ext in _LAWS_EXTENSIONS else "general"
                try:
                    _upload_to_kb(local_path, kb_id, tenant_id, parser_id=pid)
                except Exception as e:
                    _safe_print(f"           Upload error: {os.path.basename(local_path)}: {e}")

    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="ggzy.gov.cn policy file crawler (policyFileList.po)"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (https://www.ggzy.gov.cn/)")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True,
                        help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None,
                        help="Output root directory")
    parser.add_argument("--full", action="store_true",
                        help="Ignore saved state and re-crawl")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to process (0 = unlimited)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds (default: 3300 = 55 min)")
    # Legacy compatibility
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused")
    parser.add_argument("--max-days", default=0, help="Unused (all articles)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[GGZY-POLICY] 全国公共资源交易平台 - 政策法规 crawler")
    _safe_print(f"[GGZY-POLICY] Target: {args.target_url}")
    _safe_print(f"[GGZY-POLICY] KB: {args.kb_id}")
    _safe_print(f"[GGZY-POLICY] Task: {args.task_name}")
    _safe_print(f"[GGZY-POLICY] Max runtime: {args.max_runtime}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== GGZY-POLICY crawler started ===")

    global_start_time = time.time()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[GGZY-POLICY] Output directory: {output_dir}\n")
    sys.stdout.flush()

    # ── State ──
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[GGZY-POLICY] Previously processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # ── Init session ──
    http_sess = _init_session()

    # ── Phase 1: Crawl listing ──
    _safe_print("[GGZY-POLICY] Phase 1: Crawling listing pages...\n")
    sys.stdout.flush()

    articles = _crawl_all_listing(
        http_sess,
        start_time=global_start_time,
        max_runtime=args.max_runtime,
    )

    if not articles:
        _safe_print("[GGZY-POLICY] No articles found. Done.")
        return

    _safe_print(f"\n[GGZY-POLICY] {len(articles)} total articles from listing\n")
    sys.stdout.flush()

    if args.max_articles and len(articles) > args.max_articles:
        articles = articles[:args.max_articles]
        _safe_print(f"[GGZY-POLICY] Limited to {args.max_articles} articles\n")

    # ── Phase 2: Process articles ──
    _safe_print("[GGZY-POLICY] Phase 2: Processing articles...\n")
    sys.stdout.flush()

    total = _process_articles(
        articles, output_dir, args.kb_id, args.tenant_id,
        processed_ids, state, global_start_time, args.max_runtime,
        http_sess,
    )

    elapsed = time.time() - global_start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GGZY-POLICY] Done: {total} article(s) processed in {elapsed:.0f}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== GGZY-POLICY crawler finished: %d articles ===", total)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzy_policy_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
