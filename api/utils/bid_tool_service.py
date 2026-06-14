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
from urllib.parse import unquote
import time
from datetime import datetime, timedelta
from io import BytesIO

from werkzeug.datastructures import FileStorage

from common.constants import TaskStatus
from api.db.db_models import DB
from api.db.services.bid_service import (
    BidConstructionParseService,
    BidConstructionProjectService,
    BidContractParseService,
    BidEnterpriseCacheService,
    BidEnterpriseParseService,
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
CONSTRUCTION_KB_ID = "30eb6240679b11f1a8f13fbdf025dd68"
CONTRACT_KB_ID = "c1afe066679c11f1a8f13fbdf025dd68"
ENTERPRISE_KB_ID = "afd3e892679c11f1a8f13fbdf025dd68"


def _lookup_kb(kb_id: str, tenant_id: str):
    """Look up a Knowledgebase row, returning the model instance or None."""
    from api.db.db_models import Knowledgebase
    try:
        kb = Knowledgebase.get_or_none(Knowledgebase.id == kb_id)
        if kb:
            # Attach files placeholder (set before upload_document calls)
            kb.files = None
        return kb
    except Exception:
        return None


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
        text = match.group(2).strip() or unquote(url.rsplit("/", 1)[-1])
        url_lower = url.lower()
        if re.search(file_ext_pattern, url_lower) or re.search(file_ext_pattern, text.lower()):
            if url not in seen_urls:
                seen_urls.add(url)
                results.append({"file_name": text, "file_url": url})
    return results


def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using markdownify, with proper table conversion."""
    if not html:
        return ""
    from markdownify import markdownify as md
    return md(html, heading_style="ATX").strip()


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
            text = _html_to_markdown(html)
            if text:
                parts.append(text)
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
                text = _html_to_markdown(html)
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
                        "collect_url": item.get("collectUrl") or item.get("sbkjBidUrl") or "",
                    }
                    BidProjectService.upsert_project(project_data)
                except Exception as e:
                    logging.warning("Failed to cache project %s: %s", item.get("id"), e)

            # Batch fetch collect_url for cached items that lack it
            try:
                for item in items:
                    pid = item.get("id")
                    pub_time = item.get("publishTime", "")
                    if pid and not item.get("collectUrl") and not item.get("sbkjBidUrl"):
                        try:
                            url = client.get_collect_url(int(pid), str(pub_time))
                            if url:
                                item["collectUrl"] = url
                                BidProjectService.upsert_project({
                                    "id": pid, "collect_url": url,
                                })
                        except Exception:
                            pass
            except Exception as e:
                logging.warning("Tool service: batch collect_url fill failed: %s", e)

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
                    file_url = f.get("url") or f.get("fileUrl") or ""
                    BidProjectFileService.upsert_file({
                        "project_file_id": f.get("projectFileID") or f.get("projectFileId"),
                        "project_id": project_id,
                        "file_name": f.get("name", "") or f.get("fileName", ""),
                        "file_url": file_url,
                        "file_suffix": f.get("suffix", "") or f.get("fileSuffix", ""),
                        "file_size": f.get("size") or f.get("fileSize"),
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

    # 补充: 从 HTML 正文提取 <a> 文件链接（备用）
    content_html = result.get("content_html", "")
    if content_html and (not files or not any(f.get("file_url") for f in files)):
        html_links = _extract_file_urls_from_html(content_html)
        import hashlib
        for hf in html_links:
            try:
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
        files = BidProjectFileService.get_by_project(project_id)

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


def _run_import_async(project_id: int, publish_time: str, kb_id: str, user_id: str, pre_fetched_detail: dict = None):
    """Background thread: fetch detail, download/upload files, queue parse, poll until done.

    All DB operations use explicit connection_context since we're in a separate thread.
    If pre_fetched_detail is provided (from get_bid_detail), skips the redundant fetch.
    """
    parent_path = f"bid_project_{project_id}"
    combined_doc_id = None
    queued_doc_ids = []

    try:
        with DB.connection_context():
            # Phase 1: Use pre-fetched detail or fetch (cache-first)
            BidProjectParseService.upsert({
                "project_id": project_id,
                "progress": 0.05,
                "progress_msg": "Preparing project detail...",
            })

            if pre_fetched_detail:
                detail = pre_fetched_detail
            else:
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

            kb = _lookup_kb(kb_id, user_id)
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
                err, uploaded_files = FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                for doc_dict, _ in uploaded_files:
                    queued_doc_ids.append(doc_dict["id"])
                    if not combined_doc_id:
                        combined_doc_id = doc_dict["id"]
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
                        try:
                            BidProjectFileService.upsert_file({
                                "project_file_id": f["project_file_id"],
                                "local_path": local_path,
                            })
                        except Exception as meta_err:
                            logging.warning("Tool service: upsert_file metadata for %s failed (non-fatal): %s",
                                          f.get("file_name"), meta_err)

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
                                err2, uploaded_files2 = FileService.upload_document(kb, [file_obj2], user_id, parent_path=parent_path)
                                for doc_dict2, _ in uploaded_files2:
                                    doc_id = doc_dict2["id"]
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
        else:
            with DB.connection_context():
                BidProjectParseService.upsert({
                    "project_id": project_id,
                    "status": "done",
                    "progress": 1.0,
                    "progress_msg": "Import complete: no documents to parse.",
                })

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
            pass


def import_bid_to_kb(
    project_id: int,
    publish_time: str,
    kb_id: str = None,
    user_id: str = "",
    pre_fetched_detail: dict = None,
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
        pre_fetched_detail: Pre-fetched detail from get_bid_detail() — avoids duplicate call.

    Returns:
        {"kb_id": str, "combined_doc_id": str|None, "status": str, "progress": float, "message": str}
    """
    if not kb_id:
        kb_id = DEFAULT_KB_ID

    # --- Dedup check ---
    existing = BidProjectParseService.get_by_project(project_id)
    if existing and existing.get("status") == "done":
        # 验证附件是否已成功上传到KB。若仅正文被解析但附件缺失，
        # 重置状态并重新导入（处理首次导入时文件下载失败的情况）。
        queued_ids = json.loads(existing.get("queued_doc_ids") or "[]")
        files = (pre_fetched_detail or {}).get("files") or BidProjectFileService.get_by_project(project_id) or []
        files_with_urls = [f for f in files if f.get("file_url")]
        files_missing_kb = [f for f in files_with_urls if not f.get("kb_document_id")]
        if files_missing_kb and len(queued_ids) <= 1:
            logging.info(
                "Tool service: project %d status=done but %d files missing kb_document_id (queued=%d), re-importing",
                project_id, len(files_missing_kb), len(queued_ids),
            )
            # 继续往下走，触发重新导入
        else:
            return {
                "kb_id": existing["kb_id"],
                "combined_doc_id": existing.get("combined_doc_id"),
                "status": "done",
                "progress": 1.0,
                "message": "Project already imported to KB.",
            }
    if existing and existing.get("status") == "parsing":
        updated_at = existing.get("updated_at")
        if updated_at:
            if isinstance(updated_at, str):
                try:
                    updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                except Exception:
                    updated_at = None
            if updated_at and (datetime.now() - updated_at).total_seconds() > 600:
                logging.info(
                    "Tool service: project %d stuck in parsing for >10min, re-importing",
                    project_id,
                )
            else:
                return {
                    "kb_id": existing["kb_id"],
                    "combined_doc_id": existing.get("combined_doc_id"),
                    "status": "parsing",
                    "progress": existing.get("progress", 0),
                    "message": existing.get("progress_msg", "Project is currently being imported/parsed."),
                }
        else:
            return {
                "kb_id": existing["kb_id"],
                "combined_doc_id": existing.get("combined_doc_id"),
                "status": "parsing",
                "progress": existing.get("progress", 0),
                "message": existing.get("progress_msg", "Project is currently being imported/parsed."),
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
        args=(project_id, publish_time, kb_id, user_id, pre_fetched_detail),
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


# ---------------------------------------------------------------------------
# Construction project KB import — adapted from bid project pipeline
# ---------------------------------------------------------------------------

def _build_construction_combined_text(detail: dict, company: dict, title: str = "", publish_time: str = "") -> str:
    """Build a combined text document from construction project detail for KB indexing."""
    parts = []
    content = detail.get("content", "") or detail.get("content_html", "")
    if content:
        if _has_meaningful_text_content(content):
            text = _html_to_markdown(content)
            if text:
                parts.append(text)
            file_links = _extract_file_urls_from_html(content)
            if file_links:
                parts.append("\n\n---------- 附件列表 ----------\n")
                for f in file_links:
                    parts.append(f"  - {f['file_name']}: {f['file_url']}")
        else:
            file_links = _extract_file_urls_from_html(content)
            if file_links:
                parts.append("本项目主要内容为附件文件：")
                for f in file_links:
                    parts.append(f"  - {f['file_name']}: {f['file_url']}")
            else:
                text = _html_to_markdown(content)
                if text:
                    parts.append(text)

    if company:
        parts.append('\n\n========== 建设单位信息 ==========\n')
        fields = [
            ("项目名称", title),
            ("建设单位", company.get("name")),
            ("联系人", company.get("contactPerson")),
            ("联系电话", company.get("contactPhone")),
            ("地址", company.get("address")),
            ("发布时间", publish_time),
        ]
        for label, value in fields:
            if value:
                parts.append(f'{label}：{value}')
    return '\n'.join(parts)


def _run_construction_import_async(
    project_id: int,
    publish_time: str,
    kb_id: str,
    user_id: str,
    pre_fetched_detail: dict = None,
):
    """Background thread: fetch construction detail, download/upload files, queue parse, poll until done."""
    parent_path = f"construction_project_{project_id}"
    combined_doc_id = None
    queued_doc_ids = []

    try:
        with DB.connection_context():
            # Phase 1: Fetch detail
            BidConstructionParseService.upsert({
                "project_id": project_id,
                "progress": 0.05,
                "progress_msg": "Preparing project detail...",
            })

            if pre_fetched_detail:
                detail = pre_fetched_detail
            else:
                detail = get_construction_detail_cached(project_id, publish_time)

            content = detail.get("content", "")
            company = detail.get("constructionCompany") or {}
            title = detail.get("title", "") or ""
            project_files = detail.get("projectFiles", []) or []

            # Phase 2: Build combined text and upload
            BidConstructionParseService.upsert({
                "project_id": project_id,
                "progress": 0.1,
                "progress_msg": "Uploading combined document...",
            })

            combined_text = _build_construction_combined_text(detail, company, title, publish_time)

            kb = _lookup_kb(kb_id, user_id)
            if not kb:
                BidConstructionParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": f"KB {kb_id} not found or access denied.",
                })
                return

            try:
                file_obj = FileStorage(
                    stream=BytesIO(combined_text.encode("utf-8")),
                    filename=f"construction_{project_id}_content.txt",
                    content_type="text/plain",
                )
                kb.files = [file_obj]
                err, uploaded_files = FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                for doc_dict, _ in uploaded_files:
                    queued_doc_ids.append(doc_dict["id"])
                    if not combined_doc_id:
                        combined_doc_id = doc_dict["id"]
            except Exception as e:
                logging.warning("Construction import: upload combined text failed for %d: %s", project_id, e)

            # Phase 3: Download and upload attachments
            total = len(project_files)
            uploaded = 0

            with tempfile.TemporaryDirectory() as tmpdir:
                for f in project_files:
                    url = f.get("url") or f.get("fileUrl") or f.get("file_url", "")
                    if not url:
                        uploaded += 1
                        continue
                    try:
                        BidConstructionParseService.upsert({
                            "project_id": project_id,
                            "progress": 0.2 + 0.6 * (uploaded / max(total, 1)),
                            "progress_msg": f"Downloading attachments ({uploaded + 1}/{total})...",
                        })

                        local_path = download_file(url, tmpdir)
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
                                err2, uploaded_files2 = FileService.upload_document(kb, [file_obj2], user_id, parent_path=parent_path)
                                for doc_dict2, _ in uploaded_files2:
                                    queued_doc_ids.append(doc_dict2["id"])
                            except Exception as e:
                                logging.warning("Construction import: upload attachment %s failed: %s", fname, e)

                        uploaded += 1
                    except Exception as e:
                        logging.warning("Construction import: process attachment %s failed: %s",
                                       f.get("name", "unknown"), e)
                        uploaded += 1

            # Phase 4: Trigger parse for all uploaded docs
            BidConstructionParseService.upsert({
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
                        logging.warning("Construction import: queue doc %s failed: %s", doc["id"], e)

            logging.info("Construction import: docs queued for project %d, doc_ids=%s", project_id, queued_doc_ids)

        # Phase 5: Poll until all docs are done
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
                            BidConstructionParseService.upsert({
                                "project_id": project_id,
                                "status": "done",
                                "progress": 1.0,
                                "progress_msg": f"Import complete: {done_count} parsed, {fail_count} failed.",
                            })
                            logging.info("Construction import: async import completed for project %d to kb %s (%d/%d done)",
                                         project_id, kb_id, done_count, total_docs)
                            return

                        BidConstructionParseService.upsert({
                            "project_id": project_id,
                            "progress": round(progress, 2),
                            "progress_msg": f"Parsing documents: {done_count + fail_count}/{total_docs} complete...",
                        })
                except Exception as e:
                    logging.warning("Construction import: poll doc status failed for %d: %s", project_id, e)
        else:
            with DB.connection_context():
                BidConstructionParseService.upsert({
                    "project_id": project_id,
                    "status": "done",
                    "progress": 1.0,
                    "progress_msg": "Import complete: no documents to parse.",
                })

    except Exception as e:
        logging.exception("Construction import: async import failed for project %d", project_id)
        try:
            with DB.connection_context():
                BidConstructionParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": str(e),
                })
        except Exception:
            pass


