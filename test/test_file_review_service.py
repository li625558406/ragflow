"""文件审核 Service 层单测（T2：CRUD + 多轮判定 + 标注状态流转）。

测试直连**真实 MySQL**（非 sqlite、非 mock）：
- module 级 autouse fixture 在跑测前后各清理一次；所有测试行都带唯一前缀 PFX，
  清理只按该前缀删，绝不误伤 5 套预置模板与线上数据；
- 禁止调用 migrate_db() / init_database_tables()（前者含破坏性 DML：UPDATE 真实
  会话表、探针失败会重建 bid_enterprise_cache），这里只复刻「建表 + 补预置」的
  幂等片段（同 test_file_review_db.py 的做法）。

契约锁定（T1 审查踩坑）：
- create_time/update_time 是 13 位毫秒 BigIntegerField，由 BaseModel.insert /
  _normalize_data 自动维护，本层不得手写 —— test_audit_timestamps_autofilled_by_framework
  用真实写入结果锁死这一点（手写 datetime 会退化成零值且 is not None 仍过）。
"""
import json
from types import SimpleNamespace

import peewee
import pytest

from api.db.db_models import (
    _PRESET_REVIEW_TEMPLATES,
    DB,
    FileReviewAnnotation,
    FileReviewRound,
    FileReviewTemplate,
    _seed_file_review_templates,
)
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
)
from api.utils.json_encode import json_dumps

# 唯一前缀：LIKE 里 `_` 是单字符通配，但真实 id（uuid 十六进制 / 预置 slug）都不含
# "test" 片段，不存在误命中窗口
PFX = "__test_fr_svc__"

# ORDER BY id 探针模板 id：刻意不以 PFX 开头（首字符 'A' 的 ASCII 小于 PFX 的 '_'
# 与预置 slug 的小写首字母），用来证明 list_enabled 真的按 id 升序排 —— 见
# test_list_enabled_templates。清理时必须单独点名删除。
_ORDER_PROBE_ID = "A_tpl_order_probe"


def _cleanup():
    """只按测试前缀（外加排序探针）清理，幂等（表不存在时由 fixture 先建表）。"""
    FileReviewAnnotation.delete().where(FileReviewAnnotation.task_id.startswith(PFX)).execute()
    FileReviewAnnotation.delete().where(FileReviewAnnotation.file_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.task_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.file_id.startswith(PFX)).execute()
    FileReviewTemplate.delete().where(FileReviewTemplate.id.startswith(PFX)).execute()
    FileReviewTemplate.delete().where(FileReviewTemplate.id == _ORDER_PROBE_ID).execute()


@pytest.fixture(scope="module", autouse=True)
def _tables_presets_and_cleanup():
    DB.connect(reuse_if_open=True)
    try:
        for m in (FileReviewTemplate, FileReviewRound, FileReviewAnnotation):
            if not m.table_exists():
                m.create_table(safe=True)
        if FileReviewTemplate.select().where(
                FileReviewTemplate.tenant_id == "").count() < len(_PRESET_REVIEW_TEMPLATES):
            _seed_file_review_templates()
        _cleanup()
        yield
    finally:
        # try/finally 而非裸顺序：_cleanup() 抛异常时连接不能让本会话后续用例
        # 继续复用坏状态 / 泄漏（M2）。
        try:
            _cleanup()
        finally:
            DB.close()


def _mk_round(task_id, round_no, status, *, file_id=PFX + "-file", file_version="v1", **kw):
    """走 service 建轮次（审计字段由框架填，这里不传任何时间字段）。

    tenant_id / created_by 用 setdefault 而非写死：get_owned_task 的用例必须能造出
    「归某租户 / 租户为空」的轮次行，写死会让 tenant_id="t1" 撞成
    `got multiple values for keyword argument`。默认值与写死时完全一致，
    既有用例行为不变。
    """
    kw.setdefault("tenant_id", "")
    kw.setdefault("created_by", "u")
    return FileReviewRoundService.create_round(
        task_id=task_id, file_id=file_id, round_no=round_no,
        template_id="bid_doc_format", user_query="审核请求",
        file_version=file_version, status=status, **kw)


def _round_row(rid):
    return FileReviewRoundService.get_by_id(rid)


def _mk_ann(*, round_id, task_id, file_id, status="open", file_version="v1", **kw):
    """走 service 建标注（anchor 用项目统一 JSON 封装序列化）。"""
    return FileReviewAnnotationService.create(
        round_id=round_id, task_id=task_id, file_id=file_id, file_version=file_version,
        anchor=json_dumps({"p_hash": 0, "offset": 1}), matched_text="foo", ann_type="format",
        severity="high", issue="x", suggestion="y", source="ai", status=status,
        tenant_id="", created_by="u", **kw)


# ── 计划给定的 3 个基线用例 ────────────────────────────────────────────
def test_list_enabled_templates():
    # 探针：id 首字符 'A'(0x41) 小于 PFX 的 '_'(0x5F) 与预置 slug 的小写首字母，
    # 因此若 ORDER BY id 被删，MySQL 按 PK 序返回时它绝不可能排第一 -> 断言必挂。
    with DB.connection_context():
        FileReviewTemplate.create(
            id=_ORDER_PROBE_ID, name="探针", system_prompt="x",
            user_prompt_template="{user_query}{file_excerpt}{references}",
            tenant_id="", created_by="u")
        rows = FileReviewTemplateService.list_enabled()
    assert len(rows) >= 5
    assert all(r.enabled == 1 for r in rows), "list_enabled 必须过滤 enabled != 1"
    ids = [r.id for r in rows]
    assert ids == sorted(ids), "按 id 升序（前端下拉顺序需稳定）"
    assert ids[0] == _ORDER_PROBE_ID, "ORDER BY id 必须真实生效（探针 id 排序最前）"


def test_upsert_annotations_status_transition():
    tid, fid = f"{PFX}t_trans", f"{PFX}f_trans"
    with DB.connection_context():
        ann_id = FileReviewAnnotationService.create(
            round_id=f"{PFX}r1", task_id=tid, file_id=fid, file_version="v1",
            anchor=json_dumps({"p_hash": 0}), matched_text="foo", ann_type="format",
            severity="high", issue="x", suggestion="y", source="ai", status="open",
            tenant_id="", created_by="u",
        )
        assert FileReviewAnnotationService.update_status(ann_id, "fixed") is True
        ann = FileReviewAnnotationService.get_by_id(ann_id)
    assert ann is not None
    assert ann.status == "fixed"


# ── 1. 边界：查不到时的返回值必须明确（None / 0 / []，不是裸抛也不是 None 列表） ──
def test_lookup_misses_are_none_zero_and_empty_list():
    with DB.connection_context():
        assert FileReviewTemplateService.get_by_id(f"{PFX}no_such_tpl") is None
        assert FileReviewRoundService.get_by_id(f"{PFX}no_such_round") is None
        assert FileReviewAnnotationService.get_by_id(f"{PFX}no_such_ann") is None
        assert FileReviewRoundService.get_by_task(f"{PFX}no_such_task") == []
        rows = FileReviewAnnotationService.list_by_file_version(f"{PFX}no_file", "v1")
    assert rows is not None and rows == [], "无数据必须是空列表（None 会被上游直接 len() 崩掉）"


