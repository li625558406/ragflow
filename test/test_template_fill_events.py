"""TemplateFill 画布节点进度事件单测（委托改造后）：节点建任务行 + spawn 后台
线程，自身降级为观察者轮询。事件序列契约（selected→filling→filled/done、
单范本失败推 failed 不中断、画布取消写取消键推 cancelled）。全依赖 stub，
不触库/Redis/LLM/存储。"""
import asyncio
import json
from unittest.mock import MagicMock

from agent.canvas import Graph
from agent.component.template_fill import TemplateFill, TemplateFillParam


def _cand(tid, name, placeholders):
    return {"template_id": tid, "name": name, "description": "", "file_type": "docx",
            "slot_names": [p["key"] for p in placeholders],
            "_ver": MagicMock(render_file_id="v1", id="ver1"), "_placeholders": placeholders}


def _dl(doc_id):
    return {"doc_id": doc_id, "filename": f"{doc_id}.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 10, "url": f"/api/v1/agents/download?id={doc_id}&created_by=t",
            "name": f"{doc_id}.docx"}


class _Row:
    """tpl_fill_task 行桩：status/result_file_id/error 按 id 供给。"""

    def __init__(self, id, status="pending", result_file_id="", error=""):
        self.id, self.status, self.result_file_id, self.error = \
            id, status, result_file_id, error


class _TaskServiceStub:
    """TplFillTaskService 桩：rows 供给状态序列 {task_id: [Row, ...]}（按查询
    次数逐个弹出，耗尽后重复最后一个）；insert 记录参数并把节点传入的 uuid id
    依插入顺序映射到 sequences 键 task-N。"""

    def __init__(self, sequences):
        self.sequences = {k: list(v) for k, v in sequences.items()}
        self.queries = {}
        self.id_map = {}
        self.inserted = []
        self._next = 0

    @classmethod
    def find_running(cls, template_id, tenant_id):
        return None  # 画布场景默认无历史任务 → 每范本新建

    def insert(self, **kw):
        self._next += 1
        tid = kw.get("id") or f"task-{self._next}"
        self.id_map[tid] = f"task-{self._next}"
        self.inserted.append((tid, kw))
        return tid

    def get_or_none(self, id=None, **kw):
        seq = self.sequences.get(self.id_map.get(id, id))
        if not seq:
            return None
        i = self.queries.get(id, 0)
        self.queries[id] = i + 1
        return seq[min(i, len(seq) - 1)]


def _make_comp(candidates, sequences=None, canceled=False):
    """构造 TemplateFill 实例并 stub 委托面（任务服务/spawn/快照/桥接）。"""
    # 真实 __init__ 断言 canvas is Graph：MagicMock(spec=Graph) 可过 isinstance 且不触 Graph.__init__
    comp = TemplateFill(MagicMock(spec=Graph), "node1", TemplateFillParam())
    comp._canvas.get_tenant_id.return_value = "t"
    comp._param.dataset_ids = ["kb1"]  # _invoke_async 前置校验：空则 raise
    comp.check_if_canceled = MagicMock(return_value=canceled)

    async def _sel(tenant_id, cands, query):
        return list(cands)
    comp._select_templates = _sel
    comp._load_candidates = lambda tenant_id: list(candidates)
    comp._begin_fields = dict
    comp._user_file_evidence = lambda: ""

    import agent.component.template_fill as tf_mod
    svc = _TaskServiceStub(sequences or {})

    def _rev(row_id):
        """序列键 task-N → 节点传入的真实 uuid id（与生产 row.id 一致）。"""
        return next((u for u, n in svc.id_map.items() if n == row_id), row_id)
    comp._bridge_download = lambda tenant_id, cand, row: _dl(f"d-{_rev(row.id)}")
    tf_mod.TplFillTaskService = svc
    tf_mod.spawn_fill_task = lambda task_id: None

    outs = {}
    comp.set_output = lambda k, v: outs.update({k: v})
    comp._outs = outs
    return comp, svc


def _fast_sleep(monkeypatch):
    """观察者 1.5s 轮询在测试里降为空等待（单任务事件循环，无并发方需让出）。"""
    async def _sleep(_s):
        return
    monkeypatch.setattr(asyncio, "sleep", _sleep)


def _drain(comp):
    evs = []
    while not comp._event_queue.empty():
        evs.append(comp._event_queue.get_nowait())
    return evs


