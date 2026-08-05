#!/usr/bin/env python3
"""
平潭综合实验区公共资源统一平台-交易信息 智能采集爬虫（custom_runner）

站点: https://xzfwzx.pingtan.gov.cn:9999/zbgg/index.jhtml
站点ID: pingtan_ggzy | 类别(category): bid
栏目(section_name):  平潭综合实验区-交易信息-预公告
                    平潭综合实验区-交易信息-招标公告
                    平潭综合实验区-交易信息-评标结果公示
                    平潭综合实验区-交易信息-中标结果公示

覆盖 4 个业务类型（businessCatalog=ALL，仅 businessType 不同）:
  YGG    预公告          /queryContent.jspx?...&businessType=YGG&...
  ZBGG   招标公告        /queryContent.jspx?...&businessType=ZBGG&...
  PBJGGS 评标结果公示    /queryContent.jspx?...&businessType=PBJGGS&...
  ZBJGGS 中标结果公示    /queryContent.jspx?...&businessType=ZBJGGS&...

站点特征（实测 2026-08-04）:
  - 列表页 9999 端口 SSR HTML，无加密、无 SPA（curl 即可）
    容器: ul.article-list2 > li > a[href][title] + div.list-times > YYYY-MM-DD
  - 详情页两类 URL（均在 9998 端口 GBMP 系统）:
    1. YGG:    /G2/gbmp/progress/pre-notice!redirectStep.do?preNoticeId=X
    2. 其他:   /G2/gbmp/progress/js-tender-notice!redirectStep.do?projectId=X&packageId=Y
    正文嵌入 HTML，附件是 attachTenderFile_XXX 占位 div，需 Playwright 渲染
  - 附件下载: https://xzfwzx.pingtan.gov.cn:9998/G2/upload!download.do?attachId=X
  - 反爬弱: UA + Referer + verify_ssl=false 即可（老脚本 pingtan_fjsz_crawler.py 经验）

采集范围: 仅抓每个栏目列表第 1 页（用户口径）
date_filter:
  - "" / "today"   → 只保当天发布（后续增量）
  - "YYYY-MM-DD"   → 指定日期
  - 首次回溯（full_crawl=True）→ 全量
跨次去重: crawler_result.id = md5(site_id|source_url) 主键 upsert

数据落库:
  - crawler_result (category=bid, section_name=4 类标签)
  - extracted_json 收录 project_id/package_id/business_type/tenderer/agency
  - KB: 正文 md + 附件原件 + ZIP/GZ7 解压成员

用法:
  # custom_runner（unified_crawler.py 调用）
  python /ragflow/rag/svr/unified_crawler.py \\
    --tenant-id <TID> --kb-id 3b4f619c85c211f198269135a1db216c \\
    --task-name <NAME> --writer collection --category bid \\
    --date-filter today \\
    --script-args '{"site_id":"pingtan_ggzy"}'

  # 直接 CLI 测试
  python pingtan_ggzy_collection_crawler.py \\
    --tenant-id <TID> --kb-id 3b4f619c85c211f198269135a1db216c \\
    --task-name test --date-filter today
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
import zipfile
from email.header import decode_header as _mime_decode_header
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse, parse_qs

import requests as _requests
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE = "https://xzfwzx.pingtan.gov.cn"
_LIST_HOST = f"{_BASE}:9999"
_DETAIL_HOST = f"{_BASE}:9998"
_SITE_NAME = "平潭综合实验区公共资源统一平台-交易信息"
_SITE_ID = "pingtan_ggzy"
_SITE_DOMAIN = "xzfwzx.pingtan.gov.cn"

# 用户指定 KB
_DEFAULT_KB_ID = "3b4f619c85c211f198269135a1db216c"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_LIST_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{_LIST_HOST}/zbgg/index.jhtml",
}

# 节流
_PAGE_DELAY = (1.5, 3.0)       # 栏目间
_ARTICLE_DELAY = (1.0, 2.0)    # 详情之间
_ATTACHMENT_DELAY = (0.8, 1.6) # 附件之间

_MAX_RUNTIME_DEFAULT = 3300  # 55 min，给 subprocess timeout 60min 留缓冲
_ZIP_MAX_MEMBERS = 80
_ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 200MB

# 列表前缀（去除标题里的栏目标签）
_PREFIX_RE = re.compile(r"^(预公告|招标公告|评标结果公示|中标结果公示)\s*")

# 日期
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

# 详情 URL 类型识别
_PRE_NOTICE_RE = re.compile(r"pre-notice!redirectStep\.do\?preNoticeId=([a-f0-9]+)", re.I)
_PROJECT_ID_RE = re.compile(r"projectId=([a-f0-9]+)", re.I)
_PACKAGE_ID_RE = re.compile(r"packageId=([a-f0-9]+)", re.I)
_PRE_NOTICE_ID_RE = re.compile(r"preNoticeId=([a-f0-9]+)", re.I)
_ATTACH_ID_RE = re.compile(r"attachId=([a-f0-9]+)", re.I)

# 招标人 / 代理 启发式
_TENDERER_RE = re.compile(r"招标人为\s*([^，,。；;（(]{2,60}?)[，,。；;（(]")
_AGENCY_RE = re.compile(r"招标代理[^，,。；;（(]{0,30}?为\s*([^，,。；;（(]{2,60}?)[，,。；;（(]")
# 工程类别（从 URL / 详情正则提取，弱启发式）
# 详情页正文截断标记（去除页脚噪声）
_FOOTER_MARKERS = ["友情链接", "主办单位", "备案", "技术支持", "网站地图"]

# 4 个栏目：label / 中文名 / businessType / 列表 URL
_SECTIONS: List[Dict[str, str]] = [
    {
        "label": "ygg",
        "name": "平潭综合实验区-交易信息-预公告",
        "businessType": "YGG",
        "list_url": f"{_LIST_HOST}/queryContent.jspx?title=&businessCatalog=ALL&businessType=YGG&tenderType=ALL&ext=&origin=",
    },
    {
        "label": "zbgg",
        "name": "平潭综合实验区-交易信息-招标公告",
        "businessType": "ZBGG",
        "list_url": f"{_LIST_HOST}/queryContent.jspx?title=&businessCatalog=ALL&businessType=ZBGG&tenderType=ALL&ext=&origin=",
    },
    {
        "label": "pbjggs",
        "name": "平潭综合实验区-交易信息-评标结果公示",
        "businessType": "PBJGGS",
        "list_url": f"{_LIST_HOST}/queryContent.jspx?title=&businessCatalog=ALL&businessType=PBJGGS&tenderType=ALL&ext=&origin=",
    },
    {
        "label": "zbjggs",
        "name": "平潭综合实验区-交易信息-中标结果公示",
        "businessType": "ZBJGGS",
        "list_url": f"{_LIST_HOST}/queryContent.jspx?title=&businessCatalog=ALL&businessType=ZBJGGS&tenderType=ALL&ext=&origin=",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay(bounds):
    time.sleep(random.uniform(*bounds))


def _sanitize_filename(text, max_len=150) -> str:
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r"\s+", " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


def _normalize_date_filter(val: str) -> str:
    if not val:
        return ""
    val = str(val).strip().lower()
    if val == "today":
        return datetime.date.today().isoformat()
    try:
        return datetime.datetime.strptime(val.replace("/", "-"), "%Y-%m-%d").date().isoformat()
    except ValueError:
        logging.warning("Invalid date_filter=%r, ignored", val)
        return ""


def _normalize_date_str(val: str) -> str:
    if not val:
        return ""
    m = _DATE_RE.search(str(val))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(val).strip()


# ---------------------------------------------------------------------------
# HTTP session (列表页)
# ---------------------------------------------------------------------------
def _init_session() -> _requests.Session:
    sess = _requests.Session()
    sess.headers.update(_LIST_HEADERS)
    sess.verify = False
    # 会话预热
    for warm_url in (f"{_LIST_HOST}/", f"{_LIST_HOST}/zbgg/index.jhtml"):
        try:
            sess.get(warm_url, timeout=30, verify=False)
        except Exception as e:
            logging.warning("Session warmup %s: %s", warm_url, e)
    time.sleep(1)
    return sess


def _fetch_page(sess: _requests.Session, url: str, max_retries: int = 3) -> str:
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, timeout=60, verify=False)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp.text
            logging.warning("HTTP %d for %s (attempt %d)", resp.status_code, url, attempt)
            if resp.status_code in (403, 502, 503):
                time.sleep(3 * attempt * random.uniform(1.0, 2.0))
            elif attempt < max_retries:
                time.sleep(2 * attempt)
        except Exception as e:
            logging.warning("Fetch %s failed (attempt %d): %s", url, attempt, e)
            if attempt < max_retries:
                time.sleep(2 * attempt)
    return ""


# ---------------------------------------------------------------------------
# List parsing (BS4)
# ---------------------------------------------------------------------------
def _parse_list_items(html: str, page_url: str, section: Dict) -> List[Dict]:
    """ul.article-list2 > li > a[href][title] + div.list-times

    Returns: [{id, title, url, date, section_label, section_name, url_type, business_type}]
    url_type: 'pre_notice' | 'tender_notice' | 'jhtml'
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []
    seen_urls: set = set()

    ul = soup.select_one("ul.article-list2") or soup
    for li in ul.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = (a.get("href") or "").strip()
        title = (a.get("title") or "").strip() or a.get_text(strip=True)
        if not href or not title:
            continue
        if href.startswith("javascript"):
            continue

        # 日期
        date_str = ""
        dt = li.select_one("div.list-times")
        if dt:
            date_str = _normalize_date_str(dt.get_text(strip=True))

        # URL 绝对化（href 本身是 9998 完整 URL 或 9999 相对路径）
        abs_url = urljoin(page_url, href)

        # 识别 URL 类型（GBMP 多种 notice 端点结构相似，统一走 tender_notice 提取器）
        url_type = ""
        if _PRE_NOTICE_RE.search(abs_url):
            url_type = "pre_notice"
        elif "redirectStep.do" in abs_url and (
            "tender-notice" in abs_url
            or "award-notice" in abs_url        # PBJGGS 评标结果公示
            or "winning-notice" in abs_url      # ZBJGGS 中标结果公示
        ):
            url_type = "tender_notice"
        elif abs_url.endswith(".jhtml"):
            url_type = "jhtml"
        else:
            # 既不是 GBMP 也不是 jhtml — 跳过
            continue

        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        # 去除标题里的栏目前缀（如 "预公告xxxx"）
        clean_title = _PREFIX_RE.sub("", title).strip() or title

        # item_id: 用 URL 的 stable 部分
        if url_type == "pre_notice":
            m = _PRE_NOTICE_ID_RE.search(abs_url)
            item_id = f"pre_{m.group(1)}" if m else hashlib.md5(abs_url.encode()).hexdigest()
        elif url_type == "tender_notice":
            mp = _PROJECT_ID_RE.search(abs_url)
            mk = _PACKAGE_ID_RE.search(abs_url)
            item_id = (f"t_{mp.group(1)}_{mk.group(1)}"
                       if mp and mk else hashlib.md5(abs_url.encode()).hexdigest())
        else:
            item_id = hashlib.md5(abs_url.encode()).hexdigest()

        items.append({
            "id": item_id,
            "title": clean_title,
            "url": abs_url,
            "date": date_str,
            "section_label": section["label"],
            "section_name": section["name"],
            "business_type": section["businessType"],
            "url_type": url_type,
            "category": "bid",
        })
    return items


