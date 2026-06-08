#!/usr/bin/env python3
"""Fix task statuses from unified_crawler.log actual results."""
import json, os, sys, re
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import ScheduledTask, ScheduledTaskLog
from api.db.services.scheduled_task_service import ScheduledTaskLogService

# Parse the log for all finished messages
log_path = "/ragflow/logs/unified_crawler.log"
site_status = {}  # site_id -> status

with open(log_path, "r") as f:
    for line in f:
        m = re.search(r'Unified crawler finished: site=(\S+), status=(\S+)', line)
        if m:
            site_status[m.group(1)] = m.group(2)

print(f"Found {len(site_status)} site results in log")

# Status mapping for DB
STATUS_MAP = {
    "completed": "success",
    "skipped": "success",
    "empty": "success",
    "unknown": "failed",
    "error": "failed",
    "cancelled": "failed",
}

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
    if not log or log.get("status") != "failed":
        continue
    if log.get("error_msg", "") != "Process terminated externally":
        continue

    # Check if we have a log result for this site
    actual = site_status.get(sid)
    if actual:
        db_status = STATUS_MAP.get(actual, "failed")
        if db_status != "failed":
            log_obj = ScheduledTaskLog.get_or_none(ScheduledTaskLog.id == log["id"])
            if log_obj:
                old = log_obj.status
                log_obj.status = db_status
                log_obj.error_msg = ""
                log_obj.save()
                fixed += 1
                print(f"  FIXED: {sid:<30} {old} -> {db_status} (log: {actual})")

print(f"\nFixed {fixed} statuses from log results")
