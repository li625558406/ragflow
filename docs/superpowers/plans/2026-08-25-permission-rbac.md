# 权限管控（角色制 RBAC）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 B 端+C 端新增角色制权限管控：超管建角色、给角色勾模块权限、给用户挂角色，用户按权限查看模块，前端隐藏+后端强制校验。

**Architecture:** 后端新增 3 张权限表（角色/角色权限/用户角色）实现 RBAC，`@permission_required(key)` 装饰器在接口层强制校验（`is_superuser` 直接放行），权限结果用 `REDIS_CONN` 缓存并支持失效。前端通过 React Query 拉取当前用户权限点，菜单过滤 + 路由守卫，新增一个仅超管可见的「权限管理」页。

**Tech Stack:** Python Quart + Peewee + MySQL + Redis(Valkey)；前端 React 18 + Vite + TanStack Query + react-i18next。

**规格来源:** `docs/superpowers/specs/2026-08-25-permission-rbac-design.md`（方案1：独立分层 RBAC）。

---

## 文件结构

**后端（新增）**
- `api/db/services/permission_service.py` — 权限相关查询（角色、角色权限、用户角色）+ `get_user_permission_keys`
- `api/utils/permission_utils.py` — `permission_required` 装饰器 + `get_cached_user_permissions` + `invalidate_user_permissions`
- `api/apps/restful_apis/permission_app.py` — 权限管理 REST（restful_apis 目录自动注册蓝本）

**后端（修改）**
- `api/constants.py` — 模块权限点常量表 + 内置角色名 + 普通用户默认权限（**直接并入既有 api/constants.py 模块**，不建 `api/constants/` 包，否则会遮蔽该模块导致 `from api.constants import API_VERSION` 失败）
- `api/db/db_models.py` — 新增 3 个模型类 + `migrate_db()` 里建表 + seed 内置角色/权限点
- `api/utils/api_utils.py` — 无需改动（复用 `get_json_result/get_data_error_result`）

**前端（新增）**
- `web/src/services/permission-service.ts` — 权限 API 调用
- `web/src/hooks/use-permission.tsx` — `useMyPermissions` / `usePermission`
- `web/src/components/permission/permission-guard.tsx` — 路由守卫组件
- `web/src/pages/permission/index.tsx` — 权限管理主页（角色列表+勾权限）
- `web/src/pages/permission/components/role-form.tsx` — 角色新建/编辑表单
- `web/src/pages/permission/components/user-assign.tsx` — 给用户挂角色

**前端（修改）**
- `web/src/constants/permission.ts` — 新增模块权限 key 常量（保留既有 `PermissionRole`）
- `web/src/utils/api.ts` — 新增 permission 相关 URL
- `web/src/layouts/components/global-navbar.tsx` — 菜单按权限过滤
- `web/src/routes.tsx` — 新增权限管理路由 + B 端主路由包守卫
- `web/src/locales/zh.ts` — 新增中文文案（只加 zh，不同步 en）

> 前置约定：`from common import settings` 必须先于 `from rag.utils.redis_conn import REDIS_CONN` 导入（见 crawl-dedup 方案踩坑）。
> 前端 API 基址在 `web/src/utils/api.ts`；`restful_apis` 目录下的 `*_app.py` 会被 `api/apps/__init__.py` 的 `search_pages_path` 自动扫描注册，**无需**改 `__init__.py` 注册蓝本。

---

### Task 1: 后端模块权限点常量表

**Files:**
- Create: `api/constants/permission.py`
- Test: `test/test_permission_constants.py`

- [ ] **Step 1: 写常量表**

```python
# api/constants/permission.py
"""模块模块权限点常量表（单一来源，前端引用同一套 key）。"""

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
}

# 内置角色名
SUPER_ROLE_NAME = "超级管理员"
NORMAL_ROLE_NAME = "普通用户"

# 普通用户默认勾选的模块权限点
NORMAL_ROLE_PERMISSIONS = ["bid", "chat", "c_chat", "home", "user_setting"]

# 权限缓存 TTL（秒）与 key 前缀
PERMISSION_CACHE_TTL = 600
PERMISSION_CACHE_PREFIX = "perm:"
```

- [ ] **Step 2: 写常量表单元测试**

```python
# test/test_permission_constants.py
from api.constants.permission import MODULE_PERMISSIONS, NORMAL_ROLE_PERMISSIONS


def test_module_permissions_nonempty():
    assert len(MODULE_PERMISSIONS) >= 5


def test_normal_role_permissions_are_valid_keys():
    for k in NORMAL_ROLE_PERMISSIONS:
        assert k in MODULE_PERMISSIONS, f"{k} 未在 MODULE_PERMISSIONS 中定义"


def test_keys_are_stable_snake_case():
    for k in MODULE_PERMISSIONS:
        assert k.isidentifier(), f"{k} 不是合法标识符"
```

- [ ] **Step 3: 运行测试**

Run: `source .venv/bin/activate && export PYTHONPATH=$(pwd) && uv run pytest test/test_permission_constants.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add api/constants/permission.py test/test_permission_constants.py
git commit -m "feat(permission): 模块权限点常量表 + 内置角色定义"
```

---

### Task 2: 权限表模型 + 迁移建表 + seed

**Files:**
- Modify: `api/db/db_models.py`（在 `NotificationSubscription` 模型之后新增 3 个模型类；`migrate_db()` 末尾加建表+seed；顶部 `from api.constants.permission import ...`）
- Test: `test/test_permission_models.py`

