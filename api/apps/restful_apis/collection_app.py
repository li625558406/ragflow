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
"""智能采集（新系统）REST API

路由前缀：/api/v1/collection

功能：
  - 站点列表查询（读 crawler_sites.yaml）
  - 手动触发单站点采集（投递 Redis 队列由 task_executor 执行）
  - 创建/列出/删除定时采集任务（复用 scheduled_task 表）
  - 采集结果列表/详情查询（按 category 过滤，关联扩展表）
"""
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import peewee
from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.db_models import DB
from api.db.services.collection_ext_service import (
    CollectionPolicyExtService,
    CollectionPersonnelExtService,
    CollectionObjectionExtService,
)
from api.db.services.common_service import CommonService
from api.db.services.crawler_service import CrawlerResult, CrawlerResultService
from api.db.services.scheduled_task_service import (
    ScheduledTaskLogService,
    ScheduledTaskService,
)
from api.utils.api_utils import get_data_error_result, get_json_result
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

manager = Blueprint("rest_collection_app", __name__)

# category 英文 code → 中文展示名（英文 code 保留用于 DB 过滤/统计）
CATEGORY_LABELS: Dict[str, str] = {
    "bid": "标讯",
    "policy": "政策法规",
    "personnel": "人员",
    "news": "新闻资讯",
    "other": "其他",
    "objection": "异议结果",
}


def _category_label(code: str) -> str:
    """英文 category code 转中文展示名；空值返回空串；未知 code 原样返回。"""
    if not code:
        return ""
    return CATEGORY_LABELS.get(code, code)


# category 英文 code → 中文展示标签
# 用于 list/detail 接口返回 category_label 字段；category 本身保持英文 code 不变（用于过滤）
CATEGORY_LABELS: Dict[str, str] = {
    "bid": "标讯",
    "policy": "政策法规",
    "personnel": "人员",
    "news": "新闻资讯",
    "other": "其他",
    "objection": "异议结果",
}


def _category_label(code: str) -> str:
    """返回 category 的中文展示标签；未知 code 原样返回。"""
    if not code:
        return ""
    return CATEGORY_LABELS.get(code, code)


def _extract_section_name(extracted_json: Any) -> str:
    """从 extracted_json 中取 section_name（多栏目站点的子分类展示名）。

    extracted_json 可能是 dict、JSON string 或 None。
    """
    if not extracted_json:
        return ""
    if isinstance(extracted_json, str):
        try:
            extracted_json = json.loads(extracted_json)
        except (ValueError, TypeError):
            return ""
    if isinstance(extracted_json, dict):
        return str(extracted_json.get("section_name") or "").strip()
    return ""


# YAML 配置路径（容器内）
_CRAWLER_SITES_YAML = os.environ.get(
    "CRAWLER_SITES_YAML",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "rag", "svr", "crawler_sites.yaml"),
)

# unified_crawler.py 脚本路径
_UNIFIED_CRAWLER_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
    "rag", "svr", "unified_crawler.py",
)


