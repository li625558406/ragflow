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
"""「范本填写」画布节点单测。对抗性覆盖：
- build_candidates：最新版本缺失 / 填写点为空 / key 缺失的脏数据
- parse_selection：非法 JSON、id 不在候选内、空列表、脏类型（选错范本必须报错
  而非静默换第一个；多选去重保序，兼容旧版单选契约）
- _invoke_async：无已发布范本、未选知识库、选型 LLM 输出非法、工作副本缺失、
  多范本各产一份成稿、Begin 表单字段 param 直取、上传文件证据注入、
  产物落 {tenant_id}-downloads bucket、下载输出列表契约（下游 Message 依赖）
所有外部依赖（Service / settings.STORAGE_IMPL / executor 函数 / renderer.render）
经模块属性注入替身，不触真实 DB / LLM / MinIO。"""
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


class FakeCanvas:
    def __init__(self, tenant="t1", sys_query="写一份道路工程情况报告",
                 begin_fields=None, file_content=""):
        self._tenant = tenant
        self._sys_query = sys_query
        self.globals = {"sys.query": sys_query, "sys.file_content": file_content}
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
    return SimpleNamespace(placeholders=placeholders, render_file_id=render_file_id, version=1)


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


# ---------- _invoke_async 集成（全部依赖打桩） ----------

@pytest.fixture()
def patched_env(monkeypatch):
    """统一打桩：Service / settings / executor / renderer。返回记录器 dict。"""
    calls = {"put": [], "get": [], "render": None, "select_llm": 0, "gen_keys": None}

    class FakeStorage:
        def get(self, bucket, obj):
            calls["get"].append((bucket, obj))
            if obj == "render_obj":
                return b"TPL_BLOB"
            return None

        def put(self, tenant, doc_id, blob):
            calls["put"].append((tenant, doc_id, blob))

    monkeypatch.setattr(fill_template, "TplTemplateService", FakeService)
    monkeypatch.setattr(fill_template, "TplTemplateVersionService", FakeService)
    monkeypatch.setattr(fill_template.settings, "STORAGE_IMPL", FakeStorage())

    async def fake_retrieve_all_shared(tenant_id, placeholders_list, kb_ids, task_id="", should_cancel=None, sem=None):
        calls["kb_ids"] = kb_ids
        calls["shared_task_id"] = task_id
        return [{it["key"]: {"chunks": [{"content": "证据", "doc_id": "d", "doc_name": "n",
                                         "similarity": 0.9}], "query": it["key"]}
                 for it in placeholders if it.get("key")}
                for placeholders in placeholders_list]

    async def fake_generate_values(tenant_id, placeholders, chunks_by_key, params,
                                   batch_size=10, sem=None, on_progress=None, should_cancel=None):
        calls["gen_keys"] = [it["key"] for it in placeholders]
        return {it["key"]: f"值_{it['key']}" for it in placeholders}, set()

    monkeypatch.setattr(fill_template.executor, "retrieve_all_shared", fake_retrieve_all_shared)
    monkeypatch.setattr(fill_template.executor, "generate_values", fake_generate_values)

    def fake_render(file_type, blob, values, addr_by_key=None):
        calls["render"] = (file_type, blob, values, addr_by_key)
        return b"RESULT_BLOB"

    from rag.svr.template_fill import renderer
    monkeypatch.setattr(renderer, "render", fake_render)
    return calls


def _stage_one_candidate(template_id="t1", placeholders=None, file_type="docx"):
    FakeService.rows = [{"id": template_id, "name": "道路报告", "description": "d",
                         "file_type": file_type}]
    FakeService.vers = {template_id: _ver(placeholders or [_ver_slot("项目名称")])}


