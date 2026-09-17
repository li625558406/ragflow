"""FillTemplate 工具（agent/tools/template_fill.py）单测。

service 层 / spawn_fill_task / time.sleep 全部 monkeypatch，不触 DB/LLM/MinIO；
工具实例用 object.__new__ 绕过 ToolBase.__init__（其要求真实 Canvas 实例），
_param 用 SimpleNamespace 打桩。
"""

import json
from types import SimpleNamespace

import pytest


def _stub_spawn(monkeypatch, spawned):
    """monkeypatch spawn 模块的 spawn_fill_task（工具直连的延迟 import 目标）。

    spawn 模块自身零重依赖（仅 logging/threading），可直接 import；
    记录被 spawn 的 task_id 供断言，monkeypatch 收尾自动还原。
    """
    from rag.svr.template_fill import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "spawn_fill_task", lambda task_id: spawned.append(task_id))


def _svc():
    from api.db.services import template_fill_service as tpl_svc

    return tpl_svc


@pytest.fixture(autouse=True)
def _no_db_by_default(monkeypatch):
    """单测不连 DB：detail 的「按当前值反查 key」（2026-09-17）会读该范本最近一次
    done 任务的产值。默认桩成「没有已填成稿」→ 值匹配降级为空，既有按名称/key/锚
    文本匹配的用例行为不变；需要值匹配的用例在测试体内再 monkeypatch 覆盖
    （pytest 的 monkeypatch 是函数级单实例，autouse 先跑、测试体内后设者胜）。"""
    monkeypatch.setattr(
        _svc().TplFillTaskService, "latest_done",
        staticmethod(lambda template_id, tenant_id: None))


def _make_tool(canvas_tenant="tenant_x"):
    """构造绕过 ComponentBase.__init__ 的 FillTemplate 实例，打桩 _param/canvas/取消检查。"""
    from agent.tools.template_fill import FillTemplate

    tool = object.__new__(FillTemplate)
    tool._param = SimpleNamespace(outputs={}, inputs={}, debug_inputs={})
    tool.set_output = lambda key, value=None: tool._param.outputs.update({key: {"value": value}})
    tool.check_if_canceled = lambda msg="": False
    if canvas_tenant:
        tool._canvas = SimpleNamespace(get_tenant_id=lambda: canvas_tenant)
    else:
        tool._canvas = None
    return tool


def _pub_template():
    return SimpleNamespace(id="t1", status="published", file_type="docx")


# ---------- meta 声明 ----------


def test_meta_declaration():
    from agent.tools.template_fill import FillTemplate, FillTemplateParam

    param = FillTemplateParam()
    assert param.meta["name"] == "FillTemplate"
    assert "action" in param.meta["parameters"]
    assert FillTemplate.component_name == "FillTemplate"


# ---------- list_templates ----------


def test_list_templates_lists_published(monkeypatch):
    tpl_svc = _svc()
    rows = [{"id": "t1", "name": "投标申请书", "file_type": "docx"}, {"id": "t2", "name": "验收报告", "file_type": "xlsx"}]
    seen = {}

    def fake_list(tenant_id, **kw):
        seen.update(tenant_id=tenant_id, **kw)
        return rows, 2

    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(fake_list))
    out = _make_tool()._invoke(action="list_templates")
    assert "投标申请书" in out and "t1" in out and "xlsx" in out
    assert seen["tenant_id"] == "tenant_x"
    assert seen["status"] == "published"


def test_list_templates_empty_hint(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(lambda tenant_id, **kw: ([], 0)))
    out = _make_tool()._invoke(action="list_templates")
    assert "当前没有已发布的范本" in out


# ---------- detail（范本查询/填写点匹配） ----------


def _tpl_row(t_id="t1", name="投标申请书", status="published", file_type="docx"):
    return {"id": t_id, "name": name, "status": status, "file_type": file_type, "description": "用于投标申请", "latest_version": 1}


class _TplObj:
    """模拟 get_owned 返回的 peewee 模型实例（有 to_dict）。"""

    def __init__(self, row):
        self._row = row

    def to_dict(self):
        return dict(self._row)


def _patch_versions(monkeypatch, tpl_svc, placeholders):
    monkeypatch.setattr(tpl_svc.TplTemplateVersionService, "latest", staticmethod(lambda template_id: SimpleNamespace(id="ver1", placeholders=placeholders)))


def test_detail_requires_id_or_name():
    out = _make_tool()._invoke(action="detail")
    assert "template_id" in out and "template_name" in out


def test_detail_no_tenant(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    out = _make_tool(canvas_tenant=None)._invoke(action="detail", template_id="t1")
    assert "无法确定当前用户身份" in out


def test_detail_by_id_not_owned(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: None))
    out = _make_tool()._invoke(action="detail", template_id="ghost")
    assert "范本不存在或无权访问" in out


