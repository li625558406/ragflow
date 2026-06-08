#!/usr/bin/env python3
"""Find scheduled tasks with invalid KB IDs."""
import json, os, sys
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import ScheduledTask, Knowledgebase

# Get all valid KB IDs
valid_kbs = set()
for kb in Knowledgebase.select().dicts():
    valid_kbs.add(kb["id"])

print(f"Total valid KBs: {len(valid_kbs)}")

# Check all tasks
bad = []
ok = []
no_kb = []
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
    kb = r.get("kb_id", "") or ""
    if not kb:
        no_kb.append(sid)
    elif kb not in valid_kbs:
        bad.append({"sid": sid, "kb": kb, "name": r["name"], "tid": r["id"]})
    else:
        ok.append(sid)

print(f"\nTasks with valid KB: {len(ok)}")
print(f"Tasks with invalid KB: {len(bad)}")
print(f"Tasks with no KB: {len(no_kb)}")

if bad:
    print("\n--- Tasks with INVALID KB IDs ---")
    for t in bad:
        print(f"  {t['sid']:<30} kb={t['kb']}  name={t['name']}")

if no_kb:
    print(f"\n--- Tasks with NO KB ---")
    for s in no_kb:
        print(f"  {s}")
