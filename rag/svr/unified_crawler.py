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


def _resolve_kb_id(site_id: str, tenant_id: str, explicit_kb_id: str) -> str:
    """如果调用方未传 kb_id, 则按 site_id 从 crawler_task 表查询.

    设计原因: 探测器 (crawler_detector.py) 触发爬虫时不带 kb_id,
    爬虫脚本自己按 site_id 查 crawler_task 表获取, 实现"触发源/业务参数"解耦.

    解析顺序:
      1. explicit_kb_id 非空 → 直接返回 (CLI 显式指定优先)
      2. crawler_task 表中该 tenant 下 site_id 对应的 enabled=1 任务
      3. 仍找不到 → 返回空字符串 (crawler 会跳过 KB 上传, 仅写 crawler_result)
    """
    if explicit_kb_id:
        return explicit_kb_id
    try:
        from api.db.db_models import DB
        from api.db.services.crawler_service import CrawlerTaskService
    except ImportError as e:
        logging.warning("unified_crawler: cannot import CrawlerTaskService for kb_id lookup: %s", e)
        return ""

    try:
        @DB.connection_context()
        def _q() -> str:
            row = (
                CrawlerTaskService.model
                .select()
                .where(
                    (CrawlerTaskService.model.site_id == site_id)
                    & (CrawlerTaskService.model.tenant_id == tenant_id)
                    & (CrawlerTaskService.model.enabled == True)  # noqa: E712
                )
                .order_by(CrawlerTaskService.model.create_time.desc())
                .first()
            )
            if not row:
                return ""
            return row.kb_id or ""

        kb_id = _q()
        if kb_id:
            logging.info("unified_crawler: resolved kb_id=%s from crawler_task (site_id=%s)",
                         kb_id, site_id)
        else:
            logging.warning("unified_crawler: no enabled crawler_task for site_id=%s — "
                            "KB upload will be skipped", site_id)
        return kb_id
    except Exception as e:
        logging.error("unified_crawler: kb_id lookup failed for site_id=%s: %s", site_id, e)
        return ""


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

    # kb_id 解析: 未显式传入时, 按 site_id 从 crawler_task 表自动获取
    # 这样探测器可以不带 kb_id 触发爬虫, 实现触发源/业务参数解耦
    kb_id = _resolve_kb_id(site_id, args.tenant_id, args.kb_id)

    # Writer mode + category override (script_args takes precedence over CLI flags)
    writer_mode = script_args.get("writer", args.writer) or "bid"
    category = script_args.get("category", args.category) or ""
    task_id = script_args.get("task_id", "") or ""
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
        if status == "error":
            logging.error("=== Unified crawler failed: site=%s ===", site_id)
            sys.exit(1)
        logging.info("=== Unified crawler finished: site=%s, status=%s, writer=%s ===",
                     site_id, status, writer_mode)
    except Exception as e:
        _safe_print(f"[UNIFIED] ERROR: Crawler crashed: {e}")
        traceback.print_exc()
        logging.error("=== Unified crawler crashed: site=%s, error=%s ===", site_id, e)
        sys.exit(1)


if __name__ == "__main__":
    CONSUMER_NAME = "unified_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
