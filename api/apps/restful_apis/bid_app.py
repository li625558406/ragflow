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
from datetime import datetime

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.services.bid_service import (
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
from api.utils.bid_api_client import BidApiClient

manager = Blueprint("rest_bid_app", __name__)


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

    # Step 1: 查本地 DB
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

    # Step 2: 本地不足 20 条 → 调第三方 API 补充
    if total < 20:
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
            batch_id = str(datetime.now().timestamp())

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
                        "se_keywords": data.get("seKeyWords", ""),
                        "score": item.get("score"),
                        "source_type": str(item.get("sourceType")) if item.get("sourceType") else None,
                        "sync_batch_id": batch_id,
                    }
                    BidProjectService.upsert_project(project_data)
                except Exception as e:
                    logging.warning("Failed to cache project %s: %s", item.get("id"), e)

            # Step 3: 重新查 DB
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
def get_bid_project_detail(project_id):
    # 参数
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存
    obj = BidProjectDetailService.get_or_none(project_id=project_id)
    if obj:
        return get_json_result(data=obj.to_dict())

    # Step 2: 调外部 API
    if not publish_time:
        return get_data_error_result(message="publish_time is required for first fetch.")

    try:
        client = BidApiClient()
        resp = client.get_detail(project_id, publish_time)
        data = resp.get("data", {})
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch detail: {e}")

    # Step 3: 解析并存入 DB
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

    # 缓存附件
    for f in files_raw:
        try:
            BidProjectFileService.upsert_file({
                "project_file_id": f.get("projectFileID"),
                "project_id": project_id,
                "file_name": f.get("name", ""),
                "publish_time": f.get("publishTime", ""),
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
def get_bid_project_structure(project_id):
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存
    obj = BidProjectStructureService.get_or_none(project_id=project_id)
    if obj:
        return get_json_result(data=obj.to_dict())

    # Step 2: 调外部 API
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
def get_bid_project_files(project_id):
    publish_time = request.args.get("publish_time", "")

    # Step 1: 查缓存（需有 file_url 才算有效缓存）
    cached = BidProjectFileService.get_by_project(project_id)
    if cached and any(f.get("file_url") for f in cached):
        return get_json_result(data={"files": cached})

    # Step 2: 调外部 API
    if not publish_time:
        return get_data_error_result(message="publish_time is required for first fetch.")

    try:
        client = BidApiClient()
        files_raw = client.get_files(project_id, publish_time)
    except Exception as e:
        return get_data_error_result(message=f"Failed to fetch files: {e}")

    # Step 3: 存入 DB
    for f in files_raw:
        try:
            BidProjectFileService.upsert_file({
                "project_file_id": f.get("projectFileID"),
                "project_id": project_id,
                "file_name": f.get("name", ""),
                "file_url": f.get("url", ""),
                "file_suffix": f.get("suffix", ""),
                "file_size": f.get("size"),
                "state": f.get("state", "0"),
                "publish_time": f.get("publishTime", ""),
                "create_time": f.get("createTime", ""),
                "fetched_at": datetime.now(),
            })
        except Exception as e:
            logging.warning("Failed to cache file: %s", e)

    return get_json_result(data={"files": files_raw})


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


@manager.route("/bid/projects/<int:project_id>/parse", methods=["POST"])  # noqa: F821
@login_required
async def bid_project_parse(project_id):
    """触发标书项目解析——正文+附件导入知识库"""
    req = await request.get_json()
    kb_id = req.get("kb_id") if req else None
    if not kb_id:
        return get_data_error_result(message="kb_id is required")

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
                    # 计算平均进度
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
