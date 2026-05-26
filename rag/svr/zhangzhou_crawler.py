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
Dedicated web crawler for Zhangzhou Public Resource Trading Center
(漳州公共资源交易中心 — 工程建设 交易信息).

Crawls project construction listings with multi-tab detail pages.
Each listing item may link to a detail page with up to 7 tabs:
  招标公告, 招标答疑, 最高限价, 开标一览表, 中标候选人公示, 中标公告, 合同签署

Site characteristics
────────────────────
  • Listing → AJAX POST /proxy_api/publicResource/front/viewProjects
               JSON body with pageNum, pageSize, date range, filters.
               Server-rendered HTML shell with JS-rendered data listing.
  • Tabs    → GET /proxy_api/publicResource/front/relationProjects
               ?relationGuid=<guid> — returns all tab infoIDs for a project.
  • Detail  → GET /proxy_api/publicResource/front/projectDetail/<infoId>
               Returns JSON with infoContent (HTML), attachFiles, metadata.
  • Files   → Attachment paths from attachFiles; downloaded from same origin
               or via proxy paths.
  • Auth    → WAF cookies (HWWAFSESTIME, HWWAFSESID) + JSESSIONID from
               initial page visit. No access_token required.

Pagination: API returns totalSize; pageSize=15. ~50 pages for 3-day window.
Checkpoint: articles processed in batches of 10 with state persistence.
3600s timeout adaptation: max_runtime with graceful stop when < 120s remaining.

Usage (typically spawned by task_executor):
    python zhangzhou_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url http://ggzyjy.xzfwzx.zhangzhou.gov.cn/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import datetime
import json
import logging
import os
import random
import re
import sys
import time
import urllib.request
import zipfile
from urllib.parse import urljoin, urlparse

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
_MAIN_SITE = "http://ggzyjy.xzfwzx.zhangzhou.gov.cn"
_SITE_NAME = "漳州公共资源交易中心"

_LISTING_API = "/proxy_api/publicResource/front/viewProjects"
_DETAIL_API = "/proxy_api/publicResource/front/projectDetail/"

# Listing page for cookie acquisition
_LISTING_PAGE = (
    "/cms/sitemanage/index.shtml"
    "?siteId=40669965560550000"
    "&templateId=10671863355640000"
    "&cateNum=001001008"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_API_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
}

_HTML_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

# Tab labels mapped by cateNum
_TAB_LABELS = {
    "001001001": "招标计划",
    "001001002": "招标公告",
    "001001003": "招标答疑",
    "001001004": "最高限价",
    "001001005": "开标一览表",
    "001001006": "中标候选人公示",
    "001001007": "中标公告",
    "001001008": "合同签署",
}

# Sort order for tabs (by cateNum)
_TAB_ORDER_KEYS = [
    "001001001", "001001002", "001001003", "001001004",
    "001001005", "001001006", "001001007", "001001008",
]

# Anti-crawling delays (seconds)
_PAGE_DELAY = (2.0, 4.0)       # between listing pages
_ARTICLE_DELAY = (0.8, 2.0)    # between articles within batch
_TAB_DELAY = (0.3, 0.8)        # between tab detail fetches

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

