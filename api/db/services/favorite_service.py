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

from api.db.db_models import DB, Favorite
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class FavoriteService(CommonService):
    model = Favorite

    @classmethod
    @DB.connection_context()
    def create_favorite(
        cls,
        tenant_id: str,
        user_id: str,
        title: str,
        message_ids: list,
        messages_data: list = None,
        agent_id: str = None,
        conversation_id: str = None,
    ) -> Favorite:
        now = datetime.now()
        obj = cls.model(
            id=get_uuid(),
            tenant_id=tenant_id,
            user_id=user_id,
            title=title,
            message_ids=message_ids,
            messages_data=messages_data or [],
            agent_id=agent_id,
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
        )
        obj.save(force_insert=True)
        return obj

    @classmethod
    @DB.connection_context()
    def get_user_favorites(
        cls,
        tenant_id: str,
        user_id: str,
        page_number: int = 1,
        items_per_page: int = 20,
    ) -> Tuple[List[dict], int]:
        list_fields = (
            cls.model.id,
            cls.model.title,
            cls.model.message_ids,
            cls.model.created_at,
            cls.model.updated_at,
        )
        base_query = (
            cls.model.select(*list_fields)
            .where(
                (cls.model.tenant_id == tenant_id)
                & (cls.model.user_id == user_id)
            )
        )
        total = base_query.count()
        query = base_query.order_by(cls.model.updated_at.desc()).paginate(
            page_number, items_per_page
        )
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def get_favorite_detail(cls, favorite_id: str, tenant_id: str) -> Optional[dict]:
        obj = cls.model.get_or_none(
            (cls.model.id == favorite_id)
            & (cls.model.tenant_id == tenant_id)
        )
        if obj:
            return obj.to_dict()
        return None

    @classmethod
    @DB.connection_context()
    def update_favorite(
        cls, favorite_id: str, tenant_id: str, user_id: str, data: dict
    ) -> Optional[dict]:
        obj = cls.model.get_or_none(
            (cls.model.id == favorite_id)
            & (cls.model.tenant_id == tenant_id)
            & (cls.model.user_id == user_id)
        )
        if not obj:
            return None
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        obj.updated_at = datetime.now()
        obj.save()
        return obj.to_dict()

    @classmethod
    @DB.connection_context()
    def delete_favorite(cls, favorite_id: str, tenant_id: str, user_id: str) -> int:
        return (
            cls.model.delete()
            .where(
                (cls.model.id == favorite_id)
                & (cls.model.tenant_id == tenant_id)
                & (cls.model.user_id == user_id)
            )
            .execute()
        )
