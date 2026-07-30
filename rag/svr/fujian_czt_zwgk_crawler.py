"""
fujian_czt_zwgk_crawler — 福建省财政厅·政务公开 智能采集自定义 runner
=======================================================================

数据源: https://czt.fujian.gov.cn/zwgk/
6 栏目:
    - 政策文件         zcfg
    - 通知公告         tzgg
    - 统计数据         tjsj
    - 财政资金         czzj
    - 规划计划         ghjh
    - 代表委员之声     srdzxjyhtabl_60587

数据流:
    1. requests.get 每个栏目列表页 (静态 HTML)
    2. 解析 <li><a href title>标题</a><span class="bf-pass">日期</span></li>
    3. 对每个 item 分类:
       - URL 以 .htm/.html 结尾 → HTML 详情页，抓 .article_title/
         .article_time/.article_source/.TRS_Editor/.article_attachment/
         .article_relalinks，附件扫描详情 HTML
       - URL 以 .pdf/.docx/.xlsx/.zip 等结尾 → 直链文件，URL 本身即附件，
         无正文 (content 留空，KB 上传由附件链路完成)
    4. item.news_type = "福建省财政厅-政务公开"
    5. StoragePipeline.store(NormalizedItem):
       - CollectionWriter 写 crawler_result (+ collection_policy_ext 如果有 doc_number)
       - KBUploader 上传 markdown 到 KB
       - AttachmentHandler 下载并上传附件 (zip 自动解压)

去重 / 增量:
    crawler_result PK = md5(site_id|source_url)，重复触发 = upsert
    date_filter=today 时只保留 item.date == today 的条目
    无日期字段的 item 视为不匹配 (严格模式)

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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

# 项目路径初始化（容器内 /ragflow，本地 dev 时手动 cwd）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

# ── 常量 ──────────────────────────────────────────────────────────────
SITE_ID = "fujian_czt_zwgk"
SITE_NAME = "福建省财政厅-政务公开"
SITE_DOMAIN = "czt.fujian.gov.cn"
NEWS_TYPE = "福建省财政厅-政务公开"  # 用户需求：列表类型固定
SOURCE_NAME = "福建省财政厅"
ZWGK_ROOT = "https://czt.fujian.gov.cn/zwgk/"

# 6 栏目配置 (label, name, url)
SECTIONS: List[Dict[str, str]] = [
    {
        "label": "zcfg",
        "name": "福建省财政厅-政策文件",
        "url": ZWGK_ROOT + "zcfg/",
    },
    {
        "label": "tzgg",
        "name": "福建省财政厅-通知公告",
        "url": ZWGK_ROOT + "tzgg/",
    },
    {
        "label": "tjsj",
        "name": "福建省财政厅-统计数据",
        "url": ZWGK_ROOT + "tjsj/",
    },
    {
        "label": "czzj",
        "name": "福建省财政厅-财政资金",
        "url": ZWGK_ROOT + "czzj/",
    },
    {
        "label": "ghjh",
        "name": "福建省财政厅-规划计划",
        "url": ZWGK_ROOT + "ghjh/",
    },
    {
        "label": "srdzxjyhtabl",
        "name": "福建省财政厅-代表委员之声",
        "url": ZWGK_ROOT + "srdzxjyhtabl_60587/",
    },
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": ZWGK_ROOT,
}

LIST_TIMEOUT = 20
DETAIL_TIMEOUT = 20
LIST_DELAY_MIN = 0.3
LIST_DELAY_MAX = 0.8
DETAIL_DELAY_MIN = 0.5
DETAIL_DELAY_MAX = 1.5

# 详情页正文 CSS 选择器优先级
DETAIL_CONTENT_SELECTORS: Tuple[str, ...] = (
    ".TRS_Editor",
    "#detailCont",
    ".article-content",
    ".news-content",
    ".detail-content",
    ".text-content",
    ".pages_content",
    "#zoom",
    "article",
    ".content",
)

# 文件扩展名 (用于 _extract_files_from_item 兼容判定)
FILE_EXT_PATTERN = re.compile(
    r"\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|"
    r"txt|ofd|wps|et|dps|jpg|jpeg|png|gif|bmp)(\?|$)",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


# ── HTTP helpers ──────────────────────────────────────────────────────
def _throttle(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _http_get(url: str, timeout: int = 20) -> requests.Response:
    """GET 并自动按 UTF-8 解码。verify_ssl=False 政府站点常见证书问题。"""
    resp = requests.get(
        url,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        verify=False,
        allow_redirects=True,
    )
    resp.raise_for_status()
    # 强制 UTF-8 (czt.fujian.gov.cn 是 UTF-8 编码)
    ct = resp.headers.get("Content-Type", "")
    if "charset=" not in ct.lower():
        resp.encoding = "utf-8"
    return resp


# ── 列表页解析 ────────────────────────────────────────────────────────
def _parse_list_items(html: str, base_url: str,
                       section_label: str, section_name: str) -> List[Dict[str, Any]]:
    """解析 <li><a href title>标题</a><span>日期</span></li> 模式。

    使用 BS4，提取 (title, url, date, section_label, section_name, raw_id)。
    raw_id 取 URL 末段 (t20260703_7171183 或 P020260714650345462943)。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    items: List[Dict[str, Any]] = []
    seen_urls: set = set()

    for li in soup.select("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        # 必须有 title 属性 (列表项特征，过滤导航/面包屑)
        title = (a.get("title") or "").strip()
        if not title or len(title) < 4:
            continue
        href = a.get("href") or ""
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = urljoin(base_url, href)
        # 只保留本站内链接
        if SITE_DOMAIN not in urlparse(url).netloc.lower():
            continue
        # 必须匹配详情页模式: .htm/.html 或 文件扩展名
        path_lower = urlparse(url).path.lower()
        is_html_detail = path_lower.endswith((".htm", ".html"))
        is_direct_file = bool(FILE_EXT_PATTERN.search(path_lower))
        if not (is_html_detail or is_direct_file):
            continue
        # 日期: li 内 <span> 文本
        date_text = ""
        for span in li.find_all("span"):
            t = (span.get_text() or "").strip()
            m = re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", t)
            if m:
                date_text = t.replace("/", "-")[:10]
                break

        if url in seen_urls:
            continue
        seen_urls.add(url)

        # raw_id: URL 末段文件名 (去扩展名)
        path = urlparse(url).path
        raw_id = path.rsplit("/", 1)[-1] or path

        items.append({
            "title": title,
            "url": url,
            "date": date_text,
            "section_label": section_label,
            "section_name": section_name,
            "raw_id": raw_id,
            "is_html_detail": is_html_detail,
            "is_direct_file": is_direct_file,
        })

    return items


# ── 详情页解析 ────────────────────────────────────────────────────────
def _extract_html_detail(url: str) -> Dict[str, Any]:
    """抓取 HTML 详情页，提取正文/元数据/附件。

    Returns dict with keys:
        content (str), publish_datetime (str), source (str),
        doc_number (str), files (List[Dict]), related_links (List[Dict]),
        detail_html (str)
    """
    result: Dict[str, Any] = {
        "content": "",
        "publish_datetime": "",
        "source": "",
        "doc_number": "",
        "files": [],
        "related_links": [],
        "detail_html": "",
    }
    try:
        resp = _http_get(url, timeout=DETAIL_TIMEOUT)
    except Exception as e:
        logger.warning("[czt] detail fetch failed for %s: %s", url, e)
        return result

    html = resp.text
    result["detail_html"] = html

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # 标题 (备用，列表已有)
    title_el = soup.select_one(".article_title")
    if title_el:
        result["detail_title"] = (title_el.get_text(strip=True) or "").strip()

    # 发布时间: .article_time「时间: 2026-07-03」
    time_el = soup.select_one(".article_time")
    if time_el:
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)?)",
                      time_el.get_text() or "")
        if m:
            result["publish_datetime"] = m.group(1).strip()

    # 来源: .article_source「来源: xxx」
    src_el = soup.select_one(".article_source")
    if src_el:
        result["source"] = re.sub(r"^来源[:：]\s*", "",
                                  (src_el.get_text() or "").strip())

    # 正文 (.TRS_Editor 优先，回退到其他常见选择器)
    container = None
    for sel in DETAIL_CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 50:
            container = el
            break
    if container:
        result["content"] = _html_to_text(container)
    else:
        logger.debug("[czt] no content selector matched in %s", url)

    # 附件: .article_attachment 区域 <a href>
    for a in soup.select(".article_attachment a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(url, href)
        path_lower = urlparse(abs_url).path.lower()
        if not FILE_EXT_PATTERN.search(path_lower):
            continue
        name = (a.get_text() or "").strip() or urlparse(abs_url).path.rsplit("/", 1)[-1]
        result["files"].append({"file_name": name, "file_url": abs_url})

    # 相关链接: .article_relalinks 区域 <a href>
    for a in soup.select(".article_relalinks a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        abs_url = urljoin(url, href)
        title_attr = (a.get("title") or "").strip()
        text = (a.get_text() or "").strip()
        result["related_links"].append({
            "title": title_attr or text,
            "url": abs_url,
        })

    # 正文中也扫描 <a href> 指向文件的链接 (兜底)
    body_text = result.get("detail_html", "")
    seen: set = {f["file_url"] for f in result["files"]}
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>',
                         body_text, re.IGNORECASE):
        link_url = m.group(1).strip()
        link_text = m.group(2).strip() or urlparse(link_url).path.rsplit("/", 1)[-1]
        abs_url = urljoin(url, link_url)
        if abs_url in seen:
            continue
        path_lower = urlparse(abs_url).path.lower()
        if FILE_EXT_PATTERN.search(path_lower):
            result["files"].append({"file_name": link_text, "file_url": abs_url})
            seen.add(abs_url)

    # 发文字号: 从正文首位匹配
    body_head = (container.get_text() if container else "")[:500]
    dn = re.search(
        r"(?:闽|国|发改|财政|建设|交通|税务|审计|教育|卫生|人社|商务)"
        r"[\u4e00-\u9fa5]{0,15}?〔\d{4}〕\s*\d+\s*号",
        body_head,
    )
    if dn:
        result["doc_number"] = dn.group(0).strip()

    return result


def _html_to_text(container) -> str:
    """HTML 容器转纯文本 (段落用空行分隔)。"""
    text = container.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n\n".join(lines)


# ── item 构建 ─────────────────────────────────────────────────────────
def _build_item_dict(parsed: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    """合并 list item + detail 抓取结果，构建 StoragePipeline 期望的 item dict。"""
    url = parsed["url"]
    title = parsed["title"]

    if parsed["is_html_detail"]:
        content = detail.get("content", "")
        files = detail.get("files", [])
        related_links = detail.get("related_links", [])
        publish_dt = detail.get("publish_datetime", "") or parsed["date"]
        source = detail.get("source", "") or SOURCE_NAME
        doc_number = detail.get("doc_number", "")
    else:
        # 直链文件: URL 即附件，content 留空
        file_name = parsed["title"]
        # 文件名兜底: URL 末段
        if not file_name:
            file_name = urlparse(url).path.rsplit("/", 1)[-1]
        content = ""
        files = [{"file_name": file_name, "file_url": url}]
        related_links = []
        publish_dt = parsed["date"]
        source = SOURCE_NAME
        doc_number = ""

    # item_id: 用 URL 末段保持稳定
    item_id = parsed.get("raw_id") or urlparse(url).path

    return {
        "id": item_id,
        "uuid": item_id,
        "title": title,
        "url": url,
        "source_url": url,
        "date": publish_dt[:10] if publish_dt else "",
        "publishDate": publish_dt[:10] if publish_dt else "",
        "publish_datetime": publish_dt,
        "content": content,
        "content_html": detail.get("detail_html", "") if parsed["is_html_detail"] else "",
        "section": parsed["section_label"],
        "section_label": parsed["section_label"],
        "section_name": parsed["section_name"],
        "news_type": NEWS_TYPE,
        "source_site": SITE_NAME,
        "source": source,
        "issuing_authority": SOURCE_NAME,
        "doc_number": doc_number,
        "topic_category": parsed["section_name"],
        "related_links": related_links,
        "files": files,
        # 用于 collection_policy_ext
        "authority_level": "",
        "effective_date": "",
        "expiry_date": "",
        "status": "",
        "legal_basis": "",
    }


# ── 主入口 ────────────────────────────────────────────────────────────
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
        "[czt] start: tenant=%s kb=%s task=%s date_filter=%r full=%s",
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
        output_dir = os.path.join(_PROJECT_ROOT, "rag", "czt_zwgk_output")
    os.makedirs(output_dir, exist_ok=True)

    # 1. 抓取所有栏目列表页
    all_items: List[Dict[str, Any]] = []
    for sec in SECTIONS:
        try:
            _throttle(LIST_DELAY_MIN, LIST_DELAY_MAX)
            logger.info("[czt] fetching section: %s (%s)", sec["label"], sec["url"])
            resp = _http_get(sec["url"], timeout=LIST_TIMEOUT)
            page_items = _parse_list_items(
                resp.text, base_url=sec["url"],
                section_label=sec["label"], section_name=sec["name"],
            )
            logger.info("[czt] section %s: parsed %d items",
                        sec["label"], len(page_items))
            all_items.extend(page_items)
            stats["scanned_pages"] += 1
        except Exception as e:
            logger.error("[czt] section %s fetch failed: %s", sec["label"], e)
            errors.append(f"section {sec['label']}: {e}")

    stats["items_found"] = len(all_items)
    if not all_items:
        stats["status"] = "success"
        logger.info("[czt] no items found across all sections")
        print(f"[CRAWLER] Done: 0 new items, 0 updated, 0 kb uploaded", flush=True)
        return stats

    # 2. 初始化存储管道
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline
    pipeline = StoragePipeline(
        kb_id=kb_id,
        tenant_id=tenant_id,
        parser_id="naive",
        site_id=SITE_ID,
        task_name=task_name or SITE_ID,
        output_dir=output_dir,
        writer_mode="collection",
        category=category or "policy",
        task_id=task_id,
        date_filter=date_filter or "",
        site_display=f"{SITE_NAME} {SITE_DOMAIN}",
    )

    # 3. 逐条处理: HTML 详情页 → 抓详情; 直链文件 → 仅附件
    processed = 0
    for idx, parsed in enumerate(all_items, 1):
        try:
            if parsed["is_html_detail"]:
                _throttle(DETAIL_DELAY_MIN, DETAIL_DELAY_MAX)
                detail = _extract_html_detail(parsed["url"])
            else:
                detail = {
                    "content": "",
                    "publish_datetime": parsed["date"],
                    "source": SOURCE_NAME,
                    "doc_number": "",
                    "files": [{
                        "file_name": parsed["title"],
                        "file_url": parsed["url"],
                    }],
                    "related_links": [],
                    "detail_html": "",
                }

            item_dict = _build_item_dict(parsed, detail)
            normalized = item_from_dict(item_dict, site_id=SITE_ID,
                                        section=parsed["section_label"])
            store_result = pipeline.store(normalized)
            if store_result.get("project_id"):
                processed += 1
            stats["scanned_items"] += 1

            if idx % 20 == 0:
                logger.info("[czt] progress %d/%d (processed=%d)",
                            idx, len(all_items), processed)
        except Exception as e:
            logger.exception("[czt] item %d failed: %s", idx, e)
            errors.append(f"item#{idx} ({parsed.get('url', '')[:80]}): {e}")

    # 4. 汇总 stats
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
        "[czt] DONE: processed=%d, new=%d, updated=%d, kb=%d, errors=%d",
        processed, stats["items_new"], stats["items_updated"],
        stats["kb_uploaded"], len(errors),
    )
    # 触发脚本通过正则解析此行回写 crawler_task.last_run_*
    print(
        f"[CRAWLER] Done: {stats['items_new']} new items, "
        f"{stats['items_updated']} updated, {stats['kb_uploaded']} kb uploaded",
        flush=True,
    )
    return stats


# ── CLI 直跑（debug 用） ─────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", default="")
    p.add_argument("--task-name", default="manual-czt-zwgk")
    p.add_argument("--task-id", default="")
    p.add_argument("--date-filter", default="")
    p.add_argument("--output-dir", default=None)
    args = p.parse_args()

    summary = run(
        tenant_id=args.tenant_id,
        kb_id=args.kb_id,
        task_name=args.task_name,
        task_id=args.task_id,
        date_filter=args.date_filter,
        output_dir=args.output_dir,
    )
    sys.exit(0 if summary.get("status") in ("success", "success_with_errors") else 1)
