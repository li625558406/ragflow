#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""「范本填写」画布节点单测（委托改造后）。对抗性覆盖：
- build_candidates：最新版本缺失 / 填写点为空 / key 缺失的脏数据
- parse_selection：非法 JSON、id 不在候选内、空列表、脏类型（选错范本必须报错
  而非静默换第一个；多选去重保序，兼容旧版单选契约）
- _invoke_async 委托行为：每范本 insert 一条 source=canvas 的 tpl_fill_task 行
  （template_version_id/kb_ids/params 下发契约）、spawn_fill_task 被调（测试内
  零真线程）、观察者按 DB 行状态/快照推 selected→filling→filled/failed→done、
  成稿桥接落 {tenant_id}-downloads bucket、下载输出列表契约（下游 Message 依赖）
- _confirm_changed_fields（①段确认环节，未变职责）：超时兜底 + 确认载荷过滤
所有外部依赖（Service / settings.STORAGE_IMPL / spawn_fill_task / 快照）经模块
属性注入替身，不触真实 DB / LLM / MinIO / Redis / 线程。
检索/产值/渲染细节已移交 executor.execute_task，由 test_template_fill_executor.py 覆盖。"""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from agent.component import template_fill as fill_template
from agent.component.template_fill import (
    TemplateFillParam,
    build_candidates,
    parse_selection,
)

# ---------- 桩件 ----------

PH_XLSX_SLOTS = [
    {"key": "k1", "name": "项目名称", "fill_mode": "llm"},
    {"key": "k2", "name": "金额", "fill_mode": "param", "addr": "Sheet1!B2"},
]

# 工作上下文 id（会话）：与 FakeCanvas 默认 sys.session_id 一致；增量基线键按
# (template_id, context_id) 二维组织，镜像服务端 latest_done_in_context 的过滤条件
CTX = "sess-1"


class FakeCanvas:
    def __init__(self, tenant="t1", sys_query="写一份道路工程情况报告",
                 begin_fields=None, file_content="", session_id=CTX):
        self._tenant = tenant
        self._sys_query = sys_query
        # sys.session_id = 本次运行的工作上下文（canvas_service.completion 写入）；
        # 增量基线只认同上下文（见 latest_done_in_context）
        self.globals = {"sys.query": sys_query, "sys.file_content": file_content,
                        "sys.session_id": session_id}
        begin_fields = begin_fields or {}
        begin_obj = SimpleNamespace(component_name="Begin", output=lambda: begin_fields)
        self.components = {"begin": {"obj": begin_obj}}

    def is_canceled(self):
        return False

    def get_tenant_id(self):
        return self._tenant

    def get_component_name(self, cpn_id):
        return "begin"

    def get_variable_value(self, exp):
        return self._sys_query if exp == "sys.query" else None


def _make_component(param=None, canvas=None):
    cpn = fill_template.TemplateFill.__new__(fill_template.TemplateFill)
    cpn._id = "node1"
    cpn._param = param or TemplateFillParam()
    cpn._param.check()
    cpn._canvas = canvas or FakeCanvas()
    cpn._event_queue = asyncio.Queue()
    return cpn


def _ver(placeholders, render_file_id="render_obj"):
    # id 必填：委托改造后节点 insert 任务行时引用 _ver.id 作为 template_version_id
    return SimpleNamespace(placeholders=placeholders, render_file_id=render_file_id,
                           version=1, id="v1")


class FakeService:
    """替身：类方法签名对齐 TplTemplateService.get_list_page / TplTemplateVersionService.latest。"""
    rows = []
    vers = {}

    @classmethod
    def get_list_page(cls, tenant_id, keyword="", status="", page=1, size=20):
        assert status == "published"
        return [dict(r) for r in cls.rows], len(cls.rows)

    @classmethod
    def latest(cls, template_id):
        return cls.vers.get(template_id)


class _Row:
    """tpl_fill_task 行桩：status/result_file_id/error 按 id 供给观察者轮询。"""

    def __init__(self, id, status="pending", result_file_id="", error="",
                 values=None, template_version_id=""):
        self.id, self.status, self.result_file_id, self.error = \
            id, status, result_file_id, error
        self.values = values or {}
        self.template_version_id = template_version_id


class FakeTaskService:
    """TplFillTaskService 桩（与 test_template_fill_events.py 同风格）：insert 记录
    参数并把节点传入的 uuid id 依插入顺序映射到序列键 task-N；get_or_none 按
    sequences {task-N: [Row, ...]} 逐次弹出（耗尽后重复最后一个）；find_running
    恒 None（画布场景默认无历史任务 → 每范本新建）。"""

    def __init__(self):
        self.sequences = {}
        self.queries = {}
        self.id_map = {}
        self.inserted = []
        # (template_id, context_id) -> _Row 桩（增量填写场景，键含上下文以镜像服务端过滤）
        self.latest_done_map = {}
        self.ctx_calls = []        # 节点实际传下来的 (template_id, context_id)
        self._next = 0

    @classmethod
    def find_running(cls, template_id, tenant_id):
        return None

    @classmethod
    def latest_done(cls, template_id, tenant_id):
        """宽口径定位查询（对话 modify/detail 用）。节点**不应**再调它取基线——
        这里保留是让「跨上下文」用例能证明：即使宽查有历史成稿，节点拿到的
        baseline 也必须是 None。"""
        inst = getattr(cls, "_active_instance", None)
        if inst is None:
            return None
        return next((r for (t, _), r in inst.latest_done_map.items() if t == template_id), None)

    @classmethod
    def latest_done_in_context(cls, template_id, tenant_id, context_id):
        """镜像服务端实现：空上下文 → None（不做增量）；否则上下文必须精确匹配。"""
        inst = getattr(cls, "_active_instance", None)
        if inst is None:
            return None
        inst.ctx_calls.append((template_id, context_id))
        if not context_id:
            return None
        return inst.latest_done_map.get((template_id, context_id))

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
        row = seq[min(i, len(seq) - 1)]
        row.id = id  # 与生产一致：row.id 即任务 uuid（桥接 doc_id 由它派生）
        return row


def _ver_slot(name, mode="llm", addr=None):
    it = {"key": name, "name": name, "fill_mode": mode}
    if addr:
        it["addr"] = addr
    return it


# ---------- build_candidates ----------

def test_build_candidates_skips_missing_version_and_empty_slots():
    rows = [
        {"id": "t1", "name": "报告", "description": "d", "file_type": "docx"},
        {"id": "t2", "name": "无版本", "description": "", "file_type": "docx"},
        {"id": "t3", "name": "空填写点", "description": "", "file_type": "docx"},
    ]
    vers = {"t1": _ver([_ver_slot("项目名称")]), "t3": _ver([])}
    out = build_candidates(rows, vers.get)
    assert [c["template_id"] for c in out] == ["t1"]
    assert out[0]["slot_names"] == ["项目名称"]


def test_build_candidates_slots_missing_key_dropped():
    placeholders = [{"name": "无key"}, {"key": "k1", "name": ""}]
    out = build_candidates([{"id": "t1", "name": "n"}], {"t1": _ver(placeholders)}.get)
    assert out[0]["slot_names"] == ["k1"]


# ---------- parse_selection（对抗性） ----------

def test_parse_selection_valid_single_legacy():
    # 旧版单选契约 {"template_id": ...} 兼容
    assert parse_selection('{"template_id": "t2"}', ["t1", "t2"]) == ["t2"]


def test_parse_selection_multi_dedupe_preserve_order():
    ans = '{"template_ids": ["t2", "t1", "t2", "t1"]}'
    assert parse_selection(ans, ["t1", "t2"]) == ["t2", "t1"]


def test_parse_selection_invalid_json():
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection("我觉得都不合适", ["t1"])


def test_parse_selection_unknown_id():
    # 选型 LLM 编造不存在的范本 id → 必须报错，不允许静默降级第一个
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection('{"template_ids": ["t1", "ghost"]}', ["t1"])


def test_parse_selection_empty_list():
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection('{"template_ids": []}', ["t1"])


def test_parse_selection_null_value():
    with pytest.raises(ValueError):
        parse_selection('{"template_id": null}', ["t1"])


def test_parse_selection_ids_not_list():
    # 脏输出：template_ids 是字符串 → 容错为单元素；不在候选内报错
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection('{"template_ids": "ghost"}', ["t1"])
    assert parse_selection('{"template_ids": "t1"}', ["t1"]) == ["t1"]


# ---------- 参数默认值 ----------

def test_param_defaults():
    p = TemplateFillParam()
    assert p.dataset_ids == []
    assert p.query == ""
    assert set(p.outputs) == {"content", "download"}


# ---------- _invoke_async 委托行为（全部依赖打桩，零真线程） ----------

@pytest.fixture()
def patched_env(monkeypatch):
    """统一打桩：范本 Service / tpl_fill_task Service / 存储 / spawn_fill_task /
    观察快照 / 轮询 sleep。spawn_fill_task 仅记录不建线程；观察者 1.5s 轮询降为
    空等待。返回 {"calls": 记录器, "svc": 任务服务桩}。"""
    calls = {"put": [], "get": [], "spawn": []}

    class FakeStorage:
        def get(self, bucket, obj):
            calls["get"].append((bucket, obj))
            if "missing" in obj:
                return None
            return b"TPL_BLOB"

        def put(self, bucket, doc_id, blob):
            calls["put"].append((bucket, doc_id, blob))

    monkeypatch.setattr(fill_template, "TplTemplateService", FakeService)
    monkeypatch.setattr(fill_template, "TplTemplateVersionService", FakeService)
    svc = FakeTaskService()
    monkeypatch.setattr(fill_template, "TplFillTaskService", svc)
    monkeypatch.setattr(fill_template.settings, "STORAGE_IMPL", FakeStorage())
    monkeypatch.setattr(fill_template, "spawn_fill_task",
                        lambda task_id: calls["spawn"].append(task_id))
    monkeypatch.setattr(fill_template.executor, "read_progress_snapshot",
                        lambda task_id: None)
    # invoke 路径会真调 _confirm_changed_fields → 实体抽取必须桩掉（防触真 LLM）
    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    # FakeTaskService.latest_done 是 classmethod，节点走 cls 调用；桩里走 _active_instance
    # 单例路由找最新构造的桩实例，方便各测试通过 svc.latest_done_map[(tid, ctx)] 注入历史 done 行
    FakeTaskService._active_instance = svc

    async def _sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return {"calls": calls, "svc": svc}


def _stage_one_candidate(template_id="t1", placeholders=None, file_type="docx"):
    FakeService.rows = [{"id": template_id, "name": "道路报告", "description": "d",
                         "file_type": file_type}]
    FakeService.vers = {template_id: _ver(placeholders or [_ver_slot("项目名称")])}


def _stage_two_candidates():
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {t: _ver([_ver_slot("项目名称")]) for t in ("t1", "t2")}


def _seq_done(svc, n=1, result_file_id="rf1"):
    """为前 n 个任务行配置「一次非终态 → done 终态」的观察序列。"""
    svc.sequences = {f"task-{i}": [_Row(f"task-{i}"),
                                   _Row(f"task-{i}", status="done",
                                        result_file_id=result_file_id)]
                     for i in range(1, n + 1)}


def test_invoke_async_happy_path_docx(patched_env):
    """单范本成功：委托面契约全量校验——insert 行参数（版本 pin / kb_ids /
    canvas 来源 / params 下发键）+ spawn 与 insert 同 id + 事件序列
    selected→filling→filled→done + 成稿桥接落 {tenant_id}-downloads bucket +
    下载输出六字段契约（下游 Message._extract_downloads / 前端下载预览依赖）。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    # 任务行：一范本一行，id 为 uuid，版本经 _ver.id pin 住
    assert len(svc.inserted) == 1
    tid, kw = svc.inserted[0]
    assert kw["template_id"] == "t1"
    assert kw["template_version_id"] == "v1"
    assert kw["kb_ids"] == ["kb1"]
    assert kw["source"] == "canvas"
    assert kw["status"] == "pending"
    assert kw["tenant_id"] == "t1"
    assert re.fullmatch(r"[0-9a-f-]{32,}", tid), "任务 id 应为 uuid"
    # params 下发契约：背景 + 下划线保留键（executor 侧 split_canvas_params 拆解）
    params = kw["params"]
    assert params["用户需求描述"] == "写一份道路工程情况报告"
    assert params["_direct_values"] == {}
    assert params["_changed_keys"] == ["项目名称"]
    assert params["_retrieve_skip_keys"] == []
    assert params["_user_file_text"] == ""
    # spawn 与 insert 同 id（真线程已被桩掉，测试零真线程）
    assert calls["spawn"] == [tid]
    # 事件序列
    evs = _drain_events(cpn)
    assert [(e["stage"], e.get("template_id")) for e in evs] == [
        ("selected", None), ("filling", "t1"), ("filled", "t1"), ("done", None)]
    filling = evs[1]
    assert filling["task_id"] == tid
    assert filling["done"] == 0 and filling["total"] == 1
    assert filling["name"] == "道路报告"
    # 产物必须落 {tenant_id}-downloads bucket：/agents/download 与
    # /files/{id}/content 两个端点的既有读取契约都是这个 bucket
    assert calls["put"] == [("t1-downloads", f"tplfill-{tid}", b"TPL_BLOB")]
    # 下载输出契约：输出为列表（多范本各一份），每项六字段
    dls = json.loads(cpn.output("download"))
    assert isinstance(dls, list) and len(dls) == 1
    dl = dls[0]
    assert set(dl) == {"doc_id", "filename", "mime_type", "size", "url", "name"}
    assert dl["doc_id"] == f"tplfill-{tid}"
    assert dl["url"].startswith("/api/v1/agents/download?id=tplfill-")
    assert dl["name"] == "道路报告.docx"
    assert dl["filename"] == "道路报告.docx"
    assert dl["mime_type"].endswith("wordprocessingml.document")
    assert dl["size"] == len(b"TPL_BLOB")
    assert "AI 填充完成" in cpn.output("content")
    assert "共 1 个填写点" in cpn.output("content")
    assert "留空" in cpn.output("content")