# Tab names in order
_TAB_ORDER = [
    "招标计划", "招标公告", "招标答疑", "最高限价",
    "开标一览表", "中标候选人公示", "中标公告", "合同签署",
]


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


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session():
    """Visit listing page to obtain WAF cookies and JSESSIONID."""
    sess = _requests.Session()
    sess.headers.update(_HTML_HEADERS)
    try:
        sess.get(f"{_MAIN_SITE}{_LISTING_PAGE}", timeout=30)
        logging.info("Session initialized, cookies: %s",
                     {k: v[:20] for k, v in sess.cookies.items()})
    except Exception as e:
        logging.warning("Failed to init session cookies: %s", e)
    return sess


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _fetch_listing_api(sess, page_num, start_date, end_date):
    """POST to viewProjects API, return (items: list[dict], total_size: int)."""
    body = {
        "pageNum": page_num,
        "pageSize": 15,
        "projectType": None,
        "cateNum": None,
        "jurisdictionCode": None,
        "startTime": start_date,
        "endTime": end_date,
    }
    url = f"{_MAIN_SITE}{_LISTING_API}"
    try:
        resp = sess.post(url, json=body, headers=_API_HEADERS, timeout=90)
        data = resp.json()
    except Exception as e:
        logging.error("Listing API page %d error: %s", page_num, e)
        return [], 0

    code = data.get("code")
    if code != 200:
        logging.error("Listing API page %d returned code=%s", page_num, code)
        return [], 0

    d = data.get("data", {})
    items = d.get("resultList", [])
    total_size = d.get("totalSize", 0)
    return items, total_size


