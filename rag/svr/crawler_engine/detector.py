"""
SiteDetector — lightweight first-page probe for the unified crawler framework.

Scans only page 1 of a site's listing to decide whether new content exists,
WITHOUT:
  - Acquiring the full-crawl distributed lock
  - Writing to crawler_result / bid_project / KB
  - Modifying StateManager

Decision is signature-based:
  - Compute md5[:8] of the sorted (id|publish_date) pairs of the first 30 items.
  - Compare with the signature stored in Redis (`detector:state:{tenant}:{site}`).
  - If different → has_new_items=True → the caller enqueues a full crawl.

Used by crawler_detector.py.
"""

import hashlib
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


# How many top items to include in the signature. Most government list pages
# show new items at the top, so the first 30 is sensitive enough while keeping
# the hash stable against random reshuffles of older items.
SIGNATURE_ITEM_LIMIT = 30


def compute_signature(items: List[dict]) -> str:
    """Compute an 8-char signature from a list of extracted items.

    Each item contributes `id|publish_date`. Sorted before hashing so that
    order reshuffles do not trigger false positives. Returns "empty" when no
    items carry an identifying field (so an extractor regression is visible
    as a changed signature rather than silently matching the previous one).
    """
    parts: List[str] = []
    for it in items[:SIGNATURE_ITEM_LIMIT]:
        if not isinstance(it, dict):
            continue
        item_id = (
            it.get("id") or it.get("uuid") or it.get("article_id")
            or it.get("infoid") or it.get("infourl") or it.get("noticenumber") or it.get("bulletinID")
            or it.get("guid") or it.get("_id") or it.get("url")
            or it.get("href") or it.get("source_url") or it.get("link") or ""
        )
        pub = (
            it.get("publish_date") or it.get("date")
            or it.get("publishTime") or it.get("CREATE_TIME") or it.get("infodate") or ""
        )
        parts.append(f"{item_id}|{pub}")
    if not parts:
        return "empty"
    parts.sort()
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8]


