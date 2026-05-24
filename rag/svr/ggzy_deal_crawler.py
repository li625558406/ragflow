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
Dedicated web crawler for ggzy.gov.cn — 全国公共资源交易平台 交易公开.

Covers all deal types published on the national public resource trading platform.
Data volume is large (2000+ records per day), so checkpoint/resume is critical.

Site characteristics
────────────────────
  • Listing  →  POST /information/pubTradingInfo/getTradList
                 form-encoded: SOURCE_TYPE, DEAL_TIME, PAGENUMBER
                 Returns 20 records per page.
  • Detail   →  /a/ page: wrapper with all deal-stage tabs (交易公告/开标记录/
                 交易结果公示/招标资审文件澄清).  Each tab lists one or more
                 file links pointing to /b/ content pages.
  • Content  →  /b/ page: static HTML with full article text.  May contain
                 download links for doc/pdf/xls/zip attachments.
  • Auth     →  No authentication required.  Captcha (code 829) may appear
                 under heavy request load; handled by pause-and-retry.

Usage (typically spawned by task_executor):
    python ggzy_deal_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://www.ggzy.gov.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME> \\
        --max-days 1
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
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlencode

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

_STATE_FILENAME = "_crawler_state.json"

# Anti-crawling delays (seconds)
_LISTING_PAGE_DELAY = (2.0, 4.0)   # between listing API pages
_ARTICLE_DELAY = (0.5, 1.5)         # between articles within batch
_CAPTCHA_WAIT = 30                  # when captcha triggered

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
    sess.headers.update({"Referer": f"{_SITE_ROOT}/deal/dealList.html"})
    return sess


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

_LISTING_API = "/information/pubTradingInfo/getTradList"


def _fetch_listing_page(sess, page_num, time_filter="01",
                         time_begin=None, time_end=None,
                         classify=None, stage=None):
    """Fetch one page of the listing API.

    time_filter: '01'=today, '02'=3days, '03'=10days, '06'=custom range
    Returns (records: list[dict], total: int, pages: int).
    """
    form = {
        "SOURCE_TYPE": "1",
        "DEAL_TIME": time_filter,
        "PAGENUMBER": str(page_num),
    }
    if time_begin and time_filter == "06":
        form["TIMEBEGIN"] = time_begin
    if time_end and time_filter == "06":
        form["TIMEEND"] = time_end
    if classify:
        form["DEAL_CLASSIFY"] = classify
    if stage:
        form["DEAL_STAGE"] = stage

    try:
        resp = sess.post(f"{_SITE_ROOT}{_LISTING_API}", data=form, timeout=90)
        data = resp.json()
    except Exception as e:
        logging.error("Listing API error page %d: %s", page_num, e)
        return [], 0, 0

    code = str(data.get("code", ""))
    if code == "829":
        # Captcha triggered
        logging.warning("Listing API page %d: captcha triggered (code 829)", page_num)
        return None, 0, 0  # None signals captcha
    if code != "200":
        logging.warning("Listing API page %d: code=%s msg=%s",
                        page_num, code, data.get("message", ""))
        return [], 0, 0

    records = data.get("data", {}).get("records", [])
    total = data.get("data", {}).get("total", 0)
    pages = data.get("data", {}).get("pages", 0)

    articles = []
    for item in records:
        art_id = item.get("id", "").strip()
        title = item.get("title", "").strip()
        if not art_id or not title:
            continue

        url = item.get("url", "").strip()
        detail_url = urljoin(_SITE_ROOT, url) if url else ""

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": (item.get("publishTime") or "").strip(),
            "province": (item.get("provinceText") or "").strip(),
            "info_type": (item.get("informationTypeText") or "").strip(),
            "biz_type": (item.get("businessTypeText") or "").strip(),
            "platform": (item.get("transactionSourcesPlatformText") or "").strip(),
        })

    return articles, total, pages


