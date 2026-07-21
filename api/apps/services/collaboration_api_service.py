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

from common import settings
from api.db import TenantPermission
from api.db.db_models import (
    CollaborationAttachment,
    CollaborationAuditLog,
    CollaborationComment,
    CollaborationDocument,
    User,
    CollaborationDocumentACL,
    CollaborationFolder,
    CollaborationFormatRule,
    CollaborationShareLink,
    DB,
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
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
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

    # Team permission fallback: team-visible docs → editor role (full CRUD)
    if doc.permission == TenantPermission.TEAM.value:
        team_user_ids = _get_shared_tenant_user_ids(user_id)
        if doc.created_by in team_user_ids:
            return "editor"

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


async def create_spreadsheet(tenant_id: str, user_id: str, name: str, permission: str = "me", folder_id: str = None) -> dict:
    """Create a blank collaboration spreadsheet document."""
    doc_id = get_uuid()
    default_content = {
        "sheets": [{"name": "Sheet1", "data": [[""]], "colWidths": [100]}],
        "activeSheet": 0,
    }
    CollaborationDocumentService.save(
        id=doc_id,
        name=name,
        file_type="xlsx",
        content=default_content,
        markdown_content="",
        tenant_id=tenant_id,
        created_by=user_id,
        permission=permission,
        folder_id=folder_id,
    )
    return {"id": doc_id, "name": name, "file_type": "xlsx", "permission": permission, "folder_id": folder_id}


def _generate_xlsx(content: dict) -> bytes:
    """Generate .xlsx bytes from spreadsheet content.

    Supports both the new native IWorkbookData format (preferred) and the
    legacy `{sheets:[{name,data,colWidths}]}` grid format for documents
    created before the M1 spreadsheet upgrade. Conversion is delegated to
    spreadsheet_xlsx_adapter so all openpyxl↔Univer mapping lives in one place.
    """
    from api.apps.services.spreadsheet_xlsx_adapter import workbook_data_to_xlsx

    return workbook_data_to_xlsx(content or {})


async def import_xlsx(tenant_id: str, user_id: str, file_obj, folder_id: str = None) -> dict:
    """Parse a .xlsx file and create a collaboration spreadsheet document.

    Storage format: native Univer IWorkbookData (since M1). Legacy documents
    continue to render via the frontend's convertLegacyToWorkbookData fallback.
    """
    from api.apps.services.spreadsheet_xlsx_adapter import (
        XlsxTooLargeError,
        xlsx_to_workbook_data,
    )

    doc_id = get_uuid()
    name = (file_obj.filename or "imported").rsplit(".", 1)[0]

    data = file_obj.read()
    # xlsx_to_workbook_data enforces MAX_XLSX_SIZE / MAX_ROWS / MAX_COLS
    # and raises XlsxTooLargeError (a ValueError subclass) — the API layer
    # maps that to a 4xx for the frontend to render a friendly Toast.
    content = xlsx_to_workbook_data(data, doc_id)

    CollaborationDocumentService.save(
        id=doc_id,
        name=name,
        file_type="xlsx",
        content=content,
        markdown_content="",
        tenant_id=tenant_id,
        created_by=user_id,
        permission="me",
        folder_id=folder_id,
    )
    await log_audit(tenant_id, doc_id, "document.import_xlsx", {"name": name})

    return {"id": doc_id, "name": name, "file_type": "xlsx", "folder_id": folder_id}


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
    """下载最近一次导出的文件。不再在服务端生成 docx/pdf。

    前端 Univer Docs SDK 导出后通过 /exported-file 端点上传，
    后端只存文件不生成。返回 None 表示尚未导出过。
    """
    return await get_exported_file(doc_id, tenant_id)


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

    # Batch-fetch user names for all commenters
    user_ids = list({c.user_id for c in comments})
    user_map: dict[str, str] = {}
    if user_ids:
        users = User.select(User.id, User.nickname).where(User.id.in_(user_ids))
        user_map = {u.id: u.nickname for u in users}

    return [
        {
            "id": c.id,
            "document_id": c.document_id,
            "user_id": c.user_id,
            "user_name": user_map.get(c.user_id, c.user_id),
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
    # Fetch user name for the creator
    user_name = tenant_id
    try:
        users = User.select(User.id, User.nickname).where(User.id == tenant_id)
        if users:
            user_name = users[0].nickname
    except Exception:
        pass

    return {
        "id": comment_id,
        "document_id": doc_id,
        "user_id": tenant_id,
        "user_name": user_name,
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


# ── Spreadsheet image asset proxy (P4) ─────────────────────────────────
# xlsx adapter uploads floating images to MinIO bucket 'collaboration' under
# key 'docs/{doc_id}/images/{asset_id}.{ext}'. The frontend Univer drawing
# plugin renders them via <img src> which can't carry Authorization headers,
# so the asset endpoint authenticates via ?token=<jwt> query param.
_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "tiff": "image/tiff",
    "ico": "image/x-icon",
}


def list_doc_asset_ids(doc_id: str) -> list[str]:
    """Return all asset_ids (uuid) for a doc, by scanning its storage prefix.

    Used by document delete to clean up orphaned images in MinIO.
    """
    # The STORAGE_IMPL interface has no list() method; we rely on callers
    # tracking assets via the workbook's SHEET_DRAWING_PLUGIN resource.
    return []


def get_doc_asset(doc_id: str, asset_id: str, tenant_id: str) -> tuple[bytes, str]:
    """Fetch a spreadsheet image asset from MinIO.

    Returns (bytes, mimetype). Raises LookupError if not found, PermissionError
    if the caller lacks read access to the document.
    """
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")

    # Try each supported extension — the asset_id alone doesn't carry the ext.
    for ext, mime in _EXT_TO_MIME.items():
        storage_key = f"docs/{doc_id}/images/{asset_id}.{ext}"
        try:
            data = settings.STORAGE_IMPL.get("collaboration", storage_key)
            if data:
                return data, mime
        except Exception:
            continue
    raise LookupError(f"Asset {asset_id} not found for document {doc_id}")


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
        CollaborationAuditLog.select(
            CollaborationAuditLog.id,
            CollaborationAuditLog.user_id,
            CollaborationAuditLog.action,
            CollaborationAuditLog.detail,
            CollaborationAuditLog.ip_address,
            CollaborationAuditLog.create_time,
        )
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

    # Collect unique user_ids and resolve nicknames
    user_ids = list({log.user_id for log in logs if log.user_id})
    nickname_map = {}
    if user_ids:
        rows = (
            User.select(UserTenant.user_id, User.nickname)
            .join(UserTenant, on=(User.id == UserTenant.user_id))
            .where(UserTenant.tenant_id.in_(user_ids))
        )
        nickname_map = {row.user_id: row.nickname for row in rows}

    return {
        "total": total,
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "user_name": nickname_map.get(log.user_id, log.user_id),
                "action": log.action,
                "detail": log.detail,
                "ip_address": log.ip_address,
                "create_time": log.create_time,
            }
            for log in logs
        ],
    }


async def save_exported_file(doc_id: str, tenant_id: str, blob: bytes, fmt: str) -> dict:
    """前端导出 docx/pdf 后上传 blob，存到 STORAGE_IMPL，更新 file_path。

    后端不做任何格式生成，只存文件。Univer Docs 的导出在前端浏览器跑。
    """
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")
    if fmt not in ("docx", "pdf"):
        raise ValueError(f"Unsupported format: {fmt}")
    storage_key = f"{doc_id}.{fmt}"
    settings.STORAGE_IMPL.put("collaboration", storage_key, blob)
    CollaborationDocumentService.update_by_id(
        doc_id, {"file_path": storage_key, "file_type": fmt}
    )
    return {"file_path": storage_key, "size": len(blob)}


async def get_exported_file(doc_id: str, tenant_id: str) -> tuple | None:
    """返回最近一次导出的 (blob_bytes, filename, mimetype)，无则 None。"""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")
    storage_key = doc.file_path
    if not storage_key:
        return None
    ext = storage_key.rsplit(".", 1)[-1].lower()
    if ext == "docx":
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif ext == "pdf":
        mimetype = "application/pdf"
    else:
        mimetype = "application/octet-stream"
    blob = settings.STORAGE_IMPL.get("collaboration", storage_key)
    filename = f"{doc.name}.{ext}"
    return blob, filename, mimetype