- [ ] **Step 1: 加导入**

在 `db_models.py` 的 import 区（需先确认不与模型定义冲突的位置）追加：
```python
from api.constants.permission import (
    NORMAL_ROLE_NAME,
    NORMAL_ROLE_PERMISSIONS,
    SUPER_ROLE_NAME,
)
```

- [ ] **Step 2: 新增 3 个模型类**（放在 `NotificationSubscription` 类（db_table="notification_subscription"，约 2184 行）之后、`__all__`/迁移函数之前）：

```python
class PermissionRole(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    name = CharField(max_length=100, null=False, unique=True, help_text="角色名", index=True)
    description = TextField(null=True, help_text="角色描述")
    builtin = BooleanField(null=False, default=False, help_text="是否内置角色（内置不可删）", index=True)

    class Meta:
        db_table = "permission_role"


class PermissionRolePermission(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    role_id = CharField(max_length=32, null=False, index=True)
    permission_key = CharField(max_length=64, null=False, index=True)

    class Meta:
        db_table = "permission_role_permission"
        indexes = ((("role_id", "permission_key"), True),)  # 联合唯一


class PermissionUserRole(DataBaseModel):
    id = CharField(max_length=32, primary_key=True, help_text="uuid")
    user_id = CharField(max_length=32, null=False, index=True)
    role_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "permission_user_role"
        indexes = ((("user_id", "role_id"), True),)  # 联合唯一
```

- [ ] **Step 3: 迁移建表 + seed**（追加到 `migrate_db()` 末尾、`logging.disable(logging.NOTSET)` 之前）：

```python
    # ── 权限管控 RBAC（新表 + 初始 seed）─────────────
    if not PermissionRole.table_exists():
        PermissionRole.create_table(safe=True)
        logging.info("permission_role: table created")
    if not PermissionRolePermission.table_exists():
        PermissionRolePermission.create_table(safe=True)
        logging.info("permission_role_permission: table created")
    if not PermissionUserRole.table_exists():
        PermissionUserRole.create_table(safe=True)
        logging.info("permission_user_role: table created")
    seed_default_permissions()
```

- [ ] **Step 4: 写 seed 函数**（定义在 `migrate_db` 之后，使用模型类直接操作，避免循环 import）：

```python
def seed_default_permissions():
    """幂等写入内置角色：超级管理员 + 普通用户（含默认权限点）。"""
    # 内置超级管理员
    super_role = PermissionRole.get_or_none(PermissionRole.name == SUPER_ROLE_NAME)
    if not super_role:
        super_role = PermissionRole.create(
            name=SUPER_ROLE_NAME,
            description="内置超级管理员，默认拥有全部模块权限（is_superuser 亦直接放行）",
            builtin=True,
        )
        logging.info("permission_role: 创建内置【%s】", SUPER_ROLE_NAME)
    # 内置普通用户
    normal_role = PermissionRole.get_or_none(PermissionRole.name == NORMAL_ROLE_NAME)
    if not normal_role:
        normal_role = PermissionRole.create(
            name=NORMAL_ROLE_NAME,
            description="内置普通用户，默认授予基础模块",
            builtin=True,
        )
        logging.info("permission_role: 创建内置【%s】", NORMAL_ROLE_NAME)
    # 普通用户默认权限点（幂等）
    for key in NORMAL_ROLE_PERMISSIONS:
        exists = PermissionRolePermission.get_or_none(
            role_id=normal_role.id, permission_key=key
        )
        if not exists:
            PermissionRolePermission.create(role_id=normal_role.id, permission_key=key)
    # 确保超级管理员不被误勾权限（逻辑上视为全通过即可），这里不写入。
```

- [ ] **Step 5: 模型迁移测试**

```python
# test/test_permission_models.py
from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole


def test_models_have_expected_tables():
    assert PermissionRole._meta.table_name == "permission_role"
    assert PermissionRolePermission._meta.table_name == "permission_role_permission"
    assert PermissionUserRole._meta.table_name == "permission_user_role"
```

- [ ] **Step 6: 运行测试**

Run: `source .venv/bin/activate && export PYTHONPATH=$(pwd) && uv run pytest test/test_permission_models.py test/test_permission_constants.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add api/db/db_models.py test/test_permission_models.py
git commit -m "feat(permission): 新增角色/角色权限/用户角色三表 + 迁移建表 + seed 内置角色"
```

---

### Task 3: 权限查询 service

**Files:**
- Create: `api/db/services/permission_service.py`
- Test: `test/test_permission_service.py`

- [ ] **Step 1: 写 service**

