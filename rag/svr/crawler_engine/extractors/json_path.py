"""
JSON path extractor — extracts items from JSON API responses.

Uses simple dot-notation paths to navigate JSON structures.
Example: items_path="data.rows", fields: {id: "uuid", title: "name"}
"""

from typing import Any, Dict, List

from .base import BaseExtractor


class JsonPathExtractor(BaseExtractor):
    """Extracts structured data from JSON/API responses."""

    def extract(self, raw_data: Any) -> List[Dict[str, Any]]:
        """Extract items from raw JSON data."""
        if raw_data is None:
            return []

        # If raw_data is a list, use it directly
        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict):
            # Navigate to items_path if specified
            items_path = self._config.items_path
            if items_path:
                items = self._get_nested(raw_data, items_path, None)
                if items is None:
                    # Try common keys
                    for key in ("rows", "data", "list", "records", "result", "results"):
                        if key in raw_data:
                            items = raw_data[key]
                            if isinstance(items, list):
                                break
            else:
                # Try common keys or wrap
                found = False
                for key in ("rows", "data", "list", "records", "result", "results"):
                    if key in raw_data:
                        items = raw_data[key]
                        found = True
                        break
                if not found:
                    if raw_data:
                        items = [raw_data]
                    else:
                        return []
        else:
            return []

        if not isinstance(items, list):
            return []

        # Map fields
        return [self.extract_fields(item) for item in items if isinstance(item, dict)]