def _fetch_section_items(sess: _requests.Session, section: Dict, date_filter: str) -> List[Dict]:
    list_url = section["list_url"]
    html = _fetch_page(sess, list_url)
    if not html:
        logging.error("[%s] list fetch failed: %s", section["name"], list_url)
        return []

    page_items = _parse_list_items(html, list_url, section)
    collected: List[Dict] = []
    for it in page_items:
        if date_filter:
            if not it["date"] or it["date"] != date_filter:
                continue
        collected.append(it)

    logging.info("[%s] %d items (filter=%s)",
                 section["name"], len(collected), date_filter or "none")
    return collected


# ---------------------------------------------------------------------------
# Playwright detail extraction
# ---------------------------------------------------------------------------
def _init_browser():
    """启动 Playwright chromium，返回 (playwright, browser, context, page)。"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            executable_path="/opt/chrome/chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
    except Exception:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
    context = browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        ignore_https_errors=True,
    )
    page = context.new_page()
    return pw, browser, context, page


def _extract_pre_notice_detail(page, detail_url: str, listing_title: str = "") -> Dict:
    """YGG 预公告详情页提取（pre-notice!redirectStep.do?preNoticeId=X）。

    结构与 tender-notice 类似：正文嵌入 HTML，附件为 /G2/upload!download.do 链接。
    """
    result = {
        "title": listing_title, "date": "", "content_text": "",
        "attachments": [], "tenderer": "", "agency": "",
        "project_id": "", "package_id": "", "pre_notice_id": "",
    }
    m = _PRE_NOTICE_ID_RE.search(detail_url)
    if m:
        result["pre_notice_id"] = m.group(1)

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as e:
        logging.warning("Failed to load pre-notice detail %s: %s", detail_url, e)
        return result

    body_text = page.evaluate("document.body.innerText") or ""

    # 标题：尝试常见选择器，兜底用列表标题
    title = page.evaluate("""() => {
        const sels = ['h1', 'h2', '.article-title', '.news-title',
                      '.detail-title', '[class*="title"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
                const t = el.textContent.trim();
                if (t.length > 4 && t.length < 300) return t;
            }
        }
        return '';
    }""")
    if title:
        result["title"] = title

    # 日期
    dm = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
    if dm:
        result["date"] = dm.group(1)

    # 正文：优先 .div-article / article / .content
    content = page.evaluate("""() => {
        const sels = ['.div-article', 'article', '.content', '.detail',
                      '[class*="article"]', '[class*="content"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el && el.textContent.trim().length > 50) {
                return el.textContent.trim();
            }
        }
        return '';
    }""")
    if content and len(content) > 50:
        result["content_text"] = content
    else:
        body = body_text
        for marker in ["附件:", "友情链接", "主办单位"]:
            idx = body.find(marker)
            if idx > 200:
                body = body[:idx]
                break
        if len(body) > 50:
            result["content_text"] = body

    # 招标人 / 代理
    tm = _TENDERER_RE.search(body_text)
    if tm:
        result["tenderer"] = tm.group(1).strip()
    am = _AGENCY_RE.search(body_text)
    if am:
        result["agency"] = am.group(1).strip()

    # 附件
    result["attachments"] = _extract_attachments_from_page(page)
    return result


def _extract_tender_notice_detail(page, detail_url: str, listing_title: str = "") -> Dict:
    """ZBGG/PBJGGS/ZBJGGS 详情页提取（js-tender-notice!redirectStep.do?projectId=X&packageId=Y）。

    正文嵌入 HTML，附件是 attachTenderFile_XXX 占位 div，需要等 JS 渲染后
    出现的 <a href> 含 /G2/upload!download.do?attachId=X 链接。
    """
    result = {
        "title": listing_title, "date": "", "content_text": "",
        "attachments": [], "tenderer": "", "agency": "",
        "project_id": "", "package_id": "", "pre_notice_id": "",
    }
    mp = _PROJECT_ID_RE.search(detail_url)
    mk = _PACKAGE_ID_RE.search(detail_url)
    if mp:
        result["project_id"] = mp.group(1)
    if mk:
        result["package_id"] = mk.group(1)

    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as e:
        logging.warning("Failed to load tender-notice detail %s: %s", detail_url, e)
        return result

    body_text = page.evaluate("document.body.innerText") or ""

    # 标题：尝试多个选择器
    title = page.evaluate("""() => {
        const sels = ['h1', 'h2', '.article-title', '.news-title',
                      '.detail-title', '.project-name', '#projectName',
                      '[class*="title"]', '[class*="name"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
                const t = el.textContent.trim();
                if (t.length > 4 && t.length < 300) return t;
            }
        }
        return '';
    }""")
    if title:
        result["title"] = title

    # 日期
    dm = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
    if dm:
        result["date"] = dm.group(1)

    # 正文
    content = page.evaluate("""() => {
        const sels = ['.div-article', 'article', '.content', '.detail',
                      '#contentDiv', '.notice-content', '.article-content',
                      '[class*="article"]', '[class*="content"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el && el.textContent.trim().length > 50) {
                return el.textContent.trim();
            }
        }
        return '';
    }""")
    if content and len(content) > 50:
        result["content_text"] = content
    else:
        body = body_text
        for marker in ["附件:", "友情链接", "主办单位"]:
            idx = body.find(marker)
            if idx > 200:
                body = body[:idx]
                break
        if len(body) > 50:
            result["content_text"] = body

    # 招标人 / 代理（公告正文里常见）
    tm = _TENDERER_RE.search(body_text)
    if tm:
        result["tenderer"] = tm.group(1).strip()
    am = _AGENCY_RE.search(body_text)
    if am:
        result["agency"] = am.group(1).strip()

    # 附件
    result["attachments"] = _extract_attachments_from_page(page)
    return result


def _extract_attachments_from_page(page) -> List[Dict]:
    """扫描页面所有 <a href>，挑出 GBMP 下载链接。

    GBMP 链接形如：
      /G2/upload!download.do?attachId=2c90b3996812d8210168181fbc360422
    返回标准化的 file_name + file_url。
    """
    atts = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').trim();
            if (href.indexOf('/G2/upload!download.do') === -1
                && href.indexOf('attachId=') === -1) return;
            const m = href.match(/attachId=([a-f0-9]+)/i);
            if (!m) return;
            const attachId = m[1];
            if (seen.has(attachId)) return;
            seen.add(attachId);
            let abs = href;
            if (href.indexOf('http') !== 0) {
                abs = 'https://xzfwzx.pingtan.gov.cn:9998' +
                      (href.charAt(0) === '/' ? '' : '/') + href;
            }
            results.push({
                file_name: text || ('attachment_' + attachId.substring(0, 8)),
                file_url: abs,
                attach_id: attachId,
                source: 'gbmp'
            });
        });
        return results;
    }""")
    return atts or []


