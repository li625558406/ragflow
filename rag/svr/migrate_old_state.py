"""One-shot: migrate old _crawler_state.json files to crawler_state DB table."""
import sys, json, os, glob

sys.path.insert(0, "/ragflow")
from common import settings
settings.init_settings()

from api.db.db_models import DB, CrawlerState, ScheduledTask
from common.misc_utils import get_uuid

TENANT = "7ab771d4dec84f23b2c1fb5f4e453ff9"


def script_path_to_site_id(script_path, script_args="{}"):
    """Extract site_id from script_path or script_args.

    For unified_crawler.py, extract site_id from script_args JSON.
    For old scripts, derive site_id from filename.
    """
    if script_path and "unified_crawler" in script_path:
        # Unified crawler: site_id comes from script_args
        try:
            args = json.loads(script_args)
            return args.get("site_id", "")
        except Exception:
            return ""

    if not script_path:
        return None
    basename = os.path.basename(script_path).replace(".py", "")
    for suffix in ("_crawler", "_spider", "_bot"):
        if basename.endswith(suffix):
            basename = basename[:-len(suffix)]
    return basename


# Build task_name -> (script_path, script_args) from DB
tasks = list(ScheduledTask.select(
    ScheduledTask.name, ScheduledTask.script_path, ScheduledTask.script_args
).where(ScheduledTask.tenant_id == TENANT))
task_map = {}
for t in tasks:
    task_map[t.name.strip()] = (t.script_path, t.script_args or "{}")
print(f"ScheduledTasks: {len(task_map)}")

# Find all state files
state_files = (
    glob.glob("/ragflow/rag/**/_crawler_state.json", recursive=True)
    + glob.glob("/ragflow/rag/svr/**/_crawler_state.json", recursive=True)
)
print(f"State files: {len(state_files)}")

migrated = 0
errors = 0

for sf in state_files:
    dir_name = os.path.basename(os.path.dirname(sf))

    # Resolve site_id
    entry = task_map.get(dir_name)
    if entry:
        sp, sa = entry
        site_id = script_path_to_site_id(sp, sa)
    else:
        site_id = None

    # Skip test directories and unresolvable mappings
    if not site_id or "_test" in dir_name:
        continue

    # Load old state
    try:
        with open(sf, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        continue

    ids = state.get("processed_ids", state.get("processed_urls", []))
    if not ids:
        continue

    new_ids = set(str(x) for x in ids)

    try:
        existing = CrawlerState.get_or_none(
            (CrawlerState.site_id == site_id)
            & (CrawlerState.tenant_id == TENANT)
            & (CrawlerState.section == "default")
        )
        if existing:
            old_ids = set(str(x) for x in (existing.processed_ids or []))
            merged = old_ids | new_ids
            added = len(merged) - len(old_ids)
            if added > 0:
                existing.processed_ids = list(merged)
                existing.save()
                print(f"  [MERGE] {dir_name} -> {site_id}: +{added} (total={len(merged)})")
        else:
            CrawlerState.create(
                id=get_uuid(),
                site_id=site_id,
                tenant_id=TENANT,
                section="default",
                processed_ids=list(new_ids),
            )
            print(f"  [NEW] {dir_name} -> {site_id}: {len(new_ids)} IDs")
        migrated += 1
    except Exception as e:
        errors += 1
        print(f"  [ERR] {dir_name}: {e}")

# Summary
rows = list(CrawlerState.select().where(CrawlerState.tenant_id == TENANT))
total_ids = sum(len(r.processed_ids or []) for r in rows)
print(f"\nResult: migrated={migrated}, errors={errors}")
print(f"DB: {len(rows)} site rows, {total_ids} total processed IDs")