```python
# api/db/services/permission_service.py
import logging

from peewee import fn

from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole
from api.db.services.common_service import CommonService
from api.constants.permission import NORMAL_ROLE_NAME


class PermissionRoleService(CommonService):
    model = PermissionRole


class PermissionRolePermissionService(CommonService):
    model = PermissionRolePermission


class PermissionUserRoleService(CommonService):
    model = PermissionUserRole


def get_user_role_ids(user_id: str) -> list[str]:
    rows = PermissionUserRole.select(PermissionUserRole.role_id).where(
        PermissionUserRole.user_id == user_id
    )
    return [r.role_id for r in rows]


def roles_permission_keys(role_ids: list[str]) -> set[str]:
    if not role_ids:
        return set()
    rows = PermissionRolePermission.select(PermissionRolePermission.permission_key).where(
        PermissionRolePermission.role_id.in_(role_ids)
    )
    return {r.permission_key for r in rows}


def get_normal_role_ids() -> list[str]:
    role = PermissionRole.get_or_none(PermissionRole.name == NORMAL_ROLE_NAME)
    return [role.id] if role else []


def get_user_permission_keys(user_id: str) -> set[str]:
    """用户权限点并集；未挂角色时回退到内置「普通用户」。"""
    role_ids = get_user_role_ids(user_id)
    keys = roles_permission_keys(role_ids)
    if not keys:
        role_ids = get_normal_role_ids()
        keys = roles_permission_keys(role_ids)
    return keys


def get_users_with_roles() -> list[dict]:
    """用户列表 + 已挂角色名，供权限管理页使用。"""
    from api.db.db_models import User
    users = User.select(User.id, User.email, User.nickname, User.is_superuser).order_by(User.create_time)
    role_map = {}
    rows = PermissionUserRole.select(
        PermissionUserRole.user_id, PermissionRole.name
    ).join(PermissionRole, on=(PermissionRole.id == PermissionUserRole.role_id))
    for r in rows:
        role_map.setdefault(r.user_id, []).append(r.name)
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "email": u.email,
            "nickname": u.nickname,
            "is_superuser": bool(u.is_superuser),
            "roles": role_map.get(u.id, []),
        })
    return result
```

- [ ] **Step 2: 写 service 测试（mock 模型查询，验证回退逻辑）**

```python
# test/test_permission_service.py
from unittest.mock import patch, MagicMock
import api.db.services.permission_service as svc


def test_get_user_permission_keys_union(mocker):
    # 用户有 2 个角色，权限取并集
    with patch.object(svc, "get_user_role_ids", return_value=["r1", "r2"]), \
         patch.object(svc, "roles_permission_keys") as mk:
        mk.side_effect = lambda ids: {"bid", "crawler"} if ids == ["r1", "r2"] else set()
        assert svc.get_user_permission_keys("u1") == {"bid", "crawler"}


def test_get_user_permission_keys_fallback_to_normal(mocker):
    # 用户无角色 -> 回退普通用户
    with patch.object(svc, "get_user_role_ids", return_value=[]), \
         patch.object(svc, "get_normal_role_ids", return_value=["n1"]), \
         patch.object(svc, "roles_permission_keys", side_effect=lambda ids: {"bid", "c_chat"} if ids == ["n1"] else set()):
        assert svc.get_user_permission_keys("u1") == {"bid", "c_chat"}


def test_roles_permission_keys_empty_ids():
    assert svc.roles_permission_keys([]) == set()
```

- [ ] **Step 3: 运行测试**

Run: `source .venv/bin/activate && export PYTHONPATH=$(pwd) && uv run pytest test/test_permission_service.py -v`
Expected: PASS（注意 `roles_permission_keys` 被 mock，真实 DB 不参与）

- [ ] **Step 4: 提交**

```bash
git add api/db/services/permission_service.py test/test_permission_service.py
git commit -m "feat(permission): 权限查询 service（角色并集 + 普通用户回退）"
```

---

### Task 4: 权限装饰器 + 缓存 + 对抗性测试

**Files:**
- Create: `api/utils/permission_utils.py`
- Test: `test/test_permission_utils.py`

- [ ] **Step 1: 写装饰器与缓存**

```python
# api/utils/permission_utils.py
import json
import logging
from functools import wraps
from inspect import iscoroutinefunction

from common import settings  # 必须先导入 settings，避免 redis_conn 循环导入

from rag.utils.redis_conn import REDIS_CONN

from api.constants.permission import PERMISSION_CACHE_PREFIX, PERMISSION_CACHE_TTL


def permission_allowed(is_superuser: bool, permissions: set, required_key: str) -> bool:
    """纯逻辑判定：超管直接放行；否则看 required_key 是否在权限点集合。"""
    if is_superuser:
        return True
    return required_key in permissions


def get_cached_user_permissions(user_id: str) -> set:
    cache_key = f"{PERMISSION_CACHE_PREFIX}{user_id}"
    try:
        cached = REDIS_CONN.get(cache_key)
        if cached is not None:
            return set(json.loads(cached))  # set_obj 存的是 json.dumps，读回必须 json.loads（见 connector_api.py:482）
    except Exception as e:
        logging.warning("permission cache get failed: %s", e)
    from api.db.services.permission_service import get_user_permission_keys
    keys = get_user_permission_keys(user_id)
    try:
        REDIS_CONN.set_obj(cache_key, sorted(keys), PERMISSION_CACHE_TTL)
    except Exception as e:
        logging.warning("permission cache set failed: %s", e)
    return keys


def invalidate_user_permissions(user_id: str) -> None:
    try:
        REDIS_CONN.delete(f"{PERMISSION_CACHE_PREFIX}{user_id}")
    except Exception:
        pass


def permission_required(permission_key: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from api.apps import current_user
            from api.utils.api_utils import get_json_result
            from common.constants import RetCode

            user = current_user
            if not user:
                return get_json_result(code=RetCode.UNAUTHORIZED, message="未登录")
            if not permission_allowed(bool(user.is_superuser), get_cached_user_permissions(user.id), permission_key):
                return get_json_result(code=RetCode.FORBIDDEN, message="无权限访问该模块")
            if iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

- [ ] **Step 2: 写对抗性测试**

```python
# test/test_permission_utils.py
from unittest.mock import patch, MagicMock
import api.utils.permission_utils as pu


