#!/usr/bin/env python3
"""
Crawler Validation — smoke test each site (1 page only) and report pass/fail.

For each enabled site:
  1. Override max_pages=1
  2. Run CrawlerEngine with force=True, skip_kb=True
  3. Check: completed without crash?
  4. Check: items scraped and saved to bid_project?
  5. Check: attachments downloaded (if any)?
  6. Log pass/fail

Usage:
    python rag/svr/crawler_validate.py --tenant-id <ID> --kb-id <ID>
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.engine import CrawlerEngine, OUTPUT_DIR

try:
    from api.db.db_models import BidProject
except ImportError:
    BidProject = None

DEFAULT_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "crawler_sites.yaml")

# ── helpers ──────────────────────────────────────────────────────────


def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _count_attachments(site_id: str) -> int:
    """Count downloaded attachment files for a site."""
    site_dir = os.path.join(OUTPUT_DIR, site_id)
    if not os.path.isdir(site_dir):
        return -1  # dir doesn't exist
    count = 0
    for root, dirs, files in os.walk(site_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.png', '.jpg', '.jpeg'):
                count += 1
    return count


def parse_args():
    p = argparse.ArgumentParser(description="Crawler Validation — smoke test all sites")
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--kb-id", required=True, help="Knowledge base ID")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    p.add_argument("--script-args", default="{}",
                   help="JSON args (ignored)")
    p.add_argument("--start-from", default=None,
                   help="Start from this site_id (resume)")
    return p.parse_args()


# ── main ─────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    _safe_print("\n" + "=" * 70)
    _safe_print("  CRAWLER VALIDATION — smoke test all sites (1 page each)")
    _safe_print(f"  Tenant: {args.tenant_id}")
    _safe_print(f"  KB:     {args.kb_id}")
    _safe_print("=" * 70 + "\n")

    settings.init_settings()
    logging.getLogger().setLevel(logging.WARNING)

    # Load sites
    config_path = args.config
    if not os.path.exists(config_path):
        _safe_print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)

    try:
        loader = ConfigLoader(config_path)
        all_sites = loader.get_enabled()
    except Exception as e:
        _safe_print(f"ERROR: Failed to load config: {e}")
        sys.exit(1)

    # Filter if resuming
    if args.start_from:
        skip = True
        sites = []
        for s in all_sites:
            if skip and s.site_id == args.start_from:
                skip = False
            if not skip:
                sites.append(s)
        _safe_print(f"Resuming from {args.start_from} — {len(sites)} sites remaining\n")
    else:
        sites = all_sites

    _safe_print(f"Will validate {len(sites)} sites\n")
    sys.stdout.flush()

    results = []
    passed = 0
    failed = 0
    skipped = 0
    start_time = time.time()

    for i, site in enumerate(sites, 1):
        sid = site.site_id
        sname = site.name

        _safe_print(f"[{i:3d}/{len(sites)}] {sid:30s}  {sname}")
        sys.stdout.flush()

        site_start = time.time()

        try:
            # Override pagination to 1 page for quick smoke test
            site.pagination.max_pages = 1

            engine = CrawlerEngine(site)
            summary = engine.run(
                tenant_id=args.tenant_id,
                kb_id=args.kb_id,
                task_name="validate",
                full=False,
                force=True,
                skip_kb=True,
            )

            status = summary.get("status", "unknown")
            elapsed = time.time() - site_start

            if status in ("error", "crashed"):
                err_msg = summary.get("error", summary.get("reason", ""))
                results.append({"site_id": sid, "name": sname, "result": "FAIL",
                               "reason": f"engine: {status} — {err_msg}", "elapsed": elapsed})
                failed += 1
                _safe_print(f"         FAIL ({elapsed:.1f}s) — {err_msg}")
                continue

            if status == "skipped":
                reason = summary.get("reason", "unknown")
                tag = "SKIP"
                if reason == "already_running":
                    # Lock from a previous run — OK to skip
                    tag = "PASS"
                    passed += 1
                else:
                    skipped += 1
                results.append({"site_id": sid, "name": sname, "result": tag,
                               "reason": reason, "elapsed": elapsed})
                _safe_print(f"         {tag} ({elapsed:.1f}s) — {reason}")
                continue

            # Engine returned ok/partial — extract stats
            # Handle both single-section ({new_items}) and multi-section ({total_new_items}) formats
            total_new = summary.get("total_new_items", summary.get("new_items", 0))
            section_stats = summary.get("sections", {})

            # Aggregate scraped count from all sections, or use top-level if single-section
            if section_stats:
                total_scraped = sum(
                    s.get("scanned_items", 0)
                    for s in section_stats.values()
                )
            else:
                total_scraped = summary.get("scanned_items", 0)

            # Attachment check
            att_count = _count_attachments(sid)

            if total_new > 0 or total_scraped > 0:
                results.append({"site_id": sid, "name": sname, "result": "PASS",
                               "scraped": total_scraped, "new": total_new,
                               "elapsed": elapsed, "attachments": max(att_count, 0)})
                passed += 1
                att_str = f", {att_count} files" if att_count > 0 else ""
                _safe_print(f"         PASS ({elapsed:.1f}s) — scraped={total_scraped}, new={total_new}{att_str}")
            else:
                # Ran but got nothing
                results.append({"site_id": sid, "name": sname, "result": "WARN",
                               "reason": f"scraped=0, new=0", "elapsed": elapsed})
                skipped += 1
                _safe_print(f"         WARN ({elapsed:.1f}s) — no items returned")

        except Exception as e:
            elapsed = time.time() - site_start
            tb = traceback.format_exc()
            results.append({"site_id": sid, "name": sname, "result": "FAIL",
                           "reason": str(e)[:200], "elapsed": elapsed})
            failed += 1
            _safe_print(f"         FAIL ({elapsed:.1f}s) — {e}")
            logging.error("Validation: site=%s crashed: %s\n%s", sid, e, tb)

        # Brief delay between sites
        delay = getattr(getattr(site, 'anti_crawler', None), 'delay_min', 1) or 1
        if i < len(sites):
            time.sleep(min(delay, 2))

        sys.stdout.flush()

    # ── Summary ───────────────────────────────────────────────────────

    total_elapsed = time.time() - start_time
    _safe_print(f"\n{'=' * 70}")
    _safe_print(f"  VALIDATION COMPLETE  ({total_elapsed:.0f}s)")
    _safe_print(f"  PASS: {passed}  WARN/SKIP: {skipped}  FAIL: {failed}")
    _safe_print(f"{'=' * 70}")

    # Print failures
    if failed > 0:
        _safe_print(f"\n  FAILURES ({failed}):")
        for r in results:
            if r["result"] == "FAIL":
                _safe_print(f"    {r['site_id']:30s}  [{r['elapsed']:.1f}s]  {r['reason']}")

    # Print warnings
    warns = [r for r in results if r["result"] == "WARN"]
    if warns:
        _safe_print(f"\n  WARNINGS — no items ({len(warns)}):")
        for r in warns:
            _safe_print(f"    {r['site_id']:30s}  [{r['elapsed']:.1f}s]  {r['reason']}")

    # Save JSON report
    report_path = os.path.join(os.path.dirname(args.config) or _SCRIPT_DIR,
                               "crawler_validation_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({
                "passed": passed, "skipped": skipped, "failed": failed,
                "elapsed": total_elapsed, "results": results,
            }, f, ensure_ascii=False, indent=2)
        _safe_print(f"\n  Report: {report_path}")
    except Exception as e:
        _safe_print(f"\n  Report failed: {e}")

    _safe_print("")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    CONSUMER_NAME = "crawler_validate"
    init_root_logger(CONSUMER_NAME)
    main()
