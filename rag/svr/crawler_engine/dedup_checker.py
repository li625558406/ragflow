"""
Dual-layer dedup checker for the unified crawler framework.

Layer 1: In-memory StateManager check (fast, O(1))
Layer 2: bid_project DB table check (covers historical data from old scripts)

The DB check is cached per-session so each project_id is only queried once.
When a duplicate is found via DB, it's synced back to StateManager for
future fast checks.
"""

import logging
from typing import Optional, Set

from .bid_writer import gen_bid_id


class DedupChecker:
    """Dual-layer dedup: memory check + DB存量 check."""

    # Max cached DB query results — prevents unbounded memory growth
    _MAX_DB_CACHE = 50000

    def __init__(self, state_manager, tenant_id: str = "",
                 skip_db_check: bool = False, site_id: str = ""):
        """Initialize dedup checker.

        Args:
            state_manager: StateManager instance for in-memory ID tracking.
            tenant_id: Tenant ID (used for DB queries).
            skip_db_check: When True, only Layer 1 (memory) check is performed
                on the bid_project table. Collection mode still performs a
                Layer 2 check against crawler_result (keyed by site_id|url).
            site_id: Site ID, required for collection-mode Layer 2 check.
        """
        self._state = state_manager
        self._tenant_id = tenant_id
        self._skip_db_check = skip_db_check
        self._site_id = site_id
        self._db_checked: Set[int] = set()   # cached project_ids already queried
        self._coll_checked: Set[str] = set()  # cached crawler_result ids
        self._db_hits: int = 0                # count of DB-layer dedup hits
        self._memory_hits: int = 0             # count of memory-layer dedup hits

    def is_duplicate(self, item_id: str, url: str = "") -> bool:
        """Check if an item has already been processed.

        Args:
            item_id: Unique item identifier (URL string or API ID).
            url: Source URL (used for bid_project table lookup).

        Returns:
            True if the item is a duplicate and should be skipped.
        """
        if not item_id:
            return False

        # Layer 1: Memory check (fast)
        if self._state.is_processed(item_id):
            self._memory_hits += 1
            return True

        # Layer 2: DB check.
        # - bid mode: lookup bid_project by gen_bid_id(url)
        # - collection mode: lookup crawler_result by md5(site_id|url)
        #   (gen_bid_id only hashes URL without site_id, so a bid-mode site
        #   writing the same URL would false-positive; crawler_result's PK
        #   includes site_id, which is the correct scope for collection.)
        if self._skip_db_check:
            # Collection mode — check crawler_result instead of bid_project.
            if self._site_id and url:
                result_id = self._gen_result_id(self._site_id, url)
                if result_id in self._coll_checked:
                    # Already verified absent; not a dup.
                    return False
                if self._exists_in_crawler_result(result_id):
                    self._coll_checked.add(result_id)
                    self._db_hits += 1
                    self._state.mark_processed(item_id)
                    return True
                self._coll_checked.add(result_id)
                if len(self._coll_checked) > self._MAX_DB_CACHE:
                    self._coll_checked.clear()
            return False

        if url:
            project_id = gen_bid_id(url)
            if project_id not in self._db_checked:
                if self._exists_in_bid_project(project_id):
                    self._db_checked.add(project_id)
                    self._db_hits += 1
                    # Sync back to state so future checks are fast
                    self._state.mark_processed(item_id)
                    return True
                self._db_checked.add(project_id)
                # Prevent unbounded growth
                if len(self._db_checked) > self._MAX_DB_CACHE:
                    self._db_checked.clear()

        return False

    def mark_processed(self, item_id: str) -> None:
        """Mark an item as processed (after successful storage)."""
        self._state.mark_processed(item_id)

    def mark_batch_processed(self, item_ids: list) -> None:
        """Mark multiple items as processed."""
        self._state.mark_batch_processed(item_ids)

    @property
    def stats(self) -> dict:
        return {
            "memory_hits": self._memory_hits,
            "db_hits": self._db_hits,
            "db_queries": len(self._db_checked),
        }

    def _exists_in_bid_project(self, project_id: int) -> bool:
        """Check if bid_project table already has a record for this ID."""
        try:
            from api.db.services.bid_service import BidProjectService
            result = BidProjectService.get_by_project_id(project_id)
            return result is not None
        except Exception as e:
            logging.debug("DedupChecker: bid_project query failed for id=%d: %s",
                          project_id, e)
            return False

    @staticmethod
    def _gen_result_id(site_id: str, source_url: str) -> str:
        """md5(site_id|source_url) — matches CrawlerResultService.gen_result_id."""
        import hashlib
        return hashlib.md5(f"{site_id}|{source_url}".encode("utf-8")).hexdigest()

    def _exists_in_crawler_result(self, result_id: str) -> bool:
        """Check if crawler_result table already has a row for this PK."""
        try:
            from api.db.db_models import CrawlerResult
            return CrawlerResult.select().where(CrawlerResult.id == result_id).exists()
        except Exception as e:
            logging.debug("DedupChecker: crawler_result query failed for id=%s: %s",
                          result_id, e)
            return False