def test_get_by_id_empty_id_short_circuits_without_sql(monkeypatch):
    """空 id 短路：返回值对「有无短路」不敏感（无短路时 get(id == "") 抛
    DoesNotExist 也会被 except 转成 None），必须改为断言**零 SQL** —— 去掉
    `if not pid: return None` 后这里才会真失败。

    DB.execute(query) 是 peewee 所有 SQL 的唯一出口（execute_sql 仅由它调用），
    在已建立的连接内（fixture 已 connect，connection_context 复用）计数即可。
    """
    calls = []
    orig = DB.execute

    def spy(query, *a, **k):
        calls.append(query)
        return orig(query, *a, **k)

    monkeypatch.setattr(DB, "execute", spy)
    with DB.connection_context():
        assert FileReviewRoundService.get_by_id("") is None
        assert FileReviewAnnotationService.get_by_id("") is None
        assert FileReviewAnnotationService.get_by_id(None) is None
        assert FileReviewTemplateService.get_by_id("") is None
    assert calls == [], "空 id/None 必须短路，不得触库"


def test_update_status_on_missing_row_returns_false_without_raising():
    with DB.connection_context():
        assert FileReviewRoundService.update_status(f"{PFX}ghost_round", "done") is False
        assert FileReviewAnnotationService.update_status(f"{PFX}ghost_ann", "fixed") is False


# ── 3. 多 task 隔离：三个多轮判定方法都不许串号 ─────────────────────────
def test_round_queries_are_isolated_between_tasks():
    ta, tb = f"{PFX}t_iso_a", f"{PFX}t_iso_b"
    with DB.connection_context():
        _mk_round(ta, 1, "done", file_id=f"{PFX}f_iso_a")
        _mk_round(ta, 2, "annotated", file_id=f"{PFX}f_iso_a", file_version="v2")
        _mk_round(tb, 4, "done", file_id=f"{PFX}f_iso_b", file_version="v4")
        assert [r.round_no for r in FileReviewRoundService.get_by_task(ta)] == [1, 2]
        assert [r.round_no for r in FileReviewRoundService.get_by_task(tb)] == [4]
        # get_by_file 只认自己文件的轮次，不会把另一个 task 的轮次并进来
        assert [r.round_no for r in FileReviewRoundService.get_by_file(f"{PFX}f_iso_a")] == [1, 2]
        assert [r.round_no for r in FileReviewRoundService.get_by_file(f"{PFX}f_iso_b")] == [4]
        assert FileReviewRoundService.get_by_file(f"{PFX}f_iso_ghost") == []


def test_get_by_task_orders_ascending_by_round_no():
    """乱序插入也必须按 round_no 升序返回（T9 fix 端点靠 [-1] 取当前轮）。"""
    tid = f"{PFX}t_order"
    with DB.connection_context():
        _mk_round(tid, 3, "done", file_id=f"{PFX}f_order", file_version="v3")
        _mk_round(tid, 1, "done", file_id=f"{PFX}f_order", file_version="v1")
        rows = FileReviewRoundService.get_by_task(tid)
    assert [r.round_no for r in rows] == [1, 3]


# ── 4. list_open_or_new_for_next_round 的真实语义（本轮 + 仅 open/new） ──
def test_list_open_or_new_for_next_round_scopes_round_and_status():
    """对抗原计划实现：子查询只按 task_id 取 round_id（漏 round_no 过滤），
    会把上一轮的 open 标注也喂给下一轮。这里锁死正确语义。"""
    tid = f"{PFX}t_scope"
    fid = f"{PFX}f_scope"
    with DB.connection_context():
        r1 = _mk_round(tid, 1, "done", file_id=fid, file_version="v1")
        r2 = _mk_round(tid, 2, "annotated", file_id=fid, file_version="v2")
        # round 1：一条历史未处理（不该再喂给 round 2）
        _mk_ann(round_id=r1, task_id=tid, file_id=fid, status="open", file_version="v1")
        # round 2：open/new 各一条（要），fixed/wontfix 各一条（不要）
        _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="open", file_version="v2")
        _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="new", file_version="v2")
        _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="fixed", file_version="v2")
        _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="wontfix", file_version="v2")

        got2 = FileReviewAnnotationService.list_open_or_new_for_next_round(tid, 2)
        got1 = FileReviewAnnotationService.list_open_or_new_for_next_round(tid, 1)
        got_missing = FileReviewAnnotationService.list_open_or_new_for_next_round(tid, 99)

    assert sorted(a.status for a in got2) == ["new", "open"]
    assert all(a.round_id == r2 for a in got2), "必须严格限定在本轮（round_no=2）"
    assert [a.status for a in got1] == ["open"] and all(a.round_id == r1 for a in got1)
    assert got_missing == [], "该轮次不存在时返回空列表"


def test_list_open_or_new_is_isolated_between_tasks():
    ta, tb = f"{PFX}t_onn_a", f"{PFX}t_onn_b"
    with DB.connection_context():
        ra = _mk_round(ta, 1, "annotated", file_id=f"{PFX}f_onn_a")
        rb = _mk_round(tb, 1, "annotated", file_id=f"{PFX}f_onn_b")
        aa = _mk_ann(round_id=ra, task_id=ta, file_id=f"{PFX}f_onn_a")
        ab = _mk_ann(round_id=rb, task_id=tb, file_id=f"{PFX}f_onn_b")
        got_a = FileReviewAnnotationService.list_open_or_new_for_next_round(ta, 1)
        got_b = FileReviewAnnotationService.list_open_or_new_for_next_round(tb, 1)
    assert [a.id for a in got_a] == [aa], "同 round_no 不同 task 不得互串"
    assert [a.id for a in got_b] == [ab]


# ── 5. list_by_file_version：review-panel 加载语义 ──────────────────────
def test_list_by_file_version_task_id_none_returns_all_across_tasks():
    fid = f"{PFX}f_lbv"
    ta, tb = f"{PFX}t_lbv_a", f"{PFX}t_lbv_b"
    with DB.connection_context():
        ra = _mk_round(ta, 1, "done", file_id=fid)
        rb = _mk_round(tb, 1, "done", file_id=fid)
        a1 = _mk_ann(round_id=ra, task_id=ta, file_id=fid)
        a2 = _mk_ann(round_id=ra, task_id=ta, file_id=fid)
        b1 = _mk_ann(round_id=rb, task_id=tb, file_id=fid)
        _mk_ann(round_id=ra, task_id=ta, file_id=fid, file_version="v2")  # 别的版本

        all_rows = FileReviewAnnotationService.list_by_file_version(fid, "v1")
        a_rows = FileReviewAnnotationService.list_by_file_version(fid, "v1", ta)
        b_rows = FileReviewAnnotationService.list_by_file_version(fid, "v1", tb)

    # 不能断言精确 ids 顺序：生产形态（同机 Docker MySQL）两条 INSERT 可落在同一
    # 毫秒，MySQL 对并列 ORDER BY key 不保证稳定序。用集合锁内容 + 时间戳非降序锁排序。
    assert {r.id for r in all_rows} == {a1, a2, b1}, "task_id=None 必须跨 task 取全量"
    ts = [r.create_time for r in all_rows]
    assert ts == sorted(ts), "list_by_file_version 必须按 create_time 非降序"
    assert sorted(r.id for r in a_rows) == sorted([a1, a2]), "传 task_id 必须收窄"
    assert [r.id for r in b_rows] == [b1]


