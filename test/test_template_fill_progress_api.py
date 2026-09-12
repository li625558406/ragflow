# -*- coding: utf-8 -*-
"""fill-task progress 端点 payload 组装 + download 桥接的对抗性单测
（纯函数与桩对象，不触库不触 Redis 不触 MinIO）。

直接 `import api.apps.restful_apis.template_api` 会触发 api/apps/__init__.py 的
settings.init_settings()（需本机 Redis/ES）。照 test_template_api_routes.py 的
模式：桩掉 api.apps 后从源文件加载模块，其余依赖真实导入。
"""
import os
import sys
import time
import types
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
    def _task(self, status="generating", update_offset_ms=0, values=None,
              result_file_id="", error=""):
        # TplFillTask.update_time 继承 BaseModel 的 BigIntegerField（毫秒 epoch），
        # 与 datetime 无关；错写成 datetime 会让 stalled 判定抛 TypeError（端点 500）
        return SimpleNamespace(
            id="t" * 32, template_id="tpl1", status=status,
            values=values, error=error, result_file_id=result_file_id,
            tenant_id="tenant1",
            update_time=int(time.time() * 1000) - update_offset_ms)

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

    def test_snapshot_status_overrides_db(self):
        # 快照是 executor 的实时视图：其 status 优先于可能滞后的 DB 行
        p = _template_api.build_progress_payload(self._task(status="generating"),
                                                 {"status": "done"})
        assert p["status"] == "done"

    def test_stalled_after_10min(self):
        # DB update_time 超 600s 且无快照活性信号 → 判中断
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=601_000), None)
        assert p["stalled"] is True

    def test_not_stalled_when_fresh(self):
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=0), None)
        assert p["stalled"] is False

    def test_not_stalled_when_terminal(self):
        p = _template_api.build_progress_payload(self._task(status="failed",
                                                            update_offset_ms=99999_000), None)
        assert p["stalled"] is False

    def test_not_stalled_when_snapshot_alive(self):
        # 长 generating 任务：DB update_time 只在状态跃迁刷新会超阈值，
        # 但快照 updated_at（executor 每次写快照刷新）仍新鲜 → 任务活着，不误报
        snap = {"status": "generating",
                "updated_at": int(time.time() * 1000) - 10_000}
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=601_000), snap)
        assert p["stalled"] is False

    def test_stalled_when_snapshot_stale_too(self):
        # 两个活性信号都停跳才是真中断：快照 updated_at 同样超阈值 → stalled
        snap = {"status": "generating",
                "updated_at": int(time.time() * 1000) - 601_000}
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=601_000), snap)
        assert p["stalled"] is True

    def test_stalled_when_snapshot_has_no_updated_at(self):
        # 旧版本快照无 updated_at 字段：退回仅 DB 信号判定 → stalled
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=601_000),
                                                 {"status": "generating"})
        assert p["stalled"] is True

    def test_done_without_file_no_download(self):
        p = _template_api.build_progress_payload(self._task(status="done"), None)
        assert p["download"] is None

    def test_download_passthrough(self):
        # payload 是纯函数：download 由端点层桥接后传入，原样透传不加工
        d = {"doc_id": "tplfill-x", "filename": "a.docx", "name": "a.docx", "url": "/u"}
        p = _template_api.build_progress_payload(self._task(status="done"), None, download=d)
        assert p["download"] is d


class _FakeStorage:
    """记录调用序列的 STORAGE_IMPL 桩；get 返回 preset blob。"""

    def __init__(self, blob=b"docx-bytes"):
        self.blob = blob
        self.gets, self.puts = [], []

    def get(self, bucket, name):
        self.gets.append((bucket, name))
        return self.blob

    def put(self, bucket, name, blob):
        self.puts.append((bucket, name, blob))
        return True


class TestBridgeDownload:
    def setup_method(self):
        # 进程内记忆化集合是模块级状态，逐用例复位防跨用例污染
        _template_api._bridged_tasks = set()

    def _task(self, task_id="taskid1", template_id="tpl1", tenant_id="tenant1",
              result_file_id="result1"):
        return SimpleNamespace(id=task_id, template_id=template_id, tenant_id=tenant_id,
                               result_file_id=result_file_id, status="done")

    def _install_stubs(self, monkeypatch, storage, tpl_name="我的 范本.docx"):
        monkeypatch.setattr(_template_api, "settings",
                            SimpleNamespace(STORAGE_IMPL=storage))
        tpl = SimpleNamespace(name=tpl_name)
        monkeypatch.setattr(_template_api, "TplTemplateService",
                            SimpleNamespace(get_or_none=lambda id: tpl))

    def test_bridge_success_put_called(self, monkeypatch):
        storage = _FakeStorage()
        self._install_stubs(monkeypatch, storage)
        d = _template_api._bridge_download(self._task())
        assert d is not None
        assert d["doc_id"] == "tplfill-taskid1"
        assert d["filename"] == d["name"]
        assert "id=tplfill-taskid1" in d["url"]
        assert "created_by=tenant1" in d["url"]
        assert storage.gets == [("tpl1", "result1")]
        assert storage.puts == [("tenant1-downloads", "tplfill-taskid1", b"docx-bytes")]

    def test_bridge_blob_missing_returns_none(self, monkeypatch):
        storage = _FakeStorage(blob=None)
        self._install_stubs(monkeypatch, storage)
        assert _template_api._bridge_download(self._task()) is None
        assert storage.puts == []  # 空 blob 禁止 put（防下游指向不存在的对象）

    def test_bridge_memoized_skips_get(self, monkeypatch):
        storage = _FakeStorage()
        self._install_stubs(monkeypatch, storage)
        assert _template_api._bridge_download(self._task()) is not None
        assert _template_api._bridge_download(self._task()) is not None
        # 第二次起不碰 blob：get 只发生一次，put 也只发生一次
        assert len(storage.gets) == 1
        assert len(storage.puts) == 1

    def test_bridge_exception_returns_none(self, monkeypatch):
        # 模板查询抛异常（DB 抖动）不得炸轮询端点：吞异常返 None
        def boom(**kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(_template_api, "settings", SimpleNamespace(STORAGE_IMPL=_FakeStorage()))
        monkeypatch.setattr(_template_api, "TplTemplateService",
                            SimpleNamespace(get_or_none=boom))
        assert _template_api._bridge_download(self._task()) is None
