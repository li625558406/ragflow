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
import tempfile
import threading
from datetime import datetime
from io import BytesIO

from quart import request
from werkzeug.datastructures import FileStorage

from api.apps import current_user, login_required
from api.db.services.bid_service import (
    BidProjectService,
    BidProjectDetailService,
    BidProjectStructureService,
    BidProjectFileService,
    BidProjectParseService,
    BidSyncLogService,
)
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.file_service import FileService
from api.db.services.document_service import DocumentService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
)
from api.utils.bid_api_client import BidApiClient
from api.utils.bid_file_utils import download_file, extract_archive


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
# 标讯列表（纯本地查询，不调外部 API）
# ---------------------------------------------------------------------------
@manager.route("/bid/projects", methods=["GET"])  # noqa: F821
@login_required
def list_bid_projects():
    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("items_per_page", 20))
    keyword = request.args.get("keyword", "") or None
    project_class_id = request.args.get("project_class_id", "") or None
    purchase_type_id = request.args.get("purchase_type_id", "") or None
    provice_code = request.args.get("provice_code", "") or None
    city_code = request.args.get("city_code", "") or None
    start_date = request.args.get("start_date", "") or None
    end_date = request.args.get("end_date", "") or None
    project_money_min = request.args.get("project_money_min", type=int) or None
    project_money_max = request.args.get("project_money_max", type=int) or None
    part_a_name = request.args.get("part_a_name", "") or None
    part_b_name = request.args.get("part_b_name", "") or None
    has_file = request.args.get("has_file", type=int) or None
    industry_code = request.args.get("industry_code", "") or None

    objs, total = BidProjectService.get_list(
        page_number=page_number,
        items_per_page=items_per_page,
        keyword=keyword,
        project_class_id=project_class_id,
        purchase_type_id=purchase_type_id,
        provice_code=provice_code,
        city_code=city_code,
        start_date=start_date,
        end_date=end_date,
        project_money_min=project_money_min,
        project_money_max=project_money_max,
        part_a_name=part_a_name,
        part_b_name=part_b_name,
        has_file=has_file,
        industry_code=industry_code,
    )
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


def _build_combined_text(detail: dict, structure: dict) -> str:
    """将正文和结构化数据拼接成纯文本（txt格式，便于RAGFlow解析）"""
    parts = []

    # 正文内容
    if detail and detail.get("content_html"):
        text = _strip_html(detail["content_html"])
        if text:
            parts.append(text)

    # 结构化数据
    if structure:
        parts.append('\n\n========== 结构化数据 ==========\n')

        fields = [
            ("项目名称", structure.get("project_name")),
            ("项目编号", _json_display(structure.get("project_numbers"))),
            ("标段编码", _json_display(structure.get("section_codes"))),
            ("预算金额", _json_display(structure.get("budget_money"))),
            ("中标金额", _json_display(structure.get("bid_money"))),
            ("开标日期", structure.get("bid_start_date")),
            ("开标地址", _json_display(structure.get("bid_start_address"))),
            ("报名截止日期", structure.get("sign_up_stop_date")),
            ("甲方信息", _json_display(structure.get("party_a_info"))),
            ("乙方信息", _json_display(structure.get("party_b_info"))),
            ("代理机构", _json_display(structure.get("agency_info"))),
            ("投标企业", _json_display(structure.get("bid_companies"))),
        ]
        for label, value in fields:
            if value:
                parts.append(f'{label}：{value}')

    return '\n'.join(parts)