def test_invoke_async_single_candidate_skips_selection_llm(patched_env, monkeypatch):
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)

    async def boom():
        raise AssertionError("唯一候选不应调用选型 LLM")
    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl",
                        lambda *_: SimpleNamespace(async_chat=boom))
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    assert len(svc.inserted) == 1


def test_invoke_async_multi_candidate_llm_selects(patched_env, monkeypatch):
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_two_candidates()

    class FakeMdl:
        async def async_chat(self, system, msgs):
            return '{"template_id": "t2"}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: FakeMdl())
    # 桥接读取 t2 bucket 工作副本（result_file_id 命名 render_obj 以复用既有断言口径）
    _seq_done(svc, result_file_id="render_obj")
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    # 只为选中的 t2 建任务行 + spawn，落选的 t1 不建行
    assert [kw["template_id"] for _, kw in svc.inserted] == ["t2"]
    assert len(calls["spawn"]) == 1
    assert calls["get"].count(("t2", "render_obj")) == 1


def test_invoke_async_no_candidates(patched_env):
    FakeService.rows = []
    FakeService.vers = {}
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    with pytest.raises(ValueError, match="暂无可用的已发布范本"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_no_kb_selected(patched_env):
    cpn = _make_component(TemplateFillParam())  # dataset_ids 空
    with pytest.raises(ValueError, match="知识库"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_selection_llm_invalid_output(patched_env, monkeypatch):
    # 两个候选 + 选型 LLM 输出非法 → 节点报错，不静默换第一个
    _stage_two_candidates()

    class BadMdl:
        async def async_chat(self, system, msgs):
            return "输出不了 JSON"

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: BadMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        asyncio.run(cpn._invoke_async())


# 以下三个用例已随委托改造删除（职责移交 executor.execute_task，由
# test/test_template_fill_executor.py 覆盖）：
# - test_invoke_async_missing_render_blob（工作副本读取/校验在 execute_task 内）
# - test_invoke_async_xlsx_addr_passthrough（xlsx addr 坐标直写在 executor 渲染侧）
# - test_invoke_async_shared_retrieval_called_once_for_multi_templates
#   （跨范本共享检索去重在 executor retrieve_all_shared 侧）


def test_invoke_async_query_fallback_sys_query(patched_env):
    """query 为空回退 {sys.query}：解析结果经任务 params 背景字段下发 executor。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(sys_query="我的需求"))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    assert svc.inserted[0][1]["params"]["用户需求描述"] == "我的需求"


def test_invoke_async_multi_template_ids_two_outputs(patched_env, monkeypatch):
    """选型 LLM 返回多个 template_ids → 每个范本各建一行任务、各产一份成稿，
    选型顺序保序体现在任务行与下载列表。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_two_candidates()

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t2", "t1"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    _seq_done(svc, n=2)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    assert [(svc.id_map[tid], kw["template_id"]) for tid, kw in svc.inserted] == [
        ("task-1", "t2"), ("task-2", "t1")]
    assert len(calls["spawn"]) == 2
    dls = json.loads(cpn.output("download"))
    assert [d["filename"] for d in dls] == ["报告B.docx", "报告A.docx"]
    assert len(calls["put"]) == 2
    assert all(bucket == "t1-downloads" for bucket, _doc, _blob in calls["put"])
    assert "已选用 2 份范本" in cpn.output("content")
    assert "报告A" in cpn.output("content") and "报告B" in cpn.output("content")


def test_invoke_async_entities_union_flows_to_params(patched_env, monkeypatch):
    """invoke 接线端到端断言：confirm 阶段各范本 extract_entities 产出的 entities
    经 _merged_entities 并集后，随每行任务 params._entities 下发 executor
    （executor 侧 split_canvas_params 拆解为二档检索拼词）。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {
        "t1": _ver([_ver_slot("项目名称")]),
        "t2": _ver([_ver_slot("建设单位")]),
    }

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        keys = {it["key"] for it in placeholders}
        if "建设单位" in keys:
            return {"direct": {}, "entities": {"单位B": "乙公司"}}
        return {"direct": {}, "entities": {"单位A": "甲公司"}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t1", "t2"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    _seq_done(svc, n=2)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    # 并集（先到先得）：两行任务 params._entities 均为各范本实体并集
    merged = {"单位A": "甲公司", "单位B": "乙公司"}
    assert len(svc.inserted) == 2
    for _tid, kw in svc.inserted:
        assert kw["params"]["_entities"] == merged


def test_invoke_async_begin_field_param_direct(patched_env):
    """Begin 表单字段经任务 params 原样下发（executor 侧按同名 param 槽直取，
    不经 LLM）；param 槽不进检索跳过清单（llm 槽专属）。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate(placeholders=[_ver_slot("金额", mode="param", addr="Sheet1!B2")])
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(),
                          canvas=FakeCanvas(begin_fields={"金额": "1024.5"}))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    params = svc.inserted[0][1]["params"]
    assert params["金额"] == "1024.5"
    assert params["_retrieve_skip_keys"] == []


