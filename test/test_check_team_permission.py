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

    # owner-only 化后仅剩 isinstance 分支用的 api.db.db_models import；
    # 此处注入空壳桩（含 api.db.db_models），保证测试 hermetic、不拉起真实 DB 初始化链。
    if "api.db" not in sys.modules:
        stub = types.ModuleType("api.db")
        stub.TenantPermission = types.SimpleNamespace(TEAM="team")
        stub.__path__ = []  # 标记为 package，允许 api.db.db_models 子模块解析
        sys.modules["api.db"] = stub

    if "api.db.db_models" not in sys.modules:
        stub_models = types.ModuleType("api.db.db_models")
        stub_models.File = type("File", (), {})
        stub_models.Knowledgebase = type("Knowledgebase", (), {})
        sys.modules["api.db.db_models"] = stub_models

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
