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

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "output"
)

# Safety limit: max crawl iterations before forced stop
MAX_CRAWL_ITERATIONS = 10000


class CrawlerEngine:
    """Main engine that orchestrates a complete crawl cycle for one site."""

    def __init__(self, config: SiteConfig, output_dir: Optional[str] = None):
        self._config = config
        self._output_dir = output_dir or os.path.join(OUTPUT_DIR, config.site_id)
        self._task_name: str = ""
        # Layer 1: Crawl components
        self._adapter: Optional[BaseAdapter] = None
        self._paginator: Optional[BasePaginator] = None
        self._anti_crawler: Optional[AntiCrawlerManager] = None
        # Layer 2: Dedup
        self._dedup_checker = None
        # Layer 3: Storage
        self._storage_pipeline = None
        self._state = None
        self._batch_counter: int = 0

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
    ) -> Dict[str, Any]:
        """Run a full crawl cycle for the configured site.

        Returns a summary dict with statistics.
        """
        self._task_name = task_name
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

        if full:
            self._state.reset()

        # 8 AM check
        if not self._anti_crawler.check_eight_am():
            logging.info("Engine: waiting until 8 AM for site %s", self._config.site_id)
            return {"status": "skipped", "reason": "before_8am"}

        # Main crawl loop
        try:
            summary = self._crawl_sections()
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
        self._dedup_checker = DedupChecker(self._state, tenant_id)

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
            self._dedup_checker = DedupChecker(self._state, self._state.tenant_id)
            stats = self._crawl_one_section(section)
            total_stats["sections"][section.label] = stats
            all_new += stats.get("new_items", 0)
        total_stats["total_new_items"] = all_new
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

        if section:
            sl = section.listing
            listing_cfg = sl if (sl and sl.url) else parent_listing
            sp = section.pagination
            pag_cfg = sp if (sp and sp.type != parent_pagination.type) else parent_pagination
            se = section.extract
            extract_cfg = se if (se and se.fields and se.items_path) else parent_extract
        else:
            listing_cfg = parent_listing
            pag_cfg = parent_pagination
            extract_cfg = parent_extract

        state = self._state
        dedup = self._dedup_checker
        paginator = self._paginator
        anti_crawler = self._anti_crawler
        batch_size = self._config.format.upload_batch_size

        # If section overrides pagination, create a dedicated paginator
        if pag_cfg is not parent_pagination:
            paginator = PaginatorFactory.create(pag_cfg)

        # Create section-specific extractor
        section_extractor = ExtractorFactory.create(extract_cfg) if extract_cfg else None

        start_page = state.last_page + 1

        # --- Phase 1: CRAWL ---
        # Fetch first page to get total count
        logging.info("Engine: [CRAWL] fetching page %d to determine total...", start_page)
        first_params = next(paginator.pages(start_page), None)
        if first_params is None:
            return {"status": "empty", "new_items": 0}

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
        max_pages = pag_cfg.max_pages
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

                item_id = self._get_item_id(item)
                url = item.get("url") or item.get("href") or ""

                if dedup.is_duplicate(item_id, url=url):
                    continue

                new_in_page += 1

                # Fetch detail if configured
                if self._config.detail.type not in ("inline", "none"):
                    detail_result = self._adapter.fetch_detail(item)
                    if detail_result:
                        item = detail_result

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

            # Checkpoint: save state periodically
            if page % 100 == 0 or page == start_page:
                self._log_progress(page, new_in_page, all_scanned, anti_crawler.consecutive_empty)
                state.last_page = page
                state.save()

            # Stop conditions
            if max_pages > 0 and page >= max_pages:
                break
            if pag_cfg.type == "single_page":
                break

            page += 1

        # Final checkpoint
        state.last_page = 0  # reset for next run
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

    def _get_active_section_label(self) -> str:
        return getattr(self, "_active_section_label", "default")

    def _set_active_section(self, section: SectionConfig) -> None:
        self._active_section_label = section.label or "default"

    def _cleanup(self) -> None:
        """Release all resources."""
        if self._adapter:
            self._adapter.cleanup()
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

    def _log_site_info(self, total: int, total_pages: int, start_page: int) -> None:
        print(f"[CRAWLER] Total: {total} records, {total_pages} pages "
              f"(resuming from page {start_page})")
        sys.stdout.flush()

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
