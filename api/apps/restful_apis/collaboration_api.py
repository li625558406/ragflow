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

import logging
from urllib.parse import quote

from quart import Response, request
from api.apps import login_required, current_user
from api.apps.services import collaboration_api_service
from api.utils.api_utils import get_error_argument_result, get_json_result, get_request_json, validate_request
from common.constants import RetCode
from common.exceptions import ArgumentException


@manager.route("/collaboration/documents", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name", "markdown_content")
async def create_document():
    req = await get_request_json()
    name = req.get("name", "").strip()
    markdown_content = req.get("markdown_content", "")
    agent_id = req.get("agent_id")
    permission = req.get("permission", "me")

    if not name:
        return get_error_argument_result("name is required")

    try:
        doc = await collaboration_api_service.create_document(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=name,
            markdown_content=markdown_content,
            agent_id=agent_id,
            permission=permission,
        )
        return get_json_result(data=doc)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/spreadsheet", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name")
async def create_spreadsheet():
    req = await get_request_json()
    name = req.get("name", "").strip()
    permission = req.get("permission", "me")
    folder_id = req.get("folder_id")
    if not name:
        return get_error_argument_result("name is required")
    try:
        doc = await collaboration_api_service.create_spreadsheet(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=name,
            permission=permission,
            folder_id=folder_id,
        )
        return get_json_result(data=doc)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents", methods=["GET"])  # noqa: F821
@login_required
async def list_documents():
    try:
        docs = await collaboration_api_service.list_documents(
            tenant_id=current_user.id,
            user_id=current_user.id,
        )
        return get_json_result(data=docs)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_document(doc_id):
    try:
        doc = await collaboration_api_service.get_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=doc)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_document(doc_id):
    req = await get_request_json()
    try:
        doc = await collaboration_api_service.update_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
            data=req,
        )
        return get_json_result(data=doc)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/ydoc", methods=["PUT"])  # noqa: F821
