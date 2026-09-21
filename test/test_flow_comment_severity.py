# test/test_flow_comment_severity.py
"""人工批注级别（severity）端点路径测试：创建时选级别，非法值兜底 medium。

覆盖 add_comment 端点的 severity 解析/校验/透传：
  - 合法值 high/medium/low 原样透传 Service
  - 非法字符串 / 数字 / 空串 / 缺省 → 兜底 medium
  - Service 层同款兜底双保险（绕过端点直接调 Service 的防御语义照测）
  - 锚点批注与普通批注均携带级别

不依赖 Quart 运行时鉴权/DB：照 test_flow_ai_record_update.py 的模式加载
flow_app 注入桩，FlowCommentService.add_comment 整体替换捕获入参。
"""

import asyncio
import os
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _noop_decorator(f=None, *a, **kw):
    return f


def _load_flow_app():
    _make_stub_module("api.apps", current_user=types.SimpleNamespace(id="u1"),
                      login_required=_noop_decorator)
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "flow_app.py"))
    spec = spec_from_file_location("flow_app_comment_severity_under_test", path)
    mod = module_from_spec(spec)
    sys.modules["flow_app_comment_severity_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_flow_app = _load_flow_app()

from quart import Quart

FLOW_ID = "flow-1"


@pytest.fixture()
def quart_app():
    return Quart(__name__)


def _patch_flow(monkeypatch):
    monkeypatch.setattr(
        _flow_app, "_flow_dict",
        lambda fid: {"id": fid, "current_version_id": "v1", "status": "initiator",
                     "initiator_id": "u1", "leader_id": "u2", "handler_id": "u3",
                     "deleted": 0})
    monkeypatch.setattr(_flow_app, "_others_of", lambda flow, uid: [])
    monkeypatch.setattr(_flow_app, "_nickname_of", lambda uid: "用户")
    monkeypatch.setattr(_flow_app, "notify_flow_event", lambda *a, **kw: None)


def _fake_comment_svc(monkeypatch, captured):
    monkeypatch.setattr(_flow_app, "FlowCommentService", SimpleNamespace(
        add_comment=lambda flow_id, version_id, user_id, content, **kw:
            captured.update({"flow_id": flow_id, "version_id": version_id,
                             "user_id": user_id, "content": content, **kw})
            or {"id": "c1", "severity": kw.get("severity", "medium")}))


def _call(app, body, flow_id=FLOW_ID):
    async def _inner():
        async with app.test_request_context("/", method="POST", json=body):
            resp = await _flow_app.add_comment(flow_id)
        return await resp.get_json()
    return asyncio.run(_inner())


class TestAddCommentSeverity:
    def test_valid_severity_passthrough(self, quart_app, monkeypatch):
        _patch_flow(monkeypatch)
        for sev in ("high", "medium", "low"):
            captured: dict = {}
            _fake_comment_svc(monkeypatch, captured)
            res = _call(quart_app, {"content": "意见", "severity": sev})
            assert res["code"] == 0
            assert captured["severity"] == sev

    def test_invalid_severity_falls_back_medium(self, quart_app, monkeypatch):
        """非法字符串/数字/布尔 → 兜底 medium，不报错不落脏值。"""
        _patch_flow(monkeypatch)
        for bad in ("urgent", "HIGH", 1, True, None, "", ["high"]):
            captured: dict = {}
            _fake_comment_svc(monkeypatch, captured)
            res = _call(quart_app, {"content": "意见", "severity": bad})
            assert res["code"] == 0, f"severity={bad!r} 不应报错"
            assert captured["severity"] == "medium", f"severity={bad!r} 应兜底 medium"

    def test_severity_absent_defaults_medium(self, quart_app, monkeypatch):
        _patch_flow(monkeypatch)
        captured: dict = {}
        _fake_comment_svc(monkeypatch, captured)
        res = _call(quart_app, {"content": "普通批注"})
        assert res["code"] == 0
        assert captured["severity"] == "medium"

    def test_severity_with_anchor(self, quart_app, monkeypatch):
        """锚定批注同样携带级别（选中原文后选级别提交的主路径）。"""
        _patch_flow(monkeypatch)
        captured: dict = {}
        _fake_comment_svc(monkeypatch, captured)
        res = _call(quart_app, {"content": "这里要改", "severity": "high",
                                "anchor_text": "投标保证金", "anchor_para": 3,
                                "anchor_start": 12})
        assert res["code"] == 0
        assert captured["severity"] == "high"
        assert captured["anchor_text"] == "投标保证金"
        assert captured["anchor_para"] == 3
        assert captured["anchor_start"] == 12

    def test_empty_content_rejected_before_severity(self, quart_app, monkeypatch):
        """内容为空先拒绝：级别再对也不落库。"""
        _patch_flow(monkeypatch)
        captured: dict = {}
        _fake_comment_svc(monkeypatch, captured)
        res = _call(quart_app, {"content": "   ", "severity": "high"})
        assert res["code"] == 101
        assert not captured


class TestServiceSeverityFallback:
    def test_service_fallback_double_guard(self, monkeypatch):
        """Service 层兜底双保险：即使调用方绕过端点直传非法值也不落脏值。"""
        from api.db.services.flow_service import FlowCommentService

        captured: dict = {}

        class _FakeComment:
            __data__ = {"id": "c1"}

            def __init__(self, kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(FlowCommentService, "insert",
                            classmethod(lambda cls, **kw: _FakeComment(kw)))
        for bad in ("urgent", "HIGH", "", None, 1):
            captured.clear()
            FlowCommentService.add_comment("f", "v", "u", "c", severity=bad)
            assert captured["severity"] == "medium", f"Service 层 severity={bad!r} 应兜底"
        captured.clear()
        FlowCommentService.add_comment("f", "v", "u", "c", severity="high")
        assert captured["severity"] == "high"