# ── 6. list_enabled 的 enabled / 租户过滤 ───────────────────────────────
def test_list_enabled_filters_disabled_and_foreign_tenant():
    other_tenant = f"{PFX}other_tenant"
    disabled_id, other_id = f"{PFX}tpl_disabled", f"{PFX}tpl_other_tenant"
    with DB.connection_context():
        FileReviewTemplate.create(
            id=disabled_id, name="停用模板", system_prompt="s",
            user_prompt_template="{user_query}{file_excerpt}{references}",
            enabled=0, tenant_id="")
        FileReviewTemplate.create(
            id=other_id, name="他租户模板", system_prompt="s",
            user_prompt_template="{user_query}{file_excerpt}{references}",
            enabled=1, tenant_id=other_tenant)

        ids_default = {r.id for r in FileReviewTemplateService.list_enabled()}
        ids_default_explicit = {r.id for r in FileReviewTemplateService.list_enabled("")}
        ids_other = {r.id for r in FileReviewTemplateService.list_enabled(other_tenant)}

    assert disabled_id not in ids_default, "enabled=0 不得出现在可用列表"
    assert other_id not in ids_default, "他租户模板不得混入默认（系统预置）列表"
    assert ids_default == ids_default_explicit, "tenant_id 默认参数必须等价于显式 ''"
    assert other_id in ids_other, "传自身租户应能看到本租户模板"
    assert disabled_id not in ids_other, "enabled=0 对他租户同样不可见"
    assert {"bid_doc_format", "bid_qualification"} <= ids_other, "预置模板对所有租户可见"


# ── 7. 重复轮次：无唯一约束这一事实必须被锁定（T9 并发/重试会踩） ────────
def test_create_round_allows_duplicate_task_round_no():
    """（task_id, round_no) 目前只有普通复合索引、**无唯一约束**：重复插入会静默产生
    两行。此用例锁定现状（而非期望），若后续加唯一约束需同步改此用例并评估 T9
    finish 端点按 [-1] 取当前轮的歧义。"""
    tid = f"{PFX}t_dup"
    with DB.connection_context():
        _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_dup")
        _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_dup")
        rows = FileReviewRoundService.get_by_task(tid)
    assert len(rows) == 2, "无唯一约束 → 允许两行；加约束前此处必须保持 2"


# ── 8. 必填字段缺失：异常必须自然抛出，不得被 service 吞掉 ──────────────
def test_not_null_violation_is_not_swallowed():
    tid = f"{PFX}t_notnull"
    with DB.connection_context():
        with pytest.raises(peewee.IntegrityError):
            _mk_round(tid, 1, None, file_id=f"{PFX}f_notnull")  # status NOT NULL
        with pytest.raises(peewee.IntegrityError):
            FileReviewAnnotationService.create(
                round_id="r", task_id=tid, file_id=f"{PFX}f_notnull", file_version="v1",
                anchor=json_dumps({}), matched_text="", ann_type="format", severity="high",
                issue=None, suggestion="", source="ai",  # issue NOT NULL
                tenant_id="", created_by="u")
        # 失败后连接仍可用（无残留坏事务）
        assert FileReviewRoundService.get_by_task(tid) == []


# ── 9. 状态枚举白名单不在本层：非法值照写，契约显式锁定 ─────────────────
def test_update_status_writes_arbitrary_value_locks_contract():
    """本层是纯 CRUD：round.status / annotation.status 列是 varchar(16) 且无 DB 约束，
    因此任意字符串都会写入。枚举白名单与状态机由 T4 patcher / T6 executor / T9 API
    负责（那几层才有「非法转移该怎么办」的上下文：拒绝、降级还是标 failed）。
    此用例把「本层不管」这一分工锁死——若未来有人在本层加白名单，必须先改这里。"""
    tid = f"{PFX}t_bogus"
    bogus_round = "bogus_status_ok"     # 15 字符，未超 varchar(16)
    bogus_ann = "bogus_ann_ok"          # 12 字符
    assert len(bogus_round) <= 16 and len(bogus_ann) <= 16
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_bogus")
        aid = _mk_ann(round_id=rid, task_id=tid, file_id=f"{PFX}f_bogus")
        assert FileReviewRoundService.update_status(rid, bogus_round) is True
        assert FileReviewAnnotationService.update_status(aid, bogus_ann) is True
        assert FileReviewRoundService.get_by_id(rid).status == bogus_round
        assert FileReviewAnnotationService.get_by_id(aid).status == bogus_ann


# ── 9b. update_status 的 **extra 合入路径（T6 执行器靠它写 summary/minio_path/error） ──
def test_update_status_merges_extra_fields():
    tid = f"{PFX}t_extra"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_extra")
        ok = FileReviewRoundService.update_status(
            rid, "annotated", summary="整轮总结", minio_path="review/v2.docx", error=None)
        row = FileReviewRoundService.get_by_id(rid)
    assert ok is True and row.summary == "整轮总结" and row.minio_path == "review/v2.docx"
    assert row.status == "annotated"


def test_update_status_same_value_in_same_ms_still_returns_true(monkeypatch):
    """C1 回归：affected rows = 实际变化行数（连接未开 CLIENT_FOUND_ROWS），同毫秒
    同值写会得 0，但行仍存在，必须 True —— 否则 T9 finish 幂等重试会误收 404。"""
    import api.db.db_models as m
    monkeypatch.setattr(m, "current_timestamp", lambda *a, **k: 1_700_000_000_000)
    tid = f"{PFX}t_samems"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_samems")
        assert FileReviewRoundService.update_status(rid, "reviewing") is True
        assert FileReviewRoundService.update_status(rid, "reviewing") is True, \
            "同毫秒同值写：行仍在，必须 True（affected=0 不代表行不存在）"
        assert FileReviewRoundService.get_by_id(rid) is not None
        aid = _mk_ann(round_id=rid, task_id=tid, file_id=f"{PFX}f_samems", status="open")
        assert FileReviewAnnotationService.update_status(aid, "open") is True
        assert FileReviewAnnotationService.update_status(aid, "open") is True


def test_list_open_or_new_covers_duplicate_round_rows():
    """同 (task_id, round_no) 无唯一约束可产生多行（T9 并发重试），IN 必须覆盖全部。"""
    tid, fid = f"{PFX}t_dupopen", f"{PFX}f_dupopen"
    with DB.connection_context():
        r1 = _mk_round(tid, 2, "annotated", file_id=fid, file_version="v2")
        r2 = _mk_round(tid, 2, "annotated", file_id=fid, file_version="v2")
        a1 = _mk_ann(round_id=r1, task_id=tid, file_id=fid, status="open", file_version="v2")
        a2 = _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="new", file_version="v2")
        got = FileReviewAnnotationService.list_open_or_new_for_next_round(tid, 2)
    assert {x.id for x in got} == {a1, a2}, "round_id.in_() 必须覆盖同轮次的多行"


