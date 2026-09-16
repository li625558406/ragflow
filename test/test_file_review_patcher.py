"""patcher 对抗测试：唯一匹配 + docx 格式保真替换。
docx 用例一律用真实字节（python-docx 现造现读），不打桩——复用的是私有符号，
只有真跑才能证明耦合点还在。"""
import io
import zipfile

import pytest
from docx import Document

from rag.svr.file_review.patcher import (
    apply_patches,
    apply_patches_to_docx,
    find_unique,
)


def _docx_bytes(paragraphs, header=None, table=None, split_runs=False):
    """造真实 docx 字节。split_runs=True 时把每段从中间切成两个 run，
    模拟 Word 把一句话拆进多个 run 的常态（跨 run 替换的核心场景）。"""
    doc = Document()
    for text in paragraphs:
        p = doc.add_paragraph()
        if split_runs and len(text) > 1:
            mid = len(text) // 2
            p.add_run(text[:mid])
            p.add_run(text[mid:])
        else:
            p.add_run(text)
    if header:
        doc.sections[0].header.paragraphs[0].add_run(header)
    if table:
        doc.add_table(rows=1, cols=1).rows[0].cells[0].paragraphs[0].add_run(table)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _texts(blob):
    """文档全文可见段落文本（Document() 初始 0 段，故无需过滤空段）。"""
    return [p.text for p in Document(io.BytesIO(blob)).paragraphs]


# ── find_unique：唯一 / 缺失 / 歧义 ──────────────────────────────
def test_find_unique_returns_pos_when_single_match():
    assert find_unique('投标人应满足：abc 资质等级', 'abc 资质等级') > 0


def test_find_unique_returns_neg1_when_zero_match():
    assert find_unique('hello world', 'xyz') == -1


def test_find_unique_returns_neg2_when_multi_match():
    assert find_unique('foo bar foo', 'foo') == -2  # 歧义


def test_find_unique_empty_find_is_missing_not_ambiguous():
    """空 find 必须判「找不到」而不是「歧义」——否则空串会被当成匹配任意位置的锚。"""
    assert find_unique('anything', '') == -1


def test_find_unique_non_overlapping_semantics():
    """非重叠口径与 str.replace 一致：'aaa' 里找 'aa' 只算 1 次（不是 2 次）。
    若这里判成歧义（-2）而替换本身能完成，两处口径打架会出现「能改却跳过」。"""
    assert find_unique('aaa', 'aa') == 0


def test_find_unique_find_longer_than_text():
    assert find_unique('short', 'much longer than text') == -1


def test_find_unique_unicode_and_whole_text():
    assert find_unique('报价￥1,000.00元', '￥1,000.00元') > 0
    assert find_unique('整段就是锚', '整段就是锚') == 0


# ── apply_patches：纯文本层 ─────────────────────────────────────
def test_apply_patches_single_replace():
    out, applied = apply_patches('报价：1000元', [{'find': '1000元', 'replace': '1500元'}])
    assert out == '报价：1500元'
    assert applied == [True]


def test_apply_patches_skips_ambiguous():
    out, applied = apply_patches('foo bar foo baz', [{'find': 'foo', 'replace': 'QUX'}])
    assert out == 'foo bar foo baz'  # 歧义 → 一字不动
    assert applied == [False]


def test_apply_patches_skips_missing():
    out, applied = apply_patches('报价：1000元', [{'find': '不存在的片段', 'replace': 'x'}])
    assert out == '报价：1000元'
    assert applied == [False]


def test_apply_patches_sequential_sees_earlier_result():
    """后面一条 patch 必须看得见前面一条的替换结果（顺序生效）。"""
    out, applied = apply_patches(
        'A处', [{'find': 'A处', 'replace': 'B处'}, {'find': 'B处', 'replace': 'C处'}]
    )
    assert out == 'C处'
    assert applied == [True, True]


def test_apply_patches_is_order_independent_per_patch_safety():
    """第一条歧义被跳过后，第二条仍按**原文**判定，不被前一条的跳过影响。"""
    out, applied = apply_patches(
        'foo foo and bar',
        [{'find': 'foo', 'replace': 'X'}, {'find': 'bar', 'replace': 'Y'}],
    )
    assert out == 'foo foo and Y'
    assert applied == [False, True]


def test_apply_patches_empty_patches_and_none():
    assert apply_patches('text', []) == ('text', [])
    assert apply_patches('text', None) == ('text', [])


def test_apply_patches_rejects_empty_find_without_touching_text():
    """空 find 是畸形输入：跳过且不改（str.replace('', x) 会在每个字符间插入 → 灾难）。"""
    out, applied = apply_patches('abc', [{'find': '', 'replace': 'X'}])
    assert out == 'abc'
    assert applied == [False]


def test_apply_patches_malformed_element_skipped():
    """缺 find 键 / None 元素：跳过（applied=False），不抛异常打断整批。"""
    out, applied = apply_patches('abc', [{'replace': 'X'}, None])
    assert out == 'abc'
    assert applied == [False, False]


