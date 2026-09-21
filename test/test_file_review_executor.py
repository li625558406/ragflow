# test/test_file_review_executor.py
"""executor 对抗测试：多轮状态机 / LLM 输出解析容错 / 锚点唯一性 / 修复落盘 / 幂等与失败收口。

真实 MySQL（同 test_file_review_service.py 的取舍）+ 真实 docx 字节（同 test_file_review_patcher.py）：
解析与锚定复用的是私有符号，只有真造真读才能证明耦合点还在；打桩掉 DB 后只剩
「函数被调用过」这种空断言，测不出 where 条件是否真的命中目标行。
所有测试行带 PFX 前缀，清理只按该前缀删，绝不误伤线上数据。禁止调用 migrate_db()。
"""

import io
import json
import logging
from types import SimpleNamespace

import pytest
from docx import Document
from docx.shared import Pt

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
)
from rag.svr.file_review import executor
from rag.svr.template_fill.docx_utils import norm_ws

PFX = "__test_fr_exec__"

# 各 fixture 共用的正文填充段。`_compose_file_text` 的闸门按 `len(norm_ws(text))` 判定
# （空白不计），真实招标文件正文远超阈值，所以 fixture 必须用真实长度的正文——不能反向
# 把阈值调小去迁就短 fixture（那会让只带页眉的扫描件蒙混过关、诱发幻觉标注）。
_BODY = "这是一段用于通过最小正文长度校验的填充文字，用来模拟真实招标文件的正文内容，确保审核链路的正文装配不会被最小长度闸门提前拦下。"


def _cleanup():
    FileReviewAnnotation.delete().where(FileReviewAnnotation.task_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.task_id.startswith(PFX)).execute()
    FileReviewTemplate.delete().where(FileReviewTemplate.id.startswith(PFX)).execute()


@pytest.fixture(scope="module", autouse=True)
def _tables_and_cleanup():
    from api.db import db_models as dbm

    DB.connect(reuse_if_open=True)
    try:
        for m in (FileReviewTemplate, FileReviewRound, FileReviewAnnotation):
            if not m.table_exists():
                m.create_table(safe=True)
        # 预置模板必须齐：有「template_id 非法 → 回退 DEFAULT_TEMPLATE_ID」的用例要解析到它
        dbm._seed_file_review_templates()
        _cleanup()
        yield
    finally:
        try:
            _cleanup()
        finally:
            DB.close()


class _FakeStorage:
    """内存对象存储替身：只实现 executor 用到的 get/put 两个方法。"""

    def __init__(self):
        self.blobs = {}

    def get(self, bucket, name):
        return self.blobs.get((bucket, name))

    def put(self, bucket, name, blob):
        self.blobs[(bucket, name)] = blob
        return True


@pytest.fixture
def fstore(monkeypatch):
    from types import SimpleNamespace

    st = _FakeStorage()
    monkeypatch.setattr(executor, "settings", SimpleNamespace(STORAGE_IMPL=st))
    return st


def _docx(paragraphs):
    d = Document()
    for t in paragraphs:
        d.add_paragraph(t)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _mk_template(*, types=("format",), user_tpl=None):
    tid = PFX + "-tpl"
    FileReviewTemplate.delete().where(FileReviewTemplate.id == tid).execute()
    FileReviewTemplate.create(
        id=tid,
        name="测试模板",
        description="d",
        system_prompt="系统提示",
        user_prompt_template=user_tpl or "需求：{user_query}\n正文：\n{file_text}\n参考：\n{references}",
        annotation_types=json.dumps(list(types)),
        enabled=1,
        tenant_id="",
        created_by=PFX,
    )
    return tid


def _mk_round(tid, *, status="reviewing", file_version="v1", template_id=None, round_no=1, tenant_id=PFX, user_query="看看格式", **kw):
    return FileReviewRoundService.create_round(
        task_id=tid,
        file_id=PFX + "-file",
        round_no=round_no,
        template_id=PFX + "-tpl" if template_id is None else template_id,
        user_query=user_query,
        file_version=file_version,
        status=status,
        tenant_id=tenant_id,
        created_by=PFX,
        **kw,
    )


def _round(tid):
    return FileReviewRoundService.get_by_id(tid)


def _anns(tid):
    return list(FileReviewAnnotation.select().where(FileReviewAnnotation.task_id == tid).order_by(FileReviewAnnotation.create_time.asc()))


def _wire(monkeypatch, *, blob, raw, chunks=None, storage=None):
    """把 executor 的四条外部缝全部接上：文件、检索、LLM、对象存储。"""
    monkeypatch.setattr(executor, "_load_original_blob", lambda tenant_id, file_id: blob)
    monkeypatch.setattr(executor, "_retrieve_chunks", lambda tenant_id, kb_ids, query: list(chunks or []))
    monkeypatch.setattr(executor, "_call_llm", lambda tenant_id, system, user: raw)


# ── 纯函数：JSON 解析 ────────────────────────────────────────────
def test_parse_annotation_items_bare_array():
    raw = '[{"matched_text": "甲", "type": "format", "severity": "high", "issue": "i"}]'
    out = executor._parse_annotation_items(raw)
    assert len(out) == 1 and out[0]["matched_text"] == "甲"


def test_parse_annotation_items_greedy_array_before_object():
    """贪心 object 排在数组前会只吃第一个内层对象 —— 2 条变 1 条。"""
    raw = '[{"matched_text": "甲"}, {"matched_text": "乙"}]'
    assert len(executor._parse_annotation_items(raw)) == 2


def test_parse_annotation_items_recovers_from_fenced_prose():
    raw = '好的，结果如下：\n```json\n[{"matched_text": "甲", "issue": "i"}]\n```\n以上。'
    out = executor._parse_annotation_items(raw)
    assert len(out) == 1 and out[0]["issue"] == "i"


def test_parse_annotation_items_accepts_wrapped_object():
    raw = '{"annotations": [{"matched_text": "甲"}, {"matched_text": "乙"}], "note": "x"}'
    assert len(executor._parse_annotation_items(raw)) == 2


def test_parse_annotation_items_empty_array_is_empty_not_none():
    """`[]` = LLM 明确说「没问题」，与「解析不出来」是相反语义。"""
    assert executor._parse_annotation_items("[]") == []


def test_parse_annotation_items_rejects_non_dict_array_elements():
    """`["无问题"]` / `[1,2]` 是散文噪声，不是「零标注」——折成 [] 会伪造「审核通过」。"""
    assert executor._parse_annotation_items('["无问题"]') is None
    assert executor._parse_annotation_items("[1, 2]") is None


def test_parse_annotation_items_garbage_is_none():
    assert executor._parse_annotation_items("对不起，我无法完成该任务") is None
    assert executor._parse_annotation_items("") is None
    assert executor._parse_annotation_items(None) is None


def test_parse_patch_items_variants():
    assert executor._parse_patch_items('{"patches": [{"idx": 1}]}') == [{"idx": 1}]
    assert executor._parse_patch_items('[{"idx": 1}]') == [{"idx": 1}]
    assert executor._parse_patch_items("[]") == []
    assert executor._parse_patch_items("嗯") is None


# ── 纯函数：归一化 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expect",
    [
        ("high", "high"),
        ("HIGH", "high"),
        ("严重", "high"),
        ("Critical", "high"),
        ("medium", "medium"),
        ("中", "medium"),
        ("moderate", "medium"),
        ("low", "low"),
        ("低", "low"),
        ("minor", "low"),
    ],
)
def test_norm_severity_matrix(raw, expect):
    assert executor._norm_severity(raw) == expect


