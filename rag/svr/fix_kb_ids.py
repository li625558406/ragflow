#!/usr/bin/env python3
"""Fix scheduled tasks with invalid KB IDs — point them to the main 招标信息2 KB."""
import sys, os
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import ScheduledTask, Knowledgebase
from playhouse.shortcuts import update_model_from_dict

# Get all valid KB IDs
valid_kbs = set()
for kb in Knowledgebase.select().dicts():
    valid_kbs.add(kb["id"])

# Find main bidding KB (招标信息2)
MAIN_KB = None
for kb in Knowledgebase.select().dicts():
    if "招标信息2" in kb.get("name", ""):
        MAIN_KB = kb["id"]
        break

if not MAIN_KB:
    # Fall back to first KB
    for kb in Knowledgebase.select().dicts():
        MAIN_KB = kb["id"]
        break

print(f"Main KB: {MAIN_KB}")
print(f"Total valid KBs: {len(valid_kbs)}")

# Fix invalid KB IDs
fixed = []
for r in ScheduledTask.select().dicts():
    sp = r.get("script_path", "") or ""
    if "wechat" in sp.lower():
        continue
    kb = r.get("kb_id", "") or ""
    if kb and kb not in valid_kbs:
        sa = r.get("script_args", "") or ""
        sid = ""
        try:
            import json
            sid = json.loads(sa).get("site_id", "")
        except:
            pass
        # Update KB ID
        task = ScheduledTask.get_by_id(r["id"])
        task.kb_id = MAIN_KB
        task.save()
        fixed.append(sid or r["name"])

print(f"\nFixed {len(fixed)} tasks to KB {MAIN_KB}:")
for s in fixed:
    print(f"  {s}")
