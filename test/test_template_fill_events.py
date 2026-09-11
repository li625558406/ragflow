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
                        on_progress=None, should_cancel=None, decision=None):
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

    async def fake_retrieve_all(tenant_id, placeholders_list, kb_ids, task_id="",
                                should_cancel=None, sem=None):
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


def test_all_failures_push_failed_and_raise_output():
    """全部失败时节点按现有语义报错：每范本各推一条 failed（error 含原因）、
    无 filled；downloads 为空 → _invoke_async 在 set_output 与 done 之前直接
    raise ValueError（「所有范本填写均失败」），因此 done 不推送、最后一个事件
    是最后一个 failed，且 asyncio.run 会把 ValueError 抛出到调用方。"""
    import pytest

    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": RuntimeError("检索炸了"), "t2": ValueError("渲染炸了")})
    with pytest.raises(ValueError, match="所有范本填写均失败"):
        _run(comp)
    evs = _drain(comp)
    stages = [(e["data"]["stage"], e["data"].get("template_id")) for e in evs]
    # 两个 failed 都存在，error 各自携带原因
    failed = [(e["data"]["template_id"], e["data"]["error"])
              for e in evs if e["data"]["stage"] == "failed"]
    assert {tid for tid, _ in failed} == {"t1", "t2"}
    assert any("检索炸了" in err for _, err in failed)
    assert any("渲染炸了" in err for _, err in failed)
    assert "filled" not in [s for s, _ in stages]
    # 现有实现：全失败在 set_output / done 推送之前 raise，最后事件是最后一个 failed
    assert stages[-1] == ("failed", "t2")
    assert "done" not in [s for s, _ in stages]
    # 降级语义：不写任何输出（download 输出不存在）
    assert comp._outs.get("download") in (None, "")


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


def test_cancel_mid_filling_probe_passed_and_cancelled_pushed(monkeypatch):
    """filling 中途取消标志翻真：generate_values / retrieve_all_shared 收到
    should_cancel 探针（retrieve 侧还带共享限流 sem），探针命中后推 cancelled
    且不推 filled/done、不写输出。走真实 _fill_one（executor.generate_values
    打桩，抛 GenerateCancelled 前不触渲染/存储）。"""
    import agent.component.template_fill as tf_mod

    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, None)
    comp._fill_one = TemplateFill._fill_one.__get__(comp, TemplateFill)  # 真实方法

    seen = {}

    async def fake_gen(tenant_id, placeholders, chunks_by_key, background,
                       sem=None, on_progress=None, should_cancel=None):
        seen["should_cancel"] = should_cancel
        if should_cancel is not None and should_cancel():
            raise tf_mod.executor.GenerateCancelled()
        return {it["key"]: "v" for it in placeholders}, set()

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        seen["shared_kwargs"] = {"should_cancel": should_cancel, "sem": sem}
        return [{} for _ in placeholders_list]

    state = {"n": 0}

    def check(*a, **k):
        state["n"] += 1
        return state["n"] >= 3  # 1=入口 2=filling 检查通过；3=产值探针命中

    comp.check_if_canceled = check
    monkeypatch.setattr(tf_mod.executor, "generate_values", fake_gen)
    monkeypatch.setattr(tf_mod.executor, "retrieve_all_shared", fake_shared)

    asyncio.run(comp._invoke_async())
    evs = _drain(comp)
    stages = [e["data"]["stage"] for e in evs]
    # generate_values 收到 should_cancel kwarg（无参可调用探针）
    assert callable(seen["should_cancel"])
    # 共享检索收到 should_cancel + 限流信号量
    assert callable(seen["shared_kwargs"]["should_cancel"])
    assert isinstance(seen["shared_kwargs"]["sem"], asyncio.Semaphore)
    assert stages[-1] == "cancelled"
    assert "filled" not in stages and "done" not in stages
    assert "failed" not in stages  # 取消不作为范本失败降级
    assert comp._outs.get("download") in (None, "")


def test_executor_generate_cancelled_translates_to_cancelled_event(monkeypatch):
    """executor 抛 GenerateCancelled（取消标志始终为 False，纯异常路径）→
    组件经 _FillCancelled 透传分支转推 cancelled，不推 failed/done。"""
    import agent.component.template_fill as tf_mod

    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, None)
    comp._fill_one = TemplateFill._fill_one.__get__(comp, TemplateFill)

    async def fake_gen(*a, **k):
        raise tf_mod.executor.GenerateCancelled()

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        return [{} for _ in placeholders_list]

    monkeypatch.setattr(tf_mod.executor, "generate_values", fake_gen)
    monkeypatch.setattr(tf_mod.executor, "retrieve_all_shared", fake_shared)

    asyncio.run(comp._invoke_async())
    evs = _drain(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert "selected" in stages
    assert stages[-1] == "cancelled"
    assert "failed" not in stages and "filled" not in stages and "done" not in stages
    assert comp._outs.get("download") in (None, "")


def test_parse_concurrency_env_defense(monkeypatch):
    """TEMPLATE_FILL_RETRIEVAL_CONCURRENCY 坏值防御：非数字/空串降级默认（不炸
    import），0/负数钳到 1（Semaphore(0) 会让首个槽永久阻塞、画布挂死），合法值
    原样生效，未设走默认。"""
    from agent.component.template_fill import _parse_concurrency

    monkeypatch.delenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", raising=False)
    assert _parse_concurrency(2) == 2  # 未设 → 默认

    monkeypatch.setenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "abc")
    assert _parse_concurrency(2) == 2  # 非数字 → 降级默认
    monkeypatch.setenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "")
    assert _parse_concurrency(2) == 2  # 空串 → 降级默认
    monkeypatch.setenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "0")
    assert _parse_concurrency(2) == 1  # 0 → 钳到 1（防 Semaphore(0) 挂死）
    monkeypatch.setenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "-3")
    assert _parse_concurrency(2) == 1  # 负数 → 钳到 1
    monkeypatch.setenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "5")
    assert _parse_concurrency(2) == 5  # 合法值 → 原样生效