def test_invoke_async_happy_path_docx(patched_env):
    _stage_one_candidate()
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())

    assert patched_env["render"][0] == "docx"
    assert patched_env["render"][2]["项目名称"] == "值_项目名称"
    assert patched_env["kb_ids"] == ["kb1"]
    # 产物必须落 {tenant_id}-downloads bucket：/agents/download 与
    # /files/{id}/content 两个端点的既有读取契约都是这个 bucket
    assert patched_env["put"][0][0] == "t1-downloads"
    # 下载输出契约：输出为列表（多范本各一份），每项六字段供下游
    # Message._extract_downloads / 前端下载与预览按钮使用（url/name 为
    # /agents/download 直连下载新增）
    dls = json.loads(cpn.output("download"))
    assert isinstance(dls, list) and len(dls) == 1
    dl = dls[0]
    assert set(dl) == {"doc_id", "filename", "mime_type", "size", "url", "name"}
    assert dl["url"].startswith("/api/v1/agents/download?id=")
    assert dl["name"] == "道路报告.docx"
    assert dl["filename"] == "道路报告.docx"
    assert dl["mime_type"].endswith("wordprocessingml.document")
    assert dl["size"] == len(b"RESULT_BLOB")
    assert "AI 填充 1 个" in cpn.output("content")
    assert "留空" in cpn.output("content")


def test_invoke_async_single_candidate_skips_selection_llm(patched_env, monkeypatch):
    _stage_one_candidate()
    async def boom():
        raise AssertionError("唯一候选不应调用选型 LLM")
    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: SimpleNamespace(async_chat=boom))
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())


def test_invoke_async_multi_candidate_llm_selects(patched_env, monkeypatch):
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {t: _ver([_ver_slot("项目名称")]) for t in ("t1", "t2")}

    class FakeMdl:
        async def async_chat(self, system, msgs):
            return '{"template_id": "t2"}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: FakeMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())
    assert patched_env["get"].count(("t2", "render_obj")) == 1


def test_invoke_async_no_candidates(patched_env):
    FakeService.rows = []
    FakeService.vers = {}
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    with pytest.raises(ValueError, match="暂无可用的已发布范本"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_no_kb_selected(patched_env):
    cpn = _make_component(TemplateFillParam())  # dataset_ids 空
    import asyncio
    with pytest.raises(ValueError, match="知识库"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_selection_llm_invalid_output(patched_env, monkeypatch):
    # 两个候选 + 选型 LLM 输出非法 → 节点报错，不静默换第一个
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {t: _ver([_ver_slot("项目名称")]) for t in ("t1", "t2")}

    class BadMdl:
        async def async_chat(self, system, msgs):
            return "输出不了 JSON"

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: BadMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_missing_render_blob(patched_env):
    _stage_one_candidate(placeholders=[_ver_slot("项目名称")])
    FakeService.vers["t1"] = _ver([_ver_slot("项目名称")], render_file_id="missing_obj")
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    with pytest.raises(ValueError, match="工作副本缺失"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_xlsx_addr_passthrough(patched_env):
    _stage_one_candidate(file_type="xlsx",
                         placeholders=[_ver_slot("金额", mode="param", addr="Sheet1!B2")])
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())
    # xlsx 必须带 addr 映射（renderer 坐标直写依赖）；param 模式不进 LLM 产值
    assert patched_env["render"][0] == "xlsx"
    assert patched_env["render"][3] == {"金额": "Sheet1!B2"}
    assert patched_env["gen_keys"] == []


def test_invoke_async_query_fallback_sys_query(patched_env):
    """query 为空回退 {sys.query}：检索 task_id 应来自画布节点（经共享检索桩透传验证）。"""
    _stage_one_candidate()
    seen = {}

    async def fake_retrieve_all_shared(tenant_id, placeholders_list, kb_ids, task_id="", should_cancel=None, sem=None):
        seen["task_id"] = task_id
        return [{it["key"]: {"chunks": [], "query": it["key"]}
                 for it in placeholders if it.get("key")}
                for placeholders in placeholders_list]

    patched_env  # fixture 已挂 generate_values 桩（全空产值 → 留空路径）
    import asyncio
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(sys_query="我的需求"))
    cpn._param.dataset_ids = ["kb1"]
    orig = fill_template.executor.retrieve_all_shared
    fill_template.executor.retrieve_all_shared = fake_retrieve_all_shared
    try:
        asyncio.run(cpn._invoke_async())
    finally:
        fill_template.executor.retrieve_all_shared = orig
    assert seen["task_id"] == "canvas:node1"


def test_invoke_async_multi_template_ids_two_outputs(patched_env, monkeypatch):
    """选型 LLM 返回多个 template_ids → 每个范本各产一份成稿。"""
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {t: _ver([_ver_slot("项目名称")]) for t in ("t1", "t2")}

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t2", "t1"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())

    dls = json.loads(cpn.output("download"))
    assert [d["filename"] for d in dls] == ["报告B.docx", "报告A.docx"]
    assert len(patched_env["put"]) == 2
    assert all(bucket == "t1-downloads" for bucket, _doc, _blob in patched_env["put"])
    assert "已选用 2 份范本" in cpn.output("content")
    assert "报告A" in cpn.output("content") and "报告B" in cpn.output("content")


def test_invoke_async_begin_field_param_direct(patched_env):
    """Begin 表单字段与 param 模式填写点 key 同名 → 不经 LLM 直取字段值。"""
    _stage_one_candidate(placeholders=[_ver_slot("金额", mode="param", addr="Sheet1!B2")])
    cpn = _make_component(TemplateFillParam(),
                          canvas=FakeCanvas(begin_fields={"金额": "1024.5"}))
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())
    assert patched_env["gen_keys"] == []          # param 字段不进 LLM 产值
    assert patched_env["render"][2] == {"金额": "1024.5"}


