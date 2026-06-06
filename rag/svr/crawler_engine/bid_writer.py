"""
Bid database writer — stores crawler output into existing bid_* tables.

Generates unique positive bigint IDs via xxhash, then upserts into:
- bid_project (header/metadata)
- bid_project_detail (full HTML content)
- bid_project_file (attachments)
- bid_project_structure (structured extracted fields)
- bid_project_parse (KB parse tracking)

All writes use idempotent upsert — re-crawling the same URL is safe.
One item failure does not block others (error isolation).
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import xxhash

# Lazy imports — available in Docker, not required for unit tests
try:
    from api.db.services.bid_service import (
        BidProjectService,
        BidProjectDetailService,
        BidProjectStructureService,
        BidProjectFileService,
        BidProjectParseService,
    )
except ImportError:
    BidProjectService = None  # type: ignore
    BidProjectDetailService = None  # type: ignore
    BidProjectStructureService = None  # type: ignore
    BidProjectFileService = None  # type: ignore
    BidProjectParseService = None  # type: ignore

from .content_converter import (
    extract_project_fields,
    extract_detail_fields,
    extract_file_attachments,
    parse_date,
)


# Max positive signed 64-bit integer
_MAX_POSITIVE_INT63 = 0x7FFFFFFFFFFFFFFF


def gen_bid_id(source: str) -> int:
    """Generate a unique positive bigint ID from a URL or unique string.

    Uses xxhash.xxh64 to produce a stable, deterministic 64-bit hash,
    then masks to positive int63 for MySQL bigint compatibility.
    """
    return xxhash.xxh64(source.encode("utf-8")).intdigest() & _MAX_POSITIVE_INT63


def gen_file_id(file_url: str, project_id: int, index: int = 0) -> int:
    """Generate a unique file ID from URL + project + index."""
    seed = f"{file_url}|{project_id}|{index}"
    return xxhash.xxh64(seed.encode("utf-8")).intdigest() & _MAX_POSITIVE_INT63


class BidWriter:
    """Writes crawled items into the bid database tables.

    Usage:
        writer = BidWriter(kb_id="xyz", tenant_id="abc")
        for item in items:
            writer.write_all(item, site_id="fgw_zwgk")
        results = writer.flush()
    """

    def __init__(self, kb_id: str, tenant_id: str):
        self._kb_id = kb_id
        self._tenant_id = tenant_id
        self._stats = {
            "projects_new": 0,
            "projects_updated": 0,
            "projects_failed": 0,
            "details_written": 0,
            "details_failed": 0,
            "files_written": 0,
            "files_failed": 0,
            "structures_written": 0,
            "structures_failed": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_all(
        self,
        item: Dict[str, Any],
        site_id: str,
        url: Optional[str] = None,
    ) -> Optional[int]:
        """Write one crawled item to all bid tables.

        Returns the project_id if successful, None if the project itself
        could not be written.
        """
        # 1. Generate ID
        project_id = self._resolve_id(item, url)
        if project_id is None:
            logging.warning("BidWriter: cannot resolve ID for item: %s",
                           str(item.get("title", ""))[:80])
            self._stats["projects_failed"] += 1
            return None

        # 2. Write bid_project
        project_ok = self.write_project(project_id, item, site_id)
        if not project_ok:
            return None

        # 3. Write bid_project_detail (content)
        self.write_detail(project_id, item)

        # 4. Write bid_project_structure
        self.write_structure(project_id, item)

        # 5. Write bid_project_file (attachments)
        self.write_files(project_id, item)

        # 6. Optionally track KB parse state
        if self._kb_id:
            self.write_parse(project_id)

        return project_id

    def write_project(
        self,
        project_id: int,
        item: Dict[str, Any],
        site_id: str,
    ) -> bool:
        """Upsert into bid_project table."""
        if BidProjectService is None:
            logging.warning("BidWriter: BidProjectService not available, skipping project write")
            return False

        try:
            fields = extract_project_fields(item)
            fields["id"] = project_id
            fields["source_type"] = "crawler"

            # Store site_id and original URL in raw_json for reference
            raw_meta = {
                "crawler_site_id": site_id,
                "crawler_url": item.get("url") or item.get("href") or "",
                "crawled_at": datetime.now().isoformat(),
            }
            fields["raw_json"] = json.dumps(raw_meta, ensure_ascii=False)

            is_new, _ = BidProjectService.upsert_project(fields)
            if is_new:
                self._stats["projects_new"] += 1
            else:
                self._stats["projects_updated"] += 1
            return True
        except Exception as e:
            logging.error("BidWriter: project write failed (id=%d): %s", project_id, e)
            self._stats["projects_failed"] += 1
            return False

    def write_detail(self, project_id: int, item: Dict[str, Any]) -> bool:
        """Upsert into bid_project_detail table (full HTML content)."""
        if BidProjectDetailService is None:
            return False

        try:
            fields = extract_detail_fields(item)
            BidProjectDetailService.upsert_detail(project_id, fields)
            self._stats["details_written"] += 1
            return True
        except Exception as e:
            logging.error("BidWriter: detail write failed (project=%d): %s", project_id, e)
            self._stats["details_failed"] += 1
            return False

    def write_structure(self, project_id: int, item: Dict[str, Any]) -> bool:
        """Upsert into bid_project_structure table.

        Extracts structured data: project numbers, budgets, bid dates,
        party info, etc. from the crawled item.
        """
        if BidProjectStructureService is None:
            return False

        try:
            fields = self._extract_structure(item)
            if not fields:
                return True  # nothing to write

            fields["id"] = project_id
            fields["project_id"] = project_id
            fields["fetched_at"] = datetime.now()

            BidProjectStructureService.upsert_structure(project_id, fields)
            self._stats["structures_written"] += 1
            return True
        except Exception as e:
            logging.error("BidWriter: structure write failed (project=%d): %s", project_id, e)
            self._stats["structures_failed"] += 1
            return False

    def write_files(self, project_id: int, item: Dict[str, Any]) -> int:
        """Upsert attachment files into bid_project_file table.

        Returns number of files written.
        """
        if BidProjectFileService is None:
            return 0

        files = extract_file_attachments(item)
        if not files:
            return 0

        written = 0
        for idx, f in enumerate(files):
            try:
                file_url = f.get("file_url", "")
                if not file_url:
                    continue

                file_id = f.get("project_file_id") or gen_file_id(file_url, project_id, idx)
                data = {
                    "project_file_id": file_id,
                    "project_id": project_id,
                    "file_name": f.get("file_name", "")[:500],
                    "file_url": file_url[:1000],
                    "file_suffix": f.get("file_suffix", "")[:20],
                    "file_size": f.get("file_size"),
                    "state": "1",  # available
                    "publish_time": f.get("publish_time"),
                    "fetched_at": datetime.now(),
                    "created_at": datetime.now(),
                }
                BidProjectFileService.upsert_file(data)
                written += 1
            except Exception as e:
                logging.error("BidWriter: file write failed (project=%d): %s", project_id, e)
                self._stats["files_failed"] += 1

        self._stats["files_written"] += written
        return written

    def write_parse(self, project_id: int) -> bool:
        """Create or update bid_project_parse tracking record."""
        if BidProjectParseService is None:
            return False

        try:
            BidProjectParseService.upsert({
                "project_id": project_id,
                "kb_id": self._kb_id,
                "status": "pending",
                "progress": 0,
                "progress_msg": "Crawled and stored, pending KB parse",
            })
            return True
        except Exception as e:
            logging.warning("BidWriter: parse tracking write failed (project=%d): %s", project_id, e)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_id(self, item: Dict[str, Any], url: Optional[str] = None) -> Optional[int]:
        """Resolve a unique positive bigint ID from the item.

        Priority:
        1. url or href field → xxhash(url)
        2. Explicit id/uuid field → try as int or hash
        3. title + date fallback → xxhash
        """
        # Priority 1: URL-based ID
        source_url = url or item.get("url") or item.get("href") or ""
        if source_url:
            return gen_bid_id(source_url)

        # Priority 2: Existing numeric ID
        for key in ("id", "uuid", "article_id", "infoid"):
            val = item.get(key)
            if val:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    # Non-numeric ID → hash it
                    return gen_bid_id(str(val))

        # Priority 3: Title + date fallback
        title = item.get("title") or ""
        date_val = item.get("date") or item.get("publishDate") or ""
        if title:
            return gen_bid_id(f"{title}|{date_val}")

        return None

    def _extract_structure(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Extract structured fields for bid_project_structure."""
        fields = {}

        # Project name
        for key in ("projectName", "project_name", "title"):
            val = item.get(key)
            if val:
                fields["project_name"] = str(val)
                break

        # Project numbers
        for key in ("projectNumber", "projectNumbers", "project_numbers"):
            val = item.get(key)
            if val:
                fields["project_numbers"] = _to_json_str(val)
                break

        # Budget / bid money
        for key in ("budgetMoney", "budget_money"):
            val = item.get(key)
            if val:
                fields["budget_money"] = _to_json_str(val)
                break

        for key in ("bidMoney", "bid_money"):
            val = item.get(key)
            if val:
                fields["bid_money"] = _to_json_str(val)
                break

        # Dates
        for key in ("bidStartDate", "bid_start_date", "openBidDate"):
            val = item.get(key)
            if val:
                dt = parse_date(val)
                if dt:
                    fields["bid_start_date"] = dt
                    break

        for key in ("signUpStopDate", "sign_up_stop_date", "deadline"):
            val = item.get(key)
            if val:
                dt = parse_date(val)
                if dt:
                    fields["sign_up_stop_date"] = dt
                    break

        # Party info
        for key in ("partyAInfo", "party_a_info", "buyerInfo"):
            val = item.get(key)
            if val:
                fields["party_a_info"] = _to_json_str(val)
                break

        for key in ("partyBInfo", "party_b_info", "supplierInfo"):
            val = item.get(key)
            if val:
                fields["party_b_info"] = _to_json_str(val)
                break

        for key in ("agencyInfo", "agency_info"):
            val = item.get(key)
            if val:
                fields["agency_info"] = _to_json_str(val)
                break

        # Collect URL
        collect_url = item.get("url") or item.get("href") or ""
        if collect_url:
            fields["collect_url"] = str(collect_url)[:500]

        return fields

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        for k in self._stats:
            self._stats[k] = 0


def _to_json_str(val: Any) -> str:
    """Convert value to JSON string, handling lists and dicts."""
    if isinstance(val, str):
        # If it already looks like JSON, return as-is
        stripped = val.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            return stripped
        return json.dumps([val], ensure_ascii=False)
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return json.dumps([str(val)], ensure_ascii=False)
