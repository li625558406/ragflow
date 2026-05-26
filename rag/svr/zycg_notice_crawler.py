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
Dedicated web crawler for zycg.gov.cn procurement/bidding notice sections.

Covers four sections that share the same REST API:
  ddwtxm — 单位委托项目
  dzmc   — 电子卖场
  xj     — 询价
  gcjz   — 工程建筑

Site characteristics
────────────────────
All content is loaded via AJAX from the FreeCMS REST API:

  • Listing  →  /freecms/rest/v1/notice/selectInfoMore.do
    Parameters: siteId, channel, noticeType, implementWay,
                operationStartTime, operationEndTime, currPage, pageSize
  • Details  →  HTML detail pages at ggxx/info/YYYY/UUID.html
  • Files    →  /freecms/rest/v1/notice/selectNoticeDocInfo.do  (file list)
                /freecms/download/api/mongo-server/file/download?file_id=...
  • Auth     →  JSESSIONID cookie (obtained by visiting any listing page first)

No access_token or Playwright required — a plain requests.Session suffices
after obtaining the JSESSIONID cookie from a listing page visit.

Checkpoint/resume: each section is processed independently (list → details
→ download → upload → save state).  If the 3600s task timeout kills a run
mid-way, the next trigger resumes from the last checkpoint.

Usage (typically spawned by task_executor):
    python zycg_notice_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.zycg.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME> \
        --section ddwtxm,dzmc,xj,gcjz \
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
_SITE_ROOT = "https://www.zycg.gov.cn"
_BASE_PATH = "/freecms/site/zygjjgzfcgzx"

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
# Section configuration
# ---------------------------------------------------------------------------
# Each section differs only in channel, noticeType, and implementWay.
_SECTIONS = {
    "ddwtxm": {
        "label": "单位委托项目",
        "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
        "notice_type": "57,1,2,3,61",
        "implement_way": "1",
    },
    "dzmc": {
        "label": "电子卖场",
        "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
        "notice_type": "59,2,61,3,31,32",
        "implement_way": "1",
    },
    "xj": {
        "label": "询价",
        "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
        "notice_type": "59,2,61,40",
        "implement_way": "21",
    },
    "gcjz": {
        "label": "工程建筑",
        "channel": "2d8d9acf-8c1c-4d29-952d-e31927f4c5c7,31d01f79-9245-42cf-8d1a-fd1e8f414afc,614fe3a4-d550-4316-a126-7c909291415f",
        "notice_type": "78,79,80",
        "implement_way": "7",
    },
}

_SITE_ID = "6f5243ee-d4d9-4b69-abbd-1e40576ccd7d"

# API endpoints
_LISTING_API = "/freecms/rest/v1/notice/selectInfoMore.do"
_FILE_LIST_API = "/freecms/rest/v1/notice/selectNoticeDocInfo.do"


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


_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(\s+\d{1,2}:\d{1,2}(:\d{1,2})?)?$")


# ---------------------------------------------------------------------------
# HTTP session management
# ---------------------------------------------------------------------------

