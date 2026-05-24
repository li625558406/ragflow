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
Crawler for slt.fujian.gov.cn — 福建省水利厅 政务公开 (today's data).

Covers the /xxgk/ main page which has 4 modules with multiple tabs:
  Module 1 (tabtzgg):  通知公告 / 建设管理 / 规划计划
  Module 2 (tabfggw):  法规公文 / 资金信息
  Module 3 (tabdjgz):  党建工作 / 解读回应 / 纪检监察
  Module 4 (tabrsxx):  人事信息 / 职称评审 / 统计信息

Only crawls today's data (8AM rule: before 8AM -> yesterday's date).
Server-rendered HTML with avalon.js framework — no JS rendering needed.

Data flow
---------
  1. Listing: GET /xxgk/ -> parse each module's tabs for title, URL, date (MM-DD)
  2. Filter: only keep items matching today's date
  3. Detail: GET each .htm/.pdf detail page -> parse title, meta tags,
     content div, attachment links
  4. Attachments: direct download (relative or absolute URLs)
  5. ZIP auto-extraction with content parsing (pdfplumber, python-docx, openpyxl)

Checkpoint/resume: state saved per batch. Time-bounded check
(default 3300s) stops gracefully before the 3600s task-timeout window.

Usage
-----
    python slt_xxgk_crawler.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>

    # Optional:
        --max-runtime 3300    # Max runtime before graceful stop
        --full                # Ignore saved state, re-crawl
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
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SITE_ROOT = "https://slt.fujian.gov.cn"
_LISTING_URL = "/xxgk/"
_SECTION_LABEL = "福建省水利厅-政务公开"

# Checkpoint batch size (articles)
_BATCH_SIZE = 3

# Default max runtime (55 min, 5 min margin)
_MAX_RUNTIME_DEFAULT = 3300

# Anti-crawling delays (seconds)
_REQUEST_DELAY_MIN = 0.3
_REQUEST_DELAY_MAX = 1.0

# State filename
_STATE_FILENAME = "_crawler_state.json"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _sanitize_filename(text, max_len=120):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name).strip("._ ")
    return (name or "untitled")[:max_len]


