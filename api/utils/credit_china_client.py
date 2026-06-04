#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
Credit China (信用中国) Severely Dishonest Entity Query Client

Uses Playwright + ddddocr to query creditchina.gov.cn/xinxigongshi/shixinheimingdan.

The browser runs in a **dedicated worker thread** so that Playwright's
single-thread requirement is satisfied.

Anti-scraping layers:
  1. WAF (创宇盾) — cookie + rcwCQitg URL token via monkey-patched XHR
  2. Image captcha — 4-char, ORB blocks cross-origin <img> load
  3. Rate limiting — captcha valid ~5 minutes after verification
"""
import base64
import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

import ddddocr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CreditChinaResult:
    index: int = 0
    name: str = ""
    type: str = ""
    date: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class CreditChinaSearchResult:
    results: list = field(default_factory=list)
    total_size: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Dedicated worker thread — all Playwright operations happen here
# ---------------------------------------------------------------------------
_BASE_URL = "https://www.creditchina.gov.cn/xinxigongshi/shixinheimingdan/"
_MIN_INTERVAL = float(os.environ.get("CREDIT_CHINA_MIN_INTERVAL", "60"))

_task_queue: queue.Queue = queue.Queue()
_worker_started = False
_worker_thread = None
_last_request_time = 0.0


def _worker_loop():
    """Long-running worker that owns the browser and processes tasks sequentially."""
    from playwright.sync_api import sync_playwright

    global _last_request_time

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )

    # Apply stealth patches
    try:
        import tf_playwright_stealth
        tf_playwright_stealth.stync_sync(context)
        logger.info("tf-playwright-stealth applied")
    except Exception as e:
        logger.warning("stealth patch failed (non-critical): %s", e)

    ocr = ddddocr.DdddOcr(show_ad=False)
    logger.info("Credit China worker thread started (headless chromium + ddddocr)")

    while True:
        try:
            params, result_event = _task_queue.get()
            if params is None:
                logger.info("Credit China worker shutting down")
                break

            # Rate limit
            elapsed = time.monotonic() - _last_request_time
            if elapsed < _MIN_INTERVAL:
                wait = _MIN_INTERVAL - elapsed
                logger.info("Credit China rate-limit: waiting %.1fs", wait)
                time.sleep(wait)

            result = _do_search(context, browser, ocr, **params)
            _last_request_time = time.monotonic()
            result_event["result"] = result

        except Exception as e:
            logger.exception("Credit China worker error: %s", e)
            try:
                params, result_event = _task_queue.get_nowait()
            except Exception:
                pass

    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass
    logger.info("Credit China worker thread stopped")


def _ensure_worker():
    global _worker_started, _worker_thread
    if not _worker_started:
        _worker_started = True
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="credit-china-worker"
        )
        _worker_thread.start()


def _fetch_captcha_via_xhr(page):
    """Fetch captcha image via XHR (WAF wraps it with rcwCQitg). Returns bytes or None."""
    result = page.evaluate("""
        () => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', 'https://public.creditchina.gov.cn/private-api/verify/getVerify?_v=' + Math.random(), true);
                xhr.responseType = 'arraybuffer';
                xhr.onload = function() {
                    if (xhr.status === 200 && xhr.response.byteLength > 100) {
                        const arr = new Uint8Array(xhr.response);
                        let binary = '';
                        for (let i = 0; i < arr.length; i++) binary += String.fromCharCode(arr[i]);
                        resolve(btoa(binary));
                    } else {
                        resolve(JSON.stringify({error: 'bad status', status: xhr.status, size: xhr.response.byteLength}));
                    }
                };
                xhr.onerror = () => resolve(JSON.stringify({error: 'xhr error'}));
                xhr.timeout = 10000;
                xhr.ontimeout = () => resolve(JSON.stringify({error: 'timeout'}));
                xhr.send();
            });
        }
    """)
    if result:
        if result.startswith("{"):
            logger.warning("XHR captcha failed: %s", result)
            return None
        return base64.b64decode(result)
    return None


def _reload_page(page):
    """Re-navigate to the base URL for a clean page state."""
    page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector(".mask", state="hidden", timeout=15000)
    except Exception:
        pass
    page.wait_for_selector("#blackNameList input", timeout=10000)




def _parse_native_response(native_data):
    """Parse the page's native search API response into CreditChinaSearchResult."""
    data = native_data.get("data", {})
    items_raw = data.get("list", [])
    total_size = data.get("totalSize", 0)
    results = []
    for i, item in enumerate(items_raw):
        results.append(CreditChinaResult(
            index=i + 1,
            name=item.get("name", item.get("entityName", "")),
            type=item.get("type", item.get("entityType", "")),
            date=item.get("date", item.get("publishDate", "")),
            raw=item,
        ))
    logger.info("Parsed %d results (total=%d)", len(results), total_size)
    return CreditChinaSearchResult(results=results, total_size=total_size)


