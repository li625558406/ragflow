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
#  WITHOUT WARRANTIES OR CONDITIONS OR ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""全国法院失信被执行人查询 REST API

数据来源：中国执行信息公开网 (zxgk.court.gov.cn)
使用 Playwright + ddddocr 自动化查询。
"""
import logging

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.utils.api_utils import get_data_error_result, get_json_result

logger = logging.getLogger(__name__)

manager = Blueprint("rest_court_app", __name__)


@manager.route("/court/shixin/search", methods=["GET"])  # noqa: F821
@login_required
async def search_shixin():
    """查询失信被执行人

    GET /api/v1/court/shixin/search?name=张三&province=0
    GET /api/v1/court/shixin/search?card_num=110101199001011234
    """
    name = request.args.get("name", "").strip()
    card_num = request.args.get("card_num", "").strip()
    province = request.args.get("province", "0").strip()

    if not name and not card_num:
        return get_data_error_result("姓名和证件号至少填写一项")

    from api.utils.court_shixin_client import search_shixin as do_search

    # Run the sync Playwright code in a thread to avoid blocking the async event loop
    import asyncio

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: do_search(name=name, card_num=card_num, province=province),
    )

    if result.error:
        return get_data_error_result(result.error)

    items = []
    for r in result.results:
        items.append({
            "index": r.index,
            "name": r.name,
            "reg_date": r.reg_date,
            "case_code": r.case_code,
            "id": r.id,
        })

    return get_json_result(data={
        "total": result.total_size,
        "current_page": result.current_page,
        "items": items,
    })
