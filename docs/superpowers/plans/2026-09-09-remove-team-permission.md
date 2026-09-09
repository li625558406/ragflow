# 移除团队权限隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉团队（team）权限隔离——读操作全局放开（所有人可见全部知识库/智能体/对话助手/搜索应用/文件），写操作 owner-only；后续权限管控由 /permission RBAC 承接。

**Architecture:** 逐点显式修改 9 类团队过滤点。Service 层 `get_by_tenant_ids`/`get_list` 系列增加「租户参数为 `None` 时跳过租户与 permission 过滤」语义；`accessible` 拆为读门禁（存在性检查）与写门禁（新增 `owned()`，owner-only）；`check_team_permission.py` 两个检查函数改为 owner-only。不动数据模型、不动前端。

**Tech Stack:** Python Quart + Peewee + MySQL；测试用 pytest（hermetic 单元测试 + py_compile 结构验证）。

**Spec:** `docs/superpowers/specs/2026-09-09-remove-team-permission-design.md`（含 accessible/owned 读写分离修订）

**关键约定（贯穿所有任务）：**
- Service 查询函数的 `joined_tenant_ids` 参数：传 `None` = 全局（不做租户/permission 过滤）；传列表 = 按 `tenant_id.in_(列表)` 精确过滤（**删除**原 `(in_ & team) | == user_id` 联合条件）
- 所有「编辑文件前必须重新读取」规则照常生效；行号为参考，以实际内容为准
- 提交信息用中文；不部署、不重启 Docker

**Files（改动总览）:**
- Modify: `api/common/check_team_permission.py`
- Modify: `api/db/services/knowledgebase_service.py`、`api/db/services/document_service.py`、`api/db/services/canvas_service.py`、`api/db/services/dialog_service.py`、`api/db/services/search_service.py`
- Modify: `api/apps/services/dataset_api_service.py`、`api/apps/services/file_api_service.py`
- Modify: `api/apps/restful_apis/agent_api.py`、`chat_api.py`、`search_api.py`、`document_api.py`、`chunk_api.py`
- Test: `test/test_check_team_permission.py`

---

### Task 1: check_team_permission.py 改 owner-only + 单元测试

`check_kb_team_permission` 唯一调用点是文档上传（document_api.py:440，写操作）；`check_file_team_permission` 调用点是文件删除（file_api_service.py:443）、移动（:491）、下载（:595，读——Task 5 会单独放开）。两者改为纯 owner 判断后不再查库，变为纯函数。

**Files:**
- Modify: `api/common/check_team_permission.py`
- Create: `test/test_check_team_permission.py`

- [ ] **Step 1: 写失败测试**

创建 `test/test_check_team_permission.py`：

```python
"""团队权限检查 owner-only 化的单元测试（改后为纯函数，无 DB 依赖）。

对抗性覆盖：owner 放行 / 非 owner 拒绝（含 team permission、me permission、
空字段、None other、伪造结构）。
"""
import sys
import types
from pathlib import Path

import pytest


def _import_module():
    """直接 import 会拉起 api.db 初始化链；沿用 test_permission_app.py 的桩注入模式。"""
    if "api.common.check_team_permission" in sys.modules:
        del sys.modules["api.common.check_team_permission"]

    # check_team_permission 顶层 from api.db import TenantPermission 等；
    # owner-only 化后这些 import 全部移除（见 Step 3），正常 import 不再触发重依赖。
    # 此处保留桩兜底：若模块仍残留 api.db 依赖则注入空壳，保证测试 hermetic。
    if "api.db" not in sys.modules:
        stub = types.ModuleType("api.db")
        stub.TenantPermission = types.SimpleNamespace(TEAM="team")
        sys.modules["api.db"] = stub

    file_path = Path("api/common/check_team_permission.py").resolve()
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_team_permission_under_test", file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_team_permission_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _import_module()


def test_kb_owner_allowed(mod):
    assert mod.check_kb_team_permission({"tenant_id": "u1", "permission": "team"}, "u1") is True


def test_kb_non_owner_denied_even_team_permission(mod):
    """原实现里 team permission + 已加入团队可过；现在一律 owner-only。"""
    assert mod.check_kb_team_permission({"tenant_id": "u1", "permission": "team"}, "u2") is False


def test_kb_non_owner_me_permission_denied(mod):
    assert mod.check_kb_team_permission({"tenant_id": "u1", "permission": "me"}, "u2") is False


def test_kb_missing_tenant_key_denied(mod):
    assert mod.check_kb_team_permission({"permission": "team"}, "u1") is False


def test_file_owner_allowed(mod):
    assert mod.check_file_team_permission({"tenant_id": "u1", "id": "f1"}, "u1") is True


def test_file_non_owner_denied(mod):
    assert mod.check_file_team_permission({"tenant_id": "u1", "id": "f1"}, "u2") is False


def test_file_missing_tenant_denied(mod):
    assert mod.check_file_team_permission({"id": "f1"}, "u1") is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /d/AI/ragflow2 && uv run pytest test/test_check_team_permission.py -v
```

