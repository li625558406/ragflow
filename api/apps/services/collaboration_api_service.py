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

import io
import logging
import re

import settings
from api.db import TenantPermission
from api.db.db_models import CollaborationDocument, CollaborationFormatRule, UserTenant
from api.db.services.collaboration_service import CollaborationDocumentService, CollaborationFormatRuleService
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


def _check_access(obj, user_id: str) -> bool:
    """Check if user has access to a document/format-rule, following agent team permission model."""
    if obj.created_by == user_id:
        return True
    if obj.permission != TenantPermission.TEAM.value:
        return False
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    return obj.created_by in team_user_ids


def _markdown_to_lexical_json(markdown_content: str) -> dict:
    """Convert markdown content to Lexical editor JSON state.

    Produces a minimal Lexical state with paragraphs.
    Each paragraph is a <p> node with a single text child.
    """
    if not markdown_content:
        return {"root": {"children": [{"children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "", "type": "text", "version": 1}], "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1}], "direction": "ltr", "format": "", "indent": 0, "type": "root", "version": 1}}

    lines = markdown_content.strip().split("\n")
    children = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": "\u00A0", "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
            })
            continue

        # Check for heading (## or ### format)
        heading_match = None
        if stripped.startswith("### "):
            heading_match = ("h3", stripped[4:])
        elif stripped.startswith("## "):
            heading_match = ("h2", stripped[3:])
        elif stripped.startswith("# "):
            heading_match = ("h1", stripped[2:])

        if heading_match:
            tag, text = heading_match
            children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": text, "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "tag": tag, "type": "heading", "version": 1,
            })
        else:
            children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal", "style": "", "text": stripped, "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0, "type": "paragraph", "version": 1,
            })

    return {"root": {"children": children, "direction": "ltr", "format": "", "indent": 0, "type": "root", "version": 1}}


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

        if block_type == "list":
            list_type = block.get("listType", "bullet")
            for item in block.get("children", []):
                if item.get("type") != "listitem":
                    continue
                texts = [c for c in item.get("children", []) if c.get("type") == "text"]
                yield ("listitem", list_type, alignment, texts)
        else:
            texts = [c for c in block.get("children", []) if c.get("type") == "text"]
            yield (block_type, tag, alignment, texts)


def _generate_docx(content, format_config: dict = None) -> bytes:
    """Generate a .docx file from Lexical JSON content (or markdown string fallback)."""
    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches
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

    for block_type, tag_or_listtype, alignment, texts in _iter_lexical_blocks(root_children):
        if block_type == "listitem":
            if tag_or_listtype == "bullet":
                p = doc.add_paragraph(style="List Bullet")
            else:
                p = doc.add_paragraph(style="List Number")
        elif block_type == "heading":
            style_name = heading_style_map.get(tag_or_listtype, "Heading 2")
            p = doc.add_paragraph()
            p.style = doc.styles[style_name]
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


def _flush_docx_table(doc, table_rows: list[list[str]]):
    """Write accumulated table rows into a python-docx table."""
    if not table_rows:
        return
    headers = table_rows[0]
    data = table_rows[1:] if len(table_rows) > 1 else []
    cols = len(headers)
    rows = 1 + len(data)
    table = doc.add_table(rows=rows, cols=cols, style="Table Grid")
    # Header row
    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
    # Data rows
    for i, row_cells in enumerate(data):
        for j, val in enumerate(row_cells):
            if j < cols:
                table.cell(i + 1, j).text = val
    doc.add_paragraph("")  # spacer after table


