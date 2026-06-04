"""
Playwright-based WeChat MP article content fetcher.
Adapted from we-mp-rss driver/wxarticle.py.
Uses a real browser to render article pages, bypassing anti-bot detection.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class WXArticleFetcher:
    """Fetch WeChat MP article content via Playwright browser."""

    def __init__(self, proxy_url: str = "", mobile_mode: bool = True,
                 wait_timeout: int = 30000):
        self.wait_timeout = wait_timeout
        self.proxy_url = proxy_url
        self.mobile_mode = mobile_mode

    async def get_article_content(self, url: str) -> Dict:
        """Fetch article content using Playwright browser.

        Returns dict with 'content' (cleaned HTML) and 'fetch_error' (empty if success).
        """
        info: Dict = {"content": "", "fetch_error": ""}

        try:
            from .playwright_driver import PlaywrightController

            async with PlaywrightController(
                proxy_url=self.proxy_url or None,
                mobile_mode=self.mobile_mode,
            ) as controller:
                success = await controller.open_url(url, timeout=self.wait_timeout)
                if not success:
                    info["fetch_error"] = "页面加载失败"
                    return info

                page = controller.page
                await asyncio.sleep(2)

                body_text = await page.locator("body").text_content()

                # Anti-bot / deleted / restricted checks
                if "当前环境异常，完成验证后即可继续访问" in body_text:
                    info["fetch_error"] = "当前环境异常（反爬验证）"
                    return info

                for keyword in [
                    "该内容已被发布者删除",
                    "The content has been deleted",
                    "内容审核中",
                    "该内容暂时无法查看",
                    "违规无法查看",
                    "Unable to view this content",
                    "发送失败无法查看",
                ]:
                    if keyword in body_text:
                        info["fetch_error"] = keyword
                        return info

                # Extract article body (#js_content or #js_article)
                content = await page.locator("#js_content").inner_html()
                if not content:
                    content = await page.locator("#js_article").inner_html()

                if not content:
                    info["fetch_error"] = "未找到文章正文元素 (#js_content/#js_article)"
                    return info

                # Scroll to bottom to trigger lazy-loaded images
                try:
                    await self._scroll_to_bottom(page)
                except Exception as e:
                    logger.warning("滚动加载图片失败: %s", e)

                # Re-fetch content after scroll (lazy images loaded)
                content = await page.locator("#js_content").inner_html()
                if not content:
                    content = await page.locator("#js_article").inner_html()

                info["content"] = _clean_article_html(str(content))
                logger.info("Playwright extracted content: %d chars", len(info["content"]))
                return info

        except Exception as e:
            info["fetch_error"] = str(e)
            logger.error("Playwright fetch failed: %s", e)
            return info

    @staticmethod
    async def _scroll_to_bottom(page, scroll_step: int = 500,
                                 max_scrolls: int = 50, wait_ms: int = 300) -> None:
        """Scroll page to bottom to trigger lazy-loaded images."""
        try:
            total_height = await page.evaluate("() => document.body.scrollHeight")
            current_pos = 0
            scrolls = 0

            while current_pos < total_height and scrolls < max_scrolls:
                current_pos += scroll_step
                await page.evaluate(f"() => window.scrollTo(0, {current_pos})")
                await asyncio.sleep(wait_ms / 1000)
                total_height = await page.evaluate("() => document.body.scrollHeight")
                scrolls += 1

            # Scroll to top then back to bottom for remaining images
            await page.evaluate("() => window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

            # Wait for images
            try:
                await page.evaluate(r'''
                    () => new Promise((resolve) => {
                        const imgs = document.querySelectorAll('img');
                        const total = imgs.length;
                        if (total === 0) { resolve(); return; }
                        let loaded = 0;
                        const done = () => { loaded++; if (loaded >= total) resolve(); };
                        imgs.forEach(img => {
                            if (img.complete) done();
                            else { img.onload = done; img.onerror = done; }
                        });
                        setTimeout(resolve, 8000);
                    })
                ''', timeout=10000)
            except Exception:
                pass

            logger.info("Scroll complete (%d scrolls)", scrolls)
        except Exception as e:
            logger.warning("Scroll error: %s", e)


def _clean_article_html(html_content: str) -> str:
    """Clean article HTML: remove noise tags, fix images."""
    if not html_content:
        return ""

    try:
        soup = BeautifulSoup(html_content, "html.parser")

        # Remove noise elements
        for tag_name in ["script", "style", "nav", "footer", "iframe", "noscript"]:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove specific IDs used by WeChat for interactive widgets
        remove_ids = [
            "content_bottom_interaction",
            "activity-name",
            "js_pc_weapp_code",
            "js_article_bottom_bar",
            "js_pc_qr_code",
        ]
        for rid in remove_ids:
            el = soup.find(id=rid)
            if el:
                el.decompose()

        # Fix images: handle data-src, keep only src/style
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src", "")
            if "data:image" in src:
                src = img.get("data-src", "")
            style = img.get("style", "")
            img.attrs = {}
            if src:
                img["src"] = src
            if style:
                img["style"] = style

        # Remove inline event handlers
        for attr in ["onclick", "onload", "onerror"]:
            for tag in soup.find_all(attrs={attr: True}):
                del tag[attr]

        # Remove hidden elements
        for tag in soup.find_all(attrs={"style": True}):
            style_val = tag.get("style", "")
            if "display: none" in style_val.replace(" ", "") or "visibility: hidden" in style_val.replace(" ", ""):
                tag.decompose()

        return str(soup)
    except Exception as e:
        logger.warning("HTML cleanup failed: %s", e)
        return html_content


def fetch_content_sync(url: str, proxy_url: str = "") -> str:
    """Synchronous wrapper for Playwright content extraction.

    Uses a new event loop per call (matches we-mp-rss pattern).
    Returns cleaned article HTML, or empty string on failure.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            fetcher = WXArticleFetcher(proxy_url=proxy_url)
            result = loop.run_until_complete(fetcher.get_article_content(url))
            return result.get("content", "")
        finally:
            loop.close()
    except Exception as e:
        logger.error("Playwright sync fetch failed for %s: %s", url, e)
        return ""
