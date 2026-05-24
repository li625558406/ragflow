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
Dedicated web crawler for ggzyjy.xzfwzx.putian.gov.cn/fwzx/ (莆田市公共资源交易中心).

Targets the 5 modules under 工程交易 on the fwzx page:
  1. 招标计划        /ptsq/005001/005001021/guidetyright.html
  2. 招标文件预公示  /ptsq/005001/005001022/guidetyright.html
  3. 招标信息        /ptsq/005001/005001006/guidetyright.html
  4. 中标候选人公示  /ptsq/005001/005001012/guidetyright.html
  5. 中标结果公示    /ptsq/005001/005001011/guidetyright.html

Site characteristics
────────────────────
  - Built on Epoint WebBuilder CMS (SSR HTML, no SPA)
  - Listing pages: single-page SSR, no pagination, all items rendered
  - Detail pages: metadata table + content + js-driven attachment downloads
  - Attachments: two-step JS download (downloadztbattach → form auto-submit)
  - No captcha / AES encryption / rate-limit walls observed
  - requests.Session for list/detail pages; Playwright for attachment downloads

Checkpoint/resume: each module is processed independently.
If the 3600s task timeout kills the run mid-way, the next trigger resumes
from the next incomplete module.

Usage:
    python putian_fwzx_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url https://ggzyjy.xzfwzx.putian.gov.cn/fwzx/ \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import base64
import json
import logging
import os
import random
import re
import sys
import time
import zipfile
from datetime import datetime
from io import BytesIO
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
# Playwright (required for attachment downloads)
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import ddddocr

    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://ggzyjy.xzfwzx.putian.gov.cn"
_SITE_NAME = "莆田市公共资源交易中心"

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

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

_MODULES = [
    {"key": "zbjh", "name": "招标计划",
     "list_url": "/ptsq/005001/005001021/guidetyright.html"},
    {"key": "zbysgs", "name": "招标文件预公示",
     "list_url": "/ptsq/005001/005001022/guidetyright.html"},
    {"key": "zbxx", "name": "招标信息",
     "list_url": "/ptsq/005001/005001006/guidetyright.html"},
    {"key": "zbhxrgs", "name": "中标候选人公示",
     "list_url": "/ptsq/005001/005001012/guidetyright.html"},
    {"key": "zbjg", "name": "中标结果公示",
     "list_url": "/ptsq/005001/005001011/guidetyright.html"},
]

# Anti-crawling: random delays between requests
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

_STATE_FILENAME = "_crawler_state.json"

# -- Attachment download regex -------------------------------------------------
_ATTACH_PATTERN = re.compile(r"ztbfjyz\('([^']+)','([^']+)','([^']+)'\)")
_ARTICLE_LINK_PATTERN = re.compile(
    r"/ptsq/\d+/\d+/\d+/(\d{8})/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.html"
)


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


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _init_session():
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get(_SITE_ROOT + "/", timeout=30)
        time.sleep(1)
    except Exception:
        pass
    return sess


def _fetch_page(sess, url, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, timeout=60)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp.text
            if attempt < max_retries:
                time.sleep(2 * attempt)
        except Exception as e:
            logging.warning("Fetch %s failed (attempt %d): %s", url, attempt, e)
            if attempt < max_retries:
                time.sleep(2 * attempt)
    return ""


# ---------------------------------------------------------------------------
# List extraction
# ---------------------------------------------------------------------------

def _extract_list_items(html, list_url):
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True)
        if not href or not text or len(text) < 4:
            continue
        m = _ARTICLE_LINK_PATTERN.search(href)
        if not m:
            continue
        art_id = m.group(2)
        date_str = m.group(1)
        if len(date_str) == 8:
            date_str = "{}-{}-{}".format(date_str[:4], date_str[4:6], date_str[6:8])
        if art_id in seen:
            continue
        seen.add(art_id)
        articles.append({
            "id": art_id,
            "title": text,
            "url": urljoin(_SITE_ROOT, href),
            "date_str": date_str,
        })
    return articles


# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------

