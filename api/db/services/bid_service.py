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
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

from peewee import fn

from api.db.db_models import DB, BidConstructionParse, BidConstructionProject, BidContractParse, BidEnterpriseBusiness, BidEnterpriseCache, BidEnterpriseParse, BidProject, BidProjectDetail, BidProjectStructure, BidProjectFile, BidProjectParse, BidSyncLog, BidTenderSearch
from peewee import DateTimeField
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

    @staticmethod
    def _map_contract_api_to_project(item: dict, keyword: str = "") -> dict:
        """Map v2 search_contract API response item → bid_project fields."""
        import re
        title_html = item.get("title", "")
        title_plain = re.sub(r"<[^>]*>", "", title_html) if title_html else ""

        part_a_names = []
        for a in (item.get("partAInfo") or []):
            if a.get("name"):
                part_a_names.append(a["name"])

        part_b_names = []
        for b in (item.get("partBInfo") or []):
            if b.get("name"):
                part_b_names.append(b["name"])

        now = datetime.now()
        return {
            "id": item["id"],
            "title": title_plain,
            "title_html": title_html,
            "content": item.get("content", ""),
            "publish_time": item.get("publishTime"),
            "news_type_id": 3,  # 合同
            "project_class_id": item.get("projectClassID", ""),
            "project_money": item.get("projectMoney", ""),
            "part_a_names": part_a_names,
            "part_b_names": part_b_names,
            "has_file": item.get("hasFile", 0),
            "contract_end_date": item.get("contractEndDate", ""),
            "source_type": "api",
            "raw_json": item,
            "se_keywords": keyword,
            "updated_at": now,
            "fetched_at": now,
            "cache_expires_at": datetime.fromtimestamp(now.timestamp() + 3600),  # 1h TTL
        }

    @classmethod
    @DB.connection_context()
    def upsert_contract(cls, item: dict, keyword: str = "") -> Tuple[bool, object]:
        """Upsert a contract search result into bid_project. Returns (is_new, obj)."""
        data = cls._map_contract_api_to_project(item, keyword)
        pid = data["id"]
        existing = cls.model.get_or_none(cls.model.id == pid)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return False, existing
        else:
            data["created_at"] = data.get("updated_at", datetime.now())
            obj = cls.model(**data).save(force_insert=True)
            return True, obj

    @classmethod
    @DB.connection_context()
    def filter_valid_cache(cls, query):
        """Filter query to only include non-expired cached records."""
        now = datetime.now()
        return query.where(
            (cls.model.cache_expires_at.is_null()) |
            (cls.model.cache_expires_at > now)
        )


class BidProjectDetailService(CommonService):
    model = BidProjectDetail

    @classmethod
    @DB.connection_context()
    def upsert_detail(cls, project_id: int, data: dict) -> object:
        existing = cls.model.get_or_none(cls.model.project_id == project_id)
        now = datetime.now()
        defaults = {
            "fetched_at": now,
            "cache_expires_at": now + timedelta(days=30),
        }
        if existing:
            for k, v in {**defaults, **data}.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data = {**defaults, **data, "id": project_id, "project_id": project_id}
            data.setdefault("created_at", now)
            return cls.model(**data).save(force_insert=True)


class BidProjectStructureService(CommonService):
    model = BidProjectStructure

    @classmethod
    @DB.connection_context()
    def upsert_structure(cls, project_id: int, data: dict) -> object:
        existing = cls.model.get_or_none(cls.model.project_id == project_id)
        now = datetime.now()
        defaults = {
            "fetched_at": now,
            "cache_expires_at": now + timedelta(days=30),
        }
        if existing:
            for k, v in {**defaults, **data}.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data = {**defaults, **data, "id": project_id, "project_id": project_id}
            data.setdefault("created_at", now)
            return cls.model(**data).save(force_insert=True)


