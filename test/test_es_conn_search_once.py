"""_es_search_once 重试污染回归：timeout 必须进 body，不允许 kwarg 与 body 合并冲突。

背景：elasticsearch-py 9.x wrapper 会把 kwarg timeout 原地合并进 body dict；
es_conn.search 重试循环（ATTEMPT_TIME=2）在 ConnectionTimeout 后复用同一 query
dict，第二次调用即抛 "Received multiple values for 'timeout'"，检索静默降级为空。
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


def test_search_once_retry_same_dict_no_conflict():
    """重试复用同一 query dict：反复调用不得抛 ValueError（body timeout 幂等）。"""
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}}
    for _ in range(2):  # 模拟 ATTEMPT_TIME=2 的重试
        inst._es_search_once(["idx"], query, True)
    assert captured["body"]["timeout"] == "600s"
    # kwarg 与 body 不再有同名字段（9.x wrapper 的冲突条件）
    assert not (set(captured) & {"timeout"} & set(captured["body"]))


def test_search_once_preserves_existing_body_timeout():
    """caller body 已带 timeout 时不覆盖冲突（幂等覆盖为同值不炸即可）。"""
    inst, captured = _fake_conn()
    query = {"query": {"match_all": {}}, "timeout": "600s"}
    inst._es_search_once(["idx"], query, True)
    assert captured["body"]["timeout"] == "600s"