def test_norm_severity_unknown_falls_back_to_medium():
    assert executor._norm_severity("天知道") == "medium"
    assert executor._norm_severity(None) == "medium"
    assert executor._norm_severity(3) == "medium"


def test_clean_str_rejects_non_str_and_strips_control():
    assert executor._clean_str(None) == ""
    assert executor._clean_str(123) == ""
    assert executor._clean_str("  a\x00b\x1fc  ") == "abc"
    assert executor._clean_str("abcdef", 3) == "abc"


def test_norm_token_fallback_on_empty():
    assert executor._norm_token("  FORMAT ") == "format"
    assert executor._norm_token("") == "other"
    assert executor._norm_token(None, "x") == "x"


def test_summary_from_stats():
    assert executor._summary_from_stats({"total": 0, "high": 0, "medium": 0, "low": 0}) == "未发现问题"
    s = executor._summary_from_stats({"total": 3, "high": 1, "medium": 1, "low": 1})
    assert "3" in s and "高 1" in s and "中 1" in s and "低 1" in s


# ── 纯函数：锚点 ─────────────────────────────────────────────────
def _items(*texts):
    return [{"index": i, "text": t, "addr": f"a{i}"} for i, t in enumerate(texts)]


def test_compute_anchor_unique_hit():
    items = _items("封面", "投标文件缺少封面，请补充")
    a = executor._compute_anchor(items, "投标文件缺少封面")
    assert a["p_idx"] == 1 and a["a_occ"] == 1 and a["p_total"] == 2
    assert len(a["p_hash"]) == 16


def test_compute_anchor_missing_returns_empty():
    assert executor._compute_anchor(_items("甲"), "乙") == {}


def test_compute_anchor_two_paragraphs_returns_empty():
    assert executor._compute_anchor(_items("缺封面", "也缺封面"), "缺封面") == {}


def test_compute_anchor_twice_in_one_paragraph_returns_empty():
    assert executor._compute_anchor(_items("缺封面，真的缺封面"), "缺封面") == {}


def test_compute_anchor_too_short_returns_empty():
    assert executor._compute_anchor(_items("甲"), "甲") == {}


def test_compute_anchor_normalizes_whitespace_before_matching():
    """Word 把同一句拆进多段/多空白时，归一化后仍应命中（与前端同口径）。"""
    a = executor._compute_anchor(_items("投标 文件 缺少 封面"), "投标文件缺少封面")
    assert a["p_idx"] == 0


# ── 纯函数：版本基线 ─────────────────────────────────────────────
def test_latest_version_name_picks_latest_non_null_upto_round():
    from types import SimpleNamespace as NS

    rounds = [NS(id="r1", round_no=1, minio_path=None), NS(id="r2", round_no=2, minio_path="frv-t-v2"), NS(id="r3", round_no=3, minio_path="frv-t-v3")]
    assert executor._latest_version_name(rounds, rounds[2]) == "frv-t-v2"
    assert executor._latest_version_name(rounds, rounds[0]) is None


# ── 纯函数：正文装配 ─────────────────────────────────────────────
def test_body_fixture_clears_min_chars_on_its_own():
    """自检：`_BODY` 单段必须自己就能过闸门（空白不计）。

    各 fixture 都是「片段 + _BODY」或纯 `_BODY`，只要这条成立，就不会有 fixture 因为
    「补的字不够」而静默走成空白件路径（那种失败会伪装成断言不匹配，很难查）。
    """
    assert len(norm_ws(_BODY)) >= executor.MIN_FILE_TEXT_CHARS


def test_compose_file_text_joins_non_empty_and_errors_when_blank():
    long_enough = "这是一段足够长的正文内容，用于通过最小长度校验。" * 3
    out = executor._compose_file_text(_items("", long_enough, "  "))
    assert out.startswith("这是一段")
    with pytest.raises(executor.FileReviewError):
        executor._compose_file_text(_items("", "  "))


def test_compose_file_text_marks_truncation(monkeypatch):
    monkeypatch.setattr(executor, "FILE_TEXT_MAX_CHARS", 20)
    out = executor._compose_file_text(_items("啊" * 100))
    assert out.startswith("啊" * 20) and executor.TRUNCATED_NOTE.strip() in out


# ── 纯函数：docx 载入 ────────────────────────────────────────────
def test_load_docx_items_rejects_non_zip():
    with pytest.raises(executor.FileReviewError):
        executor._load_docx_items(b"%PDF-1.4 not a zip")


def test_load_docx_items_rejects_zip_without_docx_parts():
    """PK 开头但不是 docx：python-docx 抛 PackageNotFoundError，必须转成可读文案。"""
    with pytest.raises(executor.FileReviewError):
        executor._load_docx_items(b"PK\x03\x04garbage")


# ── 纯函数：补丁规划 ─────────────────────────────────────────────
class _A:
    def __init__(self, i):
        self.id = f"a{i}"


def test_plan_patches_maps_idx_and_skips_bad():
    chosen = [_A(1), _A(2), _A(3)]
    parsed = [{"idx": 1, "find": "x", "replace": "y"}, {"idx": 99, "find": "z", "replace": "w"}, {"idx": "abc", "find": "q", "replace": "r"}, {"idx": 2, "find": "x", "replace": "y"}]
    patches, by_pos = executor._plan_patches(parsed, chosen)
    assert [p["find"] for p in patches] == ["x", "x"]
    assert by_pos == {0: 1, 1: 2}


def test_plan_patches_keeps_none_find_verbatim():
    """find=None 不能被折成 ""：patcher 对非 str find 的跳过规则必须原样生效。"""
    patches, _ = executor._plan_patches([{"idx": 1, "find": None, "replace": "y"}], [_A(1)])
    assert patches == [{"find": None, "replace": "y"}]


def test_plan_patches_unescapes_double_escaped_newline():
    """LLM 双重转义纠正：JSON 里 `\\\\n` 经 json.loads 后残留字面「\\n」两字符，
    必须还原成真实换行，否则与文档任何段落文本都不可能相等（demo05 生产事故根因）。"""
    parsed = [{"idx": 1, "find": "甲行\\n乙行", "replace": "甲行改\\n乙行"}]
    patches, _ = executor._plan_patches(parsed, [_A(1)])
    assert patches == [{"find": "甲行\n乙行", "replace": "甲行改\n乙行"}]


def test_plan_patches_leaves_real_newline_and_plain_backslash_alone():
    """已是真实换行的 find 不动；不含「\\n」序列的普通反斜杠文本也不动。"""
    parsed = [{"idx": 1, "find": "甲行\n乙行", "replace": "反斜杠\\文本"}]
    patches, _ = executor._plan_patches(parsed, [_A(1)])
    assert patches == [{"find": "甲行\n乙行", "replace": "反斜杠\\文本"}]


def test_fix_prompt_and_system_forbid_newline_in_find():
    """提示词必须明确 find 不得含换行（单段落地闸门的契约前置到生成侧）。"""
    assert "不得包含换行" in executor.FIX_SYSTEM
    round_row = SimpleNamespace(user_query="修一下")
    chosen = [_A(1)]
    chosen[0].type, chosen[0].severity = "format", "high"
    chosen[0].matched_text, chosen[0].issue, chosen[0].suggestion = "原文", "问题", ""
    prompt = executor._build_fix_prompt(round_row, "正文", chosen)
    assert "不得包含换行" in prompt


def test_build_fix_prompt_numbers_items_and_states_uniqueness_rule():
    chosen = [_A(1)]
    chosen[0].type, chosen[0].severity = "format", "high"
    chosen[0].matched_text, chosen[0].issue, chosen[0].suggestion = "缺封面", "没封面", "补封面"
    from types import SimpleNamespace as NS

    p = executor._build_fix_prompt(NS(user_query="看看"), "文档正文", chosen)
    assert "[1]" in p and "缺封面" in p and "文档正文" in p and "唯一" in p


