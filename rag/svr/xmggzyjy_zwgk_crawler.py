#!/usr/bin/env python3
"""
厦门市公共资源交易网-政务公开 智能采集爬虫（智能采集系统 custom_runner）

站点: https://zyjy.as.xm.gov.cn/zwgk/  (政府站点 SSR 渲染，非 SPA)
站点ID: xmggzyjy_zwgk | 类型列(section_name): 厦门市-政策法规 / 厦门市-财政资金 / 厦门市-曝光专栏

覆盖栏目（政务公开下 3 个一级栏目）:
  zcfg 政策法规  https://zyjy.as.xm.gov.cn/zwgk/zcfg/  category=policy
  czzj 财政资金  https://zyjy.as.xm.gov.cn/zwgk/czzj/  category=other
  pgzl 曝光专栏  https://zyjy.as.xm.gov.cn/zwgk/pgzl/  category=news

站点特征（实测 2026-08-04）:
  - 纯 SSR HTML，curl 即可拿完整 DOM（非 SPA、无加密）
  - 列表 ul li > a.clearflx，标题在 a.title 属性，href 为相对路径（./gcjs/YYYYMM/xxx.htm）
  - 日期拆两段: <h1>日</h1> + <p>YYYY-MM</p>，需拼装成 YYYY-MM-DD
  - 详情页 meta 标签齐全: ArticleTitle / PubDate / ContentSource
  - 正文容器: .article_content .TRS_Editor
  - 附件: .article_attachment li a（直链相对路径）
  - 反爬弱: UA + Referer + verify_ssl=false + 会话预热即可

数据落库:
  CollectionWriter 按 section 分别以 policy/other/news 写入
    - policy → crawler_result + collection_policy_ext（文号/发文机构正则提取）
    - other / news → crawler_result + extracted_json
  extracted_json.section_name = 类型列中文显示值
  KB: 正文 md + 附件原件 + ZIP 解压成员，全部上传并解析

采集范围: 仅抓每个栏目列表第 1 页（用户口径），date_filter=today 仅保当天发布
跨次去重: crawler_result.id = md5(site_id|source_url) 主键 upsert

用法（unified_crawler.py custom_runner 调用，或 CLI 直接执行）:
    python xmggzyjy_zwgk_crawler.py \\
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
from typing import Any, Dict, List, Optional, Tuple
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
_BASE = "https://zyjy.as.xm.gov.cn"
_SITE_NAME = "厦门市公共资源交易中心-政务公开"
_SITE_ID = "xmggzyjy_zwgk"
_SITE_DOMAIN = "zyjy.as.xm.gov.cn"

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
    "Referer": f"{_BASE}/zwgk/",
}

_PAGE_DELAY = (0.8, 2.0)        # 栏目间隔（反爬保守值）
_ARTICLE_DELAY = (0.5, 1.2)     # 详情页间隔
_MAX_RUNTIME_DEFAULT = 2400     # 40 分钟安全上限

# ZIP 防爆约束
_ZIP_MAX_MEMBERS = 200
_ZIP_MAX_TOTAL_BYTES = 100 * 1024 * 1024

# 附件直链扩展名
_FILE_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|rar|zip|7z|txt|wps)(\b|[?#])", re.I)

# 厦门日期: 列表页 "时间：YYYY-MM-DD HH:MM" 或 detail meta PubDate=YYYY-MM-DD
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DETAIL_TIME_RE = re.compile(r"时间[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})")
# 文号: 厦住建建筑〔2026〕21号 / 闽政办[2024]12号 / 财库〔2024〕6号
_WENHAO_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d{1,6}\s*号")
# 发文机关启发式: 标题前缀 "XX(机关)关于/印发/转发"
_ISSUER_SPLIT_RE = re.compile(r"^(.{2,40}?)(?=关于|印发|转发)")
_ISSUER_TAIL_RE = re.compile(
    r"(?:人民政府|委员会|办公室|管理局|监督局|发改|财政|住建|交通|水利|商务|"
    r"国资|审计|自然资源|生态环境|应急|市场监管|中心|部门|单位|厅|局|委|办|部|署|院)$"
)

# 详情页正文截断标记（页脚噪声）
_FOOTER_MARKERS = ["设为首页", "主办单位", "备案序号", "技术支持",
                   "网站标识码", "闽公网安备", "业务咨询"]

# 3 个栏目：label / 中文名 / 列表 URL（必须带尾斜杠，否则 301）/ category
_SECTIONS: List[Dict[str, str]] = [
    {
        "label": "zcfg",
        "name": "厦门市-政策法规",
        "list_url": f"{_BASE}/zwgk/zcfg/",
        "category": "policy",
    },
    {
        "label": "czzj",
        "name": "厦门市-财政资金",
        "list_url": f"{_BASE}/zwgk/czzj/",
        "category": "other",
    },
    {
        "label": "pgzl",
        "name": "厦门市-曝光专栏",
        "list_url": f"{_BASE}/zwgk/pgzl/",
        "category": "news",
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
    """'2026/6/10' / '2026-6-10' → '2026-06-10'；解析失败返回原串。"""
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
    """会话预热：GET 站点根 + 政务公开首页拿 Cookie。"""
    sess = _requests.Session()
    sess.headers.update(_HEADERS)
    sess.verify = False
    for warm_url in (_BASE + "/", _BASE + "/zwgk/"):
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

_LIST_ITEM_HREF_RE = re.compile(r"^\./(?:[A-Za-z0-9_]+/)?\d{6}/t\d{8}_\d+\.htm$", re.I)


def _parse_list_items(html: str, page_url: str) -> List[Dict]:
    """从列表页提取条目。

    结构: div.ggzy_list > ul > li > a.clearflx (href=./xxxx/YYYYMM/tYYYYMMDD_xxxx.htm, title=标题)
    日期拆两段: div.ggzy_list_l h1(日) + p(YYYY-MM) → 拼成 YYYY-MM-DD
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []
    seen_urls: set = set()

    list_div = soup.select_one("div.ggzy_list") or soup
    for a_tag in list_div.find_all("a", attrs={"title": True}):
        title = (a_tag.get("title") or "").strip()
        href = (a_tag.get("href") or "").strip()
        if not title or not href:
            continue
        if href.startswith("javascript"):
            continue
        # 锚定为文章详情 URL（含年月目录 + tYYYYMMDD_xxxx.htm）
        if not _LIST_ITEM_HREF_RE.match(href):
            # 非文章链（导航/分类/其他栏目），跳过
            continue

        abs_url = urljoin(page_url, href)
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        # 日期拼装: a 内 div.ggzy_list_l > h1(日) + p(YYYY-MM)
        date_str = ""
        left_div = a_tag.select_one("div.ggzy_list_l")
        if left_div:
            h1 = left_div.find("h1")
            p = left_div.find("p")
            day = h1.get_text(strip=True) if h1 else ""
            ym = p.get_text(strip=True) if p else ""
            m_ym = re.match(r"(\d{4})[-/](\d{1,2})", ym)
            if m_ym and day.isdigit():
                date_str = f"{m_ym.group(1)}-{int(m_ym.group(2)):02d}-{int(day):02d}"
            elif m_ym:
                # 仅有年月，补 01 兜底（极少见，标记为弱日期）
                date_str = f"{m_ym.group(1)}-{int(m_ym.group(2)):02d}-01"

        # id: URL 末段 tYYYYMMDD_xxxx.htm → tYYYYMMDD_xxxx
        parsed = urlparse(abs_url)
        basename = os.path.basename(parsed.path)
        item_id = os.path.splitext(basename)[0] or hashlib.md5(abs_url.encode()).hexdigest()

        items.append({
            "id": item_id,
            "title": title,
            "url": abs_url,
            "date": date_str,
        })
    return items