def test_str_fields_are_clamped_to_column_width():
    """I1 回归：varchar 列超长会被 MySQL 静默截成半截枚举值（非严格模式不报错）。
    本层不做白名单，但不得把半截值落库 —— 断言落库值等于按列宽截断后的结果。"""
    tid, fid = f"{PFX}t_clamp", f"{PFX}f_clamp"
    long_sev = "critical_blocking_需要人工确认"  # > varchar(16)
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=fid)
        aid = FileReviewAnnotationService.create(
            round_id=rid, task_id=tid, file_id=fid, file_version="v1",
            anchor=json_dumps({"p_hash": 0}), matched_text="m", ann_type="format",
            severity=long_sev, issue="i", suggestion="", source="ai", status="open",
            tenant_id="", created_by="u")
        row = FileReviewAnnotationService.get_by_id(aid)
    assert row.severity == long_sev[:16], "超长 severity 必须被显式钳制，而非静默半截"


def test_aggregates_short_circuit_on_empty_task_id():
    """M1：task_id="" 的行可落库，不得被当成「某个任务」参与聚合。"""
    with DB.connection_context():
        # 先塞一行 task_id="" 的脏数据，若聚合不短路就会被它命中
        rid = _mk_round("", 7, "done", file_id=f"{PFX}f_empty_tid")
        _mk_ann(round_id=rid, task_id="", file_id=f"{PFX}f_empty_tid", status="open")
        assert FileReviewAnnotationService.list_open_or_new_for_next_round("", 7) == []
        assert FileReviewAnnotationService.list_open_or_new_for_next_round(None, 7) == []


# ── 10. 审计字段由框架接管（修正 1 的防回归） ───────────────────────────
def test_audit_timestamps_autofilled_by_framework():
    """对抗：service 若手写 create_time=datetime.now()，BIGINT 列会落成零值/报错。
    这里断言落库值确实是 13 位毫秒整数、_date 由 _time 派生。"""
    tid = f"{PFX}t_audit"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_audit")
        aid = _mk_ann(round_id=rid, task_id=tid, file_id=f"{PFX}f_audit")
        rrow = FileReviewRoundService.get_by_id(rid)
        arow = FileReviewAnnotationService.get_by_id(aid)
        for row in (rrow, arow):
            assert isinstance(row.create_time, int) and row.create_time > 10 ** 12, \
                "create_time 必须是框架写入的毫秒时间戳"
            assert isinstance(row.update_time, int) and row.update_time > 10 ** 12
            assert row.create_date is not None and row.update_date is not None, \
                "_date 应由框架从 _time 派生"


def test_update_status_refreshes_update_time(monkeypatch):
    """冻结时间源并推进 5ms，断言 update_time 精确等于新时间戳。

    原来的 `after >= before` 对「是否刷新」不敏感：若 _normalize_data 不再刷
    update_time，before == after 照样通过 —— 而这正是本用例要防的回归。
    """
    import api.db.db_models as m
    t = {"v": 1_700_000_000_000}
    monkeypatch.setattr(m, "current_timestamp", lambda *a, **k: t["v"])
    tid = f"{PFX}t_refresh"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=f"{PFX}f_refresh")
        before = FileReviewRoundService.get_by_id(rid).update_time
        t["v"] += 5
        FileReviewRoundService.update_status(rid, "annotated")
        after = FileReviewRoundService.get_by_id(rid).update_time
    assert after == before + 5, "update_time 必须被框架刷新为新的毫秒时间戳"


# ── 11. 默认值与可选字段 ───────────────────────────────────────────────
def test_annotation_defaults_and_optional_fields():
    tid, fid = f"{PFX}t_defaults", f"{PFX}f_defaults"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=fid)
        aid = FileReviewAnnotationService.create(
            round_id=rid, task_id=tid, file_id=fid, file_version="v1",
            anchor=json_dumps({"sheet": "S1", "cell": "A1"}), matched_text="m",
            ann_type="clause", severity="low", issue="i", suggestion="",
            source="manual", tenant_id="", created_by="u")  # status 省略
        row = FileReviewAnnotationService.get_by_id(aid)
    assert row.status == "open", "status 默认必须是 open"
    assert row.prev_annotation_id is None, "prev_annotation_id 默认 None（首轮无前身）"


def test_annotation_prev_annotation_id_chain_persisted():
    """多轮标注链：新一轮标注用 prev_annotation_id 指回上一轮同类问题。"""
    tid, fid = f"{PFX}t_chain", f"{PFX}f_chain"
    with DB.connection_context():
        r1 = _mk_round(tid, 1, "done", file_id=fid, file_version="v1")
        r2 = _mk_round(tid, 2, "reviewing", file_id=fid, file_version="v2")
        old = _mk_ann(round_id=r1, task_id=tid, file_id=fid, status="fixed")
        new = _mk_ann(round_id=r2, task_id=tid, file_id=fid, status="new",
                      file_version="v2", prev_annotation_id=old)
        row = FileReviewAnnotationService.get_by_id(new)
    assert row.prev_annotation_id == old
    assert row.round_id == r2


# ── 12. kb_ids 持久化 + 待处理标注跨轮取全 + 按轮幂等删除（T6 executor 依赖） ──
def test_create_round_persists_normalized_kb_ids():
    tid = PFX + 'kbids'
    rid = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=1, template_id='',
        user_query='q', file_version='v1', status='reviewing',
        tenant_id=PFX, kb_ids=['kb-1', 'kb-2'],
    )
    assert json.loads(_round_row(rid).kb_ids) == ['kb-1', 'kb-2']
    rid2 = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=2, template_id='',
        user_query='q', file_version='v1', status='reviewing', tenant_id=PFX,
    )
    assert _round_row(rid2).kb_ids is None
    rid3 = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=3, template_id='',
        user_query='q', file_version='v1', status='reviewing',
        tenant_id=PFX, kb_ids=['kb-9'],
    )
    assert _round_row(rid3).kb_ids == '["kb-9"]'


def test_list_pending_by_task_is_task_wide_not_round_scoped():
    """修复轮不产标注；按 round_no 圈定会让第 3 轮取到空集而静默不修。"""
    tid = PFX + 'pending'
    r_old = _mk_round(tid, 1, 'annotated')
    r_cur = _mk_round(tid, 2, 'fixing')
    for r, st in ((r_old, 'open'), (r_old, 'fixed'), (r_cur, 'new'), (r_cur, 'wontfix')):
        FileReviewAnnotationService.create(
            round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
            anchor='{}', matched_text='t', ann_type='format', severity='low',
            issue='i', suggestion='', source='ai', status=st, tenant_id=PFX)
    got = [a.status for a in FileReviewAnnotationService.list_pending_by_task(tid)]
    assert sorted(got) == ['new', 'open']
    assert FileReviewAnnotationService.list_pending_by_task('') == []


