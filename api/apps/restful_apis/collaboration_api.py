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

    if not name:
        return get_error_argument_result("name is required")

    try:
        doc = await collaboration_api_service.create_document(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=name,
            markdown_content=markdown_content,
            agent_id=agent_id,
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
            headers={"Content-Disposition": f"attachment; filename={filename}"},
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
            headers={"Content-Disposition": f"attachment; filename={filename}"},
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
    try:
        rule = await collaboration_api_service.create_format_rule(
            tenant_id=current_user.id,
            user_id=current_user.id,
            name=req.get("name", "").strip(),
            description=req.get("description", ""),
            config=req.get("config"),
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
