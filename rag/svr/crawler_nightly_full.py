#!/usr/bin/env python3
"""
Crawler Nightly Full — safety-net re-scan for all enabled sites.

Runs once daily (e.g. 2 AM cron). Sequentially re-scans all enabled sites
from page 1 to catch items the detector might have missed (e.g. retroactively
added items on early pages).

Dedup is fully active:
  - Layer 1 (memory): processed_ids preserved across runs via crawler_state table
  - Layer 2 (DB): bid_project table check always active
  - Only page position is reset to 1; already-crawled items are skipped.

Usage (invoked by task_executor.py as subprocess):
    python rag/svr/crawler_nightly_full.py \
        --tenant-id <TENANT_ID> \
        --kb-id <KB_ID> \
        --task-name <NAME>
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
from rag.svr.crawler_engine.engine import CrawlerEngine

DEFAULT_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "crawler_sites.yaml")


def _safe_print(msg):
    """Print safely, handling Unicode errors on Windows consoles."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Crawler Nightly Full — daily full crawl for all sites"
    )
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--kb-id", required=True, help="Knowledge base ID")
    p.add_argument("--task-name", required=True, help="Task name")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    # Compatibility: task_executor always passes these; nightly ignores them
    p.add_argument("--script-args", default="{}",
                   help="JSON args (ignored by nightly)")
    p.add_argument("--target-url", default="",
                   help="Compatibility (ignored)")
    p.add_argument("--access-token", default="",
                   help="Compatibility (ignored)")
    return p.parse_args()


def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[NIGHTLY] Crawler Nightly Full v1.0")
    _safe_print(f"[NIGHTLY] KB: {args.kb_id}")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    # Initialize RAGFlow settings
    settings.init_settings()
    logging.info("=== Nightly full crawl started ===")

    # Load site configs
    config_path = args.config
    if not os.path.exists(config_path):
        _safe_print(f"[NIGHTLY] ERROR: Config file not found: {config_path}")
        sys.exit(1)

    try:
        loader = ConfigLoader(config_path)
        sites = loader.get_enabled()
    except Exception as e:
        _safe_print(f"[NIGHTLY] ERROR: Failed to load config: {e}")
        sys.exit(1)

    logging.info("Nightly: %d enabled sites to crawl", len(sites))
    _safe_print(f"[NIGHTLY] {len(sites)} sites to crawl\n")
    sys.stdout.flush()

    # Crawl each site sequentially
    results = []
    success = 0
    failed = 0
    skipped = 0
    start_time = time.time()

    for i, site in enumerate(sites, 1):
        _safe_print(f"[NIGHTLY] [{i}/{len(sites)}] Crawling: {site.site_id} ({site.name})...")
        sys.stdout.flush()
        logging.info("Nightly: crawling site=%s (%d/%d)", site.site_id, i, len(sites))

        try:
            engine = CrawlerEngine(site)
            summary = engine.run(
                tenant_id=args.tenant_id,
                kb_id=args.kb_id,
                task_name=args.task_name,
                full=True,
            )

            status = summary.get("status", "unknown")
            new_items = summary.get("total_new_items", summary.get("new_items", 0))

            if status == "skipped":
                reason = summary.get("reason", "unknown")
                skipped += 1
                _safe_print(f"[NIGHTLY]   → SKIPPED: {reason}")
                results.append({"site": site.site_id, "status": "skipped", "reason": reason})
            elif status == "error":
                failed += 1
                _safe_print(f"[NIGHTLY]   → ERROR")
                results.append({"site": site.site_id, "status": "error"})
            else:
                success += 1
                _safe_print(f"[NIGHTLY]   → OK, {new_items} new items")
                results.append({"site": site.site_id, "status": "ok", "new_items": new_items})

        except Exception as e:
            failed += 1
            logging.error("Nightly: site=%s crashed: %s", site.site_id, e, exc_info=True)
            _safe_print(f"[NIGHTLY]   → CRASHED: {e}")
            results.append({"site": site.site_id, "status": "crashed", "error": str(e)})

        # Anti-crawler delay between sites
        if i < len(sites):
            delay = site.anti_crawler.delay_min
            time.sleep(delay)

        sys.stdout.flush()

    # Print summary
    elapsed = time.time() - start_time
    _safe_print(f"\n[NIGHTLY] {'=' * 40}")
    _safe_print(f"[NIGHTLY] Done in {elapsed:.0f}s")
    _safe_print(f"[NIGHTLY] {success} ok, {skipped} skipped, {failed} failed")
    _safe_print(f"[NIGHTLY] {'=' * 40}\n")

    logging.info(
        "=== Nightly finished: ok=%d skipped=%d failed=%d elapsed=%.0fs ===",
        success, skipped, failed, elapsed,
    )


if __name__ == "__main__":
    CONSUMER_NAME = "crawler_nightly_full"
    init_root_logger(CONSUMER_NAME)
    main()