def test_delete_by_round_only_removes_that_round():
    tid = PFX + 'delround'
    r1, r2 = _mk_round(tid, 1, 'reviewing'), _mk_round(tid, 2, 'reviewing')
    for r in (r1, r2):
        FileReviewAnnotationService.create(
            round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
            anchor='{}', matched_text='t', ann_type='format', severity='low',
            issue='i', suggestion='', source='ai', tenant_id=PFX)
    assert FileReviewAnnotationService.delete_by_round(r1) == 1
    rest = FileReviewAnnotation.select().where(FileReviewAnnotation.task_id == tid)
    assert [a.round_id for a in rest] == [r2]


def test_delete_by_round_default_keeps_manual_annotations():
    """默认只清 AI 产物：用户在同一轮次上补的手写批注（source=manual）不是本方法的对象。

    AI 审核重跑是「清掉自己上轮的结果」，把用户的活一起抹掉属于数据丢失。
    """
    tid = PFX + 'delroundmanual'
    r = _mk_round(tid, 1, 'reviewing')
    ai = FileReviewAnnotationService.create(
        round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
        anchor='{}', matched_text='ai', ann_type='format', severity='low',
        issue='i', suggestion='', source='ai', tenant_id=PFX)
    manual = FileReviewAnnotationService.create(
        round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
        anchor='{}', matched_text='手写', ann_type='format', severity='low',
        issue='i', suggestion='', source='manual', tenant_id=PFX)
    assert FileReviewAnnotationService.delete_by_round(r) == 1
    rest = {a.id for a in FileReviewAnnotation.select().where(FileReviewAnnotation.task_id == tid)}
    assert rest == {manual}, '手写批注必须保留'
    assert ai not in rest


def test_delete_by_round_none_source_removes_everything():
    """source=None 退化为「只按 round_id 清空整轮」——给确实需要整轮清空的场景留出口。"""
    tid = PFX + 'delroundall'
    r = _mk_round(tid, 1, 'reviewing')
    for src in ('ai', 'manual'):
        FileReviewAnnotationService.create(
            round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
            anchor='{}', matched_text=src, ann_type='format', severity='low',
            issue='i', suggestion='', source=src, tenant_id=PFX)
    assert FileReviewAnnotationService.delete_by_round(r, None) == 2
    rest = FileReviewAnnotation.select().where(FileReviewAnnotation.task_id == tid)
    assert list(rest) == []


# ── 批注删除（物理删除单条，AI / manual 均可）─────────────────────────


def test_delete_annotation_removes_single_row_physically():
    tid = PFX + 'delann'
    r = _mk_round(tid, 1, 'annotated')
    a1 = FileReviewAnnotationService.create(
        round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
        anchor='{}', matched_text='甲', ann_type='format', severity='low',
        issue='i', suggestion='', source='ai', tenant_id=PFX)
    a2 = FileReviewAnnotationService.create(
        round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
        anchor='{}', matched_text='乙', ann_type='format', severity='low',
        issue='i', suggestion='', source='manual', tenant_id=PFX)
    assert FileReviewAnnotationService.delete_annotation(a1) is True
    rest = {a.id for a in FileReviewAnnotation.select().where(
        FileReviewAnnotation.task_id == tid)}
    assert rest == {a2}, '只删目标行，同轮其他批注（含 manual）不动'


def test_delete_annotation_missing_or_empty_returns_false():
    assert FileReviewAnnotationService.delete_annotation(
        PFX + 'ghost-ann') is False
    assert FileReviewAnnotationService.delete_annotation('') is False, \
        '空 id 短路，不发 SQL'


def test_delete_annotation_is_idempotent_on_repeat():
    """重复删除幂等：第二次命中 0 行返回 False，由端点层按「不存在」回。"""
    tid = PFX + 'delannrep'
    r = _mk_round(tid, 1, 'annotated')
    aid = FileReviewAnnotationService.create(
        round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
        anchor='{}', matched_text='t', ann_type='format', severity='low',
        issue='i', suggestion='', source='ai', tenant_id=PFX)
    assert FileReviewAnnotationService.delete_annotation(aid) is True
    assert FileReviewAnnotationService.delete_annotation(aid) is False


# ── T8 助手：越权闸门 / 轮次编号 / 修复轮余额 ─────────────────────────


def _r(no):
    """只带 round_no 的轮次替身：fix_rounds_left 是纯函数，不需要真轮次行。"""
    return SimpleNamespace(round_no=no)


def test_get_owned_task_short_circuits_on_empty_args():
    assert FileReviewRoundService.get_owned_task("", "t1") == []
    assert FileReviewRoundService.get_owned_task(None, "t1") == []
    assert FileReviewRoundService.get_owned_task(PFX + "own-x", "") == []


def test_get_owned_task_denies_unknown_task():
    assert FileReviewRoundService.get_owned_task(PFX + "no-such-task", "t1") == []


def test_get_owned_task_passes_only_when_all_rounds_owned():
    tid = PFX + "own-a"
    _mk_round(tid, 1, "annotated", tenant_id="t1")
    assert len(FileReviewRoundService.get_owned_task(tid, "t1")) == 1
    # 他人租户：拒绝
    assert FileReviewRoundService.get_owned_task(tid, "t2") == []


def test_get_owned_task_denies_when_any_round_has_empty_tenant():
    """同一 task 只要**有一条**轮次 tenant 为空/他人，整体拒绝（保守口径）：
    宁可让脏数据的人自己重新发起，也不能把「是不是他的」判成「大概是」。"""
    tid = PFX + "own-b"
    _mk_round(tid, 1, "annotated", tenant_id="t1")
    _mk_round(tid, 2, "done", tenant_id="")
    assert FileReviewRoundService.get_owned_task(tid, "t1") == []


def test_next_round_counts_all_rounds_including_failed():
    tid = PFX + "next-a"
    assert FileReviewRoundService.next_round(tid) == (1, "v1")
    _mk_round(tid, 1, "annotated")
    assert FileReviewRoundService.next_round(tid) == (2, "v2")
    # failed 轮也顶号：只看 done/annotated 会让 v2 这个名字被复用，
    # 覆盖失败轮已落盘的成稿（T6 交接契约第 2 条），并把同号失败兄弟选成输入基线。
    _mk_round(tid, 2, "failed")
    assert FileReviewRoundService.next_round(tid) == (3, "v3")


def test_fix_rounds_left_counts_failed_and_clamps():
    from api.db.services.file_review_service import MAX_FIX_ROUNDS, fix_rounds_left

    assert MAX_FIX_ROUNDS == 3
    assert fix_rounds_left([]) == MAX_FIX_ROUNDS
    assert fix_rounds_left([_r(1)]) == MAX_FIX_ROUNDS          # 首轮审核不算修复轮
    assert fix_rounds_left([_r(1), _r(2)]) == MAX_FIX_ROUNDS - 1
    assert fix_rounds_left([_r(1), _r(2), _r(3)]) == MAX_FIX_ROUNDS - 2
    assert fix_rounds_left([_r(1), _r(2), _r(3), _r(4)]) == 0  # 负数钳到 0
    assert fix_rounds_left([_r(0), _r(1)]) == MAX_FIX_ROUNDS    # round_no=0 的脏行不算修复轮


# ── 11. 按文件查询（T9 面板读模型的两个数据源） ────────────────────────

