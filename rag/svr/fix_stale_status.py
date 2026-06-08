#!/usr/bin/env python3
"""Fix stale 'running' statuses — mark all as failed if no crawler process is alive."""
import json, os, sys, subprocess
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import ScheduledTask, ScheduledTaskLog
from api.db.services.scheduled_task_service import ScheduledTaskLogService
from common.time_utils import current_timestamp

# Get all PIDs of running crawler processes
try:
    result = subprocess.run(
        ["docker", "exec", "docker-ragflow-cpu-1", "ps", "aux"],
        capture_output=True, text=True
    )
    alive_pids = set()
    for line in result.stdout.splitlines():
        if "unified_crawler" in line and "grep" not in line and "tail" not in line:
            parts = line.split()
            if len(parts) > 1:
                alive_pids.add(int(parts[1]))
except Exception as e:
    print(f"PS error: {e}")
    alive_pids = set()

print(f"Alive crawler PIDs: {len(alive_pids)}")

# Now check running tasks
fixed = 0
for r in ScheduledTask.select().dicts():
    sp = r.get("script_path", "") or ""
    if "wechat" in sp.lower():
        continue
    sa = r.get("script_args", "") or ""
    sid = ""
    try:
        sid = json.loads(sa).get("site_id", "")
    except:
        pass
    tid = r["id"]
    log = ScheduledTaskLogService.get_latest_by_task_id(tid)
    if not log or log.get("status") != "running":
        continue

    # Check if log_id's PID is still alive
    log_id = log.get("id", "")
    # We can't easily map log_id to PID, so check differently
    # Mark all as failed if no crawler is alive
    if not alive_pids:
        log_obj = ScheduledTaskLog.get_or_none(ScheduledTaskLog.id == log_id)
        if log_obj:
            log_obj.status = "failed"
            log_obj.end_time = current_timestamp()
            log_obj.error_msg = "Process terminated externally"
            log_obj.save()
            fixed += 1
            print(f"  FIXED: {sid:<30} log={log_id[:12]}")

print(f"\nFixed {fixed} stale 'running' statuses")
