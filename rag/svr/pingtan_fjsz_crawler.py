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
Dedicated web crawler for xzfwzx.pingtan.gov.cn:9999/fjsz/ (平潭综合实验区公共资源统一平台).

Targets all modules and sub-categories under 交易信息:
  建设工程: 房建市政, 园林绿化, 交通港航, 水利工程, 海洋渔业, 林业工程,
           环保工程, 物业工程, 土地整治, 信息工程, 工业项目, 渔港工程
  产权交易, 政府采购, 土地交易, 海域使用权, 小型项目, 其他

Site characteristics
--------------------
  - Java / Spring MVC backend (GBMP bidding platform)
  - Listing pages: port 9999, SSR HTML for 建设工程, AJAX for others
  - Detail pages: port 9998, /G2/gbmp/progress/js-tender-notice!redirectStep.do
  - Attachments: port 9998, /G2/upload!download.do?attachId=X
  - Pagination: index.jhtml (page 1), index_N.jhtml (page 2+)
  - Anti-crawling: browser User-Agent required, random delays

Detail page structure
---------------------
  - Sidebar with notice type tabs (招标公告, 评标结果公示, etc.)
  - Main content area with project info
  - Attachments listed with file names and download links
  - GZ7 files (compressed format) need special handling

Date filtering
--------------
  Only articles with today's date are processed. This keeps the daily
  crawl volume manageable and ensures only fresh content is ingested.

Checkpoint/resume
-----------------
  Each module is processed independently. State tracks:
  - processed_ids: set of "projectId_packageId" already processed
  - module_progress: {module_key: {"page": N, "done": bool}}

  If the 3600s timeout kills the run, next trigger resumes from where it left off.

Usage:
    python pingtan_fjsz_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://xzfwzx.pingtan.gov.cn:9999/fjsz/index.jhtml \
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

from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# Playwright required for detail pages (port 9998) and AJAX module listings
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://xzfwzx.pingtan.gov.cn:9999"
_DETAIL_ROOT = "https://xzfwzx.pingtan.gov.cn:9998"
_SITE_NAME = "平潭综合实验区公共资源统一平台"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Module listing pages (left sidebar)
_MODULES = [
    # 建设工程 with sub-categories
    {"key": "fjsz", "name": "建设工程-房建市政",
     "list_url": "/fjsz/index.jhtml", "sub_cat": None},
    {"key": "yllh", "name": "建设工程-园林绿化",
     "list_url": "/yllh/index.jhtml", "sub_cat": None},
    {"key": "jtgh", "name": "建设工程-交通港航",
     "list_url": "/jtgh/index.jhtml", "sub_cat": None},
    {"key": "slgc", "name": "建设工程-水利工程",
     "list_url": "/slgc/index.jhtml", "sub_cat": None},
    {"key": "hyyy", "name": "建设工程-海洋渔业",
     "list_url": "/hyyy/index.jhtml", "sub_cat": None},
    {"key": "lygc", "name": "建设工程-林业工程",
     "list_url": "/lygc/index.jhtml", "sub_cat": None},
    {"key": "hbgc", "name": "建设工程-环保工程",
     "list_url": "/hbgc/index.jhtml", "sub_cat": None},
    {"key": "wygc", "name": "建设工程-物业工程",
     "list_url": "/wygc/index.jhtml", "sub_cat": None},
    {"key": "tdzz", "name": "建设工程-土地整治",
     "list_url": "/tdzz/index.jhtml", "sub_cat": None},
    {"key": "xxgc", "name": "建设工程-信息工程",
     "list_url": "/xxgc/index.jhtml", "sub_cat": None},
    {"key": "gyxm", "name": "建设工程-工业项目",
     "list_url": "/gyxm/index.jhtml", "sub_cat": None},
    {"key": "yggc", "name": "建设工程-渔港工程",
     "list_url": "/yggc/index.jhtml", "sub_cat": None},
    # Other modules
    {"key": "cqjy", "name": "产权交易",
     "list_url": "/cqjycbzl/index.jhtml", "sub_cat": None},
    {"key": "zfcg", "name": "政府采购",
     "list_url": "/zfcgzbcg/index.jhtml", "sub_cat": None},
    {"key": "tdjy", "name": "土地交易",
     "list_url": "/tdjytkqcrgg/index.jhtml", "sub_cat": None},
    {"key": "hysyq", "name": "海域使用权",
     "list_url": "/hysyqzbgg/index.jhtml", "sub_cat": None},
    {"key": "xxxm", "name": "小型项目",
     "list_url": "/xxxmzbgg/index.jhtml", "sub_cat": None},
    {"key": "other", "name": "其他",
     "list_url": "/othergg/index.jhtml", "sub_cat": None},
]