def _fetch_section_items(sess: _requests.Session, section: Dict, date_filter: str) -> List[Dict]:
    """抓取单栏目第 1 页（用户口径: 不翻页）。"""
    list_url = section["list_url"]
    html = _fetch_page(sess, list_url)
    if not html:
        logging.error("[%s] Failed to fetch list page: %s", section["name"], list_url)
        return []

    page_items = _parse_list_items(html, list_url)

    collected: List[Dict] = []
    for it in page_items:
        if date_filter:
            # 严格模式: 日期不匹配或无日期 → 跳过
            if not it["date"] or it["date"] != date_filter:
                continue
        it["section_label"] = section["label"]
        it["section_name"] = section["name"]
        it["category"] = section["category"]
        collected.append(it)

    logging.info("[%s] %d items (filter=%s)",
                 section["name"], len(collected), date_filter or "none")
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
    """解析详情页：标题/日期/来源/文号/发文机关/正文/附件。

    容器选择器（实测）:
      标题: <meta name="ArticleTitle"> → h1 → 面包屑末段
      日期: <meta name="PubDate"> → .article_time "时间：YYYY-MM-DD HH:MM"
      来源: <meta name="ContentSource"> → .article_source "来源：xxx"
      正文: .article_content .TRS_Editor → .article_content
      附件: .article_attachment li a
    """
    result: Dict[str, Any] = {
        "title": "", "date": "", "source": "",
        "wenhao": "", "fawenjiguan": "",
        "content_text": "", "attachments": [],
    }
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    # ---- 标题: meta → h1 ----
    meta_title = soup.find("meta", attrs={"name": "ArticleTitle"})
    if meta_title and meta_title.get("content", "").strip():
        result["title"] = meta_title["content"].strip()

    if not result["title"] or len(result["title"]) < 4:
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if 2 < len(text) < 300:
                result["title"] = text

    if not result["title"]:
        result["title"] = list_title  # 兜底=列表标题

    # ---- 日期: meta PubDate → .article_time ----
    meta_pub = soup.find("meta", attrs={"name": "PubDate"})
    if meta_pub and meta_pub.get("content", "").strip():
        result["date"] = _normalize_date_str(meta_pub["content"].strip())
    else:
        time_span = soup.select_one(".article_time")
        if time_span:
            dm = _DETAIL_TIME_RE.search(time_span.get_text())
            if dm:
                result["date"] = _normalize_date_str(dm.group(1))

    if not result["date"]:
        result["date"] = list_date  # 兜底=列表拼装日期

    # ---- 来源: meta ContentSource → .article_source ----
    meta_src = soup.find("meta", attrs={"name": "ContentSource"})
    if meta_src and meta_src.get("content", "").strip():
        result["source"] = meta_src["content"].strip()
    else:
        src_span = soup.select_one(".article_source")
        if src_span:
            txt = src_span.get_text(strip=True)
            result["source"] = re.sub(r"^来源[：:]\s*", "", txt)

    # ---- 正文: .article_content .TRS_Editor → .article_content ----
    content_div = soup.select_one(".article_content .TRS_Editor") or \
                  soup.select_one(".article_content")
    if content_div:
        # 移除附件区块（避免重复）
        for att in content_div.select(".article_attachment"):
            att.decompose()
        result["content_text"] = content_div.get_text(separator="\n", strip=True)

    if len(result["content_text"]) < 50:
        # 兜底: body 文本 + 页脚截断
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

    # ---- 文号 / 发文机关（政策法规栏目才有意义，但统一提取，无用即空）----
    wm = _WENHAO_RE.search(result["content_text"] or soup.get_text())
    if wm:
        result["wenhao"] = re.sub(r"\s+", "", wm.group(0))
    result["fawenjiguan"] = _guess_issuer(result["title"])

    # ---- 附件: .article_attachment li a（直链相对路径）----
    seen_att: set = set()
    att_block = soup.select_one(".article_attachment")
    if att_block:
        for a_tag in att_block.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith("javascript"):
                continue
            text = a_tag.get_text(strip=True)
            abs_url = urljoin(detail_url, href)
            if abs_url in seen_att:
                continue
            # 过滤非文件链（保险起见）
            if not _FILE_EXT_RE.search(abs_url):
                continue
            seen_att.add(abs_url)
            result["attachments"].append({
                "file_name": text or os.path.basename(urlparse(abs_url).path) or "附件",
                "file_url": abs_url,
                "source": "direct",
            })

    return result