def test_invoke_async_user_file_evidence_prepended(patched_env):
    """用户上传文件文本经 params._user_file_text 下发（executor 预置为每个 llm 槽
    证据首位，[用户上传文件] 前缀）；需求描述照常进背景。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(),
                          canvas=FakeCanvas(file_content="这是上传的可研报告正文"))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    params = svc.inserted[0][1]["params"]
    assert params["_user_file_text"] == "这是上传的可研报告正文"
    assert params["用户需求描述"] == "写一份道路工程情况报告"


def test_invoke_async_user_file_absent_no_injection(patched_env):
    """无上传文件（sys.file_content 为空）→ _user_file_text 空串下发。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(file_content=""))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    assert svc.inserted[0][1]["params"]["_user_file_text"] == ""


def test_invoke_async_one_template_failure_degrades_to_summary_line(patched_env, monkeypatch):
    """多范本：单个范本任务终态 failed 不拖死节点 → 该范本推 failed 并降级为
    汇总行，其余范本照常 filled 产出。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_two_candidates()

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t1", "t2"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    svc.sequences = {
        "task-1": [_Row("task-1"), _Row("task-1", status="done", result_file_id="rf1")],
        "task-2": [_Row("task-2", status="failed", error="检索炸了")]}
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    dls = json.loads(cpn.output("download"))
    assert [d["filename"] for d in dls] == ["报告A.docx"]
    evs = _drain_events(cpn)
    stages = [(e["stage"], e.get("template_id")) for e in evs]
    assert ("failed", "t2") in stages
    assert ("filled", "t1") in stages
    assert stages[-1] == ("done", None)
    assert "填写失败" in cpn.output("content") and "报告B" in cpn.output("content")
    assert "检索炸了" in cpn.output("content")
    assert "报告A" in cpn.output("content")


def test_invoke_async_all_templates_failed_raises(patched_env):
    """所有范本任务均终态 failed → 节点报错（携带首个原因），不输出空下载列表。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_one_candidate()
    svc.sequences = {"task-1": [_Row("task-1", status="failed", error="检索炸了")]}
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    with pytest.raises(ValueError, match="所有范本填写均失败"):
        asyncio.run(cpn._invoke_async())


# ---------- P2 暂停确认：_confirm_changed_fields（超时兜底 + 确认载荷过滤） ----------

def _confirm_component(monkeypatch, canvas_task_id="task-9"):
    """确认编排专用组件：画布带 task_id（真实 canvas 构造时注入，缺省回退空串即跳过等待）。
    实体抽取默认桩为空结果（防既有用例真调 LLM）；需要 direct/entities 的用例自行覆盖。"""
    canvas = FakeCanvas()
    canvas.task_id = canvas_task_id

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    return _make_component(TemplateFillParam(), canvas=canvas)


def _chosen_with_defaults():
    """一个带默认值字段（甲，旧默认值）+ 一个无默认值字段（乙）的选中范本。"""
    return [{"template_id": "t1", "name": "道路报告",
             "_placeholders": [
                 {"key": "a", "name": "甲", "fill_mode": "llm", "default_value": "旧默认"},
                 {"key": "b", "name": "乙", "fill_mode": "llm"}]}]


def _drain_events(cpn):
    events = []
    while not cpn._event_queue.empty():
        events.append(cpn._event_queue.get_nowait()["data"])
    return events


