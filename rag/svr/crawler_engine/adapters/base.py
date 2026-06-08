"""
Abstract base class for all crawler adapters.

Each adapter abstracts a specific HTTP transport strategy:
- rest_api: plain requests.Session + optional BeautifulSoup parsing
- encrypted_api: encrypted request/response with SM4/AES + signing
- spa_render: headless browser rendering for Vue/React SPA sites
- playwright_http: browser-based HTTP via existing PlaywrightHttpClient
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..config import SiteConfig, TransportConfig, ListingConfig
from ..anti_crawler import get_random_ua


class BaseAdapter(ABC):
    """Abstract adapter defining the crawl interface."""

    def __init__(self, config: SiteConfig):
        self._config = config
        self._transport = config.transport
        self._last_raw: Any = None  # raw response from last fetch_items call

    @abstractmethod
    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override: Optional["ListingConfig"] = None) -> Optional[List[Dict[str, Any]]]:
        """Fetch a single page/listing of items from the remote API/site.

        Args:
            page_params: Pagination parameters (e.g. {'page': 1, 'rows': 20}).
            listing_override: Optional section-level listing config that
                overrides the site-level listing (URL, params, etc.).

        Returns:
            List of item dicts, or None on failure.
        """
        ...

    @property
    def last_raw(self) -> Any:
        """Return the raw response from the most recent fetch_items call."""
        return self._last_raw

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch detail for a single item.

        By default, if detail type is 'inline', the item itself is
        already the full record.  Override for API-based details.
        """
        if self._config.detail.type == "css_selector":
            return self._fetch_detail_css(item)
        return item

    # Common content container selectors across Chinese gov / news sites.
    # Ordered by specificity: TRS CMS first, then other popular CMS patterns.
    _CONTENT_SELECTORS = [
        ".TRS_Editor",
        "#detailCont",
        ".article-content",
        ".news-content",
        ".detail-content",
        ".text-content",
        ".pages_content",
        "#zoom",
        ".zoom",
        "article",
        ".Custom_UnionStyle",
        ".xl_con1",
        ".content",
        "main .content",
        ".post_content",
        ".rich_media_content",
    ]

    def _fetch_detail_css(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch the item's detail URL and extract content using CSS selector.

        Shared across all adapter types.  Uses a plain requests.get() call
        which works regardless of the adapter's primary transport.

        Content extraction strategy:
        1. Try the configured ``content_field`` selector first.
        2. If not found, automatically try a list of common selectors.
        3. If nothing matches, strip the full page text (no navigation junk).
        """
        detail_cfg = self._config.detail
        content_field = detail_cfg.content_field

        # Find the URL from the item (id often holds the URL from extractor field mapping)
        detail_url = item.get("url") or item.get("href") or item.get("link") or item.get("id")
        if not detail_url:
            logging.warning("BaseAdapter: no URL in item for css_selector detail")
            return item

        # Resolve relative URLs against the listing page URL (not site root)
        from urllib.parse import urljoin
        if not detail_url.startswith("http"):
            base_url = self._config.listing.url or self._config.site_url
            detail_url = urljoin(base_url, detail_url)

        for attempt in range(3):
            try:
                resp = requests.get(detail_url, timeout=self._transport.timeout,
                                    headers={"User-Agent": get_random_ua()})
                # Force UTF-8 decoding for Chinese text
                ct = resp.headers.get("Content-Type", "")
                if "charset=" not in ct.lower():
                    resp.encoding = "utf-8"
                if resp.status_code != 200:
                    logging.warning("BaseAdapter: detail fetch HTTP %d for %s",
                                    resp.status_code, detail_url)
                    time.sleep(1 + attempt)
                    continue

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "lxml")

                # Build candidate list: configured selector first, then fallbacks
                candidates = []
                if content_field:
                    candidates.append(content_field)
                candidates.extend(self._CONTENT_SELECTORS)
                seen = set()
                unique_candidates = []
                for s in candidates:
                    if s not in seen:
                        seen.add(s)
                        unique_candidates.append(s)

                container = None
                for sel in unique_candidates:
                    el = soup.select_one(sel)
                    if el and len(el.get_text(strip=True)) > 50:
                        if sel != content_field:
                            logging.info("BaseAdapter: fallback selector '%s' matched in %s",
                                         sel, detail_url)
                        container = el
                        break

                if container:
                    item["content"] = self._html_to_text(container)
                else:
                    # Last resort: strip scripts/nav/footers then get text
                    logging.warning("BaseAdapter: no content selector matched in %s",
                                    detail_url)
                    item["content"] = self._strip_and_extract(soup)

                item["detail_html"] = resp.text
                return item

            except Exception as e:
                logging.warning("BaseAdapter: detail css fetch failed: %s", e)
                time.sleep(1 + attempt)

        return item

    @staticmethod
    def _html_to_text(container) -> str:
        """Convert an HTML container to clean text with paragraph breaks."""
        text = container.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n\n".join(lines)

    @staticmethod
    def _strip_and_extract(soup) -> str:
        """Last-resort extraction: remove nav/header/footer/scripts, then extract text."""
        from copy import copy
        s = copy(soup)
        for tag in s.find_all(["script", "style", "nav", "header", "footer",
                                "iframe", "noscript"]):
            tag.decompose()
        for el in s.select("[role='navigation'], [role='banner'], "
                           "[role='contentinfo'], .top-link, .nav, .footer, "
                           ".header, #top-link, .sidebar, .comment"):
            el.decompose()
        return BaseAdapter._html_to_text(s.find("body") or s)

    def fetch_raw(self, url: str, params: Dict = None,
                  method: str = "GET", body: Dict = None) -> Optional[Any]:
        """Fetch raw response from a URL. Override for direct data access."""
        return None

    @abstractmethod
    def cleanup(self) -> None:
        """Release any resources (sessions, browser instances, etc.)."""
        ...


class AdapterFactory:
    """Factory that returns the correct adapter for a site config."""

    @staticmethod
    def create(config: SiteConfig):
        transport_type = config.transport.type
        if transport_type == "rest_api":
            from .rest_api import RestApiAdapter
            return RestApiAdapter(config)
        elif transport_type == "encrypted_api":
            from .encrypted_api import EncryptedApiAdapter
            return EncryptedApiAdapter(config)
        elif transport_type == "spa_render":
            from .spa_render import SpaRenderAdapter
            return SpaRenderAdapter(config)
        elif transport_type == "playwright_http":
            from .playwright_http import PlaywrightHttpAdapter
            return PlaywrightHttpAdapter(config)
        else:
            raise ValueError(f"Unknown transport type: {transport_type}")