def _crawl_listing(sess, max_days=1, max_articles=0, start_time=None, max_runtime=3300):
    """Fetch all pages for today's deals."""
    today = datetime.now()

    # Determine time filter
    if max_days <= 1:
        time_filter = "01"
        time_begin = time_end = None
    elif max_days <= 3:
        time_filter = "02"
        time_begin = time_end = None
    elif max_days <= 10:
        time_filter = "03"
        time_begin = time_end = None
    else:
        time_filter = "06"
        time_begin = (today - timedelta(days=max_days)).strftime("%Y-%m-%d")
        time_end = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    all_articles = []
    seen_ids = set()
    captcha_hits = 0

    _safe_print(f"[LISTING] Time filter: {time_filter}, date: {time_begin or today.strftime('%Y-%m-%d')} ~ {time_end or today.strftime('%Y-%m-%d')}")
    sys.stdout.flush()

    # First page to get total count
    articles, total, pages = _fetch_listing_page(sess, 1, time_filter, time_begin, time_end)
    if articles is None:
        _safe_print("[LISTING] Captcha on first page, waiting 30s and retrying...")
        _request_delay(30, 35)
        articles, total, pages = _fetch_listing_page(sess, 1, time_filter, time_begin, time_end)

    if not articles:
        _safe_print("[LISTING] No articles found on page 1.")
        return []

    for a in articles:
        if a["id"] not in seen_ids:
            seen_ids.add(a["id"])
            all_articles.append(a)

    _safe_print(f"[LISTING] Page 1/{pages}: {len(articles)} items, total={total}")

    if pages <= 1:
        return all_articles

    # Crawl remaining pages
    for p in range(2, pages + 1):
        # Time-bounded check
        if start_time and (time.time() - start_time) > (max_runtime - 120):
            _safe_print(f"[LISTING] Stopping listing crawl early (runtime limit), got {len(all_articles)} articles from {p-1} pages")
            break

        _request_delay(*_LISTING_PAGE_DELAY)

        articles, _, _ = _fetch_listing_page(sess, p, time_filter, time_begin, time_end)
        if articles is None:
            captcha_hits += 1
            if captcha_hits > 3:
                _safe_print(f"[LISTING] Too many captcha hits, stopping listing at page {p}")
                break
            _safe_print(f"[LISTING] Captcha at page {p}, waiting {_CAPTCHA_WAIT}s...")
            sys.stdout.flush()
            _request_delay(_CAPTCHA_WAIT, _CAPTCHA_WAIT + 10)
            articles, _, _ = _fetch_listing_page(sess, p, time_filter, time_begin, time_end)
            if articles is None:
                _safe_print(f"[LISTING] Captcha persists, skipping page {p}")
                continue
            if not articles:
                continue

        new_count = 0
        for a in articles:
            if a["id"] not in seen_ids:
                seen_ids.add(a["id"])
                all_articles.append(a)
                new_count += 1

        _safe_print(f"[LISTING] Page {p}/{pages}: {len(articles)} items ({new_count} new, total collected: {len(all_articles)})")
        sys.stdout.flush()

        if max_articles and len(all_articles) >= max_articles:
            all_articles = all_articles[:max_articles]
            _safe_print(f"[LISTING] Reached max_articles limit ({max_articles}), stopping listing crawl")
            break

        if len(articles) < 20:
            break

    _safe_print(f"[LISTING] Collected {len(all_articles)} articles from {pages} pages")
    return all_articles


# ---------------------------------------------------------------------------
# Detail page (/a/) — extract tabs and /b/ file URLs
# ---------------------------------------------------------------------------

