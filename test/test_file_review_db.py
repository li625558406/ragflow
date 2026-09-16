import string

import pytest
from peewee import BigIntegerField, DateTimeField

from api.db.db_models import (
    _PRESET_REVIEW_TEMPLATES,
    DB,
    FileReviewAnnotation,
    FileReviewRound,
    FileReviewTemplate,
    MediumTextField,
    _ensure_file_review_templates,
    _seed_file_review_templates,
)

_EXPECTED_IDS = {"bid_doc_format", "bid_response_complete", "bid_substantive_clause",
                 "bid_qualification", "bid_price_review"}

# 本文件只往库里塞「非预置口径」的诱饵行，故清理一律按该前缀点名删除
PFX = "__test_fr_db__"


@pytest.fixture(scope="module", autouse=True)
def _ensure_file_review_tables():
    """跑测前确保 3 张表已建 + 预置模板已按行数补齐（等价 migrate_db 对应片段，幂等）。"""
    DB.connect(reuse_if_open=True)
    if not FileReviewTemplate.table_exists():
        FileReviewTemplate.create_table(safe=True)
    # init_database_tables 会先建表再进 migrate_db，故这里同样按行数判定补齐
    if FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "").count() < len(_PRESET_REVIEW_TEMPLATES):
        _seed_file_review_templates()
    for m in (FileReviewRound, FileReviewAnnotation):
        if not m.table_exists():
            m.create_table(safe=True)
    yield
    DB.close()


# ── 1. 预置模板结构完整性 + .format() 安全性（纯内存，不碰 DB） ──────────
def test_presets_structure_complete():
    """对抗视角：缺字段/空串/重复 id/超长值都必须在测试期就暴露。"""
    assert len(_PRESET_REVIEW_TEMPLATES) == 5
    ids = [t["id"] for t in _PRESET_REVIEW_TEMPLATES]
    assert len(ids) == len(set(ids)), f"预置 id 重复: {ids}"
    assert set(ids) == _EXPECTED_IDS
    for t in _PRESET_REVIEW_TEMPLATES:
        tid = t["id"]
        assert 0 < len(tid) <= 64, f"{tid} id 超 CharField(64)"
        assert 0 < len(t["name"]) <= 128, f"{tid} name 超 CharField(128)"
        assert t["name"].strip(), f"{tid} name 全空白"
        assert t["system_prompt"].strip(), f"{tid} system_prompt 为空"
        assert t["user_prompt_template"].strip(), f"{tid} user_prompt_template 为空"
        assert isinstance(t["annotation_types"], list) and t["annotation_types"], \
            f"{tid} annotation_types 必须是非空 list"
        assert all(isinstance(x, str) and x.strip() for x in t["annotation_types"]), \
            f"{tid} annotation_types 元素必须是非空字符串"


def test_presets_prompt_template_format_safe():
    """user_prompt_template 后续要 .format()：字段集必须恰好 3 个，且无游离花括号。"""
    allowed = {"user_query", "file_text", "references"}
    for t in _PRESET_REVIEW_TEMPLATES:
        tpl = t["user_prompt_template"]
        fields = {n for _, n, _, _ in string.Formatter().parse(tpl) if n}
        assert fields == allowed, f"{t['id']} 占位符={fields}"
        stripped = tpl.replace("{{", "").replace("}}", "")
        assert stripped.count("{") == stripped.count("}") == len(allowed)
        out = tpl.format(user_query="Q", file_text="E", references="R")
        assert "{{" not in out and "}}" not in out
        assert out.count("{") == out.count("}") and out.count("[") == out.count("]")
        assert out.rstrip().endswith("]") and '"matched_text"' in out
        assert '"anchor"' not in out          # 锚点由服务端反查，不得诱导 LLM 编造


def test_presets_prompt_template_requires_all_fields():
    """反向断言：占位符被删/改名后，调用方按老契约 format 必须显式报错，而不是静默产出坏 prompt。"""
    for t in _PRESET_REVIEW_TEMPLATES:
        with pytest.raises(KeyError):
            t["user_prompt_template"].format(user_query="Q", file_text="E")  # 缺 references


