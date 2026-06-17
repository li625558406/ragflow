#!/usr/bin/env python3
"""
Scrapling MCP Server — web scraping tools for AI agents.

Exposes 3 tools via MCP JSON-RPC over stdio:
  1. scrape_page           — Fetch a single web page, return cleaned text/HTML
  2. scrape_adaptive       — Extract structured data with self-healing selectors
  3. search_and_extract     — Search bid projects via RAGFlow API, then scrape detail pages

Designed to complement (not replace) rag/svr/mcp_server.py.
This server handles raw web scraping; the other handles RAGFlow API calls.

Usage:
  python3 rag/svr/mcp_scrapling.py

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


# ─────────────────────────────────────────────────────────────
# HTTP helpers (mirror rag/svr/mcp_server.py patterns)
# ─────────────────────────────────────────────────────────────

def _bearer(api_key: str) -> str:
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
    except Exception as e:
        return {"code": -1, "message": str(e), "data": None}


# ─────────────────────────────────────────────────────────────
# Content cleaning (shared across tools)
# ─────────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Strip HTML tags, decode entities, normalize whitespace."""
    if not raw:
        return ""
    s = raw
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    s = s.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r" {2,}", " ", s)
    return s.strip()


# ─────────────────────────────────────────────────────────────
# Scrapling helpers (lazy import — only when tools are called)
# ─────────────────────────────────────────────────────────────

def _get_stealthy_fetcher():
    """Lazy import StealthyFetcher — raises clear error if not installed."""
    try:
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher
    except ImportError:
        raise ImportError(
            "scrapling is not installed. Install with:\n"
            "  pip install 'scrapling[fetchers]' && scrapling install"
        )


def _scrapling_to_dict(element) -> dict:
    """Convert a scrapling element to a simple dict (for JSON serialization)."""
    result = {}
    try:
        result["tag"] = element.tag if hasattr(element, "tag") else ""
    except Exception:
        result["tag"] = ""
    try:
        result["text"] = element.text.strip() if hasattr(element, "text") and element.text else ""
    except Exception:
        result["text"] = ""
    try:
        result["attrib"] = dict(element.attrib) if hasattr(element, "attrib") else {}
    except Exception:
        result["attrib"] = {}
    return result


# ─────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────

def tool_scrape_page(url: str, extract_mode: str = "text",
                     selector: str = "", headless: bool = True,
                     solve_cloudflare: bool = False) -> str:
    """Scrape a single web page.

    Args:
        url: The page URL to scrape.
        extract_mode: "text" (cleaned text), "html" (raw HTML), or "css" (CSS selector result).
        selector: CSS selector to extract (only used when extract_mode="css").
        headless: Use headless browser (default True).
        solve_cloudflare: Attempt Cloudflare Turnstile bypass (default False).

    Returns:
        JSON string with page content.
    """
    StealthyFetcher = _get_stealthy_fetcher()

    kwargs = {"headless": headless, "network_idle": True}
    if solve_cloudflare:
        kwargs["solve_cloudflare"] = True

    try:
        page = StealthyFetcher.fetch(url, **kwargs)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    result = {"url": url, "title": ""}

    # Title
    try:
        title_el = page.css("title::text")
        if isinstance(title_el, list) and title_el:
            result["title"] = str(title_el[0]) if hasattr(title_el[0], "text") is False else title_el[0]
    except Exception:
        pass

    if extract_mode == "html":
        result["html"] = getattr(page, "html_content", "") or getattr(page, "html", "") or getattr(page, "text", "")
    elif extract_mode == "css" and selector:
        try:
            elements = page.css(selector)
            result["items"] = [_scrapling_to_dict(el) for el in elements]
            result["count"] = len(result["items"])
        except Exception as e:
            result["error"] = f"CSS selector failed: {e}"
    else:
        # Default: cleaned text — try body (JSON APIs) then text (HTML pages)
        raw_text = ""
        if hasattr(page, "body") and page.body:
            try:
                raw_text = page.body.decode("utf-8") if isinstance(page.body, bytes) else str(page.body)
            except Exception:
                raw_text = ""
        if not raw_text:
            raw_text = getattr(page, "text", "") or str(page)
        result["text"] = _clean_text(raw_text)

    return json.dumps(result, ensure_ascii=False)