预期：FAIL / ERROR（现实现走团队逻辑，`test_kb_non_owner_denied_even_team_permission` 等用例不通过或 import 失败）。

- [ ] **Step 3: 改写 check_team_permission.py 为 owner-only**

整个文件替换为（删除全部团队/DB 依赖）：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest test/test_check_team_permission.py -v
```

预期：7 passed。

- [ ] **Step 5: Commit**

```bash
git add api/common/check_team_permission.py test/test_check_team_permission.py
git commit -m "feat(api): 团队权限检查改 owner-only，移除 user_tenant 团队判断"
```

---

### Task 2: KnowledgebaseService / DocumentService 读写分离 + 全局查询语义

**Files:**
- Modify: `api/db/services/knowledgebase_service.py:136-198`（get_by_tenant_ids）、`:435-479`（get_list）、`:481-495`（accessible）
- Modify: `api/db/services/document_service.py:680-691`（accessible）

注意：该文件中 `TenantPermission`、`UserTenant` 等符号可能被其它函数使用，只删本次改动不再使用的 import（以 ruff 提示为准，Step 5 统一清理）。

- [ ] **Step 1: 改 `KnowledgebaseService.get_by_tenant_ids`（约 :136）**

将两处 `kbs = cls.model.select(*fields).join(User, ...).where(...)` 的租户/permission/status 复合 where 改为「None=全局」结构。改动后（keywords 分支合并为一个查询链）：

```python
        kbs = cls.model.select(*fields).join(User, on=(cls.model.tenant_id == User.id))
        if joined_tenant_ids is not None:
            kbs = kbs.where(cls.model.tenant_id.in_(joined_tenant_ids))
        kbs = kbs.where(cls.model.status == StatusEnum.VALID.value)
        if keywords:
            kbs = kbs.where(fn.LOWER(cls.model.name).contains(keywords.lower()))
```

（删除原 `((tenant_id.in_ & permission==TEAM) | tenant_id==user_id) & status` 复合条件；`if parser_id:` 及排序/分页逻辑保持不变。）

- [ ] **Step 2: 改 `KnowledgebaseService.get_list`（约 :435）**

将：

```python
        kbs = kbs.where(
            ((cls.model.tenant_id.in_(joined_tenant_ids) & (cls.model.permission ==
                                                            TenantPermission.TEAM.value)) | (
                cls.model.tenant_id == user_id))
            & (cls.model.status == StatusEnum.VALID.value)
        )
```

替换为：

```python
        if joined_tenant_ids is not None:
            kbs = kbs.where(cls.model.tenant_id.in_(joined_tenant_ids))
        kbs = kbs.where(cls.model.status == StatusEnum.VALID.value)
```

- [ ] **Step 3: 改 `KnowledgebaseService.accessible` 并新增 `owned`（约 :481）**

将现有 `accessible` 方法体替换，并在其后新增 `owned`：

```python
    @classmethod
    @DB.connection_context()
    def accessible(cls, kb_id, user_id):
        # 读操作全局放开：KB 存在（VALID）即可访问（2026-09-09 移除团队隔离）
        docs = cls.model.select(
            cls.model.id).where(cls.model.id == kb_id, cls.model.status == StatusEnum.VALID.value).paginate(0, 1)
        docs = docs.dicts()
        if not docs:
            return False
        return True

    @classmethod
    @DB.connection_context()
    def owned(cls, kb_id, user_id):
        # 写操作 owner-only：仅 KB 所有者可执行（2026-09-09 移除团队隔离）
        docs = cls.model.select(
            cls.model.id).where(cls.model.id == kb_id,
                                cls.model.tenant_id == user_id,
                                cls.model.status == StatusEnum.VALID.value).paginate(0, 1)
        docs = docs.dicts()
        if not docs:
            return False
        return True
