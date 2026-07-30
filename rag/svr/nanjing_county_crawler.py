"""
南靖县人民政府 — 政务公开爬虫 (custom_runner)
==============================================

Target: http://www.fjnj.gov.cn/cms/html/njxrmzf/zwgk/index.html
Tabs: 政府文件, 三农服务, 财政资金, 人事信息, 规划计划

The site uses TRS/YLCMS with:
- Listing at /cms/sitemanage/index.shtml?siteId={siteId}&page={page}
- Two detail page types:
  Type A: publicInfo.shtml?id=...&siteId=... (government documents, has ul.info metadata)
  Type B: /cms/html/njxrmzf/YYYY-MM-DD/ID.html (regular articles)

Data flow:
  1. For each of 5 tabs, paginate through listing pages
  2. Extract list items via CSS selectors (BeautifulSoup)
  3. Fetch each item's detail page, extract content + attachments
  4. Write via CollectionWriter to crawler_result + KB

Call entry: unified_crawler.py dispatches via custom_runner → run()
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
from bs4 import BeautifulSoup

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

logger = logging.getLogger("nanjing_county_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "nanjing_county_zwgk"
SITE_NAME = "南靖县人民政府"
SITE_DOMAIN = "www.fjnj.gov.cn"
SITE_URL = "http://www.fjnj.gov.cn"
CATEGORY = "news"
SITE_DISPLAY = "南靖县-政务公开"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LIST_BASE = "http://www.fjnj.gov.cn/cms/sitemanage/index.shtml"

TABS: List[Dict[str, str]] = [
    {"label": "政府文件", "slug": "zfwj", "site_id": "60421384200690000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/zfwj/index.html"},
    {"label": "三农服务", "slug": "snfw", "site_id": "60421384554190000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/snfw/index.html"},
    {"label": "财政资金", "slug": "czzj", "site_id": "60421384532060000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/czzj/index.html"},
    {"label": "人事信息", "slug": "rsxx", "site_id": "60421384215270000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/rsxx/index.html"},
    {"label": "规划计划", "slug": "ghjh", "site_id": "60421384206770000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/ghjh/index.html"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg: str):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def _fetch_html(session: requests.Session, url: str, timeout: int = 30) -> Optional[str]:
    try:
        resp = session.get(url, timeout=timeout, verify=False)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except Exception as e:
        logger.warning("Fetch failed %s: %s", url[:120], e)
        return None


def _resolve_url(href: str, base: str = SITE_URL) -> str:
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    return urljoin(base, href)


def _extract_date(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(text))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _normalize_ws(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _extract_list_items(html: str) -> List[Dict[str, str]]:
    """Parse <ul id="resources"> items from CMS listing page."""
    items = []
    soup = BeautifulSoup(html, "lxml")
    ul = soup.find("ul", id="resources")
    if not ul:
        list_div = soup.find("div", class_="mid-mj-list")
        if list_div:
            ul = list_div.find("ul")
    if not ul:
        return items

    for li in ul.find_all("li"):
        a_tag = li.find("a")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        title = _normalize_ws(a_tag.get_text())
        time_span = li.find("span", class_="list-time")
        date_str = _normalize_ws(time_span.get_text()) if time_span else ""
        date_fmt = _extract_date(date_str)
        url = _resolve_url(href)
        if url and title:
            items.append({"title": title, "url": url, "date": date_fmt})
    return items


def _get_total_pages(session: requests.Session, tab: dict) -> int:
    """Fetch page 1 to determine total pages from pagination HTML."""
    url = f"{LIST_BASE}?siteId={tab['site_id']}&page=1"
    html = _fetch_html(session, url)
    if not html:
        html = _fetch_html(session, tab["tab_url"])
    if not html:
        return 1

    soup = BeautifulSoup(html, "lxml")
    pager = soup.find("div", class_="mid-mj-page")
    if not pager:
        return 1
    tip = pager.find("span", class_="page-tip")
    if tip:
        m = re.search(r"共\s*(\d+)\s*页", tip.get_text())
        if m:
            return max(int(m.group(1)), 1)
    return 1


# ---------------------------------------------------------------------------
# Detail extraction
# ---------------------------------------------------------------------------

def _extract_public_info(html: str, base_url: str) -> Dict[str, Any]:
    """Extract from publicInfo.shtml (government document with metadata)."""
    result: Dict[str, Any] = {"content": "", "content_html": "", "attachments": [], "metadata": {}}
    soup = BeautifulSoup(html, "lxml")

    # Metadata from ul.info
    info_ul = soup.find("ul", class_="info")
    if info_ul:
        for li in info_ul.find_all("li"):
            text = _normalize_ws(li.get_text())
            if "发文机关" in text:
                result["metadata"]["issuing_authority"] = text.split("：", 1)[-1].strip()
            elif "发文字号" in text:
                result["metadata"]["doc_number"] = text.split("：", 1)[-1].strip()
            elif "发布日期" in text:
                result["metadata"]["publish_date"] = _extract_date(text)
            elif "索引号" in text:
                result["metadata"]["index_no"] = text.split("：", 1)[-1].strip()
            elif "内容概述" in text:
                result["metadata"]["summary"] = text.split("：", 1)[-1].strip()
            elif "有效性" in text:
                result["metadata"]["validity"] = text.split("：", 1)[-1].strip()
        # Attachment links
        for a_tag in info_ul.find_all("a"):
            href = a_tag.get("href", "")
            if href and re.search(r"\.(pdf|docx?|xlsx?|rar|zip|7z|pptx?)(\?|$)", href, re.I):
                result["attachments"].append({
                    "file_name": _normalize_ws(a_tag.get_text()) or "attachment",
                    "file_url": _resolve_url(href, base_url),
                })

    # Content from .tsrc divs inside #Content
    content_div = soup.find("div", id="Content") or soup.find("div", class_="content")
    if content_div:
        text_parts, html_parts = [], []
        for tsrc in content_div.find_all("div", class_="tsrc"):
            for p in tsrc.find_all("p"):
                txt = _normalize_ws(p.get_text())
                if txt and len(txt) > 2 and "扫一扫" not in txt:
                    text_parts.append(txt)
                    html_parts.append(str(p))
        if text_parts:
            result["content"] = "\n\n".join(text_parts)
            result["content_html"] = "\n".join(html_parts)
        else:
            result["content"] = _normalize_ws(content_div.get_text())
            result["content_html"] = str(content_div)

    # Extra attachments from #dbfj
    fj_div = soup.find("div", id="dbfj")
    if fj_div:
        existing = {a["file_url"] for a in result["attachments"]}
        for a_tag in fj_div.find_all("a"):
            href = a_tag.get("href", "")
            if href:
                url = _resolve_url(href, base_url)
                if url not in existing:
                    result["attachments"].append({
                        "file_name": _normalize_ws(a_tag.get_text()) or "附件",
                        "file_url": url,
                    })

    return result


def _extract_article(html: str, base_url: str) -> Dict[str, Any]:
    """Extract from /cms/html/.../ID.html (regular article)."""
    result: Dict[str, Any] = {"content": "", "content_html": "", "attachments": [], "metadata": {}}
    soup = BeautifulSoup(html, "lxml")

    content_div = soup.find("div", id="Content") or soup.find("div", class_="content")
    if content_div:
        text_parts, html_parts = [], []
        for p in content_div.find_all("p"):
            txt = _normalize_ws(p.get_text())
            if txt and len(txt) > 2 and "扫一扫" not in txt:
                text_parts.append(txt)
                html_parts.append(str(p))
        result["content"] = "\n\n".join(text_parts)
        result["content_html"] = "\n".join(html_parts)

        # Attachment links
        for a_tag in content_div.find_all("a"):
            href = a_tag.get("href", "")
            text = _normalize_ws(a_tag.get_text())
            if href and (
                re.search(r"\.(pdf|docx?|xlsx?|rar|zip|7z|pptx?|txt)(\?|$)", href, re.I) or
                "/attachment/" in href or "/file/" in href or "/pages/" in href or
                "附件" in text or "下载" in text
            ):
                result["attachments"].append({
                    "file_name": text or os.path.basename(urlparse(href).path) or "attachment",
                    "file_url": _resolve_url(href, base_url),
                })

    return result


def _fetch_detail(session: requests.Session, item: Dict[str, str]) -> Dict[str, Any]:
    url = item.get("url", "")
    if not url:
        return {"content": "", "content_html": "", "attachments": [], "metadata": {}}

    html = _fetch_html(session, url)
    if not html:
        return {"content": "", "content_html": "", "attachments": [], "metadata": {}}

    if "publicInfo.shtml" in url:
        return _extract_public_info(html, url)
    return _extract_article(html, url)


# ---------------------------------------------------------------------------
# Crawl one tab
# ---------------------------------------------------------------------------

def _crawl_tab(
    session: requests.Session,
    tab: dict,
    date_filter: str = "",
    max_pages: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Crawl all pages of one tab. Returns (items, scanned_pages)."""
    label = tab["label"]
    site_id = tab["site_id"]
    items: List[Dict[str, Any]] = []

    if max_pages <= 0:
        max_pages = _get_total_pages(session, tab)

    _safe_print(f"  [{label}] siteId={site_id}, max_pages={max_pages}")

    page = 1
    empty_streak = 0
    scanned = 0

    while page <= max_pages:
        list_url = f"{LIST_BASE}?siteId={site_id}&page={page}"
        html = _fetch_html(session, list_url)
        scanned += 1

        if not html:
            page += 1
            continue

        batch = _extract_list_items(html)
        _safe_print(f"    p{page}: {len(batch)} items")

        if not batch:
            empty_streak += 1
            if empty_streak >= 3:
                break
        else:
            empty_streak = 0

        for item in batch:
            item_date = item.get("date", "")
            if date_filter and item_date != date_filter:
                continue

            detail = _fetch_detail(session, item)
            item["content"] = detail.get("content", "")
            item["content_html"] = detail.get("content_html", "")
            item["attachments"] = detail.get("attachments", [])
            item["metadata"] = detail.get("metadata", {})
            item["tab_label"] = label
            items.append(item)

        page += 1

    _safe_print(f"  [{label}] done: {len(items)} items from {scanned} pages")
    return items, scanned


