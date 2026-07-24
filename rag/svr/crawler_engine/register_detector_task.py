"""
Register the crawler_detector meta-task in the scheduled_task table.

The detector itself is a *probe loop* — it needs to be invoked periodically
(every 60s).  We piggy-back on the existing scheduled_task_executor: this
module idempotently inserts a single row pointing to crawler_detector.py.
Run it once per tenant after deploy (or call ``ensure_detector_task`` from
code).

Identification: rows created here carry a fixed ``DETECTOR_TASK_ID_SUFFIX``
so re-runs upsert instead of duplicating.

CLI:
    python rag/svr/crawler_engine/register_detector_task.py \\
        --tenant-id <TID> --kb-id <KID> [--interval 60] [--enable/--disable]
"""

import argparse
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.time_utils import current_timestamp

DETECTOR_SCRIPT = "rag/svr/crawler_detector.py"
DETECTOR_TASK_NAME = "[detector] collection probe meta-task"
# Fixed id suffix so re-runs are idempotent per tenant.
# Full id = f"detector-{tenant_id}" (deterministic, easy to debug).
DETECTOR_TASK_ID_PREFIX = "detector-meta-"


def ensure_detector_task(
    tenant_id: str,
    kb_id: str,
    interval_seconds: int = 60,
    enabled: bool = True,
    timeout: int = 300,
) -> dict:
    """Idempotently insert or update the detector meta-task. Returns the row dict."""
    from api.db.db_models import DB
    from api.db.services.scheduled_task_service import ScheduledTaskService

    task_id = f"{DETECTOR_TASK_ID_PREFIX}{tenant_id}"

    @DB.connection_context()
    def _upsert() -> dict:
        existing = (
            ScheduledTaskService.model
            .select()
            .where(ScheduledTaskService.model.id == task_id)
            .first()
        )
        next_run = current_timestamp() + interval_seconds * 1000
        payload = {
            "id": task_id,
            "tenant_id": tenant_id,
            "name": DETECTOR_TASK_NAME,
            "description": "Meta task: invokes crawler_detector.py every minute "
                           "to probe all collection sites",
            "script_path": DETECTOR_SCRIPT,
            "script_args": "{}",
            "schedule_type": "interval",
            "cron_expression": None,
            "interval_seconds": interval_seconds,
            "enabled": enabled,
            "timeout": timeout,
            "kb_id": kb_id,
            "target_url": "",
            "next_run_time": next_run,
        }
        if existing:
            ScheduledTaskService.update_by_id(task_id, {
                "kb_id": kb_id,
                "interval_seconds": interval_seconds,
                "enabled": enabled,
                "timeout": timeout,
                "next_run_time": next_run,
            })
        else:
            ScheduledTaskService.insert(payload)

        row = (
            ScheduledTaskService.model
            .select()
            .where(ScheduledTaskService.model.id == task_id)
            .first()
        )
        return row.to_dict() if row else payload

    return _upsert()


def disable_detector_task(tenant_id: str) -> bool:
    from api.db.db_models import DB
    from api.db.services.scheduled_task_service import ScheduledTaskService
    task_id = f"{DETECTOR_TASK_ID_PREFIX}{tenant_id}"

    @DB.connection_context()
    def _disable() -> bool:
        existing = (
            ScheduledTaskService.model
            .select()
            .where(ScheduledTaskService.model.id == task_id)
            .first()
        )
        if not existing:
            return False
        ScheduledTaskService.update_by_id(task_id, {"enabled": False})
        return True

    return _disable()


def main():
    settings.init_settings()
    init_root_logger("register_detector_task")

    p = argparse.ArgumentParser(description="Register crawler_detector meta-task")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--kb-id", required=True,
                   help="KB id passed through to unified_crawler on enqueue")
    p.add_argument("--interval", type=int, default=60,
                   help="Detector meta-task interval in seconds (default 60)")
    p.add_argument("--disable", action="store_true",
                   help="Disable the meta-task instead of enabling it")
    args = p.parse_args()

    if args.disable:
        ok = disable_detector_task(args.tenant_id)
        print(f"disabled={ok}")
        return

    row = ensure_detector_task(
        tenant_id=args.tenant_id,
        kb_id=args.kb_id,
        interval_seconds=args.interval,
        enabled=True,
    )
    print(f"detector meta-task ready: id={row['id']} interval={args.interval}s "
          f"enabled={row.get('enabled')}")


if __name__ == "__main__":
    main()
