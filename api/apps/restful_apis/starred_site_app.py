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

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.services.starred_site_service import StarredSiteService
from api.utils.api_utils import get_data_error_result, get_json_result
from common.constants import RetCode

manager = Blueprint("rest_starred_site_app", __name__)


@manager.route("/starred_sites", methods=["GET"])
@login_required
async def list_starred_sites():
    try:
        items = StarredSiteService.get_user_starred_sites(
            tenant_id=current_user.id,
            user_id=current_user.id,
        )
        return get_json_result(data={"items": items})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/starred_sites", methods=["POST"])
@login_required
async def create_starred_site():
    try:
        req = await request.get_json()
    except Exception:
        return get_data_error_result(message="请求格式错误")

    site_name = (req.get("site_name") or "").strip()
    site_url = (req.get("site_url") or "").strip()

    if not site_name:
        return get_data_error_result(message="网站名称不能为空")
    if not site_url:
        return get_data_error_result(message="网站URL不能为空")

    try:
        # Check for duplicates
        existing = StarredSiteService.get_by_site_url(
            tenant_id=current_user.id,
            user_id=current_user.id,
            site_url=site_url,
        )
        if existing:
            return get_json_result(data=existing.to_dict())

        obj = StarredSiteService.create_starred_site(
            tenant_id=current_user.id,
            user_id=current_user.id,
            site_name=site_name,
            site_url=site_url,
        )
        return get_json_result(data=obj.to_dict())
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/starred_sites/<site_id>", methods=["DELETE"])
@login_required
async def delete_starred_site(site_id: str):
    try:
        count = StarredSiteService.delete_starred_site(
            site_id=site_id,
            tenant_id=current_user.id,
            user_id=current_user.id,
        )
        if count == 0:
            return get_json_result(message="点赞记录未找到或无权操作", code=RetCode.NOT_FOUND)
        return get_json_result(message="取消点赞成功")
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))
