"""
Convert crawled markdown/content into format suitable for bid database tables.

- Markdown → HTML (for bid_project_detail.content_html)
- Structured field extraction from crawled items
- Date parsing for Chinese government procurement formats
"""

import html
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Markdown → HTML conversion
# ---------------------------------------------------------------------------

def markdown_to_html(md_text: str) -> str:
    """Convert Markdown text to basic HTML.

    Handles the common patterns found in crawled government procurement
    content: headings, bold, tables, lists, paragraphs, and links.
    """
    if not md_text:
        return ""

    lines = md_text.split("\n")
    result: List[str] = []
    in_table = False
    in_list: str = ""  # "" = no list, "ul" or "ol"

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Blank line handling
        if not stripped:
            if in_table:
                result.append("</tbody></table>")
                in_table = False
            if in_list:
                result.append(f"</{in_list}>")
                in_list = ""
            continue

        # Headings (# Title, ## Title, etc.)
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            if in_list:
                result.append(f"</{in_list}>")
                in_list = ""
            level = len(heading_match.group(1))
            content = _inline_format(heading_match.group(2))
            result.append(f"<h{level}>{content}</h{level}>")
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", stripped):
            if in_table:
                result.append("</tbody></table>")
                in_table = False
            result.append("<hr>")
            continue

        # Table row: | col1 | col2 | ... |
        if stripped.startswith("|") and "|" in stripped[1:]:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Skip separator rows like |---|---|
            if all(re.match(r"^[-:]+$", c) for c in cells if c.strip()):
                continue
            if not in_table:
                result.append("<table><tbody>")
                in_table = True
            cell_tag = "th" if (i == 0 or (i > 0 and not in_table)) else "td"
            row_cells = "".join(
                f"<{cell_tag}>{_inline_format(c)}</{cell_tag}>"
                for c in cells
            )
            result.append(f"<tr>{row_cells}</tr>")
            continue

        # Unordered list item
        if re.match(r"^[-*+]\s+", stripped):
            if not in_list:
                result.append("<ul>")
                in_list = "ul"
            content = _inline_format(re.sub(r"^[-*+]\s+", "", stripped))
            result.append(f"<li>{content}</li>")
            continue

        # Ordered list item
        if re.match(r"^\d+[.)]\s+", stripped):
            if not in_list:
                result.append("<ol>")
                in_list = "ol"
            content = _inline_format(re.sub(r"^\d+[.)]\s+", "", stripped))
            result.append(f"<li>{content}</li>")
            continue

        # Blockquote
        if stripped.startswith(">"):
            content = _inline_format(stripped[1:].strip())
            result.append(f"<blockquote>{content}</blockquote>")
            continue

        # Regular paragraph
        if in_list:
            # Close list before paragraph
            result.append(f"</{in_list}>")
            in_list = ""
        result.append(f"<p>{_inline_format(stripped)}</p>")

    # Close any open elements
    if in_table:
        result.append("</tbody></table>")
    if in_list:
        result.append(f"</{in_list}>")

    return "\n".join(result)


def _inline_format(text: str) -> str:
    """Handle inline markdown formatting: bold, italic, code, links."""
    # Escape HTML entities first (but not ones we'll add)
    text = html.escape(text, quote=False)

    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)

    # Italic: *text* or _text_
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)

    # Inline code: `code`
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)

    # Links: [text](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)

    return text


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日",
    "%Y.%m.%d",
    "%Y%m%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
]


def parse_date(date_str: Any) -> Optional[datetime]:
    """Parse date string in multiple formats, including Chinese formats."""
    if date_str is None:
        return None
    if isinstance(date_str, datetime):
        return date_str

    s = str(date_str).strip()
    if not s:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    # Try extracting date with regex: YYYY-MM-DD or YYYY/MM/DD
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Structured field extraction
# ---------------------------------------------------------------------------

# Fields that map from crawled item keys to bid_project columns
# Format: (bid_project_column, [possible_item_keys])
_PROJECT_FIELD_MAP = [
    ("publish_time", ["date", "publishDate", "publishTime", "pubDate", "publish_time", "createTime"]),
    ("content", ["content", "abstract", "summary", "description", "text"]),
    ("title", ["title", "name", "projectName", "project_name", "subject"]),
    ("project_class_id", ["projectClassID", "project_class_id", "category"]),
    ("purchase_type_id", ["purchaseTypeID", "purchase_type_id"]),
    ("project_money", ["projectMoney", "project_money", "budget", "amount"]),
    ("provice_code", ["proviceCode", "provice_code", "province"]),
    ("city_code", ["cityCode", "city_code"]),
    ("county_code", ["countyCode", "county_code"]),
    ("industry_codes", ["industryCodeList", "industry_codes", "industryCodes"]),
    ("part_a_names", ["partANameList", "part_a_names", "buyerName"]),
    ("part_b_names", ["partBNameList", "part_b_names", "supplierName"]),
    ("contract_end_date", ["contractEndDate", "contract_end_date"]),
    ("news_type_id", ["newsTypeID", "news_type_id", "type"]),
    ("source_type", ["sourceType", "source_type"]),
    ("has_file", ["hasFile", "has_file"]),
]


