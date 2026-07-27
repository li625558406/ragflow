"""
Unified Crawler Framework for RAGFlow.

Configuration-driven crawling with pluggable adapters, extractors, and strategies.
"""

import re
from datetime import date, timedelta
from typing import Any, Dict

__version__ = "1.0.0"


def resolve_params(params: Dict[str, Any], page_val: str = "", size_val: str = "") -> Dict[str, Any]:
    """Resolve template placeholders in param values (recursive into nested dicts).

    Supported templates:
      - {{ page }}      → page number
      - {{ page_size }} → page size
      - {{ today }}     → current date (YYYY-MM-DD)
      - {{ N_days_ago }} → N days before today (e.g. {{ 3_days_ago }} → 3 days ago)

    Supported date formats:
      - {{ today }}        → YYYY-MM-DD
      - {{ today_colon }}  → YYYY:MM:DD (e.g. ccgp.gov.cn start_time/end_time)
      - {{ today_slash }}  → YYYY/MM/DD
      - {{ N_days_ago }}   → N days before today (YYYY-MM-DD)
      - {{ N_days_ago_colon }} → N days before today (YYYY:MM:DD)

    Returns a new dict with resolved values. Lists and non-string values are left unchanged.
    """
    today_str = date.today().isoformat()
    today_colon_str = today_str.replace("-", ":")

    def _resolve_string(val: str) -> str:
        val = val.replace("{{ page }}", page_val)
        val = val.replace("{{ page_size }}", size_val)
        val = val.replace("{{ today }}", today_str)
        val = val.replace("{{ today_colon }}", today_colon_str)
        val = val.replace("{{ today_slash }}", today_str.replace("-", "/"))

        def _days_ago(m: re.Match) -> str:
            n = int(m.group(1))
            return (date.today() - timedelta(days=n)).isoformat()

        def _days_ago_colon(m: re.Match) -> str:
            n = int(m.group(1))
            return (date.today() - timedelta(days=n)).isoformat().replace("-", ":")

        val = re.sub(r"\{\{\s*(\d+)_days_ago\s*\}\}", _days_ago, val)
        val = re.sub(r"\{\{\s*(\d+)_days_ago_colon\s*\}\}", _days_ago_colon, val)
        return val

    def _resolve(val: Any) -> Any:
        if isinstance(val, str) and "{{" in val:
            return _resolve_string(val)
        if isinstance(val, dict):
            return {k: _resolve(v) for k, v in val.items()}
        return val

    return {k: _resolve(v) for k, v in params.items()}


def resolve_url(url: str, page_val: str = "", size_val: str = "") -> str:
    """Resolve template placeholders in a URL string.

    Supports the same templates as resolve_params().
    """
    today_str = date.today().isoformat()
    today_colon_str = today_str.replace("-", ":")
    url = url.replace("{{ page }}", page_val)
    url = url.replace("{{ page_size }}", size_val)
    url = url.replace("{{ today }}", today_str)
    url = url.replace("{{ today_colon }}", today_colon_str)
    url = url.replace("{{ today_slash }}", today_str.replace("-", "/"))

    def _days_ago(m: re.Match) -> str:
        n = int(m.group(1))
        return (date.today() - timedelta(days=n)).isoformat()

    def _days_ago_colon(m: re.Match) -> str:
        n = int(m.group(1))
        return (date.today() - timedelta(days=n)).isoformat().replace("-", ":")

    url = re.sub(r"\{\{\s*(\d+)_days_ago\s*\}\}", _days_ago, url)
    url = re.sub(r"\{\{\s*(\d+)_days_ago_colon\s*\}\}", _days_ago_colon, url)
    return url
