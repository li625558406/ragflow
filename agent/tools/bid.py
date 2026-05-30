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
Use this BEFORE calling bid_search to convert user's natural-language location/industry references into codes.

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
IMPORTANT: Save the returned 'id' and 'publish_time' fields — they are REQUIRED for subsequent get_detail calls.
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
Get full detail of a specific bid project AND automatically import it into the knowledge base for parsing.
This is the PRIMARY tool to use when a user wants to see or analyze a specific bid project.

What it does (all in one call):
  1. Fetches full project detail (content HTML, structured data, attached files)
  2. Automatically imports the content + attachments into the knowledge base
  3. Triggers document parsing and waits for it to complete (up to 120s)
  4. Returns the detail summary + KB import status

When kb_import.status is "done", the KB is ready — you can immediately use KB search/retrieval
to answer the user's questions about this project's content.

IMPORTANT: Both project_id AND publish_time must be obtained from the bid_search result.
The bid_search response includes 'publish_time' for each project — use the EXACT value.
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID — obtained from the bid_search result's 'id' field.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project — MUST be obtained from the bid_search result's 'publish_time' field. Used for API authentication.",
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

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 180)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidGetDetail processing"):
            return

        from api.utils.bid_tool_service import get_bid_detail, import_bid_to_kb, check_import_status

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

            # Phase 2: Auto-import to knowledge base and wait for parsing
            # This phase is best-effort — failure here does NOT block returning the detail content
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

                if import_result.get("status") == "fail":
                    output["kb_import"] = {
                        "status": "fail",
                        "message": import_result.get("message", ""),
                    }
                elif import_result.get("status") == "done":
                    output["kb_import"] = {
                        "status": "done",
                        "kb_id": import_result.get("kb_id"),
                        "message": "Already imported to KB and parsed.",
                    }
                else:
                    # Poll until parsing completes or timeout
                    import time
                    max_wait = 120
                    poll_interval = 3
                    waited = 0
                    last_status = {}
                    while waited < max_wait:
                        if self.check_if_canceled("KB import polling"):
                            output["kb_import"] = {"status": "parsing", "message": "Polling cancelled."}
                            break
                        time.sleep(poll_interval)
                        waited += poll_interval
                        last_status = check_import_status(int(project_id))
                        if last_status.get("status") == "done":
                            output["kb_import"] = {
                                "status": "done",
                                "kb_id": last_status.get("kb_id"),
                                "combined_doc_id": last_status.get("combined_doc_id"),
                            }
                            break
                        elif last_status.get("status") == "fail":
                            output["kb_import"] = {
                                "status": "fail",
                                "message": last_status.get("message", ""),
                            }
                            break
                        else:
                            output["kb_import"] = {
                                "status": "parsing",
                                "progress": last_status.get("progress", 0),
                                "message": f"Parsing in progress ({waited}s / {max_wait}s max)...",
                            }
                    else:
                        output["kb_import"] = {
                            "status": "timeout",
                            "progress": last_status.get("progress", 0),
                            "message": f"Still parsing after {max_wait}s. Use bid_check_import_status to monitor.",
                        }
            except Exception as kb_err:
                logging.exception("BidGetDetail: KB import failed (detail content unaffected)")
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
IMPORTANT: project_id AND publish_time must be obtained from the bid_search result.
IMPORTANT: After calling this tool, you MUST poll check_import_status repeatedly (every few seconds)
           until status becomes "done", then proceed to answer user questions using KB content.
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID — obtained from the bid_search result's 'id' field.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project — MUST be obtained from the bid_search result's 'publish_time' field.",
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
Use this tool AFTER calling bid_import_to_kb to poll until parsing is complete.

Returns the current status: "parsing" (still in progress), "done" (complete), or "fail".
When status is "parsing", doc_progress shows detailed per-document progress (done/fail/running counts).
When status is "done", the KB is ready and you can answer user questions based on the imported content.

IMPORTANT: poll this tool repeatedly (every 3-5 seconds) until status becomes "done".
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Bid project ID — the same ID passed to bid_import_to_kb.",
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