def _crawl_all_listing(sess, start_date, end_date, start_time=None, max_runtime=3300):
    """Crawl all listing pages and collect article metadata."""
    all_items = []
    seen_ids = set()

    # First page
    items, total_size = _fetch_listing_api(sess, 1, start_date, end_date)
    if not items:
        _safe_print("[LISTING] No items on page 1.")
        return []

    for item in items:
        iid = item.get("infoID", "")
        if iid and iid not in seen_ids:
            seen_ids.add(iid)
            all_items.append(item)

    page_size = 15
    total_pages = max(1, (total_size + page_size - 1) // page_size)
    _safe_print(f"[LISTING] Page 1/{total_pages}: {len(items)} items, "
                f"totalSize={total_size}, totalPages={total_pages}")
    sys.stdout.flush()

    if total_pages <= 1:
        _safe_print(f"[LISTING] Collected {len(all_items)} items (single page)")
        return all_items

    for p in range(2, total_pages + 1):
        if start_time and (time.time() - start_time) > (max_runtime - 120):
            _safe_print(f"[LISTING] Stopping early (runtime limit), "
                        f"got {len(all_items)} items")
            break

        _request_delay(*_PAGE_DELAY)

        items, _ = _fetch_listing_api(sess, p, start_date, end_date)
        new_count = 0
        for item in items:
            iid = item.get("infoID", "")
            if iid and iid not in seen_ids:
                seen_ids.add(iid)
                all_items.append(item)
                new_count += 1

        _safe_print(f"[LISTING] Page {p}/{total_pages}: {len(items)} items "
                    f"({new_count} new, total: {len(all_items)})")
        sys.stdout.flush()

        if not items:
            break

    _safe_print(f"[LISTING] Collected {len(all_items)} items from listing API")
    return all_items


# ---------------------------------------------------------------------------
# Grouping by relationguid
# ---------------------------------------------------------------------------

def _group_by_relationguid(items):
    """Group listing items by relationguid.

    Items sharing a relationguid belong to the same project
    and represent different tabs (different cateNum).

    Returns list of groups, each group is a list of items.
    Items with no relationguid become single-item groups.
    """
    groups_dict = {}
    no_rel_items = []

    for item in items:
        rel = item.get("relationguid")
        if rel and str(rel) != "null" and str(rel).strip():
            if rel not in groups_dict:
                groups_dict[rel] = []
            groups_dict[rel].append(item)
        else:
            no_rel_items.append(item)

    # Sort groups: multi-tab groups first, then sorted by tab count desc
    groups = sorted(groups_dict.values(), key=lambda g: -len(g))
    # Add single-item (no relationguid) groups
    for item in no_rel_items:
        groups.append([item])

    # Sort items within each group by cateNum order
    for group in groups:
        group.sort(key=lambda i: _TAB_ORDER_KEYS.index(i.get("cateNum", "999"))
                   if i.get("cateNum", "999") in _TAB_ORDER_KEYS else 999)

    return groups


# ---------------------------------------------------------------------------
# Detail API
# ---------------------------------------------------------------------------

def _fetch_detail_api(sess, info_id):
    """GET projectDetail API for one infoId."""
    url = f"{_MAIN_SITE}{_DETAIL_API}{info_id}"
    try:
        resp = sess.get(url, headers=_HTML_HEADERS, timeout=60)
        data = resp.json()
    except Exception as e:
        logging.error("Detail API error for %s: %s", info_id, e)
        return None
    return data


# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------

def _extract_detail_json(detail_data, tab_label=""):
    """Parse projectDetail JSON into markdown content and attachments.

    Returns (content_md: str, attachments: list[dict], metadata: dict).
    """
    if not detail_data:
        return "", [], {}

    title = detail_data.get("title") or ""
    info_content = detail_data.get("infoContent") or ""
    attach_files = detail_data.get("attachFiles") or []
    info_date = detail_data.get("infoDate") or ""
    category_num = detail_data.get("categoryNum") or ""

    # ── Parse HTML content ──
    content_text = ""
    if info_content:
        soup = BeautifulSoup(info_content, "lxml")
        # Remove scripts and styles
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        content_text = soup.get_text(separator="\n", strip=True)
        content_text = re.sub(r'\n{3,}', '\n\n', content_text)

    # ── Build markdown ──
    lines = []
    if tab_label:
        lines.append(f"## {tab_label}")
        lines.append("")
    if title and title != tab_label:
        lines.append(f"**标题:** {title}")
    if info_date:
        lines.append(f"**发布时间:** {info_date}")
    if category_num:
        cat_label = _TAB_LABELS.get(category_num, category_num)
        lines.append(f"**信息类型:** {cat_label}")
    lines.append("")

    if content_text:
        lines.append(content_text)
        lines.append("")

    # ── Attachments ──
    attachments = []
    for att in attach_files:
        if not isinstance(att, dict):
            continue
        att_name = att.get("fileName") or att.get("name") or ""
        att_path = att.get("filePath") or att.get("path") or att.get("url") or ""
        if not att_path:
            continue

        # Build full URL
        if att_path.startswith("http"):
            att_url = att_path
        elif att_path.startswith("/"):
            att_url = urljoin(_MAIN_SITE, att_path)
        else:
            att_url = urljoin(_MAIN_SITE, "/" + att_path)

        ext = os.path.splitext(att_path.split("?")[0])[1].lower()
        if ext or att_name:
            attachments.append({
                "filename": att_name or os.path.basename(att_path.split("?")[0]),
                "url": att_url,
            })

    content_md = "\n".join(lines)
    metadata = {
        "title": title or tab_label,
        "date_str": info_date,
        "category_num": category_num,
    }
    return content_md, attachments, metadata


def _process_project_group(sess, group, output_dir):
    """Process one project group: fetch each tab's detail, combine into one document.

    A group is a list of listing items sharing the same relationguid
    (or a single item without relationguid).

    Returns (combined_md: str, all_attachments: list, group_id: str, tab_count: int).
    """
    group_items = sorted(group, key=lambda i: (
        _TAB_ORDER_KEYS.index(i.get("cateNum", "999"))
        if i.get("cateNum", "999") in _TAB_ORDER_KEYS else 999
    ))

    first = group_items[0]
    # Use first item's title as project title (strip tab-specific prefix)
    project_title = first.get("title", "无标题")
    project_date = first.get("infoDate", "")
    project_district = first.get("projectaddressname", "")
    relationguid = first.get("relationguid") or ""
    group_id = relationguid if relationguid else first.get("infoID", "unknown")

    all_md_parts = []
    all_attachments = []
    tab_count = len(group_items)

    # Header
    header_lines = [
        f"# {project_title}",
        "",
    ]
    if project_date:
        header_lines.append(f"**发布时间:** {project_date}")
    if project_district:
        header_lines.append(f"**项目辖区:** {project_district}")
    header_lines.append(f"**来源:** {_SITE_NAME}")
    header_lines.append(f"**网站:** {_MAIN_SITE}")
    if tab_count > 1:
        header_lines.append(f"**包含页签:** {tab_count} 个")
    header_lines.append("")
    header_lines.append("---")
    header_lines.append("")
    all_md_parts.append("\n".join(header_lines))

    for item in group_items:
        info_id = item.get("infoID", "")
        if not info_id:
            continue

        cate_num = item.get("cateNum", "")
        tab_label = _TAB_LABELS.get(cate_num, cate_num or "基本信息")
        item_title = item.get("title", "")

        _request_delay(*_TAB_DELAY)
        detail_data = _fetch_detail_api(sess, info_id)

        if not detail_data:
            all_md_parts.append(
                f"## {tab_label}\n\n**{item_title}**\n\n*内容加载失败*\n"
            )
            continue

        md, atts, _ = _extract_detail_json(detail_data, tab_label)
        if md:
            all_md_parts.append(md)
        else:
            all_md_parts.append(
                f"## {tab_label}\n\n**{item_title}**\n\n*暂无内容*\n"
            )
        if atts:
            all_attachments.extend(atts)

    # Deduplicate attachments by URL
    seen_urls = set()
    unique_atts = []
    for att in all_attachments:
        if att["url"] not in seen_urls:
            seen_urls.add(att["url"])
            unique_atts.append(att)

    # Append attachment list
    if unique_atts:
        att_lines = ["\n## 附件\n"]
        for att in unique_atts:
            att_lines.append(f"- [{att['filename']}]({att['url']})")
        all_md_parts.append("\n".join(att_lines))

    combined_md = "\n\n".join(all_md_parts)
    return combined_md, unique_atts, group_id, tab_count


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

def _download_file(sess, file_url, timeout=120):
    """Download a binary file."""
    if file_url.startswith("http://"):
        file_url = file_url.replace("http://", "https://", 1)

    # Use session for same-domain
    parsed = urlparse(file_url)
    main_parsed = urlparse(_MAIN_SITE)
    if parsed.netloc == main_parsed.netloc or "zhangzhou.gov.cn" in parsed.netloc:
        try:
            resp = sess.get(file_url, timeout=timeout, stream=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            logging.error("Download error %s: %s", file_url, e)
        return None

    # External URLs: use urllib
    req = urllib.request.Request(file_url, headers=_HTML_HEADERS)
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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", doc_id, e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Article processing (batch of 10 with checkpoint)
# ---------------------------------------------------------------------------

def _process_groups(groups, output_dir, kb_id, tenant_id,
                    processed_ids, state, start_time, max_runtime, http_sess):
    """Process project groups in batches of 10 with checkpoint.

    Each group is a list of listing items sharing the same relationguid.
    Groups are deduplicated by group_id (relationguid or infoID).
    """
    # Filter already-processed groups
    new_groups = []
    for group in groups:
        group_id = _get_group_id(group)
        if group_id not in processed_ids:
            new_groups.append(group)

    if not new_groups:
        _safe_print("[PROCESS] All groups already processed.")
        sys.stdout.flush()
        return 0

    total_items_in_groups = sum(len(g) for g in new_groups)
    _safe_print(
        f"[PROCESS] {len(new_groups)} project group(s) to process "
        f"({total_items_in_groups} total tabs)\n"
    )
    sys.stdout.flush()

    BATCH_SIZE = 10
    total_processed = 0
    batch_num = 0

    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    for batch_start in range(0, len(new_groups), BATCH_SIZE):
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

        batch_groups = new_groups[batch_start:batch_start + BATCH_SIZE]
        batch_num += 1
        md_parts = []
        batch_ids = []
        all_attachment_files = []

        total_tabs = sum(len(g) for g in batch_groups)
        _safe_print(
            f"[PROCESS] === Batch {batch_num} "
            f"({len(batch_groups)} groups, {total_tabs} tabs) ==="
        )
        sys.stdout.flush()

        for idx, group in enumerate(batch_groups, 1):
            global_idx = batch_start + idx
            first_title = group[0].get("title", "无标题")[:80]
            tab_info = f"{len(group)} tab(s)" if len(group) > 1 else "single"
            _safe_print(
                f"[PROCESS]   [{global_idx}/{len(new_groups)}] "
                f"{first_title} ({tab_info})"
            )
            sys.stdout.flush()

            # Process the project group (fetch all tabs, combine)
            try:
                combined_md, attachments, group_id, tab_count = (
                    _process_project_group(http_sess, group, output_dir)
                )
            except Exception as e:
                logging.error("Group processing error for %s: %s",
                              _get_group_id(group), e)
                batch_ids.append(_get_group_id(group))
                _safe_print(f"           Failed to process group: {e}")
                continue

            if not combined_md:
                batch_ids.append(group_id)
                _safe_print(f"           Empty content")
                continue

            # Save individual markdown
            _save_markdown(combined_md, output_dir, group_id)
            md_parts.append(combined_md)
            batch_ids.append(group_id)

            # Download attachments
            local_files = []
            if attachments:
                _safe_print(f"           {len(attachments)} attachment(s)")
                sys.stdout.flush()
                for att in attachments:
                    fname = _sanitize_filename(att["filename"])
                    safe_gid = group_id.replace("/", "_").replace("\\", "_")[:60]
                    local_name = f"{safe_gid}_{fname}"
                    local_path = os.path.join(downloads_dir, local_name)

                    if (os.path.exists(local_path) and
                            os.path.getsize(local_path) > 100):
                        _safe_print(f"           (cached) {fname}")
                        local_files.append(local_path)
                        continue

                    _safe_print(f"           downloading: {fname[:80]}")
                    sys.stdout.flush()

                    blob = _download_file(http_sess, att["url"])
                    if blob and len(blob) > 100:
                        with open(local_path, "wb") as f:
                            f.write(blob)
                        _safe_print(
                            f"           saved: {local_name[:80]} "
                            f"({len(blob)} bytes)"
                        )
                        local_files.append(local_path)

                        # ZIP extraction
                        if local_path.lower().endswith(".zip") or (
                            len(blob) >= 4 and blob[:4] == b"PK\x03\x04"
                        ):
                            extracted = _extract_zip(local_path, downloads_dir)
                            local_files.extend(extracted)
                    else:
                        _safe_print(
                            f"           download failed for {fname[:60]}"
                        )

            for lf in local_files:
                all_attachment_files.append((lf, first_title))

            _request_delay(*_ARTICLE_DELAY)

        # ── Batch checkpoint ──
        if md_parts:
            batch_path = os.path.join(
                output_dir, f"zhangzhou_batch_{batch_num:04d}.md"
            )
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                try:
                    _upload_to_kb(batch_path, kb_id, tenant_id,
                                  parser_id="laws")
                    _safe_print(
                        f"[PROCESS]   Batch {batch_num} markdown "
                        f"uploaded to KB"
                    )
                except Exception as e:
                    _safe_print(
                        f"[PROCESS]   Batch {batch_num} upload failed: {e}"
                    )

            total_processed += len(md_parts)
            _safe_print(
                f"[PROCESS]   Batch {batch_num} done "
                f"({total_processed}/{len(new_groups)})\n"
            )
            sys.stdout.flush()

        # Upload attachments
        if all_attachment_files and kb_id:
            _safe_print(
                f"[PROCESS]   Uploading {len(all_attachment_files)} "
                f"attachment(s)..."
            )
            sys.stdout.flush()
            for local_path, _art_title in all_attachment_files:
                ext = os.path.splitext(local_path)[1].lower()
                pid = "laws" if ext in _LAWS_EXTENSIONS else "general"
                try:
                    _upload_to_kb(local_path, kb_id, tenant_id,
                                  parser_id=pid)
                except Exception as e:
                    _safe_print(
                        f"           Upload error: "
                        f"{os.path.basename(local_path)}: {e}"
                    )

    return total_processed


def _get_group_id(group):
    """Return the deduplication ID for a group."""
    if not group:
        return "empty"
    first = group[0]
    rel = first.get("relationguid")
    if rel and str(rel) != "null" and str(rel).strip():
        return str(rel)
    return first.get("infoID", "unknown")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Zhangzhou public resource trading crawler (工程建设)"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for KB upload")
    parser.add_argument("--target-url", required=True,
                        help="Homepage URL (http://ggzyjy.xzfwzx.zhangzhou.gov.cn/)")
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
    parser.add_argument("--max-days", type=int, default=3,
                        help="Days back from today to crawl (default: 3)")
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
    _safe_print("[ZHANGZHOU] 漳州公共资源交易中心 crawler")
    _safe_print(f"[ZHANGZHOU] Target: {args.target_url}")
    _safe_print(f"[ZHANGZHOU] KB: {args.kb_id}")
    _safe_print(f"[ZHANGZHOU] Task: {args.task_name}")
    _safe_print(f"[ZHANGZHOU] Date range: last {args.max_days} days")
    _safe_print(f"[ZHANGZHOU] Max runtime: {args.max_runtime}s")
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== ZHANGZHOU crawler started ===")

    global_start_time = time.time()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print(f"[ZHANGZHOU] Output directory: {output_dir}\n")
    sys.stdout.flush()

    # ── Compute date range ──
    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=args.max_days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    _safe_print(f"[ZHANGZHOU] Date range: {start_date} ~ {end_date}\n")
    sys.stdout.flush()

    # ── State ──
    state = _load_state(output_dir) if not args.full else {"processed_ids": []}
    processed_ids = set(state.get("processed_ids", []))
    _safe_print(
        f"[ZHANGZHOU] Previously processed: {len(processed_ids)} item(s)\n"
    )
    sys.stdout.flush()

    # ── Init session ──
    http_sess = _init_session()

    # ── Phase 1: Crawl listing ──
    _safe_print("[ZHANGZHOU] Phase 1: Crawling listing via API...\n")
    sys.stdout.flush()

    items = _crawl_all_listing(
        http_sess, start_date, end_date,
        start_time=global_start_time,
        max_runtime=args.max_runtime,
    )

    if not items:
        _safe_print("[ZHANGZHOU] No items found. Done.")
        return

    _safe_print(f"\n[ZHANGZHOU] {len(items)} total items from listing\n")
    sys.stdout.flush()

    if args.max_articles and len(items) > args.max_articles:
        items = items[:args.max_articles]
        _safe_print(f"[ZHANGZHOU] Limited to {args.max_articles} items\n")

    # ── Phase 1.5: Group items by relationguid ──
    _safe_print("[ZHANGZHOU] Phase 1.5: Grouping items by relationguid...\n")
    sys.stdout.flush()

    groups = _group_by_relationguid(items)
    multi_tab = sum(1 for g in groups if len(g) > 1)
    single_tab = sum(1 for g in groups if len(g) == 1)
    _safe_print(
        f"[ZHANGZHOU] {len(groups)} project groups: "
        f"{multi_tab} with multiple tabs, {single_tab} single\n"
    )
    sys.stdout.flush()

    # ── Phase 2: Process groups ──
    _safe_print("[ZHANGZHOU] Phase 2: Processing project groups...\n")
    sys.stdout.flush()

    total = _process_groups(
        groups, output_dir, args.kb_id, args.tenant_id,
        processed_ids, state, global_start_time, args.max_runtime,
        http_sess,
    )

    elapsed = time.time() - global_start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(
        f"[ZHANGZHOU] Done: {total} group(s) processed in {elapsed:.0f}s"
    )
    _safe_print(f"{'='*60}\n")
    sys.stdout.flush()
    logging.info("=== ZHANGZHOU crawler finished: %d groups ===", total)


if __name__ == "__main__":
    CONSUMER_NAME = "zhangzhou_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
