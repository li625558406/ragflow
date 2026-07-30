"""
ndrc_xxgk_crawler — 中华人民共和国国家发展和改革委员会·政务公开 智能采集 runner
================================================================================

数据源: https://www.ndrc.gov.cn/xxgk/
覆盖栏目 (9 个):
  发展改革委令、规范性文件、规划文本、公告、通知、
  解读、政策图解、全国人大代表建议复文公开、全国政协委员提案复文公开

数据流:
  1. requests.get 列表页 → BS4 解析 ul.u-list > li
  2. 翻页: index_N.html 模式 (page 1 = 目录, page N = index_{N-1}.html)
  3. 对每条目 → requests.get 详情页 → BS4 解析:
     - 标题: .article_l h2.article_title / .article_ri h2
     - 日期: .shezhi .time / .article_ri p
     - 来源: .shezhi .ly.laiyuantext
     - 正文: .article_con
     - 附件: .attachment_r a[href]
  4. item_from_dict() → StoragePipeline.store()
     - crawler_result (category=policy, news_type=国家发改委-政务公开)
     - collection_policy_ext (doc_number 等)
     - KB upload (markdown)
     - AttachmentHandler (附件下载 → 解压 → KB上传)

去重 / 增量:
  crawler_result PK = md5(ndrc_xxgk|detail_page_url), 重复触发 = upsert
  首次运行: date_filter="" 全文回溯 (所有分页)
  后续运行: date_filter=today 只爬第1页, 按日期过滤当天数据

调用入口:
  由 unified_crawler.py 的 custom_runner 分支调度
  docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/unified_crawler.py \
    --tenant-id <TID> --kb-id 3b4f619c85c211f198269135a1db217c \
    --task-name ndrc_xxgk --writer collection --category policy \
    --date-filter today \
    --script-args '{"site_id":"ndrc_xxgk"}'
"""

import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional
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
SITE_ID = "ndrc_xxgk"
SITE_NAME = "国家发改委-政务公开"
SITE_DOMAIN = "www.ndrc.gov.cn"
CATEGORY = "policy"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"
NEWS_TYPE = "国家发改委-政务公开"

BASE_URL = "https://www.ndrc.gov.cn"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.ndrc.gov.cn/",
}

HTTP_TIMEOUT = 30
LIST_DELAY_MIN = 0.5
LIST_DELAY_MAX = 1.5
DETAIL_DELAY_MIN = 1.0
DETAIL_DELAY_MAX = 2.5
MAX_RETRIES = 3

# 详情页正文 CSS 选择器优先级
DETAIL_CONTENT_SELECTORS = [
    ".article_con",
    ".article_l .article_con",
    ".article_l .article_con.article_con_title",
    ".article_l .article_con.article_con_notitle",
    ".Custom_UnionStyle",
    ".TRS_Editor",
    "#zoom",
    "article",
    ".content",
]

# 文件扩展名匹配
FILE_EXT_PATTERN = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|"
    r"txt|ofd|wps|et|dps)(\?|$)",
    re.IGNORECASE,
)

# ── 栏目配置 ──────────────────────────────────────────────────────────────
SECTIONS = [
    {"id": "fzggwl", "name": "发展改革委令", "path": "/xxgk/zcfb/fzggwl/"},
    {"id": "ghxwj", "name": "规范性文件", "path": "/xxgk/zcfb/ghxwj/"},
    {"id": "ghwb", "name": "规划文本", "path": "/xxgk/zcfb/ghwb/"},
    {"id": "gg", "name": "公告", "path": "/xxgk/zcfb/gg/"},
    {"id": "tz", "name": "通知", "path": "/xxgk/zcfb/tz/"},
    {"id": "jd", "name": "解读", "path": "/xxgk/jd/jd/"},
    {"id": "zctj", "name": "政策图解", "path": "/xxgk/jd/zctj/"},
    {"id": "qgrddbjyfwgk", "name": "全国人大代表建议复文公开", "path": "/xxgk/jianyitianfuwen/qgrddbjyfwgk/"},
    {"id": "qgzxwytafwgk", "name": "全国政协委员提案复文公开", "path": "/xxgk/jianyitianfuwen/qgzxwytafwgk/"},
]

