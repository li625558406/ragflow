"""patcher 对抗测试：唯一匹配 + docx 格式保真替换。
docx 用例一律用真实字节（python-docx 现造现读），不打桩——复用的是私有符号，
只有真跑才能证明耦合点还在。"""
import io
import zipfile

import pytest
from docx import Document
from docx.shared import Pt

from rag.svr.file_review.patcher import (
    apply_patches,
    apply_patches_to_docx,
    find_unique,
)


def _docx_bytes(paragraphs, header=None, table=None):
    """造真实 docx 字节。跨 run 场景不在此处构造——那些用例必须逐 run 设 rPr
    才有断言价值，故各自现搭（见 preserves_run_formatting / true_cross_run_straddle）。"""
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph().add_run(text)
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


def test_apply_patches_to_docx_preserves_run_formatting():
    """格式保真是复用该原语的唯一理由，必须直接断言 rPr——只数 run 个数不可靠
    （"把整段塞进首 run、其余清空"的错误实现下 run 数同样不变）。"""
    doc = Document()
    p = doc.add_paragraph()
    r0 = p.add_run('报价：')
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run('1000元')
    r1.font.size = Pt(16)
    doc.add_paragraph('工期：30天')          # 另一段，不应被动
    buf = io.BytesIO()
    doc.save(buf)

    out, applied = apply_patches_to_docx(buf.getvalue(), [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert [p.text for p in d.paragraphs] == ['报价：1500元', '工期：30天']
    runs = d.paragraphs[0].runs
    assert [r.text for r in runs] == ['报价：', '1500元']
    assert runs[0].bold is True and runs[0].font.size == Pt(10)   # 区间外 run 格式原样
    assert runs[1].font.size == Pt(16)                            # 替换值落在原 run，rPr 未被重建


def test_apply_patches_to_docx_true_cross_run_straddle():
    """锚**真正跨越** run 边界（'1000元' 被切成 '10' | '00元'）时仍能替换，
    且只重写覆盖区间、保留前后 run 的格式。"""
    doc = Document()
    p = doc.add_paragraph()
    r0 = p.add_run('报价：10')
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run('00元')
    r1.font.size = Pt(16)
    buf = io.BytesIO()
    doc.save(buf)

    out, applied = apply_patches_to_docx(buf.getvalue(), [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    runs = Document(io.BytesIO(out)).paragraphs[0].runs
    assert ''.join(r.text for r in runs) == '报价：1500元'
    assert runs[0].bold is True and runs[0].font.size == Pt(10)
    assert runs[1].font.size == Pt(16)


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


# ── 畸形输入必须"跳过"，不能静默删字 / 打断整批 ──────────────────
def test_apply_patches_none_replace_is_skip_not_delete():
    """LLM 输出 "replace": null = 「没给出替换文本」，必须跳过而不是删除命中文本。
    （'' 才是显式删除；把 None 折成 '' 会让一次 LLM 脏字段不可逆地删掉正文。）"""
    out, applied = apply_patches('报价：1000元', [{'find': '1000元', 'replace': None}])
    assert out == '报价：1000元'
    assert applied == [False]


def test_apply_patches_to_docx_none_replace_is_skip_not_delete():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': None}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_falsy_non_str_replace_is_skip():
    """0 / False 同样不是"删除"的意思。"""
    out, applied = apply_patches(
        'ab', [{'find': 'ab', 'replace': 0}, {'find': 'ab', 'replace': False}]
    )
    assert out == 'ab'
    assert applied == [False, False]


def test_apply_patches_non_str_find_is_skip_not_crash():
    out, applied = apply_patches('abc', [{'find': 1, 'replace': 'Z'}])
    assert out == 'abc'
    assert applied == [False]


def test_apply_patches_non_dict_element_does_not_abort_batch():
    """真值非 dict 的元素跳过即可，不能让同批其余合规 patch 一起作废。"""
    out, applied = apply_patches(
        '报价：1000元', ['a-string', {'find': '1000元', 'replace': '1500元'}]
    )
    assert out == '报价：1500元'
    assert applied == [False, True]


def test_apply_patches_to_docx_non_dict_element_does_not_abort_batch():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(
        blob, ['a-string', {'find': '1000元', 'replace': '1500元'}]
    )
    assert applied == [False, True]
    assert _texts(out) == ['报价：1500元']


def test_apply_patches_to_docx_all_failed_returns_input_bytes():
    """有 patch 但全部未生效 → 文档一字未改，必须返回**原字节**：重存会重排 XML
    字节，让"零改动"被 T6 误存成"修复版新版本"。"""
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '不存在', 'replace': 'x'}])
    assert applied == [False]
    assert out == blob


# ── 跨行 patch 分解（demo05 生产事故回归：LLM 摘跨段 find，单段闸门必然 0 命中）──
def test_multiline_patch_decomposed_and_applied():
    """find/replace 按行剥去相同上下文后能一一配对 → 逐行落地。"""
    blob = _docx_bytes(['编号 3.1.1', '构成投标文件的其他资料'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '编号 3.1.1\n构成投标文件的其他资料', 'replace': '编号 3.1.2\n构成投标文件的其他资料'}],
    )
    assert applied == [True]
    assert _texts(out) == ['编号 3.1.2', '构成投标文件的其他资料']


