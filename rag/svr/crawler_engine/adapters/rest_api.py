"""
REST API adapter — pure HTTP via requests.Session.

Handles:
- Standard REST API JSON endpoints
- HTML page parsing via BeautifulSoup (optional)
- Cookie-based session management
- Rate limiting and retry
"""

import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

from ..config import SiteConfig
from ..session_manager import SessionManager
from .. import resolve_params, resolve_url
from .base import BaseAdapter


def _get_json_value(data, path: str) -> Any:
    """Get a nested value by dot-separated path.

    Supports dict keys and list indices (integer segments).
    Example: "data.0.detailFileObjList.0.content"
    """
    if not path:
        return None
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list):
            try:
                idx = int(key)
                if 0 <= idx < len(data):
                    data = data[idx]
                else:
                    return None
            except (ValueError, TypeError):
                return None
        else:
            return None
    return data


def _extract_tab_content(data) -> str:
    """Extract content from tab-based responses (xmzyjy pattern).

    Handles both raw tab arrays and wrapped API responses like
    {"code": 0, "data": [...]}.

    Scans tabs[].detailFileObjList[].content and tabs[].tenderPlanList[].content.
    Returns concatenated content strings.
    """
    parts = []
    # Unwrap common API response wrapper
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            tabs = inner
        else:
            tabs = [data]
    elif isinstance(data, list):
        tabs = data
    else:
        return ""
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        dfol = tab.get("detailFileObjList")
        if isinstance(dfol, list):
            for entry in dfol:
                if isinstance(entry, dict):
                    for ck in ("content", "freeMkrContent"):
                        c = entry.get(ck, "")
                        if isinstance(c, str) and len(c.strip()) > 50:
                            parts.append(c)
        tpl = tab.get("tenderPlanList")
        if isinstance(tpl, list):
            for entry in tpl:
                if isinstance(entry, dict):
                    c = entry.get("content", "")
                    if isinstance(c, str) and len(c.strip()) > 20:
                        parts.append(c)
    return "\n\n".join(parts)