def _extract_detail(html, detail_url):
    result = {"title": "", "date": "", "content_text": "", "attachments": []}
    soup = BeautifulSoup(html, "html.parser")

    # Title: try multiple strategies
    GENERIC_TITLES = ("招标计划发布表", "工程建设项目", "提前公示表")

    # 1) <h3> inside .ewb-article (works for 招标信息/中标候选人公示/中标结果公示)
    article_el = soup.select_one(".ewb-article")
    if article_el:
        h3 = article_el.select_one("h3")
        if h3:
            text = h3.get_text(strip=True)
            if text and len(text) > 2 and len(text) < 200 \
                    and not any(kw in text for kw in GENERIC_TITLES):
                result["title"] = text

    # 2) <h1>, .bt, etc.
    if not result["title"]:
        for sel in ("h1", ".detail-title", ".bt", "[class*='title']"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) > 2 and len(text) < 200 \
                        and not any(kw in text for kw in GENERIC_TITLES):
                    result["title"] = text
                    break

    # 3) Metadata table's 项目名称 row (招标计划 type)
    if not result["title"]:
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True)
                    if "项目名称" in key or "标段名称" in key or "工程名称" in key:
                        val = cells[-1].get_text(strip=True)
                        if val and len(val) > 2:
                            result["title"] = val
                            break
            if result["title"]:
                break

    # 4) Breadcrumb
    if not result["title"]:
        breadcrumb = soup.select_one(
            ".ewb-location, .location, [class*='location'], [class*='breadcrumb']")
        if breadcrumb:
            items = breadcrumb.get_text(strip=True).split(">")
            for item in reversed(items):
                item = item.strip()
                if item and len(item) > 3 and "首页" not in item \
                        and "工程交易" not in item:
                    result["title"] = item
                    break

    # Date extraction
    body_text = soup.get_text()
    date_m = re.search(r"发布时间[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", body_text)
    if date_m:
        result["date"] = date_m.group(1)
    else:
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
        if date_m:
            result["date"] = date_m.group(1)

    # Content: metadata table + body text
    content_parts = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        table_text = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                val = cells[-1].get_text(strip=True)
                if key and val and len(key) < 30 and len(key) > 1:
                    table_text.append("{}: {}".format(key, val))
        if table_text:
            joined = "\n".join(table_text)
            if len(joined) > 30:
                content_parts.append(joined)

    # Body text from content containers
    main_content = ""
    for sel in (".ewb-article", ".article-content", ".detail-content",
                ".content", "[class*='content']", "article"):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 50:
                main_content = text
                break

    if not main_content:
        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()
        main_content = soup.get_text(separator="\n", strip=True)
        cutoff_markers = ["国家部委", "联系地址：", "主办：", "备案序号：",
                          "网站标识码", "附件下载："]
        for marker in cutoff_markers:
            idx = main_content.find(marker)
            if idx > 200:
                main_content = main_content[:idx]
                break

    if main_content:
        content_parts.append(main_content)

    result["content_text"] = "\n\n".join(content_parts)

    # Extract attachments from onclick handlers
    for a_tag in soup.find_all("a", onclick=True):
        onclick = a_tag["onclick"]
        name = a_tag.get("title") or a_tag.get_text(strip=True)
        m = _ATTACH_PATTERN.search(onclick)
        if m:
            result["attachments"].append({
                "name": name,
                "download_path": m.group(1),
            })

    return result


# ---------------------------------------------------------------------------
# Attachment download (via Playwright — required for 2-step JS flow)
# ---------------------------------------------------------------------------