# ── 2. 列定义防回归（大文本必须 MEDIUMTEXT；枚举/判别键不许 NULL） ───────
def test_large_text_columns_are_mediumtext():
    """TEXT(64KB) 会静默截断整轮 LLM 原始输出 —— 见 db_models.py 同类事故注释。"""
    assert isinstance(FileReviewRound._meta.fields["llm_raw"], MediumTextField), \
        "llm_raw 用 TEXT 会把长响应截成半截 JSON（静默、无异常）"
    assert isinstance(FileReviewRound._meta.fields["summary"], MediumTextField)


def test_enum_and_discriminator_columns_not_null():
    a = FileReviewAnnotation._meta.fields
    for col in ("type", "severity", "source", "status", "file_version", "anchor", "issue"):
        assert a[col].null is False, f"annotation.{col} 不应允许 NULL"
    assert a["status"].default == "open", "新建标注必须默认 open"
    assert a["severity"].max_length >= 16, "severity 来自 LLM 输出，8 字符易 DataError 1406"
    for m, name in ((FileReviewRound, "round"), (FileReviewAnnotation, "annotation")):
        f = m._meta.fields["tenant_id"]
        assert f.null is False and f.default == "", f"{name}.tenant_id 必须 NOT NULL 且默认 ''"
    assert FileReviewTemplate._meta.fields["tenant_id"].null is False, \
        "tenant_id 是系统预置判别键（''=预置），允许 NULL 会让 == '' 过滤漏行"


def test_query_paths_have_indexes():
    """三条既定访问路径必须被索引覆盖。"""
    assert any(set(i) == {"task_id", "round_no"} for i, _ in FileReviewRound._meta.indexes)
    assert any(set(i) == {"file_id", "file_version"} for i, _ in FileReviewAnnotation._meta.indexes)
    assert FileReviewAnnotation._meta.fields["round_id"].index, "按轮次取标注是最热查询"
    assert FileReviewTemplate._meta.fields["tenant_id"].index, "按 tenant 列模板是主查询"
    # M2：FileReviewRound.task_id 的独立索引必须被移除（复合 (task_id, round_no) 左前缀已覆盖）
    assert not FileReviewRound._meta.fields["task_id"].index
    # 但 FileReviewAnnotation.task_id 的单列索引必需（其复合索引是 (file_id, file_version)，不含 task_id）
    assert FileReviewAnnotation._meta.fields["task_id"].index


# ── 3. 框架契约 / 审计字段 / seed 幂等 ──────────────────────────────────
def test_models_expose_framework_helpers():
    """service/API 层序列化依赖 DataBaseModel 提供的 to_dict。"""
    for m in (FileReviewTemplate, FileReviewRound, FileReviewAnnotation):
        assert hasattr(m, "to_dict"), f"{m.__name__} 缺少 to_dict（须继承 DataBaseModel）"
        assert hasattr(m, "to_human_model_dict"), f"{m.__name__} 缺少 to_human_model_dict"


def test_audit_fields_autofilled_on_insert():
    """对抗视角：调用方忘记传时间也必须被框架兜住，且 update 要刷新 update_time。"""
    # 列类型断言：手写 DateTimeField 会让毫秒时间戳回退成零值日期且 is not None 仍过（半回退逃逸）
    assert isinstance(FileReviewRound._meta.fields["create_time"], BigIntegerField)
    assert isinstance(FileReviewRound._meta.fields["create_date"], DateTimeField)
    assert isinstance(FileReviewRound._meta.fields["update_time"], BigIntegerField)
    assert isinstance(FileReviewRound._meta.fields["update_date"], DateTimeField)

    rid = "__audit_probe__"
    try:
        FileReviewRound.create(id=rid, task_id=rid, file_id=rid, round_no=1,
                               status="reviewing", file_version="v1")
        row = FileReviewRound.get_by_id(rid)
        assert row.create_time is not None, "create_time 未自动写入"
        assert isinstance(row.create_time, int) and row.create_time > 10**12, \
            "create_time 必须是毫秒时间戳（手写 DateTimeField 会回退成 '0000-00-00'）"
        assert row.update_time is not None, "update_time 未自动写入"
        FileReviewRound.update(status="done").where(FileReviewRound.id == rid).execute()
        assert FileReviewRound.get_by_id(rid).update_time is not None, "update 未刷新 update_time"
    finally:
        FileReviewRound.delete().where(FileReviewRound.id == rid).execute()


