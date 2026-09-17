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
from typing import ClassVar

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

    # ── 终态「DB 行权威 / 快照只是重连缓存」口径（2026-09-17 生产事故回归门）──
    # 事故：对话里就地修改（FillTemplate action=modify）把新值回写进 DB 行 values，
    # **不碰** Redis 进度快照（24h TTL）→ 快照里的旧值长期遮蔽 DB 新值，前端「预览
    # 打开拉一次 progress」与「刷新拉运行快照」两条恢复链路都拿到改前内容 → 用户
    # 看到「模型说改好了、预览没变」。实测该任务：snapshot.values['approval_authority']
    # =''，DB render 同键='李港111'，其余 112 键完全一致，DB update_time 比快照
    # updated_at 晚 17 分钟，成稿文件（真源+下载副本 md5 相同）都已含新值。

    def test_terminal_db_row_beats_stale_snapshot(self):
        """核心守卫：终态下 DB 行是权威，快照旧值不得遮蔽就地修改的结果。"""
        snap = {"status": "done", "values": {"approval_authority": "", "b": "同"}}
        p = _template_api.build_progress_payload(
            self._task(status="done",
                       values={"render": {"approval_authority": "李港111", "b": "同"}}),
            snap)
        assert p["values"] == {"approval_authority": "李港111", "b": "同"}, \
            "终态仍以快照优先 = 就地修改永远看不见（2026-09-17 事故重演）"

    def test_terminal_db_values_absent_falls_back_to_snapshot(self):
        """终态但 DB 行没有产值（脏行/未回写）→ 快照兜底，不能变成空。"""
        snap = {"status": "done", "values": {"a": "1"}}
        p = _template_api.build_progress_payload(self._task(status="done", values=None), snap)
        assert p["values"] == {"a": "1"}

    def test_terminal_db_values_empty_dict_falls_back_to_snapshot(self):
        # render 存在但为空 dict：与「没有产值」同义，不得用空对象盖掉快照
        snap = {"status": "done", "values": {"a": "1"}}
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {}}), snap)
        assert p["values"] == {"a": "1"}

    def test_authority_follows_effective_status_not_db_status(self):
        """权威判定必须按**生效状态**（快照 status 优先，同 payload.status）走：
        DB 还停在 generating、快照已 done 的那一瞬，两者口径不能分裂。
        此处 DB 行有产值而快照是旧值 → 按 done 取 DB。"""
        snap = {"status": "done", "values": {"a": "旧"}}
        p = _template_api.build_progress_payload(
            self._task(status="generating", values={"render": {"a": "改后"}}), snap)
        assert p["status"] == "done"
        assert p["values"] == {"a": "改后"}

    def test_running_snapshot_still_authoritative_over_db(self):
        """反例守卫：非终态的实时产值只在快照里（DB 行要到终态才写全量）→ 快照
        优先的口径**不得**被上面那条顺手反向统一掉。"""
        snap = {"status": "generating", "values": {"a": "实时新值"}}
        p = _template_api.build_progress_payload(
            self._task(status="generating", values={"render": {"a": "旧"}}), snap)
        assert p["values"] == {"a": "实时新值"}

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

    def test_not_stalled_when_snapshot_alive_second_epoch(self):
        # 跨量纲对抗：快照 updated_at 传旧秒级值（10位），读侧防御归一到毫秒 → 不误报
        snap = {"status": "generating",
                "updated_at": int(time.time()) - 10}
        p = _template_api.build_progress_payload(self._task(status="generating",
                                                            update_offset_ms=601_000), snap)
        assert p["stalled"] is False

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

    _PHS: ClassVar = [{"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]

    def test_done_with_placeholders_derives_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": ""}}),
            None, None, self._PHS)
        assert p["unfilled"] == [{"key": "b", "name": "乙", "required": False}]

    def test_partial_with_placeholders_derives_unfilled(self):
        # partial 也是终态（部分范本成功）：留空填写点同样要派生下发
        p = _template_api.build_progress_payload(
            self._task(status="partial", values={"render": {"a": "", "b": "y"}}),
            None, None, self._PHS)
        assert p["unfilled"] == [{"key": "a", "name": "甲", "required": True}]

    def test_non_terminal_never_derives(self):
        p = _template_api.build_progress_payload(
            self._task(status="generating",
                       values={"render": {"a": "", "b": ""}}),
            None, None, self._PHS)
        assert p["unfilled"] is None

    def test_no_placeholders_no_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": ""}}), None)
        assert p["unfilled"] is None

    def test_values_not_dict_no_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values=None), None, None, self._PHS)
        assert p["unfilled"] is None

    def test_falsy_valid_value_all_filled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "0", "b": "false"}}),
            None, None, self._PHS)
        assert p["unfilled"] is None

    def test_terminal_derivations_follow_db_render_not_snapshot(self):
        """终态 filled/unfilled 必须按 **DB render** 派生：就地修改只回写 DB 行，
        快照是改前旧值。（本例原为 test_snapshot_values_authoritative —— 断言
        「终态快照优先」正是 2026-09-17 事故的契约化，此处反转。）"""
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": ""}}),
            {"status": "done", "values": {"a": "", "b": "y"}},
            None, self._PHS)
        assert p["unfilled"] == [{"key": "b", "name": "乙", "required": False}]
        assert p["filled"] == [{"key": "a", "name": "甲"}]

    def test_done_with_placeholders_derives_filled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": ""}}),
            None, None, self._PHS)
        assert p["filled"] == [{"key": "a", "name": "甲"}]

    def test_filled_unfilled_partition_exhaustive_and_disjoint(self):
        """反漂移：filled ∪ unfilled == 全部填写点，且两者互斥（前端合并即全量映射）。"""
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": "  "}}),
            None, None, self._PHS)
        f_keys = {it["key"] for it in p["filled"]}
        u_keys = {it["key"] for it in p["unfilled"]}
        assert f_keys == {"a"} and u_keys == {"b"}
        assert f_keys | u_keys == {"a", "b"} and f_keys & u_keys == set()

    def test_fully_filled_normalizes_filled_but_no_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": "y"}}),
            None, None, self._PHS)
        assert p["unfilled"] is None
        assert p["filled"] == [{"key": "a", "name": "甲"}, {"key": "b", "name": "乙"}]

    def test_all_empty_normalizes_unfilled_but_no_filled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {}}),
            None, None, self._PHS)
        assert p["filled"] is None
        assert {it["key"] for it in p["unfilled"]} == {"a", "b"}

    def test_non_terminal_never_derives_filled(self):
        p = _template_api.build_progress_payload(
            self._task(status="generating", values={"render": {"a": "x", "b": ""}}),
            None, None, self._PHS)
        assert p["filled"] is None and p["unfilled"] is None

    def test_values_dirty_never_derives_filled(self):
        """对抗：values 非 dict（脏数据）→ 两者皆 None 且不抛。"""
        p = _template_api.build_progress_payload(
            self._task(status="done", values=["junk"]), None, None, self._PHS)
        assert p["filled"] is None and p["unfilled"] is None
        p2 = _template_api.build_progress_payload(
            self._task(status="done", values="junk"), None, None, self._PHS)
        assert p2["filled"] is None and p2["unfilled"] is None


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

    def _install_stubs(self, monkeypatch, storage, tpl_name="我的 范本.docx",
                       file_type="docx", tpl=None):
        monkeypatch.setattr(_template_api, "settings",
                            SimpleNamespace(STORAGE_IMPL=storage))
        tpl = tpl if tpl is not None else SimpleNamespace(name=tpl_name, file_type=file_type)
        monkeypatch.setattr(_template_api, "TplTemplateService",
                            SimpleNamespace(get_by_id=lambda id: (True, tpl)))

    def _not_found_stubs(self, monkeypatch, storage):
        # 模板行查不到：get_by_id 返回 (False, None)
        monkeypatch.setattr(_template_api, "settings",
                            SimpleNamespace(STORAGE_IMPL=storage))
        monkeypatch.setattr(_template_api, "TplTemplateService",
                            SimpleNamespace(get_by_id=lambda id: (False, None)))

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
        def boom(id):
            raise RuntimeError("db down")
        monkeypatch.setattr(_template_api, "settings", SimpleNamespace(STORAGE_IMPL=_FakeStorage()))
        monkeypatch.setattr(_template_api, "TplTemplateService",
                            SimpleNamespace(get_by_id=boom))
        assert _template_api._bridge_download(self._task()) is None

    def test_bridge_xlsx_template_filename_ext(self, monkeypatch):
        # xlsx 模板的成稿 filename 必须带真实扩展名 .xlsx，而非硬编码 .docx
        storage = _FakeStorage()
        self._install_stubs(monkeypatch, storage, tpl_name="台账 范本", file_type="xlsx")
        d = _template_api._bridge_download(self._task())
        assert d is not None
        assert d["filename"] == "台账 范本.xlsx"
        assert d["filename"].endswith(".xlsx")
        assert d["filename"] == d["name"]

    def test_bridge_template_missing_fallback_docx(self, monkeypatch):
        # 模板行查不到只是桥接细节：fallback 保持 .docx（名退化为 doc_id），
        # done 任务不能因此丢下载入口
        storage = _FakeStorage()
        self._not_found_stubs(monkeypatch, storage)
        d = _template_api._bridge_download(self._task())
        assert d is not None
        assert d["filename"] == "tplfill-taskid1.docx"
        assert storage.puts == [("tenant1-downloads", "tplfill-taskid1", b"docx-bytes")]

    def test_bridge_unknown_file_type_fallback_docx(self, monkeypatch):
        # file_type 是白名单外的脏值（如历史脏数据 ".doc"）→ 防御性退回 .docx
        storage = _FakeStorage()
        self._install_stubs(monkeypatch, storage, tpl_name="脏 范本", file_type=".doc")
        d = _template_api._bridge_download(self._task())
        assert d is not None
        assert d["filename"] == "脏 范本.docx"
