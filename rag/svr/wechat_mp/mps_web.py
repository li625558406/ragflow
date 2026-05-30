"""
Web-mode article collector — adapted from we-mp-rss core/wx/model/web.py.
Scrapes WeChat MP articles via the published-articles (appmsgpublish) endpoint.
This is a fallback when the API mode is unavailable.
"""

import json
import logging
import random
import time
from typing import Callable, Optional

import requests

from .gather import WxGather

logger = logging.getLogger(__name__)


class MpsWeb(WxGather):
    """Collect WeChat MP articles via the appmsgpublish (published) endpoint."""

    def content_extract(self, url: str) -> str:
        try:
            # Use requests-based extraction (same as parent)
            text = super().content_extract(url)
            return text
        except Exception as e:
            logger.error("Web content extraction failed for %s: %s", url, e)
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
        gather_content: bool = False,
        item_over_callback: Optional[Callable] = None,
        over_callback: Optional[Callable] = None,
    ):
        """Fetch published articles for a single MP.

        Args:
            faker_id: The MP's fake ID.
            mp_id: Internal MP database ID.
            mp_title: Display name of the MP.
            callback: Called for each article; return True to keep it.
            start_page: Page offset (0-based).
            max_page: Max pages to fetch.
            interval: Max random delay between pages (seconds).
            gather_content: Whether to fetch full article body HTML.
            item_over_callback: Called after all pages for this MP.
            over_callback: Called after entire collection finishes.
        """
        self.start(mp_id=mp_id)

        if self.gather_content:
            gather_content = True

        logger.info("Web mode — collecting [%s] (gather_content=%s)", mp_title, gather_content)

        url = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
        count = 5
        params = {
            "sub": "list",
            "sub_action": "list_ex",
            "begin": start_page * count,
            "count": count,
            "fakeid": faker_id,
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
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
                if ret == 200002:
                    self.error(f"Invalid arguments at begin={begin}", code="Invalid Arguments")
                    self.item_over(
                        item={"mp_id": mp_id, "mps_title": mp_title},
                        callback=item_over_callback,
                    )
                    self.over(callback=over_callback)
                    return
                if "publish_page" not in msg:
                    logger.info("No more published articles")
                    break
                if ret != 0:
                    err_msg = msg.get("base_resp", {}).get("err_msg", "unknown")
                    self.error(f"API error: {err_msg} (code={ret})")
                    break

                publish_page = json.loads(msg["publish_page"])
                for publish_item in publish_page.get("publish_list", []):
                    if "publish_info" not in publish_item:
                        continue
                    publish_info = json.loads(publish_item["publish_info"])

                    if "appmsgex" not in publish_info:
                        continue

                    for item in publish_info["appmsgex"]:
                        if gather_content and not self.has_gathered(item["aid"]):
                            item["content"] = self.content_extract(item["link"])
                            self.wait(min=3, max=10, tips=f"{item['title']} content done")
                        else:
                            item["content"] = ""

                        item["publish_info"] = publish_info.get("publish_info", "")
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
