"""
Playwright browser pool for SPA crawlers.

Reuses Chromium instances across crawl cycles to avoid cold-start
overhead.  Each subprocess crawler gets a singleton browser via
this module.
"""

import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Optional

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    os.path.expanduser("~/AppData/Local/Google/Chrome/Application/chrome.exe"),
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


class BrowserPool:
    """Singleton browser pool for Playwright-based crawling.

    Usage::

        pool = BrowserPool()
        page = await pool.get_page()
        # ... use page ...
        pool.release_page(page)
        pool.cleanup()
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._available = True

    def _find_chrome(self) -> Optional[str]:
        for p in _CHROME_PATHS:
            if os.path.exists(p):
                return p
        return None

    def start(self) -> None:
        """Launch the browser instance."""
        if self._browser is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright is required for SPA crawling. "
                "Install with: pip install playwright && python -m playwright install chromium"
            )

        # Fix: sync_playwright conflicts with a running asyncio event loop.
        # Playwright checks loop.is_running() and raises before calling run_until_complete().
        # nest_asyncio alone isn't enough — we must also temporarily patch is_running().
        _loop = None
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if _loop is not None and _loop.is_running():
            try:
                import nest_asyncio
                nest_asyncio.apply()
            except ImportError:
                logging.warning("BrowserPool: asyncio loop detected but nest_asyncio "
                                "not installed. Run: pip install nest_asyncio")

            # Temporarily make is_running() return False so Playwright's check passes.
            _original_is_running = _loop.is_running
            _loop.is_running = lambda: False

            try:
                self._playwright = sync_playwright().start()
            finally:
                _loop.is_running = _original_is_running
        else:
            self._playwright = sync_playwright().start()
        chrome_path = self._find_chrome()

        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
        }
        if chrome_path:
            launch_args["executable_path"] = chrome_path

        self._browser = self._playwright.chromium.launch(**launch_args)
        self._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        logging.info("BrowserPool: started Chromium at %s", chrome_path or "default")

    def get_page(self):
        """Get a new page from the browser context."""
        if self._browser is None:
            self.start()
        return self._context.new_page()

    def release_page(self, page) -> None:
        """Close a page and return it to the pool."""
        try:
            page.close()
        except Exception:
            pass

    def close_context(self) -> None:
        """Close the browser context (clears cookies/storage)."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

    def cleanup(self) -> None:
        """Fully shut down the browser and playwright."""
        self.close_context()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logging.info("BrowserPool: cleaned up")


# Module-level singleton
_browser_pool: Optional[BrowserPool] = None


def get_browser_pool() -> BrowserPool:
    global _browser_pool
    if _browser_pool is None:
        _browser_pool = BrowserPool()
    return _browser_pool


def cleanup_browser_pool() -> None:
    global _browser_pool
    if _browser_pool is not None:
        _browser_pool.cleanup()
        _browser_pool = None
