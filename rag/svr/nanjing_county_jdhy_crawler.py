"""
南靖县人民政府 — 解读回应爬虫 (custom_runner)
==============================================

Target: http://www.fjnj.gov.cn/cms/html/njxrmzf/jdhy/index.html
Tabs: 政策解读, 图说图解, H5解读, 音频解读, 回应关切

The site uses TRS/YLCMS with:
- Listing at /cms/sitemanage/index.shtml?siteId={siteId}&page={page}
- Detail pages: /cms/html/njxrmzf/YYYY-MM-DD/ID.html

Data flow:
  1. For each of 5 tabs, paginate through listing pages
  2. Extract list items via CSS selectors (BeautifulSoup)
  3. Fetch each item's detail page, extract content + attachments + images
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

logger = logging.getLogger("nanjing_county_jdhy_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "nanjing_county_jdhy"
SITE_NAME = "南靖县人民政府"
SITE_DOMAIN = "www.fjnj.gov.cn"
SITE_URL = "http://www.fjnj.gov.cn"
CATEGORY = "news"
SITE_DISPLAY = "南靖县-解读回应"
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
    {"label": "政策解读", "slug": "zcjd", "site_id": "60421384817130000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/zcjd/index.html"},
    {"label": "图说图解", "slug": "tstj", "site_id": "830546737142010006",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/tstj/index.html"},
    {"label": "H5解读", "slug": "h5jd", "site_id": "830621233957850000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/h5jd/index.html"},
    {"label": "音频解读", "slug": "ypjd", "site_id": "830621142905040000",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/ypjd/index.html"},
    {"label": "回应关切", "slug": "hygq1", "site_id": "830546737204910015",
     "tab_url": "http://www.fjnj.gov.cn/cms/html/njxrmzf/hygq1/index.html"},
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

def _extract_detail(html: str, base_url: str, tab_label: str = "") -> Dict[str, Any]:
    """Extract from /cms/html/.../ID.html detail page.

    Returns dict with: content, content_html, attachments, metadata, images
    """
    result: Dict[str, Any] = {
        "content": "",
        "content_html": "",
        "attachments": [],
        "metadata": {},
        "images": [],
    }
    soup = BeautifulSoup(html, "lxml")

    # ── Metadata (date + source) ──
    # Look for the metadata bar: 2026-02-09 09:23  来源：xxx
    meta_bar = soup.find("div", class_="article-infos")
    if meta_bar:
        meta_text = _normalize_ws(meta_bar.get_text())
        result["metadata"]["publish_date"] = _extract_date(meta_text)
        src_match = re.search(r"来源[：:]\s*(.+?)(?:\s|$)", meta_text)
        if src_match:
            result["metadata"]["source"] = src_match.group(1).strip()

    # ── Content ──
    content_div = soup.find("div", id="Content") or soup.find("div", class_="content")
    if not content_div:
        # Try common content areas
        for sel in [".article-content", ".article-body", ".TRS_Editor", ".main-content"]:
            content_div = soup.find("div", class_=sel.lstrip("."))
            if content_div:
                break

    if content_div:
        text_parts, html_parts = [], []

        for child in content_div.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"], recursive=True):
            # Skip utility paragraphs
            txt = _normalize_ws(child.get_text())
            if not txt:
                continue
            if "扫一扫在手机打开" in txt or "扫一扫" in txt and len(txt) < 30:
                continue

            text_parts.append(txt)
            html_parts.append(str(child))

        # Extract images
        for img in content_div.find_all("img"):
            src = img.get("src", "")
            if src and not src.startswith("data:"):
                img_url = _resolve_url(src, base_url)
                alt = img.get("alt", "") or _normalize_ws(img.get_text())
                if img_url not in [i["url"] for i in result["images"]]:
                    result["images"].append({
                        "url": img_url,
                        "alt": alt or os.path.basename(urlparse(img_url).path),
                    })

        if text_parts:
            result["content"] = "\n\n".join(text_parts)
            result["content_html"] = "\n".join(html_parts)
        else:
            result["content"] = _normalize_ws(content_div.get_text())
            result["content_html"] = str(content_div)

    # ── Attachments ──
    # A. Links inside content
    if content_div:
        for a_tag in content_div.find_all("a"):
            href = a_tag.get("href", "")
            text = _normalize_ws(a_tag.get_text())
            if href and (
                re.search(r"\.(pdf|docx?|xlsx?|rar|zip|7z|pptx?|txt)(\?|$)", href, re.I) or
                "/attachment/" in href or "/file/" in href or "/pages/" in href or
                "附件" in text or "下载" in text
            ):
                file_url = _resolve_url(href, base_url)
                existing = {a["file_url"] for a in result["attachments"]}
                if file_url not in existing:
                    result["attachments"].append({
                        "file_name": text or os.path.basename(urlparse(href).path) or "attachment",
                        "file_url": file_url,
                    })

    # B. #dbfj (附件) div
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

    # C. Related-links section (相关链接 / 附件下载区)
    for related_sel in [".article_relalinks", ".article_attachment", ".related-links", ".fujian"]:
        related_div = soup.find("div", class_=related_sel.lstrip("."))
        if related_div:
            existing = {a["file_url"] for a in result["attachments"]}
            for a_tag in related_div.find_all("a"):
                href = a_tag.get("href", "")
                if href:
                    url = _resolve_url(href, base_url)
                    if url not in existing:
                        fname = _normalize_ws(a_tag.get_text())
                        result["attachments"].append({
                            "file_name": fname or os.path.basename(urlparse(href).path) or "attachment",
                            "file_url": url,
                        })

    return result


def _fetch_detail(session: requests.Session, item: Dict[str, str], tab_label: str = "") -> Dict[str, Any]:
    url = item.get("url", "")
    if not url:
        return {"content": "", "content_html": "", "attachments": [], "metadata": {}, "images": []}

    html = _fetch_html(session, url)
    if not html:
        return {"content": "", "content_html": "", "attachments": [], "metadata": {}, "images": []}

    return _extract_detail(html, url, tab_label)


# ---------------------------------------------------------------------------
# Crawl one tab
# ---------------------------------------------------------------------------

def _crawl_tab(
    session: requests.Session,
    tab: dict,
    date_filter: str = "",
    max_pages: int = 0,
    full_crawl: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """Crawl all pages of one tab. Returns (items, scanned_pages)."""
    label = tab["label"]
    site_id = tab["site_id"]
    items: List[Dict[str, Any]] = []

    if max_pages <= 0:
        max_pages = _get_total_pages(session, tab)

    # For non-full crawl, only scan the first page (today's items appear first)
    if not full_crawl and date_filter:
        max_pages = min(max_pages, 2)

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

            detail = _fetch_detail(session, item, tab_label=label)
            item["content"] = detail.get("content", "")
            item["content_html"] = detail.get("content_html", "")
            item["attachments"] = detail.get("attachments", [])
            item["images"] = detail.get("images", [])
            item["metadata"] = detail.get("metadata", {})
            item["tab_label"] = label
            items.append(item)

        # For date-filter mode, if the last item on this page is before the
        # filter date, we can stop (items are in reverse chronological order)
        if date_filter and batch:
            last_date = batch[-1].get("date", "")
            if last_date and last_date < date_filter:
                _safe_print(f"    p{page}: last date {last_date} < filter {date_filter}, stopping")
                break

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
    _safe_print(f"南靖县人民政府 — 解读回应采集")
    _safe_print(f"Tenant: {tenant_id}  KB: {_kb_id}")
    _safe_print(f"Date filter: {date_filter or 'none'}  Full: {full_crawl}  Category: {category}")
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
            items, scanned = _crawl_tab(
                session, tab,
                date_filter=date_filter,
                full_crawl=full_crawl,
            )
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
