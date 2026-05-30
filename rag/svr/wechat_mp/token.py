"""
Token persistence — adapted from we-mp-rss driver/token.py.
Reads/writes WeChat auth credentials from the ragflow2 DB (wechat_mp_auth table).
"""

import json
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


def _get_project_base():
    """Resolve ragflow project root (same logic as common.file_utils)."""
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def _ensure_db():
    """Lazy-import and init DB services so we can read/write wechat_mp_auth."""
    sys.path.insert(0, _get_project_base())
    from api.db.services.wechat_mp_service import WechatMpAuthService
    return WechatMpAuthService


def _get_auth(tenant_id: str) -> dict | None:
    """Load WeChat auth from DB for a given tenant."""
    try:
        svc = _ensure_db()
        ok, record = svc.get_by_tenant_id(tenant_id)
        if ok and record:
            return record
    except Exception as e:
        logger.warning("Failed to load wechat auth from DB: %s", e)
    return None


def _save_auth(tenant_id: str, token_data: dict, ext_data: dict | None = None):
    """Save WeChat auth to DB."""
    try:
        svc = _ensure_db()
        payload = {
            "tenant_id": tenant_id,
            "cookie": token_data.get("cookies_str", ""),
            "token": token_data.get("token", ""),
            "expiry": token_data.get("expiry", {}).get("expiry_time"),
            "ext_data": json.dumps(ext_data) if ext_data else "",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        svc.upsert(tenant_id, payload)
        logger.info("Saved wechat auth for tenant %s", tenant_id)
    except Exception as e:
        logger.error("Failed to save wechat auth to DB: %s", e)


def get(key: str, default: str = "", tenant_id: str = "") -> str:
    """Get a specific field from saved WeChat auth."""
    if not tenant_id:
        return default
    record = _get_auth(tenant_id)
    if not record:
        return default
    value = record.get(key, default)
    if value is None:
        return default
    return str(value)


def set_token(data: dict, tenant_id: str = "", ext_data: dict | None = None):
    """Persist WeChat token + cookies for a tenant."""
    if not tenant_id:
        logger.error("set_token: missing tenant_id")
        return
    if not data.get("token"):
        return
    _save_auth(tenant_id, data, ext_data)
    logger.info("Token set for tenant %s, expires: %s", tenant_id,
                data.get("expiry", {}).get("expiry_time", "unknown"))
