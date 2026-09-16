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


def _cleanup():
    """只按测试前缀清理，幂等（表不存在时由 fixture 先建表）。"""
    FileReviewAnnotation.delete().where(FileReviewAnnotation.task_id.startswith(PFX)).execute()
    FileReviewAnnotation.delete().where(FileReviewAnnotation.file_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.task_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.file_id.startswith(PFX)).execute()
    FileReviewTemplate.delete().where(FileReviewTemplate.id.startswith(PFX)).execute()


@pytest.fixture(scope="module", autouse=True)
def _tables_presets_and_cleanup():
    DB.connect(reuse_if_open=True)
    for m in (FileReviewTemplate, FileReviewRound, FileReviewAnnotation):
        if not m.table_exists():
            m.create_table(safe=True)
    if FileReviewTemplate.select().where(
            FileReviewTemplate.tenant_id == "").count() < len(_PRESET_REVIEW_TEMPLATES):
        _seed_file_review_templates()
    _cleanup()
    yield
    _cleanup()
    DB.close()


def _mk_round(task_id, round_no, status, *, file_id, file_version="v1", **kw):
    """走 service 建轮次（审计字段由框架填，这里不传任何时间字段）。"""
    return FileReviewRoundService.create_round(
        task_id=task_id, file_id=file_id, round_no=round_no,
        template_id="bid_doc_format", user_query="审核请求",
        file_version=file_version, status=status, tenant_id="", created_by="u", **kw)


def _mk_ann(*, round_id, task_id, file_id, status="open", file_version="v1", **kw):
    """走 service 建标注（anchor 用项目统一 JSON 封装序列化）。"""
    return FileReviewAnnotationService.create(
        round_id=round_id, task_id=task_id, file_id=file_id, file_version=file_version,
        anchor=json_dumps({"p_hash": 0, "offset": 1}), matched_text="foo", type="format",
        severity="high", issue="x", suggestion="y", source="ai", status=status,
        tenant_id="", created_by="u", **kw)


# ── 计划给定的 3 个基线用例 ────────────────────────────────────────────
def test_list_enabled_templates():
    with DB.connection_context():
        rows = FileReviewTemplateService.list_enabled()
    assert len(rows) >= 5
    assert all(r.enabled == 1 for r in rows), "list_enabled 必须过滤 enabled != 1"
    ids = [r.id for r in rows]
    assert ids == sorted(ids), "按 id 升序（前端下拉顺序需稳定）"


def test_max_completed_rounds_excludes_failed():
    tid = f"{PFX}t_max_failed"
    with DB.connection_context():
        _mk_round(tid, 1, "done", file_id=f"{PFX}f1")
        _mk_round(tid, 2, "failed", file_id=f"{PFX}f1", file_version="v2")
        n = FileReviewRoundService.max_completed_round_no(tid)
    assert n == 1  # 只统计 done/annotated，不含 failed


