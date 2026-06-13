#!/usr/bin/env python3
"""
RAGFlow MCP Server — exposes 5 tools via MCP JSON-RPC over stdio.
Integrates with Hermes / Claude Code / OpenClaw.

Tools:
  1. ask_agent               — Ask a RAGFlow agent/canvas a question
  2. search_bid_projects     — Search Chinese procurement bid projects
  3. get_bid_detail          — Get full detail (content + structure + files)
  4. import_bid_to_kb        — Import project into knowledge base + trigger parse
  5. check_bid_import_status — Check KB import/parse progress

Usage:
  python3 rag/svr/mcp_server.py

Environment variables:
  RAGFLOW_BASE_URL  — RAGFlow API base (default: http://127.0.0.1:9380/api/v1)
"""

import json
import sys
import os
import re
import ssl
import urllib.request
import urllib.error

RAGFLOW_BASE = os.environ.get("RAGFLOW_BASE_URL", "http://127.0.0.1:9380/api/v1")

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _bearer(api_key: str) -> str:
    """Normalize API key to Bearer header value."""
    key = api_key.strip()
    return key if key.startswith("Bearer ") else f"Bearer {key}"


def _http_get(path: str, api_key: str, params: dict | None = None, timeout: int = 30) -> dict:
    url = RAGFLOW_BASE + path
    if params:
        qs_parts = []
        for k, v in params.items():
            if v is not None and v != "":
                qs_parts.append(f"{urllib.request.quote(k)}={urllib.request.quote(str(v))}")
        if qs_parts:
            url += "?" + "&".join(qs_parts)

    req = urllib.request.Request(url)
    req.add_header("Authorization", _bearer(api_key))
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"code": e.code, "message": str(e), "data": body}
    except Exception as e:
        return {"code": -1, "message": str(e), "data": None}


def _http_post(path: str, api_key: str, body: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    url = RAGFLOW_BASE + path

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", _bearer(api_key))
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"code": e.code, "message": str(e), "data": body}
    except Exception as e:
        return {"code": -1, "message": str(e), "data": None}


def _http_post_sse(path: str, api_key: str, body: dict | None = None, timeout: int = 120) -> dict:
    """POST and collect SSE (Server-Sent Events) response, returning aggregated result."""
    data = json.dumps({**(body or {}), "stream": True}, ensure_ascii=False).encode("utf-8")
    url = RAGFLOW_BASE + path

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", _bearer(api_key))
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")

    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"code": e.code, "message": str(e), "data": body}
    except Exception as e:
        return {"code": -1, "message": str(e), "data": None}

    # Parse SSE: extract answer from final Message or workflow_finished event
    final_answer = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
            event_type = evt.get("event", "")
            event_data = evt.get("data", {})

            # workflow_finished has the final aggregated output
            if event_type == "workflow_finished":
                content = event_data.get("outputs", {}).get("content", "")
                if content:
                    final_answer = content
                    break

            # Message node_finished also has the answer
            if event_type == "node_finished":
                if event_data.get("component_type") == "Message":
                    content = event_data.get("outputs", {}).get("content", "")
                    if content:
                        final_answer = content
        except json.JSONDecodeError:
            pass

    if final_answer:
        # Strip DeepSeek/R1 <think> blocks from the answer
        cleaned = re.sub(r"<think>.*?</think>", "", final_answer, flags=re.DOTALL).strip()
        if not cleaned:
            cleaned = final_answer.strip()
        return {"code": 0, "message": "success", "data": {"answer": cleaned}}

    return {"code": 0, "message": "success (no content in events)", "data": {"raw_events": len(raw)}}


# ═══════════════════════════════════════════════════════════════
# Tool implementations
# ═══════════════════════════════════════════════════════════════


