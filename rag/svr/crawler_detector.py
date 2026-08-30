#!/usr/bin/env python3
"""
Crawler Detector — lightweight probe that checks all enabled collection sites
for new content.

Designed to run as a meta scheduled task (interval=60s) that is picked up by
``scheduled_task_executor``.  Each invocation:

  1. Loads YAML site configs + filters to sites that are enabled in the
     ``crawler_task`` table (same source of truth as the 采集任务列表 UI)
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
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

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

# Maximum probe duration before the per-site lock auto-expires.  Must outlive the
# subprocess timeout (PROBE_SUBPROCESS_TIMEOUT) so a slow-but-healthy probe holds
# its lock for the whole run and a concurrent round can't slip a duplicate probe.
PROBE_LOCK_TIMEOUT = 180

# Wall-clock timeout for a single-site probe subprocess.  A wedged Playwright
# greenlet can NOT be broken by asyncio.wait_for on a thread (see
# scheduled_task_executor.py:335); only SIGKILL of the child works.  This bound
# must sit ABOVE a slow-but-healthy SPA probe: multi-cycle js_extract reloads
# were observed at ~70s for ggzyjd_dissent (two reload cycles, verified
# 2026-08-25).  A bound lower than a healthy probe would false-timeout and
# auto-disable every browser site.  Trade-off: a genuinely stuck site blocks the
# detector round at most this long, then the child is SIGKILLed (whole process
# group) and we move on — bounded, not permanent like the pre-fix greenlet wedge.
PROBE_SUBPROCESS_TIMEOUT = 120

# Transport backends that drive a browser (Playwright/Scrapling).  These are the
# greenlet-deadlock-prone paths, so their probe gets isolated into a SIGKILL-able
# subprocess.  Non-browser sites (urllib3/requests with socket timeouts) stay
# in-process — cheap and cannot greenlet-deadlock.
_BROWSER_ENGINES = {
    "playwright_spa", "playwright_http", "playwright",
    "scrapling", "scrapling_stealth",
}
_BROWSER_TYPES = {"spa_render", "playwright_http", "scrapling_stealth"}


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
    p.add_argument("--kb-id", default="",
                   help="(deprecated, kept for backward compat) 探测器不再消费 kb_id, "
                        "爬虫脚本 (unified_crawler.py) 按 site_id 查 crawler_task 自动解析")
    p.add_argument("--task-name", default="", help="Task name (unused; kept for compat)")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    # Compatibility: task_executor always passes these; detector ignores them
    p.add_argument("--script-args", default="{}",
                   help="JSON args (parsed for force options, rest ignored)")
    # Single-site probe subprocess mode (used by _detect_via_subprocess)
    p.add_argument("--probe-one", action="store_true",
                   help="Probe a single site and print one JSON line to stdout")
    p.add_argument("--site-id", default="", help="Site id to probe when --probe-one")
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

# "该站点已有一条爬虫任务在排队/运行中" 标记。防止 detector 对同一站点重复入队
# 全量爬虫 (高频更新站点 + 队列积压时会堆出几十条重复, 每条浪费 ~8min worker)。
# 生命周期 = 任务"排队中 + 运行中"全过程:
#   - 入队前 SET NX (本文件 _enqueue_full_crawl), 已存在则跳过入队
#   - task_executor.handle_scheduled_script_task 把爬虫跑完后 DEL (成功/失败/超时)
# 因此它能扛住任意时长的队列积压 —— key 不靠短定时器清, 而是任务真跑完才清。
# SAFETY_TTL 只是"孤儿 key"的最后保险 (如消息被手动 XTRIM 丢失), 正常不会触发。
# ★ task_executor.py 里的 _CRAWL_QUEUED_KEY 必须与此格式完全一致。
CRAWL_QUEUED_KEY = "crawl:queued:{site}"
CRAWL_QUEUED_SAFETY_TTL = 6 * 3600  # 6h, >> 单条爬虫 timeout(3600s) + 合理排队时长


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


def _get_crawler_task_site_ids() -> Optional[set]:
    """读取 ``crawler_task`` 表 enabled=1 的 site_id 集合（全局，不过滤 tenant）.

    与采集任务列表 (``/crawl4ai/tasks`` → ``CrawlerTaskService.get_list``) 及
    前端监控面板 (``collection_app._active_site_ids``) 完全对齐 ——
    探测器只探用户真正配置过采集任务的站点，避免对 YAML 里 84 个站点
    无差别探测后写回 Redis state, 造成监控面板数据漂移.

    Returns:
        set[str]: enabled=1 的 site_id 集合；DB 异常时返回 None (调用方
        应理解为"查询失败", 通常跳过本轮探测).
    """
    try:
        from api.db.db_models import DB
        from api.db.services.crawler_service import CrawlerTaskService

        @DB.connection_context()
        def _q() -> set:
            q = (CrawlerTaskService.model
                 .select(CrawlerTaskService.model.site_id)
                 .where(CrawlerTaskService.model.enabled == True))  # noqa: E712
            return {row.site_id for row in q}
        return _q()
    except Exception as e:
        logging.error("crawler_detector: query crawler_task failed: %s", e)
        return None


def _site_bypass_date_filter(site_id: str) -> bool:
    """读取 YAML 中该站点的 ``bypass_date_filter`` 字段.

    Returns:
        True 表示该站点 API 自带相对日期过滤(如 inRecentDays=N),
        detector 入队时应跳过 ``date_filter=today`` 注入;
        YAML 缺失/读失败时返回 False (沿用默认行为).
    """
    try:
        from rag.svr.crawler_engine.config import ConfigLoader
        loader = ConfigLoader(DEFAULT_CONFIG_PATH)
        loader.load()
        site = loader.get(site_id)
        return bool(getattr(site, "bypass_date_filter", False))
    except KeyError:
        return False
    except Exception as e:
        logging.warning("crawler_detector: read bypass_date_filter for %s failed: %s",
                        site_id, e)
        return False


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

def _release_crawl_queued(site_id: str) -> None:
    """入队失败时回收 crawl:queued 标记, 避免把该站锁到 TTL。

    正常路径下标记由 task_executor 在爬虫跑完后 DEL; 此函数仅用于"抢到标记但
    入队没成功"的异常兜底。
    """
    if REDIS_CONN is None:
        return
    try:
        REDIS_CONN.delete(CRAWL_QUEUED_KEY.format(site=site_id))
    except Exception as e:
        logging.warning("detector: failed to release crawl-queued for %s: %s", site_id, e)


def _enqueue_full_crawl(site_id: str, category: str,
                        tenant_id: str) -> bool:
    """探测器发现新内容后,投递一条全量采集任务到 Redis 队列.

    设计原则(2026-07-24 重构):
      探测器只负责"判断 + 触发",不碰 kb_id / 业务参数.
      kb_id 由 unified_crawler.py 按 site_id 查 crawler_task 表自动获取.
      入队消息里不带 kb_id 字段,task_executor 相应不会传 --kb-id 给子进程.

    YAML bypass_date_filter=true 的站点(如 easy_prt_bidprice, API 自带
    inRecentDays=N 相对窗口),不注入 date_filter=today, 让 API 自己负责日期过滤.
    """
    if REDIS_CONN is None:
        logging.error("detector: cannot enqueue — Redis not available")
        return False

    # 去重: 每站最多一条"排队中/运行中"的爬虫任务。高频更新站点(如招标平台全天发标)
    # 叠加队列积压时, 若无此判断会每 detect_interval 塞一条, 堆出几十条重复(每条 ~8min)。
    # SET NX 抢占标记; 标记由 task_executor 在爬虫跑完后 DEL, 故能扛住任意队列积压 ——
    # 不靠短 TTL 清(那会在任务仍排队时过期、放重复进来), 只在任务真正完成时清。
    queued_key = CRAWL_QUEUED_KEY.format(site=site_id)
    try:
        claimed = REDIS_CONN.REDIS.set(
            queued_key, "1", ex=CRAWL_QUEUED_SAFETY_TTL, nx=True
        )
    except Exception as e:
        # Redis 异常时放行入队: 宁可偶发重复(爬虫幂等去重), 不可把站点彻底漏采。
        logging.warning(
            "detector: crawl-queued NX check failed for %s (%s); enqueueing anyway",
            site_id, e,
        )
        claimed = True
    if not claimed:
        logging.info(
            "detector: crawl already queued/running for site=%s; skip duplicate enqueue",
            site_id,
        )
        return False

    script_args = {
        "site_id": site_id,
        "writer": "collection",     # ★ route to new collection pipeline
        "category": category,        # ★ from YAML, drives crawler_result.category
        "date_filter": "today",      # ★ only store today's items (dedup-friendly)
    }
    if _site_bypass_date_filter(site_id):
        # 滚动窗口站点（如 easy_prt_bidprice, inRecentDays=7）：
        # 不注入 date_filter=today；改为 full=true 让 engine 重置 last_page，
        # 否则第二次探测触发会从 state.last_page+1=page 2 开始 → 0 items。
        # 站点 API 自身的相对日期窗决定数据范围，DB upsert + processed_ids 去重依然生效。
        script_args.pop("date_filter", None)
        script_args["full"] = True
        logging.info("detector: site=%s bypass_date_filter=true → full crawl mode",
                     site_id)

    msg = {
        "id": get_uuid(),
        "task_type": "scheduled_script",
        "tenant_id": tenant_id,
        "name": f"detect:{site_id}",
        "script_path": "rag/svr/unified_crawler.py",
        "script_args": json.dumps(script_args, ensure_ascii=False),
        "timeout": 3600,
        "task_id_ref": "",
        "target_url": "",
        "llm_id": "",
        "llm_model_name": "",
        # 注意: 不带 kb_id. task_executor 在 kb_id 空时不会传 --kb-id 给子进程,
        # unified_crawler 会按 site_id 查 crawler_task 表自动补 kb_id.
        "access_token": "",
    }
    try:
        # prio 1: 爬虫优先于解析(task_executor.collect 先消费 prio 1 再 prio 0),
        # 防解析积压(~8000 文档)把爬虫饿死、失去实时性. 解析类仍走 prio 0.
        ok = REDIS_CONN.queue_product(settings.get_svr_queue_name(1), message=msg)
        if ok:
            logging.info("detector: enqueued collection crawl for site=%s (kb_id will be resolved by crawler)",
                         site_id)
        else:
            logging.error("detector: enqueue failed for site=%s", site_id)
            _release_crawl_queued(site_id)  # 入队失败别留孤儿标记, 否则该站被锁到 TTL
        return ok
    except Exception as e:
        logging.error("detector: enqueue error for site=%s: %s", site_id, e)
        _release_crawl_queued(site_id)
        return False


# ---------------------------------------------------------------------------
# Per-site probe isolation via subprocess
# ---------------------------------------------------------------------------

def _site_uses_browser(site) -> bool:
    """Whether the site's transport drives a browser (greenlet-deadlock prone)."""
    try:
        if getattr(site.transport, "type", "") in _BROWSER_TYPES:
            return True
        if getattr(site.transport, "engine", "") in _BROWSER_ENGINES:
            return True
        return False
    except Exception:
        # Fail safe: if we can't tell, isolate it.
        return True


