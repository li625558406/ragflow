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
"""
Bid Tool Service — shared service layer for Agent Canvas components and MCP tools.

Every function builds in cost-aware caching:
  1. Check local DB first (free)
  2. Call external API only if DB miss (paid)
  3. Cache API result to DB automatically (free thereafter)
"""
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from io import BytesIO

from werkzeug.datastructures import FileStorage

from common.constants import TaskStatus
from api.db.db_models import DB
from api.db.services.bid_service import (
    BidProjectService,
    BidProjectDetailService,
    BidProjectStructureService,
    BidProjectFileService,
    BidProjectParseService,
)
from api.db.services.document_service import DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.utils.bid_api_client import BidApiClient
from api.utils.bid_file_utils import download_file, extract_archive

DEFAULT_KB_ID = os.environ.get("BID_DEFAULT_KB_ID", "d23e0644578211f19c3bed5c593fe4c9")


# ---------------------------------------------------------------------------
# Helpers (mirror those in bid_app.py to avoid circular import)
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    import re
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?(div|p|h[1-6]|tr|table|ul|ol|li|br|hr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_file_urls_from_html(html: str) -> list:
    """Extract file download URLs from <a> tags in HTML content.

    Returns a list of {"file_name": str, "file_url": str} dicts.
    """
    import re
    file_ext_pattern = r'\.(pdf|doc|docx|xls|xlsx|zip|rar|7z|tar|gz|ppt|pptx|txt|cad|dwg|jpg|jpeg|png|gif|bmp)(\?|$)'
    results = []
    seen_urls = set()
    for match in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', html, re.IGNORECASE
    ):
        url = match.group(1).strip()
        text = match.group(2).strip() or url.rsplit("/", 1)[-1]
        url_lower = url.lower()
        if re.search(file_ext_pattern, url_lower) or re.search(file_ext_pattern, text.lower()):
            if url not in seen_urls:
                seen_urls.add(url)
                results.append({"file_name": text, "file_url": url})
    return results


def _has_meaningful_text_content(html: str) -> bool:
    """Check if HTML has meaningful text beyond just file-download links."""
    import re
    text_without_links = re.sub(
        r'<a[^>]*>.*?</a>', ' ', html, flags=re.DOTALL | re.IGNORECASE
    )
    stripped = _strip_html(text_without_links)
    meaningful = re.sub(r'[\s\d\W]+', '', stripped)
    return len(meaningful) >= 10


