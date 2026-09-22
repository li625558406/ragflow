# -*- coding: utf-8 -*-
"""POST /template/fill/fill-task/<task_id>/sediment 端点的对抗性单测。

端点直调（coroutine + asyncio.run），桩登录态与 DB Service。「白名单 / override /
截断」这些真正的沉淀语义用**真实** `_sediment_into_placeholders`（纯静态方法，不触库）
在内存 placeholders 上跑——只测桩的话，「端点算出的 only_keys 是否真的按预期落到
字段上」这条链路完全没被覆盖。

模块加载模式照 test_template_fill_progress_api.py：直接 import template_api 会触发
api/apps/__init__.py 的 settings.init_settings()（需本机 Redis/ES），故先桩掉
api.apps 再从源文件加载。
"""
import asyncio
import os
import sys
import types
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.db.services.template_fill_service import TplTemplateVersionService as _RealVerSvc
from rag.svr.template_fill.detector import MAX_ANCHOR_LEN


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：原样返回被装饰函数。"""
    return f


def _load_template_api():
    mod = types.ModuleType("api.apps")
    mod.current_user = SimpleNamespace(id="u1", is_superuser=1)
    mod.login_required = _noop_decorator
    sys.modules["api.apps"] = mod
    import importlib.util
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "template_api.py"))
    spec = importlib.util.spec_from_file_location("template_api_sediment_under_test", path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules["template_api_sediment_under_test"] = loaded
    spec.loader.exec_module(loaded)
    return loaded


_template_api = _load_template_api()


# ---------- 桩对象 ----------


def _make_task(**kw):
    base = dict(
        id="task1", template_id="tpl1", template_version_id="ver1", status="done",
        params={"_changed_keys": ["k1"], "_direct_values": {}},
        values={"cells": {}, "render": {"k1": "值一"}}, tenant_id="tenant1",
        result_file_id="r.docx")
    base.update(kw)
    return SimpleNamespace(**base)


class _Recorder:
    """sediment_defaults 桩：记录入参，并用真实 `_sediment_into_placeholders`
    在内存 placeholders 上执行，返回真实 written（既有调用语义、又有真实效果）。"""

    def __init__(self, placeholders):
        self.placeholders = list(placeholders)
        self.calls = []
        self.lookups = []

    def sediment_defaults(self, template_id, version_id, values, only_keys, override_keys=None):
        self.calls.append({
            "template_id": template_id,
            "version_id": version_id,
            "values": dict(values),
            "only_keys": set(only_keys),
            "override_keys": set(override_keys or set()),
        })
        return _RealVerSvc._sediment_into_placeholders(
            self.placeholders, values, only_keys, override_keys)


def _install(monkeypatch, task, *, placeholders=None, owned=True, latest_ver="MISSING",
             checked_ver="MISSING", raise_on_sediment=False):
    """装配端点依赖。`checked_ver`/`latest_ver` 传 "MISSING" 表示用默认值
    （checked 命中一条假版本行 / latest 无命中）。"""
    monkeypatch.setattr(_template_api, "TplFillTaskService", SimpleNamespace(
        get_owned=lambda tid, uid, **kw: task if owned else None))
    rec = _Recorder(placeholders or [])

    def get_by_id_checked(template_id, version_id):
        rec.lookups.append(("checked", template_id, version_id))
        if not version_id:
            return None
        return SimpleNamespace(id=version_id) if checked_ver == "MISSING" else checked_ver

    def latest(template_id):
        rec.lookups.append(("latest", template_id))
        return None if latest_ver == "MISSING" else latest_ver

    def sediment_defaults(*a, **kw):
        if raise_on_sediment:
            raise RuntimeError("db down")
        return rec.sediment_defaults(*a, **kw)

    monkeypatch.setattr(_template_api, "TplTemplateVersionService", SimpleNamespace(
        get_by_id_checked=get_by_id_checked, latest=latest,
        sediment_defaults=sediment_defaults))
    return rec


def _call(task_id="task1"):
    return asyncio.run(_template_api.sediment_fill_task(task_id))


def _ph(*keys, source=""):
    out = []
    for k in keys:
        it = {"key": k, "name": f"名{k}", "default_value": "", "default_source": ""}
        if source:
            it["default_value"] = "人工值"
            it["default_source"] = source
        out.append(it)
    return out


# ---------- 权限 / 状态闸 ----------


def test_owner_mismatch_rejected_without_write(monkeypatch):
    rec = _install(monkeypatch, _make_task(), owned=False)
    r = _call()
    assert r["code"] != 0 and "任务不存在" in r["message"]
    assert rec.calls == [] and rec.lookups == [], "越权请求不得触碰任何 Service"


def test_non_terminal_status_rejected(monkeypatch):
    """generating/pending/failed/cancelled 一律拒绝且零写入。"""
    for status in ("generating", "pending", "failed", "cancelled", ""):
        rec = _install(monkeypatch, _make_task(status=status))
        r = _call()
        assert r["code"] != 0, status
        assert "尚未完成" in r["message"], status
        assert rec.calls == [], status


# ---------- 保留键闸（反向门禁，本次改造的核心防御） ----------


def test_missing_reserved_keys_rejected(monkeypatch):
    """B端/对话直发任务（无确认记录）必须被拒——不得退化成全量沉淀。"""
    for params in ({}, {"a": "b"}, None, "junk", ["x"], 123):
        rec = _install(monkeypatch, _make_task(params=params))
        r = _call()
        assert r["code"] != 0, params
        assert "不支持写回" in r["message"], params
        assert rec.calls == [], f"params={params!r} 仍写了默认值"


def test_noop_empty_sets_writes_nothing(monkeypatch):
    """noop 轮：保留键都在但都是空集 → 落「写 0 个」而非「无限制全写」。"""
    rec = _install(monkeypatch, _make_task(params={"_changed_keys": [], "_direct_values": {}}),
                   placeholders=_ph("k1"))
    r = _call()
    assert r["code"] == 0 and r["data"]["written"] is False
    assert rec.calls == [], "空白名单不得调用 sediment_defaults"


# ---------- 白名单 / override ----------


def test_whitelist_is_union_and_others_untouched(monkeypatch):
    """only_keys = _changed_keys ∪ _direct_values；override_keys 只含直填键。"""
    ph = _ph("k1", "k2", "k3")
    rec = _install(monkeypatch, _make_task(
        params={"_changed_keys": ["k1"], "_direct_values": {"k2": "直填"}},
        values={"cells": {}, "render": {"k1": "值一", "k2": "直填", "k3": "不该写"}}), placeholders=ph)
    r = _call()
    assert r["code"] == 0 and r["data"]["written"] is True
    call = rec.calls[0]
    assert call["only_keys"] == {"k1", "k2"} and call["override_keys"] == {"k2"}
    assert call["version_id"] == "ver1" and call["template_id"] == "tpl1"
    assert ph[0]["default_value"] == "值一" and ph[1]["default_value"] == "直填"
    assert ph[2]["default_value"] == "", "白名单外字段被写入——硬闸失效"


def test_manual_default_only_overridden_by_direct(monkeypatch):
    """manual 默认值：直填键可覆盖（用户手打是显式决策），LLM/检索产的键不行。"""
    ph = _ph("k1", "k2", "k3", source="manual")
    rec = _install(monkeypatch, _make_task(
        params={"_changed_keys": ["k1", "k3"], "_direct_values": {"k3": "直填"}},
        values={"cells": {}, "render": {"k1": "LLM值", "k2": "未选中", "k3": "直填"}}),
        placeholders=ph)
    r = _call()
    assert r["code"] == 0
    assert ph[0]["default_value"] == "人工值", "非直填键不得覆盖 manual"
    assert ph[1]["default_value"] == "人工值", "白名单外不得覆盖 manual"
    assert ph[2]["default_value"] == "直填", "直填键应覆盖 manual"
    assert ph[2]["default_source"] == "auto"
    assert rec.calls[0]["override_keys"] == {"k3"}


def test_long_value_truncated_to_max_anchor_len(monkeypatch):
    """截断口径不回退：超 MAX_ANCHOR_LEN 的值按上限截断入库。"""
    long_val = "长" * (MAX_ANCHOR_LEN + 50)
    ph = _ph("k1")
    _install(monkeypatch, _make_task(values={"cells": {}, "render": {"k1": long_val}}),
             placeholders=ph)
    r = _call()
    assert r["code"] == 0
    assert len(ph[0]["default_value"]) == MAX_ANCHOR_LEN
    assert ph[0]["default_value"] == long_val[:MAX_ANCHOR_LEN]


def test_blank_value_not_sedimented(monkeypatch):
    """纯空白值 = 空值，不得抹掉历史默认值。"""
    ph = [{"key": "k1", "name": "名k1", "default_value": "旧值", "default_source": "auto"}]
    _install(monkeypatch, _make_task(values={"cells": {}, "render": {"k1": "   "}}),
             placeholders=ph)
    r = _call()
    assert r["code"] == 0 and r["data"]["written"] is False
    assert ph[0]["default_value"] == "旧值"


# ---------- 值来源脏数据 ----------


def test_values_json_string_parsed(monkeypatch):
    """历史/脏数据：values 是 JSON 字符串时按字符串解析，不静默丢值。"""
    ph = _ph("k1")
    _install(monkeypatch, _make_task(values='{"cells": {}, "render": {"k1": "从字符串来"}}'),
             placeholders=ph)
    r = _call()
    assert r["code"] == 0 and r["data"]["written"] is True
    assert ph[0]["default_value"] == "从字符串来"


def test_values_dirty_shapes_no_crash(monkeypatch):
    """values 为 None / 非 JSON 字符串 / 非 dict / render 非 dict → 按空处理，不炸。"""
    for values in (None, "not json", ["junk"], 123,
                   {"render": "abc"}, {"render": None}, {"cells": {}}):
        ph = _ph("k1")
        rec = _install(monkeypatch, _make_task(values=values), placeholders=ph)
        r = _call()
        assert r["code"] == 0, values
        assert r["data"]["written"] is False, values
        assert ph[0]["default_value"] == "", values
        assert len(rec.calls) == 1, values  # 端点仍走到 Service，只是值为空


def test_dirty_reserved_key_types_treated_as_empty(monkeypatch):
    """`_changed_keys` 非 list（字符串/数字/dict）→ 按空处理，不得逐字符当 key。"""
    for dirty in ("k1", 123, {"k1": 1}, None):
        ph = _ph("k1")
        rec = _install(monkeypatch, _make_task(
            params={"_changed_keys": dirty, "_direct_values": {}},
            values={"cells": {}, "render": {"k1": "值一"}}), placeholders=ph)
        r = _call()
        assert r["code"] == 0, dirty
        assert r["data"]["written"] is False, f"_changed_keys={dirty!r} 被当成了白名单"
        assert rec.calls == [], dirty
        assert ph[0]["default_value"] == "", dirty


def test_dirty_direct_values_type_treated_as_empty(monkeypatch):
    """`_direct_values` 非 dict → 按空处理；`_changed_keys` 仍在，白名单只剩它。"""
    ph = _ph("k1", "k2")
    rec = _install(monkeypatch, _make_task(
        params={"_changed_keys": ["k1"], "_direct_values": ["not", "dict"]},
        values={"cells": {}, "render": {"k1": "值一", "k2": "值二"}}), placeholders=ph)
    r = _call()
    assert r["code"] == 0
    assert rec.calls[0]["only_keys"] == {"k1"}
    assert rec.calls[0]["override_keys"] == set()
    assert ph[1]["default_value"] == ""


def test_non_str_whitelist_items_normalized(monkeypatch):
    """白名单含非 str 项：真值项被 str 归一（不会误命中不存在的 key），假值项丢弃。"""
    ph = _ph("k1")
    rec = _install(monkeypatch, _make_task(
        params={"_changed_keys": ["k1", 1, None, "", 0, ["nested"]],
                "_direct_values": {}},
        values={"cells": {}, "render": {"k1": "值一"}}), placeholders=ph)
    r = _call()
    assert r["code"] == 0
    assert rec.calls[0]["only_keys"] == {"k1", "1", "['nested']"}
    assert ph[0]["default_value"] == "值一"


# ---------- 版本行回落 ----------


def test_version_fallback_to_latest(monkeypatch):
    """template_version_id 为空（历史脏数据）→ 回落该范本最新版。"""
    ph = _ph("k1")
    rec = _install(monkeypatch, _make_task(template_version_id=""),
                   placeholders=ph, latest_ver=SimpleNamespace(id="ver9"))
    r = _call()
    assert r["code"] == 0 and r["data"]["written"] is True
    assert ("latest", "tpl1") in rec.lookups
    assert rec.calls[0]["version_id"] == "ver9"
    assert ph[0]["default_value"] == "值一"


def test_version_missing_returns_error(monkeypatch):
    """版本行彻底取不到 → 报错不炸，零写入。"""
    rec = _install(monkeypatch, _make_task(template_version_id=""),
                   placeholders=_ph("k1"), latest_ver=None)
    r = _call()
    assert r["code"] != 0 and "范本版本不存在" in r["message"]
    assert rec.calls == []


# ---------- 幂等 / 异常 ----------


def test_idempotent_second_call_written_false(monkeypatch):
    ph = _ph("k1")
    _install(monkeypatch, _make_task(values={"cells": {}, "render": {"k1": "值一"}}),
             placeholders=ph)
    first = _call()
    second = _call()
    assert first["data"]["written"] is True
    assert second["code"] == 0 and second["data"]["written"] is False, "重复点击必须幂等"
    assert ph[0]["default_value"] == "值一"


def test_sediment_exception_returns_friendly_error(monkeypatch):
    """沉淀抛异常 → 富文案 + 记录日志，不让 500 裸奔。"""
    _install(monkeypatch, _make_task(), placeholders=_ph("k1"), raise_on_sediment=True)
    r = _call()
    assert r["code"] != 0 and "写回失败" in r["message"]