def test_detail_by_id_lists_fill_points(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(
        monkeypatch,
        tpl_svc,
        [
            {"key": "proj_name", "name": "项目名称", "anchor": "项目名称：", "default_value": ""},
            {"key": "bidder", "name": "投标人", "anchor": "投标人：", "default_value": "某某公司"},
        ],
    )
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "投标申请书" in out and "已发布" in out and "共 2 个填写点" in out
    assert "项目名称" in out and "proj_name" in out and "某某公司" in out
    assert "keyword" in out  # 无 keyword 时引导二次查询


def test_detail_by_name_zero_hit(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(lambda tenant_id, **kw: ([], 0)))
    out = _make_tool()._invoke(action="detail", template_name="不存在的范本")
    assert "没有找到" in out and "不存在的范本" in out


def test_detail_by_name_multi_hits_lists_candidates(monkeypatch):
    tpl_svc = _svc()
    rows = [_tpl_row("t1", name="投标申请书"), _tpl_row("t2", name="投标函")]
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(lambda tenant_id, **kw: (rows, 2)))
    out = _make_tool()._invoke(action="detail", template_name="投标")
    assert "2 个" in out and "t1" in out and "t2" in out and "请指定 id" in out


def test_detail_by_name_exact_match_wins(monkeypatch):
    """模糊命中多条但名称精确相等时，直接取精确命中不追问。"""
    tpl_svc = _svc()
    rows = [_tpl_row("t1", name="投标申请书"), _tpl_row("t2", name="投标申请书（2026版）")]
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(lambda tenant_id, **kw: (rows, 2)))
    _patch_versions(monkeypatch, tpl_svc, [{"key": "k", "name": "项目名称", "anchor": "项目名称："}])
    out = _make_tool()._invoke(action="detail", template_name="投标申请书")
    assert "共 1 个填写点" in out and "id=t1" in out


def test_detail_keyword_hit_filters(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(
        monkeypatch,
        tpl_svc,
        [
            {"key": "proj_name", "name": "项目名称", "anchor": "项目名称："},
            {"key": "bidder", "name": "投标人", "anchor": "投标人（盖章）："},
            {"key": "seal_date", "name": "盖章日期", "anchor": "日期："},
        ],
    )
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="盖章")
    assert "匹配的填写点有 2 个" in out
    assert "投标人" in out and "盖章日期" in out
    assert "项目名称" not in out  # 未命中项不展示


def test_detail_keyword_case_insensitive_key_match(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(monkeypatch, tpl_svc, [{"key": "project_name", "name": "项目名称", "anchor": "名称："}])
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="PROJECT_NAME")
    assert "匹配的填写点有 1 个" in out


def test_detail_keyword_miss_explicit(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(monkeypatch, tpl_svc, [{"key": "proj", "name": "项目名称", "anchor": "项目名称："}])
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="履约保证金")
    assert "没有与「履约保证金」匹配的填写点" in out
    assert "共 1 个填写点" in out  # 总数仍在，LLM 可据此回答近似项


def test_detail_no_fill_points(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    monkeypatch.setattr(tpl_svc.TplTemplateVersionService, "latest", staticmethod(lambda template_id: None))
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "尚未配置填写点" in out


def test_detail_dirty_placeholders_json_survives(monkeypatch):
    """历史脏数据：placeholders 落成了非法 JSON 字符串 → 兜底为空清单不崩。"""
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(monkeypatch, tpl_svc, "{not valid json")
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "尚未配置填写点" in out


def test_detail_scalar_placeholders_survives(monkeypatch):
    """对抗性：合法 JSON 标量（int/bool）反序列化成功但不可迭代 → 同样兜底为空清单不抛 TypeError。"""
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(monkeypatch, tpl_svc, 5)
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "尚未配置填写点" in out
    out2 = _make_tool()._invoke(action="detail", template_id="t1")
    assert "尚未配置填写点" in out2


def test_detail_object_placeholders_survives(monkeypatch):
    """对抗性：JSON object（非数组）→ 不误报成业务状态层面的异常，走空清单降级。"""
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(monkeypatch, tpl_svc, {"key": "proj", "name": "项目名称"})
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "尚未配置填写点" in out


def test_detail_many_points_truncated(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    many = [{"key": f"k{i}", "name": f"字段{i}", "anchor": "锚"} for i in range(50)]
    _patch_versions(monkeypatch, tpl_svc, many)
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "其余 20 个填写点略" in out


def test_detail_giant_anchor_and_non_dict_rows_survive(monkeypatch):
    """对抗性：超长锚文本被截断、占位符清单混入非 dict 脏行不崩。"""
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_versions(
        monkeypatch,
        tpl_svc,
        [
            "not-a-dict",
            {"key": "proj", "name": "项目名称", "anchor": "长" * 500},
            None,
        ],
    )
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert "共 1 个填写点" in out
    assert "长" * 60 in out and "长" * 61 not in out  # 截到 60 字符


# ---------- 未知 action ----------


def test_unknown_action_rejected():
    out = _make_tool()._invoke(action="bogus")
    assert "不支持" in out
    out2 = _make_tool()._invoke()
    assert "不支持" in out2


# ---------- fill ----------


def _patch_fillable(monkeypatch, tpl_svc):
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _pub_template()))
    monkeypatch.setattr(
        tpl_svc.TplTemplateVersionService, "latest", staticmethod(lambda template_id: SimpleNamespace(id="ver1", render_file_id="v1_render.docx", placeholders=[{"key": "proj", "name": "项目名"}]))
    )