```

- [ ] **Step 4: 改 `DocumentService.accessible` 并新增 `owned`（document_service.py:680）**

将现有 `accessible` 替换，并在其后新增 `owned`（`accessible4deletion` 不动）：

```python
    @classmethod
    @DB.connection_context()
    def accessible(cls, doc_id, user_id):
        # 读操作全局放开：文档所属 KB 存在即可访问（2026-09-09 移除团队隔离）
        docs = (
            cls.model.select(cls.model.id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.id == doc_id, Knowledgebase.status == StatusEnum.VALID.value)
            .paginate(0, 1)
        )
        docs = docs.dicts()
        if not docs:
            return False
        return True

    @classmethod
    @DB.connection_context()
    def owned(cls, doc_id, user_id):
        # 写操作 owner-only：文档所属 KB 的所有者可执行（2026-09-09 移除团队隔离）
        docs = (
            cls.model.select(cls.model.id)
            .join(Knowledgebase, on=(Knowledgebase.id == cls.model.kb_id))
            .where(cls.model.id == doc_id, Knowledgebase.tenant_id == user_id)
            .paginate(0, 1)
        )
        docs = docs.dicts()
        if not docs:
            return False
        return True
```

- [ ] **Step 5: 结构验证 + 清理死 import**

```bash
python -m py_compile api/db/services/knowledgebase_service.py api/db/services/document_service.py && ruff check api/db/services/knowledgebase_service.py api/db/services/document_service.py 2>&1 | tail -5
```

预期：py_compile 无输出；ruff 若报 `TenantPermission`/`UserTenant` unused（F401），确认该文件其它函数确实不再使用后删除对应 import 行，再跑一次 ruff 至无新增错误。

- [ ] **Step 6: Commit**

```bash
git add api/db/services/knowledgebase_service.py api/db/services/document_service.py
git commit -m "feat(api): KB/文档服务读写分离——accessible 存在性检查、新增 owned、租户参数 None 全局语义"
```

---

### Task 3: UserCanvasService / DialogService / SearchService 全局查询语义

**Files:**
- Modify: `api/db/services/canvas_service.py:68-96`（get_all_agents_by_tenant_ids，**不改**，仅自己用）、`:143-180`（get_by_tenant_ids）、`:205-218`（accessible）
- Modify: `api/db/services/dialog_service.py:129-180`（get_by_tenant_ids）
- Modify: `api/db/services/search_service.py:82-115`（get_by_tenant_ids）

- [ ] **Step 1: 改 `UserCanvasService.get_by_tenant_ids`（canvas_service.py:143）**

将 keywords/else 两个分支中的 where 复合条件：

```python
                (((cls.model.user_id.in_(joined_tenant_ids)) & (cls.model.permission == TenantPermission.TEAM.value)) | (cls.model.user_id == user_id)),
```

改为「None=全局」结构——把两个分支合并：

```python
        agents = cls.model.select(*fields).join(User, on=(cls.model.user_id == User.id))
        if joined_tenant_ids is not None:
            agents = agents.where(cls.model.user_id.in_(joined_tenant_ids))
        if keywords:
            agents = agents.where(fn.LOWER(cls.model.title).contains(keywords.lower()))
```

（其后 `if canvas_category:`、排序、count、分页、latest release time 逻辑保持不变。）

- [ ] **Step 2: 改 `UserCanvasService.accessible`（canvas_service.py:205）**

```python
    @classmethod
    @DB.connection_context()
    def accessible(cls, canvas_id, tenant_id):
        # 读操作全局放开：智能体存在即可访问；写操作由各端点的 owner 校验保证
        # （2026-09-09 移除团队隔离）
        e, _c = UserCanvasService.get_by_canvas_id(canvas_id)
        return e
```

（删除函数内 `from api.db.services.user_service import UserTenantService` 局部 import 与 `tids`/`permission` 全部团队判断；函数外若 `UserTenantService`、`TenantPermission` import 因此 unused，按 Step 5 ruff 提示处理。）

- [ ] **Step 3: 改 `DialogService.get_by_tenant_ids`（dialog_service.py:129）**

将查询构造段：

```python
        dialogs = (
            cls.model.select(*fields)
            .join(User, on=(cls.model.tenant_id == User.id))
            .where(
                (cls.model.tenant_id.in_(joined_tenant_ids) | (cls.model.tenant_id == user_id))
                & (cls.model.status == StatusEnum.VALID.value),
            )
        )
