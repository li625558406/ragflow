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
Crawler for www.enjoy5191.com — 政策法规 (policies/regulations) news with attachments.

Target:
  - List:   https://www.enjoy5191.com/views/public/news.html
            ?pid=067CF385-AD64-43FD-AFC4-D1D3FB41D726
            &typeid=6C4073D7-8C93-4FAC-9E3D-937D975A5BC0
  - Detail: https://www.enjoy5191.com/views/public/article.html
            ?id=<ID>&typeid=<TYPEID>&pid=<PID>

Data is fetched via the REST API at /api/GetDataHandler.ashx:
  - Listing:  Web.GetNewsList  (params: type, page, rows)
  - Detail:   Web.GetTopOneArticle (params: id)

All content (including attachments) is hosted on www.enjoy5191.com.
This crawler uses PlaywrightHttpClient for API calls and attachment downloads.
ZIP archives are auto-extracted.

Checkpoint/resume: articles are processed in batches of 10. After each batch,
state is saved and content is uploaded to KB. If the 3600s task timeout kills
the run, the next trigger resumes from where it left off.

Usage:
    python enjoy5191_policy_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.enjoy5191.com/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import hashlib
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

import requests as _requests
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
_SITE_ROOT = "https://www.enjoy5191.com"
_LIST_PAGE_URL = (
    "{}/views/public/news.html"
    "?pid=067CF385-AD64-43FD-AFC4-D1D3FB41D726"
    "&typeid=6C4073D7-8C93-4FAC-9E3D-937D975A5BC0"
).format(_SITE_ROOT)

_API_BASE = "{}/api/GetDataHandler.ashx".format(_SITE_ROOT)

# 政策法规 type ID
_POLICY_TYPE_ID = "2B866807-C73A-4934-8740-6569E3CD5926"
_POLICY_PID = "067CF385-AD64-43FD-AFC4-D1D3FB41D726"
_SECTION_LABEL = "政策法规"
_SECTION_KEY = "enjoy5191_policy"

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
    "X-Requested-With": "XMLHttpRequest",
}

# Anti-crawling
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

# Batch checkpoint
BATCH_SIZE = 10

# Attachment file extensions
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
    if not name:
        name = "untitled"
    return name[:max_len]


# ===================================================================
# API calls
# ===================================================================


def _api_call(client, method, params=None, retries=3):
    """POST to GetDataHandler.ashx via PlaywrightHttpClient.

    Returns the data list on success, or None.
    """
    body = {"method": method}
    if params:
        body.update(params)

    for attempt in range(1, retries + 1):
        try:
            resp = client.post(
                _API_BASE,
                data=body,
                headers={"X-Requested-With": "XMLHttpRequest",
                        "User-Agent": _USER_AGENT},
                timeout=30,
            )
            result = resp.json()
            if result.get("res") == "1":
                return result.get("data", [])
            logging.warning(
                "API %s -> res=%s msg=%s (attempt %d/%d)",
                method, result.get("res"), result.get("msg"),
                attempt, retries,
            )
        except Exception as e:
            logging.warning("API %s failed: %s (attempt %d/%d)",
                          method, e, attempt, retries)
        if attempt < retries:
            time.sleep((2 ** attempt) + random.uniform(1, 3))
    return None


def _fetch_listing(client, page=1, page_size=20):
    """Fetch one page of 政策法规 article summaries."""
    return _api_call(client, "Web.GetNewsList", {
        "type": _POLICY_TYPE_ID,
        "page": page,
        "rows": page_size,
    }) or []


def _fetch_detail(client, article_id):
    """Fetch full article detail (HTML content)."""
    rows = _api_call(client, "Web.GetTopOneArticle", {"id": article_id})
    if not rows:
        return None
    return rows[0]


# ===================================================================
# HTML parsing
# ===================================================================