class BidProjectFileService(CommonService):
    model = BidProjectFile

    @classmethod
    @DB.connection_context()
    def get_by_project(cls, project_id: int) -> List[dict]:
        objs = cls.model.select().where(cls.model.project_id == project_id)
        return list(objs.dicts())

    @staticmethod
    def _sanitize_datetime(val):
        """过滤零值日期，避免 _normalize_data 的 timestamp_to_date 崩溃。"""
        if val is None:
            return None
        s = str(val)
        if s.startswith("0000-00-00") or s == "":
            return None
        return val

    @classmethod
    @DB.connection_context()
    def upsert_file(cls, data: dict) -> object:
        fid = data.get("project_file_id")
        now = datetime.now()
        defaults = {"fetched_at": now}
        existing = cls.model.get_or_none(cls.model.project_file_id == fid)
        if existing:
            for k, v in {**defaults, **data}.items():
                # 过滤零值日期字段
                if isinstance(getattr(cls.model, k, None), DateTimeField):
                    v = cls._sanitize_datetime(v)
                setattr(existing, k, v)
            # 修复已有记录中的零值日期（遍历所有DateTimeField）
            from peewee import DateTimeField as PeeweeDTF
            for field_name, field_obj in cls.model._meta.fields.items():
                if isinstance(field_obj, PeeweeDTF):
                    try:
                        val = getattr(existing, field_name, None)
                        if val is not None and str(val).startswith("0000-00-00"):
                            setattr(existing, field_name, None)
                    except Exception:
                        setattr(existing, field_name, None)
            existing.save()
            return existing
        else:
            data = {**defaults, **data}
            for k in list(data.keys()):
                if isinstance(getattr(cls.model, k, None), DateTimeField):
                    data[k] = cls._sanitize_datetime(data[k])
            data.setdefault("created_at", now)
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


class BidEnterpriseCacheService(CommonService):
    model = BidEnterpriseCache

    # TTL strategy (in hours): profile changes slowly, lists change moderately
    TTL_MAP = {
        "profile": 168,     # 7 days
        "contacts": 72,     # 3 days
        "customers": 24,    # 1 day
        "suppliers": 24,    # 1 day
    }

    @classmethod
    @DB.connection_context()
    def get_cached(
        cls,
        company_name: str,
        cache_type: str,
        page_no: int = 1,
        page_size: int = 20,
        allow_stale: bool = False,
    ) -> Optional[dict]:
        """Get cached enterprise data. Returns None on miss.

        By default only returns non-expired records. Set allow_stale=True
        to get the most recent cache regardless of expiry (for API failure fallback).
        """
        now = datetime.now()
        if allow_stale:
            obj = (cls.model
                   .select()
                   .where(
                       (cls.model.company_name == company_name)
                       & (cls.model.cache_type == cache_type)
                       & (cls.model.page_no == page_no)
                       & (cls.model.page_size == page_size)
                   )
                   .order_by(cls.model.fetched_at.desc())
                   .first())
        else:
            obj = cls.model.get_or_none(
                (cls.model.company_name == company_name)
                & (cls.model.cache_type == cache_type)
                & (cls.model.page_no == page_no)
                & (cls.model.page_size == page_size)
                & (cls.model.cache_expires_at > now)
            )
        if obj:
            return obj.to_dict()
        return None

    @classmethod
    @DB.connection_context()
    def upsert_cache(
        cls,
        company_name: str,
        cache_type: str,
        response_data: dict,
        page_no: int = 1,
        page_size: int = 20,
        ttl_hours: int = None,
    ) -> object:
        """Insert or update cached enterprise API response. Returns the row."""
        now = datetime.now()
        if ttl_hours is None:
            ttl_hours = cls.TTL_MAP.get(cache_type, 1)
        expires_at = now + timedelta(hours=ttl_hours)

        existing = cls.model.get_or_none(
            (cls.model.company_name == company_name)
            & (cls.model.cache_type == cache_type)
            & (cls.model.page_no == page_no)
            & (cls.model.page_size == page_size)
        )
        if existing:
            existing.response_json = response_data
            existing.fetched_at = now
            existing.cache_expires_at = expires_at
            existing.save()
            return existing
        else:
            return cls.model(
                company_name=company_name,
                cache_type=cache_type,
                page_no=page_no,
                page_size=page_size,
                response_json=response_data,
                fetched_at=now,
                cache_expires_at=expires_at,
                created_at=now,
            ).save(force_insert=True)


