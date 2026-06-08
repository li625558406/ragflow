"""
Pagination strategy implementations.

Supported types:
- page_no: standard page-number-based pagination (page=1,2,3,...)
- offset: offset-based pagination (start=0,20,40,...)
- total_count: drive pagination by total record count from API
- html_regex: extract next-page URL via regex from HTML
- click_next: click "next page" button (SPA)
- single_page: single page, no pagination
- static_index: static HTML pages (index_1.html, index_2.html, ...)
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Optional, Tuple

from .config import PaginationConfig


class BasePaginator(ABC):
    """Abstract base for pagination strategies."""

    def __init__(self, config: PaginationConfig):
        self._config = config

    @abstractmethod
    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        """Generate request parameters for each page."""
        ...

    @abstractmethod
    def update_total(self, response_data: Any) -> int:
        """Extract total record count from API response. Returns total_count."""
        ...

    def _get_nested(self, data: Any, field: str) -> Any:
        """Extract a value from potentially nested dict using dot notation.

        E.g., _get_nested(data, "data.total") returns data["data"]["total"].
        If the field has no dots, works like dict.get(field).
        Returns 0 for any missing keys or non-dict intermediate values.
        """
        if not isinstance(data, dict) or not field:
            return 0
        keys = field.split(".")
        for key in keys:
            if not isinstance(data, dict):
                return 0
            data = data.get(key)
            if data is None:
                return 0
        return data

    def total_pages_from_count(self, total_count: int) -> int:
        """Calculate total pages from record count."""
        ps = self._config.page_size or 20
        return (total_count + ps - 1) // ps


class PageNoPaginator(BasePaginator):
    """Standard page-number pagination: ?page=1&rows=20"""

    # Max pages safety limit when max_pages=0 (infinite generator guard)
    _SAFETY_MAX = 50000

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        page = max(start_at, self._config.start)
        max_p = self._config.max_pages if self._config.max_pages > 0 else self._SAFETY_MAX
        i = 0
        while i < max_p:
            yield {
                self._config.page_param: page + i,
                self._config.page_size_param: self._config.page_size,
            }
            i += 1

    def update_total(self, response_data: Any) -> int:
        if isinstance(response_data, dict):
            return int(self._get_nested(response_data, self._config.total_field) or 0)
        return 0


class OffsetPaginator(BasePaginator):
    """Offset-based pagination: ?start=0&limit=20"""

    _SAFETY_MAX = 50000

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        offset = start_at
        max_p = self._config.max_pages if self._config.max_pages > 0 else self._SAFETY_MAX
        i = 0
        while i < max_p:
            yield {
                self._config.page_param: offset + i * self._config.page_size,
                self._config.page_size_param: self._config.page_size,
            }
            i += 1

    def update_total(self, response_data: Any) -> int:
        if isinstance(response_data, dict):
            return int(self._get_nested(response_data, self._config.total_field) or 0)
        return 0


class TotalCountPaginator(BasePaginator):
    """Pagination driven by total count from first API response."""

    def __init__(self, config: PaginationConfig):
        super().__init__(config)
        self._total_pages: Optional[int] = None

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        if self._total_pages is None:
            yield {
                self._config.page_param: start_at,
                self._config.page_size_param: self._config.page_size,
            }
            return
        for p in range(start_at, self._total_pages + 1):
            yield {
                self._config.page_param: p,
                self._config.page_size_param: self._config.page_size,
            }

    def update_total(self, response_data: Any) -> int:
        if isinstance(response_data, dict):
            total = int(self._get_nested(response_data, self._config.total_field) or 0)
            self._total_pages = self.total_pages_from_count(total)
            max_p = self._config.max_pages
            if max_p > 0 and self._total_pages > max_p:
                self._total_pages = max_p
            return total
        return 0

    def resume(self, start_page: int) -> None:
        """Set starting page for resume."""
        self._total_pages = None  # will be set on first fetch


class SinglePagePaginator(BasePaginator):
    """Single page — no pagination."""

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        yield {}

    def update_total(self, response_data: Any) -> int:
        return 1


class HtmlRegexPaginator(BasePaginator):
    """Static HTML page pagination via URL pattern (e.g., /index_{}.htm).

    Yields params with a special ``_page_url`` key containing the constructed
    URL. The adapter should use this URL instead of the default listing URL.
    """

    _SAFETY_MAX = 50000

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        page = max(start_at, self._config.start)
        max_p = self._config.max_pages if self._config.max_pages > 0 else self._SAFETY_MAX
        pattern = self._config.page_pattern
        start = self._config.start
        first_page_no_suffix = getattr(self._config, "first_page_no_suffix", False)
        i = 0
        while i < max_p:
            current_page = page + i
            params: Dict[str, Any] = {
                self._config.page_param: current_page,
                self._config.page_size_param: self._config.page_size,
            }
            if pattern:
                # Skip suffix for start page when first_page_no_suffix is set,
                # so the adapter uses listing.url for page 1 and pattern for later pages.
                if not (first_page_no_suffix and current_page == start):
                    url_suffix = pattern.replace("{}", str(current_page))
                    params["_page_url_suffix"] = url_suffix
            yield params
            i += 1

    def update_total(self, response_data: Any) -> int:
        if isinstance(response_data, dict):
            return int(self._get_nested(response_data, self._config.total_field) or 0)
        return 0


class ClickNextPaginator(BasePaginator):
    """SPA pagination where the adapter clicks a "Next Page" button.

    The actual clicking is handled by the SpaRenderAdapter. This paginator
    just generates a page number counter for tracking purposes.
    """

    _SAFETY_MAX = 50000

    def pages(self, start_at: int = 0) -> Generator[Dict[str, Any], None, None]:
        page = max(start_at, self._config.start)
        max_p = self._config.max_pages if self._config.max_pages > 0 else self._SAFETY_MAX
        i = 0
        while i < max_p:
            yield {
                "_click_next": True,
                "_page": page + i,
            }
            i += 1

    def update_total(self, response_data: Any) -> int:
        if isinstance(response_data, dict):
            return int(self._get_nested(response_data, self._config.total_field) or 0)
        if isinstance(response_data, list):
            return len(response_data)
        return 0


class PaginatorFactory:
    """Factory for creating paginator instances."""

    @staticmethod
    def create(config: PaginationConfig) -> BasePaginator:
        type_map = {
            "page_no": PageNoPaginator,
            "offset": OffsetPaginator,
            "total_count": TotalCountPaginator,
            "single_page": SinglePagePaginator,
            "html_regex": HtmlRegexPaginator,
            "click_next": ClickNextPaginator,
        }
        cls = type_map.get(config.type)
        if cls is None:
            logging.warning("Unknown pagination type '%s', using page_no", config.type)
            cls = PageNoPaginator
        return cls(config)
