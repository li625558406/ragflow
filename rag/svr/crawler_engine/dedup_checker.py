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
                 skip_db_check: bool = False):
        """Initialize dedup checker.

        Args:
            state_manager: StateManager instance for in-memory ID tracking.
            tenant_id: Tenant ID (used for DB queries).
            skip_db_check: When True, only Layer 1 (memory) check is performed.
                Used by collection writer mode — those items live in
                ``crawler_result`` (not ``bid_project``), so Layer 2's
                bid_project lookup would false-positive on URLs that an
                unrelated bid-mode site already wrote.
        """
        self._state = state_manager
        self._tenant_id = tenant_id
        self._skip_db_check = skip_db_check
        self._db_checked: Set[int] = set()   # cached project_ids already queried
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

        # Layer 2: DB check (covers historical data) — bid mode only.
        # Collection mode writes to crawler_result, not bid_project, so
        # checking bid_project against the same URL (gen_bid_id only hashes
        # the URL, not site_id) would mark unrelated URLs as duplicates.
        if self._skip_db_check:
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
