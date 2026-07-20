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
Dedicated web crawler for www.zycg.gov.cn (中央国家机关政府采购中心).

This site uses FreeCMS with AJAX-loaded content.  The listing API returns
paginated notices, and attached files (PDF/Word) require authentication via
an access_token cookie.

Usage (typically spawned by task_executor):
    python zycg_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.zycg.gov.cn/ \
        --kb-id <KB_ID> \
        --task-name <NAME> \
        --access-token <TOKEN>
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
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
# Site configuration
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://www.zycg.gov.cn"

# Listing API (no auth required)
_LISTING_API = "/freecms/rest/v1/notice/selectInfoMore.do"

# File list API (requires access_token cookie)
_FILE_LIST_API = "/freecms/rest/v1/notice/selectNoticeDocInfo.do"

# User info API (token validation)
_USER_INFO_API = "/gateway/gp-auth-center/rest/v1/user/userInfo"

# Known channels (extracted from the homepage JS)
CHANNELS = {
    "cggg": {
        "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
        "siteId": "6f5243ee-d4d9-4b69-abbd-1e40576ccd7d",
        "label": "采购公告",
        "default_params": {
            "implementWay": "1",
            "noticeType": "1,2,3,31,32,52,57,61",
        },
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description="ZYCG crawler for scheduled tasks")
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
                        help="Comma-separated list of section labels (default: all)")
    parser.add_argument("--llm-id", default=None, help="Unused (legacy)")
    parser.add_argument("--llm-model", default=None, help="Unused (legacy)")
    parser.add_argument("--access-token", default=None,
                        help="Access token for authenticated crawling")
    parser.add_argument("--max-articles", type=int, default=0,
                        help="Max articles to fetch per section (0 = unlimited)")
    parser.add_argument("--max-days", type=int, default=365,
                        help="Max age in days for articles (default: 365)")
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


def _fetch_json(client, url, params=None, timeout=30):
    """Fetch a JSON API endpoint and return parsed response."""
    try:
        from urllib.parse import urlencode
        if params:
            url = url + "?" + urlencode(params)
        resp = client.fetch_get(url)
        return resp.json()
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


def _fetch_html(client, url, timeout=30):
    """Fetch an HTML page and return decoded text."""
    try:
        resp = client.get(url)
        return resp.text
    except Exception as e:
        logging.error("Failed to fetch HTML %s: %s", url, e)
        return None


def _download_file(client, url, timeout=120):
    """Download a binary file and return (content, content_type)."""
    try:
        # Some file URLs use http, upgrade to https
        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)
        resp = client.get(url)
        return resp.content, resp.headers.get("Content-Type", "")
    except Exception as e:
        logging.error("Failed to download %s: %s", url, e)
        return None, None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(text):
    """Try to parse a date string; return datetime or None."""
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
# Token validation
# ---------------------------------------------------------------------------

