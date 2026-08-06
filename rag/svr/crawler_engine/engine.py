"""
CrawlerEngine — main orchestrator for the unified crawler framework.

Three-layer architecture:
  Layer 1 (CRAWL): Adapter → Paginator → Extractor → AntiCrawler
  Layer 2 (DEDUP): DedupChecker (memory + DB dual check)
  Layer 3 (STORAGE): StoragePipeline (bid tables + KB upload + attachments)

Usage:
    engine = CrawlerEngine(config)
    engine.run(tenant_id="xxx", kb_id="xxx", task_name="my_task")
"""

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

try:
    from rag.utils.redis_conn import RedisDistributedLock, REDIS_CONN
except ImportError:
    RedisDistributedLock = None  # type: ignore
    REDIS_CONN = None  # type: ignore

from .config import SiteConfig, SectionConfig
from .paginator import PaginatorFactory, BasePaginator
from .anti_crawler import AntiCrawlerManager
from .adapters.base import BaseAdapter, AdapterFactory
from .extractors.base import ExtractorFactory
from .models import NormalizedItem, item_from_dict
from .progress_reporter import ProgressReporter

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "output"
)

# Safety limit: max crawl iterations before forced stop
MAX_CRAWL_ITERATIONS = 10000

# Default max pages if YAML doesn't specify one
DEFAULT_MAX_PAGES = 20