def test_fill_requires_kb_ids(monkeypatch):
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)
    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids="")
    assert "kb_ids" in out and "知识库" in out


def test_fill_rejects_unpublished(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: SimpleNamespace(id="t1", status="draft")))
    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids='["kb1"]')
    assert "发布" in out


def test_fill_success_polls_to_done(monkeypatch):
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)

    inserted = {}

    def fake_insert(**kw):
        inserted.update(kw)
        return kw.get("id")

    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(fake_insert))

    spawned = []
    _stub_spawn(monkeypatch, spawned)

    polls = {"n": 0}

    def fake_get_owned(task_id, tenant_id):
        polls["n"] += 1
        if polls["n"] == 1:
            return SimpleNamespace(id=task_id, status="pending")
        return SimpleNamespace(id=task_id, status="done", values={"render": {"proj": "漳州项目"}, "cells": {"proj": "ok"}}, error="")

    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(fake_get_owned))
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids='["kb1", "kb2"]', params='{"a": 1}')

    assert inserted["template_version_id"] == "ver1"
    assert inserted["source"] == "chat"
    assert inserted["tenant_id"] == "tenant_x"
    assert inserted["created_by"] == "tenant_x"
    assert inserted["kb_ids"] == ["kb1", "kb2"]
    assert inserted["params"] == {"a": 1}
    assert inserted["status"] == "pending"
    assert spawned == [inserted["id"]]
    assert polls["n"] >= 2
    assert "填写完成" in out and "漳州项目" in out and inserted["id"] in out


def test_fill_kb_ids_tolerant_parsing(monkeypatch):
    """kb_ids 逗号分隔字符串也能解析（LLM 输出容错）。"""
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)

    inserted = {}
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(lambda **kw: inserted.update(kw) or kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id=task_id, status="failed", error="x")))
    _stub_spawn(monkeypatch, [])
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids="kb1, kb2")
    assert inserted["kb_ids"] == ["kb1", "kb2"]
    assert "填写失败" in out  # 轮询到 failed 终态即返回摘要


def test_fill_poll_timeout_prompts_status_query(monkeypatch):
    """轮询超时：提示已提交 + 引导 action=status 查询。"""
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(lambda **kw: kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id=task_id, status="generating")))
    _stub_spawn(monkeypatch, [])
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids='["kb1"]')
    assert "已提交" in out and "task_id=" in out and "status" in out


def test_fill_bad_params_rejected(monkeypatch):
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)
    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids='["kb1"]', params="{bad json")
    assert "params" in out


# ---------- status ----------


def test_status_requires_task_id():
    out = _make_tool()._invoke(action="status")
    assert "task_id" in out


def test_status_task_not_owned(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: None))
    out = _make_tool()._invoke(action="status", task_id="nope")
    assert "任务不存在" in out


def test_status_running(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id="task1", status="generating")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写进行中" in out


def test_status_failed(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id="task1", status="failed", error="LLM 生成失败: timeout")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写失败" in out and "LLM 生成失败" in out