def _parse_detail_wrapper(html, detail_url):
    """Parse the /a/ wrapper page.

    Returns (title, metadata, files) where files is list of:
        {stage_id, stage_name, file_title, b_url}
    """
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = ""
    h4 = soup.find("h4", class_="h4_o")
    if h4:
        title = h4.get_text(strip=True)

    # Source platform
    platform = ""
    plat_label = soup.find("label", id="platformName")
    if plat_label:
        platform = plat_label.get_text(strip=True)

    # Parse tabs and their file links
    files = []
    for div in soup.find_all("div", class_="fully_toggle_cont"):
        div_id = div.get("id", "")  # e.g. "div_9001"
        stage_id = div_id.replace("div_", "")

        # Get tab label
        tab_label = ""
        tab_li = soup.find("li", id=f"t_{stage_id}")
        if tab_li:
            tab_label = tab_li.get_text(strip=True)

        for ul in div.find_all("ul", class_="fully_list"):
            for li in ul.find_all("li"):
                a_tag = li.find("a")
                if not a_tag:
                    continue
                onclick = a_tag.get("onclick", "")
                file_title = a_tag.get("title", "") or a_tag.get_text(strip=True)

                # Extract /b/ URL from showDetail(..., 'url')
                m = re.search(r"showDetail\([^,]+,\s*'[^']*',\s*'([^']+)'\)", onclick)
                if not m:
                    m = re.search(r'showDetail\([^,]+,\s*"[^"]*",\s*"([^"]+)"\)', onclick)
                if m:
                    b_url = urljoin(_SITE_ROOT, m.group(1))
                    files.append({
                        "stage_id": stage_id,
                        "stage_name": tab_label or stage_id,
                        "file_title": file_title,
                        "b_url": b_url,
                    })

    metadata = {
        "platform": platform,
        "detail_url": detail_url,
    }
    return title, metadata, files


# ---------------------------------------------------------------------------
# Content page (/b/) — extract text + attachments
# ---------------------------------------------------------------------------

def _parse_content_page(html):
    """Parse the /b/ content page HTML.

    Returns (text_content, attachments) where attachments is list of {filename, url}.
    """
    soup = BeautifulSoup(html, "lxml")

    # Strip script/style
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Find download links (doc, pdf, xls, zip, etc.)
    attachments = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        ext = os.path.splitext(href.split("?")[0].split("#")[0])[1].lower()
        text = a.get_text(strip=True)
        if ext in _ATTACH_EXTENSIONS:
            abs_url = urljoin(_SITE_ROOT, href)
            fname = text or os.path.basename(href.split("?")[0])
            attachments.append({"filename": fname, "url": abs_url})
        elif any(kw in href.lower() for kw in ("download", "attach", "upload")):
            abs_url = urljoin(_SITE_ROOT, href)
            fname = text or os.path.basename(href.split("?")[0])
            if fname:
                attachments.append({"filename": fname, "url": abs_url})

    # Also look for image attachments
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if src and not src.startswith("data:"):
            ext = os.path.splitext(src.split("?")[0])[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                abs_url = urljoin(_SITE_ROOT, src)
                fname = os.path.basename(src.split("?")[0])
                attachments.append({"filename": fname, "url": abs_url})

    # Extract text content
    body = soup.body
    if not body:
        return "", attachments

    # Remove known clutter elements
    for cls in ("location", "header", "footer", "nav", "banner"):
        for el in body.find_all(class_=re.compile(cls, re.I)):
            el.decompose()

    lines = []
    for el in body.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div"]):
        tag = el.name
        text = el.get_text(strip=True)
        if not text:
            continue
        # Skip clutter
        if any(kw in text for kw in ("版权所有", "ICP备", "公网安备", "政府网站工作年度报表")):
            continue
        if len(text) < 3 and tag == "div":
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
        elif tag == "div":
            # Only include div text if it's substantial
            if len(text) > 50:
                lines.append(text)
                lines.append("")
        else:
            # p, span inside p handled
            lines.append(text)
            lines.append("")

    content = "\n".join(lines)

    # Fallback: if no content extracted, just get body text
    if len(content.strip()) < 50:
        body_text = body.get_text(separator="\n", strip=True)
        body_text = re.sub(r'\n{3,}', '\n\n', body_text)
        content = body_text

    return content, attachments


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=60):
    """Download via session (for ggzy.gov.cn URLs)."""
    if file_url.startswith("http://"):
        file_url = file_url.replace("http://", "https://", 1)
    try:
        resp = sess.get(file_url, timeout=timeout, stream=True)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        logging.error("Download error %s: %s", file_url, e)
    return None