def tool_scrape_adaptive(url: str, selector: str, fields: str = "",
                         headless: bool = True,
                         solve_cloudflare: bool = False) -> str:
    """Extract structured data from a page using self-healing CSS selectors.

    Uses scrapling's auto_save/adaptive mechanism: on first call it fingerprints
    elements with auto_save=True; on subsequent calls it can recover from layout
    changes with adaptive=True.

    Args:
        url: The page URL to scrape.
        selector: CSS selector for the repeating elements (e.g., ".product-item").
        fields: Comma-separated field selectors relative to each element
                (e.g., "h2::text,.price::text,a::attr(href)").
        headless: Use headless browser (default True).
        solve_cloudflare: Attempt Cloudflare Turnstile bypass (default False).

    Returns:
        JSON string with extracted structured data.
    """
    StealthyFetcher = _get_stealthy_fetcher()

    kwargs = {"headless": headless, "network_idle": True}
    if solve_cloudflare:
        kwargs["solve_cloudflare"] = True

    try:
        page = StealthyFetcher.fetch(url, **kwargs)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    # Parse field list
    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else ["::text"]

    try:
        # First attempt: auto_save for fingerprinting
        elements = page.css(selector, auto_save=True)
    except Exception:
        try:
            # Fallback: standard CSS
            elements = page.css(selector)
        except Exception as e:
            return json.dumps({"error": f"Selector '{selector}' failed: {e}", "url": url}, ensure_ascii=False)

    items = []
    if not elements:
        # Try adaptive recovery
        try:
            elements = page.css(selector, adaptive=True)
        except Exception:
            pass

    if not elements:
        return json.dumps({"url": url, "items": [], "count": 0}, ensure_ascii=False)

    for el in elements:
        item = {}
        for f in field_list:
            try:
                if "::attr(" in f:
                    parts = f.rsplit("::attr(", 1)
                    inner = parts[0] if parts[0] else "*"
                    attr = parts[1].rstrip(")")
                    child = el.css(inner)
                    if isinstance(child, list) and child:
                        item[f] = child[0].attrib.get(attr, "") if hasattr(child[0], "attrib") else ""
                    else:
                        item[f] = ""
                elif f.endswith("::text"):
                    inner = f[:-6] if f != "::text" else None
                    if inner:
                        child = el.css(inner)
                    else:
                        child = [el] if el else []
                    if isinstance(child, list) and child:
                        item[f] = child[0].text if hasattr(child[0], "text") else str(child[0])
                    else:
                        item[f] = ""
                else:
                    child = el.css(f)
                    if isinstance(child, list) and child:
                        c = child[0]
                        item[f] = str(c.text).strip() if hasattr(c, "text") and c.text else str(c)
                    else:
                        item[f] = ""
            except Exception:
                item[f] = ""
        items.append(item)

    return json.dumps({"url": url, "items": items, "count": len(items)}, ensure_ascii=False)


