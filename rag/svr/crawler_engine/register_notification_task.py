"""Register notification_generator as a meta-task (interval=120s).

仿 register_detector_task.py 同套路：在 scheduled_task 表幂等插入一行，
script_path 指向 notification_generator.py，由 scheduled_task_executor 调度。
"""
import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

NOTIF_SCRIPT = "rag/svr/notification_generator.py"
NOTIF_TASK_NAME = "[notification] collection new-result meta-task"
# 固定 id 前缀：re-run 时 upsert 而非重复插入。完整 id = f"notification-meta-{tenant_id}".
NOTIF_TASK_ID_PREFIX = "notification-meta-"


def ensure_notification_task(
    tenant_id: str = "system",
    interval_seconds: int = 120,
    enabled: bool = True,
    timeout: int = 180,
) -> dict:
    """幂等插入或更新通知 meta-task。返回该行 dict。"""
    from api.db.db_models import DB
    from api.db.services.scheduled_task_service import ScheduledTaskService
    from common.time_utils import current_timestamp

    task_id = f"{NOTIF_TASK_ID_PREFIX}{tenant_id}"

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
            "name": NOTIF_TASK_NAME,
            "description": "Meta task: scan crawler_result for new items and "
                           "generate notifications every 120s",
            "script_path": NOTIF_SCRIPT,
            "script_args": "--once",
            "schedule_type": "interval",
            "cron_expression": "",
            "interval_seconds": interval_seconds,
            "enabled": enabled,
            "timeout": timeout,
            "kb_id": "",
            "target_url": "",
            "next_run_time": next_run,
        }
        if existing:
            ScheduledTaskService.update_by_id(task_id, {
                "interval_seconds": interval_seconds,
                "enabled": enabled,
                "timeout": timeout,
                "next_run_time": next_run,
            })
        else:
            ScheduledTaskService.insert(**payload)

        row = (
            ScheduledTaskService.model
            .select()
            .where(ScheduledTaskService.model.id == task_id)
            .first()
        )
        return row.to_dict() if row else payload

    return _upsert()


def disable_notification_task(tenant_id: str) -> bool:
    from api.db.db_models import DB
    from api.db.services.scheduled_task_service import ScheduledTaskService
    task_id = f"{NOTIF_TASK_ID_PREFIX}{tenant_id}"

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
    p = argparse.ArgumentParser(description="Register notification meta-task")
    p.add_argument("--tenant-id", default="system")
    p.add_argument("--interval", type=int, default=120,
                   help="notification scan interval in seconds (default 120)")
    p.add_argument("--disable", action="store_true",
                   help="Disable the meta-task instead of enabling it")
    args = p.parse_args()

    if args.disable:
        ok = disable_notification_task(args.tenant_id)
        print(f"disabled={ok}")
        return

    row = ensure_notification_task(
        tenant_id=args.tenant_id,
        interval_seconds=args.interval,
        enabled=True,
    )
    print(f"notification meta-task ready: id={row['id']} interval={args.interval}s "
          f"enabled={row.get('enabled')}")


if __name__ == "__main__":
    main()
