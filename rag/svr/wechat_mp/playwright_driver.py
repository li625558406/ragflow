"""
Async Playwright Controller — adapted from we-mp-rss driver/playwright_driver.py.
Integrated with AntiCrawlerConfig for fingerprint protection.
"""

import asyncio
import logging
import os
import sys
import time
from typing import Dict, List, Optional

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from .anti_crawler import AntiCrawlerConfig

logger = logging.getLogger(__name__)

# Chrome discovery paths — reuse same logic as crawler_utils.py
_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    os.path.expanduser("~/AppData/Local/Google/Chrome/Application/chrome.exe"),
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


class PlaywrightController:
    """Async Playwright controller with anti-detection integration."""

    def __init__(self, headless: bool = None, browser_type: str = "chromium",
                 proxy_url: Optional[str] = None, user_agent: Optional[str] = None,
                 mobile_mode: bool = False):
        self.headless = os.environ.get("HEADLESS", "true").lower() == "true" if headless is None else headless
        self.browser_type = browser_type
        self.proxy_url = proxy_url
        self.mobile_mode = mobile_mode
        self.anti_crawler_config = AntiCrawlerConfig()
        if user_agent:
            self.user_agent = user_agent
        else:
            self.user_agent = self.anti_crawler_config._ua_generator.get_realistic_user_agent(mobile_mode)

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            browser_launcher = getattr(self._playwright, self.browser_type)

            launch_options = {"headless": self.headless}
            if self.browser_type == "chromium":
                launch_options["args"] = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ]
            if self.proxy_url:
                launch_options["proxy"] = {"server": self.proxy_url}

            self._browser = await browser_launcher.launch(**launch_options)

            anti_config = self.anti_crawler_config.get_anti_crawler_config(self.mobile_mode)
            context_options = {
                "user_agent": self.user_agent,
                "viewport": anti_config.get("viewport", {"width": 1920, "height": 1080}),
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
            }
            if "extra_http_headers" in anti_config:
                context_options["extra_http_headers"] = anti_config["extra_http_headers"]
            for key in ["java_script_enabled", "ignore_https_errors", "bypass_csp"]:
                if key in anti_config:
                    context_options[key] = anti_config[key]

            self._context = await self._browser.new_context(**context_options)
            self._page = await self._context.new_page()
            await self._apply_anti_crawler_scripts(self._page)
            logger.info("Browser started (headless=%s, browser=%s)", self.headless, self.browser_type)
        except Exception as e:
            logger.error("Failed to start browser: %s", e)
            raise

    async def _apply_anti_crawler_scripts(self, page) -> None:
        try:
            init_script = AntiCrawlerConfig.get_init_script()
            await page.add_init_script(init_script)
            logger.debug("Anti-detection scripts injected")
        except Exception as e:
            logger.warning("Anti-detection script injection failed: %s", e)

    async def open_url(self, url: str, wait_until: str = "domcontentloaded",
                       timeout: int = 30000) -> bool:
        if not self.is_page_valid():
            logger.warning("Page object invalid, restarting browser...")
            await self.start_browser()
        try:
            await self._page.goto(url, wait_until=wait_until, timeout=timeout)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.error("Failed to open URL %s: %s", url, e)
            try:
                await self.close()
            except Exception:
                pass
            return False

    async def close(self) -> None:
        try:
            if self._page:
                await self._page.close()
                self._page = None
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            logger.error("Error closing browser: %s", e)

    async def Cleanup(self) -> None:
        await self.close()

    async def Close(self) -> None:
        await self.close()

    async def __aenter__(self):
        await self.start_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @property
    def page(self):
        return self._page

    @property
    def context(self):
        return self._context

    @property
    def browser(self):
        return self._browser

    def is_browser_started(self) -> bool:
        return self._browser is not None and self._page is not None

    def is_page_valid(self) -> bool:
        if self._page is None:
            return False
        try:
            return hasattr(self._page, '_impl_obj') and self._page._impl_obj is not None
        except Exception:
            return False

    async def get_cookies(self) -> List[Dict]:
        if self._context is None:
            raise RuntimeError("Browser context not initialized")
        return await self._context.cookies()

    async def add_cookies(self, cookies: List[Dict]) -> None:
        if self._context is None:
            raise RuntimeError("Browser context not initialized")
        await self._context.add_cookies(cookies)
