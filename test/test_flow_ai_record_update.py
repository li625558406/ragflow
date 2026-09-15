# test/test_flow_ai_record_update.py
"""AI 记录「发送即存回填更新」路径测试：record_id + save_as_version=false。

覆盖：
  - FlowAiChatService.update_content 字段组装语义（None 不覆盖 / 空串覆盖）
  - add_ai_record 回填分支的对抗性用例（越权 403 / 不存在 404 / 空 response /
    非 string events 序列化 / 坏 JSON body / 全空白 record_id 落创建路径）

不依赖 Quart 运行时鉴权/DB/MinIO：照 test_flow_doc_table_edit.py 的模式从源
文件加载 flow_app，注入 api.apps 最小桩；DB 调用全部 monkeypatch 掉。
"""

import asyncio
import json
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
    spec = spec_from_file_location("flow_app_ai_record_under_test", path)
    mod = module_from_spec(spec)
    sys.modules["flow_app_ai_record_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_flow_app = _load_flow_app()

from quart import Quart

FLOW_ID = "flow-1"
RECORD_ID = "rec-1"


@pytest.fixture()
def quart_app():
    app = Quart(__name__)
    return app


def _fake_svc(existing, monkeypatch):
    """替换 flow_app 命名空间里的 FlowAiChatService，捕获 update_content 调用。"""
    calls: dict = {}

    svc = SimpleNamespace(
        get_record=lambda rid: dict(existing) if existing and rid == RECORD_ID else None,
        update_content=lambda rid, response, session_id=None, template_fill_events=None:
            calls.update({"rid": rid, "response": response,
                          "session_id": session_id,
                          "template_fill_events": template_fill_events}),
        set_output_version=lambda *a, **kw: calls.update({"set_output_version": True}),
        add_record=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("回填分支不应 add_record")),
    )
    monkeypatch.setattr(_flow_app, "FlowAiChatService", svc)
    return calls


def _patch_flow(monkeypatch, flow_id=FLOW_ID):
    monkeypatch.setattr(
        _flow_app, "_flow_dict",
        lambda fid: {"id": fid, "current_version_id": "v1", "status": "initiator",
                     "initiator_id": "u1", "leader_id": "u2", "handler_id": "u3",
                     "deleted": 0})


def _call(app, body, flow_id=FLOW_ID):
    async def _inner():
        async with app.test_request_context("/", method="POST", json=body):
            resp = await _flow_app.add_ai_record(flow_id)
        return await resp.get_json()  # Quart Response.get_json 是协程
    return asyncio.run(_inner())


def _existing(**over):
    rec = {"id": RECORD_ID, "flow_id": FLOW_ID, "user_id": "u1",
           "instruction": "原始指令", "response": "（生成中…）",
           "version_id": "v1", "session_id": "", "template_fill_events": ""}
    rec.update(over)
    return rec


# ── FlowAiChatService.update_content 字段组装 ─────────────────────

class TestUpdateContentFields:
    def _capture(self, monkeypatch):
        captured = {}

        class _FakeWhere:
            def where(self, *a, **kw):
                return self

            def execute(self):
                captured["executed"] = True

        class _FakeUpdate:
            def __init__(self, **fields):
                captured["fields"] = fields

            def where(self, *a, **kw):
                return _FakeWhere()

        from api.db.services.flow_service import FlowAiChatService

        class _FakeModel:
            id = "model-id-sentinel"  # 供 cls.model.id == record_id 比较用

            @classmethod
            def update(cls, **fields):
                return _FakeUpdate(**fields)

        monkeypatch.setattr(FlowAiChatService, "model", _FakeModel)
        return FlowAiChatService, captured

    def test_none_means_not_touched(self, monkeypatch):
        svc, cap = self._capture(monkeypatch)
        svc.update_content(RECORD_ID, "最终回复")
        assert cap["fields"] == {"response": "最终回复"}

    def test_empty_string_overwrites(self, monkeypatch):
        """前端传 '' 表示清空（与 None 语义不同）——必须允许覆盖。"""
        svc, cap = self._capture(monkeypatch)
        svc.update_content(RECORD_ID, "最终回复", session_id="", template_fill_events="")
        assert cap["fields"] == {"response": "最终回复", "session_id": "", "template_fill_events": ""}

    def test_all_fields(self, monkeypatch):
        svc, cap = self._capture(monkeypatch)
        svc.update_content(RECORD_ID, "r", session_id="s1", template_fill_events="[{}]")
        assert cap["fields"] == {"response": "r", "session_id": "s1", "template_fill_events": "[{}]"}
        assert cap.get("executed")


