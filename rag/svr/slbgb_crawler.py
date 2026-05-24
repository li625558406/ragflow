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
Dedicated web crawler for www.mwr.gov.cn/zw/slbgb/ (水利部公报).

The 水利部公报 (Water Resources Bulletin) page lists each issue as a direct
PDF file link — there are NO HTML detail pages.  Each item is a PDF of the
full bulletin issue.  Only current-year issues are collected.

Site characteristics
--------------------
  - Pure SSR HTML — requests + BeautifulSoup, no Playwright needed
  - Listing: <li><span>DATE</span><a href="PDF_URL">TITLE</a></li>
  - Pagination: index_{N}.html (TRS WCM pattern, page 0 = index.html)
  - Items are sorted newest-first; stop when date year < current year
  - PDF text extraction via pdfplumber (fallback: PyMuPDF)

Usage:
    python slbgb_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>

    python slbgb_crawler.py \\
        --tenant-id <ID> --kb-id <ID> --task-name <NAME> --current-year 2025
"""

import argparse
import json
import logging
import os
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "http://www.mwr.gov.cn"
_SITE_NAME = "中华人民共和国水利部"
_LIST_URL = "/zw/slbgb/index.html"
_PAGE_BASE = "/zw/slbgb/index"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.0

_STATE_FILENAME = "_crawler_state.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    print(msg, flush=True)


def _request_delay():
    import random
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _init_session():
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": _SITE_ROOT + "/",
    })
    return sess


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _get_pagination(html):
    """Extract total page count from embedded JS variables."""
    m_total = re.search(r'var\s+countPage\s*=\s*(\d+)', html)
    total_pages = int(m_total.group(1)) if m_total else 1
    return {"total_pages": total_pages}


# ---------------------------------------------------------------------------
# Listing extraction
# ---------------------------------------------------------------------------

def _extract_listing(html, base_url):
    """Extract bulletin items from listing page HTML.

    Returns list of dicts: {id, title, date, url}
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for ul in soup.select("ul.slnewsconlist"):
        for li in ul.select("li"):
            date_tag = li.find("span")
            a_tag = li.find("a")
            if not a_tag or not date_tag:
                continue

            date_str = date_tag.get_text(strip=True)
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title:
                continue

            abs_url = urljoin(base_url, href)

            # Generate ID from filename
            fn = os.path.basename(href.split("?")[0])
            art_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', fn)
            if not art_id:
                import hashlib
                art_id = "slbgb_" + hashlib.md5(abs_url.encode()).hexdigest()[:12]

            items.append({
                "id": art_id,
                "title": title,
                "date_str": date_str,
                "url": abs_url,
            })

    return items


# ---------------------------------------------------------------------------
# File download & text extraction
# ---------------------------------------------------------------------------

def _download_pdf(sess, url, dest_dir, art_id):
    """Download a PDF file. Returns local path or None."""
    os.makedirs(dest_dir, exist_ok=True)

    fn = os.path.basename(url.split("?")[0])
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", fn)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    dest_path = os.path.join(dest_dir, safe_name)
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    try:
        _request_delay()
        resp = sess.get(url, timeout=120, stream=True)
        if resp.status_code == 200 and len(resp.content) > 100:
            cd = resp.headers.get("Content-Disposition", "")
            fn_match = re.search(
                r'filename\*?[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
            if fn_match:
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", fn_match.group(1))
                dest_path = os.path.join(dest_dir, safe_name)

            with open(dest_path, "wb") as f:
                f.write(resp.content)
            _safe_print("      Downloaded: {} ({} bytes)".format(
                safe_name, len(resp.content)))
            return dest_path
        else:
            _safe_print("      Download failed: status={}, size={}".format(
                resp.status_code, len(resp.content)))
    except Exception as e:
        logging.warning("Download error for %s: %s", url, e)

    return None


def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if member.startswith("__MACOSX") or member.startswith("."):
                    continue
                safe_name = re.sub(r'[\\/:*?"<>|]', "_",
                                   os.path.basename(member))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, "wb") as f:
                    f.write(zf.read(member))
                extracted.append(dest_path)
                _safe_print("      Extracted: {}".format(safe_name))
        os.remove(zip_path)
    except Exception as e:
        logging.warning("ZIP extract error for %s: %s",
                        os.path.basename(zip_path), e)
    return extracted