def test_confirm_wait_timeout_uses_predicted(monkeypatch):
    """Redis 一直无确认键 → 超时后按「预判 ∪ 无默认值字段」兜底自动继续
    （与确认卡初始勾选一致），且推送 confirm_pending + confirm_timeout 两个事件；
    confirm_pending 事件必须下发运行级 confirm_nonce（I2）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError("超时路径不得删除任何键")

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_predict(tenant_id, items, background=None, should_cancel=None):
        assert tenant_id == "t1"
        assert [it["key"] for it in items] == ["a"], "无默认值字段不参与预判"
        return {"a", "ghost"}  # 预判编造 key 原样带入（消费侧再过滤）

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    # 兜底：预判（a + 编造 ghost）∪ 无默认值字段（b）——乙照旧交给 LLM
    assert decisions == {"t1": {"changed": {"a", "ghost", "b"}, "values": {}}}
    events = _drain_events(cpn)
    stages = [e["stage"] for e in events]
    assert stages == ["confirm_pending", "confirm_timeout"]
    nonce = events[0]["confirm_nonce"]
    assert isinstance(nonce, str) and re.fullmatch(r"[A-Za-z0-9-]{1,64}", nonce), \
        "confirm_pending 必须下发合法的运行级 confirm_nonce"


def test_confirm_payload_filters_unknown_keys(monkeypatch):
    """确认载荷含 ghost key / 越范本 id → 只留合法 key，直填 values 进入 decisions；
    轮询/消费键必须带运行级 nonce（tpl_fill:confirm:{task_id}:{nonce}，I2），
    且与 confirm_pending 事件下发的 confirm_nonce 一致；不再推 confirm_timeout。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    payload = json.dumps({
        "t1": {"changed": ["a", "ghost", 123], "values": {"a": "直填值", "ghost": "x"}},
        "t2": {"changed": ["zzz"], "values": {"zzz": "越范本"}},
    }, ensure_ascii=False)
    seen = {"deleted": []}

    class FakeRedis:
        def get(self, k):
            seen["get"] = k
            return payload

        def delete(self, k):
            seen["deleted"].append(k)

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_predict(*a, **kw):
        return set()

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    confirm_key = seen["get"]
    assert confirm_key.startswith("tpl_fill:confirm:task-9:"), \
        f"确认键必须带运行级 nonce，实际: {confirm_key}"
    assert seen["deleted"] == [confirm_key], "确认键必须消费防重复触发"
    nonce = confirm_key.rsplit(":", 1)[1]
    events = _drain_events(cpn)
    pending = [e for e in events if e["stage"] == "confirm_pending"]
    assert pending and pending[0]["confirm_nonce"] == nonce, \
        "事件下发的 confirm_nonce 必须与轮询键中的 nonce 一致（前端携带它调 confirm 端点）"
    assert [e["stage"] for e in events] == ["confirm_pending"], "拿到确认后不得再推 confirm_timeout"
    assert decisions == {"t1": {"changed": {"a"}, "values": {"a": "直填值"}}}, \
        "ghost/非串 key 过滤、越范本 id(t2) 整体忽略、直填值进 values"


def test_confirm_payload_scalar_values_defensive(monkeypatch):
    """M1 对抗性：非官方写键塞 values 为标量 / changed 为字符串 → 不炸 run，
    按「无值/无变化」处理（只更新对应范本 decisions 为空集）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    payload = json.dumps({
        "t1": {"changed": "a", "values": "not-a-dict"},
        "t2": {"changed": ["zzz"], "values": 42},
    }, ensure_ascii=False)

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_predict(*a, **kw):
        return set()

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    assert decisions == {"t1": {"changed": set(), "values": {}}}, \
        "values 标量 / changed 非串兜底为空，不得 AttributeError"


def test_confirm_gather_cancel_raises_fill_cancelled(monkeypatch):
    """gather 取消收口：一路（预判）抛 GenerateCancelled → 转抛
    fill_template._FillCancelled（取消信号不外逸成裸异常）；return_exceptions
    语义下兄弟协程（实体抽取）跑完不被打成孤儿 Task，且不弹确认卡。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    extract_done = []

    async def fake_predict(*a, **kw):
        raise fill_template.executor.GenerateCancelled()

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        extract_done.append(True)
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)
    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)

    with pytest.raises(fill_template._FillCancelled):
        asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    assert extract_done == [True], "兄弟协程应跑完（return_exceptions 收口），不产生孤儿 Task"
    assert _drain_events(cpn) == [], "取消路径不得弹出 confirm_pending 确认卡"


def _incremental_overrides_for_t1():
    """模拟节点增量调用：候选只列 patch 项，兜底 values = LLM 抽出的 direct
    （用户在对话里说「approval_doc 填写成 港里」→ {approval_doc: 港里}）。"""
    return {
        "t1": {
            "candidates": [
                {"key": "approval_doc", "name": "批文名称及编号",
                 "default_value": "原批文"},
                {"key": "project_owner", "name": "项目业主",
                 "default_value": "原业主"},
            ],
            "predicted": ["approval_doc", "project_owner"],
            "fallback_changed": {"approval_doc", "project_owner"},
            "fallback_values": {"approval_doc": "港里"},
        },
    }


def test_incremental_confirm_empty_values_preserves_fallback(monkeypatch):
    """增量模式 + 用户在确认卡不输入（前端 inputs 默认空），点「确认并继续
    填写」→ 提交载荷 values={}。此时必须保留 fallback_values（LLM 从用户原话
    抽出的 direct），否则对话里说的「X 填写成 Y」会被静默丢弃、补丁字段留空。
    修复点：ov 分支先 dict(fb_values)，再叠非空用户输入。"""
    cpn = _confirm_component(monkeypatch)
    payload = json.dumps({
        "t1": {"changed": ["approval_doc", "project_owner"], "values": {}},
    }, ensure_ascii=False)

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())
    # 增量模式下无需预判，但函数仍可能调用，stub 兜底
    async def fake_predict(*a, **kw):
        return set()
    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(
        _chosen_with_defaults(), "需求", {}, incremental_overrides=_incremental_overrides_for_t1()))
    # fallback_values 必须保留：用户空输入 ≠ 用户改值
    assert decisions == {"t1": {
        "changed": {"approval_doc", "project_owner"},
        "values": {"approval_doc": "港里"},
    }}


def test_incremental_confirm_user_non_empty_overrides_fallback(monkeypatch):
    """用户在确认卡显式改了值（非空字符串）→ 覆盖 fallback；空字符串=未动=保留
    fallback（防前端空串误判覆盖）。"""
    cpn = _confirm_component(monkeypatch)
    payload = json.dumps({
        "t1": {
            "changed": ["approval_doc"],
            # 用户在 approval_doc 输入框改成了别的值，project_owner 没动
            "values": {"approval_doc": "用户改的批文", "project_owner": ""},
        },
    }, ensure_ascii=False)

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())
    async def fake_predict(*a, **kw):
        return set()
    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(
        _chosen_with_defaults(), "需求", {}, incremental_overrides=_incremental_overrides_for_t1()))
    # approval_doc 用户改了 → 用用户的；project_owner 不在 fallback_values 里、
    # 用户空输入 → 不入 values（空串=未动，不应覆盖）
    assert decisions["t1"]["values"] == {"approval_doc": "用户改的批文"}, \
        "非空用户输入覆盖 fallback；空串=未动不入 values"


def test_incremental_confirm_unknown_keys_filtered(monkeypatch):
    """载荷里塞越范本/非候选 key → 必须过滤；保留 fallback_values 不变。"""
    cpn = _confirm_component(monkeypatch)
    payload = json.dumps({
        "t1": {
            "changed": ["approval_doc", "ghost", "unknown"],
            "values": {"approval_doc": "", "ghost": "x", "unknown": "y"},
        },
        "t2": {"changed": ["x"], "values": {"x": "越范本"}},  # 越范本
    }, ensure_ascii=False)

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())
    async def fake_predict(*a, **kw):
        return set()
    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(
        _chosen_with_defaults(), "需求", {}, incremental_overrides=_incremental_overrides_for_t1()))
    # t2 整体忽略；t1 非候选 key 过滤；fallback 保留
    assert decisions == {"t1": {
        "changed": {"approval_doc"},  # ghost/unknown 过滤
        "values": {"approval_doc": "港里"},  # 用户空输入 + fallback
    }}


