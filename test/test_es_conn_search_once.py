"""_es_search_once kwarg 重试污染回归：所有会进 body 的参数必须以 body 传，禁止 kwarg。

背景：elasticsearch-py 9.x wrapper 会把 kwarg 原地合并进 body dict（body[key]=kwarg）；
es_conn.search 重试循环（ATTEMPT_TIME=2）在 ConnectionTimeout 后复用同一 query dict，
第二次调用即抛 "Received multiple values for '...'"，检索静默降级为空。
已修 timeout（09-08 上半场）与 track_total_hits（同族，TemplateFill 检索重试实测踩中）；
_source kwarg 一并移除（True 本就是 ES 默认值，且与 body 内字段列表潜在冲突）。
另：同租户多 KB 检索产生重复索引名，KNN 开销 N 倍放大，入口必须去重保序。
"""
import sys
import types

sys.path.insert(0, ".")


def _fake_conn():
    """构造绕过连接池的 ESConnection 实例（__new__ 跳过单例/连接；es 替换为捕获桩）。"""
    from common import settings as _settings  # noqa: F401 — 先行 import 破循环依赖

    from rag.utils import es_conn

    wrapper = es_conn.ESConnection  # @singleton 返回闭包函数
    cls = next(c.cell_contents for c in wrapper.__closure__
               if isinstance(c.cell_contents, type))  # 闭包里捕获的真实类
    inst = cls.__new__(cls)
    captured = {}
    inst.es = types.SimpleNamespace(search=lambda **kw: captured.update(kw) or {"hits": {"hits": []}})
    return inst, captured


def test_search_once_sets_timeout_in_body():
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}, "knn": {"field": "q_1_vec", "query_vector": [0.1]}}
    res = inst._es_search_once(["idx"], query, True)
    assert res == {"hits": {"hits": []}}
    assert captured["body"]["timeout"] == "600s"      # timeout 进 body
    assert "timeout" not in captured                  # kwarg 不再传 timeout（合并冲突源）


def test_search_once_track_total_hits_in_body_no_kwarg():
    """track_total_hits 进 body、不再作为 kwarg 传递（重试复用 dict 不抛多值冲突）。"""
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}}
    inst._es_search_once(["idx"], query, True)
    assert captured["body"]["track_total_hits"] is True
    assert "track_total_hits" not in captured


def test_search_once_no_source_kwarg():
    """_source kwarg 必须移除（True 为 ES 默认值；与 body 内字段列表是潜在冲突源）。"""
    inst, captured = _fake_conn()
    inst._es_search_once(["idx"], {"query": {"match_all": {}}, "_source": ["doc_id"]}, True)
    assert "_source" not in captured


def test_search_once_retry_same_dict_no_conflict():
    """重试复用同一 query dict：反复调用不得抛 ValueError（body 注入幂等）。"""
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}}
    for _ in range(2):  # 模拟 ATTEMPT_TIME=2 的重试
        inst._es_search_once(["idx"], query, True)
    assert captured["body"]["timeout"] == "600s"
    assert captured["body"]["track_total_hits"] is True
    # kwarg 与 body 不再有同名字段（9.x wrapper 的冲突条件）
    assert not (set(captured) & {"timeout", "track_total_hits", "_source"} & set(captured["body"]))


def test_search_once_dedups_index_names_keep_order():
    """同租户多 KB → 同一索引名重复 N 次：去重且保持首现顺序。"""
    inst, _ = _fake_conn()
    inst._es_search_once(
        ["ragflow_t1", "ragflow_t2", "ragflow_t1", "ragflow_t3", "ragflow_t2"], {"query": {}}, True)
    # 通过 es.search 的 index kwarg 校验（桩捕获整个 kwargs）
    # captured 里 index 是列表
    conn_captured = {}
    inst.es = types.SimpleNamespace(search=lambda **kw: conn_captured.update(kw) or {"hits": {"hits": []}})
    inst._es_search_once(
        ["ragflow_t1", "ragflow_t2", "ragflow_t1", "ragflow_t3", "ragflow_t2"], {"query": {}}, True)
    assert conn_captured["index"] == ["ragflow_t1", "ragflow_t2", "ragflow_t3"]


def test_search_once_preserves_existing_body_timeout():
    """caller body 已带 timeout 时不覆盖冲突（幂等覆盖为同值不炸即可）。"""
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}, "timeout": "600s"}
    inst._es_search_once(["idx"], query, True)
    assert captured["body"]["timeout"] == "600s"