def test_invoke_async_user_file_evidence_prepended(patched_env, monkeypatch):
    """用户上传文件文本注入：预置片段插到每个 llm 槽证据首位；需求描述进背景。"""
    _stage_one_candidate()
    seen = {"chunks": {}, "background": None}

    async def fake_generate_values(tenant_id, placeholders, chunks_by_key, params,
                                   batch_size=10, sem=None, on_progress=None, should_cancel=None):
        seen["chunks"] = {k: v["chunks"] for k, v in chunks_by_key.items()}
        seen["background"] = params
        return {it["key"]: f"值_{it['key']}" for it in placeholders}, set()

    monkeypatch.setattr(fill_template.executor, "generate_values", fake_generate_values)
    import asyncio
    cpn = _make_component(TemplateFillParam(),
                          canvas=FakeCanvas(file_content="这是上传的可研报告正文"))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())

    first = seen["chunks"]["项目名称"][0]
    assert first["content"].startswith("[用户上传文件]")
    assert "可研报告正文" in first["content"]
    # 需求描述进产值 LLM 背景信息
    assert seen["background"]["用户需求描述"] == "写一份道路工程情况报告"


def test_invoke_async_user_file_absent_no_injection(patched_env):
    """无上传文件（sys.file_content 为空）→ 证据不注入、正常走 KB 桩路径。"""
    _stage_one_candidate()
    import asyncio
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(file_content=""))
    cpn._param.dataset_ids = ["kb1"]
    asyncio.run(cpn._invoke_async())
    assert patched_env["render"][2]["项目名称"] == "值_项目名称"


def test_invoke_async_one_template_failure_degrades_to_summary_line(patched_env, monkeypatch):
    """多范本并行：单个范本填写失败不拖死节点 → 该范本降级为汇总行，其余照常产出。"""
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    # t2 工作副本缺失（render_obj 拿不到 blob）→ 该范本 _fill_one 抛 ValueError
    FakeService.vers = {"t1": _ver([_ver_slot("项目名称")]),
                        "t2": _ver([_ver_slot("项目名称")], render_file_id="missing_obj")}

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t1", "t2"]}'

    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())

    dls = json.loads(cpn.output("download"))
    assert [d["filename"] for d in dls] == ["报告A.docx"]
    assert "填写失败" in cpn.output("content") and "报告B" in cpn.output("content")
    assert "报告A" in cpn.output("content")