def tool_search_contracts(api_key: str, keyword: str = "",
                          start_date: str = "", end_date: str = "",
                          contract_end_min: str = "", contract_end_max: str = "",
                          part_a_name: str = "", part_b_name: str = "",
                          provice_code: str = "",
                          page: str = "1", items_per_page: str = "20") -> str:
    """Search contracts from the RAGFlow bidding database (cache-first).

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        keyword: Search keyword
        start_date: Publish date lower bound (YYYY-MM-DD)
        end_date: Publish date upper bound (YYYY-MM-DD)
        contract_end_min: Contract end date minimum (YYYY-MM-DD)
        contract_end_max: Contract end date maximum (YYYY-MM-DD)
        part_a_name: Party A (buyer) name filter
        part_b_name: Party B (supplier) name filter
        provice_code: Province administrative code
        page: Page number (default 1)
        items_per_page: Results per page (default 20)
    """
    params = {}
    for name, value in [
        ("keyword", keyword),
        ("start_date", start_date),
        ("end_date", end_date),
        ("contract_end_min", contract_end_min),
        ("contract_end_max", contract_end_max),
        ("part_a_name", part_a_name),
        ("part_b_name", part_b_name),
        ("provice_code", provice_code),
        ("page", page),
        ("items_per_page", items_per_page),
    ]:
        if value:
            params[name] = value

    result = _http_get("/bid/contracts", api_key, params)

    if result.get("code") != 0:
        return json.dumps({"error": result.get("message", "Unknown error")},
                          ensure_ascii=False)

    data = result.get("data", result)
    contracts = data.get("contracts", [])
    total = data.get("total", len(contracts))

    simplified = []
    for c in contracts:
        simplified.append({
            "id": c.get("id"),
            "title": c.get("title", ""),
            "publish_time": str(c.get("publish_time", "")),
            "project_money": c.get("project_money"),
            "has_file": bool(c.get("has_file")),
            "contract_end_date": c.get("contract_end_date"),
        })

    return json.dumps({
        "total": total,
        "shown": len(simplified),
        "page": int(page),
        "contracts": simplified,
        "from_cache": data.get("from_cache", False),
    }, ensure_ascii=False, indent=2)


def tool_get_bid_detail_v2(api_key: str, project_id: str, publish_time: str = "") -> str:
    """Get full detail of a bid project via v2 gateway (cache-first).

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        project_id: Bid project ID from search results
        publish_time: Publish time from search results (required for first fetch)
    """
    pid = int(project_id)
    params = {}
    if publish_time:
        params["publish_time"] = publish_time
    result = _http_get(f"/bid/projects/{pid}/detail-v2", api_key, params if params else None)

    if result.get("code") != 0:
        return json.dumps({"error": result.get("message", "Unknown error")},
                          ensure_ascii=False)

    data = result.get("data", {})
    content = data.get("content", {})
    structure = data.get("structure", {})

    return json.dumps({
        "project_id": pid,
        "content": {
            "title": content.get("title", ""),
            "content_length": len(content.get("content", "")),
            "project_money": content.get("projectMoney", ""),
            "part_a_name": content.get("partAName", ""),
            "part_b_name": content.get("partBName", ""),
            "agent_name": content.get("agentName", ""),
            "industry_name": content.get("industryName", ""),
            "files": content.get("projectFiles", []),
        },
        "structure": structure,
        "from_cache": data.get("from_cache", False),
    }, ensure_ascii=False, indent=2)


