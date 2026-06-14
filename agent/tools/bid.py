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


def _batch_fill_source_urls(items: list, client) -> None:
    """Batch fetch collect_url for items that don't have a source_url yet.

    Modifies items in-place, setting 'source_url' for each item.
    Fails silently for individual items to avoid breaking the whole response.
    """
    for item in items:
        if item.get("source_url"):
            continue
        pid = item.get("id")
        pub_time = item.get("publish_time") or ""
        if not pid:
            continue
        try:
            url = client.get_collect_url(int(pid), str(pub_time))
            item["source_url"] = url or ""
        except Exception:
            pass


class BidLookupCodeParam(ToolParamBase):
    """
    Define the BidLookupCode component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "lookup_bid_code",
            "description": """
根据中文名称查询行政区划代码或 GB/T 4754-2017 行业分类代码。返回的代码可用于后续的标讯搜索。

示例：
  - "广东" → 行政区划代码 "44"（广东省）
  - "广州" → 行政区划代码 "440100"（广州市）
  - "建筑" → 行业代码 "E"（建筑业）
  - "土木工程" → 行业代码 "E48"（土木工程建筑业）

支持模糊匹配——"广州"可匹配"广州市"，"建筑"可匹配"建筑业"。
返回匹配的代码列表及完整名称，可从中选择最合适的代码。
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
从标讯数据库中搜索全国政府采购、招投标项目。
当用户询问招标/采购/标讯相关内容时使用，例如：
  - "广东省最近的建设工程招标"
  - "搜索信息化采购项目"
  - "医疗器械相关的标讯"
  - "环保领域有哪些招标机会"

返回项目 id、标题、发布时间、金额、是否有附件等元数据。
重要提示：请保存返回结果中的 'id' 和 'publish_time' 字段——它们是获取项目详情的唯一标识。
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
                    "source_url": p.get("collect_url") or "",
                })

            # Batch fetch source URLs for results that don't have one yet
            try:
                from api.utils.bid_api_client import BidApiClient
                client = BidApiClient()
                _batch_fill_source_urls(simplified, client)
            except Exception:
                pass

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
获取指定标讯项目的完整详情，并自动触发异步导入知识库。
当用户需要查看或分析某个具体标讯项目时，这是最主要的工具。

一次调用完成以下操作：
  1. 同步获取项目完整详情（正文 HTML、结构化数据、附件列表）
  2. 异步后台导入：正文+所有附件 → 知识库 → 自动解析
  3. 立即返回详情摘要 + kb_import 状态（"parsing" 表示后台正在处理）

导入在后台运行，无需等待。kb_import 字段显示当前导入进度。
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
                    pre_fetched_detail=result,
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
将标讯项目的正文内容和附件文件导入 RAGFlow 知识库，并触发文档解析。
此工具立即返回状态 "parsing"——下载、上传和解析在后台完成。

当用户要求以下操作时使用：
  - "把这个项目导入知识库"
  - "帮我分析这个标书并写投标方案"
  - "基于这个项目生成投标文件"

去重：如果项目已导入过，直接返回现有状态。
需要提供项目的 project_id 和 publish_time。
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
查询标讯项目在知识库中的导入和解析进度。
当项目正在导入时，可反复调用此工具轮询状态直到完成。

返回当前状态："parsing"（解析中）、"done"（完成）或 "fail"（失败）。
状态为 "parsing" 时，doc_progress 字段显示每个文档的详细进度（完成/失败/进行中数量）。
状态为 "done" 时，知识库已就绪，可基于导入内容回答用户问题。

建议每隔几秒轮询一次，直到状态变为 "done"。
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
获取标讯项目的原始来源网址（采集地址）。
这是该标讯最初发布的政府网站链接。
当用户想查看原始公告页面时使用。

需要提供 project_id 和 publish_time（与 bid_get_detail 相同）。
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
面向 AI 的轻量级标讯搜索，一次调用返回丰富的结构化数据（参与方信息、投标日期、地区等）。
适合需要结构化项目概览而不需要单独调用详情接口的场景。

使用场景：
  - 快速获取项目概览及参与方信息
  - 自然语言地区/行业筛选（传入 area_name 如"广东省"）
  - 分类筛选（如 className='招标信息,中标信息'）