def _download_attachments_for_article(context, ocr_engine, detail_url, attachments,
                                       dest_dir, module_key, art_id,
                                       max_captcha_retries=5):
    """Download all attachments for an article via Playwright + ddddocr captcha.

    The site download flow:
      1. Detail page has <a class="down-txt"> links with onclick="ztbfjyz(url,'1','1')"
      2. Clicking opens a layer popup with pageVerify.html (captcha image in base64)
      3. Enter captcha code → click 确定 → download fires
      4. ztbfjyz(): when captcha is correct, the layer closes and file downloads;
         when wrong, shows "验证码错误!" and the popup stays open
    """
    if not attachments:
        return {}

    os.makedirs(dest_dir, exist_ok=True)
    results = {}
    page = None

    try:
        page = context.new_page()
        page.set_default_timeout(60000)

        download_holder = {}

        def _on_download(dl):
            name = dl.suggested_filename
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)
            save_path = os.path.join(dest_dir, safe_name)
            dl.save_as(save_path)
            download_holder['path'] = save_path
            _safe_print("      Downloaded: {} ({} bytes)".format(
                safe_name, os.path.getsize(save_path) if os.path.exists(save_path) else 0))

        page.on("download", _on_download)

        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        attach_links = page.query_selector_all('.down-txt')
        if not attach_links:
            logging.info("No .down-txt links found on %s", detail_url)
            return results

        for idx in range(len(attach_links)):
            if idx >= len(attachments):
                break

            att = attachments[idx]
            att_name = att.get("name", "unknown")
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", att_name)
            dest_path = os.path.join(dest_dir, safe_name)

            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
                results[att_name] = dest_path
                continue

            download_holder.clear()
            solved = False

            for retry in range(max_captcha_retries):
                # Close any lingering layer
                page.evaluate("""() => {
                    try { if (typeof layer !== 'undefined') layer.closeAll(); } catch(e) {}
                    document.querySelectorAll('.layui-layer-shade,.layui-layer-moves').forEach(
                        el => el.remove());
                }""")
                page.wait_for_timeout(500)

                # Re-query links (DOM may have changed after layer close)
                links = page.query_selector_all('.down-txt')
                if idx >= len(links):
                    break
                links[idx].click()
                page.wait_for_timeout(3000)

                # Find captcha iframe
                captcha_frame = None
                for frame in page.frames:
                    if 'pageVerify' in frame.url:
                        captcha_frame = frame
                        break

                if not captcha_frame:
                    continue

                # Extract base64 captcha image and imgguid
                img_info = captcha_frame.evaluate("""() => {
                    const img = document.getElementById('imgVerify');
                    if (!img) return null;
                    const src = img.src;
                    const comma = src.indexOf(',');
                    return {
                        base64: src.slice(comma + 1),
                        imgguid: document.getElementById('imgguid').value
                    };
                }""")
                if not img_info:
                    continue

                img_bytes = base64.b64decode(img_info['base64'])
                captcha_text = ocr_engine.classification(img_bytes)

                captcha_frame.fill('#yzm', captcha_text)
                page.wait_for_timeout(300)

                # Click 确定
                page.evaluate(
                    "() => { var b = document.querySelector('.layui-layer-btn0'); if(b) b.click(); }")
                page.wait_for_timeout(5000)

                if download_holder.get('path'):
                    results[att_name] = download_holder['path']
                    solved = True
                    break

                # Check if layer is still visible (captcha was wrong)
                layer_visible = page.evaluate(
                    "() => document.querySelectorAll('.layui-layer:not(.layui-layer-moves)').length > 0")
                if layer_visible:
                    # Refresh captcha: click image
                    for frame in page.frames:
                        if 'pageVerify' in frame.url:
                            try:
                                frame.click('#imgVerify')
                                page.wait_for_timeout(1000)
                            except Exception:
                                pass

            if not solved:
                _safe_print("      FAILED: {} (after {} retries)".format(
                    att_name, max_captcha_retries))

            # Re-navigate to detail page for next attachment
            if idx < len(attach_links) - 1:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                attach_links = page.query_selector_all('.down-txt')

    except Exception as e:
        logging.warning("Playwright download error for %s: %s", detail_url, e)
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    return results


# ---------------------------------------------------------------------------
# File extraction
# ---------------------------------------------------------------------------

