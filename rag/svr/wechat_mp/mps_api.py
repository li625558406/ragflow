"""
API-mode article collector — adapted from we-mp-rss core/wx/model/api.py.
Scrapes WeChat MP articles via the official appmsg API endpoint.
"""

import json
import logging
import random
import time
from typing import Callable, Optional

import requests

from .gather import WxGather

logger = logging.getLogger(__name__)


class MpsApi(WxGather):
    """Collect WeChat MP articles via the appmsg API (requires valid token)."""

    def content_extract(self, url: str) -> str:
        try:
            return super().content_extract(url)
        except Exception as e:
            logger.error("Content extraction failed for %s: %s", url, e)
        return ""

    def get_articles(
        self,
        faker_id: str = "",
        mp_id: str = "",
        mp_title: str = "",
        callback: Optional[Callable] = None,
        start_page: int = 0,
        max_page: int = 1,
        interval: int = 10,
        gather_content: bool = True,
        item_over_callback: Optional[Callable] = None,
        over_callback: Optional[Callable] = None,
    ):
        """Fetch articles for a single MP via the appmsg API.

        Args:
            faker_id: The MP's fake ID (used in API requests).
            mp_id: Internal MP database ID.
            mp_title: Display name of the MP.
            callback: Called for each article; return True to keep it.
            start_page: Page offset (0-based).
            max_page: Max pages to fetch (each page = 5 articles).
            interval: Max random delay between pages (seconds).
            gather_content: Whether to fetch full article body HTML.
            item_over_callback: Called after all pages for this MP.
            over_callback: Called after entire collection finishes.
        """
        self.start(mp_id=mp_id)

        if self.gather_content:
            gather_content = True

        logger.info("API mode — collecting [%s] (gather_content=%s)", mp_title, gather_content)

        # Open shared Playwright browser for all article fetches
        if gather_content:
            self.open_playwright()

        url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        count = 5
        params = {
            "action": "list_ex",
            "begin": start_page * count,
            "count": count,
            "fakeid": faker_id,
            "type": "9",
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
        }

        i = start_page
        while i < max_page:
            begin = i * count
            params["begin"] = str(begin)
            logger.info("Page %d (begin=%s)", i + 1, begin)

            time.sleep(random.randint(0, interval))

            try:
                headers = self.fix_header(url)
                resp = self.session.get(url, headers=headers, params=params, verify=False)
                msg = resp.json()
                self._cookies = resp.cookies

                ret = msg.get("base_resp", {}).get("ret")

                if ret == 200013:
                    self.error(f"Frequency control at begin={begin}")
                    break
                if ret == 200003:
                    self.error(f"Invalid Session at begin={begin}", code="Invalid Session")
                    break
                if "app_msg_list" not in msg:
                    logger.info("No more articles (no app_msg_list)")
                    break
                if ret != 0:
                    err_msg = msg.get("base_resp", {}).get("err_msg", "unknown")
                    self.error(f"API error: {err_msg} (code={ret})")
                    break

                for item in msg["app_msg_list"]:
                    time.sleep(random.randint(1, 3))
                    if gather_content and not self.has_gathered(item["aid"]):
                        item["content"] = self.content_extract(item["link"])
                        # Fallback: use article digest/summary when full content extraction fails
                        if not item["content"] and item.get("digest"):
                            item["content"] = f"<p>{item['digest']}</p>"
                            logger.info("%s using digest fallback (%d chars)", item.get("title", ""), len(item["digest"]))
                        # Shorter wait when using shared Playwright browser
                        if self._pw_controller:
                            time.sleep(random.randint(1, 3))
                            logger.info("%s content done", item.get("title", ""))
                        else:
                            self.wait(min=3, max=10, tips=f"{item['title']} content done")
                    else:
                        item["content"] = ""

                    item["id"] = item["aid"]
                    item["mp_id"] = mp_id

                    if callback is not None:
                        self.fill_back(
                            callback=callback,
                            data=item,
                            ext_data={"mp_title": mp_title, "mp_id": mp_id},
                        )

                logger.info("Page %d done", i + 1)
                i += 1

            except requests.exceptions.Timeout:
                logger.warning("Request timed out at page %d", i + 1)
                break
            except requests.exceptions.RequestException as e:
                logger.error("Request error at page %d: %s", i + 1, e)
                break
            finally:
                self.item_over(
                    item={"mp_id": mp_id, "mps_title": mp_title},
                    callback=item_over_callback,
                )

        self.over(callback=over_callback)

        # Close shared Playwright browser
        if gather_content:
            self.close_playwright()
