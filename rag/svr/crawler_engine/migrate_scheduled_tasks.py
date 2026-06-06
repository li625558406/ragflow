"""
Migrate scheduled_task rows from old individual scripts to unified_crawler.py.

Usage:
    python rag/svr/crawler_engine/migrate_scheduled_tasks.py --tenant-id <TID> --dry-run
    python rag/svr/crawler_engine/migrate_scheduled_tasks.py --tenant-id <TID>
"""

import argparse
import json
import os
import sys
import re

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from api.db.db_models import DB, ScheduledTask
from rag.svr.crawler_engine.migrate_state import SCRIPT_TO_SITE

# New unified entry point
UNIFIED_SCRIPT = "rag/svr/unified_crawler.py"


def extract_basename(script_path: str) -> str:
    """Extract script basename without .py suffix."""
    path = script_path.strip()
    basename = os.path.basename(path)
    if basename.endswith(".py"):
        basename = basename[:-3]
    return basename


def resolve_site_id(script_path: str) -> str:
    """Map an old script path to a new site_id."""
    basename = extract_basename(script_path)
    if basename in SCRIPT_TO_SITE:
        return SCRIPT_TO_SITE[basename]
    clean = basename.replace("_crawler", "")
    if clean in SCRIPT_TO_SITE:
        return SCRIPT_TO_SITE[clean]
    return basename


def migrate_tasks(tenant_id: str, dry_run: bool = False):
    """Migrate all scheduled tasks for a tenant to unified_crawler.py."""
    tasks = list(ScheduledTask.select().where(
        ScheduledTask.tenant_id == tenant_id
    ))

    if not tasks:
        print("No scheduled tasks found.")
        return

    updated = 0
    skipped = 0

    for task in tasks:
        old_path = task.script_path.strip()
        site_id = resolve_site_id(old_path)

        # Skip wechat_mp (excluded from unified crawler)
        if "wechat_mp" in old_path.lower():
            print(f"  [SKIP] {task.name}: wechat_mp excluded")
            skipped += 1
            continue

        # Skip if already using unified_crawler
        if "unified_crawler" in old_path:
            print(f"  [SKIP] {task.name}: already using unified_crawler")
            skipped += 1
            continue

        new_args = json.dumps({"site_id": site_id}, ensure_ascii=False)

        if dry_run:
            print(f"  [DRY-RUN] {task.name}")
            print(f"    script_path: {old_path} -> {UNIFIED_SCRIPT}")
            print(f"    script_args: '{task.script_args}' -> '{new_args}'")
            print(f"    site_id: {site_id}")
        else:
            task.script_path = UNIFIED_SCRIPT
            task.script_args = new_args
            task.save()
            print(f"  [OK] {task.name}: -> site_id={site_id}")

        updated += 1

    print()
    if dry_run:
        print(f"DRY-RUN: would update {updated} tasks, skip {skipped}")
    else:
        print(f"Done: updated {updated} tasks, skipped {skipped}")


def main():
    settings.init_settings()
    init_root_logger("migrate_scheduled_tasks")

    parser = argparse.ArgumentParser(
        description="Migrate scheduled_task rows to unified_crawler.py"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN ===\n")

    migrate_tasks(args.tenant_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
