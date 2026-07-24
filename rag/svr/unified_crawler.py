#!/usr/bin/env python3
"""
Unified crawler entry point — compatible with task_executor subprocess model.

Replaces individual crawler scripts (80+ files) with a single config-driven
entry point.  Invoked identically by task_executor.py:

    python rag/svr/unified_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url <URL> \
        --kb-id <KB_ID> \
        --task-name <NAME> \
        --script-args '{"site_id":"fgw_zwgk"}'

The ``--script-args`` JSON string specifies which site to crawl and can
override any SiteConfig field at runtime.
"""

import argparse
import json
import logging
import os
import sys
import traceback
from typing import Any, Dict

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.engine import CrawlerEngine
from rag.svr.crawler_engine.progress_reporter import ProgressReporter


# Default config path
DEFAULT_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "crawler_sites.yaml")


def _safe_print(msg):
    """Print safely, handling Unicode errors on Windows consoles."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def parse_args():
    p = argparse.ArgumentParser(
        description="Unified Crawler — config-driven web crawler for RAGFlow"
    )
    p.add_argument("--tenant-id", required=True, help="Tenant ID")
    p.add_argument("--target-url", default="", help="Target URL (for compatibility)")
    p.add_argument("--kb-id", default="",
                   help="Knowledge base ID (optional; auto-resolved from crawler_task by site_id when empty)")
    p.add_argument("--task-name", required=True, help="Task name (used for output dir)")
    p.add_argument("--script-args", default="{}",
                   help="JSON string: {site_id, section, full, ...}")
    p.add_argument("--output-dir", default=None, help="Override output directory")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                   help="Path to crawler_sites.yaml")
    p.add_argument("--force", action="store_true",
                   help="Force run: bypass Redis lock and clear stale locks")
    p.add_argument("--writer", default="bid", choices=["bid", "collection"],
                   help="storage writer mode: bid (legacy bid_*) | collection (new crawler_result)")
    p.add_argument("--category", default="",
                   help="override site category: bid|policy|personnel|news|other (default: use YAML)")
    p.add_argument("--date-filter", default="",
                   help="only store items whose date matches: today | YYYY-MM-DD (collection writer only)")
    # Compatibility arguments (ignored but accepted)
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token", "--full"):
        p.add_argument(opt, default=None)
    return p.parse_args()


def _resolve_crawler_task(site_id: str, explicit_kb_id: str) -> tuple:
    """按 site_id 从 crawler_task 表解析 (task_id, kb_id).

    设计原因: 探测器 (crawler_detector.py) 触发爬虫时不带 task_id/kb_id,
    爬虫脚本自己按 site_id 查 crawler_task 表获取, 实现"触发源/业务参数"解耦.

    与 §15.6 通用规约一致 —— crawler_task 表是全局共享的, 不按 tenant 过滤.
    同一 site_id 可能有多条 enabled=1 的任务, 取 create_time 最新的一条
    (kb_id 解析 + last_run_* 回写需要确定唯一 task_id).

    解析顺序:
      1. explicit_kb_id 非空 → 仍按 site_id 查 task_id (用于 last_run_* 回写),
         kb_id 用显式传入的值
      2. crawler_task 表中 site_id 对应的 enabled=1 任务 (全局, 不过滤 tenant)
      3. 仍找不到 → 返回 ("", explicit_kb_id), crawler 会跳过 KB 上传和 last_run_* 回写
    """
    try:
        from api.db.db_models import DB
        from api.db.services.crawler_service import CrawlerTaskService
    except ImportError as e:
        logging.warning("unified_crawler: cannot import CrawlerTaskService: %s", e)
        return ("", explicit_kb_id)

    try:
        @DB.connection_context()
        def _q() -> tuple:
            row = (
                CrawlerTaskService.model
                .select()
                .where(
                    (CrawlerTaskService.model.site_id == site_id)
                    & (CrawlerTaskService.model.enabled == True)  # noqa: E712
                )
                .order_by(CrawlerTaskService.model.create_time.desc())
                .first()
            )
            if not row:
                return ("", explicit_kb_id)
            # 显式 kb_id 优先 (CLI/API 直接指定), 否则用 DB 行的 kb_id
            kb_id = explicit_kb_id or row.kb_id or ""
            return (row.id, kb_id)

        task_id, kb_id = _q()
        if task_id:
            logging.info(
                "unified_crawler: resolved task_id=%s kb_id=%s from crawler_task (site_id=%s)",
                task_id, kb_id or "(none)", site_id,
            )
        else:
            logging.warning(
                "unified_crawler: no enabled crawler_task for site_id=%s — "
                "KB upload and last_run_* writeback will be skipped", site_id,
            )
        return (task_id, kb_id)
    except Exception as e:
        logging.error("unified_crawler: crawler_task lookup failed for site_id=%s: %s",
                      site_id, e)
        return ("", explicit_kb_id)


def _build_frontend_summary(summary: Dict[str, Any], status: str) -> Dict[str, Any]:
    """把 engine.run() 返回的内部 summary 翻译成前端 last_run_summary 期望的 shape.

    与 crawl4ai_app._pull_summary_from_redis 的输出结构保持一致, 这样
    手动触发和探测器触发产生的 last_run_summary 在前端 TasksTab 显示统一.

    engine summary 有两种形态:
      - 单 section:  {status, scanned_pages, scanned_items, new_items, bid_stats, ...}
      - 多 section:  {site_id, sections: {label: {...}, ...}, total_new_items, bid_stats, ...}
                     (顶层没有 status/scanned_pages/scanned_items, 需要从 sections 聚合)

    字段映射:
        scanned_pages (或 sum(sections[*].scanned_pages)) → pages
        scanned_items (或 sum(sections[*].scanned_items)) → items_found
        new_items / total_new_items → items_new
        bid_stats.kb_uploaded → kb_uploaded
        bid_stats.attachments_uploaded → attachments_uploaded
    """
    bid_stats = summary.get("bid_stats") or {}
    items_new = int(
        summary.get("total_new_items")
        or summary.get("new_items")
        or 0
    )
    # 多 section 站点的统计散在 sections 字典里, 顶层没有, 需聚合
    sections = summary.get("sections")
    if isinstance(sections, dict) and sections:
        pages = sum(int(s.get("scanned_pages", 0) or 0) for s in sections.values())
        items_found = sum(int(s.get("scanned_items", 0) or 0) for s in sections.values())
    else:
        pages = int(summary.get("scanned_pages", 0) or 0)
        items_found = int(summary.get("scanned_items", 0) or 0)
    return {
        "status": status,
        "pages": pages,
        "items_found": items_found,
        "items_new": items_new,
        "kb_uploaded": int(bid_stats.get("kb_uploaded", 0) or 0),
        "attachments_uploaded": int(bid_stats.get("attachments_uploaded", 0) or 0),
        "errors": [] if status == "success" else [
            summary.get("error") or "crawler failed"
        ],
    }


def _writeback_task_run_result(task_id: str, status: str,
                                summary: Dict[str, Any]) -> None:
    """探测器/定时任务触发的运行结束后, 回写 crawler_task.last_run_* 字段.

    手动触发路径 (/crawl4ai/tasks/<id>/trigger) 由 crawl4ai_app.py 自己回写,
    本函数只负责 task_executor → unified_crawler 这条路径 (探测器触发 + CLI 直跑).

    失败不抛异常, 仅打 WARN —— 回写失败不影响采集结果本身.
    """
    if not task_id:
        return
    try:
        from api.db.db_models import DB
        from api.db.services.crawler_service import CrawlerTaskService
        from common.time_utils import current_timestamp
    except ImportError as e:
        logging.warning("unified_crawler: cannot import CrawlerTaskService for writeback: %s", e)
        return

    try:
        fe_summary = _build_frontend_summary(summary, status)
        @DB.connection_context()
        def _update():
            CrawlerTaskService.update_by_id(task_id, {
                "last_run_status": status,
                "last_run_time": current_timestamp(),
                "last_run_summary": fe_summary,
            })
        _update()
        logging.info(
            "unified_crawler: writeback crawler_task[%s] last_run_status=%s items_new=%d",
            task_id, status, fe_summary["items_new"],
        )
    except Exception as e:
        logging.warning(
            "unified_crawler: writeback crawler_task[%s] failed: %s", task_id, e,
        )


def main():
    args = parse_args()

    # Parse script_args
    try:
        script_args = json.loads(args.script_args) if args.script_args else {}
    except json.JSONDecodeError:
        _safe_print(f"[UNIFIED] Invalid script-args JSON: {args.script_args}")
        script_args = {}

    site_id = script_args.get("site_id", "")
    if not site_id:
        _safe_print("[UNIFIED] ERROR: site_id is required in --script-args JSON")
        sys.exit(1)

    section = script_args.get("section")
    full_crawl = script_args.get("full", False) or bool(args.full)
    force_run = script_args.get("force", False) or bool(args.force)

    # crawler_task 解析: 按 site_id 同时解析 task_id + kb_id (探测器触发时不带这俩).
    # task_id 用于 crawler_result.task_id 关联 + 结束后回写 crawler_task.last_run_*.
    resolved_task_id, resolved_kb_id = _resolve_crawler_task(site_id, args.kb_id)
    kb_id = resolved_kb_id
    # script_args 显式传入的 task_id 优先 (手动触发 /crawl4ai/tasks/<id>/trigger 走这条路),
    # 否则用从 crawler_task 表解析出来的 (探测器触发走这条路).
    task_id = script_args.get("task_id", "") or resolved_task_id

    # Writer mode + category override (script_args takes precedence over CLI flags)
    writer_mode = script_args.get("writer", args.writer) or "bid"
    category = script_args.get("category", args.category) or ""
    date_filter = script_args.get("date_filter", args.date_filter) or ""

    _safe_print("\n" + "=" * 60)
    _safe_print(f"[UNIFIED] Unified Crawler v1.0")
    _safe_print(f"[UNIFIED] Site: {site_id}")
    _safe_print(f"[UNIFIED] KB: {kb_id or '(resolved from crawler_task)'}")
    _safe_print(f"[UNIFIED] Writer: {writer_mode}")
    if category or writer_mode == "collection":
        _safe_print(f"[UNIFIED] Category: {category or '(from YAML)'}")
    if date_filter:
        _safe_print(f"[UNIFIED] Date filter: {date_filter}")
    if section:
        _safe_print(f"[UNIFIED] Section: {section}")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    # Initialize RAGFlow settings
    settings.init_settings()
    logging.info("=== Unified crawler started: site=%s ===", site_id)

    # Load site config
    config_path = args.config
    if not os.path.exists(config_path):
        _safe_print(f"[UNIFIED] ERROR: Config file not found: {config_path}")
        sys.exit(1)

    try:
        loader = ConfigLoader(config_path)
        site_config = loader.get(site_id)
    except KeyError:
        _safe_print(f"[UNIFIED] ERROR: Site '{site_id}' not found in {config_path}")
        _safe_print(f"[UNIFIED] Available sites: {loader.list_site_ids()}")
        sys.exit(1)
    except Exception as e:
        _safe_print(f"[UNIFIED] ERROR: Failed to load config: {e}")
        sys.exit(1)

    # Override output dir if specified
    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )

    # Run the engine
    try:
        reporter = ProgressReporter(task_id) if task_id else None
        engine = CrawlerEngine(site_config, output_dir=output_dir,
                               progress_reporter=reporter)
        summary = engine.run(
            tenant_id=args.tenant_id,
            kb_id=kb_id,
            task_name=args.task_name,
            full=full_crawl,
            force=force_run,
            writer_mode=writer_mode,
            category=category,
            task_id=task_id,
            date_filter=date_filter,
        )
        status = summary.get("status", "unknown")
        # 映射 engine status → crawler_task.last_run_status (success/fail/skipped)
        if status == "error":
            fe_status = "fail"
            logging.error("=== Unified crawler failed: site=%s ===", site_id)
        elif status == "skipped":
            fe_status = "skipped"  # before_8am / already_running 等
            logging.info("=== Unified crawler skipped: site=%s, reason=%s ===",
                         site_id, summary.get("reason", ""))
        else:
            fe_status = "success"
            logging.info("=== Unified crawler finished: site=%s, status=%s, writer=%s ===",
                         site_id, status, writer_mode)
        # 回写 crawler_task.last_run_* (探测器/CLI 触发路径)
        _writeback_task_run_result(task_id, fe_status, summary)
        if fe_status == "fail":
            sys.exit(1)
    except Exception as e:
        _safe_print(f"[UNIFIED] ERROR: Crawler crashed: {e}")
        traceback.print_exc()
        logging.error("=== Unified crawler crashed: site=%s, error=%s ===", site_id, e)
        # 异常路径也回写一次 fail 状态, 避免任务列表一直显示旧的成功状态
        _writeback_task_run_result(task_id, "fail", {"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    CONSUMER_NAME = "unified_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