def import_construction_to_kb(
    project_id: int,
    publish_time: str,
    kb_id: str = None,
    user_id: str = "",
    pre_fetched_detail: dict = None,
) -> dict:
    """Import a construction project's detail + files into a knowledge base and trigger parsing.

    Returns immediately with "parsing" status. A background thread handles the actual
    download, upload, and triggers document parsing.

    Dedup: if already imported and status is 'done', returns existing info directly.
    """
    if not kb_id:
        kb_id = CONSTRUCTION_KB_ID

    # --- Dedup check ---
    existing = BidConstructionParseService.get_by_project(project_id)
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

    # --- Mark as parsing and start background thread ---
    BidConstructionParseService.upsert({
        "project_id": project_id,
        "kb_id": kb_id,
        "status": "parsing",
        "progress": 0,
        "progress_msg": "Starting import...",
    })

    thread = threading.Thread(
        target=_run_construction_import_async,
        args=(project_id, publish_time, kb_id, user_id, pre_fetched_detail),
        daemon=True,
    )
    thread.start()

    return {
        "kb_id": kb_id,
        "combined_doc_id": None,
        "status": "parsing",
        "progress": 0,
        "message": "Import started.",
    }


def check_construction_import_status(project_id: int) -> dict:
    """Check whether a construction project has been imported to a knowledge base.

    Returns:
        {"imported": bool, "kb_id": str|None, "status": str, "progress": float,
         "combined_doc_id": str|None, "message": str}
    """
    record = BidConstructionParseService.get_by_project(project_id)
    if not record:
        return {"imported": False, "kb_id": None, "status": "none", "progress": 0,
                "combined_doc_id": None, "message": "Project has not been imported yet."}

    result = {
        "imported": record.get("status") == "done",
        "kb_id": record.get("kb_id"),
        "status": record.get("status"),
        "progress": record.get("progress", 0),
        "combined_doc_id": record.get("combined_doc_id"),
        "message": record.get("progress_msg", ""),
    }
    return result


