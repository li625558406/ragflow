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
from .. import resolve_params, resolve_url
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
            // Try $http first (vue-resource / custom wrapper), fall back to
            // $axios (Vue.prototype.$axios = axios.create() pattern).
            const http = vm.$http || vm.$axios;
            if (!http) {
                resolve(JSON.stringify({error: 'No $http or $axios on Vue instance'}));
                return;
            }
            const method = (opts.method || 'GET').toLowerCase();
            const config = opts.params ? {params: opts.params} : {};
            if (opts.responseType) config.responseType = opts.responseType;

            const request = method === 'post'
                ? http.post(url, opts.body || {}, config)
                : http.get(url, config);

            request.then(resp => {
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
        self._click_next_active: bool = False  # True during a click_next sequence
        self._last_listing_url: str = ""       # Track listing URL for section changes

    def _get_page(self):
        """Get or create a page with API interception."""
        if self._page is None:
            self._page = self._pool.get_page()
            # vue_http 模式通过 page.evaluate(Vue.$http) 直接调 API，不依赖
            # response 捕获；且 _on_response 对每个 JSON response 调 body() 是
            # 同步阻塞，会拖慢 page 加载，导致部分站点（如三明）Vue app 来不及
            # mount（has_app=True 但 has_vue=False）。故 vue_http 模式跳过捕获。
            if not getattr(self._transport, "vue_http", False):
                self._setup_api_capture()
        return self._page

    def _setup_api_capture(self) -> None:
        """Setup response interception to capture API JSON data."""
        self._api_captures = []

        # NEW: route interception — amend POST body of SPA's natural XHR
        # before it fires (e.g. inject publish_start_time/end_time for date
        # filtering). Only triggers when transport.route_override configured.
        route_cfg = getattr(self._transport, "route_override", None)
        if route_cfg and isinstance(route_cfg, dict) and route_cfg:
            self._setup_route_override(route_cfg)

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

    def _setup_route_override(self, cfg: Dict[str, Any]) -> None:
        """Intercept outgoing POST requests and merge keys into the body.

        Resolves ``{{ today }}`` / ``{{ N_days_ago }}`` templates once at setup
        (dates don't change mid-crawl). The handler amends only requests whose
        original body contains ``body_has_key`` (default ``publish_start_time``)
        to avoid touching unrelated api-v2 calls (e.g. getConfigInfo).

        Config schema (YAML):
            transport:
              route_override:
                url_match: "api-v2"          # substring, required
                method: POST                 # default POST
                body_has_key: "publish_start_time"  # gate: only amend if body has this key
                merge_body:                  # keys to merge into original body
                  publish_start_time: "{{ 1_days_ago }}"
                  publish_end_time: "{{ today }}"
                  limit: 50
        """
        url_match = cfg.get("url_match", "")
        method = (cfg.get("method", "POST") or "POST").upper()
        body_has_key = cfg.get("body_has_key", "publish_start_time")
        merge_body = cfg.get("merge_body", {}) or {}

        # Pre-resolve date templates once
        from .. import resolve_params
        merge_resolved = resolve_params(dict(merge_body), "", "")

        logging.info(
            "SpaRenderAdapter: route_override active (url_match='%s', merge keys=%s)",
            url_match, list(merge_resolved.keys()),
        )

        def _handler(route, request):
            try:
                if request.method.upper() != method:
                    route.continue_()
                    return
                if url_match and url_match not in request.url:
                    route.continue_()
                    return
                post = request.post_data
                if not post:
                    route.continue_()
                    return
                try:
                    body = json.loads(post)
                except Exception:
                    route.continue_()
                    return
                if not isinstance(body, dict) or body_has_key not in body:
                    route.continue_()
                    return
                body.update(merge_resolved)
                route.continue_(post_data=json.dumps(body, ensure_ascii=False))
            except Exception as e:
                logging.warning("SpaRenderAdapter: route_override handler err: %s", e)
                try:
                    route.continue_()
                except Exception:
                    pass

        # page.route glob — use url_match substring; if empty, fall back to '*'
        pattern = url_match if url_match else "*"
        self._page.route(pattern, _handler)

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Navigate to listing page and capture API responses.

        When transport.vue_http is True, uses Vue.__vue__.$http proxy to
        call APIs directly (bypasses signature/CSRF checks on sites like zfcg).

        Supports click_next pagination: first page does full navigation +
        pre_click; subsequent pages click the "next page" button in the
        already-rendered SPA (which triggers an API call with proper signing).
        """
        if getattr(self._transport, "vue_http", False):
            return self._fetch_via_vue_http(page_params, listing_override)

        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        # Resolve {{ today }} / {{ today_colon }} / {{ N_days_ago }} templates in URL
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        url = resolve_url(url, page_val, size_val)
        is_click_next = page_params.get("_click_next", False)
        page_num = page_params.get("_page", 0)
        wait_until = "networkidle" if getattr(self._transport, "network_idle", True) else "commit"
        pre_click = getattr(self._transport, "pre_click", None)

        # Reset click_next state when the listing URL changes (new section / site)
        if self._last_listing_url and self._last_listing_url != url:
            self._click_next_active = False
        self._last_listing_url = url

        if not is_click_next:
            self._click_next_active = False

        for attempt in range(self._config.anti_crawler.max_retries):
            page = self._get_page()
            try:
                self._api_captures = []

                # ── Phase 1: get to the right page state ──
                if is_click_next and self._click_next_active:
                    # Subsequent pages — click the "next page" button.
                    next_sel = pag_cfg.next_page_selector
                    if not next_sel:
                        logging.warning(
                            "SpaRenderAdapter: click_next without next_page_selector"
                        )
                        break
                    try:
                        page.click(next_sel, timeout=5000)
                        logging.info(
                            "SpaRenderAdapter: clicked next page (page %d)", page_num
                        )
                        time.sleep(3)
                    except Exception:
                        # Button not found or not clickable — likely end of pages
                        logging.info(
                            "SpaRenderAdapter: next page button not found, "
                            "end of pages (page %d)", page_num,
                        )
                        return []
                else:
                    # First page (or non-click-next) — full navigation
                    if is_click_next:
                        self._click_next_active = True
                    try:
                        page.goto(url, wait_until=wait_until,
                                  timeout=self._transport.timeout * 1000)
                    except Exception as goto_err:
                        logging.warning(
                            "SpaRenderAdapter: page.goto(%s, wait_until=%s) failed: %s"
                            " — continuing to check DOM",
                            url, wait_until, goto_err,
                        )

                    # Wait for data container to appear
                    wait_selectors = []
                    if self._config.extract and self._config.extract.items_path:
                        wait_selectors.append(self._config.extract.items_path)
                    wait_selectors.extend([
                        "table, .list, .el-table, .ant-table",
                        ".list-item, .case-list a, a[href*='detail']",
                    ])
                    for sel in wait_selectors:
                        try:
                            page.wait_for_selector(sel, state="attached", timeout=10000)
                            break
                        except Exception:
                            continue

                    time.sleep(3)

                    # Pre-extraction UI interaction (e.g. click "当天" date filter)
                    if pre_click:
                        try:
                            # Wait for loading mask to disappear, then click.
                            # Element UI shows el-loading-mask during data fetch
                            # which intercepts pointer events.
                            try:
                                page.wait_for_selector(
                                    ".el-loading-mask", state="hidden", timeout=8000,
                                )
                            except Exception:
                                pass  # mask may have already disappeared
                            page.click(pre_click, timeout=5000, force=True)
                            logging.info(
                                "SpaRenderAdapter: pre_click '%s' clicked, waiting",
                                pre_click,
                            )
                            time.sleep(3)
                        except Exception as e:
                            logging.warning(
                                "SpaRenderAdapter: pre_click '%s' failed: %s",
                                pre_click, e,
                            )

                # ── Phase 2: extract data ──

                # Try API captures first
                if self._api_captures:
                    items = self._extract_from_api_captures()
                    if items:
                        self._last_raw = self._api_captures
                        return items

                # JS extraction (custom snippet from YAML extract.js_extract).
                # When pre_click is used, the SPA has already made a fresh API
                # call with date filtering — if items are empty after polling,
                # there are genuinely no items today.  Don't waste time on
                # reload cycles.
                if (self._config.extract
                        and getattr(self._config.extract, "js_extract", "")):
                    max_cycles = 1 if pre_click else 3
                    for reload_cycle in range(max_cycles):
                        for js_attempt in range(5):
                            items = self._extract_via_js(
                                page, self._config.extract.js_extract,
                            )
                            if items:
                                self._last_raw = items
                                return items
                            if js_attempt < 4:
                                time.sleep(3)
                        if reload_cycle < max_cycles - 1:
                            logging.info(
                                "SpaRenderAdapter: js_extract empty, reloading"
                                " (cycle %d/%d)", reload_cycle + 1, max_cycles,
                            )
                            try:
                                page.goto(url, wait_until=wait_until,
                                          timeout=self._transport.timeout * 1000)
                                if pre_click:
                                    try:
                                        page.click(pre_click, timeout=5000)
                                        time.sleep(3)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            time.sleep(3)
                    # No items found — return empty list (normal for date-filtered
                    # pages where no items match the filter).
                    if pre_click:
                        logging.info(
                            "SpaRenderAdapter: no items after pre_click — "
                            "date filter returned 0 results"
                        )
                        return []
                    raise RuntimeError(
                        "js_extract returned empty after reload cycles"
                    )

                # Fall back to DOM extraction (only when no js_extract configured)
                result = self._extract_from_dom(page)
                self._last_raw = result
                return result

            except Exception as e:
                logging.warning(
                    "SpaRenderAdapter: attempt %d failed: %s", attempt + 1, e,
                )
                time.sleep(2 ** attempt)
                self._click_next_active = False  # reset on failure
                try:
                    if self._page:
                        try:
                            self._page.close()
                        except Exception:
                            pass
                    self._page = None
                except Exception:
                    pass

        return None

    def _extract_from_api_captures(self) -> List[Dict[str, Any]]:
        """Extract items from captured API responses.

        Handles nested structures like {"code":200, "data": {"Rows": [...]}}.

        When multiple captures contain a list under the items_field, picks the
        one whose items contain the most expected source-field names from
        ``extract.fields`` (e.g. ID/TITLE/DATE). This filters out setup XHRs
        that fire on SPA boot (``getConfigInfo``, ``getInAreas_fb``) which also
        return JSON lists but with completely different schemas.
        """
        items_field = self._config.pagination.items_field
        # Expected source field names from extract.fields config
        expected_keys = set(self._config.extract.fields.values()) if self._config.extract.fields else set()
        # Drop empties
        expected_keys = {k for k in expected_keys if k}

        candidates: List[tuple] = []  # (score, items, capture)
        for capture in self._api_captures:
            data = capture.get("data")
            if not isinstance(data, dict):
                continue
            # Resolve list of items from this capture
            items = None
            if items_field:
                v = self._get_nested_value(data, items_field)
                if isinstance(v, list):
                    items = v
            if items is None:
                for key in ("rows", "Rows", "data", "Data", "list", "records", "result", "results"):
                    if key in data:
                        val = data[key]
                        if isinstance(val, list):
                            items = val
                            break
                        if isinstance(val, dict):
                            nested = self._extract_from_dict(val, items_field or "")
                            if nested:
                                items = nested
                                break
            if not items:
                continue
            # Score: how many expected source keys appear in the first item?
            score = 0
            if expected_keys and items and isinstance(items[0], dict):
                first_keys = set(items[0].keys())
                score = len(expected_keys & first_keys)
            candidates.append((score, items, capture))

        if not candidates:
            return []

        # If any candidate has score>0 (matches expected fields), pick highest.
        # Otherwise fall back to the last capture (heuristic: listing API
        # usually fires last after setup XHRs).
        max_score = max(s for s, _, _ in candidates)
        if max_score > 0:
            best = max(candidates, key=lambda c: c[0])
            return best[1]
        # No field match — return last candidate's items (most recent XHR)
        return candidates[-1][1]

    def _extract_from_dict(self, data: dict, items_field: str) -> list:
        """Recursively extract items from a nested dict."""
        if items_field:
            items = self._get_nested_value(data, items_field)
            if isinstance(items, list):
                return items
        for key in ("rows", "Rows", "data", "Data", "list", "records", "result", "results"):
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
        # network_idle=false 时用 domcontentloaded：政府站 analytics/长连接使
        # networkidle 永不空闲，goto 会超时 → Vue app 未初始化 → $http not found crash（如三明）
        wait_until = "networkidle" if getattr(self._transport, "network_idle", True) else "domcontentloaded"
        try:
            page.goto(site_url, wait_until=wait_until,
                      timeout=self._transport.timeout * 1000)
        except Exception as e:
            logging.warning("SpaRenderAdapter: vue context goto %s failed (continuing): %s",
                            wait_until, e)
        # 政府站 Vue 初始化慢，轮询等待 $http 就绪（最多 ~15s），避免 sleep(3) 太短误判
        ready = False
        for _ in range(6):
            time.sleep(2)
            try:
                ready = page.evaluate(
                    "() => { const app = document.querySelector('#app'); const vm = app && app.__vue__; return !!(vm && (vm.$http || vm.$axios)); }")
            except Exception:
                ready = False
            if ready:
                break
        if not ready:
            raise RuntimeError(f"Vue $http/$axios not found on {site_url}")

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
                # 注意：listing_params 可能含 {{ today }} / {{ N_days_ago }} 等日期模板，
                # 必须先 resolve，否则传给 API 的是字面模板串（如 operationStartTime 无效），
                # API 返回非 200 → 误判验证码错误（code=4009）。
                verify_params = dict(listing_params)
                verify_params.update({
                    "currPage": "1",
                    "pageSize": "1",
                    "verifyCode": code_val,
                })
                verify_params = resolve_params(verify_params, "1", "1")
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

        Supports GET (params) and POST (JSON body).  For POST, merges page_params
        and resolves ``{{ today }}`` / ``{{ N_days_ago }}`` templates in the body.

        When captcha is configured (type: ocr), solves the captcha via ddddocr
        and injects ``verifyCode`` into every API call.
        """
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        params = dict(listing.params)
        method = (listing.method or "GET").upper()
        pag_cfg = self._config.pagination

        if method == "POST":
            # POST: build body from listing.body, merge page_params, resolve templates
            body = dict(listing.body)
            body.update(page_params)
            page_val = str(page_params.get(pag_cfg.page_param, ""))
            size_val = str(page_params.get(pag_cfg.page_size_param, ""))
            body = resolve_params(body, page_val, size_val)
            # Auto-generate ts (timestamp) if present with value 0
            if "ts" in body and (body["ts"] == 0 or body["ts"] == "0"):
                body["ts"] = int(time.time() * 1000)
        else:
            # GET: merge page_params into query params
            body = {}
            params.update(page_params)
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
                        if method == "POST":
                            body["verifyCode"] = code
                        else:
                            params["verifyCode"] = code

                # Build opts dict for JS snippet
                http_opts: Dict[str, Any] = {}
                if method == "POST":
                    http_opts["method"] = "POST"
                    http_opts["body"] = body
                else:
                    http_opts["params"] = params

                result_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [url, http_opts],
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

        Supports GET (params) and POST (JSON body).  Resolves ``{key}`` and
        ``{{ key }}`` placeholders in both URL and body from item fields.

        When captcha is configured (type: ocr), injects ``verifyCode`` into
        every detail API call.
        """
        detail_cfg = detail_override or self._config.detail
        detail_url = detail_cfg.url
        detail_method = (detail_cfg.method or "GET").upper()
        params = dict(detail_cfg.params)
        body = dict(detail_cfg.body)

        # Resolve {key} and {{ key }} placeholders in URL, params, and body
        for key, val in item.items():
            detail_url = detail_url.replace("{" + key + "}", str(val))
            for pkey, pval in params.items():
                if isinstance(pval, str):
                    params[pkey] = pval.replace("{" + key + "}", str(val))
                    params[pkey] = params[pkey].replace("{{ " + key + " }}", str(val))
            for bkey, bval in body.items():
                if isinstance(bval, str):
                    body[bkey] = bval.replace("{" + key + "}", str(val))
                    body[bkey] = body[bkey].replace("{{ " + key + " }}", str(val))

        # Inject channel + siteId from transport/listing config if not in params
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
                        if detail_method == "POST":
                            body["verifyCode"] = code
                        else:
                            params["verifyCode"] = code

                # Build opts dict for JS snippet
                http_opts: Dict[str, Any] = {}
                if detail_method == "POST":
                    http_opts["method"] = "POST"
                    http_opts["body"] = body
                else:
                    http_opts["params"] = params

                result_json = page.evaluate(
                    _VUE_HTTP_JS,
                    [detail_url, http_opts],
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
                # 归一化结构化附件列表（gpcms 站点详情返回 attchList，元素 {fileName, fileUrl, fileExt}）。
                # 转成 item["files"] 命中 engine._extract_files_from_item 白名单（files/attachments/fileList），
                # 跳过 HTML 扫描，直接进入 NormalizedItem.attachments → CollectionWriter 存 attachments JSON +
                # AttachmentHandler 下载。否则 attchList 会被丢弃（content_field 只取正文）。
                attch_list = (self._get_nested_value(data, "data.attchList")
                              or self._get_nested_value(data, "data.attachList")
                              or self._get_nested_value(data, "data.fileList"))
                if isinstance(attch_list, list) and attch_list:
                    files = []
                    for a in attch_list:
                        if not isinstance(a, dict):
                            continue
                        furl = a.get("fileUrl") or a.get("url") or ""
                        if not furl:
                            continue
                        furl = self._normalize_freecms_url(furl)
                        fname = a.get("fileName") or a.get("name") or "attachment"
                        fext = a.get("fileExt") or ""
                        # 标准化后缀：确保带前导点（防御 fileExt 无点的情况，
                        # 否则 _process_one 的 ".zip" 判断 + RAGFlow parser 选型会失败）
                        if fext and not fext.startswith("."):
                            fext = "." + fext
                        # gpcms attchList 的 fileName 常不带后缀，用 fileExt 补全：
                        # RAGFlow 按后缀选 parser；ZIP 需 .zip 后缀 AttachmentHandler 才解压
                        if fext and not fname.lower().endswith(fext.lower()):
                            fname = fname + fext
                        files.append({
                            "file_name": fname,
                            "file_url": furl,
                            "file_suffix": fext,
                        })
                    if files:
                        item["files"] = files
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

    def _normalize_freecms_url(self, file_url: str) -> str:
        """归一化 freecms/gpcms 下载 URL：去掉 /freecms/download/ 代理前缀。

        老脚本 zfcg_crawler.py 证实：attchList 里的 fileUrl 形如
        ``https://host/freecms/download/gpx-public-file?accessCode=...``，
        直接下载返回 404；必须去掉 ``/freecms/download/`` 代理前缀，变成
        ``https://host/gpx-public-file?accessCode=...`` 才返回真实文件（带
        accessCode 自包含令牌，无需 cookies/签名）。

        新版 gpcms（如 zfcg.czj.ningde.gov.cn 政策法规栏目）的 fileUrl 是
        相对存储路径 ``/usr/local/jdyj/202507/<UUID>.pdf``，浏览器渲染时前
        端会自动加 ``/gpcms`` 前缀拼成 ``/gpcms/usr/local/jdyj/...``。本函
        数对相对路径补全站点 host + ``/gpcms`` 前缀，否则返回
        ``https:///usr/local/...``（无 host）导致下载 404。
        """
        if not file_url:
            return file_url
        from urllib.parse import urlparse
        parsed = urlparse(file_url)
        norm = parsed.path
        if parsed.query:
            norm += "?" + parsed.query
        for prefix in (
            "/freecms/download/gateway/gpx-document-zc/common/v3/base/download/",
            "/freecms/download/",
        ):
            if norm.startswith(prefix):
                norm = norm[len(prefix):]
                break
        norm = re.sub(r"^/?downloadPublicFile(\?)", r"/gpx-public-file\1", norm)
        if not norm.startswith("/"):
            norm = "/" + norm

        # 相对路径（无 host）→ 补全 site_url 的 scheme + netloc
        # 新版 gpcms 存储路径（/usr/local/...）需追加 /gpcms 前缀（前端行为）
        if not parsed.netloc:
            site_parsed = urlparse(self._config.site_url or "")
            host = site_parsed.netloc or ""
            # 已含 /gpcms 或 /freecms 前缀的不再追加
            if not (norm.startswith("/gpcms/") or norm.startswith("/freecms/")):
                norm = "/gpcms" + norm
            scheme = site_parsed.scheme or "https"
            return f"{scheme}://{host}{norm}"

        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}{norm}"

    def _extract_via_js(self, page, js_source: str) -> List[Dict[str, Any]]:
        """Run a YAML-supplied JS snippet on the page and return its result.

        The snippet is passed to ``page.evaluate`` and must return a list of
        plain dicts.  Use this when CSS selectors can't express the extraction
        (e.g. an ``<a>`` element's own text with a date suffix stripped).
        """
        try:
            result = page.evaluate(js_source)
            if isinstance(result, list):
                return result
            logging.warning("SpaRenderAdapter: js_extract returned %s, expected list",
                            type(result).__name__)
        except Exception as e:
            logging.warning("SpaRenderAdapter: js_extract failed: %s", e)
        return []

    def _extract_from_dom(self, page) -> List[Dict[str, Any]]:
        """Extract items from rendered DOM.

        If the site is configured with ``extract.type == css_selector``, return
        the full rendered HTML as a single-item list so the engine's CSS
        extractor pipeline (``_maybe_extract_from_html``) can apply the
        configured ``items_path`` and ``fields``.  Otherwise fall back to
        generic JS-based extraction.
        """
        # CSS-extractor mode: hand the rendered HTML back to the engine.
        if self._config.extract and self._config.extract.type == "css_selector":
            try:
                html = page.content()
                self._last_raw = html
                return [{"html": html}]
            except Exception as e:
                logging.warning("SpaRenderAdapter: HTML capture failed: %s", e)
                return []

        # Generic JS extraction for sites without explicit CSS config.
        try:
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
            return self._fetch_detail_css(item, detail_cfg=detail_cfg)

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
                _wu = "networkidle" if getattr(self._transport, "network_idle", True) else "commit"
                try:
                    page.goto(detail_url, wait_until=_wu,
                              timeout=self._transport.timeout * 1000)
                except Exception as goto_err:
                    logging.warning(
                        "SpaRenderAdapter: detail goto failed (continuing): %s", goto_err,
                    )
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

    def _fetch_detail_css(self, item: Dict[str, Any], detail_cfg=None) -> Dict[str, Any]:
        """Override base class: use Playwright instead of requests.get().

        Navigates to the detail page with Playwright (renders SPA JS),
        then applies the same BeautifulSoup content extraction as the base.
        """
        detail_cfg = detail_cfg or self._config.detail
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
                _wu = "networkidle" if getattr(self._transport, "network_idle", True) else "commit"
                try:
                    page.goto(detail_url, wait_until=_wu,
                              timeout=self._transport.timeout * 1000)
                except Exception as goto_err:
                    logging.warning(
                        "SpaRenderAdapter: detail css goto failed (continuing): %s", goto_err,
                    )
                # Wait for the content container to render before extracting.
                # SPA detail pages fetch values via XHR after initial render;
                # without this wait, labels appear but values are still empty.
                # Mirrors the list path's wait_for_selector + sleep(3) strategy.
                if content_field:
                    try:
                        page.wait_for_selector(content_field, timeout=10000)
                    except Exception:
                        pass
                time.sleep(3)
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
                    else:
                        logging.warning(
                            "SpaRenderAdapter: selector '%s' sel_one=%s text_len=%s (html_len=%d) in %s",
                            sel, bool(el),
                            len(el.get_text(strip=True)) if el else 0,
                            len(html), detail_url,
                        )

                if container:
                    item["content"] = self._html_to_text(container)
                else:
                    logging.warning(
                        "SpaRenderAdapter: no content selector matched in %s",
                        detail_url,
                    )
                    item["content"] = self._strip_and_extract(soup)

                item["detail_html"] = html

                # Extract structured metadata via JS if configured
                metadata_js = getattr(detail_cfg, "metadata_js", "")
                if metadata_js:
                    try:
                        metadata = page.evaluate(metadata_js)
                        if isinstance(metadata, dict) and metadata:
                            item.update(metadata)
                            logging.info(
                                "SpaRenderAdapter: extracted metadata fields: %s",
                                list(metadata.keys()),
                            )
                    except Exception as meta_err:
                        logging.warning(
                            "SpaRenderAdapter: metadata_js failed: %s", meta_err,
                        )

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
