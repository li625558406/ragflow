"""
Shared Playwright-based HTTP utilities for web crawlers.

Provides a drop-in replacement for ``requests.get`` / ``requests.post``
that routes all traffic through a headless Chromium instance, making
crawlers resistant to IP blacklisting and JavaScript challenge walls.

Usage
-----
    from rag.svr.crawler_utils import PlaywrightHttpClient

    client = PlaywrightHttpClient()
    resp = client.get("https://example.com/api/data")
    data = resp.json()
    html  = client.get("https://example.com/page").text
"""

import json
import logging
import os
import sys
import time
from contextlib import contextmanager

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Chrome / Chromium discovery
# ---------------------------------------------------------------------------
_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    os.path.expanduser("~/AppData/Local/Google/Chrome/Application/chrome.exe"),
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout  # noqa: F401
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Response wrapper — compatible with requests.Response
# ---------------------------------------------------------------------------
class _PlaywrightResponse:
    """Minimal requests.Response-compatible wrapper."""

    def __init__(self, body, status_code=200, headers=None):
        self._body = body if isinstance(body, (str, bytes)) else ""
        self.status_code = status_code
        self.headers = headers or {}

    def json(self, **kwargs):
        return json.loads(self._body, **kwargs)

    @property
    def text(self):
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return self._body

    @property
    def content(self):
        if isinstance(self._body, str):
            return self._body.encode("utf-8")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Playwright HTTP Client