# ---------------------------------------------------------------------------
# Contract search — cache-first (DB → API → upsert → return)
# ---------------------------------------------------------------------------

def search_contracts_cached(
    keyword: str = "",
    start_date: str = "",
    end_date: str = "",
    contract_end_min: str = "",
    contract_end_max: str = "",
    part_a_name: str = "",
    part_b_name: str = "",
    provice_code: str = "",
    page: int = 1,
    page_number: int = 20,
) -> dict:
    """Search contracts with cache-first strategy.

    1. Query local DB (news_type_id=3, non-expired cache)
    2. If enough results → return directly (free)
    3. Otherwise → call external API → upsert by ID → return (paid)
    4. API failure → degrade to stale DB data
    """
    import re
    from datetime import datetime, timedelta

    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    def _db_to_contract(row: dict) -> dict:
        raw = row.get("raw_json") or {}
        if raw:
            return raw
        return {
            "id": row["id"],
            "title": row.get("title_html") or row.get("title", ""),
            "publishTime": str(row.get("publish_time") or ""),
            "projectMoney": row.get("project_money") or "",
            "hasFile": row.get("has_file") or 0,
            "projectCycle": [],
            "partAInfo": [{"name": n, "contactPhone": []} for n in (row.get("part_a_names") or [])],
            "partBInfo": [{"name": n, "contactPhone": []} for n in (row.get("part_b_names") or [])],
            "contractStartDate": "",
            "contractEndDate": row.get("contract_end_date") or "",
        }

    # Step 1: Query DB
    db_objs, db_total = BidProjectService.get_list(
        page_number=page,
        items_per_page=page_number,
        keyword=keyword,
        provice_code=provice_code,
        start_date=start_date,
        end_date=end_date,
        contract_end_min=contract_end_min,
        contract_end_max=contract_end_max,
        part_a_name=part_a_name,
        part_b_name=part_b_name,
        news_type_id=3,
    )

    now = datetime.now()
    valid_objs = [o for o in db_objs
                  if not o.get("cache_expires_at") or o["cache_expires_at"] > now]
    valid_total = len(valid_objs)

    # Step 2: DB sufficient → return
    if valid_total >= page_number:
        logging.info("Bid tool: contract cache hit, returning %d results", valid_total)
        return {
            "contracts": [_db_to_contract(o) for o in valid_objs[:page_number]],
            "total": db_total,
            "from_cache": True,
        }

    # Step 3: Call API
    logging.info("Bid tool: contract cache insufficient (%d < %d), calling API", valid_total, page_number)
    try:
        client = BidApiClient()
        api_area_code = {
            "proviceCodeList": [provice_code] if provice_code else ["0"],
            "cityCodeList": [],
            "countyCodeList": [],
        }
        resp = client.search_contract(
            keyword=keyword,
            area_code=api_area_code,
            start_date=start_date,
            end_date=end_date,
            contract_end_min=contract_end_min,
            contract_end_max=contract_end_max,
            part_a_name=part_a_name,
            part_b_name=part_b_name,
            page_id=page,
            page_number=page_number,
        )
        data = resp.get("data", {})
        items = data.get("data", []) or []
        api_total = data.get("total", 0)

        # Upsert each item to DB
        for item in items:
            try:
                BidProjectService.upsert_contract(item, keyword=keyword)
            except Exception as e:
                logging.warning("Bid tool: failed to upsert contract %s: %s", item.get("id"), e)

        return {"contracts": items, "total": api_total, "from_cache": False}

    except Exception as e:
        if valid_objs:
            logging.warning("Bid tool: contract API failed, falling back to DB. error=%s", e)
            return {
                "contracts": [_db_to_contract(o) for o in valid_objs[:page_number]],
                "total": len(valid_objs),
                "from_cache": True,
                "stale": True,
            }
        raise


# ---------------------------------------------------------------------------
# v2 Detail — cache-first (DB → v2 API → upsert → return)
# ---------------------------------------------------------------------------