def test_invoke_async_all_templates_failed_raises(patched_env, monkeypatch):
    """所有范本均失败 → 节点报错（携带首个原因），不输出空下载列表。"""
    _stage_one_candidate()
    FakeService.vers["t1"] = _ver([_ver_slot("项目名称")], render_file_id="missing_obj")
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    with pytest.raises(ValueError, match="所有范本填写均失败"):
        asyncio.run(cpn._invoke_async())


def test_invoke_async_shared_retrieval_called_once_for_multi_templates(patched_env, monkeypatch):
    """多范本：共享检索只调一次（retrieve_all_shared 桩调用计数），各范本拿到各自证据。"""
    FakeService.rows = [
        {"id": "t1", "name": "报告A", "description": "", "file_type": "docx"},
        {"id": "t2", "name": "报告B", "description": "", "file_type": "docx"},
    ]
    FakeService.vers = {t: _ver([_ver_slot("项目名称")]) for t in ("t1", "t2")}
    calls = {"shared": 0}

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        calls["shared"] += 1
        assert len(placeholders_list) == 2
        return [{it["key"]: {"chunks": [{"content": f"证据_{tid}", "doc_id": "d",
                                         "doc_name": "n", "similarity": 0.9}],
                             "query": it["key"]} for it in ph if it.get("key")}
                for tid, ph in zip(("t1", "t2"), placeholders_list)]

    class MultiMdl:
        async def async_chat(self, system, msgs):
            return '{"template_ids": ["t1", "t2"]}'

    monkeypatch.setattr(fill_template.executor, "retrieve_all_shared", fake_shared)
    monkeypatch.setattr(fill_template.executor, "_build_chat_mdl", lambda *_: MultiMdl())
    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]
    import asyncio
    asyncio.run(cpn._invoke_async())
    assert calls["shared"] == 1
    assert len(patched_env["put"]) == 2


# ---------- P2 暂停确认：_confirm_changed_fields（超时兜底 + 确认载荷过滤） ----------

