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
import logging
from typing import Tuple, List

from peewee import fn

from api.db.db_models import DB, ScheduledTask, ScheduledTaskLog
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


class ScheduledTaskService(CommonService):
    model = ScheduledTask

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        tenant_id: str,
        page_number: int = 1,
        items_per_page: int = 15,
        orderby: str = "create_time",
        desc: bool = True,
        name: str = None,
        enabled: bool = None,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if name:
            query = query.where(cls.model.name ** f"%{name}%")
        if enabled is not None:
            query = query.where(cls.model.enabled == enabled)
        if desc:
            query = query.order_by(cls.model.getter_by(orderby).desc())
        else:
            query = query.order_by(cls.model.getter_by(orderby).asc())
        total = query.count()
        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def get_due_tasks(cls) -> List[dict]:
        """Return all enabled tasks whose next_run_time <= now."""
        now_ts = current_timestamp()
        query = cls.model.select().where(
            cls.model.enabled == True,
            cls.model.next_run_time.is_null(False),
            cls.model.next_run_time <= now_ts,
        )
        return list(query.dicts())

    @classmethod
    @DB.connection_context()
    def update_next_run(cls, task_id: str, next_run_ts: int):
        cls.model.update(
            next_run_time=next_run_ts,
            update_time=current_timestamp(),
        ).where(cls.model.id == task_id).execute()


class ScheduledTaskLogService(CommonService):
    model = ScheduledTaskLog

    @classmethod
    @DB.connection_context()
    def get_by_task_id(
        cls, task_id: str, page_number: int = 1, items_per_page: int = 15
    ) -> Tuple[List[dict], int]:
        query = (
            cls.model.select()
            .where(cls.model.task_id == task_id)
            .order_by(cls.model.create_time.desc())
        )
        total = query.count()
        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def get_latest_by_task_id(cls, task_id: str) -> dict | None:
        try:
            return (
                cls.model.select()
                .where(cls.model.task_id == task_id)
                .order_by(cls.model.create_time.desc())
                .get()
                .to_dict()
            )
        except Exception:
            return None
