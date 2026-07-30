"""
mohurd_mlxz_crawler — 中华人民共和国住房和城乡建设部·目录下载 智能采集 runner
==============================================================================

数据源: https://www.mohurd.gov.cn/gongkai/mlxz/index.html
覆盖年份: 2004-2023 (20 个 PDF 信息公开目录)

数据流:
  1. requests.get 列表页 → 提取所有 PDF 下载链接 (2004-2023)
  2. 下载 PDF → PyMuPDF 逐页提取超链接 (每 PDF ~600 条)
  3. 对每个超链接 → requests.get 详情页 → BS4 解析:
     - 元数据区: 索引号/发文单位/文号/分类/发文日期/主题词
     - 正文: .article-content
     - 附件: a[href$=pdf,docx,xlsx,zip,...]
  4. item_from_dict() → StoragePipeline.store()
     - crawler_result (category=policy, type=国家住建部-公开)
     - collection_policy_ext (doc_number, issuing_authority, etc.)
     - KB upload (markdown)
     - AttachmentHandler (附件下载 → 解压 → KB上传)

去重 / 增量:
  crawler_result PK = md5(mohurd_mlxz|detail_page_url), 重复触发 = upsert
  首次运行: date_filter="" 全文回溯
  后续运行: date_filter=today 只采当天, 且仅下载最新年份 PDF
  full_crawl=True: 重下所有 PDF + 全量重爬详情页

调用入口:
  由 unified_crawler.py 的 custom_runner 分支调度
  docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/unified_crawler.py \
    --tenant-id <TID> --kb-id <KID> --task-name mohurd_mlxz \
    --writer collection --category policy \
    --script-args '{"site_id":"mohurd_mlxz"}'
"""

import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# 项目路径初始化（容器内 /ragflow，本地 dev 时手动 cwd）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

# ── 常量 ────────────────────────────────────────────────────────────────
SITE_ID = "mohurd_mlxz"
SITE_NAME = "国家住建部-公开"
SITE_DOMAIN = "www.mohurd.gov.cn"
CATEGORY = "policy"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db217c"

INDEX_URL = "https://www.mohurd.gov.cn/gongkai/mlxz/index.html"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.mohurd.gov.cn/",
}

HTTP_TIMEOUT = 30
DETAIL_DELAY_MIN = 1.0
DETAIL_DELAY_MAX = 2.5

# PDF 元数据字段名 (中文 → 内部 key)
PDF_FIELD_MAP: Dict[str, str] = {
    "索引号": "index_no",
    "主题名称": "topic_category",
    "发文单位": "issuing_authority",
    "成文日期": "publish_date_raw",
    "文号": "doc_number",
    "主题词": "keywords",
    "公文名称": "pdf_title",
    "备注": "remarks",
}

# 详情页正文 CSS 选择器优先级
DETAIL_CONTENT_SELECTORS = (
    ".article-content",
    "#detailCont",
    ".news-content",
    ".detail-content",
    ".text-content",
    ".pages_content",
    "#zoom",
    "article",
    ".content",
    ".TRS_Editor",
)

# 文件扩展名匹配
FILE_EXT_PATTERN = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|"
    r"txt|ofd|wps|et|dps)(\?|$)",
    re.IGNORECASE,
)

# 禁用 SSL 警告 (政府站点常见证书问题)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ── HTTP helpers ────────────────────────────────────────────────────────
def _throttle(min_s: float = 0.5, max_s: float = 1.5) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> requests.Response:
    """GET 请求, 自动 UTF-8 解码, 禁用 SSL 验证。"""
    resp = requests.get(
        url,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        verify=False,
        allow_redirects=True,
    )
    resp.raise_for_status()
    ct = resp.headers.get("Content-Type", "")
    if "charset=" not in ct.lower():
        resp.encoding = "utf-8"
    return resp


def _download_file(url: str, dest_path: str) -> bool:
    """下载文件到本地路径, 返回是否成功。"""
    try:
        resp = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=120,
            verify=False,
            allow_redirects=True,
            stream=True,
        )
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("[mohurd] downloaded %s → %s (%d bytes)",
                     url, dest_path, os.path.getsize(dest_path))
        return True
    except Exception as e:
        logger.warning("[mohurd] download failed: %s: %s", url, e)
        return False


