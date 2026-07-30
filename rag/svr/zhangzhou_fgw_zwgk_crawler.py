#!/usr/bin/env python3
"""
漳州市发改委-政务公开 智能采集爬虫

覆盖 6 个页签：
  1. 政策文件  /cms/html/zzsfzhggwyh/zcwj/index.html
  2. 政策解读  /cms/html/zzsfzhggwyh/zcjd/index.html
  3. 通知公告  /cms/html/zzsfzhggwyh/tzgg/index.html
  4. 规划计划  /cms/html/zzsfzhggwyh/ghjh/index.html
     (含子栏目: 工作进展情况/年度计划/中长期发展规划)
  5. 人事信息  /cms/html/zzsfzhggwyh/rsxx/index.html
  6. 专题专栏  /cms/html/zzsfzhggwyh/ztzl/index.html

用法（由 unified_crawler.py 或 trigger 脚本调用）:
    python zhangzhou_fgw_zwgk_crawler.py \\
        --tenant-id <TID> --kb-id <KID> --task-name <NAME> \\
        --date-filter today \\
        --script-args '{"task_id":"<ID>"}'
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
import time
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

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
_BASE = "http://fgw.zhangzhou.gov.cn"
_SITE_NAME = "漳州市发展和改革委员会"
_SITE_ID = "zhangzhou_fgw_zwgk"
_CATEGORY = "news"
_TYPE_NAME = "漳州市发改委-政务公开"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HTML_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_PAGE_DELAY = (1.0, 2.5)
_ARTICLE_DELAY = (0.3, 1.0)

_ATTACH_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".7z",
    ".txt", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}

# 6 个页签的列表页定义
_TABS: List[Dict[str, Any]] = [
    {
        "label": "zcwj",
        "name": "政策文件",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/zcwj/index.html",
        "list_selector": "div.zfwj-main li, div.zfwj-main-list li, div.mod-contain li",
        "sub_sections": [],
    },
    {
        "label": "zcjd",
        "name": "政策解读",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/zcjd/index.html",
        "list_selector": "div.zcjd-main li, div.zcjd-main-list li",
        "sub_sections": [],
    },
    {
        "label": "tzgg",
        "name": "通知公告",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/tzgg/index.html",
        "list_selector": "div.tzgg-main li, div.tzgg-main-list li, div.mod-contain li",
        "sub_sections": [],
    },
    {
        "label": "ghjh",
        "name": "规划计划",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/ghjh/index.html",
        "list_selector": "li a[href*='/cms/html/']",
        "sub_sections": [
            {"name": "工作进展情况", "url": f"{_BASE}/cms/html/zzsfzhggwyh/gzjzqk/index.html"},
            {"name": "年度计划", "url": f"{_BASE}/cms/html/zzsfzhggwyh/ndjh/index.html"},
            {"name": "中长期发展规划", "url": f"{_BASE}/cms/html/zzsfzhggwyh/zcqfzgh/index.html"},
        ],
    },
    {
        "label": "rsxx",
        "name": "人事信息",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/rsxx/index.html",
        "list_selector": "div.mod-contain li, li a[href*='/cms/html/']",
        "sub_sections": [],
    },
    {
        "label": "ztzl",
        "name": "专题专栏",
        "url": f"{_BASE}/cms/html/zzsfzhggwyh/ztzl/index.html",
        "list_selector": "a[href*='/cms/html/zzsfzhggwyh/']",
        "sub_sections": [],
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


def _request_delay(min_s, max_s):
    time.sleep(random.uniform(min_s, max_s))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


def _is_attach_url(url):
    lower = url.lower().split("?")[0]
    return any(lower.endswith(ext) for ext in _ATTACH_EXTENSIONS)


def _normalize_date_filter(val: str) -> str:
    """规整 date_filter 为 YYYY-MM-DD 或空字符串。"""
    if not val:
        return ""
    val = val.strip().lower()
    if val == "today":
        return datetime.date.today().isoformat()
    try:
        return datetime.datetime.strptime(val.replace("/", "-"), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def _item_matches_date(item: Dict, date_filter: str) -> bool:
    """判断 item 日期是否匹配 date_filter。"""
    if not date_filter:
        return True
    for k in ("date", "pub_date", "publishDate"):
        v = item.get(k)
        if not v:
            continue
        try:
            text = str(v).strip()[:10].replace("/", "-")
            parsed = datetime.datetime.strptime(text, "%Y-%m-%d").date().isoformat()
            return parsed == date_filter
        except (ValueError, TypeError):
            continue
    return False


def _resolve_url(href: str, base_url: str) -> str:
    """解析相对 URL 为绝对 URL，统一 http→https 回退。"""
    if not href:
        return ""
    abs_url = urljoin(base_url, href)
    # 确保是同域
    if urlparse(abs_url).netloc != urlparse(_BASE).netloc:
        return ""
    return abs_url


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session() -> _requests.Session:
    sess = _requests.Session()
    sess.headers.update(_HTML_HEADERS)
    sess.verify = False
    try:
        sess.get(_BASE, timeout=30, verify=False)
        logging.info("Session initialized to %s", _BASE)
    except Exception as e:
        logging.warning("Session init warning: %s", e)
    return sess


# ---------------------------------------------------------------------------
# Listing: fetch items from a page
# ---------------------------------------------------------------------------

def _fetch_list_items(sess: _requests.Session, tab: Dict, date_filter: str) -> List[Dict]:
    """从单个 tab 列表页提取条目。

    Returns: [{title, date, url, tab_name, tab_label, source_url}]
    """
    url = tab["url"]
    tab_name = tab["name"]
    tab_label = tab["label"]

    try:
        r = sess.get(url, timeout=60, verify=False)
        r.encoding = 'utf-8'
        html = r.text
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return []

    soup = BeautifulSoup(html, 'lxml')
    items = []
    seen_urls = set()

    # 策略：找到所有指向详情页的 <a> 标签
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href or href.startswith('#') or href.startswith('javascript'):
            continue

        abs_url = _resolve_url(href, url)
        if not abs_url:
            continue

        # 过滤：只保留详情页链接
        is_detail = (
            re.search(r'/\d{4}-\d{2}-\d{2}/\d+\.html', abs_url) or
            'publicInfo.shtml?id=' in abs_url
        )
        if not is_detail:
            continue

        # 过滤导航/索引页
        if '/index.html' in abs_url:
            continue

        if abs_url in seen_urls:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        seen_urls.add(abs_url)

        # 提取日期
        date_str = _extract_date_from_element(a, abs_url)

        # 日期过滤（首次运行不过滤，后续只取当天）
        if date_filter:
            item_test = {"date": date_str}
            if not _item_matches_date(item_test, date_filter):
                continue

        items.append({
            'title': title,
            'date': date_str,
            'url': abs_url,
            'tab_name': tab_name,
            'tab_label': tab_label,
            'source_url': url,
        })

    # 子栏目
    for sub in tab.get("sub_sections", []):
        sub_items = _fetch_sub_section_items(sess, sub, tab_name, tab_label, date_filter)
        items.extend(sub_items)

    logging.info("  [%s] %d items from %s", tab_name, len(items), url)
    return items


def _fetch_sub_section_items(sess: _requests.Session, sub: Dict,
                              tab_name: str, tab_label: str,
                              date_filter: str) -> List[Dict]:
    """从子栏目页提取条目。"""
    url = sub["url"]
    sub_name = sub["name"]
    try:
        r = sess.get(url, timeout=60, verify=False)
        r.encoding = 'utf-8'
        html = r.text
    except Exception as e:
        logging.error("Failed to fetch sub-section %s: %s", url, e)
        return []

    soup = BeautifulSoup(html, 'lxml')
    items = []
    seen_urls = set()

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href or href.startswith('#') or href.startswith('javascript'):
            continue
        abs_url = _resolve_url(href, url)
        if not abs_url:
            continue
        is_detail = (
            re.search(r'/\d{4}-\d{2}-\d{2}/\d+\.html', abs_url) or
            'publicInfo.shtml?id=' in abs_url
        )
        if not is_detail or '/index.html' in abs_url:
            continue
        if abs_url in seen_urls:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        seen_urls.add(abs_url)

        date_str = _extract_date_from_element(a, abs_url)
        if date_filter:
            item_test = {"date": date_str}
            if not _item_matches_date(item_test, date_filter):
                continue

        items.append({
            'title': title,
            'date': date_str,
            'url': abs_url,
            'tab_name': tab_name,
            'tab_label': tab_label,
            'sub_name': sub_name,
            'source_url': url,
        })

    logging.info("    [%s > %s] %d items", tab_name, sub_name, len(items))
    return items


def _extract_date_from_element(a_tag, abs_url: str) -> str:
    """从 <a> 标签的父元素或 URL 中提取日期。"""
    # 从父元素文本提取 MM-DD
    parent = a_tag.parent
    if parent:
        parent_text = parent.get_text(strip=True)
        m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', parent_text)
        if m:
            return m.group(1)
        m = re.search(r'(\d{2}-\d{2})', parent_text)
        if m:
            return f"{datetime.datetime.now().year}-{m.group(1)}"

    # 从 URL 路径提取
    m = re.search(r'/(\d{4})-(\d{2})-(\d{2})/', abs_url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return ""


# ---------------------------------------------------------------------------
# Listing: 专题专栏特殊处理 — 链接网格
# ---------------------------------------------------------------------------

def _fetch_ztzl_items(sess: _requests.Session, tab: Dict, date_filter: str) -> List[Dict]:
    """专题专栏页：网格中的每个子栏目链接 → 进入子栏目页获取列表。"""
    url = tab["url"]
    tab_name = tab["name"]
    tab_label = tab["label"]

    try:
        r = sess.get(url, timeout=60, verify=False)
        r.encoding = 'utf-8'
        html = r.text
    except Exception as e:
        logging.error("Failed to fetch ztzl: %s", url, e)
        return []

    soup = BeautifulSoup(html, 'lxml')
    all_items = []
    seen_sub_urls = set()

    # 只在内容区域查找子栏目（排除 header/nav/footer）
    content_area = None
    for sel in ['div.ztzl-main', 'div.ztzl-content', 'div.content', 'div.main', 'div.mod-contain']:
        content_area = soup.select_one(sel)
        if content_area:
            break
    if not content_area:
        # Fallback: 找 breadcrumb 后面的区域
        breadcrumb = soup.find('div', class_=re.compile(r'position|crumbs', re.I))
        if breadcrumb:
            content_area = breadcrumb.parent

    search_root = content_area if content_area else soup

    # 找到所有子栏目链接（非导航链接）
    for a in search_root.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href:
            continue
        abs_url = _resolve_url(href, url)
        if not abs_url:
            continue
        # 子栏目是 /cms/html/zzsfzhggwyh/XXX/index.html 格式
        if '/cms/html/zzsfzhggwyh/' not in abs_url:
            continue
        if '/index.html' not in abs_url:
            continue
        # 排除首页、主题页面本身、政务公开等管理页面
        if abs_url == url or abs_url in seen_sub_urls:
            continue
        # 排除管理页面
        skip_patterns = ['/lxwm/', '/wzdt/', '/statistics/', '/jggk/', '/zwgk/', '/hdjl/',
                        '/personalrequest/', '/member/', '/jeaf/']
        if any(p in abs_url for p in skip_patterns):
            continue

        sub_name = a.get_text(strip=True)
        if not sub_name or len(sub_name) < 2:
            continue
        seen_sub_urls.add(abs_url)

        # 对每个子栏目，获取其列表
        sub = {"name": sub_name, "url": abs_url}
        sub_items = _fetch_sub_section_items(sess, sub, tab_name, tab_label, date_filter)
        all_items.extend(sub_items)
        _request_delay(*_PAGE_DELAY)

    logging.info("  [%s] %d items from %d sub-sections", tab_name, len(all_items), len(seen_sub_urls))
    return all_items


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _parse_cms_detail(html: str, detail_url: str) -> Dict:
    """解析 CMS HTML 详情页（/cms/html/.../YYYY-MM-DD/NNNN.html）。"""
    soup = BeautifulSoup(html, 'lxml')
    result = {
        'title': '', 'pub_date': '', 'source': '',
        'content_html': '', 'content_text': '', 'attachments': [],
    }

    # 标题 — 尝试多种选择器
    title_el = None
    for sel in ['h1', 'h2', '.article-title', '.title', '.bt', 'title']:
        title_el = soup.find(sel)
        if title_el:
            break
    if title_el:
        text = title_el.get_text(strip=True)
        # 清理标题中的分隔符
        text = text.split('_')[0].split(' - ')[0].strip()
        if len(text) > 4:
            result['title'] = text

    # 日期
    for meta_name in ('PubDate', 'publishdate', 'articledate', 'dc.date'):
        meta = soup.find('meta', attrs={'name': meta_name})
        if meta and meta.get('content'):
            result['pub_date'] = meta['content'].strip()[:10]
            break

    if not result['pub_date']:
        m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', html)
        if m:
            # 取第一个匹配，但要检查是否在正文区域
            result['pub_date'] = m.group(1)

    if not result['pub_date']:
        m = re.search(r'/(\d{4})-(\d{2})-(\d{2})/', detail_url)
        if m:
            result['pub_date'] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 来源
    source_m = re.search(r'来源[：:]\s*([^\s<]{2,30})', html)
    if source_m:
        result['source'] = source_m.group(1).strip()

    # 正文 — 尝试多种内容容器
    content_html = ""
    content_selectors = [
        'div.TRS_Editor', 'div.article-content', 'div.content',
        'div.detail', 'div.view', 'div.con', 'div.main', 'div.article',
        'div#article', 'div.zw-content', 'div.zoom',
    ]
    for sel in content_selectors:
        div = soup.select_one(sel)
        if div:
            # 移除 script/style
            for tag in div.find_all(['script', 'style']):
                tag.decompose()
            content_html = str(div)
            text = div.get_text(separator='\n', strip=True)
            if len(text) > 100:
                result['content_text'] = text
                result['content_html'] = content_html
                break

    # Fallback：取 body 中最大的文本块
    if not result['content_text']:
        body = soup.find('body')
        if body:
            for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            text = body.get_text(separator='\n', strip=True)
            if len(text) > 200:
                result['content_text'] = text

    # 附件 — 扫描所有链接
    result['attachments'] = _extract_attachments_from_soup(soup, detail_url)

    return result


def _parse_publicinfo_detail(html: str, detail_url: str) -> Dict:
    """解析 publicInfo.shtml 详情页。"""
    soup = BeautifulSoup(html, 'lxml')
    result = {
        'title': '', 'pub_date': '', 'source': '',
        'content_html': '', 'content_text': '', 'attachments': [],
    }

    # 标题
    title_el = soup.find('title')
    if title_el:
        text = title_el.get_text(strip=True)
        text = text.split('_')[0].split(' - ')[0].strip()
        if len(text) > 4:
            result['title'] = text

    if not result['title']:
        for h in soup.find_all(['h1', 'h2', 'h3']):
            text = h.get_text(strip=True)
            if len(text) > 4:
                result['title'] = text
                break

    # 日期 — 从元数据表格提取
    for table in soup.find_all('table'):
        trs = table.find_all('tr')
        if len(trs) < 2 or len(trs) > 20:
            continue
        for tr in trs:
            cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
            if len(cells) >= 2:
                for i, cell in enumerate(cells):
                    if '发布日期' in cell or '生成日期' in cell or '发布时间' in cell:
                        if i + 1 < len(cells):
                            m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', cells[i + 1])
                            if m:
                                result['pub_date'] = m.group(1)
        if result['pub_date']:
            break

    if not result['pub_date']:
        m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', html)
        if m:
            result['pub_date'] = m.group(1)

    # 来源
    source_m = re.search(r'来源[：:]\s*([^\s<]{2,30})', html)
    if source_m:
        result['source'] = source_m.group(1).strip()

    # 正文
    content_selectors = [
        'div.TRS_Editor', 'div.article-content', 'div.content',
        'div.detail', 'div.view', 'div.con', 'div.main',
        'div.info-cont', 'div.infoContent',
    ]
    for sel in content_selectors:
        div = soup.select_one(sel)
        if div:
            for tag in div.find_all(['script', 'style']):
                tag.decompose()
            result['content_html'] = str(div)
            text = div.get_text(separator='\n', strip=True)
            if len(text) > 100:
                result['content_text'] = text
                break

    if not result['content_text']:
        body = soup.find('body')
        if body:
            for tag in body.find_all(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            text = body.get_text(separator='\n', strip=True)
            if len(text) > 200:
                result['content_text'] = text

    # 附件
    result['attachments'] = _extract_attachments_from_soup(soup, detail_url)

    return result


def _extract_attachments_from_soup(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    """从 BeautifulSoup 对象提取附件列表。"""
    attachments = []
    seen_urls = set()

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href:
            continue
        abs_url = _resolve_url(href, base_url)
        if not abs_url or abs_url in seen_urls:
            continue

        title = a.get_text(strip=True)

        # 通过扩展名判断附件
        if _is_attach_url(abs_url):
            seen_urls.add(abs_url)
            attachments.append({
                'file_name': title or os.path.basename(urlparse(abs_url).path),
                'file_url': abs_url,
            })
            continue

        # 通过路径关键字判断
        attach_keywords = ['/attached/', '/upload/', 'attachments/',
                          'wordattachments/', 'pdfattachments/',
                          'fileattachments/', '/cms/pages/']
        if any(kw in href for kw in attach_keywords):
            seen_urls.add(abs_url)
            attachments.append({
                'file_name': title or os.path.basename(urlparse(abs_url).path),
                'file_url': abs_url,
            })

    return attachments


def _parse_detail(html: str, detail_url: str) -> Dict:
    """根据 URL 类型路由到正确的解析器。"""
    if 'publicInfo.shtml' in detail_url:
        return _parse_publicinfo_detail(html, detail_url)
    return _parse_cms_detail(html, detail_url)


# ---------------------------------------------------------------------------
# File download & ZIP extraction
# ---------------------------------------------------------------------------

def _download_file(sess: _requests.Session, file_url: str, timeout: int = 120) -> Optional[bytes]:
    """下载文件，返回 bytes 或 None。"""
    parsed = urlparse(file_url)
    main_parsed = urlparse(_BASE)

    if parsed.netloc == main_parsed.netloc or not parsed.netloc:
        try:
            resp = sess.get(file_url, timeout=timeout, stream=True, verify=False)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            logging.error("Download error %s: %s", file_url, e)
        return None

    # 外域文件用 urllib
    req = urllib.request.Request(file_url, headers=_HTML_HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if len(data) > 100:
            return data
    except Exception as e:
        logging.error("Download error (external) %s: %s", file_url, e)
    return None


def _extract_zip(zip_data: bytes, temp_dir: str) -> List[str]:
    """解压 ZIP 到临时目录，返回文件路径列表。"""
    import tempfile
    extracted = []
    try:
        with zipfile.ZipFile(zipfile.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))
                if not safe_name:
                    continue
                # 跳过目录项
                if safe_name.endswith('/'):
                    continue
                dest_path = os.path.join(temp_dir, safe_name)
                if os.path.exists(dest_path):
                    continue
                with open(dest_path, 'wb') as f:
                    f.write(zf.read(name))
                extracted.append(dest_path)
    except Exception as e:
        logging.warning("ZIP extract error: %s", e)
    return extracted


def _extract_file_text(filepath: str) -> str:
    """提取文件文本内容。"""
    ext = os.path.splitext(filepath)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            import fitz
            with fitz.open(filepath) as doc:
                for page in doc:
                    text += page.get_text() + "\n"
        elif ext == '.docx':
            from docx import Document
            doc = Document(filepath)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext in ('.xls', '.xlsx'):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    line = '\t'.join(str(c) if c is not None else '' for c in row)
                    text += line + "\n"
                text += "\n"
        elif ext == '.txt':
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
    except Exception as e:
        logging.warning("Failed to extract text from %s: %s", filepath, e)

    return text.strip()


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath: str, kb_id: str, tenant_id: str, parser_id: str = "naive") -> List:
    """上传文件到知识库并触发解析。"""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok or kb is None:
        logging.warning("KB %s not found, skip upload", kb_id)
        return []

    # KB 级去重
    existing = set()
    try:
        for d in DocumentService.select(DocumentService.model.name).where(
            DocumentService.model.kb_id == kb_id
        ):
            existing.add(d.name)
    except Exception:
        pass
    fname = os.path.basename(filepath)
    if fname in existing:
        logging.info("Skip duplicate: %s", fname)
        return []

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, filename, blob):
            self.id = get_uuid()
            self.filename = filename
            self.blob = blob

        def read(self):
            return self.blob

    file_obj = _FileObj(fname, blob)
    errs, doc_pairs = FileService.upload_document(kb, [file_obj], tenant_id)

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

def _write_to_db(item: Dict, detail: Dict, site_id: str, category: str,
                  task_id: str, tenant_id: str) -> Optional[str]:
    """通过 CollectionWriter 写入 crawler_result 表。"""
    try:
        from rag.svr.crawler_engine.collection_writer import CollectionWriter
        writer = CollectionWriter(kb_id="", tenant_id=tenant_id)

        data = {
            "title": detail.get('title') or item.get('title', ''),
            "url": item['url'],
            "date": detail.get('pub_date') or item.get('date', ''),
            "content": detail.get('content_text', ''),
            "content_html": detail.get('content_html', ''),
            "source": detail.get('source', ''),
            "tab_name": item.get('tab_name', ''),
            "tab_label": item.get('tab_label', ''),
            "sub_name": item.get('sub_name', ''),
            "attachments": detail.get('attachments', []),
            "type": _TYPE_NAME,
        }

        site_display = f"{_SITE_NAME} {_BASE.replace('http://', '')}"

        result_id = writer.write_all(
            item=data,
            site_id=site_id,
            category=category,
            task_id=task_id,
            site_display=site_display,
        )

        if result_id:
            logging.debug("Wrote result %s: %s", result_id, data['title'][:60])
            return result_id
        else:
            logging.debug("Skipped (filtered or duplicate): %s", data['title'][:60])
            return None
    except Exception as e:
        logging.error("CollectionWriter error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Markdown builder (for KB upload)
# ---------------------------------------------------------------------------

def _build_markdown(item: Dict, detail: Dict) -> str:
    """构建用于 KB 上传的 Markdown 文本。"""
    lines = [
        f"# {detail.get('title', item['title'])}",
        "",
        f"**来源**: {_SITE_NAME}",
        f"**栏目**: {item.get('tab_name', '')}",
    ]
    if item.get('sub_name'):
        lines.append(f"**子栏目**: {item['sub_name']}")
    if detail.get('pub_date'):
        lines.append(f"**发布日期**: {detail['pub_date']}")
    elif item.get('date'):
        lines.append(f"**发布日期**: {item['date']}")
    if detail.get('source'):
        lines.append(f"**来源**: {detail['source']}")
    lines.append(f"**原文链接**: {item['url']}")
    lines.append(f"**类型**: {_TYPE_NAME}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if detail.get('content_text'):
        lines.append(detail['content_text'])
    else:
        lines.append("(无法提取正文内容)")

    if detail.get('attachments'):
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附件")
        lines.append("")
        for att in detail['attachments']:
            lines.append(f"- [{att.get('file_name', 'attachment')}]({att.get('file_url', '')})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main crawl logic
# ---------------------------------------------------------------------------

def crawl(tenant_id: str, kb_id: str, task_id: str = "",
          date_filter: str = "", max_runtime: int = 3300):
    """主爬取逻辑。"""
    start_time = time.time()

    _safe_print("=" * 60)
    _safe_print(f"漳州市发改委-政务公开 智能采集")
    _safe_print(f"Site: {_BASE}")
    _safe_print(f"KB: {kb_id}")
    _safe_print(f"Date filter: {date_filter or 'none (full crawl)'}")
    _safe_print(f"Task ID: {task_id or 'N/A'}")
    _safe_print("=" * 60)

    sess = _init_session()

    # ---- Phase 1: 采集所有列表项 ----
    all_items = []
    for tab in _TABS:
        remaining = max_runtime - (time.time() - start_time)
        if remaining < 60:
            _safe_print(f"Timeout approaching — stopping tab collection")
            break

        tab_name = tab["name"]
        _safe_print(f"\n--- [{tab_name}] {tab['url']} ---")

        try:
            if tab["label"] == "ztzl":
                items = _fetch_ztzl_items(sess, tab, date_filter)
            else:
                items = _fetch_list_items(sess, tab, date_filter)
            all_items.extend(items)
            _safe_print(f"  Total [{tab_name}]: {len(items)} items")
        except Exception as e:
            logging.error("[%s] fetch error: %s", tab_name, e)
            _safe_print(f"  [{tab_name}] ERROR: {e}")

        _request_delay(*_PAGE_DELAY)

    # ---- 去重（按 URL） ----
    seen = set()
    unique_items = []
    for it in all_items:
        if it['url'] not in seen:
            seen.add(it['url'])
            unique_items.append(it)

    _safe_print(f"\n{'='*60}")
    _safe_print(f"Total items: {len(all_items)} (unique: {len(unique_items)})")
    _safe_print(f"{'='*60}")

    # ---- Phase 2: 处理每条详情 ----
    total_processed = 0
    total_kb = 0
    total_att = 0
    errors = []

    for idx, item in enumerate(unique_items):
        remaining = max_runtime - (time.time() - start_time)
        if remaining < 60:
            _safe_print(f"Timeout — stopping at {idx}/{len(unique_items)}")
            break

        _safe_print(f"\n[{idx+1}/{len(unique_items)}] [{item['tab_name']}] {item['title'][:80]}")
        _safe_print(f"  {item['url'][:150]}")

        # 获取详情页
        try:
            r = sess.get(item['url'], timeout=60, verify=False)
            if r.status_code != 200:
                _safe_print(f"  HTTP {r.status_code} — skipped")
                errors.append(f"HTTP {r.status_code}: {item['url']}")
                continue
            r.encoding = 'utf-8'
            html = r.text
        except Exception as e:
            _safe_print(f"  Fetch error: {e}")
            errors.append(f"Fetch error: {item['url']}: {e}")
            continue

        # 解析详情页
        detail = _parse_detail(html, item['url'])
        _safe_print(f"  Title: {detail.get('title', 'N/A')[:60]}")
        _safe_print(f"  Date: {detail.get('pub_date', item.get('date', 'N/A'))} | "
                    f"Content: {len(detail.get('content_text', ''))} chars | "
                    f"Attachments: {len(detail.get('attachments', []))}")

        # ---- 处理附件：下载、解压 ----
        attachment_files = []
        if detail.get('attachments'):
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix='zhangzhou_fgw_')
            for att in detail['attachments']:
                att_url = att['file_url']
                att_name = att.get('file_name', 'attachment')
                _safe_print(f"  Downloading: {att_name[:60]}")
                data = _download_file(sess, att_url)
                if data:
                    fname = _sanitize_filename(att_name, 100)
                    fpath = os.path.join(temp_dir, fname)
                    with open(fpath, 'wb') as f:
                        f.write(data)
                    attachment_files.append(fpath)
                    _safe_print(f"    OK ({len(data):,} bytes)")

                    # ZIP 解压
                    if fname.lower().endswith('.zip'):
                        extracted = _extract_zip(data, temp_dir)
                        for epath in extracted:
                            if epath not in attachment_files:
                                attachment_files.append(epath)
                        _safe_print(f"    ZIP extracted: {len(extracted)} files")

        # ---- 写入 crawler_result 表 ----
        tab_category = f"漳州市-{item.get('tab_name', '')}" if item.get('tab_name') else _CATEGORY
        result_id = _write_to_db(item, detail, _SITE_ID, tab_category, task_id, tenant_id)
        if result_id:
            total_processed += 1

        # ---- 构建 Markdown 并上传 KB ----
        if kb_id:
            md_content = _build_markdown(item, detail)

            # 附件文本追加到 markdown
            for fpath in attachment_files:
                ext_text = _extract_file_text(fpath)
                if ext_text:
                    md_content += f"\n\n--- {os.path.basename(fpath)} ---\n\n{ext_text}"

            # 保存临时 markdown 文件
            import tempfile
            md_dir = tempfile.mkdtemp(prefix='zhangzhou_fgw_md_')
            safe_name = _sanitize_filename(item['url'], 100)
            md_path = os.path.join(md_dir, f"{safe_name}.md")
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)

            # 上传 markdown
            try:
                _upload_to_kb(md_path, kb_id, tenant_id)
                total_kb += 1
            except Exception as e:
                logging.error("KB upload failed for %s: %s", item['url'], e)
                errors.append(f"KB upload: {item['url']}: {e}")

            # 上传附件文件
            for fpath in attachment_files:
                try:
                    _upload_to_kb(fpath, kb_id, tenant_id)
                    total_att += 1
                except Exception as e:
                    logging.error("Attachment KB upload failed: %s", e)

        _request_delay(*_ARTICLE_DELAY)

    # ---- 汇总 ----
    elapsed = time.time() - start_time
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Crawl complete.")
    _safe_print(f"  Items processed: {total_processed}")
    _safe_print(f"  KB documents: {total_kb}")
    _safe_print(f"  Attachment uploads: {total_att}")
    _safe_print(f"  Errors: {len(errors)}")
    _safe_print(f"  Elapsed: {elapsed:.0f}s")
    if errors:
        _safe_print(f"  Error details: {errors[:5]}...")
    _safe_print(f"{'='*60}")

    return {
        "status": "success" if total_processed > 0 else "fail",
        "pages": len(_TABS),
        "items_found": len(all_items),
        "items_new": total_processed,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors[:20],
    }


# ---------------------------------------------------------------------------
# Custom runner entry point (called by unified_crawler.py)
# ---------------------------------------------------------------------------

def run(tenant_id: str = "", kb_id: str = "", task_name: str = "",
        task_id: str = "", writer_mode: str = "collection", category: str = "news",
        date_filter: str = "", full_crawl: bool = False, force_run: bool = False,
        site_config: Any = None, output_dir: str = "") -> Dict:
    """Custom runner entry point — 由 unified_crawler.py 调用。

    unified_crawler.py 检测到 YAML 中 custom_runner 字段后，
    会 import 此模块并调用此函数，而不是走标准 engine 流程。
    """
    _safe_print(f"[zhangzhou_fgw_zwgk] Custom runner invoked")
    _safe_print(f"  tenant_id={tenant_id}, kb_id={kb_id}, task_id={task_id}")
    _safe_print(f"  date_filter={date_filter}, full_crawl={full_crawl}")

    # 首次触发 (trigger) 不带 date_filter → 只采当天
    # CLI 手动 full crawl → date_filter="" 不过滤
    actual_date_filter = date_filter
    if not full_crawl and not actual_date_filter:
        # 默认只采当天
        actual_date_filter = "today"

    summary = crawl(
        tenant_id=tenant_id,
        kb_id=kb_id,
        task_id=task_id,
        date_filter=actual_date_filter,
    )

    # 映射到统一状态码
    status = summary.get("status", "fail")
    if status == "fail":
        summary["status"] = "error" if summary.get("errors") else "success"

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="漳州市发改委-政务公开 智能采集爬虫"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--kb-id", default=None, help="Knowledge base ID")
    parser.add_argument("--task-name", default="zhangzhou_fgw_zwgk", help="Task name")
    parser.add_argument("--date-filter", default="", help="Date filter: today / YYYY-MM-DD / empty=full")
    parser.add_argument("--script-args", default="{}", help="JSON: {task_id, ...}")
    parser.add_argument("--max-runtime", type=int, default=3300, help="Max runtime seconds")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--project-root", default=None, help="Project root")
    parser.add_argument("--target-url", default=_BASE, help="Target URL")
    parser.add_argument("--llm-id", default=None, help="LLM ID (unused)")
    parser.add_argument("--llm-model", default=None, help="LLM model (unused)")
    parser.add_argument("--access-token", default=None, help="Access token (unused)")

    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, args.project_root)
        os.chdir(args.project_root)

    init_root_logger("zhangzhou_fgw_zwgk")
    settings.init_settings()

    # 解析 script_args
    script_args = {}
    try:
        script_args = json.loads(args.script_args)
    except (json.JSONDecodeError, TypeError):
        pass
    task_id = script_args.get("task_id", "")

    kb_id = args.kb_id or ""
    date_filter = args.date_filter or script_args.get("date_filter", "")

    logging.info("Zhangzhou FGW ZWGK Crawler | task=%s | kb=%s | date=%s",
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
        logging.info("Interrupted by user")
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        _safe_print(f"\nFATAL: {e}")
        raise


if __name__ == "__main__":
    CONSUMER_NAME = "zhangzhou_fgw_zwgk_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