def test_get_by_file_returns_latest_task_rounds():
    """「最新一行」= create_time 最大者 → 用它的 task_id 取全轮次。

    create_time 是毫秒，两个 task 必须在**不同毫秒**建行，否则 tie-break 未定义；
    故这里显式 sleep 让两次写入落在不同毫秒（10ms ≫ 1ms 分辨率）。
    """
    import time
    fid = f"{PFX}f_byfile"
    with DB.connection_context():
        _mk_round(f"{PFX}t_byfile_old", 1, "done", file_id=fid)
        time.sleep(0.01)
        _mk_round(f"{PFX}t_byfile_new", 1, "annotated", file_id=fid)
        _mk_round(f"{PFX}t_byfile_new", 2, "done", file_id=fid, file_version="v2")
        rows = FileReviewRoundService.get_by_file(fid)
    assert [r.round_no for r in rows] == [1, 2], "必须是新 task 的两轮，不是旧 task 的一轮"
    assert rows[0].task_id == f"{PFX}t_byfile_new"


def test_get_by_file_empty_and_blank_are_empty_list():
    with DB.connection_context():
        assert FileReviewRoundService.get_by_file(f"{PFX}f_byfile_ghost") == []
        assert FileReviewRoundService.get_by_file("") == []


def test_get_by_file_is_not_tenant_scoped_locks_read_policy():
    """锁住「读不限、写严格」的不对称：读路径不按 tenant 过滤（文件所有人可见）。

    若有人给 get_by_file 加上 tenant 谓词，流程场景下协作者会看不到审核结果
    （轮次行的 tenant 是发起人/画布所有者的），这条用例会立刻红。
    """
    fid = f"{PFX}f_byfile_tenant"
    with DB.connection_context():
        _mk_round(f"{PFX}t_byfile_tenant", 1, "annotated", file_id=fid,
                  tenant_id="someone_else")
        rows = FileReviewRoundService.get_by_file(fid)
    assert [r.round_no for r in rows] == [1]


def test_list_by_file_spans_versions_and_tasks():
    """面板要看到文件的**全部**标注：跨版本、跨 task。"""
    fid = f"{PFX}f_annot_file"
    with DB.connection_context():
        r1 = _mk_round(f"{PFX}t_annot_a", 1, "annotated", file_id=fid)
        r2 = _mk_round(f"{PFX}t_annot_a", 2, "done", file_id=fid, file_version="v2")
        r3 = _mk_round(f"{PFX}t_annot_b", 1, "annotated", file_id=fid)
        _mk_ann(round_id=r1, task_id=f"{PFX}t_annot_a", file_id=fid,
                file_version="v1", status="fixed")
        _mk_ann(round_id=r2, task_id=f"{PFX}t_annot_a", file_id=fid,
                file_version="v2", status="open")
        _mk_ann(round_id=r3, task_id=f"{PFX}t_annot_b", file_id=fid,
                file_version="v1", status="open")
        # 另一个文件的标注不得混入
        r_other = _mk_round(f"{PFX}t_annot_other", 1, "annotated", file_id=f"{PFX}f_other")
        _mk_ann(round_id=r_other, task_id=f"{PFX}t_annot_other", file_id=f"{PFX}f_other")
        rows = FileReviewAnnotationService.list_by_file(fid)
    assert len(rows) == 3, f"三个版本三条都要返回，实际 {len(rows)}"
    assert {r.file_version for r in rows} == {"v1", "v2"}
    assert {r.task_id for r in rows} == {f"{PFX}t_annot_a", f"{PFX}t_annot_b"}


def test_list_by_file_empty_and_blank_are_empty_list():
    with DB.connection_context():
        assert FileReviewAnnotationService.list_by_file(f"{PFX}f_annot_ghost") == []
        assert FileReviewAnnotationService.list_by_file("") == []


# ── 13. admit_fix_round：受理闸门的跨线程互斥（M3） ─────────────────────
# 这两条是本层唯一的并发语义，且都用真线程/真锁验证（不是「调一次没抛错」）。

def _patch_admission(monkeypatch, *, state, created, spawned):
    """把 admit_fix_round 依赖的四个触库点 + spawn 换成纯内存替身。

    state["rounds"] 是**共享可变列表**：get_owned_task 返回它的快照，create_round 往它
    追加 —— 这样「第二个线程看到第一个建出的修复轮」这件事才真被验证。
    """
    import time as _time

    from api.db.services import file_review_service as svc

    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: list(state["rounds"])))
    monkeypatch.setattr(svc.FileReviewAnnotationService, "list_pending_by_task",
                        classmethod(lambda cls, tid: [SimpleNamespace(severity="high")]))

    def _next_round(cls, tid):
        no = max(r.round_no for r in state["rounds"]) + 1
        return no, f"v{no}"

    def _create_round(cls, **kw):
        _time.sleep(0.3)          # 撑开竞争窗口：无锁实现下两个线程都会走进来
        state["rounds"].append(SimpleNamespace(
            round_no=kw["round_no"], status="fixing", user_query=kw["user_query"],
            file_id="f1", template_id="t1", kb_ids=None,
            # 后到者会读这一行走 is_stale_running，而该判据**直接访问** task_id /
            # create_time（刻意不 getattr 兜底）—— 行不完整就 AttributeError 而不是
            # 给出 running 结论。替身必须与真实行同形。
            task_id=kw["task_id"], create_time=int(_time.time() * 1000)))
        created.append(kw)
        return f"rid-{kw['round_no']}"

    monkeypatch.setattr(svc.FileReviewRoundService, "next_round", classmethod(_next_round))
    monkeypatch.setattr(svc.FileReviewRoundService, "create_round", classmethod(_create_round))

    from rag.svr.file_review import spawn as spawn_mod
    monkeypatch.setattr(spawn_mod, "is_running", lambda tid: False)
    monkeypatch.setattr(spawn_mod, "spawn_review_task", lambda tid: spawned.append(tid))
    return svc


def test_admit_fix_round_serializes_concurrent_callers(monkeypatch):
    """对抗性并发：两个线程同时受理同一 task，**只允许一个建出轮次**。

    这是本锁存在的唯一理由 —— REST 端点跑在 quart 事件循环、对话工具被
    common.connection_utils.timeout 丢进独立 daemon 线程，「临界区内无 await」不构成
    任何跨线程互斥。无锁实现下两个线程都会通过 status 闸门、各自 next_round() 拿到同
    一个轮号，后到者的 spawn_review_task 静默 no-op，留下永远没人消费的 fixing 行。
    """
    import threading

    state = {"rounds": [SimpleNamespace(round_no=1, status="annotated", file_id="f1",
                                        template_id="t1", kb_ids=None,
                                        user_query="审核这份招标文件",
                                        task_id=f"{PFX}t_race", create_time=0)]}
    created, spawned = [], []
    svc = _patch_admission(monkeypatch, state=state, created=created, spawned=spawned)

    barrier = threading.Barrier(2)
    results = []

    def _worker():
        barrier.wait()
        try:
            results.append(("ok", svc.admit_fix_round(
                task_id=f"{PFX}t_race", tenant_id="u1", levels=["high"])))
        except svc.FixAdmissionDenied as denied:
            results.append(("denied", denied.reason))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(kind for kind, _ in results) == ["denied", "ok"], results
    assert [v for kind, v in results if kind == "denied"] == ["running"], \
        "后到者必须看到前一个建出的 fixing 轮次（而不是也建一轮）"
    assert len(created) == 1, f"并发下只允许建一轮，实际 {len(created)}"
    assert spawned == [f"{PFX}t_race"]