def _detect_via_subprocess(site, tenant_id: str) -> Dict[str, Any]:
    """Run SiteDetector.detect() in a SIGKILL-able subprocess.

    A wedged Playwright greenlet can NOT be cancelled from the parent thread
    (asyncio.wait_for won't fire; see scheduled_task_executor.py:335).  Running
    the risky fetch in a child subprocess with a hard wall-clock timeout means a
    hung site only costs its own child, leaving the detector thread — and every
    other site — untouched.

    A plain ``subprocess.run(timeout=…)`` only SIGKILLs the child *python*
    process; Playwright spawns Chromium as a grandchild that would be orphaned.
    So we run the child in its own process group (``start_new_session=True``)
    and kill the whole group (``os.killpg``) so Chromium dies too.

    Returns:
        The detect() result dict (same keys the parent's success path reads:
        has_new_items / new_item_count / scanned_count / signature /
        last_signature / reason).  Raises RuntimeError on timeout or failure so
        probe_one_site's existing error path handles it.
    """
    cmd = [
        sys.executable, "-m", "rag.svr.crawler_detector",
        "--probe-one",
        "--tenant-id", tenant_id,
        "--site-id", site.site_id,
        "--config", DEFAULT_CONFIG_PATH,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # new process group -> we can killpg Chromium.
    )
    try:
        stdout, stderr = proc.communicate(timeout=PROBE_SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Kill the WHOLE process group (python + Playwright Chromium grandchild).
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        finally:
            proc.wait()
        raise RuntimeError(
            f"probe subprocess timeout after {PROBE_SUBPROCESS_TIMEOUT}s "
            f"(site={site.site_id}); killed process group"
        )

    payload = None
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            break

    if payload is None:
        raise RuntimeError(
            "probe subprocess produced no JSON for {0}; "
            "stdout={1!r} stderr={2!r}".format(
                site.site_id, (stdout or "")[-300:], (stderr or "")[-500:]
            )
        )
    if payload.get("error"):
        raise RuntimeError(f"site {site.site_id} probe error: {payload['error']}")
    return payload


def _probe_one(config_path: str, tenant_id: str, site_id: str) -> Dict[str, Any]:
    """Probe a single site (worker for ``--probe-one``). Returns a JSON-able dict."""
    # The subprocess is fresh: ensure Redis/settings are initialised so
    # SiteDetector.detect() can read its lock/signature state.
    try:
        settings.init_settings()
    except Exception as e:
        # already initialised / settings unavailable — tolerate
        logging.debug("probe-one: init_settings skipped or ignored: %s", e)

    loader = ConfigLoader(config_path)
    site = loader.get(site_id)  # raises KeyError if unknown
    # Tighten probe speed, same as probe_one_site.
    site.anti_crawler.max_retries = 1
    site.transport.timeout = 10

    detector = SiteDetector(site, tenant_id, collection_mode=True)
    result = detector.detect()
    try:
        from rag.svr.crawler_engine.browser_pool import cleanup_browser_pool
        cleanup_browser_pool()
    except Exception:
        pass

    return {
        "site_id": site_id,
        "has_new_items": bool(result.get("has_new_items")),
        "new_item_count": int(result.get("new_item_count", 0)),
        "scanned_count": int(result.get("scanned_count", 0)),
        "signature": result.get("signature", "") or "",
        "last_signature": result.get("last_signature", "") or "",
        "reason": result.get("reason", "") or "",
    }


def _probe_one_cli(args) -> None:
    """CLI entry for ``--probe-one``; prints one JSON line and exits 0/1."""
    payload = {"site_id": args.site_id, "error": ""}
    try:
        payload = _probe_one(args.config, args.tenant_id, args.site_id)
    except FileNotFoundError as e:
        payload = {"site_id": args.site_id, "error": f"config missing: {e}"}
    except KeyError as e:
        payload = {"site_id": args.site_id, "error": f"unknown site: {e}"}
    except Exception as e:
        logging.error("probe-one: site=%s failed: %s", args.site_id, e, exc_info=True)
        payload = {"site_id": args.site_id, "error": str(e)[:500]}
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0 if not payload.get("error") else 1)