def test_superuser_always_allowed():
    assert pu.permission_allowed(True, set(), "anything") is True
    assert pu.permission_allowed(True, {"bid"}, "crawler") is True


def test_normal_user_denied_without_key():
    assert pu.permission_allowed(False, {"bid"}, "crawler") is False


def test_normal_user_allowed_with_key():
    assert pu.permission_allowed(False, {"bid", "crawler"}, "crawler") is True


def test_required_key_undefined_in_set():
    # 对抗性：权限集含无关 key，仍拒绝
    assert pu.permission_allowed(False, {"dataset"}, "bid") is False


def test_cache_miss_fetches_db_and_sets(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value=None)
    mocker.patch.object(pu.REDIS_CONN, "set_obj")
    with patch("api.utils.permission_utils.get_user_permission_keys", return_value={"bid"}) as mk:
        assert pu.get_cached_user_permissions("u1") == {"bid"}
        pu.REDIS_CONN.set_obj.assert_called_once()


def test_cache_hit_skips_db(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value='["bid","crawler"]')
    with patch("api.utils.permission_utils.get_user_permission_keys", side_effect=AssertionError("不应查 DB")):
        assert pu.get_cached_user_permissions("u1") == {"bid", "crawler"}
```

> 注：`test_cache_hit_skips_db` 使用 `get` 返回 list（模拟 set_obj 之后 REDIS_CONN.get 反序列化出的 list）。若 `REDIS_CONN.get` 在测试环境返回 bytes/str，测试可改为 `return_value=['bid','crawler']` 已兼容。

- [ ] **Step 3: 运行测试**

Run: `source .venv/bin/activate && export PYTHONPATH=$(pwd) && uv run pytest test/test_permission_utils.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add api/utils/permission_utils.py test/test_permission_utils.py
git commit -m "feat(permission): permission_required 装饰器 + REDIS_CONN 缓存 + 对抗性测试"
```

---

### Task 5: 权限管理 REST API

**Files:**
- Create: `api/apps/restful_apis/permission_app.py`（restful_apis 目录自动注册）
- Test: `test/test_permission_app.py`

- [ ] **Step 1: 写蓝本**

```python
# api/apps/restful_apis/permission_app.py
import logging

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.utils.api_utils import get_json_result, get_data_error_result
from api.utils.permission_utils import (
    permission_required,
    get_cached_user_permissions,
    invalidate_user_permissions,
)
from api.db.services.permission_service import (
    PermissionRoleService,
    PermissionRolePermissionService,
    PermissionUserRoleService,
    get_users_with_roles,
)
from api.db.services.user_service import UserService
from common.constants import RetCode
from common.misc_utils import get_uuid

manager = Blueprint("rest_permission_app", __name__)


async def _json():
    return await request.get_json(force=True)


@manager.route("/permission/me", methods=["GET"])
@login_required
async def get_my_permissions():
    try:
        keys = get_cached_user_permissions(current_user.id)
        return get_json_result(data={
            "permissions": sorted(keys),
            "is_superuser": bool(current_user.is_superuser),
        })
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles", methods=["GET"])
@login_required
@permission_required("permission_manage")
async def list_roles():
    try:
        # 注意：CommonService.get_all 仅在 reverse 非 None 时才应用 order_by，故须显式传 reverse=False
        roles = PermissionRoleService.get_all(order_by="create_time", reverse=False)
        items = []
        for r in roles:
            perms = PermissionRolePermissionService.query(role_id=r.id)
            items.append({
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "builtin": r.builtin,
                "permissions": [p.permission_key for p in perms],
            })
        return get_json_result(data={"items": items})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles", methods=["POST"])
@login_required
@permission_required("permission_manage")
async def create_role():
    try:
        body = await _json()
        name = (body.get("name") or "").strip()
        if not name:
            return get_data_error_result(message="角色名不能为空", code=RetCode.ARGUMENT_ERROR)
        if PermissionRoleService.get_or_none(name=name):
            return get_data_error_result(message="角色名已存在", code=RetCode.ARGUMENT_ERROR)
        # 注意：CommonService.insert 返回的是 save() 的 rows（int），非模型实例，
        # 故这里显式构造 id，不能依赖 insert 返回值的 .id
        role_id = get_uuid()
        PermissionRoleService.insert(id=role_id, name=name, description=body.get("description", ""), builtin=False)
        return get_json_result(data={"id": role_id})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def update_role(role_id):
    try:
        body = await _json()
        updates = {}
        if "name" in body:
            updates["name"] = (body["name"] or "").strip()
        if "description" in body:
            updates["description"] = body["description"]
        if updates:
            PermissionRoleService.update_by_id(role_id, updates)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>", methods=["DELETE"])