def get_bid_detail_v2_cached(project_id: int, publish_time: str) -> dict:
    """Get project detail via v2 gateway with cache-first strategy.

    Returns both content (HTML body + files) and structure (parsed fields).
    Caches to bid_project_detail, bid_project_structure, bid_project_file.
    TTL: 30 days.
    """
    from datetime import datetime, timedelta

    now = datetime.now()

    # Check cache
    cached_detail = BidProjectDetailService.get_or_none(project_id=project_id)
    cached_structure = BidProjectStructureService.get_or_none(project_id=project_id)

    detail_valid = (cached_detail and cached_detail.cache_expires_at
                    and cached_detail.cache_expires_at > now)
    structure_valid = (cached_structure and cached_structure.cache_expires_at
                       and cached_structure.cache_expires_at > now)

    def _detail_to_api(row) -> dict:
        if not row:
            return {}
        d = row.to_dict() if hasattr(row, 'to_dict') else row
        return {
            "title": d.get("part_a_name", ""),
            "content": d.get("content_html", ""),
            "projectMoney": d.get("project_money", ""),
            "partAName": d.get("part_a_name", ""),
            "partBName": d.get("part_b_name", ""),
            "agentName": d.get("agent_name", ""),
            "industryName": d.get("industry_name", ""),
        }

    def _structure_to_api(row) -> dict:
        if not row:
            return {}
        d = row.to_dict() if hasattr(row, 'to_dict') else row
        return {
            "projectName": d.get("project_name", ""),
            "projectNumber": d.get("project_numbers", []),
            "budgetMoney": d.get("budget_money", []),
            "bidMoney": d.get("bid_money", []),
            "bidStartDate": d.get("bid_start_date"),
            "bidStartAddress": d.get("bid_start_address", []),
            "siginUpStopDate": d.get("sign_up_stop_date"),
            "partyAInfo": d.get("party_a_info", []),
            "partyBInfo": d.get("party_b_info", []),
            "agencyInfo": d.get("agency_info", []),
            "bidCompany": d.get("bid_companies", []),
            "sbkjBidUrl": d.get("sbkj_bid_url", ""),
            "collectUrl": d.get("collect_url", ""),
        }

    if detail_valid and structure_valid:
        logging.info("Bid tool: detail-v2 cache hit for project %s", project_id)
        cached_files = BidProjectFileService.get_by_project(project_id)

        # 缓存的附件缺少 file_url 时，尝试从附件接口补充
        if cached_files and not any(f.get("file_url") for f in cached_files):
            try:
                client = BidApiClient()
                files_api_resp = client.get_files_v2(project_id, publish_time)
                for fa in (files_api_resp.get("data") or []):
                    fid = fa.get("projectFileID") or fa.get("projectFileId")
                    fa_url = fa.get("url") or ""
                    if fid and fa_url:
                        BidProjectFileService.upsert_file({
                            "project_file_id": fid,
                            "project_id": project_id,
                            "file_name": fa.get("name", ""),
                            "file_url": fa_url,
                            "file_suffix": fa.get("suffix", ""),
                            "file_size": fa.get("size"),
                            "state": fa.get("state", "0"),
                            "publish_time": fa.get("publishTime", str(publish_time)),
                            "fetched_at": datetime.now(),
                        })
                cached_files = BidProjectFileService.get_by_project(project_id)
            except Exception:
                logging.warning("Bid tool: failed to enrich file URLs for %s", project_id, exc_info=True)

        return {
            "content": {
                **_detail_to_api(cached_detail),
                "projectFiles": [{"projectFileID": f["project_file_id"], "name": f["file_name"], "fileUrl": f.get("file_url", "")}
                                 for f in cached_files],
            },
            "structure": _structure_to_api(cached_structure),
            "from_cache": True,
        }

    # Call v2 API
    try:
        client = BidApiClient()
        content = client.get_detail_v2(project_id, publish_time)
        structure = client.get_structure_v2(project_id, publish_time)
        # v2详情的projectFiles不含下载URL，额外调附件接口
        files_api_resp = client.get_files_v2(project_id, publish_time)
        files_from_api = files_api_resp.get("data", [])
    except Exception as e:
        # Fallback to stale cache
        if cached_detail:
            cached_files = BidProjectFileService.get_by_project(project_id)
            return {
                "content": {
                    **_detail_to_api(cached_detail),
                    "projectFiles": [{"projectFileID": f["project_file_id"], "name": f["file_name"], "fileUrl": f.get("file_url", "")}
                                     for f in cached_files],
                },
                "structure": _structure_to_api(cached_structure),
                "from_cache": True,
                "stale": True,
            }
        raise

    content_data = content.get("data", {})
    structure_data = structure.get("data", {})
    content_html = content_data.get("content", "")

    # Upsert detail
    try:
        BidProjectDetailService.upsert_detail(project_id, {
            "content_html": content_html,
            "project_class_name": content_data.get("industryName", ""),
            "industry_name": content_data.get("industryName", ""),
            "part_a_name": content_data.get("partAName", ""),
            "part_b_name": content_data.get("partBName", ""),
            "agent_name": content_data.get("agentName", ""),
            "project_money": content_data.get("projectMoney", ""),
        })
    except Exception as e:
        logging.warning("Bid tool: failed to cache detail %s: %s", project_id, e)

    # Upsert structure
    try:
        BidProjectStructureService.upsert_structure(project_id, {
            "project_name": structure_data.get("projectName", ""),
            "project_numbers": structure_data.get("projectNumber", []),
            "budget_money": structure_data.get("budgetMoney", []),
            "bid_money": structure_data.get("bidMoney", []),
            "bid_start_date": structure_data.get("bidStartDate"),
            "bid_start_address": structure_data.get("bidStartAddress", []),
            "sign_up_stop_date": structure_data.get("siginUpStopDate"),
            "party_a_info": structure_data.get("partyAInfo", []),
            "party_b_info": structure_data.get("partyBInfo", []),
            "agency_info": structure_data.get("agencyInfo", []),
            "bid_companies": structure_data.get("bidCompany", []),
            "sbkj_bid_url": structure_data.get("sbkjBidUrl", ""),
            "collect_url": structure_data.get("collectUrl", ""),
        })
    except Exception as e:
        logging.warning("Bid tool: failed to cache structure %s: %s", project_id, e)

    # Upsert files — 三个来源:
    # Source A: 附件接口 getZTBProjectFiles（有下载URL）
    # Source B: 详情接口的 projectFiles（用附件接口URL补充）
    # Source C: HTML 正文中的 <a> 文件下载链接
    import hashlib
    seen_file_ids = set()

    # 先建立附件接口 URL 查找表
    file_url_map = {}
    for fa in (files_from_api or []):
        fa_id = fa.get("projectFileID") or fa.get("projectFileId")
        fa_url = fa.get("url") or ""
        if fa_id and fa_url:
            file_url_map[str(fa_id)] = fa_url

    # Source A: 附件接口数据（有URL）
    for fa in (files_from_api or []):
        try:
            fid = fa.get("projectFileID") or fa.get("projectFileId")
            file_url = fa.get("url") or ""
            if not fid:
                continue
            seen_file_ids.add(str(fid))
            BidProjectFileService.upsert_file({
                "project_file_id": fid,
                "project_id": project_id,
                "file_name": fa.get("name", ""),
                "file_url": file_url,
                "file_suffix": fa.get("suffix", ""),
                "file_size": fa.get("size"),
                "state": fa.get("state", "0"),
                "publish_time": fa.get("publishTime", str(publish_time)),
                "create_time": fa.get("createTime", ""),
                "fetched_at": datetime.now(),
            })
        except Exception as e:
            logging.warning("Bid tool: failed to cache file (api) for %s: %s", project_id, e)

    # Source B: 详情接口 projectFiles（用附件接口URL补充）
    files_raw = content_data.get("projectFiles") or content_data.get("files") or []
    for f in files_raw:
        try:
            fid = f.get("projectFileID") or f.get("projectFileId")
            file_url = f.get("fileUrl") or f.get("url") or ""
            if not file_url and fid:
                file_url = file_url_map.get(str(fid), "")
            if not fid and file_url:
                fid = int(hashlib.md5(file_url.encode()).hexdigest()[:15], 16)
            if not fid:
                continue
            if str(fid) in seen_file_ids:
                continue
            seen_file_ids.add(str(fid))
            BidProjectFileService.upsert_file({
                "project_file_id": fid,
                "project_id": project_id,
                "file_name": f.get("name", "") or f.get("fileName", ""),
                "file_url": file_url,
                "file_suffix": f.get("suffix") or f.get("fileSuffix") or "",
                "file_size": f.get("size") or f.get("fileSize"),
                "state": f.get("state", "0"),
                "publish_time": f.get("publishTime", str(publish_time)),
                "create_time": f.get("createTime", ""),
                "fetched_at": datetime.now(),
            })
        except Exception as e:
            logging.warning("Bid tool: failed to cache file (detail) for %s: %s", project_id, e)

    # Source C: 从 HTML 正文提取 <a> 文件链接
    html_file_links = _extract_file_urls_from_html(content_html)
    for hf in html_file_links:
        try:
            fid = int(hashlib.md5(hf["file_url"].encode()).hexdigest()[:15], 16)
            if str(fid) in seen_file_ids:
                continue
            seen_file_ids.add(str(fid))
            BidProjectFileService.upsert_file({
                "project_file_id": fid,
                "project_id": project_id,
                "file_name": hf["file_name"],
                "file_url": hf["file_url"],
                "file_suffix": hf["file_url"].rsplit(".", 1)[-1] if "." in hf["file_url"] else "",
                "file_size": None,
                "state": "0",
                "publish_time": str(publish_time),
                "create_time": "",
                "fetched_at": datetime.now(),
            })
        except Exception as e:
            logging.warning("Bid tool: failed to cache html file link for %s: %s", project_id, e)

    # 从 DB 构造返回数据，与 cache-hit 路径一致
    saved_detail = BidProjectDetailService.get_or_none(project_id=project_id)
    saved_structure = BidProjectStructureService.get_or_none(project_id=project_id)
    saved_files = BidProjectFileService.get_by_project(project_id)
    return {
        "content": {
            **_detail_to_api(saved_detail),
            "projectFiles": [{"projectFileID": f["project_file_id"], "name": f["file_name"], "fileUrl": f.get("file_url", "")}
                             for f in saved_files],
        },
        "structure": _structure_to_api(saved_structure),
        "from_cache": False,
    }


# ---------------------------------------------------------------------------
# Data normalization — transform v2 API responses to frontend TypeScript shapes
# ---------------------------------------------------------------------------