# ---------------------------------------------------------------------------
# Per-site probe
# ---------------------------------------------------------------------------

def probe_one_site(site, tenant_id: str) -> Dict[str, Any]:
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

        if _site_uses_browser(site):
            # Isolate the Playwright fetch into a SIGKILL-able subprocess so a
            # greenlet deadlock can't wedge the whole detector thread.
            result = _detect_via_subprocess(site, tenant_id)
        else:
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
        enq_ok = _enqueue_full_crawl(site_id, category, tenant_id)
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
# Reusable entry: run_detection(tenant_id, config_path)
# ---------------------------------------------------------------------------

def run_detection(tenant_id: str,
                  config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """探测器主逻辑的可复用入口.

    供两种调用方使用:
      1. ``main()`` (CLI 入口) —— argparse 解析后转调本函数
      2. ``scheduled_task_executor._run_detector_inproc`` —— A2 旁路方案下,
         detector 在调度器进程内通过 ``run_in_executor`` 调用本函数.

    **不** 调用 ``settings.init_settings()`` / ``init_root_logger`` —— 调用方
    应已初始化. ``finally`` 中调用 ``cleanup_browser_pool()`` 释放常驻 Chromium,
    避免在 scheduled_task_executor 进程内泄漏.

    Args:
        tenant_id: 租户 ID (探测器使用的 Redis state key 命名空间).
        config_path: ``crawler_sites.yaml`` 路径.

    Returns:
        ``{"triggered": int, "unchanged": int, "skipped": int, "errors": int,
           "summary_lines": List[str]}``
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    loader = ConfigLoader(config_path)
    all_sites = loader.load()  # Dict[str, SiteConfig] — 全量 YAML, 不预过滤

    # 以 crawler_task 表为真相源: 只探用户真正配置过采集任务的站点.
    # 与采集任务列表 /crawl4ai/tasks 及前端监控面板 _active_site_ids 完全对齐.
    # YAML 只提供站点元数据 (URL/selector/interval), 不再单独决定是否探测.
    active_site_ids = _get_crawler_task_site_ids()
    if active_site_ids is None:
        logging.warning("detector: crawler_task query failed; skipping this run")
        detectable: List[Any] = []
    else:
        detectable = [all_sites[sid] for sid in active_site_ids if sid in all_sites]
        missing_in_yaml = sorted(active_site_ids - set(all_sites.keys()))
        if missing_in_yaml:
            logging.warning("detector: crawler_task site_ids not in YAML (skipped): %s",
                            missing_in_yaml)
    logging.info("detector: %d YAML sites loaded, %d active in crawler_task",
                 len(all_sites), len(detectable))

    triggered = 0
    unchanged = 0
    skipped = 0
    errors = 0
    summary_lines: List[str] = []

    try:
        for site in detectable:
            try:
                r = probe_one_site(site, tenant_id)
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
                    f"  {site.site_id}: ERROR ({r.get('consecutive_errors', 0)}x) "
                    f"— {r.get('error', '')[:60]}"
                )
    finally:
        # A2: detector 改为在 scheduled_task_executor 进程内常驻执行后,
        # Chromium 实例也会常驻. 每轮探测结束主动释放, 下轮按需重建,
        # 既避免长期持有导致内存膨胀, 又防止上一轮残留状态污染下一轮.
        try:
            from rag.svr.crawler_engine.browser_pool import cleanup_browser_pool
            cleanup_browser_pool()
        except Exception as e:
            logging.warning("detector: cleanup_browser_pool failed: %s", e)

    logging.info(
        "=== Detector finished: triggered=%d unchanged=%d skipped=%d errors=%d ===",
        triggered, unchanged, skipped, errors,
    )

    return {
        "triggered": triggered,
        "unchanged": unchanged,
        "skipped": skipped,
        "errors": errors,
        "summary_lines": summary_lines,
    }


# ---------------------------------------------------------------------------
# Main (CLI entry, kept for backward compat & debugging)
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if args.probe_one:
        _probe_one_cli(args)

    _safe_print("\n" + "=" * 60)
    _safe_print("[DETECTOR] Crawler Detector v2.0 (collection mode)")
    _safe_print("[DETECTOR] kb_id: <unset; resolved per-site from crawler_task>")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Crawler detector started (tenant=%s) ===", args.tenant_id)

    try:
        result = run_detection(args.tenant_id, args.config)
    except FileNotFoundError as e:
        _safe_print(f"[DETECTOR] ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        _safe_print(f"[DETECTOR] ERROR: {e}")
        sys.exit(1)

    _safe_print(f"\n[DETECTOR] Summary: {result['triggered']} triggered, "
                f"{result['unchanged']} unchanged, "
                f"{result['skipped']} skipped, {result['errors']} errors")
    for line in result["summary_lines"]:
        _safe_print(line)
    _safe_print("")


if __name__ == "__main__":
    CONSUMER_NAME = "crawler_detector"
    init_root_logger(CONSUMER_NAME)
    main()