def test_event_sequence_happy_path(monkeypatch):
    """两范本成功：selected → filling → filled → done；filling 携带 task_id、
    done/total（total 缺省回落 llm 槽数）；filled 携带桥接 download。"""
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"},
                                   {"key": "k2", "fill_mode": "param"}])]
    comp, svc = _make_comp(cands, {
        "task-1": [_Row("task-1", status="generating"),
                   _Row("task-1", status="done", result_file_id="rf1")],
        "task-2": [_Row("task-2", status="generating"),
                   _Row("task-2", status="done", result_file_id="rf2")]})
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: None)
    evs = [(e["event"], e["data"]["stage"],
            e["data"].get("template_id")) for e in _run(comp)]
    assert evs == [
        ("template_fill_progress", "selected", None),
        ("template_fill_progress", "filling", "t1"),
        ("template_fill_progress", "filling", "t2"),
        ("template_fill_progress", "filled", "t1"),
        ("template_fill_progress", "filled", "t2"),
        ("template_fill_progress", "done", None),
    ]
    # 委托面：每范本各建一条 source=canvas 的任务行并 spawn（id 经 uuid 映射到序列键）
    assert [(svc.id_map[tid], kw["template_id"], kw["source"])
            for tid, kw in svc.inserted] == [
        ("task-1", "t1", "canvas"), ("task-2", "t2", "canvas")]
    # filling 事件细节（重放断言）：task_id / total 回落 llm 槽数（param 不计）
    comp2, svc2 = _make_comp(cands, {
        "task-1": [_Row("task-1", status="generating"),
                   _Row("task-1", status="done", result_file_id="rf1")],
        "task-2": [_Row("task-2", status="generating"),
                   _Row("task-2", status="done", result_file_id="rf2")]})
    raw = _run(comp2)
    uuid_of = {v: k for k, v in svc2.id_map.items()}
    tid1, tid2 = uuid_of["task-1"], uuid_of["task-2"]
    f1 = raw[1]["data"]
    assert f1["task_id"] == tid1 and f1["done"] == 0 and f1["total"] == 1
    assert f1["name"] == "范本A"
    filled1 = raw[3]["data"]
    assert filled1["download"]["url"] == f"/api/v1/agents/download?id=d-{tid1}&created_by=t"
    assert filled1["download"]["name"] == f"d-{tid1}.docx"
    assert filled1["task_id"] == tid1
    # download 输出为两份成稿 JSON 列表
    outs = json.loads(comp2._outs["download"])
    assert [d["doc_id"] for d in outs] == [f"d-{tid1}", f"d-{tid2}"]
    # content 汇总两行
    assert "已选用 2 份范本" in comp2._outs["content"]


def test_filling_values_from_snapshot_push_once(monkeypatch):
    """观察者从 Redis 快照取产值：键数增长才随 filling 事件推送（SSE 体积控制），
    键数不增长不重复推；total 优先取快照值。"""
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, {
        "task-1": [_Row("task-1", status="generating"),
                   _Row("task-1", status="generating"),
                   _Row("task-1", status="done", result_file_id="rf1")]})
    import agent.component.template_fill as tf_mod
    snaps = iter([
        {"done": 1, "total": 3, "values": {"k1": "v1"}},
        {"done": 2, "total": 3, "values": {"k1": "v1"}},   # 键数未增长 → 不再推
        None])
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: next(snaps))
    evs = _run(comp)
    fillings = [e["data"] for e in evs if e["data"]["stage"] == "filling"]
    assert len(fillings) == 2
    assert fillings[0]["values"] == {"k1": "v1"}
    assert "values" not in fillings[1]
    assert fillings[0]["total"] == 3  # 快照 total 优先
    stages = [e["data"]["stage"] for e in evs]
    assert stages[-1] == "done"


def test_single_failure_pushes_failed_and_continues(monkeypatch):
    """单范本任务终态 failed：推 failed（error 透传 DB 行）+ 其余范本照常
    filled，最终 done。"""
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, {
        "task-1": [_Row("task-1", status="failed", error="检索炸了")],
        "task-2": [_Row("task-2", status="done", result_file_id="rf2")]})
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: None)
    evs = _run(comp)
    stages = [(e["data"]["stage"], e["data"].get("template_id")) for e in evs]
    assert ("failed", "t1") in stages
    assert ("filled", "t2") in stages
    assert stages[-1] == ("done", None)
    failed_ev = next(e for e in evs if e["data"]["stage"] == "failed")
    assert "检索炸了" in failed_ev["data"]["error"]