def test_retrieval_query_uses_template_name_and_user_query():
    from types import SimpleNamespace as NS

    q = executor._retrieval_query(NS(user_query="看看格式"), NS(name="投标文件格式规范"))
    assert "格式规范" in q and "看看格式" in q
    assert executor._retrieval_query(NS(user_query=""), NS(name="")) == "招标文件要求"


# ── 集成：审查轮 ─────────────────────────────────────────────────
def test_execute_task_review_happy_path(monkeypatch, fstore):
    tid, blob = PFX + "-r1", _docx(["投标文件缺少封面" + _BODY, "其余内容正常" + _BODY])
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "投标文件缺少封面", "type": "format", "severity": "严重", "issue": "缺封面", "suggestion": "补上"}])
    _wire(monkeypatch, blob=blob, raw=raw)

    executor.execute_task(tid)

    row = _round(rid)
    assert row.status == "annotated"
    assert "1" in row.summary and "高 1" in row.summary
    anns = _anns(tid)
    assert len(anns) == 1
    assert anns[0].severity == "high" and anns[0].source == "ai" and anns[0].status == "open"
    assert anns[0].file_version == "v1" and anns[0].tenant_id == PFX
    anchor = json.loads(anns[0].anchor)
    assert anchor["p_idx"] == 0 and anchor["a_occ"] == 1 and anchor["p_total"] == 2


def test_execute_task_unknown_task_is_noop(monkeypatch, fstore):
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    executor.execute_task(PFX + "-nope")
    assert called == []


def test_execute_task_finished_round_is_noop(monkeypatch, fstore):
    tid = PFX + "-done"
    _mk_template()
    rid = _mk_round(tid, status="done")
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    executor.execute_task(tid)
    assert called == [] and _round(rid).status == "done"


def test_execute_task_unparseable_llm_marks_failed_without_annotations(monkeypatch, fstore):
    tid = PFX + "-bad"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="我不知道该怎么回答")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "无法解析" in row.error
    assert _anns(tid) == []
    assert "我不知道" in row.llm_raw  # 原始响应留档，便于排查


def test_execute_task_empty_annotation_array_marks_annotated(monkeypatch, fstore):
    """LLM 明确回空数组 = 「未发现问题」，不能判 failed（否则用户被迫无效重试）。"""
    tid = PFX + "-empty"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "annotated" and row.summary == "未发现问题" and _anns(tid) == []


def test_execute_task_retrieval_failure_marks_failed(monkeypatch, fstore, caplog):
    """检索失败不得静默按「零参考」继续——那会让用户以为「标准就是这些」。

    但 error 列**不得**带上内部异常原文：T9 会把它原样透给前端，DB 地址/内网拓扑
    是白给的信息泄露，且对用户毫无指导价值。原文只进服务端日志。
    """
    tid = PFX + "-retrfail"
    _mk_template()
    rid = _mk_round(tid, kb_ids=["kb-1"])

    def _boom(*a, **k):
        raise RuntimeError("Can't connect to MySQL server on '10.0.0.1:3306'")

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx([_BODY]))
    monkeypatch.setattr(executor, "_retrieve_chunks", _boom)
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    with caplog.at_level(logging.ERROR, logger="rag.svr.file_review.executor"):
        executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed"
    assert row.error == "服务端内部错误，请稍后重试（详见服务端日志）"
    assert "10.0.0.1" not in row.error and "RuntimeError" not in row.error
    # 原文确实被 logger.exception 记了（不然排查无从下手）
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]
    assert errs and "10.0.0.1" in str(errs[-1].exc_info[1])


def test_execute_task_unsupported_file_type_marks_failed(monkeypatch, fstore):
    tid = PFX + "-pdf"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=b"%PDF-1.4 xx", raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "暂不支持审核该文件类型" in row.error


def test_execute_task_blank_document_marks_failed(monkeypatch, fstore):
    """扫描件提不出文字：不能把空正文喂给 LLM 让它「审视全文」（等于请它编）。"""
    tid = PFX + "-blank"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["", "  "]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "failed"
    assert "扫描件" in _round(rid).error


def test_execute_task_short_document_marks_failed(monkeypatch, fstore):
    """近空正文同样必须拦下：只带页眉的扫描件（非零字，但远不足 50 字）不得送审。"""
    tid = PFX + "-short"
    _mk_template()
    rid = _mk_round(tid)
    header_only = "投标文件（正本）"
    assert 0 < len(norm_ws(header_only)) < executor.MIN_FILE_TEXT_CHARS
    _wire(monkeypatch, blob=_docx([header_only]), raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed"
    assert "扫描件" in row.error


def test_execute_task_invalid_template_id_falls_back_to_default(monkeypatch, fstore):
    tid = PFX + "-tplfb"
    _mk_template()
    rid = _mk_round(tid, template_id="not-exist-tpl")
    _wire(monkeypatch, blob=_docx([_BODY]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"


def test_execute_task_bad_placeholder_in_template_marks_failed(monkeypatch, fstore):
    tid = PFX + "-tplbad"
    _mk_template(user_tpl="需求：{user_query} 未知：{nope}")
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "failed"


def test_execute_task_persisted_kb_ids_are_used_for_retrieval(monkeypatch, fstore):
    tid = PFX + "-kbs"
    _mk_template()
    rid = _mk_round(tid, kb_ids=["kb-a", "kb-b"])
    seen = []

    def _spy(tenant_id, kb_ids, query):
        seen.append(list(kb_ids))
        return []

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx([_BODY]))
    monkeypatch.setattr(executor, "_retrieve_chunks", _spy)
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    assert seen == [["kb-a", "kb-b"]]
    assert _round(rid).status == "annotated"


def test_execute_task_without_kb_ids_skips_retrieval(monkeypatch, fstore):
    tid = PFX + "-nokb"
    _mk_template()
    rid = _mk_round(tid)
    seen = []
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx([_BODY]))
    monkeypatch.setattr(executor, "_retrieve_chunks", lambda *a, **k: seen.append(1) or [])
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    assert seen == [] and _round(rid).status == "annotated"


def test_execute_task_rerun_does_not_duplicate_annotations(monkeypatch, fstore):
    """进程被杀后重试同一轮不得留下两套标注。"""
    tid, blob = PFX + "-rerun", _docx(["投标文件缺少封面" + _BODY])
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "投标文件缺少封面", "type": "format", "severity": "high", "issue": "缺封面"}])
    _wire(monkeypatch, blob=blob, raw=raw)
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1
    FileReviewRoundService.update_status(rid, "reviewing")  # 模拟重试
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1
    assert _round(rid).status == "annotated"


def test_execute_task_collects_references_from_kb_chunks(monkeypatch, fstore):
    """检索结果必须真的进 prompt（否则「LLM+KB 出标准」这条需求是空话）。"""
    tid = PFX + "-ref"
    _mk_template()
    _mk_round(tid, kb_ids=["kb-a"])
    prompts = []

    def _llm(tenant_id, system, user):
        prompts.append(user)
        return "[]"

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx([_BODY]))
    monkeypatch.setattr(executor, "_retrieve_chunks", lambda *a: [{"content": "投标文件必须包含封面", "doc_id": "d1", "doc_name": "招标文件", "similarity": 0.9}])
    monkeypatch.setattr(executor, "_call_llm", _llm)
    executor.execute_task(tid)
    assert "投标文件必须包含封面" in prompts[0]