def test_admit_fix_round_busy_on_lock_timeout_and_never_releases_foreign_lock(monkeypatch):
    """锁被占用且超时 → busy；且超时路径**不得**去 release 别人的锁。

    「拿不到却释放」比「拿不到」危险得多：一次误释放就能让后续所有互斥失效，重新打开
    同号轮次的窗口。这里用真锁 + 真线程构造持锁者，断言锁仍在它手里。
    """
    import threading

    from api.db.services import file_review_service as svc

    monkeypatch.setattr(svc, "ADMIT_LOCK_TIMEOUT", 0.1)
    held, release = threading.Event(), threading.Event()

    def _holder():
        with svc._ADMIT_LOCK:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=_holder, daemon=True)
    holder.start()
    assert held.wait(2), "持锁线程未能启动"
    try:
        with pytest.raises(svc.FixAdmissionDenied) as ei:
            svc.admit_fix_round(task_id=f"{PFX}t_busy", tenant_id="u1", levels=["high"])
        assert ei.value.reason == "busy"
        assert ei.value.message  # 面向用户的中文文案必须存在
        assert svc._ADMIT_LOCK.locked() is True, "超时路径不得释放持锁者仍持有的锁"
    finally:
        release.set()
        holder.join(3)
    assert svc._ADMIT_LOCK.acquire(timeout=1) is True, "持锁者释放后锁必须可用"
    svc._ADMIT_LOCK.release()


# ── 14. stale 派生：进程被杀后卡住轮次的唯一判据（R-1） ──────────────────
# is_stale_running 是**纯判定**（不触库），故这里全用 SimpleNamespace + 打桩 is_running，
# 并用显式 now_ms 锁定时间轴（不给 flaky 留缝）。

# 固定的「现在」：13 位毫秒，与 create_time 列同单位。
NOW_MS = 1_800_000_000_000


def _patch_is_running(monkeypatch, alive: bool):
    """spawn._running_tasks 的替身：alive=False 模拟「线程已不存在」。"""
    from rag.svr.file_review import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "is_running", lambda tid: alive)


def _stale_row(status="reviewing", create_time=0, task_id="t1"):
    return SimpleNamespace(task_id=task_id, status=status, create_time=create_time)


def test_is_stale_running_requires_expired_grace(monkeypatch):
    """判据③：行龄必须**严格大于**宽限期（锁定 off-by-one 口径）。"""
    from api.db.services.file_review_service import STALE_GRACE_SECONDS, is_stale_running

    grace_ms = STALE_GRACE_SECONDS * 1000
    _patch_is_running(monkeypatch, alive=False)

    def _judge(age_ms):
        return is_stale_running(_stale_row(create_time=NOW_MS - age_ms), now_ms=NOW_MS)

    assert _judge(0) is False, "刚建的轮次落在建轮→起线程窗口内，不许判 stale"
    assert _judge(grace_ms - 1) is False
    assert _judge(grace_ms) is False, "恰好等于宽限期不算（严格大于）"
    assert _judge(grace_ms + 1) is True
    assert _judge(10 ** 9) is True, "行龄再大也只是 stale 的**必要**条件之一"


def test_is_stale_running_never_claims_while_thread_alive(monkeypatch):
    """判据②：线程真在跑就绝不判 stale —— 单轮耗时超过 60s 是常态，行龄不是证据。
    这条同时保证工具侧「先查轮次状态、后查线程」的既有顺序不被打乱。"""
    from api.db.services.file_review_service import is_stale_running

    _patch_is_running(monkeypatch, alive=True)
    for status in ("reviewing", "fixing"):
        row = _stale_row(status=status, create_time=0)   # 行龄 ≈ 半个世纪
        assert is_stale_running(row, now_ms=NOW_MS) is False


def test_is_stale_running_only_applies_to_running_statuses(monkeypatch):
    """判据①：终态行不参与 —— 否则历史轮次会被显示成「已中断」并隐藏修复入口。"""
    from api.db.services.file_review_service import is_stale_running

    _patch_is_running(monkeypatch, alive=False)
    for status in ("annotated", "done", "failed"):
        assert is_stale_running(_stale_row(status=status, create_time=0),
                                now_ms=NOW_MS) is False
    for status in ("reviewing", "fixing"):
        assert is_stale_running(_stale_row(status=status, create_time=0),
                                now_ms=NOW_MS) is True


def test_is_stale_running_fails_loudly_on_missing_create_time(monkeypatch):
    """缺 create_time 必须响亮报错（端点 → 500），**不得** getattr 兜底成「已中断」：
    把一个正常在跑的轮次判死比报错严重得多 —— 用户会当它没有结果而白白重跑。"""
    from api.db.services.file_review_service import is_stale_running

    _patch_is_running(monkeypatch, alive=False)
    with pytest.raises(AttributeError):
        is_stale_running(SimpleNamespace(task_id="t1", status="reviewing"), now_ms=NOW_MS)


def test_admit_fix_round_heals_stale_then_admits(monkeypatch):
    """R-1：stale 闸门从「拒绝」改为「heal → 刷新内存 → 放行走后续闸门」。
    中断轮与线程内部崩溃的 failed 轮同权：有余额即可直接发起下一轮修复，
    不再要求「重新发起审核」。"""
    stale_round = SimpleNamespace(id="r-old", round_no=1, status="reviewing",
                                  file_id="f1", template_id="t1", kb_ids=None,
                                  user_query="审核这份招标文件",
                                  task_id=f"{PFX}t_stale_heal", create_time=0)
    created, spawned, updates = [], [], []
    svc = _patch_admission(monkeypatch, state={"rounds": [stale_round]},
                           created=created, spawned=spawned)
    monkeypatch.setattr(svc.FileReviewRoundService, "update_status",
                        classmethod(lambda cls, rid, status, **extra:
                                    updates.append((rid, status, extra)) or True))

    result = svc.admit_fix_round(task_id=f"{PFX}t_stale_heal", tenant_id="u1",
                                 levels=["high"])
    assert updates and updates[0][0] == "r-old" and updates[0][1] == "failed"
    assert stale_round.status == "failed", "内存未刷新 ⇒ 后续闸门会读到旧 running 态"
    assert created and created[0]["round_no"] == 2
    assert spawned == [f"{PFX}t_stale_heal"]
    assert result.round_no == 2


