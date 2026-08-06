"""
Abstract base class for data extractors.

Extractors transform raw API/HTML responses into structured item dicts
with consistent field names.
"""

import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import quote
from typing import Any, Dict, List, Optional

from ..config import ExtractConfig


class BaseExtractor(ABC):
    """Abstract extractor — transforms raw data into structured items."""

    def __init__(self, config: ExtractConfig):
        self._config = config

    @abstractmethod
    def extract(self, raw_data: Any, **kwargs) -> List[Dict[str, Any]]:
        """Extract items from raw data.

        Args:
            raw_data: Raw response data (dict, list, or HTML string).
            **kwargs: Optional extras (e.g. base_url for CSS extractors).

        Returns:
            List of item dicts with mapped field names.
        """
        ...

    def extract_fields(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Map source fields to internal field names.

        When the CSS extractor's extract() has already populated the target
        field names (e.g. {id, title, url, date}), the source_field is a CSS
        selector (e.g. "a@href") which won't exist as a dict key.  In that
        case, fall back to the existing value in the item so we don't
        overwrite good data with empty strings.
        """
        if not self._config.fields:
            return item

        mapped = {}
        for internal_name, source_field in self._config.fields.items():
            val = self._get_nested(item, source_field)
            # If dict-key lookup found nothing, keep any existing value
            # (from CSS extractor's extract() which already mapped fields)
            if not val and internal_name in item and item[internal_name]:
                val = item[internal_name]
            mapped[internal_name] = val
        # Include unmapped fields too
        for k, v in item.items():
            if k not in mapped:
                mapped[k] = v

        # Optional URL synthesis: if url_template is configured, build a
        # clickable URL from the raw item fields (e.g. "https://x/detail?id={ID}").
        # Only overrides when the mapped url is empty, so sources that already
        # provide a URL are unaffected.
        # 模板中 {field} 的值会做 URL quote, 保证中文/特殊字符生成合法 URL.
        # 朴素 str.format 不支持 urlencode, 这里用自定义 {field} 占位替换.
        tpl = getattr(self._config, "url_template", "")
        if tpl and not mapped.get("url"):
            try:
                def _replace(m):
                    key = m.group(1)
                    # Prefer mapped (canonical field name, e.g. id mapped from
                    # M_ID); fall back to raw item key so YAML can still use
                    # source field names directly. Fixes ggzyfw_fujian_business
                    # where url_template used {id} but raw API field is M_ID.
                    val = mapped.get(key)
                    if val is None or val == "":
                        val = item.get(key, "")
                    return quote(str(val), safe="")
                mapped["url"] = re.sub(r"\{(\w+)\}", _replace, tpl)
            except (KeyError, IndexError, ValueError):
                pass

        # Optional URL transform: regex search/replace on the extracted url.
        # Generic feature — useful when the listing API returns a "summary" URL
        # (e.g. /deal/html/a/...) but the actual content body lives at a sibling
        # path (/deal/html/b/...). Applied AFTER url_template so both can compose.
        url_search = getattr(self._config, "url_search", "")
        url_replace = getattr(self._config, "url_replace", "")
        if url_search and mapped.get("url"):
            try:
                mapped["url"] = re.sub(url_search, url_replace, mapped["url"])
            except re.error as e:
                logging.warning("Extractor: invalid url_search regex %r: %s", url_search, e)
        return mapped

    @staticmethod
    def _get_nested(data: Dict[str, Any], path: str, default: Any = "") -> Any:
        """Get a nested value using dot-separated path (e.g. 'user.name')."""
        if not path:
            return default
        keys = path.split(".")
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key, default)
            else:
                return default
        return data


class ExtractorFactory:
    """Factory for creating extractor instances."""

    @staticmethod
    def create(config: ExtractConfig):
        if config.type == "json_path":
            from .json_path import JsonPathExtractor
            return JsonPathExtractor(config)
        elif config.type == "css_selector":
            from .css_selector import CssSelectorExtractor
            return CssSelectorExtractor(config)
        elif config.type == "ai":
            from .ai_extractor import AIExtractor
            return AIExtractor(config)
        else:
            from .json_path import JsonPathExtractor
            return JsonPathExtractor(config)