def _normalize_contacts(raw: dict) -> dict:
    """Normalize v2 contacts API response to match frontend ContactRecord interface.

    API returns: {companyName, pagination: {total, pageNo, pageSize, ...}, records: [...]}
    Frontend expects: {companyName, records, total, pageNo, pageSize}
    """
    pagination = raw.get("pagination", {}) or {}
    records = raw.get("records", []) or []
    normalized_records = []
    for r in records:
        normalized_records.append({
            "contactName": r.get("contactName", ""),
            "contactPhone": r.get("contactPhones", []) or [],
            "contactEmail": r.get("contactEmails", []) or [],
            "department": r.get("department", ""),
            "position": r.get("position", ""),
        })
    return {
        "companyName": raw.get("companyName", ""),
        "records": normalized_records,
        "total": int(pagination.get("total", 0)),
        "pageNo": int(pagination.get("pageNo", 1)),
        "pageSize": int(pagination.get("pageSize", len(records))),
    }


def _normalize_partners(raw: dict) -> dict:
    """Normalize v2 customers/suppliers API response to match frontend PartnerRecord interface.

    API returns: {companyName, pagination: {total, pageNo, pageSize, ...}, records: [...]}
    Frontend expects: {companyName, records, total, pageNo, pageSize}
    Record mapping: relatedProjectName→partnerCompanyName, projectTitles=[relatedProjectName]
    """
    pagination = raw.get("pagination", {}) or {}
    records = raw.get("records", []) or []
    normalized_records = []
    for r in records:
        partner_name = r.get("relatedProjectName", "") or ""
        raw_date = (r.get("projectPublishTime", "") or "").replace("T", " ")
        normalized_records.append({
            "partnerCompanyName": partner_name,
            "projectCount": str(r.get("projectCount", "1") or "1"),
            "totalAmountWan": str(r.get("totalAmountWan", "") or ""),
            "firstProjectDate": raw_date,
            "lastProjectDate": raw_date,
            "projectTitles": [partner_name] if partner_name else [],
        })
    return {
        "companyName": raw.get("companyName", ""),
        "records": normalized_records,
        "total": int(pagination.get("total", 0)),
        "pageNo": int(pagination.get("pageNo", 1)),
        "pageSize": int(pagination.get("pageSize", len(records))),
    }


# ---------------------------------------------------------------------------
# Enterprise cache-first — profile / contacts / customers / suppliers
# ---------------------------------------------------------------------------

def get_enterprise_profile_cached(company_name: str) -> dict:
    """Get enterprise profile with cache-first strategy.

    1. Check local DB cache (free)
    2. Cache miss → call v2 API → upsert → return (paid)
    3. API failure → return stale cache if available

    TTL: 7 days.
    """
    # Check cache
    cached = BidEnterpriseCacheService.get_cached(company_name, "profile")
    if cached:
        logging.info("Bid tool: enterprise profile cache hit for '%s'", company_name)
        return {
            "data": cached["response_json"],
            "from_cache": True,
        }

    # Call API
    logging.info("Bid tool: enterprise profile cache miss for '%s', calling API", company_name)
    try:
        client = BidApiClient()
        result = client.get_company_profile_summary(company_name)
        data = result.get("data", {})

        # Cache the response
        BidEnterpriseCacheService.upsert_cache(
            company_name=company_name,
            cache_type="profile",
            response_data=data,
        )
        return {"data": data, "from_cache": False}
    except Exception as e:
        # Fallback to stale cache
        stale = BidEnterpriseCacheService.get_cached(company_name, "profile", allow_stale=True)
        if stale:
            logging.warning("Bid tool: enterprise profile API failed, using stale cache. error=%s", e)
            return {"data": stale["response_json"], "from_cache": True, "stale": True}
        raise


def get_enterprise_contacts_cached(
    company_name: str, page_no: int = 1, page_size: int = 5,
) -> dict:
    """Get enterprise contacts with cache-first strategy.

    TTL: 3 days.
    """
    cached = BidEnterpriseCacheService.get_cached(company_name, "contacts", page_no, page_size)
    if cached:
        logging.info("Bid tool: enterprise contacts cache hit for '%s' p%d", company_name, page_no)
        return {
            "data": _normalize_contacts(cached["response_json"]),
            "from_cache": True,
        }

    logging.info("Bid tool: enterprise contacts cache miss for '%s', calling API", company_name)
    try:
        client = BidApiClient()
        result = client.get_company_profile_contacts(company_name, page_no, page_size)
        data = result.get("data", {})

        BidEnterpriseCacheService.upsert_cache(
            company_name=company_name,
            cache_type="contacts",
            response_data=data,
            page_no=page_no,
            page_size=page_size,
        )
        return {"data": _normalize_contacts(data), "from_cache": False}
    except Exception as e:
        stale = BidEnterpriseCacheService.get_cached(company_name, "contacts", page_no, page_size, allow_stale=True)
        if stale:
            logging.warning("Bid tool: enterprise contacts API failed, using stale cache. error=%s", e)
            return {"data": _normalize_contacts(stale["response_json"]), "from_cache": True, "stale": True}
        raise


def get_enterprise_customers_cached(
    company_name: str, page_no: int = 1, page_size: int = 20,
) -> dict:
    """Get enterprise customer projects with cache-first strategy.

    TTL: 1 day.
    """
    cached = BidEnterpriseCacheService.get_cached(company_name, "customers", page_no, page_size)
    if cached:
        logging.info("Bid tool: enterprise customers cache hit for '%s' p%d", company_name, page_no)
        return {
            "data": _normalize_partners(cached["response_json"]),
            "from_cache": True,
        }

    logging.info("Bid tool: enterprise customers cache miss for '%s', calling API", company_name)
    try:
        client = BidApiClient()
        result = client.get_company_profile_customers(company_name, page_no, page_size)
        data = result.get("data", {})

        BidEnterpriseCacheService.upsert_cache(
            company_name=company_name,
            cache_type="customers",
            response_data=data,
            page_no=page_no,
            page_size=page_size,
        )
        return {"data": _normalize_partners(data), "from_cache": False}
    except Exception as e:
        stale = BidEnterpriseCacheService.get_cached(company_name, "customers", page_no, page_size, allow_stale=True)
        if stale:
            logging.warning("Bid tool: enterprise customers API failed, using stale cache. error=%s", e)
            return {"data": _normalize_partners(stale["response_json"]), "from_cache": True, "stale": True}
        raise


def get_enterprise_suppliers_cached(
    company_name: str, page_no: int = 1, page_size: int = 20,
) -> dict:
    """Get enterprise supplier projects with cache-first strategy.

    TTL: 1 day.
    """
    cached = BidEnterpriseCacheService.get_cached(company_name, "suppliers", page_no, page_size)
    if cached:
        logging.info("Bid tool: enterprise suppliers cache hit for '%s' p%d", company_name, page_no)
        return {
            "data": _normalize_partners(cached["response_json"]),
            "from_cache": True,
        }

    logging.info("Bid tool: enterprise suppliers cache miss for '%s', calling API", company_name)
    try:
        client = BidApiClient()
        result = client.get_company_profile_suppliers(company_name, page_no, page_size)
        data = result.get("data", {})

        BidEnterpriseCacheService.upsert_cache(
            company_name=company_name,
            cache_type="suppliers",
            response_data=data,
            page_no=page_no,
            page_size=page_size,
        )
        return {"data": _normalize_partners(data), "from_cache": False}
    except Exception as e:
        stale = BidEnterpriseCacheService.get_cached(company_name, "suppliers", page_no, page_size, allow_stale=True)
        if stale:
            logging.warning("Bid tool: enterprise suppliers API failed, using stale cache. error=%s", e)
            return {"data": _normalize_partners(stale["response_json"]), "from_cache": True, "stale": True}
        raise


# ---------------------------------------------------------------------------
# Legacy passthrough functions (keep for backward compatibility)
# ---------------------------------------------------------------------------

def get_enterprise_contacts(company_name: str, page_no: int = 1, page_size: int = 5) -> dict:
    """Get enterprise contacts list (cached)."""
    return get_enterprise_contacts_cached(company_name, page_no, page_size)


