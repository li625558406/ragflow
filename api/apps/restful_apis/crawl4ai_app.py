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
"""crawl4ai 独立爬虫 REST API

任务管理 + 手动触发 + 结果查询（列表展示/过滤）。
采集执行在后台线程中运行，调用 crawl4ai Docker 引擎。
"""
import logging
import threading

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.services.crawler_service import CrawlerResultService, CrawlerTaskService
from api.utils.api_utils import get_data_error_result, get_json_result
from common.misc_utils import get_uuid

manager = Blueprint("rest_crawl4ai_app", __name__)

# 进程内运行锁: 防止同一任务并发触发
_running_tasks: set = set()
_running_lock = threading.Lock()

_TASK_EDITABLE_FIELDS = {
    "name", "description", "site_id", "target_url", "page_url_template",
    "start_page", "max_pages", "extraction_schema", "detail_config",
    "headers", "output_targets", "kb_id", "parser_id", "enabled",
}


def _validate_task_payload(body: dict, creating: bool) -> str:
    """Returns error message, or empty string if valid."""
    if creating:
        for f in ("name", "site_id", "target_url"):
            if not (body.get(f) or "").strip():
                return f"{f} is required"
    schema = body.get("extraction_schema")
    if creating or schema is not None:
        if not isinstance(schema, dict) or not schema.get("baseSelector") or not schema.get("fields"):
            return "extraction_schema must contain baseSelector and fields"
    targets = body.get("output_targets")
    if targets is not None:
        if not isinstance(targets, list) or not set(targets).issubset({"db", "kb"}):
            return 'output_targets must be a subset of ["db","kb"]'
        if "kb" in targets and not (body.get("kb_id") or "").strip():
            return "kb_id is required when output_targets contains kb"
    max_pages = body.get("max_pages")
    if max_pages is not None and (not isinstance(max_pages, int) or max_pages < 1 or max_pages > 50):
        return "max_pages must be an integer between 1 and 50"
    return ""


# ---------------------------------------------------------------------------
# 任务管理
# ---------------------------------------------------------------------------

@manager.route("/crawl4ai/tasks", methods=["POST"])  # noqa: F821
@login_required
async def create_task():
    body = await request.get_json() or {}
    err = _validate_task_payload(body, creating=True)
    if err:
        return get_data_error_result(message=err)

    data = {k: v for k, v in body.items() if k in _TASK_EDITABLE_FIELDS}
    data["id"] = get_uuid()
    data["tenant_id"] = current_user.id
    CrawlerTaskService.insert(**data)
    return get_json_result(data={"id": data["id"]})


@manager.route("/crawl4ai/tasks", methods=["GET"])  # noqa: F821
@login_required
async def list_tasks():
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)
    keyword = request.args.get("keyword", "").strip() or None
    enabled = request.args.get("enabled")
    enabled_val = None if enabled in (None, "") else enabled.lower() == "true"

    # 权限策略（2026-07-22）: 采集任务列表对所有登录用户开放，不按 tenant 过滤
    rows, total = CrawlerTaskService.get_list(
        page_number=page,
        items_per_page=page_size,
        keyword=keyword,
        enabled=enabled_val,
    )
    return get_json_result(data={"list": rows, "total": total})


@manager.route("/crawl4ai/tasks/<task_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_task(task_id):
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return get_data_error_result(message="task not found")
    return get_json_result(data=task.to_dict())


@manager.route("/crawl4ai/tasks/<task_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_task(task_id):
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return get_data_error_result(message="task not found")

    body = await request.get_json() or {}
    err = _validate_task_payload(body, creating=False)
    if err:
        return get_data_error_result(message=err)

    data = {k: v for k, v in body.items() if k in _TASK_EDITABLE_FIELDS}
    if data:
        CrawlerTaskService.update_by_id(task_id, data)
    return get_json_result(data=True)


@manager.route("/crawl4ai/tasks/<task_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_task(task_id):
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return get_data_error_result(message="task not found")
    CrawlerTaskService.delete_by_id(task_id)
    return get_json_result(data=True)


# ---------------------------------------------------------------------------
# 手动触发
# ---------------------------------------------------------------------------

@manager.route("/crawl4ai/tasks/<task_id>/trigger", methods=["POST"])  # noqa: F821
@login_required
async def trigger_task(task_id):
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return get_data_error_result(message="task not found")

    with _running_lock:
        if task_id in _running_tasks:
            return get_data_error_result(message="task is already running")
        _running_tasks.add(task_id)

    def _run():
        try:
            from api.utils.crawl4ai_executor import run_task
            summary = run_task(task_id)
            logging.info("crawl4ai task %s finished: %s", task_id, summary)
        except Exception:
            logging.exception("crawl4ai task %s crashed", task_id)
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    threading.Thread(target=_run, daemon=True, name=f"crawl4ai-{task_id[:8]}").start()
    return get_json_result(data={"triggered": True, "task_id": task_id})


@manager.route("/crawl4ai/tasks/<task_id>/status", methods=["GET"])  # noqa: F821
@login_required
async def task_status(task_id):
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return get_data_error_result(message="task not found")
    with _running_lock:
        running = task_id in _running_tasks
    return get_json_result(data={
        "running": running,
        "last_run_status": task.last_run_status,
        "last_run_time": task.last_run_time,
        "last_run_summary": task.last_run_summary,
    })


# ---------------------------------------------------------------------------
# 结果查询（列表展示 + 过滤）
# ---------------------------------------------------------------------------

@manager.route("/crawl4ai/results", methods=["GET"])  # noqa: F821
@login_required
async def list_results():
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)

    # 权限策略（2026-07-22）: 采集结果列表对所有登录用户开放
    rows, total = CrawlerResultService.get_list(
        page_number=page,
        items_per_page=page_size,
        task_id=request.args.get("task_id") or None,
        site_id=request.args.get("site_id") or None,
        status=request.args.get("status") or None,
        keyword=request.args.get("keyword", "").strip() or None,
        start_date=request.args.get("start_date") or None,
        end_date=request.args.get("end_date") or None,
    )
    return get_json_result(data={"list": rows, "total": total})


@manager.route("/crawl4ai/results/<result_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_result(result_id):
    ok, result = CrawlerResultService.get_by_id(result_id)
    if not ok:
        return get_data_error_result(message="result not found")
    return get_json_result(data=result.to_dict())


@manager.route("/crawl4ai/sites", methods=["GET"])  # noqa: F821
@login_required
async def list_sites():
    # 权限策略（2026-07-22）: 所有登录用户可见全部站点的下拉选项
    return get_json_result(data=CrawlerResultService.site_options())


# ---------------------------------------------------------------------------
# 引擎健康检查
# ---------------------------------------------------------------------------

@manager.route("/crawl4ai/health", methods=["GET"])  # noqa: F821
@login_required
async def engine_health():
    from api.utils.crawl4ai_client import Crawl4aiClient
    return get_json_result(data={"engine_alive": Crawl4aiClient().health()})
