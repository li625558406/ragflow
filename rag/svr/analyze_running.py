#!/usr/bin/env python3
"""Analyze running sites for potential issues."""
import json, os, sys
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

import yaml
from common import settings
settings.init_settings()
from api.db.db_models import ScheduledTask
from api.db.services.scheduled_task_service import ScheduledTaskLogService

with open("/ragflow/rag/svr/crawler_sites.yaml") as f:
    cfg = yaml.safe_load(f)

sites = cfg.get("sites", cfg)
# Get running site_ids
running = set()
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
        running.add(sid)

print("Running sites: %d" % len(running))

for sid in sorted(running):
    s = sites.get(sid, {})
    if not s:
        print("  MISSING: %s" % sid)
        continue
    t = s.get("transport", {})
    tt = t.get("type", "?")
    listing = s.get("listing", {})
    url = listing.get("url", "?")
    method = listing.get("method", "GET")
    extract = s.get("extract", {})
    et = extract.get("type", "?")
    ip = extract.get("items_path", "")
    fields = extract.get("fields", {})
    has_url_field = "url" in fields or "id" in fields

    issues = []
    if tt in ("spa_render", "playwright_http", "encrypted_api"):
        issues.append(tt.upper())
    if et == "css_selector" and not ip:
        issues.append("NO_ITEMS_PATH")
    if not has_url_field and et == "css_selector":
        issues.append("NO_URL_FIELD")

    status = " | ".join(issues) if issues else "OK"
    print("  %-30s %-12s %-6s %s" % (sid[:30], tt[:12], et[:6], status))