def tool_enterprise_contacts(api_key: str, company_name: str,
                              page_no: str = "1", page_size: str = "5") -> str:
    """Get enterprise contacts list.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        company_name: Company name to look up
        page_no: Page number (default 1)
        page_size: Results per page (default 5)
    """
    result = _http_get("/bid/enterprises/contacts", api_key, {
        "company_name": company_name,
        "page_no": page_no,
        "page_size": page_size,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_enterprise_customers(api_key: str, company_name: str,
                               page_no: str = "1", page_size: str = "20") -> str:
    """Get enterprise customer projects list.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        company_name: Company name to look up
        page_no: Page number (default 1)
        page_size: Results per page (default 20)
    """
    result = _http_get("/bid/enterprises/customers", api_key, {
        "company_name": company_name,
        "page_no": page_no,
        "page_size": page_size,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_enterprise_suppliers(api_key: str, company_name: str,
                               page_no: str = "1", page_size: str = "20") -> str:
    """Get enterprise supplier projects list.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        company_name: Company name to look up
        page_no: Page number (default 1)
        page_size: Results per page (default 20)
    """
    result = _http_get("/bid/enterprises/suppliers", api_key, {
        "company_name": company_name,
        "page_no": page_no,
        "page_size": page_size,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)

def tool_ask_agent(api_key: str, agent_id: str, question: str,
                   session_id: str = "") -> str:
    """Ask a RAGFlow agent a question and get the answer.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        agent_id: Agent canvas ID
        question: The question to ask
        session_id: Optional session ID for conversation continuity
    """
    # Without a session, we must bootstrap the canvas replica first
    if not session_id:
        bootstrap_result = _http_get(f"/agents/{agent_id}", api_key)
        if bootstrap_result.get("code") != 0:
            return json.dumps({
                "error": f"Failed to access agent: {bootstrap_result.get('message', 'Not found')}"
            }, ensure_ascii=False)

    body = {
        "agent_id": agent_id,
        "query": question,
    }
    if session_id:
        body["session_id"] = session_id

    result = _http_post_sse("/agents/chat/completion", api_key, body)

    if result.get("code") != 0:
        return json.dumps({"error": result.get("message", "Unknown error")},
                          ensure_ascii=False)

    data = result.get("data", result)
    answer = data.get("answer", json.dumps(data, ensure_ascii=False))

    if session_id:
        return json.dumps({"answer": answer, "session_id": session_id}, ensure_ascii=False, indent=2)
    return json.dumps({"answer": answer}, ensure_ascii=False, indent=2)


def tool_get_bid_detail(api_key: str, project_id: str, publish_time: str = "") -> str:
    """Get full detail of a bid project: content HTML, structured data, and attached files.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        project_id: Bid project ID from search results
        publish_time: Publish time from search results (required for first fetch)
    """
    pid = int(project_id)

    # Fetch detail
    detail_params = {}
    if publish_time:
        detail_params["publish_time"] = publish_time
    detail = _http_get(f"/bid/projects/{pid}/detail", api_key, detail_params if detail_params else None)

    # Fetch structure
    struct = _http_get(f"/bid/projects/{pid}/structure", api_key, detail_params if detail_params else None)

    # Fetch files
    files = _http_get(f"/bid/projects/{pid}/files", api_key, detail_params if detail_params else None)

    detail_data = detail.get("data", {}) if detail.get("code") == 0 else {}
    struct_data = struct.get("data", {}) if struct.get("code") == 0 else {}
    files_data = files.get("data", []) if files.get("code") == 0 else []

    return json.dumps({
        "project_id": pid,
        "content_html": detail_data.get("content_html", ""),
        "structure": struct_data,
        "files": files_data if isinstance(files_data, list) else [],
    }, ensure_ascii=False, indent=2)


def tool_import_bid_to_kb(api_key: str, project_id: str, publish_time: str = "",
                          kb_id: str = "") -> str:
    """Import a bid project's detail content and files into a RAGFlow knowledge base.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        project_id: Bid project ID from search results
        publish_time: Publish time from search results (required for first import)
        kb_id: Target knowledge base ID (default: d23e0644578211f19c3bed5c593fe4c9)
    """
    pid = int(project_id)
    body = {
        "kb_id": kb_id or "d23e0644578211f19c3bed5c593fe4c9",
    }
    result = _http_post(f"/bid/projects/{pid}/parse", api_key, body)
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_check_bid_import_status(api_key: str, project_id: str) -> str:
    """Check the KB import/parse status of a bid project.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        project_id: Bid project ID from search results
    """
    pid = int(project_id)
    result = _http_get(f"/bid/projects/{pid}/parse-status", api_key)
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_lookup_bid_code(keyword: str, code_type: str = "area") -> str:
    """Look up administrative area codes or industry codes by Chinese name.

    Use this BEFORE calling search_bid_projects to convert user's natural-language
    location/industry references into the correct codes.

    Args:
        keyword: Chinese name to search, e.g. "广东", "广州", "建筑", "农业"
        code_type: "area" (administrative division) or "industry" (GB/T 4754-2017)
    """
    if code_type == "area":
        result = _http_get("/bid/areas", "", {"parent_code": "all"})
        data = result.get("data", [])
        keyword_lower = keyword.strip().lower()
        matches = []
        for item in data:
            name = item.get("name", "")
            code = item.get("code", "")
            if keyword_lower in name.lower() or keyword_lower in code:
                matches.append({
                    "code": code,
                    "name": name,
                    "level": item.get("level", 0),
                    "parent_code": item.get("parent_code", ""),
                })
        # Sort: exact match first, then by level (province first)
        matches.sort(key=lambda x: (
            0 if x["name"] == keyword.strip() else 1,
            x["level"],
        ))
        if len(matches) > 20:
            matches = matches[:20]
        return json.dumps({
            "type": "area",
            "keyword": keyword,
            "total": len(matches),
            "matches": matches,
        }, ensure_ascii=False, indent=2)

    elif code_type == "industry":
        result = _http_get("/bid/industries", "")
        tree = result.get("data", [])
        keyword_lower = keyword.strip().lower()
        matches = []
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
        matches.sort(key=lambda x: (
            0 if x["name"] == keyword.strip() else 1,
            0 if x["level"] == "门类" else 1,
        ))
        if len(matches) > 20:
            matches = matches[:20]
        return json.dumps({
            "type": "industry",
            "keyword": keyword,
            "total": len(matches),
            "matches": matches,
        }, ensure_ascii=False, indent=2)

    else:
        return json.dumps({
            "error": f"Unknown code_type: {code_type}. Use 'area' or 'industry'.",
        }, ensure_ascii=False)


def tool_search_bid_projects(api_key: str, keyword: str = "",
                             project_class_id: str = "",
                             purchase_type_id: str = "",
                             provice_code: str = "",
                             city_code: str = "",
                             start_date: str = "",
                             end_date: str = "",
                             project_money_min: str = "",
                             project_money_max: str = "",
                             part_a_name: str = "",
                             part_b_name: str = "",
                             industry_code: str = "",
                             page: str = "1",
                             items_per_page: str = "20") -> str:
    """Search bid projects from the RAGFlow bidding database.

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx)
        keyword: Search keyword (matches title, content, party names)
        project_class_id: Project class filter
        purchase_type_id: Purchase type filter
        provice_code: Province code filter
        city_code: City code filter
        start_date: Publish date range start (YYYY-MM-DD)
        end_date: Publish date range end (YYYY-MM-DD)
        project_money_min: Minimum project amount
        project_money_max: Maximum project amount
        part_a_name: Party A (buyer) name filter
        part_b_name: Party B (supplier) name filter
        industry_code: Industry classification code
        page: Page number (default 1)
        items_per_page: Items per page (default 20)
    """
    params = {}
    for name, value in [
        ("keyword", keyword),
        ("project_class_id", project_class_id),
        ("purchase_type_id", purchase_type_id),
        ("provice_code", provice_code),
        ("city_code", city_code),
        ("start_date", start_date),
        ("end_date", end_date),
        ("project_money_min", project_money_min),
        ("project_money_max", project_money_max),
        ("part_a_name", part_a_name),
        ("part_b_name", part_b_name),
        ("industry_code", industry_code),
        ("page", page),
        ("items_per_page", items_per_page),
    ]:
        if value:
            params[name] = value

    result = _http_get("/bid/projects", api_key, params)

    if result.get("code") != 0:
        return json.dumps({"error": result.get("message", "Unknown error")},
                          ensure_ascii=False)

    data = result.get("data", result)
    projects = data.get("projects", [])
    total = data.get("total", len(projects))

    simplified = []
    for p in projects:
        simplified.append({
            "id": p.get("id"),
            "title": p.get("title", ""),
            "publish_time": str(p.get("publish_time", "")),
            "project_class_id": p.get("project_class_id"),
            "project_money": p.get("project_money"),
            "has_file": bool(p.get("has_file")),
        })

    return json.dumps({
        "total": total,
        "shown": len(simplified),
        "page": int(page),
        "projects": simplified,
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# MCP JSON-RPC over stdio
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "ask_agent",
        "description": "Ask a RAGFlow AI agent a question. The agent can search knowledge bases, answer questions, and execute workflows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx). Get one from the RAGFlow admin panel.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "The RAGFlow agent/canvas ID to query.",
                },
                "question": {
                    "type": "string",
                    "description": "The question or prompt to send to the agent.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional conversation session ID for maintaining context across multiple questions.",
                },
            },
            "required": ["api_key", "agent_id", "question"],
        },
    },
    {
        "name": "lookup_bid_code",
        "description": "Look up region/industry codes by Chinese name BEFORE calling search_bid_projects. The LLM does NOT know these codes natively — use this tool to convert user's natural language (e.g. '广东', '建筑') into the correct code values (e.g. '44', 'E'). Supports: area (province/city/district) and industry (GB/T 4754-2017).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Chinese name to look up, e.g. '广东', '广州', '建筑', '农业'. Partial match supported.",
                },
                "code_type": {
                    "type": "string",
                    "description": "Code type: 'area' for administrative division codes, 'industry' for GB/T 4754-2017 industry codes. Default: 'area'.",
                },
            },
            "required": ["keyword", "code_type"],
        },
    },
    {
        "name": "search_bid_projects",
        "description": "Search Chinese government procurement bid projects. Supports filtering by keyword, project class, purchase type, region, date range, money range, party names, and industry code. IMPORTANT: Use lookup_bid_code FIRST to convert location/industry names to codes before calling this tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (matches title, content, party names, keywords).",
                },
                "project_class_id": {
                    "type": "string",
                    "description": "Project class filter (e.g., 'GC' for engineering, 'HT' for contract).",
                },
                "purchase_type_id": {
                    "type": "string",
                    "description": "Purchase type filter.",
                },
                "provice_code": {
                    "type": "string",
                    "description": "Province administrative code filter.",
                },
                "city_code": {
                    "type": "string",
                    "description": "City administrative code filter.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Publish date lower bound (format: YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Publish date upper bound (format: YYYY-MM-DD).",
                },
                "project_money_min": {
                    "type": "string",
                    "description": "Minimum project amount (RMB).",
                },
                "project_money_max": {
                    "type": "string",
                    "description": "Maximum project amount (RMB).",
                },
                "part_a_name": {
                    "type": "string",
                    "description": "Party A (buyer/procuring entity) name filter.",
                },
                "part_b_name": {
                    "type": "string",
                    "description": "Party B (supplier/winner) name filter.",
                },
                "industry_code": {
                    "type": "string",
                    "description": "GB/T 4754-2017 industry classification code (e.g., 'E48' for construction).",
                },
                "page": {
                    "type": "string",
                    "description": "Page number (default: 1).",
                },
                "items_per_page": {
                    "type": "string",
                    "description": "Results per page (default: 20).",
                },
            },
            "required": ["api_key"],
        },
    },
    {
        "name": "get_bid_detail",
        "description": "Get full detail of a specific bid project: content HTML, structured data (project name, budget, parties, bid companies, dates), and attached files. Use after search_bid_projects to drill into a project the user is interested in. The project_id and publish_time must come from search_bid_projects results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Bid project ID from search_bid_projects results.",
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time from search_bid_projects results. Required when data is not yet cached.",
                },
            },
            "required": ["api_key", "project_id"],
        },
    },
    {
        "name": "import_bid_to_kb",
        "description": "Import a bid project's detail content and attached files into a RAGFlow knowledge base and trigger document parsing. After import, the KB can be used for document generation. Dedup: if already imported, returns existing status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Bid project ID from search_bid_projects results.",
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time from search_bid_projects results. Required when data is not yet cached.",
                },
                "kb_id": {
                    "type": "string",
                    "description": "Target knowledge base ID. Default: d23e0644578211f19c3bed5c593fe4c9",
                },
            },
            "required": ["api_key", "project_id"],
        },
    },
    {
        "name": "check_bid_import_status",
        "description": "Check whether a bid project has been imported to a knowledge base and its parsing progress. Returns status: none/pending/parsing/done/fail with progress (0-1).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Bid project ID from search_bid_projects results.",
                },
            },
            "required": ["api_key", "project_id"],
        },
    },
    {
        "name": "search_contracts",
        "description": "Search contract/winning-bid data from the bidding database. Cache-first: returns cached results when available (free), only calls external API on cache miss. Supports filtering by keyword, date range, contract end date, party names, and region.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (matches title, content, party names).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Publish date lower bound (format: YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Publish date upper bound (format: YYYY-MM-DD).",
                },
                "contract_end_min": {
                    "type": "string",
                    "description": "Contract end date minimum (YYYY-MM-DD).",
                },
                "contract_end_max": {
                    "type": "string",
                    "description": "Contract end date maximum (YYYY-MM-DD).",
                },
                "part_a_name": {
                    "type": "string",
                    "description": "Party A (buyer) name filter.",
                },
                "part_b_name": {
                    "type": "string",
                    "description": "Party B (supplier) name filter.",
                },
                "provice_code": {
                    "type": "string",
                    "description": "Province administrative code filter.",
                },
                "page": {
                    "type": "string",
                    "description": "Page number (default: 1).",
                },
                "items_per_page": {
                    "type": "string",
                    "description": "Results per page (default: 20).",
                },
            },
            "required": ["api_key"],
        },
    },
    {
        "name": "get_bid_detail_v2",
        "description": "Get full detail of a bid project via v2 gateway (cache-first, 30-day TTL). Returns content (HTML body + attached files) and structure (project name, budget, parties, dates, etc.). Use after search_contracts or search_bid_projects to drill into a specific project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Bid project ID from search results.",
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time from search results. Required when data is not yet cached.",
                },
            },
            "required": ["api_key", "project_id"],
        },
    },
    {
        "name": "enterprise_contacts",
        "description": "Get contact person list for a company from the bidding database. Returns names, positions, phone numbers, and email addresses.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up contacts for.",
                },
                "page_no": {
                    "type": "string",
                    "description": "Page number (default: 1).",
                },
                "page_size": {
                    "type": "string",
                    "description": "Results per page (default: 5).",
                },
            },
            "required": ["api_key", "company_name"],
        },
    },
    {
        "name": "enterprise_customers",
        "description": "Get customer project list for a company (projects where this company was the supplier/Party B). Shows who this company has served.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up customers for.",
                },
                "page_no": {
                    "type": "string",
                    "description": "Page number (default: 1).",
                },
                "page_size": {
                    "type": "string",
                    "description": "Results per page (default: 20).",
                },
            },
            "required": ["api_key", "company_name"],
        },
    },
    {
        "name": "enterprise_suppliers",
        "description": "Get supplier project list for a company (projects where this company was the buyer/Party A). Shows who has supplied this company.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company name to look up suppliers for.",
                },
                "page_no": {
                    "type": "string",
                    "description": "Page number (default: 1).",
                },
                "page_size": {
                    "type": "string",
                    "description": "Results per page (default: 20).",
                },
            },
            "required": ["api_key", "company_name"],
        },
    },
]


