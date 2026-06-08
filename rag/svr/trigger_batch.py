#!/usr/bin/env python3
"""Trigger a batch of scheduled tasks by site_id."""
import json, os, sys
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.services.scheduled_task_service import ScheduledTaskService, ScheduledTaskLogService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from rag.utils.redis_conn import REDIS_CONN
from common import settings as cfg
from api.db.db_models import ScheduledTask

site_ids = sys.argv[1:] if len(sys.argv) > 1 else []
if not site_ids:
    print("Usage: trigger_batch.py site1 site2 site3")
    sys.exit(1)

for sid in site_ids:
    for r in ScheduledTask.select().dicts():
        sa = r.get("script_args") or ""
        try:
            args = json.loads(sa) if isinstance(sa, str) else sa
            if args.get("site_id") == sid:
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
                print(f"TRIGGERED: {sid} task={tid[:8]} ok={ok}")
                break
        except Exception as e:
            print(f"ERROR: {sid} - {e}")
