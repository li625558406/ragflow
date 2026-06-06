"""
CrawlerEngine — main orchestrator for the unified crawler framework.

Ties together adapter, paginator, extractor, formatter, anti-crawler
manager, state manager, and KB uploader into a single run() method.

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
    from rag.utils.redis_conn import RedisDistributedLock
except ImportError:
    RedisDistributedLock = None  # type: ignore

from .config import SiteConfig, SectionConfig
from .paginator import PaginatorFactory, BasePaginator
from .anti_crawler import AntiCrawlerManager
from .formatter import MarkdownFormatter
from .adapters.base import BaseAdapter, AdapterFactory
from .extractors.base import ExtractorFactory

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
        self._adapter: Optional[BaseAdapter] = None
        self._paginator: Optional[BasePaginator] = None
        self._anti_crawler: Optional[AntiCrawlerManager] = None
        self._formatter: Optional[MarkdownFormatter] = None
        self._uploader: Optional[KBUploader] = None
        self._bid_writer: Optional[BidWriter] = None
        self._state: Optional[StateManager] = None
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
    ) -> Dict[str, Any]:
        """Run a full crawl cycle for the configured site.

        Returns a summary dict with statistics.
        """
        os.makedirs(self._output_dir, exist_ok=True)

        # Acquire distributed lock to prevent concurrent execution of the
        # same site+tenant.  The lock auto-expires after 2 hours so a
        # crashed process won't permanently block re-crawling.
        if RedisDistributedLock is not None:
            lock_key = f"crawler_engine:{tenant_id}:{self._config.site_id}"
            lock = RedisDistributedLock(lock_key, timeout=7200, blocking_timeout=0)
            if not lock.acquire():
                logging.warning(
                    "Engine: site=%s tenant=%s is already being crawled, skipping.",
                    self._config.site_id, tenant_id,
                )
                return {"status": "skipped", "reason": "already_running"}
            try:
                return self._run_locked(tenant_id, kb_id, task_name, full)
            finally:
                lock.release()
        else:
            return self._run_locked(tenant_id, kb_id, task_name, full)

    def _run_locked(
        self,
        tenant_id: str,
        kb_id: str,
        task_name: str = "",
        full: bool = False,
    ) -> Dict[str, Any]:
        """Internal: run after acquiring the distributed lock."""
        self._init_components(kb_id, tenant_id)
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

    def _init_components(self, kb_id: str, tenant_id: str) -> None:
        """Initialize all sub-components."""
        # Lazy imports for DB-dependent modules
        from .state_manager import StateManager
        from .kb_uploader import KBUploader
        from .bid_writer import BidWriter

        fmt_cfg = self._config.format

        self._adapter = AdapterFactory.create(self._config)
        self._paginator = PaginatorFactory.create(self._config.pagination)
        self._anti_crawler = AntiCrawlerManager(
            self._config.anti_crawler,
            self._config.transport.captcha,
        )
        self._formatter = MarkdownFormatter(
            template=fmt_cfg.template,
            title_field=fmt_cfg.title_field,
            date_field=fmt_cfg.date_field,
            parser_id=fmt_cfg.parser_id,
        )
        self._uploader = KBUploader(kb_id, tenant_id, fmt_cfg.parser_id)
        self._bid_writer = BidWriter(kb_id, tenant_id)
        self._state = StateManager(
            self._config.site_id, tenant_id,
            section=self._get_active_section_label(),
        )
        self._state.load()
        self._batch_counter = 0

    # ------------------------------------------------------------------
    # Internal: crawl loop
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
            # Re-init state for the new section
            from .state_manager import StateManager
            self._state = StateManager(
                self._config.site_id, self._state.tenant_id,
                section=section.label or "default",
            )
            self._state.load()
            stats = self._crawl_one_section(section)
            total_stats["sections"][section.label] = stats
            all_new += stats.get("new_items", 0)
        total_stats["total_new_items"] = all_new
        return total_stats

    def _crawl_one_section(self, section: Optional[SectionConfig]) -> Dict[str, Any]:
        """Crawl one section/listing from start to finish."""
        # Merge section overrides with parent config — section fields that are
        # explicitly set override the parent, otherwise inherit from parent.
        parent_listing = self._config.listing
        parent_pagination = self._config.pagination
        parent_extract = self._config.extract

        if section:
            sl = section.listing
            listing_cfg = sl if (sl and sl.url) else parent_listing
            sp = section.pagination
            pag_cfg = sp if (sp and sp.type != parent_pagination.type) else parent_pagination
            se = section.extract
            extract_cfg = se if (se and (se.fields or se.type != parent_extract.type or se.items_path)) else parent_extract
        else:
            listing_cfg = parent_listing
            pag_cfg = parent_pagination
            extract_cfg = parent_extract

        state = self._state
        paginator = self._paginator
        anti_crawler = self._anti_crawler
        fmt_cfg = self._config.format
        batch_size = fmt_cfg.upload_batch_size

        # If section overrides pagination, create a dedicated paginator
        if pag_cfg is not parent_pagination:
            paginator = PaginatorFactory.create(pag_cfg)

        # Create section-specific extractor once (not per-item)
        # Always create for field mapping; json_path needs it just as much as css_selector
        section_extractor = ExtractorFactory.create(extract_cfg) if extract_cfg else None

        start_page = state.last_page + 1

        # Fetch first page to get total count
        logging.info("Engine: fetching page %d to determine total...", start_page)
        first_params = next(paginator.pages(start_page), None)
        if first_params is None:
            return {"status": "empty", "new_items": 0}

        items = self._adapter.fetch_items(first_params, listing_override=listing_cfg)
        if not items:
            logging.warning("Engine: no data on first page, stopping.")
            return {"status": "empty", "new_items": 0}

        anti_crawler.delay()

        # Use the raw response data from the adapter to get total count
        raw_data = self._adapter.last_raw
        total_count = paginator.update_total(raw_data if raw_data is not None else items)
        total_pages = paginator.total_pages_from_count(total_count)
        self._log_site_info(total_count, total_pages, start_page)

        # Main pagination loop
        new_items: List[Dict[str, Any]] = []
        all_scanned = 0
        page = start_page
        stopped_early = False
        last_uploaded_batch = 0
        iteration = 0
        max_pages = pag_cfg.max_pages

        while True:
            iteration += 1
            if iteration > MAX_CRAWL_ITERATIONS:
                logging.warning("Engine: safety limit reached (%d iterations), stopping.",
                              MAX_CRAWL_ITERATIONS)
                break

            # Fetch page (first page already fetched above)
            if page > start_page:
                page_params = next(paginator.pages(page), None)
                if page_params is None:
                    break
                anti_crawler.delay()
                items = self._adapter.fetch_items(page_params, listing_override=listing_cfg)
                if not items:
                    logging.info("Engine: page %d empty, stopping.", page)
                    break

            all_scanned += len(items)
            new_in_page = 0

            for item in items:
                # Extract fields using section-specific extractor
                if section_extractor:
                    item = section_extractor.extract_fields(item)

                # Dedup
                item_id = self._get_item_id(item)
                if item_id and state.is_processed(item_id):
                    continue

                new_in_page += 1

                # Fetch detail if configured
                if self._config.detail.type not in ("inline", "none"):
                    detail_result = self._adapter.fetch_detail(item)
                    if detail_result:
                        item = detail_result

                # Write to bid database tables (error-isolated, won't block crawl)
                if self._bid_writer:
                    self._bid_writer.write_all(item, self._config.site_id)

                new_items.append(item)
                if item_id:
                    state.mark_processed(item_id)

            if new_in_page == 0:
                anti_crawler.record_empty_page()
                if anti_crawler.consecutive_empty >= self._config.anti_crawler.max_consecutive_empty:
                    logging.info("Engine: %d consecutive empty pages, stopping early.",
                                 anti_crawler.consecutive_empty)
                    stopped_early = True
                    break
            else:
                anti_crawler.record_new_items()

            # Checkpoint: save state + upload batches
            if page % 100 == 0 or page == start_page:
                self._log_progress(page, new_in_page, all_scanned, anti_crawler.consecutive_empty)
                state.last_page = page
                state.save()

                # Upload completed batches
                while len(new_items) - last_uploaded_batch >= batch_size:
                    batch_end = last_uploaded_batch + batch_size
                    self._batch_counter += 1
                    self._upload_batch(new_items[last_uploaded_batch:batch_end],
                                       self._batch_counter)
                    last_uploaded_batch = batch_end

            # Stop if we've hit the configured max pages
            if max_pages > 0 and page >= max_pages:
                break

            page += 1

        # Final checkpoint
        state.last_page = 0  # reset for next run
        state.save()

        # Upload remaining batches
        self._upload_remaining(new_items, last_uploaded_batch, batch_size)

        return {
            "status": "completed",
            "scanned_pages": page - start_page + 1,
            "scanned_items": all_scanned,
            "new_items": len(new_items),
            "stopped_early": stopped_early,
            "bid_stats": self._bid_writer.stats if self._bid_writer else {},
        }

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _get_item_id(self, item: Dict[str, Any]) -> str:
        """Extract unique item ID from various possible fields."""
        for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                    "bulletinID", "guid", "_id", "url", "href"):
            val = item.get(key, "")
            if val:
                return str(val)
        return ""

    def _get_active_section_label(self) -> str:
        """Get the currently active section label."""
        return getattr(self, "_active_section_label", "default")

    def _set_active_section(self, section: SectionConfig) -> None:
        """Set the active section for state management."""
        self._active_section_label = section.label or "default"

    def _upload_batch(self, items: List[Dict[str, Any]], batch_num: int) -> None:
        """Format and upload a batch of items."""
        if not items:
            return
        fmt_cfg = self._config.format
        fn = fmt_cfg.output_filename_pattern.format(
            site_id=self._config.site_id, batch_num=batch_num
        )
        path = os.path.join(self._output_dir, fn)
        self._formatter.write_batch(items, path)
        if self._uploader:
            try:
                self._uploader.upload_file(path)
            except Exception as e:
                logging.error("Engine: batch %d upload failed: %s", batch_num, e)

    def _upload_remaining(self, items: List[Dict[str, Any]],
                          last_batch: int, batch_size: int) -> None:
        """Upload any remaining items that haven't been uploaded yet."""
        while last_batch < len(items):
            batch_end = min(last_batch + batch_size, len(items))
            self._batch_counter += 1
            self._upload_batch(items[last_batch:batch_end], self._batch_counter)
            last_batch = batch_end

    def _cleanup(self) -> None:
        """Release all resources."""
        if self._adapter:
            self._adapter.cleanup()

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
            if bid_stats:
                print(f"[CRAWLER] DB: {bid_stats.get('projects_new', 0)} new / "
                      f"{bid_stats.get('projects_updated', 0)} updated projects, "
                      f"{bid_stats.get('details_written', 0)} details, "
                      f"{bid_stats.get('files_written', 0)} files")
        print("=" * 60 + "\n")
        sys.stdout.flush()