class BidEnterpriseBusinessService(CommonService):
    """企业工商信息全量缓存服务 — 新接口 /enterprise/business/all

    TTL: 7 天。
    """

    model = BidEnterpriseBusiness

    @classmethod
    @DB.connection_context()
    def get_cached(cls, keyword: str, allow_stale: bool = False) -> Optional[dict]:
        """查询缓存。默认只返回未过期的；allow_stale=True 返回最新一条（含过期）。"""
        now = datetime.now()
        if allow_stale:
            obj = (cls.model
                   .select()
                   .where(cls.model.keyword == keyword)
                   .order_by(cls.model.fetched_at.desc())
                   .first())
        else:
            obj = cls.model.get_or_none(
                (cls.model.keyword == keyword)
                & (cls.model.cache_expires_at > now)
            )
        if obj:
            return obj.to_dict()
        return None

    @classmethod
    @DB.connection_context()
    def upsert_cache(cls, keyword: str, response_data: dict, ttl_hours: int = 168) -> object:
        """覆盖写入缓存，默认 TTL=168h (7天)。"""
        now = datetime.now()
        expires_at = now + timedelta(hours=ttl_hours)

        existing = cls.model.get_or_none(cls.model.keyword == keyword)
        if existing:
            existing.response_json = response_data
            existing.fetched_at = now
            existing.cache_expires_at = expires_at
            existing.save()
            return existing
        else:
            return cls.model(
                keyword=keyword,
                response_json=response_data,
                fetched_at=now,
                cache_expires_at=expires_at,
                created_at=now,
            ).save(force_insert=True)


class BidConstructionProjectService(CommonService):
    model = BidConstructionProject

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        page_number: int = 1,
        items_per_page: int = 20,
        keyword: str = None,
        provice_code: str = None,
        city_code: str = None,
        start_date: str = None,
        end_date: str = None,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select()
        if keyword:
            query = query.where(
                (cls.model.title ** f"%{keyword}%")
                | (cls.model.summary ** f"%{keyword}%")
            )
        if provice_code:
            query = query.where(cls.model.provice_code == provice_code)
        if city_code:
            query = query.where(cls.model.city_code == city_code)
        if start_date:
            query = query.where(cls.model.publish_time >= start_date)
        if end_date:
            end_dt = end_date if " " in end_date else f"{end_date} 23:59:59"
            query = query.where(cls.model.publish_time <= end_dt)

        total = query.count()
        query = query.order_by(cls.model.publish_time.desc())
        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def upsert(cls, data: dict) -> Tuple[bool, object]:
        pid = data.get("id")
        if not pid:
            return False, None
        existing = cls.model.get_or_none(cls.model.id == pid)
        now = datetime.now()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.updated_at = now
            existing.save()
            return False, existing
        else:
            data.setdefault("created_at", now)
            data.setdefault("updated_at", now)
            return True, cls.model(**data).save(force_insert=True)

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, project_id: int) -> Optional[dict]:
        obj = cls.model.get_or_none(cls.model.id == project_id)
        if obj:
            return obj.to_dict()
        return None


class BidConstructionParseService(CommonService):
    model = BidConstructionParse

    @classmethod
    @DB.connection_context()
    def get_by_project(cls, project_id: int) -> Optional[dict]:
        obj = cls.model.get_or_none(cls.model.project_id == project_id)
        return obj.to_dict() if obj else None

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


class BidContractParseService(CommonService):
    model = BidContractParse

    @classmethod
    @DB.connection_context()
    def get_by_project(cls, project_id: int) -> Optional[dict]:
        obj = cls.model.get_or_none(cls.model.project_id == project_id)
        return obj.to_dict() if obj else None

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


class BidEnterpriseParseService(CommonService):
    model = BidEnterpriseParse

    @classmethod
    @DB.connection_context()
    def get_by_company(cls, company_name: str) -> Optional[dict]:
        obj = cls.model.get_or_none(cls.model.company_name == company_name)
        return obj.to_dict() if obj else None

    @classmethod
    @DB.connection_context()
    def upsert(cls, data: dict):
        company_name = data.get("company_name")
        existing = cls.model.get_or_none(cls.model.company_name == company_name)
        data["updated_at"] = datetime.now()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.save()
            return existing
        else:
            data.setdefault("created_at", datetime.now())
            return cls.model(**data).save(force_insert=True)