def _extract_file_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            try:
                import pdfplumber
                parts = []
                with pdfplumber.open(filepath) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            parts.append(text)
                return "\n\n".join(parts) if parts else None
            except ImportError:
                pass
            try:
                import fitz
                doc = fitz.open(filepath)
                parts = []
                for page in doc:
                    text = page.get_text()
                    if text:
                        parts.append(text)
                doc.close()
                return "\n\n".join(parts) if parts else None
            except ImportError:
                return "(PDF file, no parser available)"
        elif ext in (".doc", ".docx"):
            if ext == ".docx":
                try:
                    import docx
                    doc = docx.Document(filepath)
                    return "\n".join(
                        p.text for p in doc.paragraphs if p.text.strip())
                except ImportError:
                    pass
            return "(DOC file, text extraction limited)"
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(
                        " | ".join(str(c) if c is not None else ""
                                   for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) +
                                 "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------

def _build_markdown(art, pdf_text):
    lines = [
        "# {}".format(art.get("title", "无标题")),
        "",
        "**来源:** {}".format(_SITE_NAME),
        "**栏目:** 水利部公报",
    ]
    date_str = art.get("date_str", "")
    if date_str:
        lines.append("**发布日期:** {}".format(date_str))
    lines.append("**URL:** {}".format(art.get("url", "")))
    lines.append("")

    if pdf_text:
        if len(pdf_text) > 100000:
            pdf_text = pdf_text[:100000] + "\n\n（内容过长，已截断）"
        lines.append("## 公报内容")
        lines.append("")
        lines.append(pdf_text)
        lines.append("")
    else:
        lines.append("（无法提取PDF文本内容）")
        lines.append("")

    return "\n".join(lines)


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
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs)", len(state.get("processed_ids", [])))


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="slbgb crawler - 水利部公报"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="http://www.mwr.gov.cn/zw/slbgb/index.html")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds (default: 3300s = 55min)")
    p.add_argument("--current-year", type=int, default=None,
                   help="Target year to crawl (default: current year)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    current_year = args.current_year or datetime.now().year

    _safe_print("\n" + "=" * 60)
    _safe_print("[SLBGB] 水利部公报 crawler")
    _safe_print("[SLBGB] KB: {}".format(args.kb_id))
    _safe_print("[SLBGB] Target year: {}".format(current_year))
    _safe_print("[SLBGB] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== SLBGB crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    downloads_dir = os.path.join(output_dir, "downloads", "slbgb")
    articles_dir = os.path.join(output_dir, "articles", "slbgb")
    os.makedirs(downloads_dir, exist_ok=True)
    os.makedirs(articles_dir, exist_ok=True)
    _safe_print("[SLBGB] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # Load state
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print("[SLBGB] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    sess = _init_session()
    _safe_print("[SLBGB] Session initialized\n")
    sys.stdout.flush()

    run_start = time.time()
    total_processed = 0
    list_url = urljoin(_SITE_ROOT, _LIST_URL)

    # Get pagination
    _request_delay()
    resp = sess.get(list_url, timeout=30)
    resp.encoding = "utf-8"
    if resp.status_code != 200:
        _safe_print("[SLBGB] Failed to load listing: {}".format(resp.status_code))
        sys.stdout.flush()
        return

    paging = _get_pagination(resp.text)
    total_pages = paging["total_pages"]
    _safe_print("[SLBGB] Total pages: {}\n".format(total_pages))
    sys.stdout.flush()

    # Crawl pages until we run out of current-year items
    for page_num in range(total_pages):
        elapsed = time.time() - run_start
        remaining = args.max_runtime - elapsed
        grace = min(120, args.max_runtime * 0.05)
        if remaining < grace:
            _safe_print(
                "\n[SLBGB] Runtime {:.0f}s, remaining {:.0f}s < "
                "grace {:.0f}s, stopping early.".format(
                    elapsed, remaining, grace))
            sys.stdout.flush()
            break

        # Load page
        if page_num == 0:
            html = resp.text
        else:
            page_url = urljoin(_SITE_ROOT,
                               "{}_{}.html".format(_PAGE_BASE, page_num))
            _request_delay()
            resp = sess.get(page_url, timeout=30)
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                _safe_print("[SLBGB] Failed to load page {}: {}".format(
                    page_num, resp.status_code))
                break
            html = resp.text

        items = _extract_listing(html, list_url)

        # Filter: current year only, skip processed
        year_items = []
        seen_years = set()
        for it in items:
            try:
                item_year = int(it["date_str"][:4])
            except (ValueError, IndexError):
                item_year = 0
            seen_years.add(item_year)
            if item_year == current_year and it["id"] not in processed_ids:
                year_items.append(it)

        _safe_print("[SLBGB] Page {}: {} total, {} in {}, {} new".format(
            page_num, len(items), len(year_items), current_year,
            len([i for i in year_items if i["id"] not in processed_ids])))
        sys.stdout.flush()

        # Stop if no current-year items found AND we've seen older years
        # (items are sorted newest-first)
        if not year_items and any(y < current_year for y in seen_years):
            _safe_print("[SLBGB] No more {} items, stopping page scan.".format(
                current_year))
            sys.stdout.flush()
            break

        # Process each item
        for idx, art in enumerate(year_items, 1):
            _safe_print("[SLBGB] [{}/{}] {} ({})".format(
                idx, len(year_items),
                art["title"][:60], art["date_str"]))
            sys.stdout.flush()

            # Download PDF
            pdf_path = _download_pdf(sess, art["url"], downloads_dir, art["id"])
            if not pdf_path:
                logging.warning("Failed to download: %s", art["url"])
                continue

            # Extract text
            is_zip = (
                pdf_path.lower().endswith((".zip", ".rar", ".7z")) or
                (os.path.getsize(pdf_path) >= 4 and
                 open(pdf_path, "rb").read(4) == b"PK\x03\x04")
            )
            if is_zip:
                extracted = _extract_zip(pdf_path)
                if extracted:
                    all_texts = []
                    for ext_fp in extracted:
                        txt = _extract_file_text(ext_fp)
                        if txt:
                            all_texts.append(txt)
                    pdf_text = "\n\n".join(all_texts) if all_texts else None
                else:
                    pdf_text = None
            else:
                pdf_text = _extract_file_text(pdf_path)

            # Build markdown & save
            md = _build_markdown(art, pdf_text)
            md_path = os.path.join(articles_dir, "{}.md".format(art["id"]))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md)

            # Upload to KB
            if args.kb_id:
                try:
                    _upload_to_kb(md_path, args.kb_id, args.tenant_id)
                except Exception as e:
                    _safe_print("[SLBGB] upload failed: {}".format(e))
                    logging.error("Upload failed: %s", e)

            processed_ids.add(art["id"])
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)
            total_processed += 1

            _safe_print("[SLBGB] {} done ({} total)".format(
                art["title"][:40], total_processed))
            sys.stdout.flush()

    _safe_print("\n" + "=" * 60)
    _safe_print("[SLBGB] Done: {} articles processed.".format(total_processed))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== SLBGB crawler finished: %d articles ===", total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "slbgb_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
