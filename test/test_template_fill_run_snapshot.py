# -*- coding: utf-8 -*-
"""运行快照恢复（设计 2026-09-16）对抗性单测：
- agent 侧 `_write_run_snapshot`：无 canvas task_id 跳过 / 键与载荷契约 / done 短 TTL /
  Redis 故障只告警不抛
- api 侧 `build_run_snapshot_payload` 纯函数：键缺失、脏模板行、任一 fill task 越权
  整体按不存在、selected 挂起期无任务行、终态桥接失败、placeholder 脏数据、
  快照/DB 状态映射、错误兜底
- `_read_run_snapshot`：键缺失 / bytes 容错 / 坏 JSON / 非 dict JSON

纯函数与桩对象，不触真实 DB / Redis / MinIO / LLM。
"""
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.component import template_fill as fill_template

# ---------- api 模块加载（照 test_template_fill_progress_api.py 模式桩掉 api.apps） ----------


def _noop_decorator(f=None, *a, **kw):
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
    spec = importlib.util.spec_from_file_location("template_api_run_snapshot_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["template_api_run_snapshot_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_template_api = _load_template_api()


# ---------- _write_run_snapshot（写入侧） ----------


class FakeRedis:
    def __init__(self, exc_on_set=None):
        self.sets = []
        self._exc = exc_on_set

    def set(self, key, val, exp=0):
        if self._exc:
            raise self._exc
        self.sets.append((key, val, exp))


class _Canvas:
    def __init__(self, task_id="canvas-1"):
        self.task_id = task_id


def _make(canvas=None):
    cpn = fill_template.TemplateFill.__new__(fill_template.TemplateFill)
    cpn._id = "node1"
    cpn._canvas = canvas if canvas is not None else _Canvas()
    return cpn


class TestWriteRunSnapshot:
    def test_no_canvas_task_id_skips_write(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(fill_template, "REDIS_CONN", fake)
        _make(_Canvas(task_id=""))._write_run_snapshot("selected", templates=[{"template_id": "t1"}])
        assert fake.sets == []

    def test_happy_path_key_payload_ttl(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(fill_template, "REDIS_CONN", fake)
        _make()._write_run_snapshot(
            "confirm_pending",
            templates=[{"template_id": "t1", "name": "报告"}],
            tasks={"t1": "f1"},
            pending={"type": "confirm", "nonce": "n1"},
        )
        assert len(fake.sets) == 1
        key, val, exp = fake.sets[0]
        assert key == "tpl_fill:run:canvas-1"
        assert exp == fill_template._RUN_SNAPSHOT_TTL
        payload = json.loads(val)
        assert payload["stage"] == "confirm_pending"
        assert payload["tasks"] == {"t1": "f1"}
        assert payload["pending"] == {"type": "confirm", "nonce": "n1"}
        assert isinstance(payload["updated_at"], int)

    def test_done_short_ttl(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(fill_template, "REDIS_CONN", fake)
        _make()._write_run_snapshot("done", ttl=fill_template._RUN_SNAPSHOT_DONE_TTL)
        assert fake.sets[0][2] == fill_template._RUN_SNAPSHOT_DONE_TTL

    def test_redis_failure_swallowed(self, monkeypatch):
        class Boom(Exception):
            pass

        fake = FakeRedis(exc_on_set=Boom())
        monkeypatch.setattr(fill_template, "REDIS_CONN", fake)
        # 不抛：快照是恢复增强，不是执行权威
        _make()._write_run_snapshot("filling", tasks={"t1": "f1"})
        assert fake.sets == []


# ---------- build_run_snapshot_payload（端点组装纯函数） ----------


def _run(**over):
    base = {
        "stage": "filling",
        "templates": [{"template_id": "t1", "name": "报告", "slot_count": 5}],
        "tasks": {"t1": "f1"},
        "pending": None,
        "updated_at": 1,
    }
    base.update(over)
    return base


def _row(status="generating", values=None, error="", template_version_id="v1"):
    return SimpleNamespace(id="f1", template_id="t1", status=status, values=values,
                           error=error, template_version_id=template_version_id)


class TestBuildRunSnapshotPayload:
    def test_none_and_non_dict_run(self):
        assert _template_api.build_run_snapshot_payload(None, owned_check=lambda x: True,
                                                        get_task=lambda x: None, read_snap=lambda x: None,
                                                        version_placeholders=None, bridge=None) == {"exists": False}
        assert _template_api.build_run_snapshot_payload("junk", owned_check=lambda x: True,
                                                        get_task=lambda x: None, read_snap=lambda x: None,
                                                        version_placeholders=None, bridge=None) == {"exists": False}

    def test_any_unowned_task_hides_everything(self):
        # 越权防御：任一 fill task 非本人 → 整体按不存在（不泄漏部分状态）
        run = _run(tasks={"t1": "f1", "t2": "f2"},
                   templates=[{"template_id": "t1"}, {"template_id": "t2"}])
        payload = _template_api.build_run_snapshot_payload(
            run, owned_check=lambda fid: fid != "f2", get_task=lambda x: None,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert payload == {"exists": False}

    def test_dirty_task_values_skipped_for_ownership(self):
        # tasks 值为非字符串/空串时不参与越权检查，也不查行
        run = _run(tasks={"t1": "", "t2": 123, "t3": None},
                   templates=[{"template_id": "t1"}])
        checked = []

        def _owned(fid):
            checked.append(fid)
            return True

        payload = _template_api.build_run_snapshot_payload(
            run, owned_check=_owned, get_task=lambda x: pytest.fail("不应查行"),
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert checked == []
        assert payload["exists"] is True
        assert payload["templates"][0]["status"] == "selected"
        assert payload["templates"][0]["task_id"] == ""

    def test_selected_stage_without_task_row(self):
        # 挂起确认期：模板行尚无 fill task → status=selected
        run = _run(stage="confirm_pending",
                   pending={"type": "confirm", "nonce": "n1", "confirm_templates": []},
                   tasks={})
        payload = _template_api.build_run_snapshot_payload(
            run, owned_check=lambda x: True, get_task=lambda x: pytest.fail("无 fid 不查行"),
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert payload["exists"] is True
        assert payload["finished"] is False
        assert payload["pending"]["type"] == "confirm"
        t = payload["templates"][0]
        assert t["status"] == "selected" and t["done"] is None and t["download"] is None

    def test_done_row_bridges_and_derives_unfilled(self):
        row = _row(status="done", values={"render": {"k1": "v1"}})
        phs = [{"key": "k1", "name": "字段1", "required": True},
               {"key": "k2", "name": "字段2", "required": False}]
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True,
            get_task=lambda fid: row, read_snap=lambda x: {"status": "done"},
            version_placeholders=lambda tid, vid: phs if vid == "v1" else None,
            bridge=lambda r: {"doc_id": "d1", "filename": "x.docx"})
        t = payload["templates"][0]
        assert t["status"] == "filled" and payload["finished"] is True
        assert t["download"] == {"doc_id": "d1", "filename": "x.docx"}
        assert t["unfilled"] == [{"key": "k2", "name": "字段2", "required": False}]
        # filled 与 unfilled 互补：刷新恢复后中文名映射不丢，卡片+预览同步
        assert t["filled"] == [{"key": "k1", "name": "字段1"}]

    def test_non_filled_rows_never_carry_filled(self):
        """selected / filling / failed 行都不带 filled（未终态没有可信产值）。"""
        for st in ("generating", "failed"):
            row = _row(status=st, values={"render": {"k1": "v1"}})
            payload = _template_api.build_run_snapshot_payload(
                _run(stage="filling"), owned_check=lambda x: True,
                get_task=lambda fid: row, read_snap=lambda fid: None,
                version_placeholders=lambda *a: [{"key": "k1", "name": "字段1"}],
                bridge=None)
            t = payload["templates"][0]
            assert t["filled"] is None and t["unfilled"] is None, st
            assert t["values"] == {"k1": "v1"}, st

    def test_filled_absent_when_placeholders_missing(self):
        """version_placeholders 返回 None（版本查不到）→ filled 保持 None，不回落下发。"""
        row = _row(status="done", values={"render": {"k1": "v1"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "done"}, version_placeholders=lambda *a: None,
            bridge=None)
        assert payload["templates"][0]["filled"] is None

    def test_filled_name_falls_back_to_key(self):
        """placeholder 无 name → filled 的 name 回落 key（前端映射才不漏项）。"""
        row = _row(status="done", values={"render": {"k1": "v1"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "done"},
            version_placeholders=lambda *a: [{"key": "k1"}], bridge=None)
        assert payload["templates"][0]["filled"] == [{"key": "k1", "name": "k1"}]

    def test_dirty_render_no_filled_and_no_crash(self):
        """对抗：render 是畸形真值 → filled 为 None 且不抛（刷新恢复路径不能崩）。"""
        row = _row(status="done", values={"render": "not-a-dict"})
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "done"},
            version_placeholders=lambda *a: [{"key": "k1", "name": "字段1"}],
            bridge=None)
        assert payload["templates"][0]["filled"] is None

    def test_bridge_failure_still_filled(self):
        row = _row(status="done")
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=lambda *a: None,
            bridge=lambda r: None)
        t = payload["templates"][0]
        assert t["status"] == "filled" and t["download"] is None and t["unfilled"] is None

    def test_placeholders_dirty_values_no_unfilled(self):
        # values 非 dict（脏数据）：不派生 unfilled、不抛
        row = _row(status="done", values="not-a-dict")
        calls = []

        def _ph(tid, vid):
            calls.append(vid)
            return [{"key": "k1", "name": "字段1", "required": True}]

        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=_ph, bridge=None)
        assert payload["templates"][0]["unfilled"] is None
        assert calls == ["v1"]

    def test_empty_derived_unfilled_normalized_to_none(self):
        row = _row(status="done", values={"render": {"k1": "v1"}})
        phs = [{"key": "k1", "name": "字段1", "required": True}]
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=lambda *a: phs, bridge=None)
        assert payload["templates"][0]["unfilled"] is None

    def test_snapshot_values_authoritative_over_db(self):
        row = _row(status="generating", values={"render": {"k1": "db"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "generating", "done": 2, "total": 5,
                                 "values": {"k1": "snap"}},
            version_placeholders=None, bridge=None)
        t = payload["templates"][0]
        assert t["values"] == {"k1": "snap"} and t["done"] == 2 and t["total"] == 5

    def test_terminal_snapshot_values_must_not_mask_db(self):
        """刷新恢复链路同守卫（与 per-task progress 同一根因）：终态 done 后
        就地修改只回写 DB 行 values，Redis 进度快照仍是改前旧值（24h TTL）→
        若快照优先，用户刷新后成稿卡/预览又退回改前内容。"""
        row = _row(status="done", values={"render": {"approval_authority": "李港111"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(stage="done"), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "done", "values": {"approval_authority": ""}},
            version_placeholders=None, bridge=None)
        t = payload["templates"][0]
        assert t["status"] == "filled"
        assert t["values"] == {"approval_authority": "李港111"}, \
            "终态仍以快照优先 = 刷新恢复回退到改前内容（2026-09-17 事故重演）"

    def test_running_snapshot_values_still_authoritative(self):
        # 反例守卫：非终态的实时产值只在快照里 → 快照优先口径不得被顺手反向统一
        row = _row(status="generating", values={"render": {"k1": "旧"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "generating", "values": {"k1": "实时新值"}},
            version_placeholders=None, bridge=None)
        assert payload["templates"][0]["values"] == {"k1": "实时新值"}

    def test_db_values_fallback_when_snap_empty(self):
        row = _row(status="generating", values={"render": {"k1": "db"}})
        payload = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert payload["templates"][0]["values"] == {"k1": "db"}

    def test_row_values_non_dict_tolerated(self):
        row = _row(values="junk")
        payload = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert payload["templates"][0]["values"] is None

    def test_failed_error_from_snap_then_row(self):
        row = _row(status="failed", error="row-err")
        p1 = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "failed", "error": "snap-err"},
            version_placeholders=None, bridge=None)
        assert p1["templates"][0]["error"] == "snap-err"
        p2 = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: {"status": "failed"}, version_placeholders=None, bridge=None)
        assert p2["templates"][0]["error"] == "row-err"

    def test_partial_maps_to_filled_and_unknown_to_filling(self):
        row = _row(status="partial")
        p = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert p["templates"][0]["status"] == "filled"
        row2 = _row(status="weird")
        p2 = _template_api.build_run_snapshot_payload(
            _run(), owned_check=lambda x: True, get_task=lambda x: row2,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert p2["templates"][0]["status"] == "filling"

    def test_dirty_template_rows_skipped(self):
        run = _run(templates=["junk", None, {"name": "no-id"}, {"template_id": "ok", "name": "N"}],
                   tasks={"ok": "f1"})
        payload = _template_api.build_run_snapshot_payload(
            run, owned_check=lambda x: True, get_task=lambda x: None,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert [t["template_id"] for t in payload["templates"]] == ["ok"]

    def test_no_templates_and_missing_stage(self):
        payload = _template_api.build_run_snapshot_payload(
            {"tasks": {}}, owned_check=lambda x: True, get_task=lambda x: None,
            read_snap=lambda x: None, version_placeholders=None, bridge=None)
        assert payload == {"exists": True, "stage": "", "finished": False,
                           "templates": [], "pending": None}


# ---------- _read_run_snapshot（读侧防御） ----------


class TestReadRunSnapshot:
    def _with_redis(self, monkeypatch, val):
        class R:
            def get(self, key):
                return val
        monkeypatch.setattr(_template_api, "REDIS_CONN", R())

    def test_missing_key(self, monkeypatch):
        self._with_redis(monkeypatch, None)
        assert _template_api._read_run_snapshot("c1") is None

    def test_bytes_json_dict(self, monkeypatch):
        self._with_redis(monkeypatch, json.dumps({"stage": "done"}).encode("utf-8"))
        assert _template_api._read_run_snapshot("c1") == {"stage": "done"}

    def test_bad_json(self, monkeypatch):
        self._with_redis(monkeypatch, b"{not json")
        assert _template_api._read_run_snapshot("c1") is None

    def test_non_dict_json(self, monkeypatch):
        self._with_redis(monkeypatch, json.dumps([1, 2]).encode("utf-8"))
        assert _template_api._read_run_snapshot("c1") is None
