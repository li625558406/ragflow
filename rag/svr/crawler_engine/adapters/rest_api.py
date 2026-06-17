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
from .. import resolve_params, resolve_url
from .base import BaseAdapter


class RestApiAdapter(BaseAdapter):
    """Adapter for standard REST API / HTML sites."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        if config.transport.engine == "scrapling":
            self._session = SessionManager.create_scrapling(config.transport)
        else:
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

        # Resolve {{ page }} / {{ page_size }} / {{ today }} / {{ N_days_ago }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        params = resolve_params(params, page_val, size_val)
        url = resolve_url(url, page_val, size_val)

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

                # Try JSON first, fall back to HTML/BeautifulSoup.
                # Some APIs return JSON with wrong Content-Type (text/html).
                # Sniff the body: if it starts with { or [, treat as JSON.
                content_type = resp.headers.get("Content-Type", "")
                text = resp.text
                if "application/json" in content_type or \
                   (text and text.strip()[:1] in ("{", "[")):
                    try:
                        data = resp.json()
                    except Exception:
                        # Not valid JSON despite looking like it — fall through to HTML
                        self._last_raw = text
                        return self._parse_html_response(text)
                    self._last_raw = data
                    return self._parse_json_response(data)
                else:
                    self._last_raw = text
                    return self._parse_html_response(text)

            except Exception as e:
                logging.warning("RestApiAdapter: attempt %d failed: %s", attempt + 1, e)
                SessionManager.reset(self._session, self._transport)
                time.sleep(2 + attempt * 3)

        return None

    def _parse_json_response(self, data: Any) -> List[Dict[str, Any]]:
        """Extract items from JSON response.

        Handles both flat and nested structures:
          Flat:  {"resultList": [...]}
          Nested: {"code":200, "data": {"resultList": [...]}}
          Deep:   {"success":true, "result": {"data": {"list": [...]}}}
          Dotted: items_field="custom.infodata" for {"custom":{"infodata":[...]}}
        """
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items_field = self._config.pagination.items_field
            if items_field:
                # Support dot notation: "custom.infodata" → data["custom"]["infodata"]
                val = self._get_nested(data, items_field)
                if isinstance(val, list):
                    return val
            # Try common keys at root level
            for key in ("rows", "data", "list", "records", "result", "results", "custom"):
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        return val
                    # Key exists but is a dict — recurse into it (handles nested responses)
                    if isinstance(val, dict):
                        nested = self._parse_json_response(val)
                        # Must check isinstance(list), not truthiness —
                        # empty list [] is a valid result (no items on page).
                        if isinstance(nested, list) and not (len(nested) == 1 and nested[0] is val):
                            return nested
            # If no list found, wrap the dict itself
            return [data]
        return []

    @staticmethod
    def _get_nested(data: dict, field: str) -> Any:
        """Extract a value from nested dict using dot notation.
        E.g., _get_nested(data, "custom.infodata") → data["custom"]["infodata"].
        """
        if not isinstance(data, dict) or not field:
            return None
        keys = field.split(".")
        for key in keys:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
            if data is None:
                return None
        return data

    def _parse_html_response(self, html: str) -> List[Dict[str, Any]]:
        """Pass raw HTML through — the engine's CSS extractor handles parsing.

        The engine's _maybe_extract_from_html() will use the configured
        items_path to extract structured items from the HTML.
        """
        return [{"html": html}]

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch and extract detail page content.

        Handles:
        - api_request: fetch from a detail API URL template
        - css_selector / inline / none: delegated to base class
        """
        detail_cfg = self._config.detail

        # css_selector, inline, none, and missing URL are handled by base class
        if detail_cfg.type != "api_request" or not detail_cfg.url:
            return super().fetch_detail(item)

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
