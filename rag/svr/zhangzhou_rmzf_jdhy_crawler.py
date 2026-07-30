"""
zhangzhou_rmzf_jdhy_crawler — 漳州市人民政府·解读回应 智能采集 runner
=====================================================================

数据源: https://www.zhangzhou.gov.cn/cms/html/zzsrmzf/jdhy1/index.html
覆盖栏目 (9 个):
    本市政策解读、图说图解、媒体解读、广播解读、动漫及H5解读、
    视频解读、会议解读、新闻发布会、回应关切

数据流:
    1. requests.get 每个栏目列表页 (静态 HTML / TRS CMS sitemanage 分页)
    2. BS4 解析列表项 → title, url, date
    3. 对每项 fetch 详情页 → 标题/正文/附件
    4. item_from_dict() → StoragePipeline.store() → writer + KB + attachments

去重 / 增量:
    crawler_result PK = md5(site_id|source_url), 重复触发 = upsert
    date_filter=today 时只保留 item.date == today 的条目

调用入口:
    由 unified_crawler.py 的 custom_runner 分支调度
"""
import hashlib
import logging
import os
import random
import re
import sys
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── 项目路径初始化 ────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

# ── 常量 ──────────────────────────────────────────────────────────────────
SITE_ID = "zhangzhou_rmzf_jdhy"
SITE_NAME = "漳州市人民政府-解读回应"
SITE_DOMAIN = "www.zhangzhou.gov.cn"
CATEGORY = "漳州市人民政府-解读回应"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"
NEWS_TYPE = "漳州市人民政府-解读回应"
SOURCE_NAME = "漳州市人民政府"

BASE_URL = "https://www.zhangzhou.gov.cn"
CMS_BASE = f"{BASE_URL}/cms/html/zzsrmzf"

# ── 9 栏目配置 ───────────────────────────────────────────────────────────
SECTIONS: List[Dict[str, Any]] = [
    {"label": "bszcjd",   "name": "本市政策解读", "url": f"{CMS_BASE}/bszcjd/index.html"},
    {"label": "tstj",     "name": "图说图解",     "url": f"{CMS_BASE}/tstj/index.html"},
    {"label": "jdbd",     "name": "媒体解读",     "url": f"{CMS_BASE}/jdbd/index.html"},
    {"label": "gbjd",     "name": "广播解读",     "url": f"{CMS_BASE}/gbjd/index.html"},
    {"label": "dmjh5jd",  "name": "动漫及H5解读", "url": f"{CMS_BASE}/dmjh5jd/index.html"},
    {"label": "spjd",     "name": "视频解读",     "url": f"{CMS_BASE}/spjd/index.html"},
    {"label": "hytj",     "name": "会议解读",     "url": f"{CMS_BASE}/hytj/index.html"},
    {"label": "xwfbh",    "name": "新闻发布会",   "url": f"{CMS_BASE}/xwfbh/index.html"},
    {"label": "hygq",     "name": "回应关切",     "url": f"{CMS_BASE}/hygq/index.html"},
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{CMS_BASE}/jdhy1/index.html",
}

HTTP_TIMEOUT = 30
LIST_DELAY_MIN = 0.3
LIST_DELAY_MAX = 0.8
DETAIL_DELAY_MIN = 0.5
DETAIL_DELAY_MAX = 1.5
MAX_RETRIES = 3

# 详情页正文 CSS 选择器优先级
DETAIL_CONTENT_SELECTORS = [
    "#Content",
    "#zoom",
    ".TRS_Editor",
    ".article_content",
    ".news-content",
    ".detail-content",
    ".text-content",
    ".pages_content",
    ".Custom_UnionStyle",
    "article",
    ".content",
]

# 文件扩展名
FILE_EXT_PATTERN = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|"
    r"txt|ofd|wps|et|dps|jpg|jpeg|png|gif|bmp)(\?|$)",
    re.IGNORECASE,
)