def test_multiline_sequence_disambiguates_single_line_ambiguity():
    """单行全文多处歧义、加上后继行上下文后序列唯一 → 正确锚定（demo05
    「3.1.1 孤立编号段」形态的回归闸）。"""
    blob = _docx_bytes(['3.1.1', '构成投标文件的其他资料', '无关段落', '3.1.1', '别的段落'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '3.1.1\n构成投标文件的其他资料', 'replace': '3.1.2\n构成投标文件的其他资料'}],
    )
    assert applied == [True]
    assert _texts(out) == ['3.1.2', '构成投标文件的其他资料', '无关段落', '3.1.1', '别的段落']


def test_multiline_changed_lines_target_sequence_not_guess():
    """多变更行按序列定位：只有首个「甲行+丙行」相邻序列命中，替换落在该处，
    不会波及后面的重复「丙行」。"""
    blob = _docx_bytes(['甲行', '丙行', '丙行', '尾部'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '甲行\n丙行', 'replace': '乙行\n丁行'}],
    )
    assert applied == [True]
    assert _texts(out) == ['乙行', '丁行', '丙行', '尾部']


def test_multiline_pure_insertion_is_abandoned():
    """空 find 配非空 replace = 凭空插入新行，段落级替换无锚点 → 诚实跳过。"""
    blob = _docx_bytes(['业绩要求', '附录 4  资格审查条件'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '业绩要求\n附录 4  资格审查条件', 'replace': '业绩要求\n/\n附录 4  资格审查条件'}],
    )
    assert applied == [False]
    assert out == blob


def test_multiline_duplicate_lines_are_ambiguous_not_guessed():
    """重复行删除（find 2 行 → replace 1 行）无法判定删哪份 → 跳过，不猜。"""
    blob = _docx_bytes(['第二章  投标人须知', '第二章  投标人须知', '正文'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '第二章  投标人须知\n第二章  投标人须知', 'replace': '第二章  投标人须知'}],
    )
    assert applied == [False]
    assert out == blob


def test_multiline_patch_no_context_change_returns_false():
    """find 与 replace 完全相同（LLM 空转补丁）→ 无实际变更行 → False。"""
    blob = _docx_bytes(['甲行', '乙行'])
    out, applied = apply_patches_to_docx(
        blob, [{'find': '甲行\n乙行', 'replace': '甲行\n乙行'}]
    )
    assert applied == [False]
    assert out == blob


def test_plain_text_apply_patches_still_matches_across_lines():
    """纯文本降级路径本就支持跨行匹配（文本按行拼接），不得被分解逻辑波及。"""
    out, applied = apply_patches(
        '甲行\n乙行', [{'find': '甲行\n乙行', 'replace': '甲行改\n乙行'}]
    )
    assert applied == [True]
    assert out == '甲行改\n乙行'


# ── 序列通道失败降级单段通道（回退生产事故回归 ann 212b980c）──────────
def test_multiline_find_in_one_paragraph_falls_back_to_single():
    """find 含换行但整段摘文物理落在**一个**段落里（w:br 换行，或正向修复把含
    换行的 replace 写进单段）→ 序列通道 0 命中后降级单段通道命中。此前直接
    applied=False，回退端误报「原文已被后续修复改动」。"""
    blob = _docx_bytes(['评分项：1.技术和服务响应情况\n45.00'])
    out, applied = apply_patches_to_docx(
        blob,
        [{'find': '1.技术和服务响应情况\n45.00', 'replace': '1.技术和服务响应情况 45.00'}],
    )
    assert applied == [True]
    assert _texts(out) == ['评分项：1.技术和服务响应情况 45.00']


def test_revert_roundtrip_single_paragraph_multiline_replace():
    """正向「单行 find → 含换行 replace」落进单段后，逆补丁必须能完整还原
    （perform_revert 的数据通路对称性闸）。"""
    blob = _docx_bytes(['本项得分：30分'])
    fixed, applied = apply_patches_to_docx(
        blob, [{'find': '30分', 'replace': '28分\n（扣2分）'}]
    )
    assert applied == [True]
    reverted, applied_rev = apply_patches_to_docx(
        fixed, [{'find': '28分\n（扣2分）', 'replace': '30分'}]
    )
    assert applied_rev == [True]
    assert _texts(reverted) == _texts(blob)


def test_multiline_single_fallback_still_respects_ambiguity():
    """降级单段通道继承唯一性闸：同形两段摘文命中 2 个段落 → 仍诚实跳过。"""
    blob = _docx_bytes(['甲行\n乙行', '前言', '甲行\n乙行'])
    out, applied = apply_patches_to_docx(
        blob, [{'find': '甲行\n乙行', 'replace': '改行\n改行'}]
    )
    assert applied == [False]
    assert out == blob


def test_multiline_single_fallback_unique_in_one_of_two_paragraphs():
    """序列结构性失配（行序倒置）但单段唯一命中 → 降级通道救回。"""
    blob = _docx_bytes(['附录 4  资格审查条件', '评分项：甲行\n乙行'])
    out, applied = apply_patches_to_docx(
        blob, [{'find': '乙行\n甲行', 'replace': '乙行改\n甲行'}]
    )
    # '乙行\n甲行' 单段不存在（段内是 甲行\n乙行）→ 单段也 0 命中 → False
    assert applied == [False]
    assert out == blob
    out2, applied2 = apply_patches_to_docx(
        blob, [{'find': '甲行\n乙行', 'replace': '甲行改\n乙行改'}]
    )
    assert applied2 == [True]
    assert _texts(out2) == ['附录 4  资格审查条件', '评分项：甲行改\n乙行改']
