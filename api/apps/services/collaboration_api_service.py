#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import base64
import html
import io
import logging
import os
import re

from werkzeug.security import generate_password_hash, check_password_hash

import settings
from api.db import DB, TenantPermission
from api.db.db_models import (
    CollaborationAttachment,
    CollaborationAuditLog,
    CollaborationComment,
    CollaborationDocument,
    CollaborationDocumentACL,
    CollaborationFolder,
    CollaborationFormatRule,
    CollaborationShareLink,
    UserTenant,
)
from api.db.services.collaboration_service import (
    CollaborationAttachmentService,
    CollaborationAuditLogService,
    CollaborationCommentService,
    CollaborationDocumentACLService,
    CollaborationDocumentService,
    CollaborationFolderService,
    CollaborationFormatRuleService,
    CollaborationShareLinkService,
)
from api.db.services.user_service import UserTenantService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


def _get_shared_tenant_user_ids(user_id: str) -> set:
    """Get the set of user IDs that share at least one tenant with the given user.

    In RAGFlow, team membership is modeled via UserTenant (user_id → tenant_id).
    Two users are on the same team if they share at least one tenant_id.
    """
    # Tenant IDs that the given user belongs to
    user_records = UserTenantService.query(user_id=user_id)
    tenant_ids = [t.tenant_id for t in user_records]
    if not tenant_ids:
        return set()
    # All user IDs belonging to those tenants (including the querying user)
    members = UserTenant.select(UserTenant.user_id).where(
        UserTenant.tenant_id.in_(tenant_ids)
    )
    return {m.user_id for m in members}


# ── Security Utilities ──

def sanitize_html(text: str) -> str:
    """Escape HTML + strip dangerous attributes. For comment/document content."""
    text = html.escape(text, quote=True)
    text = re.sub(r'javascript\s*:', '', text, flags=re.IGNORECASE)
    return text


def sanitize_filename(filename: str) -> str:
    """Keep only safe chars, prevent path traversal."""
    name = os.path.basename(filename)
    name = re.sub(r'[^\w\.\-]', '_', name)
    return name or 'untitled'


ALLOWED_UPLOAD_MIMES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'application/pdf', 'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain', 'text/csv',
}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB


def validate_upload(file_obj) -> tuple[bytes, str, str]:
    """Validate uploaded file: MIME whitelist, size limit, filename sanitization.

    Returns (data, safe_filename, mime_type).
    Raises ValueError on validation failure.
    """
    mime_type = file_obj.content_type or 'application/octet-stream'
    if mime_type not in ALLOWED_UPLOAD_MIMES:
        raise ValueError(f"Unsupported file type: {mime_type}")

    # Pre-check Content-Length to reject oversize files before reading
    cl = file_obj.content_length
    if cl is not None and cl > MAX_UPLOAD_SIZE:
        raise ValueError(f"File too large (max 50MB)")

    data = file_obj.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValueError(f"File too large (max 50MB)")

    safe_name = sanitize_filename(file_obj.filename or 'untitled')
    return data, safe_name, mime_type


async def log_audit(user_id: str, document_id: str | None, action: str, detail: dict = None, ip_address: str = None):
    """Record an audit log entry. Fire-and-forget — failures are logged but not raised."""
    try:
        CollaborationAuditLogService.save(
            id=get_uuid(),
            user_id=user_id,
            document_id=document_id,
            action=action,
            detail=detail or {},
            ip_address=ip_address,
            create_time=current_timestamp(),
        )
    except Exception as e:
        logging.warning(f"Audit log failed for {action}: {e}")
ROLE_HIERARCHY = {
    "owner": 4,
    "editor": 3,
    "viewer": 2,
    "commenter": 1,
}