# ── 集成：修复轮 ─────────────────────────────────────────────────
def _fix_setup(monkeypatch, fstore, *, tag, blob, raw, sev="high"):
    """造一个「上一轮 review 已产标注、当前轮 fixing」的局面。"""
    tid = PFX + "-" + tag
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    FileReviewAnnotationService.create(
        round_id=r1,
        task_id=tid,
        file_id=PFX + "-file",
        file_version="v1",
        anchor="{}",
        matched_text="投标文件缺少封面",
        ann_type="format",
        severity=sev,
        issue="缺封面",
        suggestion="补上",
        source="ai",
        status="open",
        tenant_id=PFX,
        created_by=PFX,
    )
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    _wire(monkeypatch, blob=blob, raw=raw)
    return tid, r1, r2, store_key(tid, "v2")


def store_key(tid, ver):
    return f"frv-{tid}-{ver}"


def test_execute_task_fix_happy_path_stores_version_and_marks_fixed(monkeypatch, fstore):
    tid, r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fix", blob=_docx(["投标文件缺少封面" + _BODY]), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]})
    )
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "本轮修复 1 项" in row.summary
    assert row.minio_path == key and key in [k[1] for k in fstore.blobs]
    texts = [p.text for p in Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs]
    # find 只替换锚片段，正文其余部分（填充段）原样保留
    assert texts == ["投标文件包含封面" + _BODY]
    assert _anns(tid)[0].status == "fixed"


def test_execute_task_fix_preserves_run_formatting(monkeypatch, fstore):
    """格式保真必须逐 run 断言：run 个数在错误实现下同样不变。"""
    d = Document()
    p = d.add_paragraph()
    r0 = p.add_run("前缀：")
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run("投标文件缺少封面")
    r1.font.size = Pt(16)
    # 填充文字单独成 run：runs[0]/runs[1] 的内容、格式与下标都不受影响（断言按下标取）
    r2 = p.add_run(_BODY)
    b = io.BytesIO()
    d.save(b)
    tid, _r1, _r2, key = _fix_setup(monkeypatch, fstore, tag="fixfmt", blob=b.getvalue(), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]}))
    executor.execute_task(tid)
    runs = Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs[0].runs
    assert "".join(r.text for r in runs) == "前缀：投标文件包含封面" + _BODY
    assert runs[0].bold is True and runs[0].font.size == Pt(10)
    assert runs[1].font.size == Pt(16)


def test_execute_task_fix_unlocatable_patch_keeps_annotation_open(monkeypatch, fstore):
    """find 在文中不唯一 → patcher 跳过 → 标注保持 open、**不产新版本**（保持原样）。"""
    tid, _r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fixskip", blob=_docx(["投标文件缺少封面" + _BODY, "投标文件缺少封面" + _BODY]), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "X"}]})
    )
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "未能唯一定位" in row.summary
    assert row.minio_path is None and fstore.blobs == {}
    assert _anns(tid)[0].status == "open"


def test_execute_task_fix_without_pending_annotations_finishes_done(monkeypatch, fstore):
    tid = PFX + "-fixnone"
    _mk_template()
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: b"")
    executor.execute_task(tid)
    assert _round(r2).status == "done" and called == []


def test_execute_task_fix_non_docx_finishes_done_with_manual_hint(monkeypatch, fstore):
    """非 docx 不得走「纯文本降级」把原件覆盖成文本。"""
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixpdf", blob=b"%PDF-1.4 xx", raw=json.dumps({"patches": [{"idx": 1, "find": "a", "replace": "b"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "手动修改" in row.summary
    assert row.minio_path is None and fstore.blobs == {}
    assert _anns(tid)[0].status == "open"


def test_execute_task_fix_unparseable_marks_failed(monkeypatch, fstore):
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixbad", blob=_docx(["投标文件缺少封面" + _BODY]), raw="嗯……")
    executor.execute_task(tid)
    assert _round(r2).status == "failed"


def test_execute_task_fix_all_idx_out_of_range_marks_failed(monkeypatch, fstore):
    """LLM 回了条目但 idx 全对不上 → 畸形响应，不能伪装成「无需改动」的 done。"""
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixoob", blob=_docx(["投标文件缺少封面" + _BODY]), raw=json.dumps({"patches": [{"idx": 42, "find": "投标文件缺少封面", "replace": "X"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "failed" and "idx" in row.error


def test_execute_task_fix_empty_patches_finishes_done(monkeypatch, fstore):
    """LLM 合法地回空 patches = 无需改动：判 failed 会诱发无效重试。"""
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixempty", blob=_docx(["投标文件缺少封面" + _BODY]), raw="[]")
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "保持原样" in row.summary


def test_execute_task_fix_caps_items_at_max_fix_items(monkeypatch, fstore):
    tid = PFX + "-fixcap"
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    for i in range(5):
        FileReviewAnnotationService.create(
            round_id=r1,
            task_id=tid,
            file_id=PFX + "-file",
            file_version="v1",
            anchor="{}",
            matched_text=f"缺项{i}",
            ann_type="format",
            severity="low",
            issue="i",
            suggestion="",
            source="ai",
            status="open",
            tenant_id=PFX,
            created_by=PFX,
        )
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    monkeypatch.setattr(executor, "MAX_FIX_ITEMS", 2)
    prompts = []
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["待修复文本" + _BODY]))
    monkeypatch.setattr(executor, "_call_llm", lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "[3]" not in prompts[0] and "[2]" in prompts[0]
    assert _round(r2).status == "done"


def test_execute_task_fix_reads_previous_fixed_version_as_input(monkeypatch, fstore):
    """多轮叠加：第 3 轮的输入必须是第 2 轮修复后的版本，不是原件。"""
    tid = PFX + "-fixchain"
    _mk_template()
    v2_key = store_key(tid, "v2")
    fstore.blobs[(f"{PFX}-downloads", v2_key)] = _docx(["已修过一次的正文" + _BODY])
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    FileReviewAnnotationService.create(
        round_id=r1,
        task_id=tid,
        file_id=PFX + "-file",
        file_version="v1",
        anchor="{}",
        matched_text="缺封面",
        ann_type="format",
        severity="high",
        issue="i",
        suggestion="",
        source="ai",
        status="open",
        tenant_id=PFX,
        created_by=PFX,
    )
    FileReviewRoundService.update_status(_mk_round(tid, status="done", file_version="v2", round_no=2), "done", minio_path=v2_key)
    r3 = _mk_round(tid, status="fixing", file_version="v3", round_no=3)
    prompts = []
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["原件正文，不该被读到" + _BODY]))
    monkeypatch.setattr(executor, "_call_llm", lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "已修过一次的正文" in prompts[0] and "原件正文" not in prompts[0]
    assert _round(r3).status == "done"


def test_execute_task_reraises_when_failure_status_cannot_be_written(monkeypatch, fstore):
    """收口写库都失败时必须 raise，把球踢回 spawn 的独立 CAS——否则轮次永久卡死。"""
    tid = PFX + "-wede"
    _mk_template()
    _mk_round(tid)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="完全无法解析")

    def _boom_update(*a, **k):
        raise RuntimeError("mysql down")

    # 直接替换 Model.update（类属性覆盖，经 FileReviewRoundService.update_status 的
    # `cls.model.update(...)` 可见），让「收口 failed」这一步也失败。
    monkeypatch.setattr(FileReviewRound, "update", _boom_update)
    with pytest.raises(RuntimeError):
        executor.execute_task(tid)


def test_execute_task_skips_annotations_outside_declared_type_scope(monkeypatch, fstore):
    """模板声明 scope=format，LLM 回了个越界 type：记录（不静默丢问题）但打 warning。"""
    tid = PFX + "-scope"
    _mk_template(types=("format",))
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "甲甲甲", "type": "clause", "severity": "low", "issue": "i"}])
    _wire(monkeypatch, blob=_docx(["甲甲甲" + _BODY]), raw=raw)
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"
    assert len(_anns(tid)) == 1 and _anns(tid)[0].type == "clause"


