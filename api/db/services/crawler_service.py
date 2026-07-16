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
"""crawl4ai 独立爬虫服务 — 任务与结果的 DB 访问层"""
import hashlib
from typing import List, Optional, Tuple

from api.db.db_models import DB, CrawlerResult, CrawlerTask
from api.db.services.common_service import CommonService


def gen_result_id(site_id: str, source_url: str) -> str:
    """Deterministic result ID for dedup: md5(site_id|source_url)."""
    return hashlib.md5(f"{site_id}|{source_url}".encode("utf-8")).hexdigest()


class CrawlerTaskService(CommonService):
    model = CrawlerTask

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        tenant_id: str,
        page_number: int = 1,
        items_per_page: int = 20,
        keyword: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if keyword:
            query = query.where(
                (cls.model.name ** f"%{keyword}%") | (cls.model.site_id ** f"%{keyword}%")
            )
        if enabled is not None:
            query = query.where(cls.model.enabled == enabled)
        total = query.count()
        rows = (
            query.order_by(cls.model.create_time.desc())
            .paginate(page_number, items_per_page)
        )
        return [r.to_dict() for r in rows], total


class CrawlerResultService(CommonService):
    model = CrawlerResult

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        tenant_id: str,
        page_number: int = 1,
        items_per_page: int = 20,
        task_id: Optional[str] = None,
        site_id: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if task_id:
            query = query.where(cls.model.task_id == task_id)
        if site_id:
            query = query.where(cls.model.site_id == site_id)
        if status:
            query = query.where(cls.model.status == status)
        if keyword:
            query = query.where(cls.model.title ** f"%{keyword}%")
        if start_date:
            query = query.where(cls.model.publish_date >= start_date)
        if end_date:
            end_dt = end_date if " " in end_date else f"{end_date} 23:59:59"
            query = query.where(cls.model.publish_date <= end_dt)
        total = query.count()
        rows = (
            query.order_by(cls.model.crawled_at.desc())
            .paginate(page_number, items_per_page)
        )
        # 列表不返回大字段 markdown，避免响应过大
        out = []
        for r in rows:
            d = r.to_dict()
            d.pop("markdown", None)
            out.append(d)
        return out, total

    @classmethod
    @DB.connection_context()
    def exists_id(cls, result_id: str) -> bool:
        return cls.model.select(cls.model.id).where(cls.model.id == result_id).exists()

    @classmethod
    def upsert_result(cls, data: dict) -> bool:
        """Insert or update a crawler_result row by primary key id.

        Returns True if a new row was inserted, False if updated.
        """
        rid = data["id"]
        if cls.exists_id(rid):
            update_data = {k: v for k, v in data.items() if k != "id"}
            cls.update_by_id(rid, update_data)
            return False
        cls.insert(**data)
        return True

    @classmethod
    @DB.connection_context()
    def site_options(cls, tenant_id: str) -> List[str]:
        """Distinct site_id values, for frontend filter dropdown."""
        rows = (
            cls.model.select(cls.model.site_id)
            .where(cls.model.tenant_id == tenant_id)
            .distinct()
        )
        return [r.site_id for r in rows]