class BidTenderSearchService(CommonService):
    model = BidTenderSearch

    @classmethod
    @DB.connection_context()
    def get_list(
        cls,
        keyword: str,
        page_number: int = 1,
        items_per_page: int = 10,
        announcement_type: str = None,
        province_code: str = None,
        city_code: str = None,
        start_date: str = None,
        end_date: str = None,
    ) -> Tuple[List[dict], int]:
        query = cls.model.select().where(
            cls.model.keyword_hash == cls._hash_key(keyword)
        )
        if announcement_type:
            query = query.where(cls.model.announcement_type_filter == announcement_type)
        if province_code:
            query = query.where(cls.model.province_code_filter == province_code)
        if city_code:
            query = query.where(cls.model.city_code_filter == city_code)
        if start_date:
            query = query.where(cls.model.publish_time >= start_date)
        if end_date:
            query = query.where(cls.model.publish_time <= f"{end_date} 23:59:59")
        query = cls.filter_valid_cache(query)
        total = query.count()
        query = query.order_by(cls.model.publish_time.desc())
        query = query.paginate(page_number, items_per_page)
        return list(query.dicts()), total

    @classmethod
    @DB.connection_context()
    def upsert_item(cls, item: dict, keyword: str, search_params: dict) -> Tuple[bool, object]:
        item_id = cls._item_id(item)
        now = datetime.now()
        existing = cls.model.get_or_none(cls.model.id == item_id)
        mapped = cls._map_api_item(item, keyword, search_params, now)
        if existing:
            # 更新时保留原始 keyword_hash/keyword，避免 item 在不同搜索词之间漂移
            mapped.pop("keyword_hash", None)
            mapped.pop("keyword", None)
            mapped.pop("created_at", None)
            for k, v in mapped.items():
                setattr(existing, k, v)
            existing.save()
            return False, existing
        else:
            mapped["created_at"] = now
            return True, cls.model(**mapped).save(force_insert=True)

    @classmethod
    def filter_valid_cache(cls, query):
        now = datetime.now()
        return query.where(cls.model.cache_expires_at > now)

    @staticmethod
    def _hash_key(keyword: str) -> str:
        return hashlib.sha256(keyword.strip().lower().encode()).hexdigest()

    @staticmethod
    def _item_id(item: dict) -> str:
        raw = f"{item.get('projectNumber', '')}|{item.get('title', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _map_api_item(item: dict, keyword: str, search_params: dict, now: datetime) -> dict:
        return {
            "id": BidTenderSearchService._item_id(item),
            "keyword_hash": BidTenderSearchService._hash_key(keyword),
            "keyword": keyword,
            "title": item.get("title", ""),
            "project_name": item.get("projectName", ""),
            "project_number": item.get("projectNumber", ""),
            "publish_time": item.get("publishTime"),
            "announcement_type": item.get("announcementType", ""),
            "announcement_type_code": item.get("announcementTypeCode"),
            "bidding_stage": item.get("biddingStage", ""),
            "bidding_stage_code": item.get("biddingStageCode"),
            "procurement_method": item.get("procurementMethod", ""),
            "procurement_method_code": item.get("procurementMethodCode"),
            "industry_type": item.get("industryType", ""),
            "target_item_type": item.get("targetItemType", ""),
            "project_region_province": item.get("projectRegionProvince", ""),
            "project_region_province_code": item.get("projectRegionProvinceCode", ""),
            "project_region_city": item.get("projectRegionCity", ""),
            "project_region_city_code": item.get("projectRegionCityCode", ""),
            "content_url": item.get("contentUrl", ""),
            "project_budget_amount": item.get("projectBudgetAmount", ""),
            "project_budget_amount_unit": item.get("projectBudgetAmountUnit", ""),
            "total_amount": item.get("totalAmount", ""),
            "total_amount_unit": item.get("totalAmountUnit", ""),
            "bid_document_start_time": item.get("bidDocumentStartTime", ""),
            "bid_document_end_time": item.get("bidDocumentEndTime", ""),
            "bidding_start_time": item.get("biddingStartTime", ""),
            "bidding_end_time": item.get("biddingEndTime", ""),
            "opening_bid_time": item.get("openingBidTime", ""),
            "contract_num": item.get("contractNum", ""),
            "purchase_agency": item.get("purchaseAgency", []),
            "win_candidate": item.get("winCandidate", []),
            "contacts_purchase_agency": item.get("contactsPurchaseAgency"),
            "contacts_win_candidate": item.get("contactsWinCandidate"),
            "search_mode": search_params.get("searchMode", 2),
            "announcement_type_filter": str(search_params.get("announcementType") or ""),
            "province_code_filter": search_params.get("projectRegionProvinceCode") or "",
            "city_code_filter": search_params.get("projectRegionCityCode") or "",
            "raw_json": item,
            "fetched_at": now,
            "cache_expires_at": now + timedelta(hours=24),
        }