def validate_token(access_token, client):
    """Validate the access_token by testing the file list API with a known notice.

    The file list API (selectNoticeDocInfo.do) requires the access_token as a
    cookie. If the token is accepted, the API returns a proper JSON response
    with code "200" (data may be empty if the notice has no attachments).

    Returns True if the token is valid, False otherwise.
    """
    if not access_token:
        return False

    # Establish browser session
    _fetch_html(client, f"{_SITE_ROOT}/freecms/site/zygjjgzfcgzx/index.html")

    # Get a recent notice ID from the listing API first
    listing_params = {
        "siteId": "6f5243ee-d4d9-4b69-abbd-1e40576ccd7d",
        "channel": "d0e7c5f4-b93e-4478-b7fe-61110bb47fd5",
        "currPage": "1",
        "pageSize": "5",
        "implementWay": "1",
        "noticeType": "1,2,3,31,32,52,57,61",
    }
    listing_data = _fetch_json(client, f"{_SITE_ROOT}{_LISTING_API}", params=listing_params)
    if not listing_data or not listing_data.get("data"):
        return False

    # Use the first notice ID to test file API access
    first_id = listing_data["data"][0].get("id", "")
    if not first_id:
        return False

    # Test file list API
    file_params = {"currPage": 1, "pageSize": 5, "id": first_id}
    file_data = _fetch_json(client, f"{_SITE_ROOT}{_FILE_LIST_API}", params=file_params)

    # If the API returns a proper JSON with code "200", the token is valid
    # (even if data is empty - the notice might just have no files)
    if file_data and file_data.get("code") == "200":
        return True

    return False


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _fetch_listing_page(client, channel_config, page=1, page_size=15):
    """Fetch one page of the listing API.

    Returns list[dict] with keys: id, title, date (datetime or None), section.
    """
    params = {
        "siteId": channel_config["siteId"],
        "channel": channel_config["channel"],
        "currPage": str(page),
        "pageSize": str(page_size),
    }
    # Merge default params (implementWay, noticeType)
    params.update(channel_config.get("default_params", {}))

    data = _fetch_json(client, f"{_SITE_ROOT}{_LISTING_API}", params=params)
    if not data or data.get("code") != "200":
        return []

    items = data.get("data", [])
    articles = []
    for item in items:
        article_id = item.get("id", "").strip()
        title = item.get("title", "").strip()
        if not article_id or not title:
            continue

        date_str = item.get("addtimeStr", "").strip()
        dt = _parse_date(date_str) if date_str else None

        articles.append({
            "id": article_id,
            "title": title,
            "pageurl": item.get("pageurl", "").strip(),
            "date": dt,
            "date_str": date_str,
            "section": channel_config["label"],
        })

    return articles


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _fetch_article_content(client, pageurl):
    """Fetch and parse a zycg article detail page.

    Args:
        client: PlaywrightHttpClient instance.
        pageurl: Relative URL path from the listing API (e.g.
                 "/freecms/site/zygjjgzfcgzx/ggxx/info/2026/xxx.html").

    Returns (markdown_text, metadata_dict).
    Returns ("", {}) on failure.
    """
    url = urljoin(_SITE_ROOT, pageurl)
    html = _fetch_html(client, url)
    if not html:
        return "", {}

    soup = BeautifulSoup(html, "lxml")

    # -- Extract metadata --
    meta_title = ""
    title_tag = soup.find("h4", class_="info-title")
    if title_tag:
        meta_title = title_tag.get_text(strip=True)

    # -- Strip clutter --
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript"]):
        tag.decompose()

    # -- Parse main content --
    content_div = soup.find("div", class_="info-text", id="printArea")
    if not content_div:
        # Fallback: try info-content
        content_div = soup.find("div", class_="info-content")
    if not content_div:
        logging.warning("No content div found for article %s", article_id)
        return "", {}

    markdown_text = _content_to_markdown(content_div)

    metadata = {"title": meta_title}
    return markdown_text, metadata


def _content_to_markdown(content_div):
    """Convert the content div to Markdown text."""
    _TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
             "li", "blockquote", "pre", "img",
             "div", "section",
             "span", "strong", "font", "em", "b", "i", "u", "a"}

    lines = []
    seen_texts = set()

    for el in content_div.find_all(list(_TAGS)):
        tn = el.name

        if tn == "img":
            src = el.get("src", "")
            alt = el.get("alt", "")
            if src:
                src = urljoin(_SITE_ROOT, src)
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
# File list & download
# ---------------------------------------------------------------------------

def _fetch_file_list(client, article_id):
    """Fetch the list of attached files for a notice.

    Returns list[dict] with keys: fileUrl, fileName.
    Returns [] on failure or if no files.
    """
    params = {"currPage": 1, "pageSize": 20, "id": article_id}
    data = _fetch_json(client, f"{_SITE_ROOT}{_FILE_LIST_API}", params=params)
    if not data or data.get("code") != "200":
        return []
    return data.get("data", [])


def _sanitize_filename(name):
    """Remove or replace characters that are problematic in filenames."""
    # Replace common problematic chars
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Remove control characters
    name = re.sub(r'[\x00-\x1f]', "", name)
    # Limit length
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:195] + ext
    return name.strip()


def _build_output_filename(article_title, article_date_str, file_name):
    """Build a descriptive filename with date prefix.

    Format: YYYY-MM-DD_Title_OriginalFilename.ext
    """
    date_part = article_date_str[:10] if article_date_str else ""
    title_part = _sanitize_filename(article_title)[:80]

    base, ext = os.path.splitext(file_name)
    if not ext:
        ext = ".bin"

    parts = [p for p in [date_part, title_part, _sanitize_filename(base)] if p]
    return "_".join(parts) + ext


# ---------------------------------------------------------------------------
# Persistence & incremental state
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