# Anti-crawling: random delays
_REQUEST_DELAY_MIN = 0.5
_REQUEST_DELAY_MAX = 1.5

_STATE_FILENAME = "_crawler_state.json"

# -- Regex patterns -----------------------------------------------------------
_PROJECT_ID_RE = re.compile(r"projectId=([a-f0-9]+)", re.I)
_PACKAGE_ID_RE = re.compile(r"packageId=([a-f0-9]+)", re.I)
_PAGING_RE = re.compile(r"共(\d+)条记录\s+(\d+)/(\d+)页")
_ATTACH_ID_RE = re.compile(r"attachId=([a-f0-9]+)", re.I)


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


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _init_browser_context(browser):
    return browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        ignore_https_errors=True,
    )


# ---------------------------------------------------------------------------
# Listing extraction (Playwright)
# ---------------------------------------------------------------------------

def _extract_list_articles(page):
    """Extract article links from a listing page using Playwright.
    Handles two URL patterns:
    1. 建设工程: redirectStep.do?projectId=X&packageId=Y (port 9998)
    2. Other modules: /{module}/{id}.jhtml (port 9999)
    """
    articles = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href]').forEach(a => {
            const rawHref = a.getAttribute('href') || '';
            const text = a.textContent.trim();
            if (text.length < 5) return;

            let uniqueKey, url;

            // Pattern 1: redirectStep.do (建设工程) - port 9998
            if (rawHref.includes('redirectStep')) {
                const pidMatch = rawHref.match(/projectId=([a-f0-9]+)/i);
                const pkidMatch = rawHref.match(/packageId=([a-f0-9]+)/i);
                const pid = pidMatch ? pidMatch[1] : '';
                const pkid = pkidMatch ? pkidMatch[1] : '';
                uniqueKey = pid + '_' + pkid;
                // Always construct with port 9998 (a.href resolves to 9999)
                url = rawHref.startsWith('http') ? rawHref :
                    'https://xzfwzx.pingtan.gov.cn:9998' +
                    (rawHref.startsWith('/') ? '' : '/') + rawHref;
            }
            // Pattern 2: Numeric .jhtml (其他模块) - port 9999
            else if (/\\/\\d+\\.jhtml/.test(rawHref) || /^\\d+\\.jhtml/.test(rawHref)) {
                const idMatch = rawHref.match(/(\\d+)\\.jhtml/);
                uniqueKey = 'jhtml_' + (idMatch ? idMatch[1] : rawHref);
                // Use a.href for resolved URL (page is on port 9999)
                url = a.href;
            } else {
                return; // not an article link
            }

            if (!seen.has(uniqueKey)) {
                seen.add(uniqueKey);
                results.push({
                    uniqueKey: uniqueKey,
                    title: text,
                    url: url,
                    urlType: rawHref.includes('redirectStep') ? 'redirect' : 'jhtml'
                });
            }
        });
        return results;
    }""")
    return articles


def _extract_list_dates(page):
    """Extract dates from listing page to filter for today."""
    dates = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('.list-times span, .article-list2 li span, ' +
            '[class*=\"date\"], [class*=\"time\"]').forEach(el => {
            const text = el.textContent.trim();
            if (/\\d{4}-\\d{2}-\\d{2}/.test(text)) {
                results.push(text.match(/\\d{4}-\\d{2}-\\d{2}/)[0]);
            }
        });
        return results;
    }""")
    return dates


def _get_pagination_info(page):
    """Extract total records, current page, total pages from pagination."""
    page_text = page.evaluate("""() => {
        const el = document.querySelector('.pagesite, .pages');
        return el ? el.textContent.trim() : '';
    }""")
    m = _PAGING_RE.search(page_text)
    if m:
        return {
            "total_records": int(m.group(1)),
            "current_page": int(m.group(2)),
            "total_pages": int(m.group(3)),
        }
    return {"total_records": 0, "current_page": 1, "total_pages": 1}


# ---------------------------------------------------------------------------
# Detail extraction (Playwright)
# ---------------------------------------------------------------------------

