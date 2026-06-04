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
import json
import logging
import os
from abc import ABC

from agent.tools.base import ToolParamBase, ToolBase
from common.connection_utils import timeout


class CourtShixinSearchParam(ToolParamBase):
    def __init__(self):
        self.meta: dict = {
            "name": "court_shixin_search",
            "description": (
                "查询全国法院失信被执行人名单信息。"
                "可用于核查企业或个人是否存在失信记录（如被列入失信被执行人名单），"
                "辅助投标风险评估。数据来源：中国执行信息公开网 (zxgk.court.gov.cn)。"
            ),
            "parameters": {
                "name": {
                    "type": "string",
                    "description": "被执行人姓名或企业名称（至少2个汉字），与证件号二选一",
                    "required": False,
                },
                "card_num": {
                    "type": "string",
                    "description": "身份证号码（15或18位）或统一社会信用代码（18位），与姓名二选一",
                    "required": False,
                },
                "province": {
                    "type": "string",
                    "description": (
                        "查询省份范围，默认'0'（全国）。"
                        "可选值：11=北京,12=天津,13=河北,14=山西,15=内蒙古,21=辽宁,22=吉林,23=黑龙江,"
                        "31=上海,32=江苏,33=浙江,34=安徽,35=福建,36=江西,37=山东,41=河南,"
                        "42=湖北,43=湖南,44=广东,45=广西,46=海南,50=重庆,51=四川,52=贵州,"
                        "53=云南,54=西藏,61=陕西,62=甘肃,63=青海,64=宁夏,65=新疆"
                    ),
                    "required": False,
                },
            },
        }
        super().__init__()

    def get_input_form(self) -> dict[str, dict]:
        return {
            "name": {
                "name": "Name",
                "type": "line",
                "description": "被执行人姓名/名称",
            },
            "card_num": {
                "name": "CardNum",
                "type": "line",
                "description": "身份证号/统一社会信用代码",
            },
            "province": {
                "name": "Province",
                "type": "line",
                "description": "省份代码 (0=全国)",
            },
        }

    def check(self):
        pass


class CourtShixinSearch(ToolBase, ABC):
    component_name = "CourtShixinSearch"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 120)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("CourtShixinSearch processing"):
            return

        from api.utils.court_shixin_client import search_shixin

        try:
            name = kwargs.get("name", "")
            card_num = kwargs.get("card_num", "")
            province = kwargs.get("province", "0")

            if not name and not card_num:
                self.set_output("_ERROR", "姓名和证件号至少填写一项")
                return "错误：姓名和证件号至少填写一项"

            result = search_shixin(
                name=str(name),
                card_num=str(card_num),
                province=str(province),
            )

            if result.error:
                self.set_output("_ERROR", result.error)
                return f"查询失败：{result.error}"

            # Format output
            summary_parts = [f"共找到 {result.total_size} 条失信记录（第{result.current_page}页）\n"]
            for r in result.results:
                summary_parts.append(
                    f"{r.index}. {r.name} | 立案日期: {r.reg_date} | 案号: {r.case_code}"
                )

            summary = "\n".join(summary_parts)

            # Structured JSON for downstream components
            structured = []
            for r in result.results:
                structured.append({
                    "index": r.index,
                    "name": r.name,
                    "reg_date": r.reg_date,
                    "case_code": r.case_code,
                })

            self.set_output("json", {"total": result.total_size, "page": result.current_page, "items": structured})
            self.set_output("formalized_content", summary)
            return self.output("formalized_content")

        except Exception as e:
            logging.exception("CourtShixinSearch error: %s", e)
            self.set_output("_ERROR", str(e))
            return f"CourtShixinSearch error: {e}"

    def thoughts(self) -> str:
        return "正在查询全国法院失信被执行人名单..."