def _json_display(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return ", ".join(
                item.get("name", str(item)) if isinstance(item, dict) else str(item)
                for item in parsed
            )
        return str(parsed)
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def _build_combined_text(detail: dict, structure: dict) -> str:
    parts = []
    if detail and detail.get("content_html"):
        html = detail["content_html"]
        if _has_meaningful_text_content(html):
            text = _strip_html(html)
            if text:
                parts.append(text)
            # Also note any embedded file links within meaningful content
            file_links = _extract_file_urls_from_html(html)
            if file_links:
                parts.append("\n\n---------- 附件列表 ----------\n")
                for f in file_links:
                    parts.append(f"  - {f['file_name']}: {f['file_url']}")
        else:
            file_links = _extract_file_urls_from_html(html)
            if file_links:
                parts.append("本项目主要内容为附件文件：")
                for f in file_links:
                    parts.append(f"  - {f['file_name']}: {f['file_url']}")
            else:
                # Truly empty content — just use whatever strips out
                text = _strip_html(html)
                if text:
                    parts.append(text)
    if structure:
        parts.append('\n\n========== 结构化数据 ==========\n')
        fields = [
            ("项目名称", structure.get("project_name")),
            ("项目编号", _json_display(structure.get("project_numbers"))),
            ("标段编码", _json_display(structure.get("section_codes"))),
            ("预算金额", _json_display(structure.get("budget_money"))),
            ("中标金额", _json_display(structure.get("bid_money"))),
            ("开标日期", structure.get("bid_start_date")),
            ("开标地址", _json_display(structure.get("bid_start_address"))),
            ("报名截止日期", structure.get("sign_up_stop_date")),
            ("甲方信息", _json_display(structure.get("party_a_info"))),
            ("乙方信息", _json_display(structure.get("party_b_info"))),
            ("代理机构", _json_display(structure.get("agency_info"))),
            ("投标企业", _json_display(structure.get("bid_companies"))),
        ]
        for label, value in fields:
            if value:
                parts.append(f'{label}：{value}')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def lookup_bid_code(keyword: str, code_type: str = "area") -> dict:
    """Look up administrative area codes or industry codes by Chinese name.

    Use this BEFORE calling search_bid_projects to convert user's natural-language
    location/industry references into the correct codes.

    Args:
        keyword: Chinese name to search, e.g. "广东", "广州", "建筑", "农业"
        code_type: "area" (administrative division) or "industry" (GB/T 4754-2017)

    Returns:
        {"type": str, "keyword": str, "total": int, "matches": list[dict]}
    """
    keyword_lower = keyword.strip().lower()
    matches = []

    if code_type == "area":
        from api.db.db_models import AreaCode

        rows = AreaCode.select(
            AreaCode.code, AreaCode.name, AreaCode.parent_code, AreaCode.level
        ).dicts()

        for item in rows:
            name = item.get("name", "")
            code = item.get("code", "")
            if keyword_lower in name.lower() or keyword_lower in code:
                matches.append({
                    "code": code,
                    "name": name,
                    "level": item.get("level", 0),
                    "parent_code": item.get("parent_code", ""),
                })

        # Sort: exact name match first, then by level (province first)
        matches.sort(key=lambda x: (
            0 if x["name"] == keyword.strip() else 1,
            x["level"],
        ))
        if len(matches) > 20:
            matches = matches[:20]

    elif code_type == "industry":
        from api.utils.industry_data import get_all_industries_grouped

        tree = get_all_industries_grouped()
        for cat in tree:
            cat_name = cat.get("name", "")
            cat_code = cat.get("code", "")
            if keyword_lower in cat_name.lower() or keyword_lower in cat_code.lower():
                matches.append({
                    "code": cat_code,
                    "name": cat_name,
                    "level": "门类",
                })
            for child in (cat.get("children") or []):
                child_name = child.get("name", "")
                child_code = child.get("code", "")
                if keyword_lower in child_name.lower() or keyword_lower in child_code.lower():
                    matches.append({
                        "code": child_code,
                        "name": child_name,
                        "level": "中类",
                        "category": cat_name,
                    })

    else:
        return {
            "type": code_type,
            "keyword": keyword,
            "total": 0,
            "matches": [],
            "error": f"Unknown code_type '{code_type}'. Must be 'area' or 'industry'.",
        }

    return {
        "type": code_type,
        "keyword": keyword,
        "total": len(matches),
        "matches": matches,
    }


@DB.connection_context()
def search_bid_projects(
    keyword: str = None,
    start_date: str = None,
    end_date: str = None,
    provice_code: str = None,
    city_code: str = None,
    county_code: str = None,
    industry_code: str = None,
    news_type_id: int = None,
    project_money_min: int = None,
    project_money_max: int = None,
    part_a_name: str = None,
    part_b_name: str = None,
    has_file: int = None,
    project_class_id: str = None,
    purchase_type_id: str = None,
    exclude_keyword: str = None,
    include_keyword: str = None,
    source_type: str = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """Search bid projects. DB-first; if <20 results, auto-fallback to external API.

    Returns:
        {"projects": [...], "total": <int>}
    """
    objs, total = BidProjectService.get_list(
        page_number=page,
        items_per_page=page_size,
        keyword=keyword,
        exclude_keyword=exclude_keyword,
        include_keyword=include_keyword,
        project_class_id=project_class_id,
        purchase_type_id=purchase_type_id,
        provice_code=provice_code,
        city_code=city_code,
        county_code=county_code,
        start_date=start_date,
        end_date=end_date,
        project_money_min=project_money_min,
        project_money_max=project_money_max,
        part_a_name=part_a_name,
        part_b_name=part_b_name,
        has_file=has_file,
        file_flag=has_file,
        industry_code=industry_code,
        news_type_id=news_type_id,
        source_type=source_type,
    )

    # If results are sparse, try the external API to backfill
    if total < 20:
        try:
            client = BidApiClient()

            # Build API industry code
            api_industry_code = {"firstCodeList": ["0"], "secondCodeList": [], "thirdCodeList": []}
            if industry_code:
                if len(industry_code) == 1:
                    api_industry_code["firstCodeList"] = [industry_code]
                else:
                    api_industry_code["secondCodeList"] = [industry_code]

            # Build API area code
            api_area_code = {
                "proviceCodeList": [provice_code] if provice_code else ["0"],
                "cityCodeList": [city_code] if city_code else [],
                "countyCodeList": [county_code] if county_code else [],
            }

            from datetime import datetime, timedelta
            default_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            default_end = datetime.now().strftime("%Y-%m-%d")
            api_resp = client.search_project(
                keyword=keyword or "",
                exclude_kw=exclude_keyword or "",
                include_kw=include_keyword or "",
                source_type=source_type or "",
                class_id=str(news_type_id) if news_type_id else "-100",
                project_class_id=project_class_id or "",
                area_code=api_area_code,
                industry_code=api_industry_code,
                start_date=start_date or default_start,
                end_date=end_date or default_end,
                part_a_name=part_a_name or "",
                part_b_name=part_b_name or "",
                project_money_min=project_money_min,
                project_money_max=project_money_max,
                file_flag=has_file if has_file is not None else -1,
                purchase_type_id=purchase_type_id or "",
                page_id=1,
                page_number=50,
            )

            api_data = api_resp.get("data", {})
            items = api_data.get("data", []) or []
            for item in items:
                try:
                    pid = item.get("id")
                    if not pid:
                        continue
                    project_data = {
                        "id": pid,
                        "title": _strip_html(item.get("title", "")),
                        "title_html": item.get("title", ""),
                        "content": item.get("content", ""),
                        "publish_time": item.get("publishTime"),
                        "news_type_id": item.get("newsTypeID"),
                        "project_class_id": str(item.get("projectClassID")) if item.get("projectClassID") else None,
                        "purchase_type_id": str(item.get("purchaseTypeID")) if item.get("purchaseTypeID") else None,
                        "project_money": item.get("projectMoney", ""),
                        "provice_code": item.get("proviceCode", ""),
                        "city_code": item.get("cityCode", ""),
                        "county_code": item.get("countyCode", ""),
                        "industry_codes": json.dumps(item.get("industryCodeList", []), ensure_ascii=False),
                        "part_a_names": json.dumps(item.get("partANameList", []), ensure_ascii=False),
                        "part_b_names": json.dumps(item.get("partBNameList", []), ensure_ascii=False),
                        "has_file": item.get("hasFile", 0),
                        "contract_end_date": item.get("contractEndDate", ""),
                        "se_keywords": api_data.get("seKeyWords", ""),
                        "score": item.get("score"),
                        "source_type": str(item.get("sourceType")) if item.get("sourceType") else None,
                    }
                    BidProjectService.upsert_project(project_data)
                except Exception as e:
                    logging.warning("Failed to cache project %s: %s", item.get("id"), e)

            # Re-query DB after caching API results
            objs, total = BidProjectService.get_list(
                page_number=page,
                items_per_page=page_size,
                keyword=keyword,
                exclude_keyword=exclude_keyword,
                include_keyword=include_keyword,
                project_class_id=project_class_id,
                purchase_type_id=purchase_type_id,
                provice_code=provice_code,
                city_code=city_code,
                county_code=county_code,
                start_date=start_date,
                end_date=end_date,
                project_money_min=project_money_min,
                project_money_max=project_money_max,
                part_a_name=part_a_name,
                part_b_name=part_b_name,
                has_file=has_file,
                file_flag=has_file,
                industry_code=industry_code,
                news_type_id=news_type_id,
                source_type=source_type,
            )
        except Exception as e:
            logging.warning("Tool service: API fallback failed: %s", e)

    return {"projects": objs, "total": total}


@DB.connection_context()
def get_bid_detail(project_id: int, publish_time: str) -> dict:
    """Get full bid project detail. Cache-first: DB then external API.

    Returns:
        {"content_html": str, "structure": dict, "files": list, "cached": bool}
    """
    result = {"content_html": "", "structure": {}, "files": [], "cached": True}

    # -- Detail --
    detail_obj = BidProjectDetailService.get_or_none(project_id=project_id)
    if not detail_obj and publish_time:
        try:
            client = BidApiClient()
            resp = client.get_detail(project_id, publish_time)
            data = resp.get("data", {})
            content_html = data.get("content", "")
            detail_data = {
                "id": project_id,
                "content_html": content_html,
                "news_type_id": data.get("newsTypeID"),
                "project_class_name": data.get("projectClassName", ""),
                "purchase_type_id": data.get("purchaseType", ""),
                "industry_name": data.get("industryName", ""),
                "part_a_name": data.get("partAName", ""),
                "part_b_name": data.get("partBName", ""),
                "agent_name": data.get("agentName", ""),
                "project_money": data.get("projectMoney", ""),
                "provice_code": data.get("proviceCode", ""),
                "city_code": data.get("cityCode", ""),
                "county_code": data.get("countyCode", ""),
                "fetched_at": datetime.now(),
            }
            BidProjectDetailService.upsert_detail(project_id, detail_data)
            detail_obj = BidProjectDetailService.get_or_none(project_id=project_id)
            result["cached"] = False

            # ── Extract files from detail response ──
            # Source A: data.projectFiles (file list embedded in detail response)
            project_files = data.get("projectFiles") or []
            for pf in project_files:
                if not isinstance(pf, dict):
                    continue
                file_url = pf.get("url") or pf.get("fileUrl") or ""
                if not file_url:
                    continue
                try:
                    fid = pf.get("projectFileID") or pf.get("id")
                    if not fid:
                        import hashlib
                        fid = int(hashlib.md5(file_url.encode()).hexdigest()[:15], 16)
                    BidProjectFileService.upsert_file({
                        "project_file_id": fid,
                        "project_id": project_id,
                        "file_name": pf.get("name") or pf.get("fileName") or file_url.rsplit("/", 1)[-1],
                        "file_url": file_url,
                        "file_suffix": pf.get("suffix") or pf.get("fileSuffix") or file_url.rsplit(".", 1)[-1] if "." in file_url else "",
                        "file_size": pf.get("size") or pf.get("fileSize"),
                        "state": pf.get("state", "0"),
                        "publish_time": str(pf.get("publishTime") or publish_time),
                        "create_time": str(pf.get("createTime") or ""),
                        "fetched_at": datetime.now(),
                    })
                except Exception as e:
                    logging.warning("Tool service: cache projectFile failed: %s", e)

            # Source B: file links embedded in content_html <a> tags
            html_file_links = _extract_file_urls_from_html(content_html)
            for hf in html_file_links:
                try:
                    import hashlib
                    fid = int(hashlib.md5(hf["file_url"].encode()).hexdigest()[:15], 16)
                    BidProjectFileService.upsert_file({
                        "project_file_id": fid,
                        "project_id": project_id,
                        "file_name": hf["file_name"],
                        "file_url": hf["file_url"],
                        "file_suffix": hf["file_url"].rsplit(".", 1)[-1] if "." in hf["file_url"] else "",
                        "file_size": None,
                        "state": "0",
                        "publish_time": publish_time,
                        "create_time": "",
                        "fetched_at": datetime.now(),
                    })
                except Exception as e:
                    logging.warning("Tool service: cache html file link failed: %s", e)
        except Exception as e:
            logging.warning("Tool service: fetch detail failed for %d: %s", project_id, e)

    if detail_obj:
        result["content_html"] = detail_obj.to_dict().get("content_html", "")

    # -- Structure --
    structure_obj = BidProjectStructureService.get_or_none(project_id=project_id)
    if not structure_obj and publish_time:
        try:
            client = BidApiClient()
            resp = client.get_structure(project_id, publish_time)
            data = resp.get("data", {})
            struct_data = {
                "id": project_id,
                "project_id": project_id,
                "project_name": data.get("projectName", ""),
                "project_numbers": json.dumps(data.get("projectNumber", []), ensure_ascii=False),
                "section_codes": json.dumps(data.get("projectSectionCode", []), ensure_ascii=False),
                "budget_money": json.dumps(data.get("budgetMoney", []), ensure_ascii=False),
                "bid_money": json.dumps(data.get("bidMoney", []), ensure_ascii=False),
                "bid_start_date": data.get("bidStartDate"),
                "bid_start_address": json.dumps(data.get("bidStartAddress", []), ensure_ascii=False),
                "sign_up_stop_date": data.get("siginUpStopDate"),
                "party_a_info": json.dumps(data.get("partyAInfo", []), ensure_ascii=False),
                "party_b_info": json.dumps(data.get("partyBInfo", []), ensure_ascii=False),
                "agency_info": json.dumps(data.get("agencyInfo", []), ensure_ascii=False),
                "bid_companies": json.dumps(data.get("bidCompany", []), ensure_ascii=False),
                "sbkj_bid_url": data.get("sbkjBidUrl", ""),
                "collect_url": data.get("collectUrl", ""),
                "fetched_at": datetime.now(),
            }
            BidProjectStructureService.upsert_structure(project_id, struct_data)
            structure_obj = BidProjectStructureService.get_or_none(project_id=project_id)
        except Exception as e:
            logging.warning("Tool service: fetch structure failed for %d: %s", project_id, e)

    if structure_obj:
        result["structure"] = structure_obj.to_dict()

    # -- Files --
    files = BidProjectFileService.get_by_project(project_id)
    if (not files or not any(f.get("file_url") for f in files)) and publish_time:
        try:
            client = BidApiClient()
            files_raw = client.get_files(project_id, publish_time)
            for f in files_raw:
                try:
                    BidProjectFileService.upsert_file({
                        "project_file_id": f.get("projectFileID"),
                        "project_id": project_id,
                        "file_name": f.get("name", ""),
                        "file_url": f.get("url", ""),
                        "file_suffix": f.get("suffix", ""),
                        "file_size": f.get("size"),
                        "state": f.get("state", "0"),
                        "publish_time": f.get("publishTime", ""),
                        "create_time": f.get("createTime", ""),
                        "fetched_at": datetime.now(),
                    })
                except Exception as e:
                    logging.warning("Tool service: cache file failed: %s", e)
            files = BidProjectFileService.get_by_project(project_id)
        except Exception as e:
            logging.warning("Tool service: fetch files failed for %d: %s", project_id, e)

    result["files"] = files or []
    return result


@DB.connection_context()
def check_import_status(project_id: int) -> dict:
    """Check whether a project has already been imported to a knowledge base.

    Returns:
        {"imported": bool, "kb_id": str|None, "status": str, "progress": float,
         "combined_doc_id": str|None, "doc_progress": dict|None, "message": str}
    """
    record = BidProjectParseService.get_by_project(project_id)
    if not record:
        return {"imported": False, "kb_id": None, "status": "none", "progress": 0,
                "combined_doc_id": None, "doc_progress": None,
                "message": "Project has not been imported yet."}

    result = {
        "imported": record.get("status") == "done",
        "kb_id": record.get("kb_id"),
        "status": record.get("status"),
        "progress": record.get("progress", 0),
        "combined_doc_id": record.get("combined_doc_id"),
        "doc_progress": None,
        "message": record.get("progress_msg", ""),
    }

    # If parsing, query document-level progress for more detail
    if record.get("status") == "parsing":
        queued_doc_ids_str = record.get("queued_doc_ids")
        if queued_doc_ids_str:
            try:
                doc_ids = json.loads(queued_doc_ids_str)
                done_count = 0
                fail_count = 0
                running_count = 0
                for doc_id in doc_ids:
                    e, doc_model = DocumentService.get_by_id(doc_id)
                    if e and doc_model:
                        r = doc_model.run
                        if r == TaskStatus.DONE:
                            done_count += 1
                        elif r == TaskStatus.FAIL:
                            fail_count += 1
                        else:
                            running_count += 1
                    else:
                        fail_count += 1
                total = len(doc_ids)
                doc_progress = (done_count + fail_count) / max(total, 1)
                result["doc_progress"] = {
                    "total": total,
                    "done": done_count,
                    "fail": fail_count,
                    "running": running_count,
                    "ratio": round(doc_progress, 2),
                }
                result["progress"] = 0.9 + 0.1 * doc_progress
                if running_count == 0:
                    result["message"] = f"Parsing complete: {done_count} done, {fail_count} failed."
                else:
                    result["message"] = f"Parsing: {done_count}/{total} done, {running_count} remaining."
            except (json.JSONDecodeError, TypeError):
                pass

    return result


def _run_import_async(project_id: int, publish_time: str, kb_id: str, user_id: str):
    """Background thread: fetch detail, download/upload files, queue parse, poll until done.

    All DB operations use explicit connection_context since we're in a separate thread.
    """
    parent_path = f"bid_project_{project_id}"
    combined_doc_id = None
    queued_doc_ids = []

    try:
        with DB.connection_context():
            # Phase 1: Fetch/cache detail and structure (cache-first)
            BidProjectParseService.upsert({
                "project_id": project_id,
                "progress": 0.05,
                "progress_msg": "Fetching project detail...",
            })

            detail = get_bid_detail(project_id, publish_time)
            detail_data = detail.get("content_html", "")
            structure_data = detail.get("structure", {})

            # Phase 2: Build combined text and upload
            BidProjectParseService.upsert({
                "project_id": project_id,
                "progress": 0.1,
                "progress_msg": "Uploading combined document...",
            })

            combined_text = _build_combined_text(
                {"content_html": detail_data},
                structure_data,
            )

            kb = KnowledgebaseService.get_or_none(id=kb_id, tenant_id=user_id)
            if not kb:
                BidProjectParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": f"KB {kb_id} not found or access denied.",
                })
                return

            try:
                file_obj = FileStorage(
                    stream=BytesIO(combined_text.encode("utf-8")),
                    filename=f"project_{project_id}_content.txt",
                    content_type="text/plain",
                )
                kb.files = [file_obj]
                FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                if hasattr(file_obj, "id"):
                    combined_doc_id = file_obj.id
                    queued_doc_ids.append(combined_doc_id)
            except Exception as e:
                logging.warning("Tool service: upload combined text failed for %d: %s", project_id, e)

            # Phase 3: Download and upload attachments
            files = detail.get("files", [])
            total = len([f for f in files if f.get("file_url")])
            uploaded = 0

            with tempfile.TemporaryDirectory() as tmpdir:
                for f in files:
                    url = f.get("file_url")
                    if not url:
                        continue
                    try:
                        BidProjectParseService.upsert({
                            "project_id": project_id,
                            "progress": 0.2 + 0.6 * (uploaded / max(total, 1)),
                            "progress_msg": f"Downloading attachments ({uploaded + 1}/{total})...",
                        })

                        local_path = download_file(url, tmpdir)
                        BidProjectFileService.upsert_file({
                            "project_file_id": f["project_file_id"],
                            "local_path": local_path,
                        })

                        to_upload = [local_path]
                        if local_path.lower().endswith((".zip", ".rar")):
                            extracted = extract_archive(local_path, tmpdir)
                            to_upload.extend(extracted)

                        for path in to_upload:
                            try:
                                fname = os.path.basename(path)
                                file_obj2 = FileStorage(
                                    stream=open(path, "rb"),
                                    filename=fname,
                                )
                                FileService.upload_document(kb, [file_obj2], user_id, parent_path=parent_path)
                                doc_id = getattr(file_obj2, "id", None)
                                if doc_id:
                                    queued_doc_ids.append(doc_id)
                                    if path == local_path:
                                        BidProjectFileService.upsert_file({
                                            "project_file_id": f["project_file_id"],
                                            "kb_document_id": doc_id,
                                        })
                            except Exception as e:
                                logging.warning("Tool service: upload attachment %s failed: %s", fname, e)

                        uploaded += 1
                    except Exception as e:
                        logging.warning("Tool service: process attachment %s failed: %s", f.get("file_name"), e)
                        uploaded += 1

            # Phase 4: Trigger parse for all uploaded docs
            BidProjectParseService.upsert({
                "project_id": project_id,
                "combined_doc_id": combined_doc_id,
                "queued_doc_ids": json.dumps(queued_doc_ids),
                "progress": 0.9,
                "progress_msg": "Triggering document parsing...",
            })

            docs, _ = DocumentService.get_by_kb_id(
                kb_id=kb_id, page_number=1, items_per_page=1000,
                orderby="create_time", desc=True,
                keywords="", run_status=[], types=[], suffix=[]
            )
            kb_table_num_map = {}
            for doc in docs:
                if doc.get("run") == "0":
                    try:
                        e, doc_model = DocumentService.get_by_id(doc["id"])
                        if e:
                            doc_dict = doc_model.to_dict()
                            doc_dict["tenant_id"] = user_id
                            DocumentService.run(user_id, doc_dict, kb_table_num_map)
                    except Exception as e:
                        logging.warning("Tool service: queue doc %s failed: %s", doc["id"], e)

            logging.info("Tool service: docs queued for project %d, doc_ids=%s", project_id, queued_doc_ids)

        # Phase 5: Poll until all docs are done (outside the first connection context,
        # but each poll iteration opens its own context)
        if queued_doc_ids:
            total_docs = len(queued_doc_ids)
            while True:
                time.sleep(5)
                try:
                    with DB.connection_context():
                        done_count = 0
                        fail_count = 0
                        for doc_id in queued_doc_ids:
                            e, doc_model = DocumentService.get_by_id(doc_id)
                            if e and doc_model:
                                r = doc_model.run
                                if r == TaskStatus.DONE:
                                    done_count += 1
                                elif r == TaskStatus.FAIL:
                                    fail_count += 1

                        all_finished = (done_count + fail_count) >= total_docs
                        doc_progress = (done_count + fail_count) / total_docs
                        progress = 0.9 + 0.1 * doc_progress

                        if all_finished:
                            BidProjectParseService.upsert({
                                "project_id": project_id,
                                "status": "done",
                                "progress": 1.0,
                                "progress_msg": f"Import complete: {done_count} parsed, {fail_count} failed.",
                            })
                            logging.info("Tool service: async import completed for project %d to kb %s (%d/%d done)",
                                         project_id, kb_id, done_count, total_docs)
                            return

                        BidProjectParseService.upsert({
                            "project_id": project_id,
                            "progress": round(progress, 2),
                            "progress_msg": f"Parsing documents: {done_count + fail_count}/{total_docs} complete ({done_count} done, {fail_count} fail, {total_docs - done_count - fail_count} remaining)...",
                        })
                except Exception as e:
                    logging.warning("Tool service: poll doc status failed for %d: %s", project_id, e)

    except Exception as e:
        logging.exception("Tool service: async import failed for project %d", project_id)
        try:
            with DB.connection_context():
                BidProjectParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": str(e),
                })
        except Exception:
            pass


