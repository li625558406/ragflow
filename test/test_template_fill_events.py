"""TemplateFill 画布节点进度事件单测：事件序列契约（selected→filling→filled/done、
单范本失败推 failed 不中断、取消推 cancelled）。全依赖 stub，不触 KB/LLM/存储。"""
import asyncio
import json
from unittest.mock import MagicMock

from agent.canvas import Graph
from agent.component.template_fill import TemplateFill, TemplateFillParam


def _cand(tid, name, placeholders):
    return {"template_id": tid, "name": name, "description": "", "file_type": "docx",
            "slot_names": [p["key"] for p in placeholders],
            "_ver": MagicMock(render_file_id="v1"), "_placeholders": placeholders}


def _dl(doc_id):
    return {"doc_id": doc_id, "filename": f"{doc_id}.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 10, "url": f"/api/v1/agents/download?id={doc_id}&created_by=t",
            "name": f"{doc_id}.docx"}


def _make_comp(candidates, fill_results, canceled=False):
    """构造 TemplateFill 实例并 stub 全部外部依赖。fill_results: {tid: dl 或 Exception}"""
    # 真实 __init__ 断言 canvas is Graph：MagicMock(spec=Graph) 可过 isinstance 且不触 Graph.__init__
    comp = TemplateFill(MagicMock(spec=Graph), "node1", TemplateFillParam())
    comp._canvas.get_tenant_id.return_value = "t"
    comp._param.dataset_ids = ["kb1"]  # _invoke_async 前置校验：空则 raise
    comp.check_if_canceled = MagicMock(return_value=canceled)

    async def _sel(tenant_id, cands, query):
        return list(cands)
    comp._select_templates = _sel
    comp._load_candidates = lambda tenant_id: list(candidates)
    comp._begin_fields = lambda: {}
    comp._user_file_evidence = lambda: ""

    async def _fill_one(tenant_id, cand, chunks, query, begin_fields, user_file_text, sem,
                        on_progress=None):
        res = fill_results[cand["template_id"]]
        if isinstance(res, Exception):
            raise res
        return res, {}, 3

    comp._fill_one = _fill_one

    outs = {}
    comp.set_output = lambda k, v: outs.update({k: v})
    comp._outs = outs
    return comp


def _drain(comp):
    evs = []
    while not comp._event_queue.empty():
        evs.append(comp._event_queue.get_nowait())
    return evs


def _run(comp):
    import agent.component.template_fill as tf_mod

    async def fake_retrieve_all(tenant_id, placeholders_list, kb_ids, task_id=""):
        return [{} for _ in placeholders_list]

    orig = tf_mod.executor.retrieve_all_shared
    tf_mod.executor.retrieve_all_shared = fake_retrieve_all
    try:
        comp._canvas.get_tenant_id.return_value = "t"
        asyncio.run(comp._invoke_async())
    finally:
        tf_mod.executor.retrieve_all_shared = orig
    return _drain(comp)


def test_event_sequence_happy_path():
    """两范本成功：selected → (filling → filled)×2 → done；download 输出含 url/name。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"},
                                   {"key": "k2", "fill_mode": "param"}])]
    comp = _make_comp(cands, {"t1": _dl("d1"), "t2": _dl("d2")})
    evs = [(e["event"], e["data"]["stage"],
            e["data"].get("template_id")) for e in _run(comp)]
    assert evs == [
        ("template_fill_progress", "selected", None),
        ("template_fill_progress", "filling", "t1"),
        ("template_fill_progress", "filled", "t1"),
        ("template_fill_progress", "filling", "t2"),
        ("template_fill_progress", "filled", "t2"),
        ("template_fill_progress", "done", None),
    ]
    # 重放断言 data 细节
    comp2 = _make_comp(cands, {"t1": _dl("d1"), "t2": _dl("d2")})
    raw = _run(comp2)
    sel = raw[0]["data"]
    assert sel["templates"] == [
        {"template_id": "t1", "name": "范本A", "slot_count": 1},
        {"template_id": "t2", "name": "范本B", "slot_count": 2}]
    filled1 = raw[2]["data"]
    assert filled1["download"]["url"] == "/api/v1/agents/download?id=d1&created_by=t"
    assert filled1["download"]["name"] == "d1.docx"
    # filling 事件 total = llm 槽数（param 槽不计）
    assert raw[3]["data"]["total"] == 1 and raw[3]["data"]["done"] == 0
    # download 输出为两份成稿 JSON 列表
    outs = json.loads(comp2._outs["download"])
    assert [d["doc_id"] for d in outs] == ["d1", "d2"]


def test_single_failure_pushes_failed_and_continues():
    """单范本异常：推 failed + 其余范本照常 filled，最终 done 不 cancelled。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": RuntimeError("检索炸了"), "t2": _dl("d2")})
    evs = _run(comp)
    stages = [(e["data"]["stage"], e["data"].get("template_id")) for e in evs]
    assert ("failed", "t1") in stages
    assert ("filled", "t2") in stages
    assert stages[-1] == ("done", None)
    failed_ev = next(e for e in evs if e["data"]["stage"] == "failed")
    assert "检索炸了" in failed_ev["data"]["error"]


def test_cancel_pushes_cancelled_and_no_done():
    """选中后取消：推 cancelled、不推 filled/done、不写输出。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": _dl("d1")})
    calls = {"n": 0}

    def check(*a, **k):
        calls["n"] += 1
        return calls["n"] > 1  # 第一次（入口）不取消，第二次（_fill_and_notify 内）取消

    comp.check_if_canceled = check
    evs = _run(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert "selected" in stages
    assert stages[-1] == "cancelled"
    assert "filled" not in stages and "done" not in stages
    assert comp._outs.get("download") in (None, "")


def test_cancelled_before_start_no_output():
    """入口即取消：直接返回，无任何事件、无输出（不推 selected）。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": _dl("d1")}, canceled=True)
    evs = _run(comp)
    assert evs == []
    assert comp._outs.get("download") in (None, "")