def _download_file_urllib(file_url, timeout=60):
    """Download via urllib (fallback for external URLs)."""
    req = urllib.request.Request(file_url, headers=_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if len(data) > 100:
            return data
    except Exception as e:
        logging.error("Download error %s: %s", file_url, e)
    return None


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path, dest_dir):
    """Extract ZIP, return list of extracted file paths. ZIP kept."""
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
# Article processing (per article: /a/ page → /b/ pages → attachments)
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
            title_short = art["title"][:80]
            _safe_print(f"[PROCESS]   [{global_idx}/{len(new_articles)}] {title_short}")
            sys.stdout.flush()

            if not art.get("url"):
                batch_ids.append(art["id"])
                _safe_print(f"           No detail URL, skipping")
                continue

            # Step 1: Fetch /a/ wrapper page
            try:
                resp = http_sess.get(art["url"], timeout=60)
                wrapper_html = resp.text
            except Exception as e:
                logging.error("Failed to fetch /a/ page %s: %s", art["url"], e)
                batch_ids.append(art["id"])
                _safe_print(f"           Failed to fetch detail page")
                continue

            page_title, metadata, file_list = _parse_detail_wrapper(wrapper_html, art["url"])

            # Use API title if page title not found
            final_title = page_title or art["title"]
            date_str = art.get("date_str", "")
            province = art.get("province", "")
            info_type = art.get("info_type", "")
            biz_type = art.get("biz_type", "")

            # Step 2: Fetch each /b/ content page
            all_content_parts = []
            all_attachments = []

            if file_list:
                _safe_print(f"           {len(file_list)} file(s) across tabs")
                sys.stdout.flush()
                for f_info in file_list:
                    try:
                        resp = http_sess.get(f_info["b_url"], timeout=60)
                        b_html = resp.text
                    except Exception as e:
                        logging.error("Failed to fetch /b/ page %s: %s", f_info["b_url"], e)
                        continue

                    content_text, attachments = _parse_content_page(b_html)

                    # Build this file's section
                    section_lines = []
                    if f_info["stage_name"] != f_info["file_title"]:
                        section_lines.append(f"### {f_info['stage_name']} — {f_info['file_title']}\n")
                    else:
                        section_lines.append(f"### {f_info['stage_name']}\n")
                    section_lines.append(content_text)

                    all_content_parts.append("\n".join(section_lines))
                    all_attachments.extend(attachments)

                    _request_delay(*_ARTICLE_DELAY)
            else:
                # No tabs/files found — page may have content directly
                _safe_print(f"           No file tabs, trying direct content extraction")
                content_text, attachments = _parse_content_page(wrapper_html)
                if content_text.strip():
                    all_content_parts.append(content_text)
                all_attachments.extend(attachments)

            # Step 3: Download attachments
            local_files = []
            for att in all_attachments:
                fname = _sanitize_filename(att["filename"])
                date_prefix = date_str[:10].replace("-", "") if date_str else ""
                safe_id = art["id"].replace("/", "_").replace("\\", "_")[:60]
                local_name = f"{date_prefix}_{safe_id}_{fname}" if date_prefix else f"{safe_id}_{fname}"
                local_path = os.path.join(downloads_dir, local_name)

                if os.path.exists(local_path):
                    _safe_print(f"           (cached) {fname}")
                    local_files.append(local_path)
                    continue

                _safe_print(f"           downloading: {fname[:80]}")
                sys.stdout.flush()

                if "ggzy.gov.cn" in att["url"]:
                    blob = _download_file(http_sess, att["url"])
                else:
                    blob = _download_file_urllib(att["url"])

                if blob:
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

            # Step 4: Build markdown
            lines = [f"# {final_title}", ""]
            lines.append(f"**发布日期:** {date_str}")
            if province:
                lines.append(f"**地区:** {province}")
            if info_type:
                lines.append(f"**信息类型:** {info_type}")
            if biz_type:
                lines.append(f"**业务类型:** {biz_type}")
            if metadata.get("platform"):
                lines.append(f"**来源平台:** {metadata['platform']}")
            lines.append(f"**原文链接:** {art['url']}")
            lines.append(f"**网站:** 全国公共资源交易平台")
            lines.append("")

            if all_content_parts:
                lines.append("\n\n---\n\n".join(all_content_parts))
            else:
                lines.append(f"> 内容为空或无法提取")

            # Attachments list
            if all_attachments:
                lines.append("\n\n## 附件\n")
                for att in all_attachments:
                    lines.append(f"- [{att['filename']}]({att['url']})")

            content_md = "\n".join(lines)

            # Save individual markdown
            _save_markdown(content_md, output_dir, art["id"])
            md_parts.append(content_md)
            batch_ids.append(art["id"])
            for lf in local_files:
                all_attachment_files.append((lf, final_title))

            _request_delay(*_ARTICLE_DELAY)

        # ── Batch checkpoint ──
        if md_parts:
            batch_path = os.path.join(output_dir, f"batch_{batch_num:04d}.md")
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
        description="ggzy.gov.cn deal crawler"
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
    parser.add_argument("--max-days", type=int, default=1,
                        help="Max age in days (default: 1 = today only)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds (default: 3300 = 55 min)")
    parser.add_argument("--deals", default=None,
                        help="Comma-separated deal types: 01,02,03,04,05,90 (default: all)")
    # Legacy compatibility
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[GGZY-DEAL] 全国公共资源交易平台 - 交易公开 crawler")
    _safe_print(f"[GGZY-DEAL] Target: {args.target_url}")
    _safe_print(f"[GGZY-DEAL] KB: {args.kb_id}")
    _safe_print(f"[GGZY-DEAL] Task: {args.task_name}")
    _safe_print(f"[GGZY-DEAL] Max days: {args.max_days}")
    _safe_print(f"[GGZY-DEAL] Max runtime: {args.max_runtime}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== GGZY-DEAL crawler started ===")

    global_start_time = time.time()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[GGZY-DEAL] Output directory: {output_dir}\n")
    sys.stdout.flush()

    # State
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(f"[GGZY-DEAL] Previously processed: {len(processed_ids)} article(s)\n")
    sys.stdout.flush()

    # Init HTTP session
    http_sess = _init_session()

    # ── Phase 1: Crawl listing ──
    _safe_print("[GGZY-DEAL] Phase 1: Crawling listing API...\n")
    sys.stdout.flush()

    articles = _crawl_listing(
        http_sess,
        max_days=args.max_days,
        max_articles=args.max_articles,
        start_time=global_start_time,
        max_runtime=args.max_runtime,
    )

    if not articles:
        _safe_print("[GGZY-DEAL] No articles found. Done.")
        return

    _safe_print(f"\n[GGZY-DEAL] {len(articles)} total articles from listing\n")
    sys.stdout.flush()

    # ── Phase 2: Process articles ──
    _safe_print("[GGZY-DEAL] Phase 2: Processing articles...\n")
    sys.stdout.flush()

    total = _process_articles(
        articles, output_dir, args.kb_id, args.tenant_id,
        processed_ids, state, global_start_time, args.max_runtime,
        http_sess,
    )

    elapsed = time.time() - global_start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[GGZY-DEAL] Done: {total} article(s) processed in {elapsed:.0f}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== GGZY-DEAL crawler finished: %d articles ===", total)


if __name__ == "__main__":
    CONSUMER_NAME = "ggzy_deal_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
