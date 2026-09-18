"""FileReviewTool（agent/tools/file_review.py）单测。

Service / spawn / time.sleep 全部 monkeypatch，不触 DB/LLM/MinIO；工具实例用
object.__new__ 绕过 ToolBase.__init__（其要求真实 Canvas 实例），_param 用
SimpleNamespace 打桩（同 test_template_fill_tool.py）。

对抗性覆盖：
- file_id 三条路：Begin 输出拿到 / 拿不到 → 明确拒绝（不建 file_id 为空的轮次行）/
  Begin 组件缺失或 output() 抛错或返回非 str → 降级成同一条用户可读拒绝；
- 租户缺失 → 一律拒绝（不建 tenant 为空的轮次行，否则越权闸门先把自己挡住）；
- 模板 id：编造的 id 必须被拒（不静默回落默认值 —— 否则审计列里留下假模板记录）；
- spawn 收到的是 **task_id** 而非 round_id（T5 契约）；
- fix 的级别过滤：**不得**改写任何标注的状态（未选中级别不许被置 wontfix）；
- 级别为空 / 未知词 / 该级别无待修项 → 拒绝新建轮次；
- 轮次余额用尽 / 上一轮仍在跑 → 拒绝；
- 越权：get_owned_task 返回 [] 时 status/fix 必须拒绝；
- 轮询超时 → 返回 task_id 并引导 action=status。

代码质量审查整改回归（见文末「代码质量审查整改」段）：
- 连轮 fix 不得叠加互斥的级别指令（基准恒为首轮原始需求）；
- spawn 线程仍在收尾时不得建轮次（防永久卡死的 fixing 轮）；
- 轮次 error 全量拼接会挤占 LLM 上下文 → 必须裁剪；
- 脏 severity 不得把字面 None 渲染进给用户的文案。
"""
import time
from types import SimpleNamespace

PFX = "__test_fr_tool__"


def _svc():
    from api.db.services import file_review_service as svc

    return svc


def _tpl(tid="bid_doc_format", name="招标文件格式审核", desc="看格式与要件"):
    return SimpleNamespace(id=tid, name=name, description=desc)


def _round(no=1, status="annotated", **kw):
    base = {"id": f"r{no}", "task_id": PFX + "task", "file_id": PFX + "file",
            "round_no": no,
            "status": status, "template_id": "bid_doc_format", "user_query": "",
            "file_version": f"v{no}", "kb_ids": None, "minio_path": None,
            "summary": "", "error": None,
            # 默认「刚建的轮次」：reviewing/fixing 的行必须带 create_time，否则
            # is_stale_running 直接访问它会 AttributeError（刻意不 getattr 兜底）。
            # 需要构造「中断轮次」的用例显式传 create_time=0。
            "create_time": int(time.time() * 1000)}
    base.update(kw)
    return SimpleNamespace(**base)


def _ann(severity="high", status="open", issue="资质缺失", matched_text="甲公司"):
    return SimpleNamespace(id="ann-1", severity=severity, status=status,
                           issue=issue, matched_text=matched_text)


class _Begin:
    component_name = "Begin"

    def __init__(self, outs):
        self._outs = outs

    def output(self):
        return dict(self._outs)


class _BadBegin:
    component_name = "Begin"

    def output(self):
        raise RuntimeError("boom")


def _begin(outs):
    return {"begin": {"obj": _Begin(outs)}}


