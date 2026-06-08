#!/usr/bin/env python3
"""Get site_ids of tasks with 'running' status and re-trigger them."""
import json, os, sys
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import ScheduledTask
from api.db.services.scheduled_task_service import ScheduledTaskLogService
from api.db.services.scheduled_task_service import ScheduledTaskService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from rag.utils.redis_conn import REDIS_CONN
from common import settings as cfg

# Get tasks with "running" status that don't have finished messages
running_sids = []
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
    if log and log.get("status") == "running":
        running_sids.append(sid)

print(f"Running tasks to re-trigger: {len(running_sids)}")

# Re-trigger each
for sid in running_sids:
    for r in ScheduledTask.select().dicts():
        sp = r.get("script_path", "") or ""
        if "wechat" in sp.lower():
            continue
        sa = r.get("script_args", "") or ""
        try:
            this_sid = json.loads(sa).get("site_id", "")
        except:
            continue
        if this_sid != sid:
            continue
        tid = r["id"]
        REDIS_CONN.delete(f"{tid}-cancel")
        log_id = get_uuid()
        ScheduledTaskLogService.save(
            id=log_id, task_id=tid, tenant_id=r["tenant_id"],
            status="running", start_time=current_timestamp()
        )
        msg = {
            "id": log_id, "task_type": "scheduled_script",
            "tenant_id": r["tenant_id"], "name": r["name"],
            "script_path": r["script_path"],
            "script_args": r["script_args"] or "",
            "timeout": r["timeout"], "task_id_ref": tid,
            "target_url": r.get("target_url", "") or "",
            "llm_id": r.get("llm_id", "") or "",
            "llm_model_name": r.get("llm_model_name", "") or "",
            "kb_id": r.get("kb_id", "") or "",
            "access_token": r.get("access_token", "") or "",
        }
        ok = REDIS_CONN.queue_product(cfg.get_svr_queue_name(0), message=msg)
        print(f"  TRIGGERED: {sid:<30} ok={ok}")
        break
