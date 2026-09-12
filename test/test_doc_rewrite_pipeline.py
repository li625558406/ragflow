# test/test_doc_rewrite_pipeline.py
# -*- coding: utf-8 -*-
"""后端管道测试：Message 合并 pending_downloads（契约核心），+ canvas_service 持久化捕获。"""
from unittest.mock import MagicMock

from agent.component.message import Message


class FakeCanvas:
    def __init__(self, pending=None):
        self.globals = {}
        if pending is not None:
            self.globals["sys.pending_downloads"] = pending

    def get_tenant_id(self):
        return "t1"

    def get_variable_value(self, name):
        if name in self.globals:
            return self.globals[name]
        raise KeyError(name)

    def set_variable_value(self, name, value):
        self.globals[name] = value


def _msg_component(canvas, template):
    # ComponentBase.__init__ 断言 canvas is Graph：沿用 test_doc_rewrite_tool.py
    # 的 object.__new__ 绕行惯例，手工装 _canvas/_param（不降断言强度）。
    m = object.__new__(Message)
    m._canvas = canvas
    m._id = "msg1"
    m._param = MagicMock()
    m._param.content = [template]
    return m


def _dl(doc_id):
    return {"doc_id": doc_id, "filename": f"{doc_id}.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def test_non_stream_merges_pending_downloads():
    canvas = FakeCanvas(pending=[_dl("rewrite-x-v2")])
    m = _msg_component(canvas, "正文说明")
    downloads = []
    m._merge_pending_downloads(downloads)
    assert len(downloads) == 1
    assert downloads[0]["doc_id"] == "rewrite-x-v2"
    assert downloads[0]["url"].startswith("/api/v1/agents/download?id=rewrite-x-v2&created_by=t1")
    assert downloads[0]["name"] == "rewrite-x-v2.docx"
    # 合并后清空 pending，防重复下发
    assert canvas.globals["sys.pending_downloads"] == []


def test_merge_dedupes_by_doc_id():
    canvas = FakeCanvas(pending=[_dl("dup-id")])
    m = _msg_component(canvas, "t")
    downloads = [_dl("dup-id")]
    m._merge_pending_downloads(downloads)
    assert len(downloads) == 1


def test_merge_no_canvas_or_empty_pending_is_noop():
    m = _msg_component(FakeCanvas(), "t")
    downloads = []
    m._merge_pending_downloads(downloads)
    assert downloads == []


def test_persist_capture_shape():
    """canvas_service.completion 的 workflow_finished 捕获逻辑（抽取的纯函数）。"""
    from api.db.services.canvas_service import _extract_finished_downloads

    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {"downloads": [_dl("a")]}}}
    ) == [_dl("a")]
    assert _extract_finished_downloads({"event": "message"}) is None
    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {}}}) is None
    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {"downloads": "bad"}}}) is None
