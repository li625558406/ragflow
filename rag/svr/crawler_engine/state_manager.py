"""
Database-backed crawler state manager.

Replaces the per-crawler JSON file state (_crawler_state.json) with
a centralized DB table (crawler_state).  Supports:
- Processed ID tracking for dedup
- Page/offset progress for resume
- Extra state for complex crawlers
"""

import logging
from typing import Any, Dict, List, Optional, Set

from common.misc_utils import get_uuid
from api.db.db_models import DB, CrawlerState


# Maximum number of processed IDs to keep in memory/DB.
# When exceeded, oldest entries are trimmed on next save.
# This prevents JSONField bloat while keeping recent dedup working.
MAX_PROCESSED_IDS = 10000


class StateManager:
    """Manages crawler state persistence via the crawler_state DB table."""

    def __init__(self, site_id: str, tenant_id: str, section: str = "default"):
        self.site_id = site_id
        self.tenant_id = tenant_id
        self.section = section
        self._processed_ids: Set[str] = set()
        self._last_page: int = 0
        self._last_offset: int = 0
        self._extra: Dict[str, Any] = {}
        self._loaded = False

    # ---- Public API ----

    @DB.connection_context()
    def load(self) -> "StateManager":
        """Load state from DB. Returns self for chaining."""
        try:
            row = (CrawlerState
                   .select()
                   .where(
                       (CrawlerState.site_id == self.site_id) &
                       (CrawlerState.tenant_id == self.tenant_id) &
                       (CrawlerState.section == self.section)
                   ).first())
        except Exception as e:
            logging.warning("StateManager.load query failed: %s, using empty state", e)
            row = None

        if row:
            self._processed_ids = set(row.processed_ids or [])
            self._last_page = row.last_page or 0
            self._last_offset = row.last_offset or 0
            self._extra = row.extra_state or {}
        else:
            self._processed_ids = set()
            self._last_page = 0
            self._last_offset = 0
            self._extra = {}

        self._loaded = True
        logging.debug("StateManager loaded: %d IDs, page=%d, offset=%d",
                       len(self._processed_ids), self._last_page, self._last_offset)
        return self

    @DB.connection_context()
    def save(self) -> None:
        """Persist current state to DB.

        Automatically trims processed_ids if exceeding MAX_PROCESSED_IDS
        to prevent JSONField bloat in the database.
        """
        # Trim oldest entries if over capacity
        if len(self._processed_ids) > MAX_PROCESSED_IDS:
            excess = len(self._processed_ids) - MAX_PROCESSED_IDS
            # Convert to sorted list and remove oldest (set order is insertion order)
            sorted_ids = sorted(self._processed_ids)
            to_remove = set(sorted_ids[:excess])
            self._processed_ids -= to_remove
            logging.info("StateManager: trimmed %d old IDs (now %d/%d)",
                        excess, len(self._processed_ids), MAX_PROCESSED_IDS)

        try:
            row, created = CrawlerState.get_or_create(
                site_id=self.site_id,
                tenant_id=self.tenant_id,
                section=self.section,
                defaults={
                    "id": get_uuid(),
                    "processed_ids": list(self._processed_ids),
                    "last_page": self._last_page,
                    "last_offset": self._last_offset,
                    "extra_state": self._extra,
                },
            )
            if not created:
                row.processed_ids = list(self._processed_ids)
                row.last_page = self._last_page
                row.last_offset = self._last_offset
                row.extra_state = self._extra
                row.save()
        except Exception as e:
            logging.error("StateManager.save failed: %s", e)

    def is_processed(self, item_id: str) -> bool:
        """Check if an item has already been processed."""
        return item_id in self._processed_ids

    def mark_processed(self, item_id: str) -> None:
        """Mark a single item as processed."""
        if item_id:
            self._processed_ids.add(item_id)

    def mark_batch_processed(self, item_ids: List[str]) -> None:
        """Mark multiple items as processed."""
        for rid in item_ids:
            if rid:
                self._processed_ids.add(rid)

    # ---- Properties ----

    @property
    def processed_count(self) -> int:
        return len(self._processed_ids)

    @property
    def processed_ids(self) -> Set[str]:
        return self._processed_ids

    @processed_ids.setter
    def processed_ids(self, val: Set[str]) -> None:
        self._processed_ids = set(str(x) for x in val) if val else set()

    @property
    def last_page(self) -> int:
        return self._last_page

    @last_page.setter
    def last_page(self, val: int) -> None:
        self._last_page = val

    @property
    def last_offset(self) -> int:
        return self._last_offset

    @last_offset.setter
    def last_offset(self, val: int) -> None:
        self._last_offset = val

    @property
    def extra(self) -> Dict[str, Any]:
        return self._extra

    @extra.setter
    def extra(self, val: Dict[str, Any]) -> None:
        self._extra = val

    def reset(self) -> None:
        """Reset all state (for --full re-crawl)."""
        self._processed_ids.clear()
        self._last_page = 0
        self._last_offset = 0
        self._extra = {}