```

替换为：

```python
        dialogs = cls.model.select(*fields).join(User, on=(cls.model.tenant_id == User.id))
        if joined_tenant_ids is not None:
            dialogs = dialogs.where(cls.model.tenant_id.in_(joined_tenant_ids))
        dialogs = dialogs.where(cls.model.status == StatusEnum.VALID.value)
```

（其后 `if id:`、`if name:`、`if keywords:`、排序、分页逻辑保持不变。）

- [ ] **Step 4: 改 `SearchService.get_by_tenant_ids`（search_service.py:82）**

将查询构造段：

```python
        query = (
            cls.model.select(*fields)
            .join(User, on=(cls.model.tenant_id == User.id))
            .where(((cls.model.tenant_id.in_(joined_tenant_ids)) | (cls.model.tenant_id == user_id)) & (
                        cls.model.status == StatusEnum.VALID.value))
        )
```

替换为：

```python
        query = cls.model.select(*fields).join(User, on=(cls.model.tenant_id == User.id))
        if joined_tenant_ids is not None:
            query = query.where(cls.model.tenant_id.in_(joined_tenant_ids))
        query = query.where(cls.model.status == StatusEnum.VALID.value)
```

- [ ] **Step 5: 结构验证 + 清理死 import**

```bash
python -m py_compile api/db/services/canvas_service.py api/db/services/dialog_service.py api/db/services/search_service.py && ruff check api/db/services/canvas_service.py api/db/services/dialog_service.py api/db/services/search_service.py 2>&1 | tail -5
```

预期：py_compile 无输出；ruff F401（`TenantPermission`/`UserTenant` unused）按「其它函数是否仍使用」判断后清理。

- [ ] **Step 6: Commit**

```bash
git add api/db/services/canvas_service.py api/db/services/dialog_service.py api/db/services/search_service.py
git commit -m "feat(api): 智能体/对话/搜索服务租户参数 None 全局语义 + canvas accessible 存在性检查"
```

---

### Task 4: API 调用方改造（列表全局化 + 写端点换 owned）

**Files:**
- Modify: `api/apps/services/dataset_api_service.py`（list_datasets 约 :397-401；检索全量 KB 约 :1083-1090；写端点 6 处 accessible→owned：`:461, :485, :686, :804, :848, :890`）
- Modify: `api/apps/restful_apis/agent_api.py:428-458`（list_agents）
- Modify: `api/apps/restful_apis/chat_api.py:356-375`（list_chats）
- Modify: `api/apps/restful_apis/search_api.py:76-90`（list_searches）
- Modify: `api/apps/restful_apis/document_api.py`（`:1376` DocumentService.accessible→owned；写端点 5 处 KnowledgebaseService.accessible→owned：`:316, :1016, :1291, :1464, :1576`）
- Modify: `api/apps/restful_apis/chunk_api.py`（写端点 4 处 accessible→owned：`:204, :290, :334, :417`；`:103, :184` 为读保持不动）
- Modify: `api/apps/services/file_api_service.py:584-600`（get_file_content 去掉校验）

**读/写分类依据（实施时以此为准，逐处核对所属函数）：**
- dataset_api_service：`:461 delete_knowledge_graph`、`:485 run_index`、`:686 delete_tags`、`:804 delete_index`、`:848 run_embedding`、`:890 rename_tag` → owned；`:203 get_dataset`、`:227 get_ingestion_summary`、`:420 get_knowledge_graph`、`:545 trace_index`、`:575 list_tags`、`:597 aggregate_tags`、`:628 get_flattened_metadata`、`:733 list_ingestion_logs`、`:776 get_ingestion_log`、`:942 search` → accessible 不动
- document_api：`:316 metadata/update`、`:1016 documents DELETE`、`:1291 documents/metadatas PATCH`、`:1464 documents/parse`、`:1576 documents/stop` → owned；`:270 metadata/summary`、`:718 documents GET` → accessible 不动
- chunk_api：`:204 chunks POST`、`:290 chunks DELETE`、`:334 chunk PATCH`、`:417 chunks PATCH(批量)` → owned

- [ ] **Step 1: list_datasets 全局化（dataset_api_service.py 约 :397-401）**

将：

```python
    if ext_fields.get("owner_ids", []):
        tenant_ids = ext_fields["owner_ids"]
    else:
        tenants = TenantService.get_joined_tenants_by_user_id(tenant_id)
        tenant_ids = [m["tenant_id"] for m in tenants]