def _extract_redirect_detail(page, detail_url, listing_title=""):
    """Extract title, date, content, and attachments from redirectStep.do detail page (port 9998)."""
    result = {
        "title": listing_title,
        "date": "",
        "content_text": "",
        "attachments": [],
    }

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        logging.warning("Failed to load redirect detail %s: %s", detail_url, e)
        return result

    body_text = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    # Title: find the project title before the attachment section
    attachment_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("附件:") or line.startswith("*招标文件"):
            attachment_idx = i
            break

    nav_markers = [
        "首页", "重要通知", "交易信息", "交易指南", "中介超市",
        "预公告", "招标公告", "答疑或补充通知", "开标记录",
        "评标结果公示", "中标结果公示", "中标结果通知书", "合同", "见证书",
        "友情链接", "国家级网站", "省级公共资源网站", "本地行业网站",
        "主办单位", "附件:",
    ]
    search_end = attachment_idx - 1 if attachment_idx > 0 else len(lines) - 1
    for i in range(min(search_end, len(lines) - 1), 0, -1):
        line = lines[i]
        if len(line) > 10 and len(line) < 300:
            if not any(line.startswith(m) for m in nav_markers):
                if not re.match(r'^\d{4}-\d{2}-\d{2}', line):
                    result["title"] = line
                    break

    # Date: look for YYYY-MM-DD
    date_m = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
    if date_m:
        result["date"] = date_m.group(1)

    # Content: try content selectors first, then body text
    content = page.evaluate("""() => {
        const selectors = ['.div-article', 'article', '.content', '.detail',
                          '[class*="article"]', '[class*="content"]'];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent.trim().length > 50) {
                return el.textContent.trim();
            }
        }
        return '';
    }""")
    if content and len(content) > 50:
        result["content_text"] = content
    else:
        cutoff_markers = ["附件:", "友情链接", "主办单位：", "*招标文件"]
        body = body_text
        for marker in cutoff_markers:
            idx = body.find(marker)
            if idx > 200:
                body = body[:idx]
                break
        if len(body) > 50:
            result["content_text"] = body

    # Attachments: /G2/upload!download.do links
    atts = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const text = a.textContent.trim();
            if (href.includes('/G2/upload!download.do') || href.includes('attachId=')) {
                const attachMatch = href.match(/attachId=([a-f0-9]+)/i);
                results.push({
                    name: text || 'attachment',
                    download_url: href.startsWith('http') ? href :
                        'https://xzfwzx.pingtan.gov.cn:9998' + href,
                    attachId: attachMatch ? attachMatch[1] : ''
                });
            }
        });
        return results;
    }""")
    result["attachments"] = atts

    return result


def _extract_jhtml_detail(page, detail_url, listing_title=""):
    """Extract from .jhtml detail page (port 9999, non-建设工程 modules).

    Page structure: sidebar nav + breadcrumb + .content div containing
    title (in [class*="title"]), publish date (发布时间：YYYY-MM-DD HH:MM:SS),
    body text, and optional attachment links.
    """
    result = {
        "title": listing_title,
        "date": "",
        "content_text": "",
        "attachments": [],
    }

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        logging.warning("Failed to load jhtml detail %s: %s", detail_url, e)
        return result

    body_text = page.evaluate("document.body.innerText")

    # Title: [class*="title"] element works reliably
    title_text = page.evaluate("""() => {
        const selectors = ['[class*="title"]', 'h1', 'h2',
                          '.article-title', '.news-title', '.content-title'];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent.trim().length > 5 && el.textContent.trim().length < 300) {
                return el.textContent.trim();
            }
        }
        return '';
    }""")
    if title_text and len(title_text) > 5:
        result["title"] = title_text
    else:
        # Fall back to first substantial non-nav line
        skip_patterns = [
            "首页", "当前位置", "交易信息", "平潭", "设为首页",
            "收藏本站", "无障碍浏览", "进入长者模式", "网站首页",
        ]
        lines = [l.strip() for l in body_text.split("\n") if l.strip()]
        for line in lines:
            if len(line) > 10 and len(line) < 300:
                if not any(line.startswith(p) for p in skip_patterns):
                    if not re.match(r'^\d{4}-\d{2}-\d{2}', line):
                        result["title"] = line
                        break

    # Date: "发布时间：YYYY-MM-DD" pattern preferred
    pub_m = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", body_text)
    if pub_m:
        result["date"] = pub_m.group(1)
    else:
        date_m = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
        if date_m:
            result["date"] = date_m.group(1)

    # Content: use .content div, strip leading breadcrumb and whitespace
    content = page.evaluate("""() => {
        const el = document.querySelector('.content');
        if (el && el.textContent.trim().length > 50) {
            let text = el.textContent.trim();
            // Strip breadcrumb prefix: "首页  >  交易信息"
            const crumbs = ['首页', '>', '交易信息'];
            for (const c of crumbs) {
                if (text.startsWith(c)) {
                    let after = text.slice(c.length);
                    after = after.replace(/^[\\s>\\u00a0]+/, '');
                    text = after;
                }
            }
            // Strip leading blank lines
            text = text.replace(/^[\\s\\n\\r\\u00a0]+/, '');
            return text || el.textContent.trim();
        }
        return '';
    }""")
    if content and len(content) > 50:
        result["content_text"] = content
    else:
        cutoff_markers = [
            "附件:", "友情链接", "主办单位", "网站地图",
            "关于我们", "联系我们", "版权声明",
        ]
        body = body_text
        for marker in cutoff_markers:
            idx = body.find(marker)
            if idx > 300:
                body = body[:idx]
                break
        if len(body) > 50:
            result["content_text"] = body

    # Attachments: download.do links, file extensions, external upload hosts
    atts = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const text = a.textContent.trim();
            if (!href || seen.has(href)) return;

            const isAttachment = (
                href.includes('/G2/upload!download.do') ||
                href.includes('attachId=') ||
                href.includes('download.do') ||
                href.includes('/upload/') ||
                /\\.(pdf|doc|docx|xls|xlsx|rar|zip|7z|gz7)(\\?|$)/i.test(href)
            );
            if (isAttachment) {
                seen.add(href);
                let fullUrl = href;
                if (href.startsWith('/')) {
                    fullUrl = 'https://xzfwzx.pingtan.gov.cn:9999' + href;
                } else if (!href.startsWith('http')) {
                    fullUrl = 'https://xzfwzx.pingtan.gov.cn:9999/' + href;
                } else {
                    fullUrl = href;
                }
                const attachMatch = href.match(/attachId=([a-f0-9]+)/i);
                results.push({
                    name: text || 'attachment',
                    download_url: fullUrl,
                    attachId: attachMatch ? attachMatch[1] : ''
                });
            }
        });
        return results;
    }""")
    result["attachments"] = atts

    return result