# ── 列表页: 提取 PDF 链接 ───────────────────────────────────────────────
def _fetch_pdf_links() -> List[Dict[str, str]]:
    """从目录下载页提取所有年份 PDF 链接。

    Returns:
        [{year: "2023", title: "2023年", url: "https://..."}, ...]
    """
    resp = _http_get(INDEX_URL)
    soup = BeautifulSoup(resp.text, "lxml")

    pdf_links: List[Dict[str, str]] = []
    seen = set()

    for a in soup.select("a[href]"):
        text = (a.get_text() or "").strip()
        href = (a.get("href") or "").strip()
        if not href or not text:
            continue

        # 匹配 "2023年" 格式
        m = re.match(r"^(\d{4})年?$", text)
        if not m:
            continue

        year = m.group(1)
        abs_url = urljoin(INDEX_URL, href)

        if abs_url in seen:
            continue
        seen.add(abs_url)

        pdf_links.append({
            "year": year,
            "title": text,
            "url": abs_url,
        })

    pdf_links.sort(key=lambda x: x["year"])
    logger.info("[mohurd] found %d PDF links (years %s-%s)",
                 len(pdf_links),
                 pdf_links[0]["year"] if pdf_links else "N/A",
                 pdf_links[-1]["year"] if pdf_links else "N/A")
    return pdf_links


# ── PDF 解析: 提取超链接 ─────────────────────────────────────────────────
# 旧 PDF 条目字段标签及对应的标准键名
_OLD_PDF_LABEL_MAP: List[tuple] = [
    ("索引号", "index_no"),
    ("主题名称", "topic_category"),
    ("发文单位", "issuing_authority"),
    ("发文日期", "publish_date"),
    ("文号", "doc_number"),
    ("文 号", "doc_number"),
    ("主题词", "keywords"),
    ("主 题 词", "keywords"),
    ("公文名称", "pdf_title"),
]

# 编译标签正则: 匹配行首的 "标签名：" 或 "标签名:"
_OLD_PDF_LABEL_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(lbl) for lbl, _ in _OLD_PDF_LABEL_MAP) + r")[：:]"
)


def _parse_old_chunk(chunk: str) -> Dict[str, str]:
    """解析旧 PDF 条目块（2004-2019 格式）。

    旧格式特征：
      索引号: VAL1    主题名称： VAL2
      发文单位： VAL3 发文日期： VAL4
             VAL3续行 (缩进)
      文号： VAL5     主题词： VAL6
      公文名称： VAL7
             VAL7续行 (缩进)

    策略：逐行处理，同行多字段用标签切分，缩进行作为上一字段续行。
    """
    raw_lines = chunk.split("\n")
    meta: Dict[str, str] = {}

    def _split_inline_fields(text: str) -> List[tuple]:
        """在同行业中切分出 [(label_en, value), ...]"""
        results: List[tuple] = []
        pat = r"\s*(主题名称|发文日期|主题词|主 题 词)[：:]"
        parts = re.split(pat, text)
        # parts 格式: [before, label_cn, after, label_cn, after, ...]
        if len(parts) >= 2:
            first_val = parts[0].strip()
            if first_val:
                results.append(("_inline", first_val))
            for j in range(1, len(parts), 2):
                label_cn = parts[j]
                val = parts[j + 1].strip() if j + 1 < len(parts) else ""
                label_en = dict(_OLD_PDF_LABEL_MAP).get(label_cn, label_cn)
                results.append((label_en, val))
        else:
            stripped = text.strip()
            if stripped:
                results.append(("_inline", stripped))
        return results

    # 当前处理的标签
    current_label = "_head"  # 初始值，解析到第一个标签前
    current_value: List[str] = []
    pending_inline: List[tuple] = []  # 同行切分出的待分配片段

    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        # 检查行首是否有已知标签
        m = _OLD_PDF_LABEL_RE.match(raw_line)
        if m:
            # 保存上一标签的值
            if current_label and (current_value or pending_inline):
                all_parts = list(pending_inline) + [("", "".join(current_value).strip())]
                _assign_parts(meta, all_parts, current_label)
            pending_inline = []
            current_value = []

            label_cn = m.group(1)
            current_label = dict(_OLD_PDF_LABEL_MAP).get(label_cn, label_cn)

            # 提取标签后的内容（同行）
            rest = raw_line[m.end():].strip()
            if rest:
                # 检查同行是否还有其他标签
                inline = _split_inline_fields(rest)
                if len(inline) > 1:
                    # 第一个片段属于当前标签
                    if inline[0][0] == "_inline":
                        current_value.append(inline[0][1])
                    pending_inline = inline[1:]
                else:
                    current_value.append(inline[0][1] if inline[0][0] == "_inline" else inline[0][1])
            continue

        # 缩进行（续行）
        if raw_line and raw_line[0] in (" ", "\t", "\u3000"):
            # 检查缩进行中是否包含同行标签
            inline = _split_inline_fields(stripped)
            if len(inline) > 1:
                if inline[0][0] == "_inline":
                    current_value.append(inline[0][1])
                pending_inline.extend(inline[1:])
            else:
                current_value.append(stripped)
            continue

        # 普通行 → 可能是页眉、分类头等，跳过
        if not m and not raw_line[0] in (" ", "\t", "\u3000"):
            # 非缩进行且非标签行 → 可能是分类头，忽略
            pass

    # 保存最后一个标签的值
    if current_label and (current_value or pending_inline):
        all_parts = list(pending_inline) + [("", "".join(current_value).strip())]
        _assign_parts(meta, all_parts, current_label)

    # 日期标准化
    if meta.get("publish_date"):
        date_m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", meta["publish_date"])
        if date_m:
            meta["publish_date"] = date_m.group(1)
        else:
            date_m2 = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", meta["publish_date"])
            if date_m2:
                meta["publish_date"] = f"{date_m2.group(1)}-{int(date_m2.group(2)):02d}-{int(date_m2.group(3)):02d}"

    return meta