def test_confirm_no_default_items_skips_all(monkeypatch):
    """回归红线：全部选中范本均无默认值字段且无实体直填值 → decisions={} 跳过确认，
    不推事件、不触 Redis、不调预判（实体抽取仍会跑——它是弹卡条件的输入之一）。"""
    import asyncio

    cpn = _make_component(TemplateFillParam())  # FakeCanvas 无 task_id 也无关紧要

    def boom(*a, **kw):
        raise AssertionError("无默认值字段不应触发预判/Redis")

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template, "REDIS_CONN", boom)
    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", boom)
    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    chosen = [{"template_id": "t1", "name": "报告",
               "_placeholders": [{"key": "k1", "name": "字段", "fill_mode": "llm"}]}]
    assert asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))[0] == {}
    assert _drain_events(cpn) == []


def test_confirm_candidates_carry_direct_value(monkeypatch):
    """实体直填值进 confirm_pending 事件 candidates.direct_value（前端预填输入框）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_predict(*a, **kw):
        return set()

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {"a": "直填甲"}, "entities": {"项目名称": "A项目"}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    decisions, emap = asyncio.run(
        cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    events = _drain_events(cpn)
    pending_ev = [e for e in events if e["stage"] == "confirm_pending"][0]
    cands = pending_ev["confirm_templates"][0]["candidates"]
    by_key = {c["key"]: c for c in cands}
    assert by_key["a"]["direct_value"] == "直填甲"
    assert by_key["b"]["direct_value"] == ""
    # 兜底 values 带直填值（超时未确认也不丢用户原话给出的值）
    assert decisions["t1"]["values"] == {"a": "直填甲"}
    assert emap["t1"]["entities"] == {"项目名称": "A项目"}


def test_confirm_pops_card_for_direct_without_defaults(monkeypatch):
    """无默认值字段但有实体直填值 → 也弹确认卡（原状是直接跳过）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {"b": "直填乙"}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    chosen = [{"template_id": "t1", "name": "范本",
               "_placeholders": [{"key": "b", "name": "乙", "fill_mode": "llm"}]}]
    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))
    events = _drain_events(cpn)
    assert any(e["stage"] == "confirm_pending" for e in events), "直填值必须弹卡供确认"
    assert decisions["t1"]["values"] == {"b": "直填乙"}


def test_confirm_skipped_when_no_defaults_no_direct(monkeypatch):
    """无默认值且无直填值 → 维持原状跳过确认（返回空 decisions），不推事件。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    chosen = [{"template_id": "t1", "name": "范本",
               "_placeholders": [{"key": "b", "name": "乙", "fill_mode": "llm"}]}]
    decisions, emap = asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))
    assert decisions == {}
    assert emap["t1"] == {"direct": {}, "entities": {}}
    assert _drain_events(cpn) == []


def test_confirm_incremental_skips_entity_extraction(monkeypatch):
    """增量范本不做实体抽取（patch 流程有自己的 direct 抽取），extract 不被调用。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)
    called = {"n": 0}

    async def fake_extract(*a, **kw):
        called["n"] += 1
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    ov = {"t1": {"candidates": [{"key": "a", "name": "甲"}],
                 "predicted": ["a"], "fallback_changed": {"a"}, "fallback_values": {}}}
    asyncio.run(cpn._confirm_changed_fields(
        _chosen_with_defaults(), "需求", {}, incremental_overrides=ov))
    assert called["n"] == 0


# ---------- P2 条件执行：decision 消费侧（委托参数收窄 + 直填下发） ----------

def test_decision_conditional_execution_skips_unchanged_defaults(patched_env):
    """白名单语义（decision 消费侧，委托参数形式）：decisions={"changed": {a},
    "values": {f: 直填}}，四个 llm 字段中——
    - a（勾选）→ 走检索 + LLM（白名单唯一直填外成员）；
    - d（无默认值、未勾选）→ 进 _retrieve_skip_keys（留空交人工）；
    - e（有默认值、未勾选）→ 进 skip（executor 渲染直取默认值）；
    - f（未勾选但用户直填）→ 进 skip + 直填值经 _direct_values 下发。
    executor 侧按这些保留键跳过检索/直取默认值/沉淀 override，由
    test_template_fill_executor.py 覆盖。"""
    calls, svc = patched_env["calls"], patched_env["svc"]

    ph = [
        {"key": "a", "name": "甲", "fill_mode": "llm", "default_value": "旧甲"},
        {"key": "d", "name": "丁", "fill_mode": "llm"},
        {"key": "e", "name": "戊", "fill_mode": "llm", "default_value": "旧戊"},
        {"key": "f", "name": "己", "fill_mode": "llm", "default_value": "旧己"},
    ]
    _stage_one_candidate(placeholders=ph)
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    async def fake_confirm(chosen, query, begin_fields, *, incremental_overrides=None):
        return ({"t1": {"changed": {"a"}, "values": {"f": "直填值"}}}, {})

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    params = svc.inserted[0][1]["params"]
    # 检索/LLM 收窄：d/e/f（白名单外 + 直填）进跳过清单，只有 a 走检索
    assert params["_retrieve_skip_keys"] == ["d", "e", "f"]
    # 白名单 = _changed_keys（executor 侧按它收窄 LLM 槽）
    assert params["_changed_keys"] == ["a"]
    # 直填值优先级最高：字符串化下发，executor 渲染直取
    assert params["_direct_values"] == {"f": "直填值"}


def test_decision_empty_direct_value_renders_blank(patched_env):
    """用户直填空串 = 明确清空：_direct_values 原样带空串下发（executor 侧渲染
    为空串、不回填默认值），且该字段不进检索/LLM（进 skip 清单）。"""
    calls, svc = patched_env["calls"], patched_env["svc"]

    ph = [{"key": "a", "name": "甲", "fill_mode": "llm", "default_value": "旧甲"}]
    _stage_one_candidate(placeholders=ph)
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    async def fake_confirm(chosen, query, begin_fields, *, incremental_overrides=None):
        return ({"t1": {"changed": set(), "values": {"a": ""}}}, {})

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    params = svc.inserted[0][1]["params"]
    assert params["_direct_values"] == {"a": ""}
    assert params["_retrieve_skip_keys"] == ["a"]


# ---------- 多范本选择确认（智能折中）：_confirm_template_selection ----------

def _chosen_two():
    """AI 选出两个范本（顺序 t2→t1），占位符数不同供 slot_count 断言。"""
    return [
        {"template_id": "t2", "name": "报告B", "description": "B 描述",
         "_placeholders": [_ver_slot("a"), _ver_slot("b")]},
        {"template_id": "t1", "name": "报告A", "description": "",
         "_placeholders": [_ver_slot("a")]},
    ]


def _select_component(monkeypatch, canvas_task_id="task-9"):
    canvas = FakeCanvas()
    canvas.task_id = canvas_task_id
    return _make_component(TemplateFillParam(), canvas=canvas)


def test_select_wait_timeout_falls_back_to_ai(monkeypatch):
    """Redis 一直无选择键 → 超时推 select_timeout 并按 AI 选择原样继续；
    select_pending 事件必须下发运行级 select_nonce + 候选清单 + ai_selected。"""
    cpn = _select_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError("超时路径不得删除任何键")

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    chosen = _chosen_two()
    out = asyncio.run(cpn._confirm_template_selection("task-9", chosen))
    assert out is chosen, "超时必须原样返回 AI 选择（同一列表对象）"
    events = _drain_events(cpn)
    assert [e["stage"] for e in events] == ["select_pending", "select_timeout"]
    pending = events[0]
    assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", pending["select_nonce"] or ""), \
        "select_pending 必须下发合法的运行级 select_nonce"
    assert pending["task_id"] == "task-9"
    assert pending["ai_selected"] == ["t2", "t1"]
    assert [c["template_id"] for c in pending["select_candidates"]] == ["t2", "t1"]
    assert pending["select_candidates"][0]["slot_count"] == 2
    assert pending["select_candidates"][1]["description"] == ""