# ---------------------------------------------------------------------------
class PlaywrightHttpClient:
    """HTTP client backed by a headless Chromium browser.

    All GET/POST requests are executed inside the browser context, giving
    the remote server a real browser fingerprint (headers, TLS, JS env)
    rather than a ``requests`` library fingerprint.
    """

    _user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, headless=True, timeout=60):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "playwright is required.  Run: pip install playwright && playwright install chromium"
            )
        chrome_path = _find_chrome()
        if not chrome_path:
            raise RuntimeError(
                "Chrome not found.  Install Chrome or set one of: " + ", ".join(_CHROME_PATHS)
            )

        self._headless = headless
        self._timeout = timeout
        self._chrome_path = chrome_path
        self._pw = None
        self._browser = None

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        """Launch the browser.  Call once before making requests."""
        if self._browser is not None:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            executable_path=self._chrome_path,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ],
        )
        logging.info("PlaywrightHttpClient browser started")

    def stop(self):
        """Close the browser and free resources."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        logging.info("PlaywrightHttpClient browser stopped")

    def _ensure_started(self):
        if self._browser is None:
            self.start()

    def _navigate_to_origin(self, page, url):
        """Navigate to the origin of *url* to avoid CORS issues.

        If the server redirects HTTP to HTTPS, upgrade the fetch URL
        to HTTPS as well (browsers block mixed-content fetches).

        Returns the possibly-upgraded fetch URL.
        """
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        origin_url = f"{parsed.scheme}://{parsed.netloc}/"
        try:
            page.goto(origin_url, wait_until="domcontentloaded", timeout=10000)
            actual = urlparse(page.url)
            if actual.scheme != parsed.scheme:
                # Server redirected to different scheme (e.g. http→https)
                upgraded = parsed._replace(scheme=actual.scheme)
                return urlunparse(upgraded)
        except Exception:
            page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
        return url

    def _new_page(self):
        self._ensure_started()
        ctx = self._browser.new_context(
            user_agent=self._user_agent,
            viewport={"width": 1920, "height": 1080},
            bypass_csp=True,
        )
        page = ctx.new_page()
        page.set_default_timeout(self._timeout * 1000)
        return ctx, page

    # -- HTTP methods -------------------------------------------------------

    def get(self, url, headers=None, timeout=None, **kwargs):
        """GET a URL and return the rendered HTML.

        Navigates the browser to *url*, waits for the page to settle,
        then returns the DOM text content.
        """
        ctx, page = self._new_page()
        t = timeout or self._timeout
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=t * 1000)
            # Allow JS to execute
            page.wait_for_timeout(3000)
            body = page.evaluate("() => document.documentElement.outerHTML")
            return _PlaywrightResponse(body, status_code=200)
        except Exception as e:
            logging.error("Playwright get(%s) failed: %s", url, e)
            # Try to return whatever we have
            try:
                body = page.evaluate("() => document.documentElement.outerHTML")
                return _PlaywrightResponse(body, status_code=200)
            except Exception:
                return _PlaywrightResponse("", status_code=500)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    def fetch_get(self, url, headers=None, timeout=None):
        """GET using the browser's ``fetch()`` API — returns raw response text.

        Use this for JSON API endpoints where you need the raw response body,
        not a rendered HTML page.
        """
        ctx, page = self._new_page()
        merged_headers = dict(headers or {})

        try:
            fetch_url = self._navigate_to_origin(page, url)
            js_code = f"""
            async () => {{
                try {{
                    const resp = await fetch({json.dumps(fetch_url)}, {{
                        method: 'GET',
                        headers: {json.dumps(merged_headers)},
                    }});
                    const text = await resp.text();
                    return JSON.stringify({{
                        status: resp.status,
                        body: text,
                    }});
                }} catch (e) {{
                    return JSON.stringify({{ status: 0, body: e.message }});
                }}
            }}
            """
            result = page.evaluate(js_code)
            parsed = json.loads(result)
            return _PlaywrightResponse(
                parsed.get("body", ""),
                status_code=parsed.get("status", 500),
            )
        except Exception as e:
            logging.error("Playwright fetch_get(%s) failed: %s", url, e)
            return _PlaywrightResponse("", status_code=500)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    def post(self, url, data=None, json_body=None, headers=None, timeout=None, **kwargs):
        """POST to *url* using the browser's ``fetch()`` API.

        Parameters
        ----------
        data : dict | None
            Form-encoded body (``application/x-www-form-urlencoded``).
        json_body : dict | None
            JSON body (``application/json``).  Takes precedence over *data*.
        headers : dict | None
            Extra request headers.
        timeout : int | None
            Seconds before giving up.

        Returns
        -------
        _PlaywrightResponse
        """
        ctx, page = self._new_page()
        t = (timeout or self._timeout) * 1000

        # Build fetch options for the in-browser request
        fetch_kwargs = {}
        if json_body is not None:
            fetch_kwargs["body"] = json.dumps(json_body, ensure_ascii=False)
            fetch_kwargs["contentType"] = "application/json"
        elif data is not None:
            if isinstance(data, dict):
                from urllib.parse import urlencode
                fetch_kwargs["body"] = urlencode(data)
            else:
                fetch_kwargs["body"] = str(data)
            fetch_kwargs["contentType"] = "application/x-www-form-urlencoded"

        # Merge Content-Type: user-provided headers take precedence
        merged_headers = dict(headers or {})
        if "Content-Type" not in merged_headers and "contentType" in fetch_kwargs:
            merged_headers["Content-Type"] = fetch_kwargs.pop("contentType")
        elif "contentType" in fetch_kwargs:
            fetch_kwargs.pop("contentType")

        body_str = fetch_kwargs.get('body', '')

        try:
            # Navigate to target origin first, get possibly-upgraded URL
            fetch_url = self._navigate_to_origin(page, url)
            js_code = f"""
            async () => {{
                try {{
                    const resp = await fetch({json.dumps(fetch_url)}, {{
                        method: 'POST',
                        headers: {json.dumps(merged_headers)},
                        body: {json.dumps(body_str)},
                    }});
                    const text = await resp.text();
                    return JSON.stringify({{
                        status: resp.status,
                        body: text,
                    }});
                }} catch (e) {{
                    return JSON.stringify({{ status: 0, body: e.message }});
                }}
            }}
            """
            result = page.evaluate(js_code)
            parsed = json.loads(result)
            return _PlaywrightResponse(
                parsed.get("body", ""),
                status_code=parsed.get("status", 500),
            )
        except Exception as e:
            logging.error("Playwright post(%s) failed: %s", url, e)
            return _PlaywrightResponse("", status_code=500)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    def post_with_page(self, url, data=None, json_body=None, headers=None,
                       timeout=None, referer=None):
        """POST via a page navigation (form-submit style), returns rendered HTML.

        Use this when the POST endpoint returns an HTML page rather than JSON.
        """
        ctx, page = self._new_page()
        t = (timeout or self._timeout) * 1000

        try:
            if json_body is not None:
                payload = json.dumps(json_body, ensure_ascii=False)
                ct = "application/json"
            elif isinstance(data, str):
                payload = data
                ct = "application/x-www-form-urlencoded"
            elif isinstance(data, dict):
                from urllib.parse import urlencode
                payload = urlencode(data)
                ct = "application/x-www-form-urlencoded"
            else:
                payload = str(data or "")
                ct = "application/x-www-form-urlencoded"

            merged_headers = dict(headers or {})
            merged_headers["Content-Type"] = ct

            # Navigate to target origin first to avoid CORS blocking
            if referer:
                page.goto(referer, wait_until="domcontentloaded", timeout=t)
                page.wait_for_timeout(1000)
                fetch_url = url
            else:
                fetch_url = self._navigate_to_origin(page, url)

            js_code = f"""
            async () => {{
                try {{
                    const resp = await fetch({json.dumps(fetch_url)}, {{
                        method: 'POST',
                        headers: {json.dumps(merged_headers)},
                        body: {json.dumps(payload)},
                    }});
                    const text = await resp.text();
                    return JSON.stringify({{ status: resp.status, body: text }});
                }} catch (e) {{
                    return JSON.stringify({{ status: 0, body: e.message }});
                }}
            }}
            """
            result_str = page.evaluate(js_code)
            parsed = json.loads(result_str)
            return _PlaywrightResponse(
                parsed.get("body", ""),
                status_code=parsed.get("status", 500),
            )
        except Exception as e:
            logging.error("Playwright post_with_page(%s) failed: %s", url, e)
            return _PlaywrightResponse("", status_code=500)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    # -- binary download -------------------------------------------------------

    def download(self, url, timeout=None):
        """Download a binary file via the browser's ``fetch()`` API.

        Returns (bytes, content_type, filename) on success, or
        (None, None, None) on failure.  The file content is transferred
        from the browser as a base64-encoded string.
        """
        import base64 as _b64

        ctx, page = self._new_page()
        try:
            fetch_url = self._navigate_to_origin(page, url)
            js_code = """
            async () => {
                try {
                    const resp = await fetch(%s);
                    const blob = await resp.blob();
                    const reader = new FileReader();
                    return new Promise((resolve) => {
                        reader.onload = () => {
                            const base64 = reader.result.split(',')[1];
                            resolve(JSON.stringify({
                                status: resp.status,
                                data: base64,
                                type: blob.type || '',
                                disposition: resp.headers.get('content-disposition') || ''
                            }));
                        };
                        reader.readAsDataURL(blob);
                    });
                } catch (e) {
                    return JSON.stringify({ status: 0, data: '', error: e.message });
                }
            }
            """ % json.dumps(fetch_url)
            result = page.evaluate(js_code)
            parsed = json.loads(result)
            if parsed.get("status") != 200 or not parsed.get("data"):
                logging.error("download(%s) returned status %s", url, parsed.get("status"))
                return None, None, None

            file_bytes = _b64.b64decode(parsed["data"])
            content_type = parsed.get("type", "")
            disposition = parsed.get("disposition", "")
            filename = ""
            if "filename=" in disposition:
                filename = disposition.split("filename=")[-1].strip('" ')
            return file_bytes, content_type, filename
        except Exception as e:
            logging.error("Playwright download(%s) failed: %s", url, e)
            return None, None, None
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    # -- context manager ----------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


# ---------------------------------------------------------------------------
# Module-level convenience (auto-start / auto-stop singleton)
# ---------------------------------------------------------------------------
_default_client = None


def get_default_client():
    global _default_client
    if _default_client is None:
        _default_client = PlaywrightHttpClient()
        _default_client.start()
    return _default_client


def close_default_client():
    global _default_client
    if _default_client:
        _default_client.stop()
        _default_client = None