def _assign_parts(meta: Dict[str, str], parts: List[tuple], default_label: str):
    """将 [(label_en, value), ...] 片段分配到 meta 字典。_inline 使用 default_label。"""
    for label_en, val in parts:
        if not val:
            continue
        target = default_label if label_en in ("_inline", "") else label_en
        if target in meta and meta[target]:
            meta[target] += val
        else:
            meta[target] = val


def _parse_pdf_text_entries(full_text: str, year: str) -> List[Dict[str, Any]]:
    """从纯文本 PDF 中按分隔线拆分解析条目（旧格式 2004-2019，无超链接）。"""
    chunks = re.split(r"\.{20,}", full_text)
    if len(chunks) <= 2:
        # 早期 PDF (2004-2010) 无点号分隔，改用索引号位置切分
        idx_positions = [m.start() for m in re.finditer(r"索引号\s*[：:]", full_text)]
        if len(idx_positions) > 1:
            chunks = []
            for i, pos in enumerate(idx_positions):
                next_pos = idx_positions[i + 1] if i + 1 < len(idx_positions) else len(full_text)
                chunks.append(full_text[pos:next_pos])

    entries: List[Dict[str, Any]] = []
    seen_index: set = set()

    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 30:
            continue

        meta = _parse_old_chunk(chunk)
        index_no = meta.get("index_no", "")
        if not index_no:
            continue
        if index_no in seen_index:
            continue
        seen_index.add(index_no)

        synthetic_url = f"mohurd://{year}/index/{index_no.replace('/', '_')}"

        entries.append({
            "url": synthetic_url,
            "year": year,
            "no_detail_page": True,
            "topic_category": meta.get("topic_category", ""),
            "index_no": index_no,
            "issuing_authority": meta.get("issuing_authority", ""),
            "publish_date": meta.get("publish_date", ""),
            "doc_number": meta.get("doc_number", ""),
            "keywords": meta.get("keywords", ""),
            "pdf_title": meta.get("pdf_title", ""),
        })

    return entries


