"""团队权限检查 owner-only 化的单元测试（改后为纯函数，无 DB 依赖）。

对抗性覆盖：owner 放行 / 非 owner 拒绝（含 dict 与 Peewee model 两条分支、
team permission、me permission、空字段、None other、None kb/file、伪造结构）。

生产调用方传的是 Peewee model 对象（走 isinstance → to_dict() 分支），
dict 入参仅是兼容路径，故两条分支均有用例。
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _register(monkeypatch, name, **attrs):
    """往 sys.modules 注入空壳桩模块，测试结束后由 monkeypatch 自动还原。"""
    mod = ModuleType(name)
    mod.__path__ = []  # 标记为 package，允许子模块解析
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _import_module(monkeypatch):
    """直接 import 会拉起 api.db 初始化链；沿用 test_permission_app.py 的桩注入模式。

    owner-only 化后仅剩 isinstance 分支用的 api.db.db_models import；
    此处注入空壳桩（含 api.db、api.db.db_models），保证测试 hermetic、
    不拉起真实 DB 初始化链，且不污染其它测试的运行期惰性 import。
    """
    _register(monkeypatch, "api.db")
    stub_models = _register(
        monkeypatch,
        "api.db.db_models",
        File=type("File", (), {}),
        Knowledgebase=type("Knowledgebase", (), {}),
    )

    file_path = REPO_ROOT / "api/common/check_team_permission.py"
    spec = importlib.util.spec_from_file_location("check_team_permission_under_test", file_path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_team_permission_under_test", mod)
    spec.loader.exec_module(mod)
    return mod, stub_models


class _Env:
    """被测模块 + 桩模型类型的绑定视图。

    mod 与 stub_models 必须来自同一次 _import_module：桩类型是每次注册
    新建的，跨 fixture 分别注册会导致 isinstance 失配。
    """

    def __init__(self, mod, stub_models):
        self.mod = mod
        self.stub_models = stub_models


@pytest.fixture()
def env(monkeypatch):
    mod, stub_models = _import_module(monkeypatch)
    return _Env(mod, stub_models)


@pytest.fixture()
def mod(env):
    return env.mod


def _fake_model(stub_type, payload):
    """带 to_dict() 的假 Peewee model：继承桩里的目标类型，使 isinstance 分支真实命中。"""

    class FakeModel(stub_type):
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    return FakeModel(payload)


# ---------- dict 入参（兼容路径） ----------

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


# ---------- model 入参（生产调用方的真实路径：isinstance → to_dict()） ----------

def test_kb_model_owner_allowed(env):
    kb = _fake_model(env.stub_models.Knowledgebase, {"tenant_id": "u1", "permission": "team"})
    assert env.mod.check_kb_team_permission(kb, "u1") is True


def test_kb_model_non_owner_denied(env):
    kb = _fake_model(env.stub_models.Knowledgebase, {"tenant_id": "u1", "permission": "team"})
    assert env.mod.check_kb_team_permission(kb, "u2") is False


def test_file_model_owner_allowed(env):
    f = _fake_model(env.stub_models.File, {"tenant_id": "u1", "id": "f1"})
    assert env.mod.check_file_team_permission(f, "u1") is True


def test_file_model_non_owner_denied(env):
    f = _fake_model(env.stub_models.File, {"tenant_id": "u1", "id": "f1"})
    assert env.mod.check_file_team_permission(f, "u2") is False


# ---------- 空值边界 ----------

def test_kb_none_denied(mod):
    assert mod.check_kb_team_permission(None, "u1") is False


def test_file_none_denied(mod):
    assert mod.check_file_team_permission(None, "u1") is False


def test_other_none_denied(mod):
    assert mod.check_kb_team_permission({"tenant_id": "u1"}, None) is False


def test_other_empty_string_denied(mod):
    assert mod.check_file_team_permission({"tenant_id": "u1", "id": "f1"}, "") is False