def _json_display(raw: str | None) -> str:
    """将JSON字符串格式化为可读文本"""
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return ", ".join(
                item.get("name", str(item)) if isinstance(item, dict) else str(item)
                for item in parsed
            )
        return str(parsed)
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def _run_parse_task(project_id: int, kb_id: str, user_id: str):
    """后台线程：拼接文档+下载附件+上传KB+触发解析"""
    try:
        BidProjectParseService.upsert({
            "project_id": project_id,
            "kb_id": kb_id,
            "status": "parsing",
            "progress": 0,
            "progress_msg": "正在准备数据...",
        })

        # 获取知识库
        kb = KnowledgebaseService.get_or_none(id=kb_id, tenant_id=user_id)
        if not kb:
            BidProjectParseService.upsert({
                "project_id": project_id,
                "status": "fail",
                "progress_msg": "知识库不存在或无权访问",
            })
            return

        parent_path = f"bid_project_{project_id}"

        # 获取项目基本信息（需要publish_time来调外部API）
        project = BidProjectService.get_by_project_id(project_id)
        publish_time = project.get("publish_time", "") if project else ""

        # 获取详模块 — 缓存优先
        detail_obj = BidProjectDetailService.get_or_none(project_id=project_id)
        if not detail_obj and publish_time:
            try:
                client = BidApiClient()
                resp = client.get_detail(project_id, publish_time)
                data = resp.get("data", {})
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
                BidProjectDetailService.upsert_detail(project_id, detail_data)
                detail_obj = BidProjectDetailService.get_or_none(project_id=project_id)
            except Exception as e:
                logging.warning("Failed to fetch detail for parse: %s", e)

        # 获取结构化数据 — 缓存优先
        structure_obj = BidProjectStructureService.get_or_none(project_id=project_id)
        if not structure_obj and publish_time:
            try:
                client = BidApiClient()
                resp = client.get_structure(project_id, publish_time)
                data = resp.get("data", {})
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
                BidProjectStructureService.upsert_structure(project_id, struct_data)
                structure_obj = BidProjectStructureService.get_or_none(project_id=project_id)
            except Exception as e:
                logging.warning("Failed to fetch structure for parse: %s", e)

        detail = detail_obj.to_dict() if detail_obj else None
        structure = structure_obj.to_dict() if structure_obj else None

        # 1. 拼接文本并上传
        combined_text = _build_combined_text(detail, structure)
        BidProjectParseService.upsert({
            "project_id": project_id,
            "progress": 0.1,
            "progress_msg": "正在上传项目文档...",
        })

        combined_doc_id = None
        try:
            file_obj = FileStorage(
                stream=BytesIO(combined_text.encode("utf-8")),
                filename=f"project_{project_id}_content.txt",
                content_type="text/plain",
            )
            kb.files = [file_obj]
            FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
            # 获取最后创建的文档ID（upload_document内部生成了doc_id）
            if hasattr(file_obj, "id"):
                combined_doc_id = file_obj.id
        except Exception as e:
            logging.warning("Failed to upload combined HTML: %s", e)

        # 2. 下载并上传附件 — 缓存优先
        files = BidProjectFileService.get_by_project(project_id)
        if (not files or not any(f.get("file_url") for f in files)) and publish_time:
            try:
                client = BidApiClient()
                files_raw = client.get_files(project_id, publish_time)
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
                files = BidProjectFileService.get_by_project(project_id)
            except Exception as e:
                logging.warning("Failed to fetch files for parse: %s", e)
        if not files:
            files = []
        total = len([f for f in files if f.get("file_url")])
        uploaded = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            for f in files:
                url = f.get("file_url")
                if not url:
                    continue
                try:
                    BidProjectParseService.upsert({
                        "project_id": project_id,
                        "progress": 0.2 + 0.6 * (uploaded / max(total, 1)),
                        "progress_msg": f"正在下载附件 ({uploaded + 1}/{total})...",
                    })

                    local_path = download_file(url, tmpdir)
                    BidProjectFileService.upsert_file({
                        "project_file_id": f["project_file_id"],
                        "local_path": local_path,
                    })

                    # 解压压缩包
                    to_upload = [local_path]
                    if local_path.lower().endswith((".zip", ".rar")):
                        extracted = extract_archive(local_path, tmpdir)
                        to_upload.extend(extracted)

                    # 逐一上传到KB
                    for path in to_upload:
                        try:
                            fname = os.path.basename(path)
                            file_obj = FileStorage(
                                stream=open(path, "rb"),
                                filename=fname,
                            )
                            FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                            doc_id = getattr(file_obj, "id", None)
                            if doc_id and path == local_path:
                                BidProjectFileService.upsert_file({
                                    "project_file_id": f["project_file_id"],
                                    "kb_document_id": doc_id,
                                })
                        except Exception as e:
                            logging.warning("Failed to upload attachment %s: %s", fname, e)

                    uploaded += 1
                except Exception as e:
                    logging.warning("Failed to process attachment %s: %s", f.get("file_name"), e)
                    uploaded += 1

        # 3. 触发解析
        BidProjectParseService.upsert({
            "project_id": project_id,
            "combined_doc_id": combined_doc_id,
            "progress": 0.9,
            "progress_msg": "正在触发解析...",
        })

        # 获取KB中我们刚上传的所有文档，触发解析
        docs, _ = DocumentService.get_by_kb_id(
            kb_id=kb_id, page_number=1, items_per_page=1000,
            orderby="create_time", desc=True,
            keywords="", run_status=[], types=[], suffix=[]
        )
        doc_ids_to_parse = []
        for doc in docs:
            if doc.get("run") == "0":
                doc_ids_to_parse.append(doc["id"])

        # 使用 DocumentService.run() 来正确队列任务（创建Task记录 + 推送到Redis）
        kb_table_num_map = {}
        for doc_id in doc_ids_to_parse:
            try:
                e, doc_model = DocumentService.get_by_id(doc_id)
                if e:
                    doc_dict = doc_model.to_dict()
                    doc_dict["tenant_id"] = user_id
                    DocumentService.run(user_id, doc_dict, kb_table_num_map)
            except Exception as e:
                logging.warning("Failed to queue task for doc %s: %s", doc_id, e)

        BidProjectParseService.upsert({
            "project_id": project_id,
            "status": "done",
            "progress": 1,
            "progress_msg": "解析完成",
        })

        logging.info("Parse task completed for project %d, kb %s", project_id, kb_id)

    except Exception as e:
        logging.exception("Parse task failed for project %d", project_id)
        BidProjectParseService.upsert({
            "project_id": project_id,
            "status": "fail",
            "progress_msg": str(e),
        })


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
