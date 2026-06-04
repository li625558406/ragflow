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
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import json
import logging
import os
from abc import ABC
from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.connection_utils import timeout


class BidLookupCodeParam(ToolParamBase):
    """
    Define the BidLookupCode component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "lookup_bid_code",
            "description": """
Look up Chinese administrative area codes or GB/T 4754-2017 industry codes by Chinese name.
Use the returned codes as input for subsequent searches.

Examples:
  - "广东" → area code "44" (广东省)
  - "广州" → area code "440100" (广州市)
  - "建筑" → industry code "E" (建筑业)
  - "土木工程" → industry code "E48" (土木工程建筑业)

Supports fuzzy matching — "广州" matches "广州市", "建筑" matches "建筑业".
Returns a list of matching codes with full names, so you can pick the right one.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Chinese name to search. Can be a location name (e.g. '广东', '广州', '朝阳') or industry name (e.g. '建筑', '农业', '信息技术').",
                    "required": True,
                },
                "code_type": {
                    "type": "string",
                    "description": "Type of code to look up: 'area' for administrative divisions (provinces, cities, districts), 'industry' for GB/T 4754-2017 industry classifications.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "keyword": {
                "name": "Keyword",
                "type": "line",
            }
        }


class BidLookupCode(ToolBase, ABC):
    component_name = "BidLookupCode"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidLookupCode processing"):
            return

        from api.utils.bid_tool_service import lookup_bid_code

        try:
            keyword = kwargs.get("keyword", "")
            code_type = kwargs.get("code_type", "area")

            if not keyword:
                self.set_output("_ERROR", "keyword is required")
                return "Error: keyword is required"

            result = lookup_bid_code(keyword=str(keyword), code_type=str(code_type))

            self.set_output("json", result)
            self.set_output("formalized_content", json.dumps(result, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidLookupCode error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidLookupCode error: {e}"

    def thoughts(self) -> str:
        return """
Looking up code for "{}" (type: {})...
                """.format(
            self.get_input().get("keyword", "-"),
            self.get_input().get("code_type", "area"),
        )


class BidSearchParam(ToolParamBase):
    """
    Define the BidSearch component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_search",
            "description": """
Search Chinese government procurement bid projects from the bidding database.
Use this tool when the user asks about bid/procurement/tender projects, such as:
  - "find recent construction bids in Guangdong"
  - "search for IT procurement projects"
  - "show me bid projects about medical equipment"
  - "what bidding opportunities exist for environmental protection"

The search returns project id, title, publish_time, project_money, has_file, and other metadata.
IMPORTANT: Save the returned 'id' and 'publish_time' fields — they uniquely identify a project for detail retrieval.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (matches title, content, party names). Use specific terms for better results.",
                    "default": "",
                    "required": False,
                },
                "start_date": {
                    "type": "string",
                    "description": "Publish date lower bound in YYYY-MM-DD format. Default: 30 days ago.",
                    "default": "",
                    "required": False,
                },
                "end_date": {
                    "type": "string",
                    "description": "Publish date upper bound in YYYY-MM-DD format. Default: today.",
                    "default": "",
                    "required": False,
                },
                "provice_code": {
                    "type": "string",
                    "description": "Province administrative code for regional filtering.",
                    "default": "",
                    "required": False,
                },
                "city_code": {
                    "type": "string",
                    "description": "City administrative code for regional filtering.",
                    "default": "",
                    "required": False,
                },
                "industry_code": {
                    "type": "string",
                    "description": "GB/T 4754-2017 industry classification code. Single letter (A-T) for category, multi-char for sub-industry.",
                    "default": "",
                    "required": False,
                },
                "project_money_min": {
                    "type": "integer",
                    "description": "Minimum project amount in RMB.",
                    "default": None,
                    "required": False,
                },
                "project_money_max": {
                    "type": "integer",
                    "description": "Maximum project amount in RMB.",
                    "default": None,
                    "required": False,
                },
                "part_a_name": {
                    "type": "string",
                    "description": "Party A (buyer/procuring entity) name filter.",
                    "default": "",
                    "required": False,
                },
                "part_b_name": {
                    "type": "string",
                    "description": "Party B (supplier/winner) name filter.",
                    "default": "",
                    "required": False,
                },
                "has_file": {
                    "type": "integer",
                    "description": "Filter by attachment presence: 1 = has files, 0 = no files.",
                    "default": None,
                    "required": False,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page. Default: 10, max: 50.",
                    "default": 10,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "keyword": {
                "name": "Keyword",
                "type": "line",
            }
        }


class BidSearch(ToolBase, ABC):
    component_name = "BidSearch"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidSearch processing"):
            return

        from api.utils.bid_tool_service import search_bid_projects

        try:
            result = search_bid_projects(
                keyword=kwargs.get("keyword", ""),
                start_date=kwargs.get("start_date", ""),
                end_date=kwargs.get("end_date", ""),
                provice_code=kwargs.get("provice_code", ""),
                city_code=kwargs.get("city_code", ""),
                industry_code=kwargs.get("industry_code", ""),
                project_money_min=kwargs.get("project_money_min"),
                project_money_max=kwargs.get("project_money_max"),
                part_a_name=kwargs.get("part_a_name", ""),
                part_b_name=kwargs.get("part_b_name", ""),
                has_file=kwargs.get("has_file"),
                page=kwargs.get("page", 1),
                page_size=kwargs.get("page_size", 10),
            )

            projects = result.get("projects", [])
            total = result.get("total", 0)

            simplified = []
            for p in projects:
                simplified.append({
                    "id": p.get("id"),
                    "title": p.get("title", ""),
                    "publish_time": str(p.get("publish_time", "")),
                    "project_class_id": p.get("project_class_id"),
                    "project_money": str(p.get("project_money", "")),
                    "has_file": bool(p.get("has_file")),
                    "provice_code": p.get("provice_code"),
                    "city_code": p.get("city_code"),
                    "industry_codes": p.get("industry_codes"),
                    "part_a_names": p.get("part_a_names"),
                    "part_b_names": p.get("part_b_names"),
                    "source_type": p.get("source_type"),
                })

            output = {
                "total": total,
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "projects": simplified,
            }

            self.set_output("json", simplified)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidSearch error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidSearch error: {e}"

    def thoughts(self) -> str:
        return """
Keywords: {}
Searching bid projects database...
                """.format(self.get_input().get("keyword", "-"))


class BidGetDetailParam(ToolParamBase):
    """
    Define the BidGetDetail component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_get_detail",
            "description": """
Get full detail of a specific bid project AND automatically trigger async import into the knowledge base.
This is the PRIMARY tool to use when a user wants to see or analyze a specific bid project.

What it does (all in one call):
  1. Fetches full project detail SYNCHRONOUSLY (content HTML, structured data, attached files)
  2. Triggers ASYNC background import: combined text + all attached files → KB → parse
  3. Returns IMMEDIATELY with detail summary + kb_import status ("parsing" = background thread working)

The import runs in the background — no need to wait. The kb_import status field shows the current import progress.

            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project (used for API authentication).",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "project_id": {
                "name": "Project ID",
                "type": "line",
            }
        }