def test_select_wait_pushes_heartbeat(monkeypatch):
    """选择等待期必须周期性推送 heartbeat 事件（保活 SSE / 维持前端增量落库）：
    长等待多轮轮询中按 _CONFIRM_HEARTBEAT_INTERVAL 节流，事件带 task_id 且
    不含 template_id（前端 reducer 按无 template_id 忽略）。"""
    cpn = _select_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 10.0)
    monkeypatch.setattr(fill_template, "_CONFIRM_HEARTBEAT_INTERVAL", 3.0)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError("超时路径不得删除任何键")

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    chosen = _chosen_two()
    out = asyncio.run(cpn._confirm_template_selection("task-9", chosen))
    assert out is chosen
    events = _drain_events(cpn)
    beats = [e for e in events if e["stage"] == "heartbeat"]
    # waited 每轮 +1.5，10/1.5=7 轮；hb≥3.0 的轮次 = 第 2/4/6 轮 → 3 次心跳
    assert len(beats) == 3, f"期望 3 次心跳，实际 {len(beats)}：{[e['stage'] for e in events]}"
    for b in beats:
        assert b["task_id"] == "task-9"
        assert "template_id" not in b, "heartbeat 不得带 template_id（前端按无 template_id 忽略）"
    # 心跳穿插在 select_pending 与 select_timeout 之间，两端事件不受影响
    assert events[0]["stage"] == "select_pending"
    assert events[-1]["stage"] == "select_timeout"


