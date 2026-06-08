#!/usr/bin/env python3
"""Batch trigger scheduled tasks and monitor results.

Usage:
    python batch_run_tasks.py [--batch-size 3] [--wait 300] [--skip-wechat]
"""
import argparse
import json
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger

CONSUMER_NAME = "batch_runner"
init_root_logger(CONSUMER_NAME)


def get_tasks(skip_wechat=True):
    """Get all scheduled tasks from DB."""
    from api.db.db_models import ScheduledTask
    tasks = []
    for r in ScheduledTask.select().dicts():
        sp = r.get("script_path", "") or ""
        sa = r.get("script_args") or ""
        # Skip wechat
        if skip_wechat and "wechat" in sp.lower():
            continue
        tasks.append({
            "id": r["id"],
            "name": r["name"],
            "kb_id": r.get("kb_id", ""),
            "site_id": json.loads(sa).get("site_id", "") if sa else "",
            "script_path": sp,
        })
    return tasks


def trigger_task(task_id):
    """Trigger a task via run-now API (same as B-side)."""
    from api.db.services.scheduled_task_service import (
        ScheduledTaskService, ScheduledTaskLogService,
    )
    from common.misc_utils import get_uuid
    from common.time_utils import current_timestamp
    from rag.utils.redis_conn import REDIS_CONN
    from common import settings as cfg

    e, obj = ScheduledTaskService.get_by_id(task_id)
    if not e:
        return None, "task_not_found"

    # Clear cancel flag
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
    queue_name = cfg.get_svr_queue_name(0)
    ok = REDIS_CONN.queue_product(queue_name, message=msg)
    if not ok:
        return None, "redis_error"
    return log_id, "ok"


def check_task_result(task_id):
    """Check latest log for a task."""
    from api.db.services.scheduled_task_service import ScheduledTaskLogService
    from common.time_utils import current_timestamp

    log = ScheduledTaskLogService.get_latest_by_task_id(task_id)
    if not log:
        return {"status": "no_log", "error_msg": ""}

    status = log.get("status", "unknown")
    error_msg = log.get("error_msg", "")
    return {"status": status, "error_msg": error_msg}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--wait", type=int, default=300)
    parser.add_argument("--skip-wechat", action="store_true", default=True)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--site-ids", type=str, default="", help="Comma-separated site_ids to run")
    args = parser.parse_args()

    settings.init_settings()
    tasks = get_tasks(skip_wechat=args.skip_wechat)

    # Filter by site_ids if specified
    if args.site_ids:
        want = set(args.site_ids.split(","))
        tasks = [t for t in tasks if t["site_id"] in want]

    if args.list_only:
        print(f"{'#':<4} {'Task ID':<12} {'Site ID':<30} {'Name'}")
        print("-" * 100)
        for i, t in enumerate(tasks, 1):
            print(f"{i:<4} {t['id'][:12]:<12} {t['site_id']:<30} {t['name']}")
        print(f"\nTotal: {len(tasks)}")
        return

    print(f"Total tasks to run: {len(tasks)}, batch_size={args.batch_size}, wait={args.wait}s")
    print("=" * 80)

    results = []
    for i in range(0, len(tasks), args.batch_size):
        batch = tasks[i:i + args.batch_size]
        print(f"\n>>> Batch {i // args.batch_size + 1}: {len(batch)} tasks")

        # Trigger all in batch
        for t in batch:
            log_id, err = trigger_task(t["id"])
            if err == "ok":
                print(f"  TRIGGERED {t['site_id']:<30} log_id={log_id}")
            else:
                print(f"  FAILED   {t['site_id']:<30} error={err}")
                results.append({**t, "result": "trigger_failed", "error": err})

        # Wait for completion
        print(f"  Waiting {args.wait}s for tasks to complete...")
        time.sleep(args.wait)

        # Check results
        for t in batch:
            res = check_task_result(t["id"])
            status = res.get("status", "unknown")
            print(f"  RESULT   {t['site_id']:<30} status={status}")
            if res.get("error_msg"):
                print(f"           error: {res['error_msg'][:100]}")
            results.append({**t, "result": status})

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    ok_count = sum(1 for r in results if r.get("result") in ("ok", "running", "completed"))
    fail_count = sum(1 for r in results if r.get("result") not in ("ok", "running", "completed"))
    print(f"OK/Running: {ok_count}, Failed: {fail_count}")
    for r in results:
        if r.get("result") not in ("ok", "running", "completed"):
            print(f"  FAIL: {r['site_id']} - {r.get('result')} {r.get('error', '')}")


if __name__ == "__main__":
    main()
