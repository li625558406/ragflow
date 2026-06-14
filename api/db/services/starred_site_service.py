#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from datetime import datetime
from typing import List, Optional, Tuple

from api.db.db_models import DB, StarredSite
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class StarredSiteService(CommonService):
    model = StarredSite

    @classmethod
    @DB.connection_context()
    def create_starred_site(
        cls,
        tenant_id: str,
        user_id: str,
        site_name: str,
        site_url: str,
    ) -> StarredSite:
        now = datetime.now()
        obj = cls.model(
            id=get_uuid(),
            tenant_id=tenant_id,
            user_id=user_id,
            site_name=site_name,
            site_url=site_url,
            created_at=now,
            updated_at=now,
        )
        obj.save(force_insert=True)
        return obj

    @classmethod
    @DB.connection_context()
    def get_user_starred_sites(
        cls,
        tenant_id: str,
        user_id: str,
    ) -> List[dict]:
        """Return all starred sites ordered by created_at ASC (FIFO — first liked appears first)."""
        items = (
            cls.model.select()
            .where(
                (cls.model.tenant_id == tenant_id)
                & (cls.model.user_id == user_id)
            )
            .order_by(cls.model.created_at.asc())
            .dicts()
        )
        return list(items)

    @classmethod
    @DB.connection_context()
    def get_by_site_url(
        cls,
        tenant_id: str,
        user_id: str,
        site_url: str,
    ) -> Optional[StarredSite]:
        return cls.model.get_or_none(
            (cls.model.tenant_id == tenant_id)
            & (cls.model.user_id == user_id)
            & (cls.model.site_url == site_url)
        )

    @classmethod
    @DB.connection_context()
    def delete_starred_site(
        cls,
        site_id: str,
        tenant_id: str,
        user_id: str,
    ) -> int:
        return (
            cls.model.delete()
            .where(
                (cls.model.id == site_id)
                & (cls.model.tenant_id == tenant_id)
                & (cls.model.user_id == user_id)
            )
            .execute()
        )