def _do_search(context, browser, ocr, keyword, page_num=1, page_size=10, max_retries=5):
    """Run one search attempt. Called from the worker thread."""
    page = None
    try:
        page = context.new_page()

        # ---- Response capture for native search API calls ----
        search_response_holder = {"data": None}

        def _on_response(resp):
            url = resp.url
            if "typeNameAndCount" in url and search_response_holder["data"] is None:
                try:
                    body = resp.text()
                    data = json.loads(body)
                    if data.get("status") == 1:
                        search_response_holder["data"] = data
                        logger.info("Captured native search response: total=%s",
                                    data.get("data", {}).get("totalSize", "?"))
                except Exception:
                    pass

        page.on("response", _on_response)

        page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=30000)
        # Wait for WAF challenge to complete
        try:
            page.wait_for_selector(".mask", state="hidden", timeout=15000)
        except Exception:
            pass
        page.wait_for_selector("#blackNameList input", timeout=10000)
        time.sleep(1)

        for attempt in range(max_retries):
            logger.info(
                "Credit China search attempt %d/%d: keyword=%s",
                attempt + 1, max_retries, keyword,
            )

            # Install route handler with fulfill (required for XHR captcha to work)
            captcha_holder = {"data": None}

            def _handle_route(route):
                try:
                    resp = route.fetch()
                    body = resp.body()
                    if resp.status == 200 and len(body) > 100:
                        captcha_holder["data"] = body
                        headers = {k: v for k, v in resp.headers.items()
                                   if k != "content-length"}
                        headers["access-control-allow-origin"] = "*"
                        route.fulfill(response=resp, headers=headers, body=body)
                    else:
                        route.continue_()
                except Exception:
                    route.continue_()

            page.route("**/private-api/verify/getVerify**", _handle_route)

            # 1. Click search using native Playwright click (triggers full event chain)
            page.fill("#blackNameList input", keyword)
            try:
                page.click(".infoCheckBtn", timeout=5000)
            except Exception:
                page.evaluate("document.querySelector('.infoCheckBtn').click()")

            # 2. Wait for captcha modal
            try:
                page.wait_for_selector(".vcodepop", timeout=8000)
            except Exception:
                # No captcha — check for DOM results or native API response
                try:
                    row_count = page.evaluate(
                        "document.querySelectorAll('#blackNameList table tbody tr').length"
                    )
                    if row_count > 0:
                        logger.info("Results appeared without captcha")
                        return _extract_results_from_dom(page) or CreditChinaSearchResult(
                            error="查询失败，请重试"
                        )
                except Exception:
                    pass
                # Check if page's native search already responded
                if search_response_holder["data"] is not None:
                    native_data = search_response_holder["data"]
                    logger.info("Using native response (no captcha path)")
                    return _parse_native_response(native_data)
                logger.info("No captcha modal and no native response, reloading...")
                _reload_page(page)
                continue

            # ---- Captcha loop: OCR → verify via UI ----
            for captcha_try in range(5):
                # 3. Get captcha image via XHR (fast, skip slow route handler)
                time.sleep(0.5)
                captcha_data = None
                try:
                    captcha_data = _fetch_captcha_via_xhr(page)
                except Exception as e:
                    logger.warning("XHR error: %s", e)

                if not captcha_data:
                    logger.warning("No captcha image, refreshing...")
                    page.evaluate(
                        "document.querySelector('.vcodepop .vcodeimgbox span').click()"
                    )
                    time.sleep(1)
                    continue

                # 4. OCR with preprocessing fallback
                captcha_text = ocr.classification(captcha_data).strip().upper()

                # If OCR result too short, try binarized version
                if len(captcha_text) < 4:
                    logger.info("OCR short result '%s', trying binarized...", captcha_text)
                    try:
                        from PIL import Image
                        import io as _io
                        img = Image.open(_io.BytesIO(captcha_data))
                        gray = img.convert('L').point(lambda x: 0 if x < 128 else 255, '1')
                        buf = _io.BytesIO()
                        gray.save(buf, format='PNG')
                        captcha_text2 = ocr.classification(buf.getvalue()).strip().upper()
                        logger.info("Binarized OCR: '%s'", captcha_text2)
                        if len(captcha_text2) >= len(captcha_text):
                            captcha_text = captcha_text2
                    except Exception as e:
                        logger.warning("Binarized OCR error: %s", e)

                logger.info("Captcha OCR: '%s' (len=%d)", captcha_text, len(captcha_text))

                if not captcha_text or len(captcha_text) < 3:
                    logger.warning("Weak OCR, refreshing captcha...")
                    page.evaluate("document.querySelector('.vcodepop .vcodeimgbox span').click()")
                    time.sleep(1)
                    continue

                # 5. Fill captcha and click verify
                try:
                    if not page.locator(".vcodepop").is_visible():
                        break  # Modal closed, exit captcha loop
                except Exception:
                    break

                page.fill("#vcode", captcha_text)
                page.evaluate("document.querySelector('.vcodepop .confirm').click()")
                time.sleep(2)

                # Check if modal closed (= verification succeeded)
                try:
                    modal_visible = page.locator(".vcodepop").is_visible()
                except Exception:
                    modal_visible = False

                if not modal_visible:
                    # Captcha verified! Page will call search API on re-click.
                    logger.info("Captcha verified! Re-clicking search...")
                    time.sleep(1)
                    search_response_holder["data"] = None

                    page.fill("#blackNameList input", keyword)
                    try:
                        page.click(".infoCheckBtn", timeout=5000)
                    except Exception:
                        page.evaluate("document.querySelector('.infoCheckBtn').click()")

                    # Wait for page's native search API response (not our XHR)
                    for _ in range(20):
                        if search_response_holder["data"] is not None:
                            break
                        time.sleep(1)
                    else:
                        # Check for captcha
                        try:
                            if page.locator(".vcodepop").is_visible():
                                logger.info("Re-click triggered captcha, solving...")
                                continue
                        except Exception:
                            pass

                    native_data = search_response_holder["data"]
                    if native_data:
                        logger.info("Using native search response")
                        return _parse_native_response(native_data)

                    # Only as last resort, try DOM
                    try:
                        page.wait_for_selector("#blackNameList table tbody tr", timeout=5000)
                    except Exception:
                        pass
                    dom_result = _extract_results_from_dom(page)
                    if dom_result and dom_result.results:
                        logger.info("DOM results: %d rows", len(dom_result.results))
                        return dom_result

                    logger.warning("No results after captcha OK + re-click")
                    break

                # Check error
                try:
                    error_tip = page.text_content(".errortip")
                    if error_tip and ("错误" in error_tip or "不正确" in error_tip):
                        logger.warning("Captcha error: %s", error_tip)
                        page.evaluate("document.querySelector('.vcodepop .vcodeimgbox span').click()")
                        time.sleep(1)
                        continue
                    if error_tip and "失效" in error_tip:
                        logger.warning("Captcha expired")
                        break
                except Exception:
                    pass

                # Refresh captcha for next try
                try:
                    if page.locator(".vcodepop").is_visible():
                        page.evaluate("document.querySelector('.vcodepop .vcodeimgbox span').click()")
                        time.sleep(1)
                    else:
                        break  # Modal closed, exit captcha loop
                except Exception:
                    break

            else:
                # Exhausted captcha retries - reload page
                logger.info("Exhausted captcha retries, reloading page...")
                _reload_page(page)

        return CreditChinaSearchResult(error="验证码识别多次失败，请稍后重试")

    except Exception as e:
        logger.exception("Credit China search error")
        return CreditChinaSearchResult(error=f"查询异常: {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def _extract_results_from_dom(page):
    """Extract search results from the page DOM table. Returns CreditChinaSearchResult or None."""
    try:
        rows = page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#blackNameList table tbody tr');
                if (!rows.length) return [];
                return Array.from(rows).map((row, i) => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 3) return null;
                    return {
                        index: i + 1,
                        name: (cells[0] || {}).textContent || '',
                        type: (cells[1] || {}).textContent || '',
                        date: (cells[2] || {}).textContent || ''
                    };
                }).filter(Boolean);
            }
        """)
        if not rows:
            return None
        results = []
        for r in rows:
            results.append(CreditChinaResult(
                index=r.get("index", 0),
                name=r.get("name", "").strip(),
                type=r.get("type", "").strip(),
                date=r.get("date", "").strip(),
            ))
        logger.info("DOM extraction: %d rows", len(results))
        if results:
            return CreditChinaSearchResult(results=results, total_size=len(results))
        return None
    except Exception as e:
        logger.warning("DOM extraction failed: %s", e)
        return None


def _extract_results(page, keyword):
    """Extract search results from the page. Returns CreditChinaSearchResult or None."""
    try:
        # Use XHR to call the search API directly via page context
        js_code = """
        (keyword) => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                const params = new URLSearchParams({
                    keyword: keyword,
                    searchState: '1',
                    type: 'shixinheimingdan',
                    entityType: '',
                    page: '1',
                    pageSize: '10'
                });
                xhr.open('GET', 'https://public.creditchina.gov.cn/private-api/typeNameAndCountSearch?' + params.toString(), true);
                xhr.onreadystatechange = function() {
                    if (xhr.readyState === 4) {
                        try {
                            resolve(JSON.parse(xhr.responseText));
                        } catch(e) {
                            resolve({error: 'parse error', text: xhr.responseText.substring(0, 500)});
                        }
                    }
                };
                xhr.onerror = () => resolve({error: 'xhr error'});
                xhr.timeout = 15000;
                xhr.ontimeout = () => resolve({error: 'timeout'});
                xhr.send();
            });
        }
        """
        result_json = page.evaluate(js_code, keyword)
        logger.info("Search API response: %s", json.dumps(result_json, ensure_ascii=False)[:500])

        if isinstance(result_json, dict) and result_json.get("status") != 1:
            err_msg = result_json.get("message", "unknown error")
            if "验证码" in err_msg or "刷新" in err_msg or str(result_json.get("code")) == "40001":
                return None  # Token/captcha expired, trigger retry with page reload
            return CreditChinaSearchResult(error=err_msg)

        data = result_json.get("data", {})
        items_raw = data.get("list", [])
        total_size = data.get("totalSize", 0)

        results = []
        for i, item in enumerate(items_raw):
            results.append(
                CreditChinaResult(
                    index=i + 1,
                    name=item.get("name", item.get("entityName", "")),
                    type=item.get("type", item.get("entityType", "")),
                    date=item.get("date", item.get("publishDate", "")),
                    raw=item,
                )
            )

        logger.info("Credit China search OK: %d results, total=%d", len(results), total_size)
        return CreditChinaSearchResult(results=results, total_size=total_size)

    except Exception as e:
        logger.warning("Failed to extract results: %s", e)
        return None


def search_credit_china(
    keyword: str = "",
    page_num: int = 1,
    page_size: int = 10,
    max_retries: int = 5,
    timeout: float = 120.0,
) -> CreditChinaSearchResult:
    """
    Query 严重失信主体 from creditchina.gov.cn.

    Thread-safe: submits the task to the dedicated browser worker thread
    and blocks until the result is ready (or timeout).

    Args:
        keyword:     主体名称/统一社会信用代码
        page_num:    页码 (default 1)
        page_size:   每页条数 (default 10)
        max_retries: captcha retry count
        timeout:     max seconds to wait for a result
    """
    _ensure_worker()

    if not keyword:
        return CreditChinaSearchResult(error="请输入查询关键词")

    result_event = {"result": None}
    _task_queue.put(({
        "keyword": keyword,
        "page_num": page_num,
        "page_size": page_size,
        "max_retries": max_retries,
    }, result_event))

    deadline = time.monotonic() + timeout
    while result_event["result"] is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CreditChinaSearchResult(error="查询超时，请稍后重试")
        time.sleep(min(1, remaining))

    return result_event["result"]