# ── apply_patches_to_docx：格式保真层 ───────────────────────────
def test_apply_patches_to_docx_single_unique_replace():
    blob = _docx_bytes(['报价：1000元', '工期：30天'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    assert _texts(out) == ['报价：1500元', '工期：30天']


def test_apply_patches_to_docx_cross_run_preserves_other_runs():
    """跨 run 句：只重写 anchor 覆盖区间，段内其余 run 的文本/格式不动。"""
    blob = _docx_bytes(['报价：1000元'], split_runs=True)
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    assert _texts(out) == ['报价：1500元']
    # 跨 run 替换后仍是两个 run（区间外结构不被压平）
    assert len(Document(io.BytesIO(out)).paragraphs[0].runs) == 2


def test_apply_patches_to_docx_covers_header():
    """页眉里的问题句也要能改——这是复用 _build_addr_map 而非 doc.paragraphs 的理由。"""
    blob = _docx_bytes(['正文段落'], header='秘密标记')
    out, applied = apply_patches_to_docx(blob, [{'find': '秘密标记', 'replace': '公开标记'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert '公开标记' in d.sections[0].header.paragraphs[0].text


def test_apply_patches_to_docx_covers_table_cell():
    blob = _docx_bytes(['正文'], table='表内2000元')
    out, applied = apply_patches_to_docx(blob, [{'find': '2000元', 'replace': '2500元'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert '表内2500元' in d.tables[0].rows[0].cells[0].paragraphs[0].text


def test_apply_patches_to_docx_skips_when_in_two_paragraphs():
    """全文档两处命中 → 歧义 → 两处都不动（不是改第一处）。"""
    blob = _docx_bytes(['报价：1000元', '保证金：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元', '保证金：1000元']


def test_apply_patches_to_docx_ambiguity_spans_body_and_table():
    """跨区域歧义同样要拦：正文 1 处 + 表格 1 处 = 2 个候选段落 → 跳过。"""
    blob = _docx_bytes(['正文3000元'], table='表内3000元')
    out, applied = apply_patches_to_docx(blob, [{'find': '3000元', 'replace': '4000元'}])
    assert applied == [False]
    assert '3000元' in _texts(out)[0]
    assert '表内3000元' in Document(io.BytesIO(out)).tables[0].rows[0].cells[0].paragraphs[0].text


def test_apply_patches_to_docx_skips_when_twice_in_one_paragraph():
    """同一段里出现两次 → 段内歧义 → 跳过。"""
    blob = _docx_bytes(['区间为1000元到1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [False]
    assert _texts(out) == ['区间为1000元到1000元']


def test_apply_patches_to_docx_skips_missing():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '不存在的片段', 'replace': 'x'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_sequential():
    """docx 层同样顺序生效：后一条看得见前一条写入的文本。"""
    blob = _docx_bytes(['A处标记'])
    out, applied = apply_patches_to_docx(
        blob, [{'find': 'A处', 'replace': 'B处'}, {'find': 'B处', 'replace': 'C处'}]
    )
    assert applied == [True, True]
    assert _texts(out) == ['C处标记']


def test_apply_patches_to_docx_empty_patches_returns_input_untouched():
    """无 patch 不解析也不重存（重存会重排 XML 字节），原字节原样返回。"""
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [])
    assert out == blob
    assert applied == []


def test_apply_patches_to_docx_rejects_empty_find():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '', 'replace': 'X'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_empty_replace_deletes_text():
    """replace 为空串 = 删除该片段，是合法操作（不是"没改"）。"""
    blob = _docx_bytes(['报价：1000元（含税）'])
    out, applied = apply_patches_to_docx(blob, [{'find': '（含税）', 'replace': ''}])
    assert applied == [True]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_malformed_element_skipped():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'replace': 'X'}, None])
    assert applied == [False, False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_raises_on_non_docx_bytes():
    """非 docx 字节必须响亮失败（实测抛 zipfile.BadZipFile），不能静默返回原样——
    静默会让「修复成功」的假象流到用户面前。T6 捕获后把该轮置 failed。
    注意：patches 为空时走短路，不会解析，故此处必须传一条 patch 才会触发解析。
    断言具体类型而非裸 Exception：裸 Exception 会被实现自身的 bug（AttributeError/
    TypeError 等）蒙混过关，测不出「正确拒绝」与「崩了」的区别。"""
    with pytest.raises(zipfile.BadZipFile):
        apply_patches_to_docx(b'not a docx at all', [{'find': 'a', 'replace': 'b'}])


def test_apply_patches_to_docx_applied_length_matches_patches():
    """applied 与 patches 一一对应（T6 靠下标把「未修复」写回对应标注）。"""
    blob = _docx_bytes(['报价：1000元', '工期：30天'])
    _, applied = apply_patches_to_docx(
        blob,
        [{'find': '1000元', 'replace': '1500元'},
         {'find': '30天', 'replace': '60天'},
         {'find': '不存在', 'replace': 'x'}],
    )
    assert applied == [True, True, False]