# ---------------------------------------------------------------------------
# Attachment download & ZIP extraction
# ---------------------------------------------------------------------------

def _download_attachment(sess: _requests.Session, att: Dict, dest_dir: str) -> Optional[str]:
    """下载附件（直链），返回本地文件路径或 None。"""
    url = att.get("file_url", "")
    name = att.get("file_name", "attachment")
    if not url:
        return None

    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _sanitize_filename(name, 120)
    if not re.search(r"\.\w{2,5}$", safe_name):
        url_name = os.path.basename(urlparse(url).path)
        if re.search(r"\.\w{2,5}$", url_name):
            safe_name = _sanitize_filename(url_name, 120)

    dest_path = os.path.join(dest_dir, safe_name)
    try:
        _request_delay(_ARTICLE_DELAY)
        resp = sess.get(url, timeout=120, verify=False, stream=True)
        if resp.status_code != 200:
            logging.warning("Attachment HTTP %s: %s", resp.status_code, url)
            return None
        # Content-Disposition 文件名优先
        cd = resp.headers.get("Content-Disposition", "")
        fn_m = re.search(r'filename[^;=\n]*=["\']?([^"\'\n;]+)', cd, re.I)
        if fn_m:
            safe_name = _sanitize_filename(fn_m.group(1), 120)
            dest_path = os.path.join(dest_dir, safe_name)

        content = resp.content
        if len(content) <= 100:
            logging.warning("Attachment too small: %s", url)
            return None

        head = content[:64].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
            logging.warning("Attachment URL returned HTML (gateway page?), skipped: %s", url)
            return None

        with open(dest_path, "wb") as f:
            f.write(content)
        logging.info("Downloaded: %s (%d bytes)", safe_name, len(content))
        return dest_path
    except Exception as e:
        logging.error("Download error %s: %s", url, e)
        return None