def _parse_detail_html(html_content, base_url=_SITE_ROOT):
    """Parse article HTML content, extract text and attachment links.

    Returns (text_content, attachments_list).
      text_content: plain text extracted from HTML
      attachments_list: [{url, filename, ext}]
    """
    if not html_content or not html_content.strip():
        return "", []

    soup = BeautifulSoup(html_content, "lxml")

    # Strip scripts and styles
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Extract text
    text_parts = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                             "li", "blockquote", "pre", "div", "span",
                             "section", "td", "th"]):
        # Skip children of already processed block elements
        if el.name in ("span",) and el.find_parent(
            ["p", "h1", "h2", "h3", "li", "div", "td", "th"]
        ):
            continue

        direct_texts = []
        for child in el.children:
            if child.name is None:
                t = (child.string or "").strip()
                if t:
                    direct_texts.append(t)

        if not direct_texts:
            text = el.get_text(strip=True)
        else:
            text = " ".join(direct_texts)

        if not text:
            continue

        if el.name == "h1":
            text_parts.append("\n# {}\n".format(text))
        elif el.name == "h2":
            text_parts.append("\n## {}\n".format(text))
        elif el.name == "h3":
            text_parts.append("\n### {}\n".format(text))
        elif el.name in ("h4", "h5", "h6"):
            text_parts.append("\n**{}**\n".format(text))
        elif el.name == "blockquote":
            text_parts.append("> {}".format(text))
        elif el.name == "li":
            text_parts.append("- {}".format(text))
        elif el.name == "pre":
            text_parts.append("```\n{}\n```".format(text))
        elif el.name == "p":
            text_parts.append(text)
        elif el.name in ("div", "section"):
            # Only add if not a container of other processed elements
            if not el.find(["p", "h1", "h2", "h3", "h4", "li", "table"]):
                text_parts.append(text)
            else:
                text_parts.append(text)

    text_content = "\n\n".join(text_parts)
    text_content = re.sub(r"\n{3,}", "\n\n", text_content)

    # Extract attachment links
    attachments = []
    seen_urls = set()
    for a_tag in soup.find_all("a", href=True):
        href = (a_tag.get("href") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        if seen_urls.add(href):
            continue

        # Determine file extension
        path_part = href.split("?")[0].split("#")[0].lower()
        matched_ext = ""
        for ext in _ATTACHMENT_EXTS:
            if path_part.endswith(ext):
                matched_ext = ext
                break

        if not matched_ext:
            # Check if link text has a known extension
            link_text = (a_tag.get_text() or "").strip().lower()
            for ext in _ATTACHMENT_EXTS:
                if link_text.endswith(ext):
                    matched_ext = ext
                    break

        if not matched_ext:
            # Check for download/file/upload keywords in href
            if not any(kw in href.lower() for kw in
                      ("download", "upload", "file", "attachment", "getfile")):
                continue

        # Resolve relative URLs
        full_url = href
        if not href.startswith("http"):
            full_url = urljoin(base_url, href)

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

    return text_content, attachments


# ===================================================================
# Attachment download
# ===================================================================


def _download_attachment(client, att_url, dest_dir, filename, timeout=120):
    """Download an attachment via PlaywrightHttpClient.

    Returns local path or None.
    """
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(filename, max_len=150)
    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    try:
        file_bytes, content_type, content_filename = client.download(
            att_url, timeout=timeout
        )
        if file_bytes and len(file_bytes) > 100:
            with open(dest_path, "wb") as f:
                f.write(file_bytes)
            return dest_path
        else:
            logging.warning("Download empty/error for %s: %d bytes",
                          att_url, len(file_bytes or b""))
            return None
    except Exception as e:
        logging.warning("Download failed for %s: %s", att_url, e)

        # Fallback: requests with common browser headers
        try:
            resp = _requests.get(att_url, headers=_HEADERS,
                               timeout=timeout, stream=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                return dest_path
        except Exception:
            pass

        return None


# ===================================================================
# ZIP extraction
# ===================================================================


def _extract_zip(zip_path):
    """Extract a ZIP file, remove original, return extracted file paths."""
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(os.path.basename(name),
                                               max_len=150)
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
        logging.warning("ZIP extract error for %s: %s",
                      os.path.basename(zip_path), e)
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
    logging.info("State saved (%d IDs, completed=%s)",
                 len(state.get("processed_ids", [])),
                 state.get("completed", False))


def _save_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id))
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

    class _FileObj:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b
        def read(self):
            return self.blob

    fo = _FileObj(os.path.basename(filepath), blob)
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
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ===================================================================
# CLI
# ===================================================================


