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
Crawler for fujian.etrading.cn — 政策法规 (statute) section.

Target:
  - List:   https://fujian.etrading.cn/zcfg/statute.html
  - Detail: https://fujian.etrading.cn/zcfg/{cat}/{date}/{uuid}.html

The list page is server-rendered HTML with links to static detail pages.
Detail pages contain the full content (SSR), including attachment download
links (doc, pdf, zip, etc.). ZIP archives are auto-extracted.

Only 13 items total — a small, focused dataset.

Checkpoint/resume: articles are processed in batches of 5. After each batch,
state is saved and content is uploaded to KB.

Usage:
    python etrading_statute_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://fujian.etrading.cn/ \
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
import zipfile
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
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://fujian.etrading.cn"
_LIST_URL = "{}/zcfg/statute.html".format(_SITE_ROOT)

_SECTION_LABEL = "政策法规"
_SECTION_KEY = "etrading_statute"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

# Anti-crawling
_REQUEST_DELAY_MIN = 0.5
_REQUEST_DELAY_MAX = 1.5

# Batch checkpoint
BATCH_SIZE = 5

_ATTACHMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".csv", ".rtf",
}

_EXT_LAWS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name)
    name = name.strip("._ ")
    return (name or "untitled")[:max_len]


def _fetch_html(url, client=None, retries=3):
    """Fetch an HTML page. PlaywrightHttpClient first, requests fallback."""
    for attempt in range(retries):
        try:
            if client:
                resp = client.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 100:
                    return resp.text
            else:
                resp = requests.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 100:
                    return resp.text
        except Exception as e:
            logging.warning("Fetch %s attempt %d: %s", url, attempt + 1, e)
        if attempt < retries - 1:
            time.sleep((2 ** attempt) + random.uniform(1, 3))
    return None


# ===================================================================
# Listing extraction
# ===================================================================

_DETAIL_URL_RE = re.compile(r'/zcfg/(\d+)/(\d{8})/([a-f0-9-]+)\.html', re.I)
_SUBCAT_URL_RE = re.compile(r'/zcfg/(\d{6})/statute\.html', re.I)


def _extract_subcategory_links(html):
    """Find subcategory pages linked from the main statute page."""
    soup = BeautifulSoup(html, "lxml")
    subs = []
    seen = set()
    for a_tag in soup.find_all("a", href=True):
        href = (a_tag.get("href") or "").strip()
        m = _SUBCAT_URL_RE.search(href)
        if not m:
            continue
        code = m.group(1)
        if code in seen:
            continue
        seen.add(code)
        full_url = href if href.startswith("http") else urljoin(_SITE_ROOT, href)
        subs.append(full_url)
    return subs


def _extract_list_links(html):
    """Find all detail page links in a statute listing HTML page.

    Returns list[dict]: {id, url, title_hint}
    """
    soup = BeautifulSoup(html, "lxml")
    seen = set()
    articles = []

    for a_tag in soup.find_all("a", href=True):
        href = (a_tag.get("href") or "").strip()
        m = _DETAIL_URL_RE.search(href)
        if not m:
            continue

        category = m.group(1)
        date_str = "{}-{}-{}".format(m.group(2)[:4], m.group(2)[4:6], m.group(2)[6:8])
        art_id = m.group(3)[:12]

        if art_id in seen:
            continue
        seen.add(art_id)

        full_url = href if href.startswith("http") else urljoin(_SITE_ROOT, href)
        title_hint = (a_tag.get_text() or "").strip()
        # Clean up title
        title_hint = re.sub(r'\s+', ' ', title_hint)
        # Try to get title from title attribute
        if not title_hint or len(title_hint) < 4:
            title_hint = (a_tag.get("title") or "").strip()

        articles.append({
            "id": art_id,
            "url": full_url,
            "title_hint": title_hint,
            "date_str": date_str,
            "category": category,
        })

    return articles


# ===================================================================
# Detail extraction
# ===================================================================