def _extract_zip(zip_path: str) -> List[str]:
    """解压 ZIP（含容量/成员数防护）；加密包跳过。返回解压出的文件路径。"""
    extracted: List[str] = []
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
                # 路径穿越防护
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
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath: str, kb_id: str, tenant_id: str, parser_id: str = "naive") -> List:
    """上传文件到 KB 并触发解析；KB 内文件名去重（重跑不重复上传）。"""
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
    """创建整轮复用的 CollectionWriter（stats 区分 new/updated）。"""
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    return CollectionWriter(kb_id="", tenant_id=tenant_id)


def _write_to_db(writer, item: Dict, detail: Dict, task_id: str) -> Optional[str]:
    """按 item.category 分别写入 crawler_result（+ policy 扩展表）。

    writer 由 crawl() 创建并复用，便于用 writer.stats 精确统计 new/updated。
    """
    try:
        category = item.get("category", "other")
        date_val = detail.get("date") or item.get("date", "")
        data = {
            "title": detail.get("title") or item.get("title", ""),
            "url": item["url"],                                  # 完整详情 URL（避免前端 404）
            "date": date_val,
            "publish_datetime": date_val,
            "content": detail.get("content_text", ""),
            "section_name": item.get("section_name", ""),        # ★ 前端类型列显示值
            "subsection_name": item.get("section_name", ""),
            "doc_number": detail.get("wenhao", ""),
            "issuing_authority": detail.get("fawenjiguan", ""),
            "source": detail.get("source", ""),
            "topic_category": item.get("section_name", ""),
            "attachments": detail.get("attachments", []),
            "type": item.get("section_name", ""),
        }

        result_id = writer.write_all(
            item=data,
            site_id=_SITE_ID,
            category=category,                                   # ★ 按栏目分别 policy/other/news
            task_id=task_id,
            site_display=f"{_SITE_NAME} {_SITE_DOMAIN}",
        )
        if result_id:
            logging.debug("Wrote result %s [%s]: %s",
                          result_id, category, str(data["title"])[:60])
        return result_id
    except Exception as e:
        logging.error("CollectionWriter error for %s: %s", item.get("url"), e)
        return None


# ---------------------------------------------------------------------------
# Markdown builder (KB upload)
# ---------------------------------------------------------------------------

