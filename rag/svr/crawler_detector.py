#!/usr/bin/env python3
"""
Crawler Detector — lightweight probe that checks all enabled collection sites
for new content.

Designed to run as a meta scheduled task (interval=60s) that is picked up by
``scheduled_task_executor``.  Each invocation:

  1. Loads all enabled + detect_enabled sites from crawler_sites.yaml
  2. For each site whose ``next_run_at`` has arrived (stored in Redis):
     a. Skip on quiet_hours / auto_disabled / already-probing lock
     b. Call SiteDetector.detect() — fetches page-1 only, computes signature
     c. Compare with last signature
        - changed → enqueue unified_crawler (writer=collection, date_filter=today)
                    miss_count = 0
        - unchanged → miss_count += 1
     d. Compute next interval via exponential backoff (capped at detect_max_interval)
     e. Persist state to Redis
  3. Print summary

Usage (invoked by task_executor.py as subprocess):
    python rag/svr/crawler_detector.py \\
        --tenant-id <TENANT_ID> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.detector import SiteDetector

try:
    from rag.utils.redis_conn import REDIS_CONN, RedisDistributedLock
except ImportError:
    REDIS_CONN = None  # type: ignore
    RedisDistributedLock = None  # type: ignore

DEFAULT_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "crawler_sites.yaml")

# Persist detector state for 30 days so inactive sites don't lose backoff info
STATE_TTL = 30 * 86400

# Consecutive probe failures before a site is auto-disabled
AUTO_DISABLE_THRESHOLD = 5

# Maximum probe duration before the per-site lock auto-expires (also serves as
# a safety bound: a probe taking longer than this is considered stuck)
PROBE_LOCK_TIMEOUT = 120


def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Crawler Detector — checks collection sites for new content"
    )
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--kb-id", required=True, help="Knowledge base ID")
    p.add_argument("--task-name", required=True, help="Task name")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    # Compatibility: task_executor always passes these; detector ignores them
    p.add_argument("--script-args", default="{}",
                   help="JSON args (parsed for force options, rest ignored)")
    p.add_argument("--target-url", default="", help="Compatibility (ignored)")
    p.add_argument("--access-token", default="", help="Compatibility (ignored)")
    p.add_argument("--llm-id", default="", help="Compatibility (ignored)")
    p.add_argument("--llm-model", default="", help="Compatibility (ignored)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# State helpers (single JSON blob per site)
# ---------------------------------------------------------------------------

STATE_KEY = "detector:state:{tenant}:{site}"
LOCK_KEY = "detector:lock:{tenant}:{site}"
FORCE_KEY = "detector:force:{tenant}:{site}"  # set by /detect/trigger API


def _load_state(tenant_id: str, site_id: str) -> Dict[str, Any]:
    if REDIS_CONN is None:
        return {}
    key = STATE_KEY.format(tenant=tenant_id, site=site_id)
    try:
        raw = REDIS_CONN.get(key)
        if not raw:
            return {}
        return json.loads(raw)
    except Exception as e:
        logging.warning("detector: failed to load state for %s: %s", site_id, e)
        return {}


def _save_state(tenant_id: str, site_id: str, state: Dict[str, Any]) -> None:
    if REDIS_CONN is None:
        return
    key = STATE_KEY.format(tenant=tenant_id, site=site_id)
    try:
        REDIS_CONN.set_obj(key, state, exp=STATE_TTL)
    except Exception as e:
        logging.warning("detector: failed to save state for %s: %s", site_id, e)


def _clear_force(tenant_id: str, site_id: str) -> None:
    if REDIS_CONN is None:
        return
    try:
        REDIS_CONN.delete(FORCE_KEY.format(tenant=tenant_id, site=site_id))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

def _in_quiet_hours(spec: str, now: Optional[datetime] = None) -> bool:
    """Parse "0-7" style range (24h, local time). Returns True if inside."""
    if not spec or "-" not in spec:
        return False
    try:
        parts = spec.split("-")
        start_h = int(parts[0])
        end_h = int(parts[1])
    except (ValueError, IndexError):
        return False
    if start_h == end_h:
        return False
    now = now or datetime.now()
    cur = now.hour
    if start_h < end_h:
        return start_h <= cur < end_h
    # wrap-around (e.g. "22-6")
    return cur >= start_h or cur < end_h


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------

def _next_interval(miss_count: int, base: int, cap: int) -> int:
    """Exponential backoff: base * 2^miss_count, capped."""
    if miss_count <= 0:
        return base
    return min(base * (2 ** miss_count), cap)


# ---------------------------------------------------------------------------
# Enqueue full crawl
# ---------------------------------------------------------------------------

def _enqueue_full_crawl(site_id: str, category: str,
                        tenant_id: str, kb_id: str) -> bool:
    if REDIS_CONN is None:
        logging.error("detector: cannot enqueue — Redis not available")
        return False
    if not kb_id:
        logging.error(
            "detector: cannot enqueue crawl for site=%s — meta-task kb_id is "
            "empty. Re-register via /collection/detect/install.",
            site_id,
        )
        return False

    msg = {
        "id": get_uuid(),
        "task_type": "scheduled_script",
        "tenant_id": tenant_id,
        "name": f"detect:{site_id}",
        "script_path": "rag/svr/unified_crawler.py",
        "script_args": json.dumps({
            "site_id": site_id,
            "writer": "collection",     # ★ route to new collection pipeline
            "category": category,        # ★ from YAML, drives crawler_result.category
            "date_filter": "today",      # ★ only store today's items (dedup-friendly)
        }, ensure_ascii=False),
        "timeout": 3600,
        "task_id_ref": "",
        "target_url": "",
        "llm_id": "",
        "llm_model_name": "",
        "kb_id": kb_id,
        "access_token": "",
    }
    try:
        ok = REDIS_CONN.queue_product(settings.get_svr_queue_name(0), message=msg)
        if ok:
            logging.info("detector: enqueued collection crawl for site=%s", site_id)
        else:
            logging.error("detector: enqueue failed for site=%s", site_id)
        return ok
    except Exception as e:
        logging.error("detector: enqueue error for site=%s: %s", site_id, e)
        return False


# ---------------------------------------------------------------------------
# Per-site probe
# ---------------------------------------------------------------------------

def probe_one_site(site, tenant_id: str, kb_id: str) -> Dict[str, Any]:
    """Probe a single site end-to-end: load state → probe → save state."""
    site_id = site.site_id
    now = int(time.time())
    state = _load_state(tenant_id, site_id)

    # Respect auto-disable
    if state.get("auto_disabled"):
        return {"site_id": site_id, "status": "auto_disabled"}

    # Respect force-trigger flag (set by /detect/trigger API)
    forced = REDIS_CONN is not None and REDIS_CONN.exist(
        FORCE_KEY.format(tenant=tenant_id, site=site_id)
    )
    if not forced:
        next_run_at = int(state.get("next_run_at") or 0)
        if now < next_run_at:
            return {"site_id": site_id, "status": "not_due",
                    "next_run_at": next_run_at, "now": now}

        # Quiet hours: push next_run_at forward by 1h and bail
        if _in_quiet_hours(site.detect_quiet_hours or ""):
            _save_state(tenant_id, site_id, {
                **state,
                "next_run_at": now + 3600,
                "last_check": now,
                "quiet_skipped": int(state.get("quiet_skipped", 0)) + 1,
            })
            return {"site_id": site_id, "status": "quiet_hours"}

    # Acquire per-site probe lock (prevents overlap if meta-task beats us)
    if RedisDistributedLock is not None:
        lock = RedisDistributedLock(
            LOCK_KEY.format(tenant=tenant_id, site=site_id),
            timeout=PROBE_LOCK_TIMEOUT, blocking_timeout=0,
        )
        if not lock.acquire():
            return {"site_id": site_id, "status": "already_probing"}
    else:
        lock = None

    try:
        # Tighten retry/timeout for probe speed
        site.anti_crawler.max_retries = 1
        site.transport.timeout = 10

        detector = SiteDetector(site, tenant_id, collection_mode=True)
        result = detector.detect()
    except Exception as e:
        logging.error("detector: probe crashed for site=%s: %s",
                      site_id, e, exc_info=True)
        consecutive_errors = int(state.get("consecutive_errors", 0)) + 1
        auto_disabled = consecutive_errors >= AUTO_DISABLE_THRESHOLD
        new_state = {
            **state,
            "last_check": now,
            "consecutive_errors": consecutive_errors,
            "auto_disabled": auto_disabled,
            "next_run_at": now + int(state.get("cur_interval",
                                                site.detect_interval)),
            "last_error": str(e)[:200],
        }
        _save_state(tenant_id, site_id, new_state)
        if forced:
            _clear_force(tenant_id, site_id)
        return {"site_id": site_id, "status": "error", "error": str(e),
                "consecutive_errors": consecutive_errors,
                "auto_disabled": auto_disabled}
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass

    # Successful probe — compute backoff
    has_new = bool(result.get("has_new_items"))
    base = site.detect_min_interval or site.detect_interval
    cap = site.detect_max_interval or 3600
    new_miss = 0 if has_new else int(state.get("miss_count", 0)) + 1
    new_interval = _next_interval(new_miss, base, cap)

    if has_new:
        category = (getattr(site, "category", "") or "bid").strip()
        enq_ok = _enqueue_full_crawl(site_id, category, tenant_id, kb_id)
    else:
        enq_ok = False

    new_state = {
        "next_run_at": now + new_interval,
        "last_sig": result.get("signature", ""),
        "miss_count": new_miss,
        "cur_interval": new_interval,
        "last_check": now,
        "consecutive_errors": 0,
        "last_new_count": int(result.get("new_item_count", 0)) if has_new else 0,
        "last_reason": result.get("reason", ""),
        "last_enqueue_ok": enq_ok,
        "auto_disabled": False,
    }
    _save_state(tenant_id, site_id, new_state)
    if forced:
        _clear_force(tenant_id, site_id)

    return {
        "site_id": site_id,
        "status": "ok",
        "has_new": has_new,
        "new_count": result.get("new_item_count", 0),
        "scanned": result.get("scanned_count", 0),
        "signature": result.get("signature", ""),
        "last_signature": result.get("last_signature", ""),
        "reason": result.get("reason", ""),
        "next_interval": new_interval,
        "enqueued": enq_ok,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[DETECTOR] Crawler Detector v2.0 (collection mode)")
    _safe_print(f"[DETECTOR] KB: {args.kb_id}")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Crawler detector started (tenant=%s) ===", args.tenant_id)

    if not os.path.exists(args.config):
        _safe_print(f"[DETECTOR] ERROR: Config file not found: {args.config}")
        sys.exit(1)

    try:
        loader = ConfigLoader(args.config)
        sites = loader.get_enabled()
    except Exception as e:
        _safe_print(f"[DETECTOR] ERROR: Failed to load config: {e}")
        sys.exit(1)

    detectable = [s for s in sites if s.detect_enabled]
    logging.info("detector: %d enabled sites, %d with detection enabled",
                 len(sites), len(detectable))

    triggered = 0
    unchanged = 0
    skipped = 0
    errors = 0
    summary_lines = []

    for site in detectable:
        try:
            r = probe_one_site(site, args.tenant_id, args.kb_id)
        except Exception as e:
            errors += 1
            logging.error("detector: unexpected error for %s: %s",
                          site.site_id, e, exc_info=True)
            summary_lines.append(f"  {site.site_id}: FATAL — {e}")
            continue

        status = r.get("status", "")
        if status == "ok":
            if r.get("has_new"):
                triggered += 1
                summary_lines.append(
                    f"  {site.site_id}: +{r.get('new_count', 0)} new → enqueued "
                    f"(next in {r.get('next_interval', 0)}s)"
                )
            else:
                unchanged += 1
                summary_lines.append(
                    f"  {site.site_id}: unchanged (next in {r.get('next_interval', 0)}s)"
                )
        elif status == "not_due":
            skipped += 1
        elif status == "auto_disabled":
            skipped += 1
            summary_lines.append(f"  {site.site_id}: AUTO-DISABLED (5+ errors)")
        elif status == "quiet_hours":
            skipped += 1
        elif status == "already_probing":
            skipped += 1
        elif status == "error":
            errors += 1
            summary_lines.append(
                f"  {site.site_id}: ERROR ({r.get('consecutive_errors', 0)}x) — {r.get('error', '')[:60]}"
            )

    _safe_print(f"\n[DETECTOR] Summary: {triggered} triggered, "
                f"{unchanged} unchanged, {skipped} skipped, {errors} errors")
    for line in summary_lines:
        _safe_print(line)
    _safe_print("")

    logging.info(
        "=== Detector finished: triggered=%d unchanged=%d skipped=%d errors=%d ===",
        triggered, unchanged, skipped, errors,
    )


if __name__ == "__main__":
    CONSUMER_NAME = "crawler_detector"
    init_root_logger(CONSUMER_NAME)
    main()
