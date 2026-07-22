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
"""智能采集（新系统）扩展表 Service 层。

为 CollectionWriter 提供 policy/personnel 扩展表的写入与查询能力。
与 bid_service.py / crawler_service.py 完全独立。
"""
import logging
from typing import Any, Dict, List, Optional

from api.db.db_models import DB, CollectionPolicyExt, CollectionPersonnelExt
from api.db.services.common_service import CommonService


class CollectionPolicyExtService(CommonService):
    """collection_policy_ext CRUD。"""
    model = CollectionPolicyExt

    @classmethod
    def _exists(cls, result_id: str) -> bool:
        return (
            cls.model
            .select(cls.model.result_id)
            .where(cls.model.result_id == result_id)
            .exists()
        )

    @classmethod
    @DB.connection_context()
    def upsert(cls, result_id: str, fields: Dict[str, Any]) -> bool:
        """按 result_id 主键 upsert。返回 True=新插入。"""
        data = {k: v for k, v in fields.items() if hasattr(cls.model, k)}
        data["result_id"] = result_id
        try:
            if cls._exists(result_id):
                update_data = {k: v for k, v in data.items() if k != "result_id"}
                if update_data:
                    cls.update_by_id(result_id, update_data)
                return False
            cls.insert(**data)
            return True
        except Exception as e:
            logging.error("CollectionPolicyExtService.upsert failed (result=%s): %s",
                          result_id, e)
            return False

    @classmethod
    @DB.connection_context()
    def get_by_result_ids(cls, result_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量查询，返回 {result_id: row_dict}，避免 N+1。"""
        if not result_ids:
            return {}
        rows = cls.model.select().where(cls.model.result_id.in_(result_ids))
        return {r.result_id: r.to_dict() for r in rows}


class CollectionPersonnelExtService(CommonService):
    """collection_personnel_ext CRUD。"""
    model = CollectionPersonnelExt

    @classmethod
    def _exists(cls, result_id: str) -> bool:
        return (
            cls.model
            .select(cls.model.result_id)
            .where(cls.model.result_id == result_id)
            .exists()
        )

    @classmethod
    @DB.connection_context()
    def upsert(cls, result_id: str, fields: Dict[str, Any]) -> bool:
        data = {k: v for k, v in fields.items() if hasattr(cls.model, k)}
        data["result_id"] = result_id
        try:
            if cls._exists(result_id):
                update_data = {k: v for k, v in data.items() if k != "result_id"}
                if update_data:
                    cls.update_by_id(result_id, update_data)
                return False
            cls.insert(**data)
            return True
        except Exception as e:
            logging.error("CollectionPersonnelExtService.upsert failed (result=%s): %s",
                          result_id, e)
            return False

    @classmethod
    @DB.connection_context()
    def get_by_result_ids(cls, result_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not result_ids:
            return {}
        rows = cls.model.select().where(cls.model.result_id.in_(result_ids))
        return {r.result_id: r.to_dict() for r in rows}


def get_ext_by_category(category: str, result_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """按 category 路由批量查询扩展字段。非 policy/personnel 返回空 dict。"""
    if not result_ids:
        return {}
    if category == "policy":
        return CollectionPolicyExtService.get_by_result_ids(result_ids)
    if category == "personnel":
        return CollectionPersonnelExtService.get_by_result_ids(result_ids)
    return {}


def upsert_ext_by_category(category: str, result_id: str,
                           fields: Dict[str, Any]) -> bool:
    """按 category 路由写入扩展字段。非 policy/personnel 直接返回 True（无操作）。"""
    if category == "policy":
        return CollectionPolicyExtService.upsert(result_id, fields)
    if category == "personnel":
        return CollectionPersonnelExtService.upsert(result_id, fields)
    return True