```

替换为：

```python
    if ext_fields.get("owner_ids", []):
        tenant_ids = ext_fields["owner_ids"]
    else:
        # 2026-09-09 移除团队隔离：不指定 owner_ids 时全局可见
        tenant_ids = None
```

（`TenantService` 在本文件 :90 仍有 `TenantService.get_by_id` 使用，import 保留。）

- [ ] **Step 2: 检索全量 KB 全局化（dataset_api_service.py 约 :1083-1090）**

将：

```python
    requested_kb_ids = req.get("kb_ids") or []
    tenants = UserTenantService.query(user_id=tenant_id)
    tenant_ids = [t.tenant_id for t in tenants]

    if not tenant_ids:
        return True, {"chunks": [], "total": 0, "doc_aggs": [], "kb_names": {}}

    # Get all permitted KB IDs (team KBs from joined tenants + owned KBs)
    all_kb_records, _ = KnowledgebaseService.get_by_tenant_ids(
        tenant_ids, tenant_id,
        page_number=None, items_per_page=None,
        orderby="create_time", desc=True, keywords=""
    )
```

替换为：

```python
    requested_kb_ids = req.get("kb_ids") or []

    # 2026-09-09 移除团队隔离：全局可见全部 KB
    all_kb_records, _ = KnowledgebaseService.get_by_tenant_ids(
        None, tenant_id,
        page_number=None, items_per_page=None,
        orderby="create_time", desc=True, keywords=""
    )
```

（若 `UserTenantService` import 因此变为 unused 且文件内无其它使用，删除 import 行；以 ruff 为准。）

- [ ] **Step 3: dataset_api_service 6 处写端点 accessible→owned**

对 `:461, :485, :686, :804, :848, :890` 六行（先 Read 确认各自所属函数是上表中的写函数）：

```python
    if not KnowledgebaseService.accessible(dataset_id, tenant_id):
```

逐处替换为：

```python
    if not KnowledgebaseService.owned(dataset_id, tenant_id):
```

- [ ] **Step 4: list_agents 全局化（agent_api.py:428-458）**

将 `tenants = TenantService.get_joined_tenants_by_user_id(tenant_id)` 到 `effective_owner_ids = list(authorized_owner_ids)` 的整段授权逻辑：

```python
    tenants = TenantService.get_joined_tenants_by_user_id(tenant_id)
    authorized_owner_ids = {member["tenant_id"] for member in tenants}
    authorized_owner_ids.add(tenant_id)

    if owner_ids:
        requested_owner_ids = set(owner_ids)
        unauthorized_owner_ids = requested_owner_ids - authorized_owner_ids
        if unauthorized_owner_ids:
            return get_json_result(
                data=False,
                message="Only authorized owner_ids can be queried.",
                code=RetCode.OPERATING_ERROR,
            )
        effective_owner_ids = list(requested_owner_ids)
    else:
        effective_owner_ids = list(authorized_owner_ids)

    canvas, total = UserCanvasService.get_by_tenant_ids(
        effective_owner_ids,
        tenant_id,
        page_number,
        items_per_page,
        order_by,
        desc,
        keywords,
        canvas_category,
    )
```

替换为：

```python
    # 2026-09-09 移除团队隔离：列表全局可见；owner_ids 仅作为过滤条件，不再做授权校验
    canvas, total = UserCanvasService.get_by_tenant_ids(
        owner_ids or None,
        tenant_id,
        page_number,
        items_per_page,
        order_by,
        desc,
        keywords,
        canvas_category,
    )
```

（若 `TenantService` 在本文件无其它使用则删 import；`get_json_result`/`RetCode` 文件内必然还有其它使用，import 不动。）

- [ ] **Step 5: list_chats 全局化（chat_api.py:356-375）**

将 else 分支：

```python
        else:
            chats, total = DialogService.get_by_tenant_ids(
                [], current_user.id, page_number, items_per_page, orderby, desc, keywords, **exact_filters
            )
```

替换为：

```python
        else:
            # 2026-09-09 移除团队隔离：全局可见
            chats, total = DialogService.get_by_tenant_ids(
                None, current_user.id, page_number, items_per_page, orderby, desc, keywords, **exact_filters
            )