def _confirm_component(monkeypatch, canvas_task_id="task-9"):
    """确认编排专用组件：画布带 task_id（真实 canvas 构造时注入，缺省回退空串即跳过等待）。"""
    canvas = FakeCanvas()
    canvas.task_id = canvas_task_id
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
    """Redis 一直无确认键 → 超时后按预判集合自动继续，且推送 confirm_pending +
    confirm_timeout 两个事件；无默认值字段（乙）不进 decisions 载荷；
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

    decisions = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    assert decisions == {"t1": {"changed": {"a", "ghost"}, "values": {}}}
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

    decisions = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
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

    decisions = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    assert decisions == {"t1": {"changed": set(), "values": {}}}, \
        "values 标量 / changed 非串兜底为空，不得 AttributeError"


def test_confirm_no_default_items_skips_all(monkeypatch):
    """回归红线：全部选中范本均无默认值字段 → 直接返回 {}，不推事件、不触 Redis、
    不调预判（首填链路与现状完全一致）。"""
    import asyncio

    cpn = _make_component(TemplateFillParam())  # FakeCanvas 无 task_id 也无关紧要

    def boom(*a, **kw):
        raise AssertionError("无默认值字段不应触发预判/Redis")

    monkeypatch.setattr(fill_template, "REDIS_CONN", boom)
    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", boom)
    chosen = [{"template_id": "t1", "name": "报告",
               "_placeholders": [{"key": "k1", "name": "字段", "fill_mode": "llm"}]}]
    assert asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {})) == {}
    assert _drain_events(cpn) == []


# ---------- P2 条件执行：decision 消费侧（检索收窄 + 直填优先 + 沉淀 override） ----------

def test_decision_conditional_execution_skips_unchanged_defaults(patched_env, monkeypatch):
    """P2 核心语义（decision 消费侧）：decisions={"changed": {a}, "values": {f: 直填}}，
    四个 llm 字段中——
    - a（有默认值、预判变化 C∩D）→ 走检索 + LLM；
    - d（无默认值 N）→ 走检索 + LLM；
    - e（有默认值、未变化 D−C）→ 不检索不调 LLM，渲染直取默认值；
    - f（有默认值、未变化但用户直填）→ 不检索不调 LLM，渲染直取直填值，
      沉淀收到 override_keys={f}（直填覆盖沉淀保护）。
    同时验证：_fill_one 收到的 llm_placeholders 与检索清单同口径收窄。"""
    import asyncio

    ph = [
        {"key": "a", "name": "甲", "fill_mode": "llm", "default_value": "旧甲"},
        {"key": "d", "name": "丁", "fill_mode": "llm"},
        {"key": "e", "name": "戊", "fill_mode": "llm", "default_value": "旧戊"},
        {"key": "f", "name": "己", "fill_mode": "llm", "default_value": "旧己"},
    ]
    _stage_one_candidate(placeholders=ph)
    FakeService.vers["t1"] = SimpleNamespace(placeholders=ph, render_file_id="render_obj",
                                             version=1, id="v1")
    seen = {"retrieved": None}
    sed_calls = []

    def fake_sediment(cls, template_id, version_id, values, override_keys=None):
        sed_calls.append({"tid": template_id, "vid": version_id,
                          "values": dict(values), "override": set(override_keys or set())})

    monkeypatch.setattr(FakeService, "sediment_defaults", classmethod(fake_sediment),
                        raising=False)

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        seen["retrieved"] = [[it["key"] for it in ph] for ph in placeholders_list]
        return [{it["key"]: {"chunks": [], "query": it["key"]} for it in ph if it.get("key")}
                for ph in placeholders_list]

    monkeypatch.setattr(fill_template.executor, "retrieve_all_shared", fake_shared)

    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    async def fake_confirm(chosen, query, begin_fields):
        return {"t1": {"changed": {"a"}, "values": {"f": "直填值"}}}

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    # 检索清单只含 a（C∩D）与 d（N）；e（D−C）与 f（直填）被排除
    assert seen["retrieved"] == [["a", "d"]]
    # LLM 产值同口径收窄
    assert patched_env["gen_keys"] == ["a", "d"]
    # 渲染产值：e 直取默认值、f 直取直填值（优先级最高）、a/d 走 LLM 产值
    assert patched_env["render"][2] == {"a": "值_a", "d": "值_d", "e": "旧戊", "f": "直填值"}
    # 沉淀：带 override_keys={f}，且 e 的默认值语义不被本次产值破坏
    assert len(sed_calls) == 1
    assert sed_calls[0]["override"] == {"f"}
    assert sed_calls[0]["values"]["e"] == "旧戊"


def test_decision_empty_direct_value_renders_blank(patched_env, monkeypatch):
    """用户直填空串 = 明确清空：不进 LLM、渲染为空（build_values 空值落空串），
    且不回填默认值（直填先于 _merge_default_values 摘出 missing）。"""
    import asyncio

    ph = [{"key": "a", "name": "甲", "fill_mode": "llm", "default_value": "旧甲"}]
    _stage_one_candidate(placeholders=ph)
    FakeService.vers["t1"] = SimpleNamespace(placeholders=ph, render_file_id="render_obj",
                                             version=1, id="v1")

    async def fake_shared(tenant_id, placeholders_list, kb_ids, task_id="",
                          should_cancel=None, sem=None):
        return [{it["key"]: {"chunks": [], "query": it["key"]} for it in ph if it.get("key")}
                for ph in placeholders_list]

    monkeypatch.setattr(fill_template.executor, "retrieve_all_shared", fake_shared)

    cpn = _make_component(TemplateFillParam())
    cpn._param.dataset_ids = ["kb1"]

    async def fake_confirm(chosen, query, begin_fields):
        return {"t1": {"changed": set(), "values": {"a": ""}}}

    cpn._confirm_changed_fields = fake_confirm
    asyncio.run(cpn._invoke_async())

    # 直填字段不进 LLM（检索清单同样排除，但 gen_keys 已足够断言口径）
    assert patched_env["gen_keys"] == []
    # 渲染为空串（清空语义），而非旧默认值「旧甲」
    assert patched_env["render"][2] == {"a": ""}