# ── SSL / 日志 ────────────────────────────────────────────────────────────
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
            logger.warning("[ndrc] HTTP GET attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(1, 3))
    raise last_exc  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════
# 列表页解析
# ═══════════════════════════════════════════════════════════════════════════

def _parse_listing_html(html: str, base_url: str) -> tuple:
    """解析列表页 HTML, 返回 (items, total_pages).

    Returns:
        items: [{title, url, date}, ...]
        total_pages: int
    """
    soup = BeautifulSoup(html, "lxml")

    items = []
    for li in soup.select(".u-list > li"):
        # 跳过空占位 li
        if "empty" in (li.get("class") or []):
            continue

        a_tag = li.select_one("a")
        if not a_tag:
            continue
        title = (a_tag.get("title") or a_tag.get_text() or "").strip()
        href = (a_tag.get("href") or "").strip()
        if not title or not href:
            continue

        # 日期
        date_span = li.select_one("span")
        date_str = (date_span.get_text() or "").strip() if date_span else ""
        # 标准化 YYYY/MM/DD → YYYY-MM-DD
        date_normalized = _normalize_date_str(date_str)

        abs_url = urljoin(base_url, href)

        items.append({
            "title": title,
            "url": abs_url,
            "date": date_normalized,
        })

    # 提取总页数
    total_pages = 1
    page_match = re.search(r"createPageHTML\((\d+)", html)
    if page_match:
        total_pages = int(page_match.group(1))

    return items, total_pages


def _normalize_date_str(raw: str) -> str:
    """标准化日期字符串到 YYYY-MM-DD."""
    if not raw:
        return ""
    # YYYY/MM/DD 或 YYYY-MM-DD
    m = re.search(r"(\d{4})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{1,2})", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # YYYY年MM月DD日
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return raw[:10] if len(raw) >= 10 else raw


def _crawl_listing_page(base_url: str, page_index: int) -> str:
    """获取列表页 HTML.

    Args:
        base_url: 栏目目录 URL (e.g. https://www.ndrc.gov.cn/xxgk/zcfb/tz/)
        page_index: 0-based page index (0 = 首页, 1 = index_1.html, ...)
    """
    if page_index == 0:
        url = base_url
    else:
        url = urljoin(base_url, f"index_{page_index}.html")
    resp = _http_get(url)
    return resp.text


def _compute_page_limit(total_pages: int, date_filter: str) -> int:
    """根据 date_filter 计算实际要爬的页数."""
    if date_filter:
        # 增量模式: 只爬第 1 页
        return 1
    return total_pages


# ═══════════════════════════════════════════════════════════════════════════
# 详情页解析
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_detail(url: str) -> Optional[Dict[str, Any]]:
    """抓取详情页并提取结构化数据.

    Returns:
        {
            "title": str,
            "publish_date": "YYYY-MM-DD",
            "source": str,
            "content": str (plain text),
            "content_html": str,
            "files": [{file_name, file_url}, ...],
        }
        或 None (抓取失败)
    """
    try:
        resp = _http_get(url)
    except Exception as e:
        logger.warning("[ndrc] detail page fetch failed: %s: %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # ── 标题 ──────────────────────────────────────────────────────────
    title = ""
    # Template A: .article_l h2.article_title
    title_el = soup.select_one(".article_l h2.article_title")
    if title_el:
        title = title_el.get_text(strip=True)
    # Template B: .article_ri h2 (建议提案复文)
    if not title:
        title_el = soup.select_one(".article_ri h2")
        if title_el:
            title = title_el.get_text(strip=True)
    # 兜底 1: 页面第一个 h2
    if not title:
        h2s = soup.select("h2")
        if h2s:
            title = h2s[0].get_text(strip=True)
    # 兜底 2: <title> 标签 (格式: "【标题】-国家发展和改革委员会")
    if not title:
        title_tag = soup.select_one("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            # 去掉站点后缀: "-国家发展和改革委员会" 或 " - 国家发展和改革委员会"
            raw_title = re.sub(r"\s*[-—]\s*国家发展和改革委员会.*$", "", raw_title)
            # 去掉多余空白
            title = raw_title.strip()

    # ── 发布日期 ──────────────────────────────────────────────────────
    publish_date = ""
    # 优先 .shezhi .time
    time_el = soup.select_one(".shezhi .time")
    if time_el:
        time_text = time_el.get_text(strip=True)
        publish_date = _normalize_date_str(time_text)
    # 建议提案复文: .article_ri p
    if not publish_date:
        ri_p = soup.select_one(".article_ri p")
        if ri_p:
            p_text = ri_p.get_text(strip=True)
            publish_date = _normalize_date_str(p_text)

    # ── 来源 ──────────────────────────────────────────────────────────
    source = ""
    source_el = soup.select_one(".shezhi .ly.laiyuantext")
    if source_el:
        source_text = source_el.get_text(strip=True)
        m = re.search(r"来源[：:]\s*(.+)", source_text)
        if m:
            source = m.group(1).strip()

    # ── 正文 ──────────────────────────────────────────────────────────
    content = ""
    content_html = ""
    for sel in DETAIL_CONTENT_SELECTORS:
        content_el = soup.select_one(sel)
        if content_el and len(content_el.get_text(strip=True)) > 50:
            content_html = str(content_el)
            content = _html_to_text(content_el)
            break

    # 如果正文很短, 尝试用整个 article 区域
    if not content or len(content) < 50:
        article_el = soup.select_one(".article_l")
        if article_el:
            # 移除 shezhi (元数据) 和 attachment
            for remove_sel in [".shezhi", ".attachment", ".article_r", "script", "style"]:
                for el in article_el.select(remove_sel):
                    el.decompose()
            content_html = str(article_el)
            content = _html_to_text(article_el)

    # ── 附件 ──────────────────────────────────────────────────────────
    files = []
    attachment_r = soup.select_one(".attachment_r")
    if attachment_r:
        for a in attachment_r.select("a[href]"):
            file_name = (a.get_text() or "").strip()
            file_href = (a.get("href") or "").strip()
            if file_href:
                abs_file_url = urljoin(url, file_href)
                if not file_name:
                    file_name = abs_file_url.rsplit("/", 1)[-1]
                files.append({
                    "file_name": file_name,
                    "file_url": abs_file_url,
                })

    return {
        "title": title or "Untitled",
        "publish_date": publish_date,
        "source": source,
        "content": content,
        "content_html": content_html,
        "files": files,
    }


def _html_to_text(container) -> str:
    """HTML 容器转纯文本 (段落用空行分隔)."""
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
    section_id: str,
) -> Dict[str, Any]:
    """构建 StoragePipeline 期望的 item dict."""
    url = listing_item["url"]
    title = detail.get("title") or listing_item.get("title") or "Untitled"
    publish_date = detail.get("publish_date") or listing_item.get("date") or ""
    content = detail.get("content", "")
    content_html = detail.get("content_html", "")
    files = detail.get("files", [])
    source = detail.get("source", "")

    # item_id: URL 末段
    path = urlparse(url).path
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
        "content_html": content_html,
        "section": section_id,
        "section_label": section_name,
        "section_name": section_name,
        "news_type": NEWS_TYPE,
        "source_site": SITE_NAME,
        "source": source,
        "issuing_authority": source or "国家发展和改革委员会",
        "doc_number": "",
        "topic_category": "",
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
    category: str = "policy",
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config=None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """custom_runner 入口."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logging.info(
        "[ndrc] start: tenant=%s kb=%s task=%s date_filter=%r",
        tenant_id, kb_id, task_name, date_filter,
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
        output_dir = os.path.join(_PROJECT_ROOT, "rag", "ndrc_xxgk_output")
    os.makedirs(output_dir, exist_ok=True)

    kb_id = kb_id or KB_ID_DEFAULT

    # ── 解析 date_filter ──────────────────────────────────────────────
    target_date = ""
    if date_filter and date_filter.lower() != "today":
        target_date = date_filter[:10]  # YYYY-MM-DD
    elif date_filter and date_filter.lower() == "today":
        target_date = date.today().isoformat()

    # ── Phase 1: 爬取所有栏目的列表项 ─────────────────────────────────
    all_items: List[Dict[str, Any]] = []

    for sec in SECTIONS:
        section_base = BASE_URL + sec["path"]
        section_id = sec["id"]
        section_name = sec["name"]

        logger.info("[ndrc] Listing: %s (%s)", section_name, section_base)

        try:
            # 第一页
            _throttle(LIST_DELAY_MIN, LIST_DELAY_MAX)
            html = _crawl_listing_page(section_base, 0)
            items, total_pages = _parse_listing_html(html, section_base)

            page_limit = _compute_page_limit(total_pages, date_filter)
            logger.info("[ndrc]   %s: page 1/%d, got %d items (total_pages=%d)",
                         section_name, page_limit, len(items), total_pages)

            # 为每个 item 打上栏目标签
            for item in items:
                item["section_id"] = section_id
                item["section_name"] = section_name
            all_items.extend(items)
            stats["scanned_pages"] += 1

            # 翻页 (只在非增量模式下)
            if not date_filter:
                for page_idx in range(1, total_pages):
                    _throttle(LIST_DELAY_MIN, LIST_DELAY_MAX)
                    try:
                        html = _crawl_listing_page(section_base, page_idx)
                        page_items, _ = _parse_listing_html(html, section_base)
                        for item in page_items:
                            item["section_id"] = section_id
                            item["section_name"] = section_name
                        all_items.extend(page_items)
                        stats["scanned_pages"] += 1
                        if page_idx % 5 == 0:
                            logger.info("[ndrc]   %s: page %d/%d, total_items=%d",
                                         section_name, page_idx + 1, total_pages, len(all_items))
                    except Exception as e:
                        logger.warning("[ndrc]   %s page %d failed: %s", section_name, page_idx + 1, e)
                        errors.append(f"list:{section_id}:p{page_idx + 1}: {e}")

        except Exception as e:
            logger.exception("[ndrc]   %s listing failed: %s", section_name, e)
            errors.append(f"list:{section_id}: {e}")
            continue

    stats["items_found"] = len(all_items)
    logger.info("[ndrc] Total listing items: %d (from %d pages)", len(all_items), stats["scanned_pages"])

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
                logger.warning("[ndrc] detail fetch failed for: %s", item["url"][:100])
                continue

            # 如果详情页有更准确的日期, 在增量模式下再检查一次
            detail_date = detail.get("publish_date", "")
            if target_date and detail_date and detail_date != target_date:
                skipped_date += 1
                continue

            # 构建 item dict
            item_dict = _build_item_dict(
                item,
                detail,
                section_name=item.get("section_name", ""),
                section_id=item.get("section_id", ""),
            )

            # 归一化并存储
            normalized = item_from_dict(
                item_dict,
                site_id=SITE_ID,
                section=item.get("section_id", ""),
            )

            store_result = pipeline.store(normalized)
            if store_result.get("project_id"):
                processed += 1

            stats["scanned_items"] += 1

            if idx % 20 == 0:
                logger.info(
                    "[ndrc] progress %d/%d (stored=%d, skipped_date=%d, skipped_detail=%d)",
                    idx, len(all_items), processed, skipped_date, skipped_detail,
                )

        except Exception as e:
            logger.exception("[ndrc] item %d failed: %s", idx, e)
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
        "[ndrc] DONE: items=%d processed=%d new=%d updated=%d kb=%d "
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
    import argparse
    p = argparse.ArgumentParser(description="NDRC 政务公开 爬虫")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", default=KB_ID_DEFAULT)
    p.add_argument("--task-name", default="ndrc_xxgk")
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
        output_dir=args.output_dir or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