```

（owner_ids 分支不动——传入列表即精确过滤，语义在 Task 3 已改为纯 in_。）

- [ ] **Step 6: list_searches 全局化（search_api.py:76-90）**

将：

```python
        if not owner_ids:
            tenants = []
            search_apps, total = SearchService.get_by_tenant_ids(tenants, current_user.id, page_number, items_per_page, orderby, desc, keywords)
```

替换为：

```python
        if not owner_ids:
            # 2026-09-09 移除团队隔离：全局可见
            search_apps, total = SearchService.get_by_tenant_ids(None, current_user.id, page_number, items_per_page, orderby, desc, keywords)
```

- [ ] **Step 7: document_api 写端点 5 处 + 摄取门禁**

先 Read 核对 `:316, :1016, :1291, :1464, :1576` 所属函数是上表写函数，然后逐处：

```python
    if not KnowledgebaseService.accessible(kb_id=dataset_id, user_id=tenant_id):
```

→

```python
    if not KnowledgebaseService.owned(kb_id=dataset_id, user_id=tenant_id):
```

再改 `_run_sync`（:1374-1377）中：

```python
        if not DocumentService.accessible(doc_id, user_id):
```

→

```python
        if not DocumentService.owned(doc_id, user_id):
```

- [ ] **Step 7b: 补 agent 写端点缺失的 owner 校验（spec 3.2 修订）**

`reset_agent`（agent_api.py 约 :789-797）：把 accessible 门禁整体替换为 owner 硬校验（沿用 :731/:716 的既有模式）：

```python
@manager.route("/agents/<agent_id>/reset", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def reset_agent(agent_id, tenant_id):
    # 2026-09-09 移除团队隔离：reset 覆写 DSL，属写操作，仅 agent 所有者可执行
    if not UserCanvasService.query(user_id=tenant_id, id=agent_id):
        return get_json_result(
            data=False,
            message="Only owner of canvas authorized for this operation.",
            code=RetCode.OPERATING_ERROR,
        )
```

`delete_agent_session_item`（agent_api.py 约 :294-300）：把 accessible 门禁替换为「会话创建者或 agent 所有者」（conv 在后续代码已有获取，直接复用）：

```python
@manager.route("/agents/<agent_id>/sessions/<session_id>", methods=["DELETE"])  # noqa: F821
@login_or_apikey_required
def delete_agent_session_item(agent_id, session_id, tenant_id):
    # 2026-09-09 移除团队隔离：仅会话创建者或 agent 所有者可删除会话
    _, conv = API4ConversationService.get_by_id(session_id)
    _, user_canvas = UserCanvasService.get_by_id(agent_id)
    conv_owner = bool(conv) and conv.user_id == tenant_id
    canvas_owner = bool(user_canvas) and user_canvas.user_id == tenant_id
    if not (conv_owner or canvas_owner):
        return get_json_result(
            data=False,
            message="No authorization for this operation.",
            code=RetCode.OPERATING_ERROR,
        )
```

（注意：替换后函数体内原先后面的 `_, conv = API4ConversationService.get_by_id(session_id)` 获取语句不要重复——把原 try 块内对 conv 的获取与本次获取合并，保持 MinIO 清理逻辑原样。）

`debug_agent_component`（:578）、`get_agent_session`（GET :270）、执行/会话读取类端点：**不改**——使用类操作全局放开是终态（spec 3.2）。

- [ ] **Step 8: chunk_api 写端点 4 处**

先 Read 核对 `:204, :290, :334, :417` 所属函数是 chunks 增/删/改，然后逐处：

```python
    if not KnowledgebaseService.accessible(kb_id=dataset_id, user_id=tenant_id):
```

→

```python
    if not KnowledgebaseService.owned(kb_id=dataset_id, user_id=tenant_id):
```

（`:103, :184` 是 GET chunks 读端点，**不动**。）

- [ ] **Step 9: 文件下载全局放开（file_api_service.py:584-600）**

将 `get_file_content` 中：

```python
    e, file = FileService.get_by_id(file_id)
    if not e:
        return False, "Document not found!"
    if not check_file_team_permission(file, uid):
        return False, "No authorization."
    return True, file
```

替换为：

```python
    e, file = FileService.get_by_id(file_id)
    if not e:
        return False, "Document not found!"
    # 2026-09-09 移除团队隔离：文件下载读操作全局放开
    return True, file
```

（`:443, :491` 删除/移动的 `check_file_team_permission` **不动**——写操作 owner-only；文件顶部 import 若仍被这两处使用则保留。）

- [ ] **Step 10: 结构验证**

```bash
python -m py_compile api/apps/services/dataset_api_service.py api/apps/services/file_api_service.py api/apps/restful_apis/agent_api.py api/apps/restful_apis/chat_api.py api/apps/restful_apis/search_api.py api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py && ruff check api/apps/services/dataset_api_service.py api/apps/services/file_api_service.py api/apps/restful_apis/agent_api.py api/apps/restful_apis/chat_api.py api/apps/restful_apis/search_api.py api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py 2>&1 | tail -8
```

预期：py_compile 无输出；ruff 无新增 F401（unused import 按上述各步说明处理）。

- [ ] **Step 11: 全量残留检查**

```bash
grep -rn "get_joined_tenants_by_user_id" api --include=*.py
grep -rn "TenantService.get_joined_tenants\|UserTenantService.query" api/apps --include=*.py
```

预期：`get_joined_tenants_by_user_id` 仅剩 `api/db/services/user_service.py` 定义处（与 check_team_permission 的旧引用已在 Task 1 删除）；apps 层无残留团队过滤调用。若有漏网点，按同模式补改并纳入本次 commit。

- [ ] **Step 12: Commit**

```bash
git add api/apps/services/dataset_api_service.py api/apps/services/file_api_service.py api/apps/restful_apis/agent_api.py api/apps/restful_apis/chat_api.py api/apps/restful_apis/search_api.py api/apps/restful_apis/document_api.py api/apps/restful_apis/chunk_api.py
git commit -m "feat(api): 列表全局可见 + 写端点 owner-only，移除团队租户过滤调用"
```

---

### Task 5: 全量验证 + 收尾

**Files:**
- 无新增改动（验证任务；发现问题修复后并入本任务 commit）

- [ ] **Step 1: 单元测试**

```bash
uv run pytest test/test_check_team_permission.py -v
```

预期：7 passed。

- [ ] **Step 2: 全量测试回归**

```bash
uv run pytest test/ -x -q --ignore=test/playwright 2>&1 | tail -15
```

预期：无新增失败（记录基线：若有与本改动无关的既有失败，列出并说明）。

- [ ] **Step 3: 对抗性自查清单（人工核对 grep 结果）**

```bash
grep -rn "permission == TenantPermission.TEAM" api/db/services/knowledgebase_service.py api/db/services/canvas_service.py
grep -rn "check_kb_team_permission\|check_file_team_permission" api --include=*.py
grep -rn "\.owned(" api --include=*.py
```

核对点：
1. KB/智能体 service 的 `permission == TEAM` 过滤只剩 `get_all_kb_by_tenant_ids`/`get_all_agents_by_tenant_ids`（「我的智能体」计数用，保留）
2. `check_*_team_permission` 调用点仍为 document_api:440（上传）、file_api_service:443（删除）、:491（移动）三处，全部为写操作
3. `owned(` 出现在 Task 4 列出的全部 16 处（dataset 6 + document 5+1 + chunk 4）

- [ ] **Step 4: 收尾记录**

按全局规则更新 `D:\AI\ragflow2\CHANGE.md`（追加迭代条目）并在项目 `CLAUDE.md` 参考表该行追加「已实施」状态：

```markdown
git add D:/AI/ragflow2/CHANGE.md D:/AI/ragflow2/CLAUDE.md
git commit -m "docs: 团队权限移除迭代记录"
```

- [ ] **Step 5: 最终整体代码审查（由主会话调度 superpowers:code-reviewer）**

审查范围：Task 1-5 全部 commit。重点：读/写分类是否有漏网（尤其 dataset_api_service 15 处 accessible 的读写归类）、`None` 全局语义是否与所有调用方一致、文档摄取（document_api:1376）改 owned 后自建文档摄取不受影响。

**部署（用户明确指示后才执行）：** 后端成套 SCP 上述 12 个 py 文件 → 容器内 import 冒烟 → `docker restart docker-ragflow-cpu-1`；两账号交叉验证（B 可见 A 的 KB/智能体/文件；B 写 A 的资源被拒）。