def _get_user_role(doc_id: str, user_id: str) -> str | None:
    """Get the effective role of a user on a document, or None if no access.

    Priority: ACL entry > owner by created_by > team permission fallback
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        return None

    # Owner always has owner role
    if doc.created_by == user_id:
        return "owner"

    # Check ACL for explicit grant
    acl = CollaborationDocumentACLService.query(document_id=doc_id, user_id=user_id)
    if acl:
        return acl[0].role

    # Team permission fallback: team-visible docs → viewer role
    if doc.permission == TenantPermission.TEAM.value:
        team_user_ids = _get_shared_tenant_user_ids(user_id)
        if doc.created_by in team_user_ids:
            return "viewer"

    return None


def _check_access(obj, user_id: str) -> bool:
    """Check if user has access to a document/format-rule/folder, following agent team permission model.

    For objects with a 'permission' field (documents, format rules), respects the permission setting.
    For objects without a 'permission' field (folders), allows team members access.
    """
    if obj.created_by == user_id:
        return True
    perm = getattr(obj, 'permission', None)
    if perm is not None and perm != TenantPermission.TEAM.value:
        return False
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    return obj.created_by in team_user_ids


def _check_role(doc_id: str, user_id: str, required_role: str) -> bool:
    """Check if user has at least the required role on a document."""
    role = _get_user_role(doc_id, user_id)
    if role is None:
        return False
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


def _cell_text_children(cell_text: str) -> list[dict]:
    """Build Lexical text children for a single table cell."""
    text_children = []
    for seg in _parse_inline_markdown(cell_text):
        fmt = 0
        if seg["bold"]:
            fmt |= 1   # IS_BOLD
        if seg["italic"]:
            fmt |= 2   # IS_ITALIC
        if seg["strike"]:
            fmt |= 4   # IS_STRIKETHROUGH
        if seg["code"]:
            fmt |= 16  # IS_CODE
        text_children.append({
            "detail": 0, "format": fmt, "mode": "normal",
            "style": "", "text": seg["text"], "type": "text", "version": 1,
        })
    return text_children


def _build_lexical_table(rows: list[list[str]]) -> dict:
    """Build a Lexical table node from markdown table rows (cell strings)."""
    row_nodes = []
    for row_cells in rows:
        cell_nodes = []
        for cell_text in row_cells:
            cell_nodes.append({
                "type": "tablecell",
                "children": [{
                    "children": _cell_text_children(cell_text),
                    "direction": "ltr", "format": "", "indent": 0,
                    "type": "paragraph", "version": 1,
                }],
                "direction": "ltr", "format": "", "indent": 0,
                "headerState": 0, "colSpan": 1, "rowSpan": 1,
                "version": 1,
            })
        row_nodes.append({
            "type": "tablerow",
            "children": cell_nodes,
            "direction": "ltr", "format": "", "indent": 0,
            "version": 1,
        })
    return {
        "type": "table",
        "children": row_nodes,
        "direction": "ltr", "format": "", "indent": 0,
        "version": 1,
    }


def _markdown_to_lexical_json(markdown_content: str) -> dict:
    """Convert markdown content to Lexical editor JSON state.

    Parses block-level (headings, lists, tables, quotes, callouts) and
    inline formatting (bold, italic, code, strikethrough) into Lexical nodes.
    """
    if not markdown_content:
        return {"root": {"children": [{"children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "", "type": "text", "version": 1}], "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1}], "direction": "ltr", "format": "", "indent": 0, "type": "root", "version": 1}}

    lines = markdown_content.strip().split("\n")
    children = []
    table_buffer = []
    callout_type = None
    callout_lines = []
    code_block_lang = None
    code_block_lines = []

    for line in lines:
        stripped = line.strip()

        # ── Callout: inside a callout block ──
        if callout_type is not None:
            if stripped == ":::":
                _flush_callout(children, callout_type, callout_lines)
                callout_type = None
                callout_lines = []
                continue
            callout_lines.append(stripped)
            continue

        # ── Code block: inside a code block ──
        if code_block_lang is not None:
            if stripped == "```":
                _flush_code_block(children, code_block_lang, code_block_lines)
                code_block_lang = None
                code_block_lines = []
                continue
            code_block_lines.append(line)
            continue

        # ── Callout: start detection (:::type emoji) ──
        m = re.match(r'^:::(info|warning|tip|danger)\s', stripped)
        if m:
            callout_type = m.group(1)
            continue

        # ── Code block: start detection ``` ──
        cm = re.match(r'^```(\w*)$', stripped)
        if cm:
            code_block_lang = cm.group(1) or ''
            continue

        # ── Table detection ──
        if _is_md_table_row(stripped):
            if _is_md_table_separator(stripped):
                continue
            table_buffer.append(_parse_md_table_row(stripped))
            continue

        # Not a table row — flush buffered table first
        if table_buffer:
            children.append(_build_lexical_table(table_buffer))
            table_buffer = []

        # ── Empty / blank lines ──
        if not stripped:
            children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "\u00A0", "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
            })
            continue

        # ── Image detection (standalone image block) ──
        img_m = re.match(r'^!\[(.*)\]\((.+)\)$', stripped)
        if img_m:
            children.append({
                "altText": img_m.group(1), "src": img_m.group(2),
                "width": 0, "height": 0,
                "type": "image", "version": 1,
            })
            continue

        # Check for heading
        heading_tag = None
        body = stripped
        if stripped.startswith("### "):
            heading_tag, body = "h3", stripped[4:]
        elif stripped.startswith("## "):
            heading_tag, body = "h2", stripped[3:]
        elif stripped.startswith("# "):
            heading_tag, body = "h1", stripped[2:]

        # Check for quote
        is_quote = False
        if not heading_tag and stripped.startswith("> "):
            is_quote = True
            body = stripped[2:]

        # Check for list items
        list_type = None
        list_checked = None
        if not heading_tag and not is_quote:
            if stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
                list_type, list_checked, body = "check", True, stripped[6:]
            elif stripped.startswith("- [ ] "):
                list_type, list_checked, body = "check", False, stripped[6:]
            elif stripped.startswith("- "):
                list_type, body = "bullet", stripped[2:]
            elif stripped.startswith("1. "):
                list_type, body = "number", stripped[3:]

        # Build text children with inline formatting
        text_children = _cell_text_children(body)

        if heading_tag:
            children.append({
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "tag": heading_tag, "type": "heading", "version": 1,
            })
        elif list_type:
            listitem = {
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "type": "listitem", "version": 1,
            }
            if list_checked is not None:
                listitem["checked"] = list_checked
            children.append({
                "children": [listitem],
                "direction": "ltr", "format": "", "indent": 0,
                "listType": list_type, "type": "list", "version": 1,
            })
        elif is_quote:
            children.append({
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "type": "quote", "version": 1,
            })
        else:
            children.append({
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "type": "paragraph", "version": 1,
            })

    # Flush remaining buffers
    if callout_type is not None:
        _flush_callout(children, callout_type, callout_lines)
    if code_block_lang is not None:
        _flush_code_block(children, code_block_lang, code_block_lines)
    if table_buffer:
        children.append(_build_lexical_table(table_buffer))

    return {"root": {"children": children, "direction": "ltr", "format": "", "indent": 0, "type": "root", "version": 1}}


def _flush_code_block(children: list, language: str, code_lines: list):
    """Build a Lexical code node from accumulated lines and append to children."""
    code_children = []
    for cl in code_lines:
        code_children.append({
            "detail": 0, "format": 0, "mode": "normal",
            "style": "", "text": cl + "\n", "type": "code-highlight", "version": 1,
        })
    if not code_children:
        code_children.append({
            "detail": 0, "format": 0, "mode": "normal",
            "style": "", "text": "", "type": "code-highlight", "version": 1,
        })
    children.append({
        "children": code_children,
        "direction": "ltr", "format": "", "indent": 0,
        "type": "code",
        "language": language,
        "version": 1,
    })


def _flush_callout(children: list, callout_type: str, callout_lines: list):
    """Build a Lexical callout node from accumulated lines and append to children."""
    callout_children = []
    for cl in callout_lines:
        txt = cl.strip()
        if not txt:
            callout_children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "\u00A0", "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
            })
        else:
            callout_children.append({
                "children": _cell_text_children(txt),
                "direction": "ltr", "format": "", "indent": 0,
                "type": "paragraph", "version": 1,
            })
    # Always have at least one paragraph child
    if not callout_children:
        callout_children.append({
            "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "", "type": "text", "version": 1}],
            "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
        })
    children.append({
        "children": callout_children,
        "direction": "ltr", "format": "", "indent": 0,
        "type": "callout",
        "calloutType": callout_type,
        "version": 1,
    })


def _parse_css_style(style_str: str) -> dict:
    """Parse CSS style string into a dict of property→value."""
    result = {}
    if not style_str:
        return result
    for part in style_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            key, value = part.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def _lexical_parse_format(format_val) -> int:
    """Parse Lexical format value (may be str or int) into int bitmask."""
    if isinstance(format_val, int):
        return format_val
    if isinstance(format_val, str):
        try:
            return int(format_val)
        except (ValueError, TypeError):
            return 0
    return 0


def _add_styled_docx_run(paragraph, text_node: dict):
    """Add a styled text run to a python-docx paragraph from a Lexical text node."""
    text = text_node.get("text", "")
    if not text:
        return

    run = paragraph.add_run(text)
    styles = _parse_css_style(text_node.get("style", ""))

    if "font-family" in styles:
        run.font.name = styles["font-family"]
    if "font-size" in styles:
        try:
            from docx.shared import Pt
            size_val = float(styles["font-size"].replace("pt", "").strip())
            run.font.size = Pt(size_val)
        except (ValueError, TypeError):
            pass
    if "color" in styles:
        color = styles["color"]
        if color.startswith("#") and len(color) == 7:
            from docx.shared import RGBColor
            try:
                r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
                run.font.color.rgb = RGBColor(r, g, b)
            except ValueError:
                pass

    fmt = _lexical_parse_format(text_node.get("format", 0))
    if fmt & 1:    # IS_BOLD
        run.bold = True
    if fmt & 2:    # IS_ITALIC
        run.italic = True
    if fmt & 8:    # IS_UNDERLINE
        run.underline = True
    if fmt & 4:    # IS_STRIKETHROUGH
        run.font.strike = True
    if fmt & 16:   # IS_CODE
        run.font.name = "Courier New"
    if fmt & 32:   # IS_SUBSCRIPT
        run.font.subscript = True
    if fmt & 64:   # IS_SUPERSCRIPT
        run.font.superscript = True


def _iter_lexical_blocks(root_children: list):
    """Yield (block_type, tag_or_listtype, alignment, texts) for each renderable block."""
    for block in root_children:
        block_type = block.get("type", "paragraph")
        block_format = block.get("format", "")
        alignment = block_format if block_format in ("left", "center", "right", "justify") else None
        tag = block.get("tag", "")

        if block_type == "table":
            rows = []
            for row_node in block.get("children", []):
                if row_node.get("type") != "tablerow":
                    continue
                cells = []
                for cell_node in row_node.get("children", []):
                    if cell_node.get("type") != "tablecell":
                        continue
                    texts = [c for c in cell_node.get("children", []) if c.get("type") == "text"]
                    cells.append(texts)
                rows.append(cells)
            yield ("table", None, None, rows, None)
        elif block_type == "list":
            list_type = block.get("listType", "bullet")
            for item in block.get("children", []):
                if item.get("type") != "listitem":
                    continue
                checked = item.get("checked", None)
                texts = [c for c in item.get("children", []) if c.get("type") == "text"]
                yield ("listitem", list_type, alignment, texts, checked)
        elif block_type == "callout":
            # Callout has paragraph children; flatten all text nodes
            all_texts = []
            for child in block.get("children", []):
                all_texts.extend([c for c in child.get("children", []) if c.get("type") == "text"])
            yield ("callout", block.get("calloutType", "info"), alignment, all_texts, None)
        elif block_type == "code":
            # Code block: collect text from code-highlight children
            code_texts = []
            for child in block.get("children", []):
                if child.get("type") == "code-highlight":
                    code_texts.append(child)
            yield ("code", block.get("language", ""), alignment, code_texts, None)
        elif block_type == "image":
            yield ("image", None, alignment, [{"text": block.get("altText", ""), "src": block.get("src", "")}], None)
        else:
            texts = [c for c in block.get("children", []) if c.get("type") == "text"]
            yield (block_type, tag, alignment, texts, None)


def _generate_docx(content, format_config: dict = None) -> bytes:
    """Generate a .docx file from Lexical JSON content (or markdown string fallback)."""
    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        logging.error("python-docx not installed")
        return b""

    cfg = format_config or {}
    doc = DocxDocument()

    # Default styles
    style = doc.styles["Normal"]
    font = style.font
    font.name = cfg.get("font_name", "SimSun")
    font.size = Pt(cfg.get("font_size", 12))
    pf = style.paragraph_format
    pf.line_spacing = cfg.get("line_spacing", 1.5)

    # Margins
    margins = cfg.get("margins", {})
    for section in doc.sections:
        section.top_margin = Inches(margins.get("top", 1.0))
        section.bottom_margin = Inches(margins.get("bottom", 1.0))
        section.left_margin = Inches(margins.get("left", 1.0))
        section.right_margin = Inches(margins.get("right", 1.0))

    # Determine whether we have Lexical JSON or plain markdown
    root_children = None
    if isinstance(content, dict) and content.get("root"):
        root_children = content["root"].get("children", [])
    elif isinstance(content, str):
        # Fallback: plain markdown
        return _generate_docx_markdown(content, cfg)

    if not root_children:
        doc.add_paragraph("")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    alignment_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    heading_style_map = {"h1": "Heading 1", "h2": "Heading 2", "h3": "Heading 3"}

    for block_type, tag_or_listtype, alignment, texts, checked in _iter_lexical_blocks(root_children):
        # ── Table ──
        if block_type == "table":
            rows_data = texts  # list[list[list[dict]]]: rows × cells × text_nodes
            if not rows_data or not rows_data[0]:
                continue
            cols = len(rows_data[0])
            total_rows = len(rows_data)
            table = doc.add_table(rows=total_rows, cols=cols, style="Table Grid")
            # Header row (first row)
            for j, cell_texts in enumerate(rows_data[0]):
                cell = table.cell(0, j)
                cell_paragraph = cell.paragraphs[0]
                for text_node in cell_texts:
                    run = cell_paragraph.add_run(text_node.get("text", ""))
                    run.bold = True
            # Data rows
            for i, row_cells in enumerate(rows_data[1:]):
                for j, cell_texts in enumerate(row_cells):
                    if j < cols:
                        cell = table.cell(i + 1, j)
                        cell_paragraph = cell.paragraphs[0]
                        cell_paragraph.clear()
                        for text_node in cell_texts:
                            _add_styled_docx_run(cell_paragraph, text_node)
            doc.add_paragraph("")  # spacer after table
            continue

        # ── Paragraphs / Headings / Lists ──
        if block_type == "listitem":
            if tag_or_listtype == "check":
                # Checklist items with checkmark prefix
                p = doc.add_paragraph(style="List Bullet")
                prefix = "☑ " if checked else "☐ "
                run = p.add_run(prefix)
                run.font.name = "Segoe UI Symbol"
            elif tag_or_listtype == "bullet":
                p = doc.add_paragraph(style="List Bullet")
            else:
                p = doc.add_paragraph(style="List Number")
        elif block_type == "heading":
            style_name = heading_style_map.get(tag_or_listtype, "Heading 2")
            p = doc.add_paragraph()
            p.style = doc.styles[style_name]
        elif block_type == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.5)
            # Quote text in italic
            for text_node in texts:
                run = p.add_run(text_node.get("text", ""))
                run.italic = True
            continue
        elif block_type == "callout":
            p = doc.add_paragraph()
            emoji_map = {"info": "\U0001F4A1", "warning": "\u26A0\uFE0F", "tip": "\u2705", "danger": "\U0001F6AB"}
            prefix = emoji_map.get(tag_or_listtype, "")
            if prefix:
                p.add_run(prefix + " ")
            for text_node in texts:
                _add_styled_docx_run(p, text_node)
            continue
        elif block_type == "code":
            # Render code block in monospace font with light background
            p = doc.add_paragraph()
            lang_label = f"[{tag_or_listtype}] " if tag_or_listtype else ""
            if lang_label:
                run = p.add_run(lang_label)
                run.bold = True
                run.font.size = Pt(8)
            for text_node in texts:
                run = p.add_run(text_node.get("text", ""))
                run.font.name = "Courier New"
                run.font.size = Pt(9)
            continue
        elif block_type == "image":
            # Image: add a placeholder paragraph
            alt = texts[0].get("text", "") if texts else ""
            src = texts[0].get("src", "") if texts else ""
            p = doc.add_paragraph()
            run = p.add_run(f"[Image: {alt or src}]")
            run.italic = True
            run.font.color.rgb = RGBColor(150, 150, 150)
            continue
        else:
            p = doc.add_paragraph()

        if alignment and alignment in alignment_map:
            p.alignment = alignment_map[alignment]

        for text_node in texts:
            _add_styled_docx_run(p, text_node)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _is_md_table_row(line: str) -> bool:
    """Check if a line is a markdown table row (starts and ends with |)."""
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _is_md_table_separator(line: str) -> bool:
    """Check if a line is a markdown table separator (|---|:---|)."""
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    # Separator cells contain only :, -, spaces
    cells = [c.strip() for c in s[1:-1].split("|")]
    return all(re.match(r"^:?-+:?$", c) for c in cells if c or True)


def _parse_md_table_row(line: str) -> list[str]:
    """Parse a markdown table row into a list of cell values."""
    s = line.strip()
    cells = s[1:-1].split("|")
    return [c.strip() for c in cells]


def _set_cell_markdown(cell, text: str, bold_all: bool = False):
    """Set table cell text with inline markdown formatting."""
    # Clear default empty paragraph
    p = cell.paragraphs[0]
    p.clear()
    for seg in _parse_inline_markdown(text):
        run = p.add_run(seg["text"])
        if bold_all or seg["bold"]:
            run.bold = True
        if seg["italic"]:
            run.italic = True
        if seg["code"]:
            run.font.name = "Courier New"
        if seg["strike"]:
            run.font.strike = True


def _flush_docx_table(doc, table_rows: list[list[str]]):
    """Write accumulated table rows into a python-docx table."""
    if not table_rows:
        return
    headers = table_rows[0]
    data = table_rows[1:] if len(table_rows) > 1 else []
    cols = len(headers)
    rows = 1 + len(data)
    table = doc.add_table(rows=rows, cols=cols, style="Table Grid")
    # Header row — all runs bold
    for j, h in enumerate(headers):
        _set_cell_markdown(table.cell(0, j), h, bold_all=True)
    # Data rows
    for i, row_cells in enumerate(data):
        for j, val in enumerate(row_cells):
            if j < cols:
                _set_cell_markdown(table.cell(i + 1, j), val)
    doc.add_paragraph("")  # spacer after table


def _parse_inline_markdown(text: str) -> list[dict]:
    """Parse inline markdown formatting.

    Returns a list of dicts with keys: text, bold, italic, code, strike.
    Handles ``**bold**``, ``*italic*``, `` `code` ``, ``~~strike~~``.
    """
    segments: list[dict] = []
    i = 0
    buf = ""

    while i < len(text):
        # ── **bold** ──
        if i + 1 < len(text) and text[i : i + 2] == "**":
            end = text.find("**", i + 2)
            if end != -1:
                if buf:
                    segments.append({"text": buf, "bold": False, "italic": False, "code": False, "strike": False})
                    buf = ""
                for seg in _parse_inline_markdown(text[i + 2 : end]):
                    seg["bold"] = True
                    segments.append(seg)
                i = end + 2
                continue
        # ── ~~strikethrough~~ ──
        if i + 1 < len(text) and text[i : i + 2] == "~~":
            end = text.find("~~", i + 2)
            if end != -1:
                if buf:
                    segments.append({"text": buf, "bold": False, "italic": False, "code": False, "strike": False})
                    buf = ""
                for seg in _parse_inline_markdown(text[i + 2 : end]):
                    seg["strike"] = True
                    segments.append(seg)
                i = end + 2
                continue
        # ── *italic* ──
        if text[i] == "*":
            end = text.find("*", i + 1)
            if end != -1:
                if buf:
                    segments.append({"text": buf, "bold": False, "italic": False, "code": False, "strike": False})
                    buf = ""
                for seg in _parse_inline_markdown(text[i + 1 : end]):
                    seg["italic"] = True
                    segments.append(seg)
                i = end + 1
                continue
        # ── `code` ──
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1:
                if buf:
                    segments.append({"text": buf, "bold": False, "italic": False, "code": False, "strike": False})
                    buf = ""
                segments.append({"text": text[i + 1 : end], "bold": False, "italic": False, "code": True, "strike": False})
                i = end + 1
                continue
        buf += text[i]
        i += 1

    if buf:
        segments.append({"text": buf, "bold": False, "italic": False, "code": False, "strike": False})
    return segments


def _add_markdown_paragraph(doc, text: str, style_name: str = None):
    """Add a paragraph with inline markdown formatting as styled runs."""
    p = doc.add_paragraph()
    if style_name:
        p.style = doc.styles[style_name]
    else:
        # When no style given, clear the default empty run added by add_paragraph()
        pass
    # Clear default empty run
    p.clear()

    for seg in _parse_inline_markdown(text):
        run = p.add_run(seg["text"])
        if seg["bold"]:
            run.bold = True
        if seg["italic"]:
            run.italic = True
        if seg["code"]:
            run.font.name = "Courier New"
        if seg["strike"]:
            run.font.strike = True


def _generate_docx_markdown(markdown_content: str, cfg: dict) -> bytes:
    """Generate .docx from plain markdown string with inline formatting.

    Handles: headings, lists, tables, quotes, checklists,
             code blocks, callouts, and images.
    """

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches, RGBColor
    except ImportError:
        return b""

    doc = DocxDocument()

    font_name = cfg.get("font_name", "SimSun")
    font_size = cfg.get("font_size", 12)
    style = doc.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(font_size)
    style.paragraph_format.line_spacing = cfg.get("line_spacing", 1.5)

    margins = cfg.get("margins", {})
    for section in doc.sections:
        section.top_margin = Inches(margins.get("top", 1.0))
        section.bottom_margin = Inches(margins.get("bottom", 1.0))
        section.left_margin = Inches(margins.get("left", 1.0))
        section.right_margin = Inches(margins.get("right", 1.0))

    if not markdown_content:
        doc.add_paragraph("")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    lines = markdown_content.strip().split("\n")
    table_buffer = []
    code_block_lang = None
    code_block_lines = []
    callout_type = None
    callout_lines = []
    emoji_map = {"info": "\U0001F4A1", "warning": "\u26A0\uFE0F", "tip": "\u2705", "danger": "\U0001F6AB"}

    def _flush_table():
        nonlocal table_buffer
        if table_buffer:
            _flush_docx_table(doc, table_buffer)
            table_buffer = []

    def _flush_code_block():
        nonlocal code_block_lang, code_block_lines
        if not code_block_lines:
            code_block_lang = None
            code_block_lines = []
            return
        for cl in code_block_lines:
            p = doc.add_paragraph()
            p.clear()
            run = p.add_run(cl)
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        code_block_lang = None
        code_block_lines = []

    def _flush_callout():
        nonlocal callout_type, callout_lines
        if not callout_lines:
            callout_type = None
            callout_lines = []
            return
        emoji = emoji_map.get(callout_type or "", "")
        for cl in callout_lines:
            p = doc.add_paragraph()
            p.clear()
            run = p.add_run(emoji + " " + cl)
        callout_type = None
        callout_lines = []

    for line in lines:
        stripped = line.strip()

        # ── Inside callout block ──
        if callout_type is not None:
            if stripped == ":::":
                _flush_callout()
                continue
            callout_lines.append(stripped)
            continue

        # ── Inside code block ──
        if code_block_lang is not None:
            if stripped == "```":
                _flush_code_block()
                continue
            code_block_lines.append(line)
            continue

        # ── Callout start ──
        if re.match(r'^:::(info|warning|tip|danger)\s', stripped):
            callout_type = re.match(r'^:::(info|warning|tip|danger)', stripped).group(1)
            continue

        # ── Code block start ──
        cm = re.match(r'^```(\w*)$', stripped)
        if cm:
            code_block_lang = cm.group(1) or ''
            continue

        # ── Table ──
        if _is_md_table_row(stripped):
            _flush_table()
            if _is_md_table_separator(stripped):
                continue
            table_buffer.append(_parse_md_table_row(stripped))
            continue

        _flush_table()

        if not stripped:
            doc.add_paragraph("")
            continue

        # ── Image ──
        img_m = re.match(r'^!\[(.*)\]\((.+)\)$', stripped)
        if img_m:
            p = doc.add_paragraph()
            run = p.add_run(f"[Image: {img_m.group(1) or img_m.group(2)}]")
            run.italic = True
            run.font.color.rgb = RGBColor(150, 150, 150)
            continue

        # ── Headings ──
        if stripped.startswith("### "):
            _add_markdown_paragraph(doc, stripped[4:], "Heading 3")
        elif stripped.startswith("## "):
            _add_markdown_paragraph(doc, stripped[3:], "Heading 2")
        elif stripped.startswith("# "):
            _add_markdown_paragraph(doc, stripped[2:], "Heading 1")
        # ── Quote ──
        elif stripped.startswith("> "):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.5)
            for seg in _parse_inline_markdown(stripped[2:]):
                run = p.add_run(seg["text"])
                run.italic = True
                if seg["bold"]:
                    run.bold = True
        # ── Checklist ──
        elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            p = doc.add_paragraph(style="List Bullet")
            prefix = "\u2611 "  # ☑
            run = p.add_run(prefix)
            run.font.name = "Segoe UI Symbol"
            for seg in _parse_inline_markdown(stripped[6:]):
                run2 = p.add_run(seg["text"])
                if seg["bold"]: run2.bold = True
                if seg["italic"]: run2.italic = True
        elif stripped.startswith("- [ ] "):
            p = doc.add_paragraph(style="List Bullet")
            prefix = "\u2610 "  # ☐
            run = p.add_run(prefix)
            run.font.name = "Segoe UI Symbol"
            for seg in _parse_inline_markdown(stripped[6:]):
                run2 = p.add_run(seg["text"])
                if seg["bold"]: run2.bold = True
                if seg["italic"]: run2.italic = True
        # ── Lists ──
        elif stripped.startswith("- "):
            _add_markdown_paragraph(doc, stripped[2:], "List Bullet")
        elif stripped.startswith("1. "):
            _add_markdown_paragraph(doc, stripped[3:], "List Number")
        else:
            _add_markdown_paragraph(doc, stripped)

    # Flush remaining buffers
    _flush_table()
    _flush_code_block()
    _flush_callout()

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _escape_xml(text: str) -> str:
    """Escape text for use inside reportlab XML Paragraph tags."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_pdf_inline_markup(text_node: dict) -> str:
    """Build reportlab XML markup for a single Lexical text node."""
    text = _escape_xml(text_node.get("text", ""))
    if not text:
        return ""

    styles = _parse_css_style(text_node.get("style", ""))
    fmt = _lexical_parse_format(text_node.get("format", 0))

    font_name = styles.get("font-family", "")
    font_size = styles.get("font-size", "")
    font_color = styles.get("color", "")

    # Build XML tags
    font_attrs = ""
    if font_name:
        font_attrs += f' face="{font_name}"'
    if font_size:
        size_pt = font_size.replace("pt", "").strip()
        font_attrs += f' size="{size_pt}"'
    if font_color:
        font_attrs += f' color="{font_color}"'

    result = text
    if font_attrs:
        result = f"<font{font_attrs}>{result}</font>"
    if fmt & 1:   # IS_BOLD
        result = f"<b>{result}</b>"
    if fmt & 2:   # IS_ITALIC
        result = f"<i>{result}</i>"
    if fmt & 8:   # IS_UNDERLINE
        result = f"<u>{result}</u>"
    if fmt & 4:   # IS_STRIKETHROUGH
        result = f"<strike>{result}</strike>"
    if fmt & 32:  # IS_SUBSCRIPT
        result = f"<sub>{result}</sub>"
    if fmt & 64:  # IS_SUPERSCRIPT
        result = f"<super>{result}</super>"

    return result


