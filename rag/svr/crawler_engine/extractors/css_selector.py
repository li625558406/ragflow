"""
CSS selector extractor — extracts data from HTML pages.

Uses BeautifulSoup (lxml parser) with CSS selectors to find and
extract structured data from HTML.
"""

import logging
from typing import Any, Dict, List

from .base import BaseExtractor


class CssSelectorExtractor(BaseExtractor):
    """Extracts data from HTML using CSS selectors."""

    def extract(self, raw_data: Any) -> List[Dict[str, Any]]:
        """Extract items from HTML string or BeautifulSoup object."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logging.warning("BeautifulSoup not available for CSS extraction")
            return []

        if isinstance(raw_data, str):
            soup = BeautifulSoup(raw_data, "lxml")
        else:
            # Assume it's already parsed or convertible
            soup = BeautifulSoup(str(raw_data), "lxml")

        items = []

        # Determine the container selector from items_path or use defaults
        container_sel = self._config.items_path or "ul.list li, table tbody tr, .article-item, .news-item"
        containers = soup.select(container_sel)

        for el in containers:
            item = {}

            # Extract fields based on configured mappings
            for internal_name, source_field in self._config.fields.items():
                val = self._extract_field(el, source_field)
                if val is not None:
                    item[internal_name] = val

            # If no fields configured, extract all text from the element
            if not self._config.fields:
                # Try extracting links
                link = el.select_one("a")
                if link:
                    item["title"] = link.get_text(strip=True)
                    href = link.get("href", "")
                    item["url"] = href
                    # Try to extract date from the row text
                    text = el.get_text(strip=True)
                    item["text"] = text
                else:
                    item["text"] = el.get_text(strip=True)

            items.append(item)

        return items

    @staticmethod
    def _extract_field(el, source_field: str):
        """Extract a value from a BeautifulSoup element.

        Supports the following syntax:
        - ``"a"`` — get text content of first matching child element
        - ``"a@href"`` — get an attribute of a child element
        - ``"@data-id"`` — get an attribute of the container element itself
        - ``"td:last-child"`` — CSS pseudo-classes work on the selector part

        Returns the extracted value (stripped), or None if not found.
        """
        if not source_field:
            return None

        if '@' in source_field:
            # Attribute extraction
            parts = source_field.rsplit('@', 1)
            selector = parts[0] if parts[0] else None
            attr = parts[1]
            if selector:
                found = el.select_one(selector)
                if found:
                    val = found.get(attr, '')
                    return val.strip() if isinstance(val, str) else str(val) if val else ''
            else:
                # "@attr" means attribute on the container element itself
                val = el.get(attr, '')
                return val.strip() if isinstance(val, str) else str(val) if val else ''
            return None
        else:
            # Text content extraction
            found = el.select_one(source_field)
            if found:
                return found.get_text(strip=True)
            return None