def _patch(monkeypatch, *, templates=None, rounds=None, pending=None,
           next_round=(1, "v1"), left=3, running=False):
    """统一打桩：Service 查询 + spawn + sleep。返回记录写操作的 calls。

    running 对应 spawn 模块的防重入集合：默认 False（无线程在跑，闸门放行）。
    """
    svc = _svc()
    calls = {"rounds": [], "spawned": [], "ann_updates": [], "round_updates": []}

    monkeypatch.setattr(svc.FileReviewRoundService, "create_round",
                        staticmethod(lambda **kw: calls["rounds"].append(kw) or "round-1"))
    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        staticmethod(lambda task_id, tid: list(rounds or [])))
    monkeypatch.setattr(svc.FileReviewRoundService, "next_round",
                        staticmethod(lambda task_id: next_round))
    monkeypatch.setattr(svc.FileReviewTemplateService, "list_enabled",
                        staticmethod(lambda tid: list(templates or [])))
    monkeypatch.setattr(svc.FileReviewAnnotationService, "list_pending_by_task",
                        staticmethod(lambda task_id: list(pending or [])))
    # 任何对标注状态的改写都是本任务明确禁止的（未选中级别不得被置 wontfix），
    # 记下来供 test_fix_never_mutates_annotation_status 断言。
    monkeypatch.setattr(svc.FileReviewAnnotationService, "update_status",
                        staticmethod(
                            lambda aid, status: calls["ann_updates"].append((aid, status))))
    # R-1 heal 落库走的是**轮次** Service 的 update_status（heal_stale_round 内部调用，
    # admit_fix_round 的 stale→heal 分支会触达），与上面的标注 Service 同名不同类，
    # 必须分开打桩并分别记录（与 test_file_review_api.py 的 _patch_services 同款）。
    monkeypatch.setattr(svc.FileReviewRoundService, "update_status",
                        staticmethod(
                            lambda rid, status, **extra:
                            calls.setdefault("round_updates", []).append(
                                (rid, status, extra)) or True))
    monkeypatch.setattr(svc, "fix_rounds_left", lambda rows: left)
    monkeypatch.setattr("agent.tools.file_review.time.sleep", lambda s: None)

    from rag.svr.file_review import spawn as spawn_mod
    monkeypatch.setattr(spawn_mod, "spawn_review_task",
                        lambda task_id: calls["spawned"].append(task_id))
    # 防重入集合由 spawn 模块持有；工具侧只读它来判定「线程是否还在收尾」。
    monkeypatch.setattr(spawn_mod, "is_running", lambda task_id: running)
    return calls


def _make_tool(tenant="tenant_x", components=None):
    from agent.tools.file_review import FileReviewTool

    tool = object.__new__(FileReviewTool)
    tool._param = SimpleNamespace(outputs={}, inputs={}, debug_inputs={})
    tool.set_output = lambda key, value=None: tool._param.outputs.update({key: {"value": value}})
    tool.check_if_canceled = lambda msg="": False
    tool._canvas = SimpleNamespace(get_tenant_id=lambda: tenant,
                                   components=components or {})
    return tool


# ---------- meta 声明 ----------

def test_meta_declaration():
    from agent.tools.file_review import FileReviewTool, FileReviewToolParam

    param = FileReviewToolParam()
    assert param.meta["name"] == "FileReviewTool"
    assert param.meta["parameters"]["action"]["enum"] == [
        "list_templates", "review", "status", "fix"]
    # 类名必须与 T7 节点 FileReview 不同：component_class 按类名解析且 agent.component
    # 优先于 agent.tools，同名会互相遮蔽
    assert FileReviewTool.component_name == "FileReviewTool"


def test_unknown_action_lists_options():
    out = _make_tool()._invoke(action="nope")
    assert "不支持的 action" in out and "list_templates" in out


# ---------- list_templates ----------

def test_list_templates_renders_rows(monkeypatch):
    _patch(monkeypatch, templates=[_tpl("t1", "投标文件审核"), _tpl("t2", "合同审核")])
    out = _make_tool()._invoke(action="list_templates")
    assert "投标文件审核" in out and "t1" in out and "合同审核" in out


def test_list_templates_empty(monkeypatch):
    _patch(monkeypatch, templates=[])
    out = _make_tool()._invoke(action="list_templates")
    assert "没有可用的审核模板" in out


# ---------- review：file_id 解析 ----------