def _generate_pdf(content, format_config: dict = None) -> bytes:
    """Generate a PDF file from Lexical JSON content (or markdown string fallback)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
    except ImportError:
        logging.error("reportlab not installed")
        return b""

    cfg = format_config or {}
    font_name = cfg.get("font_name", "Helvetica")
    font_size = cfg.get("font_size", 12)
    line_spacing = cfg.get("line_spacing", 1.5)
    margins = cfg.get("margins", {})

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=inch * margins.get("left", 1.0),
                            rightMargin=inch * margins.get("right", 1.0),
                            topMargin=inch * margins.get("top", 1.0),
                            bottomMargin=inch * margins.get("bottom", 1.0))

    styles = getSampleStyleSheet()

    def _make_style(name, parent, **kwargs):
        return ParagraphStyle(name, parent=styles[parent],
                              fontName=kwargs.get("fontName", font_name),
                              fontSize=kwargs.get("fontSize", font_size),
                              leading=kwargs.get("leading", font_size * line_spacing),
                              alignment=kwargs.get("alignment", TA_LEFT))

    story = []

    # Determine whether we have Lexical JSON or plain markdown
    root_children = None
    if isinstance(content, dict) and content.get("root"):
        root_children = content["root"].get("children", [])
    elif isinstance(content, str):
        return _generate_pdf_markdown(content, cfg)

    if not root_children:
        story.append(Paragraph("", _make_style("CustomNormal", "Normal")))
    else:
        alignment_map = {
            "left": TA_LEFT,
            "center": TA_CENTER,
            "right": TA_RIGHT,
            "justify": TA_JUSTIFY,
        }

        for block_type, tag_or_listtype, alignment, texts, checked in _iter_lexical_blocks(root_children):
            para_alignment = alignment_map.get(alignment, TA_LEFT) if alignment else TA_LEFT

            # ── Table ──
            if block_type == "table":
                rows_data = texts  # list[list[list[dict]]]: rows × cells × text_nodes
                if not rows_data or not rows_data[0]:
                    continue
                n_cols = len(rows_data[0])
                table_data = []
                for row_cells in rows_data:
                    row_data = []
                    for cell_texts in row_cells:
                        cell_markup = "".join(_build_pdf_inline_markup(t) for t in cell_texts)
                        cell_style = _make_style("TmpCell", "Normal")
                        row_data.append(Paragraph(cell_markup, cell_style))
                    table_data.append(row_data)
                from reportlab.platypus import Table as PdfTable, TableStyle as PdfTableStyle
                from reportlab.lib import colors as pdf_colors
                page_width = A4[0] - inch * (margins.get("left", 1.0) + margins.get("right", 1.0))
                col_w = min(page_width / max(n_cols, 1), inch * 2.0)
                tbl = PdfTable(table_data, colWidths=[col_w] * n_cols)
                tbl.setStyle(PdfTableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, pdf_colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), pdf_colors.HexColor("#E0E0E0")),
                    ("FONTSIZE", (0, 0), (-1, -1), max(font_size - 2, 8)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(tbl)
                story.append(Spacer(1, font_size * 0.5))
                continue

            # ── Paragraphs / Headings / Lists ──
            if block_type == "listitem":
                if tag_or_listtype == "check":
                    bullet = "\u2611 " if checked else "\u2610 "  # ☑ or ☐
                elif tag_or_listtype == "bullet":
                    bullet = "\u2022 "
                else:
                    bullet = "1. "
                markup_parts = [bullet] + [_build_pdf_inline_markup(t) for t in texts]
                para_style = _make_style("CustomList", "Normal")
                story.append(Paragraph("".join(markup_parts), para_style))
            elif block_type == "heading":
                tag = tag_or_listtype
                heading_size = {"h1": font_size + 6, "h2": font_size + 4, "h3": font_size + 2}.get(tag, font_size + 4)
                heading_parent = {"h1": "Heading1", "h2": "Heading2", "h3": "Heading3"}.get(tag, "Heading2")
                para_style = _make_style(f"Custom{tag.upper()}", heading_parent,
                                         fontSize=heading_size, alignment=para_alignment)
                markup = "".join(_build_pdf_inline_markup(t) for t in texts)
                story.append(Paragraph(markup, para_style))
            elif block_type == "quote":
                para_style = _make_style("CustomQuote", "Normal", alignment=para_alignment)
                para_style.leftIndent = 20
                markup = "".join(_build_pdf_inline_markup(t) for t in texts)
                story.append(Paragraph(f"<i>{markup}</i>", para_style))
            elif block_type == "callout":
                emoji_map = {"info": "\U0001F4A1", "warning": "\u26A0\uFE0F", "tip": "\u2705", "danger": "\U0001F6AB"}
                prefix = emoji_map.get(tag_or_listtype, "")
                para_style = _make_style("CustomCallout", "Normal", alignment=para_alignment)
                markup = prefix + " " + "".join(_build_pdf_inline_markup(t) for t in texts)
                story.append(Paragraph(markup, para_style))
            elif block_type == "code":
                code_style = _make_style("CustomCode", "Normal",
                                         fontName="Courier", fontSize=font_size - 2)
                for text_node in texts:
                    code_text = _escape_xml(text_node.get("text", ""))
                    story.append(Paragraph(f"<font face='Courier' size='{font_size - 2}'>{code_text}</font>", code_style))
            elif block_type == "image":
                alt = texts[0].get("text", "") if texts else ""
                src = texts[0].get("src", "") if texts else ""
                para_style = _make_style("CustomImage", "Normal", alignment=para_alignment)
                story.append(Paragraph(f"<i>[Image: {_escape_xml(alt or src)}]</i>", para_style))
            else:
                para_style = _make_style("CustomNormal", "Normal", alignment=para_alignment)
                if texts:
                    markup = "".join(_build_pdf_inline_markup(t) for t in texts)
                    story.append(Paragraph(markup, para_style))
                else:
                    story.append(Spacer(1, font_size * 0.5))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def _generate_pdf_markdown(markdown_content: str, cfg: dict) -> bytes:
    """Generate PDF from plain markdown string (no inline styles).

    Handles: headings, lists, tables, quotes, checklists,
             code blocks, callouts, and images.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_LEFT
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except ImportError:
        return b""

    font_name = cfg.get("font_name", "Helvetica")
    font_size = cfg.get("font_size", 12)
    line_spacing = cfg.get("line_spacing", 1.5)
    margins = cfg.get("margins", {})

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=inch * margins.get("left", 1.0),
                            rightMargin=inch * margins.get("right", 1.0),
                            topMargin=inch * margins.get("top", 1.0),
                            bottomMargin=inch * margins.get("bottom", 1.0))

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle("CustomNormal", parent=styles["Normal"],
                                  fontName=font_name, fontSize=font_size,
                                  leading=font_size * line_spacing)

    story = []
    table_buffer: list[list[str]] = []
    code_block_lang = None
    code_block_lines = []
    callout_type = None
    callout_lines = []
    emoji_map = {"info": "\U0001F4A1", "warning": "\u26A0\uFE0F", "tip": "\u2705", "danger": "\U0001F6AB"}

    def _flush_pdf_table():
        nonlocal table_buffer
        if not table_buffer:
            return
        headers = table_buffer[0]
        data = table_buffer[1:] if len(table_buffer) > 1 else []
        table_data = [headers] + data
        n_cols = len(headers)
        page_width = A4[0] - inch * (margins.get("left", 1.0) + margins.get("right", 1.0))
        col_w = min(page_width / n_cols, inch * 2.0)
        tbl = Table(table_data, colWidths=[col_w] * n_cols)
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E0E0")),
            ("FONTSIZE", (0, 0), (-1, -1), max(font_size - 2, 8)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
        story.append(Spacer(1, font_size * 0.5))
        table_buffer = []

    def _flush_code_block_pdf():
        nonlocal code_block_lang, code_block_lines
        if not code_block_lines:
            code_block_lang = None
            code_block_lines = []
            return
        code_style = ParagraphStyle("MarkdownCode", parent=styles["Normal"],
                                    fontName="Courier", fontSize=font_size - 2,
                                    leading=(font_size - 1) * 1.2)
        for cl in code_block_lines:
            escaped = _escape_xml(cl)
            story.append(Paragraph(f"<font face='Courier' size='{font_size - 2}'>{escaped}</font>", code_style))
        code_block_lang = None
        code_block_lines = []

    def _flush_callout_pdf():
        nonlocal callout_type, callout_lines
        if not callout_lines:
            callout_type = None
            callout_lines = []
            return
        emoji = emoji_map.get(callout_type or "", "")
        for cl in callout_lines:
            escaped = _escape_xml(cl)
            story.append(Paragraph(f"{emoji} {escaped}", normal_style))
        callout_type = None
        callout_lines = []

    if not markdown_content:
        story.append(Paragraph("", normal_style))
    else:
        for line in markdown_content.strip().split("\n"):
            stripped = line.strip()

            # ── Inside callout block ──
            if callout_type is not None:
                if stripped == ":::":
                    _flush_callout_pdf()
                    continue
                callout_lines.append(stripped)
                continue

            # ── Inside code block ──
            if code_block_lang is not None:
                if stripped == "```":
                    _flush_code_block_pdf()
                    continue
                code_block_lines.append(line)
                continue

            # ── Callout start ──
            if re.match(r'^:::(info|warning|tip|danger)\s', stripped):
                callout_type = re.match(r'^:::(info|warning|tip|danger)', stripped).group(1)
                continue

            # ── Code block start ──
            cm = re.match(r'^```(\w*)$', stripped)
            if cm:
                code_block_lang = cm.group(1) or ''
                continue

            # ── Table ──
            if _is_md_table_row(stripped):
                _flush_pdf_table()
                if _is_md_table_separator(stripped):
                    continue
                table_buffer.append(_parse_md_table_row(stripped))
                continue

            _flush_pdf_table()

            if not stripped:
                story.append(Spacer(1, font_size * 0.5))
                continue

            # ── Image ──
            img_m = re.match(r'^!\[(.*)\]\((.+)\)$', stripped)
            if img_m:
                image_style = ParagraphStyle("CustomImage", parent=styles["Normal"],
                                             fontName=font_name, fontSize=font_size - 2)
                story.append(Paragraph(f"<i>[Image: {_escape_xml(img_m.group(1) or img_m.group(2))}]</i>", image_style))
                continue

            # ── Headings ──
            if stripped.startswith("### "):
                hs = ParagraphStyle("CustomH3", parent=styles["Heading3"],
                                    fontName=font_name, fontSize=font_size + 2)
                story.append(Paragraph(stripped[4:], hs))
            elif stripped.startswith("## "):
                hs = ParagraphStyle("CustomH2", parent=styles["Heading2"],
                                    fontName=font_name, fontSize=font_size + 4)
                story.append(Paragraph(stripped[3:], hs))
            elif stripped.startswith("# "):
                hs = ParagraphStyle("CustomH1", parent=styles["Heading1"],
                                    fontName=font_name, fontSize=font_size + 6)
                story.append(Paragraph(stripped[2:], hs))
            # ── Quote ──
            elif stripped.startswith("> "):
                qs = ParagraphStyle("CustomQuote", parent=styles["Normal"],
                                    fontName=font_name, fontSize=font_size,
                                    leftIndent=20, alignment=TA_LEFT)
                story.append(Paragraph(f"<i>{_escape_xml(stripped[2:])}</i>", qs))
            # ── Checklist ──
            elif stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
                ls = ParagraphStyle("CheckChecked", parent=styles["Normal"],
                                    fontName=font_name, fontSize=font_size)
                story.append(Paragraph(f"\u2611 {_escape_xml(stripped[6:])}", ls))
            elif stripped.startswith("- [ ] "):
                ls = ParagraphStyle("CheckUnchecked", parent=styles["Normal"],
                                    fontName=font_name, fontSize=font_size)
                story.append(Paragraph(f"\u2610 {_escape_xml(stripped[6:])}", ls))
            # ── Lists ──
            elif stripped.startswith("- "):
                story.append(Paragraph(f"\u2022 {_escape_xml(stripped[2:])}", normal_style))
            elif stripped.startswith("1. "):
                story.append(Paragraph(f"1. {_escape_xml(stripped[3:])}", normal_style))
            else:
                story.append(Paragraph(stripped, normal_style))

        # Flush remaining buffers
        _flush_pdf_table()
        _flush_code_block_pdf()
        _flush_callout_pdf()

    doc.build(story)
    buf.seek(0)
    return buf.read()


async def create_document(tenant_id: str, user_id: str, name: str, markdown_content: str, agent_id: str = None, permission: str = "me", folder_id: str = None) -> dict:
    """Create a collaboration document from chat message content."""
    doc_id = get_uuid()
    content = _markdown_to_lexical_json(markdown_content)
    CollaborationDocumentService.save(
        id=doc_id,
        name=name,
        file_type="docx",
        content=content,
        markdown_content=markdown_content,
        tenant_id=tenant_id,
        created_by=user_id,
        agent_id=agent_id,
        permission=permission,
        folder_id=folder_id,
    )
    return {"id": doc_id, "name": name, "file_type": "docx", "permission": permission, "folder_id": folder_id}


async def list_documents(tenant_id: str, user_id: str) -> list:
    """List collaboration documents visible to the current user (own + team-shared + ACL)."""
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    # Get document IDs where user has explicit ACL grant
    acl_doc_ids = [
        a.document_id
        for a in CollaborationDocumentACLService.query(user_id=user_id)
    ]
    base_condition = (
        (
            (CollaborationDocument.created_by.in_(team_user_ids))
            & (CollaborationDocument.permission == TenantPermission.TEAM.value)
        )
        | (CollaborationDocument.created_by == user_id)
    )
    if acl_doc_ids:
        base_condition |= CollaborationDocument.id.in_(acl_doc_ids)
    docs = (
        CollaborationDocument.select()
        .where(base_condition)
        .order_by(CollaborationDocument.create_time.desc())
    )
    result = []
    for d in docs:
        result.append({
            "id": d.id,
            "name": d.name,
            "file_type": d.file_type,
            "agent_id": d.agent_id,
            "folder_id": d.folder_id,
            "sort_order": d.sort_order,
            "create_time": d.create_time,
            "update_time": d.update_time,
            "created_by": d.created_by,
            "permission": d.permission,
        })
    return result


async def get_document(doc_id: str, tenant_id: str) -> dict:
    """Get a single collaboration document with content."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    user_role = _get_user_role(doc_id, tenant_id)
    if not user_role:
        raise PermissionError("Access denied")
    return {
        "id": doc.id,
        "name": doc.name,
        "file_type": doc.file_type,
        "file_path": doc.file_path,
        "content": doc.content,
        "markdown_content": doc.markdown_content,
        "agent_id": doc.agent_id,
        "created_by": doc.created_by,
        "permission": doc.permission,
        "role": user_role,
        "create_time": doc.create_time,
        "update_time": doc.update_time,
        "ydoc": base64.b64encode(doc.ydoc).decode("ascii") if doc.ydoc else None,
        "version": doc.version or 0,
    }


async def update_document(doc_id: str, tenant_id: str, data: dict) -> dict:
    """Update document name and/or content. Requires editor role or higher."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")

    update_data = {}
    if "name" in data:
        update_data["name"] = data["name"]
    if "content" in data:
        update_data["content"] = data["content"]
    if "markdown_content" in data:
        update_data["markdown_content"] = data["markdown_content"]
        update_data["file_path"] = None  # invalidate cached file so download regenerates
    if "permission" in data:
        update_data["permission"] = data["permission"]
    if "folder_id" in data:
        update_data["folder_id"] = data["folder_id"]
    if "sort_order" in data:
        update_data["sort_order"] = data["sort_order"]
    if "ydoc_state" in data:
        update_data["ydoc"] = base64.b64decode(data["ydoc_state"])
        update_data["version"] = (doc.version or 0) + 1

    if update_data:
        CollaborationDocumentService.update_by_id(doc_id, update_data)
        await log_audit(tenant_id, doc_id, "document.update", {"fields": list(update_data.keys())})
    return {"id": doc_id, "updated": list(update_data.keys())}


