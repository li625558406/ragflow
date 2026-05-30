"""
WxGather base class — adapted from we-mp-rss core/wx/base.py.
Article collection framework with token management, proxy support, and dedup.
"""

import json
import logging
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from . import token as token_store

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4; rv:109.0) Gecko/20100101 Firefox/114.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.67",
    "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
]


class WxGather:
    """Base class for WeChat MP article collection.

    Provides: token loading, HTTP session management, proxy support,
    content extraction, dedup, and callback-based article processing.
    """

    def __init__(self, tenant_id: str = "", gather_content: bool = False,
                 proxy_enabled: bool = False, http_proxy_url: str = "",
                 deno_proxy_url: str = ""):
        self.tenant_id = tenant_id
        self.articles: List[Dict] = []
        self.aids: List[str] = []
        self.start_time: Optional[float] = None
        self._cookies: Dict = {}

        # Config
        self.gather_content = gather_content
        self.proxy_enabled = proxy_enabled
        self.http_proxy_url = http_proxy_url
        self.deno_proxy_url = deno_proxy_url

        # HTTP session
        session = requests.Session()
        session.timeout = (5, 10)
        self.session = session

        # Load credentials
        self._load_token()

    # ── Token & headers ──────────────────────────────────────

    def _load_token(self):
        """Load WeChat auth token and cookies from the credential store."""
        self.token = ""
        self.cookies_str = ""
        self.user_agent = random.choice(USER_AGENTS)
        self.headers = {"User-Agent": self.user_agent}

        if self.tenant_id:
            self.cookies_str = token_store.get("cookie", "", tenant_id=self.tenant_id)
            self.token = token_store.get("token", "", tenant_id=self.tenant_id)
            self.headers = {
                "Cookie": self.cookies_str,
                "User-Agent": self.user_agent,
            }
        logger.info("Token loaded for tenant %s: token=%s...", self.tenant_id,
                     self.token[:20] if self.token else "(empty)")

    def get_token(self):
        """Refresh token from store (call before each collect cycle)."""
        self._load_token()

    # ── Header building ──────────────────────────────────────

    def fix_header(self, url: str = "") -> Dict[str, str]:
        """Build request headers with a fresh UA and referer."""
        user_agent = random.choice(USER_AGENTS)
        headers = self.headers.copy()
        headers.update({
            "User-Agent": user_agent,
            "Referer": url or "https://mp.weixin.qq.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        return headers

    # ── Proxy support ────────────────────────────────────────

    def _get_proxies(self) -> Optional[Dict]:
        if not self.proxy_enabled or not self.http_proxy_url:
            return None
        return {"http": self.http_proxy_url, "https": self.http_proxy_url}

    def _proxy_request(self, url: str) -> str:
        """Request URL through Deno proxy or direct HTTP proxy."""
        import urllib.parse

        if self.proxy_enabled and self.deno_proxy_url:
            proxy_url = f"{self.deno_proxy_url}?url={urllib.parse.quote(url, safe='')}"
            logger.info("Using Deno proxy: %s", proxy_url)
            try:
                resp = self.session.get(proxy_url, headers=self.headers, timeout=(10, 30))
                if resp.status_code == 200:
                    return resp.text
                logger.warning("Deno proxy returned %s", resp.status_code)
            except Exception as e:
                logger.error("Deno proxy error: %s", e)

        proxies = self._get_proxies()
        try:
            resp = self.session.get(url, headers=self.headers, proxies=proxies, timeout=(10, 30))
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.error("Direct request failed: %s", e)

        return ""

    # ── Content extraction ───────────────────────────────────

    def content_extract(self, url: str) -> str:
        """Fetch and clean article HTML content."""
        text = ""
        try:
            headers = self.fix_header(url)

            if self.proxy_enabled and self.deno_proxy_url:
                text = self._proxy_request(url)
                if text:
                    text = self._clean_html(text)
                    return text

            proxies = self._get_proxies()
            resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
            if resp.status_code == 200:
                text = resp.text
                text = self._clean_html(text)
        except Exception:
            pass
        return text

    def _clean_html(self, html_content: str) -> str:
        """Remove common noise elements from article HTML."""
        if "当前环境异常，完成验证后即可继续访问" in html_content:
            logger.warning("Anti-bot verification page detected")
            return ""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            for tag_name in ['script', 'style', 'nav', 'footer', 'iframe', 'noscript']:
                for tag in soup.find_all(tag_name):
                    tag.decompose()
            for attr in ['style', 'onclick', 'onload', 'onerror']:
                for tag in soup.find_all(attrs={attr: True}):
                    del tag[attr]
            return str(soup)
        except Exception:
            return html_content

    # ── Dedup ────────────────────────────────────────────────

    def has_gathered(self, aid: str) -> bool:
        if aid in self.aids:
            return True
        self.aids.append(aid)
        return False

    def record_aid(self, aid: str):
        self.aids.append(aid)

    # ── Article callback ─────────────────────────────────────

    def fill_back(self, callback: Optional[Callable] = None, data: Optional[Dict] = None,
                  ext_data: Optional[Dict] = None):
        """Build article dict and invoke callback. If callback returns truthy, append to list."""
        if callback is None or data is None:
            return

        publish_info_raw = data.get("publish_info", {}) or {}
        if isinstance(publish_info_raw, dict):
            publish_info_str = json.dumps(publish_info_raw)
        else:
            publish_info_str = str(publish_info_raw)

        art = {
            "id": str(data.get("id", data.get("aid", ""))),
            "mp_id": data.get("mp_id", ""),
            "title": data.get("title", ""),
            "url": data.get("link", ""),
            "pic_url": data.get("cover", ""),
            "content": data.get("content", ""),
            "description": data.get("digest", ""),
            "publish_type": data.get("publish_type", 0),
            "art_type": data.get("type", 0),
            "show_type": data.get("show_type", 0) or data.get("item_show_type", 0),
            "publish_src": data.get("publish_src", 0) or publish_info_raw.get("publish_src", 0),
            "publish_status": data.get("publish_status", "200") or publish_info_raw.get("publish_status", 0),
            "publish_time": data.get("update_time", ""),
            "create_time": data.get("create_time", ""),
            "original_check_type": data.get("original_check_type", 0),
            "in_profile": data.get("in_profile", 0),
            "pre_publish_status": data.get("pre_publish_status", 0),
            "service_type": data.get("service_type", 0) or publish_info_raw.get("service_type", 0),
            "item_show_type": data.get("item_show_type", 0),
            "copyright_stat": data.get("copyright_stat", 0) or publish_info_raw.get("copyright_stat", 0),
            "has_red_packet_cover": data.get("has_red_packet_cover", 0),
            "is_deleted": data.get("is_deleted", False),
            "publish_info": publish_info_str,
        }

        if callback(art):
            art["ext"] = ext_data
            self.articles.append(art)

    # ── Search ───────────────────────────────────────────────

    def search_biz(self, keyword: str = "", limit: int = 10, offset: int = 0) -> Optional[Dict]:
        """Search WeChat Official Accounts by keyword."""
        self.get_token()
        if not self.token:
            logger.error("Cannot search — not logged in")
            return None

        url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
        params = {
            "action": "search_biz",
            "begin": offset,
            "count": limit,
            "query": keyword,
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }
        headers = self.fix_header(url)
        try:
            proxies = self._get_proxies()
            resp = requests.get(url, params=params, headers=headers, proxies=proxies)
            resp.raise_for_status()
            data = resp.json()
            if data.get("base_resp", {}).get("ret") == 200013:
                logger.error("Frequency control — stop at search '%s'", keyword)
                return None
            if data.get("base_resp", {}).get("ret") != 0:
                err_msg = data.get("base_resp", {}).get("err_msg", "unknown")
                err_code = data.get("base_resp", {}).get("ret", -1)
                logger.error("Search error: %s (code=%s)", err_msg, err_code)
                return None
            if "publish_page" in data:
                data["publish_page"] = json.loads(data["publish_page"])
            return data
        except Exception as e:
            logger.error("Search request failed: %s", e)
            raise

    # ── Lifecycle ────────────────────────────────────────────

    def start(self, mp_id: Optional[str] = None):
        """Initialize a collection cycle."""
        self.articles.clear()
        self.get_token()
        if not self.token:
            self.error("请先扫码登录公众号平台")
            return
        self.start_time = time.time()

    def item_over(self, item: Optional[Dict] = None, callback: Optional[Callable] = None):
        """Called after processing a single MP's articles."""
        logger.info("Item completed: %s", item.get("mps_title", "unknown") if item else "unknown")
        if callback:
            callback(item)
        self.wait(min=3, max=10, tips=f"{item.get('mps_title', '')} 处理完成" if item else "")

    def over(self, callback: Optional[Callable] = None):
        """Called when collection is complete for all MPs."""
        end_time = time.time()
        elapsed = end_time - (self.start_time or end_time)
        logger.info("Collection complete: %d articles in %.1fs", len(self.articles), elapsed)

        if callback:
            callback(self.articles)

    def error(self, message: str, code: Optional[str] = None):
        """Handle a non-recoverable error."""
        if code == "Invalid Session":
            logger.error("Invalid session — re-login required: %s", message)
            raise Exception(message)
        logger.error(message)

    # ── Utilities ────────────────────────────────────────────

    @staticmethod
    def wait(min: int = 10, max: int = 60, tips: str = ""):
        """Randomized delay between requests to avoid rate-limiting."""
        delay = random.randint(min, max)
        if tips:
            logger.info("%s — waiting %ds...", tips, delay)
        else:
            logger.info("Waiting %ds...", delay)
        time.sleep(delay)

    @staticmethod
    def dateformat(timestamp: Any) -> str:
        """Convert Unix timestamp to local datetime string."""
        from datetime import datetime, timezone
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            return str(timestamp)
        utc_dt = datetime.fromtimestamp(ts, timezone.utc)
        local_dt = utc_dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def model(model_type: Optional[str] = None):
        """Factory: return the appropriate collector for the given mode."""
        mode = model_type or "api"
        logger.info("Collect mode: %s", mode)
        if mode == "web":
            from .mps_web import MpsWeb
            return MpsWeb()
        else:
            from .mps_api import MpsApi
            return MpsApi()

    @property
    def all_count(self) -> int:
        return len(self.articles)
