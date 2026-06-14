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
"""世舶科技标讯 REST API

策略：所有按次计费的 API 都走「缓存优先」模式
  1. 先查本地 DB → 有数据则直接返回（免费）
  2. 无数据 → 调用世舶科技付费 API → 存入 DB → 返回
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.services.bid_service import (
    BidConstructionParseService,
    BidContractParseService,
    BidEnterpriseParseService,
    BidProjectService,
    BidProjectDetailService,
    BidProjectStructureService,
    BidProjectFileService,
    BidProjectParseService,
    BidSyncLogService,
)
from api.db.services.document_service import DocumentService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
)
from api.utils.bid_api_client import BidApiClient, BidApiError
from api.utils.bid_tool_service import (
    get_enterprise_contacts_cached,
    get_enterprise_customers_cached,
    get_enterprise_profile_cached,
    get_enterprise_suppliers_cached,
)

manager = Blueprint("rest_bid_app", __name__)


# ---------------------------------------------------------------------------
# 第三方 API 调用频率限制（Redis Token Bucket）
# ---------------------------------------------------------------------------

import time

from rag.utils.redis_conn import REDIS_CONN

# 限额配置: (capacity, rate_per_second)
# capacity = 突发最大请求数, rate = 恢复速度（每秒恢复几个 token）
BID_RATE_LIMITS = {
    "search": (30, 2),       # 搜索类: 30 burst, 2/s 恢复 → ~15次/分钟
    "detail": (20, 1),       # 详情类: 20 burst, 1/s 恢复 → ~12次/分钟
    "enterprise": (10, 0.5),  # 企业类: 10 burst, 0.5/s → ~7次/分钟
}

BID_RATE_LIMIT_RESET_KEY = "bid_rate_limit_reset:{user_id}:{category}"


def check_bid_rate_limit(category: str) -> dict | None:
    """检查当前用户的第三方 API 调用频率。

    返回 None 表示通过，返回 {"retry_after": N} 表示被限流。
    在调用 BidApiClient 之前调用此函数。
    """
    if category not in BID_RATE_LIMITS or not REDIS_CONN.is_alive():
        return None

    capacity, rate = BID_RATE_LIMITS[category]
    user_id = current_user.id
    key = f"bid_rl:{user_id}:{category}"
    now = time.time()

    try:
        result = REDIS_CONN.lua_token_bucket(
            keys=[key], args=[capacity, rate, now, 1],
        )
    except Exception:
        return None

    if result and int(result[0]) == 1:
        return None

    remaining = float(result[1]) if result else 0
    wait = max(1, int((1 - remaining / rate))) if rate > 0 else 60
    return {"retry_after": wait}


def bid_rate_limit(category: str):
    """装饰器: 对第三方 API 调用做 per-user Token Bucket 限流。

    category: "search" / "detail" / "enterprise"
    返回 429 时响应体包含 X-RateLimit-Reset 头（秒），前端可据此做冷却倒计时。
    """
    if category not in BID_RATE_LIMITS:
        # 未知 category 不限流
        def noop(f):
            return f
        return noop

    capacity, rate = BID_RATE_LIMITS[category]

    def decorator(func):
        from functools import wraps

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not REDIS_CONN.is_alive():
                return await func(*args, **kwargs)

            user_id = current_user.id
            key = f"bid_rl:{user_id}:{category}"
            now = time.time()

            try:
                result = REDIS_CONN.lua_token_bucket(
                    keys=[key],
                    args=[capacity, rate, now, 1],
                )
            except Exception:
                # Redis 异常时放行
                return await func(*args, **kwargs)

            if result and int(result[0]) == 1:
                return await func(*args, **kwargs)

            # 限流触发，计算等待时间
            try:
                remaining = float(result[1]) if result else 0
                wait = max(1, int((1 - remaining / rate))) if rate > 0 else 60
            except Exception:
                wait = 10

            from quart import jsonify

            resp = jsonify({
                "code": 429,
                "message": f"请求过于频繁，请 {wait} 秒后重试",
                "data": {"retry_after": wait},
            })
            resp.status_code = 429
            resp.headers["X-RateLimit-Category"] = category
            resp.headers["X-RateLimit-Reset"] = str(wait)
            resp.headers["Retry-After"] = str(wait)
            return resp

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 地区编码 — 省市联动
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 行业分类（GB/T 4754-2017）— 静态数据，无需数据库
# ---------------------------------------------------------------------------
@manager.route("/bid/industries", methods=["GET"])  # noqa: F821
@login_required
def list_industries():
    from api.utils.industry_data import get_all_industries_grouped
    data = get_all_industries_grouped()
    return get_json_result(data=data)


@manager.route("/bid/areas", methods=["GET"])  # noqa: F821
@login_required
def list_areas():
    from api.db.db_models import AreaCode

    parent_code = request.args.get("parent_code", "0")
    level = request.args.get("level", type=int)

    query = AreaCode.select(AreaCode.code, AreaCode.name, AreaCode.parent_code, AreaCode.level)
    if level is not None:
        query = query.where(AreaCode.level == level)
    if parent_code != "all":
        query = query.where(AreaCode.parent_code == parent_code)

    rows = list(query.dicts())
    return get_json_result(data=rows)


# ---------------------------------------------------------------------------
# 标讯列表（本地查询优先，不足20条时自动调第三方API补充）
# ---------------------------------------------------------------------------
def _parse_dt(dt_str: str):
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _safe_json(val) -> str:
    if val is None:
        return "[]"
    return json.dumps(val, ensure_ascii=False)


@manager.route("/bid/projects", methods=["GET"])  # noqa: F821
@login_required
def list_bid_projects():
    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("items_per_page", 20))
    keyword = request.args.get("keyword", "") or None
    exclude_keyword = request.args.get("exclude_keyword", "") or None
    include_keyword = request.args.get("include_keyword", "") or None
    project_class_id = request.args.get("project_class_id", "") or None
    purchase_type_id = request.args.get("purchase_type_id", "") or None
    provice_code = request.args.get("provice_code", "") or None
    city_code = request.args.get("city_code", "") or None
    county_code = request.args.get("county_code", "") or None
    start_date = request.args.get("start_date", "") or None
    end_date = request.args.get("end_date", "") or None
    contract_end_min = request.args.get("contract_end_min", "") or None
    contract_end_max = request.args.get("contract_end_max", "") or None
    project_money_min = request.args.get("project_money_min", type=int) or None
    project_money_max = request.args.get("project_money_max", type=int) or None
    part_a_name = request.args.get("part_a_name", "") or None
    part_b_name = request.args.get("part_b_name", "") or None
    agent_name = request.args.get("agent_name", "") or None
    has_file = request.args.get("has_file", type=int) or None
    file_flag = request.args.get("file_flag", type=int) or None
    industry_code = request.args.get("industry_code", "") or None
    news_type_id = request.args.get("news_type_id", type=int) or None
    source_type = request.args.get("source_type", "") or None
    data_source = request.args.get("data_source", "auto")

    # Step 1: 查本地 DB（供 API-first 降级及非 api 模式使用）
    objs, total = BidProjectService.get_list(
        page_number=page_number,
        items_per_page=items_per_page,
        keyword=keyword,
        exclude_keyword=exclude_keyword,
        include_keyword=include_keyword,
        project_class_id=project_class_id,
        purchase_type_id=purchase_type_id,
        provice_code=provice_code,
        city_code=city_code,
        county_code=county_code,
        start_date=start_date,
        end_date=end_date,
        contract_end_min=contract_end_min,
        contract_end_max=contract_end_max,
        project_money_min=project_money_min,
        project_money_max=project_money_max,
        part_a_name=part_a_name,
        part_b_name=part_b_name,
        has_file=has_file,
        file_flag=file_flag,
        industry_code=industry_code,
        news_type_id=news_type_id,
        source_type=source_type,
    )

    # Step 2 (API-first): data_source=api 时直接调 API 返回，异步缓存 DB
    if data_source == "api":
        if source_type == 'crawler':
            return get_json_result(data={"projects": objs, "total": total})

        rl = check_bid_rate_limit("search")
        if rl:
            return get_data_error_result(
                message=f"请求过于频繁，请 {rl['retry_after']} 秒后重试",
                code=429,
            )

        try:
            client = BidApiClient()

            # 行业编码映射
            api_industry_code = {"firstCodeList": ["0"], "secondCodeList": [], "thirdCodeList": []}
            if industry_code:
                if len(industry_code) == 1:
                    api_industry_code["firstCodeList"] = [industry_code]
                else:
                    api_industry_code["secondCodeList"] = [industry_code]

            # 地区编码映射
            api_area_code = {
                "proviceCodeList": [provice_code] if provice_code else ["0"],
                "cityCodeList": [city_code] if city_code else [],
                "countyCodeList": [county_code] if county_code else [],
            }

            api_resp = client.search_project(
                keyword=keyword or "",
                exclude_kw=exclude_keyword or "",
                include_kw=include_keyword or "",
                source_type=source_type or "",
                class_id=str(news_type_id) if news_type_id else "-100",
                project_class_id=project_class_id or "",
                search_mode=1,
                area_code=api_area_code,
                industry_code=api_industry_code,
                start_date=start_date or "",
                end_date=end_date or "",
                contract_end_min=contract_end_min or "",
                contract_end_max=contract_end_max or "",
                part_a_name=part_a_name or "",
                part_b_name=part_b_name or "",
                agent_name=agent_name or "",
                project_money_min=project_money_min,
                project_money_max=project_money_max,
                file_flag=file_flag if file_flag is not None else -1,
                purchase_type_id=purchase_type_id or "",
                page_id=page_number,
                page_number=items_per_page,
            )

            data = api_resp.get("data", {})
            items = data.get("data", [])
            api_total = data.get("total", 0)
            logging.info("Bid list [api-first]: page=%d, API returned %d items, total=%d",
                         page_number, len(items), api_total)

            # 异步缓存到 DB（fire-and-forget，不阻塞返回）
            threading.Thread(target=_cache_api_results, args=(items, data), daemon=True).start()

            return get_json_result(data={
                "projects": [_api_item_to_project(item, data) for item in items],
                "total": api_total,
            })
        except Exception as e:
            logging.warning("Bid list [api-first]: API failed (%s), fallback to DB cache: %s",
                             type(e).__name__, e)
            return get_json_result(data={"projects": objs, "total": total})

    # Step 3 (DB-first): 本地不足 20 条 → 调第三方 API 补充
    # 爬虫数据不需要外部 API 补充（source_type='crawler' 时跳过）
    if total < 20 and source_type != 'crawler':
        rl = check_bid_rate_limit("search")
        if rl:
            return get_data_error_result(
                message=f"请求过于频繁，请 {rl['retry_after']} 秒后重试",
                code=429,
            )

        logging.info("Bid list: local total=%d (<20), falling back to external API", total)
        try:
            client = BidApiClient()
            logging.info("Bid list: calling search_project API with keyword=%s, provice=%s, industry=%s, date=%s~%s",
                         keyword, provice_code, industry_code, start_date, end_date)

            # 行业编码映射
            api_industry_code = {"firstCodeList": ["0"], "secondCodeList": [], "thirdCodeList": []}
            if industry_code:
                if len(industry_code) == 1:
                    api_industry_code["firstCodeList"] = [industry_code]
                else:
                    api_industry_code["secondCodeList"] = [industry_code]

            # 地区编码映射
            api_area_code = {
                "proviceCodeList": [provice_code] if provice_code else ["0"],
                "cityCodeList": [city_code] if city_code else [],
                "countyCodeList": [county_code] if county_code else [],
            }

            api_resp = client.search_project(
                keyword=keyword or "",
                exclude_kw=exclude_keyword or "",
                include_kw=include_keyword or "",
                source_type=source_type or "",
                class_id=str(news_type_id) if news_type_id else "-100",
                project_class_id=project_class_id or "",
                search_mode=1,
                area_code=api_area_code,
                industry_code=api_industry_code,
                start_date=start_date or "",
                end_date=end_date or "",
                contract_end_min=contract_end_min or "",
                contract_end_max=contract_end_max or "",
                part_a_name=part_a_name or "",
                part_b_name=part_b_name or "",
                agent_name=agent_name or "",
                project_money_min=project_money_min,
                project_money_max=project_money_max,
                file_flag=file_flag if file_flag is not None else -1,
                purchase_type_id=purchase_type_id or "",
                page_id=1,
                page_number=50,
            )

            data = api_resp.get("data", {})
            items = data.get("data", [])
            logging.info("Bid list: API returned %d items, total=%s", len(items), data.get("total", "?"))
            _cache_api_results(items, data)

            # Step 4: 重新查 DB（DB-first 模式：API 补充后用 DB 排序返回）
            objs, total = BidProjectService.get_list(
                page_number=page_number,
                items_per_page=items_per_page,
                keyword=keyword,
                exclude_keyword=exclude_keyword,
                include_keyword=include_keyword,
                project_class_id=project_class_id,
                purchase_type_id=purchase_type_id,
                provice_code=provice_code,
                city_code=city_code,
                county_code=county_code,
                start_date=start_date,
                end_date=end_date,
                contract_end_min=contract_end_min,
                contract_end_max=contract_end_max,
                project_money_min=project_money_min,
                project_money_max=project_money_max,
                part_a_name=part_a_name,
                part_b_name=part_b_name,
                has_file=has_file,
                file_flag=file_flag,
                industry_code=industry_code,
                news_type_id=news_type_id,
                source_type=source_type,
            )
        except Exception as e:
            logging.warning("Bid list: API fallback FAILED — type=%s, message=%s", type(e).__name__, e)
            logging.warning("Bid list: returning %d local results (total=%d) after API failure", len(objs), total)

    return get_json_result(data={"projects": objs, "total": total})


# ---------------------------------------------------------------------------
# 标讯详情 — 缓存优先
#   1. 查 bid_project_detail 表
#   2. 无数据 → 调 getZTBProjectDetail → 存 DB → 返回
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/<int:project_id>/detail", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_bid_project_detail(project_id):
    # 参数
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存
    obj = BidProjectDetailService.get_or_none(project_id=project_id)
    if obj:
        return get_json_result(data=obj.to_dict())

    # Step 2: 爬虫项目无需外部 API（detail 已在 writer.write_detail 中写入）
    project = BidProjectService.get_by_project_id(project_id)
    if project and project.get("source_type") == "crawler":
        return get_json_result(data={
            "id": project_id,
            "content_html": "",
        })

    # Step 3: 调外部 API
    if not publish_time:
        return get_data_error_result(message="publish_time is required for first fetch.")

    try:
        client = BidApiClient()
        resp = client.get_detail(project_id, publish_time)
        data = resp.get("data", {})
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch detail: {e}")

    # Step 4: 解析并存入 DB
    files_raw = data.get("projectFiles") or []
    detail_data = {
        "id": project_id,
        "content_html": data.get("content", ""),
        "news_type_id": data.get("newsTypeID"),
        "project_class_name": data.get("projectClassName", ""),
        "purchase_type_id": data.get("purchaseType", ""),
        "industry_name": data.get("industryName", ""),
        "part_a_name": data.get("partAName", ""),
        "part_b_name": data.get("partBName", ""),
        "agent_name": data.get("agentName", ""),
        "project_money": data.get("projectMoney", ""),
        "provice_code": data.get("proviceCode", ""),
        "city_code": data.get("cityCode", ""),
        "county_code": data.get("countyCode", ""),
        "fetched_at": datetime.now(),
    }
    try:
        BidProjectDetailService.upsert_detail(project_id, detail_data)
    except Exception as e:
        logging.warning("Failed to cache project detail: %s", e)

    # 缓存附件（完整字段）
    for f in files_raw:
        try:
            BidProjectFileService.upsert_file({
                "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
                "project_id": project_id,
                "file_name": f.get("name", "") or f.get("fileName", ""),
                "file_url": f.get("url") or f.get("fileUrl") or "",
                "file_suffix": f.get("suffix", "") or f.get("fileSuffix", ""),
                "file_size": f.get("size") or f.get("fileSize"),
                "publish_time": _parse_dt(f.get("publishTime", "")),
            })
        except Exception as e:
            logging.warning("Failed to cache file: %s", e)

    return get_json_result(data=detail_data)


# ---------------------------------------------------------------------------
# 结构化数据 — 缓存优先
#   1. 查 bid_project_structure 表
#   2. 无数据 → 调 getZTBStructreDetail → 存 DB → 返回
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/<int:project_id>/structure", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_bid_project_structure(project_id):
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存
    obj = BidProjectStructureService.get_or_none(project_id=project_id)
    if obj:
        return get_json_result(data=obj.to_dict())

    # Step 2: 爬虫项目跳过外部 API
    project = BidProjectService.get_by_project_id(project_id)
    if project and project.get("source_type") == "crawler":
        return get_json_result(data={})

    # Step 3: 调外部 API
    if not publish_time:
        return get_data_error_result(message="publish_time is required for first fetch.")

    try:
        client = BidApiClient()
        resp = client.get_structure(project_id, publish_time)
        data = resp.get("data", {})
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch structure: {e}")

    # Step 3: 解析并存入 DB
    import json
    struct_data = {
        "id": project_id,
        "project_id": project_id,
        "project_name": data.get("projectName", ""),
        "project_numbers": json.dumps(data.get("projectNumber", []), ensure_ascii=False),
        "section_codes": json.dumps(data.get("projectSectionCode", []), ensure_ascii=False),
        "budget_money": json.dumps(data.get("budgetMoney", []), ensure_ascii=False),
        "bid_money": json.dumps(data.get("bidMoney", []), ensure_ascii=False),
        "bid_start_date": data.get("bidStartDate"),
        "bid_start_address": json.dumps(data.get("bidStartAddress", []), ensure_ascii=False),
        "sign_up_stop_date": data.get("siginUpStopDate"),
        "party_a_info": json.dumps(data.get("partyAInfo", []), ensure_ascii=False),
        "party_b_info": json.dumps(data.get("partyBInfo", []), ensure_ascii=False),
        "agency_info": json.dumps(data.get("agencyInfo", []), ensure_ascii=False),
        "bid_companies": json.dumps(data.get("bidCompany", []), ensure_ascii=False),
        "sbkj_bid_url": data.get("sbkjBidUrl", ""),
        "collect_url": data.get("collectUrl", ""),
        "fetched_at": datetime.now(),
    }
    try:
        BidProjectStructureService.upsert_structure(project_id, struct_data)
    except Exception as e:
        logging.warning("Failed to cache project structure: %s", e)

    return get_json_result(data=struct_data)


# ---------------------------------------------------------------------------
# 附件列表 — 缓存优先
#   1. 查 bid_project_file 表
#   2. 无数据 → 调 getZTBProjectFiles → 存 DB → 返回
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/<int:project_id>/files", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_bid_project_files(project_id):
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存（需有 file_url 才算有效缓存）
    cached = BidProjectFileService.get_by_project(project_id)
    logging.info("[files-endpoint] project=%s, cached count=%d, has_file_url=%s",
                 project_id, len(cached),
                 [{k: v for k, v in f.items() if k in ("file_name", "file_url")} for f in cached[:3]] if cached else [])
    if cached and any(f.get("file_url") for f in cached):
        return get_json_result(data={"files": cached})

    # Step 2: 爬虫项目跳过外部 API
    project = BidProjectService.get_by_project_id(project_id)
    if project and project.get("source_type") == "crawler":
        return get_json_result(data={"files": cached or []})

    # Step 3: 调外部 API
    if not publish_time:
        return get_data_error_result(message="publish_time is required for first fetch.")

    try:
        client = BidApiClient()
        files_raw = client.get_files(project_id, publish_time)
        logging.info("[files-endpoint] API files_raw count=%d, keys=%s, sample=%s",
                      len(files_raw),
                      list(files_raw[0].keys()) if files_raw else [],
                      [{k: v for k, v in f.items() if k in ("name", "url", "fileUrl", "suffix")} for f in files_raw[:3]] if files_raw else [])
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch files: {e}")

    # Step 3: 存入 DB
    for f in files_raw:
        try:
            BidProjectFileService.upsert_file({
                "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
                "project_id": project_id,
                "file_name": f.get("name", "") or f.get("fileName", ""),
                "file_url": f.get("url") or f.get("fileUrl") or "",
                "file_suffix": f.get("suffix", "") or f.get("fileSuffix", ""),
                "file_size": f.get("size") or f.get("fileSize"),
                "state": f.get("state", "0"),
                "publish_time": _parse_dt(f.get("publishTime", "")),
                "create_time": _parse_dt(f.get("createTime", "")),
                "fetched_at": datetime.now(),
            })
        except Exception as e:
            logging.warning("Failed to cache file: %s", e)

    # 统一转为 snake_case，与 DB 缓存格式一致
    files_normalized = []
    for f in files_raw:
        files_normalized.append({
            "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
            "project_id": project_id,
            "file_name": f.get("name", "") or f.get("fileName", ""),
            "file_url": f.get("url") or f.get("fileUrl") or "",
            "file_suffix": f.get("suffix", "") or f.get("fileSuffix", ""),
            "file_size": f.get("size") or f.get("fileSize"),
            "state": f.get("state", "0"),
            "publish_time": f.get("publishTime", ""),
            "create_time": f.get("createTime", ""),
        })

    return get_json_result(data={"files": files_normalized})


# ---------------------------------------------------------------------------
# 项目详情 v2（世舶直连网关，适用于合同/标讯/中标等所有类型）
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/<int:project_id>/detail-v2", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_bid_project_detail_v2(project_id):
    """获取项目详情（v2网关，缓存优先：DB → API → 存DB → 返回）

    query params:
        publish_time: 发布时间（必填，格式 YYYY-MM-DD HH:mm:ss）
    """
    publish_time = request.args.get("publish_time", "")
    if not publish_time:
        return get_data_error_result(message="publish_time is required")

    # Step 1: 查DB缓存
    cached_detail = BidProjectDetailService.get_or_none(project_id=project_id)
    cached_structure = BidProjectStructureService.get_or_none(project_id=project_id)
    now = datetime.now()

    detail_valid = (cached_detail and cached_detail.cache_expires_at
                    and cached_detail.cache_expires_at > now)
    structure_valid = (cached_structure and cached_structure.cache_expires_at
                       and cached_structure.cache_expires_at > now)

    # Helper: convert DB row → API-compatible dict for frontend
    def _detail_to_api(row) -> dict:
        if not row:
            return {}
        d = row.to_dict() if hasattr(row, 'to_dict') else row
        return {
            "title": d.get("part_a_name", "") or d.get("project_class_name", ""),
            "content": d.get("content_html", ""),
            "projectMoney": d.get("project_money", ""),
            "partAName": d.get("part_a_name", ""),
            "partBName": d.get("part_b_name", ""),
            "agentName": d.get("agent_name", ""),
            "industryName": d.get("industry_name", ""),
        }

    def _parse_json_field(val):
        """防御性解析：db 缓存可能由不同端点写入（json.dumps 字符串 vs 原始列表），
        统一返回 Python 列表/字典供 JSON 序列化。"""
        if val is None:
            return []
        if isinstance(val, (list, dict)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return val

    def _structure_to_api(row) -> dict:
        if not row:
            return {}
        d = row.to_dict() if hasattr(row, 'to_dict') else row
        return {
            "projectName": d.get("project_name", ""),
            "projectNumber": _parse_json_field(d.get("project_numbers")),
            "budgetMoney": _parse_json_field(d.get("budget_money")),
            "bidMoney": _parse_json_field(d.get("bid_money")),
            "bidStartDate": d.get("bid_start_date"),
            "bidStartAddress": _parse_json_field(d.get("bid_start_address")),
            "siginUpStopDate": d.get("sign_up_stop_date"),
            "partyAInfo": _parse_json_field(d.get("party_a_info")),
            "partyBInfo": _parse_json_field(d.get("party_b_info")),
            "agencyInfo": _parse_json_field(d.get("agency_info")),
            "bidCompany": _parse_json_field(d.get("bid_companies")),
            "sbkjBidUrl": d.get("sbkj_bid_url", ""),
            "collectUrl": d.get("collect_url", ""),
        }

    if detail_valid and structure_valid:
        logging.info("Bid detail-v2: cache hit for project %s", project_id)
        cached_files = BidProjectFileService.get_by_project(project_id)
        logging.info("[detail-v2] cached_files count=%d, sample=%s", len(cached_files),
                      [{k: v for k, v in f.items() if k in ("project_file_id", "file_name", "file_url")} for f in cached_files[:3]] if cached_files else [])
        return get_json_result(data={
            "content": {
                **_detail_to_api(cached_detail),
                "projectFiles": [
                    {"projectFileID": f["project_file_id"], "name": f["file_name"], "url": f.get("file_url") or ""}
                    for f in cached_files
                ],
            },
            "structure": _structure_to_api(cached_structure),
            "from_cache": True,
        })

    # Step 2: 调v2 API
    try:
        client = BidApiClient()
        content = client.get_detail_v2(project_id, publish_time)
        structure = client.get_structure_v2(project_id, publish_time)
    except BidApiError as e:
        # API失败 → 降级返回DB中已有数据（即使过期）
        if cached_detail:
            logging.warning("Bid detail-v2: API failed, falling back to stale cache for %s", project_id)
            cached_files = BidProjectFileService.get_by_project(project_id)
            return get_json_result(data={
                "content": {
                    **_detail_to_api(cached_detail),
                    "projectFiles": [
                        {"projectFileID": f["project_file_id"], "name": f["file_name"], "url": f.get("file_url") or ""}
                        for f in cached_files
                    ],
                },
                "structure": _structure_to_api(cached_structure),
                "from_cache": True,
                "stale": True,
            })
        return get_data_error_result(message=f"API error: {e}")
    except Exception as e:
        if cached_detail:
            cached_files = BidProjectFileService.get_by_project(project_id)
            return get_json_result(data={
                "content": {
                    **_detail_to_api(cached_detail),
                    "projectFiles": [
                        {"projectFileID": f["project_file_id"], "name": f["file_name"], "url": f.get("file_url") or ""}
                        for f in cached_files
                    ],
                },
                "structure": _structure_to_api(cached_structure),
                "from_cache": True,
                "stale": True,
            })
        logging.exception("Failed to get bid detail v2: %s", e)
        return get_data_error_result(message=f"Failed to get detail: {e}")

    # Step 3: 存入DB
    content_data = content.get("data", {})
    structure_data = structure.get("data", {})

    # Upsert detail
    detail_row = {
        "content_html": content_data.get("content", ""),
        "project_class_name": content_data.get("industryName", ""),
        "industry_name": content_data.get("industryName", ""),
        "part_a_name": content_data.get("partAName", ""),
        "part_b_name": content_data.get("partBName", ""),
        "agent_name": content_data.get("agentName", ""),
        "project_money": content_data.get("projectMoney", ""),
    }
    try:
        BidProjectDetailService.upsert_detail(project_id, detail_row)
    except Exception as e:
        logging.warning("Bid detail-v2: failed to cache detail for %s: %s", project_id, e)

    # Upsert structure
    struct_row = {
        "project_name": structure_data.get("projectName", ""),
        "project_numbers": structure_data.get("projectNumber", []),
        "budget_money": structure_data.get("budgetMoney", []),
        "bid_money": structure_data.get("bidMoney", []),
        "bid_start_date": structure_data.get("bidStartDate"),
        "bid_start_address": structure_data.get("bidStartAddress", []),
        "sign_up_stop_date": structure_data.get("siginUpStopDate"),
        "party_a_info": json.dumps(structure_data.get("partyAInfo", []), ensure_ascii=False),
        "party_b_info": json.dumps(structure_data.get("partyBInfo", []), ensure_ascii=False),
        "agency_info": json.dumps(structure_data.get("agencyInfo", []), ensure_ascii=False),
        "bid_companies": json.dumps(structure_data.get("bidCompany", []), ensure_ascii=False),
        "sbkj_bid_url": structure_data.get("sbkjBidUrl", ""),
        "collect_url": structure_data.get("collectUrl", ""),
    }
    try:
        BidProjectStructureService.upsert_structure(project_id, struct_row)
    except Exception as e:
        logging.warning("Bid detail-v2: failed to cache structure for %s: %s", project_id, e)

    # Upsert files — v2 projectFiles 可能不含 url，补调 v1 获取下载链接
    files_raw = (content_data.get("projectFiles") or
                 content_data.get("files") or [])
    _needs_v1_urls = files_raw and not any(f.get("url") or f.get("fileUrl") for f in files_raw)
    if _needs_v1_urls:
        try:
            v1_files = client.get_files(project_id, publish_time)
            v1_url_map = {}
            for vf in v1_files:
                vf_name = vf.get("name", "") or vf.get("fileName", "")
                vf_url = vf.get("url") or vf.get("fileUrl") or ""
                if vf_name and vf_url:
                    v1_url_map[vf_name] = vf_url
            if v1_url_map:
                for f in files_raw:
                    f_name = f.get("name", "") or f.get("fileName", "")
                    if f_name in v1_url_map:
                        f["url"] = v1_url_map[f_name]
                logging.info("[detail-v2] patched %d files with v1 urls", len(v1_url_map))
        except Exception as e:
            logging.warning("[detail-v2] v1 files fallback failed: %s", e)

    logging.info("[detail-v2] API files_raw count=%d, keys=%s, sample=%s",
                  len(files_raw),
                  list(files_raw[0].keys()) if files_raw else [],
                  [{k: v for k, v in f.items() if k in ("projectFileID", "name", "url", "fileUrl")} for f in files_raw[:3]] if files_raw else [])
    for f in files_raw:
        try:
            BidProjectFileService.upsert_file({
                "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
                "project_id": project_id,
                "file_name": f.get("name", "") or f.get("fileName", ""),
                "file_url": f.get("fileUrl", "") or f.get("url", ""),
                "publish_time": _parse_dt(f.get("publishTime", "")),
            })
        except Exception as e:
            logging.warning("Bid detail-v2: failed to cache file for %s: %s", project_id, e)

    return get_json_result(data={
        "content": content_data,
        "structure": structure_data,
        "from_cache": False,
    })


# ---------------------------------------------------------------------------
# 同步日志
# ---------------------------------------------------------------------------
@manager.route("/bid/sync-logs", methods=["GET"])  # noqa: F821
@login_required
def list_bid_sync_logs():
    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("items_per_page", 15))
    objs, total = BidSyncLogService.get_list(
        page_number=page_number, items_per_page=items_per_page
    )
    return get_json_result(data={"logs": objs, "total": total})


# ---------------------------------------------------------------------------
# 手动触发同步
# ---------------------------------------------------------------------------
@manager.route("/bid/trigger-sync", methods=["POST"])  # noqa: F821
@login_required
def trigger_bid_sync():
    """手动触发标讯同步（后台子进程执行，立即返回）"""
    import subprocess
    import sys as _sys

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.abspath(os.path.join(_script_dir, "..", "..", "..", ".."))
    _sync_script = os.path.join(_project_root, "rag", "svr", "bid_sync.py")

    try:
        subprocess.Popen(
            [_sys.executable, _sync_script],
            cwd=_project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return get_json_result(message="同步任务已启动，请稍后查看同步日志。")
    except Exception as e:
        return get_data_error_result(message=f"启动同步失败: {e}")


# ---------------------------------------------------------------------------
# 标讯解析 — 正文+附件导入知识库
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """去除HTML标签，提取纯文本"""
    import re
    # 移除script/style标签及其内容
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # 将块级元素替换为换行
    text = re.sub(r'</?(div|p|h[1-6]|tr|table|ul|ol|li|br|hr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    # 将其他标签移除
    text = re.sub(r'<[^>]+>', '', text)
    # 处理HTML实体
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    # 压缩多余空白
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _cache_api_results(items: list, api_data: dict, batch_id: str = None):
    """将 API 搜索结果异步缓存到 bid_project 表（fire-and-forget）"""
    if not items:
        return
    batch_id = batch_id or str(datetime.now().timestamp())
    for item in items:
        try:
            project_id = item.get("id")
            if not project_id:
                continue
            project_data = {
                "id": project_id,
                "title": _strip_html(item.get("title", "")),
                "title_html": item.get("title", ""),
                "content": item.get("content", ""),
                "publish_time": _parse_dt(item.get("publishTime")),
                "news_type_id": item.get("newsTypeID"),
                "project_class_id": str(item.get("projectClassID")) if item.get("projectClassID") else None,
                "purchase_type_id": str(item.get("purchaseTypeID")) if item.get("purchaseTypeID") else None,
                "project_money": item.get("projectMoney", ""),
                "provice_code": item.get("proviceCode", ""),
                "city_code": item.get("cityCode", ""),
                "county_code": item.get("countyCode", ""),
                "industry_codes": _safe_json(item.get("industryCodeList", [])),
                "part_a_names": _safe_json(item.get("partANameList", [])),
                "part_b_names": _safe_json(item.get("partBNameList", [])),
                "has_file": item.get("hasFile", 0),
                "contract_end_date": item.get("contractEndDate", ""),
                "se_keywords": api_data.get("seKeyWords", ""),
                "score": item.get("score"),
                "source_type": str(item.get("sourceType")) if item.get("sourceType") else None,
                "sync_batch_id": batch_id,
            }
            BidProjectService.upsert_project(project_data)
        except Exception as e:
            logging.warning("Failed to cache project %s: %s", item.get("id"), e)


def _api_item_to_project(item: dict, api_data: dict) -> dict:
    """将 API 搜索结果 item 转为 DB 格式（snake_case 字段名），与前端 BidProject 类型对齐"""
    return {
        "id": item.get("id"),
        "title": _strip_html(item.get("title", "")),
        "content": item.get("content", ""),
        "project_money": item.get("projectMoney", ""),
        "publish_time": _parse_dt(item.get("publishTime")),
        "part_a_names": _safe_json(item.get("partANameList", [])),
        "part_b_names": _safe_json(item.get("partBNameList", [])),
        "provice_code": item.get("proviceCode", ""),
        "city_code": item.get("cityCode", ""),
        "county_code": item.get("countyCode", ""),
        "project_class_id": str(item.get("projectClassID")) if item.get("projectClassID") else None,
        "purchase_type_id": str(item.get("purchaseTypeID")) if item.get("purchaseTypeID") else None,
        "news_type_id": item.get("newsTypeID"),
        "has_file": item.get("hasFile", 0),
        "contract_end_date": item.get("contractEndDate", ""),
        "se_keywords": api_data.get("seKeyWords", ""),
        "industry_codes": _safe_json(item.get("industryCodeList", [])),
        "source_type": str(item.get("sourceType")) if item.get("sourceType") else None,
        "score": item.get("score"),
    }


def _run_parse_task(project_id: int, kb_id: str, user_id: str):
    """后台线程：调用共享 tool service 完成文档导入+解析"""
    from api.utils.bid_tool_service import import_bid_to_kb

    project = BidProjectService.get_by_project_id(project_id)
    publish_time = str(project.get("publish_time", "") or "") if project else ""

    import_bid_to_kb(
        project_id=project_id,
        publish_time=publish_time,
        kb_id=kb_id,
        user_id=user_id,
    )


_DEFAULT_BID_KB_ID = "d23e0644578211f19c3bed5c593fe4c9"

@manager.route("/bid/projects/<int:project_id>/parse", methods=["POST"])  # noqa: F821
@login_required
async def bid_project_parse(project_id):
    """触发标书项目解析——正文+附件导入知识库"""
    body = await request.get_json() or {}
    kb_id = body.get("kb_id") or _DEFAULT_BID_KB_ID
    user_id = current_user.id

    # 检查是否已有解析进行中
    existing = BidProjectParseService.get_by_project(project_id)
    if existing and existing.get("status") == "parsing":
        return get_data_error_result(message="该表单已在进行解析，请等待完成")

    # 启动后台线程
    thread = threading.Thread(
        target=_run_parse_task,
        args=(project_id, kb_id, user_id),
        daemon=True,
    )
    thread.start()

    return get_json_result(data={
        "project_id": project_id,
        "kb_id": kb_id,
        "status": "pending",
    })


@manager.route("/bid/projects/<int:project_id>/parse-status", methods=["GET"])  # noqa: F821
@login_required
def bid_project_parse_status(project_id):
    """查询标书项目的解析进度"""
    record = BidProjectParseService.get_by_project(project_id)

    if not record:
        return get_json_result(data={
            "project_id": project_id,
            "status": "none",
            "progress": 0,
            "progress_msg": "",
            "kb_id": "",
            "combined_doc_id": "",
        })

    # 如果记录状态是parsing，检查一下KB中文档的实际状态
    status = record.get("status", "pending")
    progress = record.get("progress", 0)
    progress_msg = record.get("progress_msg", "")

    if status == "parsing" and record.get("kb_id"):
        try:
            # Use queued_doc_ids for project-specific filtering (avoid cross-project pollution)
            queued_doc_ids_str = record.get("queued_doc_ids")
            if queued_doc_ids_str:
                try:
                    doc_ids = json.loads(queued_doc_ids_str)
                except (json.JSONDecodeError, TypeError):
                    doc_ids = []
            else:
                doc_ids = []

            if doc_ids:
                done_count = 0
                fail_count = 0
                running_count = 0
                for doc_id in doc_ids:
                    e, doc_model = DocumentService.get_by_id(doc_id)
                    if e and doc_model:
                        r = doc_model.run
                        if r == "3" or r == 3:
                            done_count += 1
                        elif r == "4" or r == 4:
                            fail_count += 1
                        else:
                            running_count += 1
                    else:
                        fail_count += 1
                total = len(doc_ids)
                if running_count > 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"解析中 ({done_count}/{total} 已完成)"
                elif fail_count > 0 and running_count == 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"部分文档解析失败 ({fail_count}/{total})"
                    if done_count == 0:
                        status = "fail"
                elif done_count >= total and total > 0:
                    status = "done"
                    progress = 1
                    progress_msg = f"全部解析完成 ({total} 个文档)"
            else:
                # Fallback: no queued_doc_ids — query recent KB docs (backwards compatibility)
                docs, _ = DocumentService.get_by_kb_id(
                    kb_id=record["kb_id"], page_number=1, items_per_page=1000,
                    orderby="create_time", desc=True,
                    keywords="", run_status=[], types=[], suffix=[]
                )
                if docs:
                    running_count = sum(1 for d in docs if d.get("run") == "1")
                    done_count = sum(1 for d in docs if d.get("run") == "3")
                    fail_count = sum(1 for d in docs if d.get("run") == "4")
                    total = len(docs)
                    if running_count > 0:
                        avg_progress = sum(float(d.get("progress", 0)) for d in docs) / max(total, 1)
                        progress = 0.9 + 0.1 * avg_progress
                        progress_msg = f"解析中 ({done_count}/{total} 已完成)"
                    elif fail_count > 0 and running_count == 0:
                        status = "fail"
                        progress_msg = f"部分文档解析失败 ({fail_count}/{total})"
                    elif done_count >= total and total > 0:
                        status = "done"
                        progress = 1
                        progress_msg = f"全部解析完成 ({total} 个文档)"
        except Exception as e:
            logging.warning("Failed to query KB doc status: %s", e)

    return get_json_result(data={
        "project_id": project_id,
        "status": status,
        "progress": progress,
        "progress_msg": progress_msg,
        "kb_id": record.get("kb_id", ""),
        "combined_doc_id": record.get("combined_doc_id", ""),
    })
@manager.route("/bid/stats", methods=["GET"])  # noqa: F821
@login_required
def bid_stats():
    from api.db.db_models import BidProject
    from peewee import fn

    total_count = BidProject.select().count()
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_count = BidProject.select().where(
        BidProject.publish_time >= today_str
    ).count()
    has_file_count = BidProject.select().where(BidProject.has_file == 1).count()

    class_counts = {}
    rows = BidProject.select(
        BidProject.project_class_id,
        fn.COUNT(BidProject.id).alias("cnt")
    ).where(
        BidProject.project_class_id.is_null(False)
    ).group_by(BidProject.project_class_id).dicts()
    for r in rows:
        class_counts[r["project_class_id"]] = r["cnt"]

    return get_json_result(data={
        "total": total_count,
        "today": today_count,
        "has_file": has_file_count,
        "by_class": class_counts,
    })


# ---------------------------------------------------------------------------
# 采集源网址
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/<int:project_id>/collect-url", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_bid_project_collect_url(project_id):
    # Step 1: Check if this is a crawler project — return URL from DB
    project = BidProjectService.get_by_project_id(project_id)
    if project and project.get("source_type") == "crawler":
        struct = BidProjectStructureService.get_or_none(project_id=project_id)
        url = struct.to_dict().get("collect_url", "") if struct else ""
        return get_json_result(data={"url": url})

    # Step 2: Fall through to external API for non-crawler projects
    publish_time = request.args.get("publish_time", "")
    if not publish_time:
        return get_data_error_result(message="publish_time is required")
    try:
        client = BidApiClient()
        url = client.get_collect_url(project_id, publish_time)
        return get_json_result(data={"url": url})
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch collect url: {e}")


# ---------------------------------------------------------------------------
# 项目编号查询
# ---------------------------------------------------------------------------
@manager.route("/bid/projects/by-number", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("search")
async def search_project_by_number():
    project_number = request.args.get("project_number", "")
    publish_time = request.args.get("publish_time", "")
    if not project_number:
        return get_data_error_result(message="project_number is required")
    try:
        client = BidApiClient()
        projects = client.get_project_by_number(project_number, publish_time)
        return get_json_result(data={"projects": projects})
    except Exception as e:
        return get_data_error_result(message=f"Failed to search by number: {e}")


# ---------------------------------------------------------------------------
# 合同数据搜索（v2 网关）
# ---------------------------------------------------------------------------
@manager.route("/bid/contracts", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("search")
async def list_bid_contracts():
    """合同搜索（v2网关，缓存优先：DB → API补充 → 存DB → 返回）

    策略：
      1. 先查DB（news_type_id=3，缓存未过期）
      2. DB够 → 直接返回（免费）
      3. DB不够 → 调API → id去重upsert → 再查DB → 返回
      4. API失败 → 降级返回DB已有数据
    """
    page_id = int(request.args.get("page", 1))
    page_number = int(request.args.get("items_per_page", 20))
    keyword = request.args.get("keyword", "") or None
    exclude_kw = request.args.get("exclude_keyword", "") or None
    include_kw = request.args.get("include_keyword", "") or None
    provice_code = request.args.get("provice_code", "") or None
    city_code = request.args.get("city_code", "") or None
    start_date = request.args.get("start_date", "") or None
    end_date = request.args.get("end_date", "") or None
    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    contract_end_min = request.args.get("contract_end_min", "") or None
    contract_end_max = request.args.get("contract_end_max", "") or None
    part_a_name = request.args.get("part_a_name", "") or None
    part_b_name = request.args.get("part_b_name", "") or None
    agent_name = request.args.get("agent_name", "") or None
    project_money_min = request.args.get("project_money_min", "") or None
    project_money_max = request.args.get("project_money_max", "") or None
    file_flag = request.args.get("file_flag", type=int) or None
    industry_code = request.args.get("industry_code", "") or None
    purchase_type_id = request.args.get("purchase_type_id", "") or None

    def _db_to_contract(row: dict) -> dict:
        """Convert bid_project DB row → contract API response format."""
        raw = row.get("raw_json") or {}
        if raw:
            return raw
        # Fallback: reconstruct from DB fields
        return {
            "id": row["id"],
            "title": row.get("title_html") or row.get("title", ""),
            "publishTime": str(row.get("publish_time") or ""),
            "projectMoney": row.get("project_money") or "",
            "hasFile": row.get("has_file") or 0,
            "projectCycle": [],
            "partAInfo": [{"name": n, "contactPhone": []} for n in (row.get("part_a_names") or [])],
            "partBInfo": [{"name": n, "contactPhone": []} for n in (row.get("part_b_names") or [])],
            "contractStartDate": "",
            "contractEndDate": row.get("contract_end_date") or "",
        }

    # Step 1: 查DB缓存
    db_objs, db_total = BidProjectService.get_list(
        page_number=page_id,
        items_per_page=page_number,
        keyword=keyword,
        include_keyword=include_kw,
        exclude_keyword=exclude_kw,
        purchase_type_id=purchase_type_id,
        provice_code=provice_code,
        city_code=city_code,
        start_date=start_date,
        end_date=end_date,
        contract_end_min=contract_end_min,
        contract_end_max=contract_end_max,
        part_a_name=part_a_name,
        part_b_name=part_b_name,
        has_file=file_flag if file_flag is not None else None,
        industry_code=industry_code,
        news_type_id=3,  # 合同类型
    )

    # 过滤过期缓存
    now = datetime.now()
    valid_objs = [o for o in db_objs
                  if not o.get("cache_expires_at") or o["cache_expires_at"] > now]
    valid_total = len(valid_objs)

    # Step 2: DB够 → 直接返回
    if valid_total >= page_number:
        logging.info("Bid contracts: cache hit, returning %d results", valid_total)
        return get_json_result(data={
            "contracts": [_db_to_contract(o) for o in valid_objs[:page_number]],
            "total": db_total,  # DB total 作为近似值
        })

    # Step 3: DB不够 → 调API补充
    logging.info("Bid contracts: cache insufficient (%d < %d), calling API", valid_total, page_number)

    # Rate limit
    rl = check_bid_rate_limit("search")
    if rl:
        if valid_total > 0:
            return get_json_result(data={
                "contracts": [_db_to_contract(o) for o in valid_objs],
                "total": valid_total,
                "from_cache": True,
            })
        return get_data_error_result(
            message=f"请求过于频繁，请 {rl['retry_after']} 秒后重试",
            code=429,
        )

    try:
        client = BidApiClient()

        api_industry_code = {"firstCodeList": ["0"], "secondCodeList": [], "thirdCodeList": []}
        if industry_code:
            if len(industry_code) == 1:
                api_industry_code["firstCodeList"] = [industry_code]
            else:
                api_industry_code["secondCodeList"] = [industry_code]

        api_area_code = {
            "proviceCodeList": [provice_code] if provice_code else ["0"],
            "cityCodeList": [city_code] if city_code else [],
            "countyCodeList": [],
        }

        resp = client.search_contract(
            keyword=keyword or "",
            exclude_kw=exclude_kw or "",
            include_kw=include_kw or "",
            area_code=api_area_code,
            industry_code=api_industry_code,
            start_date=start_date or "",
            end_date=end_date or "",
            contract_end_min=contract_end_min or "",
            contract_end_max=contract_end_max or "",
            part_a_name=part_a_name or "",
            part_b_name=part_b_name or "",
            agent_name=agent_name or "",
            project_money_min=project_money_min or "",
            project_money_max=project_money_max or "",
            file_flag=file_flag if file_flag is not None else -1,
            purchase_type_id=purchase_type_id or "",
            page_id=page_id,
            page_number=page_number,
        )
        data = resp.get("data", {})
        items = data.get("data", []) or []
        api_total = data.get("total", 0)

        # Step 4: 逐条upsert到DB（id去重）
        upserted = 0
        for item in items:
            try:
                BidProjectService.upsert_contract(item, keyword=keyword or "")
                upserted += 1
            except Exception as e:
                logging.warning("Bid contracts: failed to upsert item %s: %s", item.get("id"), e)
        logging.info("Bid contracts: upserted %d/%d items to DB", upserted, len(items))

        return get_json_result(data={"contracts": items, "total": api_total})

    except BidApiError as e:
        # API失败 → 降级返回DB数据
        if valid_objs:
            logging.warning("Bid contracts: API failed, falling back to DB cache. error=%s", e)
            return get_json_result(data={
                "contracts": [_db_to_contract(o) for o in valid_objs[:page_number]],
                "total": len(valid_objs),
                "from_cache": True,
                "stale": True,
            })
        return get_data_error_result(message=f"API error: {e}")
    except Exception as e:
        if valid_objs:
            logging.warning("Bid contracts: unexpected error, falling back to DB. error=%s", e)
            return get_json_result(data={
                "contracts": [_db_to_contract(o) for o in valid_objs[:page_number]],
                "total": len(valid_objs),
                "from_cache": True,
                "stale": True,
            })
        logging.exception("Bid contracts search failed: %s", e)
        return get_data_error_result(message=f"Failed to search contracts: {e}")


# ---------------------------------------------------------------------------
# 企业画像（v2 网关）
# ---------------------------------------------------------------------------
@manager.route("/bid/enterprises/profile", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("enterprise")
async def get_enterprise_profile():
    company_name = request.args.get("company_name", "")
    if not company_name:
        return get_data_error_result(message="company_name is required")
    try:
        result = get_enterprise_profile_cached(company_name)
        return get_json_result(data=result.get("data", {}))
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch enterprise profile: {e}")


@manager.route("/bid/enterprises/contacts", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("enterprise")
async def get_enterprise_contacts():
    company_name = request.args.get("company_name", "")
    page_no = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 5))
    if not company_name:
        return get_data_error_result(message="company_name is required")
    try:
        result = get_enterprise_contacts_cached(company_name, page_no, min(page_size, 5))
        return get_json_result(data=result.get("data", {}))
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch enterprise contacts: {e}")


@manager.route("/bid/enterprises/customers", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("enterprise")
async def get_enterprise_customers():
    company_name = request.args.get("company_name", "")
    page_no = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    if not company_name:
        return get_data_error_result(message="company_name is required")
    try:
        result = get_enterprise_customers_cached(company_name, page_no, min(page_size, 20))
        return get_json_result(data=result.get("data", {}))
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch enterprise customers: {e}")


@manager.route("/bid/enterprises/suppliers", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("enterprise")
async def get_enterprise_suppliers():
    company_name = request.args.get("company_name", "")
    page_no = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    if not company_name:
        return get_data_error_result(message="company_name is required")
    try:
        result = get_enterprise_suppliers_cached(company_name, page_no, min(page_size, 20))
        return get_json_result(data=result.get("data", {}))
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch enterprise suppliers: {e}")


# ---------------------------------------------------------------------------
# 拟在建项目（v2 网关）
# ---------------------------------------------------------------------------
@manager.route("/bid/construction/projects", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("search")
async def list_construction_projects():
    page_id = int(request.args.get("page", 1))
    page_number = int(request.args.get("items_per_page", 20))
    keyword = request.args.get("keyword", "") or None
    provice_code = request.args.get("provice_code", "") or None
    city_code = request.args.get("city_code", "") or None
    start_date = request.args.get("start_date", "") or None
    end_date = request.args.get("end_date", "") or None

    try:
        client = BidApiClient()

        api_area_code = {
            "proviceCodeList": [provice_code] if provice_code else ["0"],
            "cityCodeList": [city_code] if city_code else [],
            "countyCodeList": [],
        }

        resp = client.search_nzj_project(
            keyword=keyword or "",
            area_code=api_area_code,
            start_date=start_date or "",
            end_date=end_date or "",
            page_id=page_id,
            page_number=page_number,
        )
        data = resp.get("data", {})
        items = data.get("data", []) or []
        total = data.get("total", 0)
        return get_json_result(data={"projects": items, "total": total})
    except Exception as e:
        logging.warning("Construction projects search failed: %s", e)
        return get_data_error_result(message=f"Failed to search construction projects: {e}")


@manager.route("/bid/construction/projects/<int:project_id>/detail", methods=["GET"])  # noqa: F821
@login_required
@bid_rate_limit("detail")
async def get_construction_project_detail(project_id):
    publish_time = request.args.get("publish_time", "")
    if not publish_time:
        return get_data_error_result(message="publish_time is required")
    try:
        from api.utils.bid_tool_service import get_construction_detail_cached
        data = get_construction_detail_cached(project_id, publish_time)
        return get_json_result(data=data)
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch construction project detail: {e}")


_CONSTRUCTION_KB_ID = "30eb6240679b11f1a8f13fbdf025dd68"


@manager.route("/bid/construction/projects/<int:project_id>/parse", methods=["POST"])  # noqa: F821
@login_required
async def construction_project_parse(project_id):
    """触发拟在建项目解析——正文+附件导入知识库"""
    req = await request.get_json()
    publish_time = req.get("publish_time", "") if req else ""
    user_id = current_user.id

    existing = BidConstructionParseService.get_by_project(project_id)
    if existing and existing.get("status") == "parsing":
        return get_data_error_result(message="该项目已在进行解析，请等待完成")

    from api.utils.bid_tool_service import import_construction_to_kb

    result = import_construction_to_kb(
        project_id=project_id,
        publish_time=publish_time,
        kb_id=_CONSTRUCTION_KB_ID,
        user_id=user_id,
    )

    if result.get("status") == "fail":
        return get_data_error_result(message=result.get("message", "Import failed"))

    return get_json_result(data={
        "project_id": project_id,
        "kb_id": _CONSTRUCTION_KB_ID,
        "status": result.get("status", "pending"),
    })


@manager.route("/bid/construction/projects/<int:project_id>/parse-status", methods=["GET"])  # noqa: F821
@login_required
def construction_project_parse_status(project_id):
    """查询拟在建项目的解析进度"""
    record = BidConstructionParseService.get_by_project(project_id)

    if not record:
        return get_json_result(data={
            "project_id": project_id,
            "status": "none",
            "progress": 0,
            "progress_msg": "",
            "kb_id": "",
            "combined_doc_id": "",
        })

    status = record.get("status", "pending")
    progress = record.get("progress", 0)
    progress_msg = record.get("progress_msg", "")

    if status == "parsing" and record.get("kb_id"):
        try:
            queued_doc_ids_str = record.get("queued_doc_ids")
            if queued_doc_ids_str:
                try:
                    doc_ids = json.loads(queued_doc_ids_str)
                except (json.JSONDecodeError, TypeError):
                    doc_ids = []
            else:
                doc_ids = []

            if doc_ids:
                done_count = 0
                fail_count = 0
                running_count = 0
                for doc_id in doc_ids:
                    e, doc_model = DocumentService.get_by_id(doc_id)
                    if e and doc_model:
                        r = doc_model.run
                        if r == "3" or r == 3:
                            done_count += 1
                        elif r == "4" or r == 4:
                            fail_count += 1
                        else:
                            running_count += 1
                    else:
                        fail_count += 1
                total = len(doc_ids)
                if running_count > 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"解析中 ({done_count}/{total} 已完成)"
                elif fail_count > 0 and running_count == 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"部分文档解析失败 ({fail_count}/{total})"
                    if done_count == 0:
                        status = "fail"
                elif done_count >= total and total > 0:
                    status = "done"
                    progress = 1
                    progress_msg = f"全部解析完成 ({total} 个文档)"
        except Exception as e:
            logging.warning("Failed to query construction KB doc status: %s", e)

    return get_json_result(data={
        "project_id": project_id,
        "status": status,
        "progress": progress,
        "progress_msg": progress_msg,
        "kb_id": record.get("kb_id", ""),
        "combined_doc_id": record.get("combined_doc_id", ""),
    })


# ---------------------------------------------------------------------------
# Contract parse endpoints
# ---------------------------------------------------------------------------

_CONTRACT_KB_ID = "c1afe066679c11f1a8f13fbdf025dd68"


@manager.route("/bid/contracts/<int:project_id>/parse", methods=["POST"])
@login_required
async def contract_project_parse(project_id):
    """触发合同项目解析——正文+附件导入知识库"""
    user_id = current_user.id
    body = await request.get_json() or {}
    publish_time = body.get("publish_time", "")

    existing = BidContractParseService.get_by_project(project_id)
    if existing and existing.get("status") == "parsing":
        return get_data_error_result(message="该项目已在进行解析，请等待完成")

    from api.utils.bid_tool_service import import_contract_to_kb

    result = import_contract_to_kb(
        project_id=project_id,
        publish_time=publish_time,
        kb_id=_CONTRACT_KB_ID,
        user_id=user_id,
    )

    if result.get("status") == "fail":
        return get_data_error_result(message=result.get("message", "Import failed"))

    return get_json_result(data={
        "project_id": project_id,
        "kb_id": _CONTRACT_KB_ID,
        "status": result.get("status", "pending"),
    })


@manager.route("/bid/contracts/<int:project_id>/parse-status", methods=["GET"])
@login_required
def contract_project_parse_status(project_id):
    """查询合同项目的解析进度"""
    record = BidContractParseService.get_by_project(project_id)

    if not record:
        return get_json_result(data={
            "project_id": project_id,
            "status": "none",
            "progress": 0,
            "progress_msg": "",
            "kb_id": "",
            "combined_doc_id": "",
        })

    status = record.get("status", "pending")
    progress = record.get("progress", 0)
    progress_msg = record.get("progress_msg", "")

    if status == "parsing" and record.get("kb_id"):
        try:
            queued_doc_ids_str = record.get("queued_doc_ids")
            if queued_doc_ids_str:
                try:
                    doc_ids = json.loads(queued_doc_ids_str)
                except (json.JSONDecodeError, TypeError):
                    doc_ids = []
            else:
                doc_ids = []

            if doc_ids:
                done_count = 0
                fail_count = 0
                running_count = 0
                for doc_id in doc_ids:
                    e, doc_model = DocumentService.get_by_id(doc_id)
                    if e and doc_model:
                        r = doc_model.run
                        if r == "3" or r == 3:
                            done_count += 1
                        elif r == "4" or r == 4:
                            fail_count += 1
                        else:
                            running_count += 1
                    else:
                        fail_count += 1
                total = len(doc_ids)
                if running_count > 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"解析中 ({done_count}/{total} 已完成)"
                elif fail_count > 0 and running_count == 0:
                    progress = 0.9 + 0.1 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"部分文档解析失败 ({fail_count}/{total})"
                    if done_count == 0:
                        status = "fail"
                elif done_count >= total and total > 0:
                    status = "done"
                    progress = 1
                    progress_msg = f"全部解析完成 ({total} 个文档)"
        except Exception as e:
            logging.warning("Failed to query contract KB doc status: %s", e)

    return get_json_result(data={
        "project_id": project_id,
        "status": status,
        "progress": progress,
        "progress_msg": progress_msg,
        "kb_id": record.get("kb_id", ""),
        "combined_doc_id": record.get("combined_doc_id", ""),
    })


# ---------------------------------------------------------------------------
# Enterprise parse endpoints
# ---------------------------------------------------------------------------

_ENTERPRISE_KB_ID = "afd3e892679c11f1a8f13fbdf025dd68"


@manager.route("/bid/enterprises/parse", methods=["POST"])
@login_required
async def enterprise_parse():
    """触发企业档案解析——导入知识库"""
    user_id = current_user.id
    body = await request.get_json() or {}
    company_name = body.get("company_name", "")
    if not company_name:
        return get_data_error_result(message="company_name is required")

    existing = BidEnterpriseParseService.get_by_company(company_name)
    if existing and existing.get("status") == "parsing":
        return get_data_error_result(message="该企业已在进行解析，请等待完成")

    from api.utils.bid_tool_service import import_enterprise_to_kb

    result = import_enterprise_to_kb(
        company_name=company_name,
        kb_id=_ENTERPRISE_KB_ID,
        user_id=user_id,
    )

    if result.get("status") == "fail":
        return get_data_error_result(message=result.get("message", "Import failed"))

    return get_json_result(data={
        "company_name": company_name,
        "kb_id": _ENTERPRISE_KB_ID,
        "status": result.get("status", "pending"),
    })


@manager.route("/bid/enterprises/parse-status", methods=["GET"])
@login_required
def enterprise_parse_status():
    """查询企业档案的解析进度"""
    company_name = request.args.get("company_name", "")
    if not company_name:
        return get_data_error_result(message="company_name is required")

    record = BidEnterpriseParseService.get_by_company(company_name)

    if not record:
        return get_json_result(data={
            "company_name": company_name,
            "status": "none",
            "progress": 0,
            "progress_msg": "",
            "kb_id": "",
            "combined_doc_id": "",
        })

    status = record.get("status", "pending")
    progress = record.get("progress", 0)
    progress_msg = record.get("progress_msg", "")

    if status == "parsing" and record.get("kb_id"):
        try:
            queued_doc_ids_str = record.get("queued_doc_ids")
            if queued_doc_ids_str:
                try:
                    doc_ids = json.loads(queued_doc_ids_str)
                except (json.JSONDecodeError, TypeError):
                    doc_ids = []
            else:
                doc_ids = []

            if doc_ids:
                done_count = 0
                fail_count = 0
                running_count = 0
                for doc_id in doc_ids:
                    e, doc_model = DocumentService.get_by_id(doc_id)
                    if e and doc_model:
                        r = doc_model.run
                        if r == "3" or r == 3:
                            done_count += 1
                        elif r == "4" or r == 4:
                            fail_count += 1
                        else:
                            running_count += 1
                    else:
                        fail_count += 1
                total = len(doc_ids)
                if running_count > 0:
                    progress = 0.5 + 0.5 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"解析中 ({done_count}/{total} 已完成)"
                elif fail_count > 0 and running_count == 0:
                    progress = 0.5 + 0.5 * (done_count + fail_count) / max(total, 1)
                    progress_msg = f"部分文档解析失败 ({fail_count}/{total})"
                    if done_count == 0:
                        status = "fail"
                elif done_count >= total and total > 0:
                    status = "done"
                    progress = 1
                    progress_msg = f"全部解析完成 ({total} 个文档)"
        except Exception as e:
            logging.warning("Failed to query enterprise KB doc status: %s", e)

    return get_json_result(data={
        "company_name": company_name,
        "status": status,
        "progress": progress,
        "progress_msg": progress_msg,
        "kb_id": record.get("kb_id", ""),
        "combined_doc_id": record.get("combined_doc_id", ""),
    })
