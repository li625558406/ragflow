"""
Session factory for crawler HTTP clients.

Creates and manages requests.Session instances with appropriate
headers, cookies, proxies, and SSL settings per transport config.
"""

import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import TransportConfig, ProxyConfig


class SessionManager:
    """Create and configure requests.Session objects."""

    @staticmethod
    def create(config: TransportConfig) -> requests.Session:
        """Create a session from transport configuration."""
        sess = requests.Session()

        # Headers
        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
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
