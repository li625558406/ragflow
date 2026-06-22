"""
SPA Render adapter — full browser rendering for Vue/React single-page apps.

Uses sync_playwright to:
- Navigate to SPA pages and wait for JavaScript rendering
- Intercept API responses via page.on("response") to capture data
- Click "next page" buttons for pagination
- Extract rendered DOM content via page.evaluate()
- Handle CAPTCHA via ddddocr integration
"""

import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from ..config import SiteConfig
from ..browser_pool import get_browser_pool
from .. import resolve_params
from .base import BaseAdapter

try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False


# JavaScript snippet that calls Vue.__vue__.$http to bypass signature/csrf checks.
# The zfcg API requires requests to go through Vue's axios interceptors which
# add crypto signatures — plain fetch() returns "验签比对失败".
_VUE_HTTP_JS = """
([url, opts]) => {
    return new Promise((resolve) => {
        try {
            const vm = document.querySelector('#app').__vue__;
            const http = vm.$http;
            http.get(url, opts).then(resp => {
                if (opts.responseType === 'arraybuffer') {
                    let bytes = new Uint8Array(resp.data);
                    let binary = '';
                    bytes.forEach(b => binary += String.fromCharCode(b));
                    resolve(JSON.stringify({b64: btoa(binary)}));
                } else {
                    resolve(JSON.stringify(resp.data));
                }
            }).catch(err => {
                resolve(JSON.stringify({error: err.message}));
            });
        } catch(e) {
            resolve(JSON.stringify({error: e.message}));
        }
    });
}
"""