@login_required
async def save_ydoc_state(doc_id):
    """Save Yjs binary state for real-time collaboration persistence."""
    req = await get_request_json()
    try:
        result = await collaboration_api_service.save_ydoc_state(
            doc_id=doc_id,
            tenant_id=current_user.id,
            data=req,
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_document(doc_id):
    try:
        await collaboration_api_service.delete_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/download", methods=["GET"])  # noqa: F821
@login_required
async def download_document(doc_id):
    file_type = request.args.get("type", "docx")
    try:
        blob, filename, mimetype = await collaboration_api_service.download_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
            file_type=file_type,
        )
        return Response(
            blob,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    except ValueError as e:
        return get_error_argument_result(str(e))
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/apply-rule", methods=["POST"])  # noqa: F821
@login_required
async def apply_format_rule(doc_id):
    req = await get_request_json()
    rule_id = req.get("rule_id")
    if not rule_id:
        return get_error_argument_result("rule_id is required")
    try:
        blob, filename, mimetype = await collaboration_api_service.apply_format_rule(
            doc_id=doc_id,
            tenant_id=current_user.id,
            rule_id=rule_id,
        )
        return Response(
            blob,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/format-rules", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name")
async def create_format_rule():
    req = await get_request_json()
    permission = req.get("permission", "me")
    try:
        rule = await collaboration_api_service.create_format_rule(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req.get("name", "").strip(),
            description=req.get("description", ""),
            config=req.get("config"),
            permission=permission,
        )
        return get_json_result(data=rule)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/format-rules", methods=["GET"])  # noqa: F821
@login_required
async def list_format_rules():
    try:
        rules = await collaboration_api_service.list_format_rules(
            tenant_id=current_user.id,
            user_id=current_user.id,
        )
        return get_json_result(data=rules)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/format-rules/<rule_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_format_rule(rule_id):
    req = await get_request_json()
    try:
        rule = await collaboration_api_service.update_format_rule(
            rule_id=rule_id,
            tenant_id=current_user.id,
            data=req,
        )
        return get_json_result(data=rule)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/format-rules/<rule_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_format_rule(rule_id):
    try:
        await collaboration_api_service.delete_format_rule(
            rule_id=rule_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Folder API ──

@manager.route("/collaboration/folders", methods=["POST"])  # noqa: F821
@login_required
@validate_request("name")
async def create_folder():
    req = await get_request_json()
    try:
        folder = await collaboration_api_service.create_folder(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req["name"],
            parent_id=req.get("parent_id"),
        )
        return get_json_result(data=folder)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/folders", methods=["GET"])  # noqa: F821
@login_required
async def list_folders():
    try:
        folders = await collaboration_api_service.list_folders(
            tenant_id=current_user.id,
            user_id=current_user.id,
        )
        return get_json_result(data=folders)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/folders/<folder_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_folder(folder_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.update_folder(
            folder_id=folder_id,
            tenant_id=current_user.id,
            data=req,
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/folders/<folder_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_folder(folder_id):
    try:
        await collaboration_api_service.delete_folder(
            folder_id=folder_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Document Move API ──

@manager.route("/collaboration/documents/<doc_id>/move", methods=["POST"])  # noqa: F821
@login_required
async def move_document(doc_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.move_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
            folder_id=req.get("folder_id"),
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Document Import API ──

@manager.route("/collaboration/documents/import", methods=["POST"])  # noqa: F821
@login_required
async def import_document():
    files = await request.files
    file_obj = files.get("file")
    if not file_obj:
        return get_error_argument_result("file is required")
    folder_id = (await request.form).get("folder_id")
    try:
        filename = (file_obj.filename or "").lower()
        content_type = file_obj.content_type or ""
        is_excel = (
            filename.endswith(('.xlsx', '.xls'))
            or 'spreadsheet' in content_type
            or 'excel' in content_type
        )
        if is_excel:
            doc = await collaboration_api_service.import_xlsx(
                tenant_id=current_user.id,
                user_id=current_user.id,
                file_obj=file_obj,
                folder_id=folder_id,
            )
        else:
            doc = await collaboration_api_service.import_docx(
                tenant_id=current_user.id,
                user_id=current_user.id,
                file_obj=file_obj,
                folder_id=folder_id,
            )
        return get_json_result(data=doc)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except RuntimeError as e:
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── ACL / Collaborator API ──

@manager.route("/collaboration/documents/<doc_id>/collaborators", methods=["GET"])  # noqa: F821
@login_required
async def list_collaborators(doc_id):
    try:
        collaborators = await collaboration_api_service.list_collaborators(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=collaborators)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/collaborators", methods=["POST"])  # noqa: F821
@login_required
@validate_request("user_id", "role")
async def add_collaborator(doc_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.add_collaborator(
            doc_id=doc_id,
            tenant_id=current_user.id,
            user_id=req["user_id"],
            role=req["role"],
        )
        return get_json_result(data=result)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/collaborators/<collaborator_user_id>", methods=["PUT"])  # noqa: F821
@login_required
@validate_request("role")
async def update_collaborator(doc_id, collaborator_user_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.update_collaborator_role(
            doc_id=doc_id,
            tenant_id=current_user.id,
            collaborator_user_id=collaborator_user_id,
            role=req["role"],
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/collaborators/<collaborator_user_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def remove_collaborator(doc_id, collaborator_user_id):
    try:
        await collaboration_api_service.remove_collaborator(
            doc_id=doc_id,
            tenant_id=current_user.id,
            collaborator_user_id=collaborator_user_id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Comment API ──

@manager.route("/collaboration/documents/<doc_id>/comments", methods=["GET"])  # noqa: F821
@login_required
async def list_comments(doc_id):
    try:
        comments = await collaboration_api_service.list_comments(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=comments)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/comments", methods=["POST"])  # noqa: F821
@login_required
async def create_comment(doc_id):
    req = await get_request_json()
    try:
        comment = await collaboration_api_service.create_comment(
            doc_id=doc_id,
            tenant_id=current_user.id,
            content=req.get("content", ""),
            parent_comment_id=req.get("parent_comment_id"),
            anchor_block_key=req.get("anchor_block_key"),
            anchor_offset_start=req.get("anchor_offset_start"),
            anchor_offset_end=req.get("anchor_offset_end"),
        )
        return get_json_result(data=comment)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/comments/<comment_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_comment(doc_id, comment_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.update_comment(
            doc_id=doc_id,
            comment_id=comment_id,
            tenant_id=current_user.id,
            content=req.get("content", ""),
        )
        return get_json_result(data=result)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/comments/<comment_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_comment(doc_id, comment_id):
    try:
        await collaboration_api_service.delete_comment(
            doc_id=doc_id,
            comment_id=comment_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/comments/<comment_id>/resolve", methods=["POST"])  # noqa: F821
@login_required
async def resolve_comment(doc_id, comment_id):
    try:
        result = await collaboration_api_service.resolve_comment(
            doc_id=doc_id,
            comment_id=comment_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/comments/<comment_id>/unresolve", methods=["POST"])  # noqa: F821
@login_required
async def unresolve_comment(doc_id, comment_id):
    try:
        result = await collaboration_api_service.unresolve_comment(
            doc_id=doc_id,
            comment_id=comment_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Version History API ──

@manager.route("/collaboration/documents/<doc_id>/versions", methods=["GET"])  # noqa: F821
@login_required
async def list_versions(doc_id):
    try:
        versions = await collaboration_api_service.list_versions(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=versions)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/versions/<version>/restore", methods=["POST"])  # noqa: F821
@login_required
async def restore_version(doc_id, version):
    try:
        result = await collaboration_api_service.restore_version(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=result)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Share Link API ──

@manager.route("/collaboration/documents/<doc_id>/share", methods=["POST"])  # noqa: F821
@login_required
async def create_or_update_share(doc_id):
    req = await get_request_json()
    try:
        result = await collaboration_api_service.create_or_update_share(
            doc_id=doc_id,
            tenant_id=current_user.id,
            permission=req.get("permission", "view"),
            password=req.get("password"),
            expires_at=req.get("expires_at"),
        )
        return get_json_result(data=result)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/share", methods=["GET"])  # noqa: F821
@login_required
async def get_share(doc_id):
    try:
        result = await collaboration_api_service.get_share(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=result)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/share", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_share(doc_id):
    try:
        await collaboration_api_service.delete_share(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Attachment API ──

@manager.route("/collaboration/documents/<doc_id>/attachments", methods=["POST"])  # noqa: F821
@login_required
async def upload_attachment(doc_id):
    files = await request.files
    file_obj = files.get("file")
    if not file_obj:
        return get_error_argument_result("file is required")
    try:
        result = await collaboration_api_service.upload_attachment(
            doc_id=doc_id,
            tenant_id=current_user.id,
            file_obj=file_obj,
        )
        return get_json_result(data=result)
    except ValueError as e:
        return get_error_argument_result(str(e))
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except RuntimeError as e:
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/attachments", methods=["GET"])  # noqa: F821
@login_required
async def list_attachments(doc_id):
    try:
        result = await collaboration_api_service.list_attachments(
            doc_id=doc_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=result)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/attachments/<attachment_id>", methods=["GET"])  # noqa: F821
@login_required
async def download_attachment(doc_id, attachment_id):
    try:
        data, filename, mimetype = await collaboration_api_service.download_attachment(
            doc_id=doc_id,
            attachment_id=attachment_id,
            tenant_id=current_user.id,
        )
        return Response(
            data,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except RuntimeError as e:
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/attachments/<attachment_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_attachment(doc_id, attachment_id):
    try:
        await collaboration_api_service.delete_attachment(
            doc_id=doc_id,
            attachment_id=attachment_id,
            tenant_id=current_user.id,
        )
        return get_json_result(data=True)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Audit Log API ──

@manager.route("/collaboration/documents/<doc_id>/audit-logs", methods=["GET"])  # noqa: F821
@login_required
async def list_audit_logs(doc_id):
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 50, type=int)
    try:
        result = await collaboration_api_service.list_audit_logs(
            doc_id=doc_id,
            tenant_id=current_user.id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return get_json_result(data=result)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


# ── Public Share Access (no login required) ──

@manager.route("/share/doc/<token>", methods=["GET"])  # noqa: F821
async def access_shared_doc(token):
    password = request.args.get("password")
    try:
        doc = await collaboration_api_service.access_shared_doc(
            token=token,
            password=password,
        )
        return get_json_result(data=doc)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)


@manager.route("/share/doc/<token>/verify", methods=["POST"])  # noqa: F821
async def verify_share_password(token):
    req = await get_request_json()
    password = req.get("password", "")
    try:
        doc = await collaboration_api_service.access_shared_doc(
            token=token,
            password=password,
        )
        return get_json_result(data=doc)
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.OPERATING_ERROR)
    except Exception as e:
        logging.error(e)
        return get_json_result(message=str(e), code=RetCode.SERVER_ERROR)
