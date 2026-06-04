#!/usr/bin/env python
"""
WeChat MP Article Crawler — invoked by ragflow2 task_executor for scheduled collection.

Usage:
    python rag/svr/wechat_mp_crawler.py \\
        --tenant-id <tenant_id> \\
        --task-name <task_name> \\
        --kb-id <kb_id> \\
        --mp-ids <comma_separated_faker_ids> \\
        --gather-content true|false \\
        --max-page 1 \\
        --interval 10

The script:
1. Reads WeChat auth from the DB for the given tenant
2. Collects latest articles from each MP via the WeChat appmsg API
3. Deduplicates against a per-task state file
4. Uploads new articles as Markdown documents to the target RAGFlow KB
5. Outputs a JSON summary to stdout (captured by task_executor)
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wechat_mp_crawler")


# ── Project path setup ──────────────────────────────────────

def _setup_path():
    # __file__ = /ragflow/rag/svr/wechat_mp_crawler.py
    # Need /ragflow on sys.path so "from rag.svr.xxx" resolves correctly.
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _get_state_dir():
    return str(Path(__file__).resolve().parent.parent / "data" / "wechat_mp_states")


# ── State file management ───────────────────────────────────

def load_state(task_name: str) -> dict:
    state_dir = _get_state_dir()
    state_file = os.path.join(state_dir, f"{task_name}_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load state file %s: %s", state_file, e)
    return {"processed_aids": [], "mp_articles": {}}


def save_state(task_name: str, state: dict):
    state_dir = _get_state_dir()
    os.makedirs(state_dir, exist_ok=True)
    state_file = os.path.join(state_dir, f"{task_name}_state.json")
    # Limit stored aids to the most recent 5000 to prevent unbounded growth
    if len(state.get("processed_aids", [])) > 5000:
        state["processed_aids"] = state["processed_aids"][-5000:]
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── KB document upload ──────────────────────────────────────

def upload_articles_to_kb(articles: list, kb_id: str, tenant_id: str) -> list:
    """Upload collected articles as Markdown documents to a RAGFlow knowledge base.

    Uses ragflow2 internal services (DocumentService, FileService, STORAGE_IMPL)
    to create documents directly — no REST API call needed.
    """
    _setup_path()

    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.document_service import DocumentService
    from api.db.services.file_service import FileService
    from api.db import FileType, KNOWLEDGEBASE_FOLDER_NAME
    from common.constants import FileSource, ParserType
    from common.misc_utils import get_uuid
    from api import settings

    # Validate KB exists
    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        logger.error("Knowledge base %s not found", kb_id)
        return []

    # Get or create KB folder in file tree
    root_folder = FileService.get_root_folder(tenant_id)
    kb_root_folder = FileService.new_a_file_from_kb(
        tenant_id, KNOWLEDGEBASE_FOLDER_NAME, root_folder["id"]
    )
    kb_folder = FileService.new_a_file_from_kb(
        tenant_id, kb.name, kb_root_folder["id"]
    )

    uploaded = []
    for art in articles:
        try:
            title = art.get("title", "Untitled")
            # Sanitize filename: remove path separators and limit length
            safe_name = "".join(c for c in title if c not in r'\/:*?"<>|')[:120]
            if not safe_name.strip():
                safe_name = f"wechat_article_{art.get('id', get_uuid())}"
            filename = f"{safe_name}.md"

            # Build Markdown content
            content_lines = [
                f"# {title}",
                "",
                f"**来源**: {art.get('mp_title', art.get('ext', {}).get('mp_title', '未知公众号'))}",
                f"**发布时间**: {_format_time(art.get('publish_time', ''))}",
                f"**原文链接**: {art.get('url', '')}",
                "",
            ]
            if art.get("description"):
                content_lines.append(f"> {art['description']}")
                content_lines.append("")
            if art.get("content"):
                content_lines.append(art["content"])
            else:
                content_lines.append(f"[查看原文]({art.get('url', '')})")

            markdown_body = "\n".join(content_lines)
            blob = markdown_body.encode("utf-8")

            # Upload blob to storage
            location = get_uuid()
            while settings.STORAGE_IMPL.obj_exist(kb_id, location):
                location = get_uuid()
            settings.STORAGE_IMPL.put(kb_id, location, blob)

            # Build and insert Document record
            doc = {
                "id": get_uuid(),
                "kb_id": kb.id,
                "parser_id": kb.parser_id,
                "pipeline_id": kb.pipeline_id,
                "parser_config": kb.parser_config,
                "created_by": tenant_id,
                "type": FileType.OTHER.value,
                "name": filename,
                "location": location,
                "size": len(blob),
                "suffix": "md",
                "source_type": FileSource.KNOWLEDGEBASE,
            }
            DocumentService.insert(doc)
            FileService.add_file_from_kb(doc, kb_folder["id"], tenant_id)

            logger.info("Uploaded: %s -> %s/%s", title, kb.name, filename)
            uploaded.append({"title": title, "doc_id": doc["id"], "kb_name": kb.name})

        except Exception as e:
            logger.error("Failed to upload article '%s': %s", title, e)

    return uploaded


def _format_time(ts) -> str:
    """Convert a Unix timestamp (int or str) to ISO datetime string."""
    if not ts:
        return ""
    try:
        t = int(ts)
        return datetime.fromtimestamp(t, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(ts)


# ── MP info lookup ──────────────────────────────────────────

def lookup_mp_info(tenant_id: str, faker_ids: list) -> list:
    """Look up MP details (name, faker_id) from wechat_mp_account table.

    Returns list of dicts with keys: mp_id, faker_id, mp_title.
    Falls back to using faker_id as both mp_id and title if DB unavailable.
    """
    if not faker_ids:
        return []

    _setup_path()
    try:
        from api.db.services.wechat_mp_service import WechatMpAccountService
        accounts = WechatMpAccountService.list_by_faker_ids(tenant_id, faker_ids)
        if accounts:
            return [
                {
                    "mp_id": a.get("id", a.get("faker_id", "")),
                    "faker_id": a.get("faker_id", ""),
                    "mp_title": a.get("mp_name", a.get("faker_id", "")),
                }
                for a in accounts
            ]
    except Exception as e:
        logger.warning("Could not look up MP info from DB: %s", e)

    # Fallback: use faker_id as everything
    return [{"mp_id": fid, "faker_id": fid, "mp_title": fid} for fid in faker_ids]


# ── Main collection logic ───────────────────────────────────

def collect_articles(tenant_id: str, mp_info_list: list, gather_content: bool,
                     max_page: int, interval: int) -> list:
    """Collect articles from a list of MPs.

    Args:
        tenant_id: RAGFlow tenant ID (for auth lookup).
        mp_info_list: List of dicts with mp_id, faker_id, mp_title.
        gather_content: Whether to fetch full article body.
        max_page: Max pages per MP (1 page = 5 articles).
        interval: Max random delay between pages.

    Returns:
        List of article dicts.
    """
    from rag.svr.wechat_mp.mps_api import MpsApi

    all_articles = []

    for mp_info in mp_info_list:
        mp_id = mp_info["mp_id"]
        faker_id = mp_info["faker_id"]
        mp_title = mp_info["mp_title"]

        logger.info("Starting collection for [%s] (faker_id=%s)", mp_title, faker_id)

        articles_buffer = []

        def article_callback(art: dict) -> bool:
            articles_buffer.append(art)
            return True

        try:
            gather = MpsApi(
                tenant_id=tenant_id,
                gather_content=gather_content,
            )
            gather.get_articles(
                faker_id=faker_id,
                mp_id=mp_id,
                mp_title=mp_title,
                callback=article_callback,
                max_page=max_page,
                interval=interval,
                gather_content=gather_content,
            )
            all_articles.extend(articles_buffer)
            logger.info("Collected %d articles from [%s]", len(articles_buffer), mp_title)
        except Exception as e:
            logger.error("Collection failed for [%s]: %s", mp_title, e)

    return all_articles


# ── CLI entry point ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WeChat MP Article Crawler")
    parser.add_argument("--tenant-id", required=True, help="RAGFlow tenant ID")
    parser.add_argument("--task-name", required=True, help="Scheduled task name (for state tracking)")
    parser.add_argument("--kb-id", default="", help="Target knowledge base ID")
    parser.add_argument("--mp-ids", default="", help="Comma-separated faker_ids")
    parser.add_argument("--gather-content", default="true", choices=["true", "false"],
                        help="Whether to fetch full article body")
    parser.add_argument("--max-page", type=int, default=1, help="Max pages per MP for incremental runs")
    parser.add_argument("--first-max-page", type=int, default=0,
                        help="Max pages per MP for first-time full crawl (0 = use --max-page)")
    parser.add_argument("--interval", type=int, default=10, help="Max delay between pages (seconds)")
    parser.add_argument("--script-args", default="", help="JSON string with mp_ids, gather_content, first_max_page, etc.")
    args = parser.parse_args()

    # Parse --script-args JSON (used by task_executor for wechat_mp tasks)
    _script_args = {}
    if args.script_args:
        try:
            _script_args = json.loads(args.script_args)
        except json.JSONDecodeError:
            logger.warning("Failed to parse --script-args JSON: %s", args.script_args)

    # --script-args values override individual CLI args
    if _script_args.get("mp_ids"):
        args.mp_ids = ",".join(_script_args["mp_ids"])
    if "gather_content" in _script_args:
        args.gather_content = "true" if _script_args["gather_content"] else "false"
    if "first_max_page" in _script_args:
        args.first_max_page = int(_script_args["first_max_page"])

    _setup_path()

    gather_content = args.gather_content.lower() == "true"
    mp_ids_raw = [x.strip() for x in args.mp_ids.split(",") if x.strip()]

    summary = {
        "task_name": args.task_name,
        "tenant_id": args.tenant_id,
        "started_at": datetime.now().isoformat(),
        "mp_count": len(mp_ids_raw),
        "articles_collected": 0,
        "articles_new": 0,
        "articles_uploaded": 0,
        "error": None,
    }

    try:
        # Load state for dedup
        state = load_state(args.task_name)
        processed_aids = set(state.get("processed_aids", []))

        # Determine effective max_page: first run → full crawl, subsequent → incremental
        is_first_run = len(processed_aids) == 0
        first_max_page = args.first_max_page if args.first_max_page > 0 else args.max_page
        effective_max_page = first_max_page if is_first_run else args.max_page
        if is_first_run and first_max_page > args.max_page:
            logger.info("First run detected — using full crawl with max_page=%d", effective_max_page)
        else:
            logger.info("Incremental run — using max_page=%d", effective_max_page)

        # Look up MP info
        mp_info_list = lookup_mp_info(args.tenant_id, mp_ids_raw)

        # Collect articles
        all_articles = collect_articles(
            tenant_id=args.tenant_id,
            mp_info_list=mp_info_list,
            gather_content=gather_content,
            max_page=effective_max_page,
            interval=args.interval,
        )
        summary["articles_collected"] = len(all_articles)

        # Dedup: filter out already-processed articles
        new_articles = [a for a in all_articles if a.get("id") not in processed_aids]
        summary["articles_new"] = len(new_articles)

        if new_articles:
            logger.info("%d new articles found", len(new_articles))

            # Upload to KB if configured
            if args.kb_id:
                logger.info("Uploading to KB %s...", args.kb_id)
                uploaded = upload_articles_to_kb(
                    new_articles, args.kb_id, args.tenant_id
                )
                summary["articles_uploaded"] = len(uploaded)

            # Update state
            for a in new_articles:
                processed_aids.add(a.get("id", ""))
            state["processed_aids"] = list(processed_aids)
            state["last_run"] = datetime.now().isoformat()
            if new_articles:
                mp_id = new_articles[0].get("mp_id", "")
                art_id = new_articles[0].get("id", "")
                state["mp_articles"][mp_id] = {
                    "last_aid": art_id,
                    "count": len([a for a in new_articles if a.get("mp_id") == mp_id]),
                }
            save_state(args.task_name, state)
        else:
            logger.info("No new articles found")

    except Exception as e:
        logger.exception("Crawler failed")
        summary["error"] = str(e)

    summary["finished_at"] = datetime.now().isoformat()

    # Write JSON summary to stdout — captured by task_executor
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