def _parse_pdf_hyperlinks(pdf_path: str, year: str) -> List[Dict[str, Any]]:
    """从 PDF 中提取所有超链接及关联元数据。

    2020+ PDF: 有超链接 → 提取 hyperlinks + 元数据
    2004-2019 PDF: 纯文本无超链接 → 按分隔线拆分文本条目

    Returns:
        [{url, year, topic_category, index_no, issuing_authority,
          publish_date, doc_number, keywords, pdf_title, no_detail_page?}, ...]
    """
    import fitz

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    entries: List[Dict[str, Any]] = []
    seen_urls: set = set()

    current_topic = ""  # 当前页面的主题分类 (如 "住房保障")

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text(sort=True)
        links = page.get_links()

        # 解析页面文本获取元数据
        page_meta = _parse_page_meta(text)

        # 更新当前主题
        if page_meta.get("topic_category"):
            current_topic = page_meta["topic_category"]

        # 匹配每个超链接到对应的元数据条目
        for link in links:
            uri = (link.get("uri") or "").strip()
            if not uri or "mohurd.gov.cn" not in uri:
                continue
            if uri in seen_urls:
                continue
            seen_urls.add(uri)

            entry = {
                "url": uri,
                "year": year,
                "no_detail_page": False,
                "topic_category": current_topic or page_meta.get("topic_category", ""),
                "index_no": page_meta.get("index_no", ""),
                "issuing_authority": page_meta.get("issuing_authority", ""),
                "publish_date": page_meta.get("publish_date", ""),
                "doc_number": page_meta.get("doc_number", ""),
                "keywords": page_meta.get("keywords", ""),
                "pdf_title": page_meta.get("pdf_title", ""),
            }
            entries.append(entry)

    # 旧 PDF (2004-2019): 无超链接，用纯文本解析
    if len(entries) == 0 and total_pages > 0:
        full_text_parts: List[str] = []
        for page_num in range(total_pages):
            try:
                full_text_parts.append(doc[page_num].get_text(sort=True))
            except Exception:
                pass
        full_text = "\n".join(full_text_parts)
        entries = _parse_pdf_text_entries(full_text, year)

    doc.close()
    link_or_text = "links" if any(not e.get("no_detail_page") for e in entries) else "text entries"
    logger.info("[mohurd] PDF %s: parsed %d pages, extracted %d %s",
                 year, total_pages, len(entries), link_or_text)
    return entries


def _parse_page_meta(text: str) -> Dict[str, str]:
    """从单页 PDF 文本中提取元数据。

    文本格式示例:
        10BA、住房保障
        索引号：     000013338/2023-00823    主题名称： 住房保障
        发文单位： 住房城乡建设部      成文日期：  2023-08-17
        文号：     建提复字〔2023〕第29号   主题词：
        公文名称： 关于政协第十四届全国委员会...提案答复的函

    注意: 一页可能有 1-4 条记录, 这里只提取第一条的元数据作为该页链接的默认值。
          多个条目在同一页时, 超链接通过位置匹配; 实际精确元数据从详情页获取。
    """
    meta: Dict[str, str] = {}

    # 提取主题分类头: "10BA、住房保障"
    header_m = re.match(r"^[\dA-Z]+[、，,]\s*(.+?)(?:\n|$)", text.strip())
    if header_m:
        meta["topic_category"] = header_m.group(1).strip()

    # 构建字段标签列表用于值边界截断
    _all_labels = list(PDF_FIELD_MAP.keys())
    _label_union = "|".join(re.escape(lbl) for lbl in _all_labels)

    for cn_key, en_key in PDF_FIELD_MAP.items():
        # 模式: "索引号：\n000013338/2023-00823" 或 "索引号：     000013338/2023-00823"
        # 旧 PDF 同行多字段，值截断到下一个字段标签前
        m = re.search(
            re.escape(cn_key) + r"[：:]\s*\n?\s*(\S[^\n]*)",
            text,
        )
        if m:
            val = m.group(1).strip()
            # 截断到下一个字段标签（旧 PDF 同行多字段）
            cutoff = re.search(r"\s+(" + _label_union + r")[：:]", val)
            if cutoff:
                val = val[:cutoff.start()].strip()
            if val:
                meta[en_key] = val

    # 日期标准化
    if "publish_date_raw" in meta:
        date_m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", meta["publish_date_raw"])
        if date_m:
            meta["publish_date"] = date_m.group(1)

    return meta


