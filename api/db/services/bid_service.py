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
from datetime import datetime
from typing import Tuple, List, Optional

from peewee import fn

from api.db.db_models import DB, BidProject, BidProjectDetail, BidProjectStructure, BidProjectFile, BidProjectParse, BidSyncLog
from api.db.services.common_service import CommonService


class BidProjectService(CommonService):
    model = BidProject

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        page_number: int = 1,
        items_per_page: int = 20,
        keyword: str = None,
        exclude_keyword: str = None,
        include_keyword: str = None,
        project_class_id: str = None,
        purchase_type_id: str = None,
        provice_code: str = None,
        city_code: str = None,
        county_code: str = None,
        start_date: str = None,
        end_date: str = None,
        contract_end_min: str = None,
        contract_end_max: str = None,
        project_money_min: int = None,
        project_money_max: int = None,
        part_a_name: str = None,
        part_b_name: str = None,
        has_file: int = None,
        file_flag: int = None,
        industry_code: str = None,
        news_type_id: int = None,
        source_type: str = None,
        orderby: str = "publish_time",
        desc: bool = True,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select()

        if keyword:
            query = query.where(cls.model.title ** f"%{keyword}%")
        if project_class_id:
            query = query.where(cls.model.project_class_id == project_class_id)
        if purchase_type_id:
            query = query.where(cls.model.purchase_type_id == purchase_type_id)
        if provice_code:
            query = query.where(cls.model.provice_code == provice_code)
        if city_code:
            query = query.where(cls.model.city_code == city_code)
        if start_date:
            query = query.where(cls.model.publish_time >= start_date)
        if end_date:
            # If end_date is just a date (no time), append 23:59:59 so records
            # with time like "2026-05-27 10:30:00" are not excluded by string comparison.
            end_dt = end_date if " " in end_date else f"{end_date} 23:59:59"
            query = query.where(cls.model.publish_time <= end_dt)
        if project_money_min is not None:
            query = query.where(cls.model.project_money >= project_money_min)
        if project_money_max is not None:
            query = query.where(cls.model.project_money <= project_money_max)
        if part_a_name:
            query = query.where(cls.model.part_a_names ** f"%{part_a_name}%")
        if part_b_name:
            query = query.where(cls.model.part_b_names ** f"%{part_b_name}%")
        if has_file is not None:
            query = query.where(cls.model.has_file == has_file)
        if industry_code:
            # industry_codes is stored as JSON array like ["G544","E481"]
            # Single-letter codes are categories (A-T), use prefix match
            # Multi-character codes are sub-industries, use exact match
            if len(industry_code) == 1:
                query = query.where(cls.model.industry_codes ** f'%"{industry_code}%')
            else:
                query = query.where(cls.model.industry_codes ** f'%"{industry_code}"%')
        if county_code:
            query = query.where(cls.model.county_code == county_code)
        if contract_end_min:
            query = query.where(cls.model.contract_end_date >= contract_end_min)
        if contract_end_max:
            end_dt = contract_end_max if " " in contract_end_max else f"{contract_end_max} 23:59:59"
            query = query.where(cls.model.contract_end_date <= end_dt)
        if file_flag is not None and file_flag in (0, 1):
            query = query.where(cls.model.has_file == file_flag)
        if news_type_id is not None:
            query = query.where(cls.model.news_type_id == news_type_id)
        if source_type:
            query = query.where(cls.model.source_type == source_type)
        if include_keyword:
            query = query.where(cls.model.title ** f"%{include_keyword}%")
        if exclude_keyword:
            query = query.where(~(cls.model.title ** f"%{exclude_keyword}%"))

        total = query.count()

        if desc:
            query = query.order_by(cls.model.getter_by(orderby).desc())
        else:
            query = query.order_by(cls.model.getter_by(orderby).asc())

        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def upsert_project(cls, data: dict) -> Tuple[bool, object]:
        """Insert or update a bid project by ID. Returns (is_new, obj)."""
        pid = data.get("id")
        if not pid:
            return False, None
        existing = cls.model.get_or_none(cls.model.id == pid)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return False, existing
        else:
            now = datetime.now()
            data["created_at"] = now
            data["updated_at"] = now
            obj = cls.model(**data).save(force_insert=True)
            return True, obj

    @classmethod
    @DB.connection_context()
    def get_by_project_id(cls, project_id: int) -> Optional[dict]:
        obj = cls.model.get_or_none(cls.model.id == project_id)
        if obj:
            return obj.to_dict()
        return None


class BidProjectDetailService(CommonService):
    model = BidProjectDetail

    @classmethod
    @DB.connection_context()
    def upsert_detail(cls, project_id: int, data: dict) -> object:
        existing = cls.model.get_or_none(cls.model.project_id == project_id)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data["id"] = project_id
            data["project_id"] = project_id
            data["created_at"] = datetime.now()
            return cls.model(**data).save(force_insert=True)


class BidProjectStructureService(CommonService):
    model = BidProjectStructure

    @classmethod
    @DB.connection_context()
    def upsert_structure(cls, project_id: int, data: dict) -> object:
        existing = cls.model.get_or_none(cls.model.project_id == project_id)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data["id"] = project_id
            data["project_id"] = project_id
            data["created_at"] = datetime.now()
            return cls.model(**data).save(force_insert=True)


class BidProjectFileService(CommonService):
    model = BidProjectFile

    @classmethod
    @DB.connection_context()
    def get_by_project(cls, project_id: int) -> List[dict]:
        objs = cls.model.select().where(cls.model.project_id == project_id)
        return list(objs.dicts())

    @classmethod
    @DB.connection_context()
    def upsert_file(cls, data: dict) -> object:
        fid = data.get("project_file_id")
        existing = cls.model.get_or_none(cls.model.project_file_id == fid)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data["created_at"] = datetime.now()
            return cls.model(**data).save(force_insert=True)


class BidSyncLogService(CommonService):
    model = BidSyncLog

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        page_number: int = 1,
        items_per_page: int = 15,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select().order_by(cls.model.created_at.desc())
        total = query.count()
        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total


class BidProjectParseService(CommonService):
    model = BidProjectParse

    @classmethod
    @DB.connection_context()
    def get_by_project(cls, project_id: int):
        obj = cls.model.get_or_none(cls.model.project_id == project_id)
        if obj:
            return obj.to_dict()
        return None

    @classmethod
    @DB.connection_context()
    def upsert(cls, data: dict):
        project_id = data.get("project_id")
        existing = cls.model.get_or_none(cls.model.project_id == project_id)
        data["updated_at"] = datetime.now()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data.setdefault("created_at", datetime.now())
            return cls.model(**data).save(force_insert=True)