class CrawlerEngine:
    """Main engine that orchestrates a complete crawl cycle for one site."""

    def __init__(self, config: SiteConfig, output_dir: Optional[str] = None,
                 progress_reporter: Optional[ProgressReporter] = None):
        self._config = config
        self._output_dir = output_dir or os.path.join(OUTPUT_DIR, config.site_id)
        self._task_name: str = ""
        # Layer 1: Crawl components
        self._adapter: Optional[BaseAdapter] = None
        self._detail_adapter: Optional[BaseAdapter] = None  # override transport for detail
        self._paginator: Optional[BasePaginator] = None
        self._anti_crawler: Optional[AntiCrawlerManager] = None
        # Layer 2: Dedup
        self._dedup_checker = None
        # Layer 3: Storage
        self._storage_pipeline = None
        self._state = None
        self._batch_counter: int = 0
        # Progress reporting (WebSocket front-end)
        self._reporter = progress_reporter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        tenant_id: str,
        kb_id: str,
        task_name: str = "",
        full: bool = False,
        force: bool = False,
        skip_kb: bool = False,
        skip_attachments: bool = False,
        writer_mode: str = "bid",
        category: str = "",
        task_id: str = "",
        date_filter: str = "",
    ) -> Dict[str, Any]:
        """Run a full crawl cycle for the configured site.

        Returns a summary dict with statistics.
        """
        self._task_name = task_name
        # Resolve effective category: explicit override > site config default
        self._writer_mode = writer_mode
        self._category = category or getattr(self._config, "category", "") or "bid"
        self._task_id = task_id
        self._date_filter = date_filter
        os.makedirs(self._output_dir, exist_ok=True)

        # Acquire distributed lock to prevent concurrent execution of the
        # same site+tenant.  TTL 1800s (30 min).
        if RedisDistributedLock is not None and not force:
            lock_key = f"crawler_engine:{tenant_id}:{self._config.site_id}"
            lock = RedisDistributedLock(lock_key, timeout=1800, blocking_timeout=0)
            if not lock.acquire():
                logging.warning(
                    "Engine: site=%s tenant=%s is already being crawled, skipping.",
                    self._config.site_id, tenant_id,
                )
                return {"status": "skipped", "reason": "already_running"}
            try:
                return self._run_locked(
                    tenant_id, kb_id, task_name, full,
                    skip_kb, skip_attachments,
                )
            finally:
                lock.release()
        elif force and REDIS_CONN is not None:
            lock_key = f"crawler_engine:{tenant_id}:{self._config.site_id}"
            REDIS_CONN.delete(lock_key)
            logging.info("Engine: force mode, cleared lock for site=%s", self._config.site_id)
            lock = RedisDistributedLock(lock_key, timeout=1800, blocking_timeout=0)
            try:
                return self._run_locked(
                    tenant_id, kb_id, task_name, full,
                    skip_kb, skip_attachments,
                )
            finally:
                lock.release()
        else:
            return self._run_locked(
                tenant_id, kb_id, task_name, full,
                skip_kb, skip_attachments,
            )

    def _run_locked(
        self,
        tenant_id: str,
        kb_id: str,
        task_name: str = "",
        full: bool = False,
        skip_kb: bool = False,
        skip_attachments: bool = False,
    ) -> Dict[str, Any]:
        """Internal: run after acquiring the distributed lock."""
        self._init_components(kb_id, tenant_id,
                              skip_kb, skip_attachments)
        self._log_header(kb_id)
        # Stash flags so per-section _crawl_sections can re-apply state resets
        # after each section reloads its own StateManager.
        self._full_crawl = bool(full)

        if full:
            # Re-scan from page 1 to catch items the detector may have missed,
            # but keep processed_ids so the memory dedup layer still works.
            # DB-layer dedup (bid_project table) is always active regardless.
            self._state.last_page = 0
            self._state.last_offset = 0
            logging.info(
                "Engine: full crawl mode — resetting page to 1, "
                "keeping %d processed IDs for dedup",
                self._state.processed_count,
            )
        elif getattr(self, "_date_filter", ""):
            # date_filter (e.g. "today") changes the data set each run —
            # today's items always start at page 1, so last_page must reset.
            # Keep processed_ids so already-stored items are deduped within the day.
            self._state.last_page = 0
            self._state.last_offset = 0
            logging.info(
                "Engine: date_filter='%s' — resetting to page 1 "
                "(keeping %d processed IDs for dedup)",
                self._date_filter, self._state.processed_count,
            )
            # Inject date_filter into listing params when the site declares
            # startdate / enddate keys (e.g. EpointWebBuilder CMS). Without this
            # many APIs return unordered history data and date_filter on the
            # writer side rejects everything in the first N pages.
            self._inject_date_filter_to_listing(self._date_filter)

        # 8 AM check
        if not self._anti_crawler.check_eight_am():
            logging.info("Engine: waiting until 8 AM for site %s", self._config.site_id)
            skipped_summary = {"status": "skipped", "reason": "before_8am"}
            if self._reporter is not None and self._reporter.enabled:
                self._reporter.publish_done("skipped", skipped_summary)
            return skipped_summary

        # Main crawl loop
        try:
            summary = self._crawl_sections()
        except Exception as e:
            logging.exception("Engine: crawl_sections crashed for site=%s: %s",
                              self._config.site_id, e)
            if self._reporter is not None and self._reporter.enabled:
                self._reporter.publish_done("fail", {"error": str(e), "site_id": self._config.site_id})
            raise
        finally:
            self._cleanup()

        self._log_footer(summary)
        return summary

    # ------------------------------------------------------------------
    # Internal: component initialization
    # ------------------------------------------------------------------

    def _init_components(self, kb_id: str, tenant_id: str,
                         skip_kb: bool = False,
                         skip_attachments: bool = False) -> None:
        """Initialize all sub-components for all three layers."""
        # Layer 1: Crawl components
        from .state_manager import StateManager
        fmt_cfg = self._config.format

        self._adapter = AdapterFactory.create(self._config)
        self._paginator = PaginatorFactory.create(self._config.pagination)

        # Detail adapter override (e.g. spa_render for SPA detail pages)
        self._detail_adapter = None
        if (self._config.detail.transport
                and self._config.detail.transport.type != self._config.transport.type):
            from .config import SiteConfig
            detail_site = SiteConfig(
                name=self._config.name,
                site_id=self._config.site_id,
                site_url=self._config.site_url,
                transport=self._config.detail.transport,
                listing=self._config.listing,
                pagination=self._config.pagination,
                anti_crawler=self._config.anti_crawler,
                extract=self._config.extract,
                detail=self._config.detail,
            )
            detail_transport_type = self._config.detail.transport.type
            # Reset detail transport to avoid infinite recursion
            detail_site.detail.transport = None
            self._detail_adapter = AdapterFactory.create(detail_site)
            logging.info("Engine: detail transport override %s -> %s",
                         self._config.transport.type,
                         detail_transport_type)
        self._anti_crawler = AntiCrawlerManager(
            self._config.anti_crawler,
            self._config.transport.captcha,
        )

        # Layer 2: Dedup
        self._state = StateManager(
            self._config.site_id, tenant_id,
            section=self._get_active_section_label(),
        )
        self._state.load()

        from .dedup_checker import DedupChecker
        # Collection mode writes to crawler_result (not bid_project), so
        # Layer 2's bid_project DB check must be skipped — otherwise URLs
        # that any bid-mode site already wrote are incorrectly flagged as
        # duplicates (gen_bid_id only hashes the URL, ignoring site_id).
        self._dedup_checker = DedupChecker(
            self._state, tenant_id,
            skip_db_check=(self._writer_mode != "bid"),
            site_id=self._config.site_id,
        )

        # Layer 3: Storage
        from .storage_pipeline import StoragePipeline
        self._storage_pipeline = StoragePipeline(
            kb_id=kb_id,
            tenant_id=tenant_id,
            parser_id=fmt_cfg.parser_id,
            site_id=self._config.site_id,
            task_name=self._task_name or "",
            output_dir=self._output_dir,
            skip_kb=skip_kb,
            skip_attachments=skip_attachments,
            writer_mode=getattr(self, "_writer_mode", "bid"),
            category=getattr(self, "_category", "bid"),
            task_id=getattr(self, "_task_id", ""),
            date_filter=getattr(self, "_date_filter", ""),
            site_display=self._build_site_display(),
        )

        self._batch_counter = 0

    # ------------------------------------------------------------------
    # Internal: crawl loop (three-layer separation)
    # ------------------------------------------------------------------

    def _crawl_sections(self) -> Dict[str, Any]:
        """Crawl all sections (or just the main listing)."""
        sections = self._config.sections
        if not sections:
            return self._crawl_one_section(None)

        total_stats = {"site_id": self._config.site_id, "sections": {}}
        all_new = 0
        acc_bid_stats: Dict[str, int] = {}
        acc_dedup_stats: Dict[str, int] = {}
        for section in sections:
            self._set_active_section(section)
            # Re-init dedup for the new section
            from .state_manager import StateManager
            from .dedup_checker import DedupChecker
            self._state = StateManager(
                self._config.site_id, self._state.tenant_id,
                section=section.label or "default",
            )
            self._state.load()
            # Per-section reset: full / date_filter reset must apply to EACH
            # section's state, not just the first one. Otherwise sections after
            # the first resume from a stale last_page and skip today's data.
            if getattr(self, "_full_crawl", False) or getattr(self, "_date_filter", ""):
                self._state.last_page = 0
                self._state.last_offset = 0
            self._dedup_checker = DedupChecker(
                self._state, self._state.tenant_id,
                skip_db_check=(self._writer_mode != "bid"),
                site_id=self._config.site_id,
            )
            stats = self._crawl_one_section(section)
            total_stats["sections"][section.label] = stats
            all_new += stats.get("new_items", 0)
            # Accumulate pipeline + dedup stats across sections
            for k, v in stats.get("bid_stats", {}).items():
                acc_bid_stats[k] = acc_bid_stats.get(k, 0) + v
            for k, v in stats.get("dedup_stats", {}).items():
                acc_dedup_stats[k] = acc_dedup_stats.get(k, 0) + v
        total_stats["total_new_items"] = all_new
        total_stats["bid_stats"] = acc_bid_stats
        total_stats["dedup_stats"] = acc_dedup_stats
        return total_stats

    def _crawl_one_section(self, section: Optional[SectionConfig]) -> Dict[str, Any]:
        """Crawl one section/listing using three-layer separation.

        Phase 1 (CRAWL): Fetch pages → extract items → produce raw dicts
        Phase 2 (DEDUP): Filter duplicates via DedupChecker
        Phase 3 (STORAGE): Write to bid tables, KB, handle attachments
        """
        # Resolve section-level config overrides
        parent_listing = self._config.listing
        parent_pagination = self._config.pagination
        parent_extract = self._config.extract
        parent_detail = self._config.detail

        if section:
            sl = section.listing
            # Section can override URL alone, or just params/body (e.g. change
            # noticeType while keeping parent's URL). Build a merged config so
            # section-level params are honored even without a section URL.
            if sl and (sl.url or sl.params or sl.body or sl.method):
                from dataclasses import replace as _dc_replace
                merged_params = dict(parent_listing.params or {})
                if sl.params:
                    merged_params.update(sl.params)
                merged_body = dict(parent_listing.body or {})
                if sl.body:
                    merged_body.update(sl.body)
                listing_cfg = _dc_replace(
                    parent_listing,
                    url=sl.url or parent_listing.url,
                    method=sl.method or parent_listing.method,
                    body_type=sl.body_type or parent_listing.body_type,
                    params=merged_params,
                    body=merged_body,
                )
            else:
                listing_cfg = parent_listing
            sp = section.pagination
            # Use section pagination when type differs OR items_field differs
            # (e.g. jrkb section: same single_page type but items_field="data"
            # vs parent's "result.records" — must override to parse correctly).
            pag_cfg = sp if (sp and (
                sp.type != parent_pagination.type
                or (sp.items_field and sp.items_field != parent_pagination.items_field)
            )) else parent_pagination
            se = section.extract
            extract_cfg = se if (se and se.fields and se.items_path) else parent_extract
            sd = section.detail
            detail_cfg = sd if (sd and sd.type) else parent_detail
        else:
            listing_cfg = parent_listing
            pag_cfg = parent_pagination
            extract_cfg = parent_extract
            detail_cfg = parent_detail

        state = self._state
        dedup = self._dedup_checker
        paginator = self._paginator
        anti_crawler = self._anti_crawler
        batch_size = self._config.format.upload_batch_size

        # If section overrides pagination, create a dedicated paginator
        if pag_cfg is not parent_pagination:
            paginator = PaginatorFactory.create(pag_cfg)

        # When a section uses a different items_field (e.g. jrkb "data" vs
        # parent "result.records"), inject it into page_params so the adapter
        # parses the JSON response with the correct path.
        _items_field_ovr = ""
        if pag_cfg is not parent_pagination and pag_cfg.items_field and \
                pag_cfg.items_field != parent_pagination.items_field:
            _items_field_ovr = pag_cfg.items_field

        # Create section-specific extractor
        section_extractor = ExtractorFactory.create(extract_cfg) if extract_cfg else None

        start_page = state.last_page + 1
        # paginator.start 是站点首PageIndex 的权威值（1-based 站点=1，
        # 0-based 站点=0，例如 EpointWebBuilder）。last_page=0 表示
        # 首次扫描或被 date_filter/full 重置，应用 config.start；
        # last_page>0 表示已扫到该页，从下一页继续。
        if state.last_page == 0:
            start_page = pag_cfg.start

        # --- Phase 1: CRAWL ---
        # Fetch first page to get total count
        logging.info("Engine: [CRAWL] fetching page %d to determine total...", start_page)
        first_params = next(paginator.pages(start_page), None)
        if first_params is None:
            return {"status": "empty", "new_items": 0}

        if _items_field_ovr:
            first_params["_items_field_override"] = _items_field_ovr
        items = self._adapter.fetch_items(first_params, listing_override=listing_cfg)
        if not items:
            logging.warning("Engine: [CRAWL] no data on first page, stopping.")
            return {"status": "empty", "new_items": 0}

        items = self._maybe_extract_from_html(items, section_extractor,
                                                base_url=listing_cfg.url)
        anti_crawler.delay()

        raw_data = self._adapter.last_raw
        total_count = paginator.update_total(raw_data if raw_data is not None else items)
        total_pages = paginator.total_pages_from_count(total_count)
        self._log_site_info(total_count, total_pages, start_page)

        # Main pagination loop — crawl all pages
        all_scanned = 0
        page = start_page
        stopped_early = False
        iteration = 0
        max_pages = pag_cfg.max_pages or DEFAULT_MAX_PAGES
        section_label = section.label if section else "default"

        while True:
            iteration += 1
            if iteration > MAX_CRAWL_ITERATIONS:
                logging.warning("Engine: safety limit reached (%d iterations), stopping.",
                              MAX_CRAWL_ITERATIONS)
                break

            # Fetch subsequent pages (first page already fetched)
            if page > start_page:
                page_params = next(paginator.pages(page), None)
                if page_params is None:
                    break
                anti_crawler.delay()
                if _items_field_ovr:
                    page_params["_items_field_override"] = _items_field_ovr
                items = self._adapter.fetch_items(page_params, listing_override=listing_cfg)
                if not items:
                    logging.info("Engine: [CRAWL] page %d empty, stopping.", page)
                    break
                items = self._maybe_extract_from_html(items, section_extractor,
                                                        base_url=listing_cfg.url)

            all_scanned += len(items)

            # Phase 2: DEDUP — filter new items
            new_in_page = 0
            for item in items:
                # Apply field mapping from extractor
                if section_extractor:
                    item = section_extractor.extract_fields(item)

                # Normalize relative URL to absolute using site_url.
                # Many listing APIs (EpointWebBuilder, TRS, custom CMS) return
                # relative paths like "/gcxx/001001/.../uuid.html"; storing
                # these directly causes frontend "原文链接" to 404 (需求: source
                # URL must be clickable). Only absolutize when site_url is set
                # and the URL is relative.
                raw_url = item.get("url") or item.get("href") or ""
                if raw_url and not isinstance(raw_url, str):
                    raw_url = str(raw_url)
                    item["url"] = raw_url
                if raw_url and not raw_url.startswith(("http://", "https://")):
                    site_root = self._config.site_url or ""
                    if site_root:
                        from urllib.parse import urljoin
                        item["url"] = urljoin(site_root, raw_url)

                item_id = self._get_item_id(item)
                url = item.get("url") or item.get("href") or ""

                dup = dedup.is_duplicate(item_id, url=url)
                if dup:
                    continue

                new_in_page += 1

                # Fetch detail if configured
                if detail_cfg.type not in ("inline", "none", ""):
                    detail_adapter = self._detail_adapter or self._adapter
                    detail_result = detail_adapter.fetch_detail(item, detail_override=detail_cfg)
                    if detail_result:
                        item = detail_result

                # Extract file URLs from detail HTML so attachments flow to KB
                self._extract_files_from_item(item)

                # Inject section metadata so CollectionWriter can surface it
                # to extracted_json (frontend 类型 column reads section_name).
                if section:
                    item["section_label"] = section.label
                    item["section_name"] = section.name or section.label

                # Phase 3: STORAGE — write to all targets
                normalized = item_from_dict(item, site_id=self._config.site_id,
                                            section=section_label)
                storage_result = self._storage_pipeline.store(normalized)

                # Mark processed in both layers
                dedup.mark_processed(item_id)

            if new_in_page == 0:
                anti_crawler.record_empty_page()
                if anti_crawler.consecutive_empty >= self._config.anti_crawler.max_consecutive_empty:
                    logging.info("Engine: %d consecutive empty pages, stopping early.",
                                 anti_crawler.consecutive_empty)
                    stopped_early = True
                    break
            else:
                anti_crawler.record_new_items()

            # Checkpoint: save state every page
            state.last_page = page
            # Reporter: per-page progress to WebSocket (print stays 10-page to avoid stdout spam)
            if self._reporter is not None and self._reporter.enabled:
                self._reporter.publish_progress(page, total_pages, new_in_page, all_scanned)
            if page % 10 == 0:
                self._log_progress(page, new_in_page, all_scanned, anti_crawler.consecutive_empty)
            if page % 10 == 0 or new_in_page > 0:
                state.save()

            # Stop conditions
            if max_pages > 0 and page >= max_pages:
                break
            if total_pages > 0 and page > total_pages:
                break
            if pag_cfg.type == "single_page":
                break

            page += 1

        # Final checkpoint — save processed IDs but keep last_page
        state.save()

        # Collect stats
        pipeline_stats = self._storage_pipeline.stats
        dedup_stats = dedup.stats

        return {
            "status": "completed",
            "scanned_pages": page - start_page + 1,
            "scanned_items": all_scanned,
            "new_items": pipeline_stats.get("items_stored", 0),
            "stopped_early": stopped_early,
            "bid_stats": pipeline_stats,
            "dedup_stats": dedup_stats,
        }

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _maybe_extract_from_html(
        self, items: List[Dict[str, Any]], extractor: Any,
        base_url: str = ""
    ) -> List[Dict[str, Any]]:
        """If the adapter returned raw HTML, use the CSS extractor to parse it."""
        if (
            extractor is not None
            and len(items) == 1
            and "html" in items[0]
        ):
            raw_html = items[0]["html"]
            url = base_url or self._config.listing.url
            extracted = extractor.extract(raw_html, base_url=url)
            if extracted:
                logging.info(
                    "Engine: extracted %d items from raw HTML via CSS extractor",
                    len(extracted),
                )
                return extracted
        return items

    def _get_item_id(self, item: Dict[str, Any]) -> str:
        """Extract unique item ID from various possible fields."""
        for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                    "bulletinID", "guid", "_id", "url", "href"):
            val = item.get(key, "")
            if val:
                return str(val)
        return ""

    def _extract_files_from_item(self, item: Dict[str, Any]) -> None:
        """Extract file download URLs from detail HTML and add as 'files' key.

        Crawler detail adapters only extract text content, never file/attachment
        info. This scans the HTML for <a> links to known file types (PDF, DOC, etc.)
        and adds them so _extract_attachments() → StoragePipeline._handle_attachments()
        can download and upload them to KB.

        Mirrors bid_tool_service._extract_file_urls_from_html().
        """
        # Skip if files/attachments already present (e.g. from API JSON response)
        if item.get("files") or item.get("attachments") or item.get("fileList"):
            return

        # Look for HTML content in various keys.
        # `content` is included because most API adapters (encrypted_api,
        # rest_api) store the decrypted HTML payload under this key, and
        # for many government sites that HTML embeds <a> file download
        # links (PDF/DOC/ZIP) which we want to pick up here.
        html = (item.get("content_html") or item.get("detail_html") or
                item.get("html") or item.get("detail") or
                item.get("content") or "")
        if not html or not isinstance(html, str):
            return

        # Resolve relative URLs against the item's own URL
        base_url = item.get("url") or ""

        import re
        from urllib.parse import unquote, urljoin

        file_ext_pattern = (
            r'\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|'
            r'txt|cad|dwg|jpg|jpeg|png|gif|bmp)(\?|$)'
        )
        results = []
        seen_urls = set()
        for match in re.finditer(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>',
            html, re.IGNORECASE,
        ):
            url = match.group(1).strip()
            text = match.group(2).strip() or unquote(url.rsplit("/", 1)[-1])
            url_lower = url.lower()
            if (re.search(file_ext_pattern, url_lower)
                    or re.search(file_ext_pattern, text.lower())):
                # Resolve relative URLs against the item's base URL
                if base_url and not url.startswith("http"):
                    url = urljoin(base_url, url)
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append({"file_name": text, "file_url": url})

        # Fallback: when no href-based file links are found, scan onclick handlers.
        # Many Chinese gov CMS (EpointWebBuilder, TRS) hide attachment downloads
        # behind JS handlers like <a onclick="ztbfjyz('/zzggzy/...')">name</a>
        # where the <a> tag has NO href attribute. Without this fallback all
        # attachments on such sites are silently dropped.
        if not results:
            onclick_patterns = [
                r"""ztbfjyz\s*\(\s*['"]([^'"]+)['"]""",        # EpointWebBuilder (漳州 etc.)
                r"""window\.open\s*\(\s*['"]([^'"]+)['"]""",   # 通用 window.open
                r"""location\.href\s*=\s*['"]([^'"]+)['"]""",  # 通用 location.href
                r"""downloadFile\s*\(\s*['"]([^'"]+)['"]""",   # 通用 downloadFile
            ]
            # 用 DOTALL 的内层 capture：允许 <a onclick="..."> 内部嵌套 <div> 等子元素
            # （EpointWebBuilder 模板：<a class="item" onclick="ztbfjyz(...)"><div
            # class="item-name">filename.pdf</div></a>）。原 `([^<]*)` 会因 <div> 失配。
            # 用 backreference 捕获 onclick 属性值：双引号属性里可嵌单引号
            # （EpointWebBuilder: onclick="ztbfjyz('/path','1','1')"）。
            # 简单的 `["']([^"']+)["']` 会因内层 ' 而截断。
            for a_match in re.finditer(
                r'<a\b([^>]*?)onclick=(["\'])(.*?)\2([^>]*)>(.*?)</a>',
                html, re.IGNORECASE | re.DOTALL,
            ):
                pre_attrs, _q, onclick_code, post_attrs, inner_html = a_match.groups()
                # 链接文本：先剥嵌套标签，再清空白
                link_text = re.sub(r"<[^>]+>", "", inner_html).strip()
                # fallback 到 <a title="..."> 属性（EpointWebBuilder 在该属性里放文件名）
                if not link_text:
                    m_title = re.search(r'title=["\']([^"\']+)["\']',
                                        pre_attrs + post_attrs, re.IGNORECASE)
                    if m_title:
                        link_text = m_title.group(1).strip()
                if not link_text:
                    link_text = f"attachment_{len(results) + 1}"
                path = ""
                for pattern in onclick_patterns:
                    m = re.search(pattern, onclick_code)
                    if m:
                        path = m.group(1)
                        break
                if not path:
                    continue
                fname = link_text or unquote(path.rsplit("/", 1)[-1])
                # Resolve relative path against the item's base URL
                if base_url and not path.startswith("http"):
                    path = urljoin(base_url, path)
                path_lower = path.lower()
                # Accept only file-like links: either has a file extension, or
                # matches a known CMS attachment path prefix (avoids treating
                # JS navigation handlers like ztbfjyz('/xx/jump') as files).
                # EpointWebBuilder 实际下载 URL：
                #   /EpointWebBuilder/pages/webbuildermis/attach/downloadztbattach?attachGuid=...
                if (re.search(file_ext_pattern, path_lower)
                        or re.search(file_ext_pattern, fname.lower())
                        or "/zzggzy/" in path
                        or "/attach/download" in path_lower
                        or "attachguid=" in path_lower):
                    if path not in seen_urls:
                        seen_urls.add(path)
                        results.append({"file_name": fname, "file_url": path})

        if results:
            item["files"] = results
            logging.info(
                "Engine: extracted %d file URLs from detail HTML for item %s",
                len(results), item.get("title", "")[:60],
            )

    def _get_active_section_label(self) -> str:
        return getattr(self, "_active_section_label", "default")

    def _inject_date_filter_to_listing(self, date_filter: str) -> None:
        """When date_filter is set, inject it into listing.params.startdate / enddate
        if the site declares those keys (empty string or absent value).

        Many Chinese gov CMS APIs (EpointWebBuilder, TRS) accept a date window
        in the listing request. Without it, they return unordered history data,
        causing the CollectionWriter's per-item date_filter to reject everything
        in the first N pages — observed on gcjyzx.zhangzhou.gov.cn where 600
        recent items contained 0 matching the target day.

        Behavior:
        - Only inject if listing.params already has 'startdate' / 'enddate' keys
          (i.e. site opted in by declaring them, even with empty value).
        - Range: [date_filter 00:00:00, date_filter 23:59:59]
        - Falls back to '{date} 00:00:00' / '{date} 23:59:59' format which most
          EpointWebBuilder endpoints accept.
        """
        if not date_filter:
            return
        params = self._config.listing.params or {}
        # Also consider section-level listing override (sections may declare
        # their own params dict).
        targets = [params]
        for sec in (self._config.sections or []):
            if sec.listing and sec.listing.params:
                targets.append(sec.listing.params)
        for p in targets:
            if "startdate" in p or "enddate" in p:
                p["startdate"] = f"{date_filter} 00:00:00"
                p["enddate"] = f"{date_filter} 23:59:59"
                logging.info(
                    "Engine: date_filter injected into listing params "
                    "(startdate=%s, enddate=%s)",
                    p["startdate"], p["enddate"],
                )

    def _set_active_section(self, section: SectionConfig) -> None:
        self._active_section_label = section.label or "default"

    def _cleanup(self) -> None:
        """Release all resources."""
        if self._adapter:
            self._adapter.cleanup()
        if self._detail_adapter:
            self._detail_adapter.cleanup()
        if self._storage_pipeline:
            self._storage_pipeline.cleanup()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_header(self, kb_id: str = "") -> None:
        cfg = self._config
        print("\n" + "=" * 60)
        print(f"[CRAWLER] {cfg.name} ({cfg.site_id})")
        print(f"[CRAWLER] Transport: {cfg.transport.type}")
        print(f"[CRAWLER] KB: {kb_id}")
        print("=" * 60 + "\n")
        sys.stdout.flush()
        self._report(f"[CRAWLER] {cfg.name} ({cfg.site_id}) · transport={cfg.transport.type} · kb={kb_id}")

    def _log_site_info(self, total: int, total_pages: int, start_page: int) -> None:
        print(f"[CRAWLER] Total: {total} records, {total_pages} pages "
              f"(resuming from page {start_page})")
        sys.stdout.flush()
        self._report(f"共 {total} 条 / {total_pages} 页，从第 {start_page} 页续爬")

    def _build_site_display(self) -> str:
        """从 YAML 配置派生展示用站点串: '中文名称 域名'.

        - 名称取 self._config.name, 缺失时回退到 site_id
        - 域名从 self._config.site_url 的 netloc 提取 (如 https://x.gov.cn/ → x.gov.cn)
        - 两者拼接用单空格分隔；任一缺失时只返回另一部分
        """
        name = (getattr(self._config, "name", "") or self._config.site_id).strip()
        site_url = (getattr(self._config, "site_url", "") or "").strip()
        domain = ""
        if site_url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(site_url).netloc
            except Exception:
                domain = ""
        if name and domain:
            return f"{name} {domain}"
        return name or domain

    def _log_progress(self, page: int, new_in_page: int, scanned: int,
                      stale: int) -> None:
        print(f"[CRAWLER]   Page {page}: {new_in_page} new / "
              f"{scanned} scanned (stale streak={stale})")
        sys.stdout.flush()

    def _log_footer(self, summary: Dict[str, Any]) -> None:
        print("\n" + "=" * 60)
        status = summary.get("status", "unknown")
        if status == "skipped":
            print(f"[CRAWLER] Skipped: {summary.get('reason', '')}")
        else:
            total_new = summary.get("total_new_items",
                                    summary.get("new_items", 0))
            print(f"[CRAWLER] Done: {total_new} new items")
            bid_stats = summary.get("bid_stats", {})
            if isinstance(bid_stats, dict):
                print(f"[CRAWLER] DB: {bid_stats.get('bid_written', 0)} bid / "
                      f"{bid_stats.get('kb_uploaded', 0)} KB / "
                      f"{bid_stats.get('attachments_uploaded', 0)} attachments")
            dedup_stats = summary.get("dedup_stats", {})
            if isinstance(dedup_stats, dict):
                print(f"[CRAWLER] Dedup: {dedup_stats.get('memory_hits', 0)} mem / "
                      f"{dedup_stats.get('db_hits', 0)} DB hits")
        print("=" * 60 + "\n")
        sys.stdout.flush()
        # Publish done event to WebSocket subscribers
        if self._reporter is not None and self._reporter.enabled:
            done_status = "skipped" if status == "skipped" else "success"
            self._reporter.publish_done(done_status, summary)
            self._report(f"[CRAWLER] Done · status={done_status} · summary={summary}")

    def _report(self, text: str, level: str = "info") -> None:
        """Forward a log line to the progress reporter (no-op if absent)."""
        if self._reporter is not None and self._reporter.enabled:
            self._reporter.publish_log(text, level)
