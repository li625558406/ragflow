#!/usr/bin/env python3
"""
Crawler Detector — lightweight probe that checks all enabled sites for new content.

Runs every 5 minutes (via scheduled task). For each due site:
  1. SiteDetector.detect() — fetch page-1 listing only
  2. If new items found → enqueue a full crawl via Redis Stream
  3. Update last_check timestamp in Redis

Usage (invoked by task_executor.py as subprocess):
    python rag/svr/crawler_detector.py \
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
from common.misc_utils import get_uuid
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.detector import SiteDetector

try:
    from rag.utils.redis_conn import REDIS_CONN
except ImportError:
    REDIS_CONN = None  # type: ignore

DEFAULT_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "crawler_sites.yaml")


def _safe_print(msg):
    """Print safely, handling Unicode errors on Windows consoles."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Crawler Detector — checks sites for new content"
    )
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--kb-id", required=True, help="Knowledge base ID")
    p.add_argument("--task-name", required=True, help="Task name")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    # Compatibility: task_executor always passes these; detector ignores them
    p.add_argument("--script-args", default="{}",
                   help="JSON args (ignored by detector)")
    p.add_argument("--target-url", default="",
                   help="Compatibility (ignored by detector)")
    p.add_argument("--access-token", default="",
                   help="Compatibility (ignored by detector)")
    p.add_argument("--llm-id", default="",
                   help="Compatibility (ignored by detector)")
    p.add_argument("--llm-model", default="",
                   help="Compatibility (ignored by detector)")
    return p.parse_args()


def _is_due(site, tenant_id: str) -> bool:
    """Check if a site is due for detection based on its interval."""
    if REDIS_CONN is None:
        return True

    interval = site.detect_interval or 300  # default 5 minutes
    key = f"detector:last_check:{site.site_id}:{tenant_id}"
    last = REDIS_CONN.get(key)
    if last is None:
        return True  # never checked before
    return (time.time() - float(last)) >= interval


def _update_last_check(site_id: str, tenant_id: str) -> None:
    """Record the detection timestamp in Redis."""
    if REDIS_CONN is None:
        return
    key = f"detector:last_check:{site_id}:{tenant_id}"
    REDIS_CONN.set(key, str(time.time()))


def _trigger_full_crawl(site_id: str, tenant_id: str, kb_id: str, task_name: str) -> bool:
    """Enqueue a full crawl task for the given site into Redis Stream."""
    if REDIS_CONN is None:
        logging.error("Detector: cannot trigger full crawl — Redis not available")
        return False

    msg = {
        "id": get_uuid(),
        "task_type": "scheduled_script",
        "tenant_id": tenant_id,
        "name": f"detect-trigger:{site_id}",
        "script_path": "rag/svr/unified_crawler.py",
        "script_args": json.dumps({"site_id": site_id}),
        "timeout": 3600,
        "kb_id": kb_id,
    }

    try:
        ok = REDIS_CONN.queue_product(settings.get_svr_queue_name(0), message=msg)
        if ok:
            logging.info("Detector: enqueued full crawl for site=%s", site_id)
        else:
            logging.error("Detector: failed to enqueue full crawl for site=%s", site_id)
        return ok
    except Exception as e:
        logging.error("Detector: enqueue error for site=%s: %s", site_id, e)
        return False


def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[DETECTOR] Crawler Detector v1.0")
    _safe_print(f"[DETECTOR] KB: {args.kb_id}")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    # Initialize RAGFlow settings
    settings.init_settings()
    logging.info("=== Crawler detector started ===")

    # Load site configs
    config_path = args.config
    if not os.path.exists(config_path):
        _safe_print(f"[DETECTOR] ERROR: Config file not found: {config_path}")
        sys.exit(1)

    try:
        loader = ConfigLoader(config_path)
        sites = loader.get_enabled()
    except Exception as e:
        _safe_print(f"[DETECTOR] ERROR: Failed to load config: {e}")
        sys.exit(1)

    logging.info("Detector: loaded %d enabled sites", len(sites))

    # Filter to detection-enabled sites
    detectable = [s for s in sites if s.detect_enabled]
    logging.info("Detector: %d sites with detection enabled", len(detectable))

    # Process each due site
    triggered = 0
    skipped = 0
    errors = 0
    summary_lines = []

    for site in detectable:
        if not _is_due(site, args.tenant_id):
            skipped += 1
            continue

        logging.info("Detector: probing site=%s ...", site.site_id)

        try:
            # Detector is a fast probe — cap retries and timeout to avoid getting stuck
            site.anti_crawler.max_retries = 1
            site.transport.timeout = 10
            detector = SiteDetector(site, args.tenant_id)
            result = detector.detect()

            if result.get("has_new_items"):
                new_count = result.get("new_item_count", 0)
                _safe_print(f"[DETECTOR] {site.site_id}: {new_count} NEW items found — triggering full crawl")
                sys.stdout.flush()

                if _trigger_full_crawl(
                    site.site_id, args.tenant_id, args.kb_id, args.task_name
                ):
                    triggered += 1
                    summary_lines.append(f"  {site.site_id}: +{new_count} new → triggered")
                else:
                    errors += 1
                    summary_lines.append(f"  {site.site_id}: +{new_count} new → enqueue FAILED")
            else:
                scanned = result.get("scanned_count", 0)
                reason = result.get("reason", "no new")
                summary_lines.append(f"  {site.site_id}: {scanned} scanned, {reason}")

        except Exception as e:
            errors += 1
            logging.error("Detector: error probing site=%s: %s", site.site_id, e, exc_info=True)
            summary_lines.append(f"  {site.site_id}: ERROR — {e}")

        # Always update last_check timestamp (even on error, to avoid tight error loops)
        _update_last_check(site.site_id, args.tenant_id)

    # Print summary
    _safe_print(f"\n[DETECTOR] Summary: {triggered} triggered, {skipped} not due, {errors} errors")
    for line in summary_lines:
        _safe_print(line)
    _safe_print("")

    logging.info(
        "=== Detector finished: triggered=%d skipped=%d errors=%d ===",
        triggered, skipped, errors,
    )


if __name__ == "__main__":
    CONSUMER_NAME = "crawler_detector"
    init_root_logger(CONSUMER_NAME)
    main()