async def save_ydoc_state(doc_id: str, tenant_id: str, data: dict) -> dict:
    """Save Yjs binary state from the frontend (periodic persistence)."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")

    update_data = {}
    ydoc_state_b64 = data.get("ydoc_state")
    if ydoc_state_b64:
        update_data["ydoc"] = base64.b64decode(ydoc_state_b64)
        update_data["version"] = (doc.version or 0) + 1
    if "content" in data:
        update_data["content"] = data["content"]
    if "markdown_content" in data:
        update_data["markdown_content"] = data["markdown_content"]
        update_data["file_path"] = None

    if update_data:
        CollaborationDocumentService.update_by_id(doc_id, update_data)

    return {"id": doc_id, "version": update_data.get("version", doc.version or 0)}


async def delete_document(doc_id: str, tenant_id: str) -> bool:
    """Delete a collaboration document and its stored file. Requires owner role."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")

    # Delete stored file if exists
    if doc.file_path:
        try:
            settings.STORAGE_IMPL.rm("collaboration", doc.file_path)
        except Exception as ex:
            logging.warning(f"Failed to delete file {doc.file_path}: {ex}")

    CollaborationDocumentService.delete_by_id(doc_id)
    await log_audit(tenant_id, doc_id, "document.delete", {"name": doc.name})
    return True


