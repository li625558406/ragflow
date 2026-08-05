"""C 端 + B 端 admin API 集成测试。

集成测试 (Quart test_client) 需要 Redis 才能导入 `api.apps`，
本地 CI 环境无 Redis，因此本测试分两层：

1. 结构层：用 AST 解析 `notification_app.py`，验证所有 15 个路由
   已正确挂到名为 `manager` 的 Blueprint 上，路径/方法与设计一致。
   这是无需启动 Quart app 即可验证"端点接线正确"的最可靠手段。

2. 服务层：直接调用 NotificationService / NotificationUserService，
   验证被端点调用的核心读/写行为（未读计数、标已阅）确实工作。
"""
import ast
import os
from pathlib import Path

import pytest

from api.db.db_models import DB, Notification, NotificationUser
from api.db.services.notification_service import (
    NotificationService, NotificationUserService,
)

_APP_FILE = (
    Path(__file__).resolve().parent.parent
    / "api" / "apps" / "restful_apis" / "notification_app.py"
)


# ---------------------------------------------------------------------------
# 结构层：AST 验证端点接线
# ---------------------------------------------------------------------------

def _parse_routes():
    """解析 notification_app.py，返回 [(blueprint_var, path, {methods})] 列表。"""
    source = _APP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes = []
    for node in tree.body:
        # @<var>.route(...) 装饰的 async def
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            call = None
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                call = dec
            elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Call):
                call = dec.value
            if not call:
                continue
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                continue
            bp_var = None
            if isinstance(func.value, ast.Name):
                bp_var = func.value.id
            path = None
            if call.args and isinstance(call.args[0], ast.Constant):
                path = call.args[0].value
            methods = set()
            for kw in call.keywords:
                if kw.arg == "methods" and isinstance(kw.value, ast.List):
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant):
                            methods.add(elt.value)
            if bp_var and path is not None:
                routes.append((bp_var, path, frozenset(methods)))
    return routes


def test_file_exists():
    assert _APP_FILE.exists(), f"notification_app.py missing at {_APP_FILE}"


def test_manager_blueprint_defined():
    """文件必须暴露 `manager` Blueprint（符合 register_page 自动注册约定）。"""
    source = _APP_FILE.read_text(encoding="utf-8")
    assert "manager = Blueprint(" in source, (
        "missing `manager = Blueprint(...)` — register_page 无法自动注册"
    )


def _routes_by_path():
    """按 path 分组：{path: {methods,...}}（同一路径多方法合并）。"""
    out = {}
    for _bp, path, methods in _parse_routes():
        out.setdefault(path, set()).update(methods)
    return out


def test_all_c_end_routes_present():
    routes = _routes_by_path()
    expected = {
        "/notifications/unread/count": {"GET"},
        "/notifications/unread": {"GET"},
        "/notifications/<notification_id>": {"GET"},
        "/notifications/<notification_id>/read": {"POST"},
        "/notifications/read-all": {"POST"},
        "/notifications/batch-read": {"POST"},
        # subscription 同一路径支持 GET + PUT
        "/notifications/subscription": {"GET", "PUT"},
    }
    for path, methods in expected.items():
        assert path in routes, f"missing path {path}; have {list(routes)}"
        assert routes[path] == methods, (
            f"path {path} expected methods {methods}, got {routes[path]}"
        )


def test_all_admin_routes_present():
    routes = _routes_by_path()
    expected = {
        "/admin/notifications": {"GET"},
        # 同一路径支持 GET + DELETE
        "/admin/notifications/<notification_id>": {"GET", "DELETE"},
        "/admin/notifications/stats": {"GET"},
        # 同一路径支持 GET + PUT
        "/admin/notifications/config": {"GET", "PUT"},
    }
    for path, methods in expected.items():
        assert path in routes, f"missing path {path}; have {list(routes)}"
        assert routes[path] == methods, (
            f"path {path} expected methods {methods}, got {routes[path]}"
        )


def test_all_routes_on_manager_blueprint():
    """所有 15 个路由都必须挂在 `manager` Blueprint 上。"""
    for bp_var, _path, _m in _parse_routes():
        assert bp_var == "manager", (
            f"route on '{bp_var}' should be on 'manager' "
            "(register_page only reads page.manager)"
        )


def _function_defs():
    """返回 {func_name: ast.FunctionDef} for top-level defs in notification_app.py."""
    source = _APP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _attr_names(node):
    """递归收集一个 AST 节点子树中被引用的属性/字段名称。

    涵盖两种形式：
    - ast.Attribute.attr：直接属性访问 u.is_superuser
    - getattr(obj, "name", ...) 中第二个位置参数的字符串常量：
      getattr(u, "is_superuser", False)
    这覆盖本项目中 _is_admin 的两种等价写法。
    """
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            names.append(child.attr)
        elif (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "getattr"
            and len(child.args) >= 2
            and isinstance(child.args[1], ast.Constant)
            and isinstance(child.args[1].value, str)
        ):
            names.append(child.args[1].value)
    return names


def test_is_admin_uses_is_superuser_only():
    """_is_admin 必须依据 is_superuser；User 上不存在 role 字段（role 位于 UserTenant），
    因此 _is_admin 函数体不允许引用 'role' 属性，且必须引用 'is_superuser'。"""
    funcs = _function_defs()
    assert "_is_admin" in funcs, "missing _is_admin definition"
    body_names = set(_attr_names(funcs["_is_admin"]))
    assert "is_superuser" in body_names, (
        "_is_admin must reference is_superuser for admin gating"
    )
    assert "role" not in body_names, (
        "_is_admin must NOT reference User.role (field does not exist on User; "
        "tenant-scoped owner role lives on UserTenant and is not consulted here)"
    )


# ---------------------------------------------------------------------------
# 服务层：被端点调用的核心行为
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_and_seed():
    DB.connect(reuse_if_open=True)
    NotificationUser.delete().execute()
    Notification.delete().execute()
    NotificationService.create_notification(
        site_id="x", site_display="测试站 example.com",
        category="news", batch_key="x::1",
        title="t", summary="s", result_ids=["a"],
        result_count=1, publish_range="2026-08-05", created_at=1,
    )
    yield


def test_unread_count_endpoint_logic():
    """模拟 GET /notifications/unread/count?user_id=userA。"""
    nid = Notification.get(Notification.site_id == "x").id
    NotificationUserService.fan_out(nid, ["userA"])
    assert NotificationUserService.get_unread_count("userA") == 1


def test_mark_read_endpoint_logic():
    """模拟 POST /notifications/{id}/read?user_id=userA。"""
    nid = Notification.get(Notification.site_id == "x").id
    NotificationUserService.fan_out(nid, ["userA"])
    NotificationUserService.mark_read("userA", [nid])
    assert NotificationUserService.get_unread_count("userA") == 0


def test_mark_all_read_endpoint_logic():
    """模拟 POST /notifications/read-all?user_id=userA。"""
    nid = Notification.get(Notification.site_id == "x").id
    NotificationUserService.fan_out(nid, ["userA"])
    updated = NotificationUserService.mark_all_read("userA")
    assert updated == 1
    assert NotificationUserService.get_unread_count("userA") == 0


def test_unread_list_endpoint_logic():
    """模拟 GET /notifications/unread?user_id=userA。"""
    nid = Notification.get(Notification.site_id == "x").id
    NotificationUserService.fan_out(nid, ["userA"])
    items, total = NotificationUserService.get_unread("userA", 1, 20)
    assert total == 1
    assert items[0]["id"] == nid
    assert items[0]["is_read"] is False