def test_status_done_summary(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(
        tpl_svc.TplFillTaskService,
        "get_owned",
        staticmethod(
            lambda task_id, tenant_id: SimpleNamespace(
                id="task1", status="done", values={"render": {"proj": "漳州重要的项目" * 20, "date": "2026-09-07"}, "cells": {"proj": "ok", "date": "ok"}}, error=""
            )
        ),
    )
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写完成" in out
    assert "proj" in out and "date" in out and "2026-09-07" in out
    assert "漳州重要的项目" in out  # 截断前仍含开头内容
    assert "task_id=task1" in out
    assert "范本库" in out  # 下载指引


def test_status_partial_lists_manual_fields(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(
        tpl_svc.TplFillTaskService,
        "get_owned",
        staticmethod(
            lambda task_id, tenant_id: SimpleNamespace(
                id="task1", status="partial", values={"render": {"proj": "P1", "signer": "{{signer:待人工}}"}, "cells": {"proj": "ok", "signer": "manual", "seal": "not_found"}}, error=""
            )
        ),
    )
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "部分完成" in out
    assert "待人工补充字段" in out and "signer" in out and "seal" in out


# ---------- fill 按 template_name 模糊解析 ----------


def _fill_tpl_row(tid, name, status="published"):
    return {"id": tid, "name": name, "status": status, "file_type": "docx"}


def _patch_name_rows(monkeypatch, tpl_svc, rows):
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(lambda tenant_id, keyword="", page=1, size=50, **kw: (rows, len(rows))))


def test_fill_requires_id_or_name(monkeypatch):
    """template_id/template_name 均缺省时明确提示二者之一（不再只逼 id）。"""
    out = _make_tool()._invoke(action="fill", kb_ids='["kb1"]')
    assert "template_id" in out and "template_name" in out


def test_fill_by_name_unique_published_proceeds(monkeypatch):
    """事故同构：部分名称命中 1 已发布 + 1 已停用 → 自动选已发布发起（不反问）。"""
    tpl_svc = _svc()
    _patch_name_rows(
        monkeypatch,
        tpl_svc,
        [
            _fill_tpl_row("t2", "XX范本-机电监理（停用）", status="disabled"),
            _fill_tpl_row("t1", "XX范本-机电施工"),
        ],
    )
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _pub_template()))
    monkeypatch.setattr(tpl_svc.TplTemplateVersionService, "latest", staticmethod(lambda template_id: SimpleNamespace(id="ver1", render_file_id="v1_render.docx", placeholders=[])))
    inserted = {}
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(lambda **kw: inserted.update(kw) or kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id=task_id, status="failed", error="kb empty")))
    spawned = []
    _stub_spawn(monkeypatch, spawned)
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_name="机电施工", kb_ids='["kb1"]')
    assert inserted["template_id"] == "t1" and spawned == [inserted["id"]]
    assert "填写失败" in out


def test_fill_by_name_zero_hit(monkeypatch):
    tpl_svc = _svc()
    _patch_name_rows(monkeypatch, tpl_svc, [])
    out = _make_tool()._invoke(action="fill", template_name="不存在的范本", kb_ids='["kb1"]')
    assert "没有找到" in out and "不存在的范本" in out


def test_fill_by_name_all_unpublished_rejected(monkeypatch):
    """命中但全部草稿/停用 → 拒绝发起并说明。"""
    tpl_svc = _svc()
    _patch_name_rows(
        monkeypatch,
        tpl_svc,
        [
            _fill_tpl_row("t1", "范本A", status="draft"),
            _fill_tpl_row("t2", "范本B", status="disabled"),
        ],
    )
    out = _make_tool()._invoke(action="fill", template_name="范本", kb_ids='["kb1"]')
    assert "均未发布" in out


def test_fill_by_name_multi_published_asks(monkeypatch):
    """多个已发布命中 → 列候选让用户挑，不发起任务。"""
    tpl_svc = _svc()
    _patch_name_rows(
        monkeypatch,
        tpl_svc,
        [
            _fill_tpl_row("t1", "范本A"),
            _fill_tpl_row("t2", "范本B"),
        ],
    )
    inserted = {}
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(lambda **kw: inserted.update(kw) or kw.get("id")))
    out = _make_tool()._invoke(action="fill", template_name="范本", kb_ids='["kb1"]')
    assert "已发布范本有 2 个" in out and "t1" in out and "t2" in out
    assert not inserted


def test_fill_by_name_exact_wins_over_fuzzy(monkeypatch):
    """名称精确相等的候选优先于模糊命中（两个都发布时不再误选）。"""
    tpl_svc = _svc()
    _patch_name_rows(
        monkeypatch,
        tpl_svc,
        [
            _fill_tpl_row("t_fuzzy", "招标范本扩展版"),
            _fill_tpl_row("t_exact", "招标范本"),
        ],
    )
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned", staticmethod(lambda template_id, tenant_id, for_update=False: _pub_template()))
    monkeypatch.setattr(tpl_svc.TplTemplateVersionService, "latest", staticmethod(lambda template_id: SimpleNamespace(id="ver1", render_file_id="v1_render.docx", placeholders=[])))
    inserted = {}
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert", staticmethod(lambda **kw: inserted.update(kw) or kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(lambda task_id, tenant_id: SimpleNamespace(id=task_id, status="failed", error="x")))
    _stub_spawn(monkeypatch, [])
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    _make_tool()._invoke(action="fill", template_name="招标范本", kb_ids='["kb1"]')
    assert inserted["template_id"] == "t_exact"


# ---------- modify（就地修改成稿） ----------


