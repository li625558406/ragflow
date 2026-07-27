"""
Session factory for crawler HTTP clients.

Creates and manages requests.Session instances with appropriate
headers, cookies, proxies, and SSL settings per transport config.
"""

import logging
import random
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import TransportConfig, ProxyConfig
from .anti_crawler import get_random_ua


class ScraplingSession:
    """Thin wrapper around scrapling.Fetcher that mimics requests.Session.

    Only supports GET requests to HTML pages — JSON API endpoints should
    continue using the default requests.Session.
    """

    def __init__(self, config: TransportConfig):
        try:
            from scrapling.fetchers import Fetcher  # noqa: F401
        except ImportError:
            raise ImportError(
                "scrapling is required when transport.engine is 'scrapling'. "
                "Install it with: pip install 'scrapling[fetchers]' && scrapling install"
            )
        self._config = config
        self._timeout = config.timeout
        # requests-compatible attributes used by adapter (cookies.clear, etc.)
        self.cookies = {}

    def get(self, url: str, **kwargs):
        """Mimic requests.Session.get() — returns a response-like object."""
        from scrapling.fetchers import Fetcher
        params = kwargs.get("params")
        # Forward configured headers (User-Agent/Referer/X-Requested-With, etc.)
        # to scrapling. Without this, scrapling defaults Referer to
        # https://www.google.com and sites with referer-based filtering reject us.
        headers = dict(self._config.headers or {})
        # Caller can still override per-call
        caller_headers = kwargs.get("headers")
        if caller_headers:
            headers.update(caller_headers)
        page = Fetcher.get(
            url,
            params=params,
            timeout=self._timeout,
            impersonate=self._config.impersonate or None,
            headers=headers or None,
        )
        return _ScraplingResponse(page)

    def post(self, url: str, **kwargs):
        """NOT supported — scrapling.Fetcher is GET-only. Raise clear error."""
        raise NotImplementedError(
            "scrapling.Fetcher does not support POST. "
            "Use transport.engine='requests' for POST APIs."
        )

    def close(self):
        """No-op — scrapling.Fetcher is stateless per request."""
        pass


class _ScraplingResponse:
    """Response-like object wrapping a scrapling Selector."""

    def __init__(self, page):
        self._page = page
        # For JSON API responses, scrapling leaves page.text empty and puts
        # raw bytes in page.body (often without charset hint, causing mojibake
        # for Chinese content). Prefer page.body for JSON endpoints so we can
        # honor Content-Type charset, then fall back to page.text for HTML.
        self.text = ""
        body = getattr(page, "body", None)
        if isinstance(body, bytes) and body:
            ctype = ""
            try:
                ctype = (getattr(page, "headers", {}) or {}).get("Content-Type", "")
            except Exception:
                pass
            if "charset=" in ctype.lower():
                # e.g. "application/json;charset=utf-8"
                enc = ctype.lower().split("charset=", 1)[1].split(";")[0].strip()
                try:
                    self.text = body.decode(enc)
                except (LookupError, UnicodeDecodeError):
                    self.text = body.decode("utf-8", errors="replace")
            else:
                # Try utf-8 first (correct for nearly all CN gov sites), fall back gracefully
                try:
                    self.text = body.decode("utf-8")
                except UnicodeDecodeError:
                    self.text = body.decode("gbk", errors="replace")
        if not self.text:
            self.text = getattr(page, "text", "") or ""
        self.status_code = getattr(page, "status", 200)
        self.headers = getattr(page, "headers", {}) or {}

    def json(self):
        """Attempt JSON decode of page text."""
        import json
        return json.loads(self.text)


class SessionManager:
    """Create and configure requests.Session objects."""

    @staticmethod
    def create(config: TransportConfig) -> requests.Session:
        """Create a session from transport configuration."""
        sess = requests.Session()

        # Headers — random UA per session unless YAML overrides it
        default_headers = {
            "User-Agent": get_random_ua(),
            "Accept": "application/json, text/html, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        default_headers.update(config.headers)
        sess.headers.update(default_headers)

        # Cookies
        if config.cookies:
            for k, v in config.cookies.items():
                sess.cookies.set(k, v)

        # SSL
        sess.verify = config.verify_ssl

        # Force UTF-8 encoding for Chinese government sites.
        # Without this, requests defaults to ISO-8859-1 when the server
        # doesn't specify a charset, garbling all Chinese text.
        def _force_utf8(resp, *args, **kwargs):
            ct = resp.headers.get("Content-Type", "")
            if "charset=" not in ct.lower():
                resp.encoding = "utf-8"
        sess.hooks["response"].append(_force_utf8)

        # Proxy
        if config.proxy and config.proxy.url:
            SessionManager._configure_proxy(sess, config.proxy)

        # Session-init URL (e.g. to obtain JSESSIONID)
        if config.session_init_url:
            try:
                sess.get(config.session_init_url, timeout=config.timeout)
            except Exception as e:
                logging.warning("Session init request to %s failed: %s",
                                config.session_init_url, e)

        return sess

    @staticmethod
    def create_scrapling(config: TransportConfig) -> "ScraplingSession":
        """Create a scrapling-based (curl_cffi) session that bypasses TLS fingerprint checks.

        Use this for any site whose WAF blocks the default urllib3/requests
        TLS fingerprint — applies to BOTH HTML pages and JSON APIs (e.g.
        zycg.gov.cn family). `_ScraplingResponse` handles JSON bodies with
        charset-aware decoding, so JSON APIs work transparently.

        Limitations vs `create()`: scrapling.Fetcher is GET-only and stateless
        per request (cookies don't persist between calls). For POST APIs or
        sites requiring sticky cookies, use `create()` with `engine='requests'`.
        """
        return ScraplingSession(config)

    @staticmethod
    def reset(sess: requests.Session, config: TransportConfig) -> None:
        """Clear cookies and re-initialize the session."""
        sess.cookies.clear()
        if config.session_init_url:
            try:
                sess.get(config.session_init_url, timeout=config.timeout)
            except Exception as e:
                logging.warning("Session reset request to %s failed: %s",
                                config.session_init_url, e)

    @staticmethod
    def _configure_proxy(sess: requests.Session, proxy: ProxyConfig) -> None:
        proxy_url = proxy.url
        if proxy.username:
            # Inject credentials: socks5://user:pass@host:port
            from urllib.parse import urlparse, urlunparse
            p = urlparse(proxy_url)
            auth = f"{proxy.username}:{proxy.password}"
            netloc = f"{auth}@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            proxy_url = urlunparse((p.scheme, netloc, p.path or "", "", "", ""))
        sess.proxies.update({"http": proxy_url, "https": proxy_url})