def _load_sites_from_yaml() -> List[Dict[str, Any]]:
    """读取 crawler_sites.yaml，返回精简的站点元数据列表。"""
    if not os.path.exists(_CRAWLER_SITES_YAML):
        return []
    try:
        import yaml
        with open(_CRAWLER_SITES_YAML, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as e:
        logging.error("collection_api: failed to load YAML %s: %s", _CRAWLER_SITES_YAML, e)
        return []

    sites: List[Dict[str, Any]] = []
    for site_id, data in (raw.get("sites") or {}).items():
        if not isinstance(data, dict):
            continue
        sites.append({
            "site_id": site_id,
            "name": data.get("name", site_id),
            "site_url": data.get("site_url", ""),
            "category": data.get("category", "bid"),
            "enabled": data.get("enabled", True),
        })
    return sites


def _build_script_args(site_id: str, category: str, full: bool,
                       section: str = "", date_filter: str = "") -> str:
    """构造 unified_crawler.py 的 --script-args JSON。

    date_filter: "" 不过滤；"today" 只保当天；"YYYY-MM-DD" 只保指定日期。
    传入 "today" 时由 CLI/writer 在运行时解析为当天日期，适合定时任务。
    """
    args: Dict[str, Any] = {
        "site_id": site_id,
        "writer": "collection",
        "category": category,
    }
    if full:
        args["full"] = True
    if section:
        args["section"] = section
    if date_filter:
        args["date_filter"] = date_filter
    return json.dumps(args, ensure_ascii=False)


_SITE_META_CACHE: Optional[Dict[str, Dict[str, str]]] = None
_SITE_META_CACHE_MTIME: float = 0.0


def _build_site_metadata_map() -> Dict[str, Dict[str, str]]:
    """读取 YAML 构建 site_id → {name, domain} 查询表。

    domain 取 site_url 的 netloc（如 https://xx.gov.cn → xx.gov.cn），
    site_url 缺失或解析失败时 domain 为空串。按 YAML mtime 做模块级缓存。
    """
    global _SITE_META_CACHE, _SITE_META_CACHE_MTIME
    try:
        mtime = os.path.getmtime(_CRAWLER_SITES_YAML)
    except OSError:
        return {}
    if _SITE_META_CACHE is not None and _SITE_META_CACHE_MTIME == mtime:
        return _SITE_META_CACHE

    sites = _load_sites_from_yaml()
    mapping: Dict[str, Dict[str, str]] = {}
    for s in sites:
        url = s.get("site_url", "") or ""
        domain = ""
        if url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
            except Exception:
                domain = ""
        mapping[s["site_id"]] = {
            "site_name": s.get("name", s["site_id"]),
            "site_domain": domain,
        }
    _SITE_META_CACHE = mapping
    _SITE_META_CACHE_MTIME = mtime
    return mapping


def _enqueue_one_shot_task(tenant_id: str, name: str, site_id: str,
                           category: str, kb_id: str,
                           full: bool = False,
                           date_filter: str = "") -> Dict[str, Any]:
    """创建一次性 ScheduledTask 记录并立即投递到 Redis 队列。

    返回 {"task_id", "log_id"} 或抛异常。
    """
    from common import settings
    from rag.utils.redis_conn import REDIS_CONN

    task_id = get_uuid()
    log_id = get_uuid()
    script_args = _build_script_args(site_id, category, full,
                                     date_filter=date_filter)

    # 1. 创建 ScheduledTask（enabled=False 防止被定时调度器重复拾起）
    ScheduledTaskService.insert({
        "id": task_id,
        "tenant_id": tenant_id,
        "name": name,
        "description": f"[one-shot] Manual collection trigger: {site_id} ({category})",
        "script_path": _UNIFIED_CRAWLER_SCRIPT,
        "script_args": script_args,
        "schedule_type": "interval",
        "interval_seconds": 86400,  # 不会真的周期触发（enabled=False）
        "enabled": False,            # 关键：一次性手动任务
        "timeout": 3600,
        "kb_id": kb_id,
        "target_url": "",
        "last_run_time": current_timestamp(),
        "last_run_status": "running",
        "next_run_time": None,
    })

    # 2. 创建运行日志
    ScheduledTaskLogService.save({
        "id": log_id,
        "task_id": task_id,
        "tenant_id": tenant_id,
        "status": "running",
        "start_time": current_timestamp(),
    })

    # 3. 投递到 Redis 队列，由 task_executor.py 消费执行
    msg = {
        "id": log_id,
        "task_type": "scheduled_script",
        "tenant_id": tenant_id,
        "name": name,
        "script_path": _UNIFIED_CRAWLER_SCRIPT,
        "script_args": script_args,
        "timeout": 3600,
        "task_id_ref": task_id,
        "target_url": "",
        "llm_id": "",
        "llm_model_name": "",
        "kb_id": kb_id,
        "access_token": "",
    }
    if not REDIS_CONN.queue_product(settings.get_svr_queue_name(0), message=msg):
        ScheduledTaskLogService.update_by_id(
            log_id, {"status": "fail", "error_msg": "Redis enqueue failed"}
        )
        raise RuntimeError("Redis enqueue failed")

    return {"task_id": task_id, "log_id": log_id}


# ---------------------------------------------------------------------------
# 站点元信息
# ---------------------------------------------------------------------------

@manager.route("/collection/sites", methods=["GET"])  # noqa: F821
@login_required
async def list_sites():
    """列出 crawler_sites.yaml 中所有可用站点。"""
    category = request.args.get("category", "").strip() or None
    enabled_only = request.args.get("enabled", "true").lower() != "false"

    sites = _load_sites_from_yaml()
    if category:
        sites = [s for s in sites if s["category"] == category]
    if enabled_only:
        sites = [s for s in sites if s["enabled"]]

    return get_json_result(data={"list": sites, "total": len(sites)})


# ---------------------------------------------------------------------------
# 手动触发
# ---------------------------------------------------------------------------

@manager.route("/collection/trigger", methods=["POST"])  # noqa: F821
@login_required
async def trigger_run():
    """手动触发单站点采集。

    Body:
        {
            "site_id": "xxx",        # 必填
            "category": "policy",    # 可选，默认从 YAML 读
            "kb_id":   "xxx",        # 可选
            "full":    false,        # 可选，是否全量重扫
            "section": "",           # 可选
            "date_filter": ""        # 可选, "today" 或 "YYYY-MM-DD"
        }
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    # 从 YAML 查 category 兜底
    category = (body.get("category") or "").strip()
    if not category:
        sites = _load_sites_from_yaml()
        match = next((s for s in sites if s["site_id"] == site_id), None)
        category = match["category"] if match else "bid"

    kb_id = (body.get("kb_id") or "").strip()
    full = bool(body.get("full", False))
    section = (body.get("section") or "").strip()
    date_filter = (body.get("date_filter") or "").strip()

    try:
        result = _enqueue_one_shot_task(
            tenant_id=current_user.id,
            name=f"[collection] {site_id}",
            site_id=site_id,
            category=category,
            kb_id=kb_id,
            full=full,
            date_filter=date_filter,
        )
    except Exception as e:
        logging.exception("collection_api: trigger failed: %s", e)
        return get_data_error_result(message=f"trigger failed: {e}")

    return get_json_result(data=result)


@manager.route("/collection/tasks/<task_id>/status", methods=["GET"])  # noqa: F821
@login_required
async def get_task_status(task_id: str):
    """查询任务最新运行状态（读 scheduled_task_log）。"""
    @DB.connection_context()
    def _query() -> Optional[Dict[str, Any]]:
        row = (
            ScheduledTaskLogService.model
            .select()
            .where(ScheduledTaskLogService.model.task_id == task_id)
            .order_by(ScheduledTaskLogService.model.start_time.desc())
            .first()
        )
        return row.to_dict() if row else None

    log = _query()
    if not log:
        return get_data_error_result(message="task log not found")
    return get_json_result(data=log)


# ---------------------------------------------------------------------------
# 定时任务管理
# ---------------------------------------------------------------------------

@manager.route("/collection/scheduled", methods=["POST"])  # noqa: F821
@login_required
async def create_scheduled():
    """创建定时采集任务（复用 scheduled_task 表）。

    Body:
        {
            "site_id": "xxx",
            "category": "policy",
            "schedule_type": "cron" | "interval",
            "cron_expression": "0 2 * * *",       # schedule_type=cron 时必填
            "interval_seconds": 86400,            # schedule_type=interval 时必填
            "kb_id": "xxx",
            "enabled": true,
            "name": "...",                        # 可选
            "date_filter": "today"                # 可选, 推荐每日任务用 "today"
        }
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    schedule_type = (body.get("schedule_type") or "interval").strip()
    if schedule_type not in ("cron", "interval"):
        return get_data_error_result(message="schedule_type must be cron or interval")

    cron_expr = (body.get("cron_expression") or "").strip()
    interval = int(body.get("interval_seconds") or 0)
    if schedule_type == "cron" and not cron_expr:
        return get_data_error_result(message="cron_expression is required for cron schedule")
    if schedule_type == "interval" and interval <= 0:
        return get_data_error_result(message="interval_seconds must be positive")

    category = (body.get("category") or "bid").strip()
    kb_id = (body.get("kb_id") or "").strip()
    name = (body.get("name") or f"[collection] {site_id}").strip()
    enabled = bool(body.get("enabled", True))
    date_filter = (body.get("date_filter") or "").strip()
    if date_filter and date_filter.lower() != "today":
        # 校验 YYYY-MM-DD 格式
        from datetime import datetime as _dt
        try:
            _dt.strptime(date_filter.replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            return get_data_error_result(
                message='date_filter must be "today" or "YYYY-MM-DD"')

    # 计算首次 next_run_time
    if schedule_type == "interval":
        next_run = current_timestamp() + interval * 1000
    else:
        try:
            from croniter import croniter
            from datetime import datetime
            next_run = int(croniter(cron_expr, datetime.now().astimezone()).get_next() * 1000)
        except Exception as e:
            return get_data_error_result(message=f"invalid cron_expression: {e}")

    task_id = get_uuid()
    ScheduledTaskService.insert({
        "id": task_id,
        "tenant_id": current_user.id,
        "name": name,
        "description": f"Scheduled collection: {site_id} ({category})",
        "script_path": _UNIFIED_CRAWLER_SCRIPT,
        "script_args": _build_script_args(site_id, category, full=False,
                                          date_filter=date_filter),
        "schedule_type": schedule_type,
        "cron_expression": cron_expr,
        "interval_seconds": interval if schedule_type == "interval" else None,
        "enabled": enabled,
        "timeout": 3600,
        "kb_id": kb_id,
        "target_url": "",
        "next_run_time": next_run,
    })
    return get_json_result(data={"id": task_id, "next_run_time": next_run})


@manager.route("/collection/scheduled", methods=["GET"])  # noqa: F821
@login_required
async def list_scheduled():
    """列出当前租户的定时采集任务。

    只返回 script_path 指向 unified_crawler.py 且 script_args 含 "writer":"collection"
    的任务，避免和旧系统混淆。
    """
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)

    @DB.connection_context()
    def _query() -> Dict[str, Any]:
        query = (
            ScheduledTaskService.model
            .select()
            .where(
                (ScheduledTaskService.model.tenant_id == current_user.id)
                & (ScheduledTaskService.model.script_path == _UNIFIED_CRAWLER_SCRIPT)
                & (ScheduledTaskService.model.script_args ** '%"writer": "collection"%')
            )
        )
        total = query.count()
        rows = (
            query
            .order_by(ScheduledTaskService.model.create_time.desc())
            .paginate(page, page_size)
        )
        items = []
        for r in rows:
            d = r.to_dict()
            try:
                args = json.loads(d.get("script_args") or "{}")
            except Exception:
                args = {}
            d["parsed_args"] = args
            items.append(d)
        return {"list": items, "total": total}

    return get_json_result(data=_query())


@manager.route("/collection/scheduled/<task_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_scheduled(task_id: str):
    """删除定时采集任务。"""
    ok, task = ScheduledTaskService.get_by_id(task_id)
    if not ok or task.tenant_id != current_user.id:
        return get_data_error_result(message="task not found")
    ScheduledTaskService.delete_by_id(task_id)
    return get_json_result(data={"id": task_id})


# ---------------------------------------------------------------------------
# 结果查询
# ---------------------------------------------------------------------------

@manager.route("/collection/results", methods=["GET"])  # noqa: F821
@login_required
async def list_results():
    """采集结果列表（按 category 过滤）。

    Query:
        category: bid|policy|personnel|news|other
        site_id, task_id, status, keyword, start_date, end_date
        page, page_size
    """
    category = request.args.get("category", "").strip() or None
    site_id = request.args.get("site_id", "").strip() or None
    task_id = request.args.get("task_id", "").strip() or None
    status = request.args.get("status", "").strip() or None
    keyword = request.args.get("keyword", "").strip() or None
    start_date = request.args.get("start_date", "").strip() or None
    end_date = request.args.get("end_date", "").strip() or None
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)
    with_ext = request.args.get("with_ext", "true").lower() != "false"

    @DB.connection_context()
    def _query() -> Dict[str, Any]:
        q = CrawlerResult.select().where(CrawlerResult.tenant_id == current_user.id)
        if category:
            q = q.where(CrawlerResult.category == category)
        if site_id:
            q = q.where(CrawlerResult.site_id == site_id)
        if task_id:
            q = q.where(CrawlerResult.task_id == task_id)
        if status:
            q = q.where(CrawlerResult.status == status)
        if keyword:
            q = q.where(CrawlerResult.title ** f"%{keyword}%")
        if start_date:
            q = q.where(CrawlerResult.publish_date >= start_date)
        if end_date:
            end_dt = end_date if " " in end_date else f"{end_date} 23:59:59"
            q = q.where(CrawlerResult.publish_date <= end_dt)

        total = q.count()
        rows = q.order_by(CrawlerResult.crawled_at.desc()).paginate(page, page_size)
        items = []
        for r in rows:
            d = r.to_dict()
            d.pop("markdown", None)  # 列表不返回大字段
            items.append(d)

        # 附加站点中文名 + 域名 (优先用 DB 存的 site_display, 回退到 YAML 派生)
        site_map = _build_site_metadata_map() if items else {}
        for it in items:
            it["category_label"] = _category_label(it.get("category", ""))
            stored_display = (it.get("site_display") or "").strip()
            if stored_display:
                # DB 已存 "名称 域名" 拼接串, 拆开填充 site_name + site_domain
                if " " in stored_display:
                    name_part, domain_part = stored_display.split(" ", 1)
                    it["site_name"] = name_part
                    it["site_domain"] = domain_part
                else:
                    it["site_name"] = stored_display
                    it["site_domain"] = ""
                it["site_display"] = stored_display
            else:
                meta = site_map.get(it.get("site_id", ""), {})
                it["site_name"] = meta.get("site_name", "") or it.get("site_id", "")
                it["site_domain"] = meta.get("site_domain", "")
                it["site_display"] = f"{it['site_name']} {it['site_domain']}".strip()

        # 批量补充扩展字段
        if with_ext and category in ("policy", "personnel", "objection") and items:
            result_ids = [it["id"] for it in items]
            if category == "policy":
                ext_map = CollectionPolicyExtService.get_by_result_ids(result_ids)
            elif category == "personnel":
                ext_map = CollectionPersonnelExtService.get_by_result_ids(result_ids)
            else:  # objection
                ext_map = CollectionObjectionExtService.get_by_result_ids(result_ids)
            for it in items:
                it["ext"] = ext_map.get(it["id"], {})
        # 附加 category 中文展示名（英文 code 保留用于过滤）+ section_name（多栏目站点的子分类）
        for it in items:
            it["category_label"] = _category_label(it.get("category", ""))
            it["section_name"] = _extract_section_name(it.get("extracted_json"))
        return {"list": items, "total": total}

    return get_json_result(data=_query())


@manager.route("/collection/results/<result_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_result(result_id: str):
    """采集结果详情（含 markdown 正文 + 扩展字段）。"""
    @DB.connection_context()
    def _query() -> Optional[Dict[str, Any]]:
        row = (
            CrawlerResult
            .select()
            .where(
                (CrawlerResult.id == result_id)
                & (CrawlerResult.tenant_id == current_user.id)
            )
            .first()
        )
        if not row:
            return None
        d = row.to_dict()
        d["category_label"] = _category_label(d.get("category", ""))
        # 附加站点中文名 + 域名 (优先用 DB 存的 site_display, 回退到 YAML 派生)
        stored_display = (d.get("site_display") or "").strip()
        if stored_display:
            if " " in stored_display:
                name_part, domain_part = stored_display.split(" ", 1)
                d["site_name"] = name_part
                d["site_domain"] = domain_part
            else:
                d["site_name"] = stored_display
                d["site_domain"] = ""
            d["site_display"] = stored_display
        else:
            site_map = _build_site_metadata_map()
            meta = site_map.get(d.get("site_id", ""), {})
            d["site_name"] = meta.get("site_name", "") or d.get("site_id", "")
            d["site_domain"] = meta.get("site_domain", "")
            d["site_display"] = f"{d['site_name']} {d['site_domain']}".strip()
        category = d.get("category", "bid")
        if category == "policy":
            ext = CollectionPolicyExtService.get_by_result_ids([result_id])
            d["ext"] = ext.get(result_id, {})
        elif category == "personnel":
            ext = CollectionPersonnelExtService.get_by_result_ids([result_id])
            d["ext"] = ext.get(result_id, {})
        elif category == "objection":
            ext = CollectionObjectionExtService.get_by_result_ids([result_id])
            d["ext"] = ext.get(result_id, {})
        else:
            d["ext"] = {}
        d["category_label"] = _category_label(d.get("category", ""))
        d["section_name"] = _extract_section_name(d.get("extracted_json"))
        return d

    data = _query()
    if not data:
        return get_data_error_result(message="result not found")
    return get_json_result(data=data)


@manager.route("/collection/stats", methods=["GET"])  # noqa: F821
@login_required
async def collection_stats():
    """按 category 统计采集结果数量。"""
    from peewee import fn

    @DB.connection_context()
    def _query() -> List[Dict[str, Any]]:
        rows = (
            CrawlerResult
            .select(CrawlerResult.category, fn.COUNT(CrawlerResult.id).alias("count"))
            .where(CrawlerResult.tenant_id == current_user.id)
            .group_by(CrawlerResult.category)
        )
        return [{"category": r.category or "",
                 "category_label": _category_label(r.category or ""),
                 "count": r.count} for r in rows]

    return get_json_result(data={"list": _query()})


# ---------------------------------------------------------------------------
# 探测监控 (Detect monitor)
# ---------------------------------------------------------------------------

# Redis key patterns — must mirror crawler_detector.py
_DETECT_STATE_KEY = "detector:state:{tenant}:{site}"
_DETECT_LOCK_KEY = "detector:lock:{tenant}:{site}"
_DETECT_FORCE_KEY = "detector:force:{tenant}:{site}"
_DETECT_STATE_TTL = 30 * 86400


def _detect_redis():
    """Lazy import — avoid Redis dependency at module load."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
        return REDIS_CONN
    except Exception:
        return None


def _detect_state(tenant_id: str, site_id: str) -> Dict[str, Any]:
    rc = _detect_redis()
    if rc is None:
        return {}
    try:
        import json as _json
        raw = rc.get(_DETECT_STATE_KEY.format(tenant=tenant_id, site=site_id))
        if not raw:
            return {}
        return _json.loads(raw) or {}
    except Exception:
        return {}


def _detect_save(tenant_id: str, site_id: str, state: Dict[str, Any]) -> bool:
    rc = _detect_redis()
    if rc is None:
        return False
    try:
        return bool(rc.set_obj(
            _DETECT_STATE_KEY.format(tenant=tenant_id, site=site_id),
            state, exp=_DETECT_STATE_TTL,
        ))
    except Exception:
        return False


_YAML_SITE_MAP_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_YAML_SITE_MAP_MTIME: float = 0.0


def _yaml_site_map() -> Dict[str, Dict[str, Any]]:
    """Return site_id → metadata dict, cached by YAML mtime.

    Re-parsing the YAML on every ``/detect/state`` request is wasteful (84+
    sites, ~5k LOC).  Cache the derived map keyed on file mtime so hot-reload
    still works (next request after edit re-parses).
    """
    global _YAML_SITE_MAP_CACHE, _YAML_SITE_MAP_MTIME
    try:
        mtime = os.path.getmtime(_CRAWLER_SITES_YAML)
    except OSError:
        return {}
    if _YAML_SITE_MAP_CACHE is not None and _YAML_SITE_MAP_MTIME == mtime:
        return _YAML_SITE_MAP_CACHE

    from rag.svr.crawler_engine.config import ConfigLoader
    try:
        loader = ConfigLoader(_CRAWLER_SITES_YAML)
        sites = loader.load()
    except Exception as e:
        logging.error("collection_api: failed to load YAML for detect: %s", e)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for sid, cfg in sites.items():
        out[sid] = {
            "site_id": sid,
            "name": cfg.name or sid,
            "site_url": cfg.site_url,
            "category": cfg.category,
            "enabled": cfg.enabled,
            "detect_interval": cfg.detect_interval,
            "detect_max_interval": cfg.detect_max_interval,
            "detect_min_interval": cfg.detect_min_interval,
            "detect_quiet_hours": cfg.detect_quiet_hours,
        }
    _YAML_SITE_MAP_CACHE = out
    _YAML_SITE_MAP_MTIME = mtime
    return out


@manager.route("/collection/detect/state", methods=["GET"])  # noqa: F821
@login_required
async def detect_list_state():
    """所有站点的探测状态 (合并 YAML 元数据 + Redis 运行时 state).

    Query:
        category, enabled_only, status (changed|unchanged|auto_disabled|never_probed)
        page, page_size
    """
    category = request.args.get("category", "").strip() or None
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 50)), 500)

    site_map = _yaml_site_map()
    if not site_map:
        return get_json_result(data={"list": [], "total": 0})

    now = int(time.time())
    rows = []
    for sid, meta in site_map.items():
        if category and meta["category"] != category:
            continue
        st = _detect_state(current_user.id, sid)
        next_run_at = int(st.get("next_run_at") or 0)
        last_check = int(st.get("last_check") or 0)

        # Status classification for filter / display
        if st.get("auto_disabled"):
            status = "auto_disabled"
        elif st.get("manual_disabled"):
            status = "manual_disabled"
        elif not st:
            status = "never_probed"
        elif next_run_at <= now:
            status = "due"
        elif int(st.get("consecutive_errors", 0)) > 0:
            status = "error"
        elif int(st.get("miss_count", 0)) >= 4:
            status = "cold"
        else:
            status = "active"

        rows.append({
            **meta,
            "next_run_at": next_run_at,
            "next_run_in_sec": max(0, next_run_at - now),
            "last_check": last_check,
            "last_check_ago_sec": (now - last_check) if last_check else 0,
            "miss_count": int(st.get("miss_count", 0)),
            "cur_interval": int(st.get("cur_interval", 0)),
            "last_sig": st.get("last_sig", ""),
            "last_new_count": int(st.get("last_new_count", 0)),
            "consecutive_errors": int(st.get("consecutive_errors", 0)),
            "last_reason": st.get("last_reason", ""),
            "last_error": st.get("last_error", ""),
            "last_enqueue_ok": st.get("last_enqueue_ok"),
            "status": status,
        })

    # Optional status filter (post-compute)
    status_filter = request.args.get("status", "").strip() or None
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]

    rows.sort(key=lambda r: (r["status"] != "due", r["next_run_at"] or 0))
    total = len(rows)
    start = (page - 1) * page_size
    return get_json_result(data={
        "list": rows[start:start + page_size],
        "total": total,
        "now": now,
    })


@manager.route("/collection/detect/reset", methods=["POST"])  # noqa: F821
@login_required
async def detect_reset():
    """重置某站点 backoff: miss_count=0, cur_interval=detect_interval, next_run_at=now.

    Body: {"site_id": "xxx"}
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    site_map = _yaml_site_map()
    if site_id not in site_map:
        return get_data_error_result(message=f"unknown site_id: {site_id}")

    base = site_map[site_id]["detect_min_interval"] or site_map[site_id]["detect_interval"]
    now = int(time.time())
    st = _detect_state(current_user.id, site_id)
    st.update({
        "miss_count": 0,
        "cur_interval": base,
        "next_run_at": now,
        "consecutive_errors": 0,
        "auto_disabled": False,
        "manual_disabled": False,
        "reset_at": now,
    })
    ok = _detect_save(current_user.id, site_id, st)
    return get_json_result(data={"site_id": site_id, "reset": ok,
                                 "next_interval": base})


@manager.route("/collection/detect/disable", methods=["POST"])  # noqa: F821
@login_required
async def detect_disable():
    """关闭某站点探测 (manual_disabled=true).

    Body: {"site_id": "xxx"}
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    st = _detect_state(current_user.id, site_id)
    st["manual_disabled"] = True
    st["auto_disabled"] = False
    ok = _detect_save(current_user.id, site_id, st)
    return get_json_result(data={"site_id": site_id, "disabled": ok})


@manager.route("/collection/detect/enable", methods=["POST"])  # noqa: F821
@login_required
async def detect_enable():
    """启用某站点探测 (清除 manual_disabled / auto_disabled).

    Body: {"site_id": "xxx"}
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    st = _detect_state(current_user.id, site_id)
    st["manual_disabled"] = False
    st["auto_disabled"] = False
    st["consecutive_errors"] = 0
    st["next_run_at"] = int(time.time())
    ok = _detect_save(current_user.id, site_id, st)
    return get_json_result(data={"site_id": site_id, "enabled": ok})


@manager.route("/collection/detect/trigger", methods=["POST"])  # noqa: F821
@login_required
async def detect_trigger():
    """立即触发探测 (写 force key, 下次 meta-task 拾起时无视 next_run_at).

    Body: {"site_id": "xxx"}
    """
    body = await request.get_json() or {}
    site_id = (body.get("site_id") or "").strip()
    if not site_id:
        return get_data_error_result(message="site_id is required")

    rc = _detect_redis()
    if rc is None:
        return get_data_error_result(message="Redis not available")
    try:
        rc.set(_DETECT_FORCE_KEY.format(tenant=current_user.id, site=site_id),
               "1", exp=300)
    except Exception as e:
        return get_data_error_result(message=f"set force flag failed: {e}")
    return get_json_result(data={"site_id": site_id, "forced": True})


@manager.route("/collection/detect/stats", methods=["GET"])  # noqa: F821
@login_required
async def detect_stats():
    """探测统计: active/cold/due/error/disabled/never_probed 计数 + 平均 interval."""
    import statistics as _stats
    site_map = _yaml_site_map()
    if not site_map:
        return get_json_result(data={"total": 0, "buckets": {}, "avg_interval": 0})

    counts = {
        "active": 0, "cold": 0, "due": 0, "error": 0,
        "auto_disabled": 0, "manual_disabled": 0, "never_probed": 0,
    }
    intervals = []
    now = int(time.time())
    for sid in site_map:
        st = _detect_state(current_user.id, sid)
        if st.get("auto_disabled"):
            counts["auto_disabled"] += 1
            continue
        if st.get("manual_disabled"):
            counts["manual_disabled"] += 1
            continue
        if not st:
            counts["never_probed"] += 1
            continue
        iv = int(st.get("cur_interval", 0))
        if iv > 0:
            intervals.append(iv)
        if int(st.get("consecutive_errors", 0)) > 0:
            counts["error"] += 1
        elif int(st.get("next_run_at", 0)) <= now:
            counts["due"] += 1
        elif int(st.get("miss_count", 0)) >= 4:
            counts["cold"] += 1
        else:
            counts["active"] += 1

    avg_interval = int(_stats.mean(intervals)) if intervals else 0
    return get_json_result(data={
        "total": len(site_map),
        "buckets": counts,
        "avg_interval": avg_interval,
        "now": now,
    })


@manager.route("/collection/detect/activity", methods=["GET"])  # noqa: F821
@login_required
async def detect_activity():
    """近 N 秒内每个站点新增的 crawler_result 条数 (活动流).

    Query:
        window (int, default 3600): 回看窗口秒数, 限制 [60, 86400]
        limit  (int, default 20): 最多返回多少站点, 限制 [1, 100]

    Returns:
        {
            "now_ms": int,
            "window": int,
            "items": [
                {"site_id": str, "site_name": str, "category": str,
                 "count": int, "first_at_ms": int, "last_at_ms": int,
                 "last_title": str}
            ],
            "total_count": int  # 所有站点合计新增
        }
    """
    window = max(60, min(int(request.args.get("window", 3600)), 86400))
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - window * 1000

    site_map = _yaml_site_map()

    try:
        rows = (
            CrawlerResult
            .select(
                CrawlerResult.site_id,
                peewee.fn.COUNT(CrawlerResult.id).alias("cnt"),
                peewee.fn.MIN(CrawlerResult.crawled_at).alias("first_at"),
                peewee.fn.MAX(CrawlerResult.crawled_at).alias("last_at"),
            )
            .where(
                (CrawlerResult.tenant_id == current_user.id)
                & (CrawlerResult.crawled_at.is_null(False))
                & (CrawlerResult.crawled_at >= since_ms)
            )
            .group_by(CrawlerResult.site_id)
            .order_by(peewee.SQL("last_at").desc())
            .limit(limit)
        )
        items = []
        total = 0
        for r in rows:
            cnt = int(r.cnt or 0)
            total += cnt
            meta = site_map.get(r.site_id, {})
            items.append({
                "site_id": r.site_id,
                "site_name": meta.get("name") or r.site_id,
                "category": meta.get("category") or "",
                "count": cnt,
                "first_at_ms": int(r.first_at or 0),
                "last_at_ms": int(r.last_at or 0),
            })
    except Exception as e:
        logging.exception("collection_api: detect_activity failed: %s", e)
        return get_data_error_result(message=f"activity query failed: {e}")

    # 每个 site 的最新 title: 用单条 SQL 一次性取所有 site 的 last_at 对应行
    # 子查询取每个 site 在窗口内的 max(crawled_at) 对应的 id, 再 JOIN 回主表拿 title.
    if items:
        try:
            site_ids = [it["site_id"] for it in items]
            # 子查询: 按 site_id 分组取最大 crawled_at 对应的 (site_id, id)
            sub = (
                CrawlerResult
                .select(
                    CrawlerResult.site_id.alias("sid"),
                    peewee.fn.MAX(CrawlerResult.id).alias("max_id"),
                )
                .where(
                    (CrawlerResult.tenant_id == current_user.id)
                    & (CrawlerResult.site_id.in_(site_ids))
                    & (CrawlerResult.crawled_at.is_null(False))
                    & (CrawlerResult.crawled_at >= since_ms)
                )
                .group_by(CrawlerResult.site_id)
                .alias("sub")
            )
            last_title_map: Dict[str, str] = {}
            for r in (
                CrawlerResult
                .select(CrawlerResult.site_id, CrawlerResult.title)
                .join(
                    sub,
                    on=(CrawlerResult.id == sub.c.max_id),
                )
            ):
                last_title_map[r.site_id] = r.title or ""
            for it in items:
                it["last_title"] = last_title_map.get(it["site_id"], "")
        except Exception as e:
            logging.warning("collection_api: detect_activity title fetch failed: %s", e)
            for it in items:
                it.setdefault("last_title", "")

    return get_json_result(data={
        "now_ms": now_ms,
        "window": window,
        "items": items,
        "total_count": total,
    })


@manager.route("/collection/detect/install", methods=["POST"])  # noqa: F821
@login_required
async def detect_install():
    """注册/更新 detector meta-task (调用 ensure_detector_task 幂等).

    Body: {"interval_seconds": 60}
    kb_id 已废弃 —— 探测器不再消费 kb_id,爬虫脚本会按 site_id 查 crawler_task 表自动获取.
    """
    body = await request.get_json() or {}
    interval_seconds = int(body.get("interval_seconds", 60))
    if interval_seconds < 30:
        return get_data_error_result(message="interval_seconds must be >= 30")

    try:
        from rag.svr.crawler_engine.register_detector_task import ensure_detector_task
        row = ensure_detector_task(
            tenant_id=current_user.id,
            interval_seconds=interval_seconds,
            enabled=True,
        )
    except Exception as e:
        logging.exception("collection_api: detect_install failed: %s", e)
        return get_data_error_result(message=f"install failed: {e}")
    return get_json_result(data={"task": row})