def tool_search_and_extract(api_key: str, keyword: str = "",
                            project_id: str = "",
                            publish_time: str = "") -> str:
    """Search RAGFlow bid projects then scrape detail pages.

    Combines two operations:
    1. Call RAGFlow's bid search/projects API (if keyword provided)
    2. For a specific project, fetch and scrape the original source page

    Use this when the AI needs to read the ORIGINAL webpage content of a
    bid project (not just the structured data from the API).

    Args:
        api_key: RAGFlow API key (format: ragflow-xxx).
        keyword: Optional search keyword for bidding projects.
                 If empty, only fetches detail for the given project_id.
        project_id: Bid project ID. If provided with publish_time, scrapes the
                    original source page of this project.
        publish_time: Required with project_id when data is not cached.

    Returns:
        JSON string with search results and/or scraped original page content.
    """
    result = {}

    # 1. Search if keyword provided
    if keyword:
        resp = _http_get("/bid/projects", api_key, {
            "keyword": keyword,
            "page": "1",
            "items_per_page": "5",
        })
        if resp.get("code") == 0 and resp.get("data"):
            projects = resp["data"].get("projects", []) or resp["data"].get("items", [])
            result["search_results"] = [
                {
                    "id": str(p.get("id", "")),
                    "title": p.get("title", ""),
                    "publish_time": p.get("publish_time", ""),
                }
                for p in (projects or [])[:5]
            ]
            result["search_total"] = resp["data"].get("total", 0)

    # 2. Get original source URL and scrape it
    if project_id and publish_time:
        from urllib.parse import quote
        detail_resp = _http_get(
            f"/bid/projects/{quote(project_id)}/detail-v2",
            api_key,
            {"publish_time": publish_time}
        )
        if detail_resp.get("code") == 0 and detail_resp.get("data"):
            source_url = detail_resp["data"].get("source_url", "") or detail_resp["data"].get("collect_url", "")
            if source_url:
                try:
                    scraped = json.loads(tool_scrape_page(source_url, extract_mode="text"))
                    result["original_page"] = scraped
                except Exception:
                    result["original_page"] = {"error": "Failed to scrape source page", "url": source_url}

    return json.dumps(result, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# Tool schema definitions
# ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "scrape_page",
        "description": (
            "Scrape a single web page and return cleaned text, HTML, or CSS-extracted data. "
            "Uses stealth browser to bypass anti-bot protections. "
            "Use this when you need to read the original content of a web page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The web page URL to scrape.",
                },
                "extract_mode": {
                    "type": "string",
                    "description": "Extraction mode: 'text' (cleaned plain text, default), 'html' (raw HTML), or 'css' (CSS selector result).",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector to extract (only used when extract_mode='css').",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Use headless browser. Set to false to show the browser window. Default: true.",
                },
                "solve_cloudflare": {
                    "type": "boolean",
                    "description": "Attempt to bypass Cloudflare Turnstile. Default: false.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "scrape_adaptive",
        "description": (
            "Extract structured data from a page using self-healing CSS selectors. "
            "The selector auto-adapts to website layout changes — ideal for recurring "
            "scraping of pages that change structure. Returns JSON with extracted fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The web page URL to scrape.",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for repeating elements (e.g., '.product-item', 'table tbody tr').",
                },
                "fields": {
                    "type": "string",
                    "description": "Comma-separated field selectors relative to each element (e.g., 'h2::text,.price::text,a::attr(href)').",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Use headless browser. Default: true.",
                },
                "solve_cloudflare": {
                    "type": "boolean",
                    "description": "Attempt to bypass Cloudflare Turnstile. Default: false.",
                },
            },
            "required": ["url", "selector"],
        },
    },
    {
        "name": "search_and_extract",
        "description": (
            "Search RAGFlow bid projects by keyword, then scrape the original source "
            "web page of a specific project. Combines bid database search with live "
            "web scraping — useful when the API data is insufficient and you need to "
            "read the original government procurement announcement page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "RAGFlow API key for authentication (format: ragflow-xxx).",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search keyword for bidding projects (matches title/content).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Specific bid project ID to scrape. Required with publish_time for detail scraping.",
                },
                "publish_time": {
                    "type": "string",
                    "description": "Publish time of the project (format: YYYY-MM-DD). Required with project_id.",
                },
            },
            "required": ["api_key"],
        },
    },
]


# ─────────────────────────────────────────────────────────────
# JSON-RPC handling
# ─────────────────────────────────────────────────────────────

def _jsonrpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _jsonrpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_request(req: dict) -> dict | None:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    if method == "initialize":
        return _jsonrpc_result(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "scrapling-mcp",
                "version": "1.0.0",
            },
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _jsonrpc_result(rid, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "scrape_page":
            try:
                result_text = tool_scrape_page(
                    url=arguments.get("url", ""),
                    extract_mode=arguments.get("extract_mode", "text"),
                    selector=arguments.get("selector", ""),
                    headless=arguments.get("headless", True),
                    solve_cloudflare=arguments.get("solve_cloudflare", False),
                )
                return _jsonrpc_result(rid, {"content": [{"type": "text", "text": result_text}]})
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "scrape_adaptive":
            try:
                result_text = tool_scrape_adaptive(
                    url=arguments.get("url", ""),
                    selector=arguments.get("selector", ""),
                    fields=arguments.get("fields", ""),
                    headless=arguments.get("headless", True),
                    solve_cloudflare=arguments.get("solve_cloudflare", False),
                )
                return _jsonrpc_result(rid, {"content": [{"type": "text", "text": result_text}]})
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        elif tool_name == "search_and_extract":
            try:
                result_text = tool_search_and_extract(
                    api_key=arguments.get("api_key", ""),
                    keyword=arguments.get("keyword", ""),
                    project_id=str(arguments.get("project_id", "")),
                    publish_time=arguments.get("publish_time", ""),
                )
                return _jsonrpc_result(rid, {"content": [{"type": "text", "text": result_text}]})
            except Exception as e:
                return _jsonrpc_result(rid, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })

        return _jsonrpc_error(rid, -32601, f"Unknown tool: {tool_name}")

    return _jsonrpc_error(rid, -32601, f"Method not found: {method}")


def main():
    """MCP stdio main loop."""
    print("Scrapling MCP Server starting...", file=sys.stderr, flush=True)

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