# ── 详情页抓取 ──────────────────────────────────────────────────────────
def _fetch_detail_page(url: str) -> Dict[str, Any]:
    """抓取详情页, 提取标题/元数据/正文/附件。

    Returns dict with keys:
        title, content, content_html, detail_files,
        index_no, issuing_authority, doc_number, category,
        publish_date, publish_datetime, keywords, expiry_date, effective_date
    """
    result: Dict[str, Any] = {
        "title": "",
        "content": "",
        "content_html": "",
        "detail_files": [],
        "index_no": "",
        "issuing_authority": "",
        "doc_number": "",
        "category": "",
        "publish_date": "",
        "publish_datetime": "",
        "keywords": "",
        "expiry_date": "",
        "effective_date": "",
    }

    try:
        resp = _http_get(url, timeout=HTTP_TIMEOUT)
    except Exception as e:
        logger.warning("[mohurd] detail fetch failed for %s: %s", url, e)
        return result

    html = resp.text
    result["content_html"] = html
    soup = BeautifulSoup(html, "lxml")

    # ── 标题 ──
    # 优先从公文名称区域取
    title_el = soup.select_one(".gongkai-title, .article-title, .detail-title, h1, h2")
    if not title_el:
        # 从元数据区找"公文名称"
        for row in soup.select("li, .info-item, tr"):
            txt = row.get_text(strip=True)
            if "公文名称" in txt:
                title_el = row
                break
    if title_el:
        title = re.sub(r"^.*?公文名称[：:]\s*", "", title_el.get_text(strip=True))
        if len(title) > 4:
            result["title"] = title.strip()
        elif title_el.name in ("h1", "h2"):
            result["title"] = title_el.get_text(strip=True)

    # 如果标题为空, 用 page title
    if not result["title"]:
        t = soup.select_one("title")
        if t:
            result["title"] = (t.get_text() or "").strip()

    # ── 元数据 ──
    # 详情页元数据在 li 元素中: <li>索引号：000013338/...</li>
    info_items: Dict[str, str] = {}
    for li in soup.select("li, .info-item"):
        text = (li.get_text() or "").strip()
        # 匹配 "标签：值" 或 "标签：\n值"
        for label_cn, label_en in [
            ("索引号", "index_no"),
            ("索 引 号", "index_no"),
            ("发文单位", "issuing_authority"),
            ("文号", "doc_number"),
            ("文 号", "doc_number"),
            ("分类", "category"),
            ("分 类", "category"),
            ("发文日期", "publish_date"),
            ("实施日期", "effective_date"),
            ("废止日期", "expiry_date"),
            ("主题词", "keywords"),
            ("主 题 词", "keywords"),
            ("公文名称", "title"),
            ("主题分类", "category"),
        ]:
            m = re.search(re.escape(label_cn) + r"[：:]\s*(.+)", text)
            if m:
                val = m.group(1).strip()
                if val:
                    info_items[label_en] = val

    # 也尝试从非 li 的纯文本中解析
    if not info_items:
        body_text = soup.get_text()
        for label_cn, label_en in [
            ("索引号", "index_no"),
            ("索 引 号", "index_no"),
            ("发文单位", "issuing_authority"),
            ("文号", "doc_number"),
            ("文 号", "doc_number"),
            ("分类", "category"),
            ("分 类", "category"),
            ("发文日期", "publish_date"),
            ("实施日期", "effective_date"),
            ("废止日期", "expiry_date"),
            ("主题词", "keywords"),
            ("主 题 词", "keywords"),
        ]:
            m = re.search(
                re.escape(label_cn) + r"[：:]\s*(\S[^\n]{0,200})",
                body_text,
            )
            if m:
                val = m.group(1).strip()
                if val:
                    info_items[label_en] = val

    # 合并元数据
    for k, v in info_items.items():
        if k == "title" and v and len(v) > len(result["title"]):
            result["title"] = v
        elif k in result and not result[k]:
            result[k] = v

    # 标准化日期
    for date_key in ("publish_date", "effective_date", "expiry_date"):
        val = result.get(date_key, "")
        if val:
            m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", val)
            if m:
                result[date_key] = m.group(1)

    # 保存发布时间 (含时分秒)
    time_m = re.search(
        r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)",
        html,
    )
    if time_m:
        result["publish_datetime"] = time_m.group(1)
    elif result.get("publish_date"):
        result["publish_datetime"] = result["publish_date"]

    # ── 正文 ──
    container = None
    for sel in DETAIL_CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 50:
            container = el
            break
    if container:
        result["content"] = _html_to_text(container)
    else:
        # 兜底: body 文本
        body = soup.find("body")
        if body:
            result["content"] = _html_to_text(body)

    # ── 附件 ──
    seen_files: set = set()
    # 常见附件选择器
    for a in soup.select(
        ".article-attachment a[href], "
        ".article_attachment a[href], "
        ".fujian a[href], "
        ".file-list a[href]"
    ):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen_files:
            continue
        path_lower = urlparse(abs_url).path.lower()
        if not FILE_EXT_PATTERN.search(path_lower):
            continue
        name = (a.get_text() or "").strip() or urlparse(abs_url).path.rsplit("/", 1)[-1]
        result["detail_files"].append({"file_name": name, "file_url": abs_url})
        seen_files.add(abs_url)

    # 扫正文中链接作为附件兜底
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>',
        html,
        re.IGNORECASE,
    ):
        link_url = m.group(1).strip()
        link_text = m.group(2).strip() or urlparse(link_url).path.rsplit("/", 1)[-1]
        abs_url = urljoin(url, link_url)
        if abs_url in seen_files:
            continue
        path_lower = urlparse(abs_url).path.lower()
        if FILE_EXT_PATTERN.search(path_lower):
            result["detail_files"].append({"file_name": link_text, "file_url": abs_url})
            seen_files.add(abs_url)

    return result


