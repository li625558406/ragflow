"""
Abstract base class for all crawler adapters.

Each adapter abstracts a specific HTTP transport strategy:
- rest_api: plain requests.Session + optional BeautifulSoup parsing
- encrypted_api: encrypted request/response with SM4/AES + signing
- spa_render: headless browser rendering for Vue/React SPA sites
- playwright_http: browser-based HTTP via existing PlaywrightHttpClient
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from ..config import SiteConfig, TransportConfig, ListingConfig


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
        return item

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