def import_bid_to_kb(
    project_id: int,
    publish_time: str,
    kb_id: str = None,
    user_id: str = "",
) -> dict:
    """Import a bid project's detail + files into a knowledge base and trigger parsing.

    Returns immediately with "parsing" status. A background thread handles the actual
    download, upload, and triggers document parsing. Use check_import_status() to poll
    until parsing is complete.

    Dedup: if already imported and status is 'done', returns existing info directly.
    Costs: calls external APIs only when data is not already cached in local DB.

    Args:
        project_id: Bid project ID from the search results.
        publish_time: Publish time string from the search results.
        kb_id: Target knowledge base ID. Defaults to BID_DEFAULT_KB_ID env var.
        user_id: Tenant/user ID for KB operations (from canvas tenant or API key).

    Returns:
        {"kb_id": str, "combined_doc_id": str|None, "status": str, "progress": float, "message": str}
    """
    if not kb_id:
        kb_id = DEFAULT_KB_ID

    # --- Dedup check ---
    existing = BidProjectParseService.get_by_project(project_id)
    if existing and existing.get("status") == "done":
        return {
            "kb_id": existing["kb_id"],
            "combined_doc_id": existing.get("combined_doc_id"),
            "status": "done",
            "progress": 1.0,
            "message": "Project already imported to KB.",
        }
    if existing and existing.get("status") == "parsing":
        return {
            "kb_id": existing["kb_id"],
            "combined_doc_id": existing.get("combined_doc_id"),
            "status": "parsing",
            "progress": existing.get("progress", 0),
            "message": existing.get("progress_msg", "Project is currently being imported/parsed."),
        }

    # --- Validate KB ---
    kb = KnowledgebaseService.get_or_none(id=kb_id, tenant_id=user_id)
    if not kb:
        return {
            "kb_id": kb_id,
            "combined_doc_id": None,
            "status": "fail",
            "progress": 0,
            "message": f"Knowledge base '{kb_id}' not found or access denied for user '{user_id}'.",
        }

    # --- Mark as parsing and start background thread ---
    BidProjectParseService.upsert({
        "project_id": project_id,
        "kb_id": kb_id,
        "status": "parsing",
        "progress": 0,
        "progress_msg": "Starting import...",
    })

    thread = threading.Thread(
        target=_run_import_async,
        args=(project_id, publish_time, kb_id, user_id),
        daemon=True,
    )
    thread.start()

    return {
        "kb_id": kb_id,
        "combined_doc_id": None,
        "status": "parsing",
        "progress": 0,
        "message": "Import started. Poll check_import_status for progress.",
    }