def get_enterprise_customers(company_name: str, page_no: int = 1, page_size: int = 20) -> dict:
    """Get enterprise customer project list (cached)."""
    return get_enterprise_customers_cached(company_name, page_no, page_size)


def get_enterprise_suppliers(company_name: str, page_no: int = 1, page_size: int = 20) -> dict:
    """Get enterprise supplier project list (cached)."""
    return get_enterprise_suppliers_cached(company_name, page_no, page_size)


# ---------------------------------------------------------------------------
# 拟在建项目 — cache-first + detail
# ---------------------------------------------------------------------------

@DB.connection_context()
def search_construction_cached(
    keyword: str = "",
    provice_code: str = "",
    city_code: str = "",
    start_date: str = "",
    end_date: str = "",
    page_id: int = 1,
    page_number: int = 20,
) -> dict:
    """Search construction projects with cache-first strategy.

    1. Query local DB
    2. If enough results → return directly (free)
    3. Otherwise → call external API → upsert by ID → return (paid)
    """
    # Step 1: Query DB
    db_objs, db_total = BidConstructionProjectService.get_list(
        page_number=page_id,
        items_per_page=page_number,
        keyword=keyword,
        provice_code=provice_code,
        city_code=city_code,
        start_date=start_date,
        end_date=end_date,
    )

    now = datetime.now()
    valid_objs = [o for o in db_objs
                  if not o.get("cache_expires_at") or o["cache_expires_at"] > now]
    valid_total = len(valid_objs)

    def _db_to_api(row: dict) -> dict:
        raw = row.get("raw_json") or {}
        if raw:
            return raw
        return {
            "id": row["id"],
            "title": row.get("title", ""),
            "summary": row.get("summary", ""),
            "publishTime": str(row.get("publish_time") or ""),
            "proviceCode": row.get("provice_code", ""),
            "cityCode": row.get("city_code", ""),
            "countyCode": row.get("county_code", ""),
            "hasFile": row.get("has_file", 0),
            "score": row.get("score"),
        }

    # Step 2: DB sufficient → return
    if valid_total >= page_number:
        logging.info("Construction: cache hit, returning %d results", valid_total)
        return {
            "projects": [_db_to_api(o) for o in valid_objs[:page_number]],
            "total": db_total,
            "from_cache": True,
        }

    # Step 3: Call API
    logging.info("Construction: cache insufficient (%d < %d), calling API", valid_total, page_number)
    try:
        client = BidApiClient()
        api_area_code = {
            "proviceCodeList": [provice_code] if provice_code else ["0"],
            "cityCodeList": [city_code] if city_code else [],
            "countyCodeList": [],
        }
        resp = client.search_nzj_project(
            keyword=keyword,
            area_code=api_area_code,
            start_date=start_date,
            end_date=end_date,
            page_id=page_id,
            page_number=page_number,
        )
        data = resp.get("data", {})
        items = data.get("data", []) or []
        api_total = data.get("total", 0)

        # Upsert each item to DB (1h TTL for search results)
        one_hour = datetime.fromtimestamp(now.timestamp() + 3600)
        for item in items:
            try:
                pid = item.get("id")
                if not pid:
                    continue
                BidConstructionProjectService.upsert({
                    "id": pid,
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "publish_time": item.get("publishTime"),
                    "provice_code": item.get("proviceCode", ""),
                    "city_code": item.get("cityCode", ""),
                    "county_code": item.get("countyCode", ""),
                    "has_file": item.get("hasFile", 0),
                    "score": item.get("score"),
                    "raw_json": item,
                    "se_keywords": keyword,
                    "fetched_at": now,
                    "cache_expires_at": one_hour,
                })
            except Exception as e:
                logging.warning("Construction: failed to upsert %s: %s", item.get("id"), e)

        return {"projects": items, "total": api_total, "from_cache": False}

    except Exception as e:
        if valid_objs:
            logging.warning("Construction: API failed, falling back to DB. error=%s", e)
            return {
                "projects": [_db_to_api(o) for o in valid_objs[:page_number]],
                "total": len(valid_objs),
                "from_cache": True,
                "stale": True,
            }
        raise


@DB.connection_context()
def get_construction_detail_cached(project_id: int, publish_time: str) -> dict:
    """Get construction project detail with cache-first strategy.

    TTL: 30 days.
    """
    now = datetime.now()

    # Check cache
    cached = BidConstructionProjectService.get_by_id(project_id)
    if cached and cached.get("detail_json") and cached.get("cache_expires_at"):
        cache_expires = cached["cache_expires_at"]
        if isinstance(cache_expires, str):
            cache_expires = datetime.fromisoformat(cache_expires)
        if cache_expires and cache_expires > now:
            logging.info("Construction: detail cache hit for %s", project_id)
            return {**cached["detail_json"], "from_cache": True}

    # Call API
    logging.info("Construction: detail cache miss for %s, calling API", project_id)
    try:
        client = BidApiClient()
        result = client.get_nzj_project_detail(project_id, publish_time)

        # Upsert detail to DB (30d TTL)
        thirty_days = now + timedelta(days=30)
        BidConstructionProjectService.upsert({
            "id": project_id,
            "detail_json": result,
            "publish_time": publish_time,
            "fetched_at": now,
            "cache_expires_at": thirty_days,
        })
        return {**result, "from_cache": False}
    except Exception as e:
        # Fallback to stale cache
        if cached and cached.get("detail_json"):
            logging.warning("Construction: detail API failed, using stale cache. error=%s", e)
            return {**cached["detail_json"], "from_cache": True, "stale": True}
        raise


# ---------------------------------------------------------------------------
# Contract KB import pipeline
# ---------------------------------------------------------------------------

