#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  written permission governing permissions and limitations under the License.
#
import json
import logging
import os
import threading
from abc import ABC

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.connection_utils import timeout

# Execution timeout for the crawl tool (seconds).
# Crawler operations are network-heavy; 120s covers most single-page crawls.
_EXEC_TIMEOUT = int(os.environ.get("CRAWL_FETCH_TIMEOUT", 120))

# Max items to return in tool output (content text).
_MAX_OUTPUT_ITEMS = 10

# Max content length per item (truncated for LLM context).
_MAX_CONTENT_CHARS = 2000

# Default path to crawler_sites.yaml.
# In container: /ragflow/rag/svr/crawler_sites.yaml
_YAML_PATH = os.environ.get(
    "CRAWL_SITES_YAML",
    "/ragflow/rag/svr/crawler_sites.yaml",
)


def _load_loader():
    """Create a ConfigLoader for the default YAML path."""
    from rag.svr.crawler_engine.config import ConfigLoader
    return ConfigLoader(_YAML_PATH)


def _build_site_catalog() -> str:
    """Build a catalog string from crawler_sites.yaml for the tool description."""
    try:
        loader = _load_loader()
        entries = []
        for sid in loader.list_site_ids():
            try:
                cfg = loader.get(sid)
                enabled = getattr(cfg, "enabled", True)
                if enabled:
                    entries.append(f"  {sid}: {cfg.name}")
            except Exception:
                continue
        if not entries:
            return "  (no sites configured)"
        return "\n".join(entries)
    except Exception:
        return "  zhangzhou: 漳州公共资源交易中心\n  (site list load failed)"


class CrawlFetchParam(ToolParamBase):
    """Parameters for the CrawlFetch tool."""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "crawl_fetch",
            "description": f"""
使用时机：仅在知识库检索、标讯搜索API、网络搜索（Tavily/DuckDuckGo）等所有工具都无法找到用户所需数据时使用。这是最后的兜底数据源。

功能说明：爬取已配置的政府/招投标网站，提取公告/通知/正文内容，存入数据库，并将内容返回给你。知识库导入和附件解析会在后台自动完成。

如何选择 site_id：根据用户提到的地区和主题，从下方站点列表中选择匹配的 site_id。
- 用户提到城市/省份（如"漳州"、"福建"）→ 选择包含该地区名称的 site_id
- 用户提到主题（如"住建部"、"交通厅"）→ 选择对应的政府部门站点
- 如果不确定，选择最具体的匹配。例如：用户说"福建交通厅招标公告" → 选 "jtyst_zwgk"

返回内容：JSON 格式，包含 "items" 数组。每个条目有：title（标题）、content（正文摘要，最多2000字）、publish_time（发布时间）、has_attachment（是否有附件，true/false）、money（金额）。直接使用这些内容回答用户问题。

注意事项：
- 必须先尝试已有工具（检索、标讯搜索、Tavily、DuckDuckGo等），确认都无法满足后再使用本工具。
- max_pages 默认设为 1（仅最新数据）。仅当用户明确要求查看更多历史数据时才增加到 2-5。
- max_pages 不要超过 5，否则会超时。
- 爬取的数据会永久存入数据库，重复爬取同一站点会自动跳过已存在的数据。
- 可以告知用户数据已保存，稍后即可在知识库中检索到。

可用站点（site_id: 描述）：
{_build_site_catalog()}
""",
            "parameters": {
                "site_id": {
                    "type": "string",
                    "description": "Site ID to crawl from (pick from the site list in the description above). Match user's geographic area or topic to a site_id.",
                    "required": True,
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Max pages to crawl. Default 1 (latest only). Increase to 2-5 only if user asks for more history. Never exceed 5.",
                    "default": 1,
                    "required": False,
                },
                "force": {
                    "type": "boolean",
                    "description": "Force crawl even if another crawl is already running for this site.",
                    "default": False,
                    "required": False,
                },
            },
        }
        super().__init__()


