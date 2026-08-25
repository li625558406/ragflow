# test/test_permission_app.py
"""权限管理 REST 蓝本的结构性冒烟测试（Hermetic，无需 Redis/外部依赖）。

说明：直接 `import api.apps.restful_apis.permission_app` 会触发 api/apps/__init__.py 的
`settings.init_settings()`，进而需要本机 Redis 与 HF 模型拉取（非单元测试环境）。故本测试
改为从源文件加载蓝本模块，并在 sys.modules 里注入最小桩依赖，验证端点齐全、无语法/引用错误。
真实运行时的鉴权/权限校验留给 Task 10 容器内冒烟。
"""
import sys
import types
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _load_permission_app():
    # 注入最小桩依赖，避免触发真实 api.apps 的 init_settings / Redis / HF 下载
    def noop_decorator(*a, **kw):
        def deco(f):
            return f
        return deco

    class RetCode:
        SUCCESS = 0
        ARGUMENT_ERROR = 101
        DATA_ERROR = 102
        UNAUTHORIZED = 401
        FORBIDDEN = 403

    _make_stub_module("api.apps", current_user=None, login_required=noop_decorator)
    _make_stub_module("api.utils.api_utils", get_json_result=lambda *a, **kw: None,
                      get_data_error_result=lambda *a, **kw: None)
    _make_stub_module("api.utils.permission_utils", permission_required=noop_decorator,
                      get_cached_user_permissions=lambda *a, **kw: set(),
                      invalidate_user_permissions=lambda *a, **kw: None)
    _make_stub_module("api.db.services.permission_service",
                      PermissionRoleService=object, PermissionRolePermissionService=object,
                      PermissionUserRoleService=object, get_users_with_roles=lambda: [])
    _make_stub_module("api.db.services.user_service", UserService=object)
    _make_stub_module("common.constants", RetCode=RetCode)
    _make_stub_module("common.misc_utils", get_uuid=lambda: "stub-uuid")

    file_path = Path("api/apps/restful_apis/permission_app.py").resolve()
    spec = spec_from_file_location("permission_app_under_test", file_path)
    mod = module_from_spec(spec)
    sys.modules["permission_app_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_blueprint_endpoints_exposed():
    """蓝本已定义全部核心端点（加载成功即验证无语法/引用错误）。"""
    app = _load_permission_app()
    assert hasattr(app, "manager")
    for name in ("get_my_permissions", "list_roles", "create_role",
                 "update_role", "delete_role", "set_role_permissions",
                 "list_users", "set_user_roles"):
        assert hasattr(app, name), f"缺少端点 {name}"
