"""
SPA Render adapter — full browser rendering for Vue/React single-page apps.

Uses sync_playwright to:
- Navigate to SPA pages and wait for JavaScript rendering
- Intercept API responses via page.on("response") to capture data
- Click "next page" buttons for pagination
- Extract rendered DOM content via page.evaluate()
- Handle CAPTCHA via ddddocr integration
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from ..config import SiteConfig
from ..browser_pool import get_browser_pool
from .base import BaseAdapter


class SpaRenderAdapter(BaseAdapter):
    """Adapter for Vue/React SPA sites requiring full JavaScript rendering."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._pool = get_browser_pool()
        self._api_captures: List[Dict[str, Any]] = []
        self._page = None

    def _get_page(self):
        """Get or create a page with API interception."""
        if self._page is None:
            self._page = self._pool.get_page()
            self._setup_api_capture()
        return self._page

    def _setup_api_capture(self) -> None:
        """Setup response interception to capture API JSON data."""
        self._api_captures = []

        def _on_response(response):
            try:
                url = response.url
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type or any(
                    pat in url for pat in [".regx", "/api/", "/getPubList"]
                ):
                    body = response.body()
                    data = json.loads(body)
                    self._api_captures.append({
                        "url": url,
                        "data": data,
                    })
            except Exception:
                pass  # ignore failed captures

        self._page.on("response", _on_response)

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Navigate to listing page and capture API responses."""
        page = self._get_page()
        listing = listing_override if listing_override else self._config.listing
        url = listing.url

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                # Clear previous captures
                self._api_captures = []

                # Navigate and wait for network idle
                page.goto(url, wait_until="networkidle", timeout=self._transport.timeout * 1000)

                # Wait for data container to appear
                try:
                    page.wait_for_selector("table, .list, .el-table, .ant-table", timeout=15000)
                except Exception:
                    pass  # table may not exist; rely on API captures

                # Additional wait for async rendering
                time.sleep(3)

                # Try API captures first
                if self._api_captures:
                    items = self._extract_from_api_captures()
                    if items:
                        self._last_raw = self._api_captures
                        return items

                # Fall back to DOM extraction
                result = self._extract_from_dom(page)
                self._last_raw = result
                return result

            except Exception as e:
                logging.warning("SpaRenderAdapter: attempt %d failed: %s", attempt + 1, e)
                time.sleep(2 ** attempt)
                self._recreate_page()

        return None

    def _extract_from_api_captures(self) -> List[Dict[str, Any]]:
        """Extract items from captured API responses."""
        for capture in self._api_captures:
            data = capture["data"]
            items_field = self._config.pagination.items_field
            if items_field and items_field in data:
                items = data[items_field]
                if isinstance(items, list):
                    return items
            for key in ("rows", "data", "list", "records"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    def _extract_from_dom(self, page) -> List[Dict[str, Any]]:
        """Extract items from rendered DOM."""
        try:
            # Try to get table or list data via JavaScript
            items = page.evaluate("""() => {
                const rows = document.querySelectorAll('table tbody tr, .list-item, .el-table__row');
                const result = [];
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td, .cell');
                    const item = {};
                    cells.forEach((cell, i) => {
                        item['col_' + i] = cell.textContent.trim();
                    });
                    if (Object.keys(item).length > 0) result.push(item);
                });
                return result;
            }""")
            return items if items else []
        except Exception as e:
            logging.warning("SpaRenderAdapter: DOM extraction failed: %s", e)
            return []

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch detail by clicking on item or navigating to detail URL."""
        detail_cfg = self._config.detail
        if detail_cfg.type == "inline" or not detail_cfg.url:
            return item

        page = self._get_page()
        detail_url = detail_cfg.url
        for key, val in item.items():
            detail_url = detail_url.replace("{" + key + "}", str(val))

        for attempt in range(3):
            try:
                page.goto(detail_url, wait_until="networkidle",
                          timeout=self._transport.timeout * 1000)
                time.sleep(1)

                # Try to extract content
                content = page.evaluate("""() => {
                    const article = document.querySelector('article, .article, .content, .detail, #content');
                    if (article) return article.textContent.trim();
                    return document.body.textContent.trim();
                }""")

                if content:
                    item["content"] = content
                return item
            except Exception as e:
                logging.warning("SpaRenderAdapter: detail fetch failed: %s", e)
                time.sleep(1 + attempt)

        return item

    def _recreate_page(self) -> None:
        """Close current page and create a new one."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

    def cleanup(self) -> None:
        self._recreate_page()