def _html_to_text(container) -> str:
    """HTML 容器转纯文本 (段落用空行分隔)。"""
    text = container.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n\n".join(lines)


# ── item 构建 ───────────────────────────────────────────────────────────
def _build_item_dict_no_detail(pdf_entry: Dict[str, Any]) -> Dict[str, Any]:
    """仅从 PDF 元数据构建 item dict（旧格式 2004-2019，无详情页）。"""
    publish_date = pdf_entry.get("publish_date", "")
    year = pdf_entry.get("year", "")
    title = pdf_entry.get("pdf_title", "")
    doc_number = pdf_entry.get("doc_number", "")
    index_no = pdf_entry.get("index_no", "")
    issuing_authority = pdf_entry.get("issuing_authority", "")
    topic_category = pdf_entry.get("topic_category", "")

    # 从 PDF 元数据拼装正文
    content_parts = []
    if title:
        content_parts.append(f"公文名称：{title}")
    if index_no:
        content_parts.append(f"索引号：{index_no}")
    if issuing_authority:
        content_parts.append(f"发文单位：{issuing_authority}")
    if publish_date:
        content_parts.append(f"发文日期：{publish_date}")
    if doc_number:
        content_parts.append(f"文号：{doc_number}")
    if topic_category:
        content_parts.append(f"主题分类：{topic_category}")
    content = "\n".join(content_parts)

    url = pdf_entry.get("url", "")
    path = urlparse(url).path if url else index_no.replace("/", "_")
    item_id_raw = path.rsplit("/", 1)[-1] or path
    if "." in item_id_raw:
        item_id_raw = item_id_raw.rsplit(".", 1)[0]

    return {
        "id": item_id_raw,
        "uuid": item_id_raw,
        "title": title,
        "url": url,
        "source_url": url,
        "date": publish_date,
        "publishDate": publish_date,
        "publish_date": publish_date,
        "publish_datetime": publish_date,
        "content": content,
        "content_html": f"<pre>{content}</pre>",
        "section": year,
        "section_label": f"{year}年",
        "section_name": "国家住建部-公开",
        "news_type": "国家住建部-公开",
        "source_site": SITE_NAME,
        "issuing_authority": issuing_authority or "住房和城乡建设部",
        "doc_number": doc_number,
        "topic_category": topic_category,
        "keywords": pdf_entry.get("keywords", ""),
        "index_no": index_no,
        "authority_level": "",
        "effective_date": "",
        "expiry_date": "",
        "status": "",
        "legal_basis": "",
        "files": [],
        "year": year,
        "source_pdf": f"住建部{year}年信息公开目录",
    }


