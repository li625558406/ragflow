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

import settings
from api.db.services.collaboration_service import CollaborationDocumentService, CollaborationFormatRuleService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


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


def _generate_docx(markdown_content: str, format_config: dict = None) -> bytes:
    """Generate a .docx file from markdown content using python-docx."""
    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt, Inches, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        logging.error("python-docx not installed")
        return b""

    doc = DocxDocument()

    # Apply format config
    cfg = format_config or {}
    font_name = cfg.get("font_name", "SimSun")
    font_size = cfg.get("font_size", 12)
    line_spacing = cfg.get("line_spacing", 1.5)
    margins = cfg.get("margins", {})  # top, bottom, left, right in inches

    # Set default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = font_name
    font.size = Pt(font_size)
    pf = style.paragraph_format
    pf.line_spacing = line_spacing

    # Set margins
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
    for line in lines:
        stripped = line.strip()
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

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _generate_pdf(markdown_content: str, format_config: dict = None) -> bytes:
    """Generate a PDF file from markdown content using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        logging.error("reportlab not installed")
        return b""

    cfg = format_config or {}
    font_name = cfg.get("font_name", "Helvetica")
    font_size = cfg.get("font_size", 12)
    line_spacing = cfg.get("line_spacing", 1.5)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=inch * cfg.get("margins", {}).get("left", 1.0),
                            rightMargin=inch * cfg.get("margins", {}).get("right", 1.0),
                            topMargin=inch * cfg.get("margins", {}).get("top", 1.0),
                            bottomMargin=inch * cfg.get("margins", {}).get("bottom", 1.0))

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle("CustomNormal", parent=styles["Normal"],
                                  fontName=font_name, fontSize=font_size,
                                  leading=font_size * line_spacing)

    story = []
    if not markdown_content:
        story.append(Paragraph("", normal_style))
    else:
        for line in markdown_content.strip().split("\n"):
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, font_size * 0.5))
                continue
            if stripped.startswith("### "):
                heading_style = ParagraphStyle("CustomH3", parent=styles["Heading3"],
                                               fontName=font_name, fontSize=font_size + 2)
                story.append(Paragraph(stripped[4:], heading_style))
            elif stripped.startswith("## "):
                heading_style = ParagraphStyle("CustomH2", parent=styles["Heading2"],
                                               fontName=font_name, fontSize=font_size + 4)
                story.append(Paragraph(stripped[3:], heading_style))
            elif stripped.startswith("# "):
                heading_style = ParagraphStyle("CustomH1", parent=styles["Heading1"],
                                               fontName=font_name, fontSize=font_size + 6)
                story.append(Paragraph(stripped[2:], heading_style))
            else:
                story.append(Paragraph(stripped, normal_style))

    doc.build(story)
    buf.seek(0)
    return buf.read()


async def create_document(tenant_id: str, user_id: str, name: str, markdown_content: str, agent_id: str = None) -> dict:
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
    )
    return {"id": doc_id, "name": name, "file_type": "docx"}


async def list_documents(tenant_id: str, user_id: str) -> list:
    """List collaboration documents for the current user."""
    docs = CollaborationDocumentService.query(
        tenant_id=tenant_id, created_by=user_id,
        reverse=True, order_by="create_time",
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
        })
    return result


async def get_document(doc_id: str, tenant_id: str) -> dict:
    """Get a single collaboration document with content."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if doc.tenant_id != tenant_id:
        raise PermissionError("Access denied")
    return {
        "id": doc.id,
        "name": doc.name,
        "file_type": doc.file_type,
        "file_path": doc.file_path,
        "content": doc.content,
        "markdown_content": doc.markdown_content,
        "agent_id": doc.agent_id,
        "create_time": doc.create_time,
        "update_time": doc.update_time,
    }


async def update_document(doc_id: str, tenant_id: str, data: dict) -> dict:
    """Update document name and/or content."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if doc.tenant_id != tenant_id:
        raise PermissionError("Access denied")

    update_data = {}
    if "name" in data:
        update_data["name"] = data["name"]
    if "content" in data:
        update_data["content"] = data["content"]
    if "markdown_content" in data:
        update_data["markdown_content"] = data["markdown_content"]

    if update_data:
        CollaborationDocumentService.update_by_id(doc_id, update_data)
    return {"id": doc_id, "updated": list(update_data.keys())}


async def delete_document(doc_id: str, tenant_id: str) -> bool:
    """Delete a collaboration document and its stored file."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if doc.tenant_id != tenant_id:
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
    if doc.tenant_id != tenant_id:
        raise PermissionError("Access denied")

    markdown_content = doc.markdown_content or ""
    blob = b""
    filename = f"{doc.name}.{file_type}"

    if file_type == "docx":
        blob = _generate_docx(markdown_content)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif file_type == "pdf":
        blob = _generate_pdf(markdown_content)
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
    if doc.tenant_id != tenant_id:
        raise PermissionError("Access denied")

    e, rule = CollaborationFormatRuleService.get_by_id(rule_id)
    if not e:
        raise LookupError("Format rule not found")
    if rule.tenant_id != tenant_id:
        raise PermissionError("Access denied")

    config = rule.config or {}
    markdown_content = doc.markdown_content or ""
    file_type = doc.file_type or "docx"
    blob = b""

    if file_type == "docx":
        blob = _generate_docx(markdown_content, config)
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif file_type == "pdf":
        blob = _generate_pdf(markdown_content, config)
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


async def create_format_rule(tenant_id: str, user_id: str, name: str, description: str = "", config: dict = None) -> dict:
    """Create a format rule."""
    rule_id = get_uuid()
    CollaborationFormatRuleService.save(
        id=rule_id,
        name=name,
        description=description or "",
        config=config or {},
        tenant_id=tenant_id,
        created_by=user_id,
    )
    return {"id": rule_id, "name": name}


async def list_format_rules(tenant_id: str, user_id: str) -> list:
    """List format rules for the current user."""
    rules = CollaborationFormatRuleService.query(
        tenant_id=tenant_id, created_by=user_id,
        reverse=True, order_by="create_time",
    )
    result = []
    for r in rules:
        result.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "config": r.config,
            "create_time": r.create_time,
        })
    return result


async def update_format_rule(rule_id: str, tenant_id: str, data: dict) -> dict:
    """Update a format rule."""
    e, rule = CollaborationFormatRuleService.get_by_id(rule_id)
    if not e:
        raise LookupError("Format rule not found")
    if rule.tenant_id != tenant_id:
        raise PermissionError("Access denied")

    update_data = {}
    for key in ("name", "description", "config"):
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
    if rule.tenant_id != tenant_id:
        raise PermissionError("Access denied")
    CollaborationFormatRuleService.delete_by_id(rule_id)
    return True