def test_retrieval_cancelled_pushes_cancelled_and_no_output(monkeypatch):
    """检索阶段取消（生产上最常见窗口：分钟级大 KNN）：retrieve_all_shared 桩抛
    GenerateCancelled（取消标志恒 False，纯异常路径）→ 组件就地推 cancelled
    终态，异常不外逸（外逸会被 base.invoke_async 吞成空 _ERROR → canvas 误判
    正常 + 下游空输出）、不推 failed/done、不写输出。"""
    import agent.component.template_fill as tf_mod

    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, None)

    async def fake_shared(*a, **k):
        raise tf_mod.executor.GenerateCancelled()

    monkeypatch.setattr(tf_mod.executor, "retrieve_all_shared", fake_shared)

    # 取消异常必须在组件内消化（不外逸）：正常返回且推 cancelled 终态
    asyncio.run(comp._invoke_async())
    evs = _drain(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert "selected" in stages
    assert stages[-1] == "cancelled"
    assert "failed" not in stages and "filled" not in stages and "done" not in stages
    assert comp._outs.get("download") in (None, "")


def test_values_backfill_pushed_before_render(monkeypatch):
    """渲染前产值补推（实时预览兜底）：默认值兜底（D−C）与 param 直取不经过
    LLM 批次回调，产值从不随 filling 事件下发——预览里这些槽位永远停在虚线。
    真实 _fill_one + 桩 generate_values / 渲染 / 存储 / 沉淀：断言渲染前有带
    values 的 filling 事件、覆盖默认值兜底字段、不带 done/total（进度口径仍以
    LLM 批次为准）、空串产值（missing 留空）不补推。"""
    import agent.component.template_fill as tf_mod
    from rag.svr.template_fill import renderer

    cands = [_cand("t1", "范本A", [
        {"key": "llm_k", "fill_mode": "llm"},
        {"key": "def_k", "fill_mode": "llm", "default_value": "默认值"},
        {"key": "empty_k", "fill_mode": "llm"},
    ])]
    comp = _make_comp(cands, None)
    comp._fill_one = TemplateFill._fill_one.__get__(comp, TemplateFill)

    async def no_confirm(chosen, query, begin_fields):
        return {}  # 跳过 P2 确认流（有默认值字段会触发预判 LLM + Redis 挂起）

    comp._confirm_changed_fields = no_confirm

    async def fake_gen(tenant_id, placeholders, chunks_by_key, background,
                       sem=None, on_progress=None, should_cancel=None):
        # def_k 是 D−C 免 LLM 不进清单；empty_k 进 LLM 但产不出（missing 留空）
        assert [it["key"] for it in placeholders] == ["llm_k", "empty_k"]
        return {"llm_k": "LLM值"}, {"empty_k"}

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        return [{} for _ in placeholders_list]

    monkeypatch.setattr(tf_mod.executor, "generate_values", fake_gen)
    monkeypatch.setattr(tf_mod.executor, "retrieve_all_shared", fake_shared)
    monkeypatch.setattr(tf_mod, "settings",
                        MagicMock(get=lambda b, k: b"blob", put=lambda *a: None))
    monkeypatch.setattr(renderer, "render",
                        lambda ft, blob, values, addr=None: b"out")
    monkeypatch.setattr(tf_mod.TplTemplateVersionService, "sediment_defaults",
                        lambda *a, **k: None)

    asyncio.run(comp._invoke_async())
    evs = _drain(comp)
    # 渲染前的补推事件：filling + values 覆盖 llm 产值与默认值兜底，空串不推
    idx_render = next(i for i, e in enumerate(evs) if e["data"]["stage"] == "filled")
    backfills = [e["data"] for e in evs[:idx_render]
                 if e["data"]["stage"] == "filling" and e["data"].get("values")]
    assert len(backfills) == 1
    bf = backfills[0]
    assert bf["values"] == {"llm_k": "LLM值", "def_k": "默认值"}
    assert "done" not in bf and "total" not in bf
    assert bf["template_id"] == "t1"
    # 成稿照常产出
    assert json.loads(comp._outs["download"])[0]["doc_id"]