def test_execute_task_drops_empty_shell_annotations(monkeypatch, fstore):
    tid = PFX + "-shell"
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "", "issue": "", "severity": "low"}, {"matched_text": "甲甲甲", "issue": "真的问题", "severity": "low"}])
    _wire(monkeypatch, blob=_docx(["甲甲甲" + _BODY]), raw=raw)
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1 and _round(rid).status == "annotated"


# ── 审查轮三态：None（解析失败）/ []（明确零问题）/ 非空但全废（畸形） ──
def test_review_round_three_state_separation(monkeypatch, fstore):
    """三态必须严格分开，混同任两条都会出事故：

    - None 混成 [] → 解析失败伪造成「审核通过」（用户看到假的干净报告）；
    - [] 混成 None → LLM 明确说没问题却判故障，逼用户无效重试；
    - 非空但全废（条目在、原文与问题描述皆空）混成 [] → 同样是伪造「审核通过」。
    """
    _mk_template()

    # ① None：不可解析 → failed
    t_none = PFX + "-3s-none"
    r_none = _mk_round(t_none)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="我拒绝回答该问题")
    executor.execute_task(t_none)
    assert _round(r_none).status == "failed" and _anns(t_none) == []

    # ② []：合法空数组 = LLM 明确报告零问题 → annotated + 「未发现问题」
    t_empty = PFX + "-3s-empty"
    r_empty = _mk_round(t_empty)
    _wire(monkeypatch, blob=_docx([_BODY]), raw="[]")
    executor.execute_task(t_empty)
    row = _round(r_empty)
    assert row.status == "annotated" and row.summary == "未发现问题" and _anns(t_empty) == []

    # ③ 非空但全废 → failed（不许伪装成零问题）
    t_shell = PFX + "-3s-shell"
    r_shell = _mk_round(t_shell)
    _wire(monkeypatch, blob=_docx([_BODY]), raw=json.dumps([{"type": "low"}]))
    executor.execute_task(t_shell)
    row = _round(r_shell)
    assert row.status == "failed" and _anns(t_shell) == []
    assert row.summary is None, "畸形响应不得留下「未发现问题」这类摘要"


def test_execute_task_shell_only_annotations_keeps_previous_annotations(monkeypatch, fstore):
    """畸形响应下一条新标注都不该落，**更不该先把旧标注删了**（那是重试前的既得结果）。"""
    tid = PFX + "-shellkeep"
    _mk_template()
    rid = _mk_round(tid)  # 模拟进程被杀后滞留 reviewing 的重跑
    old = FileReviewAnnotationService.create(
        round_id=rid,
        task_id=tid,
        file_id=PFX + "-file",
        file_version="v1",
        anchor="{}",
        matched_text="上一轮的问题",
        ann_type="format",
        severity="high",
        issue="旧",
        suggestion="",
        source="ai",
        status="open",
        tenant_id=PFX,
        created_by=PFX,
    )
    _wire(monkeypatch, blob=_docx([_BODY]), raw=json.dumps([{"matched_text": "", "issue": ""}]))
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "均无可读的原文与问题描述" in row.error
    assert [a.id for a in _anns(tid)] == [old], "畸形响应不得删掉旧标注"


def test_execute_task_non_str_annotation_fields_marks_failed(monkeypatch, fstore):
    """matched_text/issue 非 str 会被 _clean_str 归空 → 与空壳同罪，不能落成零问题。"""
    tid = PFX + "-nonstr"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx([_BODY]), raw=json.dumps([{"matched_text": 123, "issue": 456}]))
    executor.execute_task(tid)
    assert _round(rid).status == "failed" and _anns(tid) == []


# ── 修复轮：落盘 / 收口 / 状态置位的失败窗口 ───────────────────────────
class _BrokenStorage(_FakeStorage):
    def put(self, bucket, name, blob):
        raise RuntimeError("minio down")


def _mk_open_ann(tid, rid, matched, file_version="v1"):
    return FileReviewAnnotationService.create(
        round_id=rid,
        task_id=tid,
        file_id=PFX + "-file",
        file_version=file_version,
        anchor="{}",
        matched_text=matched,
        ann_type="format",
        severity="high",
        issue="i",
        suggestion="",
        source="ai",
        status="open",
        tenant_id=PFX,
        created_by=PFX,
    )


_FIX_RAW = json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]})


def test_fix_round_store_failure_keeps_annotations_open_and_retry_succeeds(monkeypatch, fstore):
    """Put 失败时若已把标注标 fixed，就成了「标注说已修、文档没改、重试说没事」的死局。"""
    tid, _r1, r2, key = _fix_setup(monkeypatch, fstore, tag="fixputfail", blob=_docx(["投标文件缺少封面" + _BODY]), raw=_FIX_RAW)
    monkeypatch.setattr(executor, "settings", SimpleNamespace(STORAGE_IMPL=_BrokenStorage()))

    executor.execute_task(tid)

    row = _round(r2)
    assert row.status == "failed" and row.minio_path is None
    assert _anns(tid)[0].status == "open", "落盘失败时标注必须停在 open（否则重试无路可走）"
    # 重试：对象存储恢复 → 同一轮必须能修完，证明没有死局
    FileReviewRoundService.update_status(r2, "fixing")
    monkeypatch.setattr(executor, "settings", SimpleNamespace(STORAGE_IMPL=fstore))
    executor.execute_task(tid)
    assert _round(r2).status == "done" and _anns(tid)[0].status == "fixed"
    assert key in [k[1] for k in fstore.blobs]


def test_fix_round_recording_failure_keeps_annotations_open_and_retry_succeeds(monkeypatch, fstore):
    """落盘成功但轮次收口写库失败：标注必须还停在 open，否则重试会看到「没有待修复项」直接 done。

    收口写库是「这一轮已完成」的权威记录，标注置 fixed 是它的派生结果 —— 派生状态不能
    抢先于权威记录落地。
    """
    tid, _r1, r2, key = _fix_setup(monkeypatch, fstore, tag="fixrecfail", blob=_docx(["投标文件缺少封面" + _BODY]), raw=_FIX_RAW)
    orig_update = FileReviewRound.update
    armed = {"yes": True}

    def _flaky_update(*a, **k):
        if armed["yes"]:
            armed["yes"] = False
            raise RuntimeError("MySQL server has gone away")
        return orig_update(*a, **k)

    monkeypatch.setattr(FileReviewRound, "update", _flaky_update)
    executor.execute_task(tid)

    row = _round(r2)
    assert row.status == "failed"
    assert _anns(tid)[0].status == "open", "轮次未收口成功前不得标 fixed"
    assert key in [k[1] for k in fstore.blobs], "前置条件：本次是「落盘成功、收口失败」"
    FileReviewRoundService.update_status(r2, "fixing")
    executor.execute_task(tid)
    assert _round(r2).status == "done" and _anns(tid)[0].status == "fixed"


