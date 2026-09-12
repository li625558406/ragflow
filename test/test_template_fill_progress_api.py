# -*- coding: utf-8 -*-
"""fill-task progress 端点 payload 组装的对抗性单测（纯函数，不触库不触 Redis）。

直接 `import api.apps.restful_apis.template_api` 会触发 api/apps/__init__.py 的
settings.init_settings()（需本机 Redis/ES）。照 test_template_api_routes.py 的
模式：桩掉 api.apps 后从源文件加载模块，其余依赖真实导入。
"""
import os
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：原样返回被装饰函数（否则 blueprint 端点名冲突）。"""
    return f


def _load_template_api():
    _make_stub = getattr(sys.modules.get("test_template_api_routes"), "_make_stub_module", None)
    if _make_stub is None:
        def _make_stub(name, **attrs):
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
            return mod
    _make_stub("api.apps", current_user=SimpleNamespace(id="u1"), login_required=_noop_decorator)
    import importlib.util
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "template_api.py"))
    spec = importlib.util.spec_from_file_location("template_api_progress_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["template_api_progress_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_template_api = _load_template_api()


class TestBuildProgressPayload:
    def _task(self, status="generating", update_offset_s=0, values=None,
              result_file_id="", error=""):
        return SimpleNamespace(
            id="t" * 32, template_id="tpl1", status=status,
            values=values, error=error, result_file_id=result_file_id,
            tenant_id="tenant1",
            update_time=datetime.now() - timedelta(seconds=update_offset_s))

    def test_running_merges_snapshot(self):
        snap = {"status": "generating", "done": 3, "total": 10,
                "values": {"a": "1"}, "error": ""}
        p = _template_api.build_progress_payload(self._task(status="generating"), snap)
        assert p["status"] == "generating"
        assert p["done"] == 3 and p["total"] == 10
        assert p["values"] == {"a": "1"}
        assert p["stalled"] is False
        assert p["download"] is None

    def test_snapshot_none_falls_back_to_db(self):
        # Redis 挂掉：DB 行权威（done 时 values.render 仍可回看）
        p = _template_api.build_progress_payload(self._task(
            status="done", values={"cells": {}, "render": {"a": "1"}}), None)
        assert p["status"] == "done"
        assert p["values"] == {"a": "1"}

    def test_stalled_after_10min(self):
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_s=601), None)
        assert p["stalled"] is True

    def test_not_stalled_when_terminal(self):
        p = _template_api.build_progress_payload(self._task(status="failed",
                                                            update_offset_s=99999), None)
        assert p["stalled"] is False

    def test_done_without_file_no_download(self):
        p = _template_api.build_progress_payload(self._task(status="done"), None)
        assert p["download"] is None