# ── SSL ────────────────────────────────────────────────────────────────────
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# HTTP helpers
# ═══════════════════════════════════════════════════════════════════════════

def _throttle(min_s: float = 0.5, max_s: float = 1.5) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> requests.Response:
    """GET 请求, 自动 UTF-8 解码, 禁用 SSL 验证, 带重试."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
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
        except requests.RequestException as e:
            last_exc = e
            logger.warning("[jdhy] HTTP GET attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(1, 3))
    raise last_exc  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════
# 列表页解析
# ═══════════════════════════════════════════════════════════════════════════

def _extract_site_id(html: str) -> Optional[str]:
    """从列表页 HTML 提取 siteId (用于分页)."""
    m = re.search(r'siteId=(\d+)', html)
    if m:
        return m.group(1)
    return None


def _parse_listing_html(html: str, base_url: str) -> Tuple[List[Dict[str, Any]], int]:
    """解析 TRS CMS 列表页 HTML.

    Returns:
        items: [{title, url, date}, ...]
        total_pages: int (从分页提取)
    """
    soup = BeautifulSoup(html, "lxml")
    items: List[Dict[str, Any]] = []
    seen_urls: set = set()

    # 文章 URL 特征:
    #   类型 A: /YYYY-MM-DD/XXXXXXXXX.html (标准文章, 权重 100)
    #   类型 B: /publicInfo.shtml?id=...      (信息公开页, 权重 50)
    DATE_HREF_RE = re.compile(r"/\d{4}-\d{2}-\d{2}/")
    PUBLIC_INFO_RE = re.compile(r"/publicInfo\.shtml")
    list_candidates = soup.select("ul, .list, [class*='list']")
    best_ul = None
    best_score = 0
    for ul in list_candidates:
        article_links = [
            a for a in ul.select("li a[href]")
            if not a.get("href", "").startswith(("javascript:", "#"))
        ]
        if not article_links:
            continue
        date_count = sum(1 for a in article_links if DATE_HREF_RE.search(a.get("href", "")))
        pi_count = sum(1 for a in article_links if PUBLIC_INFO_RE.search(a.get("href", "")))
        score = date_count * 100 + pi_count * 50 + len(article_links)
        if score > best_score:
            best_score = score
            best_ul = ul

    if not best_ul:
        # 兜底: 取 body 中所有看起来像列表项的链接
        logger.debug("[jdhy] no list container found, trying generic extraction")
        all_links = soup.select("a[href*='html']")
        for a in all_links:
            href = (a.get("href") or "").strip()
            title = (a.get_text() or "").strip()
            if len(title) < 4 or not href:
                continue
            abs_url = urljoin(base_url, href)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)
            date_str = _extract_date_near_element(a)
            items.append({
                "title": title,
                "url": abs_url,
                "date": date_str,
            })
    else:
        for li in best_ul.select("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            title = (a.get("title") or a.get_text() or "").strip()
            if not title or len(title) < 4:
                continue
            href = (a.get("href") or "").strip()
            if href.startswith(("javascript:", "#", "mailto:")):
                continue
            abs_url = urljoin(base_url, href)
            if SITE_DOMAIN not in urlparse(abs_url).netloc.lower():
                continue
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)
            date_str = _extract_date_near_element(a)
            items.append({
                "title": title,
                "url": abs_url,
                "date": date_str,
            })

    # 提取总页数
    total_pages = 1
    page_text = soup.get_text()
    m = re.search(r'共\s*(\d+)\s*页', page_text)
    if m:
        total_pages = int(m.group(1))
    else:
        page_nums = set()
        for a in soup.select("a[href*='page=']"):
            pm = re.search(r'page=(\d+)', a.get("href", ""))
            if pm:
                page_nums.add(int(pm.group(1)))
        if page_nums:
            total_pages = max(page_nums)

    return items, total_pages


def _extract_date_near_element(el) -> str:
    """从元素附近的文本提取日期."""
    parent = el.parent
    if parent:
        for span in parent.select("span, em, .date, [class*='time'], [class*='date']"):
            t = (span.get_text() or "").strip()
            m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", t)
            if m:
                return m.group(1).replace("/", "-")[:10]
    if parent:
        for sib in parent.children:
            if hasattr(sib, "get_text"):
                t = sib.get_text().strip()
                m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", t)
                if m:
                    return m.group(1).replace("/", "-")[:10]
    return ""


def _crawl_listing(url: str, site_id: Optional[str], page: int) -> str:
    """获取指定页的列表 HTML."""
    if page == 1:
        resp = _http_get(url)
        return resp.text
    if not site_id:
        raise ValueError(f"Cannot paginate without siteId for {url}")
    pag_url = f"{BASE_URL}/cms/sitemanage/index.shtml?siteId={site_id}&page={page}"
    resp = _http_get(pag_url)
    return resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 详情页解析
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_detail(url: str) -> Optional[Dict[str, Any]]:
    """抓取详情页并提取结构化数据.

    Returns:
        {
            "title": str,
            "publish_date": "YYYY-MM-DD",
            "publish_datetime": "YYYY-MM-DD HH:MM",
            "source": str,
            "content": str (plain text),
            "content_html": str,
            "files": [{file_name, file_url}, ...],
        }
    """
    try:
        resp = _http_get(url)
    except Exception as e:
        logger.warning("[jdhy] detail page fetch failed: %s: %s", url, e)
        return None

    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    # ── 标题 ──────────────────────────────────────────────────────────
    title = ""
    # 标准文章页: content-title 区域
    title_el = soup.select_one(".content-title")
    if title_el:
        t = title_el.get_text(strip=True)
        if len(t) > 2:
            title = t
    if not title:
        for sel in ["h1", ".article-title", ".xl_tit", ".news-title", "h2"]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if len(t) > 2:
                    title = t
                    break
    if not title:
        page_title = soup.select_one("title")
        if page_title:
            raw = page_title.get_text(strip=True)
            # 移除末尾的栏目名后缀
            raw = re.sub(r"\s*[-—_]\s*(漳州市人民政府|图说图解|媒体解读|广播解读"
                         r"|动漫及H5解读|视频解读|会议解读|会议图解|新闻发布会"
                         r"|回应关切|本市政策解读|解读回应).*$", "", raw)
            title = raw.strip()

    # ── 发布日期/时间 ──────────────────────────────────────────────────
    publish_date = ""
    publish_datetime = ""
    page_text = soup.get_text()
    # 优先匹配 "发布时间：YYYY-MM-DD HH:MM" 格式
    m = re.search(r"发布时间[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*(\d{1,2}:\d{2})?", page_text[:3000])
    if m:
        publish_date = m.group(1).replace("/", "-")[:10]
        if m.group(2):
            publish_datetime = f"{publish_date} {m.group(2)}"
        else:
            publish_datetime = publish_date
    else:
        for pat in [
            r"日期[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
        ]:
            m = re.search(pat, page_text[:3000])
            if m:
                publish_date = m.group(1).replace("/", "-")[:10]
                publish_datetime = publish_date
                break

    # ── 来源 ──────────────────────────────────────────────────────────
    source = ""
    m = re.search(r"来源[：:]\s*(.+?)(?:\s|$|浏|日|发|【|<)", page_text[:2000])
    if m:
        source = m.group(1).strip()

    # ── 正文 ──────────────────────────────────────────────────────────
    content = ""
    content_html = ""
    content_images: List[Dict[str, str]] = []

    # 先检查 #Content 区域
    content_el = soup.select_one("#Content")
    img_count = len(content_el.select("img")) if content_el else 0
    text_len = len(content_el.get_text(strip=True)) if content_el else 0

    # 图说图解类: #Content 有图片但文字短 → 图片型页面
    if content_el and img_count > 0 and text_len < 80:
        for img in content_el.select("img"):
            src = (img.get("src") or "").strip()
            if src and not src.startswith("data:"):
                abs_src = urljoin(url, src)
                alt = (img.get("alt") or "").strip() or title or "图片"
                content_images.append({
                    "file_name": f"{alt}.jpg" if not alt.endswith((".jpg", ".png", ".gif")) else alt,
                    "file_url": abs_src,
                })
        content = f"[图说图解] {title}"
        content_html = str(content_el)
    else:
        # 标准文本型: 遍历选择器
        for sel in DETAIL_CONTENT_SELECTORS:
            el = soup.select_one(sel)
            if el and len(el.get_text(strip=True)) > 50:
                content_html = str(el)
                content = _html_to_text(el)
                break

    # 兜底: 找最大的文本块
    if not content or len(content) < 50:
        # 图片型页面已处理过，不走兜底
        if not content_images:
            for remove_sel in ["script", "style", "nav", "header", "footer",
                               ".header", ".footer", ".nav", ".top", ".bottom"]:
                for el in soup.select(remove_sel):
                    el.decompose()
            candidates = []
            for div in soup.find_all(["div", "article", "section"]):
                t = div.get_text(strip=True)
                if len(t) > 100:
                    candidates.append((len(t), div))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                best = candidates[0][1]
                content_html = str(best)
                content = _html_to_text(best)

    # ── 标准页面的图片提取 (#Content 中图片也加为附件) ───────────
    if content_el and not content_images:
        for img in content_el.select("img"):
            src = (img.get("src") or "").strip()
            if src and not src.startswith("data:"):
                abs_src = urljoin(url, src)
                alt = (img.get("alt") or "").strip() or title or "图片"
                content_images.append({
                    "file_name": f"{alt}.jpg" if not alt.endswith((".jpg", ".png", ".gif")) else alt,
                    "file_url": abs_src,
                })

    # ── 附件 ──────────────────────────────────────────────────────────
    files = _extract_files(soup, url)

    # 合并图片到附件列表
    seen_file_urls = {f["file_url"] for f in files}
    for img_file in content_images:
        if img_file["file_url"] not in seen_file_urls:
            files.append(img_file)

    return {
        "title": title or "Untitled",
        "publish_date": publish_date,
        "publish_datetime": publish_datetime or publish_date,
        "source": source or SOURCE_NAME,
        "content": content,
        "content_html": content_html,
        "files": files,
    }


def _extract_files(soup: BeautifulSoup, detail_url: str) -> List[Dict[str, str]]:
    """从详情页提取附件列表."""
    files: List[Dict[str, str]] = []
    seen: set = set()

    # 策略 1: 附件专用容器
    attachment_selectors = [
        ".article_attachment", ".attachment", ".fujian", "[class*='attach']",
        "[class*='file']", "[class*='fj']",
    ]
    for sel in attachment_selectors:
        for a in soup.select(f"{sel} a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            abs_url = urljoin(detail_url, href)
            if abs_url in seen:
                continue
            path_lower = urlparse(abs_url).path.lower()
            if FILE_EXT_PATTERN.search(path_lower):
                name = (a.get_text() or "").strip() or path_lower.rsplit("/", 1)[-1]
                files.append({"file_name": name, "file_url": abs_url})
                seen.add(abs_url)

    # 策略 2: 正文中所有指向文件的链接
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(detail_url, href)
        if abs_url in seen:
            continue
        path_lower = urlparse(abs_url).path.lower()
        if FILE_EXT_PATTERN.search(path_lower):
            name = (a.get_text() or "").strip() or path_lower.rsplit("/", 1)[-1]
            files.append({"file_name": name, "file_url": abs_url})
            seen.add(abs_url)

    return files


def _html_to_text(container) -> str:
    """HTML 容器转纯文本."""
    text = container.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Item 构建
# ═══════════════════════════════════════════════════════════════════════════

def _build_item_dict(
    listing_item: Dict[str, Any],
    detail: Dict[str, Any],
    section_name: str,
    section_label: str,
) -> Dict[str, Any]:
    """构建 StoragePipeline 期望的 item dict."""
    url = listing_item["url"]
    title = detail.get("title") or listing_item.get("title") or "Untitled"
    publish_date = detail.get("publish_date") or listing_item.get("date") or ""
    content = detail.get("content", "")
    content_html = detail.get("content_html", "")
    files = detail.get("files", [])
    source = detail.get("source", "")

    item_id = hashlib.md5(f"{SITE_ID}|{url}".encode()).hexdigest()

    return {
        "id": item_id,
        "uuid": item_id,
        "title": title,
        "url": url,
        "source_url": url,
        "date": publish_date,
        "publishDate": publish_date,
        "publish_datetime": detail.get("publish_datetime", publish_date),
        "content": content,
        "content_html": content_html,
        "section": section_label,
        "section_label": section_name,
        "section_name": section_name,
        "news_type": NEWS_TYPE,
        "source_site": SITE_NAME,
        "source": source,
        "issuing_authority": SOURCE_NAME,
        "doc_number": "",
        "topic_category": section_name,
        "authority_level": "",
        "effective_date": "",
        "expiry_date": "",
        "status": "",
        "legal_basis": "",
        "files": files,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════

def run(
    tenant_id: str,
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = "news",
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config=None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """custom_runner 入口."""
    # 初始化 RAGFlow settings (STORAGE_IMPL 等)
    from common import settings as crawler_settings
    crawler_settings.init_settings()

    logging.info(
        "[jdhy] start: tenant=%s kb=%s task=%s date_filter=%r full=%s",
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
    errors: List[str] = stats["errors"]

    if not output_dir:
        output_dir = os.path.join(_PROJECT_ROOT, "rag", "jdhy_output")
    os.makedirs(output_dir, exist_ok=True)

    kb_id = kb_id or KB_ID_DEFAULT

    # ── 解析 date_filter ──────────────────────────────────────────────
    target_date = ""
    if date_filter and date_filter.lower() != "today":
        target_date = date_filter[:10]
    elif date_filter and date_filter.lower() == "today":
        target_date = date.today().isoformat()

    # ── Phase 1: 爬取所有栏目列表项 ─────────────────────────────────
    all_items: List[Dict[str, Any]] = []

    for sec in SECTIONS:
        section_label = sec["label"]
        section_name = sec["name"]
        section_url = sec["url"]

        logger.info("[jdhy] Listing: %s (%s)", section_name, section_url)

        try:
            _throttle(LIST_DELAY_MIN, LIST_DELAY_MAX)
            html = _http_get(section_url).text
            site_id = _extract_site_id(html)
            items, total_pages = _parse_listing_html(html, section_url)
            stats["scanned_pages"] += 1

            logger.info("[jdhy]   %s: page 1/%d, got %d items (siteId=%s)",
                         section_name, total_pages, len(items), site_id)

            for item in items:
                item["section_label"] = section_label
                item["section_name"] = section_name
            all_items.extend(items)

            # 翻页 (增量模式只爬第1页)
            if total_pages > 1 and not date_filter:
                for page_idx in range(2, total_pages + 1):
                    _throttle(LIST_DELAY_MIN, LIST_DELAY_MAX)
                    try:
                        page_html = _crawl_listing(section_url, site_id, page_idx)
                        page_items, _ = _parse_listing_html(page_html, section_url)
                        for item in page_items:
                            item["section_label"] = section_label
                            item["section_name"] = section_name
                        all_items.extend(page_items)
                        stats["scanned_pages"] += 1
                        if page_idx % 10 == 0:
                            logger.info("[jdhy]   %s: page %d/%d, total_items=%d",
                                         section_name, page_idx, total_pages, len(all_items))
                    except Exception as e:
                        logger.warning("[jdhy]   %s page %d failed: %s", section_name, page_idx, e)
                        errors.append(f"list:{section_label}:p{page_idx}: {e}")

        except Exception as e:
            logger.exception("[jdhy]   %s listing failed: %s", section_name, e)
            errors.append(f"list:{section_label}: {e}")
            continue

    stats["items_found"] = len(all_items)
    logger.info("[jdhy] Total listing items: %d (from %d pages)", len(all_items), stats["scanned_pages"])

    if not all_items:
        stats["status"] = "success"
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
        date_filter=date_filter,
        site_display=f"{SITE_NAME} {SITE_DOMAIN}",
    )

    # ── Phase 3: 逐条抓取详情页并存储 ───────────────────────────────────
    processed = 0
    skipped_date = 0
    skipped_detail = 0

    for idx, item in enumerate(all_items, 1):
        try:
            # 增量模式下按日期过滤
            if target_date and item.get("date") != target_date:
                skipped_date += 1
                continue

            # 抓取详情页
            _throttle(DETAIL_DELAY_MIN, DETAIL_DELAY_MAX)
            detail = _fetch_detail(item["url"])

            if detail is None:
                skipped_detail += 1
                logger.warning("[jdhy] detail fetch failed for: %s", item["url"][:120])
                continue

            # 如果详情页有更准确的日期, 在增量模式下再检查一次
            detail_date = detail.get("publish_date", "")
            if target_date and detail_date and detail_date != target_date:
                skipped_date += 1
                continue

            # 构建 item dict
            item_dict = _build_item_dict(
                item, detail,
                section_name=item.get("section_name", ""),
                section_label=item.get("section_label", ""),
            )

            # 归一化并存储
            normalized = item_from_dict(
                item_dict,
                site_id=SITE_ID,
                section=item.get("section_label", ""),
            )

            store_result = pipeline.store(normalized)
            if store_result.get("project_id"):
                processed += 1

            stats["scanned_items"] += 1

            if idx % 50 == 0:
                logger.info(
                    "[jdhy] progress %d/%d (stored=%d, skipped_date=%d, skipped_detail=%d)",
                    idx, len(all_items), processed, skipped_date, skipped_detail,
                )

        except Exception as e:
            logger.exception("[jdhy] item %d failed: %s", idx, e)
            errors.append(f"item#{idx} ({item.get('url', '')[:80]}): {e}")

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
        "[jdhy] DONE: items=%d processed=%d new=%d updated=%d kb=%d "
        "skipped_date=%d skipped_detail=%d errors=%d",
        len(all_items), processed,
        stats["items_new"], stats["items_updated"],
        stats["kb_uploaded"],
        skipped_date, skipped_detail, len(errors),
    )

    print(
        f"[CRAWLER] Done: {stats['items_new']} new items, "
        f"{stats['items_updated']} updated, {stats['kb_uploaded']} kb uploaded",
        flush=True,
    )
    return stats


# ── CLI 直跑 (debug 用) ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from common import settings as cli_settings
    cli_settings.init_settings()

    import argparse
    p = argparse.ArgumentParser(description="漳州市人民政府 解读回应 爬虫")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", default=KB_ID_DEFAULT)
    p.add_argument("--task-name", default="zhangzhou_rmzf_jdhy")
    p.add_argument("--task-id", default="")
    p.add_argument("--category", default=CATEGORY)
    p.add_argument("--date-filter", default="")
    p.add_argument("--full-crawl", action="store_true")
    p.add_argument("--output-dir", default="")
    args = p.parse_args()
    summary = run(
        tenant_id=args.tenant_id,
        kb_id=args.kb_id,
        task_name=args.task_name,
        task_id=args.task_id,
        category=args.category,
        date_filter=args.date_filter,
        full_crawl=args.full_crawl,
        output_dir=args.output_dir or "",
    )
    print("\nSummary:", summary)