async def download_document(doc_id: str, tenant_id: str, file_type: str = "docx") -> tuple:
    """Generate and return document blob for download.

    Returns (blob_bytes, filename, mimetype).
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    # Prefer Lexical JSON (has inline styles), fall back to markdown
    content = doc.content if (isinstance(doc.content, dict) and doc.content.get("root")) else (doc.markdown_content or "")
    blob = b""
    filename = f"{doc.name}.{file_type}"

    if file_type == "docx":
        blob = _generate_docx(content)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif file_type == "pdf":
        blob = _generate_pdf(content)
        mimetype = "application/pdf"
    else:
        raise ValueError(f"Unsupported file type: {file_type}")

    # Cache file path for reuse
    if blob and not doc.file_path:
        storage_key = f"{doc_id}.{file_type}"
        try:
            settings.STORAGE_IMPL.put("collaboration", storage_key, blob)
            CollaborationDocumentService.update_by_id(doc_id, {"file_path": storage_key, "file_type": file_type})
        except Exception as ex:
            logging.warning(f"Failed to cache file to storage: {ex}")

    return blob, filename, mimetype


async def apply_format_rule(doc_id: str, tenant_id: str, rule_id: str) -> tuple:
    """Apply a format rule to a document and regenerate the file.

    Returns (blob_bytes, filename, mimetype).
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    e, rule = CollaborationFormatRuleService.get_by_id(rule_id)
    if not e:
        raise LookupError("Format rule not found")
    if not _check_access(rule, tenant_id):
        raise PermissionError("Access denied")

    config = rule.config or {}
    content = doc.content if (isinstance(doc.content, dict) and doc.content.get("root")) else (doc.markdown_content or "")
    file_type = doc.file_type or "docx"
    blob = b""

    if file_type == "docx":
        blob = _generate_docx(content, config)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif file_type == "pdf":
        blob = _generate_pdf(content, config)
        mimetype = "application/pdf"
    else:
        raise ValueError(f"Unsupported file type: {file_type}")

    filename = f"{doc.name}.{file_type}"

    # Update cached file
    if blob:
        storage_key = f"{doc_id}.{file_type}"
        try:
            settings.STORAGE_IMPL.put("collaboration", storage_key, blob)
            CollaborationDocumentService.update_by_id(doc_id, {"file_path": storage_key})
        except Exception as ex:
            logging.warning(f"Failed to update cached file: {ex}")

    return blob, filename, mimetype


