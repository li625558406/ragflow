"""flow 版本来源白名单纯逻辑单测（upload_version 的 source 表单字段）。

说明：直接 `from api.apps.restful_apis.flow_app import ...` 会触发 api/apps/__init__.py
的 `settings.init_settings()`，进而需要本机 Redis/ES（非单元测试环境）。故照
test_flow_doc_table_edit.py 的模式：从源文件加载 flow_app 模块，并注入最小桩依赖
（仅 api.apps，避免 init_settings；其余模块均可真实导入）。
"""

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


def _noop_decorator(*a, **kw):
    def deco(f):
        return f

    return deco


def _load_flow_app():
    _make_stub_module("api.apps", current_user=None, login_required=_noop_decorator)
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "flow_app.py"))
    spec = spec_from_file_location("flow_app_under_test_source", path)
    mod = module_from_spec(spec)
    sys.modules["flow_app_under_test_source"] = mod
    spec.loader.exec_module(mod)
    return mod


_flow_app = _load_flow_app()


def test_normalize_version_source():
    _normalize_version_source = _flow_app._normalize_version_source
    assert _normalize_version_source(None) == "manual_upload"
    assert _normalize_version_source("") == "manual_upload"
    assert _normalize_version_source("manual_upload") == "manual_upload"
    assert _normalize_version_source("ai_template_fill") == "ai_template_fill"
    assert _normalize_version_source("hacker") == "manual_upload"  # 白名单外回退


def test_version_content_max_constant():
    assert _flow_app._VERSION_CONTENT_MAX == 20000