def test_fix_round_idx_mismatch_does_not_mark_annotation_fixed(monkeypatch, fstore, caplog):
    """LLM 把 idx 串位：find 改的是文档里**另一处**（仍唯一定位）→ 不能给原标注盖已修复。

    只按序号盖 fixed = 假修复：错的标注变 fixed，真正的问题分毫未动。
    """
    tid = PFX + "-fixmis"
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    a_id = _mk_open_ann(tid, r1, "投标文件缺少封面")
    b_id = _mk_open_ann(tid, r1, "投标文件缺少目录")
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    # pending 顺序由 create_time 决定（同毫秒并列时无序），按**实际**顺序定位 A 的槽位
    slot_a = [x.id for x in FileReviewAnnotationService.list_pending_by_task(tid)].index(a_id) + 1
    _wire(
        monkeypatch,
        blob=_docx(["投标文件缺少封面" + _BODY, "投标文件缺少目录" + _BODY]),
        raw=json.dumps({"patches": [{"idx": slot_a, "find": "投标文件缺少目录", "replace": "投标文件包含目录"}]}),
    )
    with caplog.at_level(logging.WARNING, logger="rag.svr.file_review.executor"):
        executor.execute_task(tid)
    assert _round(r2).status == "done"
    assert FileReviewAnnotationService.get_by_id(a_id).status == "open", "idx 串位不得产生假修复"
    assert FileReviewAnnotationService.get_by_id(b_id).status == "open"
    assert "does not match annotation" in caplog.text


def test_fix_round_patch_find_superset_of_matched_text_still_marks_fixed(monkeypatch, fstore):
    """find 比 matched_text 长（向外扩上下文保证全文唯一）是提示词要求的合法写法，
    关联校验不能因此永不修复。"""
    tid, _r1, r2, _key = _fix_setup(
        monkeypatch,
        fstore,
        tag="fixsup",
        blob=_docx(["前缀：投标文件缺少封面" + _BODY]),
        raw=json.dumps({"patches": [{"idx": 1, "find": "前缀：投标文件缺少封面", "replace": "前缀：投标文件包含封面"}]}),
    )
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "本轮修复 1 项" in row.summary
    assert _anns(tid)[0].status == "fixed"


# ── 摘要必须报真实待修总数（MAX_FIX_ITEMS 截断） ────────────────────────
def _mk_25_pending(tid):
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    for i in range(25):
        _mk_open_ann(tid, r1, f"待修{i}号")
    return _mk_round(tid, status="fixing", file_version="v2", round_no=2)


def test_fix_summary_reports_true_pending_total_when_capped(monkeypatch, fstore):
    """pending 25 > 单轮上限 20：摘要必须说明「本轮只处理前 20 项、其余 5 项留待下一轮」，
    否则用户以为总共只有 20 个问题（剩下 5 个既不进摘要也不进任何计数）。"""
    tid = PFX + "-fixcapmsg"
    r2 = _mk_25_pending(tid)
    monkeypatch.setattr(executor, "MAX_FIX_ITEMS", 20)
    pending = FileReviewAnnotationService.list_pending_by_task(tid)
    patches = [{"idx": i + 1, "find": a.matched_text, "replace": f"已修{i}号"} for i, a in enumerate(pending[:20])]
    _wire(monkeypatch, blob=_docx([f"待修{i}号" + _BODY for i in range(25)]), raw=json.dumps({"patches": patches}))
    executor.execute_task(tid)

    row = _round(r2)
    assert row.status == "done"
    assert "本轮修复 20 项" in row.summary
    assert "本轮仅处理前 20 项" in row.summary and "其余 5 项" in row.summary
    statuses = [a.status for a in _anns(tid)]
    assert statuses.count("fixed") == 20 and statuses.count("open") == 5


def test_fix_no_patch_summary_reports_true_pending_total_when_capped(monkeypatch, fstore):
    """「本轮未产出可落地补丁」分支同样要报真实总数（原实现报的是截断后的 chosen 数）。"""
    tid = PFX + "-fixcapmsg2"
    r2 = _mk_25_pending(tid)
    monkeypatch.setattr(executor, "MAX_FIX_ITEMS", 20)
    _wire(monkeypatch, blob=_docx([f"待修{i}号" + _BODY for i in range(25)]), raw="[]")
    executor.execute_task(tid)

    row = _round(r2)
    assert row.status == "done"
    assert "共 25 项待修复" in row.summary and "保持原样" in row.summary
    assert "本轮仅处理前 20 项" in row.summary and "其余 5 项" in row.summary


# ── 版本基线：不依赖入参顺序 ────────────────────────────────────────────
def test_latest_version_name_does_not_depend_on_input_order():
    """乱序列表下必须选 round_no 最大者（取「最后一个命中」会退回到更旧的版本）。"""
    rounds = [
        SimpleNamespace(id="r2", round_no=2, minio_path="v2"),
        SimpleNamespace(id="r1", round_no=1, minio_path="v1"),
        SimpleNamespace(id="r3", round_no=3, minio_path=None),
    ]
    assert executor._latest_version_name(rounds, rounds[2]) == "v2"


def test_latest_version_name_tie_breaks_to_later_row():
    """同 round_no 多行（该组合无唯一约束）时后者胜，与升序输入下「最后命中」等价。"""
    rounds = [
        SimpleNamespace(id="a", round_no=2, minio_path="vA"),
        SimpleNamespace(id="b", round_no=2, minio_path="vB"),
        SimpleNamespace(id="cur", round_no=3, minio_path=None),
    ]
    assert executor._latest_version_name(rounds, rounds[2]) == "vB"


# ── 输入版本对象失效：显式回退原件 ──────────────────────────────────────
def test_load_input_blob_falls_back_to_original_when_version_missing(monkeypatch, fstore, caplog):
    """版本对象已被清理（或桶策略变更）时回退原件并告警。

    「第 3 轮在原件基线上修」是显式约定而非静默行为：没有这条告警，用户只会看到
    「越修越回到起点」而完全无从察觉。
    """
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: b"ORIGINAL")
    row = SimpleNamespace(id="r", tenant_id=PFX, file_id="f")
    with caplog.at_level(logging.WARNING, logger="rag.svr.file_review.executor"):
        assert executor._load_input_blob(row, "frv-gone-v2") == b"ORIGINAL"
    assert "version blob missing" in caplog.text


# ── 超长 / emoji 匹配串 ────────────────────────────────────────────────
def test_clean_str_and_anchor_survive_emoji_and_long_text():
    """emoji（代理对）+ 超长：截断后仍是合法 UTF-8，锚点计算不得炸。"""
    matched = "😀" * 600
    cleaned = executor._clean_str(matched, executor.MATCHED_TEXT_MAX)
    assert len(cleaned) <= executor.MATCHED_TEXT_MAX
    assert cleaned == cleaned.encode("utf-8").decode("utf-8")  # 截断不得留下半个码点
    text = "前缀" + matched + "投标文件缺少封面" + _BODY
    anchor = executor._compute_anchor([{"index": 0, "text": text, "addr": "a0"}], cleaned)
    assert isinstance(anchor, dict)
    anchor2 = executor._compute_anchor([{"index": 0, "text": text, "addr": "a0"}], "投标文件缺少封面")
    assert anchor2["p_idx"] == 0 and anchor2["a_occ"] == 1


# ── 老 .doc（OLE2）原件就地转 docx ────────────────────────────────────
def test_load_input_blob_converts_legacy_doc(monkeypatch):
    """原件是 OLE2 魔数（老 .doc）时必须转 docx 再返回——生产事故：用户版本文件
    是 .doc，blob 直通 _load_docx_items 的 PK 校验被拒，整轮 failed。"""
    row = SimpleNamespace(id="r", tenant_id=PFX, file_id="f")
    calls = []
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: b"\xd0\xcf\x11\xe0OLDC")
    monkeypatch.setattr(executor, "_convert_doc_to_docx",
                        lambda blob: calls.append(blob) or b"PK\x03\x04new")
    assert executor._load_input_blob(row, None) == b"PK\x03\x04new"
    assert calls == [b"\xd0\xcf\x11\xe0OLDC"]


