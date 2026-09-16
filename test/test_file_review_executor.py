# test/test_file_review_executor.py
"""executor 对抗测试：多轮状态机 / LLM 输出解析容错 / 锚点唯一性 / 修复落盘 / 幂等与失败收口。

真实 MySQL（同 test_file_review_service.py 的取舍）+ 真实 docx 字节（同 test_file_review_patcher.py）：
解析与锚定复用的是私有符号，只有真造真读才能证明耦合点还在；打桩掉 DB 后只剩
「函数被调用过」这种空断言，测不出 where 条件是否真的命中目标行。
所有测试行带 PFX 前缀，清理只按该前缀删，绝不误伤线上数据。禁止调用 migrate_db()。
"""

import io
import json

import pytest
from docx import Document
from docx.shared import Pt

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
)
from rag.svr.file_review import executor

PFX = "__test_fr_exec__"


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
    tid, blob = PFX + "-r1", _docx(["投标文件缺少封面", "其余内容正常"])
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
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="我不知道该怎么回答")
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
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "annotated" and row.summary == "未发现问题" and _anns(tid) == []


def test_execute_task_retrieval_failure_marks_failed(monkeypatch, fstore):
    """检索失败不得静默按「零参考」继续——那会让用户以为「标准就是这些」。"""
    tid = PFX + "-retrfail"
    _mk_template()
    rid = _mk_round(tid, kb_ids=["kb-1"])

    def _boom(*a, **k):
        raise RuntimeError("retriever down")

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks", _boom)
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "retriever down" in row.error


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


def test_execute_task_invalid_template_id_falls_back_to_default(monkeypatch, fstore):
    tid = PFX + "-tplfb"
    _mk_template()
    rid = _mk_round(tid, template_id="not-exist-tpl")
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"


def test_execute_task_bad_placeholder_in_template_marks_failed(monkeypatch, fstore):
    tid = PFX + "-tplbad"
    _mk_template(user_tpl="需求：{user_query} 未知：{nope}")
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
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

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
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
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks", lambda *a, **k: seen.append(1) or [])
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    assert seen == [] and _round(rid).status == "annotated"


def test_execute_task_rerun_does_not_duplicate_annotations(monkeypatch, fstore):
    """进程被杀后重试同一轮不得留下两套标注。"""
    tid, blob = PFX + "-rerun", _docx(["投标文件缺少封面"])
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

    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
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
        type="format",
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
        monkeypatch, fstore, tag="fix", blob=_docx(["投标文件缺少封面"]), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]})
    )
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "本轮修复 1 项" in row.summary
    assert row.minio_path == key and key in [k[1] for k in fstore.blobs]
    texts = [p.text for p in Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs]
    assert texts == ["投标文件包含封面"]
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
    b = io.BytesIO()
    d.save(b)
    tid, _r1, r2, key = _fix_setup(monkeypatch, fstore, tag="fixfmt", blob=b.getvalue(), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]}))
    executor.execute_task(tid)
    runs = Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs[0].runs
    assert "".join(r.text for r in runs) == "前缀：投标文件包含封面"
    assert runs[0].bold is True and runs[0].font.size == Pt(10)
    assert runs[1].font.size == Pt(16)


def test_execute_task_fix_unlocatable_patch_keeps_annotation_open(monkeypatch, fstore):
    """find 在文中不唯一 → patcher 跳过 → 标注保持 open、**不产新版本**（保持原样）。"""
    tid, _r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fixskip", blob=_docx(["投标文件缺少封面", "投标文件缺少封面"]), raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "X"}]})
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
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixbad", blob=_docx(["投标文件缺少封面"]), raw="嗯……")
    executor.execute_task(tid)
    assert _round(r2).status == "failed"


def test_execute_task_fix_all_idx_out_of_range_marks_failed(monkeypatch, fstore):
    """LLM 回了条目但 idx 全对不上 → 畸形响应，不能伪装成「无需改动」的 done。"""
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixoob", blob=_docx(["投标文件缺少封面"]), raw=json.dumps({"patches": [{"idx": 42, "find": "投标文件缺少封面", "replace": "X"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "failed" and "idx" in row.error


def test_execute_task_fix_empty_patches_finishes_done(monkeypatch, fstore):
    """LLM 合法地回空 patches = 无需改动：判 failed 会诱发无效重试。"""
    tid, _r1, r2, _key = _fix_setup(monkeypatch, fstore, tag="fixempty", blob=_docx(["投标文件缺少封面"]), raw="[]")
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
            type="format",
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
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["待修复文本"]))
    monkeypatch.setattr(executor, "_call_llm", lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "[3]" not in prompts[0] and "[2]" in prompts[0]
    assert _round(r2).status == "done"


def test_execute_task_fix_reads_previous_fixed_version_as_input(monkeypatch, fstore):
    """多轮叠加：第 3 轮的输入必须是第 2 轮修复后的版本，不是原件。"""
    tid = PFX + "-fixchain"
    _mk_template()
    v2_key = store_key(tid, "v2")
    fstore.blobs[(f"{PFX}-downloads", v2_key)] = _docx(["已修过一次的正文"])
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    FileReviewAnnotationService.create(
        round_id=r1,
        task_id=tid,
        file_id=PFX + "-file",
        file_version="v1",
        anchor="{}",
        matched_text="缺封面",
        type="format",
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
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["原件正文，不该被读到"]))
    monkeypatch.setattr(executor, "_call_llm", lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "已修过一次的正文" in prompts[0] and "原件正文" not in prompts[0]
    assert _round(r3).status == "done"


def test_execute_task_reraises_when_failure_status_cannot_be_written(monkeypatch, fstore):
    """收口写库都失败时必须 raise，把球踢回 spawn 的独立 CAS——否则轮次永久卡死。"""
    tid = PFX + "-wede"
    _mk_template()
    _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="完全无法解析")

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
    _wire(monkeypatch, blob=_docx(["甲甲甲是一段足够长的正文内容，重复重复重复重复。"]), raw=raw)
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"
    assert len(_anns(tid)) == 1 and _anns(tid)[0].type == "clause"


def test_execute_task_drops_empty_shell_annotations(monkeypatch, fstore):
    tid = PFX + "-shell"
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "", "issue": "", "severity": "low"}, {"matched_text": "甲甲甲", "issue": "真的问题", "severity": "low"}])
    _wire(monkeypatch, blob=_docx(["甲甲甲是一段足够长的正文内容，重复重复重复重复。"]), raw=raw)
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1 and _round(rid).status == "annotated"
