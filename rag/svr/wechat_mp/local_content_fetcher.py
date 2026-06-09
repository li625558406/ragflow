"""
Local Content Fetcher — run on residential IP Windows machine.

Fetches WeChat MP article content using Playwright Firefox with anti-detection
scripts, then outputs a JSON file ready for server-side KB upload.

Usage:
    # Fetch content for specific article URLs (from server's JSON output)
    python local_content_fetcher.py --input urls.json --output articles_with_content.json

    # Or: fetch articles from WeChat API directly, then get content for each
    python local_content_fetcher.py --tenant-id <TID> --mp-ids <faker_ids> --output articles.json
"""

import os
import sys

# IMPORTANT: The wechat_mp/ directory contains a token.py file which shadows
# Python's stdlib `token` module. This causes a circular import when stdlib
# traceback→linecache→tokenize→token imports pick up our token.py instead.
# Remove the script's directory from sys.path BEFORE any other imports.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p) != _SCRIPT_DIR]

import argparse
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

# we-mp-rss anti-detection modules
_WE_MP_RSS = str(Path("D:/AI/we-mp-rss").resolve())
if _WE_MP_RSS not in sys.path:
    sys.path.insert(0, _WE_MP_RSS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("local_content_fetcher")


# ── Content fetcher (local Playwright Firefox) ─────────────

class LocalContentFetcher:
    """Fetches WeChat article content using local Playwright Firefox."""

    def __init__(self):
        self._browser_type = "firefox"
        self._playwright = None
        self._browser = None

    async def _lazy_init(self):
        from playwright.async_api import async_playwright
        from driver.anti_crawler_config import AntiCrawlerConfig
        from driver.user_agent import UserAgentGenerator

        if self._browser is not None:
            return

        self._playwright = await async_playwright().start()

        ua_gen = UserAgentGenerator()
        anti = AntiCrawlerConfig()
        self._ua = ua_gen.get_realistic_user_agent(mobile_mode=True)
        self._anti_cfg = anti.get_anti_crawler_config(mobile_mode=True)
        self._init_script = AntiCrawlerConfig.get_init_script()

        if self._browser_type == "chromium":
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-dev-shm-usage", "--no-sandbox"],
            )
        else:
            self._browser = await self._playwright.firefox.launch(headless=True)

        logger.info("Browser launched: %s", self._browser_type)

    async def fetch(self, url: str) -> dict:
        """Fetch one article. Returns {title, text, html, error, ...}."""
        result = {
            "url": url,
            "title": "",
            "author": "",
            "text": "",
            "html": "",
            "publish_time": 0,
            "description": "",
            "mp_name": "",
            "error": "",
        }

        context = None
        try:
            await self._lazy_init()

            context = await self._browser.new_context(
                user_agent=self._ua,
                viewport=self._anti_cfg.get("viewport", {"width": 720, "height": 1920}),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()
            if self._init_script:
                await page.add_init_script(self._init_script)

            logger.info("Opening: %s", url[:80])
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(3)

            body_text = await page.locator("body").text_content()

            # CAPTCHA check
            if body_text and "当前环境异常，完成验证后即可继续访问" in body_text:
                result["error"] = "CAPTCHA"
                return result

            # Deleted checks
            deleted_msgs = {
                "该内容已被发布者删除": "DELETED",
                "内容审核中": "DELETED_AUDIT",
                "该内容暂时无法查看": "DELETED_UNAVAILABLE",
                "违规无法查看": "DELETED_VIOLATION",
                "发送失败无法查看": "DELETED_FAILED",
            }
            for marker, tag in deleted_msgs.items():
                if (body_text or "").find(marker) >= 0:
                    result["error"] = tag
                    return result

            # Title
            result["title"] = await _safe_get(page, 'meta[property="og:title"]', "content", 3000)
            if not result["title"]:
                try:
                    result["title"] = await page.evaluate("() => document.title")
                except Exception:
                    pass

            # Author
            result["author"] = await _safe_get(page, 'meta[property="og:article:author"]', "content", 3000)

            # Description
            result["description"] = await _safe_get(page, 'meta[property="og:description"]', "content", 3000)

            # MP name
            try:
                result["mp_name"] = await page.evaluate(
                    '() => { const el = document.getElementById("js_wx_follow_nickname"); return el ? el.textContent : ""; }'
                )
            except Exception:
                pass
            if not result["mp_name"]:
                result["mp_name"] = result["author"]

            # Publish time
            try:
                pub = await page.locator("#publish_time").text_content(timeout=3000)
                if pub:
                    result["publish_time"] = _parse_pub_time(pub.strip())
            except Exception:
                pass

            # Scroll for lazy loading
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
            except Exception:
                pass

            # Extract article body
            html = ""
            for sel in ["#js_content", "#js_article", "body"]:
                try:
                    html = await page.locator(sel).inner_html(timeout=5000)
                    if html and len(html) > 200:
                        break
                except Exception:
                    continue

            if not html:
                result["error"] = "NO_CONTENT"
                return result

            result["html"] = _clean_html(html)

            # Extract plain text
            from bs4 import BeautifulSoup
            text = BeautifulSoup(result["html"], "html.parser").get_text()
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            result["text"] = text

            if not text:
                result["error"] = "EMPTY_TEXT"

            logger.info("OK: title=%s text_len=%d", result["title"][:40], len(text))

        except Exception as e:
            result["error"] = str(e)
            logger.error("Fetch error: %s", e)

        finally:
            if context is not None:
                await context.close()

        return result

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


# ── Helpers ────────────────────────────────────────────────

async def _safe_get(page, selector, attr, timeout):
    try:
        return await page.locator(selector).get_attribute(attr, timeout=timeout) or ""
    except Exception:
        return ""


def _parse_pub_time(s: str) -> int:
    from datetime import datetime
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y年%m月%d日 %H:%M"]:
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return 0


def _clean_html(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for hidden in soup.select('[style*="display:none"], [style*="display: none"], [aria-hidden="true"]'):
        hidden.decompose()
    for script in soup.find_all("script"):
        script.decompose()
    for rid in ["content_bottom_interaction", "js_article_bottom_bar",
                 "js_pc_weapp_code", "js_novel_card", "js_pc_qr_code"]:
        el = soup.find(id=rid)
        if el:
            el.decompose()
    return str(soup)


# ── Article list from WeChat API ───────────────────────────

def get_articles_from_api(tenant_id: str, faker_ids: list, max_page: int = 2,
                          interval: int = 5) -> list:
    """Get article list from WeChat appmsg API (runs fine on any IP).

    This only gets metadata (title, url, digest, etc.), no content body.
    """
    import requests
    import urllib3
    urllib3.disable_warnings()

    # Read auth from ragflow2
    ragflow_path = str(Path(__file__).resolve().parent.parent.parent.parent)
    if ragflow_path not in sys.path:
        sys.path.insert(0, ragflow_path)

    from common import settings
    settings.init_settings()
    from api.db.services.wechat_mp_service import WechatMpAuthService, WechatMpAccountService

    ok, auth = WechatMpAuthService.get_by_tenant_id(tenant_id)
    if not ok:
        logger.error("No WeChat auth for tenant %s", tenant_id)
        return []

    token = auth.get("token", "")
    cookie = auth.get("cookie", "")

    # Look up MP names
    mp_info_list = WechatMpAccountService.list_by_faker_ids(tenant_id, faker_ids)
    mp_map = {m["faker_id"]: m for m in mp_info_list}

    all_articles = []

    for fid in faker_ids:
        mp_info = mp_map.get(fid, {"mp_name": fid, "id": fid})
        logger.info("Getting article list for [%s]", mp_info.get("mp_name", fid))

        for page in range(max_page):
            params = {
                "action": "list_ex",
                "begin": page * 5,
                "count": 5,
                "fakeid": fid,
                "type": "9",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
            }
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            }
            try:
                resp = requests.get(
                    "https://mp.weixin.qq.com/cgi-bin/appmsg",
                    params=params, headers=headers, verify=False, timeout=15,
                )
                data = resp.json()
                ret = data.get("base_resp", {}).get("ret", -1)
                if ret == 200013:  # freq control
                    logger.warning("Freq control hit, stopping")
                    break
                if ret != 0:
                    logger.warning("API error: ret=%d, msg=%s", ret, data.get("base_resp", {}).get("err_msg", ""))
                    break

                app_list = data.get("app_msg_list", [])
                if not app_list:
                    break

                for item in app_list:
                    article = {
                        "id": str(item.get("aid", "")),
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "description": item.get("digest", ""),
                        "cover": item.get("cover", ""),
                        "publish_time": item.get("update_time", 0),
                        "create_time": item.get("create_time", 0),
                        "mp_id": mp_info.get("id", fid),
                        "mp_title": mp_info.get("mp_name", fid),
                        "faker_id": fid,
                        "content": "",  # to be filled by local fetch
                    }
                    all_articles.append(article)

                time.sleep(1 + __import__("random").randint(0, interval))
            except Exception as e:
                logger.error("API error for [%s]: %s", mp_info.get("mp_name", fid), e)
                break

    return all_articles


# ── Main ────────────────────────────────────────────────────

async def main_async(args):
    fetcher = LocalContentFetcher()

    # Determine input articles
    if args.input:
        logger.info("Loading article URLs from: %s", args.input)
        with open(args.input, "r", encoding="utf-8") as f:
            articles = json.load(f)
    else:
        # Fetch from WeChat API directly
        logger.info("Fetching article list from WeChat API...")
        mp_ids = [x.strip() for x in args.mp_ids.split(",") if x.strip()]
        articles = get_articles_from_api(
            tenant_id=args.tenant_id,
            faker_ids=mp_ids,
            max_page=args.max_page,
            interval=args.interval,
        )

    if not articles:
        logger.warning("No articles found!")
        await fetcher.close()
        return

    total = len(articles)
    logger.info("Found %d articles, fetching content...", total)

    try:
        for i, art in enumerate(articles):
            url = art.get("url", "")
            if not url:
                logger.warning("[%d/%d] Skipping article without URL: %s", i+1, total, art.get("title", ""))
                continue

            # Skip if already has content and not forcing
            if art.get("content") and art.get("content") != "DELETED" and not args.force:
                logger.info("[%d/%d] Already has content, skipping: %s", i+1, total, art["title"][:50])
                continue

            logger.info("[%d/%d] Fetching: %s", i+1, total, art["title"][:50])
            result = await fetcher.fetch(url)
            art["content"] = result["text"]
            art["content_html"] = result["html"]
            art["fetch_error"] = result["error"]
            if result["title"] and not art.get("title"):
                art["title"] = result["title"]
            if result["description"]:
                art["description"] = result["description"]
            if result["mp_name"]:
                art["mp_title"] = result["mp_name"]

            # Delay between articles
            delay = 2 + __import__("random").randint(0, args.interval)
            await asyncio.sleep(delay)
    finally:
        await fetcher.close()

    # Write output
    output_path = args.output or "articles_with_content.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    # Summary
    success = sum(1 for a in articles if a.get("content") and a.get("fetch_error", "") == "")
    deleted = sum(1 for a in articles if "DELETED" in (a.get("fetch_error", "") or ""))
    captcha = sum(1 for a in articles if "CAPTCHA" in (a.get("fetch_error", "") or ""))
    no_content = sum(1 for a in articles if not a.get("content"))
    logger.info("DONE. Output: %s", output_path)
    logger.info("  Success: %d, Deleted: %d, CAPTCHA: %d, NoContent: %d, Total: %d",
                success, deleted, captcha, no_content, total)


def main():
    parser = argparse.ArgumentParser(description="Local WeChat MP Content Fetcher")
    parser.add_argument("--input", help="Input JSON file with article URLs (if not using API)")
    parser.add_argument("--output", default="articles_with_content.json", help="Output JSON file")
    parser.add_argument("--tenant-id", help="RAGFlow tenant ID (for WeChat auth)")
    parser.add_argument("--mp-ids", default="", help="Comma-separated faker_ids")
    parser.add_argument("--max-page", type=int, default=2, help="Max pages per MP")
    parser.add_argument("--interval", type=int, default=8, help="Max delay between articles (seconds)")
    parser.add_argument("--force", action="store_true", help="Re-fetch articles that already have content")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
