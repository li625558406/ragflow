#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
"""写操作 owner-only 门禁（2026-09-09 团队权限隔离移除）。

读操作全局放开，写操作仅资源所有者可执行；权限管控后续由
/permission 页面的 RBAC（@permission_required）承接。
原团队（user_tenant join）判断已移除。
"""

from api.db.db_models import File, Knowledgebase


def check_kb_team_permission(kb: dict | Knowledgebase, other: str) -> bool:
    """KB 写操作门禁：仅 KB 所有者（tenant_id 相同）可执行。"""
    kb = kb.to_dict() if isinstance(kb, Knowledgebase) else kb
    if not kb or not other:
        return False
    return kb.get("tenant_id") == other


def check_file_team_permission(file: dict | File, other: str) -> bool:
    """文件写操作门禁：仅文件所有者（tenant_id 相同）可执行。"""
    file = file.to_dict() if isinstance(file, File) else file
    if not file or not other:
        return False
    return file.get("tenant_id") == other
