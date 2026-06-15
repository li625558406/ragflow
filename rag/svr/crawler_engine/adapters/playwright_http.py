"""
Playwright HTTP adapter — browser-based HTTP via existing PlaywrightHttpClient.

Wraps the mature PlaywrightHttpClient from rag.svr.crawler_utils, providing
the adapter interface for sites that need browser TLS fingerprints but
don't require full SPA JavaScript rendering.
"""

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from ..config import SiteConfig
from .base import BaseAdapter

# Ensure the crawler_utils module is importable
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class PlaywrightHttpAdapter(BaseAdapter):
    """Adapter using PlaywrightHttpClient for browser-fingerprint-protected sites."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        """Lazy-init the PlaywrightHttpClient."""
        if self._client is None:
            # Fix: sync_playwright conflicts with a running asyncio event loop.
            try:
                import asyncio
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                except ImportError:
                    logging.warning("PlaywrightHttpAdapter: asyncio loop detected but "
                                    "nest_asyncio not installed.")

            from rag.svr.crawler_utils import PlaywrightHttpClient
            self._client = PlaywrightHttpClient()
        return self._client

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch items using PlaywrightHttpClient."""
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        method = listing.method.upper()

        params = dict(listing.params)
        params.update(page_params)

        # Resolve {{ page }} / {{ page_size }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        for key, val in list(params.items()):
            if isinstance(val, str) and "{{" in val:
                val = val.replace("{{ page }}", page_val)
                val = val.replace("{{ page_size }}", size_val)
                params[key] = val

        client = self._get_client()

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                if method == "POST":
                    body_type = getattr(listing, "body_type", None) or "form"
                    if body_type == "json":
                        resp = client.post(url, json_body=params, timeout=self._transport.timeout)
                    else:
                        resp = client.post(url, data=params, timeout=self._transport.timeout)
                else:
                    # Build query string and use fetch_get for proper response body
                    from urllib.parse import urlencode
                    qs = urlencode(params)
                    full_url = f"{url}?{qs}" if qs else url
                    resp = client.fetch_get(full_url, timeout=self._transport.timeout)

                if resp.status_code == 429:
                    time.sleep((2 ** attempt) + 1)
                    continue

                if resp.status_code != 200:
                    logging.warning("PlaywrightHttpAdapter: HTTP %d for %s", resp.status_code, url)
                    time.sleep(2 + attempt * 2)
                    continue

                self._last_raw = resp
                return self._parse_response(resp)

            except Exception as e:
                logging.warning("PlaywrightHttpAdapter: attempt %d failed: %s", attempt + 1, e)
                time.sleep(2 + attempt * 3)

        return None

    def _parse_response(self, resp) -> List[Dict[str, Any]]:
        """Parse response, trying JSON then HTML."""
        try:
            import json
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                items_field = self._config.pagination.items_field
                if items_field and items_field in data:
                    items = data[items_field]
                    return items if isinstance(items, list) else []
                for key in ("rows", "data", "list", "records", "result"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return [data]
        except Exception:
            pass

        # HTML fallback
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "lxml")
            items = []
            for tag in soup.select("ul.list li, table tbody tr, .article-item"):
                text = tag.get_text(strip=True)
                if text:
                    items.append({"text": text})
            return items if items else [{"html": resp.text}]
        except ImportError:
            return [{"html": resp.text}]

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch detail page."""
        detail_cfg = self._config.detail

        # css_selector / inline / none handled by base class
        if detail_cfg.type != "api_request" or not detail_cfg.url:
            return super().fetch_detail(item)

        url = detail_cfg.url
        for key, val in item.items():
            url = url.replace("{" + key + "}", str(val))

        client = self._get_client()
        for attempt in range(3):
            try:
                resp = client.get(url, timeout=self._transport.timeout)
                if resp.status_code == 200:
                    item["content"] = resp.text
                return item
            except Exception as e:
                logging.warning("PlaywrightHttpAdapter: detail fetch failed: %s", e)
                time.sleep(1 + attempt)

        return item

    def cleanup(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