def _patch_modify_env(monkeypatch, tpl_svc, *, task_values=None, placeholders=None,
                      render_raises=False, pinned_task=None):
    """modify 全链路打桩：latest_done 任务 / 版本 / storage / renderer / 回写。

    sediment 桩只为断言**未被调用**——modify 不再自动沉淀默认值（改由用户在成稿行
    点「写回范本库」触发）。

    pinned_task：传入时打桩 get_owned 返回它，且 latest_done 计数器可用于断言
    「带 task_id 的 modify 不得回落到最近一份」（桩内不能抛异常——_invoke 顶层
    try/except 会把异常吞成返回串，断言会假通过）。"""
    from rag.svr.template_fill import renderer as renderer_mod

    calls = {"put": [], "patched": None, "sediment": None, "rendered": None,
             "latest_calls": 0}

    def fake_latest(template_id, tenant_id):
        calls["latest_calls"] += 1
        return SimpleNamespace(
            id="task1", template_id=template_id, status="done",
            tenant_id="tenant_x", create_time=1758000000000,
            values=task_values, result_file_id="v1_result_task1.docx")

    monkeypatch.setattr(tpl_svc.TplFillTaskService, "latest_done", staticmethod(fake_latest))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: pinned_task))
    monkeypatch.setattr(
        tpl_svc.TplTemplateVersionService, "latest",
        staticmethod(lambda template_id: SimpleNamespace(
            id="ver1", version=1, render_file_id="v1_render.docx",
            placeholders=placeholders if placeholders is not None else [{"key": "approval_authority", "name": "审批权限", "anchor": "审批权限："}])))
    monkeypatch.setattr(
        tpl_svc.TplTemplateService, "get_or_none",
        staticmethod(lambda id=None: SimpleNamespace(file_type="docx")))
    monkeypatch.setattr(tpl_svc, "_storage_get", lambda bucket, name: b"render-copy-blob")
    monkeypatch.setattr(
        tpl_svc, "_storage_put",
        lambda bucket, name, blob: calls["put"].append((bucket, name, blob)))

    def fake_render(file_type, blob, values, addr_by_key=None):
        if render_raises:
            raise ValueError("boom")
        calls["rendered"] = dict(values)
        return b"new-docx"

    monkeypatch.setattr(renderer_mod, "render", fake_render)
    monkeypatch.setattr(
        tpl_svc.TplFillTaskService, "patch_values",
        staticmethod(lambda task_id, values: calls.__setitem__("patched", (task_id, values)) or True))
    monkeypatch.setattr(
        tpl_svc.TplTemplateVersionService, "sediment_defaults",
        staticmethod(lambda *a, **kw:
                     calls.__setitem__("sediment", (a, kw)) or True))
    return calls


def test_modify_requires_template_or_name(monkeypatch):
    out = _make_tool()._invoke(action="modify", params='{"k": "v"}')
    assert "template_id" in out and "template_name" in out


def test_modify_requires_params(monkeypatch):
    out = _make_tool()._invoke(action="modify", template_id="t1", params="")
    assert "params" in out
    out2 = _make_tool()._invoke(action="modify", template_id="t1", params="{bad json")
    assert "params" in out2


def test_modify_no_done_task(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "latest_done",
                        staticmethod(lambda template_id, tenant_id: None))
    out = _make_tool()._invoke(action="modify", template_id="t1", params='{"k": "v"}')
    assert "已完成" in out and "fill" in out


def test_modify_unknown_key_rejected_without_touching_doc(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc)
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"nope_key": "李港"}')
    assert "不在该范本填写点" in out and "nope_key" in out
    assert calls["put"] == [] and calls["patched"] is None


