"""
REST API adapter — pure HTTP via requests.Session.

Handles:
- Standard REST API JSON endpoints
- HTML page parsing via BeautifulSoup (optional)
- Cookie-based session management
- Rate limiting and retry
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

from ..config import SiteConfig
from ..session_manager import SessionManager
from .base import BaseAdapter


class RestApiAdapter(BaseAdapter):
    """Adapter for standard REST API / HTML sites."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._session = SessionManager.create(config.transport)

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch a page of items from the listing URL.

        Handles both JSON API responses and HTML pages (via BeautifulSoup).
        """
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        method = listing.method.upper()

        # Merge template params with page params
        params = dict(listing.params)

        # Handle html_regex pagination: _page_url_suffix overrides the URL path
        url_suffix = page_params.pop("_page_url_suffix", "")
        if url_suffix:
            from urllib.parse import urlparse, urljoin
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            url = urljoin(base, url_suffix)

        params.update(page_params)

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                if method == "POST":
                    body_type = listing.body_type
                    if body_type == "json":
                        resp = self._session.post(url, json=params, timeout=self._transport.timeout)
                    elif body_type == "form":
                        resp = self._session.post(url, data=params, timeout=self._transport.timeout)
                    else:
                        resp = self._session.post(url, params=params, timeout=self._transport.timeout)
                else:
                    resp = self._session.get(url, params=params, timeout=self._transport.timeout)

                if resp.status_code == 429:
                    wait = (2 ** attempt) + random.uniform(1, 3)
                    logging.warning("RestApiAdapter: 429 rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    SessionManager.reset(self._session, self._transport)
                    continue

                if resp.status_code != 200:
                    logging.warning("RestApiAdapter: HTTP %d for %s", resp.status_code, url)
                    SessionManager.reset(self._session, self._transport)
                    time.sleep(2 + attempt * 2)
                    continue

                # Try JSON first, fall back to HTML/BeautifulSoup
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type or url.endswith(".regx"):
                    data = resp.json()
                    self._last_raw = data
                    return self._parse_json_response(data)
                else:
                    self._last_raw = resp.text
                    return self._parse_html_response(resp.text)

            except Exception as e:
                logging.warning("RestApiAdapter: attempt %d failed: %s", attempt + 1, e)
                SessionManager.reset(self._session, self._transport)
                time.sleep(2 + attempt * 3)

        return None

    def _parse_json_response(self, data: Any) -> List[Dict[str, Any]]:
        """Extract items from JSON response."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items_field = self._config.pagination.items_field
            if items_field and items_field in data:
                items = data[items_field]
                if isinstance(items, list):
                    return items
            # Try common keys
            for key in ("rows", "data", "list", "records", "result", "results"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # If no list found, wrap the dict itself
            return [data]
        return []

    def _parse_html_response(self, html: str) -> List[Dict[str, Any]]:
        """Parse HTML page for items. Override for site-specific parsing."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logging.warning("BeautifulSoup not available for HTML parsing")
            return [{"html": html}]

        soup = BeautifulSoup(html, "lxml")
        # Default: extract all text from body
        items = []
        # Look for list items, tables, or article blocks
        for tag in soup.select("ul.list li, table tbody tr, .article-item, .news-item"):
            text = tag.get_text(strip=True)
            if text:
                items.append({"text": text})
        if not items:
            body = soup.find("body")
            if body:
                items.append({"html": str(body)})
        return items

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """If detail type is 'api_request', fetch the detail page."""
        detail_cfg = self._config.detail
        if detail_cfg.type == "inline" or not detail_cfg.url:
            return item

        url_template = detail_cfg.url
        # Replace {id}, {uuid}, etc. placeholders
        for key, val in item.items():
            url_template = url_template.replace("{" + key + "}", str(val))

        params = dict(detail_cfg.params)
        for attempt in range(3):
            try:
                if detail_cfg.method.upper() == "POST":
                    resp = self._session.post(url_template, data=params, timeout=self._transport.timeout)
                else:
                    resp = self._session.get(url_template, params=params, timeout=self._transport.timeout)
                if resp.status_code == 200:
                    return {"detail": resp.text, **item}
            except Exception as e:
                logging.warning("RestApiAdapter: detail fetch failed: %s", e)
                time.sleep(1 + attempt)
        return item

    def cleanup(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