class BidGetDetail(ToolBase, ABC):
    component_name = "BidGetDetail"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidGetDetail processing"):
            return

        from api.utils.bid_tool_service import get_bid_detail, import_bid_to_kb

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")

            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            # Phase 1: Fetch detail (cache-first)
            result = get_bid_detail(int(project_id), str(publish_time))

            # Extract plain text summary for LLM
            content_html = result.get("content_html", "")
            structure = result.get("structure", {})
            files = result.get("files", [])

            # Build a readable summary
            import re
            text_preview = re.sub(r'<[^>]+>', '', content_html)[:2000] if content_html else ""

            file_list = []
            for f in (files or []):
                file_list.append({
                    "name": f.get("file_name", ""),
                    "url": f.get("file_url", ""),
                    "suffix": f.get("file_suffix", ""),
                    "size": f.get("file_size"),
                })

            output = {
                "project_id": project_id,
                "content_preview": text_preview,
                "content_length": len(content_html) if content_html else 0,
                "structure": {
                    "project_name": structure.get("project_name"),
                    "project_numbers": structure.get("project_numbers"),
                    "budget_money": structure.get("budget_money"),
                    "bid_money": structure.get("bid_money"),
                    "bid_start_date": structure.get("bid_start_date"),
                    "bid_start_address": structure.get("bid_start_address"),
                    "sign_up_stop_date": structure.get("sign_up_stop_date"),
                    "party_a_info": structure.get("party_a_info"),
                    "party_b_info": structure.get("party_b_info"),
                    "agency_info": structure.get("agency_info"),
                    "bid_companies": structure.get("bid_companies"),
                },
                "files": file_list,
                "cached": result.get("cached", True),
            }

            # Phase 2: Auto-import to knowledge base (ASYNC — non-blocking)
            # This phase is best-effort. The background thread handles:
            #   - Fetching full detail + structure
            #   - Uploading combined text as a KB document
            #   - Downloading attached files, extracting archives, uploading to KB
            #   - Triggering parsing and polling until complete
            # We return immediately; use bid_check_import_status to monitor progress.
            try:
                user_id = ""
                if hasattr(self, '_canvas'):
                    user_id = self._canvas.get_tenant_id() or ""

                import_result = import_bid_to_kb(
                    project_id=int(project_id),
                    publish_time=str(publish_time),
                    kb_id=None,
                    user_id=user_id,
                )

                output["kb_import"] = {
                    "status": import_result.get("status", "parsing"),
                    "kb_id": import_result.get("kb_id"),
                    "combined_doc_id": import_result.get("combined_doc_id"),
                    "progress": import_result.get("progress", 0),
                    "message": import_result.get("message", ""),
                }
            except Exception as kb_err:
                logging.exception("BidGetDetail: KB import trigger failed (detail content unaffected)")
                output["kb_import"] = {
                    "status": "error",
                    "message": f"KB import error: {str(kb_err)}. Detail content is available below.",
                }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidGetDetail error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidGetDetail error: {e}"

    def thoughts(self) -> str:
        return "Fetching bid project detail for #{}...".format(
            self.get_input().get("project_id", "-")
        )