def test_admit_fix_round_after_heal_respects_quota(monkeypatch):
    """自愈不是绕过闸门：heal 后继续走 no_quota——余额耗尽照样拒绝。
    余额口径 = round_no>1 的行数（failed 也计入），故需 3 条修复轮才耗尽
    MAX_FIX_ROUNDS=3；末轮保持 stale 态以验证「拒绝发生在 heal 之后」。"""
    rounds = [SimpleNamespace(id="r1", round_no=1, status="annotated", file_id="f1",
                              template_id="t1", kb_ids=None,
                              user_query="审核这份招标文件",
                              task_id=f"{PFX}t_stale_quota", create_time=0),
              SimpleNamespace(id="r2", round_no=2, status="failed", file_id="f1",
                              template_id="t1", kb_ids=None, user_query="修复",
                              task_id=f"{PFX}t_stale_quota", create_time=0),
              SimpleNamespace(id="r3", round_no=3, status="failed", file_id="f1",
                              template_id="t1", kb_ids=None, user_query="修复",
                              task_id=f"{PFX}t_stale_quota", create_time=0),
              SimpleNamespace(id="r4", round_no=4, status="fixing", file_id="f1",
                              template_id="t1", kb_ids=None, user_query="修复",
                              task_id=f"{PFX}t_stale_quota", create_time=0)]
    updates = []
    svc = _patch_admission(monkeypatch, state={"rounds": rounds},
                           created=[], spawned=[])
    monkeypatch.setattr(svc.FileReviewRoundService, "update_status",
                        classmethod(lambda cls, rid, status, **extra:
                                    updates.append((rid, status)) or True))

    with pytest.raises(svc.FixAdmissionDenied) as ei:
        svc.admit_fix_round(task_id=f"{PFX}t_stale_quota", tenant_id="u1",
                            levels=["high"])
    assert ei.value.reason == "no_quota"
    assert ("r4", "failed") in updates, "拒绝发生在 heal 之后：中断轮必须已被回落"


def test_admit_fix_round_rejects_non_list_levels(monkeypatch):
    """R-7 防御闸：levels 非 list（None / 字符串 / 元组 / 集合）在取锁与触库
    **之前**拒绝——不得 TypeError 冒烟成 500，也不得消耗锁等待窗口。"""
    from api.db.services import file_review_service as svc

    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: (_ for _ in ()).throw(
                            AssertionError("levels 判型必须发生在任何触库之前"))))
    for bad in (None, "high", ("high",), {"high"}):
        with pytest.raises(svc.FixAdmissionDenied) as ei:
            svc.admit_fix_round(task_id="t1", tenant_id="u1", levels=bad)
        assert ei.value.reason == "invalid_levels"
        assert not svc._ADMIT_LOCK.locked(), "拒绝路径不得持有受理锁"


def test_admit_fix_round_no_pending_message_carries_full_picture(monkeypatch):
    """拒绝文案是用户看到的最后一句：只说「没有」用户不知道该改选哪个级别，
    只能靠猜或反复试。措辞由本层统一给出，REST 端点与对话工具逐字共用同一份。"""
    from api.db.services import file_review_service as svc

    # 修复轮余额充足（只有首轮），确保拦下它的是 no_pending 而不是 no_quota。
    rounds = [SimpleNamespace(round_no=1, status="annotated", file_id="f1",
                              template_id="t1", kb_ids=None,
                              user_query="审核这份招标文件",
                              task_id=f"{PFX}t_nopending", create_time=0)]
    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: list(rounds)))
    monkeypatch.setattr(svc.FileReviewAnnotationService, "list_pending_by_task",
                        classmethod(lambda cls, tid: [SimpleNamespace(severity="high"),
                                                      SimpleNamespace(severity="high"),
                                                      SimpleNamespace(severity="medium")]))
    _patch_is_running(monkeypatch, alive=False)

    with pytest.raises(svc.FixAdmissionDenied) as ei:
        svc.admit_fix_round(task_id=f"{PFX}t_nopending", tenant_id="u1", levels=["low"])
    assert ei.value.reason == "no_pending"
    assert ei.value.message == ("没有【提示】级别的待修复问题。"
                                "待修复问题：共 3 条（严重 2 条、一般 1 条）")


def test_severity_helpers_render_unknown_without_losing_it():
    """脏 severity 不许渲染成字面 None；未登记的值原样透出（排障线索不能丢），
    且排在已知级别之后。空集必须明说「无」，不留空让用户/LLM 去猜。"""
    from api.db.services.file_review_service import severity_cn, severity_summary

    assert severity_cn("high") == "严重"
    assert severity_cn(None) == "未知" and severity_cn("") == "未知"
    assert severity_cn("weird") == "weird"
    assert severity_summary([]) == "无"
    assert severity_summary([SimpleNamespace(severity="medium"),
                             SimpleNamespace(severity="weird"),
                             SimpleNamespace(severity=None)]) == \
        "共 3 条（一般 1 条、weird 1 条、未知 1 条）"


# ── 15. heal_stale_round：中断轮次惰性自愈（R-1 落库单点） ───────────────
# 判定复用 is_stale_running（纯谓词），落库走 update_status；DB 用例走真库，
# 时间轴用显式 now_ms 锁定（不给 flaky 留缝）。

def test_heal_stale_round_persists_failed_with_error(monkeypatch):
    """命中谓词 → 落库 failed + 中断文案，且**同步刷新传入行的内存字段**——
    调用方（state 端点 / 受理闸门）随后读的是回落后的值，不重查库。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_db"
    rid = _mk_round(tid, 1, "reviewing")
    row = _round_row(rid)
    _patch_is_running(monkeypatch, alive=False)

    assert heal_stale_round(row, now_ms=row.create_time + 61_000) is True
    assert row.status == "failed" and "已中断" in row.error
    after = _round_row(rid)
    assert after.status == "failed" and "已中断" in after.error


def test_heal_stale_round_skips_live_thread_terminal_and_grace(monkeypatch):
    """谓词不成立一律不动库：线程活着（单轮超 60s 是常态）、行龄在宽限期内、终态行。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_skip"
    rid = _mk_round(tid, 1, "reviewing")
    row = _round_row(rid)

    _patch_is_running(monkeypatch, alive=True)
    assert heal_stale_round(row, now_ms=row.create_time + 10 ** 9) is False

    _patch_is_running(monkeypatch, alive=False)
    assert heal_stale_round(row, now_ms=row.create_time) is False, "行龄 0 在宽限期内"

    done = _round_row(_mk_round(tid, 2, "done", file_version="v2"))
    assert heal_stale_round(done, now_ms=done.create_time + 10 ** 9) is False

    after = _round_row(rid)
    assert after.status == "reviewing" and not after.error


def test_heal_stale_round_is_idempotent(monkeypatch):
    """heal 后 status 离开 RUNNING 集合，谓词不再成立 ⇒ 第二次调用返回 False、
    不再触库。并发双 heal 最终行值相同（同一份文案），无害。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_idem"
    rid = _mk_round(tid, 1, "fixing")
    row = _round_row(rid)
    _patch_is_running(monkeypatch, alive=False)
    when = row.create_time + 61_000

    assert heal_stale_round(row, now_ms=when) is True
    row.error = "SENTINEL"  # 若第二次误写库并刷新内存，哨兵会被覆盖而暴露
    assert heal_stale_round(row, now_ms=when) is False
    assert row.error == "SENTINEL"               # 内存未被第二次触碰
    assert _round_row(rid).error != "SENTINEL"   # 库里仍是首轮落下的文案