def _init_session():
    """Create a requests.Session with JSESSIONID cookie from zycg.gov.cn."""
    sess = _requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get(f"{_SITE_ROOT}{_BASE_PATH}/ddwtxm/index.html", timeout=15)
    except Exception as e:
        logging.warning("Failed to get session cookie: %s", e)
    return sess


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _fetch_listing_page(sess, section_cfg, start_date, end_date, page=1):
    """Fetch one page of the listing API.

    Returns list[dict]: {id, title, url, date_str, section}.
    """
    params = {
        "siteId": _SITE_ID,
        "channel": section_cfg["channel"],
        "currPage": str(page),
        "pageSize": "15",
        "noticeType": section_cfg["notice_type"],
        "implementWay": section_cfg.get("implement_way", "1"),
        "operationStartTime": start_date,
        "operationEndTime": end_date,
    }

    try:
        resp = sess.get(f"{_SITE_ROOT}{_LISTING_API}", params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        logging.error("Listing API error for %s page %d: %s",
                      section_cfg["label"], page, e)
        return []

    if data.get("code") != "200":
        return []

    items = data.get("data", [])
    articles = []
    for item in items:
        art_id = item.get("id", "").strip()
        title = item.get("title", "").strip()
        if not art_id or not title:
            continue

        pageurl = item.get("pageurl", "").strip()
        detail_url = urljoin(_SITE_ROOT, pageurl) if pageurl else ""

        date_str = item.get("addtimeStr", "").strip()

        articles.append({
            "id": art_id,
            "title": title,
            "url": detail_url,
            "date_str": date_str,
            "section": section_cfg["label"],
        })

    return articles


def _crawl_listing(sess, section_cfg, start_date, end_date):
    """Fetch all pages of the listing API for one section."""
    label = section_cfg["label"]
    all_articles = []
    seen_ids = set()

    for page in range(1, 200):  # safety cap
        articles = _fetch_listing_page(
            sess, section_cfg, start_date, end_date, page=page
        )
        if not articles:
            _safe_print(f"[{label}]   Page {page}: empty, stopping")
            break

        new_count = 0
        for art in articles:
            if art["id"] not in seen_ids:
                seen_ids.add(art["id"])
                all_articles.append(art)
                new_count += 1

        _safe_print(f"[{label}]   Page {page}: {len(articles)} items ({new_count} new)")

        if len(articles) < 15:
            break

        _request_delay()

    _safe_print(f"[{label}]   Total: {len(all_articles)} articles")
    return all_articles


# ---------------------------------------------------------------------------
# File list API
# ---------------------------------------------------------------------------

def _fetch_file_list(sess, article_id):
    """Fetch the list of attached files for a notice.

    Returns list[dict] with keys: fileUrl, fileName.
    """
    params = {"currPage": 1, "pageSize": 20, "id": article_id}
    try:
        resp = sess.get(f"{_SITE_ROOT}{_FILE_LIST_API}", params=params, timeout=30)
        data = resp.json()
        if data.get("code") == "200":
            return data.get("data", [])
    except Exception as e:
        logging.error("File list API error for %s: %s", article_id, e)
    return []


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=60):
    """Download a binary file through the session.

    Returns (bytes, content_type) or (None, None).
    """
    if file_url.startswith("http://"):
        file_url = file_url.replace("http://", "https://", 1)

    try:
        resp = sess.get(file_url, timeout=timeout, stream=True)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content, resp.headers.get("Content-Type", "")
    except Exception as e:
        logging.error("Download error %s: %s", file_url, e)
    return None, None