def test_modify_success_merges_and_overwrites(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(
        monkeypatch, tpl_svc,
        task_values={"cells": {"approval_authority": "not_found", "other": "filled"},
                     "render": {"approval_authority": "", "other": "已批准"}},
        placeholders=[{"key": "approval_authority", "name": "审批权限", "anchor": "审批权限："},
                      {"key": "other", "name": "其他", "anchor": "其他："}])
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    assert "就地修改" in out and "李港" in out and "task1" in out
    # 覆盖写原成稿对象名（卡片链接不变）
    assert calls["put"][0][1] == "v1_result_task1.docx"
    # 重渲染用合并后的完整产值（原值 + patch）
    assert calls["rendered"] == {"approval_authority": "李港", "other": "已批准"}
    # values 回写：patch 置 filled，其他字段保留
    tid, values = calls["patched"]
    assert tid == "task1"
    assert values["render"] == {"approval_authority": "李港", "other": "已批准"}
    assert values["cells"]["approval_authority"] == "filled"
    assert values["cells"]["other"] == "filled"
    # 反回归：modify 不再自动沉淀默认值（改由用户点「写回范本库」按钮触发）
    assert calls["sediment"] is None, "modify 不得自动沉淀默认值"


def test_modify_empty_value_marks_not_found(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(
        monkeypatch, tpl_svc,
        task_values={"cells": {"approval_authority": "filled"}, "render": {"approval_authority": "旧值"}})
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": ""}')
    assert "就地修改" in out
    assert calls["patched"][1]["cells"]["approval_authority"] == "not_found"
    assert calls["patched"][1]["render"]["approval_authority"] == ""


def test_modify_render_fail_leaves_original(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc, render_raises=True)
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    assert "渲染异常" in out and "未改动" in out
    assert calls["put"] == [] and calls["patched"] is None


def test_modify_by_name_unique_resolves(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              task_values={"cells": {}, "render": {}})
    seen = {}

    def fake_list(tenant_id, keyword="", **kw):
        seen["kw"] = keyword
        return ([{"id": "t1", "name": "机电施工招标范本", "file_type": "docx", "status": "published"}], 1)

    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page", staticmethod(fake_list))
    out = _make_tool()._invoke(action="modify", template_name="机电施工",
                               params='{"approval_authority": "李港"}')
    assert seen["kw"] == "机电施工"
    assert "就地修改" in out
    assert calls["put"][0][0] == "t1"


def test_modify_rebridges_download_copy(monkeypatch):
    """改完必须同名覆盖 {tenant}-downloads/tplfill-{task_id}。

    卡片「查看填写内容」/下载走的都是桥接副本；只更新 {template_id} bucket 会让
    卡片继续拿到改前的旧字节（对象名确定、REST 层桥接有进程内记忆化不会自动重拷），
    表现为「模型说改了、预览文件还是旧文案」。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              task_values={"cells": {}, "render": {}})
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    assert len(calls["put"]) == 2, calls["put"]
    assert calls["put"][0] == ("t1", "v1_result_task1.docx", b"new-docx")
    # 桥接 bucket 必须取自任务所属租户（不是画布租户/请求租户），对象名 tplfill-{task_id}
    assert calls["put"][1] == ("tenant_x-downloads", "tplfill-task1", b"new-docx")
    # 成功路径不应出现降级提示（否则用户会以为下载副本没同步）
    assert "下载副本同步失败" not in out


def test_modify_bridge_failure_degrades_not_silent(monkeypatch):
    """桥接失败不得伪装成完全成功：主成稿仍写、values 仍回写，但回执必须点明
    下载副本可能还是旧内容——否则又是一次「说改了但文件没变」。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              task_values={"cells": {}, "render": {}})
    puts = calls["put"]

    def put_or_raise(bucket, name, blob):
        if str(bucket).endswith("-downloads"):
            raise RuntimeError("downloads bucket down")
        puts.append((bucket, name, blob))

    monkeypatch.setattr(tpl_svc, "_storage_put", put_or_raise)
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    # 主成稿照写、values 照回写：桥接是派生副本，失败不能中断主流程
    assert puts == [("t1", "v1_result_task1.docx", b"new-docx")]
    assert calls["patched"] is not None
    assert "就地修改" in out and "下载副本同步失败" in out


def _pinned_task(tid="task1", template_id="t1", status="done", values=None):
    return SimpleNamespace(id=tid, template_id=template_id, status=status,
                           tenant_id="tenant_x", create_time=1758000000000,
                           values=values if values is not None else {"cells": {}, "render": {}},
                           result_file_id="v1_result_task1.docx")


def test_modify_task_id_pins_target_and_skips_latest(monkeypatch):
    """显式 task_id 优先：不得回落到「最近一份」，且回执自证改的是哪一份。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc, pinned_task=_pinned_task())
    out = _make_tool()._invoke(action="modify", template_id="t1", task_id="task1",
                               params='{"approval_authority": "李港"}')
    assert calls["latest_calls"] == 0, "带 task_id 时不得回落到 latest_done"
    assert "task_id=task1" in out and "该成稿生成于" in out
    assert calls["put"][1][1] == "tplfill-task1"


def test_modify_task_id_not_owned_rejected(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc)  # get_owned 恒返回 None
    out = _make_tool()._invoke(action="modify", template_id="t1", task_id="someone_else",
                               params='{"approval_authority": "李港"}')
    assert "不存在或无权访问" in out
    assert calls["put"] == [] and calls["patched"] is None


def test_modify_task_id_of_other_template_rejected(monkeypatch):
    """task_id 必须落在同一范本内，否则会改到别的范本的成稿上。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              pinned_task=_pinned_task(template_id="t_other"))
    out = _make_tool()._invoke(action="modify", template_id="t1", task_id="task1",
                               params='{"approval_authority": "李港"}')
    assert "不属于该范本" in out
    assert calls["put"] == [] and calls["patched"] is None


