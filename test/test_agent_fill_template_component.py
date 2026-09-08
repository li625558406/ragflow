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
- parse_selection：非法 JSON、id 不在候选内（选错范本必须报错而非静默换第一个）
- _invoke_async：无已发布范本、未选知识库、选型 LLM 输出非法、工作副本缺失、
  xlsx addr 透传、下载输出契约（下游 Message _extract_downloads 依赖）
所有外部依赖（Service / settings.STORAGE_IMPL / executor 函数 / renderer.render）
经模块属性注入替身，不触真实 DB / LLM / MinIO。"""
import json
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
    def __init__(self, tenant="t1", sys_query="写一份道路工程情况报告"):
        self._tenant = tenant
        self._sys_query = sys_query

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

def test_parse_selection_valid():
    assert parse_selection('{"template_id": "t2"}', ["t1", "t2"]) == "t2"


def test_parse_selection_invalid_json():
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection("我觉得都不合适", ["t1"])


def test_parse_selection_unknown_id():
    # 选型 LLM 编造不存在的范本 id → 必须报错，不允许静默降级第一个
    with pytest.raises(ValueError, match="未能从已发布范本中选出"):
        parse_selection('{"template_id": "ghost"}', ["t1"])


def test_parse_selection_null_value():
    with pytest.raises(ValueError):
        parse_selection('{"template_id": null}', ["t1"])


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

    async def fake_retrieve_all(tenant_id, placeholders, kb_ids, params, task_id=""):
        calls["kb_ids"] = kb_ids
        return {it["key"]: {"chunks": [{"content": "证据", "doc_id": "d", "doc_name": "n",
                                        "similarity": 0.9}], "query": it["key"]}
                for it in placeholders if it.get("key")}, {}

    async def fake_generate_values(tenant_id, placeholders, chunks_by_key, params,
                                   batch_size=10):
        calls["gen_keys"] = [it["key"] for it in placeholders]
        return {it["key"]: f"值_{it['key']}" for it in placeholders}, set()

    monkeypatch.setattr(fill_template.executor, "_retrieve_all", fake_retrieve_all)
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
    # 下载输出契约：下游 Message._extract_downloads 靠这四个字段渲染下载按钮
    dl = json.loads(cpn.output("download"))
    assert set(dl) == {"doc_id", "filename", "mime_type", "size"}
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
    """query 为空回退 {sys.query}：检索 query 应来自 sys.query 值（经 retrieve 桩透传验证）。"""
    _stage_one_candidate()
    seen = {}

    async def fake_retrieve_all(tenant_id, placeholders, kb_ids, params, task_id=""):
        seen["task_id"] = task_id
        return {}, {}

    patched_env  # fixture 已挂 generate_values 桩（全空产值 → 留空路径）
    import asyncio
    cpn = _make_component(TemplateFillParam(), canvas=FakeCanvas(sys_query="我的需求"))
    cpn._param.dataset_ids = ["kb1"]
    orig = fill_template.executor._retrieve_all
    fill_template.executor._retrieve_all = fake_retrieve_all
    try:
        asyncio.run(cpn._invoke_async())
    finally:
        fill_template.executor._retrieve_all = orig
    assert seen["task_id"] == "canvas:node1"