async def create_format_rule(tenant_id: str, user_id: str, name: str, description: str = "", config: dict = None, permission: str = "me") -> dict:
    """Create a format rule."""
    rule_id = get_uuid()
    CollaborationFormatRuleService.save(
        id=rule_id,
        name=name,
        description=description or "",
        config=config or {},
        tenant_id=tenant_id,
        created_by=user_id,
        permission=permission,
    )
    return {"id": rule_id, "name": name, "permission": permission}


async def list_format_rules(tenant_id: str, user_id: str) -> list:
    """List format rules visible to the current user (own + team-shared)."""
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    rules = (
        CollaborationFormatRule.select()
        .where(
            (
                (CollaborationFormatRule.created_by.in_(team_user_ids))
                & (CollaborationFormatRule.permission == TenantPermission.TEAM.value)
            )
            | (CollaborationFormatRule.created_by == user_id)
        )
        .order_by(CollaborationFormatRule.create_time.desc())
    )
    result = []
    for r in rules:
        result.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "config": r.config,
            "create_time": r.create_time,
            "created_by": r.created_by,
            "permission": r.permission,
        })
    return result


async def update_format_rule(rule_id: str, tenant_id: str, data: dict) -> dict:
    """Update a format rule."""
    e, rule = CollaborationFormatRuleService.get_by_id(rule_id)
    if not e:
        raise LookupError("Format rule not found")
    if not _check_access(rule, tenant_id):
        raise PermissionError("Access denied")

    update_data = {}
    for key in ("name", "description", "config", "permission"):
        if key in data:
            update_data[key] = data[key]

    if update_data:
        CollaborationFormatRuleService.update_by_id(rule_id, update_data)
    return {"id": rule_id, "updated": list(update_data.keys())}


async def delete_format_rule(rule_id: str, tenant_id: str) -> bool:
    """Delete a format rule."""
    e, rule = CollaborationFormatRuleService.get_by_id(rule_id)
    if not e:
        raise LookupError("Format rule not found")
    if not _check_access(rule, tenant_id):
        raise PermissionError("Access denied")
    CollaborationFormatRuleService.delete_by_id(rule_id)
    return True


# ── Folder CRUD ──

async def create_folder(tenant_id: str, user_id: str, name: str, parent_id: str = None) -> dict:
    """Create a collaboration folder."""
    folder_id = get_uuid()
    CollaborationFolderService.save(
        id=folder_id,
        name=name,
        parent_id=parent_id,
        tenant_id=tenant_id,
        created_by=user_id,
        create_time=current_timestamp(),
    )
    return {"id": folder_id, "name": name, "parent_id": parent_id}


async def list_folders(tenant_id: str, user_id: str) -> list:
    """List folders visible to the current user, returned as flat list with tree data."""
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    folders = (
        CollaborationFolder.select()
        .where(CollaborationFolder.created_by.in_(team_user_ids))
        .order_by(CollaborationFolder.sort_order)
    )
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "created_by": f.created_by,
            "sort_order": f.sort_order,
            "create_time": f.create_time,
        }
        for f in folders
    ]


async def update_folder(folder_id: str, tenant_id: str, data: dict) -> dict:
    """Update folder name or parent."""
    e, folder = CollaborationFolderService.get_by_id(folder_id)
    if not e:
        raise LookupError("Folder not found")
    if not _check_access(folder, tenant_id):
        raise PermissionError("Access denied")
    update_data = {}
    for key in ("name", "parent_id", "sort_order"):
        if key in data:
            update_data[key] = data[key]
    if update_data:
        CollaborationFolderService.update_by_id(folder_id, update_data)
    return {"id": folder_id, "updated": list(update_data.keys())}


async def delete_folder(folder_id: str, tenant_id: str) -> bool:
    """Delete a folder. Documents in the folder become root-level."""
    e, folder = CollaborationFolderService.get_by_id(folder_id)
    if not e:
        raise LookupError("Folder not found")
    if not _check_access(folder, tenant_id):
        raise PermissionError("Access denied")
    # Move child documents to root and child folders to root atomically
    with DB.atomic():
        CollaborationDocument.update(folder_id=None).where(
            CollaborationDocument.folder_id == folder_id
        ).execute()
        CollaborationFolder.update(parent_id=None).where(
            CollaborationFolder.parent_id == folder_id
        ).execute()
        CollaborationFolderService.delete_by_id(folder_id)
    return True


async def move_document(doc_id: str, tenant_id: str, folder_id: str | None) -> dict:
    """Move a document to a folder (or root if folder_id is None)."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")
    CollaborationDocumentService.update_by_id(doc_id, {"folder_id": folder_id})
    return {"id": doc_id, "folder_id": folder_id}


# ── ACL Management ──

async def list_collaborators(doc_id: str, tenant_id: str) -> list[dict]:
    """List all collaborators for a document. Requires owner role."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")
    acls = CollaborationDocumentACLService.query(document_id=doc_id)
    return [
        {"id": a.id, "user_id": a.user_id, "role": a.role, "granted_by": a.granted_by, "create_time": a.create_time}
        for a in acls
    ]


