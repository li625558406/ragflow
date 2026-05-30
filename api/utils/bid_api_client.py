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
"""世舶科技标讯信息 API 客户端

阿里云 API Gateway AppCode 认证。

环境变量:
    BID_API_BASE_URL: API 基础地址 (默认: https://gov.market.alicloudapi.com)
    BID_APP_CODE:     AppCode 鉴权码
    BID_APP_KEY:      预留
    BID_APP_SECRET:   预留
"""

import json
import logging
import os
from typing import Optional

import requests

BID_API_BASE_URL = os.getenv("BID_API_BASE_URL", "https://gov.market.alicloudapi.com")
BID_APP_CODE = os.getenv("BID_APP_CODE", "")


class BidApiError(Exception):
    """API 调用异常"""

    def __init__(self, message: str, code: int = -1, raw: dict = None):
        super().__init__(message)
        self.code = code
        self.raw = raw or {}


class BidApiClient:
    """世舶科技标讯 API 客户端（阿里云 API Gateway 2025 版）"""

    def __init__(
        self,
        base_url: str = None,
        app_code: str = None,
        timeout: int = 60,
    ):
        self.base_url = (base_url or BID_API_BASE_URL).rstrip("/")
        self.app_code = app_code or BID_APP_CODE
        self.timeout = timeout
        if not self.app_code:
            logging.warning("BidApiClient: BID_APP_CODE is not set! External API calls will fail.")
        else:
            logging.info("BidApiClient: initialized with base_url=%s, app_code=%s...", self.base_url, self.app_code[:8])

    def _post_json(self, endpoint: str, payload: dict) -> dict:
        """发送 JSON POST 请求（搜索类接口）。"""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": f"APPCODE {self.app_code}",
        }
        try:
            resp = requests.post(url, data=json.dumps(payload, ensure_ascii=False), headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            raise BidApiError(f"Request timeout ({self.timeout}s): {endpoint}")
        except requests.exceptions.RequestException as e:
            raise BidApiError(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            raise BidApiError(f"Invalid JSON response: {e}")

        code = data.get("code", -1)
        if code != 200:
            raise BidApiError(
                data.get("msg", data.get("subMsg", "Unknown error")),
                code=code,
                raw=data,
            )
        return data

    def _post_form(self, endpoint: str, data: dict) -> dict:
        """发送 form-encoded POST 请求（详情/结构化/附件类接口）。"""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Authorization": f"APPCODE {self.app_code}",
        }
        try:
            resp = requests.post(url, data=data, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            result = resp.json()
        except requests.exceptions.Timeout:
            raise BidApiError(f"Request timeout ({self.timeout}s): {endpoint}")
        except requests.exceptions.RequestException as e:
            raise BidApiError(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            raise BidApiError(f"Invalid JSON response: {e}")

        code = result.get("code")
        state = result.get("state")
        sub_code = result.get("subCode")
        if code is not None and code != 200:
            raise BidApiError(
                result.get("msg", result.get("subMsg", "Unknown error")),
                code=code,
                raw=result,
            )
        if state is not None and state != 1:
            raise BidApiError(
                result.get("msg", result.get("subMsg", "Unknown error")),
                code=state,
                raw=result,
            )
        if sub_code is not None and sub_code != "0000000000":
            raise BidApiError(
                result.get("subMsg", "Unknown error"),
                code=-1,
                raw=result,
            )
        return result

    # ------------------------------------------------------------------
    # 搜索接口（JSON body）
    # ------------------------------------------------------------------

    def search_project(
        self,
        keyword: str = "",
        exclude_kw: str = "",
        include_kw: str = "",
        source_type: str = "",
        class_id: str = "-100",
        project_class_id: str = "",
        search_mode: int = 1,
        area_code: dict = None,
        industry_code: dict = None,
        start_date: str = "",
        end_date: str = "",
        contract_end_min: str = "",
        contract_end_max: str = "",
        part_a_name: str = "",
        part_b_name: str = "",
        agent_name: str = "",
        project_money_min: int = None,
        project_money_max: int = None,
        file_flag: int = -1,
        purchase_type_id: str = "",
        user_id: int = 88,
        page_id: int = 1,
        page_number: int = 20,
        search_type: int = 1,
    ) -> dict:
        """招标采购信息搜索（JSON body → /bid/searchProjectApi2025）

        返回 data.data 为列表, data.total 为总数, data.hasNext 指示是否有更多页。
        """
        payload = {
            "keyword": keyword,
            "excludeKW": exclude_kw,
            "inCludeKW": include_kw,
            "sourceType": source_type,
            "classID": class_id,
            "projectClassID": project_class_id,
            "searchMode": search_mode,
            "areaCode": area_code or {"proviceCodeList": ["0"], "cityCodeList": [], "countyCodeList": []},
            "industryCode": industry_code or {"firstCodeList": ["0"], "secondCodeList": [], "thirdCodeList": []},
            "startDate": start_date,
            "endDate": end_date,
            "contractEndMin": contract_end_min,
            "contractEndMax": contract_end_max,
            "partAName": part_a_name,
            "partBName": part_b_name,
            "agentName": agent_name,
            "projectMoneyMin": project_money_min,
            "projectMoneyMax": project_money_max,
            "fileFlag": file_flag,
            "purchaseTypeID": purchase_type_id,
            "userID": user_id,
            "pageID": page_id,
            "pageNumber": page_number,
            "searchType": search_type,
        }
        payload = {k: v for k, v in payload.items() if v is not None and v != ""}
        return self._post_json("/bid/searchProjectApi2025", payload)

    # ------------------------------------------------------------------
    # 详情 / 结构化 / 附件接口（form-encoded）
    # ------------------------------------------------------------------

    def get_detail(self, project_id: int, publish_time: str) -> dict:
        """获取招标采购信息正文（form-encoded → /bid/getProject2025）

        返回: data.content (HTML正文), data.projectFiles (附件列表)
        """
        return self._post_form(
            "/bid/getProject2025",
            data={"id": str(project_id), "publishTime": publish_time},
        )

    def get_structure(self, project_id: int, publish_time: str) -> dict:
        """获取项目结构化数据（form-encoded → /bid/getZTBStructreDetail2025）

        返回: 项目名称、编码、金额、甲乙代理方、投标企业等
        """
        return self._post_form(
            "/bid/getZTBStructreDetail2025",
            data={"id": str(project_id), "publishTime": publish_time},
        )

    def get_files(self, project_id: int, publish_time: str) -> list:
        """获取项目附件列表（form-encoded → /bid/getProjectFiles2025）

        返回: 附件列表（含下载地址、文件类型、大小等）
        """
        result = self._post_form(
            "/bid/getProjectFiles2025",
            data={"projectId": str(project_id), "publishTime": publish_time},
        )
        return result.get("data", [])