def _extract_detail(html, page_url):
    """Extract title, date, content, and attachments from a detail page HTML.

    Returns dict: {title, date_str, content_text, attachments: [{url, filename, ext}]}
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Title
    title = ""
    for sel in ["h1", ".title", "[class*='title']", ".bt", "h2", "h3"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break
    if not title:
        title = (soup.title.string or "").strip() if soup.title else ""

    # Date — from text or meta
    date_str = ""
    body_text = soup.body.get_text() if soup.body else ""
    m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', body_text)
    if m:
        date_str = "{}-{:02d}-{:02d}".format(
            int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Content
    content_text = ""
    for sel in [".content", ".article-content", ".detail-content",
                "[class*='content']", "article", "main", ".main", ".page-main"]:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 50:
            content_text = el.get_text(separator="\n", strip=True)
            break
    if not content_text and soup.body:
        # Remove noisy elements
        for tag in soup.select("nav, header, footer, .header, .footer, "
                              ".menu, .nav, .sidebar, .top-header"):
            tag.decompose()
        content_text = soup.body.get_text(separator="\n", strip=True)
    # Clean up excessive whitespace
    content_text = re.sub(r'\n{3,}', '\n\n', content_text)

    # Attachments
    attachments = []
    seen_urls = set()
    for a_tag in soup.find_all("a", href=True):
        href = (a_tag.get("href") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue

        lower_href = href.lower()
        path_part = href.split("?")[0].split("#")[0].lower()
        matched_ext = ""
        for ext in _ATTACHMENT_EXTS:
            if path_part.endswith(ext):
                matched_ext = ext
                break

        if not matched_ext:
            link_text = (a_tag.get_text() or "").strip().lower()
            for ext in _ATTACHMENT_EXTS:
                if link_text.endswith(ext):
                    matched_ext = ext
                    break

        if not matched_ext:
            if not any(kw in lower_href for kw in
                      ("download", "upload", "file", "attachment", "getfile")):
                continue

        full_url = href if href.startswith("http") else urljoin(page_url, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        filename = (a_tag.get_text() or "").strip()
        if not filename:
            filename = href.split("/")[-1].split("?")[0]
        if not filename:
            filename = "attachment" + (matched_ext or "")

        attachments.append({
            "url": full_url,
            "filename": filename,
            "ext": matched_ext,
        })

    return {
        "title": title,
        "date_str": date_str,
        "content_text": content_text,
        "attachments": attachments,
    }


# ===================================================================
# Attachment download
# ===================================================================

def _download_attachment(att_url, dest_dir, filename, pw_client=None, timeout=120):
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(filename)
    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    # Strategy 1: PlaywrightHttpClient.download
    if pw_client:
        try:
            data, ct, fn = pw_client.download(att_url, timeout=timeout)
            if data and len(data) > 100:
                with open(dest_path, "wb") as f:
                    f.write(data)
                return dest_path
        except Exception as e:
            logging.debug("PW download failed for %s: %s", att_url, e)

    # Strategy 2: requests
    try:
        resp = requests.get(att_url, headers=_HEADERS, timeout=timeout, stream=True)
        if resp.status_code == 200 and len(resp.content) > 100:
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return dest_path
    except Exception as e:
        logging.warning("Download failed for %s: %s", att_url, e)

    return None


# ===================================================================
# ZIP extraction
# ===================================================================

def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(os.path.basename(name))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, 'wb') as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
                _safe_print("      [extract] {}".format(safe_name))
        os.remove(zip_path)
    except zipfile.BadZipFile:
        logging.warning("Not a valid ZIP: %s", zip_path)
    except Exception as e:
        logging.warning("ZIP extract error: %s", e)
    return extracted


# ===================================================================
# State management
# ===================================================================

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": [], "completed": False}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _save_markdown(content, output_dir, article_id):
    d = os.path.join(output_dir, "articles")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "{}.md".format(article_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ===================================================================
# KB upload
# ===================================================================

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="general"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b
        def read(self):
            return self.blob

    fo = _FO(os.path.basename(filepath), blob)
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            DocumentService.update_by_id(did, {"parser_id": parser_id})
        except Exception:
            pass
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="fujian.etrading.cn statute crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://fujian.etrading.cn/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ===================================================================
# Main
# ===================================================================

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[ESTATUTE] fujian.etrading.cn 政策法规 crawler")
    _safe_print("[ESTATUTE] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ESTATUTE crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[ESTATUTE] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[ESTATUTE] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[ESTATUTE] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Start client --------------------------------------------------------
    pw_client = PlaywrightHttpClient()
    pw_client.start()

    try:
        # ===================================================================
        # Step 1: Fetch main page → subcategory pages → extract links
        # ===================================================================
        _safe_print("[ESTATUTE] Step 1/3: Fetching listing pages...")
        sys.stdout.flush()

        list_html = _fetch_html(_LIST_URL, client=pw_client)
        if not list_html:
            _safe_print("[ESTATUTE] ERROR: Failed to fetch listing page.")
            sys.stdout.flush()
            return

        # Discover subcategory pages (012001, 012002, etc.)
        sub_urls = _extract_subcategory_links(list_html)
        if not sub_urls:
            # Fallback: use main page only
            sub_urls = [_LIST_URL]
        _safe_print("[ESTATUTE]   Found {} subcategory pages".format(len(sub_urls)))
        sys.stdout.flush()

        # Collect articles from each subcategory (dedup by id)
        all_articles = []
        seen_ids = set()
        for sub_url in sub_urls:
            _safe_print("[ESTATUTE]   Fetching {}".format(sub_url.rsplit("/zcfg/", 1)[-1]))
            sys.stdout.flush()
            _request_delay()
            sub_html = _fetch_html(sub_url, client=pw_client)
            if not sub_html:
                logging.warning("Failed to fetch subcategory: %s", sub_url)
                continue
            for art in _extract_list_links(sub_html):
                if art["id"] not in seen_ids:
                    seen_ids.add(art["id"])
                    all_articles.append(art)

        _safe_print("[ESTATUTE]   Found {} articles total (expected: 13)".format(
            len(all_articles)))
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ESTATUTE] No articles found. Check page structure.")
            return

        # Filter already processed
        new_articles = [a for a in all_articles
                       if a["id"] not in processed_ids]
        _safe_print("[ESTATUTE]   {} new (skipped {} already processed)".format(
            len(new_articles), len(all_articles) - len(new_articles)))
        sys.stdout.flush()

        if not new_articles:
            _safe_print("[ESTATUTE] Nothing new. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            return

        # ===================================================================
        # Step 2: Fetch details + attachments (batch of 5)
        # ===================================================================
        _safe_print("[ESTATUTE] Step 2/3: Fetching {} articles in batches of {}...".format(
            len(new_articles), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_articles)
        success_count = 0
        fail_count = 0
        batch_num = 0

        for batch_start in range(0, total, BATCH_SIZE):
            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                _safe_print("[ESTATUTE]   [{}/{}] {}".format(
                    global_idx, total,
                    art.get("title_hint", art["url"])[:60]))
                sys.stdout.flush()

                # Fetch detail page
                detail_html = _fetch_html(art["url"], client=pw_client)
                if not detail_html:
                    fail_count += 1
                    content_text = "标题: {}\nURL: {}".format(
                        art.get("title_hint", ""), art["url"])
                    attachments = []
                    title = art.get("title_hint", "无标题")
                    date_str = art.get("date_str", "")
                else:
                    detail = _extract_detail(detail_html, art["url"])
                    content_text = detail["content_text"]
                    attachments = detail["attachments"]
                    title = detail["title"] or art.get("title_hint") or "无标题"
                    date_str = detail["date_str"] or art.get("date_str", "")

                if not content_text:
                    fail_count += 1
                    content_text = "标题: {}\nURL: {}".format(title, art["url"])

                # ---- Download attachments ----
                local_att_files = []
                if attachments:
                    att_dir = os.path.join(output_dir, "attachments", art["id"])
                    for att in attachments:
                        _safe_print("      [dl] {}".format(
                            att.get("filename", "")[:50]))
                        sys.stdout.flush()

                        fp = _download_attachment(
                            att["url"], att_dir,
                            att.get("filename", "unknown"),
                            pw_client=pw_client,
                        )
                        if fp:
                            local_att_files.append(fp)
                            ext = os.path.splitext(fp)[1].lower()
                            is_zip = ext == ".zip"
                            if not is_zip:
                                try:
                                    with open(fp, "rb") as f:
                                        is_zip = f.read(4) == b"PK\x03\x04"
                                except Exception:
                                    pass
                            if is_zip:
                                extracted = _extract_zip(fp)
                                local_att_files.extend(extracted)

                # ---- Build markdown ----
                lines = [
                    "# {}".format(title),
                    "**栏目:** {}".format(_SECTION_LABEL),
                    "**日期:** {}".format(date_str),
                    "**URL:** {}".format(art["url"]),
                    "",
                    "## 正文",
                    "",
                    content_text,
                    "",
                ]

                if attachments:
                    lines.append("## 附件列表")
                    lines.append("")
                    for i, att in enumerate(attachments, 1):
                        lines.append("{}. **{}** — [{}]({})".format(
                            i, att.get("filename", "unknown"),
                            att.get("ext", "").upper().lstrip("."),
                            att.get("url", ""),
                        ))
                    lines.append("")

                md_content = "\n".join(lines)
                _save_markdown(md_content, output_dir, art["id"])
                md_parts.append(md_content)
                batch_ids.append(art["id"])

                art_md_path = os.path.join(
                    output_dir, "articles", "{}.md".format(art["id"]))
                batch_files.append((art_md_path, "general"))
                for att_path in local_att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in _EXT_LAWS else "general"
                    batch_files.append((att_path, pid))

                success_count += 1
                _request_delay()

            # ── Checkpoint ──
            if md_parts:
                batch_path = os.path.join(output_dir,
                    "batch_{:03d}.md".format(batch_num))
                with open(batch_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_parts))

                processed_ids.update(batch_ids)
                state["processed_ids"] = list(processed_ids)
                _save_state(output_dir, state)

                if args.kb_id:
                    try:
                        _upload_to_kb(batch_path, args.kb_id, args.tenant_id)
                        for fp, parser in batch_files:
                            if os.path.exists(fp):
                                _upload_to_kb(fp, args.kb_id, args.tenant_id,
                                             parser_id=parser)
                    except Exception as e:
                        _safe_print("[ESTATUTE]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload batch %d: %s", batch_num, e)

                _safe_print("[ESTATUTE]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

    finally:
        pw_client.stop()

    # -- Mark complete -------------------------------------------------------
    state["completed"] = True
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[ESTATUTE] Done: {} articles ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[ESTATUTE] Total found on listing: {}".format(len(all_articles)))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== ESTATUTE crawler finished: %d articles ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "etrading_statute_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
