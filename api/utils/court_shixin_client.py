#
#  Copyright 2026 The InfiniFlow Authors. All Rights. Reserved.
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
Court Dishonest Debtor (失信被执行人) Query Client

Uses Playwright + ddddocr to query zxgk.court.gov.cn/shixin.

The browser runs in a **dedicated worker thread** so that Playwright's
single-thread requirement is satisfied regardless of which Quart async
worker calls search_shixin().

Anti-scraping layers:
  1. WAF (创宇盾) — cookie + URL token
  2. Image captcha — 4-char, server-session-bound
  3. Rate limiting — max 1 request per 45 seconds
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
class ShixinResult:
    index: int = 0
    name: str = ""
    reg_date: str = ""
    case_code: str = ""
    id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ShixinSearchResult:
    results: list = field(default_factory=list)
    current_page: int = 1
    total_size: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Dedicated worker thread — all Playwright operations happen here
# ---------------------------------------------------------------------------
_BASE_URL = "https://zxgk.court.gov.cn/shixin"
_MIN_INTERVAL = float(os.environ.get("COURT_SHIXIN_MIN_INTERVAL", "45"))

_task_queue: queue.Queue = queue.Queue()  # (params_dict, result_event)
_worker_started = False
_worker_thread = None


def _worker_loop():
    """Long-running worker that owns the browser and processes tasks sequentially."""
    from playwright.sync_api import sync_playwright

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
    logger.info("Court worker thread started (headless chromium + ddddocr)")

    while True:
        try:
            params, result_event = _task_queue.get()
            if params is None:
                # Shutdown signal
                logger.info("Court worker shutting down")
                break

            result = _do_search(context, browser, ocr, **params)
            result_event["result"] = result

        except Exception as e:
            logger.exception("Court worker error: %s", e)
            # Try to send error back if result_event is available
            # This is best-effort; if we can't, the caller will timeout
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
    logger.info("Court worker thread stopped")


def _ensure_worker():
    global _worker_started, _worker_thread
    if not _worker_started:
        _worker_started = True
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="court-worker")
        _worker_thread.start()


def _do_search(context, browser, ocr, name, card_num, province, max_retries):
    """Run one search attempt. Called from the worker thread."""
    page = None
    try:
        page = context.new_page()
        page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#pName", timeout=10000)

        for attempt in range(max_retries):
            logger.info(
                "Court search attempt %d/%d: name=%s card_num=%s",
                attempt + 1, max_retries, name, card_num,
            )

            # 1. Refresh captcha
            page.evaluate("() => refresh()")
            page.wait_for_selector("#captchaImg", state="attached", timeout=5000)
            page.wait_for_function(
                """() => {
                    const img = document.getElementById('captchaImg');
                    return img && img.complete && img.naturalWidth > 0;
                }""",
                timeout=10000,
            )

            captcha_id = page.evaluate("() => document.getElementById('captchaId').value")

            # 2. Extract captcha image via canvas
            captcha_b64 = page.evaluate("""() => {
                const img = document.getElementById('captchaImg');
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return canvas.toDataURL('image/png').split(',')[1];
            }""")
            captcha_bytes = base64.b64decode(captcha_b64)

            captcha_text = ocr.classification(captcha_bytes)
            captcha_text = captcha_text.strip()
            logger.info("Captcha OCR: '%s' (id=%s)", captcha_text, captcha_id[:8])

            if not captcha_text:
                logger.warning("Empty captcha OCR, retrying...")
                continue

            # 3. Fill form
            page.fill("#pName", name)
            page.fill("#pCardNum", card_num)
            page.select_option("#pProvince", province)
            page.fill("#yzm", captcha_text)

            # 4. Submit via AJAX (same as the page's own search() function)
            result_text = page.evaluate(
                """() => {
                    return new Promise((resolve, reject) => {
                        $.ajax({
                            url: 'searchSX.do',
                            type: 'post',
                            dataType: 'json',
                            data: $('#myform').serialize(),
                            success: function(json) {
                                resolve(JSON.stringify(json));
                            },
                            error: function(xhr) {
                                resolve('AJAX_ERROR:' + xhr.status);
                            }
                        });
                    });
                }"""
            )

            # 5. Parse
            if result_text == "error" or result_text.startswith("AJAX_ERROR"):
                logger.warning("Search returned '%s', retrying...", result_text)
                continue

            json_data = json.loads(result_text)
            if not isinstance(json_data, list) or len(json_data) == 0:
                return ShixinSearchResult(error="服务器返回空数据")

            item = json_data[0]
            raw_results = item.get("result", [])
            current_page = item.get("currentPage", 1)
            total_size = item.get("totalSize", 0)

            results = []
            for i, r in enumerate(raw_results):
                rd = r.get("regDate")
                reg_date = ""
                if rd:
                    reg_date = f"{rd.get('year', 0) + 1900}年{rd.get('month', 0) + 1}月{rd.get('date', 0)}日"
                results.append(
                    ShixinResult(
                        index=i + 1,
                        name=r.get("iname", ""),
                        reg_date=reg_date,
                        case_code=r.get("caseCode", ""),
                        id=r.get("id", ""),
                        raw=r,
                    )
                )

            logger.info(
                "Court search OK: %d results, total=%d", len(results), total_size
            )
            return ShixinSearchResult(
                results=results,
                current_page=current_page,
                total_size=total_size,
            )

        return ShixinSearchResult(error="验证码识别多次失败，请稍后重试")

    except Exception as e:
        logger.exception("Court search error")
        return ShixinSearchResult(error=f"查询异常: {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def search_shixin(
    name: str = "",
    card_num: str = "",
    province: str = "0",
    max_retries: int = 3,
    timeout: float = 120.0,
) -> ShixinSearchResult:
    """
    Query 失信被执行人 from zxgk.court.gov.cn/shixin.

    Thread-safe: submits the task to the dedicated browser worker thread
    and blocks until the result is ready (or timeout).

    Args:
        name:        被执行人姓名/名称
        card_num:    身份证号码/组织机构代码
        province:    省份代码, "0" = 全国
        max_retries: captcha retry count
        timeout:     max seconds to wait for a result
    """
    _ensure_worker()

    if not name and not card_num:
        return ShixinSearch(error="姓名和证件号至少填写一项")

    result_event = {"result": None}
    _task_queue.put(({
        "name": name,
        "card_num": card_num,
        "province": province,
        "max_retries": max_retries,
    }, result_event))

    # Block until result is ready or timeout
    deadline = time.monotonic() + timeout
    while result_event["result"] is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ShixinSearchResult(error="查询超时，请稍后重试")
        time.sleep(min(1, remaining))

    return result_event["result"]


def search_shixin_detail(case_id: str, case_code: str = "") -> dict:
    """View detail of a specific 失信 record (same worker-thread pattern)."""
    _ensure_worker()

    result_event = {"result": None}
    _task_queue.put(({
        "action": "detail",
        "case_id": case_id,
        "case_code": case_code,
    }, result_event))

    deadline = time.monotonic() + 90.0
    while result_event["result"] is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"error": "获取详情超时"}
        time.sleep(min(1, remaining))

    return result_event["result"]
