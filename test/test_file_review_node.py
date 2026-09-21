"""「文件审核」画布节点单测。对抗性覆盖：
- file_id 三条解析路径：参数写死 uuid / 参数写引用 {begin@review_file_id} / 参数留空扫 Begin 输出
- 解析落空 → 抛错（不许静默建一条 file_id 为空的轮次行）；非引用垃圾串**原样透传**
  （不吞不改——错误由 executor 的「文件不存在」响亮暴露，节点不做创造性猜测）
- 租户缺失 → 抛错（不许建 tenant_id 为空的轮次行）
- 建轮次契约：round_no=1 / status=reviewing / file_version=v1 / created_by=tenant_id /
  kb_ids 原样透传（形态归一是 Service 层 _normalize_kb_ids 的职责，节点不二次解析）/
  user_query 来自 custom_prompt
- spawn 收到的是 **task_id** 而不是 round id（T5 契约）
- 两次调用产出两个不同 task_id，且都不等于画布 task_id（复用会让不同文件互相覆盖）
所有外部依赖（Service / spawn / Begin 组件）经替身注入，不触真实 DB / 线程 / LLM。
"""
import pytest

from agent.component import file_review as fr
from agent.component.file_review import FileReviewParam

# ---------- 桩件 ----------


class _Begin:
    component_name = "Begin"

    def __init__(self, outs):
        self._outs = outs

    def output(self):
        return dict(self._outs)


class FakeCanvas:
    def __init__(self, tenant="t1", begin_outs=None, refs=None):
        self._tenant = tenant
        self._refs = refs or {}
        self.task_id = "canvas-task-id"     # 画布运行 id：跨运行不变，节点不得复用
        self.components = {"begin": {"obj": _Begin(begin_outs or {})}}

    def get_tenant_id(self):
        return self._tenant

    def get_variable_value(self, exp):
        return self._refs.get(exp)

    def get_component_name(self, cpn_id):
        return "begin"


def _make(param=None, canvas=None):
    """绕过 __init__ 直接装桩（与本仓 test_agent_fill_template_component.py 同款）。"""
    cpn = fr.FileReview.__new__(fr.FileReview)
    cpn._id = "review_0"
    cpn._param = param or FileReviewParam()
    cpn._param.check()
    cpn._canvas = canvas or FakeCanvas()
    return cpn


@pytest.fixture
def rec(monkeypatch):
    """记录 create_round / spawn_review_task 的调用，并返回可变 round id。"""
    calls = {"rounds": [], "spawned": [], "round_id": "round-1"}

    def _create_round(**kwargs):
        calls["rounds"].append(kwargs)
        return calls["round_id"]

    monkeypatch.setattr(fr.FileReviewRoundService, "create_round", _create_round)
    monkeypatch.setattr(fr.spawn_mod, "spawn_review_task",
                        lambda tid: calls["spawned"].append(tid))
    return calls


# ---------- file_id 解析 ----------

def test_file_id_literal_param(rec):
    p = FileReviewParam()
    p.file_id = "upload-uuid-1"
    cpn = _make(p)
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-1"