# ---------------------------------------------------------------------------
# run() — custom_runner entry point
# ---------------------------------------------------------------------------

def run(
    tenant_id: str = "",
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = CATEGORY,
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config: Any = None,
    output_dir: str = "",
) -> dict:
    """Custom runner entry point called by unified_crawler.py.

    Returns a flat summary dict consumed by _writeback_task_run_result().
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    _kb_id = kb_id or KB_ID_DEFAULT

    _safe_print("=" * 60)
    _safe_print(f"南靖县人民政府 — 政务公开采集")
    _safe_print(f"Tenant: {tenant_id}  KB: {_kb_id}")
    _safe_print(f"Date filter: {date_filter or 'none'}  Category: {category}")
    _safe_print("=" * 60)

    # Lazy-import writer services (available inside Docker container)
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline

    writer = CollectionWriter(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        date_filter=date_filter,
    )

    # Storage pipeline (handles KB upload via KBUploader)
    pipeline = StoragePipeline(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        site_id=SITE_ID,
        site_display=SITE_DISPLAY,
        task_name=task_name,
        output_dir=output_dir,
        writer_mode="collection",
        category=category,
        task_id=task_id,
        date_filter=date_filter,
    )

    session = _make_session()

    # Stats
    total_scanned = 0
    total_items = 0
    total_new = 0
    total_updated = 0
    total_failed = 0
    total_filtered = 0
    total_kb = 0
    total_att = 0
    errors: List[str] = []

    for tab in TABS:
        try:
            items, scanned = _crawl_tab(session, tab, date_filter=date_filter)
        except Exception as e:
            msg = f"[{tab['label']}] crawl error: {e}"
            logger.exception(msg)
            errors.append(msg)
            continue

        total_scanned += scanned

        for item in items:
            # Write to crawler_result
            result_id = writer.write_all(
                item=item,
                site_id=SITE_ID,
                category=category,
                task_id=task_id,
                site_display=SITE_DISPLAY,
            )
            if not result_id:
                continue
            total_items += 1

            # Write to KB via pipeline
            normalized = item_from_dict(item, site_id=SITE_ID, section=item.get("tab_label", ""))
            try:
                store_result = pipeline.store(normalized)
                if store_result.get("doc_id"):
                    total_kb += 1
                attach_results = store_result.get("attachment_results", [])
                if attach_results:
                    total_att += sum(1 for a in attach_results if a.get("success"))
            except Exception as e:
                logger.warning("Pipeline store failed for %s: %s", item.get("url", "")[:80], e)

        time.sleep(random.uniform(0.5, 1.0))

    # Pull stats from writer
    wstats = writer.stats
    total_new = wstats.get("results_new", 0)
    total_updated = wstats.get("results_updated", 0)
    total_failed = wstats.get("results_failed", 0)
    total_filtered = wstats.get("results_filtered_out", 0)

    _safe_print("\n" + "=" * 60)
    _safe_print(f"DONE  pages={total_scanned}  items={total_items}  "
                f"new={total_new}  upd={total_updated}  fail={total_failed}  "
                f"kb={total_kb}  att={total_att}")
    _safe_print("=" * 60)

    return {
        "status": "success" if not errors else ("fail" if total_items == 0 else "partial"),
        "pages": total_scanned,
        "items_found": total_items,
        "items_new": total_new,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors,
    }