def _extract_detail(page, art):
    """Dispatch to correct detail extractor based on URL type."""
    detail_url = art.get("url", "")
    url_type = art.get("urlType", "redirect")
    listing_title = art.get("title", "")

    if url_type == "jhtml":
        return _extract_jhtml_detail(page, detail_url, listing_title)
    else:
        return _extract_redirect_detail(page, detail_url, listing_title)


# ---------------------------------------------------------------------------
# Attachment download (Playwright)
# ---------------------------------------------------------------------------

def _download_attachment(context, att, dest_dir):
    """Download an attachment using Playwright context for proper session handling."""
    url = att.get("download_url", "")
    name = att.get("name", "unknown")

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) if name else "attachment"
    # Ensure extension
    if not re.search(r'\.\w{2,5}$', safe_name):
        ext_match = re.search(r'\.(\w{2,5})$', name)
        if not ext_match:
            safe_name += ".bin"

    dest_path = os.path.join(dest_dir, safe_name)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    try:
        _request_delay()

        # Use requests with Playwright cookies (avoids download dialog issues)
        cookies = context.cookies()
        import requests
        sess = requests.Session()
        for c in cookies:
            sess.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )

        # Use the attachment URL's origin as referer
        ref_base = "/".join(url.split("/")[:3])  # https://host:port
        sess.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": ref_base + "/",
        })

        resp = sess.get(url, timeout=120, stream=True, verify=False)
        if resp.status_code == 200 and len(resp.content) > 100:
            # Get filename from Content-Disposition
            cd = resp.headers.get("Content-Disposition", "")
            fn_match = re.search(
                r'filename\*?[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I
            )
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
        logging.warning("Download error for %s: %s", name, e)

    return None


# ---------------------------------------------------------------------------
# File extraction
# ---------------------------------------------------------------------------

def _extract_gz7(filepath):
    """Extract GZ7 compressed files (Epoint proprietary format, zip-based)."""
    dest_dir = os.path.dirname(filepath)
    extracted = []
    try:
        # GZ7 is a ZIP-based format
        with zipfile.ZipFile(filepath, "r") as zf:
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
                _safe_print("      Extracted from GZ7: {}".format(safe_name))
        os.remove(filepath)
    except Exception as e:
        logging.warning("GZ7 extract error for %s: %s",
                        os.path.basename(filepath), e)
    return extracted


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
    return {"processed_ids": [], "completed_modules": [],
            "module_progress": {}}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("State saved (%d IDs, %d modules done)",
                 len(state.get("processed_ids", [])),
                 len(state.get("completed_modules", [])))


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
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=did)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing for %s: %s", did, e)