class CrawlFetch(ToolBase, ABC):
    """Agent tool — crawl a configured site as a fallback data source."""

    component_name = "CrawlFetch"

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("CrawlFetch"):
            return

        site_id = str(kwargs.get("site_id", "")).strip()
        max_pages = int(kwargs.get("max_pages", 1) or 1)
        force = bool(kwargs.get("force", False))

        if not site_id:
            self.set_output("_ERROR", "site_id is required")
            return "Error: site_id is required. Pick from the available sites list."

        # --- Load site config ---
        try:
            loader = _load_loader()
            try:
                config = loader.get(site_id)
            except KeyError:
                available = ", ".join(loader.list_site_ids()[:20])
                return f"Error: site '{site_id}' not found. Available: {available}..."
        except Exception as e:
            self.set_output("_ERROR", str(e))
            return f"Error loading site config: {e}"

        # --- Get tenant_id and kb_id from canvas context ---
        tenant_id = ""
        kb_id = ""
        if hasattr(self, "_canvas") and self._canvas:
            tenant_id = self._canvas.get_tenant_id() or ""
            try:
                g = self._canvas.get_globals()
                kb_id = g.get("kb_id", "") if g else ""
            except Exception:
                pass

        if not tenant_id:
            self.set_output("_ERROR", "tenant_id not available in canvas context")
            return "Error: cannot determine tenant_id from canvas context."

        # --- Override max_pages in config ---
        original_max_pages = config.pagination.max_pages
        config.pagination.max_pages = max_pages

        # === Phase 1: Sync — crawl + write to bid tables ===
        try:
            from rag.svr.crawler_engine.engine import CrawlerEngine

            engine = CrawlerEngine(config)
            result = engine.run(
                tenant_id=tenant_id,
                kb_id=kb_id,
                task_name=f"agent_crawl_{site_id}",
                force=force,
                skip_kb=True,           # skip KB upload during crawl
                skip_attachments=True,   # skip attachment download during crawl
            )
        except Exception as e:
            logging.exception("CrawlFetch engine error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"CrawlFetch error: {e}"
        finally:
            config.pagination.max_pages = original_max_pages

        # === Phase 2: Query bid_project for content to return to LLM ===
        items = self._query_recent_items(site_id)

        # === Phase 3: Async — import to KB + parse (background) ===
        if kb_id and items:
            self._async_import_to_kb(items, kb_id, tenant_id)

        # === Format and return ===
        output = self._format_result(config.name, site_id, result, items)

        self.set_output("json", output)
        self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
        return self.output("formalized_content")

    def _query_recent_items(self, site_id: str) -> list:
        """Query bid_project table for the latest items from this site."""
        try:
            from api.db import DB
            from api.db.db_models import BidProject

            rows = (BidProject
                    .select(BidProject.id, BidProject.title,
                            BidProject.content, BidProject.publish_time,
                            BidProject.has_file, BidProject.project_money)
                    .where(BidProject.source_type == site_id)
                    .order_by(BidProject.publish_time.desc())
                    .limit(_MAX_OUTPUT_ITEMS))

            with DB.connection_context():
                items = []
                for r in rows:
                    pub_time = ""
                    if r.publish_time:
                        pub_time = r.publish_time.strftime("%Y-%m-%d %H:%M") if hasattr(r.publish_time, "strftime") else str(r.publish_time)
                    content = (r.content or "")[:_MAX_CONTENT_CHARS]
                    items.append({
                        "id": r.id,
                        "title": r.title or "",
                        "content": content,
                        "publish_time": pub_time,
                        "has_attachment": bool(r.has_file),
                        "money": r.project_money or "",
                    })
                return items
        except Exception as e:
            logging.warning("CrawlFetch: failed to query bid_project: %s", e)
            return []

    def _async_import_to_kb(self, items: list, kb_id: str,
                            tenant_id: str) -> None:
        """Import crawled items to KB in background threads."""
        for item in items:
            project_id = item.get("id")
            pub_time = item.get("publish_time", "")
            if not project_id:
                continue
            try:
                t = threading.Thread(
                    target=self._import_one,
                    args=(project_id, pub_time, kb_id, tenant_id),
                    daemon=True,
                )
                t.start()
            except Exception as e:
                logging.warning("CrawlFetch: async import failed for %s: %s", project_id, e)

    @staticmethod
    def _import_one(project_id: int, publish_time: str,
                    kb_id: str, tenant_id: str) -> None:
        """Import one bid project to KB (runs in background thread)."""
        try:
            from api.utils.bid_tool_service import import_bid_to_kb
            import_bid_to_kb(
                project_id=project_id,
                publish_time=publish_time,
                kb_id=kb_id,
                user_id=tenant_id,
            )
            logging.info("CrawlFetch: async KB import started for project %s", project_id)
        except Exception as e:
            logging.warning("CrawlFetch: async KB import failed for %s: %s", project_id, e)

    def _format_result(self, site_name: str, site_id: str,
                       result: dict, items: list) -> dict:
        """Format engine result + content items for the LLM."""
        status = result.get("status", "unknown")

        if status == "skipped":
            return {
                "status": "skipped",
                "site": site_name,
                "site_id": site_id,
                "reason": result.get("reason", "unknown"),
                "message": f"Site '{site_name}' crawl skipped: {result.get('reason', 'unknown')}",
            }

        if status == "empty":
            return {
                "status": "empty",
                "site": site_name,
                "site_id": site_id,
                "message": f"No new data found from '{site_name}'. All items may be duplicates.",
            }

        # Stats from engine
        new_items = result.get("new_items", 0)
        scanned_pages = result.get("scanned_pages", 0)
        bid_stats = result.get("bid_stats", {})
        bid_written = bid_stats.get("bid_written", 0) if isinstance(bid_stats, dict) else new_items
        dedup_stats = result.get("dedup_stats", {})
        dup_count = (dedup_stats.get("db_hits", 0) + dedup_stats.get("memory_hits", 0)
                      ) if isinstance(dedup_stats, dict) else 0

        output = {
            "status": "completed",
            "site": site_name,
            "site_id": site_id,
            "summary": {
                "new_items_stored": new_items,
                "bid_written": bid_written,
                "duplicates_skipped": dup_count,
            },
            "items": items,
            "message": (
                f"Crawled '{site_name}': {new_items} new items stored in database. "
                f"{len(items)} items returned. "
                f"KB import and parsing running in background."
            ),
        }

        return output

    def thoughts(self) -> str:
        site_id = self.get_input().get("site_id", "-")
        return f"Crawling site '{site_id}'..."