def _download_file_urllib(file_url, timeout=60):
    """Download a binary file via urllib (fallback for non-session URLs)."""
    req = urllib.request.Request(file_url, headers=_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read(), file_url
    except Exception as e:
        logging.error("Download error %s: %s", file_url, e)
        return None, None


# ---------------------------------------------------------------------------
# Detail page extraction
# ---------------------------------------------------------------------------

def _extract_detail(html, detail_url, section_label):
    """Parse a zycg ggxx detail page and return (content_md, attachments, metadata)."""
    soup = BeautifulSoup(html, "lxml")

    # ── Strip clutter ──
    for tag in soup.find_all(
        ["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]
    ):
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
    dm = re.search(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})", body_text)
    if dm:
        date_str = dm.group(1)
    else:
        dm = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", body_text)
        if dm:
            date_str = dm.group(1)

    # ── Content area ──
    content_div = None
    for cls in ("info-content", "article-content", "content", "info-text", "article"):
        content_div = soup.find("div", class_=re.compile(cls, re.I))
        if content_div and len(content_div.get_text(strip=True)) > 100:
            break
    if not content_div:
        content_div = soup.body

    # ── HTML-embedded attachments ──
    html_attachments = []
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
                html_attachments.append({"filename": fname, "url": file_url})

    # ── Build Markdown ──
    lines = [f"# {title}", ""]
    if date_str:
        lines.append(f"**日期:** {date_str}")
    lines.append(f"**来源:** {detail_url}")
    lines.append(f"**栏目:** {section_label}")
    lines.append(f"**网站:** 中央国家机关政府采购中心")
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

    # Fallback text extraction
    if len(lines) < 8 and content_div:
        text = content_div.get_text(separator="\n", strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        lines.append(text)

    content_md = "\n".join(lines)
    metadata = {"title": title, "date_str": date_str}
    return content_md, html_attachments, metadata


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
    return {"processed_ids": [], "completed_sections": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d sections done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_sections", [])))


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
# Section-level processing (list → details → download → upload)
# ---------------------------------------------------------------------------

def _process_articles(articles, output_dir, kb_id, tenant_id,
                      processed_ids, state, start_time, max_runtime,
                      http_sess):
    """Process articles in batches of 10 with checkpoint after each batch."""
    section_key = articles[0]["section"] if articles else "unknown"
    label = section_key

    new_articles = [a for a in articles if a["id"] not in processed_ids]
    if not new_articles:
        _safe_print(f"[{label}] All articles already processed, nothing to do.")
        sys.stdout.flush()
        return 0

    _safe_print(f"[{label}] {len(new_articles)} new article(s) to process\n")
    sys.stdout.flush()

    BATCH_SIZE = 10
    total_processed = 0
    batch_num = 0

    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    for batch_start in range(0, len(new_articles), BATCH_SIZE):
        # ── Time-bounded check ──
        elapsed = time.time() - start_time
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                f"\n[{label}] Runtime {elapsed:.0f}s, "
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

        _safe_print(f"[{label}] === Batch {batch_num} ({len(batch)} articles) ===")
        sys.stdout.flush()

        for idx, art in enumerate(batch, 1):
            global_idx = batch_start + idx
            title_short = art["title"][:70]
            _safe_print(f"[{label}]   [{global_idx}/{len(new_articles)}] {title_short}")
            sys.stdout.flush()

            if not art.get("url"):
                batch_ids.append(art["id"])
                _safe_print(f"         No detail URL, skipping")
                continue

            # Step 1: Fetch detail page HTML
            try:
                resp = http_sess.get(art["url"], timeout=30)
                html = resp.text
            except Exception as e:
                logging.error("Detail page error for %s: %s", art["url"], e)
                batch_ids.append(art["id"])
                _safe_print(f"         Failed to fetch detail page, skipping")
                continue

            if not html:
                batch_ids.append(art["id"])
                _safe_print(f"         Empty detail page, skipping")
                continue

            # Step 2: Extract content + HTML attachments
            content_md, html_attachments, metadata = _extract_detail(
                html, art["url"], art.get("section", label)
            )
            if not content_md:
                batch_ids.append(art["id"])
                continue

            art_title = metadata.get("title") or art["title"]
            art_date = metadata.get("date_str") or art.get("date_str", "")

            # Step 3: Merge file list from API with HTML attachments
            attachment_list = list(html_attachments)
            api_files = _fetch_file_list(http_sess, art["id"])
            existing_urls = {a["url"] for a in attachment_list}
            for f in api_files:
                file_url = f.get("fileUrl", "").strip()
                file_name = f.get("fileName", "document").strip()
                if not file_url:
                    continue
                abs_url = urljoin(_SITE_ROOT, file_url)
                if abs_url not in existing_urls:
                    attachment_list.append({
                        "filename": file_name,
                        "url": abs_url,
                    })

            # Step 4: Download attachments
            local_files = []
            if attachment_list:
                _safe_print(f"         {len(attachment_list)} attachment(s)")
                sys.stdout.flush()
                for att in attachment_list:
                    fname = _sanitize_filename(att["filename"])
                    date_prefix = art_date[:10].replace("-", "") if art_date else ""
                    safe_id = art["id"].replace("/", "_").replace("\\", "_")[:60]
                    local_name = f"{date_prefix}_{safe_id}_{fname}" if date_prefix else f"{safe_id}_{fname}"
                    local_path = os.path.join(downloads_dir, local_name)

                    if os.path.exists(local_path):
                        _safe_print(f"         (cached) {fname}")
                        local_files.append(local_path)
                        continue

                    _safe_print(f"         downloading: {fname}")
                    sys.stdout.flush()

                    # Use session download for API file URLs, urllib for direct upload URLs
                    if "mongo-server/file/download" in att["url"] or "download/api" in att["url"]:
                        blob, _ = _download_file(http_sess, att["url"])
                    else:
                        blob, _ = _download_file_urllib(att["url"])

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
                        _safe_print(f"         download failed for {fname} (empty or too small)")

            # Add attachment links to markdown
            if attachment_list:
                content_md += "\n\n## 附件\n\n"
                for att in attachment_list:
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
            section_slug = section_key.replace(" ", "_")[:20]
            batch_md_path = os.path.join(
                output_dir, f"{section_slug}_batch_{batch_num:03d}.md"
            )
            with open(batch_md_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                try:
                    _upload_to_kb(batch_md_path, kb_id, tenant_id, parser_id="laws")
                    _safe_print(f"[{label}]   Batch {batch_num} markdown uploaded to KB")
                except Exception as e:
                    _safe_print(f"[{label}]   Batch {batch_num} upload failed: {e}")

            total_processed += len(md_parts)
            _safe_print(f"[{label}]   Batch {batch_num} done ({total_processed}/{len(new_articles)})\n")
            sys.stdout.flush()

        # Upload attachments for this batch
        if all_attachment_files and kb_id:
            _safe_print(f"[{label}]   Uploading {len(all_attachment_files)} attachment(s)...")
            sys.stdout.flush()
            for local_path, _art_title in all_attachment_files:
                ext = os.path.splitext(local_path)[1].lower()
                pid = "laws" if ext in _LAWS_EXTENSIONS else "general"
                try:
                    _upload_to_kb(local_path, kb_id, tenant_id, parser_id=pid)
                except Exception as e:
                    _safe_print(f"         Upload error: {os.path.basename(local_path)}: {e}")

    # Mark section complete
    if total_processed > 0:
        state.setdefault("completed_sections", [])
        if section_key not in state["completed_sections"]:
            state["completed_sections"].append(section_key)
            _save_state(output_dir, state)

    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="zycg.gov.cn notice crawler (ddwtxm, dzmc, xj, gcjz)"
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
                        help="Comma-separated sections: ddwtxm,dzmc,xj,gcjz (default: all)")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=1,
                        help="Max age in days for articles (default: 1 = today only)")
    parser.add_argument("--max-runtime", type=int, default=3300,
                        help="Max runtime in seconds before graceful stop (default: 3300 = 55 min)")
    # Legacy compatibility
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None, help="Unused (no auth needed)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print(f"\n{'='*60}")
    _safe_print("[ZYCG-NOTICE] 中央国家机关政府采购中心 - 采购公告 crawler")
    _safe_print(f"[ZYCG-NOTICE] Target: {args.target_url}")
    _safe_print(f"[ZYCG-NOTICE] KB: {args.kb_id}")
    _safe_print(f"[ZYCG-NOTICE] Task: {args.task_name}")
    _safe_print(f"[ZYCG-NOTICE] Max days: {args.max_days}")
    _safe_print(f"[ZYCG-NOTICE] Max runtime: {args.max_runtime}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZYCG-NOTICE crawler started ===")

    global_start_time = time.time()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZYCG-NOTICE] Output directory: {output_dir}\n")
    sys.stdout.flush()

    # ── Select sections ──
    if args.section:
        selected = {}
        for s in args.section.split(","):
            s = s.strip()
            if s in _SECTIONS:
                selected[s] = _SECTIONS[s]
        if not selected:
            _safe_print(f"[ZYCG-NOTICE] No matching sections for '{args.section}', using all")
            selected = dict(_SECTIONS)
    else:
        selected = dict(_SECTIONS)

    _safe_print(f"[ZYCG-NOTICE] Sections to crawl:")
    for slug, cfg in selected.items():
        _safe_print(f"         - {cfg['label']} ({slug})")
    _safe_print("")
    sys.stdout.flush()

    # ── State ──
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_sections": []
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_sections = set(state.get("completed_sections", []))
    _safe_print(f"[ZYCG-NOTICE] Previously processed: {len(processed_ids)} article(s)")
    _safe_print(f"[ZYCG-NOTICE] Completed sections: {len(completed_sections)}\n")
    sys.stdout.flush()

    # ── Date range ──
    today = datetime.now()
    start_date = (today - timedelta(days=args.max_days)).strftime("%Y-%m-%d 00:00:00")
    end_date = today.strftime("%Y-%m-%d 23:59:59")
    _safe_print(f"[ZYCG-NOTICE] Date range: {start_date} ~ {end_date}\n")
    sys.stdout.flush()

    # ── Init HTTP session ──
    http_sess = _init_session()

    total_processed = 0

    # ── Process each section ──
    for slug, cfg in selected.items():
        label = cfg["label"]
        section_key = label  # use label as state key

        if section_key in completed_sections:
            _safe_print(f"[ZYCG-NOTICE] === {label} (SKIPPED, already completed) ===\n")
            sys.stdout.flush()
            continue

        _safe_print(f"[ZYCG-NOTICE] === {label} ===")
        sys.stdout.flush()

        # ── Step 1: Crawl listing ──
        _safe_print(f"[{label}] Step 1/3: Crawling listing API...")
        sys.stdout.flush()

        articles = _crawl_listing(http_sess, cfg, start_date, end_date)

        if not articles:
            _safe_print(f"[{label}] No articles found.\n")
            sys.stdout.flush()
            completed_sections.add(section_key)
            state["completed_sections"] = list(completed_sections)
            _save_state(output_dir, state)
            continue

        if args.max_articles and len(articles) > args.max_articles:
            articles = articles[:args.max_articles]
            _safe_print(f"[{label}] Limited to {args.max_articles} articles")

        # ── Step 2 + 3: Process articles ──
        _safe_print(f"[{label}] Step 2/3: Processing {len(articles)} articles...\n")
        sys.stdout.flush()

        n = _process_articles(
            articles, output_dir, args.kb_id, args.tenant_id,
            processed_ids, state, global_start_time, args.max_runtime,
            http_sess,
        )
        total_processed += n

        # Mark section complete
        completed_sections.add(section_key)
        state["completed_sections"] = list(completed_sections)
        _save_state(output_dir, state)

    # ── Done ──
    elapsed = time.time() - global_start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"[ZYCG-NOTICE] Done: {total_processed} article(s) processed in {elapsed:.0f}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== ZYCG-NOTICE crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "zycg_notice_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