class SpaRenderAdapter(BaseAdapter):
    """Adapter for Vue/React SPA sites requiring full JavaScript rendering."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._pool = get_browser_pool()
        self._api_captures: List[Dict[str, Any]] = []
        self._page = None
        self._vue_ready = False
        self._captcha_code: Optional[str] = None
        self._api_base: str = ""

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
        """Navigate to listing page and capture API responses.

        When transport.vue_http is True, uses Vue.__vue__.$http proxy to
        call APIs directly (bypasses signature/CSRF checks on sites like zfcg).
        """
        if getattr(self._transport, "vue_http", False):
            return self._fetch_via_vue_http(page_params, listing_override)

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
        """Extract items from captured API responses.

        Handles nested structures like {"code":200, "data": {"resultList": [...]}}.
        """
        for capture in self._api_captures:
            data = capture["data"]
            items_field = self._config.pagination.items_field
            if items_field and items_field in data:
                items = data[items_field]
                if isinstance(items, list):
                    return items
            # Try common keys; recurse into dict values for nested responses
            for key in ("rows", "data", "list", "records", "result", "results"):
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        return val
                    if isinstance(val, dict):
                        nested = self._extract_from_dict(val, items_field)
                        if nested:
                            return nested
        return []

    def _extract_from_dict(self, data: dict, items_field: str) -> list:
        """Recursively extract items from a nested dict."""
        if items_field and items_field in data:
            items = data[items_field]
            if isinstance(items, list):
                return items
        for key in ("rows", "data", "list", "records", "result", "results"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    return val
                if isinstance(val, dict):
                    return self._extract_from_dict(val, items_field)
        return []

    # ------------------------------------------------------------------
    # Vue $http mode — for sites whose API requires Vue's axios interceptors
    # (e.g. zfcg — plain fetch() returns "验签比对失败")
    # ------------------------------------------------------------------

    def _ensure_vue_context(self) -> None:
        """Navigate to site homepage and wait for Vue app to mount.

        The Vue $http proxy is only available after the SPA has booted on
        the origin domain.  We navigate once and reuse the page across
        paginated API calls so that cookies / csrf tokens stay valid.
        """
        if self._vue_ready:
            return
        page = self._get_page()
        site_url = self._config.site_url
        if not site_url:
            # Fall back to origin derived from the listing URL
            from urllib.parse import urlparse
            parts = urlparse(self._config.listing.url)
            site_url = f"{parts.scheme}://{parts.netloc}"
        logging.info("SpaRenderAdapter: navigating to %s for Vue context", site_url)
        page.goto(site_url, wait_until="networkidle",
                  timeout=self._transport.timeout * 1000)
        time.sleep(3)
        # Verify Vue $http is reachable
        try:
            page.evaluate("() => document.querySelector('#app').__vue__.$http")
        except Exception as e:
            raise RuntimeError(
                f"Vue $http not found on {site_url}: {e}"
            ) from e

        # Derive API base from listing URL (e.g. https://host/gpcms/rest/web/v2)
        from urllib.parse import urlparse
        listing_url = self._config.listing.url
        self._api_base = listing_url.rsplit("/", 2)[0]

        self._vue_ready = True
        logging.info("SpaRenderAdapter: Vue $http context ready")

    # ------------------------------------------------------------------
    # CAPTCHA handling
    # ------------------------------------------------------------------

    def _solve_captcha(self) -> Optional[str]:
        """Fetch captcha image, OCR with ddddocr, verify against listing API.

        Returns the verified captcha code string, or None on failure.
        The code is cached in ``self._captcha_code`` and reused across
        paginated API calls.
        """
        captcha_cfg = self._transport.captcha
        if not captcha_cfg or captcha_cfg.type != "ocr":
            return None
        if not DDDDOCR_AVAILABLE:
            logging.warning("SpaRenderAdapter: ddddocr not installed, captcha disabled")
            return None
        if self._captcha_code:
            return self._captcha_code  # cached

        page = self._get_page()
        self._ensure_vue_context()

        ocr = ddddocr.DdddOcr(show_ad=False)
        captcha_url = f"{self._api_base}/index/getVerify"
        listing_url = self._config.listing.url

        # Build base params for captcha verification (need channel + siteId)
        listing_params = dict(self._config.listing.params)

        for attempt in range(15):
            try:
                # 1. Fetch captcha image
                result_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [captcha_url, {
                        "params": {"_t": str(int(time.time() * 1000))},
                        "responseType": "arraybuffer",
                    }],
                )
                cap = json.loads(result_json)
                if cap.get("error") or not cap.get("b64"):
                    logging.warning(
                        "SpaRenderAdapter: captcha fetch failed (attempt %d): %s",
                        attempt + 1, cap.get("error", "no b64"),
                    )
                    time.sleep(0.3)
                    continue

                # 2. OCR the image
                img_bytes = base64.b64decode(cap["b64"])
                code_val = ocr.classification(img_bytes).strip()
                if not code_val:
                    time.sleep(0.3)
                    continue

                # 3. Verify against listing API
                verify_params = dict(listing_params)
                verify_params.update({
                    "currPage": "1",
                    "pageSize": "1",
                    "verifyCode": code_val,
                })
                verify_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [listing_url, {"params": verify_params}],
                )
                verify_data = json.loads(verify_json)
                if verify_data.get("code") == "200":
                    self._captcha_code = code_val
                    logging.info(
                        "SpaRenderAdapter: captcha solved after %d attempts: %s",
                        attempt + 1, code_val,
                    )
                    return code_val

                logging.debug(
                    "SpaRenderAdapter: captcha verify failed (attempt %d): "
                    "code=%s msg=%s", attempt + 1,
                    verify_data.get("code"), verify_data.get("msg"),
                )
                time.sleep(0.3)

            except Exception as e:
                logging.warning(
                    "SpaRenderAdapter: captcha attempt %d error: %s",
                    attempt + 1, e,
                )
                time.sleep(0.3)

        logging.error("SpaRenderAdapter: captcha solving failed after 15 attempts")
        return None

    def _invalidate_captcha(self) -> None:
        """Clear cached captcha code (call when API returns auth error)."""
        self._captcha_code = None

    # ------------------------------------------------------------------
    # Vue $http listings
    # ------------------------------------------------------------------

    def _fetch_via_vue_http(self, page_params: Dict[str, Any],
                            listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch listing data via Vue.__vue__.$http proxy.

        Navigates to the site homepage once (for Vue context + cookies), then
        calls the listing API through Vue's axios instance on every page.

        When captcha is configured (type: ocr), solves the captcha via ddddocr
        and injects ``verifyCode`` into every API call.
        """
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        params = dict(listing.params)

        # Merge page_params (from paginator) into listing params
        params.update(page_params)

        # Resolve any remaining {{ page }} / {{ page_size }} / {{ today }} / {{ N_days_ago }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        params = resolve_params(params, page_val, size_val)

        self._ensure_vue_context()
        page = self._get_page()
        max_retries = self._config.anti_crawler.max_retries

        for attempt in range(max_retries):
            try:
                # Inject captcha verifyCode if configured
                captcha_cfg = self._transport.captcha
                if captcha_cfg and captcha_cfg.type == "ocr":
                    code = self._solve_captcha()
                    if code:
                        params["verifyCode"] = code

                result_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [url, {"params": params}],
                )
                data = json.loads(result_json)

                if isinstance(data, dict) and "error" in data:
                    logging.warning(
                        "SpaRenderAdapter: Vue $http error: %s", data["error"]
                    )
                    time.sleep(2 ** attempt)
                    continue

                # Check for captcha/auth errors (4001 = captcha, 4009 = signature)
                if isinstance(data, dict):
                    code = data.get("code", "")
                    if code in ("4001", "4009"):
                        logging.warning(
                            "SpaRenderAdapter: API auth error code=%s msg=%s",
                            code, data.get("msg", ""),
                        )
                        if code == "4001":
                            self._invalidate_captcha()
                        time.sleep(2 ** attempt)
                        continue

                self._last_raw = data
                self._api_captures = [{"url": url, "data": data}]
                items = self._extract_from_api_captures()
                return items if items else []

            except Exception as e:
                logging.warning(
                    "SpaRenderAdapter: Vue $http attempt %d failed: %s",
                    attempt + 1, e,
                )
                time.sleep(2 ** attempt)

        return None

    def _fetch_detail_via_vue_http(self, item: Dict[str, Any],
                                     detail_override=None) -> Dict[str, Any]:
        """Fetch detail content via Vue $http proxy.

        Calls the detail API through Vue's axios instance and extracts content
        from the JSON response using content_field path (e.g. "data.content").

        When captcha is configured (type: ocr), injects ``verifyCode`` into
        every detail API call.  Also resolves ``{{ field }}`` placeholders in
        params from the item dict (e.g. ``{{ planId }}``, ``{{ channel }}``).
        """
        detail_cfg = detail_override or self._config.detail
        detail_url = detail_cfg.url
        params = dict(detail_cfg.params)
        for key, val in item.items():
            detail_url = detail_url.replace("{" + key + "}", str(val))
            for pkey, pval in params.items():
                if isinstance(pval, str):
                    params[pkey] = pval.replace("{" + key + "}", str(val))
                    # Also handle {{ param }} template placeholders in params
                    params[pkey] = params[pkey].replace("{{ " + key + " }}", str(val))

        # Inject channel + siteId from transport/lising config if not in params
        listing_params = self._config.listing.params
        if "channel" not in params and "channel" in listing_params:
            params["channel"] = listing_params["channel"]
        if "siteId" not in params and "siteId" in listing_params:
            params["siteId"] = listing_params["siteId"]

        self._ensure_vue_context()
        page = self._get_page()

        for attempt in range(3):
            try:
                # Inject captcha verifyCode if configured
                captcha_cfg = self._transport.captcha
                if captcha_cfg and captcha_cfg.type == "ocr":
                    code = self._solve_captcha()
                    if code:
                        params["verifyCode"] = code

                result_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [detail_url, {"params": params}],
                )
                data = json.loads(result_json)

                if isinstance(data, dict) and "error" in data:
                    logging.warning(
                        "SpaRenderAdapter: Vue $http detail error: %s",
                        data["error"],
                    )
                    time.sleep(1 + attempt)
                    continue

                # Handle API auth errors
                if isinstance(data, dict):
                    code = data.get("code", "")
                    if code in ("4001", "4009"):
                        logging.warning(
                            "SpaRenderAdapter: detail API auth error code=%s msg=%s",
                            code, data.get("msg", ""),
                        )
                        if code == "4001":
                            self._invalidate_captcha()
                        time.sleep(1 + attempt)
                        continue

                # Extract content using content_field path (e.g. "data.content")
                content = self._get_nested_value(data, detail_cfg.content_field)
                if content:
                    item["content"] = str(content)
                else:
                    item["content"] = json.dumps(data, ensure_ascii=False)
                return item

            except Exception as e:
                logging.warning(
                    "SpaRenderAdapter: Vue $http detail attempt %d failed: %s",
                    attempt + 1, e,
                )
                time.sleep(1 + attempt)

        return item

    @staticmethod
    def _get_nested_value(data: dict, path: str) -> Any:
        """Get a nested dict value by dot-separated path (e.g. "data.content")."""
        if not path:
            return None
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data

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

    def fetch_detail(self, item: Dict[str, Any],
                     detail_override=None) -> Optional[Dict[str, Any]]:
        """Fetch detail by navigating to detail URL with Playwright.

        - css_selector: use Playwright to get rendered HTML, then BS4 extraction
        - api_request: navigate to API URL and extract content via page.evaluate()
        - inline / none: delegate to base class

        When transport.vue_http is True, api_request calls use Vue $http proxy
        and extract content from JSON response directly.
        """
        detail_cfg = detail_override or self._config.detail

        if detail_cfg.type == "css_selector":
            return self._fetch_detail_css(item)

        # inline / none handled by base class
        if detail_cfg.type != "api_request" or not detail_cfg.url:
            return super().fetch_detail(item)

        # Vue $http detail path — call API through Vue's axios proxy
        if getattr(self._transport, "vue_http", False):
            return self._fetch_detail_via_vue_http(item, detail_override=detail_cfg)

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

    def _fetch_detail_css(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Override base class: use Playwright instead of requests.get().

        Navigates to the detail page with Playwright (renders SPA JS),
        then applies the same BeautifulSoup content extraction as the base.
        """
        detail_cfg = self._config.detail
        content_field = detail_cfg.content_field

        detail_url = item.get("url") or item.get("href") or item.get("link") or item.get("id")
        if not detail_url:
            logging.warning("SpaRenderAdapter: no URL in item for css_selector detail")
            return item

        from urllib.parse import urljoin
        if not detail_url.startswith("http"):
            base_url = self._config.listing.url or self._config.site_url
            detail_url = urljoin(base_url, detail_url)

        page = self._get_page()
        for attempt in range(3):
            try:
                page.goto(detail_url, wait_until="networkidle",
                          timeout=self._transport.timeout * 1000)
                time.sleep(1)
                html = page.content()

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")

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
                            logging.info(
                                "SpaRenderAdapter: fallback selector '%s' matched in %s",
                                sel, detail_url,
                            )
                        container = el
                        break

                if container:
                    item["content"] = self._html_to_text(container)
                else:
                    logging.warning(
                        "SpaRenderAdapter: no content selector matched in %s",
                        detail_url,
                    )
                    item["content"] = self._strip_and_extract(soup)

                item["detail_html"] = html
                return item

            except Exception as e:
                logging.warning("SpaRenderAdapter: detail css fetch failed: %s", e)
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
            self._vue_ready = False
            self._captcha_code = None

    def cleanup(self) -> None:
        self._recreate_page()