async def add_collaborator(doc_id: str, tenant_id: str, user_id: str, role: str) -> dict:
    """Add a collaborator to a document. Requires owner role."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")
    if role not in ROLE_HIERARCHY:
        raise ValueError(f"Invalid role: {role}. Must be one of: {', '.join(ROLE_HIERARCHY.keys())}")
    acl_id = get_uuid()
    CollaborationDocumentACLService.save(
        id=acl_id,
        document_id=doc_id,
        user_id=user_id,
        role=role,
        granted_by=tenant_id,
        create_time=current_timestamp(),
    )
    return {"id": acl_id, "document_id": doc_id, "user_id": user_id, "role": role}


async def update_collaborator_role(doc_id: str, tenant_id: str, collaborator_user_id: str, role: str) -> dict:
    """Update a collaborator's role. Requires owner role."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")
    if role not in ROLE_HIERARCHY:
        raise ValueError(f"Invalid role: {role}. Must be one of: {', '.join(ROLE_HIERARCHY.keys())}")
    acls = CollaborationDocumentACLService.query(document_id=doc_id, user_id=collaborator_user_id)
    if not acls:
        raise LookupError("Collaborator not found")
    CollaborationDocumentACLService.update_by_id(acls[0].id, {"role": role})
    return {"document_id": doc_id, "user_id": collaborator_user_id, "role": role}


async def remove_collaborator(doc_id: str, tenant_id: str, collaborator_user_id: str) -> bool:
    """Remove a collaborator from a document. Requires owner role."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")
    acls = CollaborationDocumentACLService.query(document_id=doc_id, user_id=collaborator_user_id)
    if not acls:
        raise LookupError("Collaborator not found")
    CollaborationDocumentACLService.delete_by_id(acls[0].id)
    return True


# ── Word Import ──

async def import_docx(tenant_id: str, user_id: str, file_obj, folder_id: str = None) -> dict:
    """Parse a .docx file and create a collaboration document.

    Extracts headings, paragraphs, tables, and inline formatting,
    converting them into Lexical JSON editor state.
    """
    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise RuntimeError("python-docx not installed")

    doc = DocxDocument(file_obj)
    doc_id = get_uuid()
    name = (file_obj.filename or "imported").rsplit(".", 1)[0]

    heading_map = {
        0: ("h1", "# "),
        1: ("h2", "## "),
        2: ("h3", "### "),
    }

    lexical_children = []
    markdown_lines = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            lexical_children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "\u00A0", "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
            })
            markdown_lines.append("")
            continue

        # Detect heading by outline level or style name
        outline_level = para.paragraph_format.outline_level if para.style else None
        heading_tag = None
        heading_prefix = ""
        if outline_level is not None and 0 <= outline_level <= 2:
            heading_tag, heading_prefix = heading_map.get(outline_level, (None, ""))
        elif para.style and para.style.name:
            style_name = para.style.name.lower()
            if "heading 1" in style_name:
                heading_tag, heading_prefix = "h1", "# "
            elif "heading 2" in style_name:
                heading_tag, heading_prefix = "h2", "## "
            elif "heading 3" in style_name:
                heading_tag, heading_prefix = "h3", "### "

        # Build text children with inline formatting from runs
        text_children = []
        md_parts = []
        for run in para.runs:
            run_text = run.text or ""
            if not run_text:
                continue
            fmt = 0
            if run.bold:
                fmt |= 1
            if run.italic:
                fmt |= 2
            if run.underline:
                fmt |= 8
            if run.font and run.font.strike:
                fmt |= 4
            # Markdown generation
            if run.bold and run.italic:
                md_parts.append(f"***{run_text}***")
            elif run.bold:
                md_parts.append(f"**{run_text}**")
            elif run.italic:
                md_parts.append(f"*{run_text}*")
            elif run.font and run.font.strike:
                md_parts.append(f"~~{run_text}~~")
            else:
                md_parts.append(run_text)
            style_str = ""
            if run.font and run.font.name:
                style_str = f"font-family: {run.font.name};"
            if run.font and run.font.size:
                size_pt = run.font.size.pt
                style_str += f"font-size: {size_pt}pt;"
            text_children.append({
                "detail": 0, "format": fmt, "mode": "normal",
                "style": style_str, "text": run_text, "type": "text", "version": 1,
            })

        if not text_children:
            text_children = [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": text, "type": "text", "version": 1}]
            md_parts.append(text)

        # Build the Lexical block
        if heading_tag:
            lexical_children.append({
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "tag": heading_tag, "type": "heading", "version": 1,
            })
            markdown_lines.append(heading_prefix + "".join(md_parts))
        else:
            lexical_children.append({
                "children": text_children,
                "direction": "ltr", "format": "", "indent": 0,
                "type": "paragraph", "version": 1,
            })
            markdown_lines.append("".join(md_parts))

    # Handle tables
    for table in doc.tables:
        row_nodes = []
        table_md_rows = []
        for i, row in enumerate(table.rows):
            cell_nodes = []
            md_cells = []
            for cell in row.cells:
                cell_text = cell.text.replace("\n", " ").strip()
                md_cells.append(cell_text)
                cell_nodes.append({
                    "type": "tablecell",
                    "children": [{
                        "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": cell_text, "type": "text", "version": 1}],
                        "direction": "ltr", "format": "", "indent": 0,
                        "type": "paragraph", "version": 1,
                    }],
                    "direction": "ltr", "format": "", "indent": 0,
                    "headerState": 0, "colSpan": 1, "rowSpan": 1, "version": 1,
                })
            table_md_rows.append("| " + " | ".join(md_cells) + " |")
            row_nodes.append({
                "type": "tablerow",
                "children": cell_nodes,
                "direction": "ltr", "format": "", "indent": 0, "version": 1,
            })
        if row_nodes:
            if len(table_md_rows) > 0:
                md_full = table_md_rows[0] + "\n"
                if len(table_md_rows) > 0:
                    col_count = table_md_rows[0].count("|") - 1
                    if col_count > 0:
                        md_full += "|" + " --- |" * col_count + "\n"
                for r in table_md_rows[1:]:
                    md_full += r + "\n"
                markdown_lines.append(md_full.strip())
            lexical_children.append({
                "type": "table",
                "children": row_nodes,
                "direction": "ltr", "format": "", "indent": 0, "version": 1,
            })

    content = {"root": {"children": lexical_children, "direction": "ltr", "format": "", "indent": 0, "type": "root", "version": 1}}
    markdown_content = "\n".join(markdown_lines)

    CollaborationDocumentService.save(
        id=doc_id,
        name=name,
        file_type="docx",
        folder_id=folder_id,
        content=content,
        markdown_content=markdown_content,
        tenant_id=tenant_id,
        created_by=user_id,
        permission="me",
    )
    return {"id": doc_id, "name": name, "file_type": "docx"}


# ── Comment CRUD ──

async def list_comments(doc_id: str, tenant_id: str) -> list[dict]:
    """List all non-deleted comments for a document."""
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    comments = (
        CollaborationComment.select()
        .where(
            (CollaborationComment.document_id == doc_id)
            & (CollaborationComment.deleted_at.is_null())
        )
        .order_by(CollaborationComment.create_time.asc())
    )
    return [
        {
            "id": c.id,
            "document_id": c.document_id,
            "user_id": c.user_id,
            "parent_comment_id": c.parent_comment_id,
            "anchor_block_key": c.anchor_block_key,
            "anchor_offset_start": c.anchor_offset_start,
            "anchor_offset_end": c.anchor_offset_end,
            "content": c.content,
            "resolved": c.resolved,
            "create_time": c.create_time,
            "update_time": c.update_time,
        }
        for c in comments
    ]


async def create_comment(
    doc_id: str,
    tenant_id: str,
    content: str,
    parent_comment_id: str | None = None,
    anchor_block_key: str | None = None,
    anchor_offset_start: int | None = None,
    anchor_offset_end: int | None = None,
) -> dict:
    """Create a comment on a document."""
    role = _get_user_role(doc_id, tenant_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY.get("commenter", 1):
        raise PermissionError("Access denied")

    if not content or not content.strip():
        raise ValueError("Comment content is required")

    safe_content = sanitize_html(content.strip())

    comment_id = get_uuid()
    now = current_timestamp()
    CollaborationCommentService.save(
        id=comment_id,
        document_id=doc_id,
        user_id=tenant_id,
        parent_comment_id=parent_comment_id,
        anchor_block_key=anchor_block_key,
        anchor_offset_start=anchor_offset_start,
        anchor_offset_end=anchor_offset_end,
        content=safe_content,
        create_time=now,
        update_time=now,
    )
    return {
        "id": comment_id,
        "document_id": doc_id,
        "user_id": tenant_id,
        "parent_comment_id": parent_comment_id,
        "content": safe_content,
        "resolved": False,
        "create_time": now,
        "update_time": now,
    }


async def update_comment(doc_id: str, comment_id: str, tenant_id: str, content: str) -> dict:
    """Edit own comment content."""
    e, comment = CollaborationCommentService.get_by_id(comment_id)
    if not e:
        raise LookupError("Comment not found")
    if comment.document_id != doc_id:
        raise LookupError("Comment not found")
    if comment.user_id != tenant_id:
        raise PermissionError("Can only edit own comments")
    if comment.deleted_at is not None:
        raise LookupError("Comment has been deleted")
    if not content or not content.strip():
        raise ValueError("Comment content is required")

    safe_content = sanitize_html(content.strip())

    CollaborationCommentService.update_by_id(comment_id, {
        "content": safe_content,
        "update_time": current_timestamp(),
    })
    return {"id": comment_id, "content": safe_content}


async def delete_comment(doc_id: str, comment_id: str, tenant_id: str) -> bool:
    """Soft-delete own comment. Also soft-deletes all child comments."""
    e, comment = CollaborationCommentService.get_by_id(comment_id)
    if not e:
        raise LookupError("Comment not found")
    if comment.document_id != doc_id:
        raise LookupError("Comment not found")
    if comment.user_id != tenant_id:
        raise PermissionError("Can only delete own comments")

    now = current_timestamp()
    # Collect all descendant IDs iteratively, avoiding circular references
    ids_to_delete = {comment_id}
    child_ids = [comment_id]
    while child_ids:
        children = CollaborationComment.select().where(
            (CollaborationComment.parent_comment_id.in_(child_ids))
            & (CollaborationComment.deleted_at.is_null())
        )
        child_ids = []
        for c in children:
            if c.id not in ids_to_delete:
                child_ids.append(c.id)
                ids_to_delete.add(c.id)

    for cid in ids_to_delete:
        CollaborationCommentService.update_by_id(cid, {"deleted_at": now})
    return True


async def resolve_comment(doc_id: str, comment_id: str, tenant_id: str) -> dict:
    """Mark a comment as resolved."""
    e, comment = CollaborationCommentService.get_by_id(comment_id)
    if not e:
        raise LookupError("Comment not found")
    if comment.document_id != doc_id:
        raise LookupError("Comment not found")
    if comment.deleted_at is not None:
        raise LookupError("Comment has been deleted")

    CollaborationCommentService.update_by_id(comment_id, {"resolved": True})
    return {"id": comment_id, "resolved": True}


async def unresolve_comment(doc_id: str, comment_id: str, tenant_id: str) -> dict:
    """Reopen a resolved comment."""
    e, comment = CollaborationCommentService.get_by_id(comment_id)
    if not e:
        raise LookupError("Comment not found")
    if comment.document_id != doc_id:
        raise LookupError("Comment not found")
    if comment.deleted_at is not None:
        raise LookupError("Comment has been deleted")

    CollaborationCommentService.update_by_id(comment_id, {"resolved": False})
    return {"id": comment_id, "resolved": False}


# ── Version History ──

async def list_versions(doc_id: str, tenant_id: str) -> dict:
    """Get document version info for the version dropdown.

    Returns the current version counter and whether a restorable ydoc state exists.
    Each save_ydoc_state() call increments version, so the counter reflects save count.
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    return {
        "current_version": doc.version or 0,
        "has_ydoc": bool(doc.ydoc),
        "update_time": doc.update_time,
    }


