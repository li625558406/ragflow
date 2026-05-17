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
import os
from datetime import datetime

from quart import request

from api.apps import current_user, login_required
from api.db.services.scheduled_task_service import (
    ScheduledTaskService,
    ScheduledTaskLogService,
)
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
)
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from rag.utils.redis_conn import REDIS_CONN
from common import settings


def _get_state_file_path(task_name):
    """Return the absolute path to the crawler state file for a given task name."""
    from common.file_utils import get_project_base_directory
    return os.path.join(get_project_base_directory(), "rag", task_name.strip(), "_crawler_state.json")


@manager.route("/scheduled-tasks", methods=["POST"])  # noqa: F821
@login_required
async def create_scheduled_task():
    req = await get_request_json()
    if not req:
        return get_data_error_result(message="Request body is required.")

    req["id"] = get_uuid()
    req["tenant_id"] = current_user.id

    # Compute initial next_run_time (in milliseconds, matching current_timestamp())
    if req.get("schedule_type") == "cron" and req.get("cron_expression"):
        try:
            from croniter import croniter

            cron = croniter(req["cron_expression"], datetime.now().astimezone())
            req["next_run_time"] = int(cron.get_next() * 1000)
        except (ValueError, ImportError) as e:
            return get_data_error_result(message=f"Invalid cron expression: {e}")
    elif req.get("schedule_type") == "interval" and req.get("interval_seconds"):
        req["next_run_time"] = current_timestamp() + int(req["interval_seconds"]) * 1000

    valid_fields = {
        "id", "tenant_id", "name", "description", "script_path", "script_args",
        "schedule_type", "cron_expression", "interval_seconds", "enabled",
        "last_run_time", "last_run_status", "next_run_time", "timeout",
        "max_retries", "retry_count", "target_url", "llm_id", "llm_model_name", "kb_id",
        "access_token",
    }
    data = {k: v for k, v in req.items() if k in valid_fields}
    ScheduledTaskService.save(**data)
    return get_json_result(data=data)


@manager.route("/scheduled-tasks", methods=["GET"])  # noqa: F821
@login_required
def list_scheduled_tasks():
    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("items_per_page", 15))
    name = request.args.get("name", None) or None
    enabled_str = request.args.get("enabled", None)
    enabled = None
    if enabled_str is not None:
        enabled = enabled_str.lower() == "true"

    objs, total = ScheduledTaskService.get_list(
        tenant_id=current_user.id,
        page_number=page_number,
        items_per_page=items_per_page,
        name=name,
        enabled=enabled,
    )
    return get_json_result(data={"tasks": objs, "total": total})


@manager.route("/scheduled-tasks/<task_id>", methods=["GET"])  # noqa: F821
@login_required
def get_scheduled_task(task_id):
    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")
    return get_json_result(data=obj.to_dict())


