#!/usr/bin/env python3
"""Rebuild crawler_state.processed_ids from bid_project table data.

Scans all bid_project records with source_type='crawler', extracts the
original item_id (URL or API ID) from raw_json, and writes them into
the corresponding crawler_state record's processed_ids.

Also resets corrupted last_page values to 0.
"""
import json, os, sys, logging
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import BidProject, CrawlerState
from common.misc_utils import get_uuid
import peewee

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Collect all crawler projects grouped by site_id
projects_by_site = {}
query = (BidProject
          .select(BidProject.id, BidProject.raw_json)
          .where(BidProject.source_type == "crawler"))

total = 0
for row in query.iterator():
    total += 1
    try:
        meta = row.raw_json
        if isinstance(meta, str):
            # Handle double-encoded JSON (Peewee JSONField quirk)
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if isinstance(meta, str):
            # Still a string after first decode
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}

    site_id = meta.get("crawler_site_id", "")
    url = meta.get("crawler_url", "")

    # The item_id for dedup is whatever the engine used.
    # For URL-based items, it's the URL. For id-based items, we use
    # the bid_project id as a proxy (since gen_bid_id(url) == row.id).
    # Store both url (for memory dedup) and row.id (for reference).
    item_id = url if url else str(row.id)

    if not site_id:
        continue

    if site_id not in projects_by_site:
        projects_by_site[site_id] = set()
    projects_by_site[site_id].add(item_id)

print("Scanned %d bid_project records (crawler)" % total)
print("Found %d unique site_ids" % len(projects_by_site))

# 2. For each site, update or create crawler_state record
rebuilt = 0
for site_id, item_ids in sorted(projects_by_site.items()):
    count = len(item_ids)
    try:
        row, created = CrawlerState.get_or_create(
            site_id=site_id,
            tenant_id="default",
            section="default",
            defaults={
                "id": get_uuid(),
                "processed_ids": list(item_ids),
                "last_page": 0,
                "last_offset": 0,
                "extra_state": {},
            },
        )
        if not created:
            existing = set(row.processed_ids or [])
            merged = existing | item_ids
            if merged != existing:
                row.processed_ids = list(merged)
                row.last_page = 0  # Reset corrupted page
                row.save()
                print("  UPDATED %s: %d + %d = %d total" % (
                    site_id, len(existing), count, len(merged)))
            else:
                print("  OK %s: already has %d IDs (bid_project has %d)" % (
                    site_id, len(existing), count))
        else:
            print("  CREATED %s: %d IDs from bid_project" % (site_id, count))
        rebuilt += 1
    except Exception as e:
        print("  ERROR %s: %s" % (site_id, e))

# 3. Reset corrupted last_page values for ALL crawler_state records
#    Any last_page > 0 with 0 processed_ids is likely corrupted by concurrent runs
print("\n--- Resetting corrupted last_page values ---")
corrupted = 0
for row in CrawlerState.select():
    id_count = len(row.processed_ids or [])
    page = row.last_page or 0
    # If page is very high but no processed IDs, it's corrupted
    if page > 100 and id_count < page * 2:
        old_page = page
        row.last_page = 0
        row.save()
        print("  RESET %s/%s/%s: page %d -> 0 (had %d IDs)" % (
            row.site_id, row.tenant_id, row.section, old_page, id_count))
        corrupted += 1

print("\n=== SUMMARY ===")
print("Total bid_project records scanned: %d" % total)
print("Sites rebuilt/created: %d" % rebuilt)
total_ids = sum(len(ids) for ids in projects_by_site.values())
print("Total processed_ids written: %d" % total_ids)
print("Corrupted last_page reset: %d" % corrupted)
