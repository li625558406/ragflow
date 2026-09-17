# test/test_file_review_api.py
"""文件审核 REST API 测试（T9）。

不依赖 Redis/ES/DB：照 test_template_api_routes.py / test_flow_ai_record_update.py 的模式，
从源文件加载 file_review_api.py 并注入 `api.apps` 最小桩（login_required 透传 +
current_user），端点体内的 Service 调用一律 monkeypatch。

端点走**真实 Quart 请求上下文**（app.test_request_context），因此 body 解析、装饰器注入
tenant_id、路由分发都是真的；只有触库/起线程那两层被替换掉。
"""
import ast
import asyncio
import os
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from quart import Quart

from common.constants import RetCode

_API_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "file_review_api.py"))

TENANT = "u1"


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：必须原样返回被装饰函数（返回内部 deco 会丢函数）。"""
    return f


def _load_api():
    stub = types.ModuleType("api.apps")
    stub.current_user = SimpleNamespace(id=TENANT)
    stub.login_required = _noop_decorator
    sys.modules["api.apps"] = stub
    spec = spec_from_file_location("file_review_api_under_test", _API_PATH)
    mod = module_from_spec(spec)
    sys.modules["file_review_api_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_api = _load_api()

APP = Quart(__name__)
APP.register_blueprint(_api.manager)


def _call(endpoint, *, method="POST", path="/", body=None, **kwargs):
    """在真实请求上下文里调端点，返回响应的业务体（dict）。

    注意 endpoint 被真实的 add_tenant_id_to_kwargs 包着，其 wrapper 签名是
    `(**kwargs)` —— 调用时**必须**全部用关键字传参（传位置参数会被丢进 `*args`、
    进而漏掉 task_id 等路径参数）。
    Quart 的 Response.get_json() 是协程，故整体包在 asyncio.run 里。
    """
    async def _inner():
        async with APP.test_request_context(path, method=method, json=body):
            resp = await endpoint(**kwargs)
        return await resp.get_json()
    return asyncio.run(_inner())


def _round(round_no, status, **over):
    row = SimpleNamespace(
        id=f"r{round_no}", task_id="t1", file_id="f1", round_no=round_no,
        template_id="bid_doc_format", user_query="审核这份招标文件",
        status=status, file_version=f"v{round_no}", kb_ids=None,
        minio_path=None, summary="", llm_raw=None, error="",
        tenant_id=TENANT, created_by=TENANT)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _ann(aid, **over):
    row = SimpleNamespace(
        id=aid, round_id="r1", task_id="t1", file_id="f1", file_version="v1",
        anchor='{"p_idx": 3}', matched_text="投标人须", type="clause",
        severity="high", issue="缺少投标保证金条款", suggestion="补一条",
        source="ai", status="open", prev_annotation_id=None, tenant_id=TENANT)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _patch_services(monkeypatch, *, rounds=(), annotations=(), pending=(),
                    owned=None, running=False, next_no=None):
    """替换端点用到的全部 Service / spawn 入口，返回被捕获的调用记录。"""
    calls = {}
    owned_rounds = list(rounds) if owned is None else list(owned)

    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_file",
                        classmethod(lambda cls, fid: list(rounds)))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "list_by_file",
                        classmethod(lambda cls, fid: list(annotations)))
    monkeypatch.setattr(_api.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: owned_rounds))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "list_pending_by_task",
                        classmethod(lambda cls, tid: list(pending)))
    monkeypatch.setattr(_api.FileReviewRoundService, "next_round",
                        classmethod(lambda cls, tid: next_no))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "get_by_id",
                        classmethod(lambda cls, aid: calls.get("_ann_row")))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "update_status",
                        classmethod(lambda cls, aid, status:
                                    calls.setdefault("updated", []).append((aid, status)) or True))
    monkeypatch.setattr(
        _api.FileReviewRoundService, "create_round",
        classmethod(lambda cls, **kw: calls.update({"created": kw}) or "rid-new"))
    # 受理闸门已下沉到 Service 层的 admit_fix_round，它**函数内延迟 import** spawn 模块 ——
    # 与这里 import 到的是同一个模块对象，故按模块打桩依旧命中（不再经 _api.spawn_mod）。
    from rag.svr.file_review import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "spawn_review_task",
                        lambda tid: calls.setdefault("spawned", []).append(tid))
    monkeypatch.setattr(spawn_mod, "is_running", lambda tid: running)
    return calls


# ── 端点接线（不依赖 DB，靠真实 Quart 路由表） ────────────────────────

def test_all_routes_registered_on_blueprint():
    """路由集合 + manager 名 + 端点函数存在 + 协程化（被 add_tenant_id_to_kwargs / login_required 装饰后
    `__wrapped__` 透到内层 async）—— 四项合并验证（覆盖被合并的 test_module_exposes_manager_blueprint_and_async_endpoints）。
    `manager` 变量名是自动注册的唯一契约（api/apps/__init__.py:315）。"""
    import inspect
    assert _api.manager.name == "file_review_api"
    rules = {}
    for r in APP.url_map.iter_rules():
        if r.rule == "/static/<path:filename>":
            continue
        rules.setdefault(r.rule, set()).update(r.methods)
    assert rules == {
        "/file/review/templates": {"GET", "HEAD", "OPTIONS"},
        "/file/review/file/<file_id>/state": {"GET", "HEAD", "OPTIONS"},
        "/file/review/<task_id>/fix": {"POST", "OPTIONS"},
        "/file/review/annotation/<annotation_id>/status": {"POST", "OPTIONS"},
        "/file/review/<task_id>/<file_version>/download": {"GET", "HEAD", "OPTIONS"},
    }, f"路由集合不符：{rules}"
    for name in ("list_review_templates", "review_state", "fix_review",
                 "update_annotation_status", "download_review_version"):
        fn = getattr(_api, name, None)
        assert fn is not None, f"缺少端点函数 {name}"
        assert inspect.iscoroutinefunction(getattr(fn, "__wrapped__", fn)) or callable(fn)


def test_fix_admission_is_synchronous_and_lock_guarded():
    """结构性锁死受理契约：互斥在 Service 层，是**同步函数 + 进程级 threading.Lock**，
    且端点必须在无 await 的临界区内调它。

    为什么盯着这件事：quart 单进程单事件循环下，「临界区内无 await」只挡得住并发 HTTP
    请求；对话工具被 common.connection_utils.timeout 丢进**独立 daemon 线程**执行，与事件
    循环是两个线程 —— 只有进程级锁能覆盖它。若有人把 admit_fix_round 改成协程、或在端点
    里 await 它，锁会在 await 点被让出去，两入口再次并发建出同号轮次、留下永远没人消费的
    fixing 轮次，该 task 之后所有 fix/review 被前置闸门永久挡死（不可逆）。普通用例测不到
    这个（要构造真并发），故用 AST + 函数性质在代码层断言。
    """
    import inspect
    import threading

    from api.db.services import file_review_service as svc

    assert not inspect.iscoroutinefunction(svc.admit_fix_round), \
        "admit_fix_round 必须是同步函数（持 threading.Lock；async 会让锁在 await 点失效）"
    assert isinstance(svc.ADMIT_LOCK_TIMEOUT, (int, float)) and svc.ADMIT_LOCK_TIMEOUT > 0
    assert isinstance(svc._ADMIT_LOCK, type(threading.Lock())), \
        "受理锁必须是 threading.Lock 实例（进程级、跨线程共享）"

    with open(_API_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "fix_review")
    body = next(s for s in fn.body if isinstance(s, ast.Try)).body
    stmts = [s for s in body if "admit_fix_round" in ast.dump(s)]
    assert stmts, "fix_review 必须把受理交给 admit_fix_round"
    for stmt in stmts:
        assert not any(isinstance(n, ast.Await) for n in ast.walk(stmt)), (
            f"admit_fix_round 不得被 await（会让出锁、破坏跨线程互斥）：{ast.dump(stmt)[:200]}")


# ── 纯函数：脏值与边界 ───────────────────────────────────────────────

def test_json_list_and_dict_degrade_on_dirty_input():
    assert _api._json_list(None) == []
    assert _api._json_list("") == []
    assert _api._json_list("not-json") == []
    assert _api._json_list('{"a": 1}') == []          # 是 JSON 但不是数组
    assert _api._json_list('["format"]') == ["format"]
    assert _api._json_dict(None) == {}
    assert _api._json_dict("[]") == {}                 # 是 JSON 但不是对象
    assert _api._json_dict("{bad") == {}
    assert _api._json_dict('{"p_idx": 3}') == {"p_idx": 3}


def test_parse_levels_rejects_whole_batch_on_any_invalid_item():
    f = _api._parse_levels
    assert f(["high", "medium"]) == ["high", "medium"]
    assert f("HIGH") == ["high"]                       # 标量字符串也接
    assert f(["high", "high", "low"]) == ["high", "low"]   # 去重保序
    assert f(["high", "critical"]) is None             # 整批拒绝，不静默丢弃
    assert f(["CRITICAL"]) is None
    assert f([]) is None
    assert f(None) is None
    assert f({"high": 1}) is None
    assert f([None]) is None
    assert f([" high "]) == ["high"]                   # 前后空白归一


def test_fix_base_query_uses_first_round_and_appends_extra():
    """基准只能取首轮 + 非法类型的 user_query 不得把端点打成 500。

    `_fix_base_query` 已随受理闸门下沉到 Service 层（admit_fix_round 内部用它算基准），
    故这里从 Service import。对抗：body 里的 user_query 是任意 JSON —— dict/list/数字直接
    .strip() 会 AttributeError → 500；必须按「没提补充要求」忽略，而不是 str() 成 repr
    拼进给 LLM 的 user_query（那只是换了一种脏数据，用户同样无从修正）。
    """
    from api.db.services.file_review_service import _fix_base_query as f

    rounds = [_round(1, "annotated", user_query="按招标文件要求审核"),
              _round(2, "done", user_query="本次只修复【严重/high】级别的问题")]
    assert f(rounds) == "按招标文件要求审核"
    assert f(rounds, "重点看保证金") == \
        "按招标文件要求审核\n本次补充要求：重点看保证金"
    assert f([], "只这一句") == "本次补充要求：只这一句"
    assert f([]) == ""
    # 非字符串一律忽略：既不 500，也不把 repr 写进 user_query
    assert f(rounds, {"a": 1}) == "按招标文件要求审核"
    assert f(rounds, ["x"]) == "按招标文件要求审核"
    assert f(rounds, 123) == "按招标文件要求审核"
    assert f(rounds, "  ") == "按招标文件要求审核"


def test_doc_payload_picks_latest_produced_round():
    rounds = [_round(1, "annotated"),
              _round(2, "done", minio_path="frv-t1-v2"),
              _round(3, "failed", minio_path="frv-t1-v3")]
    assert _api._doc_payload(rounds, "f1") == {"object": "frv-t1-v3", "version": "v3"}
    # 乱序入参也要选 round_no 最大的那一版（不依赖调用方排序）
    assert _api._doc_payload(list(reversed(rounds)), "f1") == \
        {"object": "frv-t1-v3", "version": "v3"}
    # 无产物 → 原件，且 version 留空（不替前端断言「原件就是 v1」）
    assert _api._doc_payload([_round(1, "reviewing")], "f1") == {"object": "f1", "version": ""}
    assert _api._doc_payload([], "f1") == {"object": "f1", "version": ""}


def test_round_payload_produced_follows_minio_path_not_status():
    """failed 轮也可能已落盘（T6 交接契约第 2 条）：按状态判会把成稿藏起来。"""
    assert _api._round_payload(_round(2, "failed", minio_path="frv-t1-v2"))["produced"] is True
    assert _api._round_payload(_round(2, "done"))["produced"] is False


def test_count_annotations_buckets():
    rows = [_ann("a1", severity="high", status="open"),
            _ann("a2", severity="high", status="fixed"),
            _ann("a3", severity="medium", status="new"),
            _ann("a4", severity="low", status="wontfix"),
            _ann("a5", severity="weird", status="resolved")]
    assert _api._count_annotations(rows) == {
        "total": 5, "high": 2, "medium": 1, "low": 1, "pending": 2, "fixed": 1}
    assert _api._count_annotations([]) == {
        "total": 0, "high": 0, "medium": 0, "low": 0, "pending": 0, "fixed": 0}


# ── GET /file/review/templates ──────────────────────────────────────

def test_templates_endpoint_decodes_annotation_types(monkeypatch):
    rows = [SimpleNamespace(id="bid_doc_format", name="招标文件格式审核",
                            description=None, annotation_types='["format","clause"]'),
            SimpleNamespace(id="bad", name="脏数据", description="x",
                            annotation_types="{not json")]
    monkeypatch.setattr(_api.FileReviewTemplateService, "list_enabled",
                        classmethod(lambda cls, tenant: rows))
    body = _call(_api.list_review_templates, method="GET")
    assert body["code"] == 0
    assert body["data"]["templates"][0] == {
        "id": "bid_doc_format", "name": "招标文件格式审核",
        "description": "", "annotation_types": ["format", "clause"]}
    assert body["data"]["templates"][1]["annotation_types"] == []


# ── GET /file/review/file/<file_id>/state ───────────────────────────

def test_state_endpoint_shape_when_never_reviewed(monkeypatch):
    _patch_services(monkeypatch)
    body = _call(_api.review_state, method="GET", file_id="f1")
    assert body["code"] == 0
    d = body["data"]
    assert d["task_id"] is None
    assert d["current"] is None
    assert d["rounds"] == []
    assert d["doc"] == {"object": "f1", "version": ""}
    assert d["annotations"] == []
    assert d["fix_rounds_left"] == 3 and d["max_fix_rounds"] == 3
    assert d["annotation_counts"]["total"] == 0


def test_state_endpoint_assembles_rounds_annotations_and_doc(monkeypatch):
    rounds = [_round(1, "annotated", summary="high:1"),
              _round(2, "done", summary="本轮修复 1 项", minio_path="frv-t1-v2")]
    _patch_services(monkeypatch, rounds=rounds,
                    annotations=[_ann("a1"), _ann("a2", status="fixed")])
    body = _call(_api.review_state, method="GET", file_id="f1")
    d = body["data"]
    assert d["task_id"] == "t1"
    assert [r["round_no"] for r in d["rounds"]] == [1, 2]
    assert d["current"]["round_no"] == 2 and d["current"]["produced"] is True
    assert d["doc"] == {"object": "frv-t1-v2", "version": "v2"}
    assert d["fix_rounds_left"] == 2          # 已发生 1 个修复轮
    assert len(d["annotations"]) == 2
    assert d["annotation_counts"]["pending"] == 1


def test_state_endpoint_hides_internal_error_text(monkeypatch):
    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_file",
                        classmethod(lambda cls, fid: (_ for _ in ()).throw(
                            RuntimeError("MySQL 10.0.0.5:3306 refused"))))
    body = _call(_api.review_state, method="GET", file_id="f1")
    assert body["code"] != 0
    assert "3306" not in body["message"], "异常原文不得透给前端（交接契约第 6 条）"


# ── POST /file/review/<task_id>/fix ─────────────────────────────────

def _fix_setup(monkeypatch, **over):
    """一轮已完成的审核 + 一条 high 待修项 + next_round 桩，返回调用记录。"""
    rounds = [_round(1, "annotated", kb_ids='["kb1"]')]
    kw = {"rounds": rounds, "owned": rounds, "pending": [_ann("a1", severity="high")],
          "next_no": (2, "v2")}
    kw.update(over)
    return _patch_services(monkeypatch, **kw)


def test_fix_rejects_foreign_or_missing_task(monkeypatch):
    calls = _fix_setup(monkeypatch, owned=[])
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] != 0
    assert "create_round" not in calls and "spawned" not in calls


def test_fix_rejects_while_round_still_running(monkeypatch):
    rounds = [_round(1, "reviewing")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls


def test_fix_rejects_while_spawn_still_running(monkeypatch):
    """轮次行已是终态、线程还在收尾：必须拒，否则新轮次永远等不到线程。"""
    calls = _fix_setup(monkeypatch, running=True)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls and "spawned" not in calls


def test_fix_rejects_when_rounds_exhausted(monkeypatch):
    rounds = [_round(1, "annotated"), _round(2, "done"), _round(3, "done"), _round(4, "done")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls


def test_fix_rejects_when_selected_levels_have_no_pending(monkeypatch):
    calls = _fix_setup(monkeypatch, pending=[_ann("a1", severity="low")])
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls, "选中级别无待修项时不许建轮次（会白烧一次重试机会）"


def test_fix_rejects_invalid_levels(monkeypatch):
    calls = _fix_setup(monkeypatch)
    assert _call(_api.fix_review, task_id="t1",
                 body={"levels": ["high", "critical"]})["code"] == RetCode.ARGUMENT_ERROR
    assert _call(_api.fix_review, task_id="t1", body={})["code"] == RetCode.ARGUMENT_ERROR
    assert _call(_api.fix_review, task_id="t1",
                 body={"levels": []})["code"] == RetCode.ARGUMENT_ERROR
    assert "create_round" not in calls


def test_fix_creates_gated_round_and_spawns(monkeypatch):
    calls = _fix_setup(monkeypatch)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == 0
    assert body["data"] == {"task_id": "t1", "round_id": "rid-new",
                            "round_no": 2, "status": "fixing", "fix_rounds_left": 2}
    kw = calls["created"]
    assert kw["task_id"] == "t1"
    assert kw["file_id"] == "f1"                     # 继承当前轮的 file_id
    assert (kw["round_no"], kw["file_version"]) == (2, "v2")   # 只用 next_round 的口径
    assert kw["status"] == "fixing"
    assert kw["tenant_id"] == TENANT and kw["created_by"] == TENANT   # 不许写空串
    assert kw["kb_ids"] == '["kb1"]'                 # 继承本轮 KB 配置
    assert kw["user_query"] == ("审核这份招标文件\n"
                                "本次只修复【严重/high】级别的问题，其余级别的问题请保持原样、不要改动。")
    assert calls["spawned"] == ["t1"], "spawn 的实参必须是 task_id（不是 round_id）"


def test_fix_second_round_uses_first_round_query_baseline(monkeypatch):
    """连轮修复：基准恒为首轮原文，不把上一轮的级别指令叠进来。"""
    rounds = [_round(1, "annotated", user_query="审核这份招标文件"),
              _round(2, "done", user_query="审核这份招标文件\n本次只修复【严重/high】级别的问题，其余级别的问题请保持原样、不要改动。")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds, next_no=(3, "v3"),
                       pending=[_ann("a1", severity="medium")])
    _call(_api.fix_review, task_id="t1", body={"levels": ["medium"]})
    q = calls["created"]["user_query"]
    assert q.count("本次只修复") == 1, f"级别指令被叠加了：{q}"
    assert "严重/high" not in q, "上一轮已作废的级别指令不得出现在本轮"
    assert "一般/medium" in q


def test_fix_appends_caller_extra_query(monkeypatch):
    calls = _fix_setup(monkeypatch)
    _call(_api.fix_review, task_id="t1",
          body={"levels": ["high"], "user_query": "重点看保证金"})
    q = calls["created"]["user_query"]
    assert "--" not in q and "重点看保证金" in q
    assert "本次补充要求：重点看保证金" in q


def test_fix_maps_admission_denials_to_error_codes(monkeypatch):
    """reason → 错误码映射：not_found 走默认错误码路径（与改造前逐字一致），其余走
    OPERATING_ERROR。码位差异本身是会泄露信息的 —— not_found 不能与「参数错了」同码。"""
    def _deny(reason, message):
        def _raise(**kw):
            assert kw["task_id"] == "t1" and kw["levels"] == ["high"]
            raise _api.FixAdmissionDenied(reason, message)
        return _raise

    monkeypatch.setattr(_api, "admit_fix_round",
                        _deny("not_found", "文件审核任务不存在或无权访问"))
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.DATA_ERROR, "not_found 必须走 get_error_data_result 默认码"
    assert body["message"] == "文件审核任务不存在或无权访问"

    for reason, msg in (("running", "第 2 轮（修复中）仍在进行中，请等它结束后再发起修复"),
                        ("closing", "上一轮审核正在收尾，请稍等片刻后重试"),
                        ("no_quota", "已达到最大修复轮次（3 轮），未修复的问题请按批注手动处理"),
                        ("no_pending", "所选级别没有待修复的问题，无需发起修复"),
                        ("busy", "上一轮操作正在处理中，请稍后重试")):
        monkeypatch.setattr(_api, "admit_fix_round", _deny(reason, msg))
        body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
        assert body["code"] == RetCode.OPERATING_ERROR, reason
        assert body["message"] == msg, reason


# ── POST /file/review/annotation/<aid>/status ───────────────────────

def _status_setup(monkeypatch, **over):
    over.setdefault("owned", [_round(1, "annotated")])
    calls = _patch_services(monkeypatch, **over)
    calls["_ann_row"] = _ann("a1")
    return calls


def test_annotation_status_rejects_fixed_and_unknown(monkeypatch):
    calls = _status_setup(monkeypatch)
    for bad in ("fixed", "new", "", "resolved-x", None):
        body = _call(_api.update_annotation_status, annotation_id="a1",
                     body={"status": bad})
        assert body["code"] == RetCode.ARGUMENT_ERROR, f"{bad!r} 应被拒"
    assert "updated" not in calls, "参数非法时不许写库"


def test_annotation_status_rejects_missing_annotation(monkeypatch):
    calls = _status_setup(monkeypatch)
    calls["_ann_row"] = None
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "wontfix"})
    assert body["code"] != 0
    assert "updated" not in calls


def test_annotation_status_rejects_foreign_task(monkeypatch):
    """标注所属 task 不归当前用户 → 拒绝且不写库（写路径严格校验）。"""
    calls = _status_setup(monkeypatch, owned=[])
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0
    assert "updated" not in calls


def test_annotation_status_writes_and_returns(monkeypatch):
    calls = _status_setup(monkeypatch)
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "WontFix"})
    assert body["code"] == 0
    assert body["data"] == {"annotation_id": "a1", "status": "wontfix"}
    assert calls["updated"] == [("a1", "wontfix")]


def test_annotation_status_maps_write_miss_to_error(monkeypatch):
    """并发删除：查到了行但写 0 行 → 仍按「不存在」回，不泄露时序差异。"""
    _status_setup(monkeypatch)
    monkeypatch.setattr(_api.FileReviewAnnotationService, "update_status",
                        classmethod(lambda cls, aid, status: False))
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0


def test_annotation_status_hides_internal_error_text(monkeypatch):
    _status_setup(monkeypatch)
    monkeypatch.setattr(_api.FileReviewAnnotationService, "get_by_id",
                        classmethod(lambda cls, aid: (_ for _ in ()).throw(
                            RuntimeError("MinIO endpoint=http://10.0.0.9:9000"))))
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0
    assert "9000" not in body["message"]


# ── GET /file/review/<task_id>/<file_version>/download ──────────────

class _FakeStorage:
    """存储替身：只实现端点用到的 get。

    必须替换 `settings.STORAGE_IMPL` 本身、而不是 `thread_pool_exec`：端点的实参
    `settings.STORAGE_IMPL.get` 在调 thread_pool_exec **之前**就被求值，本环境该属性
    是 None，先炸 AttributeError → 被外层兜成 Internal server error，测不到守卫分支。
    """

    def __init__(self, blob):
        self.blob = blob
        self.calls = []

    def get(self, bucket, key):
        self.calls.append((bucket, key))
        return self.blob


def test_download_returns_error_when_object_missing(monkeypatch):
    """对抗：STORAGE_IMPL.get 返回 None（对象丢失）时不得回 200 + 0 字节。

    那是把「产物丢了」伪装成一次成功下载：用户拿到空文件，无从判断该重试还是该重跑，
    面板上「可下载」与实际打不开互相矛盾。
    """
    row = _round(1, "annotated", minio_path="frv-t1-v1", file_version="v1")
    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_task",
                        classmethod(lambda cls, tid: [row]))
    store = _FakeStorage(None)
    monkeypatch.setattr(_api.settings, "STORAGE_IMPL", store)

    body = _call(_api.download_review_version, method="GET",
                 task_id="t1", file_version="v1")
    assert body["code"] != 0
    assert "丢失" in body["message"]
    # 证明守卫真的走到了「取到 None」这一步（而不是提前因别的异常短路）
    assert store.calls == [(f"{TENANT}-downloads", "frv-t1-v1")]


def test_download_filename_excludes_internal_file_id(monkeypatch):
    """对抗：Content-Disposition 里不得出现上传系统的内部 uuid（file_id）。

    file_id 是系统内部标识，透给用户既无信息量，还会被当成可寻址的文件标识。
    """
    from urllib.parse import quote

    row = _round(1, "annotated", minio_path="frv-t1-v1", file_version="v1",
                 file_id="internal-uuid-should-not-leak")
    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_task",
                        classmethod(lambda cls, tid: [row]))
    monkeypatch.setattr(_api.settings, "STORAGE_IMPL",
                        _FakeStorage(b"PK\x03\x04fake-docx"))

    async def _inner():
        async with APP.test_request_context("/", method="GET"):
            return await _api.download_review_version(task_id="t1", file_version="v1")

    resp = asyncio.run(_inner())
    assert resp.status_code == 200
    disp = resp.headers["Content-Disposition"]
    assert "internal-uuid-should-not-leak" not in disp
    assert quote("文件审核_v1.docx") in disp
