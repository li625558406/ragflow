#!/usr/bin/env python3
"""
三明市公共资源交易网-政策法规 智能采集爬虫（智能采集系统 custom_runner）

站点: https://smggzy.sm.gov.cn/smwz/zcfg/  (Epoint WebBuilder CMS, SSR 渲染)
站点ID: smggzy_zcfg | category: policy | 类型列: 三明市-政策法规

覆盖页签（CategoryNum）:
  综合类(003001) 工程建设(003002) 政府采购(003003)
  土地矿产(003004) 产权交易(003005) 其他(003006)

站点特征（实测 2026-08-03 + 旧脚本 smzcfg_crawler.py 逆向成果）:
  - 列表页 SSR，20条/页；第N页 GET {list_url}?pageing={N}（返回同构HTML片段）
  - 列表 URL 必须带尾斜杠（无斜杠 301）
  - 详情页 /smwz/InfoDetail/?InfoID={UUID}&CategoryNum={code}
  - 标题 .ewb-show-title；日期【信息时间：YYYY-MM-DD】；正文 #mainContent
  - 附件双通道: bqpoint 下载网关（URL无扩展名）+ 直链文件后缀
  - totalPageNums JS 变量仅多页页签存在，缺失=单页
  - 反爬弱: UA+Referer、verify_ssl=false、0.8~2.0s 延迟、会话预热即可

数据落库:
  CollectionWriter(category="policy") → crawler_result + collection_policy_ext
  extracted_json.section_name = "三明市-政策法规"（前端类型列显示值）
  extracted_json.tab_name / subsection_name = 页签中文名（次级字段）
  KB: 正文markdown + 附件原件 + ZIP解压成员，全部上传并解析

用法（unified_crawler.py custom_runner 调用，或 CLI 直接执行）:
    python smggzy_zcfg_crawler.py \\
        --tenant-id <TID> --kb-id <KID> --task-name <NAME> \\
        --date-filter today
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
_BASE = "https://smggzy.sm.gov.cn"
_SITE_NAME = "三明市公共资源交易网-政策法规"
_SITE_ID = "smggzy_zcfg"
_CATEGORY = "policy"
_TYPE_NAME = "三明市-政策法规"
_SITE_DOMAIN = "smggzy.sm.gov.cn"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://smggzy.sm.gov.cn/smwz/zcfg/",
}

_PAGE_DELAY = (0.8, 2.0)       # 列表翻页间隔（反爬保守值，旧脚本验证过）
_ARTICLE_DELAY = (0.5, 1.2)     # 详情页间隔
_MAX_PAGES = 50                 # 单页签最大翻页数（兜底，实测最多2页）
_MAX_RUNTIME_DEFAULT = 3000     # 50分钟安全上限

# ZIP 防爆约束
_ZIP_MAX_MEMBERS = 200
_ZIP_MAX_TOTAL_BYTES = 100 * 1024 * 1024

# 附件直链扩展名
_FILE_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|rar|zip|7z|txt)(\b|[?#])", re.I)

# 旧脚本逆向正则（smzcfg_crawler.py）
_INFOID_RE = re.compile(
    r"InfoID=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
_CATEGORYNUM_RE = re.compile(r"CategoryNum=(\d+)", re.I)
_TOTAL_PAGES_RE = re.compile(r"var\s+totalPageNums\s*=\s*(\d+)")
_INFO_TIME_RE = re.compile(r"信息时间[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})")
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
# 文号: 发改法规〔2026〕195号 / 闽政办[2024]12号
_WENHAO_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d{1,6}\s*号")
# 发文机关启发式: 标题中 "XX(机关)关于/印发/转发" 前缀
_ISSUER_SPLIT_RE = re.compile(r"^(.{2,40}?)(?=关于|印发|转发)")
_ISSUER_TAIL_RE = re.compile(
    r"(?:人民政府|委员会|办公室|管理局|监督局|发改|财政|住建|交通|水利|商务|"
    r"国资|审计|自然资源|生态环境|应急|市场监管|中心|部门|单位|厅|局|委|办|部|署|院)$"
)

# 详情页正文截断标记（页脚噪声，旧脚本成果）
_FOOTER_MARKERS = ["设为首页", "主办：", "备案序号：", "技术支持：",
                   "访问统计", "闽公网安备", "网站标识码"]
# 标题上溯扫描时排除的导航/功能行（旧脚本成果）
_NAV_LINE_RE = re.compile(
    r"^(当前位置|首页|交易信息|建设工程|政府采购|土地矿产|产权交易|"
    r"中介机构|其他交易|政策法规|服务指引|学习园地|政务公开|"
    r"中心介绍|行政事务|中心动态|政民互动|下载中心|企业信息库|"
    r"数字证书|区块链|存证|【|阅读次数|我要打印|我要关闭|"
    r"综合类|工程建设|其他|"
    r"\d{4}年\d{1,2}月\d{1,2}日\s*(星期[一二三四五六日])?)$"
)

# 6 个页签（URL 必须带尾斜杠，实测无斜杠 301）
_TABS: List[Dict[str, str]] = [
    {"code": "003001", "name": "综合类", "list_url": f"{_BASE}/smwz/zcfg/003001/"},
    {"code": "003002", "name": "工程建设", "list_url": f"{_BASE}/smwz/zcfg/003002/"},
    {"code": "003003", "name": "政府采购", "list_url": f"{_BASE}/smwz/zcfg/003003/"},
    {"code": "003004", "name": "土地矿产", "list_url": f"{_BASE}/smwz/zcfg/003004/"},
    {"code": "003005", "name": "产权交易", "list_url": f"{_BASE}/smwz/zcfg/003005/"},
    {"code": "003006", "name": "其他", "list_url": f"{_BASE}/smwz/zcfg/003006/"},
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


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r"\s+", " ", name)
    name = name.strip("._ ")
    return name[:max_len] if name else "untitled"


def _normalize_date_filter(val: str) -> str:
    """规整 date_filter 为 YYYY-MM-DD 或空字符串。"""
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
    """'2026/2/11' → '2026-02-11'；解析失败返回原串。"""
    if not val:
        return ""
    m = _DATE_RE.search(str(val))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(val).strip()


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _init_session() -> _requests.Session:
    """会话预热：先 GET 站点根拿 Cookie（旧脚本反爬策略）。"""
    sess = _requests.Session()
    sess.headers.update(_HEADERS)
    sess.verify = False
    for warm_url in (_BASE + "/", _BASE + "/smwz/zcfg/"):
        try:
            sess.get(warm_url, timeout=30, verify=False)
        except Exception as e:
            logging.warning("Session warmup %s: %s", warm_url, e)
    time.sleep(1)
    return sess


def _fetch_page(sess: _requests.Session, url: str, max_retries: int = 3) -> str:
    """带重试的页面抓取；403/502 视为反爬信号加倍等待。"""
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
# Listing
# ---------------------------------------------------------------------------

def _parse_list_items(html: str, page_url: str) -> List[Dict]:
    """从列表页/分页fragment提取条目。

    结构: ul.ewb-notice-items > li > a.l(标题+InfoID链接) + span.ewb-ndate(日期)
    页面可能是完整HTML（第1页）或片段（?pageing=N），统一用 InfoID 正则锚定。
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_ids = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        m = _INFOID_RE.search(href)
        if not m:
            continue
        info_id = m.group(1)
        if info_id in seen_ids:
            continue
        seen_ids.add(info_id)

        cm = _CATEGORYNUM_RE.search(href)
        cat_num = cm.group(1) if cm else ""

        # 日期: 同一 li 内的 span.ewb-ndate
        date_str = ""
        li = a_tag.find_parent("li")
        if li:
            span = li.find("span", class_=re.compile("ewb-ndate"))
            if span:
                date_str = _normalize_date_str(span.get_text(strip=True))

        # 原文URL: 直接用列表页给出的 href 规整为绝对URL（不做模板拼接，避免断链）
        abs_url = urljoin(page_url, href)

        items.append({
            "id": info_id,
            "title": title,
            "url": abs_url,
            "category_num": cat_num,
            "date": date_str,
        })
    return items


