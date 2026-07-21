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
        result = await collaboration_api_service.download_document(
            doc_id=doc_id,
            tenant_id=current_user.id,
            file_type=file_type,
        )
        if not result:
            return get_json_result(
                message="No exported file yet. Please export from editor first.",
                code=RetCode.NOT_FOUND,
            )
        blob, filename, mimetype = result
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


@manager.route("/collaboration/documents/<doc_id>/exported-file", methods=["POST"])  # noqa: F821
@login_required
async def upload_exported_file(doc_id):
    """前端导出 docx/pdf 后上传，后端只存文件不生成。"""
    tenant_id = current_user.id
    fmt = (request.args.get("format") or "docx").lower()
    blob = await request.get_data()
    try:
        result = await collaboration_api_service.save_exported_file(
            doc_id, tenant_id, blob, fmt
        )
        return get_json_result(data=result)
    except LookupError as ex:
        return get_json_result(message=str(ex), code=RetCode.NOT_FOUND)
    except PermissionError as ex:
        return get_json_result(message=str(ex), code=RetCode.FORBIDDEN)
    except ValueError as ex:
        return get_json_result(message=str(ex), code=RetCode.ARGUMENT_ERROR)
    except Exception as ex:
        logging.error(ex)
        return get_json_result(message=str(ex), code=RetCode.SERVER_ERROR)


@manager.route("/collaboration/documents/<doc_id>/exported-file", methods=["GET"])  # noqa: F821
@login_required
async def download_exported_file(doc_id):
    """下载最近一次导出的文件。无则返回 404 JSON。"""
    tenant_id = current_user.id
    try:
        result = await collaboration_api_service.get_exported_file(doc_id, tenant_id)
    except LookupError as ex:
        return get_json_result(message=str(ex), code=RetCode.NOT_FOUND)
    except PermissionError as ex:
        return get_json_result(message=str(ex), code=RetCode.FORBIDDEN)
    except Exception as ex:
        logging.error(ex)
        return get_json_result(message=str(ex), code=RetCode.SERVER_ERROR)
    if not result:
        return get_json_result(
            message="No exported file yet. Please export from editor first.",
            code=RetCode.NOT_FOUND,
        )
    blob, filename, mimetype = result
    quoted_filename = quote(filename)
    response = Response(blob, mimetype=mimetype)
    response.headers.add(
        "Content-Disposition",
        f"attachment; filename*=UTF-8''{quoted_filename}",
    )
    return response


@manager.route("/collaboration/documents/<doc_id>/assets/<asset_id>", methods=["GET"])  # noqa: F821
async def get_document_asset(doc_id, asset_id):
    """Serve a spreadsheet image asset from MinIO.

    Auth via ?token=<jwt> query param (not @login_required) because the URL
    is consumed by Univer's <img src>, which cannot carry Authorization
    headers. The token is the same JWT used everywhere else.
    """
    from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
    from common import settings
    from api.db.services.user_token_service import UserTokenService
    from api.db.services import UserService
    from common.constants import StatusEnum

    # Resolve tenant_id from token — query param first, Authorization header second
    token = request.args.get("token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    # Frontend may pass the full "Bearer <jwt>" string in the query string;
    # strip the prefix so jwt.loads / DB lookup match.
    if token and token.lower().startswith("bearer "):
        token = token[7:]
    if not token:
        return get_json_result(message="Missing token", code=RetCode.UNAUTHORIZED)

    try:
        jwt = Serializer(secret_key=settings.SECRET_KEY)
        access_token = str(jwt.loads(token))
    except Exception:
        access_token = token  # raw token fallback

    user_token = UserTokenService.find_by_token(access_token)
    if user_token:
        users = UserService.query(id=user_token.user_id, status=StatusEnum.VALID.value)
    else:
        users = UserService.query(access_token=access_token, status=StatusEnum.VALID.value)
    if not users:
        return get_json_result(message="Invalid token", code=RetCode.UNAUTHORIZED)

    try:
        data, mimetype = collaboration_api_service.get_doc_asset(
            doc_id=doc_id,
            asset_id=asset_id,
            tenant_id=users[0].id,
        )
        # Cache-Control: immutable + 1 day so repeated <img> renders don't re-fetch.
        return Response(
            data,
            mimetype=mimetype,
            headers={
                "Cache-Control": "private, max-age=86400, immutable",
                "Content-Length": str(len(data)),
            },
        )
    except LookupError as e:
        return get_json_result(message=str(e), code=RetCode.NOT_FOUND)
    except PermissionError as e:
        return get_json_result(message=str(e), code=RetCode.UNAUTHORIZED)
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
        if not is_excel:
            return get_error_argument_result(
                "Only Excel files are supported via this endpoint. Use Univer Docs editor import instead."
            )
        doc = await collaboration_api_service.import_xlsx(
            tenant_id=current_user.id,
            user_id=current_user.id,
            file_obj=file_obj,
            folder_id=folder_id,
        )
        return get_json_result(data=doc)
    except ValueError as e:
        # XlsxTooLargeError is a ValueError subclass — surface its friendly
        # Chinese message (size/rows/cols exceeded) so the frontend can show
        # it as a Toast rather than a generic "invalid argument".
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