def _generate_docx_markdown(markdown_content: str, cfg: dict) -> bytes:
    """Legacy: generate .docx from plain markdown string (no inline styles)."""

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches
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

    def _flush_table():
        nonlocal table_buffer
        if table_buffer:
            _flush_docx_table(doc, table_buffer)
            table_buffer = []

    for line in lines:
        stripped = line.strip()

        if _is_md_table_row(stripped):
            if _is_md_table_separator(stripped):
                continue
            table_buffer.append(_parse_md_table_row(stripped))
            continue

        # Not a table row — flush any buffered table first
        _flush_table()

        if not stripped:
            doc.add_paragraph("")
            continue
        if stripped.startswith("### "):
            p = doc.add_paragraph(stripped[4:])
            p.style = doc.styles["Heading 3"]
        elif stripped.startswith("## "):
            p = doc.add_paragraph(stripped[3:])
            p.style = doc.styles["Heading 2"]
        elif stripped.startswith("# "):
            p = doc.add_paragraph(stripped[2:])
            p.style = doc.styles["Heading 1"]
        elif stripped.startswith("- "):
            doc.add_paragraph(stripped[2:], style="List Bullet")
        elif stripped.startswith("1. "):
            doc.add_paragraph(stripped[3:], style="List Number")
        else:
            doc.add_paragraph(stripped)

    _flush_table()

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

        for block_type, tag_or_listtype, alignment, texts in _iter_lexical_blocks(root_children):
            para_alignment = alignment_map.get(alignment, TA_LEFT) if alignment else TA_LEFT

            if block_type == "listitem":
                bullet = "\u2022 " if tag_or_listtype == "bullet" else "1. "
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
    """Legacy: generate PDF from plain markdown string (no inline styles)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
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

    def _flush_pdf_table():
        nonlocal table_buffer
        if not table_buffer:
            return
        headers = table_buffer[0]
        data = table_buffer[1:] if len(table_buffer) > 1 else []
        table_data = [headers] + data
        n_cols = len(headers)
        # Estimate column widths; use narrow default for tables with many cols
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

    if not markdown_content:
        story.append(Paragraph("", normal_style))
    else:
        for line in markdown_content.strip().split("\n"):
            stripped = line.strip()

            if _is_md_table_row(stripped):
                if _is_md_table_separator(stripped):
                    continue
                table_buffer.append(_parse_md_table_row(stripped))
                continue

            _flush_pdf_table()

            if not stripped:
                story.append(Spacer(1, font_size * 0.5))
                continue
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
            else:
                story.append(Paragraph(stripped, normal_style))

        _flush_pdf_table()

    doc.build(story)
    buf.seek(0)
    return buf.read()


async def create_document(tenant_id: str, user_id: str, name: str, markdown_content: str, agent_id: str = None, permission: str = "me") -> dict:
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
    )
    return {"id": doc_id, "name": name, "file_type": "docx", "permission": permission}


async def list_documents(tenant_id: str, user_id: str) -> list:
    """List collaboration documents visible to the current user (own + team-shared)."""
    team_user_ids = _get_shared_tenant_user_ids(user_id)
    docs = (
        CollaborationDocument.select()
        .where(
            (
                (CollaborationDocument.created_by.in_(team_user_ids))
                & (CollaborationDocument.permission == TenantPermission.TEAM.value)
            )
            | (CollaborationDocument.created_by == user_id)
        )
        .order_by(CollaborationDocument.create_time.desc())
    )
    result = []
    for d in docs:
        result.append({
            "id": d.id,
            "name": d.name,
            "file_type": d.file_type,
            "agent_id": d.agent_id,
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
    if not _check_access(doc, tenant_id):
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
        "create_time": doc.create_time,
        "update_time": doc.update_time,
    }


async def update_document(doc_id: str, tenant_id: str, data: dict) -> dict:
    """Update document name and/or content."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_access(doc, tenant_id):
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

    if update_data:
        CollaborationDocumentService.update_by_id(doc_id, update_data)
    return {"id": doc_id, "updated": list(update_data.keys())}


async def delete_document(doc_id: str, tenant_id: str) -> bool:
    """Delete a collaboration document and its stored file."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_access(doc, tenant_id):
        raise PermissionError("Access denied")

    # Delete stored file if exists
    if doc.file_path:
        try:
            settings.STORAGE_IMPL.rm("collaboration", doc.file_path)
        except Exception as ex:
            logging.warning(f"Failed to delete file {doc.file_path}: {ex}")

    CollaborationDocumentService.delete_by_id(doc_id)
    return True


async def download_document(doc_id: str, tenant_id: str, file_type: str = "docx") -> tuple:
    """Generate and return document blob for download.

    Returns (blob_bytes, filename, mimetype).
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _check_access(doc, tenant_id):
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
    if not _check_access(doc, tenant_id):
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