每页最多返回 20 条结果。
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
                    "source_url": item.get("collectUrl") or item.get("sbkjBidUrl") or "",
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
从标讯数据库中搜索合同/中标结果数据。
返回包含合同详情的项目：合同日期、项目周期、参与方信息及联系人。
当用户询问合同、中标结果或中标企业时使用。

参数与 bid_search 类似，但返回结果包含 contractStartDate（合同开始日期）、contractEndDate（合同结束日期）、projectCycle（项目周期）等字段。
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
            from api.utils.bid_tool_service import search_contracts_cached

            result = search_contracts_cached(
                keyword=kwargs.get("keyword", ""),
                start_date=kwargs.get("start_date", ""),
                end_date=kwargs.get("end_date", ""),
                contract_end_min=kwargs.get("contract_end_min", ""),
                contract_end_max=kwargs.get("contract_end_max", ""),
                part_a_name=kwargs.get("part_a_name", ""),
                part_b_name=kwargs.get("part_b_name", ""),
                provice_code=kwargs.get("provice_code", ""),
                page=kwargs.get("page", 1),
                page_number=20,
            )

            items = result.get("contracts", [])
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
                    "source_url": "",
                })

            # Batch fetch source URLs for all results
            try:
                from api.utils.bid_api_client import BidApiClient
                client = BidApiClient()
                _batch_fill_source_urls(simplified, client)
            except Exception:
                pass

            output = {
                "total": result.get("total", 0),
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "contracts": simplified,
                "from_cache": result.get("from_cache", False),
                "stale": result.get("stale", False),
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
将自然语言查询重写为结构化的标讯搜索条件。
这是一个 AI 驱动的工具，能从自然语言描述中提取：搜索关键词、同义词、参与方名称、地区代码和行业代码。

示例："军队采购网 病床 北京" → {
  searchPhrase: "病床",
  partyANames: ["军队采购网"],
  areaCode: {proviceCodeList: ["110000"]},
  industryCodes: [{thirdCodeList: ["C277", "Q831"]}]
}

获取重写后的条件后，使用 bid_search 进行正式搜索。
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
根据关键词或短语推理行业分类代码。
返回候选行业代码列表及完整分类路径。

示例："教育" → [
  {thirdCodeList: ["P824"], fullTitle: "教育-教育-高等教育", minTitle: "高等教育"},
  {thirdCodeList: ["P823"], fullTitle: "教育-教育-中等教育", minTitle: "中等教育"},
  ...
]

返回的代码可作为 bid_search 的 industry_code 参数使用。
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
从标讯数据库获取企业综合画像。
返回：基本信息（注册信息、注册资本、法定代表人、经营范围）、
项目洞察（按行业的投标/中标统计）、关系概览（客户/供应商数量）。

当用户询问企业背景、资质能力、招投标历史或商业关系时使用。
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

            from api.utils.bid_tool_service import get_enterprise_profile_cached
            result = get_enterprise_profile_cached(company_name)
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

            # Auto-import to KB (best-effort, async)
            try:
                from api.utils.bid_tool_service import import_enterprise_to_kb
                user_id = ""
                if hasattr(self, '_canvas'):
                    user_id = self._canvas.get_tenant_id() or ""
                import_result = import_enterprise_to_kb(
                    company_name=company_name, kb_id=None, user_id=user_id,
                    pre_fetched_profile=raw_data,
                )
                output["kb_import"] = import_result
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            except Exception as kb_err:
                logging.warning("BidEnterpriseProfile: auto KB import failed: %s", kb_err)
                output["kb_import"] = {"status": "fail", "message": str(kb_err)}
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
搜索拟在建项目信息（处于规划、审批或早期施工阶段的项目）。
返回项目标题、摘要、建设单位和发布时间。

当用户询问即将开工的建设项目、基础设施规划或仍在审批流程中的项目时使用。
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
            from api.utils.bid_tool_service import search_construction_cached

            today = datetime.now().strftime("%Y-%m-%d")
            default_start = f"{today} 00:00:00"
            default_end = f"{today} 23:59:59"

            result = search_construction_cached(
                keyword=kwargs.get("keyword", ""),
                provice_code=kwargs.get("provice_code", ""),
                start_date=kwargs.get("start_date", "") or default_start,
                end_date=kwargs.get("end_date", "") or default_end,
                page_id=kwargs.get("page", 1),
                page_number=20,
            )

            items = result.get("projects", [])
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
                    "source_url": "",
                })

            # Batch fetch source URLs for all results
            try:
                from api.utils.bid_api_client import BidApiClient
                client = BidApiClient()
                _batch_fill_source_urls(simplified, client)
            except Exception:
                pass

            output = {
                "total": result.get("total", 0),
                "shown": len(simplified),
                "page": kwargs.get("page", 1),
                "projects": simplified,
                "from_cache": result.get("from_cache", False),
                "stale": result.get("stale", False),
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


# =============================================================================
# BidGetContractDetail — v2 合同详情（缓存优先）
# =============================================================================

class BidGetContractDetailParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_get_contract_detail",
            "description": """
获取合同/中标项目的完整详情（v2 网关——缓存优先）。
与 bid_get_detail 类似，但通过 v2 接口获取内容正文和结构化数据。
当用户需要查看合同项目的详细正文、结构化的项目信息和附件列表时使用。

一次调用返回：
  - content: 正文 HTML（含附件文件链接）
  - structure: 结构化数据（项目名称、编号、金额、日期、参与方、代理机构等）
  - from_cache: 是否来自本地缓存

数据缓存 30 天，重复查询免费。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Contract/project ID from search results.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project (YYYY-MM-DD or YYYY-MM-DD HH:mm:ss). Used for API authentication on first fetch.",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"project_id": {"name": "Project ID", "type": "line"}}


class BidGetContractDetail(ToolBase, ABC):
    component_name = "BidGetContractDetail"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidGetContractDetail processing"):
            return

        from api.utils.bid_tool_service import get_bid_detail_v2_cached
        import re

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")

            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            result = get_bid_detail_v2_cached(int(project_id), str(publish_time))

            content = result.get("content", {})
            structure = result.get("structure", {})

            # Build readable summary
            content_html = content.get("content", "")
            text_preview = re.sub(r'<[^>]+>', '', content_html)[:2000] if content_html else ""

            # Extract files
            project_files = content.get("projectFiles") or []
            file_list = []
            for f in project_files:
                file_list.append({
                    "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
                    "name": f.get("name") or f.get("fileName", ""),
                    "url": f.get("fileUrl") or f.get("url", ""),
                })

            output = {
                "project_id": project_id,
                "content_preview": text_preview,
                "content_length": len(content_html) if content_html else 0,
                "structure": {
                    "project_name": structure.get("projectName"),
                    "project_number": structure.get("projectNumber"),
                    "budget_money": structure.get("budgetMoney"),
                    "bid_money": structure.get("bidMoney"),
                    "bid_start_date": structure.get("bidStartDate"),
                    "bid_start_address": structure.get("bidStartAddress"),
                    "sign_up_stop_date": structure.get("siginUpStopDate"),
                    "party_a_info": structure.get("partyAInfo"),
                    "party_b_info": structure.get("partyBInfo"),
                    "agency_info": structure.get("agencyInfo"),
                    "bid_company": structure.get("bidCompany"),
                },
                "files": file_list,
                "from_cache": result.get("from_cache", False),
                "stale": result.get("stale", False),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))

            # Auto-import to KB (best-effort, async)
            try:
                from api.utils.bid_tool_service import import_contract_to_kb
                user_id = ""
                if hasattr(self, '_canvas'):
                    user_id = self._canvas.get_tenant_id() or ""
                import_result = import_contract_to_kb(
                    project_id=int(project_id), publish_time=str(publish_time),
                    kb_id=None, user_id=user_id, pre_fetched_detail=result,
                )
                output["kb_import"] = import_result
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            except Exception as kb_err:
                logging.warning("BidGetContractDetail: auto KB import failed: %s", kb_err)
                output["kb_import"] = {"status": "fail", "message": str(kb_err)}
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))

            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidGetContractDetail error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidGetContractDetail error: {e}"

    def thoughts(self) -> str:
        return "Fetching contract detail v2 for #{}...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# BidEnterpriseContacts — 企业联系人
# =============================================================================

class BidEnterpriseContactsParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_enterprise_contacts",
            "description": """
获取企业的联系人信息（v2 API）。
返回企业的联系人列表，包括姓名、职位、电话、邮箱等。

当用户询问某家企业的联系人、谁可以联系时使用。
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up.",
                    "required": True,
                },
                "page_no": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page. Default: 5.",
                    "default": 5,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class BidEnterpriseContacts(ToolBase, ABC):
    component_name = "BidEnterpriseContacts"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidEnterpriseContacts processing"):
            return
        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            from api.utils.bid_tool_service import get_enterprise_contacts

            result = get_enterprise_contacts(
                company_name=company_name,
                page_no=kwargs.get("page_no", 1),
                page_size=kwargs.get("page_size", 5),
            )

            data = result.get("data", {})
            records = data.get("records", []) or []
            contacts_cn = []
            for r in records:
                contacts_cn.append({
                    "姓名": r.get("contactName", ""),
                    "电话": r.get("contactPhone", []),
                    "邮箱": r.get("contactEmail", []),
                    "部门": r.get("department", ""),
                    "职位": r.get("position", ""),
                })

            output = {
                "企业名称": data.get("companyName", company_name),
                "联系人列表": contacts_cn,
                "总数": data.get("total", len(contacts_cn)),
                "当前页": data.get("pageNo", 1),
                "每页条数": data.get("pageSize", len(contacts_cn)),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidEnterpriseContacts error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidEnterpriseContacts error: {e}"

    def thoughts(self) -> str:
        return "Fetching contacts for '{}'...".format(
            self.get_input().get("company_name", "-")
        )


# =============================================================================
# BidEnterpriseCustomers — 企业客户
# =============================================================================

class BidEnterpriseCustomersParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_enterprise_customers",
            "description": """
获取企业的客户项目列表（v2 API）。
返回该企业作为供应商/乙方的项目记录，即该企业服务过哪些客户。

当用户询问某家企业的客户、中标过哪些项目、为谁提供服务时使用。
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up.",
                    "required": True,
                },
                "page_no": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page. Default: 20.",
                    "default": 20,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class BidEnterpriseCustomers(ToolBase, ABC):
    component_name = "BidEnterpriseCustomers"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidEnterpriseCustomers processing"):
            return
        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            from api.utils.bid_tool_service import get_enterprise_customers

            result = get_enterprise_customers(
                company_name=company_name,
                page_no=kwargs.get("page_no", 1),
                page_size=kwargs.get("page_size", 20),
            )

            data = result.get("data", {})
            records = data.get("records", []) or []
            customers_cn = []
            for r in records:
                customers_cn.append({
                    "客户名称": r.get("partnerCompanyName", ""),
                    "项目数量": r.get("projectCount", ""),
                    "总金额(万元)": r.get("totalAmountWan", ""),
                    "首次合作日期": r.get("firstProjectDate", ""),
                    "最近合作日期": r.get("lastProjectDate", ""),
                    "项目名称": r.get("projectTitles", []),
                })

            output = {
                "企业名称": data.get("companyName", company_name),
                "客户项目列表": customers_cn,
                "总数": data.get("total", len(customers_cn)),
                "当前页": data.get("pageNo", 1),
                "每页条数": data.get("pageSize", len(customers_cn)),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidEnterpriseCustomers error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidEnterpriseCustomers error: {e}"

    def thoughts(self) -> str:
        return "Fetching customers for '{}'...".format(
            self.get_input().get("company_name", "-")
        )


# =============================================================================
# BidEnterpriseSuppliers — 企业供应商
# =============================================================================

class BidEnterpriseSuppliersParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_enterprise_suppliers",
            "description": """
获取企业的供应商项目列表（v2 API）。
返回该企业作为采购方/甲方的项目记录，即该企业采购了哪些供应商的服务。

当用户询问某家企业的供应商、采购过哪些服务、与哪些公司合作时使用。
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up.",
                    "required": True,
                },
                "page_no": {
                    "type": "integer",
                    "description": "Page number. Default: 1.",
                    "default": 1,
                    "required": False,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page. Default: 20.",
                    "default": 20,
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class BidEnterpriseSuppliers(ToolBase, ABC):
    component_name = "BidEnterpriseSuppliers"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidEnterpriseSuppliers processing"):
            return
        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            from api.utils.bid_tool_service import get_enterprise_suppliers

            result = get_enterprise_suppliers(
                company_name=company_name,
                page_no=kwargs.get("page_no", 1),
                page_size=kwargs.get("page_size", 20),
            )

            data = result.get("data", {})
            records = data.get("records", []) or []
            suppliers_cn = []
            for r in records:
                suppliers_cn.append({
                    "供应商名称": r.get("partnerCompanyName", ""),
                    "项目数量": r.get("projectCount", ""),
                    "总金额(万元)": r.get("totalAmountWan", ""),
                    "首次合作日期": r.get("firstProjectDate", ""),
                    "最近合作日期": r.get("lastProjectDate", ""),
                    "项目名称": r.get("projectTitles", []),
                })

            output = {
                "企业名称": data.get("companyName", company_name),
                "供应商项目列表": suppliers_cn,
                "总数": data.get("total", len(suppliers_cn)),
                "当前页": data.get("pageNo", 1),
                "每页条数": data.get("pageSize", len(suppliers_cn)),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")
        except Exception as e:
            logging.exception("BidEnterpriseSuppliers error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidEnterpriseSuppliers error: {e}"

    def thoughts(self) -> str:
        return "Fetching suppliers for '{}'...".format(
            self.get_input().get("company_name", "-")
        )


# =============================================================================
# BidConstructionGetDetail — 拟在建项目详情（缓存优先）
# =============================================================================

class BidConstructionGetDetailParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "bid_construction_get_detail",
            "description": """
获取拟在建项目的完整详情（缓存优先）。
返回项目正文内容、建设单位及附件列表。

数据缓存 30 天，重复查询免费。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Construction project ID from search results.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project (YYYY-MM-DD or YYYY-MM-DD HH:mm:ss).",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"project_id": {"name": "Project ID", "type": "line"}}


class BidConstructionGetDetail(ToolBase, ABC):
    component_name = "BidConstructionGetDetail"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("BidConstructionGetDetail processing"):
            return

        from api.utils.bid_tool_service import get_construction_detail_cached
        import re

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")

            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            result = get_construction_detail_cached(int(project_id), str(publish_time))

            content_html = result.get("content", "")
            text_preview = re.sub(r'<[^>]+>', '', content_html)[:2000] if content_html else ""

            project_files = result.get("projectFiles") or []
            file_list = []
            for f in project_files:
                file_list.append({
                    "name": f.get("name") or f.get("fileName", ""),
                    "url": f.get("fileUrl") or f.get("url", ""),
                })

            output = {
                "project_id": project_id,
                "construction_company": result.get("constructionCompany", ""),
                "content_preview": text_preview,
                "content_length": len(content_html) if content_html else 0,
                "files": file_list,
                "from_cache": result.get("from_cache", False),
                "stale": result.get("stale", False),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))

            # Auto-import to KB (best-effort, async)
            try:
                from api.utils.bid_tool_service import import_construction_to_kb
                user_id = ""
                if hasattr(self, '_canvas'):
                    user_id = self._canvas.get_tenant_id() or ""
                import_result = import_construction_to_kb(
                    project_id=int(project_id), publish_time=str(publish_time),
                    user_id=user_id, pre_fetched_detail=result,
                )
                output["kb_import"] = import_result
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            except Exception as kb_err:
                logging.warning("BidConstructionGetDetail: auto KB import failed: %s", kb_err)
                output["kb_import"] = {"status": "fail", "message": str(kb_err)}
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))

            return self.output("formalized_content")

        except Exception as e:
            logging.exception("BidConstructionGetDetail error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"BidConstructionGetDetail error: {e}"

    def thoughts(self) -> str:
        return "Fetching construction detail for #{}...".format(
            self.get_input().get("project_id", "-")
        )


class ConstructionImportToKbParam(ToolParamBase):
    """
    Define the ConstructionImportToKb component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "construction_import_to_kb",
            "description": """
将拟在建项目（construction project）的正文内容和附件文件导入 RAGFlow 知识库，并触发文档解析。
此工具立即返回状态 "parsing"——下载、上传和解析在后台完成。

当用户要求以下操作时使用：
  - "把这个拟在建项目导入知识库"
  - "分析这个建设项目的详情"
  - "基于这个工程项目生成方案"

去重：如果项目已导入过，直接返回现有状态。
需要提供项目的 project_id 和 publish_time。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Construction project ID.",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the construction project.",
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


class ConstructionImportToKb(ToolBase, ABC):
    component_name = "ConstructionImportToKb"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("ConstructionImportToKb processing"):
            return

        from api.utils.bid_tool_service import (
            import_construction_to_kb,
            check_construction_import_status,
        )

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")

            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            user_id = ""
            if hasattr(self, '_canvas'):
                user_id = self._canvas.get_tenant_id() or ""

            # Check existing status first
            status = check_construction_import_status(int(project_id))
            if status.get("status") == "done":
                output = {
                    "project_id": int(project_id),
                    "kb_id": status.get("kb_id"),
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
            result = import_construction_to_kb(
                project_id=int(project_id),
                publish_time=str(publish_time),
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
            logging.exception("ConstructionImportToKb error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"ConstructionImportToKb error: {e}"

    def thoughts(self) -> str:
        return "Importing construction project #{} to knowledge base...".format(
            self.get_input().get("project_id", "-")
        )


class ConstructionCheckImportStatusParam(ToolParamBase):
    """
    Define the ConstructionCheckImportStatus component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "construction_check_import_status",
            "description": """
查询拟在建项目在知识库中的导入和解析进度。
当项目正在导入时，可反复调用此工具轮询状态直到完成。

返回当前状态："parsing"（解析中）、"done"（完成）或 "fail"（失败）。
状态为 "done" 时，知识库已就绪，可基于导入内容回答用户问题。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "Construction project ID.",
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


class ConstructionCheckImportStatus(ToolBase, ABC):
    component_name = "ConstructionCheckImportStatus"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("ConstructionCheckImportStatus processing"):
            return

        from api.utils.bid_tool_service import check_construction_import_status

        try:
            project_id = kwargs.get("project_id")
            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            result = check_construction_import_status(int(project_id))
            output = {
                "project_id": int(project_id),
                "imported": result.get("imported", False),
                "kb_id": result.get("kb_id"),
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "combined_doc_id": result.get("combined_doc_id"),
                "message": result.get("message"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("ConstructionCheckImportStatus error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"ConstructionCheckImportStatus error: {e}"

    def thoughts(self) -> str:
        return "Checking import status for construction project #{}...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# ContractImportToKb — 合同项目导入知识库
# =============================================================================

class ContractImportToKbParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "contract_import_to_kb",
            "description": """
将中标/合同项目的详情正文和附件导入知识库并进行解析。
返回导入状态（parsing/done/fail）和进度信息。
如果项目已经导入过，会返回已有的状态而不重复导入。

当用户需要将合同内容存入知识库以便后续检索或分析时使用。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "合同项目ID",
                    "required": True,
                },
                "publish_time": {
                    "type": "string",
                    "description": "合同发布时间（YYYY-MM-DD）",
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "project_id": {"name": "Project ID", "type": "line"},
            "publish_time": {"name": "Publish Time", "type": "line"},
        }


class ContractImportToKb(ToolBase, ABC):
    component_name = "ContractImportToKb"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("ContractImportToKb processing"):
            return

        from api.utils.bid_tool_service import (
            import_contract_to_kb,
            check_contract_import_status,
        )

        try:
            project_id = kwargs.get("project_id")
            publish_time = kwargs.get("publish_time", "")
            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            user_id = ""
            if hasattr(self, '_canvas'):
                user_id = self._canvas.get_tenant_id() or ""

            # Check existing status first
            status = check_contract_import_status(int(project_id))
            if status.get("status") == "done":
                output = {
                    "project_id": int(project_id),
                    "kb_id": status.get("kb_id"),
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
            result = import_contract_to_kb(
                project_id=int(project_id),
                publish_time=str(publish_time),
                kb_id=None,
                user_id=user_id,
            )

            output = {
                "project_id": int(project_id),
                **result,
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("ContractImportToKb error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"ContractImportToKb error: {e}"

    def thoughts(self) -> str:
        return "Importing contract project #{} to knowledge base...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# ContractCheckImportStatus — 检查合同导入状态
# =============================================================================

class ContractCheckImportStatusParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "contract_check_import_status",
            "description": """
查询合同项目导入知识库的状态和进度。
返回 status（none/parsing/done/fail）、progress（0-1）、message。

当用户想知道合同是否已成功导入知识库或检查导入进度时使用。
            """,
            "parameters": {
                "project_id": {
                    "type": "integer",
                    "description": "合同项目ID",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"project_id": {"name": "Project ID", "type": "line"}}


class ContractCheckImportStatus(ToolBase, ABC):
    component_name = "ContractCheckImportStatus"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("ContractCheckImportStatus processing"):
            return

        from api.utils.bid_tool_service import check_contract_import_status

        try:
            project_id = kwargs.get("project_id")
            if not project_id:
                self.set_output("_ERROR", "project_id is required")
                return "Error: project_id is required"

            result = check_contract_import_status(int(project_id))
            output = {
                "project_id": int(project_id),
                "imported": result.get("imported", False),
                "kb_id": result.get("kb_id"),
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "combined_doc_id": result.get("combined_doc_id"),
                "message": result.get("message"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("ContractCheckImportStatus error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"ContractCheckImportStatus error: {e}"

    def thoughts(self) -> str:
        return "Checking import status for contract project #{}...".format(
            self.get_input().get("project_id", "-")
        )


# =============================================================================
# EnterpriseImportToKb — 企业档案导入知识库
# =============================================================================

class EnterpriseImportToKbParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "enterprise_import_to_kb",
            "description": """
将企业综合档案（工商信息、经营范围、联系方式、中标/投标统计）导入知识库并进行解析。
返回导入状态（parsing/done/fail）和进度信息。
如果企业已经导入过，会返回已有的状态而不重复导入。

当用户需要将企业信息存入知识库以便后续检索或分析时使用。
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "企业名称",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class EnterpriseImportToKb(ToolBase, ABC):
    component_name = "EnterpriseImportToKb"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("EnterpriseImportToKb processing"):
            return

        from api.utils.bid_tool_service import (
            import_enterprise_to_kb,
            check_enterprise_import_status,
        )

        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            user_id = ""
            if hasattr(self, '_canvas'):
                user_id = self._canvas.get_tenant_id() or ""

            # Check existing status first
            status = check_enterprise_import_status(company_name)
            if status.get("status") == "done":
                output = {
                    "company_name": company_name,
                    "kb_id": status.get("kb_id"),
                    "status": "done",
                    "progress": 1.0,
                    "message": "Enterprise was already imported to KB.",
                }
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
                return self.output("formalized_content")

            if status.get("status") == "parsing":
                output = {
                    "company_name": company_name,
                    "kb_id": status.get("kb_id"),
                    "status": "parsing",
                    "progress": status.get("progress", 0),
                    "message": "Enterprise is currently being imported/parsed.",
                }
                self.set_output("json", output)
                self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
                return self.output("formalized_content")

            # Execute import
            result = import_enterprise_to_kb(
                company_name=company_name,
                kb_id=None,
                user_id=user_id,
            )

            output = {
                "company_name": company_name,
                **result,
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("EnterpriseImportToKb error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"EnterpriseImportToKb error: {e}"

    def thoughts(self) -> str:
        return "Importing enterprise profile for '{}' to knowledge base...".format(
            self.get_input().get("company_name", "-")
        )


# =============================================================================
# EnterpriseCheckImportStatus — 检查企业导入状态
# =============================================================================

class EnterpriseCheckImportStatusParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "enterprise_check_import_status",
            "description": """
查询企业档案导入知识库的状态和进度。
返回 status（none/parsing/done/fail）、progress（0-1）、message。

当用户想知道企业是否已成功导入知识库或检查导入进度时使用。
            """,
            "parameters": {
                "company_name": {
                    "type": "string",
                    "description": "企业名称",
                    "required": True,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {"company_name": {"name": "Company Name", "type": "line"}}


class EnterpriseCheckImportStatus(ToolBase, ABC):
    component_name = "EnterpriseCheckImportStatus"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("EnterpriseCheckImportStatus processing"):
            return

        from api.utils.bid_tool_service import check_enterprise_import_status

        try:
            company_name = kwargs.get("company_name", "")
            if not company_name:
                self.set_output("_ERROR", "company_name is required")
                return "Error: company_name is required"

            result = check_enterprise_import_status(company_name)
            output = {
                "company_name": company_name,
                "imported": result.get("imported", False),
                "kb_id": result.get("kb_id"),
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "combined_doc_id": result.get("combined_doc_id"),
                "message": result.get("message"),
            }

            self.set_output("json", output)
            self.set_output("formalized_content", json.dumps(output, ensure_ascii=False, indent=2))
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("EnterpriseCheckImportStatus error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"EnterpriseCheckImportStatus error: {e}"

    def thoughts(self) -> str:
        return "Checking import status for enterprise '{}'...".format(
            self.get_input().get("company_name", "-")
        )