def test_file_id_from_reference(rec):
    p = FileReviewParam()
    p.file_id = "{begin@review_file_id}"
    cpn = _make(p, FakeCanvas(begin_outs={"review_file_id": "ignored"},
                              refs={"begin@review_file_id": "upload-uuid-2"}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-2"


def test_file_id_falls_back_to_begin_output(rec):
    """参数留空（B端用户不填）时，仍应取到前端送入 Begin 的上传 id。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": "upload-uuid-3"}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-3"


def test_file_id_ref_unresolved_falls_back(rec):
    """上游没推（组件在、但该输出为空）：展开为空 → 回退 Begin 输出，
    不得把 '{begin@...}' 当 id。"""
    # 注意本用例只覆盖「上游没推」这一种；「引用写错」是另外的结局（拼错的 id 含下划线时
    # 不匹配引用正则 → 字面串原样透传、不触发回退），见 test_file_id_underscore_ref_stays_literal。
    p = FileReviewParam()
    p.file_id = "{begin@review_file_id}"
    cpn = _make(p, FakeCanvas(begin_outs={"review_file_id": "upload-uuid-4"}, refs={}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-4"


def test_file_id_missing_raises(rec):
    with pytest.raises(ValueError, match="未指定待审核文件"):
        _make()._invoke()
    assert rec["rounds"] == []
    assert rec["spawned"] == []


def test_tenant_missing_raises(rec):
    cpn = _make(canvas=FakeCanvas(tenant="", begin_outs={"review_file_id": "u"}))
    with pytest.raises(ValueError, match="租户"):
        cpn._invoke()
    assert rec["rounds"] == []


def test_non_ref_garbage_passed_through(rec):
    """非引用形态的串不做任何猜测，原样落库——由 executor 报「文件不存在」。"""
    p = FileReviewParam()
    p.file_id = "not-a-uuid"
    _make(p)._invoke()
    assert rec["rounds"][0]["file_id"] == "not-a-uuid"


def test_file_id_underscore_ref_stays_literal(rec):
    """不匹配引用正则的 {cpn_x@var} 形态不进展开，原样落库——
    错误由 executor 的「文件不存在」暴露，节点不做创造性猜测。"""
    p = FileReviewParam()
    p.file_id = "{begin_x@review_file_id}"
    _make(p)._invoke()
    assert rec["rounds"][0]["file_id"] == "{begin_x@review_file_id}"


def test_begin_output_raising_falls_back_to_error(rec):
    """Begin 读输出出问题不应逸出异常，应降级成同一条用户可读报错。"""
    class _BadBegin:
        component_name = "Begin"

        def output(self):
            raise RuntimeError("boom")

    cpn = _make(canvas=FakeCanvas(begin_outs={}))
    cpn._canvas.components = {"begin": {"obj": _BadBegin()}}
    with pytest.raises(ValueError, match="未指定待审核文件"):
        cpn._invoke()
    assert rec["rounds"] == []


def test_begin_output_non_str_ignored(rec):
    """Begin 输出不是 str（此处为 int）时按取不到处理，不得把 123 当 file_id。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": 123}))
    with pytest.raises(ValueError, match="未指定待审核文件"):
        cpn._invoke()
    assert rec["rounds"] == []


# ---------- 建轮次契约 ----------

def test_create_round_contract(rec):
    p = FileReviewParam()
    p.file_id = "u1"
    p.template_id = ""
    p.custom_prompt = "重点看资质"
    p.dataset_ids = ["kb1", "kb2"]
    cpn = _make(p)
    cpn._invoke()
    kw = rec["rounds"][0]
    assert kw["round_no"] == 1
    assert kw["status"] == "reviewing"
    assert kw["file_version"] == "v1"
    # 留空 → 落**具体**默认模板 id，而不是空串：轮次行是「用了哪套模板」的审计记录，
    # 写空等于对「默认」这件事撒谎；且 T9 的修复轮按 cur.template_id 继承。
    assert kw["template_id"] == "bid_doc_format"
    assert kw["user_query"] == "重点看资质"
    assert kw["tenant_id"] == "t1"
    assert kw["created_by"] == "t1"
    assert kw["kb_ids"] == ["kb1", "kb2"]


def test_template_id_override(rec):
    p = FileReviewParam()
    p.file_id = "u1"
    p.template_id = "bid_qualification"
    _make(p)._invoke()
    assert rec["rounds"][0]["template_id"] == "bid_qualification"


# ---------- spawn 契约 ----------

def test_spawn_receives_task_id_not_round_id(rec):
    rec["round_id"] = "round-xyz"
    p = FileReviewParam()
    p.file_id = "u1"
    _make(p)._invoke()
    assert rec["spawned"] == [rec["rounds"][0]["task_id"]]
    assert "round-xyz" not in rec["spawned"]


def test_no_spawn_when_create_round_fails(rec, monkeypatch):
    """建轮次失败必须不 spawn——否则起一个永远读不到轮次行的线程。"""
    def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(fr.FileReviewRoundService, "create_round", _boom)
    p = FileReviewParam()
    p.file_id = "u1"
    with pytest.raises(RuntimeError, match="db down"):
        _make(p)._invoke()
    assert rec["spawned"] == []


def test_task_id_fresh_per_invoke(rec):
    """两次运行必须是两个 task：复用画布 task_id 会让不同文件的多轮审核串链。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": "u1"}))
    cpn._invoke()
    cpn._invoke()
    t1, t2 = (r["task_id"] for r in rec["rounds"])
    assert t1 != t2
    assert "canvas-task-id" not in (t1, t2)


# ---------- 输出与元数据 ----------

def test_outputs_after_invoke(rec):
    rec["round_id"] = "round-9"
    p = FileReviewParam()
    p.file_id = "u1"
    cpn = _make(p)
    cpn._invoke()
    assert cpn.output("task_id") == rec["rounds"][0]["task_id"]
    assert cpn.output("round_id") == "round-9"
    # file_id 必须进 outputs：前端进度卡轮询端点只认 file_id，而节点 inputs 是空 dict，
    # SSE node_finished 事件里无处可取（生产实测进度卡永不出现的根因之一）
    assert cpn.output("file_id") == "u1"
    assert cpn.output("content")


def test_param_outputs_declared():
    """输出的键必须在 param 里声明，否则画布序列化 DSL 时下游引用不到。"""
    assert set(FileReviewParam().outputs) == {"task_id", "round_id", "file_id", "content"}


def test_check_always_true():
    """canvas.load() 会调 param.check() 并把异常包装成节点级报错——
    本节点是运行期解析 file_id，配置期不该拦人。"""
    assert FileReviewParam().check() is True


def test_thoughts_returns_str():
    """canvas.run 的 node_started 对批内每个组件调 thoughts()，基类抛
    NotImplementedError 会杀掉整条 SSE 流（生产事故：流程页文件审核整轮无回复）。"""
    cpn = _make()
    assert isinstance(cpn.thoughts(), str) and cpn.thoughts()
