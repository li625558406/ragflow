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
import json
import logging
import os
import subprocess
import threading

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.services.crawler_service import CrawlerResultService, CrawlerTaskService
from api.utils.api_utils import get_data_error_result, get_json_result
from common.misc_utils import get_uuid

manager = Blueprint("rest_crawl4ai_app", __name__)

# YAML 站点配置路径（用于判断 task 是否走 YAML unified_crawler）
_CRAWLER_SITES_YAML = os.environ.get(
    "CRAWLER_SITES_YAML",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "rag", "svr", "crawler_sites.yaml"),
)
_UNIFIED_CRAWLER_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
    "rag", "svr", "unified_crawler.py",
)


def _is_yaml_site(site_id: str) -> tuple[bool, str]:
    """Check if site_id is configured in crawler_sites.yaml.
    Returns (is_yaml, category).
    """
    if not os.path.exists(_CRAWLER_SITES_YAML):
        return False, ""
    try:
        import yaml
        with open(_CRAWLER_SITES_YAML, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        sites = raw.get("sites") or {}
        if site_id in sites:
            cfg = sites[site_id] or {}
            return True, cfg.get("category", "bid")
    except Exception as e:
        logging.warning("crawl4ai_app: load YAML failed: %s", e)
    return False, ""


def _pull_summary_from_redis(task_id: str) -> dict:
    """Read the last `done` summary from Redis history list for a task.

    Returns {} if Redis unavailable or no done message found.
    """
    try:
        from rag.utils.redis_conn import REDIS_CONN
        client = getattr(REDIS_CONN, "REDIS", None)
        if client is None:
            return {}
        history_key = f"crawler:task:{task_id}:history"
        raw_items = client.lrange(history_key, -50, -1)  # last 50 messages
        for raw in reversed(raw_items):
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                msg = json.loads(raw)
                if msg.get("type") == "done":
                    return {
                        "status": msg.get("status", ""),
                        "summary": msg.get("summary", {}),
                        "ts": msg.get("ts"),
                    }
            except Exception:
                continue
    except Exception as e:
        logging.warning("crawl4ai_app: pull summary from redis failed: %s", e)
    return {}

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

    # 判断是否走 YAML unified_crawler（如 ggzyjd_dissent / ggzyjd_cases 等配置在 YAML 的站点）
    is_yaml, yaml_category = _is_yaml_site(task.site_id)
    if is_yaml and yaml_category and yaml_category != "bid":
        writer_mode = "collection"
    elif is_yaml:
        writer_mode = "bid"
    else:
        writer_mode = ""  # 非 YAML → 走 crawl4ai_executor

    def _run():
        try:
            if writer_mode:
                # YAML 站点 → unified_crawler 子进程
                script_args = json.dumps({
                    "site_id": task.site_id,
                    "writer": writer_mode,
                    "category": yaml_category or "bid",
                    "task_id": task_id,
                    "date_filter": "today",
                }, ensure_ascii=False)
                cmd = [
                    "python", _UNIFIED_CRAWLER_SCRIPT,
                    "--tenant-id", task.tenant_id,
                    "--kb-id", task.kb_id or "",
                    "--task-name", f"manual-{task.site_id}",
                    "--writer", writer_mode,
                    "--script-args", script_args,
                ]
                logging.info("task %s dispatch → unified_crawler (site=%s writer=%s)",
                             task_id, task.site_id, writer_mode)
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                if proc.returncode != 0:
                    logging.error("unified_crawler %s failed rc=%d: %s",
                                  task.site_id, proc.returncode, proc.stderr[-2000:])
                else:
                    logging.info("unified_crawler %s done: %s",
                                 task.site_id, proc.stdout[-500:])
                status = "success" if proc.returncode == 0 else "fail"
                # Pull structured summary from Redis history (written by ProgressReporter)
                summary = _pull_summary_from_redis(task_id)
                update_payload: dict = {"last_run_status": status}
                if summary:
                    update_payload["last_run_summary"] = summary
                CrawlerTaskService.update_by_id(task_id, update_payload)
            else:
                # 非 YAML 站点 → crawl4ai executor (LLM-based)
                from api.utils.crawl4ai_executor import run_task
                summary = run_task(task_id)
                logging.info("crawl4ai task %s finished: %s", task_id, summary)
        except Exception:
            logging.exception("crawl4ai task %s crashed", task_id)
            CrawlerTaskService.update_by_id(task_id, {"last_run_status": "fail"})
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