def test_all_failures_push_failed_and_raise_output(monkeypatch):
    """全部失败时节点按现有语义报错：每范本各推一条 failed（error 含原因）、
    无 filled；downloads 为空 → _invoke_async 在 set_output 与 done 之前直接
    raise ValueError，因此 done 不推送。"""
    import pytest
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, {
        "task-1": [_Row("task-1", status="failed", error="检索炸了")],
        "task-2": [_Row("task-2", status="failed", error="渲染炸了")]})
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: None)
    with pytest.raises(ValueError, match="所有范本填写均失败"):
        _run(comp)
    evs = _drain(comp)
    stages = [(e["data"]["stage"], e["data"].get("template_id")) for e in evs]
    failed = [(e["data"]["template_id"], e["data"]["error"])
              for e in evs if e["data"]["stage"] == "failed"]
    assert {tid for tid, _ in failed} == {"t1", "t2"}
    assert any("检索炸了" in err for _, err in failed)
    assert any("渲染炸了" in err for _, err in failed)
    assert "filled" not in [s for s, _ in stages]
    assert stages[-1] == ("failed", "t2")
    assert "done" not in [s for s, _ in stages]
    assert comp._outs.get("download") in (None, "")


def test_cancel_writes_cancel_key_and_pushes_cancelled(monkeypatch):
    """观察轮询中画布被停止：对未终态任务写取消键（executor 探针消费，线程
    自行收口置 cancelled）、推 cancelled、不推 filled/done、不写输出。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp, svc = _make_comp(cands, {
        "task-1": [_Row("task-1", status="generating")]})
    calls = {"n": 0}
    cancel_keys = []

    def check(*a, **k):
        calls["n"] += 1
        return calls["n"] > 1  # 第一次（入口）不取消，第二次（观察循环顶）取消

    comp.check_if_canceled = check
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "write_cancel_key",
                        lambda task_id: cancel_keys.append(task_id))
    _fast_sleep(monkeypatch)
    evs = _run(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert "selected" in stages
    assert stages[-1] == "cancelled"
    assert "filled" not in stages and "done" not in stages
    assert comp._outs.get("download") in (None, "")
    # 取消键写给本次 spawn 的任务行
    assert cancel_keys == [svc.inserted[0][0]]


def test_cancelled_before_start_no_output():
    """入口即取消：直接返回，无任何事件、无输出（不推 selected）。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, canceled=True)
    evs = _run(comp)
    assert evs == []
    assert comp._outs.get("download") in (None, "")


def test_bridge_failure_degrades_to_failed_and_raises(monkeypatch):
    """done 任务成稿桥接失败（对象缺失/存储异常）：降级为 failed 事件（error=
    成稿对象读取失败），不炸观察循环；downloads 为空时汇总 raise。"""
    import pytest
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, {
        "task-1": [_Row("task-1", status="done", result_file_id="rf1")]})
    comp._bridge_download = lambda tenant_id, cand, row: None
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: None)
    with pytest.raises(ValueError, match="成稿对象读取失败"):
        _run(comp)
    evs = _drain(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert stages == ["selected", "failed"]
    assert "done" not in stages
    assert comp._outs.get("download") in (None, "")


def test_cancelled_task_row_pushes_failed(monkeypatch):
    """任务行已被取消（cancel_running 收口）：观察者推 failed（error=任务已取消），
    不推 filled/done；唯一任务失败 → downloads 空汇总 raise。"""
    import pytest
    _fast_sleep(monkeypatch)
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp, _ = _make_comp(cands, {
        "task-1": [_Row("task-1", status="cancelled")]})
    import agent.component.template_fill as tf_mod
    monkeypatch.setattr(tf_mod.executor, "read_progress_snapshot",
                        lambda task_id: None)
    with pytest.raises(ValueError, match="任务已取消"):
        _run(comp)
    evs = _drain(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert stages == ["selected", "failed"]
    assert evs[-1]["data"]["error"] == "任务已取消"
    assert comp._outs.get("download") in (None, "")


def _run(comp):
    """触发 _invoke_async 并 drain 事件队列（委托面已由 _make_comp 桩定）。"""
    comp._canvas.get_tenant_id.return_value = "t"
    asyncio.run(comp._invoke_async())
    return _drain(comp)
