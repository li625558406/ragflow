"""
SiteDetector — lightweight first-page probe for the unified crawler framework.

Scans only page 1 of a site's listing to check for new content, WITHOUT:
  - Acquiring distributed locks
  - Writing to bid_project / KB
  - Modifying StateManager

Used by crawler_detector.py to decide whether to trigger a full crawl.
"""

import logging
from typing import Any, Dict, List, Optional

try:
    from rag.utils.redis_conn import REDIS_CONN
except ImportError:
    REDIS_CONN = None  # type: ignore

from .config import SiteConfig, SectionConfig
from .paginator import PaginatorFactory
from .anti_crawler import AntiCrawlerManager
from .adapters.base import AdapterFactory
from .extractors.base import ExtractorFactory
from .bid_writer import gen_bid_id


class SiteDetector:
    """Lightweight detector: fetch page-1 listing and check for new items."""

    def __init__(self, config: SiteConfig, tenant_id: str):
        self._config = config
        self._tenant_id = tenant_id

    def detect(self) -> Dict[str, Any]:
        """Probe the site for new content.

        Returns:
            {
                "site_id": str,
                "has_new_items": bool,
                "new_item_count": int,
                "scanned_count": int,
                "sections": [
                    {"label": str, "has_new": bool, "new_count": int, "scanned": int}
                ]
            }
        """
        site_id = self._config.site_id

        # 1. Check if the site is currently being full-crawled (skip if so)
        if REDIS_CONN is not None:
            lock_key = f"crawler_engine:{self._tenant_id}:{site_id}"
            if REDIS_CONN.exist(lock_key):
                logging.info("Detector: site=%s is locked (full crawl running), skipping.", site_id)
                return {
                    "site_id": site_id,
                    "has_new_items": False,
                    "new_item_count": 0,
                    "scanned_count": 0,
                    "reason": "already_running",
                }

        # 2. Determine which sections to scan
        sections = self._config.sections
        if sections:
            results = []
            total_new = 0
            total_scanned = 0
            for section in sections:
                r = self._detect_section(section)
                results.append(r)
                total_new += r["new_count"]
                total_scanned += r["scanned"]
            return {
                "site_id": site_id,
                "has_new_items": total_new > 0,
                "new_item_count": total_new,
                "scanned_count": total_scanned,
                "sections": results,
            }
        else:
            r = self._detect_section(None)
            return {
                "site_id": site_id,
                "has_new_items": r["has_new"],
                "new_item_count": r["new_count"],
                "scanned_count": r["scanned"],
                "sections": [r],
            }

    def _detect_section(self, section: Optional[SectionConfig]) -> Dict[str, Any]:
        """Detect new items in a single section (or the main listing)."""
        label = section.label if section else "default"

        # Resolve config overrides (same logic as CrawlerEngine._crawl_one_section)
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

        try:
            adapter = AdapterFactory.create(self._config)
            paginator = PaginatorFactory.create(pag_cfg)
            section_extractor = ExtractorFactory.create(extract_cfg) if extract_cfg else None

            # Anti-crawler delay
            anti_crawler = AntiCrawlerManager(
                self._config.anti_crawler,
                self._config.transport.captcha,
            )

            # Fetch page 1 only
            first_params = next(paginator.pages(1), None)
            if first_params is None:
                return {"label": label, "has_new": False, "new_count": 0, "scanned": 0}

            items = adapter.fetch_items(first_params, listing_override=listing_cfg)
            if not items:
                return {"label": label, "has_new": False, "new_count": 0, "scanned": 0}

            # Extract structured data if needed (HTML response)
            items = self._maybe_extract(items, section_extractor, base_url=listing_cfg.url)

            anti_crawler.delay()

            # Check each item for novelty
            new_count = 0
            scanned = len(items)
            for item in items:
                if self._is_new(item):
                    new_count += 1

            logging.info(
                "Detector: site=%s section=%s scanned=%d new=%d",
                self._config.site_id, label, scanned, new_count,
            )

            return {
                "label": label,
                "has_new": new_count > 0,
                "new_count": new_count,
                "scanned": scanned,
            }

        except Exception as e:
            logging.error(
                "Detector: site=%s section=%s error: %s",
                self._config.site_id, label, e,
                exc_info=True,
            )
            return {"label": label, "has_new": False, "new_count": 0, "scanned": 0, "error": str(e)}

    def _is_new(self, item: dict) -> bool:
        """Check if a single item is new (not yet in bid_project)."""
        url = item.get("url", "") or item.get("source_url", "") or item.get("link", "")
        item_id = item.get("id", "") or url

        if not url and not item_id:
            return True  # Can't determine — assume new

        # Check bid_project table only (not StateManager — that's full-crawl state)
        if url:
            project_id = gen_bid_id(url)
            try:
                from api.db.services.bid_service import BidProjectService
                result = BidProjectService.get_by_project_id(project_id)
                if result is not None:
                    return False  # Already exists
            except Exception as e:
                logging.debug("Detector: bid_project check failed for id=%d: %s", project_id, e)

        return True

    def _maybe_extract(self, items: list, extractor, base_url: str = "") -> list:
        """Apply extraction if the raw response is HTML/TEXT而非结构化数据."""
        if not extractor or not items:
            return items

        first = items[0] if items else {}
        # If items are already dicts with meaningful keys, skip extraction
        if isinstance(first, dict) and len(first) > 2:
            return items

        try:
            return extractor.extract_items(items, base_url=base_url)
        except Exception:
            return items
