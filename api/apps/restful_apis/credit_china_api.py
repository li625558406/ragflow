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
"""信用中国严重失信主体查询 REST API

数据来源：信用中国 (creditchina.gov.cn)
使用 Playwright + ddddocr 自动化查询。
"""
import logging

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.utils.api_utils import get_data_error_result, get_json_result

logger = logging.getLogger(__name__)

manager = Blueprint("rest_credit_china_app", __name__)


@manager.route("/credit-china/search", methods=["GET"])  # noqa: F821
@login_required
async def search_credit_china():
    """查询严重失信主体

    GET /api/v1/credit-china/search?keyword=xxx&type=shixinheimingdan
    """
    keyword = request.args.get("keyword", "").strip()
    page = request.args.get("page", "1").strip()
    page_size = request.args.get("pageSize", "10").strip()

    if not keyword:
        return get_data_error_result("请输入查询关键词")

    from api.utils.credit_china_client import search_credit_china as do_search

    import asyncio

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: do_search(
            keyword=keyword,
            page_num=int(page),
            page_size=int(page_size),
        ),
    )

    if result.error:
        return get_data_error_result(result.error)

    items = []
    for r in result.results:
        items.append({
            "index": r.index,
            "name": r.name,
            "type": r.type,
            "date": r.date,
        })

    return get_json_result(data={
        "total": result.total_size,
        "items": items,
    })