def extract_project_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract bid_project column values from a crawled item dict.

    Returns a dict suitable for BidProjectService.upsert_project().
    Does NOT include 'id' — caller must add that.
    """
    fields: Dict[str, Any] = {
        "source_type": "crawler",
    }

    for db_col, item_keys in _PROJECT_FIELD_MAP:
        for key in item_keys:
            val = item.get(key)
            if val is not None and val != "":
                if db_col == "publish_time":
                    dt = parse_date(val)
                    if dt:
                        fields[db_col] = dt
                elif db_col in ("industry_codes", "part_a_names", "part_b_names"):
                    # JSON fields — ensure list or JSON string
                    if isinstance(val, list):
                        import json
                        fields[db_col] = json.dumps(val, ensure_ascii=False)
                    elif isinstance(val, str):
                        fields[db_col] = val
                    else:
                        fields[db_col] = str(val)
                elif db_col == "has_file":
                    try:
                        fields[db_col] = int(val)
                    except (ValueError, TypeError):
                        pass
                else:
                    fields[db_col] = str(val)[:500] if db_col in ("title", "content") else str(val)
                break  # first match wins

    # Ensure title is set
    if "title" not in fields:
        fields["title"] = item.get("text", "Untitled")[:500]

    # Ensure content is set
    if "content" not in fields:
        content = item.get("content", "") or item.get("text", "") or ""
        fields["content"] = content[:2000] if content else ""

    return fields


def extract_detail_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract fields for bid_project_detail from a crawled item."""
    import json

    detail: Dict[str, Any] = {
        "fetched_at": datetime.now(),
    }

    # Content HTML
    content = item.get("content", "") or ""
    if content:
        # If content looks like HTML already, use it directly; otherwise convert
        if _looks_like_html(content):
            detail["content_html"] = content
        else:
            detail["content_html"] = markdown_to_html(content)

    # Structured fields
    for db_col, item_keys in [
        ("news_type_id", ["newsTypeID", "news_type_id"]),
        ("project_class_name", ["projectClassName", "project_class_name"]),
        ("purchase_type_id", ["purchaseTypeID", "purchase_type_id"]),
        ("industry_name", ["industryName", "industry_name"]),
        ("part_a_name", ["partAName", "part_a_name"]),
        ("part_b_name", ["partBName", "part_b_name"]),
        ("agent_name", ["agentName", "agent_name"]),
        ("project_money", ["projectMoney", "project_money"]),
        ("provice_code", ["proviceCode", "provice_code"]),
        ("city_code", ["cityCode", "city_code"]),
        ("county_code", ["countyCode", "county_code"]),
    ]:
        for key in item_keys:
            val = item.get(key)
            if val is not None and val != "":
                detail[db_col] = str(val)
                break

    return detail


def extract_file_attachments(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract file/attachment info from a crawled item.

    Looks for common attachment patterns in crawled data:
    - 'attachments' list
    - 'files' list
    - 'fileList' list
    - 'file' single dict
    - Individual file_* fields
    """
    files = []

    # Check for explicit attachment list fields
    for key in ("attachments", "files", "fileList", "appendixList"):
        val = item.get(key)
        if isinstance(val, list):
            for f in val:
                if isinstance(f, dict):
                    files.append(_normalize_file(f, item))

    # Check for single file dict
    if not files:
        for key in ("file", "attachment"):
            val = item.get(key)
            if isinstance(val, dict):
                files.append(_normalize_file(val, item))

    # Check for file_url/file_name fields at item level
    file_url = item.get("file_url") or item.get("fileUrl") or item.get("download_url")
    if file_url and not files:
        files.append({
            "file_name": item.get("file_name") or item.get("fileName") or "attachment",
            "file_url": file_url,
            "file_suffix": item.get("file_suffix") or item.get("fileSuffix") or "",
            "file_size": item.get("file_size") or item.get("fileSize"),
        })

    return files


def _normalize_file(f: Dict[str, Any], parent_item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a file dict to standard keys."""
    return {
        "file_name": f.get("name") or f.get("fileName") or f.get("file_name") or "attachment",
        "file_url": f.get("url") or f.get("fileUrl") or f.get("file_url") or f.get("downloadUrl") or "",
        "file_suffix": f.get("suffix") or f.get("fileSuffix") or f.get("file_suffix") or "",
        "file_size": f.get("size") or f.get("fileSize") or f.get("file_size"),
        "publish_time": parse_date(
            f.get("publishTime") or parent_item.get("publishTime") or parent_item.get("date")
        ),
    }


def _looks_like_html(text: str) -> bool:
    """Check if text appears to be HTML."""
    return bool(re.search(r"<\s*(html|div|p|table|span|a|br|ul|ol|li|h[1-6])\b", text, re.IGNORECASE))
