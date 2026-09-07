"""FillTemplate 工具（agent/tools/template_fill.py）单测。

service 层 / _spawn_fill_task / time.sleep 全部 monkeypatch，不触 DB/LLM/MinIO；
工具实例用 object.__new__ 绕过 ToolBase.__init__（其要求真实 Canvas 实例），
_param 用 SimpleNamespace 打桩。
"""
import sys
import types
from types import SimpleNamespace


def _stub_template_api(monkeypatch, spawned):
    """sys.modules 预注册 api.apps.restful_apis.template_api 桩。

    真实 import 会触发 api/apps/__init__.py 的 settings.init_settings()（需本机
    Redis/ES），照 test_template_api_routes.py 的桩模式；工具侧延迟 import 语义
    不变，仅替换 import 解析结果。monkeypatch 收尾自动还原 sys.modules。
    """
    apps = types.ModuleType("api.apps")
    apps.__path__ = []
    restful = types.ModuleType("api.apps.restful_apis")
    restful.__path__ = []
    tpl = types.ModuleType("api.apps.restful_apis.template_api")
    tpl._spawn_fill_task = lambda task_id: spawned.append(task_id)
    restful.template_api = tpl
    monkeypatch.setitem(sys.modules, "api.apps", apps)
    monkeypatch.setitem(sys.modules, "api.apps.restful_apis", restful)
    monkeypatch.setitem(sys.modules, "api.apps.restful_apis.template_api", tpl)


def _svc():
    from api.db.services import template_fill_service as tpl_svc
    return tpl_svc


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
    rows = [{"id": "t1", "name": "投标申请书", "file_type": "docx"},
            {"id": "t2", "name": "验收报告", "file_type": "xlsx"}]
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
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_list_page",
                        staticmethod(lambda tenant_id, **kw: ([], 0)))
    out = _make_tool()._invoke(action="list_templates")
    assert "当前没有已发布的范本" in out


# ---------- 未知 action ----------

def test_unknown_action_rejected():
    out = _make_tool()._invoke(action="bogus")
    assert "不支持" in out
    out2 = _make_tool()._invoke()
    assert "不支持" in out2


# ---------- fill ----------

def _patch_fillable(monkeypatch, tpl_svc):
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False: _pub_template()))
    monkeypatch.setattr(tpl_svc.TplTemplateVersionService, "latest",
                        staticmethod(lambda template_id: SimpleNamespace(
                            id="ver1", render_file_id="v1_render.docx",
                            placeholders=[{"key": "proj", "name": "项目名"}])))


def test_fill_requires_kb_ids(monkeypatch):
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)
    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids="")
    assert "kb_ids" in out and "知识库" in out


def test_fill_rejects_unpublished(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplTemplateService, "get_owned",
                        staticmethod(lambda template_id, tenant_id, for_update=False:
                                     SimpleNamespace(id="t1", status="draft")))
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
    _stub_template_api(monkeypatch, spawned)

    polls = {"n": 0}

    def fake_get_owned(task_id, tenant_id):
        polls["n"] += 1
        if polls["n"] == 1:
            return SimpleNamespace(id=task_id, status="pending")
        return SimpleNamespace(id=task_id, status="done",
                               values={"render": {"proj": "漳州项目"}, "cells": {"proj": "ok"}},
                               error="")

    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned", staticmethod(fake_get_owned))
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_id="t1",
                               kb_ids='["kb1", "kb2"]', params='{"a": 1}')

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
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert",
                        staticmethod(lambda **kw: inserted.update(kw) or kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id=task_id, status="failed", error="x")))
    _stub_template_api(monkeypatch, [])
    monkeypatch.setattr("agent.tools.template_fill.time.sleep", lambda s: None)

    out = _make_tool()._invoke(action="fill", template_id="t1", kb_ids="kb1, kb2")
    assert inserted["kb_ids"] == ["kb1", "kb2"]
    assert "填写失败" in out  # 轮询到 failed 终态即返回摘要


def test_fill_poll_timeout_prompts_status_query(monkeypatch):
    """轮询超时：提示已提交 + 引导 action=status 查询。"""
    tpl_svc = _svc()
    _patch_fillable(monkeypatch, tpl_svc)
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "insert",
                        staticmethod(lambda **kw: kw.get("id")))
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id=task_id, status="generating")))
    _stub_template_api(monkeypatch, [])
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
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: None))
    out = _make_tool()._invoke(action="status", task_id="nope")
    assert "任务不存在" in out


def test_status_running(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id="task1", status="generating")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写进行中" in out


def test_status_failed(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id="task1", status="failed", error="LLM 生成失败: timeout")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写失败" in out and "LLM 生成失败" in out


def test_status_done_summary(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id="task1", status="done",
                            values={"render": {"proj": "漳州重要的项目" * 20, "date": "2026-09-07"},
                                    "cells": {"proj": "ok", "date": "ok"}},
                            error="")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "填写完成" in out
    assert "proj" in out and "date" in out and "2026-09-07" in out
    assert "漳州重要的项目" in out  # 截断前仍含开头内容
    assert "task_id=task1" in out
    assert "范本库" in out  # 下载指引


def test_status_partial_lists_manual_fields(monkeypatch):
    tpl_svc = _svc()
    monkeypatch.setattr(tpl_svc.TplFillTaskService, "get_owned",
                        staticmethod(lambda task_id, tenant_id: SimpleNamespace(
                            id="task1", status="partial",
                            values={"render": {"proj": "P1", "signer": "{{signer:待人工}}"},
                                    "cells": {"proj": "ok", "signer": "manual", "seal": "not_found"}},
                            error="")))
    out = _make_tool()._invoke(action="status", task_id="task1")
    assert "部分完成" in out
    assert "待人工补充字段" in out and "signer" in out and "seal" in out
