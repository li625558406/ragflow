"""
Markdown formatting engine for crawler output.

Renders item data into markdown using Jinja2-style templates with
per-site customization.  Supports:
- Template strings with {{ field_name }} placeholders
- Table formatting for structured fields
- Batch file output with separator
"""

import logging
import os
from typing import Any, Dict, List


class MarkdownFormatter:
    """Format crawled items into Markdown using templates."""

    def __init__(self, template: str = "", title_field: str = "title",
                 date_field: str = "date", parser_id: str = "naive"):
        self._template = template
        self._title_field = title_field
        self._date_field = date_field
        self._parser_id = parser_id

    def format_item(self, item: Dict[str, Any]) -> str:
        """Format a single item to Markdown string."""
        if self._template:
            return self._render_template(self._template, item)
        return self._default_format(item)

    def _render_template(self, template: str, item: Dict[str, Any]) -> str:
        """Simple {{ var }} template rendering (no Jinja2 dependency)."""
        result = template
        for key, value in item.items():
            placeholder = "{{ " + key + " }}"
            # Also support {{ key }} (without spaces)
            placeholder2 = "{{" + key + "}}"
            val_str = str(value) if value is not None else ""
            result = result.replace(placeholder, val_str)
            result = result.replace(placeholder2, val_str)
        return result

    def _default_format(self, item: Dict[str, Any]) -> str:
        """Default markdown format: h1 title + key-value table."""
        title = str(item.get(self._title_field, "") or item.get("title", "") or "Untitled")
        lines = [
            f"# {title}",
            "",
        ]

        # Date line
        date_val = item.get(self._date_field) or item.get("date") or item.get("publishDate") or ""
        if date_val:
            lines.append(f"**日期:** {date_val}")
            lines.append("")

        # Key-value table for remaining fields
        skip_keys = {self._title_field, self._date_field, "title", "date", "publishDate", "id", "uuid"}
        kv_fields = [(k, v) for k, v in item.items()
                     if k not in skip_keys and v is not None and str(v).strip()]

        if kv_fields:
            lines.append("| 字段 | 值 |")
            lines.append("|------|-----|")
            for k, v in kv_fields:
                # Clean up newlines in values for table
                clean_v = str(v).replace("\n", " ").replace("|", "\\|")
                lines.append(f"| {k} | {clean_v} |")
            lines.append("")

        # Content field (long text)
        content = item.get("content", "")
        if content and str(content).strip():
            lines.append(str(content))
            lines.append("")

        return "\n".join(lines)

    def format_batch(self, items: List[Dict[str, Any]], separator: str = "\n\n---\n\n") -> str:
        """Format multiple items, separated by the separator string."""
        return separator.join(self.format_item(item) for item in items)

    def write_batch(self, items: List[Dict[str, Any]], output_path: str,
                    separator: str = "\n\n---\n\n") -> None:
        """Format and write a batch of items to a file."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        content = self.format_batch(items, separator)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        logging.info("Formatter: wrote %d items to %s", len(items), output_path)
