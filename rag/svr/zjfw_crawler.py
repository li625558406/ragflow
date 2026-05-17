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
Dedicated web crawler for zjfw.zhangzhou.gov.cn (漳州市工程项目中介服务平台).

This is a JavaScript-heavy SPA. All data is loaded via a signed API:
  POST /imng/api-v2/{action}/{method}?s={sig}&t={ts}
  Content-Type: application/json

The signature mechanism involves:
  1. Fetching the homepage to extract a __signature token from the HTML.
  2. Generating a 6-char key by using each character of __signature as an
     index into the string "0123456789abcdef".
  3. Building a timestamp string: "{rand8}_{key}_{epoch_ms}".
  4. Passing the original __signature as ?s and the timestamp as ?t.

Content sections (user-selected):
  - tzgg (通知公告 / Policy Notices) — IndexCmd.getPoliciesRegulationsList
      Listing returns rows with {id, title, publish_date, ...}.
      Detail via PoliciesRegulationsCmd.getPoliciesRegulationsDetail → CONTENT (HTML).
  - cggg (采购公告 / Procurement Notices) — BidNoticeCmd.queryBidNoticeList
      Listing returns rows with {bid_id, bid_title, publish_date, ...}.
      Detail via BidNoticeCmd.getBidsInfo → BID_CONTENT (text/html).

Usage (typically spawned by task_executor):
    python zjfw_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url http://zjfw.zhangzhou.gov.cn/imng/zjfw \
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
import string
import sys
import time
from datetime import datetime, timedelta

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
# Known sections — each entry is:
#   ( (list_action, list_method), (detail_action, detail_method), display_label )
# These are full Java class names reverse-engineered from LEx.Command JS.
# ---------------------------------------------------------------------------
SECTIONS = {
    "tzgg": (
        ("fujian.zhangzhougaoxin.app.icity.index.IndexCmd", "getPoliciesRegulationsList"),
        ("product.app.icity.policiesRegulations.PoliciesRegulationsCmd", "getPoliciesRegulationsDetail"),
        "通知公告",
    ),
    "cggg": (
        ("fujian.zhangzhougaoxin.app.icity.index.IndexCmd", "getBidInfo"),
        ("fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd", "getBidsInfo"),
        "采购公告",
    ),
}

_SITE_ROOT = "http://zjfw.zhangzhou.gov.cn"
_API_BASE = _SITE_ROOT + "/imng/api-v2"


def parse_args():
    parser = argparse.ArgumentParser(description="ZJFW crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (e.g. http://zjfw.zhangzhou.gov.cn/imng/zjfw)")
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
    parser.add_argument("--tzgg-only", action="store_true",
                        help="Only crawl tzgg section (convenience)")
    parser.add_argument("--cggg-only", action="store_true",
                        help="Only crawl cggg section (convenience)")
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
    ),
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
}

def _fetch(url, client, timeout=30):
    """Fetch a URL and return decoded text."""
    try:
        resp = client.get(url)
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


# ---------------------------------------------------------------------------
# Signature generation (reverse-engineered from LEx.Command JS)
# ---------------------------------------------------------------------------

_SIG_CHARS = "0123456789abcdef"


