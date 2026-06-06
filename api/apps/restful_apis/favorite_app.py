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

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.services.favorite_service import FavoriteService
from api.utils.api_utils import get_data_error_result, get_json_result
from common.constants import RetCode

manager = Blueprint("rest_favorite_app", __name__)


@manager.route("/favorite/save", methods=["POST"])
@login_required
async def save_favorite():
    try:
        req = await request.get_json()
    except Exception:
        return get_data_error_result(message="请求格式错误")

    title = (req.get("title") or "").strip()
    message_ids = req.get("message_ids") or []
    messages_data = req.get("messages_data") or []
    agent_id = req.get("agent_id")
    conversation_id = req.get("conversation_id")

    if not title:
        return get_data_error_result(message="标题不能为空")
    if not message_ids:
        return get_data_error_result(message="请至少选择一条消息")

    try:
        favorite = FavoriteService.create_favorite(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            title=title,
            message_ids=message_ids,
            messages_data=messages_data,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        return get_json_result(data=favorite.to_dict())
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/favorite/list", methods=["GET"])
@login_required
async def list_favorites():
    try:
        page = int(request.args.get("page", 1))
        items_per_page = int(request.args.get("items_per_page", 20))
    except (TypeError, ValueError):
        return get_data_error_result(message="分页参数格式错误")

    try:
        items, total = FavoriteService.get_user_favorites(
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            page_number=page,
            items_per_page=items_per_page,
        )
        return get_json_result(data={"items": items, "total": total})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/favorite/<favorite_id>", methods=["GET"])
@login_required
async def get_favorite(favorite_id: str):
    try:
        detail = FavoriteService.get_favorite_detail(favorite_id, current_user.tenant_id)
        if not detail:
            return get_json_result(message="收藏未找到", code=RetCode.NOT_FOUND)
        return get_json_result(data=detail)
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/favorite/<favorite_id>", methods=["PUT"])
@login_required
async def update_favorite(favorite_id: str):
    try:
        req = await request.get_json()
    except Exception:
        return get_data_error_result(message="请求格式错误")

    data = {}
    if "title" in req:
        title = (req["title"] or "").strip()
        if not title:
            return get_data_error_result(message="标题不能为空")
        data["title"] = title
    if "messages_data" in req:
        data["messages_data"] = req["messages_data"]

    if not data:
        return get_data_error_result(message="没有更新内容")

    try:
        result = FavoriteService.update_favorite(
            favorite_id, current_user.tenant_id, current_user.id, data
        )
        if not result:
            return get_json_result(message="收藏未找到或无权操作", code=RetCode.NOT_FOUND)
        return get_json_result(data=result)
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/favorite/<favorite_id>", methods=["DELETE"])
@login_required
async def delete_favorite(favorite_id: str):
    try:
        count = FavoriteService.delete_favorite(
            favorite_id, current_user.tenant_id, current_user.id
        )
        if count == 0:
            return get_json_result(message="收藏未找到或无权操作", code=RetCode.NOT_FOUND)
        return get_json_result(message="删除成功")
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/favorite/<favorite_id>/download", methods=["GET"])
@login_required
async def download_favorite(favorite_id: str):
    try:
        detail = FavoriteService.get_favorite_detail(favorite_id, current_user.tenant_id)
        if not detail:
            return get_json_result(message="收藏未找到", code=RetCode.NOT_FOUND)
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))

    messages = detail.get("messages_data") or []
    md_lines = [f"# {detail['title']}\n"]
    for m in messages:
        role_label = "用户" if m.get("role") == "user" else "助手"
        md_lines.append(f"## {role_label}\n\n{m.get('content', '')}\n")

    markdown_content = "\n".join(md_lines)
    safe_filename = detail["title"].replace("/", "_").replace("\\", "_")
    return Response(
        markdown_content,
        mimetype="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}.md"
        },
    )