def test_confirm_wait_pushes_heartbeat(monkeypatch):
    """字段确认等待期同样必须推送 heartbeat 事件（与选择等待同语义）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 10.0)
    monkeypatch.setattr(fill_template, "_CONFIRM_HEARTBEAT_INTERVAL", 3.0)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError("超时路径不得删除任何键")

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_predict(*a, **kw):
        return set()

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    events = _drain_events(cpn)
    beats = [e for e in events if e["stage"] == "heartbeat"]
    assert len(beats) == 3, f"期望 3 次心跳，实际 {len(beats)}：{[e['stage'] for e in events]}"
    for b in beats:
        assert b["task_id"] == "task-9"
        assert "template_id" not in b
    assert events[0]["stage"] == "confirm_pending"
    assert events[-1]["stage"] == "confirm_timeout"


def test_select_payload_filters_unknown_and_returns_subset(monkeypatch):
    """用户提交含 ghost id → 只留合法子集（保 AI 选择顺序）；轮询/消费键必须
    带运行级 nonce（tpl_fill:select:{task_id}:{nonce}）且与事件下发的一致；
    拿到合法提交后不得再推 select_timeout。"""
    cpn = _select_component(monkeypatch)
    payload = json.dumps({"template_ids": ["ghost", "t1", "t1"]})
    seen = {"deleted": []}

    class FakeRedis:
        def get(self, k):
            seen["get"] = k
            return payload

        def delete(self, k):
            seen["deleted"].append(k)

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    chosen = _chosen_two()
    out = asyncio.run(cpn._confirm_template_selection("task-9", chosen))
    assert [c["template_id"] for c in out] == ["t1"], "ghost 过滤 + 去重保序"
    select_key = seen["get"]
    assert select_key.startswith("tpl_fill:select:task-9:"), \
        f"选择键必须带运行级 nonce，实际: {select_key}"
    assert seen["deleted"] == [select_key], "选择键必须消费防重复触发"
    nonce = select_key.rsplit(":", 1)[1]
    events = _drain_events(cpn)
    pending = [e for e in events if e["stage"] == "select_pending"]
    assert pending and pending[0]["select_nonce"] == nonce, \
        "事件下发的 select_nonce 必须与轮询键中的 nonce 一致（前端携带它调 select-confirm 端点）"
    assert [e["stage"] for e in events] == ["select_pending"], \
        "拿到合法提交后不得再推 select_timeout"


@pytest.mark.parametrize("payload", [
    '{"template_ids": []}',
    '{"template_ids": ["ghost"]}',
    '{"template_ids": "t1"}',
    '"just-a-string"',
    "not-json",
])
def test_select_payload_garbage_falls_back_to_ai(monkeypatch, payload):
    """对抗性：空提交/全非法 id/脏结构/坏 JSON → 不炸 run，兜底按 AI 选择继续
    （推 select_timeout，返回原 chosen）。"""
    cpn = _select_component(monkeypatch)

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    chosen = _chosen_two()
    out = asyncio.run(cpn._confirm_template_selection("task-9", chosen))
    assert out is chosen
    stages = [e["stage"] for e in _drain_events(cpn)]
    assert stages == ["select_pending", "select_timeout"]


def test_select_cancel_during_wait_raises(monkeypatch):
    """等待期间画布被停止 → _FillCancelled（invoke 统一收口为 cancelled 终态）。"""
    cpn = _select_component(monkeypatch)

    class CancelCanvas(FakeCanvas):
        def is_canceled(self):
            return True

    cpn._canvas = CancelCanvas()
    cpn._canvas.task_id = "task-9"

    def boom(*a, **kw):
        raise AssertionError("取消路径不得触 Redis")

    monkeypatch.setattr(fill_template, "REDIS_CONN", boom)
    with pytest.raises(fill_template._FillCancelled):
        asyncio.run(cpn._confirm_template_selection("task-9", _chosen_two()))
    assert _drain_events(cpn)[0]["stage"] == "select_pending", \
        "取消前 select_pending 已下发（前端卡片可静默过期）"


def test_invoke_async_multi_selection_confirm_replaces(patched_env, monkeypatch):
    """端到端（智能折中主路径）：LLM 选 2 个 + 画布带 task_id →
    事件序列 selected→select_pending→selected(子集)→filling→…→done；
    用户勾选子集后只为子集建任务行/产成稿。"""
    calls, svc = patched_env["calls"], patched_env["svc"]
    _stage_two_candidates()

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t2", "t1"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    _seq_done(svc, n=1)
    payload = json.dumps({"template_ids": ["t2"]})

    class FakeRedis:
        def get(self, k):
            return payload

        def delete(self, k):
            pass

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())
    canvas = FakeCanvas()
    canvas.task_id = "run-1"
    cpn = _make_component(TemplateFillParam(), canvas=canvas)
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    evs = _drain_events(cpn)
    stages = [e["stage"] for e in evs]
    assert stages == ["selected", "select_pending", "selected",
                      "filling", "filled", "done"], f"实际: {stages}"
    # 二次 selected 是勾选后的子集（整体替换语义，前端行卡片同步收敛）
    assert [t["template_id"] for t in evs[0]["templates"]] == ["t2", "t1"]
    assert [t["template_id"] for t in evs[2]["templates"]] == ["t2"]
    # 只为勾选子集建任务行 + spawn，落选的 t1 不建行
    assert [kw["template_id"] for _, kw in svc.inserted] == ["t2"]
    assert calls["spawn"] == [svc.inserted[0][0]]


def test_invoke_async_multi_selection_no_task_id_skips_pause(patched_env, monkeypatch):
    """画布无 task_id（SSE 回传不可用）→ 多选也不询问，直接按 AI 选择填
    （不推 select_pending、不触 Redis）；单选路径即使有 task_id 也不询问。"""
    svc = patched_env["svc"]
    _stage_two_candidates()

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t2", "t1"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    _seq_done(svc, n=2)

    def boom(*a, **kw):
        raise AssertionError("无 task_id / 单选不应触 Redis")

    monkeypatch.setattr(fill_template, "REDIS_CONN", boom)
    cpn = _make_component(TemplateFillParam())  # FakeCanvas 无 task_id
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    stages = [e["stage"] for e in _drain_events(cpn)]
    assert "select_pending" not in stages
    assert len(svc.inserted) == 2

    # 单选：LLM 只选出 1 个 + 画布带 task_id → 不询问
    _stage_two_candidates()
    _seq_done(svc, n=1)
    svc.inserted.clear()
    svc.id_map.clear()
    svc.queries.clear()
    svc._next = 0

    class SingleMdl:
        async def async_chat(self, system, msgs):
            return '{"template_id": "t2"}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: SingleMdl())
    canvas = FakeCanvas()
    canvas.task_id = "run-2"
    cpn2 = _make_component(TemplateFillParam(), canvas=canvas)
    cpn2._param.dataset_ids = ["kb1"]
    asyncio.run(cpn2._invoke_async())
    stages2 = [e["stage"] for e in _drain_events(cpn2)]
    assert "select_pending" not in stages2
    assert len(svc.inserted) == 1


# ========== 增量填写（同范本有 done → 走增量路径）==========

def _stage_candidate_with_baseline(template_id="t1", placeholders=None,
                                     baseline_render=None, baseline_version="v1"):
    """布置场景：单范本 + latest_done 返回含 baseline render 的历史任务行（版本匹配）。"""
    placeholders = placeholders or [_ver_slot("title"), _ver_slot("body")]
    FakeService.rows = [{"id": template_id, "name": "道路报告", "description": "d",
                         "file_type": "docx"}]
    ver = _ver(placeholders)
    FakeService.vers = {template_id: ver}
    # 让 baseline 版本 = ver.id（默认 v1）
    FakeService.vers[template_id] = _ver(placeholders, render_file_id="render_obj")
    return _Row("hist-task-1", status="done", result_file_id="hist-render",
                values={"render": baseline_render or {}})


def _patch_extract_patch_llm(monkeypatch, payload: dict):
    """monkeypatch extract_patch_values 用 LLM 的 stub：返回指定 payload。"""
    captured = {}

    class _Stub:
        async def async_chat(self, system, msgs):
            captured["system"] = system
            captured["msgs"] = msgs
            return json.dumps(payload, ensure_ascii=False)

    # 直接替 extract_patch_values 的 LLM 调用（节点也走 _build_chat_mdl）
    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: _Stub())
    return captured


def test_incremental_existing_done_baseline_applied(patched_env, monkeypatch):
    """同范本有 done baseline → 走增量：确认卡只列 patch 项，任务下发带
    _baseline_values（executor 端兜回缺失字段）。"""
    svc = patched_env["svc"]
    base_render = {"title": "李港", "body": "原正文", "extra": "原附注"}
    svc.latest_done_map[("t1", CTX)] = _Row("hist-task", status="done",
                                      result_file_id="hist-render",
                                      values={"render": base_render},
                                      template_version_id="v1")
    _stage_one_candidate(placeholders=[
        _ver_slot("title"), _ver_slot("body"), _ver_slot("extra")])
    _patch_extract_patch_llm(monkeypatch, {
        "intent": "patch",
        "direct": {"title": "李港"},
        "changed": ["title"],
    })
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    # 任务下发：params 应包含 _baseline_values（executor 端 split_canvas_params 拆出 baseline_values）
    tid, kw = svc.inserted[0]
    assert "_baseline_values" in kw["params"]
    assert kw["params"]["_baseline_values"] == base_render


def test_incremental_baseline_version_mismatch_skips(patched_env, monkeypatch):
    """latest_done 的版本与当前最新版本不一致 → 跳过 baseline，走全量填充（baseline 不能跨版本混用）。"""
    svc = patched_env["svc"]
    # baseline 模板版本是 v1，当前模板版本改 v2：节点比对失败，跳过
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done", result_file_id="hr",
                                     values={"render": {"title": "OLD_VERSION"}},
                                     template_version_id="v1")
    _stage_one_candidate()
    # 当前 ver 的 id 默认是 _ver 的 "v1"（参 _ver fixture）；改为 v2 触发版本不一致
    FakeService.vers["t1"].id = "v2"
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    # 没 baseline → 不下发非空 _baseline_values（key 存在但为空 dict = 无基线）
    tid, kw = svc.inserted[0]
    assert not kw["params"].get("_baseline_values")


def test_noop_intent_uses_baseline_light_dup(patched_env, monkeypatch):
    """intent=noop → 不出确认卡（保留 baseline 兜回）；任务仍 spawn 但决策空。"""
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done",
                                     values={"render": {"title": "上次", "body": "上次正文"}},
                                     template_version_id="v1")
    _stage_one_candidate(placeholders=[_ver_slot("title"), _ver_slot("body")])
    _patch_extract_patch_llm(monkeypatch, {"intent": "noop"})
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    tid, kw = svc.inserted[0]
    # baseline 仍下传（executor 端兜回 missing）
    assert kw["params"].get("_baseline_values") == {
        "title": "上次", "body": "上次正文"}
    # _changed_keys 空（_llm_fill_items 返回 []）
    assert kw["params"]["_changed_keys"] == []


def test_refill_intent_drops_baseline(patched_env, monkeypatch):
    """intent=refill → baselines.pop + 不下发 baseline_values，走全量填写。"""
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done",
                                     values={"render": {"title": "上次"}},
                                     template_version_id="v1")
    _stage_one_candidate()
    _patch_extract_patch_llm(monkeypatch, {"intent": "refill"})
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    tid, kw = svc.inserted[0]
    assert not kw["params"].get("_baseline_values")
    # 全量范本：_changed_keys = 所有 LLM 占位符 key
    assert set(kw["params"]["_changed_keys"]) == {"项目名称"}


def test_no_latest_done_falls_back_to_full_fill(patched_env):
    """无 done baseline → 走现状全量填充（latest_done_map 空）。"""
    svc = patched_env["svc"]
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    tid, kw = svc.inserted[0]
    assert not kw["params"].get("_baseline_values")
    assert kw["params"]["_changed_keys"] == ["项目名称"]


def test_baseline_from_other_context_not_inherited(patched_env, monkeypatch):
    """【demo03 事故回归门】历史成稿只存在于**别的**上下文 → 本轮不得继承。

    事故现场：demo02 里手改出的值落在该行 values.render，demo03（全新流程）
    用 latest_done 宽查把它当基线 → 该字段被塞进 _retrieve_skip_keys（既不检索
    也不给 LLM）→ 新流程产出的是上一个流程的手改值。

    断言三件事缺一不可：①无 _baseline_values；②该字段回到 LLM 白名单
    （_changed_keys），即没有进 skip；③任务行落的是**本轮**上下文。
    """
    svc = patched_env["svc"]
    # 只有「别的会话」有历史成稿，当前上下文（CTX）没有
    svc.latest_done_map[("t1", "other-session")] = _Row(
        "hist-other", status="done", result_file_id="hr",
        values={"render": {"项目名称": "上个流程的手改值"}},
        template_version_id="v1")
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    tid, kw = svc.inserted[0]
    assert not kw["params"].get("_baseline_values"), "跨上下文的历史成稿不得成为基线"
    assert kw["params"]["_changed_keys"] == ["项目名称"], "该字段必须回到 LLM 白名单"
    assert "项目名称" not in (kw["params"].get("_retrieve_skip_keys") or []), \
        "被基线命中才进 skip；跨上下文未命中即不得进"
    assert kw["flow_instance_id"] == CTX, "任务行必须落在本轮上下文"


def test_baseline_skipped_when_no_work_context(patched_env, monkeypatch):
    """旁路调用（SDK/自建画布）取不到 sys.session_id → 空上下文一律不做增量。

    安全默认：宁可全量重填，也不跨一个无法证明的边界继承。即便宽查
    （latest_done）能查到历史成稿，也不得使用。
    """
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", "")] = _Row("hist", status="done",
                                     values={"render": {"项目名称": "旧值"}},
                                     template_version_id="v1")
    _stage_one_candidate()
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(session_id=""))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    tid, kw = svc.inserted[0]
    assert not kw["params"].get("_baseline_values")
    assert kw["params"]["_changed_keys"] == ["项目名称"]
    assert kw["flow_instance_id"] == ""
    assert svc.ctx_calls == [("t1", "")], "仍应以空上下文去问服务层（由服务层拒）"


def test_baseline_context_id_is_stripped_and_passed(patched_env, monkeypatch):
    """上下文 id 必须原样（去空白后）传到服务层，并落进任务行的 flow_instance_id。"""
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", "sess-9")] = _Row(
        "hist", status="done", result_file_id="hr",
        values={"render": {"项目名称": "同上下文旧值"}},
        template_version_id="v1")
    _stage_one_candidate()
    _patch_extract_patch_llm(monkeypatch, {"intent": "patch", "direct": {}, "changed": []})
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(session_id="  sess-9  "))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    assert svc.ctx_calls == [("t1", "sess-9")], "两侧空白必须剥掉，否则匹配不上"
    tid, kw = svc.inserted[0]
    assert kw["flow_instance_id"] == "sess-9"
    assert kw["params"].get("_baseline_values") == {"项目名称": "同上下文旧值"}


def test_work_context_id_reads_session_and_degrades_to_blank():
    """_work_context_id 对抗性单测：正常取值 / 去空白 / 各类残缺画布一律空串不抛。"""
    def _ctx(canvas):
        cpn = _make_component(TemplateFillParam(), canvas=canvas)
        return cpn._work_context_id()

    assert _ctx(FakeCanvas()) == CTX
    assert _ctx(FakeCanvas(session_id="  sess-2  ")) == "sess-2"
    # globals 缺 sys.session_id
    c = FakeCanvas()
    del c.globals["sys.session_id"]
    assert _ctx(c) == ""
    # sys.session_id 为 None / 空串
    c = FakeCanvas()
    c.globals["sys.session_id"] = None
    assert _ctx(c) == ""
    # globals 整体为空字典
    c = FakeCanvas()
    c.globals = {}
    assert _ctx(c) == ""
    # 无 globals 属性（SimpleNamespace 无该字段 → AttributeError 被兜底）
    assert _ctx(SimpleNamespace()) == ""
    # globals 为 None（.get 抛 AttributeError 被兜底）
    c = FakeCanvas()
    c.globals = None
    assert _ctx(c) == ""
    # 非字符串类型（int）应被 str() 化而非抛
    c = FakeCanvas()
    c.globals["sys.session_id"] = 12345
    assert _ctx(c) == "12345"


def test_incremental_summary_text_branches(patched_env, monkeypatch):
    """汇总文案分支：增量 patch / noop / 全量三种文案互不相同。"""
    svc = patched_env["svc"]

    # Case A：增量 patch
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done",
                                     values={"render": {"title": "李港"}},
                                     template_version_id="v1")
    _stage_one_candidate(placeholders=[_ver_slot("title"), _ver_slot("body")])
    _patch_extract_patch_llm(monkeypatch, {
        "intent": "patch",
        "direct": {"title": "李港"},
        "changed": ["title"],
    })
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    content = cpn._param.outputs["content"]["value"]
    assert "本次增量更新 1 个字段" in content
    assert "其余沿用上次填写值" in content

    # 重置
    svc.latest_done_map.clear()
    FakeService.rows.clear()
    FakeService.vers.clear()
    svc.inserted.clear()
    svc.id_map.clear()
    svc.queries.clear()
    svc.sequences = {}
    svc._next = 0
    fill_template.executor._last_snapshot_ts.clear()


def test_incremental_cands_carry_direct_value(patched_env, monkeypatch):
    """增量模式 cands 携带 direct_value（LLM 从用户原话抽出的 direct），供前端
    确认卡输入框预填：用户提交时若没改，values_raw 等于 direct_value → 后端
    ov 分支非空 user input 覆盖 fallback 路径，值与 fallback 一致；同时让用户
    看到/编辑 AI 抽取结果。无 direct 的字段（如 LLM 未抽出）必须为空串。"""
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done",
                                     values={"render": {"title": "原标题",
                                                        "body": "原正文"}},
                                     template_version_id="v1")
    _stage_one_candidate(placeholders=[_ver_slot("title"), _ver_slot("body")])
    # LLM 抽：title 有 direct 值；body 只入 changed 没 direct 值（兜底走 baseline）
    _patch_extract_patch_llm(monkeypatch, {
        "intent": "patch",
        "direct": {"title": "李港"},
        "changed": ["title", "body"],
    })
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    # 拦截 _confirm_changed_fields，捕获 incremental_overrides 入参
    captured = {}

    async def fake_confirm(chosen, query, begin_fields, *, incremental_overrides=None):
        captured["overrides"] = incremental_overrides
        # 返回空 decision → 模拟「用户点确认并继续填写」走默认 patch 路径
        return ({tid: {"changed": set(), "values": {}} for c in chosen for tid in [c["template_id"]]}, {})

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    ov = captured["overrides"]
    assert "t1" in ov, "增量模式必须传 incremental_overrides[tid]"
    cands = ov["t1"]["candidates"]
    by_key = {c["key"]: c for c in cands}
    # title 有 direct → 写入 direct_value（"李港"）；default_value 是基线原值（"原标题"）
    assert by_key["title"]["direct_value"] == "李港"
    assert by_key["title"]["default_value"] == "原标题"
    # body 无 direct → direct_value 空串（前端不预填）
    assert by_key["body"]["direct_value"] == ""
    assert by_key["body"]["default_value"] == "原正文"


def test_incremental_cands_direct_value_skips_non_llm_slots(patched_env, monkeypatch):
    """非 llm 填写点（param 模式）不进 cands（不参与确认），自然也不带 direct_value；
    patch_keys 里的 llm 占位符才进入 candidates。本测试覆盖：direct 含非 llm
    key 时也不出现在 candidates 里（cands 只取 by_key 交集）。"""
    svc = patched_env["svc"]
    svc.latest_done_map[("t1", CTX)] = _Row("hist", status="done",
                                     values={"render": {"title": "原"}},
                                     template_version_id="v1")
    # 一个 llm 字段（title）+ 一个 param 字段（phone 填模式 param）
    phs = [_ver_slot("title"),
           {"key": "phone", "name": "电话", "fill_mode": "param", "addr": "B2"}]
    _stage_one_candidate(placeholders=phs)
    # LLM 抽到 direct={"title": "李港", "phone": "12345"}（不应被采用）
    _patch_extract_patch_llm(monkeypatch, {
        "intent": "patch",
        "direct": {"title": "李港", "phone": "12345"},
        "changed": ["title", "phone"],
    })
    _seq_done(svc)
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    captured = {}

    async def fake_confirm(chosen, query, begin_fields, *, incremental_overrides=None):
        captured["overrides"] = incremental_overrides
        return ({tid: {"changed": set(), "values": {}} for c in chosen for tid in [c["template_id"]]}, {})

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    cands = captured["overrides"]["t1"]["candidates"]
    keys = [c["key"] for c in cands]
    assert "title" in keys
    assert "phone" not in keys, "非 llm 字段不进 candidates（与 _llm_fill_items 范围对齐）"
    by_key = {c["key"]: c for c in cands}
    assert by_key["title"]["direct_value"] == "李港"