class SiteDetector:
    """Lightweight detector: fetch page-1 listing, compare signature."""

    def __init__(self, config: SiteConfig, tenant_id: str,
                 collection_mode: bool = True):
        """
        Args:
            config: site configuration
            tenant_id: tenant owning the Redis state key
            collection_mode: True (default) — pure signature compare, no DB hit.
                False preserved for backward compatibility with any caller that
                still expects the legacy per-item bid_project lookup behaviour.
        """
        self._config = config
        self._tenant_id = tenant_id
        self._collection_mode = collection_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self) -> Dict[str, Any]:
        """Probe the site for new content.

        Returns:
            {
                "site_id": str,
                "has_new_items": bool,
                "new_item_count": int,
                "scanned_count": int,
                "signature": str,          # this probe's signature
                "last_signature": str,     # previous signature from Redis
                "reason": str,             # changed | unchanged | empty |
                                           # error | locked
                "sections": [
                    {"label": str, "has_new": bool, "new_count": int,
                     "scanned": int, "signature": str}
                ]
            }
        """
        site_id = self._config.site_id

        # 1. If a full crawl is currently running for this site, skip probe
        #    (avoids signature flapping during an in-progress write).
        if REDIS_CONN is not None:
            lock_key = f"crawler_engine:{self._tenant_id}:{site_id}"
            if REDIS_CONN.exist(lock_key):
                logging.info(
                    "Detector: site=%s is locked (full crawl running), skipping.",
                    site_id,
                )
                return self._empty_result(site_id, reason="locked")

        # 2. Read previous signature (sections may have individual keys in
        #    the future; for now we store one signature per site).
        last_sig = self._read_last_signature()

        # 3. Probe each section (or the main listing)
        sections = self._config.sections
        if sections:
            section_results: List[Dict[str, Any]] = []
            total_scanned = 0
            new_signature_parts: List[str] = []
            for section in sections:
                r = self._detect_section(section)
                section_results.append(r)
                total_scanned += r.get("scanned", 0)
                # Note: section-level "has_new" only signals "data present"
                # (sig is non-empty).  Site-level change detection is driven
                # PURELY by signature comparison below; mixing in section
                # has_new here would flag every probe as changed.
                if r.get("signature") and r["signature"] not in ("empty", "error"):
                    new_signature_parts.append(f"{r['label']}:{r['signature']}")

            # Site-level signature combines section signatures so that a new
            # item in ANY section flips the global signature.
            if new_signature_parts:
                new_sig = hashlib.md5(
                    "|".join(sorted(new_signature_parts)).encode("utf-8")
                ).hexdigest()[:8]
            else:
                new_sig = "empty"

            changed = new_sig not in ("empty", "error") and new_sig != last_sig
            return {
                "site_id": site_id,
                "has_new_items": changed,
                "new_item_count": total_scanned if changed else 0,
                "scanned_count": total_scanned,
                "signature": new_sig,
                "last_signature": last_sig,
                "reason": self._reason(changed, new_sig, last_sig, total_scanned),
                "sections": section_results,
            }
        else:
            r = self._detect_section(None)
            new_sig = r.get("signature", "empty")
            changed = new_sig not in ("empty", "error") and new_sig != last_sig
            return {
                "site_id": site_id,
                "has_new_items": changed,
                "new_item_count": r.get("scanned", 0) if changed else 0,
                "scanned_count": r.get("scanned", 0),
                "signature": new_sig,
                "last_signature": last_sig,
                "reason": self._reason(changed, new_sig, last_sig, r.get("scanned", 0)),
                "sections": [r],
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _detect_section(self, section: Optional[SectionConfig]) -> Dict[str, Any]:
        """Detect new items in a single section (or the main listing)."""
        label = section.label if section else "default"

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

        adapter = None
        try:
            adapter = AdapterFactory.create(self._config)
            paginator = PaginatorFactory.create(pag_cfg)
            section_extractor = (
                ExtractorFactory.create(extract_cfg) if extract_cfg else None
            )

            # Keep probe cheap: no retries, short timeout
            anti_crawler = AntiCrawlerManager(
                self._config.anti_crawler,
                self._config.transport.captcha,
            )

            # Fetch page 1 only
            first_params = next(paginator.pages(1), None)
            if first_params is None:
                return self._empty_section(label)

            items = adapter.fetch_items(first_params, listing_override=listing_cfg)
            if not items:
                return self._empty_section(label)

            items = self._maybe_extract(items, section_extractor,
                                        base_url=listing_cfg.url)
            anti_crawler.delay()

            scanned = len(items)
            sig = compute_signature(items)
            # For section-level result, we don't know the previous per-section
            # signature without an extra Redis read. has_new at section level is
            # True whenever signature is non-empty and non-"empty"; the caller
            # combines these into the site-level comparison.
            return {
                "label": label,
                "has_new": sig not in ("empty", ""),
                "new_count": scanned if sig not in ("empty", "") else 0,
                "scanned": scanned,
                "signature": sig,
            }

        except Exception as e:
            logging.error(
                "Detector: site=%s section=%s error: %s",
                self._config.site_id, label, e, exc_info=True,
            )
            return {
                "label": label, "has_new": False, "new_count": 0,
                "scanned": 0, "signature": "error", "error": str(e),
            }
        finally:
            if adapter is not None:
                try:
                    adapter.cleanup()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_last_signature(self) -> str:
        """Read previous signature from Redis JSON state blob.

        State schema (set by crawler_detector.py):
            detector:state:{tenant}:{site} = {
                "next_run_at": int, "last_sig": str, "miss_count": int,
                "cur_interval": int, "last_check": int,
                "consecutive_errors": int, "last_new_count": int,
                "auto_disabled": bool
            }
        """
        if REDIS_CONN is None:
            return ""
        key = f"detector:state:{self._tenant_id}:{self._config.site_id}"
        try:
            import json
            raw = REDIS_CONN.get(key)
            if not raw:
                return ""
            state = json.loads(raw)
            return str(state.get("last_sig") or "")
        except Exception as e:
            logging.debug("Detector: failed to read last_sig for %s: %s",
                          self._config.site_id, e)
            return ""

    @staticmethod
    def _reason(changed: bool, new_sig: str, last_sig: str,
                scanned: int) -> str:
        if scanned == 0:
            return "empty"
        if changed:
            return "changed"
        if new_sig == last_sig:
            return "unchanged"
        return "unchanged"

    @staticmethod
    def _empty_result(site_id: str, reason: str = "empty") -> Dict[str, Any]:
        return {
            "site_id": site_id,
            "has_new_items": False,
            "new_item_count": 0,
            "scanned_count": 0,
            "signature": "empty",
            "last_signature": "",
            "reason": reason,
            "sections": [],
        }

    @staticmethod
    def _empty_section(label: str) -> Dict[str, Any]:
        return {
            "label": label, "has_new": False, "new_count": 0,
            "scanned": 0, "signature": "empty",
        }

    def _maybe_extract(self, items: list, extractor, base_url: str = "") -> list:
        """Apply extraction if the adapter returned raw HTML rather than dicts.

        Mirrors CrawlerEngine._maybe_extract_from_html: when the adapter hands
        back a single-element list with an ``html`` key, run the CSS extractor
        to produce structured dicts before computing the signature.
        """
        if extractor is None or not items:
            return items
        if len(items) == 1 and isinstance(items[0], dict) and "html" in items[0]:
            try:
                extracted = extractor.extract(items[0]["html"], base_url=base_url)
                if extracted:
                    return extracted
            except Exception as e:
                logging.debug("Detector: extractor failed for %s: %s",
                              self._config.site_id, e)
        return items