def test_load_input_blob_docx_passthrough_no_convert(monkeypatch):
    """真 docx（PK）不得触发转换；转换器异常时原样冒泡由 execute_task 记 failed。"""
    row = SimpleNamespace(id="r", tenant_id=PFX, file_id="f")
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: b"PK\x03\x04real")
    called = []
    monkeypatch.setattr(executor, "_convert_doc_to_docx", lambda b: called.append(b))
    assert executor._load_input_blob(row, None) == b"PK\x03\x04real"
    assert called == []


def test_load_input_blob_version_blob_skips_convert(monkeypatch, fstore):
    """修复版本是我们自己产出的 docx：即使（防御性）命中 OLE2 前缀判断场景，
    版本分支命中时直接返回，不走原件转换路径。"""
    fstore.blobs[(PFX + "-downloads", "frv-t-v2")] = b"PK\x03\x04fixed"
    row = SimpleNamespace(id="r", tenant_id=PFX, file_id="f")
    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: (_ for _ in ()).throw(AssertionError("must not load original")))
    assert executor._load_input_blob(row, "frv-t-v2") == b"PK\x03\x04fixed"


# ── 修复对比 + 回退：补丁落库与 perform_revert ────────────────────────


def test_execute_task_fix_persists_applied_patch_on_annotation(monkeypatch, fstore):
    """「确实修好了」那一刻（过 _patch_matches_annotation 闸）必须把实际补丁
    {find, replace} 同笔写进 patch_json——它是前端对比与回退的唯一权威记录。"""
    tid, _r1, _r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixpatch",
        blob=_docx(["投标文件缺少封面" + _BODY]),
        raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]}),
    )
    executor.execute_task(tid)
    ann = _anns(tid)[0]
    assert ann.status == "fixed"
    assert json.loads(ann.patch_json) == {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}


def test_execute_task_fix_skipped_patch_leaves_patch_json_empty(monkeypatch, fstore):
    """未落地的补丁不得写 patch_json（open 行永远无补丁——前端据此隐藏对比）。"""
    tid, _r1, _r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixpatchskip",
        blob=_docx(["投标文件缺少封面" + _BODY, "投标文件缺少封面" + _BODY]),
        raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "X"}]}),
    )
    executor.execute_task(tid)
    ann = _anns(tid)[0]
    assert ann.status == "open" and ann.patch_json == ""


def _revert_setup(tag, *, status="fixed", patch=None, blob=None, minio=True):
    """造一条 fixed+patch 标注 + 一轮已落盘成稿（round 2, v2），供 perform_revert 用。
    minio_path 走 update_status extra 合入（create_round 签名不收它，与生产同径）。"""
    tid = PFX + "-" + tag
    _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    rid2 = _mk_round(tid, status="done", file_version="v2", round_no=2)
    if minio:
        FileReviewRoundService.update_status(rid2, "done", minio_path=store_key(tid, "v2"))
    aid = FileReviewAnnotationService.create(
        round_id=rid2,
        task_id=tid, file_id=PFX + "-file", file_version="v1", anchor="{}",
        matched_text="投标文件缺少封面", ann_type="format", severity="high",
        issue="缺封面", suggestion="补上", source="ai", status="open",
        tenant_id=PFX, created_by=PFX,
    )
    FileReviewAnnotationService.update_status(
        aid, status, patch_json=json.dumps(patch, ensure_ascii=False) if patch is not None else "")
    if blob is not None:
        st.blobs[(f"{PFX}-downloads", store_key(tid, "v2"))] = blob
    return aid, FileReviewAnnotation.get_by_id(aid)


st = _FakeStorage()


@pytest.fixture
def fst(monkeypatch):
    st.blobs.clear()
    monkeypatch.setattr(executor, "settings", SimpleNamespace(STORAGE_IMPL=st))
    return st


def test_perform_revert_requires_fixed_status(fst):
    aid, row = _revert_setup("rv-open", status="open",
                             patch={"find": "A", "replace": "B"})
    with pytest.raises(executor.FileReviewError, match="只有已修复/已确认保留的批注才能回退"):
        executor.perform_revert(row)
    assert FileReviewAnnotation.get_by_id(aid).status == "open"


def test_perform_revert_allowed_from_resolved(fst):
    """确认保留（resolved）后仍可回退：确认只是标记，文档同样含修复内容。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "rv-resolved", status="resolved", patch=patch,
        blob=_docx(["投标文件包含封面" + _BODY]))
    out = executor.perform_revert(row)
    assert out["version"] == "v3"
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "open" and json.loads(after.patch_json) == patch


def test_perform_revert_rejects_empty_and_garbage_patch_json(fst):
    # 空 patch_json（旧版本修复未存档）
    aid, row = _revert_setup("rv-empty", patch=None)
    with pytest.raises(executor.FileReviewError, match="没有可回退的修复记录"):
        executor.perform_revert(row)
    # patch_json 是垃圾（脏值）
    _aid2, row2 = _revert_setup("rv-garbage", patch={"find": "A", "replace": "B"})
    FileReviewAnnotation.update(patch_json="not-json").where(
        FileReviewAnnotation.id == row2.id).execute()
    row2 = FileReviewAnnotation.get_by_id(row2.id)
    with pytest.raises(executor.FileReviewError, match="没有可回退的修复记录"):
        executor.perform_revert(row2)
    assert FileReviewAnnotation.get_by_id(aid).status == "fixed"


def test_perform_revert_rejects_deletion_and_empty_find(fst):
    # 删除型（replace=""）逆向后 find=""，patcher 恒跳过 → 显式拒绝
    _aid, row = _revert_setup("rv-del", patch={"find": "被删段", "replace": ""})
    with pytest.raises(executor.FileReviewError, match="删除型修复"):
        executor.perform_revert(row)
    # find=""（纯插入型）无原文可恢复
    _aid2, row2 = _revert_setup("rv-nofind", patch={"find": "", "replace": "插入的文本"})
    with pytest.raises(executor.FileReviewError, match="缺少原文内容"):
        executor.perform_revert(row2)


def test_perform_revert_rejects_when_source_text_gone(fst):
    """后续轮又改过该处 → 逆补丁不命中 → 拒绝且批注保持 fixed+patch 原样。"""
    aid, row = _revert_setup(
        "rv-gone",
        patch={"find": "投标文件缺少封面", "replace": "投标文件包含封面"},
        # 文中已无 replace 文本（被后续轮改成第三种内容）
        blob=_docx(["投标文件改成了别的" + _BODY]),
    )
    with pytest.raises(executor.FileReviewError, match="原文已被后续修复改动"):
        executor.perform_revert(row)
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "fixed" and json.loads(after.patch_json)["find"] == "投标文件缺少封面"
    # 未产任何新轮次/新对象
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 2


def test_perform_revert_rejects_when_no_stored_blob(fst):
    aid, row = _revert_setup("rv-noblob", patch={"find": "A", "replace": "B"}, minio=False)
    with pytest.raises(executor.FileReviewError, match="文档版本内容缺失"):
        executor.perform_revert(row)


def test_perform_revert_happy_path_restores_text_and_creates_revert_round(fst):
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "rv-ok", patch=patch, blob=_docx(["投标文件包含封面" + _BODY]))
    out = executor.perform_revert(row)
    assert out == {"version": "v3", "round_no": 3}

    # 新版本字节 = 原文恢复
    texts = [p.text for p in Document(io.BytesIO(
        st.blobs[(f"{PFX}-downloads", store_key(row.task_id, "v3"))])).paragraphs]
    assert texts == ["投标文件缺少封面" + _BODY]

    # revert 轮：终态一次写齐，不烧修复额度
    rounds = FileReviewRoundService.get_by_task(row.task_id)
    rv = [r for r in rounds if r.round_no == 3][0]
    assert rv.status == "done" and rv.kind == "revert"
    assert rv.minio_path == store_key(row.task_id, "v3")
    assert "回退批注修复" in rv.summary
    from api.db.services.file_review_service import fix_rounds_left, MAX_FIX_ROUNDS
    # r2 是真修复轮计 1 次；v3 revert 轮不吃额度 → 剩 MAX-1 而非 MAX-2
    assert fix_rounds_left(rounds) == MAX_FIX_ROUNDS - 1

    # 批注回 open + patch **保留**（自由切换：前端据此显示「恢复 AI 修改」）
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "open" and json.loads(after.patch_json) == patch


def test_perform_revert_second_call_rejected_after_first(fst):
    """回退后状态是 open（patch 保留）：状态闸拒绝二次回退（防重复建轮）。
    再切回去走 perform_reapply，不是再回退。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    _aid, row = _revert_setup("rv-twice", patch=patch, blob=_docx(["投标文件包含封面" + _BODY]))
    executor.perform_revert(row)
    with pytest.raises(executor.FileReviewError):
        executor.perform_revert(FileReviewAnnotation.get_by_id(_aid))
    assert len([r for r in FileReviewRoundService.get_by_task(row.task_id) if r.round_no == 4]) == 0