def _build_item_dict(
    pdf_entry: Dict[str, Any],
    detail: Dict[str, Any],
) -> Dict[str, Any]:
    """合并 PDF 元数据 + 详情页抓取结果, 构建 StoragePipeline 期望的 item dict。"""
    url = pdf_entry["url"]
    title = detail.get("title") or pdf_entry.get("pdf_title") or "Untitled"

    # 发布日期: 详情页优先, PDF 兜底
    publish_date = (
        detail.get("publish_date")
        or pdf_entry.get("publish_date")
        or ""
    )[:10]

    # 文件列表
    files = detail.get("detail_files", [])

    # 正文
    content = detail.get("content", "")

    # 元数据
    index_no = detail.get("index_no") or pdf_entry.get("index_no", "")
    issuing_authority = detail.get("issuing_authority") or pdf_entry.get("issuing_authority", "")
    doc_number = detail.get("doc_number") or pdf_entry.get("doc_number", "")
    topic_category = detail.get("category") or pdf_entry.get("topic_category", "")
    keywords = detail.get("keywords") or pdf_entry.get("keywords", "")

    # item_id: URL 末段
    path = urlparse(url).path
    item_id_raw = path.rsplit("/", 1)[-1] or path
    # 去扩展名
    if "." in item_id_raw:
        item_id_raw = item_id_raw.rsplit(".", 1)[0]

    return {
        "id": item_id_raw,
        "uuid": item_id_raw,
        "title": title,
        "url": url,
        "source_url": url,
        "date": publish_date,
        "publishDate": publish_date,
        "publish_date": publish_date,
        "publish_datetime": detail.get("publish_datetime") or publish_date,
        "content": content,
        "content_html": detail.get("content_html", ""),
        "section": pdf_entry.get("year", ""),
        "section_label": f"{pdf_entry.get('year', '')}年",
        "section_name": "国家住建部-公开",
        "news_type": "国家住建部-公开",
        "source_site": SITE_NAME,
        "issuing_authority": issuing_authority or "住房和城乡建设部",
        "doc_number": doc_number,
        "topic_category": topic_category,
        "keywords": keywords,
        "index_no": index_no,
        "authority_level": "",
        "effective_date": detail.get("effective_date", ""),
        "expiry_date": detail.get("expiry_date", ""),
        "status": "",
        "legal_basis": "",
        "files": files,
        "year": pdf_entry.get("year", ""),
        "source_pdf": f"住建部{pdf_entry.get('year', '')}年信息公开目录",
    }


