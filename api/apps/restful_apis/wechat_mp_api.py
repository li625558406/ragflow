"""
WeChat MP REST API — QR login, account search, subscription management.
Auto-registered at /api/v1/wechat-mp/* via ragflow2's blueprint scanner.
"""

import asyncio
import base64
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from quart import request

from api.apps import current_user, login_required
from api.db.services.wechat_mp_service import WechatMpAccountService, WechatMpAuthService
from api.utils.api_utils import (
    get_data_error_result,
    get_error_argument_result,
    get_json_result,
    get_request_json,
    server_error_response,
)

logger = logging.getLogger(__name__)

# ── In-memory login session tracker ─────────────────────────

# Maps tenant_id -> asyncio.Task (the background login task)
_login_sessions: dict = {}


def _get_tenant_id() -> str:
    return getattr(current_user, "id", "") or ""


def _get_project_base() -> str:
    return str(Path(__file__).resolve().parent.parent.parent.parent)


# ── QR code login helper ────────────────────────────────────

async def _run_qr_login(tenant_id: str):
    """Background coroutine: open browser, capture QR, wait for scan, persist token."""
    try:
        # Ensure project root is on path so wechat_mp imports work
        project_base = _get_project_base()
        if project_base not in sys.path:
            sys.path.insert(0, project_base)

        from rag.svr.wechat_mp.wx_login import WxLogin

        wx = WxLogin(tenant_id=tenant_id)
        session = await wx.login_via_qrcode()

        if session and session.get("token"):
            logger.info("QR login succeeded for tenant %s", tenant_id)
        else:
            logger.warning("QR login failed or timed out for tenant %s", tenant_id)
    except Exception as e:
        logger.exception("QR login error for tenant %s: %s", tenant_id, e)


def _ensure_qrcode_dir():
    os.makedirs("static", exist_ok=True)


# ── Endpoints ───────────────────────────────────────────────