def _fetch_tab_items(sess: _requests.Session, tab: Dict, date_filter: str) -> List[Dict]:
    """抓取单个页签的全部列表条目（含翻页与增量过滤）。"""
    list_url = tab["list_url"]
    first_html = _fetch_page(sess, list_url)
    if not first_html:
        logging.error("[%s] Failed to fetch list page: %s", tab["name"], list_url)
        return []

    m = _TOTAL_PAGES_RE.search(first_html)
    total_pages = int(m.group(1)) if m else 1   # 变量缺失 = 单页页签
    total_pages = min(total_pages, _MAX_PAGES)

    collected = []
    seen_ids = set()
    empty_streak = 0

    for page_num in range(1, total_pages + 1):
        # 注意: list_url 尾斜杠必须保留——无斜杠 URL 带 query 会 301（Location 补斜杠），
        # 直接带斜杠请求可省一次重定向跳转（实测 200）
        html = first_html if page_num == 1 else _fetch_page(
            sess, list_url + f"?pageing={page_num}")
        if not html:
            logging.warning("[%s] page %d fetch failed, stop paging", tab["name"], page_num)
            break

        page_items = _parse_list_items(html, list_url)
        if not page_items:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue
        empty_streak = 0

        page_dates = []
        for it in page_items:
            if it["id"] in seen_ids:
                continue
            seen_ids.add(it["id"])

            if date_filter:
                if not it["date"]:
                    # 无日期条目无法判断归属，增量模式严格跳过
                    continue
                page_dates.append(it["date"])
                if it["date"] != date_filter:
                    continue

            it["tab_name"] = tab["name"]
            it["tab_code"] = tab["code"]
            collected.append(it)

        # 增量早停: 列表按时间倒序，本页全部早于目标日期 → 后续页更旧
        if date_filter and page_dates and all(d < date_filter for d in page_dates):
            break

        if page_num < total_pages:
            _request_delay(_PAGE_DELAY)

    logging.info("[%s] %d items collected (total_pages=%d, filter=%s)",
                 tab["name"], len(collected), total_pages, date_filter or "none")
    return collected


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def _guess_issuer(title: str) -> str:
    """从标题前缀启发式提取发文机关；无法确定返回空（宁缺勿错）。"""
    if not title:
        return ""
    m = _ISSUER_SPLIT_RE.match(title.strip())
    if not m:
        return ""
    cand = m.group(1).strip()
    if _ISSUER_TAIL_RE.search(cand):
        return cand
    return ""


