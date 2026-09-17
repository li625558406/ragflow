"""TplFillTaskService 增量基线查询的口径守卫。

为什么单独成文件：``latest_done``（宽口径，服务「定位」）与
``latest_done_in_context``（收窄口径，服务「继承」）的区别**全部**体现在
WHERE 子句里。组件/工具层的桩件只能证明「调用方传了 context_id」，证明不了
「服务端真的拿它过滤」——一个把 ``flow_instance_id`` 条件删掉的改动，桩件测试
是绿的，生产却是 2026-09-17 demo03 事故重演。故这里直接对生成的查询条件断言。

手法：``@DB.connection_context()`` 与 ``@classmethod`` 叠在函数上，用
``__func__.__wrapped__`` 取回裸函数体，再把 ``cls.model`` 换成记录列引用的假
模型，从而在不连库的前提下看到 WHERE 里到底用了哪些列。
"""

import pytest

from api.db.services.template_fill_service import TplFillTaskService

COLUMNS = ("template_id", "tenant_id", "flow_instance_id", "status", "create_time")


class _Expr:
    def __init__(self, sql, used):
        self.sql, self.used = sql, used

    def __and__(self, other):
        return _Expr(f"({self.sql}) AND ({other.sql})", self.used | other.used)

    def __str__(self):
        return self.sql


class _Col:
    """列引用替身：`== 值` 产出一个可 `&` 组合、可 `str()` 的表达式，并记录列名。"""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Expr(f"{self.name}={other!r}", {self.name})

    def __hash__(self):
        return id(self)

    def desc(self):
        return _Expr(f"{self.name} DESC", set())


class _FakeModel:
    """既是模型（暴露列）又是查询记录器（select/where/order_by/first）。"""

    def __init__(self, row="ROW"):
        for c in COLUMNS:
            setattr(self, c, _Col(c))
        self.used = set()      # 本次查询实际引用到的列名
        self.expr = None
        self.order = None
        self._row = row

    def select(self):
        return self

    def where(self, expr):
        self.expr = expr
        self.used |= expr.used
        return self

    def order_by(self, *args):
        self.order = args
        return self

    def first(self):
        return self._row


@pytest.fixture
def fake_model(monkeypatch):
    fm = _FakeModel()
    monkeypatch.setattr(TplFillTaskService, "model", fm)
    return fm


def _in_context(template_id, tenant_id, context_id):
    fn = TplFillTaskService.latest_done_in_context.__func__.__wrapped__
    return fn(TplFillTaskService, template_id, tenant_id, context_id)


def _latest_done(template_id, tenant_id):
    fn = TplFillTaskService.latest_done.__func__.__wrapped__
    return fn(TplFillTaskService, template_id, tenant_id)


# ---------- 收窄口径：latest_done_in_context ----------

def test_in_context_filters_by_flow_instance_id(fake_model):
    """核心守卫：WHERE 必须含 flow_instance_id（收窄），否则跨流程串值。"""
    assert _in_context("tpl1", "ten1", "ctx-1") == "ROW"
    assert "flow_instance_id" in fake_model.used, \
        "收窄口径丢了 flow_instance_id 过滤 = demo03 事故重演"
    assert fake_model.used == {"template_id", "tenant_id", "flow_instance_id", "status"}
    assert "'ctx-1'" in str(fake_model.expr)


def test_in_context_empty_context_returns_none_without_query(fake_model):
    """空上下文必须**在查询之前**返回 None（安全默认：无法证明边界则不继承）。"""
    assert _in_context("tpl1", "ten1", "") is None
    assert fake_model.expr is None and fake_model.used == set(), "不得发起查询"


def test_in_context_none_context_returns_none_without_query(fake_model):
    assert _in_context("tpl1", "ten1", None) is None
    assert fake_model.expr is None, "不得发起查询"


@pytest.mark.parametrize("blank", ["", None, 0, [], {}, False])
def test_in_context_all_falsy_contexts_degrade_to_full_fill(fake_model, blank):
    """对抗性：任何 falsy 上下文都不得退化成「不限上下文」的宽查。"""
    assert _in_context("tpl1", "ten1", blank) is None
    assert fake_model.expr is None


def test_in_context_whitespace_context_is_not_blank(fake_model):
    """反例守卫：空格串是 truthy，会真去查——所以**节点侧必须 strip 后再传**
    （见 test_agent_fill_template_component.py::test_baseline_context_id_is_stripped_and_passed）。
    这里固化服务层不 trim 的分工，避免后人误以为服务层兜了底而删掉节点侧 strip。"""
    assert _in_context("tpl1", "ten1", "   ") == "ROW"
    assert fake_model.expr is not None, "空格串在服务层不视为空（由调用方负责 strip）"


# ---------- 宽口径：latest_done（刻意不收窄） ----------

def test_latest_done_stays_broad_for_locate(fake_model):
    """latest_done 服务「定位」（用户说「把 XX 改成 YY」时找最近成稿），刻意不收窄。
    与 in_context 的差异正是它存在的理由——若有人「顺手」合并两者，此断言失败。"""
    assert _latest_done("tpl1", "ten1") == "ROW"
    assert "flow_instance_id" not in fake_model.used, \
        "latest_done 是定位口径，跨会话找到用户刚填的稿正是期望行为，不得收窄"
    assert fake_model.used == {"template_id", "tenant_id", "status"}
