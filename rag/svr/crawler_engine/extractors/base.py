"""
Abstract base class for data extractors.

Extractors transform raw API/HTML responses into structured item dicts
with consistent field names.
"""

from abc import ABC, abstractmethod
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