def test_upsert_annotations_status_transition():
    tid, fid = f"{PFX}t_trans", f"{PFX}f_trans"
    with DB.connection_context():
        ann_id = FileReviewAnnotationService.create(
            round_id=f"{PFX}r1", task_id=tid, file_id=fid, file_version="v1",
            anchor=json_dumps({"p_hash": 0}), matched_text="foo", type="format",
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
        assert FileReviewRoundService.get_by_id("") is None, "空 id 应短路，不去打 id IS NULL"
        assert FileReviewRoundService.max_completed_round_no(f"{PFX}no_such_task") == 0
        assert FileReviewRoundService.get_by_task(f"{PFX}no_such_task") == []
        rows = FileReviewAnnotationService.list_by_file_version(f"{PFX}no_file", "v1")
    assert rows is not None and rows == [], "无数据必须是空列表（None 会被上游直接 len() 崩掉）"


def test_update_status_on_missing_row_returns_false_without_raising():
    with DB.connection_context():
        assert FileReviewRoundService.update_status(f"{PFX}ghost_round", "done") is False
        assert FileReviewAnnotationService.update_status(f"{PFX}ghost_ann", "fixed") is False


# ── 2. max_completed_round_no 的对抗：中间态/失败态一律不计入 ────────────
def test_max_completed_round_no_ignores_reviewing_fixing_and_failed():
    """对抗：把「更大轮次号但未完成」的行塞进来，max 必须是最大的**已完成**轮次，
    而不是最大轮次号（若实现漏了 status 过滤，会返回 9）。"""
    tid = f"{PFX}t_mixed"
    with DB.connection_context():
        _mk_round(tid, 1, "done", file_id=f"{PFX}f_mixed", file_version="v1")
        _mk_round(tid, 3, "annotated", file_id=f"{PFX}f_mixed", file_version="v3")
        _mk_round(tid, 5, "reviewing", file_id=f"{PFX}f_mixed", file_version="v5")
        _mk_round(tid, 7, "fixing", file_id=f"{PFX}f_mixed", file_version="v7")
        _mk_round(tid, 9, "failed", file_id=f"{PFX}f_mixed", file_version="v9")
        assert FileReviewRoundService.max_completed_round_no(tid) == 3
        # annotated 也必须计入（只测 done 会漏掉「已标注待修复」这一态）
        _mk_round(tid, 11, "annotated", file_id=f"{PFX}f_mixed", file_version="v11")
        assert FileReviewRoundService.max_completed_round_no(tid) == 11


# ── 3. 多 task 隔离：三个多轮判定方法都不许串号 ─────────────────────────
def test_round_queries_are_isolated_between_tasks():
    ta, tb = f"{PFX}t_iso_a", f"{PFX}t_iso_b"
    with DB.connection_context():
        _mk_round(ta, 1, "done", file_id=f"{PFX}f_iso_a")
        _mk_round(ta, 2, "annotated", file_id=f"{PFX}f_iso_a", file_version="v2")
        _mk_round(tb, 4, "done", file_id=f"{PFX}f_iso_b", file_version="v4")
        assert FileReviewRoundService.max_completed_round_no(ta) == 2
        assert FileReviewRoundService.max_completed_round_no(tb) == 4
        assert [r.round_no for r in FileReviewRoundService.get_by_task(ta)] == [1, 2]
        assert [r.round_no for r in FileReviewRoundService.get_by_task(tb)] == [4]
        assert [r.task_id for r in FileReviewRoundService.get_by_task(ta)] == [ta, ta]


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

    assert [r.id for r in all_rows] == [a1, a2, b1], "task_id=None 跨 task 取全量且按 create_time 升序"
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
                anchor=json_dumps({}), matched_text="", type="format", severity="high",
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


# ── 10. 审计字段由框架接管（修正 1 的防回归） ───────────────────────────
def test_audit_timestamps_autofilled_by_framework():
    """对抗：service 若手写 create_time=datetime.now()，BIGINT 列会落成零值/报错。
    这里断言落库值确实是 13 位毫秒整数，且 update 会刷新 update_time。"""
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
        before = rrow.update_time
        FileReviewRoundService.update_status(rid, "annotated")
        after = FileReviewRoundService.get_by_id(rid).update_time
    assert after >= before, "update 必须刷新 update_time"


# ── 11. 默认值与可选字段 ───────────────────────────────────────────────
def test_annotation_defaults_and_optional_fields():
    tid, fid = f"{PFX}t_defaults", f"{PFX}f_defaults"
    with DB.connection_context():
        rid = _mk_round(tid, 1, "reviewing", file_id=fid)
        aid = FileReviewAnnotationService.create(
            round_id=rid, task_id=tid, file_id=fid, file_version="v1",
            anchor=json_dumps({"sheet": "S1", "cell": "A1"}), matched_text="m",
            type="clause", severity="low", issue="i", suggestion="",
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