def parse_args():
    p = argparse.ArgumentParser(
        description="enjoy5191.com 政策法规 news crawler with attachments"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://www.enjoy5191.com/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-pages", type=int, default=50,
                   help="Max pages to crawl (default: 50)")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop (default: 3300)")
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
    _safe_print("[EPOLICY] enjoy5191 政策法规 crawler with attachments")
    _safe_print("[EPOLICY] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== EPOLICY crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[EPOLICY] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[EPOLICY] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[EPOLICY] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Start PlaywrightHttpClient ------------------------------------------
    client = PlaywrightHttpClient()
    client.start()
    epolicy_start = time.time()

    try:
        # ===================================================================
        # Step 1: Fetch all listing pages
        # ===================================================================
        _safe_print("[EPOLICY] Step 1/3: Fetching 政策法规 listing...")
        sys.stdout.flush()

        all_articles = []
        page_num = 1
        page_size = 20

        while page_num <= args.max_pages:
            rows = _fetch_listing(client, page=page_num, page_size=page_size)
            if not rows:
                _safe_print("[EPOLICY]   Page {} empty, stopping.".format(page_num))
                break

            for row in rows:
                art_id = str(row.get("ID", ""))
                title = (row.get("TITLE") or "").strip()
                if not art_id or not title:
                    continue

                all_articles.append({
                    "id": art_id,
                    "title": title,
                    "date_str": (row.get("TM") or "")[:19],
                })

            _safe_print("[EPOLICY]   Page {}: {} items (total: {})".format(
                page_num, len(rows), len(all_articles)))
            sys.stdout.flush()

            if len(rows) < page_size:
                break

            page_num += 1
            _request_delay()

        _safe_print("[EPOLICY]   Total articles: {}".format(len(all_articles)))
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[EPOLICY] No articles found. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            return

        # Filter already processed
        new_articles = [a for a in all_articles
                       if a["id"] not in processed_ids]
        _safe_print("[EPOLICY]   {} new (skipped {} already processed)".format(
            len(new_articles), len(all_articles) - len(new_articles)))
        sys.stdout.flush()

        if not new_articles:
            _safe_print("[EPOLICY] Nothing new. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            return

        # ===================================================================
        # Step 2: Fetch details + parse attachments (batch of 10)
        # ===================================================================
        _safe_print("[EPOLICY] Step 2/3: Fetching {} articles in batches of {}...".format(
            len(new_articles), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_articles)
        success_count = 0
        fail_count = 0
        batch_num = 0

        for batch_start in range(0, total, BATCH_SIZE):
            # ── Runtime check for graceful stop ──
            elapsed = time.time() - epolicy_start
            if elapsed > args.max_runtime - 120:
                _safe_print("[EPOLICY]   Runtime {:.0f}s, stopping early. "
                          "Next run will resume.".format(elapsed))
                break

            batch = new_articles[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []

            for idx, art in enumerate(batch, 1):
                global_idx = batch_start + idx
                title_preview = art["title"][:60] if art["title"] else "(no title)"
                _safe_print("[EPOLICY]   [{}/{}] {}".format(
                    global_idx, total, title_preview))
                sys.stdout.flush()

                # Fetch detail via API
                detail = _fetch_detail(client, art["id"])
                if not detail:
                    fail_count += 1
                    content_text = "标题: {}\n日期: {}".format(
                        art["title"], art.get("date_str", ""))
                    attachments = []
                else:
                    content_html = detail.get("CONTENTS") or ""
                    content_text, attachments = _parse_detail_html(content_html)
                    if not content_text:
                        # Fallback: just strip HTML
                        try:
                            soup = BeautifulSoup(content_html, "lxml")
                            content_text = soup.get_text(separator="\n", strip=True)
                        except Exception:
                            content_text = content_html
                    if detail.get("TITLE"):
                        art["title"] = detail["TITLE"]
                    if detail.get("TM"):
                        art["date_str"] = str(detail["TM"])[:19]

                if not content_text:
                    fail_count += 1
                    content_text = "标题: {}\n日期: {}".format(
                        art["title"], art.get("date_str", ""))

                # ---- Download attachments ----
                local_att_files = []
                if attachments:
                    att_dir = os.path.join(output_dir, "attachments", art["id"])
                    for att in attachments:
                        _safe_print("      [dl] {}".format(
                            att.get("filename", "")[:50]))
                        sys.stdout.flush()

                        fp = _download_attachment(
                            client,
                            att["url"],
                            att_dir,
                            att.get("filename", "unknown"),
                        )
                        if fp:
                            local_att_files.append(fp)
                            # Check + extract ZIP
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
                date_str = art.get("date_str", "")
                detail_url = (
                    "{}/views/public/article.html"
                    "?id={}&typeid={}&pid={}"
                ).format(_SITE_ROOT, art["id"], _POLICY_TYPE_ID, _POLICY_PID)

                lines = [
                    "# {}".format(art["title"]),
                    "**栏目:** {}".format(_SECTION_LABEL),
                    "**日期:** {}".format(date_str),
                    "**URL:** {}".format(detail_url),
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

                # Collect files for upload
                article_md_path = os.path.join(
                    output_dir, "articles", "{}.md".format(art["id"]))
                batch_files.append((article_md_path, "general"))
                for att_path in local_att_files:
                    ext = os.path.splitext(att_path)[1].lower()
                    pid = "laws" if ext in _EXT_LAWS else "general"
                    batch_files.append((att_path, pid))

                success_count += 1
                _request_delay()

            # ── Checkpoint: save batch + upload + update state ──
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
                        _safe_print("[EPOLICY]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload failed for batch %d: %s",
                                    batch_num, e)

                _safe_print("[EPOLICY]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

    finally:
        client.stop()

    # -- Mark complete -------------------------------------------------------
    state["completed"] = True
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[EPOLICY] Done: {} articles processed ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[EPOLICY] Total articles found: {}".format(len(all_articles)))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== EPOLICY crawler finished: %d articles ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "enjoy5191_policy_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