def _save_markdown(content, output_dir, article_id):
    """Save article content as markdown file under output_dir/articles/."""
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, f"{article_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _save_download(file_content, output_dir, filename):
    """Save a downloaded file to the output directory."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "wb") as f:
        f.write(file_content)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    """Upload a file to the knowledge base and queue it for parsing.

    Args:
        filepath: Absolute path to the file to upload.
        kb_id: Target knowledge base ID.
        tenant_id: Tenant ID.
        parser_id: Parser type to set on the document (default: "laws").
    """
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService
    from common.constants import ParserType

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

        # Override parser_id to "laws" mode
        try:
            DocumentService.update_by_id(doc_id, {"parser_id": parser_id})
            logging.info("Parser type set to '%s' for document %s", parser_id, doc_id)
        except Exception as e:
            logging.error("Failed to update parser_id for document %s: %s", doc_id, e)

        # Queue parsing
        try:
            DocumentService.begin2parse(doc_id)
            DocumentService.run(tenant_id, doc, {})
            logging.info("Parsing task queued for document %s", doc_id)
        except Exception as e:
            logging.error("Failed to queue parsing for document %s: %s", doc_id, e)

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
    _safe_print("[ZYCG] Starting 中央国家机关政府采购中心 crawler")
    _safe_print(f"[ZYCG] Target URL: {args.target_url}")
    _safe_print(f"[ZYCG] Task name: {args.task_name}")
    _safe_print(f"[ZYCG] Target KB: {args.kb_id}")
    _safe_print(f"[ZYCG] Max days: {args.max_days}")
    if args.max_articles:
        _safe_print(f"[ZYCG] Max articles/section: {args.max_articles}")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== ZYCG crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        # -----------------------------------------------------------------------
        # Step 0: Validate access token
        # -----------------------------------------------------------------------
        _safe_print("[ZYCG] Step 0/5: Validating access token...")
        sys.stdout.flush()

        if not args.access_token:
            _safe_print("[ZYCG] ERROR: --access-token is required for zycg crawler")
            sys.stdout.flush()
            sys.exit(1)

        if not validate_token(args.access_token, client):
            _safe_print("[ZYCG] ERROR: Access token is invalid or expired!")
            _safe_print("[ZYCG] Please update the access token in the task settings and try again.")
            sys.stdout.flush()
            sys.exit(1)

        _safe_print("[ZYCG] Access token is valid.\n")
        sys.stdout.flush()

        # Establish browser session
        _fetch_html(client, f"{_SITE_ROOT}/freecms/site/zygjjgzfcgzx/index.html")

        # -----------------------------------------------------------------------
        # Select sections
        # -----------------------------------------------------------------------
        if args.section:
            selected = {}
            for label in args.section.split(","):
                label = label.strip()
                if label in CHANNELS:
                    selected[label] = CHANNELS[label]
            if not selected:
                _safe_print(f"[ZYCG] No matching sections for '{args.section}', using all")
                sys.stdout.flush()
                selected = dict(CHANNELS)
        else:
            selected = dict(CHANNELS)

        _safe_print(f"[ZYCG] Sections to crawl: {len(selected)}")
        for label, cfg in selected.items():
            _safe_print(f"         - {cfg['label']} ({label})")
        sys.stdout.flush()

        output_dir = args.output_dir or os.path.join(
            _PROJECT_ROOT, "rag", args.task_name.strip()
        )
        os.makedirs(output_dir, exist_ok=True)
        _safe_print(f"\n[ZYCG] Output directory: {output_dir}\n")
        sys.stdout.flush()

        state = _load_state(output_dir) if not args.full else {"processed_ids": []}
        processed_ids = set(state.get("processed_ids", []))
        _safe_print(f"[ZYCG] Already processed: {len(processed_ids)} article(s)\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 1: Crawl listing pages
        # -----------------------------------------------------------------------
        _safe_print("[ZYCG] Step 1/5: Crawling listing pages...\n")
        sys.stdout.flush()

        all_articles = []
        section_stats = {}
        cutoff = datetime.now() - timedelta(days=args.max_days)

        for section_id, channel_cfg in selected.items():
            _safe_print(f"[ZYCG]   Section '{channel_cfg['label']}':")
            sys.stdout.flush()

            section_articles = []
            page = 1

            while True:
                if args.max_articles and len(section_articles) >= args.max_articles:
                    break

                _safe_print(f"[ZYCG]     Page {page}:")
                sys.stdout.flush()

                arts = _fetch_listing_page(client, channel_cfg, page=page)
                if not arts:
                    _safe_print("[ZYCG]     -> No more articles, stopping")
                    sys.stdout.flush()
                    break

                date_cutoff_hit = False
                for art in arts:
                    if args.max_articles and len(section_articles) >= args.max_articles:
                        break
                    if art.get("date") and art["date"] < cutoff:
                        date_cutoff_hit = True
                        break
                    section_articles.append(art)

                if date_cutoff_hit:
                    _safe_print("[ZYCG]     -> Hit date cutoff, stopping pagination")
                    sys.stdout.flush()
                    break

                if len(arts) < 15:
                    _safe_print(f"[ZYCG]     -> Last page ({len(arts)} items)")
                    sys.stdout.flush()
                    break

                page += 1
                time.sleep(0.3)

            count = len(section_articles)
            section_stats[channel_cfg["label"]] = count
            all_articles.extend(section_articles)

            _safe_print(f"[ZYCG]     -> {count} articles\n")
            sys.stdout.flush()

        _safe_print(f"[ZYCG] Collected {len(all_articles)} total articles\n")
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ZYCG] No articles found, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print("[ZYCG] Breakdown by section:")
        for label, count in sorted(section_stats.items(), key=lambda x: -x[1]):
            _safe_print(f"         - {label}: {count}")
        sys.stdout.flush()

        # Deduplicate with state
        if processed_ids:
            new_articles = [a for a in all_articles if a["id"] not in processed_ids]
            skipped = len(all_articles) - len(new_articles)
            _safe_print(f"\n[ZYCG] Skipping {skipped} already-processed article(s)")
            sys.stdout.flush()
            all_articles = new_articles

        if not all_articles:
            _safe_print("[ZYCG] All articles already processed, nothing to do")
            sys.stdout.flush()
            sys.exit(0)

        # -----------------------------------------------------------------------
        # Step 2: Fetch detail pages + file lists
        # -----------------------------------------------------------------------
        _safe_print(f"\n[ZYCG] Step 2/5: Fetching {len(all_articles)} article details...\n")
        sys.stdout.flush()

        total = len(all_articles)
        article_data = []  # list of dicts for processed articles

        for idx, art in enumerate(all_articles, 1):
            _safe_print(f"[ZYCG] [{idx}/{total}] {art['section']}: {art['title'][:70]}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s - %s", idx, total, art["section"], art["title"])

            # Fetch detail text
            content, metadata = _fetch_article_content(client, art.get("pageurl", ""))
            if not content:
                _safe_print("[ZYCG]   -> Empty content, skipping")
                sys.stdout.flush()
                continue

            _safe_print(f"[ZYCG]   -> Content: {len(content)} chars")
            sys.stdout.flush()

            # Fetch file list
            files = _fetch_file_list(client, art["id"])
            if files:
                _safe_print(f"[ZYCG]   -> Files: {len(files)} attachment(s)")
                for f in files:
                    _safe_print(f"         - {f.get('fileName', 'unknown')}")
            else:
                _safe_print("[ZYCG]   -> No attachments")
            sys.stdout.flush()

            article_date_str = ""
            if art.get("date"):
                article_date_str = art["date"].strftime("%Y-%m-%d")

            article_data.append({
                "id": art["id"],
                "title": art["title"],
                "date_str": article_date_str,
                "section": art["section"],
                "content": content,
                "files": files,
            })

            time.sleep(0.2)

        if not article_data:
            _safe_print("[ZYCG] No articles processed successfully, exiting")
            sys.stdout.flush()
            sys.exit(0)

        _safe_print(f"\n[ZYCG] Details fetched for {len(article_data)} article(s)\n")
        sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 3: Download attachments + save markdown
        # -----------------------------------------------------------------------
        _safe_print("[ZYCG] Step 3/5: Downloading attachments...\n")
        sys.stdout.flush()

        downloads_dir = os.path.join(output_dir, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        downloaded_files = []  # list of (local_path, article_title, article_date)

        for art in article_data:
            if not art["files"]:
                continue

            for f in art["files"]:
                file_url = f.get("fileUrl", "").strip()
                file_name = f.get("fileName", "document").strip()
                if not file_url:
                    continue

                # Build descriptive filename
                safe_name = _build_output_filename(art["title"], art["date_str"], file_name)
                local_path = os.path.join(downloads_dir, safe_name)

                # Skip if already downloaded
                if os.path.exists(local_path):
                    _safe_print(f"[ZYCG]   Already exists: {safe_name}")
                    downloaded_files.append((local_path, art["title"], art["date_str"]))
                    continue

                _safe_print(f"[ZYCG]   Downloading: {file_name}")
                sys.stdout.flush()

                file_content, content_type = _download_file(client, file_url)
                if not file_content:
                    _safe_print("[ZYCG]     -> Download failed, skipping")
                    sys.stdout.flush()
                    continue

                _save_download(file_content, downloads_dir, safe_name)
                _safe_print(f"[ZYCG]     -> Saved: {safe_name} ({len(file_content)} bytes)")
                sys.stdout.flush()

                downloaded_files.append((local_path, art["title"], art["date_str"]))
                time.sleep(0.5)

        _safe_print(f"\n[ZYCG] Downloaded {len(downloaded_files)} file(s)\n")
        sys.stdout.flush()

        # Save markdown for detail content
        _safe_print("[ZYCG] Saving article markdown...\n")
        sys.stdout.flush()

        md_parts = []
        for art in article_data:
            lines = [
                f"# {art['title']}",
                f"**Section:** {art['section']}",
                f"**Date:** {art['date_str']}",
                f"**ID:** {art['id']}",
                "",
                art["content"],
                "",
                "---",
            ]
            md_parts.append("\n".join(lines))
            _save_markdown("\n".join(lines), output_dir, art["id"])

        # Also save a combined markdown for full-text indexing
        combined = "\n\n".join(md_parts)
        combined_path = os.path.join(output_dir, "articles_combined.md")
        with open(combined_path, "w", encoding="utf-8") as f:
            f.write(combined)
        _safe_print(f"[ZYCG] Combined markdown: {combined_path} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Update state
        new_ids = [art["id"] for art in article_data]
        if new_ids:
            processed_ids.update(new_ids)
            _save_state(output_dir, {"processed_ids": list(processed_ids)})

        # -----------------------------------------------------------------------
        # Step 4: Upload downloaded files to KB
        # -----------------------------------------------------------------------
        if downloaded_files:
            _safe_print(f"[ZYCG] Step 4/5: Uploading {len(downloaded_files)} file(s) to KB {args.kb_id}...\n")
            sys.stdout.flush()

            upload_ok = 0
            upload_fail = 0
            for local_path, art_title, art_date in downloaded_files:
                _safe_print(f"[ZYCG]   Uploading: {os.path.basename(local_path)}")
                sys.stdout.flush()
                logging.info("Uploading %s to KB %s", local_path, args.kb_id)

                try:
                    doc_pairs = _upload_to_kb(local_path, args.kb_id, args.tenant_id)
                    upload_ok += 1
                    _safe_print("[ZYCG]     -> Upload & parse queued successfully")
                    sys.stdout.flush()
                except Exception as e:
                    upload_fail += 1
                    _safe_print(f"[ZYCG]     -> Upload failed: {e}")
                    sys.stdout.flush()
                    logging.error("Upload failed for %s: %s", local_path, e)

            _safe_print(f"\n[ZYCG] Upload complete: {upload_ok} success, {upload_fail} failed\n")
            sys.stdout.flush()
        else:
            _safe_print("[ZYCG] Step 4/5: No files to upload.\n")
            sys.stdout.flush()

        # -----------------------------------------------------------------------
        # Step 5: Upload combined markdown to KB for full-text indexing
        # -----------------------------------------------------------------------
        _safe_print("[ZYCG] Step 5/5: Uploading article text to KB for indexing...")
        sys.stdout.flush()

        try:
            _upload_to_kb(combined_path, args.kb_id, args.tenant_id, parser_id="naive")
            _safe_print(" done!\n")
            sys.stdout.flush()
        except Exception as e:
            _safe_print(f" failed: {e}\n")
            sys.stdout.flush()
            logging.error("Markdown upload failed: %s", e)

        _safe_print(f"[ZYCG] {'='*60}")
        _safe_print(f"[ZYCG] Crawl finished: {len(article_data)} articles, {len(downloaded_files)} files")
        _safe_print(f"[ZYCG] {'='*60}\n")
        sys.stdout.flush()

    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "zycg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