def _jsonrpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _jsonrpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_request(req: dict) -> dict | None:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    # --- Lifecycle ---
    if method == "initialize":
        return _jsonrpc_result(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "ragflow-mcp",
                "version": "1.0.0",
            },
        })

    if method == "notifications/initialized":
        return None  # No response for notifications

    # --- Tools ---
    if method == "tools/list":
        return _jsonrpc_result(rid, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "ask_agent":
            try:
                result_text = tool_ask_agent(
                    api_key=arguments.get("api_key", ""),
                    agent_id=arguments.get("agent_id", ""),
                    question=arguments.get("question", ""),
                    session_id=arguments.get("session_id", ""),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "lookup_bid_code":
            try:
                result_text = tool_lookup_bid_code(
                    keyword=arguments.get("keyword", ""),
                    code_type=arguments.get("code_type", "area"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "search_bid_projects":
            try:
                result_text = tool_search_bid_projects(
                    api_key=arguments.get("api_key", ""),
                    keyword=arguments.get("keyword", ""),
                    project_class_id=arguments.get("project_class_id", ""),
                    purchase_type_id=arguments.get("purchase_type_id", ""),
                    provice_code=arguments.get("provice_code", ""),
                    city_code=arguments.get("city_code", ""),
                    start_date=arguments.get("start_date", ""),
                    end_date=arguments.get("end_date", ""),
                    project_money_min=arguments.get("project_money_min", ""),
                    project_money_max=arguments.get("project_money_max", ""),
                    part_a_name=arguments.get("part_a_name", ""),
                    part_b_name=arguments.get("part_b_name", ""),
                    industry_code=arguments.get("industry_code", ""),
                    page=arguments.get("page", "1"),
                    items_per_page=arguments.get("items_per_page", "20"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "get_bid_detail":
            try:
                result_text = tool_get_bid_detail(
                    api_key=arguments.get("api_key", ""),
                    project_id=str(arguments.get("project_id", "")),
                    publish_time=arguments.get("publish_time", ""),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "import_bid_to_kb":
            try:
                result_text = tool_import_bid_to_kb(
                    api_key=arguments.get("api_key", ""),
                    project_id=str(arguments.get("project_id", "")),
                    publish_time=arguments.get("publish_time", ""),
                    kb_id=arguments.get("kb_id", ""),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "check_bid_import_status":
            try:
                result_text = tool_check_bid_import_status(
                    api_key=arguments.get("api_key", ""),
                    project_id=str(arguments.get("project_id", "")),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "search_contracts":
            try:
                result_text = tool_search_contracts(
                    api_key=arguments.get("api_key", ""),
                    keyword=arguments.get("keyword", ""),
                    start_date=arguments.get("start_date", ""),
                    end_date=arguments.get("end_date", ""),
                    contract_end_min=arguments.get("contract_end_min", ""),
                    contract_end_max=arguments.get("contract_end_max", ""),
                    part_a_name=arguments.get("part_a_name", ""),
                    part_b_name=arguments.get("part_b_name", ""),
                    provice_code=arguments.get("provice_code", ""),
                    page=arguments.get("page", "1"),
                    items_per_page=arguments.get("items_per_page", "20"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "get_bid_detail_v2":
            try:
                result_text = tool_get_bid_detail_v2(
                    api_key=arguments.get("api_key", ""),
                    project_id=str(arguments.get("project_id", "")),
                    publish_time=arguments.get("publish_time", ""),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "enterprise_contacts":
            try:
                result_text = tool_enterprise_contacts(
                    api_key=arguments.get("api_key", ""),
                    company_name=arguments.get("company_name", ""),
                    page_no=arguments.get("page_no", "1"),
                    page_size=arguments.get("page_size", "5"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "enterprise_customers":
            try:
                result_text = tool_enterprise_customers(
                    api_key=arguments.get("api_key", ""),
                    company_name=arguments.get("company_name", ""),
                    page_no=arguments.get("page_no", "1"),
                    page_size=arguments.get("page_size", "20"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "enterprise_suppliers":
            try:
                result_text = tool_enterprise_suppliers(
                    api_key=arguments.get("api_key", ""),
                    company_name=arguments.get("company_name", ""),
                    page_no=arguments.get("page_no", "1"),
                    page_size=arguments.get("page_size", "20"),
                )
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": result_text}]
                })
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        return _jsonrpc_error(rid, -32601, f"Unknown tool: {tool_name}")

    # --- Unknown method ---
    return _jsonrpc_error(rid, -32601, f"Method not found: {method}")


def main():
    """MCP stdio main loop — read JSON-RPC from stdin, write to stdout."""

    # stderr is free for logging because MCP transport uses stdout
    print("RAGFlow MCP Server starting...", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"MCP parse error: {e}", file=sys.stderr, flush=True)
            continue

        response = handle_request(request)
        if response is not None:
            out = json.dumps(response, ensure_ascii=False)
            sys.stdout.write(out + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