@manager.route("/wechat-mp/auth/qrcode", methods=["GET"])  # noqa: F821
@login_required
async def get_auth_qrcode():
    """Start WeChat QR login flow and return the QR code image (base64)."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return get_error_argument_result("Missing tenant_id")

    # Cancel any existing login for this tenant
    existing = _login_sessions.get(tenant_id)
    if existing and not existing.done():
        existing.cancel()

    project_base = _get_project_base()
    if project_base not in sys.path:
        sys.path.insert(0, project_base)

    try:
        from rag.svr.wechat_mp.wx_login import WxLogin, QRCODE_PATH

        wx = WxLogin(tenant_id=tenant_id)
        driver = wx.controller
        await driver.start_browser()
        await driver.open_url("https://mp.weixin.qq.com/")
        page = driver.page
        await page.wait_for_load_state("networkidle")

        # Match original we-mp-rss wx.py: use query_selector (ElementHandle)
        # which has NO visibility check and NO auto-waiting. The QR image is
        # rendered in the initial HTML (server-side), Vue only controls its
        # visibility — but screenshot() works on hidden elements too.
        qr_sel = ".login__type__container__scan__qrcode"
        qrcode_el = await page.query_selector(qr_sel)
        if not qrcode_el:
            await driver.close()
            return get_data_error_result(message="QR code element not found on page")

        code_src = await qrcode_el.get_attribute("src")
        logger.info("QR code src: %s", code_src)

        _ensure_qrcode_dir()
        await qrcode_el.screenshot(path=QRCODE_PATH)

        # Read QR image as base64
        qrcode_base64 = ""
        if os.path.exists(QRCODE_PATH) and os.path.getsize(QRCODE_PATH) > 364:
            with open(QRCODE_PATH, "rb") as f:
                qrcode_base64 = base64.b64encode(f.read()).decode("utf-8")

        # Store the WxLogin instance for status polling
        _login_sessions[tenant_id] = {
            "wx": wx,
            "started_at": time.time(),
            "status": "pending",
        }

        # Start background wait for scan
        async def _wait_scan():
            try:
                page = wx.controller.page
                await page.wait_for_event("framenavigated", timeout=5 * 60 * 1000)
                session_data = await wx._call_success()
                _login_sessions[tenant_id]["status"] = "done"
                _login_sessions[tenant_id]["session"] = session_data
                await wx.close()
            except Exception as e:
                logger.warning("QR scan wait error: %s", e)
                _login_sessions[tenant_id]["status"] = "timeout"
                try:
                    await wx.close()
                except Exception:
                    pass

        asyncio.create_task(_wait_scan())

        return get_json_result(data={
            "qrcode_base64": f"data:image/png;base64,{qrcode_base64}",
            "qrcode_url": f"/{QRCODE_PATH}?t={int(time.time())}",
            "status": "pending",
        })

    except Exception as e:
        logger.exception("Failed to get QR code")
        return server_error_response(e)


@manager.route("/wechat-mp/auth/status", methods=["GET"])  # noqa: F821
@login_required
async def get_auth_status():
    """Check WeChat login status for the current tenant."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return get_error_argument_result("Missing tenant_id")

    session_info = _login_sessions.get(tenant_id)

    # First check in-memory session
    if session_info and session_info.get("status") == "done":
        sess = session_info.get("session", {})
        ext_data = sess.get("ext_data") if isinstance(sess, dict) else None
        mp_name = ""
        if ext_data and isinstance(ext_data, dict):
            mp_name = ext_data.get("wx_app_name", "")
        return get_json_result(data={
            "login_status": True,
            "mp_name": mp_name,
            "token": sess.get("token", "")[:20] + "..." if isinstance(sess, dict) and sess.get("token") else "",
        })

    if session_info and session_info.get("status") == "timeout":
        return get_json_result(data={"login_status": False, "reason": "timeout"})

    # Check DB for existing token
    ok, auth = WechatMpAuthService.get_by_tenant_id(tenant_id)
    if ok and auth and auth.get("token"):
        ext_data = {}
        try:
            import json
            ext_data = json.loads(auth.get("ext_data", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        return get_json_result(data={
            "login_status": True,
            "mp_name": ext_data.get("wx_app_name", ""),
            "token": (auth.get("token", "") or "")[:20] + "...",
        })

    in_progress = session_info and session_info.get("status") == "pending"
    return get_json_result(data={
        "login_status": False,
        "pending": in_progress,
    })


@manager.route("/wechat-mp/auth", methods=["DELETE"])  # noqa: F821
@login_required
async def logout_wechat():
    """Logout: clear stored WeChat credentials for current tenant."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return get_error_argument_result("Missing tenant_id")

    # Cancel any active login session
    session_info = _login_sessions.pop(tenant_id, None)
    if session_info:
        wx = session_info.get("wx") if isinstance(session_info, dict) else None
        if wx:
            try:
                await wx.close()
            except Exception:
                pass

    WechatMpAuthService.delete_by_tenant_id(tenant_id)
    return get_json_result(data={"message": "Logged out"})


@manager.route("/wechat-mp/search", methods=["GET"])  # noqa: F821
@login_required
async def search_mp():
    """Search WeChat Official Accounts by keyword. Requires valid login."""
    tenant_id = _get_tenant_id()
    keyword = request.args.get("kw", "").strip()
    limit = int(request.args.get("limit", 10))
    offset = int(request.args.get("offset", 0))

    if not keyword:
        return get_error_argument_result("Missing 'kw' parameter")

    project_base = _get_project_base()
    if project_base not in sys.path:
        sys.path.insert(0, project_base)

    try:
        from rag.svr.wechat_mp.gather import WxGather

        gather = WxGather(tenant_id=tenant_id)
        if not gather.token:
            return get_data_error_result(message="Not logged in — please scan QR code first")

        result = gather.search_biz(keyword, limit=limit, offset=offset)
        if result is None:
            return get_data_error_result(message="Search failed — session may be expired")

        # Extract account info from response
        accounts = []
        biz_list = result.get("list", [])
        for biz in biz_list:
            accounts.append({
                "faker_id": biz.get("fakeid", ""),
                "mp_name": biz.get("nickname", ""),
                "mp_cover": biz.get("round_head_img", ""),
                "mp_intro": biz.get("signature", ""),
                "service_type": biz.get("service_type", 0),
            })

        return get_json_result(data={"accounts": accounts, "total": result.get("total", 0)})

    except Exception as e:
        logger.exception("MP search failed")
        return server_error_response(e)


@manager.route("/wechat-mp/accounts", methods=["GET"])  # noqa: F821
@login_required
async def list_mp_accounts():
    """List all subscribed MP accounts for current tenant."""
    tenant_id = _get_tenant_id()
    accounts = WechatMpAccountService.list_by_tenant(tenant_id)
    return get_json_result(data={"accounts": accounts})


@manager.route("/wechat-mp/accounts", methods=["POST"])  # noqa: F821
@login_required
async def add_mp_account():
    """Subscribe to a WeChat MP account."""
    tenant_id = _get_tenant_id()
    req = await get_request_json()
    if not req:
        return get_data_error_result(message="Request body is required")

    mp_name = req.get("mp_name", "").strip()
    faker_id = req.get("faker_id", "").strip()
    if not mp_name or not faker_id:
        return get_error_argument_result("mp_name and faker_id are required")

    try:
        record = WechatMpAccountService.add_account(
            tenant_id=tenant_id,
            mp_name=mp_name,
            faker_id=faker_id,
            mp_cover=req.get("mp_cover", ""),
            mp_intro=req.get("mp_intro", ""),
        )
        return get_json_result(data=record)
    except Exception as e:
        logger.exception("Failed to add MP account")
        return server_error_response(e)


@manager.route("/wechat-mp/accounts/<account_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_mp_account(account_id: str):
    """Remove a subscribed MP account."""
    tenant_id = _get_tenant_id()
    ok = WechatMpAccountService.remove_account(tenant_id, account_id)
    if not ok:
        return get_data_error_result(message="Account not found")
    return get_json_result(data={"message": "Deleted"})
