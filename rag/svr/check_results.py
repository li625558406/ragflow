#!/usr/bin/env python3
"""Check results of recently triggered tasks."""
import json, os, sys
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.services.scheduled_task_service import ScheduledTaskLogService
from api.db.db_models import ScheduledTask, CrawlerState
from api.db.db_models import BidProject

# Check which tasks have completed
results = {}
for r in ScheduledTask.select().dicts():
    sa = r.get("script_args") or ""
    sp = r.get("script_path") or ""
    if "wechat" in sp.lower():
        continue
    try:
        args = json.loads(sa) if isinstance(sa, str) else sa
        sid = args.get("site_id", "")
        if not sid:
            continue
        tid = r["id"]
        log = ScheduledTaskLogService.get_latest_by_task_id(tid)
        if log:
            status = log.get("status", "no_log")
        else:
            status = "no_log"
        results[sid] = status
    except:
        pass

# Count by status
ok = [s for s, st in results.items() if st == "success"]
running = [s for s, st in results.items() if st == "running"]
fail = [s for s, st in results.items() if st not in ("success", "running", "no_log")]
waiting = [s for s, st in results.items() if st == "no_log"]

print(f"Results: {len(results)} tasks checked")
print(f"  success: {len(ok)}")
print(f"  running: {len(running)}")
print(f"  failed:   {len(fail)}")
print(f"  waiting:  {len(waiting)}")
print()
if running:
    print(f"Still running: {running[:10]}...")
if fail:
    print(f"Failed: {fail[:10]}...")
print()

# For failed tasks, show error messages
for sid in fail[:20]:
    for r in ScheduledTask.select().dicts():
        sa = r.get("script_args") or ""
        try:
            args = json.loads(sa) if isinstance(sa, str) else sa
            if args.get("site_id") == sid:
                tid = r["id"]
                log = ScheduledTaskLogService.get_latest_by_task_id(tid)
                if log:
                    err = str(log.get("error_msg", ""))[:200]
                    print(f"FAIL: {sid:<30} err={err}")
                break
        except:
            pass