async def restore_version(doc_id: str, tenant_id: str) -> dict:
    """Restore document to the latest saved ydoc state.

    Tells the frontend to reload from ydoc, discarding any unsaved local changes.
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")
    if not doc.ydoc:
        raise ValueError("No saved state to restore")

    await log_audit(tenant_id, doc_id, "version.restore", {"version": doc.version or 0})

    return {
        "id": doc_id,
        "version": doc.version or 0,
        "ydoc": base64.b64encode(doc.ydoc).decode("ascii"),
    }


# ── Share Link ──

async def create_or_update_share(doc_id: str, tenant_id: str, permission: str = "view", password: str = None, expires_at: int = None) -> dict:
    """Create or update a share link for a document. One share per document."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")

    if permission not in ("view", "edit"):
        raise ValueError("Permission must be 'view' or 'edit'")

    password_hash = generate_password_hash(password) if password else None

    # Check if share already exists
    existing = CollaborationShareLinkService.query(document_id=doc_id)
    if existing:
        share = existing[0]
        update_data = {"permission": permission, "update_time": current_timestamp()}
        if password is not None:
            # password="" means clear password, password="xxx" means set new password
            update_data["password_hash"] = generate_password_hash(password) if password else None
        if expires_at is not None:
            update_data["expires_at"] = expires_at
        CollaborationShareLinkService.update_by_id(share.id, update_data)
        token = share.token
        await log_audit(tenant_id, doc_id, "share.update", {"permission": permission})
    else:
        token = get_uuid()
        CollaborationShareLinkService.save(
            id=get_uuid(),
            document_id=doc_id,
            token=token,
            permission=permission,
            password_hash=generate_password_hash(password) if password else None,
            expires_at=expires_at,
            created_by=tenant_id,
            create_time=current_timestamp(),
            update_time=current_timestamp(),
        )
        await log_audit(tenant_id, doc_id, "share.create", {"permission": permission})

    return {
        "document_id": doc_id,
        "token": token,
        "permission": permission,
        "has_password": bool(password),
        "expires_at": expires_at,
    }


async def get_share(doc_id: str, tenant_id: str) -> dict | None:
    """Get current share link info for a document."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")

    existing = CollaborationShareLinkService.query(document_id=doc_id)
    if not existing:
        return None

    share = existing[0]
    return {
        "document_id": share.document_id,
        "token": share.token,
        "permission": share.permission,
        "has_password": bool(share.password_hash),
        "expires_at": share.expires_at,
    }


async def delete_share(doc_id: str, tenant_id: str) -> bool:
    """Delete the share link for a document."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")

    existing = CollaborationShareLinkService.query(document_id=doc_id)
    if not existing:
        raise LookupError("Share link not found")

    CollaborationShareLinkService.delete_by_id(existing[0].id)
    await log_audit(tenant_id, doc_id, "share.delete")

    return True


async def access_shared_doc(token: str, password: str = None) -> dict:
    """Access a shared document by token. Returns document data if access is granted."""
    existing = CollaborationShareLinkService.query(token=token)
    if not existing:
        raise LookupError("Share link not found or expired")

    share = existing[0]

    # Check expiry
    if share.expires_at and share.expires_at < current_timestamp():
        raise PermissionError("Share link has expired")

    # Check password
    if share.password_hash:
        if not password:
            raise PermissionError("Password required")
        if not check_password_hash(share.password_hash, password):
            raise PermissionError("Incorrect password")

    e, doc = CollaborationDocumentService.get_by_id(share.document_id)
    if not e:
        raise LookupError("Document not found")

    return {
        "id": doc.id,
        "name": doc.name,
        "content": doc.content,
        "markdown_content": doc.markdown_content,
        "permission": share.permission,
        "has_password": bool(share.password_hash),
    }


# ── Attachment CRUD ──

async def upload_attachment(doc_id: str, tenant_id: str, file_obj) -> dict:
    """Upload an attachment to a document. Stored in MinIO via STORAGE_IMPL."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")

    data, safe_name, mime_type = validate_upload(file_obj)

    attachment_id = get_uuid()
    ext = f".{safe_name.rsplit('.', 1)[-1].lower()}" if "." in safe_name else ""
    storage_key = f"attachments/{doc_id}/{attachment_id}{ext}"

    try:
        settings.STORAGE_IMPL.put("collaboration", storage_key, data)
    except Exception as e:
        logging.error(f"Failed to upload attachment to storage: {e}")
        raise RuntimeError("Failed to store attachment")

    CollaborationAttachmentService.save(
        id=attachment_id,
        document_id=doc_id,
        file_name=safe_name,
        file_size=len(data),
        mime_type=mime_type,
        storage_key=storage_key,
        uploader_id=tenant_id,
        create_time=current_timestamp(),
    )

    await log_audit(tenant_id, doc_id, "attachment.upload", {"file_name": safe_name, "file_size": len(data)})

    return {
        "id": attachment_id,
        "document_id": doc_id,
        "file_name": safe_name,
        "file_size": len(data),
        "mime_type": mime_type,
    }


async def list_attachments(doc_id: str, tenant_id: str) -> list[dict]:
    """List all attachments for a document."""
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    attachments = CollaborationAttachmentService.query(document_id=doc_id)
    return [
        {
            "id": a.id,
            "document_id": a.document_id,
            "file_name": a.file_name,
            "file_size": a.file_size,
            "mime_type": a.mime_type,
            "uploader_id": a.uploader_id,
            "create_time": a.create_time,
        }
        for a in attachments
    ]


async def download_attachment(doc_id: str, attachment_id: str, tenant_id: str) -> tuple[bytes, str, str]:
    """Download an attachment. Returns (data, filename, mime_type)."""
    e, attachment = CollaborationAttachmentService.get_by_id(attachment_id)
    if not e:
        raise LookupError("Attachment not found")
    if attachment.document_id != doc_id:
        raise LookupError("Attachment not found")

    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    try:
        data = settings.STORAGE_IMPL.get("collaboration", attachment.storage_key)
    except Exception as e:
        logging.error(f"Failed to download attachment: {e}")
        raise RuntimeError("Failed to retrieve attachment")

    return data, attachment.file_name, attachment.mime_type


async def delete_attachment(doc_id: str, attachment_id: str, tenant_id: str) -> bool:
    """Delete an attachment."""
    e, attachment = CollaborationAttachmentService.get_by_id(attachment_id)
    if not e:
        raise LookupError("Attachment not found")
    if attachment.document_id != doc_id:
        raise LookupError("Attachment not found")
    if not _check_role(doc_id, tenant_id, "editor"):
        raise PermissionError("Access denied")

    # Delete DB record first (source of truth), then best-effort storage cleanup
    CollaborationAttachmentService.delete_by_id(attachment_id)

    try:
        settings.STORAGE_IMPL.rm("collaboration", attachment.storage_key)
    except Exception as e:
        logging.warning(f"Failed to delete attachment from storage (orphaned): {e}")

    await log_audit(tenant_id, doc_id, "attachment.delete", {"file_name": attachment.file_name})

    return True


# ── Audit Log ──

async def list_audit_logs(doc_id: str, tenant_id: str, limit: int = 50, offset: int = 0) -> dict:
    """List audit logs for a document. Owner only."""
    if not _check_role(doc_id, tenant_id, "owner"):
        raise PermissionError("Access denied")

    logs = (
        CollaborationAuditLog.select()
        .where(CollaborationAuditLog.document_id == doc_id)
        .order_by(CollaborationAuditLog.create_time.desc())
        .offset(offset)
        .limit(limit)
    )
    total = (
        CollaborationAuditLog.select()
        .where(CollaborationAuditLog.document_id == doc_id)
        .count()
    )

    return {
        "total": total,
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "action": log.action,
                "detail": log.detail,
                "ip_address": log.ip_address,
                "create_time": log.create_time,
            }
            for log in logs
        ],
    }
