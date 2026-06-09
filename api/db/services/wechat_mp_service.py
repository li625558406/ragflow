"""
WeChat MP services — CRUD for wechat_mp_account and wechat_mp_auth tables.
"""

import logging
from datetime import datetime

from api.db.db_models import DB, WechatMpAccount, WechatMpAuth
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


class WechatMpAccountService(CommonService):
    model = WechatMpAccount

    @classmethod
    @DB.connection_context()
    def list_by_tenant(cls, tenant_id: str):
        """List all MP accounts for a tenant."""
        return list(
            cls.model.select().where(
                cls.model.tenant_id == tenant_id,
                cls.model.status == 1,
            ).dicts()
        )

    @classmethod
    @DB.connection_context()
    def list_by_faker_ids(cls, tenant_id: str, faker_ids: list):
        """List MP accounts matching given faker_ids for a tenant."""
        if not faker_ids:
            return []
        return list(
            cls.model.select().where(
                cls.model.tenant_id == tenant_id,
                cls.model.faker_id.in_(faker_ids),
            ).dicts()
        )

    @classmethod
    @DB.connection_context()
    def get_by_faker_id(cls, tenant_id: str, faker_id: str):
        """Get a single MP account by faker_id."""
        try:
            return cls.model.get(
                (cls.model.tenant_id == tenant_id) &
                (cls.model.faker_id == faker_id)
            )
        except Exception:
            return None

    @classmethod
    @DB.connection_context()
    def add_account(cls, tenant_id: str, mp_name: str, faker_id: str,
                    mp_cover: str = "", mp_intro: str = "") -> dict:
        """Add a new MP account for a tenant. Returns the created record as dict."""
        existing = cls.get_by_faker_id(tenant_id, faker_id)
        if existing:
            # Update existing and reactivate if previously soft-deleted
            existing.mp_name = mp_name
            existing.status = 1
            if mp_cover:
                existing.mp_cover = mp_cover
            if mp_intro:
                existing.mp_intro = mp_intro
            existing.updated_at = datetime.now()
            existing.save()
            return existing.to_dict()

        record = cls.model.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            mp_name=mp_name,
            faker_id=faker_id,
            mp_cover=mp_cover,
            mp_intro=mp_intro,
            status=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        return record.to_dict()

    @classmethod
    @DB.connection_context()
    def remove_account(cls, tenant_id: str, account_id: str) -> bool:
        """Soft-delete (set status=0) an MP account."""
        rows = (
            cls.model.update(status=0, updated_at=datetime.now())
            .where(
                (cls.model.id == account_id) &
                (cls.model.tenant_id == tenant_id)
            )
            .execute()
        )
        return rows > 0


class WechatMpAuthService(CommonService):
    model = WechatMpAuth

    @classmethod
    @DB.connection_context()
    def get_by_tenant_id(cls, tenant_id: str):
        """Get auth record for a tenant. Returns (ok, record_dict)."""
        try:
            record = cls.model.get_or_none(cls.model.tenant_id == tenant_id)
            if record:
                return True, record.to_dict()
        except Exception as e:
            logger.warning("Failed to get auth for tenant %s: %s", tenant_id, e)
        return False, None

    @classmethod
    @DB.connection_context()
    def upsert(cls, tenant_id: str, payload: dict):
        """Insert or update auth for a tenant."""
        existing = cls.model.get_or_none(cls.model.tenant_id == tenant_id)
        if existing:
            for key, value in payload.items():
                if hasattr(existing, key) and key != "tenant_id":
                    setattr(existing, key, value)
            existing.save()
        else:
            payload.setdefault("id", get_uuid())
            payload["tenant_id"] = tenant_id
            cls.model.create(**payload)

    @classmethod
    @DB.connection_context()
    def delete_by_tenant_id(cls, tenant_id: str) -> bool:
        """Delete auth record for a tenant."""
        rows = (
            cls.model.delete()
            .where(cls.model.tenant_id == tenant_id)
            .execute()
        )
        return rows > 0