def _parse_detail_page(html: str, detail_url: str, list_title: str, list_date: str) -> Dict:
    """解析详情页：标题/日期/文号/发文机关/正文/附件。"""
    result = {
        "title": "", "date": "", "wenhao": "", "fawenjiguan": "",
        "content_text": "", "attachments": [],
    }
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    # ---- 标题: 多级回退 ----
    title_el = soup.select_one(".ewb-show-title")
    if title_el:
        text = title_el.get_text(strip=True)
        if len(text) > 2:
            result["title"] = text

    if not result["title"]:
        # 旧脚本策略: 信息时间行上溯扫描，排除导航/功能行
        body_text = soup.get_text()
        body_lines = [ln.strip() for ln in body_text.split("\n")]
        info_idx = next((i for i, ln in enumerate(body_lines) if "信息时间" in ln), -1)
        if info_idx > 0:
            for j in range(info_idx - 1, max(0, info_idx - 20), -1):
                line = body_lines[j]
                if not line or len(line) < 4 or len(line) > 300:
                    continue
                if _NAV_LINE_RE.match(line):
                    continue
                result["title"] = line
                break

    if not result["title"]:
        route_el = soup.select_one(".ewb-route")
        if route_el:
            for part in reversed(route_el.get_text(strip=True).split(">")):
                part = part.strip()
                if part and len(part) > 3 and "首页" not in part and "政策法规" not in part:
                    result["title"] = part
                    break

    if not result["title"]:
        for sel in ("h1", "h2", "h3"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if 2 < len(text) < 300:
                    result["title"] = text
                    break

    if not result["title"]:
        result["title"] = list_title   # 最终兜底=列表标题

    # ---- 日期: 信息时间正则，回退列表日期 ----
    body_text_all = soup.get_text()
    dm = _INFO_TIME_RE.search(body_text_all)
    if dm:
        result["date"] = _normalize_date_str(dm.group(1))
    else:
        result["date"] = list_date

    # ---- 正文: #mainContent (div.ewb-show-con) ----
    content_div = soup.select_one("#mainContent") or soup.select_one(".ewb-show-con")
    if content_div:
        result["content_text"] = content_div.get_text(separator="\n", strip=True)

    if len(result["content_text"]) < 50:
        # 兜底: body文本 + 页脚截断（旧脚本策略）
        text = soup.get_text(separator="\n", strip=True)
        for marker in _FOOTER_MARKERS:
            idx = text.find(marker)
            if idx > 200:
                text = text[:idx]
                break
        if len(text) > len(result["content_text"]):
            result["content_text"] = text

    if len(result["content_text"]) > 50000:
        result["content_text"] = result["content_text"][:50000] + "\n\n（内容过长，已截断）"

    # ---- 文号 / 发文机关 ----
    wm = _WENHAO_RE.search(result["content_text"] or body_text_all)
    if wm:
        result["wenhao"] = re.sub(r"\s+", "", wm.group(0))
    result["fawenjiguan"] = _guess_issuer(result["title"])

    # ---- 附件: 双通道（bqpoint网关 + 直链后缀）----
    seen_att = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith("javascript"):
            continue
        text = a_tag.get_text(strip=True)
        abs_url = urljoin(detail_url, href)
        if abs_url in seen_att:
            continue

        if "download.bqpoint.com" in href or "downloaddetail" in href:
            seen_att.add(abs_url)
            result["attachments"].append({
                "file_name": text or "附件下载",
                "file_url": abs_url,
                "source": "bqpoint",
            })
        elif _FILE_EXT_RE.search(href):
            seen_att.add(abs_url)
            result["attachments"].append({
                "file_name": text or os.path.basename(urlparse(abs_url).path) or "attachment",
                "file_url": abs_url,
                "source": "direct",
            })

    return result


# ---------------------------------------------------------------------------
# Attachment download & ZIP extraction
# ---------------------------------------------------------------------------

def _download_attachment(sess: _requests.Session, att: Dict, dest_dir: str) -> Optional[str]:
    """下载附件（直链或 bqpoint 网关），返回本地文件路径或 None。"""
    url = att.get("file_url", "")
    name = att.get("file_name", "attachment")
    if not url:
        return None

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(name, 120)
    # 网关链接无扩展名时，尝试从 URL basename 补
    if not re.search(r"\.\w{2,5}$", safe_name):
        url_name = os.path.basename(urlparse(url).path)
        if re.search(r"\.\w{2,5}$", url_name):
            safe_name = _sanitize_filename(url_name, 120)

    dest_path = os.path.join(dest_dir, safe_name)
    try:
        _request_delay(_ARTICLE_DELAY)
        parsed = urlparse(url)
        if parsed.netloc == urlparse(_BASE).netloc or not parsed.netloc:
            resp = sess.get(url, timeout=120, verify=False)
        else:
            # 外部网关（bqpoint 等）走独立请求
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            ctx = None
            resp = urllib.request.urlopen(req, timeout=120)
            resp.status_code = resp.getcode()
            resp_content = resp.read()
            resp_headers = dict(resp.headers or {})
            if resp.status_code == 200 and len(resp_content) > 100:
                cd = resp_headers.get("Content-Disposition", "")
                fn_m = re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
                if fn_m:
                    safe_name = _sanitize_filename(fn_m.group(1), 120)
                    dest_path = os.path.join(dest_dir, safe_name)
                with open(dest_path, "wb") as f:
                    f.write(resp_content)
                logging.info("Downloaded (external): %s (%d bytes)", safe_name, len(resp_content))
                return dest_path
            return None

        if resp.status_code != 200 or len(resp.content) <= 100:
            logging.warning("Attachment HTTP %s / too small: %s", resp.status_code, url)
            return None

        # 直链下载: 优先 Content-Disposition 文件名
        cd = resp.headers.get("Content-Disposition", "")
        fn_m = re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
        if fn_m:
            safe_name = _sanitize_filename(fn_m.group(1), 120)
            dest_path = os.path.join(dest_dir, safe_name)

        head = resp.content[:64].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            logging.warning("Attachment URL returned HTML (gateway page?), skipped: %s", url)
            return None

        with open(dest_path, "wb") as f:
            f.write(resp.content)
        logging.info("Downloaded: %s (%d bytes)", safe_name, len(resp.content))
        return dest_path
    except Exception as e:
        logging.error("Download error %s: %s", url, e)
        return None


def _extract_zip(zip_path: str) -> List[str]:
    """解压 ZIP（含容量/成员数防护）；加密包跳过。返回解压出的文件路径。"""
    extracted = []
    dest_dir = os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [n for n in zf.namelist()
                       if not n.startswith("__MACOSX")
                       and not os.path.basename(n).startswith(".")
                       and not n.endswith("/")]
            if len(members) > _ZIP_MAX_MEMBERS:
                logging.warning("ZIP has %d members > %d, skipped: %s",
                                len(members), _ZIP_MAX_MEMBERS, zip_path)
                return []
            total = sum(i.file_size for i in zf.infolist())
            if total > _ZIP_MAX_TOTAL_BYTES:
                logging.warning("ZIP uncompressed size %d > limit, skipped: %s",
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
            parts = []
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
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath: str, kb_id: str, tenant_id: str, parser_id: str = "naive") -> List:
    """上传文件到 KB 并触发解析；KB 内文件名去重。"""
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok or kb is None:
        logging.warning("KB %s not found, skip upload", kb_id)
        return []

    # KB 级文件名去重（重跑不重复上传）
    # 注意: DocumentService 无 select 类方法（模板里的写法是坏的）；DB 在 db_models 里。
    # KB 可能有 5w+ 文档 → 精确 name 点查，不能全表拉名字。
    fname = os.path.basename(filepath)
    try:
        from api.db.db_models import DB
        with DB.connection_context():
            dup = DocumentService.model.select(DocumentService.model.id).where(
                (DocumentService.model.kb_id == kb_id)
                & (DocumentService.model.name == fname)).count()
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
    """创建复用于整轮采集的 CollectionWriter（stats 区分 new/updated）。"""
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    return CollectionWriter(kb_id="", tenant_id=tenant_id)


def _write_to_db(writer, item: Dict, detail: Dict, task_id: str) -> Optional[str]:
    """经 CollectionWriter 写 crawler_result + collection_policy_ext。

    writer 由 crawl() 创建并复用，便于用 writer.stats 精确统计 new/updated。
    """
    try:
        date_val = detail.get("date") or item.get("date", "")
        data = {
            "title": detail.get("title") or item.get("title", ""),
            "url": item["url"],                                  # 完整详情URL（含InfoID+CategoryNum）
            "date": date_val,
            "publish_datetime": date_val,
            "content": detail.get("content_text", ""),
            "section_name": _TYPE_NAME,                          # ★ 前端类型列显示值
            "subsection_name": item.get("tab_name", ""),
            "tab_name": item.get("tab_name", ""),                # 页签名次级字段
            "doc_number": detail.get("wenhao", ""),
            "issuing_authority": detail.get("fawenjiguan", ""),
            "topic_category": item.get("tab_name", ""),          # policy扩展表主题归类
            "attachments": detail.get("attachments", []),
            "type": _TYPE_NAME,
        }

        result_id = writer.write_all(
            item=data,
            site_id=_SITE_ID,
            category=_CATEGORY,
            task_id=task_id,
            site_display=f"{_SITE_NAME} {_SITE_DOMAIN}",
        )
        if result_id:
            logging.debug("Wrote result %s: %s", result_id, str(data["title"])[:60])
        return result_id
    except Exception as e:
        logging.error("CollectionWriter error for %s: %s", item.get("url"), e)
        return None


# ---------------------------------------------------------------------------
# Markdown builder (KB upload)
# ---------------------------------------------------------------------------

def _build_markdown(item: Dict, detail: Dict, attachment_texts: List) -> str:
    """KB 上传用 markdown：元信息 + 正文 + 附件/ZIP成员文本。"""
    title = detail.get("title") or item.get("title", "无标题")
    lines = [f"# {title}", ""]
    lines.append(f"**栏目:** {_TYPE_NAME}")
    if item.get("tab_name"):
        lines.append(f"**子栏目:** {item['tab_name']}")
    if detail.get("fawenjiguan"):
        lines.append(f"**发布机构:** {detail['fawenjiguan']}")
    if detail.get("wenhao"):
        lines.append(f"**发文字号:** {detail['wenhao']}")
    date_str = detail.get("date") or item.get("date", "")
    if date_str:
        lines.append(f"**发布日期:** {date_str}")
    lines.append(f"**原文链接:** {item['url']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    content = detail.get("content_text", "")
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
    _safe_print(f"Site: {_BASE}/smwz/zcfg/  |  Site ID: {_SITE_ID}")
    _safe_print(f"KB: {kb_id or '(none)'}")
    _safe_print(f"Date filter: {date_filter or 'none (full crawl)'}")
    _safe_print(f"Task ID: {task_id or 'N/A'}")
    _safe_print("=" * 60)

    sess = _init_session()

    # CollectionWriter 整轮复用：stats 精确区分 new/updated（重跑 items_new=0）
    try:
        writer = _make_writer(tenant_id)
    except Exception as e:
        sess.close()
        logging.error("CollectionWriter init failed: %s", e)
        return {
            "status": "fail",
            "pages": len(_TABS),
            "items_found": 0,
            "items_new": 0,
            "kb_uploaded": 0,
            "attachments_uploaded": 0,
            "errors": [f"writer init: {e}"],
        }

    # ---- Phase 1: 6页签列表采集 ----
    all_items: List[Dict] = []
    try:
        for tab in _TABS:
            if max_runtime - (time.time() - start_time) < 60:
                _safe_print("Timeout approaching — stop list collection")
                break
            _safe_print(f"\n--- [{tab['name']}] {tab['list_url']} ---")
            try:
                items = _fetch_tab_items(sess, tab, date_filter)
                all_items.extend(items)
                _safe_print(f"  [{tab['name']}] {len(items)} items")
            except Exception as e:
                logging.error("[%s] list error: %s", tab["name"], e)
                _safe_print(f"  [{tab['name']}] ERROR: {e}")
            _request_delay(_PAGE_DELAY)

        # URL 去重（跨页签可能有重叠）
        seen = set()
        unique_items = []
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

        for idx, item in enumerate(unique_items):
            if max_runtime - (time.time() - start_time) < 60:
                _safe_print(f"Timeout — stop at {idx}/{len(unique_items)}")
                break

            try:
                _safe_print(f"\n[{idx + 1}/{len(unique_items)}] [{item['tab_name']}] {item['title'][:80]}")

                html = _fetch_page(sess, item["url"])
                if not html:
                    errors.append(f"detail fetch failed: {item['url']}")
                    _safe_print("  detail fetch FAILED")
                    continue

                detail = _parse_detail_page(html, item["url"], item["title"], item.get("date", ""))
                _safe_print(f"  Title: {detail['title'][:60]}")
                _safe_print(f"  Date: {detail['date'] or 'N/A'} | Content: {len(detail['content_text'])} chars"
                            f" | Wenhao: {detail['wenhao'] or '-'} | Attachments: {len(detail['attachments'])}")

                # 附件下载 + ZIP 解压 + 文本提取
                attachment_files: List[str] = []
                attachment_texts: List = []
                if detail["attachments"]:
                    temp_dir = tempfile.mkdtemp(prefix="smggzy_zcfg_")
                    for att in detail["attachments"]:
                        _safe_print(f"  Downloading: {att.get('file_name', 'attachment')[:60]}")
                        fpath = _download_attachment(sess, att, temp_dir)
                        if not fpath:
                            continue
                        attachment_files.append(fpath)

                        # 只按扩展名解压 .zip：.docx/.xlsx/.pptx 也是 ZIP 容器（PK\x03\x04 魔数），
                        # 若按魔数判断会被拆成 XML 碎片上传 KB，破坏原始文档。
                        # 原始文件本身已加入 attachment_files，会整体上传 KB。
                        if fpath.lower().endswith(".zip"):
                            extracted = _extract_zip(fpath)
                            for epath in extracted:
                                attachment_files.append(epath)
                                attachment_texts.append(
                                    (os.path.basename(epath), _extract_file_text(epath)))
                            _safe_print(f"    ZIP extracted: {len(extracted)} files")
                        else:
                            attachment_texts.append(
                                (os.path.basename(fpath), _extract_file_text(fpath)))

                # DB 写入（crawler_result + collection_policy_ext）
                result_id = _write_to_db(writer, item, detail, task_id)
                if not result_id:
                    errors.append(f"db write failed: {item['url']}")

                # KB 上传（正文md + 附件 + ZIP成员）
                if kb_id:
                    try:
                        md_content = _build_markdown(item, detail, attachment_texts)
                        md_dir = tempfile.mkdtemp(prefix="smggzy_zcfg_md_")
                        # 文件名带发布日期：不同文章同标题（跨页签/同题不同文）不互相顶掉；
                        # 完全同 URL 的真重复由 crawler_result 主键去重，不会进到这里。
                        _md_date = (detail.get("date") or item.get("date") or "").strip()
                        md_name = _sanitize_filename(
                            f"{detail['title'] or item['title']}_{_md_date}" if _md_date
                            else (detail["title"] or item["title"]), 100)
                        md_path = os.path.join(md_dir, f"{md_name}.md")
                        with open(md_path, "w", encoding="utf-8") as f:
                            f.write(md_content)
                        if _upload_to_kb(md_path, kb_id, tenant_id):
                            kb_uploaded += 1

                        # 附件名加 URL 短哈希前缀：多条目的同名附件（如 1.pdf）不互相顶掉；
                        # 前缀对同一 URL 稳定 → 重跑幂等去重仍然成立。
                        url_tag = hashlib.md5(item["url"].encode("utf-8")).hexdigest()[:6]
                        for fpath in attachment_files:
                            try:
                                _tagged = os.path.join(
                                    os.path.dirname(fpath),
                                    f"{url_tag}_{os.path.basename(fpath)}")
                                if _tagged != fpath and not os.path.exists(_tagged):
                                    os.replace(fpath, _tagged)
                                    fpath = _tagged
                                if _upload_to_kb(fpath, kb_id, tenant_id):
                                    att_uploaded += 1
                            except Exception as e:
                                logging.error("Attachment KB upload failed: %s", e)
                                errors.append(f"kb upload att: {os.path.basename(fpath)}: {e}")
                    except Exception as e:
                        logging.error("KB phase error for %s: %s", item["url"], e)
                        errors.append(f"kb phase: {item['url']}: {e}")

                _request_delay(_ARTICLE_DELAY)
            except Exception as e:
                # 单条处理异常不中断整轮采集（对抗畸形页面/磁盘/编码等意外）
                logging.exception("Item processing failed: %s", item.get("url"))
                errors.append(f"item: {item.get('url')}: {e}")

    finally:
        sess.close()

    elapsed = time.time() - start_time
    # writer.stats 精确区分 new/updated：重跑时 items_new=0、items_updated=N，status 仍 success
    wstats = writer.stats
    items_new = wstats.get("results_new", 0)
    items_updated = wstats.get("results_updated", 0)
    items_written = items_new + items_updated
    status = "success" if (items_written > 0 or not unique_items) else "fail"
    summary = {
        "status": status,
        "pages": len(_TABS),
        "items_found": len(all_items),
        "items_new": items_new,
        "items_updated": items_updated,
        "kb_uploaded": kb_uploaded,
        "attachments_uploaded": att_uploaded,
        "errors": errors[:20],
    }
    _safe_print(f"\n{'=' * 60}")
    _safe_print(f"Crawl complete: found={len(all_items)} new={items_new} updated={items_updated} "
                f"kb={kb_uploaded} att={att_uploaded} errors={len(errors)} "
                f"elapsed={elapsed:.0f}s")
    _safe_print(f"SUMMARY: {json.dumps(summary, ensure_ascii=False)}")
    _safe_print("=" * 60)
    return summary


# ---------------------------------------------------------------------------
# Custom runner entry point (unified_crawler.py 调用契约)
# ---------------------------------------------------------------------------

def run(tenant_id: str = "", kb_id: str = "", task_name: str = "",
        task_id: str = "", writer_mode: str = "collection", category: str = "policy",
        date_filter: str = "", full_crawl: bool = False, force_run: bool = False,
        site_config: Any = None, output_dir: str = "") -> Dict:
    """custom_runner 入口。site_config 仅用于日志——页签/解析逻辑脚本自持。"""
    _safe_print(f"[smggzy_zcfg] custom runner invoked")
    _safe_print(f"  tenant_id={tenant_id} kb_id={kb_id} task_id={task_id}")
    _safe_print(f"  date_filter={date_filter!r} full_crawl={full_crawl} force_run={force_run}")

    actual_date_filter = date_filter
    if not full_crawl and not actual_date_filter:
        actual_date_filter = "today"   # 非全量且未指定 → 默认只采当天

    summary = crawl(
        tenant_id=tenant_id,
        kb_id=kb_id,
        task_id=task_id,
        date_filter=actual_date_filter,
    )
    if summary.get("status") == "fail" and not summary.get("errors"):
        summary["status"] = "success"
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="三明市公共资源交易网-政策法规 智能采集爬虫")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--kb-id", default="", help="Knowledge base ID")
    parser.add_argument("--task-name", default="smggzy_zcfg", help="Task name")
    parser.add_argument("--date-filter", default="", help="today / YYYY-MM-DD / 空=全量")
    parser.add_argument("--script-args", default="{}", help='JSON: {"task_id": "..."}')
    parser.add_argument("--max-runtime", type=int, default=_MAX_RUNTIME_DEFAULT)
    parser.add_argument("--output-dir", default=None, help="unused (DB/KB only)")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--target-url", default=_BASE, help="unused")
    parser.add_argument("--llm-id", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--access-token", default=None)
    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, args.project_root)
        os.chdir(args.project_root)

    settings.init_settings()

    script_args = {}
    try:
        script_args = json.loads(args.script_args)
    except (json.JSONDecodeError, TypeError):
        pass
    task_id = script_args.get("task_id", "")
    kb_id = args.kb_id or script_args.get("kb_id", "")
    date_filter = args.date_filter or script_args.get("date_filter", "")

    logging.info("SMGGZY ZCFG Crawler | task=%s | kb=%s | date=%s",
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
        raise


if __name__ == "__main__":
    CONSUMER_NAME = "smggzy_zcfg_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