def test_seed_is_idempotent():
    """手工清表/重复调用/中途失败重试都必须安全（修前第二次调用即 IntegrityError）。"""
    before = FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "").count()
    _seed_file_review_templates()   # 不应抛 IntegrityError
    _seed_file_review_templates()   # 第三次同理
    after = FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "").count()
    assert before == after == 5, f"seed 不幂等/行数漂移: {before} -> {after}"


def test_seed_self_heals_after_partial_loss():
    """模拟「建表已提交但 seed 中途失败」：删掉 2 套后重跑 ensure 必须补齐。

    不直调 migrate_db()：那会执行真实迁移 DML（bid_enterprise_cache 探针行、按失败重建整表、
    api_4_conversation/flow_ai_chat 的 UPDATE、89 处 alter 等），慢且会污染线上库。
    """
    FileReviewTemplate.delete().where(
        FileReviewTemplate.id << ["bid_qualification", "bid_price_review"]).execute()
    assert FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "").count() == 3
    try:
        assert _ensure_file_review_templates() is None
        assert FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "").count() == 5
    finally:
        _seed_file_review_templates()   # 任何断言失败都恢复 5 套，避免脏状态传染后续用例


def test_seed_leaves_foreign_rows_untouched():
    """drift sync 只认预置 ID：别的租户/用户的模板行不得被写入或改写。"""
    decoy = "decoy_tpl_" + PFX
    FileReviewTemplate.delete().where(FileReviewTemplate.id == decoy).execute()
    FileReviewTemplate.create(
        id=decoy, name="别人的模板", description="d", system_prompt="s",
        user_prompt_template="{user_query}", annotation_types="[]",
        enabled=0, tenant_id="other-tenant", created_by="someone",
    )
    try:
        _seed_file_review_templates()
        row = FileReviewTemplate.get(FileReviewTemplate.id == decoy)
        assert row.enabled == 0 and row.tenant_id == "other-tenant" and row.name == "别人的模板"
    finally:
        FileReviewTemplate.delete().where(FileReviewTemplate.id == decoy).execute()


def test_seed_drift_syncs_prompt_text_but_never_re_enables():
    """常量文案修订必须能到已初始化的库（否则改了也白改）；但管理员显式停用的
    enabled=0 是用户态，启动同步不得把它重新打开。"""
    tpl_id = _PRESET_REVIEW_TEMPLATES[0]["id"]
    FileReviewTemplate.update(
        system_prompt="被改坏的旧值", enabled=0,
    ).where(FileReviewTemplate.id == tpl_id).execute()
    try:
        assert _seed_file_review_templates() == ""
        row = FileReviewTemplate.get(FileReviewTemplate.id == tpl_id)
        assert row.system_prompt == _PRESET_REVIEW_TEMPLATES[0]["system_prompt"]
        assert row.user_prompt_template == _PRESET_REVIEW_TEMPLATES[0]["user_prompt_template"]
        assert row.enabled == 0
    finally:
        _seed_file_review_templates()
        FileReviewTemplate.update(enabled=1).where(FileReviewTemplate.id == tpl_id).execute()


def test_ensure_returns_error_text_on_seed_failure(monkeypatch):
    """seed 失败必须把错误文本回传给调用方（migrate_db 在恢复日志级别后输出）。"""
    import api.db.db_models as dbm

    FileReviewTemplate.delete().where(FileReviewTemplate.id == "bid_price_review").execute()

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(dbm, "_seed_file_review_templates", _boom)
    try:
        # 内部 try/except 兜住，不外抛；错误以文本形式返回
        err = dbm._ensure_file_review_templates()
        assert err and "boom" in err, f"seed 失败未回传错误文本: {err!r}"
    finally:
        monkeypatch.undo()
        _seed_file_review_templates()


def test_preset_templates_seeded():
    with DB.connection_context():
        rows = FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == "")
        ids = {r.id for r in rows}
    assert _EXPECTED_IDS <= ids, f"预置模板缺失: {_EXPECTED_IDS - ids}"
    assert len(ids) == 5