@login_required
@permission_required("permission_manage")
async def delete_role(role_id):
    try:
        role = PermissionRoleService.get_or_none(id=role_id)
        if not role:
            return get_data_error_result(message="角色不存在", code=RetCode.DATA_ERROR)
        if role.builtin:
            return get_data_error_result(message="内置角色不可删除", code=RetCode.FORBIDDEN)
        related_users = PermissionUserRoleService.query(role_id=role_id)
        with PermissionRoleService.model._meta.database.atomic():
            PermissionUserRoleService.filter_delete([PermissionUserRoleService.model.role_id == role_id])
            PermissionRolePermissionService.filter_delete([PermissionRolePermissionService.model.role_id == role_id])
            PermissionRoleService.delete_by_id(role_id)
        for u in related_users:
            invalidate_user_permissions(u.user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>/permissions", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def set_role_permissions(role_id):
    try:
        body = await _json()
        keys = body.get("permission_keys") or []
        role = PermissionRoleService.get_or_none(id=role_id)
        if not role:
            return get_data_error_result(message="角色不存在", code=RetCode.DATA_ERROR)
        with PermissionRolePermissionService.model._meta.database.atomic():
            PermissionRolePermissionService.filter_delete(
                [PermissionRolePermissionService.model.role_id == role_id]
            )
            for k in keys:
                PermissionRolePermissionService.insert(role_id=role_id, permission_key=k)
        # 失效相关用户缓存
        users = PermissionUserRoleService.query(role_id=role_id)
        for u in users:
            invalidate_user_permissions(u.user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/users", methods=["GET"])
@login_required
@permission_required("permission_manage")
async def list_users():
    try:
        return get_json_result(data={"items": get_users_with_roles()})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/users/<user_id>/roles", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def set_user_roles(user_id):
    try:
        body = await _json()
        role_ids = body.get("role_ids") or []
        user = UserService.get_or_none(id=user_id)
        if not user:
            return get_data_error_result(message="用户不存在", code=RetCode.DATA_ERROR)
        with PermissionUserRoleService.model._meta.database.atomic():
            PermissionUserRoleService.filter_delete(
                [PermissionUserRoleService.model.user_id == user_id]
            )
            for rid in role_ids:
                PermissionUserRoleService.insert(user_id=user_id, role_id=rid)
        invalidate_user_permissions(user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))
```

- [ ] **Step 2: 写蓝本单测（mock 依赖，验证权限拒绝/放行）**

```python
# test/test_permission_app.py
import api.apps.restful_apis.permission_app as app


def test_blueprint_endpoints_exposed():
    """蓝本已定义全部核心端点（import 成功即验证无语法/引用错误）。"""
    assert hasattr(app, "manager")
    for name in ("get_my_permissions", "list_roles", "create_role",
                 "update_role", "delete_role", "set_role_permissions",
                 "list_users", "set_user_roles"):
        assert hasattr(app, name), f"缺少端点 {name}"
```

> 权限拒绝/放行逻辑已在 Task 3/4 用纯函数单测覆盖；本任务单测聚焦「蓝本端点齐全」，复杂的 `current_user` app context 校验留给 Task 10 容器内冒烟。

- [ ] **Step 3: 运行测试**

Run: `source .venv/bin/activate && export PYTHONPATH=$(pwd) && uv run pytest test/test_permission_app.py -v`
Expected: PASS

- [ ] **Step 4: 手工冒烟（容器内）**

```bash
# 确认蓝本已注册 & 接口可达
docker exec docker-ragflow-cpu-1 python -c "
from api.apps import app
urls = [r.rule for r in app.url_map.iter_rules() if 'permission' in r.rule]
print(urls)
"
# 期待打印含 /api/v1/permission/me 等规则
```

- [ ] **Step 5: 提交**

```bash
git add api/apps/restful_apis/permission_app.py test/test_permission_app.py
git commit -m "feat(permission): 角色/用户角色/权限点 REST API（权限管理页后端）"
```

---

### Task 6: 前端 api.ts + permission-service + use-permission hook

**Files:**
- Modify: `web/src/utils/api.ts`
- Create: `web/src/services/permission-service.ts`
- Create: `web/src/hooks/use-permission.tsx`

- [ ] **Step 1: api.ts 加 URL**（在对象内任选合适位置追加）：

```typescript
  // permission
  permissionMe: `${restAPIv1}/permission/me`,
  permissionRoles: `${restAPIv1}/permission/roles`,
  permissionRole: (id: string) => `${restAPIv1}/permission/roles/${id}`,
  permissionRolePermissions: (id: string) =>
    `${restAPIv1}/permission/roles/${id}/permissions`,
  permissionUsers: `${restAPIv1}/permission/users`,
  permissionUserRoles: (userId: string) =>
    `${restAPIv1}/permission/users/${userId}/roles`,
```

- [ ] **Step 2: 写 permission-service.ts**（参照 `user-service.ts` 的 `request` 用法）：

```typescript
import api from '@/utils/api';
import registerServer from '@/utils/register-server';
import request from '@/utils/request';

const {
  permissionMe,
  permissionRoles,
  permissionRole,
  permissionRolePermissions,
  permissionUsers,
  permissionUserRoles,
} = api;

const methods = {
  myPermissions: {
    url: permissionMe,
    method: 'get',
  },
  listRoles: {
    url: permissionRoles,
    method: 'get',
  },
  createRole: {
    url: permissionRoles,
    method: 'post',
  },
  updateRole: (id: string) => ({
    url: permissionRole(id),
    method: 'put',
  }),
  deleteRole: (id: string) => ({
    url: permissionRole(id),
    method: 'delete',
  }),
  setRolePermissions: (id: string) => ({
    url: permissionRolePermissions(id),
    method: 'put',
  }),
  listUsers: {
    url: permissionUsers,
    method: 'get',
  },
  setUserRoles: (userId: string) => ({
    url: permissionUserRoles(userId),
    method: 'put',
  }),
} as const;

export const permissionService = registerServer<typeof methods>(methods);

export default permissionService;
```

- [ ] **Step 3: 提交**

```bash
git add web/src/utils/api.ts web/src/services/permission-service.ts
git commit -m "feat(permission): 前端权限 API service + URL 常量"
```

> `web/src/hooks/use-permission.tsx`（`useMyPermissions` / `usePermission`）在 Task 8 落地——它导入 `@/constants/permission` 的 `ModulePermissionKey`，需等 Task 7 定义常量后才可接线，故不在此任务创建。

---

### Task 7(常量表): 前端常量 + 权限管理页 + 路由 + i18n

**Files:**
- Modify: `web/src/constants/permission.ts`
- Create: `web/src/pages/permission/index.tsx`
- Create: `web/src/pages/permission/components/role-form.tsx`
- Create: `web/src/pages/permission/components/user-assign.tsx`
- Modify: `web/src/routes.tsx`
- Modify: `web/src/locales/zh.ts`

- [ ] **Step 1: constants/permission.ts 追加模块权限常量**（保留既有 `PermissionRole`）：

```typescript
export enum PermissionRole {
  Me = 'me',
  Team = 'team',
}

// ── 模块级权限点（与后端 api/constants/permission.py 一一对应）──
export type ModulePermissionKey =
  | 'bid'
  | 'dataset'
  | 'chat'
  | 'search'
  | 'agent'
  | 'memory'
  | 'file'
  | 'crawler'
  | 'user_setting'
  | 'home'
  | 'c_chat'
  | 'permission_manage';

export const MODULE_PERMISSIONS: Record<ModulePermissionKey, string> = {
  bid: '标讯管理',
  dataset: '知识库',
  chat: '对话',
  search: '搜索',
  agent: 'Agent 画布/流程',
  memory: '记忆',
  file: '文件',
  crawler: '智能采集',
  user_setting: '用户设置',
  home: 'C 端着陆页',
  c_chat: '投标助手对话',
  permission_manage: '权限管理',
};
```

- [ ] **Step 2: i18n（只加 zh.ts）**——在 `web/src/locales/zh.ts` 追加命名空间 `permission`：

```typescript
// 追加 / 合并到 zh.ts 的导出对象中
permission: {
  title: '权限管理',
  roleName: '角色名',
  description: '描述',
  createRole: '新建角色',
  editRole: '编辑角色',
  deleteRole: '删除角色',
  save: '保存',
  cancel: '取消',
  permissionKeys: '模块权限',
  assignTo: '分配用户角色',
  user: '用户',
  roles: '角色',
  confirmDelete: '确认删除该角色？',
  builtinCannotDelete: '内置角色不可删除',
},
```

> 用 `json` 定位 `zh.ts` 中 `knowledgeConfiguration` 等已有键，按同样格式合并到导出对象（键为 TS 中文字符串字面量）。

- [ ] **Step 3: 权限管理页** `web/src/pages/permission/index.tsx`：

```tsx
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import permissionService from '@/services/permission-service';
import { MODULE_PERMISSIONS, type ModulePermissionKey } from '@/constants/permission';
import RoleForm from './components/role-form';
import UserAssign from './components/user-assign';

export default function PermissionManage() {
  const { t } = useTranslation();
  const { data, refetch } = useQuery({
    queryKey: ['permissionRoles'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const roles = data?.items ?? [];

  const handleDeleteRole = async (id: string) => {
    await permissionService.deleteRole(id);
    refetch();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('permission.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <RoleForm roles={roles} onRefresh={refetch} permissionKeys={MODULE_PERMISSIONS} />
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th>{t('permission.roleName')}</th>
              <th>{t('permission.permissionKeys')}</th>
              <th>{t('permission.description')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {roles.map((r: any) => (
              <tr key={r.id} className="border-t">
                <td>{r.name}{r.builtin ? `（${t('permission.roles')}）` : ''}</td>
                <td>
                  {(r.permissions ?? []).map((k: string) => (
                    <span key={k} className="mr-1 inline-block rounded bg-bg-card px-2 py-1 text-xs">
                      {MODULE_PERMISSIONS[k as ModulePermissionKey] ?? k}
                    </span>
                  ))}
                </td>
                <td>{r.description}</td>
                <td>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={r.builtin}
                    onClick={() => handleDeleteRole(r.id)}
                  >
                    {t('permission.deleteRole')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <UserAssign />
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: 角色表单** `web/src/pages/permission/components/role-form.tsx`（新建 / 编辑角色 + 勾权限点，调用 `createRole` + `setRolePermissions`）：

```tsx
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import permissionService from '@/services/permission-service';
import type { ModulePermissionKey } from '@/constants/permission';

export default function RoleForm({
  permissionKeys,
  onRefresh,
}: {
  permissionKeys: Record<ModulePermissionKey, string>;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [keys, setKeys] = useState<string[]>([]);

  const toggle = (k: string) =>
    setKeys((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  const submit = async () => {
    if (!name.trim()) return;
    const { data } = await permissionService.createRole({ name, description });
    await permissionService.setRolePermissions(data.data.id, { permission_keys: keys });
    setName(''); setDescription(''); setKeys([]);
    onRefresh();
  };

  return (
    <div className="space-y-3 border p-4 rounded-lg">
      <Input
        placeholder={t('permission.roleName')}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <Input
        placeholder={t('permission.description')}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <div className="grid grid-cols-3 gap-2">
        {(Object.entries(permissionKeys) as [string, string][]).map(([k, label]) => (
          <label key={k} className="flex items-center gap-1 text-sm">
            <Checkbox checked={keys.includes(k)} onCheckedChange={() => toggle(k)} />
            {label}
          </label>
        ))}
      </div>
      <Button onClick={submit}>{t('permission.createRole')}</Button>
    </div>
  );
}
```

- [ ] **Step 5: 用户分配** `web/src/pages/permission/components/user-assign.tsx`：

```tsx
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import permissionService from '@/services/permission-service';

export default function UserAssign() {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const { data, refetch } = useQuery({
    queryKey: ['permissionUsers'],
    queryFn: async () => {
      const { data } = await permissionService.listUsers();
      return data.data ?? { items: [] };
    },
  });
  const rolesQuery = useQuery({
    queryKey: ['permissionRolesForAssign'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const roles = rolesQuery.data?.items ?? [];

  const users = data?.items ?? [];

  const toggle = (userId: string, roleId: string) =>
    setSelected((prev) => {
      const cur = prev[userId] ?? [];
      const next = cur.includes(roleId) ? cur.filter((x) => x !== roleId) : [...cur, roleId];
      return { ...prev, [userId]: next };
    });

  const save = async (userId: string) => {
    await permissionService.setUserRoles(userId, { role_ids: selected[userId] ?? [] });
    refetch();
  };

  return (
    <div className="border p-4 rounded-lg space-y-3">
      <h3>{t('permission.assignTo')}</h3>
      {users.map((u: any) => (
        <div key={u.id} className="flex items-center justify-between">
          <span>
            {u.nickname || u.email}
            {u.is_superuser ? ' ⭐' : ''}
          </span>
          <div className="flex gap-2 items-center">
            {/* 角色选择：简化用按钮勾选，正式可用 Select */}
            {roles.map((r: any) => (
              <Button key={r.id} size="sm" variant="ghost" onClick={() => toggle(u.id, r.id)}>
                {r.name}
              </Button>
            ))}
            <Button size="sm" onClick={() => save(u.id)}>
              {t('permission.save')}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: routes.tsx 新增权限管理路由**（在 B 端 root-layout children 内、`Routes.Crawl4ai` 附近追加）：

```tsx
      {
        path: '/permission',
        Component: () => import('@/pages/permission'),
      },
```

- [ ] **Step 7: 提交**

```bash
git add web/src/constants/permission.ts web/src/pages/permission web/src/routes.tsx web/src/locales/zh.ts
git commit -m "feat(permission): 前端权限管理页 + 路由 + 中文 i18n"
```

---

### Task 8(回接): 补全 usePermission hook

**Files:**
- Create: `web/src/hooks/use-permission.tsx`（本任务落地，接上 Task 7 的常量）

- [ ] **Step 1: 写 hook**

```tsx
// web/src/hooks/use-permission.tsx
import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import permissionService from '@/services/permission-service';
import { type ModulePermissionKey } from '@/constants/permission';

export interface PermissionState {
  permissions: string[];
  isSuperuser: boolean;
  loading: boolean;
}

export const useMyPermissions = (): PermissionState => {
  const { data, isLoading } = useQuery({
    queryKey: ['myPermissions'],
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const { data } = await permissionService.myPermissions();
      return data.data ?? { permissions: [], isSuperuser: false };
    },
  });
  return {
    permissions: data?.permissions ?? [],
    isSuperuser: !!data?.isSuperuser,
    loading: isLoading,
  };
};

export const usePermission = () => {
  const { permissions, isSuperuser, loading } = useMyPermissions();
  const permSet = useMemo(() => new Set(permissions), [permissions]);
  const hasPermission = useCallback(
    (key: ModulePermissionKey | string) => isSuperuser || permSet.has(key),
    [isSuperuser, permSet],
  );
  return { hasPermission, permissions, isSuperuser, loading };
};
```

> 前置：Task 7 已在 `web/src/constants/permission.ts` 定义 `ModulePermissionKey`。

- [ ] **Step 2: 提交**

```bash
git add web/src/hooks/use-permission.tsx
git commit -m "feat(permission): usePermission hook（读取当前用户权限点）"
```

---

### Task 9: 前端菜单过滤 + 路由守卫

**Files:**
- Modify: `web/src/layouts/components/global-navbar.tsx`
- Create: `web/src/components/permission/permission-guard.tsx`
- Modify: `web/src/routes.tsx`（B 端主路由包守卫）

- [ ] **Step 1: 写 PermissionGuard**：

```tsx
// web/src/components/permission/permission-guard.tsx
import { Navigate } from 'react-router';
import { usePermission } from '@/hooks/use-permission';

export default function PermissionGuard({
  permission,
  children,
}: {
  permission: string;
  children: React.ReactNode;
}) {
  const { hasPermission, loading } = usePermission();
  if (loading) return null; // 或 loading 组件
  if (!hasPermission(permission)) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
```

- [ ] **Step 2: global-navbar.tsx 菜单过滤**：

在 `menuItems` 每项加 `permission` 字段：
```tsx
const menuItems = [
  { path: Routes.Root, name: 'header.Root', icon: LucideHouse, permission: 'bid' },
  { path: Routes.Datasets, name: 'header.dataset', permission: 'dataset' },
  { path: Routes.Chats, name: 'header.chat', permission: 'chat' },
  { path: Routes.Searches, name: 'header.search', permission: 'search' },
  { path: Routes.Agents, name: 'header.flow', permission: 'agent' },
  { path: Routes.Memories, name: 'header.memories', permission: 'memory' },
  { path: Routes.Files, name: 'header.fileManager', permission: 'file' },
  { path: Routes.Crawl4ai, name: 'header.crawl4ai', permission: 'crawler' },
];
```
渲染时过滤：
```tsx
const { hasPermission } = usePermission();
const visibleMenu = menuItems.filter((it) => hasPermission(it.permission));
```
并把 `menuItems.map(...)` 改为 `visibleMenu.map(...)`（两个分支（css anchor / 非 anchor）都要改）。

- [ ] **Step 3: 提交**

```bash
git add web/src/components/permission/permission-guard.tsx web/src/layouts/components/global-navbar.tsx
git commit -m "feat(permission): 前端菜单按权限过滤 + 路由守卫组件"
```

---

### Task 10: 给模块接口加 @permission_required + 部署脚本 + 文档

**Files:**
- Modify: 各模块 `api/apps/restful_apis/*_app.py`（代表性接口）
- Create: `scripts/_smoke_permission.py`（容器内冒烟）

- [ ] **Step 1: 给代表性接口加装饰器**（示例：`api/apps/restful_apis/dataset_api.py` 列表接口）：

```python
from api.utils.permission_utils import permission_required

@manager.route("/datasets", methods=["GET"])
@login_required
@permission_required("dataset")
async def list_datasets(...):
    ...
```

> 全量接口任务较广，本计划给出「机制 + 代表样例」。**剩余模块接口需在实现后按以下清单补挂装饰器**（每个接口对应它的模块 key）：
> - `dataset_api.py` → `dataset`
> - `bid_app.py`（投标接口）→ `bid`
> - `chat_api.py` → `chat`
> - `search_api.py` → `search`
> - `agent_api.py` → `agent`
> - `memory_api.py` → `memory`
> - `file_api.py`、`file2document_api.py` → `file`
> - `crawl4ai_app.py` → `crawler`
> 注：C 端接口（`c_chat`）与小流量端（`home`）在实现阶段对照 `routes.tsx` 归属后补挂。

- [ ] **Step 2: 写容器内冒烟脚本** `scripts/_smoke_permission.py`：

```python
# scripts/_smoke_permission.py — 容器内跑：检查蓝本注册 + seed 完成
from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole

assert PermissionRole.table_exists(), "permission_role 未建表"
assert PermissionRolePermission.table_exists()
assert PermissionUserRole.table_exists()
print("permission tables OK")

normal = PermissionRole.get_or_none(name="普通用户")
assert normal, "普通用户角色未 seed"
print("seed OK:", normal.name)
print("smoke passed")
```

- [ ] **Step 3: 冒烟 + 导出账号验证**

```bash
docker exec docker-ragflow-cpu-1 python /ragflow/scripts/_smoke_permission.py
# 期望打印 permission tables OK / seed OK / smoke passed
# 手工：用超管账号登录 → 权限管理可见；用普通用户登录 → 只看基础模块
```

- [ ] **Step 4: 更新 CLAUDE.md 参考文档表**（在「智能采集系统设计」条目后追加一行登记本文档）：

```markdown
| 权限管控 RBAC | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-25-permission-rbac-design.md` | 角色+权限点+用户角色三表、@permission_required、前端菜单过滤/路由守卫、B端权限管理页、存量用户默认普通用户 |
```

- [ ] **Step 5: 提交**

```bash
git add api/apps/restful_apis/dataset_api.py scripts/_smoke_permission.py CLAUDE.md
git commit -m "feat(permission): 模块接口加 @permission_required + 容器冒烟脚本 + 文档登记"
```

---

## 部署提醒（遵循 CLAUDE.md）

- 本地开发：`web/src` 热部署自动生效；`api/` 下 Python 改动无需手动部署（本地直接跑）.
- 服务器上线：**成套 SCP**（一次部署，避免单文件遗漏）：
  `api/constants/permission.py`、`api/db/db_models.py`、`api/db/services/permission_service.py`、`api/utils/permission_utils.py`、`api/apps/restful_apis/permission_app.py` + 前端 `web/src/constants/permission.ts`、`web/src/services/permission-service.ts`、`web/src/hooks/use-permission.tsx`、`web/src/components/permission/*`、`web/src/pages/permission/*`、`web/src/routes.tsx`、`web/src/layouts/components/global-navbar.tsx`、`web/src/locales/zh.ts`、`web/src/utils/api.ts` → 重启后端容器。
- 新表在容器启动时由 `init_database_tables` + `migrate_db` 自动建表并 seed。
- 前端改动若涉及 `node_modules` 或热部署不生效，才 `npm run build`；其余改完即生效。

## 验收清单（对照 spec §10）

- [ ] 超管登录 → 权限管理可见，可建角色/勾权限/挂用户
- [ ] 普通用户只见授权模块；直达无权限路由被守卫拦截
- [ ] 老用户（无角色）显示普通用户基础模块
- [ ] 无权限用户直调受控接口返回 403；超管直调全通
- [ ] 改角色权限后，目标用户刷新后权限点生效（缓存失效）
