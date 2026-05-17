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
import asyncio
import faulthandler
import logging
import os
import signal
import sys
import time
from datetime import datetime

from common import settings
from common.config_utils import show_configs
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from common.versions import get_ragflow_version
from rag.utils.redis_conn import REDIS_CONN

POLL_INTERVAL = int(os.environ.get("SCHEDULED_TASK_POLL_INTERVAL", "15"))
stop_event = asyncio.Event()


def signal_handler(sig, frame):
    logging.info("Received interrupt signal, shutting down...")
    stop_event.set()
    time.sleep(1)
    sys.exit(0)


async def compute_next_run(task: dict) -> int:
    """Compute the next run timestamp (in milliseconds) based on schedule_type."""
    now = datetime.now().astimezone()
    if task.get("schedule_type") == "cron" and task.get("cron_expression"):
        try:
            from croniter import croniter

            cron = croniter(task["cron_expression"], now)
            return int(cron.get_next() * 1000)
        except Exception as e:
            logging.warning(f"Failed to compute next cron run for task {task.get('id')}: {e}")
            return current_timestamp() + 3600000  # default 1h
    elif task.get("schedule_type") == "interval" and task.get("interval_seconds"):
        return current_timestamp() + int(task["interval_seconds"]) * 1000
    return current_timestamp() + 3600000


async def dispatch_due_tasks():
    """Query and enqueue all due scheduled tasks to Redis."""
    from api.db.services.scheduled_task_service import (
        ScheduledTaskService,
        ScheduledTaskLogService,
    )

    try:
        due_tasks = ScheduledTaskService.get_due_tasks()
    except Exception as e:
        logging.warning(f"Failed to query due tasks: {e}")
        return

    if not due_tasks:
        return

    logging.info(f"Found {len(due_tasks)} due scheduled task(s)")

    for task in due_tasks:
        # Clear any stale cancel flag from a previous stop.
        REDIS_CONN.delete(f"{task['id']}-cancel")

        log_id = get_uuid()
        log_entry = {
            "id": log_id,
            "task_id": task["id"],
            "tenant_id": task["tenant_id"],
            "status": "running",
            "start_time": current_timestamp(),
        }
        try:
            ScheduledTaskLogService.save(**log_entry)
        except Exception as e:
            logging.error(f"Failed to create log entry for task {task['id']}: {e}")
            continue

        msg = {
            "id": log_id,
            "task_type": "scheduled_script",
            "tenant_id": task["tenant_id"],
            "name": task.get("name", ""),
            "script_path": task["script_path"],
            "script_args": task.get("script_args", "") or "",
            "timeout": task.get("timeout", 3600),
            "task_id_ref": task["id"],
            "target_url": task.get("target_url", "") or "",
            "llm_id": task.get("llm_id", "") or "",
            "llm_model_name": task.get("llm_model_name", "") or "",
            "kb_id": task.get("kb_id", "") or "",
            "access_token": task.get("access_token", "") or "",
        }

        if not REDIS_CONN.queue_product(settings.get_svr_queue_name(0), message=msg):
            logging.error(f"Failed to enqueue scheduled task {task['id']} to Redis")
            ScheduledTaskLogService.update_by_id(
                log_id, {"status": "fail", "error_msg": "Redis enqueue failed"}
            )
            continue

        # Compute and persist next run time
        next_run = await compute_next_run(task)
        ScheduledTaskService.update_by_id(
            task["id"],
            {
                "last_run_time": current_timestamp(),
                "last_run_status": "running",
                "next_run_time": next_run,
                "retry_count": 0,
            },
        )
        logging.info(
            f"Enqueued scheduled task {task['id']} ({task['name']}), next run at {next_run}"
        )


async def main():
    logging.info(r"""
   ____       _           _         _            _____     _        _                   _
  / ___|  ___| | ___  ___| |__   __| | ___  ___ |_   _|__ | |_  ___| |_ _ __ ___  _   _| |_ ___
 \___ \ / _ \ |/ _ \/ __| '_ \ / _` |/ _ \/ __|  | |/ _ \| \ \/ / | __| '__/ _ \| | | | __/ _ \
  ___) |  __/ |  __/ (__| | | | (_| |  __/\__ \  | | (_) | |\  /| | |_| | | (_) | |_| | ||  __/
 |____/ \___|_|\___|\___|_| |_|\__,_|\___||___/  |_|\___/|_| \/ |_|\__|_|  \___/ \__,_|\__\___|
    """)
    logging.info(f"RAGFlow scheduled task executor version: {get_ragflow_version()}")
    show_configs()
    settings.init_settings()
    if sys.platform != "win32":
        from common.signal_utils import start_tracemalloc_and_snapshot, stop_tracemalloc

        signal.signal(signal.SIGUSR1, start_tracemalloc_and_snapshot)
        signal.signal(signal.SIGUSR2, stop_tracemalloc)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logging.info(
        f"Scheduled task executor ready. Poll interval: {POLL_INTERVAL}s"
    )
    while not stop_event.is_set():
        await dispatch_due_tasks()
        await asyncio.sleep(POLL_INTERVAL)
    logging.error("BUG!!! You should not reach here!!!")


if __name__ == "__main__":
    CONSUMER_NAME = "scheduled_task_executor"
    faulthandler.enable()
    init_root_logger(CONSUMER_NAME)
    asyncio.run(main())
