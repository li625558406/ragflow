"""
中华人民共和国水利部 — 水利部公报 采集 (custom_runner)
========================================================

Target: http://www.mwr.gov.cn/zw/slbgb/

Site characteristics
────────────────────
Static HTML page (no SPA, no encryption, no OAuth). jQuery is used only for
the navigation menu. Direct HTTP GET returns ~28KB of full HTML.

List structure (page 1, ~25 items across 5 <ul> blocks):
    <ul class="slnewsconlist">
      <li>
        <span>YYYY-MM-DD</span>
        <a href="./YYYYMM/P020...pdf">YYYY年第N期（总第NN期）</a>
      </li>
      ...
    </ul>

The <a href> is a **direct PDF link** — there is no HTML detail page. The PDF
itself is both the "正文" and the attachment.

Publishing cadence: ~5 issues/year. `date_filter=today` will match 0 items on
most days — this is correct behaviour. Detector triggers a full crawl when the
listing signature changes (new issue published); writer then keeps only items
matching the publish date.

Anti-crawler level: 🟢 L1 (open HTTP, no Cloudflare, no cookie check).

Call entry: unified_crawler.py dispatches via custom_runner → run().
"""

import gzip
import logging
import os
import random
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict  # noqa: E402

logger = logging.getLogger("mwr_slbgb_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "mwr_slbgb"
SITE_DISPLAY = "国家水利部-水利部公报"
CATEGORY = "other"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"

LISTING_URL = "http://www.mwr.gov.cn/zw/slbgb/"
SITE_ROOT = "http://www.mwr.gov.cn"
ISSUING_AUTHORITY = "中华人民共和国水利部"
TOPIC_CATEGORY = "水利部公报"
PARSER_ID = "general"

_TAG = "[MWR-SLBGB]"

_REQUEST_DELAY_MIN = 0.8
_REQUEST_DELAY_MAX = 2.0
_MAX_RETRIES = 3

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_HEADERS_PDF = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Regex for titles like "2026年第1期（总第75期）" or "2025年第3期"
_ISSUE_RE = re.compile(
    r"(\d{4})\s*年\s*第\s*(\d+)\s*期(?:[（(]\s*总\s*第\s*(\d+)\s*期\s*[）)])?"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg: str) -> None:
    print(msg, flush=True)


def _request_delay() -> None:
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _normalize_date(date_str: str) -> str:
    """Normalize 'YYYY-MM-DD' / 'YYYY/MM/DD' / 'YYYY.MM.DD' → 'YYYY-MM-DD'."""
    if not date_str:
        return ""
    s = date_str.strip()
    m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        try:
            return "{:04d}-{:02d}-{:02d}".format(int(y), int(mo), int(d))
        except ValueError:
            return s
    return s


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 30) -> Optional[bytes]:
    """HTTP GET with retries + gzip handling. Returns raw bytes or None."""
    hdrs = dict(headers or _HEADERS)
    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs, method="GET")
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=_SSL_CTX) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                return data
        except Exception as e:
            last_exc = e
            logger.warning("HTTP GET %s failed (attempt %d/%d): %s",
                           url, attempt, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES:
                time.sleep(1 + attempt)  # 2s, 3s backoff
    _safe_print("{} WARNING: GET failed after {} attempts: {}".format(
        _TAG, _MAX_RETRIES, last_exc))
    return None


def _http_get_html(url: str) -> Optional[str]:
    data = _http_get(url, _HEADERS)
    if data is None:
        return None
    # Site is UTF-8; fall back to GBK if decode fails (rare for mwr.gov.cn)
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _http_download(url: str, timeout: int = 60) -> Optional[bytes]:
    """Download binary (PDF/zip). Returns bytes or None."""
    return _http_get(url, _HEADERS_PDF, timeout=timeout)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_issue_no(title: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Extract (year, issue_no, total_no) from a gazette title.

    Returns (None, None, None) if not parseable.
    """
    if not title:
        return (None, None, None)
    m = _ISSUE_RE.search(title)
    if not m:
        return (None, None, None)
    y = int(m.group(1))
    n = int(m.group(2))
    total = int(m.group(3)) if m.group(3) else None
    return (y, n, total)


def _parse_listing(html_str: str) -> List[Dict[str, str]]:
    """Parse the listing page and return extracted items.

    Returns: [{title, date, pdf_url (absolute)}, ...]
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _safe_print("{} ERROR: bs4 not available".format(_TAG))
        return []

    soup = BeautifulSoup(html_str, "html.parser")
    items: List[Dict[str, str]] = []
    seen_urls: set = set()

    for ul in soup.select("ul.slnewsconlist"):
        for li in ul.find_all("li", recursive=False):
            a = li.find("a")
            span = li.find("span")
            if not a or not a.get("href"):
                continue
            href = a["href"].strip()
            pdf_url = urllib.parse.urljoin(LISTING_URL, href)
            if pdf_url in seen_urls:
                continue
            seen_urls.add(pdf_url)
            title = (a.get_text(strip=True) or "").strip()
            date = (span.get_text(strip=True) if span else "") or ""
            date = _normalize_date(date)
            if not title:
                continue
            items.append({
                "title": title,
                "date": date,
                "pdf_url": pdf_url,
            })

    return items


def _filter_by_date(items: List[Dict[str, str]],
                    date_filter: str) -> List[Dict[str, str]]:
    """Filter items by date_filter ('' / 'today' / 'YYYY-MM-DD')."""
    if not date_filter:
        return items
    if date_filter == "today":
        target = datetime.now().strftime("%Y-%m-%d")
    else:
        target = _normalize_date(date_filter)
    return [it for it in items if it.get("date") == target]


# ---------------------------------------------------------------------------
# PDF / zip handling
# ---------------------------------------------------------------------------

def _save_pdf_to_temp(pdf_bytes: bytes, file_name: str,
                      tmp_dir: str) -> Tuple[Optional[str], List[str]]:
    """Save bytes to tmp_dir/file_name. Detect zip magic and extract.

    Returns (primary_path, [extra_paths]). primary_path may be None if bytes
    are empty; extra_paths holds extracted zip members (if any).
    """
    extra: List[str] = []
    if not pdf_bytes:
        return (None, extra)

    safe_name = re.sub(r"[\\/:*?\"<>|]", "_", file_name)[:120]
    path = os.path.join(tmp_dir, safe_name)

    is_zip = (
        safe_name.lower().endswith(".zip") or
        (len(pdf_bytes) >= 4 and pdf_bytes[:4] == b"PK\x03\x04")
    )
    if is_zip:
        path += ".zip"

    with open(path, "wb") as f:
        f.write(pdf_bytes)

    if is_zip:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(tmp_dir)
            extracted: List[str] = []
            for member in zf.namelist():
                member_path = os.path.join(tmp_dir, member)
                if os.path.isfile(member_path) and os.path.getsize(member_path) > 0:
                    extracted.append(member_path)
            os.remove(path)
            _safe_print("{}   zip extracted: {} files".format(_TAG, len(extracted)))
            # Return first extracted file as primary + rest as extras
            if extracted:
                return (extracted[0], extracted[1:])
            return (None, extra)
        except zipfile.BadZipFile as e:
            logger.warning("zip extraction failed for %s: %s", path, e)
            return (None, extra)

    # Not a zip — verify it's a real PDF by magic
    if not (len(pdf_bytes) >= 4 and pdf_bytes[:4] == b"%PDF"):
        logger.warning("downloaded file is not PDF (no %%PDF magic): %s", file_name)
        # Still keep it — the KB parser may handle it, or user will see error in UI
    return (path, extra)


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(item: Dict[str, Any], pdf_url: str) -> str:
    """Build minimal markdown document. PDF content is parsed by KB separately."""
    lines: List[str] = []
    lines.append("# {}".format(item.get("title", "Untitled")))
    lines.append("")

    pub = item.get("date", "")
    if pub:
        lines.append("**发布日期:** {}".format(pub))

    y = item.get("issue_year")
    n = item.get("issue_no")
    total = item.get("total_no")
    bits: List[str] = []
    if y:
        bits.append("{}年".format(y))
    if n:
        bits.append("第{}期".format(n))
    if total:
        bits.append("（总第{}期）".format(total))
    if bits:
        lines.append("**期号:** {}".format("".join(bits)))

    lines.append("**发文机构:** {}".format(ISSUING_AUTHORITY))
    lines.append("**主题分类:** {}".format(TOPIC_CATEGORY))
    lines.append("**原文链接:** {}".format(pdf_url))
    lines.append("")
    lines.append("**正文内容:** 请下载下方 PDF 附件查看公报完整内容。")
    lines.append("")

    return "\n".join(lines)


def _pdf_filename(item: Dict[str, Any]) -> str:
    """Build a filesystem-safe PDF filename from the title."""
    title = item.get("title", "gazette")
    safe = re.sub(r"[\\/:*?\"<>|]", "_", title)[:100]
    return "{}.pdf".format(safe)


# ---------------------------------------------------------------------------
# Main entry
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
    _kb_id = kb_id or KB_ID_DEFAULT

    _safe_print("=" * 60)
    _safe_print("{} 水利部公报采集 (custom_runner)".format(_TAG))
    _safe_print("{} Tenant: {}  KB: {}".format(_TAG, tenant_id, _kb_id))
    _safe_print("{} Date filter: {}  Category: {}".format(
        _TAG, date_filter or "none", category))
    _safe_print("{} Full crawl: {}  Force: {}".format(_TAG, full_crawl, force_run))
    _safe_print("=" * 60)
    sys.stdout.flush()

    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline

    writer = CollectionWriter(
        kb_id=_kb_id,
        tenant_id=tenant_id,
        date_filter=date_filter,
    )
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

    total_items = 0
    total_new = 0
    total_kb = 0
    total_att = 0
    errors: List[str] = []

    # ── Step 1: Fetch listing ─────────────────────────────────────────
    _safe_print("{} Fetching listing: {}".format(_TAG, LISTING_URL))
    sys.stdout.flush()
    html_str = _http_get_html(LISTING_URL)
    if not html_str:
        msg = "Failed to fetch listing page"
        _safe_print("{} ERROR: {}".format(_TAG, msg))
        errors.append(msg)
        return {
            "status": "fail",
            "pages": 0, "items_found": 0, "items_new": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": errors,
        }

    all_items = _parse_listing(html_str)
    _safe_print("{} Parsed {} list items".format(_TAG, len(all_items)))
    sys.stdout.flush()

    if not all_items:
        return {
            "status": "success",
            "pages": 1, "items_found": 0, "items_new": 0,
            "kb_uploaded": 0, "attachments_uploaded": 0,
            "errors": [],
        }

    # ── Step 2: Apply date filter (date_filter=today → only today's items)
    #   For initial backfill (date_filter='') keep everything.
    filtered = _filter_by_date(all_items, date_filter)
    _safe_print("{} After date_filter '{}': {} items".format(
        _TAG, date_filter or "none", len(filtered)))
    sys.stdout.flush()

    if not filtered:
        _safe_print("{} No items matched date filter.".format(_TAG))
        return {
            "status": "success",
            "pages": 1,
            "items_found": len(all_items),
            "items_new": 0,
            "kb_uploaded": 0,
            "attachments_uploaded": 0,
            "errors": [],
        }

    # ── Step 3: Process each item ─────────────────────────────────────
    for i, listing in enumerate(filtered, 1):
        title = listing["title"]
        pdf_url = listing["pdf_url"]
        pub_date = listing.get("date", "")

        _safe_print("{} [{}/{}] {}".format(_TAG, i, len(filtered), title[:70]))
        sys.stdout.flush()

        issue_year, issue_no, total_no = _parse_issue_no(title)

        item_dict: Dict[str, Any] = {
            "title": title,
            "url": pdf_url,
            "date": pub_date,
            "issue_year": issue_year,
            "issue_no": issue_no,
            "total_no": total_no,
            "issuing_authority": ISSUING_AUTHORITY,
            "topic_category": TOPIC_CATEGORY,
            "section_name": SITE_DISPLAY,
            "site_id": SITE_ID,
            "content": _build_markdown(
                {
                    "title": title,
                    "date": pub_date,
                    "issue_year": issue_year,
                    "issue_no": issue_no,
                    "total_no": total_no,
                },
                pdf_url,
            ),
            "attachments": [
                {
                    "file_name": _pdf_filename({"title": title}),
                    "file_url": pdf_url,
                    "file_suffix": ".pdf",
                }
            ],
        }

        # Write to crawler_result via CollectionWriter
        try:
            result_id = writer.write_all(
                item=item_dict,
                site_id=SITE_ID,
                category=category,
                task_id=task_id,
                site_display=SITE_DISPLAY,
            )
        except Exception as e:
            msg = "CollectionWriter failed for {}: {}".format(title[:50], e)
            _safe_print("{}   ERROR: {}".format(_TAG, msg))
            logger.error(msg)
            errors.append(msg)
            continue

        if not result_id:
            # Filtered by writer (date/dedup)
            continue

        total_items += 1
        # Heuristic: upsert returns truthy id → first time = new
        # We can't reliably tell new vs updated without a return flag;
        # treat every result_id as "new" for stats purposes (dedup at DB level
        # means no duplicate rows, only updates).
        total_new += 1

        # ── Download PDF locally + upload via pipeline ──────────────
        _request_delay()

        tmp_dir = tempfile.mkdtemp(prefix="mwr_slbgb_")
        try:
            pdf_bytes = _http_download(pdf_url)
            if not pdf_bytes:
                msg = "PDF download failed: {}".format(pdf_url)
                _safe_print("{}   WARNING: {}".format(_TAG, msg))
                errors.append(msg)
                # Still upload markdown via pipeline (KB has at least metadata)
            else:
                primary_path, extra_paths = _save_pdf_to_temp(
                    pdf_bytes, _pdf_filename({"title": title}), tmp_dir
                )
                local_files = ([primary_path] if primary_path else []) + extra_paths

                # Upload local files directly to KB (skip pipeline attachment URL
                # download — we already have the bytes).
                for fp in local_files:
                    if not fp or not os.path.exists(fp):
                        continue
                    if os.path.getsize(fp) == 0:
                        continue
                    try:
                        from rag.svr.crawler_engine.kb_uploader import KBUploader
                        uploader = KBUploader(_kb_id, tenant_id)
                        doc_ids = uploader.upload_file(fp)
                        if doc_ids:
                            total_att += 1
                            _safe_print("{}   uploaded: {}".format(
                                _TAG, os.path.basename(fp)))
                    except Exception as e:
                        msg = "KB upload failed for {}: {}".format(
                            os.path.basename(fp), e)
                        _safe_print("{}   WARNING: {}".format(_TAG, msg))
                        logger.warning(msg)
                        errors.append(msg)

            # Upload markdown content to KB (separate doc for the metadata page)
            try:
                normalized = item_from_dict(item_dict, site_id=SITE_ID)
                store_result = pipeline.store(normalized)
                if store_result.get("doc_id"):
                    total_kb += 1
                # pipeline.store may also handle attachments via URL — but
                # since we already uploaded local files, attachment results
                # from pipeline would be redundant. We accept the redundancy
                # (KB dedupes by name+hash).
            except Exception as e:
                msg = "Pipeline store failed for {}: {}".format(title[:50], e)
                _safe_print("{}   WARNING: {}".format(_TAG, msg))
                logger.warning(msg)
                errors.append(msg)

        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # ── Step 4: Summary ───────────────────────────────────────────────
    _safe_print("{} Done: {} items processed, {} new, {} KB uploads, {} attachments".format(
        _TAG, total_items, total_new, total_kb, total_att))
    sys.stdout.flush()

    return {
        "status": "success" if not errors or total_items > 0 else "fail",
        "pages": 1,
        "items_found": len(all_items),
        "items_new": total_new,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors[:20],  # cap to avoid bloating last_run_summary
    }


if __name__ == "__main__":
    # Local test: python mwr_slbgb_crawler.py
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    summary = run(
        tenant_id="",
        kb_id=KB_ID_DEFAULT,
        task_name="local_test_mwr",
        task_id="",
        date_filter="",  # full backfill
    )
    print("Summary:", summary)