def test_modify_task_id_not_done_rejected(monkeypatch):
    """非 done（无可用成稿）拒绝，且错误串回显真实 status 便于用户判断。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              pinned_task=_pinned_task(status="partial"))
    out = _make_tool()._invoke(action="modify", template_id="t1", task_id="task1",
                               params='{"approval_authority": "李港"}')
    assert "尚未完成" in out and "partial" in out
    assert calls["put"] == [] and calls["patched"] is None


def test_modify_blank_task_id_falls_back_to_latest(monkeypatch):
    """空白 task_id（LLM 常传 ""/空格）等价于未指定：走 latest_done，不能误判成越权。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              task_values={"cells": {}, "render": {}})
    out = _make_tool()._invoke(action="modify", template_id="t1", task_id="   ",
                               params='{"approval_authority": "李港"}')
    assert calls["latest_calls"] == 1
    assert "就地修改" in out


def test_modify_values_dirty_json_string_survives(monkeypatch):
    """历史脏数据（values 存成 JSON 字符串）不能炸 modify：解析后照常合并。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(
        monkeypatch, tpl_svc,
        task_values=json.dumps({"cells": {"approval_authority": "filled"},
                                "render": {"approval_authority": "旧值"}}))
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    assert "就地修改" in out
    assert calls["rendered"] == {"approval_authority": "李港"}


def test_modify_values_non_dict_render_does_not_crash(monkeypatch):
    """values.render 为标量/None 等脏形态：dict(...) 前必须兜底，不能抛 TypeError。"""
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc,
                              task_values={"cells": "oops", "render": None})
    out = _make_tool()._invoke(action="modify", template_id="t1",
                               params='{"approval_authority": "李港"}')
    assert "就地修改" in out
    assert calls["rendered"] == {"approval_authority": "李港"}


# ---------- detail：按当前成稿值反查 key（2026-09-17 实测缺口） ----------


def _patch_current(monkeypatch, tpl_svc, render=None, task_id=None, task=None):
    """打桩 detail 的当前值来源。

    render 非 None → latest_done 返回带该 render 的任务；
    task_id 非 None → 同时打桩 get_owned 返回值（测显式指定通道）。"""
    seen = {"latest": 0, "owned": []}
    stub = task if task is not None else SimpleNamespace(
        id="task1", values={"cells": {}, "render": render})
    monkeypatch.setattr(
        tpl_svc.TplFillTaskService, "latest_done",
        staticmethod(lambda template_id, tenant_id: (seen.__setitem__("latest", seen["latest"] + 1)
                                                    or stub)))
    monkeypatch.setattr(
        tpl_svc.TplFillTaskService, "get_owned",
        staticmethod(lambda tid, tenant_id: seen["owned"].append(tid) or stub))
    return seen


_PH_TENDERER = [
    {"key": "tenderer_name", "name": "招标人名称", "anchor": "招标人名称：", },
    {"key": "tenderer_name_2", "name": "招标人名称", "anchor": "招标人名称：", },
    {"key": "project_owner", "name": "项目业主", "anchor": "项目业主："},
]

_LONG_COMPANY = "石狮市交通建设投资有限责任公司"


def test_detail_keyword_matches_current_value(monkeypatch):
    """用户贴的是「值」不是字段名：按当前成稿值反查出真正持有该值的 key。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, _PH_TENDERER)
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc, render={"project_owner": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword=_LONG_COMPANY)
    assert "project_owner" in out
    assert "当前值" in out and _LONG_COMPANY in out
    # 值匹配命中的只有 project_owner：两个同名 tenderer_name 不得被捎带列出
    assert "tenderer_name" not in out
    assert "当前值」对应的那个 key" in out


def test_detail_keyword_value_match_multi_keys_collision(monkeypatch):
    """同一段文字真在多处（用户的「4 处同名」场景）：全部列出，让模型自己判断改哪几处。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, _PH_TENDERER)
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc,
                   render={"tenderer_name": _LONG_COMPANY,
                           "tenderer_name_2": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword=_LONG_COMPANY)
    assert "tenderer_name" in out and "tenderer_name_2" in out
    assert out.count(_LONG_COMPANY) >= 2


def test_detail_keyword_value_match_merges_with_name_match(monkeypatch):
    """名称命中 ∪ 值命中：两类通道取并集，不是互相替代。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [
        {"key": "zh_city", "name": "石狮市项目名称", "anchor": "项目名称："},
        {"key": "project_owner", "name": "项目业主", "anchor": "项目业主："},
    ])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc, render={"project_owner": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="石狮市")
    # zh_city 按中文名称命中，project_owner 按当前值命中 → 并集 2 个
    assert "匹配的填写点有 2 个" in out
    assert "zh_city" in out and "project_owner" in out