def _build_markdown(item: Dict, detail: Dict, attachment_texts: List) -> str:
    """KB 上传用 markdown：元信息 + 正文 + 附件/ZIP 成员文本。"""
    title = detail.get("title") or item.get("title", "无标题")
    lines = [f"# {title}", ""]
    lines.append(f"**栏目:** {item.get('section_name', _SITE_NAME)}")
    if detail.get("source"):
        lines.append(f"**来源:** {detail['source']}")
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
    _safe_print(f"Site: {_BASE}/zwgk/  |  Site ID: {_SITE_ID}")
    _safe_print(f"KB: {kb_id or '(none)'}")
    _safe_print(f"Date filter: {date_filter or 'none (full crawl)'}")
    _safe_print(f"Task ID: {task_id or 'N/A'}")
    _safe_print(f"Sections: {len(_SECTIONS)} (zcfg/czzj/pgzl)")
    _safe_print("=" * 60)

    sess = _init_session()

    try:
        writer = _make_writer(tenant_id)
    except Exception as e:
        sess.close()
        logging.error("CollectionWriter init failed: %s", e)
        return {
            "status": "fail",
            "pages": len(_SECTIONS),
            "items_found": 0, "items_new": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": [f"writer init: {e}"],
        }

    # ---- Phase 1: 3 栏目第 1 页列表采集 ----
    all_items: List[Dict] = []
    try:
        for sec in _SECTIONS:
            if max_runtime - (time.time() - start_time) < 60:
                _safe_print("Timeout approaching — stop list collection")
                break
            _safe_print(f"\n--- [{sec['name']}] {sec['list_url']} ---")
            try:
                items = _fetch_section_items(sess, sec, date_filter)
                all_items.extend(items)
                _safe_print(f"  [{sec['name']}] {len(items)} items")
            except Exception as e:
                logging.error("[%s] list error: %s", sec["name"], e)
                _safe_print(f"  [{sec['name']}] ERROR: {e}")
            _request_delay(_PAGE_DELAY)

        # URL 去重（跨栏目极少重叠，但保险）
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

        for idx, item in enumerate(unique_items):
            if max_runtime - (time.time() - start_time) < 60:
                _safe_print(f"Timeout — stop at {idx}/{len(unique_items)}")
                break

            try:
                _safe_print(f"\n[{idx + 1}/{len(unique_items)}] [{item['section_name']}] {item['title'][:80]}")

                html = _fetch_page(sess, item["url"])
                if not html:
                    errors.append(f"detail fetch failed: {item['url']}")
                    _safe_print("  detail fetch FAILED")
                    continue

                detail = _parse_detail_page(html, item["url"], item["title"], item.get("date", ""))
                _safe_print(f"  Title: {detail['title'][:60]}")
                _safe_print(f"  Date: {detail['date'] or 'N/A'} | Source: {detail.get('source') or '-'}"
                            f" | Content: {len(detail['content_text'])} chars"
                            f" | Wenhao: {detail['wenhao'] or '-'}"
                            f" | Attachments: {len(detail['attachments'])}")

                # 附件下载 + ZIP 解压 + 文本提取
                attachment_files: List[str] = []
                attachment_texts: List[Tuple[str, str]] = []
                if detail["attachments"]:
                    temp_dir = tempfile.mkdtemp(prefix="xmggzyjy_zwgk_")
                    for att in detail["attachments"]:
                        _safe_print(f"  Downloading: {att.get('file_name', 'attachment')[:60]}")
                        fpath = _download_attachment(sess, att, temp_dir)
                        if not fpath:
                            continue
                        attachment_files.append(fpath)

                        # 只按扩展名解压 .zip（docx/xlsx 是 ZIP 容器但整体上传）
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

                # DB 写入（按 category 分别走 policy_ext 或纯 crawler_result）
                result_id = _write_to_db(writer, item, detail, task_id)
                if not result_id:
                    errors.append(f"db write failed: {item['url']}")

                # KB 上传（正文 md + 附件 + ZIP 成员）
                if kb_id:
                    try:
                        md_content = _build_markdown(item, detail, attachment_texts)
                        md_dir = tempfile.mkdtemp(prefix="xmggzyjy_zwgk_md_")
                        _md_date = (detail.get("date") or item.get("date") or "").strip()
                        md_name = _sanitize_filename(
                            f"{detail['title'] or item['title']}_{_md_date}" if _md_date
                            else (detail["title"] or item["title"]), 100)
                        md_path = os.path.join(md_dir, f"{md_name}.md")
                        with open(md_path, "w", encoding="utf-8") as f:
                            f.write(md_content)
                        if _upload_to_kb(md_path, kb_id, tenant_id):
                            kb_uploaded += 1

                        # 附件名加 URL 短哈希前缀，避免多文章同名附件互相顶掉
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
                # 单条异常不中断整轮采集（对抗畸形页面/磁盘/编码意外）
                logging.exception("Item processing failed: %s", item.get("url"))
                errors.append(f"item: {item.get('url')}: {e}")

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
    """custom_runner 入口。site_config 仅用于日志——栏目/解析逻辑脚本自持。"""
    _safe_print(f"[xmggzyjy_zwgk] custom runner invoked")
    _safe_print(f"  tenant_id={tenant_id} kb_id={kb_id} task_id={task_id}")
    _safe_print(f"  date_filter={date_filter!r} full_crawl={full_crawl} force_run={force_run}")

    actual_date_filter = date_filter
    # 非全量且未指定 → 默认只采当天（用户口径: 后续触发只取当天）
    if not full_crawl and not actual_date_filter:
        actual_date_filter = "today"

    summary = crawl(
        tenant_id=tenant_id,
        kb_id=kb_id,
        task_id=task_id,
        date_filter=actual_date_filter,
    )
    # 首次接入且当天无更新时，空结果不应判 fail（让 trigger 状态好看）
    if summary.get("status") == "fail" and not summary.get("errors"):
        summary["status"] = "success"
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="厦门市公共资源交易网-政务公开 智能采集爬虫")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--kb-id", default="", help="Knowledge base ID")
    parser.add_argument("--task-name", default="xmggzyjy_zwgk", help="Task name")
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

    script_args: Dict[str, Any] = {}
    try:
        script_args = json.loads(args.script_args)
    except (json.JSONDecodeError, TypeError):
        pass
    task_id = script_args.get("task_id", "")
    kb_id = args.kb_id or script_args.get("kb_id", "")
    date_filter = args.date_filter or script_args.get("date_filter", "")

    logging.info("XMGGOJY ZWGK Crawler | task=%s | kb=%s | date=%s",
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
    CONSUMER_NAME = "xmggzyjy_zwgk_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