def _extract_zip(zip_path):
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("__MACOSX") or name.startswith("."):
                    continue
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, "wb") as f:
                    f.write(zf.read(name))
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
                return "\n\n".join(parts)
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
                return "\n\n".join(parts)
            except ImportError:
                return "(PDF file, no parser available)"
        elif ext == ".docx":
            import docx
            doc = docx.Document(filepath)
            return "\n".join(
                p.text for p in doc.paragraphs if p.text.strip())
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(
                        " | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)
    return None


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------

def _build_markdown(art, detail, attachment_texts):
    title = detail.get("title") or art.get("title", "无标题")
    lines = [
        "# {}".format(title),
        "",
        "**栏目:** {}".format(art.get("section", "")),
    ]
    date_str = detail.get("date") or art.get("date_str", "")
    if date_str:
        lines.append("**日期:** {}".format(date_str))
    lines.append("**URL:** {}".format(art.get("url", "")))
    lines.append("")

    content = detail.get("content_text", "")
    if content:
        if len(content) > 50000:
            content = content[:50000] + "\n\n（内容过长，已截断）"
        lines.append("## 详细内容")
        lines.append("")
        lines.append(content)
        lines.append("")

    if attachment_texts:
        lines.append("## 附件内容")
        lines.append("")
        for fname, ftext in attachment_texts:
            lines.append("### {}".format(fname))
            lines.append("")
            if ftext:
                if len(ftext) > 50000:
                    ftext = ftext[:50000] + "\n\n（附件内容过长，已截断）"
                lines.append(ftext)
            else:
                lines.append("（无法提取文本内容）")
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
    return {"processed_ids": [], "completed_sections": []}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d sections done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_sections", [])))


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
# Module-level processing
# ---------------------------------------------------------------------------

def _process_module(sess, pw_context, ocr_engine, output_dir, kb_id, tenant_id,
                    module_info, processed_ids, state):
    module_key = module_info["key"]
    module_name = module_info["name"]
    list_url = urljoin(_SITE_ROOT, module_info["list_url"])

    if module_key in state.get("completed_sections", []):
        _safe_print("[{}]   Already completed, skipping.".format(module_name))
        sys.stdout.flush()
        return 0

    _safe_print("[{}]   Fetching listing: {}".format(module_name, list_url))
    sys.stdout.flush()

    list_html = _fetch_page(sess, list_url)
    if not list_html:
        _safe_print("[{}]   ERROR: Failed to fetch listing page".format(module_name))
        sys.stdout.flush()
        return 0

    articles = _extract_list_items(list_html, list_url)
    _safe_print("[{}]   Found {} total articles".format(
        module_name, len(articles)))
    sys.stdout.flush()

    new_articles = [a for a in articles if a["id"] not in processed_ids]
    _safe_print("[{}]   {} new, {} already processed".format(
        module_name, len(new_articles), len(articles) - len(new_articles)))
    sys.stdout.flush()

    if not new_articles:
        if module_key not in state.get("completed_sections", []):
            state.setdefault("completed_sections", []).append(module_key)
            _save_state(output_dir, state)
        return 0

    for a in new_articles:
        a["section"] = module_name

    BATCH_SIZE = 10
    total_processed = 0
    fail_count = 0
    downloads_dir = os.path.join(output_dir, "downloads")

    for batch_start in range(0, len(new_articles), BATCH_SIZE):
        batch = new_articles[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        md_parts = []
        batch_ids = []

        for idx, art in enumerate(batch, 1):
            global_idx = batch_start + idx
            _safe_print("[{}]   [{}/{}] {}".format(
                module_name, global_idx, len(new_articles),
                art["title"][:60]))
            sys.stdout.flush()

            detail_html = _fetch_page(sess, art["url"])
            if not detail_html:
                fail_count += 1
                detail = {
                    "title": art["title"],
                    "date": art.get("date_str", ""),
                    "content_text": "标题: {}\n日期: {}\nURL: {}".format(
                        art["title"], art.get("date_str", ""), art["url"]),
                    "attachments": [],
                }
            else:
                detail = _extract_detail(detail_html, art["url"])

            # Download attachments (via Playwright + ddddocr when available)
            attachment_texts = []
            if pw_context and ocr_engine and detail.get("attachments"):
                dest_dir = os.path.join(downloads_dir, module_key, art["id"])
                file_results = _download_attachments_for_article(
                    pw_context, ocr_engine, art["url"],
                    detail["attachments"], dest_dir,
                    module_key, art["id"])
                for att in detail["attachments"]:
                    att_name = att.get("name", "unknown")
                    fp = file_results.get(att_name)
                    if fp:
                        is_zip = (
                                fp.lower().endswith((".zip", ".rar")) or
                                (os.path.getsize(fp) >= 4 and
                                 open(fp, "rb").read(4) == b"PK\x03\x04")
                        )
                        if is_zip:
                            extracted = _extract_zip(fp)
                            for ext_fp in extracted:
                                text = _extract_file_text(ext_fp)
                                attachment_texts.append(
                                    (os.path.basename(ext_fp), text))
                        else:
                            text = _extract_file_text(fp)
                            attachment_texts.append((att_name, text))

            md = _build_markdown(art, detail, attachment_texts)

            articles_dir = os.path.join(output_dir, "articles", module_key)
            os.makedirs(articles_dir, exist_ok=True)
            md_path = os.path.join(articles_dir,
                                   "{}.md".format(art["id"]))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md)

            md_parts.append(md)
            batch_ids.append(art["id"])

            _request_delay()

        # Checkpoint
        if md_parts:
            batch_path = os.path.join(
                output_dir, "{}_{:03d}.md".format(module_key, batch_num))
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(md_parts))

            processed_ids.update(batch_ids)
            state["processed_ids"] = list(processed_ids)
            _save_state(output_dir, state)

            if kb_id:
                try:
                    _upload_to_kb(batch_path, kb_id, tenant_id)
                except Exception as e:
                    _safe_print("[{}]   batch {} upload failed: {}".format(
                        module_name, batch_num, e))
                    logging.error("Upload failed for %s batch %d: %s",
                                  module_name, batch_num, e)

            total_processed += len(md_parts)
            _safe_print("[{}]   batch {} uploaded ({}/{} done)".format(
                module_name, batch_num, total_processed,
                len(new_articles)))
            sys.stdout.flush()

    if module_key not in state.get("completed_sections", []):
        state.setdefault("completed_sections", []).append(module_key)
        _save_state(output_dir, state)

    _safe_print("[{}]   Done: {} processed, {} failed\n".format(
        module_name, total_processed, fail_count))
    sys.stdout.flush()
    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="putian.fwzx crawler - 莆田市公共资源交易中心工程交易"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://ggzyjy.xzfwzx.putian.gov.cn/fwzx/")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Comma-separated: zbjh,zbysgs,zbxx,zbhxrgs,zbjg")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime (default: 3300s=55min)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[PUTIAN] 莆田市公共资源交易中心 工程交易 crawler")
    _safe_print("[PUTIAN] KB: {}".format(args.kb_id))
    _safe_print("[PUTIAN] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Putian FWZX crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[PUTIAN] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_sections": []
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_sections = set(state.get("completed_sections", []))
    _safe_print("[PUTIAN] Previously processed: {}, completed modules: {}\n".format(
        len(processed_ids), len(completed_sections)))
    sys.stdout.flush()

    if args.section:
        selected = set(args.section.split(","))
        active_modules = [m for m in _MODULES if m["key"] in selected]
    else:
        active_modules = list(_MODULES)

    _safe_print("[PUTIAN] Modules: {}".format(
        ", ".join(m["name"] for m in active_modules)))
    sys.stdout.flush()

    # Init requests session for list/detail pages
    sess = _init_session()

    # Init Playwright browser for attachment downloads
    pw_context = None
    pw_browser = None
    pw_playwright = None

    if PLAYWRIGHT_AVAILABLE:
        chrome_path = _find_chrome()
        if chrome_path:
            try:
                pw_playwright = sync_playwright().start()
                pw_browser = pw_playwright.chromium.launch(
                    headless=True,
                    executable_path=chrome_path,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-sandbox"],
                )
                pw_context = pw_browser.new_context(
                    user_agent=_USER_AGENT,
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                )
                _safe_print("[PUTIAN] Playwright browser started "
                            "(for attachment downloads)\n")
                sys.stdout.flush()
            except Exception as e:
                _safe_print("[PUTIAN] WARNING: Playwright failed: {}"
                            .format(e))
                _safe_print("[PUTIAN]   Attachments will be skipped.\n")
                sys.stdout.flush()
    else:
        _safe_print("[PUTIAN] WARNING: playwright not installed, "
                    "attachments will be skipped.\n")
        sys.stdout.flush()

    # Init ddddocr for captcha solving
    ocr_engine = None
    if DDDDOCR_AVAILABLE:
        try:
            ocr_engine = ddddocr.DdddOcr(show_ad=False)
            _safe_print("[PUTIAN] ddddocr captcha solver ready\n")
            sys.stdout.flush()
        except Exception as e:
            _safe_print("[PUTIAN] WARNING: ddddocr init failed: {}"
                        .format(e))
            _safe_print("[PUTIAN]   Attachments will be skipped.\n")
            sys.stdout.flush()

    try:
        run_start = time.time()
        total_processed = 0
        stopped_early = False

        for mod_info in active_modules:
            elapsed = time.time() - run_start
            remaining = args.max_runtime - elapsed
            grace = min(120, args.max_runtime * 0.05)
            if remaining < grace:
                _safe_print(
                    "\n[PUTIAN] Runtime {:.0f}s, remaining {:.0f}s < "
                    "grace {:.0f}s, stopping early.".format(
                        elapsed, remaining, grace))
                sys.stdout.flush()
                stopped_early = True
                break

            n = _process_module(sess, pw_context, ocr_engine, output_dir, args.kb_id,
                                args.tenant_id, mod_info, processed_ids, state)
            total_processed += n

        _safe_print("\n" + "=" * 60)
        if stopped_early:
            _safe_print("[PUTIAN] Partial run: {} articles.".format(
                total_processed))
        else:
            _safe_print("[PUTIAN] Done: {} articles processed.".format(
                total_processed))
        _safe_print("=" * 60 + "\n")
        sys.stdout.flush()
        logging.info("=== Putian FWZX crawler finished: %d articles ===",
                     total_processed)

    finally:
        if pw_context:
            try:
                pw_context.close()
            except Exception:
                pass
        if pw_browser:
            try:
                pw_browser.close()
            except Exception:
                pass
        if pw_playwright:
            try:
                pw_playwright.stop()
            except Exception:
                pass


if __name__ == "__main__":
    CONSUMER_NAME = "putian_fwzx_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