class BidImportToKbParam(ToolParamBase):
    """
    Define the BidImportToKb component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_import_to_kb",
            "description": """
Import a bid project's detail content and attached files into a RAGFlow knowledge base, then trigger document parsing.
This tool returns IMMEDIATELY with status "parsing" — the download, upload and parsing happen in the background.

Use this tool when the user wants to:
  - "import this project to my knowledge base"
  - "analyze this bid and help me write a response"
  - "generate a bid document based on this project"

Dedup: If the project was already imported, returns the existing status directly.
Provide the project_id and publish_time of the project to import.
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project.",
                    "required": True,
                },
                "kb_id": {
                    "type": "string",
                    "description": "Target knowledge base ID. Uses the default bid KB if not specified.",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "project_id": {
                "name": "Project ID",
                "type": "line",
            }
        }


class BidImportToKb(ToolBase, ABC):
    component_name = "BidImportToKb"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidImportToKb processing"):
            return

        from api.utils.bid_tool_service import import_bid_to_kb, check_import_status

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")
            kb_id = kwargs.get("kb_id", "")

            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            # Get user_id from canvas context if available
            user_id = ""
            if hasattr(self, '_canvas'):
                user_id = self._canvas.get_tenant_id() or ""

            # Check existing status first
            status = check_import_status(int(project_id))
            if status.get("status") == "done":
                output = {
                    "project_id": int(project_id),
                    "kb_id": status.get("kb_id"),
                    "combined_doc_id": status.get("combined_doc_id"),
                    "status": "done",
                    "progress": 1.0,
                    "message": "Project was already imported to KB.",
                }
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
                return self.output("formalized_content")

            if status.get("status") == "parsing":
                output = {
                    "project_id": int(project_id),
                    "kb_id": status.get("kb_id"),
                    "status": "parsing",
                    "progress": status.get("progress", 0),
                    "message": "Project is currently being imported/parsed.",
                }
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
                return self.output("formalized_content")

            # Execute import
            result = import_bid_to_kb(
                project_id=int(project_id),
                publish_time=str(publish_time),
                kb_id=kb_id if kb_id else None,
                user_id=user_id,
            )

            output = {
                "project_id": int(project_id),
                "kb_id": result.get("kb_id"),
                "combined_doc_id": result.get("combined_doc_id"),
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "message": result.get("message"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidImportToKb error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidImportToKb error: {e}"

    def thoughts(self) -> str:
        return "Importing bid project #{} to knowledge base...".format(
            self.get_input().get("project_id", "-")
        )


class BidCheckImportStatusParam(ToolParamBase):
    """
    Define the BidCheckImportStatus component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_check_import_status",
            "description": """
Check the import/parsing progress of a bid project in the knowledge base.
Use this to poll until parsing is complete.

Returns the current status: "parsing" (still in progress), "done" (complete), or "fail".
When status is "parsing", doc_progress shows detailed per-document progress (done/fail/running counts).
When status is "done", the KB is ready and you can answer user questions based on the imported content.

Poll repeatedly (every few seconds) until status becomes "done".
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "project_id": {
                "name": "Project ID",
                "type": "line",
            }
        }


class BidCheckImportStatus(ToolBase, ABC):
    component_name = "BidCheckImportStatus"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidCheckImportStatus processing"):
            return

        from api.utils.bid_tool_service import check_import_status

        try:
            project_id = kwargs.get("project_id")
            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            result = check_import_status(int(project_id))

            output = {
                "project_id": int(project_id),
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "message": result.get("message", ""),
                "doc_progress": result.get("doc_progress"),
                "kb_id": result.get("kb_id"),
                "combined_doc_id": result.get("combined_doc_id"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidCheckImportStatus error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidCheckImportStatus error: {e}"

    def thoughts(self) -> str:
        return "Checking import status for project #{}...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# BidGetSource — 获取原始采集网址
# =============================================================================

class BidGetSourceParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_get_source",
            "description": """
Get the original source URL (collect URL) of a bid project.
This is the original government website where the bid was published.
Use this when the user wants to see the original announcement page.

Requires project_id and publish_time (same as bid_get_detail).
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"project_id": {"name": "Project ID", "type": "line"}}


class BidGetSource(ToolBase, ABC):
    component_name = "BidGetSource"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidGetSource processing"):
            return
        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")
            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            from api.utils.bid_api_client import BidApiClient
            client = BidApiClient()
            url = client.get_collect_url(int(project_id), str(publish_time))

            output = {"project_id": project_id, "source_url": url}
            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidGetSource error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidGetSource error: {e}"

    def thoughts(self) -> str:
        return "Fetching source URL for project #{}...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# BidSearchAI — AI 专用轻量搜索
# =============================================================================

class BidSearchAIParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_search_ai",
            "description": """
AI-friendly lightweight bid project search.
Returns richer structured data (party info, bid dates, areas) in a single call.
Ideal for AI agents that need structured project overview without separate detail calls.

Use this when you need:
  - Quick project overview with structured party information
  - Natural language area/industry filtering (pass area_name like '广东省')
  - Category filtering (e.g., className='招标信息,中标信息')

Returns up to 20 results per page.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword.",
                    "default": "",
                    "required": False,
                },
                "class_name": {
                    "type": "string",
                    "description": "Category filter (comma-separated): 招标信息,中标信息,合同信息,采购意向,拍租信息",
                    "default": "",
                    "required": False,
                },
                "area_name": {
                    "type": "string",
                    "description": "Area name for filtering (e.g., '广东省', '北京').",
                    "default": "",
                    "required": False,
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"keyword": {"name": "Keyword", "type": "line"}}


class BidSearchAI(ToolBase, ABC):
    component_name = "BidSearchAI"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidSearchAI processing"):
            return
        try:
            from datetime import datetime, timedelta
            from api.utils.bid_api_client import BidApiClient

            client = BidApiClient()
            default_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
            default_end = datetime.now().strftime("%Y-%m-%d 23:59:59")

            result = client.search_project_ai(
                keyword=kwargs.get("keyword", ""),
                class_name=kwargs.get("class_name", ""),
                area_name=kwargs.get("area_name", ""),
                start_date=kwargs.get("start_date", "") or default_start,
                end_date=kwargs.get("end_date", "") or default_end,
                page_id=kwargs.get("page", 1),
                page_number=20,
            )

            data = result.get("data", {})
            items = data.get("data", []) or []
            simplified = []
            for item in items:
                simplified.append({
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "news_type_name": item.get("newsTypeName", ""),
                    "publish_time": item.get("publishTime", ""),
                    "area_name": item.get("areaName", ""),
                    "project_money": item.get("projectMoney", ""),
                    "project_class": item.get("projectClass", ""),
                    "purchase_type": item.get("purchaseType", ""),
                    "part_a_info": item.get("partAInfo", []),
                    "part_b_info": item.get("partBInfo", []),
                    "agency_info": item.get("agencyInfo", []),
                    "bid_start_date": item.get("bidStartDate", ""),
                    "bid_start_address": item.get("bidStartAddress", ""),
                    "sign_up_stop_date": item.get("siginUpStopDate", ""),
                })

            output = {
                "total": data.get("total", 0),
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "projects": simplified,
            }

            self.set_output("json", simplified)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidSearchAI error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidSearchAI error: {e}"

    def thoughts(self) -> str:
        return "AI searching bid projects: '{}'...".format(
            self.get_input().get("keyword", "-")
        )


# =============================================================================
# BidSearchContract — 合同数据搜索
# =============================================================================

class BidSearchContractParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_search_contract",
            "description": """
Search contract/bid-result data from the bidding database.
Returns projects with contract details: contract dates, project cycle, party info with contacts.
Use this when the user asks about contracts, bid results, or winners.

Parameters are similar to bid_search but results include contractStartDate, contractEndDate, projectCycle.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword.",
                    "default": "",
                    "required": False,
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "contract_end_min": {
                    "type": "string",
                    "description": "Contract end date minimum (YYYY-MM-DD).",
                    "default": "",
                    "required": False,
                },
                "contract_end_max": {
                    "type": "string",
                    "description": "Contract end date maximum (YYYY-MM-DD).",
                    "default": "",
                    "required": False,
                },
                "part_a_name": {
                    "type": "string",
                    "description": "Party A (buyer) name filter.",
                    "default": "",
                    "required": False,
                },
                "part_b_name": {
                    "type": "string",
                    "description": "Party B (supplier/winner) name filter.",
                    "default": "",
                    "required": False,
                },
                "provice_code": {
                    "type": "string",
                    "description": "Province administrative code.",
                    "default": "",
                    "required": False,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"keyword": {"name": "Keyword", "type": "line"}}


class BidSearchContract(ToolBase, ABC):
    component_name = "BidSearchContract"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidSearchContract processing"):
            return
        try:
            from datetime import datetime, timedelta
            from api.utils.bid_api_client import BidApiClient

            client = BidApiClient()
            default_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d 00:00:00")
            default_end = datetime.now().strftime("%Y-%m-%d 23:59:59")

            api_area_code = {
                "proviceCodeList": [kwargs.get("provice_code")] if kwargs.get("provice_code") else ["0"],
                "cityCodeList": [],
                "countyCodeList": [],
            }

            result = client.search_contract(
                keyword=kwargs.get("keyword", ""),
                area_code=api_area_code,
                start_date=kwargs.get("start_date", "") or default_start,
                end_date=kwargs.get("end_date", "") or default_end,
                contract_end_min=kwargs.get("contract_end_min", ""),
                contract_end_max=kwargs.get("contract_end_max", ""),
                part_a_name=kwargs.get("part_a_name", ""),
                part_b_name=kwargs.get("part_b_name", ""),
                page_id=kwargs.get("page", 1),
                page_number=20,
            )

            data = result.get("data", {})
            items = data.get("data", []) or []
            simplified = []
            for item in items:
                simplified.append({
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "publish_time": item.get("publishTime", ""),
                    "project_money": item.get("projectMoney", ""),
                    "has_file": bool(item.get("hasFile")),
                    "project_cycle": item.get("projectCycle", []),
                    "part_a_info": item.get("partAInfo", []),
                    "part_b_info": item.get("partBInfo", []),
                    "contract_start_date": item.get("contractStartDate", ""),
                    "contract_end_date": item.get("contractEndDate", ""),
                })

            output = {
                "total": data.get("total", 0),
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "contracts": simplified,
            }

            self.set_output("json", simplified)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidSearchContract error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidSearchContract error: {e}"

    def thoughts(self) -> str:
        return "Searching contracts: '{}'...".format(
            self.get_input().get("keyword", "-")
        )


# =============================================================================
# BidRewriteQuery — AI 搜索条件重写
# =============================================================================

class BidRewriteQueryParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_rewrite_query",
            "description": """
Rewrite a natural language query into structured search conditions for bid search.
This is an AI-powered tool that extracts: search phrases, synonyms, party names,
area codes, and industry codes from a natural language description.

Example: "军队采购网 病床 北京" → {
  searchPhrase: "病床",
  partyANames: ["军队采购网"],
  areaCode: {proviceCodeList: ["110000"]},
  industryCodes: [{thirdCodeList: ["C277", "Q831"]}]
}

After getting the rewritten conditions, use bid_search with the extracted parameters.
            """,
            "parameters": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query to rewrite.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"query": {"name": "Query", "type": "line"}}


class BidRewriteQuery(ToolBase, ABC):
    component_name = "BidRewriteQuery"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidRewriteQuery processing"):
            return
        try:
            query = kwargs.get("query", "")
            if not query:
                self.set_output("_ERROR", "query is required")
                return "Error: query is required"

            from api.utils.bid_api_client import BidApiClient
            client = BidApiClient()
            result = client.ai_search_rewrite(query)

            data = result.get("data", {})
            output = {
                "request_key": data.get("requestKey", ""),
                "status": data.get("status", ""),
                "search_condition": data.get("searchCondition"),
                "industry_codes": data.get("industryCodes"),
                "area_code": data.get("areaCode"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidRewriteQuery error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidRewriteQuery error: {e}"

    def thoughts(self) -> str:
        return "Rewriting query: '{}'...".format(
            self.get_input().get("query", "-")[:50]
        )


# =============================================================================
# BidIndustryTag — AI 行业标签推理
# =============================================================================

class BidIndustryTagParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_industry_tag",
            "description": """
Infer industry classification codes from a keyword or phrase.
Returns a list of candidate industry codes with full path titles.

Example: "教育" → [
  {thirdCodeList: ["P824"], fullTitle: "教育-教育-高等教育", minTitle: "高等教育"},
  {thirdCodeList: ["P823"], fullTitle: "教育-教育-中等教育", minTitle: "中等教育"},
  ...
]

Use the returned codes as industry_code parameter in bid_search.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Industry keyword or phrase to reason about.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"keyword": {"name": "Keyword", "type": "line"}}


class BidIndustryTag(ToolBase, ABC):
    component_name = "BidIndustryTag"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidIndustryTag processing"):
            return
        try:
            keyword = kwargs.get("keyword", "")
            if not keyword:
                self.set_output("_ERROR", "keyword is required")
                return "Error: keyword is required"

            from api.utils.bid_api_client import BidApiClient
            client = BidApiClient()
            codes = client.ai_industry_reasoning(keyword)

            output = {"keyword": keyword, "candidates": codes}
            self.set_output("json", codes)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidIndustryTag error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidIndustryTag error: {e}"

    def thoughts(self) -> str:
        return "Inferring industry codes for '{}'...".format(
            self.get_input().get("keyword", "-")
        )


# =============================================================================
# BidEnterpriseProfile — 企业画像
# =============================================================================

class BidEnterpriseProfileParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_enterprise_profile",
            "description": """
Get comprehensive enterprise profile from the bidding database.
Returns: basic info (registration, capital, legal rep, business scope),
project insights (bid/win statistics by industry), relationship summary
(customer/supplier counts).

Use this when the user asks about a company's background, capabilities,
bidding history, or business relationships.
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class BidEnterpriseProfile(ToolBase, ABC):
    component_name = "BidEnterpriseProfile"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidEnterpriseProfile processing"):
            return
        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            from api.utils.bid_api_client import BidApiClient
            client = BidApiClient()
            result = client.get_company_profile_summary(company_name)
            raw_data = result.get("data", {})

            # Extract key fields for a readable summary
            base = raw_data.get("baseInfo", {})
            profile = base.get("enterpriseProfile", {})
            reg = base.get("registrationInfo", {})
            operation = base.get("operationInfo", {})
            contact = base.get("contactInfo", {})
            insights = raw_data.get("projectInsights", {})
            relationships = raw_data.get("relationshipSummary", {})
            status = raw_data.get("dataStatus", {})

            output = {
                "company_name": company_name,
                "type": profile.get("companyTypeName", ""),
                "legal_representative": profile.get("legalRepresentative", ""),
                "establishment_date": profile.get("establishmentDate", ""),
                "operating_status": profile.get("operatingStatus", {}).get("statusName", ""),
                "industry": profile.get("industryName", ""),
                "registered_region": profile.get("registeredRegion", {}),
                "credit_code": reg.get("creditCode", ""),
                "registered_capital": reg.get("registeredCapital", {}),
                "business_scope": operation.get("businessScope", ""),
                "registered_address": contact.get("registeredAddress", ""),
                "website": contact.get("website", ""),
                "contact_phones": contact.get("contactPhones", []),
                "contact_emails": contact.get("contactEmails", []),
                "bid_statistics": insights.get("bidStatistics", []),
                "win_statistics": insights.get("winStatistics", []),
                "contact_person_count": relationships.get("contactPersonCount", ""),
                "customer_project_count": relationships.get("customerProjectCount", ""),
                "supplier_project_count": relationships.get("supplierProjectCount", ""),
                "data_status": status,
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidEnterpriseProfile error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidEnterpriseProfile error: {e}"

    def thoughts(self) -> str:
        return "Fetching enterprise profile for '{}'...".format(
            self.get_input().get("company_name", "-")
        )


# =============================================================================
# BidConstructionSearch — 拟在建项目搜索
# =============================================================================

class BidConstructionSearchParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_construction_search",
            "description": """
Search 'under-construction' (拟在建) project information.
These are projects in planning, approval, or early construction stages.
Returns project title, summary, construction company, and publish time.

Use this when the user asks about upcoming construction projects,
infrastructure plans, or projects still in the approval process.
            """,
            "parameters": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (e.g., hospital, highway, school).",
                    "default": "",
                    "required": False,
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD HH:mm:ss format.",
                    "default": "",
                    "required": False,
                },
                "provice_code": {
                    "type": "string",
                    "description": "Province administrative code.",
                    "default": "",
                    "required": False,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"keyword": {"name": "Keyword", "type": "line"}}


class BidConstructionSearch(ToolBase, ABC):
    component_name = "BidConstructionSearch"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidConstructionSearch processing"):
            return
        try:
            from datetime import datetime, timedelta
            from api.utils.bid_api_client import BidApiClient

            client = BidApiClient()
            default_start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
            default_end = datetime.now().strftime("%Y-%m-%d 23:59:59")

            api_area_code = {
                "proviceCodeList": [kwargs.get("provice_code")] if kwargs.get("provice_code") else ["0"],
                "cityCodeList": [],
                "countyCodeList": [],
            }

            result = client.search_nzj_project(
                keyword=kwargs.get("keyword", ""),
                area_code=api_area_code,
                start_date=kwargs.get("start_date", "") or default_start,
                end_date=kwargs.get("end_date", "") or default_end,
                page_id=kwargs.get("page", 1),
                page_number=20,
            )

            data = result.get("data", {})
            items = data.get("data", []) or []
            simplified = []
            for item in items:
                simplified.append({
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "publish_time": item.get("publishTime", ""),
                    "provice_code": item.get("proviceCode", ""),
                    "city_code": item.get("cityCode", ""),
                    "county_code": item.get("countyCode", ""),
                    "has_file": bool(item.get("hasFile")),
                    "score": item.get("score"),
                })

            output = {
                "total": data.get("total", 0),
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "projects": simplified,
            }

            self.set_output("json", simplified)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidConstructionSearch error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidConstructionSearch error: {e}"

    def thoughts(self) -> str:
        return "Searching construction projects: '{}'...".format(
            self.get_input().get("keyword", "-")
        )