def _fetch_signature(home_url, client):
    """Fetch the homepage and extract the __signature value.

    The signature is embedded somewhere in the HTML as a JavaScript variable:
      var __signature = "29e63b961c88e08197c81248600dd50b";

    Returns the signature string, or None if not found.
    """
    html = _fetch(home_url, client)
    if not html:
        return None
    m = re.search(r'__signature\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    logging.warning("__signature not found in homepage HTML")
    return None


def _generate_key(sig):
    """Generate a 6-character key from the signature.

    Reverse-engineered from LEx.Command JS:
      var key = "";
      var keyIndex = -1;
      for (var i = 0; i < 6; i++) {
        var c = sig.charAt(keyIndex + 1);
        key += c;
        keyIndex = chars.indexOf(c);
        if (keyIndex < 0 || keyIndex >= sig.length) keyIndex = i;
      }

    Each iteration picks the char at (prev_index + 1), then uses that
    char's position in "0123456789abcdef" as the next index.
    """
    key = ""
    key_index = -1
    for i in range(6):
        c = sig[key_index + 1]
        key += c
        key_index = _SIG_CHARS.index(c)
        if key_index < 0 or key_index >= len(sig):
            key_index = i
    return key


def _gen_timestamp(key):
    """Build the timestamp parameter: "{rand8}_{key}_{epoch_ms}".

    Matches the JS: parseInt(Math.random() * 90000000 + 10000000)
    The rand8 is 8 random decimal digits (10000000-99999999).
    """
    rand8 = str(random.randint(10000000, 99999999))
    epoch_ms = int(time.time() * 1000)
    ts = f"{rand8}_{key}_{epoch_ms}"
    return ts.replace("+", "_")


def _ensure_signature(sig_cache, client):
    """Ensure we have a cached signature; fetch if needed.

    sig_cache is a dict: {"sig": str or None, "key": str or None, "home_url": str}
    Returns True if signature is available.
    """
    if sig_cache.get("sig") and sig_cache.get("key"):
        return True
    sig = _fetch_signature(sig_cache["home_url"], client)
    if not sig:
        return False
    sig_cache["sig"] = sig
    sig_cache["key"] = _generate_key(sig)
    return True


def _call_api(action, method, params, sig_cache, client, timeout=30):
    """Call a signed API endpoint.

    POST to /imng/api-v2/{action}/{method}?s={sig}&t={ts}
    with JSON body = params.

    The Referer header is set dynamically so the nginx WAF can validate
    the request source.

    Returns parsed JSON dict, or None on failure.
    """
    if not _ensure_signature(sig_cache, client):
        logging.error("Cannot sign API call: no signature available")
        return None

    ts = _gen_timestamp(sig_cache["key"])
    url = f"{_API_BASE}/{action}/{method}?s={sig_cache['sig']}&t={ts}"

    try:
        headers = {"Referer": sig_cache.get("home_url", _SITE_ROOT + "/imng/zjfw")}
        resp = client.post(url, json_body=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        logging.error("API call %s/%s failed: %s", action, method, e)
        return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
    if not text:
        return None
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
# Listing parsing — tzgg (通知公告)
# ---------------------------------------------------------------------------

def _extract_tzgg_articles(api_data, section_label):
    """Extract articles from getPoliciesRegulationsList response.

    Actual API response: {total: 74, data: [{ID, TITLE, DISPLAYTIME, ...}]}

    Returns list[dict] with keys: id, title, date, section.
    """
    articles = []
    rows = []
    if isinstance(api_data, dict):
        rows = api_data.get("data") or api_data.get("rows") or []
    elif isinstance(api_data, list):
        rows = api_data

    for row in rows:
        if not isinstance(row, dict):
            continue
        article_id = row.get("ID") or row.get("id") or ""
        if not article_id:
            continue
        title = (row.get("TITLE") or row.get("title") or "").strip()
        if not title or len(title) < 2:
            continue

        date_str = (row.get("DISPLAYTIME") or row.get("CREATDATE") or
                    row.get("displaytime") or "").strip()
        dt = _parse_date(date_str) if date_str else None

        articles.append({
            "id": str(article_id),
            "title": title,
            "date": dt,
            "section": section_label,
        })

    return articles


# ---------------------------------------------------------------------------
# Listing parsing — cggg (采购公告)
# ---------------------------------------------------------------------------

def _extract_cggg_articles(api_data, section_label):
    """Extract articles from getBidInfo response.

    Actual API response: {total: 367, data: [{ID, TITLE, CREATDATE, ...}]}

    Returns list[dict] with keys: id, title, date, section.
    """
    articles = []
    rows = []
    if isinstance(api_data, dict):
        rows = api_data.get("data") or api_data.get("rows") or []
    elif isinstance(api_data, list):
        rows = api_data

    for row in rows:
        if not isinstance(row, dict):
            continue
        article_id = row.get("ID") or row.get("id") or ""
        if not article_id:
            continue
        title = (row.get("TITLE") or row.get("title") or "").strip()
        if not title or len(title) < 2:
            continue

        date_str = (row.get("CREATDATE") or row.get("DISPLAYTIME") or
                    row.get("displaytime") or "").strip()
        dt = _parse_date(date_str) if date_str else None

        articles.append({
            "id": str(article_id),
            "title": title,
            "date": dt,
            "section": section_label,
        })

    return articles


# ---------------------------------------------------------------------------
# Article detail — tzgg
# ---------------------------------------------------------------------------

def _fetch_tzgg_detail(article_id, sig_cache, client):
    """Fetch tzgg article detail.

    Actual API response:
      {data: [{ID, TITLE, CONTENT (HTML), SOURCE, DISPLAYTIME, ...}]}

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    data = _call_api(
        "product.app.icity.policiesRegulations.PoliciesRegulationsCmd",
        "getPoliciesRegulationsDetail",
        {"id": article_id}, sig_cache, client,
    )
    if not data:
        return "", [], {}

    rows = []
    if isinstance(data, dict):
        rows = data.get("data") or data.get("rows") or []
    elif isinstance(data, list):
        rows = data

    if not rows or not isinstance(rows, list):
        return "", [], {}

    # Take the first item
    detail = rows[0] if isinstance(rows[0], dict) else {}

    meta_title = (detail.get("TITLE") or detail.get("title") or "").strip()
    meta_date = (detail.get("DISPLAYTIME") or detail.get("CREATDATE") or "").strip()
    meta_source = (detail.get("SOURCE") or detail.get("source") or "").strip()

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    content_html = (detail.get("CONTENT") or detail.get("content") or "").strip()
    if not content_html or content_html == "-":
        return "", [], metadata

    markdown_text = _html_to_markdown(content_html)
    return markdown_text, [], metadata


# ---------------------------------------------------------------------------
# Article detail — cggg
# ---------------------------------------------------------------------------

def _fetch_cggg_detail(article_id, sig_cache, client):
    """Fetch cggg article detail.

    Actual API response:
      {data: [{ID, TITLE, BID_CONTENT (text/html), CREATDATE, ...}]}

    Returns (markdown_text, resources_list, metadata_dict).
    Returns ("", [], {}) on failure.
    """
    data = _call_api(
        "fujian.zhangzhougaoxin.app.icity.browse.bidNotice.BidNoticeCmd",
        "getBidsInfo",
        {"bid_id": article_id}, sig_cache, client,
    )
    if not data:
        return "", [], {}

    rows = []
    if isinstance(data, dict):
        rows = data.get("data") or data.get("rows") or []
    elif isinstance(data, list):
        rows = data

    if not rows or not isinstance(rows, list):
        return "", [], {}

    detail = rows[0] if isinstance(rows[0], dict) else {}

    meta_title = (detail.get("TITLE") or detail.get("title") or "").strip()
    meta_date = (detail.get("CREATDATE") or detail.get("DISPLAYTIME") or "").strip()
    meta_source = (detail.get("SOURCE") or detail.get("source") or "").strip()

    metadata = {
        "title": meta_title,
        "date": meta_date,
        "source": meta_source,
    }

    content_text = (detail.get("BID_CONTENT") or detail.get("bid_content") or "").strip()
    if not content_text:
        return "", [], metadata

    if "<" in content_text and ">" in content_text:
        markdown_text = _html_to_markdown(content_text)
    else:
        markdown_text = content_text

    return markdown_text, [], metadata


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _html_to_markdown(html_text):
    """Convert an HTML fragment to Markdown text."""
    soup = BeautifulSoup(html_text, "lxml")

    # Strip clutter
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    _TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "img",
             "div", "section", "table",
             "span", "strong", "font", "em", "b", "i", "u", "a"}

    lines = []
    seen_texts = set()

    for el in soup.find_all(list(_TAGS)):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
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
    args = parse_args()
    _safe_print(f"\n{'='*60}")
    _safe_print("[ZJFW] Starting Zhangzhou Intermediary Service Platform crawler")
    _safe_print(f"[ZJFW] Target URL: {args.target_url}")
    _safe_print(f"[ZJFW] Task name: {args.task_name}")
    _safe_print(f"[ZJFW] Target KB: {args.kb_id}")
    _safe_print(f"[ZJFW] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[ZJFW] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== ZJFW crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        # Determine which sections to crawl
        if args.tzgg_only:
            selected = {"tzgg": SECTIONS["tzgg"]}
        elif args.cggg_only:
            selected = {"cggg": SECTIONS["cggg"]}
        elif args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in SECTIONS:
                    selected[label] = SECTIONS[label]
            if not selected:
                _safe_print(f"[ZJFW] No matching sections for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(SECTIONS)
        else:
            selected = dict(SECTIONS)

        _safe_print(f"[ZJFW] Sections to crawl: {len(selected)}")
        for label, (_, _, display) in selected.items():
            _safe_print(f"         - {display}")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[ZJFW] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[ZJFW] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # Initialise signature cache (fetches homepage on first use)
        sig_cache = {"sig": None, "key": None, "home_url": args.target_url.rstrip("/")}

        # -----------------------------------------------------------------------
        # Step 1: Crawl listing pages
        # -----------------------------------------------------------------------
        _safe_print("[ZJFW] Step 1/4: Crawling listing pages...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}
        cutoff = datetime.now() - timedelta(days=args.max_days)

        for section_id, ((list_action, list_method), (detail_action, detail_method), display_label) in selected.items():
            _safe_print(f"[ZJFW]   Section '{display_label}':")
            sys.stdout.flush()

            section_articles = []
            page_size = 50 if section_id == "cggg" else 20
            start = 0

            while True:
                if args.max_articles and len(section_articles) >= args.max_articles:
                    break

                if section_id == "tzgg":
                    params = {"start": start, "limit": page_size, "child_type": "ZXJX"}
                else:
                    params = {"start": start, "limit": page_size}

                _safe_print(f"[ZJFW]     Fetching offset {start} (limit {page_size})")
                sys.stdout.flush()

                data = _call_api(list_action, list_method, params, sig_cache, client)
                if not data:
                    logging.warning("Failed to fetch listing for %s at offset %d",
                                    section_id, start)
                    break

                # Extract articles
                if section_id == "tzgg":
                    arts = _extract_tzgg_articles(data, display_label)
                else:
                    arts = _extract_cggg_articles(data, display_label)

                if not arts:
                    _safe_print("[ZJFW]     -> No more articles found, stopping")
                    sys.stdout.flush()
                    break

                # Filter by date and max_articles
                date_cutoff_hit = False
                for art in arts:
                    if args.max_articles and len(section_articles) >= args.max_articles:
                        break
                    if art.get("date") and art["date"] < cutoff:
                        date_cutoff_hit = True
                        break
                    section_articles.append(art)

                # Check if fewer results returned than page_size (last page)
                if len(arts) < page_size:
                    _safe_print(f"[ZJFW]     -> Last page ({len(arts)} items)")
                    sys.stdout.flush()
                    break

                if date_cutoff_hit:
                    _safe_print("[ZJFW]     -> Hit date cutoff, stopping pagination")
                    sys.stdout.flush()
                    break

                start += page_size
                time.sleep(0.3)

            count = len(section_articles)
            section_stats[display_label] = count
            all_articles.extend(section_articles)

            _safe_print(f"[ZJFW]     -> {count} articles\n")
            sys.stdout.flush()

        _safe_print(f"[ZJFW] Collected {len(all_articles)} total articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ZJFW] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print("[ZJFW] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Deduplicate with state (by article ID)
        if processed_ids:
            new_articles = [a for a in all_articles if a["id"] not in processed_ids]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[ZJFW] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print("[ZJFW] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # -----------------------------------------------------------------------
        # Step 2: Fetch detail pages
        # -----------------------------------------------------------------------
        _safe_print(f"\n[ZJFW] Step 2/4: Fetching {len(all_articles)} article details...\n")
        sys.stdout.flush()

        # Map display labels to detail API function
        def _get_detail_fn(display_label):
            if display_label == "通知公告":
                return _fetch_tzgg_detail
            if display_label == "采购公告":
                return _fetch_cggg_detail
            return None

        md_parts = []
        success_count = 0
        fail_count = 0
        total = len(all_articles)

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[ZJFW] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            detail_fn = _get_detail_fn(art["section"])
            if detail_fn is None:
                _safe_print("[ZJFW]   -> Unknown section, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            content, resources, metadata = detail_fn(art["id"], sig_cache, client)

            if not content:
                _safe_print("[ZJFW]   -> Empty content, skipped")
                sys.stdout.flush()
                fail_count += 1
                continue

            _safe_print(f"[ZJFW]   -> {len(content)} chars")
            sys.stdout.flush()

            article_date_str = ""
            if art.get("date"):
                article_date_str = art["date"].strftime("%Y-%m-%d")
            elif metadata.get("date"):
                article_date_str = metadata["date"]

            source_str = metadata.get("source", "")
            source_line = f"**Source:** {source_str}" if source_str else ""

            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section']}",
                f"**Date:** {article_date_str}",
            ]
            if source_line:
                lines.append(source_line)
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            md_parts.append("\n".join(lines))
            success_count += 1

        if not md_parts:
            _safe_print("[ZJFW] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[ZJFW] Details: {success_count} success, {fail_count} failed\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 3: Save markdown
        # -----------------------------------------------------------------------
        _safe_print("[ZJFW] Step 3/4: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        _safe_print(f"[ZJFW] Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        new_ids = [a["id"] for a in all_articles]
        if new_ids:
            processed_ids.update(new_ids)
            _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # -----------------------------------------------------------------------
        # Step 4: Upload to KB
        # -----------------------------------------------------------------------
        _safe_print(f"[ZJFW] Step 4/4: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s ...", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            _safe_print("[ZJFW] Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            _safe_print(f"[ZJFW] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
