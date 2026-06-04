"""
WeChat MP login via Playwright — adapted from we-mp-rss driver/wx.py.
Handles QR-code login, token-based auth, and session extraction.
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .playwright_driver import PlaywrightController
from .cookies import expire
from . import token as token_store

logger = logging.getLogger(__name__)

WX_LOGIN = "https://mp.weixin.qq.com/"
WX_HOME = "https://mp.weixin.qq.com/cgi-bin/home"
QRCODE_PATH = "static/wx_qrcode.png"


class WxLogin:
    """Async WeChat MP login manager — QR scan + token auth."""

    def __init__(self, tenant_id: str = ""):
        self.tenant_id = tenant_id
        self._has_login = False
        self._has_code = False
        self._session: Optional[Dict] = None
        self._ext_data: Optional[Dict] = None
        self._callback: Optional[Callable] = None
        self.controller = PlaywrightController()

    # ── QR code ──────────────────────────────────────────────

    @property
    def qrcode_path(self) -> str:
        return QRCODE_PATH

    def has_login(self) -> bool:
        return self._has_login

    def has_code(self) -> bool:
        return os.path.exists(QRCODE_PATH)

    def qr_status(self) -> Dict:
        return {"login_status": self.has_login(), "qr_code": self.has_code()}

    def qrcode_response(self) -> Dict:
        return {
            "code": f"/{QRCODE_PATH}?t={int(time.time())}",
            "is_exists": self.has_code(),
        }

    # ── Public API ───────────────────────────────────────────

    async def login_via_qrcode(self, callback: Optional[Callable] = None) -> Optional[Dict]:
        """Open mp.weixin.qq.com, capture QR code, wait for scan, extract session."""
        self._has_login = False
        self._session = None

        self._cleanup_resources()
        self.controller = PlaywrightController()
        driver = self.controller

        try:
            logger.info("Starting browser for QR login...")
            await driver.start_browser()
            await driver.open_url(WX_LOGIN)
            page = driver.page
            await page.wait_for_load_state("networkidle")

            # Wait for Vue to fetch the QR code image (src is populated by Vue
            # after an API call). The QR img is initially hidden via visibility:hidden,
            # so we must NOT use ElementHandle.screenshot() — Playwright 1.20+
            # scrolls into view and waits for visibility, which times out.
            # Use page.screenshot(clip=bbox) instead, which captures the region
            # regardless of CSS visibility.
            qr_sel = ".login__type__container__scan__qrcode"
            qrcode = await page.query_selector(qr_sel)
            if not qrcode:
                raise RuntimeError("QR code element not found on login page")

            # Wait for Vue to populate the QR src (poll up to 15s)
            code_src = None
            for _ in range(30):
                code_src = await qrcode.get_attribute("src")
                if code_src:
                    break
                await asyncio.sleep(0.5)

            logger.info("QR code src: %s", code_src)

            os.makedirs(os.path.dirname(QRCODE_PATH), exist_ok=True)

            # Capture QR via page-level clip to bypass visibility check
            bbox = await qrcode.bounding_box()
            if bbox:
                clip = {
                    "x": bbox["x"],
                    "y": bbox["y"],
                    "width": bbox["width"],
                    "height": bbox["height"],
                }
                await page.screenshot(path=QRCODE_PATH, clip=clip)
            else:
                await qrcode.screenshot(path=QRCODE_PATH)

            if os.path.getsize(QRCODE_PATH) <= 364:
                raise RuntimeError("QR code image not loaded — page may have changed. "
                                   f"src={code_src or 'None'}")

            self._has_code = True
            logger.info("QR code saved, waiting for scan (5 min timeout)...")

            # Wait for redirect to home after scan
            try:
                await page.wait_for_event("framenavigated", timeout=5 * 60 * 1000)
            except Exception:
                logger.warning("QR scan timed out (5 min)")
                return None

            self._has_login = True
            self._callback = callback
            session = await self._call_success()
            return session

        except Exception as e:
            logger.error("QR login failed: %s", e)
            return None
        finally:
            await self.close()

    async def login_via_token(self, callback: Optional[Callable] = None) -> Optional[Dict]:
        """Login using a stored token from DB (for the current tenant)."""
        if not self.tenant_id:
            logger.error("tenant_id is required for token login")
            return None

        saved_token = token_store.get("token", "", tenant_id=self.tenant_id)
        if not saved_token:
            logger.warning("No saved token for tenant %s", self.tenant_id)
            return None

        self._callback = callback

        try:
            if not self.controller.is_browser_started() or not self.controller.is_page_valid():
                logger.info("Starting browser for token login...")
                await self.controller.start_browser()

                cookies_str = token_store.get("cookie", "", tenant_id=self.tenant_id)
                if cookies_str:
                    cookies = self._parse_cookie_string(cookies_str)
                    if cookies:
                        await self.controller.add_cookies(cookies)

            await self.controller.open_url(
                f"{WX_HOME}?t=home/index&lang=zh_CN&token={saved_token}"
            )
            page = self.controller.page
            if page is None:
                logger.error("Page object is None")
                return None

            # Click through any redirect interstitial
            try:
                qrcode_link = page.locator("#jumpUrl")
                await qrcode_link.wait_for(state="visible", timeout=5000)
                await qrcode_link.click()
                await asyncio.sleep(2)
            except Exception:
                pass

            # Check if we need to re-login
            has_login_prompt = page.locator("body:has-text('使用账号登录')")
            if await has_login_prompt.count() > 0:
                self._has_login = False
                logger.warning("Token expired — re-login required for tenant %s", self.tenant_id)
                return None

            return await self._call_success()

        except Exception as e:
            logger.error("Token login failed: %s", e)
            return None
        finally:
            await self.close()

    async def close(self) -> None:
        try:
            if self.controller:
                await self.controller.close()
        except Exception as e:
            logger.warning("Error closing browser: %s", e)

    async def cleanup(self) -> None:
        await self.close()

    # ── Internal ─────────────────────────────────────────────

    async def _call_success(self) -> Optional[Dict]:
        """Extract token + cookies from the current page, format and persist."""
        controller = self.controller
        if controller is None:
            logger.error("Browser controller not initialized")
            return None

        page = controller.page
        current_url = page.url if page else ""

        # Wait for the final home page URL (not intermediate redirects)
        # WeChat login may go through redirects before reaching cgi-bin/home
        if "cgi-bin/home" not in current_url and page:
            try:
                await page.wait_for_url("**/cgi-bin/home**", timeout=15000)
                current_url = page.url
                logger.info("Page navigated to home: %s", current_url[:100])
            except Exception:
                logger.warning("Wait for home URL timed out, current URL: %s", current_url[:100])

        # Also wait for networkidle to ensure all cookies are set
        if page:
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        token_val = await self._extract_token_from_page()
        cookies = await controller.get_cookies()

        # Diagnostic logging
        cookie_names = [c.get("name", "") for c in cookies if c.get("domain", "").endswith("qq.com")]
        mp_cookies = [c.get("name", "") for c in cookies if "mp.weixin" in c.get("domain", "")]
        logger.info("Login extraction: url=%s token=%s token_len=%d total_cookies=%d mp_cookies=%s all_qq_cookie_names=%s",
                    (current_url or "")[:80], (token_val or "")[:15],
                    len(token_val or ""), len(cookies), mp_cookies,
                    [n for n in cookie_names if n in ("slave_sid", "slave_user", "bizuin", "token", "wxuin")])

        self._session = self._format_token(cookies, token_val or "")

        if self._session and self._session.get("expiry"):
            self._has_login = True
            try:
                self._ext_data = await self._extract_wechat_data()
            except Exception as e:
                logger.warning("Could not extract WeChat account data: %s", e)
                self._ext_data = None

            # Persist to DB
            if self._session.get("token") and self.tenant_id:
                token_store.set_token(self._session, tenant_id=self.tenant_id, ext_data=self._ext_data)
                logger.info("Token saved for tenant %s", self.tenant_id)
        else:
            self._has_login = False
            logger.warning("Login did not succeed — no valid session")

        if self._callback:
            self._callback(self._session, self._ext_data)

        return self._session

    async def _extract_token_from_page(self) -> Optional[str]:
        """Extract token from URL, localStorage, sessionStorage, or cookies."""
        controller = self.controller
        if not controller or not controller.page:
            return None

        page = controller.page

        # 1. URL query param
        current_url = page.url
        token_match = re.search(r'token=([^&]+)', current_url)
        if token_match:
            return token_match.group(1)

        # 2. localStorage
        try:
            token_val = await page.evaluate("() => localStorage.getItem('token')")
            if token_val:
                return token_val
        except Exception:
            pass

        # 3. sessionStorage
        try:
            token_val = await page.evaluate("() => sessionStorage.getItem('token')")
            if token_val:
                return token_val
        except Exception:
            pass

        # 4. cookie named 'token'
        try:
            cookies = await page.context.cookies()
            for c in cookies:
                if 'token' in c.get('name', '').lower():
                    return c['value']
        except Exception:
            pass

        return ""

    def _format_token(self, cookies: List[Dict], token_val: str = "") -> Dict:
        """Build the standard session dict from cookies + token."""
        cookies_str = ""
        for c in cookies:
            cookies_str += f"{c['name']}={c['value']}; "
            if not token_val and 'token' in c.get('name', '').lower():
                token_val = c['value']

        cookie_expiry = expire(cookies)
        return {
            "cookies": cookies,
            "cookies_str": cookies_str.strip(),
            "token": token_val,
            "wx_login_url": QRCODE_PATH,
            "expiry": cookie_expiry,
        }

    async def _extract_wechat_data(self) -> Dict:
        """Extract WeChat account info from the home page."""
        controller = self.controller
        if not controller or not controller.page:
            return {}

        page = controller.page
        data: Dict[str, str] = {}

        selectors = {
            "wx_app_name": [
                ".weui-desktop_name",
                ".acount_box-nickname",
                ".account_box-panel-head__nickname",
            ],
            "wx_logo": [
                ".weui-desktop-account__img",
                ".weui-desktop-account__thumb",
                ".account_box-panel-head__thumb",
            ],
        }

        for key, selector_list in selectors.items():
            data[key] = ""
            for sel in selector_list:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.wait_for(state="visible", timeout=2000)
                        if key == "wx_logo":
                            data[key] = await el.get_attribute("src") or ""
                        else:
                            data[key] = (await el.text_content()) or ""
                        break
                except Exception:
                    continue

        return data

    @staticmethod
    def _parse_cookie_string(cookies_str: str) -> List[Dict]:
        """Parse 'name=value; name2=value2' into a list of cookie dicts."""
        cookies = []
        for part in cookies_str.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": "mp.weixin.qq.com",
                    "path": "/",
                })
        return cookies

    def _cleanup_resources(self) -> None:
        try:
            if os.path.exists(QRCODE_PATH):
                os.remove(QRCODE_PATH)
        except Exception:
            pass
        self._has_login = False
        self._has_code = False


# ── Module-level convenience ─────────────────────────────────

def create_login_manager(tenant_id: str = "") -> WxLogin:
    return WxLogin(tenant_id=tenant_id)