def test_detail_keyword_value_match_case_insensitive(monkeypatch):
    """值匹配大小写不敏感（英文值如公司英文名/编号）。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [{"key": "bank", "name": "开户行", "anchor": "开户行："}])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc, render={"bank": "Bank of China"})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="bank of china")
    assert "bank" in out and "当前值" in out


def test_detail_keyword_value_empty_never_matches(monkeypatch):
    """空串/None 值不得被任意关键词命中（否则未填字段会污染每一次查询）。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [{"key": "a", "name": "甲", "anchor": "甲："},
                                           {"key": "b", "name": "乙", "anchor": "乙："}])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc,
                   render={"a": "", "b": None, "c": "   "})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="石狮市")
    assert "没有与" in out
    assert "当前值" not in out


def test_detail_keyword_miss_mentions_four_channels(monkeypatch):
    """未命中提示必须说明查了四处（否则模型会以为该字段不存在而放弃）。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, _PH_TENDERER)
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    _patch_current(monkeypatch, tpl_svc, render={"project_owner": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="不存在的文本")
    assert "中文名称、英文 key、锚文本、当前成稿值" in out


def test_detail_without_keyword_skips_current_lookup(monkeypatch):
    """无 keyword 不查当前值：少一次 DB 查询（detail 全量清单是高频调用）。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, _PH_TENDERER)
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    seen = _patch_current(monkeypatch, tpl_svc, render={"project_owner": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1")
    assert seen["latest"] == 0
    assert "共 3 个填写点" in out
    assert "当前值" not in out


def test_detail_keyword_current_lookup_failure_degrades(monkeypatch):
    """当前值查询炸了不能拖垮 detail：仍返回按名称/key/锚文本匹配的清单。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, _PH_TENDERER)
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))

    def boom(template_id, tenant_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(tpl_svc.TplFillTaskService, "latest_done", staticmethod(boom))
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword="招标人名称")
    assert "范本填写执行失败" not in out
    assert "招标人名称" in out


def test_detail_keyword_task_id_pins_current_values(monkeypatch):
    """task_id 显式指定：当前值取该任务，而不是范本最近一份。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [{"key": "project_owner", "name": "项目业主", "anchor": "项目业主："}])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    seen = _patch_current(monkeypatch, tpl_svc, render={"project_owner": _LONG_COMPANY})
    out = _make_tool()._invoke(action="detail", template_id="t1",
                               keyword=_LONG_COMPANY, task_id="task9")
    assert seen["owned"] == ["task9"]
    assert seen["latest"] == 0
    assert "project_owner" in out


def test_detail_current_values_dirty_payloads_survive(monkeypatch):
    """values 的四种脏形态（JSON 串 / 非 dict / render 非 dict / None）都不得抛错。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [{"key": "project_owner", "name": "项目业主", "anchor": "项目业主："}])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    dirty = [
        SimpleNamespace(id="t", values=json.dumps({"render": {"project_owner": _LONG_COMPANY}})),
        SimpleNamespace(id="t", values="not json{"),
        SimpleNamespace(id="t", values=["list"]),
        SimpleNamespace(id="t", values={"render": "scalar"}),
        SimpleNamespace(id="t", values=None),
        SimpleNamespace(id="t"),
    ]
    for stub in dirty:
        monkeypatch.setattr(tpl_svc.TplFillTaskService, "latest_done",
                            staticmethod(lambda a, b, _s=stub: _s))
        out = _make_tool()._invoke(action="detail", template_id="t1", keyword=_LONG_COMPANY)
        assert "范本填写执行失败" not in out, (stub, out)
    # JSON 串那一份是有效值：反查应命中
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "latest_done",
                        staticmethod(lambda a, b: dirty[0]))
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword=_LONG_COMPANY)
    assert "project_owner" in out


def test_detail_current_value_giant_value_truncated(monkeypatch):
    """超长当前值必须截断（防一条脏值刷爆上下文）。"""
    tpl_svc = _svc()
    _patch_versions(monkeypatch, tpl_svc, [{"key": "project_owner", "name": "项目业主", "anchor": "项目业主："}])
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _TplObj(_tpl_row())))
    giant = "狮" * 5000
    _patch_current(monkeypatch, tpl_svc, render={"project_owner": giant})
    out = _make_tool()._invoke(action="detail", template_id="t1", keyword=giant)
    assert "范本填写执行失败" not in out
    assert len(out) < 3000


def test_modify_by_name_multi_asks(monkeypatch):
    tpl_svc = _svc()
    calls = _patch_modify_env(monkeypatch, tpl_svc)
    rows = [{"id": "t1", "name": "范本A", "file_type": "docx", "status": "published"},
            {"id": "t2", "name": "范本B", "file_type": "docx", "status": "published"}]
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page",
                        staticmethod(lambda tenant_id, keyword="", **kw: (rows, 2)))
    out = _make_tool()._invoke(action="modify", template_name="范本",
                               params='{"approval_authority": "李港"}')
    assert "请指定 id" in out
    assert calls["put"] == []
