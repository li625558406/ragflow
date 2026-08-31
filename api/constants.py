#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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

NAME_LENGTH_LIMIT = 2**10

IMG_BASE64_PREFIX = "data:image/png;base64,"

API_VERSION = "v1"
RAG_FLOW_SERVICE_NAME = "ragflow"
REQUEST_WAIT_SEC = 2
REQUEST_MAX_WAIT_SEC = 300

DATASET_NAME_LIMIT = 128
FILE_NAME_LEN_LIMIT = 255
MEMORY_NAME_LIMIT = 128
MEMORY_SIZE_LIMIT = 10*1024*1024 # Byte

# ---------------------------------------------------------------------------
# 权限管控（角色制 RBAC）—— 模块权限点常量（单一来源，前端引用同一套 key）
# 注：权限常量直接并入本模块，不建 api/constants/ 包 —— 因为本项目 api/constants.py
# 本身就是模块（含 API_VERSION），建同名包会遮蔽它导致 from api.constants import API_VERSION 失败。
# ---------------------------------------------------------------------------

# 权限点 key -> 中文名（用于前端展示 + seed 进 permission_role_permission）
MODULE_PERMISSIONS = {
    "bid": "标讯管理",
    "dataset": "知识库",
    "chat": "对话",
    "search": "搜索",
    "agent": "Agent 画布/流程",
    "memory": "记忆",
    "file": "文件",
    "crawler": "智能采集",
    "user_setting": "用户设置",
    "home": "C 端着陆页",
    "c_chat": "投标助手对话",
    "permission_manage": "权限管理",
    "hr_manage": "人事管理",
    "hr_finance": "薪资财务",
}

# 内置角色名
SUPER_ROLE_NAME = "超级管理员"
NORMAL_ROLE_NAME = "普通用户"

# 普通用户默认勾选的模块权限点
NORMAL_ROLE_PERMISSIONS = ["bid", "chat", "c_chat", "home", "user_setting"]

# 权限缓存 TTL（秒）与 key 前缀
PERMISSION_CACHE_TTL = 600
PERMISSION_CACHE_PREFIX = "perm:"