def test_perform_revert_stale_row_rejected_by_in_lock_refetch(fst):
    """并发双回退只成功一次：入参行是取锁前的旧快照，等锁期间另一请求已把批注
    改回 open（模拟并发回退完成）——perform_revert 必须在 _ADMIT_LOCK 内按 id
    重取行再判定，而不是拿陈旧入参照跑，否则逆补丁被重复应用=修复被静默前放。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "rv-stale", patch=patch, blob=_docx(["投标文件包含封面" + _BODY]))
    # 模拟并发请求抢先完成回退（拿锁→改库→放锁），本调用手里只剩旧快照 row
    FileReviewAnnotationService.update_status(aid, "open")
    stale = FileReviewAnnotation.get_by_id(aid)
    stale.status = "fixed"
    stale.patch_json = json.dumps(patch, ensure_ascii=False)
    with pytest.raises(executor.FileReviewError, match="只有已修复/已确认保留的批注才能回退"):
        executor.perform_revert(stale)
    # 批注仍停在 open（未被陈旧快照二次处理），也无 round 4
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "open"
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 2


# ── 恢复 AI 修改（perform_reapply）：回退的对偶，原文 ⇄ AI 修改自由切换 ──


def test_perform_reapply_happy_path_applies_forward_and_creates_apply_round(fst):
    """回退后（open+patch，文档是原文）恢复：正补丁命中 → 产 kind='apply' 轮
    （不烧修复额度）→ 批注 resolved + patch 保留。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "ra-ok", status="open", patch=patch,
        blob=_docx(["投标文件缺少封面" + _BODY]))
    out = executor.perform_reapply(row)
    assert out["applied"] is True and out["version"] == "v3"

    texts = [p.text for p in Document(io.BytesIO(
        st.blobs[(f"{PFX}-downloads", store_key(row.task_id, "v3"))])).paragraphs]
    assert texts == ["投标文件包含封面" + _BODY]

    rounds = FileReviewRoundService.get_by_task(row.task_id)
    ap = [r for r in rounds if r.round_no == 3][0]
    assert ap.status == "done" and ap.kind == "apply"
    assert "恢复批注修改" in ap.summary

    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "resolved" and json.loads(after.patch_json) == patch

    from api.db.services.file_review_service import fix_rounds_left, MAX_FIX_ROUNDS
    # r2 修复轮计 1；revert/apply 轮都不吃额度
    assert fix_rounds_left(rounds) == MAX_FIX_ROUNDS - 1


def test_perform_reapply_full_toggle_roundtrip(fst):
    """回退 → 恢复 → 再回退：补丁始终保留，文档在原文/AI 修改间完整往复。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    original = _docx(["投标文件缺少封面" + _BODY])
    fixed = _docx(["投标文件包含封面" + _BODY])
    aid, row = _revert_setup("ra-toggle", status="fixed", patch=patch, blob=fixed)
    executor.perform_revert(row)                      # → v3 = 原文, open+patch
    row2 = FileReviewAnnotation.get_by_id(aid)
    executor.perform_reapply(row2)                    # → v4 = AI 修改, resolved+patch
    row3 = FileReviewAnnotation.get_by_id(aid)
    executor.perform_revert(row3)                     # → v5 = 原文, open+patch
    def blob_texts(v):
        return [p.text for p in Document(io.BytesIO(
            st.blobs[(f"{PFX}-downloads", store_key(row.task_id, v))])).paragraphs]
    assert blob_texts("v3") == [p.text for p in Document(io.BytesIO(original)).paragraphs]
    assert blob_texts("v4") == [p.text for p in Document(io.BytesIO(fixed)).paragraphs]
    assert blob_texts("v5") == [p.text for p in Document(io.BytesIO(original)).paragraphs]
    final = FileReviewAnnotation.get_by_id(aid)
    assert final.status == "open" and json.loads(final.patch_json) == patch
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 5


def test_perform_reapply_idempotent_when_fix_already_in_doc(fst):
    """文档已含修复（self-heal 出口把 fixed 打回 open 的历史行 / 重复恢复）：
    find 打不中但 replace 在 → 不动文档不产轮，仅置 resolved。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "ra-idem", status="open", patch=patch,
        blob=_docx(["投标文件包含封面" + _BODY]))
    out = executor.perform_reapply(row)
    assert out["applied"] is False
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 2
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "resolved" and json.loads(after.patch_json) == patch


def test_perform_reapply_rejects_when_neither_side_present(fst):
    """find/replace 都不在文档（原文被后续轮改写）→ 诚实拒绝，批注原样。"""
    patch = {"find": "投标文件缺少封面", "replace": "投标文件包含封面"}
    aid, row = _revert_setup(
        "ra-gone", status="open", patch=patch,
        blob=_docx(["投标文件改成了别的" + _BODY]))
    with pytest.raises(executor.FileReviewError, match="原文已被后续修复改动"):
        executor.perform_reapply(row)
    after = FileReviewAnnotation.get_by_id(aid)
    assert after.status == "open" and json.loads(after.patch_json) == patch
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 2


def test_perform_reapply_zero_diff_patch_flips_status_without_round(fst):
    """find == replace 的零差异补丁：应用等于没应用，不产轮只置 resolved。"""
    patch = {"find": "同文本", "replace": "同文本"}
    aid, row = _revert_setup(
        "ra-zero", status="open", patch=patch,
        blob=_docx(["同文本" + _BODY]))
    out = executor.perform_reapply(row)
    assert out["applied"] is False
    assert len(FileReviewRoundService.get_by_task(row.task_id)) == 2
    assert FileReviewAnnotation.get_by_id(aid).status == "resolved"


def test_perform_reapply_rejects_missing_and_garbage_patch(fst):
    _aid, row = _revert_setup("ra-nopatch", status="open", patch=None)
    with pytest.raises(executor.FileReviewError, match="没有可恢复的 AI 修改记录"):
        executor.perform_reapply(row)
    _aid2, row2 = _revert_setup("ra-garbage", status="open", patch={"find": "A", "replace": "B"})
    FileReviewAnnotation.update(patch_json="not-json").where(
        FileReviewAnnotation.id == row2.id).execute()
    with pytest.raises(executor.FileReviewError, match="没有可恢复的 AI 修改记录"):
        executor.perform_reapply(FileReviewAnnotation.get_by_id(row2.id))