@manager.route("/scheduled-tasks/<task_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_scheduled_task(task_id):
    req = await get_request_json()
    if not req:
        return get_data_error_result(message="Request body is required.")

    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")

    # Recompute next_run_time if schedule changed
    if "schedule_type" in req or "cron_expression" in req or "interval_seconds" in req:
        schedule_type = req.get("schedule_type", obj.schedule_type)
        if schedule_type == "cron":
            cron_expr = req.get("cron_expression", obj.cron_expression)
            if cron_expr:
                try:
                    from croniter import croniter

                    cron = croniter(cron_expr, datetime.now().astimezone())
                    req["next_run_time"] = int(cron.get_next() * 1000)
                except (ValueError, ImportError) as e:
                    return get_data_error_result(
                        message=f"Invalid cron expression: {e}"
                    )
        elif schedule_type == "interval":
            interval = req.get("interval_seconds", obj.interval_seconds)
            if interval:
                req["next_run_time"] = current_timestamp() + int(interval) * 1000

    ScheduledTaskService.update_by_id(task_id, req)
    return get_json_result(data=req)


@manager.route("/scheduled-tasks/<task_id>", methods=["DELETE"])  # noqa: F821
@login_required
def delete_scheduled_task(task_id):
    e, obj = ScheduledTaskService.get_by_id(task_id)
    if e:
        state_path = _get_state_file_path(obj.name)
        if os.path.exists(state_path):
            try:
                os.remove(state_path)
            except Exception:
                pass
    ScheduledTaskService.delete_by_id(task_id)
    ScheduledTaskLogService.filter_delete(
        [ScheduledTaskLogService.model.task_id == task_id]
    )
    return get_json_result(data=True)


@manager.route("/scheduled-tasks/<task_id>/toggle", methods=["POST"])  # noqa: F821
@login_required
async def toggle_scheduled_task(task_id):
    req = await get_request_json()
    enabled = req.get("enabled", True)
    data = {"enabled": enabled}

    if enabled:
        e, obj = ScheduledTaskService.get_by_id(task_id)
        if e:
            if obj.schedule_type == "cron" and obj.cron_expression:
                try:
                    from croniter import croniter

                    cron = croniter(obj.cron_expression, datetime.now().astimezone())
                    data["next_run_time"] = int(cron.get_next() * 1000)
                except (ValueError, ImportError):
                    pass
            elif obj.schedule_type == "interval" and obj.interval_seconds:
                data["next_run_time"] = current_timestamp() + obj.interval_seconds * 1000

    ScheduledTaskService.update_by_id(task_id, data)
    return get_json_result(data=data)


@manager.route("/scheduled-tasks/<task_id>/state", methods=["GET"])  # noqa: F821
@login_required
def get_scheduled_task_state(task_id):
    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")

    state_path = _get_state_file_path(obj.name)
    if not os.path.exists(state_path):
        return get_json_result(data={"processed_urls": []})

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            import json
            data = json.load(f)
        return get_json_result(data=data)
    except Exception as e:
        return get_data_error_result(message=f"Failed to read state file: {e}")


@manager.route("/scheduled-tasks/<task_id>/state", methods=["PUT"])  # noqa: F821
@login_required
async def update_scheduled_task_state(task_id):
    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")

    req = await get_request_json()
    if req is None:
        return get_data_error_result(message="Request body is required.")

    state_path = _get_state_file_path(obj.name)
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        import json
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False, indent=2)
        return get_json_result(data=req)
    except Exception as e:
        return get_data_error_result(message=f"Failed to write state file: {e}")


@manager.route("/scheduled-tasks/<task_id>/run-now", methods=["POST"])  # noqa: F821
@login_required
async def run_scheduled_task_now(task_id):
    """Enqueue a scheduled task to Redis for immediate execution by task_executor."""
    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")

    # Clear any stale cancel flag from a previous stop, so a new
    # execution is not immediately rejected by the pre-start check.
    REDIS_CONN.delete(f"{task_id}-cancel")

    log_id = get_uuid()
    log_entry = {
        "id": log_id,
        "task_id": task_id,
        "tenant_id": obj.tenant_id,
        "status": "running",
        "start_time": current_timestamp(),
    }
    ScheduledTaskLogService.save(**log_entry)

    msg = {
        "id": log_id,
        "task_type": "scheduled_script",
        "tenant_id": obj.tenant_id,
        "name": obj.name,
        "script_path": obj.script_path,
        "script_args": obj.script_args or "",
        "timeout": obj.timeout,
        "task_id_ref": task_id,
        "target_url": obj.target_url or "",
        "llm_id": obj.llm_id or "",
        "llm_model_name": obj.llm_model_name or "",
        "kb_id": obj.kb_id or "",
        "access_token": obj.access_token or "",
    }
    if not REDIS_CONN.queue_product(settings.get_svr_queue_name(0), message=msg):
        return get_data_error_result(message="Cannot access Redis.")

    return get_json_result(data={"log_id": log_id})


@manager.route("/scheduled-tasks/<task_id>/logs", methods=["GET"])  # noqa: F821
@login_required
def list_scheduled_task_logs(task_id):
    page_number = int(request.args.get("page", 1))
    items_per_page = int(request.args.get("items_per_page", 15))

    objs, total = ScheduledTaskLogService.get_by_task_id(
        task_id, page_number, items_per_page
    )
    return get_json_result(data={"logs": objs, "total": total})


@manager.route("/scheduled-tasks/<task_id>/stop", methods=["POST"])  # noqa: F821
@login_required
def stop_scheduled_task(task_id):
    """Cancel a running scheduled task execution."""
    from api.db.services.task_service import has_canceled

    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return get_data_error_result(message="Task not found.")

    REDIS_CONN.set(f"{task_id}-cancel", "x", 3600)

    # Mark the latest running log as canceled
    latest_log = ScheduledTaskLogService.get_latest_by_task_id(task_id)
    if latest_log and latest_log.get("status") == "running":
        ScheduledTaskLogService.update_by_id(latest_log["id"], {
            "status": "fail",
            "end_time": current_timestamp(),
            "error_msg": "Manually stopped by user",
        })

    ScheduledTaskService.update_by_id(task_id, {
        "last_run_status": "fail",
    })

    return get_json_result(data=True)