class RestApiAdapter(BaseAdapter):
    """Adapter for standard REST API / HTML sites."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        if config.transport.engine == "scrapling":
            self._session = SessionManager.create_scrapling(config.transport)
        else:
            self._session = SessionManager.create(config.transport)

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch a page of items from the listing URL.

        Handles both JSON API responses and HTML pages (via BeautifulSoup).
        """
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        method = listing.method.upper()

        # Merge template params with page params
        params = dict(listing.params)

        # Handle html_regex pagination: _page_url_suffix overrides the URL path
        url_suffix = page_params.pop("_page_url_suffix", "")
        # Section-level items_field override (e.g. jrkb "data" vs parent "result.records")
        self._items_field_override = page_params.pop("_items_field_override", "")
        if url_suffix:
            from urllib.parse import urlparse, urljoin
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            url = urljoin(base, url_suffix)

        params.update(page_params)

        # Resolve {{ page }} / {{ page_size }} / {{ today }} / {{ N_days_ago }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        params = resolve_params(params, page_val, size_val)
        url = resolve_url(url, page_val, size_val)

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                if method == "POST":
                    body_type = listing.body_type
                    if body_type == "json":
                        resp = self._session.post(url, json=params, timeout=self._transport.timeout)
                    elif body_type == "form":
                        resp = self._session.post(url, data=params, timeout=self._transport.timeout)
                    else:
                        resp = self._session.post(url, params=params, timeout=self._transport.timeout)
                else:
                    resp = self._session.get(url, params=params, timeout=self._transport.timeout)

                if resp.status_code == 429:
                    wait = (2 ** attempt) + random.uniform(1, 3)
                    logging.warning("RestApiAdapter: 429 rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    SessionManager.reset(self._session, self._transport)
                    continue

                if resp.status_code != 200:
                    logging.warning("RestApiAdapter: HTTP %d for %s", resp.status_code, url)
                    SessionManager.reset(self._session, self._transport)
                    time.sleep(2 + attempt * 2)
                    continue

                # Try JSON first, fall back to HTML/BeautifulSoup.
                # Some APIs return JSON with wrong Content-Type (text/html).
                # Sniff the body: if it starts with { or [, treat as JSON.
                content_type = resp.headers.get("Content-Type", "")
                text = resp.text
                if "application/json" in content_type or \
                   (text and text.strip()[:1] in ("{", "[")):
                    try:
                        data = resp.json()
                    except Exception:
                        # resp.json() uses strict parsing; many government CMS
                        # APIs (e.g. TRS/WAS5) return JSON with unescaped control
                        # chars inside string values (literal \n in content HTML).
                        # Fall back to lenient parsing before giving up.
                        try:
                            data = json.loads(text, strict=False)
                        except Exception:
                            # Still invalid JSON — fall through to HTML
                            self._last_raw = text
                            return self._parse_html_response(text)
                    self._last_raw = data
                    return self._parse_json_response(data)
                else:
                    self._last_raw = text
                    return self._parse_html_response(text)

            except Exception as e:
                logging.warning("RestApiAdapter: attempt %d failed: %s", attempt + 1, e)
                SessionManager.reset(self._session, self._transport)
                time.sleep(2 + attempt * 3)

        return None

    def _parse_json_response(self, data: Any) -> List[Dict[str, Any]]:
        """Extract items from JSON response.

        Handles both flat and nested structures:
          Flat:  {"resultList": [...]}
          Nested: {"code":200, "data": {"resultList": [...]}}
          Deep:   {"success":true, "result": {"data": {"list": [...]}}}
          Dotted: items_field="custom.infodata" for {"custom":{"infodata":[...]}}
        """
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items_field = getattr(self, "_items_field_override", "") or \
                self._config.pagination.items_field
            if items_field:
                # Support dot notation: "custom.infodata" → data["custom"]["infodata"]
                val = self._get_nested(data, items_field)
                # Handle double-encoded JSON: some sites (e.g. Putian todaykaib.json)
                # return a JSON string that needs secondary parsing
                if isinstance(val, str) and val.strip()[:1] in ("{", "["):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        pass
                if isinstance(val, list):
                    items = val
                elif isinstance(val, dict):
                    items = self._fallback_find_list(val)
                else:
                    items = self._fallback_find_list(data)
            else:
                items = self._fallback_find_list(data)
        else:
            return []

        # Client-side sort + limit (for APIs that return full dataset without pagination)
        extr = self._config.extract
        if extr.sort_field and items:
            try:
                items.sort(
                    key=lambda x: (x.get(extr.sort_field) or ""),
                    reverse=extr.sort_descending,
                )
            except Exception:
                pass  # sorting is best-effort
        if extr.max_items and len(items) > extr.max_items:
            items = items[:extr.max_items]

        return items

    def _fallback_find_list(self, data: dict) -> List[Dict[str, Any]]:
        """Fallback: search common keys for a list value."""
        for key in ("rows", "data", "list", "records", "result", "results", "custom"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    return val
                # Key exists but is a dict — recurse into it
                if isinstance(val, dict):
                    nested = self._parse_json_response(val)
                    if isinstance(nested, list) and not (len(nested) == 1 and nested[0] is val):
                        return nested
        # If no list found, wrap the dict itself
        return [data]

    @staticmethod
    def _get_nested(data: dict, field: str) -> Any:
        """Extract a value from nested dict using dot notation.
        E.g., _get_nested(data, "custom.infodata") → data["custom"]["infodata"].
        """
        if not isinstance(data, dict) or not field:
            return None
        keys = field.split(".")
        for key in keys:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
            if data is None:
                return None
        return data

    def _parse_html_response(self, html: str) -> List[Dict[str, Any]]:
        """Pass raw HTML through — the engine's CSS extractor handles parsing.

        The engine's _maybe_extract_from_html() will use the configured
        items_path to extract structured items from the HTML.
        """
        return [{"html": html}]

    def fetch_detail(self, item: Dict[str, Any], detail_override=None) -> Optional[Dict[str, Any]]:
        """Fetch and extract detail page content.

        Handles:
        - api_request: fetch from a detail API URL template
        - css_selector / inline / none: delegated to base class

        Args:
            item: The item dict to enrich with detail content.
            detail_override: Optional DetailConfig to use instead of self._config.detail
                            (used for section-level detail overrides).
        """
        detail_cfg = detail_override or self._config.detail

        # css_selector, inline, none, and missing URL are handled by base class
        if detail_cfg.type != "api_request" or not detail_cfg.url:
            return super().fetch_detail(item)

        url_template = detail_cfg.url
        # Replace {id}, {uuid}, etc. placeholders
        for key, val in item.items():
            url_template = url_template.replace("{" + key + "}", str(val))

        body_type = detail_cfg.body_type or "query"
        params = dict(detail_cfg.params)
        # Resolve {field_name} placeholders in param values (e.g. bulletinId)
        for pkey, pval in list(params.items()):
            if isinstance(pval, str) and "{" in pval:
                for key, val in item.items():
                    pval = pval.replace("{" + key + "}", str(val))
                params[pkey] = pval

        for attempt in range(3):
            try:
                if detail_cfg.method.upper() == "POST":
                    if body_type == "json":
                        resp = self._session.post(url_template, json=params, timeout=self._transport.timeout)
                    elif body_type == "form":
                        resp = self._session.post(url_template, data=params, timeout=self._transport.timeout)
                    else:  # query
                        resp = self._session.post(url_template, params=params, timeout=self._transport.timeout)
                else:
                    resp = self._session.get(url_template, params=params, timeout=self._transport.timeout)
                if resp.status_code == 200:
                    result = dict(item)
                    result["detail_html"] = resp.text

                    # Extract content from JSON response using content_field
                    try:
                        try:
                            data = resp.json()
                        except Exception:
                            # Lenient fallback for gov CMS APIs that emit
                            # unescaped control chars in string values.
                            data = json.loads(resp.text, strict=False)
                        cf = detail_cfg.content_field
                        content = _get_json_value(data, cf) if cf else ""
                        # Fallback: tab-based responses (xmzyjy pattern)
                        if not content:
                            content = _extract_tab_content(data)
                        if content:
                            result["content"] = str(content)
                        # Also extract common structured fields from JSON
                        for field in ("projectClassName", "projectMoney", "partAName",
                                      "partBName", "agentName", "newsTypeID",
                                      "purchaseTypeID", "industryName"):
                            if field in data and not result.get(field):
                                result[field] = data[field]
                        # Merge inner data dict (commonly: response.data contains
                        # content + structured metadata we want downstream).
                        inner = data.get("data") if isinstance(data, dict) else None
                        if isinstance(inner, dict):
                            for k, v in inner.items():
                                if k in result:
                                    continue
                                if v in (None, "", [], {}):
                                    continue
                                result[k] = v
                        # Parse attachment_fields — JSON string or list of file dicts.
                        # Many sites (e.g. fycbid) return a JSON-encoded string of
                        # file list under a field like "fileUrls".
                        collected: List[Dict[str, Any]] = []
                        for fld in detail_cfg.attachment_fields:
                            raw = _get_json_value(data, fld)
                            if isinstance(raw, str) and raw.strip():
                                try:
                                    parsed = json.loads(raw)
                                except json.JSONDecodeError:
                                    parsed = None
                                if isinstance(parsed, list):
                                    collected.extend([f for f in parsed if isinstance(f, dict)])
                                elif isinstance(parsed, dict):
                                    collected.append(parsed)
                            elif isinstance(raw, list):
                                collected.extend([f for f in raw if isinstance(f, dict)])
                            elif isinstance(raw, dict):
                                collected.append(raw)
                        # Apply file_url_template: construct download URL for files that only have an ID
                        if detail_cfg.file_url_template and collected:
                            for f in collected:
                                has_url = any(f.get(k) for k in ("file_url", "fileUrl", "url", "downloadUrl", "attUrl", "_href"))
                                if not has_url:
                                    file_id = f.get("id") or f.get("fileId") or f.get("file_id") or ""
                                    if file_id:
                                        f["file_url"] = detail_cfg.file_url_template.replace("{id}", str(file_id))
                        if collected:
                            existing = result.get("files")
                            if isinstance(existing, list):
                                result["files"] = existing + collected
                            else:
                                result["files"] = collected
                    except Exception:
                        pass  # Not JSON, keep as raw text

                    return result
            except Exception as e:
                logging.warning("RestApiAdapter: detail fetch failed: %s", e)
                time.sleep(1 + attempt)
        return item

    def cleanup(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