# ── 主入口 ──────────────────────────────────────────────────────────────
def run(
    tenant_id: str,
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = "policy",
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config=None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """custom_runner 入口。"""
    logging.info(
        "[mohurd] start: tenant=%s kb=%s task=%s date_filter=%r full=%s",
        tenant_id, kb_id, task_name, date_filter, full_crawl,
    )

    stats: Dict[str, Any] = {
        "status": "running",
        "scanned_pages": 0,
        "scanned_items": 0,
        "items_found": 0,
        "items_new": 0,
        "items_updated": 0,
        "kb_uploaded": 0,
        "attachments_uploaded": 0,
        "errors": [],
    }
    errors = stats["errors"]

    if not output_dir:
        output_dir = os.path.join(_PROJECT_ROOT, "rag", "mohurd_mlxz_output")
    os.makedirs(output_dir, exist_ok=True)

    kb_id = kb_id or KB_ID_DEFAULT

    # ── Phase 1: 下载 PDF ──────────────────────────────────────────────
    pdf_links = _fetch_pdf_links()
    stats["scanned_pages"] = len(pdf_links)

    if not pdf_links:
        stats["status"] = "success"
        logger.warning("[mohurd] no PDF links found on index page")
        print("[CRAWLER] Done: 0 new items, 0 updated, 0 kb uploaded", flush=True)
        return stats

    # 确定要处理的年份
    years_to_process: List[Dict[str, str]] = []
    if full_crawl:
        years_to_process = pdf_links
    elif date_filter:
        # 增量模式: 只处理最新年份
        years_to_process = [pdf_links[-1]]
        logger.info("[mohurd] date_filter mode: only process year %s",
                     pdf_links[-1]["year"])
    else:
        # 首次全量
        years_to_process = pdf_links

    # 下载并解析所有 PDF
    all_entries: List[Dict[str, Any]] = []
    pdf_dir = os.path.join(output_dir, "pdfs")
    os.makedirs(pdf_dir, exist_ok=True)

    for pdf_info in years_to_process:
        year = pdf_info["year"]
        pdf_path = os.path.join(pdf_dir, f"{year}.pdf")

        # 跳过已下载的 PDF
        need_download = True
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            logger.info("[mohurd] PDF %s already downloaded (%d bytes), skip",
                         year, os.path.getsize(pdf_path))
            need_download = False

        if need_download:
            _throttle(1.0, 3.0)
            if not _download_file(pdf_info["url"], pdf_path):
                errors.append(f"download PDF {year}: failed")
                continue

        # 解析 PDF 提取超链接
        try:
            entries = _parse_pdf_hyperlinks(pdf_path, year)
            all_entries.extend(entries)
            logger.info("[mohurd] year %s: %d entries extracted", year, len(entries))
        except Exception as e:
            logger.exception("[mohurd] PDF %s parse failed: %s", year, e)
            errors.append(f"parse PDF {year}: {e}")
            # 继续处理其他年份

    stats["items_found"] = len(all_entries)

    if not all_entries:
        stats["status"] = "success" if not errors else "error"
        print("[CRAWLER] Done: 0 new items, 0 updated, 0 kb uploaded", flush=True)
        return stats

    # ── Phase 2: 初始化存储管道 ─────────────────────────────────────────
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline
    pipeline = StoragePipeline(
        kb_id=kb_id,
        tenant_id=tenant_id,
        parser_id="naive",
        site_id=SITE_ID,
        task_name=task_name or SITE_ID,
        output_dir=output_dir,
        writer_mode="collection",
        category=category or CATEGORY,
        task_id=task_id,
        date_filter="",  # MOHURD PDF 是年度归档，条目日期分布在全年，不过滤
        site_display=f"{SITE_NAME} {SITE_DOMAIN}",
    )

    # ── Phase 3: 逐条抓取详情页并存储 ───────────────────────────────────
    processed = 0
    for idx, entry in enumerate(all_entries, 1):
        try:
            if entry.get("no_detail_page"):
                # 旧 PDF 无详情页，直接用 PDF 元数据构建 item
                item_dict = _build_item_dict_no_detail(entry)
            else:
                # 抓取详情页
                _throttle(DETAIL_DELAY_MIN, DETAIL_DELAY_MAX)
                detail = _fetch_detail_page(entry["url"])
                item_dict = _build_item_dict(entry, detail)
            normalized = item_from_dict(
                item_dict,
                site_id=SITE_ID,
                section=entry.get("year", ""),
            )

            # 存储到 DB + KB
            store_result = pipeline.store(normalized)
            if store_result.get("project_id"):
                processed += 1

            stats["scanned_items"] += 1

            if idx % 50 == 0:
                logger.info("[mohurd] progress %d/%d (stored=%d)",
                             idx, len(all_entries), processed)

        except Exception as e:
            logger.exception("[mohurd] entry %d failed: %s", idx, e)
            errors.append(f"entry#{idx} ({entry.get('url', '')[:80]}): {e}")

    # ── Phase 4: 汇总 ───────────────────────────────────────────────────
    try:
        pipeline.cleanup()
    except Exception:
        pass

    cw_stats = pipeline._collection_writer.stats if pipeline._collection_writer else {}
    pl_stats = pipeline.stats
    stats["items_new"] = cw_stats.get("results_new", 0)
    stats["items_updated"] = cw_stats.get("results_updated", 0)
    stats["kb_uploaded"] = pl_stats.get("kb_uploaded", 0)
    stats["attachments_uploaded"] = pl_stats.get("attachments_uploaded", 0)
    stats["status"] = "success" if not errors else "success_with_errors"

    logger.info(
        "[mohurd] DONE: entries=%d processed=%d new=%d updated=%d kb=%d errors=%d",
        len(all_entries), processed,
        stats["items_new"], stats["items_updated"],
        stats["kb_uploaded"], len(errors),
    )

    print(
        f"[CRAWLER] Done: {stats['items_new']} new items, "
        f"{stats['items_updated']} updated, {stats['kb_uploaded']} kb uploaded",
        flush=True,
    )
    return stats


# ── CLI 直跑 (debug 用) ────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import argparse
    p = argparse.ArgumentParser(description="MOHURD 目录下载 爬虫")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", default=KB_ID_DEFAULT)
    p.add_argument("--task-name", default="manual-mohurd-mlxz")
    p.add_argument("--task-id", default="")
    p.add_argument("--date-filter", default="")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full-crawl", action="store_true",
                   help="Re-download all PDFs and crawl all entries")
    p.add_argument("--force-run", action="store_true")
    args = p.parse_args()

    summary = run(
        tenant_id=args.tenant_id,
        kb_id=args.kb_id,
        task_name=args.task_name,
        task_id=args.task_id,
        date_filter=args.date_filter,
        output_dir=args.output_dir,
        full_crawl=args.full_crawl,
        force_run=args.force_run,
    )
    sys.exit(0 if summary.get("status") in ("success", "success_with_errors") else 1)
