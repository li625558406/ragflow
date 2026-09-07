# test/test_template_api_routes.py
"""template_api 冒烟测试：不依赖 Quart 运行时上下文/DB/MinIO。

说明：直接 `import api.apps.restful_apis.template_api` 会触发 api/apps/__init__.py
的 `settings.init_settings()`，进而需要本机 Redis/ES（非单元测试环境）。故照
test_flow_doc_table_edit.py 的模式：从源文件加载模块并注入最小桩（仅 api.apps，
避免 init_settings；api_utils / template_fill_service / common / detector 均可真实
导入——test_template_fill_utils.py 已验证该 import 链不会立即触库）。
"""

import inspect
import os
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：必须原样返回被装饰函数。

    注意不能写成「返回内部 deco」的形式——@login_required 无括号直接作用于
    端点函数，若返回 deco 会丢失原函数（名字变 deco → blueprint 端点名冲突、
    iscoroutinefunction/getsource 全失效）。
    """
    return f


def _load_template_api():
    _make_stub_module("api.apps", current_user=None, login_required=_noop_decorator)
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "template_api.py"))
    spec = spec_from_file_location("template_api_under_test", path)
    mod = module_from_spec(spec)
    sys.modules["template_api_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_template_api = _load_template_api()

EXPECTED_ENDPOINTS = (
    "upload_template",
    "list_templates",
    "detect_placeholders",
    "save_placeholders",
    "publish_template",
    "disable_template",
    "get_template",
    "preview_template",
    "download_template",
)


def test_template_api_module_loads_and_registers_routes():
    mod = _template_api
    for name in EXPECTED_ENDPOINTS:
        fn = getattr(mod, name, None)
        assert fn is not None, f"缺少端点函数 {name}"
        assert inspect.iscoroutinefunction(fn), f"{name} 必须是 async 函数"


def test_all_routes_registered_on_blueprint():
    """9 个端点必须全部挂到 blueprint 上，且 methods 正确（防止漏装饰器/漏路径）。"""
    from quart import Quart

    app = Quart(__name__)
    app.register_blueprint(_template_api.manager)
    rules = {}
    for r in app.url_map.iter_rules():
        if r.rule == "/static/<path:filename>":
            continue
        rules[r.rule] = r.methods
    expected = {
        "/template/fill/upload": {"POST"},
        "/template/fill/list": {"GET"},
        "/template/fill/detect": {"POST"},
        "/template/fill/<template_id>/save-placeholders": {"POST"},
        "/template/fill/<template_id>/publish": {"POST"},
        "/template/fill/<template_id>/disable": {"POST"},
        "/template/fill/<template_id>": {"GET"},
        "/template/fill/<template_id>/preview": {"GET"},
        "/template/fill/<template_id>/file": {"GET"},
    }
    for rule, methods in expected.items():
        assert rule in rules, f"路由未注册: {rule}（现有: {sorted(rules)}）"
        assert methods <= rules[rule], f"{rule} methods 不符: {rules[rule]}"


def test_file_type_of_boundaries():
    """_file_type_of 纯函数边界：None/空/无扩展名/大写/伪扩展名。"""
    f = _template_api._file_type_of
    assert f(None) is None
    assert f("") is None
    assert f("template") is None
    assert f("模板.docx") == "docx"
    assert f("模板.DOCX") == "docx"  # 大写扩展名按 lower 归一
    assert f("模板.Xlsx") == "xlsx"
    assert f("a.b.docx") == "docx"
    assert f("evil.docx.exe") is None  # 伪扩展名
    assert f("template.docxx") is None  # endswith 前缀误伤
    assert f("template.doc") is None  # 旧版 doc 不支持


def test_extract_candidates_dispatches_by_file_type():
    """_extract_candidates 按 file_type 分发：docx 走段落提取、xlsx 走单元格提取。"""
    import io

    from docx import Document
    from openpyxl import Workbook

    doc = Document()
    doc.add_paragraph("项目名称：____")
    buf = io.BytesIO()
    doc.save(buf)
    cands = _template_api._extract_candidates("docx", buf.getvalue())
    assert any("项目名称" in c["text"] for c in cands)

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "合计：____元"
    xbuf = io.BytesIO()
    wb.save(xbuf)
    cands = _template_api._extract_candidates("xlsx", xbuf.getvalue())
    assert any("合计" in c["text"] for c in cands)


def test_list_endpoint_accepts_garbage_pagination_via_type_int():
    """D 项回归：args.get(..., type=int) 转换失败回默认值——这里直接验证
    werkzeug MultiDict 语义与端点取参写法一致，防有人改回 int(args.get(...))。"""
    from werkzeug.datastructures import MultiDict

    args = MultiDict([("page", "abc"), ("size", "-3")])
    assert args.get("page", 1, type=int) == 1  # 转换失败回默认，不抛异常
    assert args.get("size", 20, type=int) == -3  # 转换成功原样返回，负值由 service 层钳制
    # 端点源码必须使用 type=int 取参（防回退成 int(args.get(...)) 导致 500）
    import inspect as _inspect

    src = _inspect.getsource(_template_api.list_templates)
    assert "type=int" in src and "int(args.get" not in src
