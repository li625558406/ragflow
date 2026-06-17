"""
Scrapling Stealth adapter — browser-based stealth fetching + self-healing selectors.

Wraps scrapling.StealthyFetcher to provide:
- Cloudflare Turnstile bypass
- Browser TLS fingerprint impersonation
- Self-healing element tracking (auto_save / adaptive)
- Network idle waiting for SPA rendering

Designed as a drop-in replacement for spa_render and playwright_http adapters.
Opt-in via transport.type: scrapling_stealth in crawler_sites.yaml.

When Scrapling is not installed, import of this module itself will not fail
(only the adapter constructor raises on first use with a clear install hint).
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from ..config import SiteConfig
from .. import resolve_params, resolve_url
from .base import BaseAdapter


class ScraplingStealthAdapter(BaseAdapter):
    """Adapter using scrapling.StealthyFetcher for anti-bot-protected / SPA sites."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._page = None
        self._auto_saved = False

    # ------------------------------------------------------------------
    # Lazy init helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_installed():
        """Verify scrapling is importable; raise with install hint if not."""
        try:
            import scrapling  # noqa: F401
        except ImportError:
            raise ImportError(
                "scrapling is required for transport type 'scrapling_stealth'. "
                "Install it with: pip install 'scrapling[fetchers]' && scrapling install"
            )

    @staticmethod
    def _get_stealthy_fetcher():
        """Return the StealthyFetcher class (lazy import)."""
        ScraplingStealthAdapter._check_installed()
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher

    # ------------------------------------------------------------------
    # fetch_items — main listing fetch
    # ------------------------------------------------------------------

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch a single page of items using StealthyFetcher.

        Strategy:
        1. Navigate to listing URL with stealth browser
        2. If listing API is intercepted (JSON), extract directly
        3. Otherwise fall back to DOM extraction via CSS selectors
        4. Self-healing: first call uses auto_save=True, subsequent use adaptive=True
        """
        StealthyFetcher = self._get_stealthy_fetcher()
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        method = listing.method.upper()

        # Build request params from listing config + paginator
        params = dict(listing.params)
        # Handle html_regex pagination URL suffix
        url_suffix = page_params.pop("_page_url_suffix", "")
        if url_suffix and isinstance(url_suffix, str) and url_suffix:
            from urllib.parse import urlparse, urljoin
            parts = urlparse(url)
            base = f"{parts.scheme}://{parts.netloc}"
            url = urljoin(base, url_suffix)

        # Resolve {{ page }} / {{ page_size }} / {{ today }} / {{ N_days_ago }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        url = resolve_url(url, page_val, size_val)
        params = resolve_params(params, page_val, size_val)
        params.update(page_params)

        fetch_kwargs = self._build_fetch_kwargs()
        max_retries = self._config.anti_crawler.max_retries

        for attempt in range(max_retries):
            try:
                if method == "GET":
                    page = StealthyFetcher.fetch(url, params=params, **fetch_kwargs)
                else:
                    body = listing.body if listing.body else params
                    page = StealthyFetcher.fetch(
                        url, method=method, body=body, **fetch_kwargs
                    )

                # ── Extract items ──
                items = self._extract_items(page)
                if items:
                    self._last_raw = page
                    return items

                # Fallback: try extracting from raw page text as JSON
                items = self._extract_json_from_text(page)
                if items:
                    self._last_raw = page
                    return items

                logging.warning(
                    "ScraplingStealthAdapter: no items extracted from %s", url
                )
                return []

            except Exception as e:
                logging.warning(
                    "ScraplingStealthAdapter: attempt %d/%d for %s failed: %s",
                    attempt + 1, max_retries, url, e,
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        return None

    # ------------------------------------------------------------------
    # Extraction strategies
    # ------------------------------------------------------------------

    def _extract_items(self, page) -> List[Dict[str, Any]]:
        """Extract items from the page using configured CSS/XPath selectors.

        Supports self-healing: first call uses auto_save=True to fingerprint
        elements; subsequent calls can pass adaptive=True to recover from
        layout changes.
        """
        extract_cfg = self._config.extract
        adaptive = getattr(self._transport, "adaptive", False)

        # Determine selector from config
        selector = extract_cfg.items_path or ""
        if not selector:
            # Try to auto-detect from common patterns
            return self._auto_detect_items(page)

        css_kwargs = {}
        if adaptive:
            if not self._auto_saved:
                css_kwargs["auto_save"] = True
                self._auto_saved = True
            else:
                css_kwargs["adaptive"] = True

        try:
            elements = page.css(selector, **css_kwargs)
        except Exception as e:
            logging.warning("ScraplingStealthAdapter: css('%s') failed: %s", selector, e)
            return []

        if not elements:
            return []

        items = []
        fields = extract_cfg.fields or {}
        for el in elements:
            item: Dict[str, Any] = {}
            if fields:
                for internal_name, source_field in fields.items():
                    # source_field can be a CSS pseudo-element like "a::attr(href)"
                    # or a relative CSS selector like ".title::text"
                    value = self._extract_field(el, source_field)
                    item[internal_name] = value
            else:
                # No field mapping — store each child as a key-value pair
                for child in el.css("*"):
                    tag = child.attrib.get("class", "") if hasattr(child, "attrib") else ""
                    text = child.text if hasattr(child, "text") else str(child)
                    if text:
                        item[tag or "text"] = text.strip()
            if item:
                items.append(item)

        return items

    @staticmethod
    def _extract_field(element, source_field: str) -> str:
        """Extract a single field value from a Scrapling element.

        Supports:
        - Relative CSS selectors ending in ::text or ::attr(name)
        - Direct attribute access
        - Fallback to .text
        """
        if not source_field:
            return ""
        try:
            if "::attr(" in source_field:
                # e.g. "a::attr(href)" — extract attribute
                parts = source_field.rsplit("::attr(", 1)
                inner_sel = parts[0] if parts[0] else "*"
                attr_name = parts[1].rstrip(")")
                child = element.css(inner_sel)
                if isinstance(child, list) and child:
                    return child[0].attrib.get(attr_name, "") if hasattr(child[0], "attrib") else ""
                return ""
            elif source_field.endswith("::text"):
                inner_sel = source_field[:-6]
                child = element.css(inner_sel)
                if isinstance(child, list) and child:
                    return child[0].text.strip() if hasattr(child[0], "text") and child[0].text else ""
                return ""
            else:
                child = element.css(source_field)
                if isinstance(child, list) and child:
                    c = child[0]
                    return str(c.text).strip() if hasattr(c, "text") and c.text else str(c)
                return ""
        except Exception:
            return ""

    def _extract_json_from_text(self, page) -> List[Dict[str, Any]]:
        """Attempt to parse the page body as JSON (for API-response sites).

        Scrapling renders JSON responses as HTML (<body><p>{...}</p></body>),
        so page.text may be empty while page.body holds the raw bytes.
        Tries multiple sources: body bytes, html_content <pre> extraction,
        and page.text as a last resort.
        """
        try:
            # Strategy 1: Raw body bytes (most reliable for JSON API responses)
            raw_bytes = getattr(page, "body", b"")
            if raw_bytes:
                try:
                    data = json.loads(raw_bytes)
                    return self._extract_list_from_json(data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            # Strategy 2: Extract <pre> text from rendered HTML
            html_content = getattr(page, "html_content", "")
            if html_content:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, "lxml")
                pre = soup.find("pre")
                if pre:
                    try:
                        data = json.loads(pre.get_text())
                        return self._extract_list_from_json(data)
                    except (json.JSONDecodeError, Exception):
                        pass

            # Strategy 3: page.text (fallback, usually empty for JSON)
            raw_text = getattr(page, "text", "")
            if raw_text and raw_text.strip()[:1] in ("{", "["):
                data = json.loads(raw_text)
                return self._extract_list_from_json(data)
        except Exception:
            pass
        return []

    def _extract_list_from_json(self, data: dict) -> List[Dict[str, Any]]:
        """Extract a list of items from a parsed JSON dict."""
        items_field = self._config.pagination.items_field
        if items_field:
            nested = self._config.pagination.items_field
            val = data
            for part in nested.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if isinstance(val, list):
                return val
        for key in ("rows", "data", "list", "records", "result", "results"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []

    def _auto_detect_items(self, page) -> List[Dict[str, Any]]:
        """Auto-detect list items from common table/list patterns in the DOM."""
        try:
            tables = page.css("table tbody tr, .list-item, .el-table__row, .ant-table-row")
            items = []
            for row in tables:
                cells = row.css("td, th, .cell")
                item = {}
                for i, cell in enumerate(cells):
                    txt = cell.text.strip() if hasattr(cell, "text") else str(cell)
                    if txt:
                        item[f"col_{i}"] = txt
                if item:
                    items.append(item)
            return items
        except Exception:
            return []

    # ------------------------------------------------------------------
    # fetch_detail
    # ------------------------------------------------------------------

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch detail page content.

        Routes to the correct strategy based on detail.type:
        - css_selector: navigate to detail page, extract content
        - api_request: call detail API
        - inline / none: delegate to base
        """
        detail_cfg = self._config.detail

        if detail_cfg.type == "css_selector":
            return self._fetch_detail_stealth(item)

        if detail_cfg.type == "api_request" and detail_cfg.url:
            return self._fetch_detail_api(item)

        return super().fetch_detail(item)

    def _fetch_detail_stealth(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch detail using StealthyFetcher + BeautifulSoup extraction.

        Uses scrapling for the HTTP fetch (stealth browser for JS-protected
        detail pages), then falls back to the same BeautifulSoup content
        extraction that all other adapters share.
        """
        StealthyFetcher = self._get_stealthy_fetcher()
        detail_cfg = self._config.detail

        # Priority: detail config URL template > item fields
        detail_url = ""
        if detail_cfg.url:
            detail_url = detail_cfg.url
            # Resolve {KEY} / {{ KEY }} placeholders from item values
            for key, val in item.items():
                detail_url = detail_url.replace("{" + key + "}", str(val))
                detail_url = detail_url.replace("{{" + key + "}}", str(val))
            # If any unresolved placeholders remain, discard template and use item fields
            if "{" in detail_url:
                detail_url = ""
        if not detail_url:
            detail_url = item.get("url") or item.get("href") or item.get("link") or item.get("id")
        if not detail_url:
            return item

        from urllib.parse import urljoin
        if not detail_url.startswith("http"):
            list_url = self._config.listing.url or self._config.site_url
            detail_url = urljoin(list_url, detail_url)

        fetch_kwargs = self._build_fetch_kwargs()
        # Detail pages don't need network idle — faster to get DOM immediately
        fetch_kwargs.pop("network_idle", None)

        for attempt in range(3):
            try:
                page = StealthyFetcher.fetch(detail_url, **fetch_kwargs)
                html = getattr(page, "html_content", "") or getattr(page, "html", "") or getattr(page, "text", "")

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")

                # Build candidate selectors: configured first, then fallbacks
                content_field = detail_cfg.content_field
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
                        container = el
                        break

                if container:
                    item["content"] = self._html_to_text(container)
                else:
                    item["content"] = self._strip_and_extract(soup)

                item["detail_html"] = html
                return item

            except Exception as e:
                logging.warning(
                    "ScraplingStealthAdapter: detail fetch attempt %d failed: %s",
                    attempt + 1, e,
                )
                time.sleep(1 + attempt)

        return item

    def _fetch_detail_api(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch detail by calling the detail API via StealthyFetcher."""
        StealthyFetcher = self._get_stealthy_fetcher()
        detail_cfg = self._config.detail
        url = detail_cfg.url

        # Substitute {field} placeholders
        params = dict(detail_cfg.params)
        for key, val in item.items():
            url = url.replace("{" + key + "}", str(val))
            for pkey, pval in params.items():
                if isinstance(pval, str):
                    params[pkey] = pval.replace("{" + key + "}", str(val))

        fetch_kwargs = self._build_fetch_kwargs()
        fetch_kwargs.pop("network_idle", None)

        for attempt in range(3):
            try:
                resp = StealthyFetcher.fetch(url, params=params, **fetch_kwargs)
                raw_text = resp.text if hasattr(resp, "text") else ""
                try:
                    data = json.loads(raw_text)
                    content = self._get_nested_value(data, detail_cfg.content_field)
                    item["content"] = str(content) if content else raw_text
                except json.JSONDecodeError:
                    item["content"] = raw_text
                return item
            except Exception as e:
                logging.warning(
                    "ScraplingStealthAdapter: detail API attempt %d failed: %s",
                    attempt + 1, e,
                )
                time.sleep(1 + attempt)

        return item

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_fetch_kwargs(self) -> Dict[str, Any]:
        """Build keyword arguments for StealthyFetcher.fetch() from config."""
        kwargs: Dict[str, Any] = {}
        transport = self._transport

        if hasattr(transport, "headless"):
            kwargs["headless"] = transport.headless
        if hasattr(transport, "network_idle") and transport.network_idle:
            kwargs["network_idle"] = True
        if hasattr(transport, "solve_cloudflare") and transport.solve_cloudflare:
            kwargs["solve_cloudflare"] = True

        # Browser fingerprint impersonation
        if hasattr(transport, "impersonate") and transport.impersonate:
            kwargs["impersonate"] = transport.impersonate

        # Block resources for speed
        if hasattr(transport, "block_resources") and transport.block_resources:
            kwargs["block_resources"] = True

        # Timeout — StealthyFetcher uses MILLISECONDS; transport config is in SECONDS
        if hasattr(transport, "timeout"):
            kwargs["timeout"] = transport.timeout * 1000

        return kwargs

    @staticmethod
    def _get_nested_value(data: dict, path: str) -> Any:
        """Get nested dict value by dot-separated path (e.g. 'data.content')."""
        if not path:
            return None
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Scrapling StealthyFetcher is stateless per request; no cleanup needed."""
        # StealthyFetcher.fetch() opens and closes the browser for each call
        # (unlike the persistent Playwright page in SpaRenderAdapter).
        # No explicit cleanup required.
        pass