def _get_target_date():
    """Return target date string (YYYY-MM-DD) using 8AM rule."""
    now = datetime.now()
    if now.hour < 8:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def _extract_article_id(url):
    """Extract article ID from URL.

    Patterns:
      .../t20260522_7151072.htm   -> 7151072
      .../P020260521585193992660.pdf -> P020260521585193992660
    """
    m = re.search(r'/t\d{8}_(\d+)\.htm', url)
    if m:
        return m.group(1)
    m = re.search(r'/([A-Z]\d{15,})\.(?:pdf|doc|docx|xlsx|xls|zip|rar)', url)
    if m:
        return m.group(1)
    # DocId from URL path
    path = urllib.parse.urlparse(url).path
    m = re.search(r'/(\d{7,})\.(?:htm|html)', path)
    if m:
        return m.group(1)
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _extract_date_from_url(url):
    """Extract date from URL path.

    Patterns:
      .../202605/t20260522_... -> 2026-05-22
      .../202605/...           -> 2026-05
    """
    m = re.search(r'/t(\d{4})(\d{2})(\d{2})_', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'/(\d{4})(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _resolve_url(href, base_url):
    """Resolve a relative href against a base URL."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return urllib.parse.urljoin(base_url, href)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url, referer=None, timeout=30):
    """GET request returning bytes or None."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if referer:
        req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logging.error("GET %s failed: %s", url, e)
        return None


def _download_binary(url, referer=None, timeout=60):
    """Download binary content; returns bytes or None."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if referer:
        req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logging.error("Download failed: %s — %s", url[:120], e)
        return None


# ---------------------------------------------------------------------------
# Main page parsing — modules with tabs
# ---------------------------------------------------------------------------

def _parse_xxgk_main(html_bytes, base_url, target_date):
    """Parse the /xxgk/ main page for all modules and tabs matching target_date.

    The page has 4 modules, each with multiple tabs. Each tab's content
    is a <ul class="xw-list-1"> inside a .bd div. Tab headers are inside
    .tab1.tab2.hd blocks.

    Dates are in MM-DD format in <span> elements. URLs contain full dates.
    Returns list of {"title", "url", "date_str", "article_id", "section"}.
    """
    if not html_bytes:
        return []

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    target_mmdd = target_date[5:]  # "MM-DD"

    # Find all tab modules
    # Each module is a div[id] containing .tab1.tab2.hd and .bd
    module_containers = []

    # Look for modules by their id pattern (tabtzgg, tabfggw, tabdjgz, tabrsxx)
    for module_id in ["tabtzgg", "tabfggw", "tabdjgz", "tabrsxx"]:
        mod = soup.find(id=module_id)
        if mod:
            module_containers.append(mod)

    # Also find any .tab1.tab2.hd outside known modules (e.g. top section)
    for tab_hd in soup.find_all("div", class_="tab1"):
        if not tab_hd.find_parent(id=lambda x: x and x.startswith("tab")):
            parent = tab_hd.parent
            if parent and parent not in module_containers:
                # Check if this is a valid module with .bd sibling
                bd = parent.find("div", class_="bd") if hasattr(parent, "find") else None
                if bd and bd.find("ul", class_="xw-list-1"):
                    module_containers.append(parent)

    for module_div in module_containers:
        # Get tab headers
        tab_hd = module_div.find("div", class_=re.compile(r"tab1"))
        if not tab_hd:
            continue

        # Collect tab names and their order
        tab_names = []
        tab_ul = tab_hd.find("ul", class_=re.compile(r"new-tit"))
        if tab_ul:
            for li in tab_ul.find_all("li"):
                a_tag = li.find("a", href=True)
                if a_tag:
                    name = (a_tag.get("title") or a_tag.get_text()).strip()
                    if name:
                        tab_names.append(name)

        # Get content lists
        bd = module_div.find("div", class_="bd")
        if not bd:
            continue

        ul_list = bd.find_all("ul", class_="xw-list-1")
        for tab_idx, ul_el in enumerate(ul_list):
            section_label = tab_names[tab_idx] if tab_idx < len(tab_names) else f"模块-{tab_idx}"

            for li in ul_el.find_all("li"):
                span = li.find("span")
                a_tag = li.find("a", href=True)
                if not a_tag:
                    continue

                href = a_tag["href"].strip()
                title = (a_tag.get("title") or a_tag.get_text()).strip()
                if not title or len(title) < 2:
                    continue

                # Skip non-article links
                if href.startswith("javascript:") or href == "#":
                    continue

                # Date in MM-DD format
                date_mmdd = span.get_text(strip=True) if span else ""
                if not date_mmdd or not re.match(r'^\d{2}-\d{2}$', date_mmdd):
                    continue

                if date_mmdd != target_mmdd:
                    continue

                # Build full date
                full_date = f"{target_date[:5]}{date_mmdd}"

                # Some articles may have date in URL but not in span
                url_date = _extract_date_from_url(href)
                if url_date and url_date >= "2020":
                    if url_date != full_date:
                        # URL date takes priority if it's more specific
                        if len(url_date) == 10 and url_date[5:] != date_mmdd:
                            continue  # URL says different date, skip
                        full_date = url_date

                article_id = _extract_article_id(href)
                if article_id in seen:
                    continue
                seen.add(article_id)

                articles.append({
                    "title": title,
                    "url": _resolve_url(href, base_url),
                    "date_str": full_date,
                    "article_id": article_id,
                    "section": section_label,
                })

    return articles


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_detail_html(html_bytes, detail_url):
    """Parse an slt.fujian.gov.cn article detail page.

    Title: <h1 class="xl_tit"> or meta ArticleTitle
    Metadata: meta PubDate, ContentSource, .xl_tit2_l span
    Content: #detailCont or .smgb-article (no TRS_Editor)
    Attachments: .myzj_xl_list links
    """
    if not html_bytes:
        return None

    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    info = {
        "title": "",
        "pub_date": "",
        "info_source": "",
        "article_id": _extract_article_id(detail_url),
        "content_html": "",
        "content_text": "",
        "attachments": [],
    }

    # Title: <h1 class="xl_tit">
    h1 = soup.find("h1", class_="xl_tit")
    if h1:
        info["title"] = h1.get_text(strip=True)

    # Fallback: meta ArticleTitle
    if not info["title"]:
        meta_title = soup.find("meta", attrs={"name": "ArticleTitle"})
        if meta_title and meta_title.get("content"):
            info["title"] = meta_title["content"].strip()

    # Metadata from meta tags
    meta_pubdate = soup.find("meta", attrs={"name": "PubDate"})
    if meta_pubdate and meta_pubdate.get("content"):
        info["pub_date"] = meta_pubdate["content"].strip()

    meta_source = soup.find("meta", attrs={"name": "ContentSource"})
    if meta_source and meta_source.get("content"):
        info["info_source"] = meta_source["content"].strip()

    # Also try .xl_tit2 spans for date and source
    tit2_div = soup.find("div", class_="xl_tit2")
    if tit2_div:
        tit2_l = tit2_div.find("div", class_="xl_tit2_l")
        if tit2_l:
            for sp in tit2_l.find_all("span"):
                text = sp.get_text(strip=True)
                if "来源" in text:
                    src = re.sub(r'^来源[：:]\s*', '', text).strip()
                    if src:
                        info["info_source"] = src
                elif re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', text):
                    if not info["pub_date"]:
                        info["pub_date"] = text.strip()
                elif re.match(r'^\d{4}-\d{2}-\d{2}$', text):
                    if not info["pub_date"]:
                        info["pub_date"] = text.strip()

    # Content: #detailCont > .smgb-article
    content_div = soup.find(id="detailCont") or soup.find("div", class_="smgb-article")
    if content_div:
        for tag in content_div.find_all(["script", "style"]):
            tag.decompose()
        # Remove the more2/info bar at top if present
        for header in content_div.find_all("div", class_="xl_tit2"):
            header.decompose()
        for header in content_div.find_all("h1", class_="xl_tit"):
            header.decompose()
        for header in content_div.find_all("div", class_="xl_tit1"):
            header.decompose()

        info["content_html"] = content_div.decode_contents()
        info["content_text"] = content_div.get_text(separator="\n", strip=True)

    # Attachments: .myzj_xl_list links
    for att_ul in soup.find_all("ul", class_=re.compile(r"myzj_xl_list")):
        for li in att_ul.find_all("li"):
            a_tag = li.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"].strip()
            text = (a_tag.get_text() or "").strip()
            att_url = _resolve_url(href, detail_url)

            info["attachments"].append({
                "filename": text or os.path.basename(urllib.parse.urlparse(att_url).path),
                "url": att_url,
                "type": "download",
            })

    return info


# ---------------------------------------------------------------------------
# Attachment download + processing
# ---------------------------------------------------------------------------

def _download_attachments(attachments, download_dir):
    """Download attachments to local directory. Returns list of local file paths."""
    os.makedirs(download_dir, exist_ok=True)
    local_files = []

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue
        fname = _sanitize_filename(att.get("filename", "attachment"), max_len=120)
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext and not fname.lower().endswith(ext):
            fname += ext

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        data = _download_binary(url)
        if data:
            with open(filepath, "wb") as f:
                f.write(data)
            local_files.append(filepath)
            time.sleep(random.uniform(0.1, 0.3))

    return local_files


def _extract_zip(filepath):
    """Extract ZIP file to same directory. Returns list of extracted file paths."""
    extracted = []
    extract_dir = os.path.splitext(filepath)[0] + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                safe_name = _sanitize_filename(name, max_len=120)
                out_path = os.path.join(extract_dir, safe_name)
                os.makedirs(os.path.dirname(out_path) or extract_dir, exist_ok=True)
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except Exception as e:
        logging.warning("ZIP extraction failed for %s: %s", filepath, e)
    return extracted


# ---------------------------------------------------------------------------
# Text extraction from binary files
# ---------------------------------------------------------------------------

def _extract_text_from_file(filepath):
    """Extract plain text from PDF / DOCX / XLSX / TXT files."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber
            parts = []
            with pdfplumber.open(filepath) as pdf:
                for pg in pdf.pages:
                    text = pg.extract_text()
                    if text:
                        parts.append(text)
            return "\n\n".join(parts)
        elif ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append(f"### {ws.title}\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(detail, download_dir, source_url):
    """Build a Markdown document from article detail + attachments."""
    info = detail or {}
    title = info.get("title", "无标题")
    pub_date = info.get("pub_date", "")
    info_source = info.get("info_source", "")

    lines = [
        f"# {title}",
        "",
        f"**数据来源:** 福建省水利厅",
        f"**页面地址:** {source_url}",
        f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if pub_date:
        lines.append(f"**发布时间:** {pub_date}")
    if info_source:
        lines.append(f"**信息来源:** {info_source}")
    lines.append("")

    # Main content
    content_text = info.get("content_text", "")
    if content_text:
        lines.append("---")
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
        lines.append(content_clean)
        lines.append("")

    # Attachments
    attachments = info.get("attachments", [])
    if attachments:
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")

        for att in attachments:
            fname = att.get("filename", "unknown")
            att_url = att.get("url", "")
            lines.append(f"- [{fname}]({att_url})")
        lines.append("")

        # Embed extracted attachment text
        if download_dir and os.path.isdir(download_dir):
            lines.append("### 附件内容")
            lines.append("")
            for att in attachments:
                if att.get("type") != "download":
                    continue
                fname = att.get("filename", "")
                local_path = os.path.join(download_dir, fname)
                safe_name = _sanitize_filename(fname, max_len=120)
                alt_path = os.path.join(download_dir, safe_name)
                if not os.path.exists(local_path) and os.path.exists(alt_path):
                    local_path = alt_path
                if not os.path.exists(local_path):
                    for root, _, files in os.walk(download_dir):
                        for fn in files:
                            if fn == safe_name or fn == fname:
                                local_path = os.path.join(root, fn)
                                break
                if not os.path.exists(local_path):
                    continue

                lines.append(f"#### {fname}")
                lines.append("")
                text = _extract_text_from_file(local_path)
                if text and text.strip():
                    if len(text) > 50000:
                        text = text[:50000] + "\n\n（内容过长，已截断）"
                    lines.append(text)
                else:
                    lines.append("（无法提取文本内容）")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State management
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


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(md_content, attachment_files, kb_id, tenant_id, folder_name):
    """Upload markdown + attachment files to knowledge base."""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"Knowledge base {kb_id} not found")

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FO(f"{folder_name}.md", md_content.encode("utf-8"))
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("MD upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)

    for fp in attachment_files:
        fname = os.path.basename(fp)
        with open(fp, "rb") as f:
            blob = f.read()
        fo2 = _FO(fname, blob)
        errs2, pairs2 = FileService.upload_document(kb, [fo2], tenant_id)
        if errs2:
            logging.warning("Attachment upload errors: %s", errs2)
        for doc, _ in pairs2:
            did = doc["id"]
            try:
                DocumentService.begin2parse(did)
                DocumentService.run(tenant_id, doc, {})
            except Exception as e:
                logging.error("Queue parse for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="slt.fujian.gov.cn xxgk crawler — 福建省水利厅 政务公开 (today only)"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--target-url", default=None,
                   help="Optional — specify a different listing URL")
    p.add_argument("--output-dir", default=None,
                   help="Output root directory (default: PROJECT_ROOT/rag/<task_name>)")
    p.add_argument("--full", action="store_true",
                   help="Ignore saved state and re-crawl all")
    p.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT,
                   help="Max runtime in seconds (default: 3300)")
    for opt in ("--max-days", "--hours", "--max-articles",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[SLT-XXGK] 福建省水利厅 — 政务公开 crawler")
    _safe_print("[SLT-XXGK] KB: {}".format(args.kb_id))
    _safe_print("[SLT-XXGK] Task: {}".format(args.task_name))
    _safe_print("[SLT-XXGK] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== SLT-XXGK crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[SLT-XXGK] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # ── State ──────────────────────────────────────────────────────────
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))

    _safe_print("[SLT-XXGK] Already processed: {} article(s)".format(len(processed_ids)))
    sys.stdout.flush()

    target_date = _get_target_date()
    _safe_print("[SLT-XXGK] Target date: {} (8AM rule)".format(target_date))
    sys.stdout.flush()

    crawl_start = time.time()

    # ── Step 1: Crawl listing page ──────────────────────────────────────
    _safe_print("\n[SLT-XXGK] Step 1/3: Crawling listing page...")
    sys.stdout.flush()

    listing_url = args.target_url or (_SITE_ROOT + _LISTING_URL)
    _safe_print("[SLT-XXGK]   Fetching: {}".format(listing_url))
    sys.stdout.flush()

    html_bytes = _http_get(listing_url, referer=_SITE_ROOT + "/")
    if not html_bytes:
        _safe_print("[SLT-XXGK] Failed to fetch listing page, aborting.")
        sys.stdout.flush()
        return

    all_articles = _parse_xxgk_main(html_bytes, listing_url, target_date)
    _safe_print("[SLT-XXGK]   -> {} article(s) total for {}".format(len(all_articles), target_date))
    sys.stdout.flush()

    if not all_articles:
        _safe_print("[SLT-XXGK] No articles found for today, done.")
        sys.stdout.flush()
        return

    # Section breakdown
    section_counts = {}
    for a in all_articles:
        sec = a.get("section", "unknown")
        section_counts[sec] = section_counts.get(sec, 0) + 1
    for sec, cnt in sorted(section_counts.items()):
        _safe_print("[SLT-XXGK]     {}: {} article(s)".format(sec, cnt))
    sys.stdout.flush()

    # Filter already-processed
    new_articles = [
        a for a in all_articles
        if a.get("article_id") and a["article_id"] not in processed_ids
    ]
    skipped = len(all_articles) - len(new_articles)
    if skipped:
        _safe_print("[SLT-XXGK] {} already processed, {} new".format(skipped, len(new_articles)))
        sys.stdout.flush()

    if not new_articles:
        _safe_print("[SLT-XXGK] All articles for today already processed.")
        sys.stdout.flush()
        return

    # ── Step 2: Process each article ───────────────────────────────────
    _safe_print("\n[SLT-XXGK] Step 2/3: Processing {} article(s)...\n".format(len(new_articles)))
    sys.stdout.flush()

    processed_count = 0
    stopped_early = False
    downloads_dir = os.path.join(output_dir, "downloads")

    for idx, article in enumerate(new_articles, 1):
        # ── Time-bounded check ─────────────────────────────────────────
        elapsed = time.time() - crawl_start
        remaining = args.max_runtime - elapsed
        if remaining < 120:
            _safe_print(
                "\n[SLT-XXGK] Runtime {:.0f}s, {:.0f}s remaining (limit {}s), "
                "stopping gracefully. {} processed. "
                "Next run will resume.".format(elapsed, remaining, args.max_runtime, processed_count))
            sys.stdout.flush()
            stopped_early = True
            break

        article_id = article["article_id"]
        title = article["title"]
        detail_url = article["url"]
        date_str = article.get("date_str", "")
        section_label = article.get("section", "")

        _safe_print("[SLT-XXGK] [{}/{}] [{}] {}...".format(
            idx, len(new_articles), section_label, title[:50]))
        sys.stdout.flush()

        # Handle PDF files directly (extract text from PDF itself)
        if detail_url.lower().endswith(".pdf"):
            dl_name = "{}_{}".format(article_id[:12], _sanitize_filename(title[:30], 40))
            article_dl_dir = os.path.join(downloads_dir, dl_name)
            os.makedirs(article_dl_dir, exist_ok=True)

            fname = os.path.basename(urllib.parse.urlparse(detail_url).path)
            pdf_path = os.path.join(article_dl_dir, fname)
            pdf_data = _download_binary(detail_url, referer=listing_url)
            if pdf_data:
                with open(pdf_path, "wb") as f:
                    f.write(pdf_data)
                pdf_text = _extract_text_from_file(pdf_path)
                md_content = "\n".join([
                    f"# {title}",
                    "",
                    f"**数据来源:** 福建省水利厅",
                    f"**页面地址:** {detail_url}",
                    f"**抓取时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"**发布时间:** {date_str}",
                    "",
                    "---",
                    "",
                    "## 正文（PDF提取）",
                    "",
                    pdf_text or "（无法提取PDF文本内容）",
                ])
                date_for_name = date_str or datetime.now().strftime("%Y-%m-%d")
                folder_name = _sanitize_filename(
                    "{}_{}_{}".format(date_for_name, article_id[:12], title[:40]), max_len=120)
                md_path = os.path.join(output_dir, f"{folder_name}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                _safe_print("[SLT-XXGK]   Saved PDF ({} chars)".format(len(md_content)))
                sys.stdout.flush()

                if args.kb_id:
                    try:
                        _upload_to_kb(md_content, [pdf_path], args.kb_id,
                                     args.tenant_id, folder_name)
                        _safe_print("[SLT-XXGK]   Uploaded to KB {}".format(args.kb_id))
                        sys.stdout.flush()
                    except Exception as e:
                        logging.error("KB upload failed: %s", e)

                processed_ids.add(article_id)
                processed_count += 1
                time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))
                continue

        # Fetch HTML detail page
        html_bytes = _http_get(detail_url, referer=listing_url)
        if not html_bytes:
            logging.warning("Failed to fetch detail: %s", detail_url)
            processed_ids.add(article_id)
            continue

        detail = _parse_detail_html(html_bytes, detail_url)
        if not detail:
            logging.warning("Failed to parse detail: %s", detail_url)
            processed_ids.add(article_id)
            continue

        # Prefer detail page title if longer
        if detail.get("title") and len(detail["title"]) > len(title):
            title = detail["title"]

        # Download attachments
        attachments = detail.get("attachments", [])
        local_files = []
        article_dl_dir = ""
        if attachments:
            dl_name = "{}_{}".format(article_id[:12], _sanitize_filename(title[:30], 40))
            article_dl_dir = os.path.join(downloads_dir, dl_name)
            local_files = _download_attachments(attachments, article_dl_dir)
            # Extract ZIPs
            for fp in list(local_files):
                is_zip = fp.lower().endswith(".zip")
                if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                    with open(fp, "rb") as f:
                        is_zip = f.read(4) == b"PK\x03\x04"
                if is_zip:
                    extracted = _extract_zip(fp)
                    local_files.remove(fp)
                    local_files.extend(extracted)

        # Build markdown
        md_content = _build_markdown(detail, article_dl_dir, detail_url)

        # Save markdown locally
        date_for_name = date_str or datetime.now().strftime("%Y-%m-%d")
        folder_name = _sanitize_filename(
            "{}_{}_{}".format(date_for_name, article_id[:12], title[:40]), max_len=120)
        md_path = os.path.join(output_dir, f"{folder_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        _safe_print("[SLT-XXGK]   Saved ({} chars, {} attachments)".format(
            len(md_content), len(local_files)))
        sys.stdout.flush()

        # Upload to KB
        if args.kb_id:
            try:
                _upload_to_kb(md_content, local_files, args.kb_id,
                             args.tenant_id, folder_name)
                _safe_print("[SLT-XXGK]   Uploaded to KB {}".format(args.kb_id))
                sys.stdout.flush()
            except Exception as e:
                logging.error("KB upload failed: %s", e)
                _safe_print("[SLT-XXGK]   Upload error: {}".format(e))
                sys.stdout.flush()

        processed_ids.add(article_id)
        processed_count += 1

        # Checkpoint every batch
        if processed_count % _BATCH_SIZE == 0:
            _save_state(output_dir, {"processed_ids": list(processed_ids)})
            _safe_print("[SLT-XXGK]   Checkpoint ({} processed)".format(processed_count))
            sys.stdout.flush()

        # Anti-crawling delay
        time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))

    # ── Final state ────────────────────────────────────────────────────
    state["processed_ids"] = list(processed_ids)
    _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[SLT-XXGK] Crawl complete — {} new article(s)".format(processed_count))
    if stopped_early:
        _safe_print("[SLT-XXGK] Stopped early, will resume next run")
    _safe_print("[SLT-XXGK] Target date: {}".format(target_date))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== SLT-XXGK crawler finished ===")


if __name__ == "__main__":
    CONSUMER_NAME = "slt_xxgk_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
