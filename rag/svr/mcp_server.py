#!/usr/bin/env python3
"""
RAGFlow MCP Server — exposes ask_agent and search_bid_projects as MCP tools.
Integrates with Hermes / OpenClaw via stdio JSON-RPC transport.

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
        "name": "search_bid_projects",
        "description": "Search Chinese government procurement bid projects. Supports filtering by keyword, project class, purchase type, region, date range, money range, party names, and industry code.",
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