# ── add_ai_record 回填分支 ────────────────────────────────────────

class TestAiRecordUpdatePath:
    def test_happy_path_updates_not_inserts(self, quart_app, monkeypatch):
        _patch_flow(monkeypatch)
        calls = _fake_svc(_existing(), monkeypatch)
        version_svc = SimpleNamespace(add_version=lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("回填分支不应创建版本")))
        monkeypatch.setattr(_flow_app, "FlowVersionService", version_svc)

        res = _call(quart_app, {"record_id": RECORD_ID, "save_as_version": False,
                                "response": "最终回复", "session_id": "sess-1",
                                "template_fill_events": [{"stage": "selected"}]})
        assert res["code"] == 0
        assert calls["rid"] == RECORD_ID
        assert calls["response"] == "最终回复"
        assert calls["session_id"] == "sess-1"
        # 非 string 的 events 必须被 json 序列化成 string 才落库
        assert isinstance(calls["template_fill_events"], str)
        assert json.loads(calls["template_fill_events"]) == [{"stage": "selected"}]
        assert res["data"]["output_version_id"] == ""
        assert "set_output_version" not in calls

    def test_non_owner_rejected_403(self, quart_app, monkeypatch):
        """记录本人才能回填：同流程其他参与人/任意人都不行。"""
        _patch_flow(monkeypatch)
        calls = _fake_svc(_existing(user_id="someone-else"), monkeypatch)
        monkeypatch.setattr(_flow_app, "current_user", types.SimpleNamespace(id="u1"))

        res = _call(quart_app, {"record_id": RECORD_ID, "save_as_version": False,
                                "response": "x"})
        assert res["code"] == 403
        assert not calls  # 未触发任何写

    def test_record_not_found_404(self, quart_app, monkeypatch):
        _patch_flow(monkeypatch)
        _fake_svc(None, monkeypatch)
        res = _call(quart_app, {"record_id": "ghost", "save_as_version": False,
                                "response": "x"})
        assert res["code"] == 404

    def test_record_of_another_flow_404(self, quart_app, monkeypatch):
        """跨流程携带他人 record_id 也不可回填（flow_id 归属校验）。"""
        _patch_flow(monkeypatch)
        _fake_svc(_existing(flow_id="flow-OTHER"), monkeypatch)
        res = _call(quart_app, {"record_id": RECORD_ID, "save_as_version": False,
                                "response": "x"})
        assert res["code"] == 404

    def test_empty_response_rejected(self, quart_app, monkeypatch):
        _patch_flow(monkeypatch)
        calls = _fake_svc(_existing(), monkeypatch)
        for bad in ("", "   ", None):
            res = _call(quart_app, {"record_id": RECORD_ID, "save_as_version": False,
                                    "response": bad})
            assert res["code"] == 101, f"response={bad!r} 应拒绝"
        assert "response" not in calls

    def test_blank_record_id_handling(self, quart_app, monkeypatch):
        """record_id='   ' 非空字符串：查不到记录 → 404（而非 500）。"""
        _patch_flow(monkeypatch)
        _fake_svc(_existing(), monkeypatch)
        res = _call(quart_app, {"record_id": "   ", "save_as_version": False,
                                "response": "x"})
        assert res["code"] == 404

    def test_empty_record_id_falls_to_create_path(self, quart_app, monkeypatch):
        """record_id='' 为 falsy → 落创建路径 → 缺 instruction 被业务码拒绝（而非 500）。"""
        _patch_flow(monkeypatch)
        _fake_svc(_existing(), monkeypatch)
        res = _call(quart_app, {"record_id": "", "save_as_version": False,
                                "response": "x"})
        assert res["code"] == 101

    def test_garbage_body_not_500(self, quart_app, monkeypatch):
        """body 缺关键字段/非预期形态：走创建路径并得到业务错误码而非异常。"""
        _patch_flow(monkeypatch)
        _fake_svc(_existing(), monkeypatch)
        res = _call(quart_app, {})
        assert res["code"] == 101
        res2 = _call(quart_app, {"record_id": None, "save_as_version": False})
        assert res2["code"] == 101

    def test_missing_events_means_not_touched(self, quart_app, monkeypatch):
        """请求未带 events → None → 不覆盖库中已有事件（防误清空语义）。"""
        _patch_flow(monkeypatch)
        calls = _fake_svc(_existing(), monkeypatch)
        res = _call(quart_app, {"record_id": RECORD_ID, "save_as_version": False,
                                "response": "r"})
        assert res["code"] == 0
        assert calls["template_fill_events"] is None
        assert calls["response"] == "r"