def _extract_jhtml_detail(page, detail_url: str, listing_title: str = "") -> Dict:
    """少数 .jhtml 详情页（CMS 标准文章页）。"""
    result = {
        "title": listing_title, "date": "", "content_text": "",
        "attachments": [], "tenderer": "", "agency": "",
        "project_id": "", "package_id": "", "pre_notice_id": "",
    }
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as e:
        logging.warning("Failed to load jhtml detail %s: %s", detail_url, e)
        return result

    body_text = page.evaluate("document.body.innerText") or ""

    title = page.evaluate("""() => {
        const sels = ['h1', 'h2', '.article-title', '[class*="title"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
                const t = el.textContent.trim();
                if (t.length > 4 && t.length < 300) return t;
            }
        }
        return '';
    }""")
    if title:
        result["title"] = title

    pm = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", body_text)
    if pm:
        result["date"] = pm.group(1)
    else:
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", body_text)
        if dm:
            result["date"] = dm.group(1)

    content = page.evaluate("""() => {
        const sels = ['.content', '.article-content', '.div-article',
                      'article', '[class*="content"]'];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el && el.textContent.trim().length > 50) {
                return el.textContent.trim();
            }
        }
        return '';
    }""")
    if content and len(content) > 50:
        result["content_text"] = content
    elif len(body_text) > 50:
        body = body_text
        for marker in ["附件:", "友情链接", "主办单位", "网站地图"]:
            idx = body.find(marker)
            if idx > 200:
                body = body[:idx]
                break
        result["content_text"] = body

    # 附件：jhtml 页面通常是直链
    atts = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').trim();
            if (!href || href.startsWith('javascript')) return;
            if (!/\\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|gz|gz7|wps|et|dps)(\\?|$)/i.test(href)) return;
            const abs = href.startsWith('http') ? href :
                new URL(href, document.baseURI).href;
            if (seen.has(abs)) return;
            seen.add(abs);
            results.push({
                file_name: text || abs.split('/').pop(),
                file_url: abs,
                attach_id: '',
                source: 'direct'
            });
        });
        return results;
    }""")
    result["attachments"] = atts or []
    return result


def _extract_detail(page, item: Dict) -> Dict:
    """根据 url_type 派发到对应详情提取器。"""
    detail_url = item["url"]
    listing_title = item.get("title", "")
    url_type = item.get("url_type", "")
    if url_type == "pre_notice":
        return _extract_pre_notice_detail(page, detail_url, listing_title)
    if url_type == "tender_notice":
        return _extract_tender_notice_detail(page, detail_url, listing_title)
    return _extract_jhtml_detail(page, detail_url, listing_title)


# ---------------------------------------------------------------------------
# Attachment download & ZIP/GZ7 extraction
# ---------------------------------------------------------------------------
def _parse_cd_filename(cd: str) -> str:
    """从 Content-Disposition 头解析文件名，规避中文 mojibake。

    支持三种格式：
      1. RFC 5987: filename*=UTF-8''%E4%B8%AD%E6%A0%87...
      2. MIME encoded-word: filename="=?utf-8?B?5Lit5paH...?="
      3. 直插 UTF-8 字节（requests 可能 latin-1 化）: filename="中标....pdf"
    返回空串表示解析失败。
    """
    if not cd:
        return ""

    # 1) RFC 5987 ext-value（最高优先级，标准用法）
    m = re.search(r'filename\*\s*=\s*([^;]+)', cd, re.I)
    if m:
        raw = m.group(1).strip().strip('"')
        # 形如 UTF-8''%xx 或 utf-8'lang'%xx
        parts = raw.split("'", 2)
        if len(parts) == 3:
            charset, _lang, value = parts
        else:
            charset, value = "utf-8", parts[-1]
        try:
            decoded = unquote(value, encoding=charset or "utf-8", errors="strict")
            if decoded:
                return decoded
        except (LookupError, UnicodeDecodeError):
            pass

    # 2) 普通 filename="..." / filename=...
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        cand = m.group(1).strip()
        # 2a) MIME encoded-word
        if "=?utf-8?" in cand.lower() or "=?gbk?" in cand.lower():
            try:
                parts = _mime_decode_header(cand)
                cand = "".join(
                    (s.decode(enc or "utf-8") if isinstance(s, bytes) else s)
                    for s, enc in parts
                )
                if cand:
                    return cand
            except Exception:
                pass
        # 2b) requests 用 latin-1 还原了 UTF-8 字节 — 修回
        try:
            fixed = cand.encode("latin-1").decode("utf-8")
            if fixed and fixed != cand:
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        # 2c) 已经是正常 Unicode 字符串
        if cand:
            return cand

    return ""


def _download_attachment(sess: _requests.Session, att: Dict, dest_dir: str,
                          context_cookies: Optional[List[Dict]] = None) -> Optional[str]:
    """下载附件，复用 playwright context cookies 兜底 GBMP 鉴权。

    sess: 主 session（用于直链）
    context_cookies: playwright context.cookies() 返回值（用于 GBMP 链接）
    """
    url = att.get("file_url", "")
    name = att.get("file_name") or "attachment"
    if not url:
        return None

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(name, 120)
    if not re.search(r"\.\w{2,5}$", safe_name):
        url_name = os.path.basename(urlparse(url).path)
        if re.search(r"\.\w{2,5}$", url_name):
            safe_name = _sanitize_filename(url_name, 120)
        else:
            # 附件源是 GBMP download API，按 attachId + 推断扩展名兜底
            ext = ".bin"
            cd_hint = (att.get("content_disposition") or "").lower()
            for hint_ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar"]:
                if hint_ext in cd_hint:
                    ext = hint_ext
                    break
            safe_name += ext

    dest_path = os.path.join(dest_dir, safe_name)
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 100:
        return dest_path

    # 选 session：GBMP 链接用注入 cookies 的临时 session
    use_sess = sess
    if context_cookies and "upload!download.do" in url:
        dl_sess = _requests.Session()
        for c in context_cookies:
            try:
                dl_sess.cookies.set(c["name"], c["value"],
                                    domain=c.get("domain", "") or _SITE_DOMAIN,
                                    path=c.get("path", "/"))
            except Exception:
                pass
        dl_sess.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{_DETAIL_HOST}/",
        })
        dl_sess.verify = False
        use_sess = dl_sess

    try:
        _request_delay(_ATTACHMENT_DELAY)
        resp = use_sess.get(url, timeout=180, verify=False, stream=True)
        if resp.status_code != 200:
            logging.warning("Attachment HTTP %s: %s", resp.status_code, url)
            return None
        cd = resp.headers.get("Content-Disposition", "")
        cand = _parse_cd_filename(cd)
        if cand:
            safe_name = _sanitize_filename(cand, 120)
            dest_path = os.path.join(dest_dir, safe_name)

        content = resp.content
        if len(content) <= 100:
            logging.warning("Attachment too small: %s", url)
            return None
        head = content[:64].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            logging.warning("Attachment URL returned HTML (login page?), skipped: %s", url)
            return None

        with open(dest_path, "wb") as f:
            f.write(content)
        logging.info("Downloaded: %s (%d bytes)", safe_name, len(content))
        return dest_path
    except Exception as e:
        logging.error("Download error %s: %s", url, e)
        return None


def _extract_zip(zip_path: str) -> List[str]:
    """解压 ZIP（含容量/成员数/路径穿越防护）；加密包跳过。"""
    extracted: List[str] = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [n for n in zf.namelist()
                       if not n.startswith("__MACOSX")
                       and not os.path.basename(n).startswith(".")
                       and not n.endswith("/")]
            if len(members) > _ZIP_MAX_MEMBERS:
                logging.warning("ZIP %d members > %d, skipped: %s",
                                len(members), _ZIP_MAX_MEMBERS, zip_path)
                return []
            total = sum(i.file_size for i in zf.infolist())
            if total > _ZIP_MAX_TOTAL_BYTES:
                logging.warning("ZIP uncompressed %d > limit, skipped: %s",
                                total, zip_path)
                return []
            for name in members:
                safe_name = _sanitize_filename(os.path.basename(name), 120)
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                try:
                    with open(dest_path, "wb") as f:
                        f.write(zf.read(name))
                    extracted.append(dest_path)
                    logging.info("  Extracted: %s", safe_name)
                except RuntimeError as e:
                    logging.warning("  ZIP member skipped (encrypted?): %s — %s", name, e)
    except Exception as e:
        logging.warning("ZIP extract error for %s: %s", os.path.basename(zip_path), e)
    return extracted


def _extract_gz7(filepath: str) -> List[str]:
    """GZ7 (Epoint 专有 zip-based 格式) 解压。"""
    extracted: List[str] = []
    dest_dir = os.path.dirname(filepath)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            members = [n for n in zf.namelist()
                       if not n.startswith("__MACOSX")
                       and not os.path.basename(n).startswith(".")
                       and not n.endswith("/")]
            if len(members) > _ZIP_MAX_MEMBERS:
                return []
            for name in members:
                safe_name = _sanitize_filename(os.path.basename(name), 120)
                if not safe_name:
                    continue
                dest_path = os.path.join(dest_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                try:
                    with open(dest_path, "wb") as f:
                        f.write(zf.read(name))
                    extracted.append(dest_path)
                    logging.info("  Extracted from GZ7: %s", safe_name)
                except RuntimeError as e:
                    logging.warning("  GZ7 member skipped: %s — %s", name, e)
    except Exception as e:
        logging.warning("GZ7 extract error for %s: %s", os.path.basename(filepath), e)
    return extracted


def _extract_file_text(filepath: str) -> str:
    """尽力提取文件文本（pdf/docx/xlsx/txt），失败返回空串。"""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        if ext == ".pdf":
            try:
                import fitz
                with fitz.open(filepath) as doc:
                    return "\n\n".join(p.get_text() for p in doc if p.get_text().strip())
            except ImportError:
                pass
            try:
                import pdfplumber
                with pdfplumber.open(filepath) as pdf:
                    return "\n\n".join(p.extract_text() for p in pdf.pages if p.extract_text())
            except ImportError:
                return ""
        if ext == ".docx":
            from docx import Document
            doc = Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if ext in (".xls", ".xlsx"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
            parts: List[str] = []
            for ws in wb.worksheets:
                rows = [" | ".join(str(c) if c is not None else "" for c in row)
                        for row in ws.iter_rows(values_only=True)]
                if rows:
                    parts.append(f"### {ws.title}\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return ""


# ---------------------------------------------------------------------------
# KB upload (复用 xmggzyjy_zwgk_crawler.py 同款实现)
# ---------------------------------------------------------------------------
def _upload_to_kb(filepath: str, kb_id: str, tenant_id: str,
                   parser_id: str = "naive") -> List:
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok or kb is None:
        logging.warning("KB %s not found, skip upload", kb_id)
        return []

    fname = os.path.basename(filepath)
    try:
        from api.db.db_models import DB
        with DB.connection_context():
            dup = (DocumentService.model.select(DocumentService.model.id)
                   .where((DocumentService.model.kb_id == kb_id)
                          & (DocumentService.model.name == fname)).count())
        if dup > 0:
            logging.info("Skip duplicate KB doc: %s", fname)
            return []
    except Exception as e:
        logging.warning("KB dedup check failed (continue): %s", e)

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, filename, blob):
            self.id = get_uuid()
            self.filename = filename
            self.blob = blob

        def read(self):
            return self.blob

    errs, doc_pairs = FileService.upload_document(kb, [_FileObj(fname, blob)], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)

    for doc, _ in doc_pairs:
        doc_id = doc["id"]
        logging.info("Document %s uploaded to KB %s", doc_id, kb_id)
        try:
            DocumentService.update_by_id(doc_id, {"parser_id": parser_id})
        except Exception as e:
            logging.error("Failed to update parser_id: %s", e)
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("Failed to queue parsing: %s", e)
    return doc_pairs


# ---------------------------------------------------------------------------
# CollectionWriter integration
# ---------------------------------------------------------------------------
def _make_writer(tenant_id: str):
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    return CollectionWriter(kb_id="", tenant_id=tenant_id)


def _write_to_db(writer, item: Dict, detail: Dict, task_id: str) -> Optional[str]:
    try:
        date_val = detail.get("date") or item.get("date", "")
        section_name = item.get("section_name", "")
        # extracted_json 字段（去 None）
        ext = {
            "section_name": section_name,
            "subsection_name": section_name,
            "business_type": item.get("business_type", ""),
            "url_type": item.get("url_type", ""),
            "project_id": detail.get("project_id", ""),
            "package_id": detail.get("package_id", ""),
            "pre_notice_id": detail.get("pre_notice_id", ""),
            "tenderer": detail.get("tenderer", ""),
            "agency": detail.get("agency", ""),
            "source": "平潭综合实验区公共资源统一平台",
            "topic_category": section_name.split("-")[-1] if section_name else "",
            "type": section_name.split("-")[-1] if section_name else "",
            "doc_number": "",
            "issuing_authority": detail.get("agency", "") or "",
        }
        data = {
            "title": detail.get("title") or item.get("title", ""),
            "url": item["url"],                  # ★ 完整详情 URL，前端原文链接直接打开
            "date": date_val,
            "publish_datetime": date_val,
            "content": detail.get("content_text", ""),
            "section_name": section_name,        # ★ 前端类型列显示
            "subsection_name": section_name,
            "source": ext["source"],
            "topic_category": ext["topic_category"],
            "type": ext["type"],
            "tenderer": detail.get("tenderer", ""),
            "agency": detail.get("agency", ""),
            "project_id": detail.get("project_id", ""),
            "package_id": detail.get("package_id", ""),
            "pre_notice_id": detail.get("pre_notice_id", ""),
            "business_type": item.get("business_type", ""),
            "attachments": detail.get("attachments", []),
        }

        result_id = writer.write_all(
            item=data,
            site_id=_SITE_ID,
            category="bid",                     # ★ 招投标类
            task_id=task_id,
            site_display=f"{_SITE_NAME} {_SITE_DOMAIN}",
        )
        if result_id:
            logging.debug("Wrote result %s: %s",
                          result_id, str(data["title"])[:60])
        return result_id
    except Exception as e:
        logging.error("CollectionWriter error for %s: %s", item.get("url"), e)
        return None


# ---------------------------------------------------------------------------
# Markdown builder (KB 上传)
# ---------------------------------------------------------------------------
def _build_markdown(item: Dict, detail: Dict, attachment_texts: List) -> str:
    title = detail.get("title") or item.get("title", "无标题")
    lines = [f"# {title}", ""]
    section_name = item.get("section_name", _SITE_NAME)
    lines.append(f"**栏目:** {section_name}")
    if detail.get("tenderer"):
        lines.append(f"**招标人:** {detail['tenderer']}")
    if detail.get("agency"):
        lines.append(f"**招标代理:** {detail['agency']}")
    if detail.get("project_id"):
        lines.append(f"**项目ID:** {detail['project_id']}")
    if detail.get("package_id"):
        lines.append(f"**包件ID:** {detail['package_id']}")
    if detail.get("pre_notice_id"):
        lines.append(f"**预公告ID:** {detail['pre_notice_id']}")
    date_str = detail.get("date") or item.get("date", "")
    if date_str:
        lines.append(f"**发布日期:** {date_str}")
    lines.append(f"**原文地址:** {item['url']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    content = detail.get("content_text", "")
    if len(content) > 50000:
        content = content[:50000] + "\n\n（内容过长，已截断）"
    lines.append(content if content else "（无法提取正文内容）")

    if attachment_texts:
        lines += ["", "---", "", "## 附件内容", ""]
        for fname, ftext in attachment_texts:
            lines.append(f"### {fname}")
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
# Main crawl logic
# ---------------------------------------------------------------------------
def crawl(tenant_id: str, kb_id: str, task_id: str = "",
          date_filter: str = "", max_runtime: int = _MAX_RUNTIME_DEFAULT) -> Dict:
    start_time = time.time()
    date_filter = _normalize_date_filter(date_filter)

    _safe_print("=" * 60)
    _safe_print(f"{_SITE_NAME} 智能采集")
    _safe_print(f"Site ID: {_SITE_ID}")
    _safe_print(f"KB: {kb_id or '(none)'}")
    _safe_print(f"Date filter: {date_filter or 'none (full crawl)'}")
    _safe_print(f"Task ID: {task_id or 'N/A'}")
    _safe_print(f"Sections: {len(_SECTIONS)} (ygg/zbgg/pbjggs/zbjggs)")
    _safe_print("=" * 60)

    sess = _init_session()

    try:
        writer = _make_writer(tenant_id)
    except Exception as e:
        sess.close()
        logging.error("CollectionWriter init failed: %s", e)
        return {
            "status": "fail", "pages": len(_SECTIONS),
            "items_found": 0, "items_new": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": [f"writer init: {e}"],
        }

    # ---- Phase 1: 4 栏目列表第 1 页 ----
    all_items: List[Dict] = []
    try:
        for sec in _SECTIONS:
            if max_runtime - (time.time() - start_time) < 60:
                _safe_print("Timeout approaching — stop list collection")
                break
            _safe_print(f"\n--- [{sec['name']}] ---")
            _safe_print(f"  URL: {sec['list_url']}")
            try:
                items = _fetch_section_items(sess, sec, date_filter)
                all_items.extend(items)
                _safe_print(f"  [{sec['name']}] {len(items)} items")
            except Exception as e:
                logging.error("[%s] list error: %s", sec["name"], e)
                _safe_print(f"  [{sec['name']}] ERROR: {e}")
            _request_delay(_PAGE_DELAY)

        # URL 去重
        seen: set = set()
        unique_items: List[Dict] = []
        for it in all_items:
            if it["url"] not in seen:
                seen.add(it["url"])
                unique_items.append(it)

        _safe_print(f"\n{'=' * 60}")
        _safe_print(f"List phase done: {len(all_items)} items (unique: {len(unique_items)})")
        _safe_print(f"{'=' * 60}")

        # ---- Phase 2: 详情 + 附件 + 入库 + KB ----
        kb_uploaded = 0
        att_uploaded = 0
        errors: List[str] = []

        if not unique_items:
            _safe_print("No items to process (date_filter empty result)")
        else:
            # 启动 Playwright
            pw = browser = context = page = None
            try:
                pw, browser, context, page = _init_browser()
                context_cookies = None  # 首次访问详情后取一次

                for idx, item in enumerate(unique_items):
                    if max_runtime - (time.time() - start_time) < 60:
                        _safe_print(f"Timeout — stop at {idx}/{len(unique_items)}")
                        break
                    try:
                        _safe_print(f"\n[{idx + 1}/{len(unique_items)}] "
                                    f"[{item['section_name'].split('-')[-1]}] "
                                    f"{item['title'][:80]}")

                        detail = _extract_detail(page, item)
                        # 取一次 cookies，复用给附件下载
                        if context_cookies is None:
                            try:
                                context_cookies = context.cookies()
                            except Exception:
                                context_cookies = []

                        _safe_print(f"  Title: {(detail.get('title') or '')[:60]}")
                        _safe_print(f"  Date: {detail.get('date') or 'N/A'}"
                                    f" | Content: {len(detail.get('content_text', ''))} chars"
                                    f" | Attachments: {len(detail.get('attachments', []))}"
                                    f" | Tenderer: {detail.get('tenderer') or '-'}")

                        # 附件下载 + 解压
                        attachment_files: List[str] = []
                        attachment_texts: List[Tuple[str, str]] = []
                        if detail["attachments"]:
                            temp_dir = tempfile.mkdtemp(prefix="pingtan_ggzy_")
                            for att in detail["attachments"]:
                                # 增量适配：同 item 多附件服务器 CD 可能返回相同 filename（如都叫"中标候选人公示.pdf"），
                                # 导致 _download_attachment 内部 dest_path 冲突 + KB 上传 url_tag 前缀无法区分。
                                # 下载完成后立即按 attach_id/url-hash 唯一化重命名，存量下载函数不动。
                                _safe_print(f"  Downloading: {att.get('file_name', '')[:60]}")
                                fpath = _download_attachment(
                                    sess, att, temp_dir, context_cookies=context_cookies)
                                if fpath:
                                    aid = att.get("attach_id") or ""
                                    if not aid:
                                        aid = hashlib.md5(
                                            att.get("file_url", "").encode("utf-8")).hexdigest()[:8]
                                    base, ext = os.path.splitext(fpath)
                                    unique_path = f"{base}.{aid[:6]}{ext}"
                                    if unique_path != fpath and not os.path.exists(unique_path):
                                        try:
                                            os.replace(fpath, unique_path)
                                            fpath = unique_path
                                        except OSError as e:
                                            logging.warning("Rename to unique failed: %s", e)
                                if not fpath:
                                    continue
                                attachment_files.append(fpath)
                                low = fpath.lower()
                                if low.endswith(".zip"):
                                    extracted = _extract_zip(fpath)
                                elif low.endswith(".gz7") or low.endswith(".gz"):
                                    extracted = _extract_gz7(fpath)
                                else:
                                    extracted = []
                                for epath in extracted:
                                    attachment_files.append(epath)
                                    attachment_texts.append(
                                        (os.path.basename(epath), _extract_file_text(epath)))
                                if extracted:
                                    _safe_print(f"    Extracted: {len(extracted)} files")
                                # 非压缩包：提取文本
                                if not extracted and not low.endswith((".zip", ".gz7", ".gz")):
                                    attachment_texts.append(
                                        (os.path.basename(fpath), _extract_file_text(fpath)))

                        # DB 写入
                        result_id = _write_to_db(writer, item, detail, task_id)
                        if not result_id:
                            errors.append(f"db write failed: {item['url']}")

                        # KB 上传
                        if kb_id:
                            try:
                                md_content = _build_markdown(item, detail, attachment_texts)
                                md_dir = tempfile.mkdtemp(prefix="pingtan_ggzy_md_")
                                _md_date = (detail.get("date") or item.get("date") or "").strip()
                                md_name = _sanitize_filename(
                                    (detail.get("title") or item["title"]) +
                                    (f"_{_md_date}" if _md_date else ""), 100)
                                md_path = os.path.join(md_dir, f"{md_name}.md")
                                with open(md_path, "w", encoding="utf-8") as f:
                                    f.write(md_content)
                                if _upload_to_kb(md_path, kb_id, tenant_id):
                                    kb_uploaded += 1

                                # 附件名加 URL 短哈希前缀避免多文章同名附件互相顶掉
                                url_tag = hashlib.md5(
                                    item["url"].encode("utf-8")).hexdigest()[:6]
                                for fpath in attachment_files:
                                    try:
                                        tagged = os.path.join(
                                            os.path.dirname(fpath),
                                            f"{url_tag}_{os.path.basename(fpath)}")
                                        if tagged != fpath and not os.path.exists(tagged):
                                            os.replace(fpath, tagged)
                                            fpath = tagged
                                        if _upload_to_kb(fpath, kb_id, tenant_id):
                                            att_uploaded += 1
                                    except Exception as e:
                                        logging.error("Attachment KB upload failed: %s", e)
                                        errors.append(
                                            f"kb upload att: {os.path.basename(fpath)}: {e}")
                            except Exception as e:
                                logging.error("KB phase error for %s: %s", item["url"], e)
                                errors.append(f"kb phase: {item['url']}: {e}")

                        _request_delay(_ARTICLE_DELAY)
                    except Exception as e:
                        logging.exception("Item processing failed: %s", item.get("url"))
                        errors.append(f"item: {item.get('url')}: {e}")
            finally:
                # 关 Playwright
                try:
                    if context: context.close()
                    if browser: browser.close()
                    if pw: pw.stop()
                except Exception:
                    pass

    finally:
        sess.close()

    elapsed = time.time() - start_time
    wstats = writer.stats
    items_new = wstats.get("results_new", 0)
    items_updated = wstats.get("results_updated", 0)
    items_written = items_new + items_updated
    status = "success" if (items_written > 0 or not unique_items) else "fail"
    summary = {
        "status": status,
        "pages": len(_SECTIONS),
        "items_found": len(all_items),
        "items_new": items_new,
        "items_updated": items_updated,
        "kb_uploaded": kb_uploaded,
        "attachments_uploaded": att_uploaded,
        "errors": errors[:20],
    }
    _safe_print(f"\n{'=' * 60}")
    _safe_print(f"Crawl complete: found={len(all_items)} new={items_new} "
                f"updated={items_updated} kb={kb_uploaded} att={att_uploaded} "
                f"errors={len(errors)} elapsed={elapsed:.0f}s")
    _safe_print(f"SUMMARY: {json.dumps(summary, ensure_ascii=False)}")
    _safe_print("=" * 60)
    return summary


# ---------------------------------------------------------------------------
# Custom runner entry point (unified_crawler.py 调用契约)
# ---------------------------------------------------------------------------
def run(tenant_id: str = "", kb_id: str = "", task_name: str = "",
        task_id: str = "", writer_mode: str = "collection", category: str = "bid",
        date_filter: str = "", full_crawl: bool = False, force_run: bool = False,
        site_config: Any = None, output_dir: str = "") -> Dict:
    """custom_runner 入口。site_config 仅用于日志——栏目/解析逻辑脚本自持。"""
    _safe_print(f"[pingtan_ggzy] custom runner invoked")
    _safe_print(f"  tenant_id={tenant_id} kb_id={kb_id} task_id={task_id}")
    _safe_print(f"  date_filter={date_filter!r} full_crawl={full_crawl} force_run={force_run}")

    actual_date_filter = date_filter
    if not full_crawl and not actual_date_filter:
        actual_date_filter = "today"

    actual_kb_id = kb_id or _DEFAULT_KB_ID

    summary = crawl(
        tenant_id=tenant_id,
        kb_id=actual_kb_id,
        task_id=task_id,
        date_filter=actual_date_filter,
    )
    # 空结果（无 errors）不应判 fail
    if summary.get("status") == "fail" and not summary.get("errors"):
        summary["status"] = "success"
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="平潭综合实验区公共资源统一平台-交易信息 智能采集爬虫")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--kb-id", default=_DEFAULT_KB_ID,
                        help=f"Knowledge base ID (default: {_DEFAULT_KB_ID})")
    parser.add_argument("--task-name", default="pingtan_ggzy", help="Task name")
    parser.add_argument("--date-filter", default="",
                        help="today / YYYY-MM-DD / 空=全量")
    parser.add_argument("--script-args", default="{}",
                        help='JSON: {"task_id": "..."}')
    parser.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT)
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, args.project_root)
        os.chdir(args.project_root)

    settings.init_settings()

    script_args: Dict[str, Any] = {}
    try:
        script_args = json.loads(args.script_args)
    except (json.JSONDecodeError, TypeError):
        pass
    task_id = script_args.get("task_id", "")
    kb_id = args.kb_id or script_args.get("kb_id", "")
    date_filter = args.date_filter or script_args.get("date_filter", "")

    logging.info("PingtanGGZY Crawler | task=%s | kb=%s | date=%s",
                 args.task_name, kb_id, date_filter or "full")

    try:
        summary = crawl(
            tenant_id=args.tenant_id,
            kb_id=kb_id,
            task_id=task_id,
            date_filter=date_filter,
            max_runtime=args.max_runtime,
        )
        print(f"\nSUMMARY: {json.dumps(summary, ensure_ascii=False)}")
    except KeyboardInterrupt:
        _safe_print("\nInterrupted by user")
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        _safe_print(f"\nFATAL: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