def _run_contract_import_async(
    project_id: int,
    publish_time: str,
    kb_id: str,
    user_id: str,
    pre_fetched_detail: dict = None,
):
    """Background thread: fetch contract detail, download/upload files, queue parse, poll until done."""
    parent_path = f"contract_{project_id}"
    combined_doc_id = None
    queued_doc_ids = []

    try:
        with DB.connection_context():
            # Phase 1: Fetch detail
            BidContractParseService.upsert({
                "project_id": project_id,
                "progress": 0.05,
                "progress_msg": "Preparing contract detail...",
            })

            if pre_fetched_detail:
                detail = pre_fetched_detail
            else:
                detail = get_bid_detail_v2_cached(project_id, publish_time)

            content_html = (detail.get("content") or {}).get("content", "")
            structure = detail.get("structure", {})
            project_files = (detail.get("content") or {}).get("projectFiles", []) or []

            # Phase 2: Build combined text and upload
            BidContractParseService.upsert({
                "project_id": project_id,
                "progress": 0.1,
                "progress_msg": "Uploading combined document...",
            })

            combined_text = _build_combined_text(
                {"content_html": content_html},
                structure,
            )

            kb = _lookup_kb(kb_id, user_id)
            if not kb:
                BidContractParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": f"KB {kb_id} not found or access denied.",
                })
                return

            try:
                file_obj = FileStorage(
                    stream=BytesIO(combined_text.encode("utf-8")),
                    filename=f"contract_{project_id}_content.txt",
                    content_type="text/plain",
                )
                err, uploaded_files = FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                for doc_dict, _ in uploaded_files:
                    queued_doc_ids.append(doc_dict["id"])
                    if not combined_doc_id:
                        combined_doc_id = doc_dict["id"]
            except Exception as e:
                logging.warning("Contract import: upload combined text failed for %d: %s", project_id, e)

            # Phase 3: Download and upload attachments
            total = len(project_files)
            uploaded = 0

            with tempfile.TemporaryDirectory() as tmpdir:
                for f in project_files:
                    url = (f.get("url") or f.get("fileUrl") or f.get("file_url", ""))
                    if not url:
                        uploaded += 1
                        continue
                    try:
                        BidContractParseService.upsert({
                            "project_id": project_id,
                            "progress": 0.2 + 0.6 * (uploaded / max(total, 1)),
                            "progress_msg": f"Downloading attachments ({uploaded + 1}/{total})...",
                        })

                        local_path = download_file(url, tmpdir)
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
                                err2, uploaded_files2 = FileService.upload_document(kb, [file_obj2], user_id, parent_path=parent_path)
                                for doc_dict2, _ in uploaded_files2:
                                    queued_doc_ids.append(doc_dict2["id"])
                            except Exception as e:
                                logging.warning("Contract import: upload attachment %s failed: %s", fname, e)

                        uploaded += 1
                    except Exception as e:
                        logging.warning("Contract import: process attachment %s failed: %s",
                                       f.get("name", "unknown"), e)
                        uploaded += 1

            # Phase 4: Trigger parse for all uploaded docs
            BidContractParseService.upsert({
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
                        logging.warning("Contract import: queue doc %s failed: %s", doc["id"], e)

            logging.info("Contract import: docs queued for project %d, doc_ids=%s", project_id, queued_doc_ids)

        # Phase 5: Poll until all docs are done
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
                            BidContractParseService.upsert({
                                "project_id": project_id,
                                "status": "done",
                                "progress": 1.0,
                                "progress_msg": f"Import complete: {done_count} parsed, {fail_count} failed.",
                            })
                            logging.info("Contract import: async import completed for project %d to kb %s (%d/%d done)",
                                         project_id, kb_id, done_count, total_docs)
                            return

                        BidContractParseService.upsert({
                            "project_id": project_id,
                            "progress": round(progress, 2),
                            "progress_msg": f"Parsing documents: {done_count + fail_count}/{total_docs} complete...",
                        })
                except Exception as e:
                    logging.warning("Contract import: poll doc status failed for %d: %s", project_id, e)
        else:
            with DB.connection_context():
                BidContractParseService.upsert({
                    "project_id": project_id,
                    "status": "done",
                    "progress": 1.0,
                    "progress_msg": "Import complete: no documents to parse.",
                })

    except Exception as e:
        logging.exception("Contract import: async import failed for project %d", project_id)
        try:
            with DB.connection_context():
                BidContractParseService.upsert({
                    "project_id": project_id,
                    "status": "fail",
                    "progress_msg": str(e),
                })
        except Exception:
            pass


def import_contract_to_kb(
    project_id: int,
    publish_time: str,
    kb_id: str = None,
    user_id: str = "",
    pre_fetched_detail: dict = None,
) -> dict:
    """Import a contract project's detail + files into a knowledge base and trigger parsing.

    Returns immediately with "parsing" status. A background thread handles the actual
    download, upload, and triggers document parsing.

    Dedup: if already imported and status is 'done', returns existing info directly.
    """
    if not kb_id:
        kb_id = CONTRACT_KB_ID

    # --- Dedup check ---
    existing = BidContractParseService.get_by_project(project_id)
    if existing and existing.get("status") == "done":
        queued_ids = json.loads(existing.get("queued_doc_ids") or "[]")
        files = (pre_fetched_detail or {}).get("files") or BidProjectFileService.get_by_project(project_id) or []
        files_with_urls = [f for f in files if f.get("file_url")]
        files_missing_kb = [f for f in files_with_urls if not f.get("kb_document_id")]
        if files_missing_kb and len(queued_ids) <= 1:
            logging.info(
                "Tool service: contract %d status=done but %d files missing kb_document_id (queued=%d), re-importing",
                project_id, len(files_missing_kb), len(queued_ids),
            )
        else:
            return {
                "kb_id": existing["kb_id"],
                "combined_doc_id": existing.get("combined_doc_id"),
                "status": "done",
                "progress": 1.0,
                "message": "Project already imported to KB.",
            }
    if existing and existing.get("status") == "parsing":
        updated_at = existing.get("updated_at")
        if updated_at:
            if isinstance(updated_at, str):
                try:
                    updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                except Exception:
                    updated_at = None
            if updated_at and (datetime.now() - updated_at).total_seconds() > 600:
                logging.info(
                    "Tool service: contract %d stuck in parsing for >10min, re-importing",
                    project_id,
                )
            else:
                return {
                    "kb_id": existing["kb_id"],
                    "combined_doc_id": existing.get("combined_doc_id"),
                    "status": "parsing",
                    "progress": existing.get("progress", 0),
                    "message": existing.get("progress_msg", "Project is currently being imported/parsed."),
                }
        else:
            return {
                "kb_id": existing["kb_id"],
                "combined_doc_id": existing.get("combined_doc_id"),
                "status": "parsing",
                "progress": existing.get("progress", 0),
                "message": existing.get("progress_msg", "Project is currently being imported/parsed."),
            }

    # --- Mark as parsing and start background thread ---
    BidContractParseService.upsert({
        "project_id": project_id,
        "kb_id": kb_id,
        "status": "parsing",
        "progress": 0,
        "progress_msg": "Starting import...",
    })

    thread = threading.Thread(
        target=_run_contract_import_async,
        args=(project_id, publish_time, kb_id, user_id, pre_fetched_detail),
        daemon=True,
    )
    thread.start()

    return {
        "kb_id": kb_id,
        "combined_doc_id": None,
        "status": "parsing",
        "progress": 0,
        "message": "Import started.",
    }


def check_contract_import_status(project_id: int) -> dict:
    """Check whether a contract project has been imported to a knowledge base."""
    record = BidContractParseService.get_by_project(project_id)
    if not record:
        return {"imported": False, "kb_id": None, "status": "none", "progress": 0,
                "combined_doc_id": None, "message": "Project has not been imported yet."}

    result = {
        "imported": record.get("status") == "done",
        "kb_id": record.get("kb_id"),
        "status": record.get("status"),
        "progress": record.get("progress", 0),
        "combined_doc_id": record.get("combined_doc_id"),
        "message": record.get("progress_msg", ""),
    }
    return result


# ---------------------------------------------------------------------------
# Enterprise KB import pipeline
# ---------------------------------------------------------------------------

def _build_enterprise_combined_text(profile: dict) -> str:
    """Build a combined text document from enterprise profile for KB indexing."""
    lines = []
    lines.append(f"# 企业档案: {profile.get('companyName', '')}")
    lines.append("")

    base = profile.get("baseInfo", {})
    reg = base.get("registrationInfo", {})
    op = base.get("operationInfo", {})
    contact = base.get("contactInfo", {})
    ep = base.get("enterpriseProfile", {})

    lines.append("## 基本信息")
    lines.append(f"- 企业类型: {ep.get('companyTypeName', '')}")
    lines.append(f"- 所属行业: {ep.get('industryName', '')}")
    lines.append(f"- 法定代表人: {ep.get('legalRepresentative', '')}")
    lines.append(f"- 成立日期: {ep.get('establishmentDate', '')}")
    lines.append(f"- 经营状态: {ep.get('operatingStatus', {}).get('statusName', '')}")
    lines.append("")

    lines.append("## 工商信息")
    lines.append(f"- 统一社会信用代码: {reg.get('creditCode', '')}")
    rc = reg.get("registeredCapital") or {}
    lines.append(f"- 注册资本: {rc.get('amount', '')} {rc.get('unitName', '')}")
    lines.append(f"- 经营范围: {op.get('businessScope', '')}")
    lines.append("")

    lines.append("## 联系方式")
    lines.append(f"- 注册地址: {contact.get('registeredAddress', '')}")
    lines.append(f"- 网站: {contact.get('website', '')}")
    phones = contact.get("contactPhones", [])
    if phones:
        lines.append(f"- 电话: {', '.join(str(p) for p in phones)}")
    emails = contact.get("contactEmails", [])
    if emails:
        lines.append(f"- 邮箱: {', '.join(str(e) for e in emails)}")
    lines.append("")

    insights = profile.get("projectInsights", {})
    bid_stats = insights.get("bidStatistics", [])
    if bid_stats:
        lines.append("## 投标统计")
        for s in bid_stats:
            lines.append(f"- {s.get('industryName', '')}: 项目数 {s.get('projectCount', 0)}, "
                         f"份额 {s.get('projectShare', '')}, 预算 {s.get('budgetAmountWan', '')}万")
        lines.append("")

    win_stats = insights.get("winStatistics", [])
    if win_stats:
        lines.append("## 中标统计")
        for s in win_stats:
            lines.append(f"- {s.get('industryName', '')}: 项目数 {s.get('projectCount', 0)}, "
                         f"份额 {s.get('projectShare', '')}, 预算 {s.get('budgetAmountWan', '')}万")
        lines.append("")

    rs = profile.get("relationshipSummary", {})
    lines.append("## 关系概览")
    lines.append(f"- 联系人: {rs.get('contactPersonCount', 0)} 人")
    lines.append(f"- 客户项目: {rs.get('customerProjectCount', 0)} 个")
    lines.append(f"- 供应商项目: {rs.get('supplierProjectCount', 0)} 个")

    return "\n".join(lines)


def _run_enterprise_import_async(
    company_name: str,
    kb_id: str,
    user_id: str,
    pre_fetched_profile: dict = None,
):
    """Background thread: build enterprise text, upload, queue parse, poll until done."""
    parent_path = f"enterprise_{company_name}"
    combined_doc_id = None
    queued_doc_ids = []

    try:
        with DB.connection_context():
            # Phase 1: Fetch profile
            BidEnterpriseParseService.upsert({
                "company_name": company_name,
                "progress": 0.05,
                "progress_msg": "Preparing enterprise profile...",
            })

            if pre_fetched_profile:
                profile = pre_fetched_profile
            else:
                cached = get_enterprise_profile_cached(company_name)
                profile = cached.get("data", cached) if isinstance(cached, dict) else cached

            # Phase 2: Build combined text and upload
            BidEnterpriseParseService.upsert({
                "company_name": company_name,
                "progress": 0.1,
                "progress_msg": "Uploading combined document...",
            })

            combined_text = _build_enterprise_combined_text(profile)

            kb = _lookup_kb(kb_id, user_id)
            if not kb:
                BidEnterpriseParseService.upsert({
                    "company_name": company_name,
                    "status": "fail",
                    "progress_msg": f"KB {kb_id} not found or access denied.",
                })
                return

            try:
                safe_name = company_name.replace("/", "_").replace("\\", "_")[:100]
                file_obj = FileStorage(
                    stream=BytesIO(combined_text.encode("utf-8")),
                    filename=f"enterprise_{safe_name}_profile.txt",
                    content_type="text/plain",
                )
                err, uploaded_files = FileService.upload_document(kb, [file_obj], user_id, parent_path=parent_path)
                for doc_dict, _ in uploaded_files:
                    queued_doc_ids.append(doc_dict["id"])
                    if not combined_doc_id:
                        combined_doc_id = doc_dict["id"]
            except Exception as e:
                logging.warning("Enterprise import: upload combined text failed for '%s': %s", company_name, e)

            # Phase 3: Trigger parse
            BidEnterpriseParseService.upsert({
                "company_name": company_name,
                "combined_doc_id": combined_doc_id,
                "queued_doc_ids": json.dumps(queued_doc_ids),
                "progress": 0.5,
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
                        logging.warning("Enterprise import: queue doc %s failed: %s", doc["id"], e)

            logging.info("Enterprise import: docs queued for '%s', doc_ids=%s", company_name, queued_doc_ids)

        # Phase 4: Poll until all docs are done
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
                        progress = 0.5 + 0.5 * doc_progress

                        if all_finished:
                            BidEnterpriseParseService.upsert({
                                "company_name": company_name,
                                "status": "done",
                                "progress": 1.0,
                                "progress_msg": f"Import complete: {done_count} parsed, {fail_count} failed.",
                            })
                            logging.info("Enterprise import: async import completed for '%s' to kb %s (%d/%d done)",
                                         company_name, kb_id, done_count, total_docs)
                            return

                        BidEnterpriseParseService.upsert({
                            "company_name": company_name,
                            "progress": round(progress, 2),
                            "progress_msg": f"Parsing documents: {done_count + fail_count}/{total_docs} complete...",
                        })
                except Exception as e:
                    logging.warning("Enterprise import: poll doc status failed for '%s': %s", company_name, e)
        else:
            with DB.connection_context():
                BidEnterpriseParseService.upsert({
                    "company_name": company_name,
                    "status": "done",
                    "progress": 1.0,
                    "progress_msg": "Import complete: no documents to parse.",
                })

    except Exception as e:
        logging.exception("Enterprise import: async import failed for '%s'", company_name)
        try:
            with DB.connection_context():
                BidEnterpriseParseService.upsert({
                    "company_name": company_name,
                    "status": "fail",
                    "progress_msg": str(e),
                })
        except Exception:
            pass


def import_enterprise_to_kb(
    company_name: str,
    kb_id: str = None,
    user_id: str = "",
    pre_fetched_profile: dict = None,
) -> dict:
    """Import an enterprise profile into a knowledge base and trigger parsing.

    Returns immediately with "parsing" status. A background thread handles the actual
    upload and triggers document parsing.

    Dedup: if already imported and status is 'done', returns existing info directly.
    """
    if not kb_id:
        kb_id = ENTERPRISE_KB_ID

    # --- Dedup check ---
    existing = BidEnterpriseParseService.get_by_company(company_name)
    if existing and existing.get("status") == "done":
        return {
            "kb_id": existing["kb_id"],
            "combined_doc_id": existing.get("combined_doc_id"),
            "status": "done",
            "progress": 1.0,
            "message": "Enterprise already imported to KB.",
        }
    if existing and existing.get("status") == "parsing":
        return {
            "kb_id": existing["kb_id"],
            "combined_doc_id": existing.get("combined_doc_id"),
            "status": "parsing",
            "progress": existing.get("progress", 0),
            "message": existing.get("progress_msg", "Enterprise is currently being imported/parsed."),
        }

    # --- Mark as parsing and start background thread ---
    BidEnterpriseParseService.upsert({
        "company_name": company_name,
        "kb_id": kb_id,
        "status": "parsing",
        "progress": 0,
        "progress_msg": "Starting import...",
    })

    thread = threading.Thread(
        target=_run_enterprise_import_async,
        args=(company_name, kb_id, user_id, pre_fetched_profile),
        daemon=True,
    )
    thread.start()

    return {
        "kb_id": kb_id,
        "combined_doc_id": None,
        "status": "parsing",
        "progress": 0,
        "message": "Import started.",
    }


def check_enterprise_import_status(company_name: str) -> dict:
    """Check whether an enterprise has been imported to a knowledge base."""
    record = BidEnterpriseParseService.get_by_company(company_name)
    if not record:
        return {"imported": False, "kb_id": None, "status": "none", "progress": 0,
                "combined_doc_id": None, "message": "Enterprise has not been imported yet."}

    result = {
        "imported": record.get("status") == "done",
        "kb_id": record.get("kb_id"),
        "status": record.get("status"),
        "progress": record.get("progress", 0),
        "combined_doc_id": record.get("combined_doc_id"),
        "message": record.get("progress_msg", ""),
    }
    return result