# ---------------------------------------------------------------------------
# Module-level processing
# ---------------------------------------------------------------------------

def _process_module(context, output_dir, kb_id, tenant_id,
                    module_info, processed_ids, state, today):
    module_key = module_info["key"]
    module_name = module_info["name"]
    list_base = urljoin(_SITE_ROOT, module_info["list_url"])
    # Remove trailing 'index.jhtml' to get base path
    list_dir = list_base.rsplit("/", 1)[0]
    list_url = list_dir + "/index.jhtml"
    page_base = list_dir + "/index"

    if module_key in state.get("completed_modules", []):
        _safe_print("[{}]   Already completed, skipping.".format(module_name))
        sys.stdout.flush()
        return 0

    progress = state.get("module_progress", {}).get(
        module_key, {"page": 1, "done": False}
    )

    _safe_print("[{}]   Listing URL: {}".format(module_name, list_url))
    sys.stdout.flush()

    page = context.new_page()
    BATCH_SIZE = 5  # Smaller batches due to Playwright overhead
    total_processed = 0
    downloads_dir = os.path.join(output_dir, "downloads")

    try:
        # Load first page to get pagination info
        page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        paging = _get_pagination_info(page)
        total_pages = paging["total_pages"]
        _safe_print("[{}]   Total pages: {} (resuming from page {})".format(
            module_name, total_pages, progress["page"]))
        sys.stdout.flush()

        if total_pages == 0:
            state.setdefault("completed_modules", []).append(module_key)
            _save_state(output_dir, state)
            return 0

        stop_early = False

        for page_num in range(progress["page"], total_pages + 1):
            if page_num > 1:
                page_url = "{}_{}.jhtml".format(page_base, page_num)
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    logging.warning("Failed to load page %d: %s", page_num, e)
                    break

            articles = _extract_list_articles(page)

            # Filter: only today's articles AND not already processed
            new_articles = []
            seen_today = False
            for a in articles:
                if a["uniqueKey"] in processed_ids:
                    continue
                new_articles.append(a)

            if len(new_articles) == 0:
                _safe_print("[{}]   Page {}: all items already processed".format(
                    module_name, page_num))
                # Continue checking - there might be unprocessed articles on later pages
                if page_num - progress["page"] > 20:
                    # If we've checked 20 pages with nothing new, stop
                    break
                continue

            _safe_print("[{}]   Page {}/{}: {} items ({} total)".format(
                module_name, page_num, total_pages,
                len(articles), len(new_articles)))
            sys.stdout.flush()

            for a in new_articles:
                a["section"] = module_name

            # Process in batches
            for batch_start in range(0, len(new_articles), BATCH_SIZE):
                batch = new_articles[batch_start:batch_start + BATCH_SIZE]
                batch_num = (batch_start // BATCH_SIZE) + 1
                md_parts = []
                batch_ids = []

                for idx, art in enumerate(batch, 1):
                    global_idx = batch_start + idx
                    _safe_print("[{}]   [{}/{}] {}".format(
                        module_name, global_idx, len(new_articles),
                        art["title"][:60]))
                    sys.stdout.flush()

                    # Extract detail using the same page
                    detail = _extract_detail(page, art)

                    # Download attachments
                    attachment_texts = []
                    for att in detail.get("attachments", []):
                        att_name = att.get("name", "unknown")
                        dest_dir = os.path.join(
                            downloads_dir, module_key, art["uniqueKey"])
                        fp = _download_attachment(context, att, dest_dir)
                        if fp:
                            is_zip = (
                                fp.lower().endswith((".zip", ".rar", ".gz7")) or
                                (os.path.getsize(fp) >= 4 and
                                 open(fp, "rb").read(4) == b"PK\x03\x04")
                            )
                            if fp.lower().endswith(".gz7"):
                                extracted = _extract_gz7(fp)
                            elif is_zip:
                                extracted = _extract_zip(fp)
                            else:
                                extracted = []

                            if extracted:
                                for ext_fp in extracted:
                                    text = _extract_file_text(ext_fp)
                                    attachment_texts.append(
                                        (os.path.basename(ext_fp), text))
                            else:
                                text = _extract_file_text(fp)
                                attachment_texts.append((att_name, text))

                    md = _build_markdown(art, detail, attachment_texts)

                    articles_dir = os.path.join(
                        output_dir, "articles", module_key)
                    os.makedirs(articles_dir, exist_ok=True)
                    md_path = os.path.join(
                        articles_dir, "{}.md".format(art["uniqueKey"]))
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(md)

                    md_parts.append(md)
                    batch_ids.append(art["uniqueKey"])

                    _request_delay()

                # Checkpoint after each batch
                if md_parts:
                    batch_path = os.path.join(
                        output_dir, "{}_{:03d}_{:03d}.md".format(
                            module_key, page_num, batch_num))
                    with open(batch_path, "w", encoding="utf-8") as f:
                        f.write("\n\n---\n\n".join(md_parts))

                    processed_ids.update(batch_ids)
                    state["processed_ids"] = list(processed_ids)
                    state.setdefault("module_progress", {})[module_key] = {
                        "page": page_num, "done": False}
                    _save_state(output_dir, state)

                    if kb_id:
                        try:
                            _upload_to_kb(batch_path, kb_id, tenant_id)
                        except Exception as e:
                            _safe_print("[{}]   batch upload failed: {}".format(
                                module_name, e))
                            logging.error("Upload failed: %s", e)

                    total_processed += len(md_parts)
                    _safe_print("[{}]   p{}b{} uploaded ({} total)".format(
                        module_name, page_num, batch_num, total_processed))
                    sys.stdout.flush()

            # Update page progress
            state.setdefault("module_progress", {})[module_key] = {
                "page": page_num + 1, "done": False}
            _save_state(output_dir, state)

        # Module complete
        state.setdefault("completed_modules", []).append(module_key)
        state.setdefault("module_progress", {})[module_key] = {
            "page": total_pages, "done": True}
        _save_state(output_dir, state)

    finally:
        page.close()

    _safe_print("[{}]   Done: {} processed\n".format(
        module_name, total_processed))
    sys.stdout.flush()
    return total_processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="pingtan fjsz crawler - 平潭综合实验区公共资源统一平台"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://xzfwzx.pingtan.gov.cn:9999/fjsz/index.jhtml")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--section", default=None,
                   help="Comma-separated module keys (e.g. fjsz,yllh,zfcg)")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds (default: 3300s = 55min)")
    for opt in ("--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[PINGTAN] 平潭综合实验区公共资源统一平台 crawler")
    _safe_print("[PINGTAN] KB: {}".format(args.kb_id))
    _safe_print("[PINGTAN] Max runtime: {}s".format(args.max_runtime))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== PINGTAN crawler started ===")

    today = _today_str()
    _safe_print("[PINGTAN] Today: {}".format(today))
    sys.stdout.flush()

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[PINGTAN] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed_modules": [], "module_progress": {}
    }
    processed_ids = set(state.get("processed_ids", []))
    completed_modules = set(state.get("completed_modules", []))
    _safe_print("[PINGTAN] Previously processed: {}, completed modules: {}\n".format(
        len(processed_ids), len(completed_modules)))
    sys.stdout.flush()

    if args.section:
        selected = set(args.section.split(","))
        active_modules = [m for m in _MODULES if m["key"] in selected]
    else:
        active_modules = [m for m in _MODULES if m["key"] not in completed_modules]

    _safe_print("[PINGTAN] Active modules: {}".format(
        ", ".join(m["name"] for m in active_modules)))
    sys.stdout.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = _init_browser_context(browser)
        _safe_print("[PINGTAN] Browser initialized\n")
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
                        "\n[PINGTAN] Runtime {:.0f}s, remaining {:.0f}s < "
                        "grace {:.0f}s, stopping early.".format(
                            elapsed, remaining, grace))
                    sys.stdout.flush()
                    stopped_early = True
                    break

                n = _process_module(context, output_dir, args.kb_id,
                                    args.tenant_id, mod_info,
                                    processed_ids, state, today)
                total_processed += n

            _safe_print("\n" + "=" * 60)
            if stopped_early:
                _safe_print("[PINGTAN] Partial run: {} articles. "
                            "Will resume from checkpoint.".format(total_processed))
            else:
                _safe_print("[PINGTAN] Done: {} articles processed.".format(
                    total_processed))
            _safe_print("=" * 60 + "\n")
            sys.stdout.flush()
            logging.info("=== PINGTAN crawler finished: %d articles ===",
                         total_processed)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    CONSUMER_NAME = "pingtan_fjsz_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