def test_review_uses_file_id_from_begin_output(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": PFX + "upload-1"}))
    tool._invoke(action="review", user_query="重点看资质", kb_ids='["kb1", "kb2"]')
    kw = calls["rounds"][0]
    assert kw["file_id"] == PFX + "upload-1"
    assert kw["round_no"] == 1 and kw["file_version"] == "v1"
    assert kw["status"] == "reviewing"
    assert kw["tenant_id"] == "tenant_x" and kw["created_by"] == "tenant_x"
    assert kw["user_query"] == "重点看资质"
    assert kw["kb_ids"] == ["kb1", "kb2"]


def test_review_spawns_task_id_not_round_id(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert calls["spawned"] == [calls["rounds"][0]["task_id"]]
    assert "round-1" not in calls["spawned"]


def test_review_refuses_without_file_id(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    out = _make_tool(components=_begin({}))._invoke(action="review")
    assert "上传" in out
    assert calls["rounds"] == [] and calls["spawned"] == []


def test_review_refuses_when_begin_component_missing(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    out = _make_tool(components={})._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_when_begin_output_raises(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components={"begin": {"obj": _BadBegin()}})
    out = tool._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_when_begin_output_non_str(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": 123}))
    out = tool._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_without_tenant(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(tenant="", components=_begin({"review_file_id": "u1"}))
    out = tool._invoke(action="review")
    assert "身份" in out and calls["rounds"] == []


# ---------- review：模板与 kb_ids ----------

def test_review_rejects_unknown_template(monkeypatch):
    """LLM 编造的模板 id 不许静默回落默认值：否则轮次行会留下一条假模板审计记录。"""
    calls = _patch(monkeypatch, templates=[_tpl("t1", "投标文件审核")])
    tool = _make_tool(components=_begin({"review_file_id": "u1"}))
    out = tool._invoke(action="review", template_id="made-up-id")
    assert "不可用" in out and "投标文件审核" in out
    assert calls["rounds"] == []


def test_review_default_template_comes_from_executor_constant(monkeypatch):
    from rag.svr.file_review.executor import DEFAULT_TEMPLATE_ID

    calls = _patch(monkeypatch, templates=[_tpl(DEFAULT_TEMPLATE_ID)])
    _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert calls["rounds"][0]["template_id"] == DEFAULT_TEMPLATE_ID


def test_review_kb_ids_tolerant_parsing(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": "u1"}))
    tool._invoke(action="review", kb_ids="kb1, kb2")
    assert calls["rounds"][0]["kb_ids"] == ["kb1", "kb2"]
    calls["rounds"].clear()
    tool._invoke(action="review", kb_ids="")
    assert calls["rounds"][0]["kb_ids"] is None


# ---------- review：轮询 ----------

def test_review_poll_timeout_returns_task_id(monkeypatch):
    running = [_round(1, "reviewing")]
    calls = _patch(monkeypatch, templates=[_tpl()], rounds=running)
    out = _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert "仍在进行中" in out and "task_id=" in out and "status" in out
    assert calls["spawned"]


def test_review_poll_terminal_returns_summary(monkeypatch):
    done = [_round(1, "annotated", summary="共发现 3 处问题")]
    _patch(monkeypatch, templates=[_tpl()], rounds=done, pending=[_ann()])
    out = _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert "审核完成" in out and "共发现 3 处问题" in out
    assert "资质缺失" in out and "严重" in out


# ---------- status ----------

def test_status_requires_task_id():
    out = _make_tool()._invoke(action="status")
    assert "task_id" in out


def test_status_not_owned_refused(monkeypatch):
    _patch(monkeypatch, rounds=[])
    out = _make_tool()._invoke(action="status", task_id=PFX + "other-task")
    assert "没有找到" in out and "无权访问" in out


def test_status_lists_pending_grouped_by_severity(monkeypatch):
    rows = [_round(1, "annotated"), _round(2, "done", minio_path="frv-x-v2")]
    _patch(monkeypatch, rounds=rows,
           pending=[_ann("high"), _ann("medium"), _ann("low")])
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "严重" in out and "一般" in out and "提示" in out
    assert "成稿" in out and "余额" in out


def test_status_reports_failed_round_error(monkeypatch):
    rows = [_round(1, "failed", error="LLM 输出无法解析为修复补丁列表")]
    _patch(monkeypatch, rounds=rows)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "本轮失败" in out and "无法解析" in out


def test_status_reports_interrupted_round(monkeypatch):
    """服务重启后轮次停在 reviewing（线程没了、状态不回落）。若照 status 直译中文，
    答复会是「审核中」—— 与「永远不会出结果」的真相正好相反，用户只能干等。"""
    rows = [_round(1, "reviewing", create_time=0)]
    _patch(monkeypatch, rounds=rows, running=False)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "已中断" in out and "重新发起审核" in out
    assert "审核中" not in out


def test_status_not_interrupted_while_thread_alive(monkeypatch):
    """线程真在跑就不许说「已中断」——哪怕行龄远超宽限期（单轮 > 60s 是常态）。"""
    rows = [_round(1, "reviewing", create_time=0)]
    _patch(monkeypatch, rounds=rows, running=True)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "审核中" in out and "已中断" not in out


# ---------- fix ----------

def test_fix_creates_new_round_and_encodes_levels(monkeypatch):
    cur = _round(1, "annotated", user_query="重点看资质", kb_ids='["kb1"]')
    calls = _patch(monkeypatch, rounds=[cur], next_round=(2, "v2"), left=3,
                   pending=[_ann("high")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    kw = calls["rounds"][0]
    assert kw["round_no"] == 2 and kw["file_version"] == "v2"
    assert kw["status"] == "fixing"
    assert kw["file_id"] == cur.file_id          # 沿用该 task 的文件，不重新解析 Begin
    assert kw["template_id"] == cur.template_id
    assert kw["kb_ids"] == cur.kb_ids            # 修复轮继承知识库
    assert kw["tenant_id"] == "tenant_x" and kw["created_by"] == "tenant_x"
    assert "严重" in kw["user_query"] and "重点看资质" in kw["user_query"]
    assert calls["spawned"] == [kw["task_id"]]


def test_fix_never_mutates_annotation_status(monkeypatch):
    """级别过滤**不得**靠把未选中级别置 wontfix 实现。

    wontfix 的语义是「用户决定永不修复此条」；自动置位后用户改口「把中等的也修了」
    会静默失效（list_pending_by_task 不再看到它），且用户无法从对话里察觉。
    过滤改为写进本轮 user_query（executor 的修复 prompt 会带上它）。
    """
    cur = _round(1, "annotated")
    calls = _patch(monkeypatch, rounds=[cur], next_round=(2, "v2"),
                   pending=[_ann("high"), _ann("medium")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert calls["ann_updates"] == []
    assert calls["rounds"]


def test_fix_rejects_while_previous_round_running(monkeypatch):
    calls = _patch(monkeypatch, rounds=[_round(1, "reviewing")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert "仍在进行中" in out and calls["rounds"] == []


def test_fix_rejects_when_rounds_exhausted(monkeypatch):
    """文案由 Service 层受理闸门统一给出（T8/T9 共用同一份，不再各自措辞）。"""
    calls = _patch(monkeypatch, rounds=[_round(3, "done")], left=0)
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert "最大修复轮" in out and "手动处理" in out and calls["rounds"] == []


def test_fix_rejects_unknown_levels(monkeypatch):
    """未知级别必须拒绝而不是兜底成 medium：用户只想要 low 却被升格成 medium
    会让「只修低级别」变成「修中等级别」，与用户明确的指令相反。"""
    calls = _patch(monkeypatch, rounds=[_round(1, "annotated")], pending=[_ann("low")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="urgent")
    assert "levels" in out and calls["rounds"] == []


def test_fix_heals_interrupted_round_then_admits(monkeypatch):
    """服务重启后轮次永久停在 reviewing（线程没了、状态不回落）。R-1 自愈改变了受理
    语义：不再「拒 + 让用户重新发起审核」（旧契约，用例已作废），而是受理点上就地
    heal 为 failed、与崩溃 failed 轮同权走后续闸门 —— 有余额且有待修项即可直接发起
    下一轮修复。本用例锁定工具侧 heal→放行的完整链路与落库内容（受理闸门在 Service
    层，工具线程同步直调，无 R-6 的 to_thread 改造）。"""
    calls = _patch(monkeypatch, rounds=[_round(1, "reviewing", create_time=0)],
                   next_round=(2, "v2"), pending=[_ann("high")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")

    # 不再拒绝：新轮次照建、spawn 照发
    assert len(calls["rounds"]) == 1
    assert calls["rounds"][0]["round_no"] == 2 and calls["rounds"][0]["status"] == "fixing"
    assert calls["spawned"] == [PFX + "task"]
    # heal 落库恰好一次：置 failed 且 error 是确定性的中断文案（不是用户输入派生）
    assert len(calls["round_updates"]) == 1
    rid, status, extra = calls["round_updates"][0]
    assert (rid, status) == ("r1", "failed") and "已中断" in extra["error"]
    # heal 后 cur 已是 failed 终态，_poll 即刻给出状态摘要而不是「仍在进行中」干等
    assert "本轮失败" in out and "已中断" in out


def test_fix_rejects_when_no_pending_at_that_level(monkeypatch):
    """拒绝文案必须带全貌（各级别条数）而不是只说「没有」—— 否则用户无从知道该改选
    哪个级别，只能靠猜或反复试。文案与 REST 端点是同一份（Service 层给出）。"""
    calls = _patch(monkeypatch, rounds=[_round(1, "annotated")],
                   pending=[_ann("high"), _ann("high"), _ann("medium")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="low")
    assert "没有【提示】级别的待修复问题" in out
    assert "共 3 条" in out and "严重 2 条" in out and "一般 1 条" in out
    assert calls["rounds"] == []


def test_fix_not_owned_refused(monkeypatch):
    calls = _patch(monkeypatch, rounds=[])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "other", levels="high")
    assert "无权访问" in out and calls["rounds"] == []


def test_parse_levels_tolerant_and_ordered():
    from agent.tools.file_review import FileReviewTool

    assert FileReviewTool._parse_levels("低,high,LOW,严重") == ["high", "low"]
    assert FileReviewTool._parse_levels('["medium"]') == ["medium"]
    assert FileReviewTool._parse_levels(["medium", "high"]) == ["high", "medium"]
    assert FileReviewTool._parse_levels("") == []
    assert FileReviewTool._parse_levels(None) == []


# ---------- 代码质量审查整改（I1 / I2 / M3 / M7） ----------

def test_fix_second_round_does_not_stack_level_directives(monkeypatch):
    """连轮修复的拼装基准必须是**首轮原始需求**，不是上一轮（I1）。

    第 2 次修复时 rounds[-1] 本身就是修复轮，其 user_query 里带着**上一轮**已作废的
    级别指令；拿它当基准会拼出「只修【严重】…」+「只修【一般】…」两条互斥指令，而
    executor._build_fix_prompt 把 round_row.user_query **原样**塞进「用户需求：」——
    LLM 同时收到两条相斥的「只修某一级别」，结果该修的不修、或越界改了用户没选的级别。
    """
    r1 = _round(1, "annotated", user_query="重点看资质")
    r2 = _round(2, "done",
                user_query="重点看资质\n"
                           "本次只修复【严重/high】级别的问题，其余级别的问题请保持原样、不要改动。")
    calls = _patch(monkeypatch, rounds=[r1, r2], next_round=(3, "v3"),
                   pending=[_ann("medium")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="medium")

    q = calls["rounds"][0]["user_query"]
    assert q.count("本次只修复") == 1          # 上一轮的级别指令不得被带过来
    assert "一般/medium" in q                  # 本轮选中的级别（中英双写，与待修项里的
    assert "严重" not in q                     # 「级别=high」对齐，免去模型自行映射）
    assert "重点看资质" in q                    # 首轮原始需求必须保留（executor 只认它）


def test_fix_refuses_while_spawn_still_running(monkeypatch):
    """spawn 线程仍在收尾时不得建轮次（I2），否则留下不可自愈的卡死轮。

    三个已核实的事实叠加：① spawn_review_task 在 task_id 已在 _running_tasks 时**直接
    return**（无返回值、不报错）；② executor._run_fix_round **先**把轮次行置终态 done，
    **再** _settle_annotations 写最多 20 条标注，中间有宽度可达 20 次 DB 往返的窗口，
    轮次行读出来已是 done（前置闸门放行）而线程仍在集合里；③ execute_task 每次只处理
    rounds[-1] 一轮、不循环。于是新轮没有线程消费，恒停在 fixing，该 task 之后所有
    fix/review 都被前置闸门挡死，且 _force_fail_round 只在崩溃分支触发，无看门狗回收。
    本检查是唯一可行的闸门：_running_tasks 对同一 task_id 只有本调用点会写入（_review
    用全新 uuid），故观测到 False 时紧随其后的 spawn 必然能真正注册线程——它把不可逆的
    卡死换成了可重试的「稍后再试」，不是单纯降低概率。
    """
    calls = _patch(monkeypatch, rounds=[_round(1, "done")], running=True,
                   pending=[_ann("high")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")

    assert calls["rounds"] == [] and calls["spawned"] == []
    assert "稍后" in out or "收尾" in out


def test_fix_proceeds_when_spawn_not_running(monkeypatch):
    """反向用例：线程已退出时轮次照建，防过度拦截。"""
    calls = _patch(monkeypatch, rounds=[_round(1, "done")], running=False,
                   pending=[_ann("high")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")

    assert len(calls["rounds"]) == 1
    assert calls["spawned"] == [PFX + "task"]


def test_fix_surfaces_admission_denial_message_verbatim(monkeypatch):
    """受理闸门的拒绝文案必须**原样**进对话（工具不重写、不吞掉、不换通用兜底）。

    工具层已不再自己判前置状态（再判一次就是又一个「检查通过但线程未注册」的窗口），
    故所有拒绝分支的文案只有 Service 一处来源。这里直接打桩闸门按 reason 抛错，证明
    工具确实把 denied.message 透出、且入参形态（levels 列表 / 空 override）没被改动。
    """
    from api.db.services import file_review_service as svc

    _patch(monkeypatch, rounds=[_round(1, "annotated")], pending=[_ann("high")])
    seen = []

    def _deny(**kw):
        seen.append(kw)
        raise svc.FixAdmissionDenied("busy", "上一轮操作正在处理中，请稍后重试")

    monkeypatch.setattr(svc, "admit_fix_round", _deny)
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")

    assert out == "上一轮操作正在处理中，请稍后重试"
    assert seen == [{"task_id": PFX + "task", "tenant_id": "tenant_x",
                     "levels": ["high"], "user_query_override": ""}]


def test_fix_rejects_while_previous_round_fixing(monkeypatch):
    """_RUNNING_ROUND_STATUSES 的第二个值 fixing 也必须被拦：只测 reviewing 会漏。

    刻意让 is_running 同时为 True——若闸门顺序被写反（先查线程后查轮次状态），文案会
    变成「收尾」而非「仍在进行中」，本用例即失败。
    """
    calls = _patch(monkeypatch, rounds=[_round(1, "fixing")], running=True)
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")

    assert "仍在进行中" in out
    assert calls["rounds"] == [] and calls["spawned"] == []


def test_status_reports_produced_doc_for_failed_round(monkeypatch):
    """failed 轮次也可能已落盘成稿（T6 交接契约第 2 条：收口那步抛错前成稿已写 MinIO）。

    用 done + minio_path 构造等于没测——那种输入按 status 判断也能过；必须用 failed
    才能锁住「判据只看 minio_path，与 status 无关」这条契约。
    """
    rows = [_round(1, "failed", error="收口阶段抛错", minio_path="frv-x-v1")]
    _patch(monkeypatch, rounds=rows)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")

    assert "已产出第 1 轮成稿" in out


def test_status_truncates_pending_list(monkeypatch):
    """待修项超过 _MAX_LIST_ITEMS 只列前 N 条 + 一句「其余 N 条略」。

    返回值直接进 LLM 上下文，31 条全列会把上下文挤掉；截断数必须如实告知总数，
    否则用户会把「列出的条数」当成「问题总数」。
    """
    from agent.tools.file_review import _MAX_LIST_ITEMS

    assert _MAX_LIST_ITEMS == 30
    pending = [_ann("high", issue=f"问题{i}") for i in range(31)]
    _patch(monkeypatch, rounds=[_round(1, "annotated")], pending=pending)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")

    assert out.count("- [") == _MAX_LIST_ITEMS
    assert "其余 1 条略" in out


def test_status_clips_round_error(monkeypatch):
    """error 列允许写满 2000 字（executor），全量拼接会挤占 LLM 上下文。

    同函数内 summary/issue/matched_text 一律过 _clip，error 是唯一的漏网字段。
    """
    from agent.tools.file_review import _MAX_SUMMARY_CHARS

    rows = [_round(1, "failed", error="错" * 900)]
    _patch(monkeypatch, rounds=rows)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")

    assert out.count("错") == _MAX_SUMMARY_CHARS


def test_status_renders_dirty_severity_safely(monkeypatch):
    """脏 severity 的两条口径各不相同，都要锁住：

    None/空串 ⇒ 折成「未知」（那是「没有级别」，渲染字面 None 是纯 bug）；
    不认识的字符串 ⇒ **原样透出**（保留排障线索），不许抹平也不许渲染成 None。
    """
    rows = [_round(1, "annotated")]
    _patch(monkeypatch, rounds=rows, pending=[_ann(None), _ann("bogus")])
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")

    assert "None" not in out          # 字面 None 绝不许出现在给用户/LLM 的文案里
    assert "未知" in out              # None 那一行被折成「未知」
    assert "bogus" in out             # 未知串原样透出，不抹平


def test_review_reports_error_when_spawn_raises(monkeypatch):
    """外部依赖故障：spawn 抛错要变成用户可读文案 + _ERROR 置位。

    轮次行已落库这条**已知后果**如实断言，不假装不存在：create_round 在 spawn 之前，
    此时会留下一条 reviewing 轮（无人在跑，只能靠 T9 的 retry/看门狗回收）。
    """
    from rag.svr.file_review import spawn as spawn_mod

    calls = _patch(monkeypatch, templates=[_tpl()])

    def _boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn_mod, "spawn_review_task", _boom)
    tool = _make_tool(components=_begin({"review_file_id": "u1"}))
    out = tool._invoke(action="review")

    assert out == "文件审核执行失败：boom"
    assert tool._param.outputs["_ERROR"]["value"] == "boom"
    assert len(calls["rounds"]) == 1
    assert calls["rounds"][0]["status"] == "reviewing"
