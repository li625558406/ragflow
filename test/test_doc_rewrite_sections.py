# test/test_doc_rewrite_sections.py
"""heading 切节对抗测试：无heading/多级/中文样式名/大纲级别/邻接标题/表格/空文档。"""

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn

from rag.svr.document_rewrite.sections import (
    NoSectionError,
    build_outline,
    get_section,
    split_sections,
)


def _doc_with_headings():
    doc = Document()
    doc.add_paragraph("文档总标题")  # 第一个 heading 之前的游离段落，不属于任何节
    doc.add_heading("实施方案", level=1)
    doc.add_paragraph("第一节正文第一段。")
    doc.add_paragraph("第一节正文第二段。")
    doc.add_heading("进度安排", level=1)
    doc.add_paragraph("第二节正文。")
    doc.add_heading("附录", level=1)  # 邻接标题：紧随其后又是标题，本节无正文段
    doc.add_heading("封底", level=1)
    doc.add_paragraph("封底说明。")
    return doc


def test_split_basic():
    sections = split_sections(_doc_with_headings())
    assert [s["section_no"] for s in sections] == [1, 2, 3, 4]
    assert sections[0]["title"] == "实施方案"
    assert sections[0]["para_start"] == 1
    assert sections[0]["para_end"] == 3
    assert sections[1]["para_start"] == 4
    assert sections[1]["para_end"] == 5
    assert sections[2]["title"] == "附录"
    # 邻接标题：附录节 para_end == para_start，body 为空
    assert sections[2]["para_end"] == sections[2]["para_start"]
    assert sections[2]["word_count"] == 0
    assert "第一节正文第一段。" in sections[0]["preview"]


def test_no_heading_raises():
    doc = Document()
    doc.add_paragraph("只有正文没有标题")
    with pytest.raises(NoSectionError):
        split_sections(doc)


def test_empty_document_raises():
    with pytest.raises(NoSectionError):
        split_sections(Document())


def test_chinese_style_name_heading():
    doc = Document()
    st = doc.styles.add_style("标题 1", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("中文样式标题", style=st)
    doc.add_paragraph("正文。")
    sections = split_sections(doc)
    assert len(sections) == 1
    assert sections[0]["title"] == "中文样式标题"


def test_outline_level_zero_heading():
    doc = Document()
    p = doc.add_paragraph("大纲级别标题")
    pPr = p._p.get_or_add_pPr()
    pPr.append(pPr.makeelement(qn("w:outlineLvl"), {qn("w:val"): "0"}))
    doc.add_paragraph("正文。")
    sections = split_sections(doc)
    assert len(sections) == 1


def test_section_containing_table_word_count_counts_paragraphs_only():
    doc = Document()
    doc.add_heading("含表节", level=1)
    doc.add_paragraph("节内段落。")
    doc.add_table(rows=1, cols=2)  # 表格不出现在 doc.paragraphs，正文段计数不受影响
    doc.add_paragraph("表后段落。")
    sections = split_sections(doc)
    assert sections[0]["para_end"] == 2  # 段落索引空间里表格不可见
    assert "节内段落。" in sections[0]["preview"]


def test_get_section_and_outline():
    sections = split_sections(_doc_with_headings())
    assert get_section(sections, 2)["title"] == "进度安排"
    assert get_section(sections, 99) is None
    outline = build_outline(sections)
    assert "第1节 实施方案" in outline
    assert "第3节 附录" in outline


def test_multi_level_headings_follow_top_level_section():
    """heading2/3 不是节边界：跟随所属一级节，作为正文段处理（spec §10）。"""
    doc = Document()
    doc.add_heading("总述", level=1)
    doc.add_paragraph("总述引言。")
    doc.add_heading("背景", level=2)
    doc.add_paragraph("背景详情。")
    doc.add_heading("措施", level=3)
    doc.add_paragraph("措施详情。")
    doc.add_heading("进度安排", level=1)
    doc.add_paragraph("第二节正文。")
    sections = split_sections(doc)
    assert len(sections) == 2
    assert sections[0]["title"] == "总述"
    # heading2/3 及其下段落全部归入第一节正文区间
    assert sections[0]["para_start"] == 0
    assert sections[0]["para_end"] == 5
    assert "背景详情。" in sections[0]["preview"] or "总述引言。" == sections[0]["preview"]
    assert sections[1]["title"] == "进度安排"
    assert sections[1]["para_start"] == 6


# ---------- 「第X章」文本章标题兜底（2026-09-13：政府范本 .doc 转换后章标题样式丢失） ----------


def test_chapter_title_plain_text_recognized():
    """普通文本段落「第一章 招标公告」按章标题识别为顶层节。"""
    doc = Document()
    doc.add_paragraph("第一章 招标公告")
    doc.add_paragraph("项目名称、标段号：某高速公路项目。")
    doc.add_paragraph("第二章 投标人须知")
    doc.add_paragraph("须知正文。")
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == ["第一章 招标公告", "第二章 投标人须知"]
    assert sections[0]["para_start"] == 0
    assert sections[0]["para_end"] == 1
    assert "项目名称" in sections[0]["preview"]


def test_chapter_title_mixed_with_heading1_in_document_order():
    """Heading 1 章（第三、四章）与普通文本章（第一、二章）混合时按文档顺序统一切节。"""
    doc = Document()
    doc.add_paragraph("第一章 招标公告")  # Normal 普通文本
    doc.add_paragraph("公告正文。")
    doc.add_heading("第二章 投标人须知", level=1)  # 真 Heading 1
    doc.add_paragraph("须知正文。")
    doc.add_paragraph("第三章 评标办法")  # Normal 普通文本
    doc.add_paragraph("评标正文。")
    sections = split_sections(doc)
    assert [s["section_no"] for s in sections] == [1, 2, 3]
    assert [s["title"] for s in sections] == ["第一章 招标公告", "第二章 投标人须知", "第三章 评标办法"]
    assert sections[2]["para_start"] == 4


def test_toc_leader_line_not_chapter_title():
    """目录行「第一章 招标公告......2」有点线引导，不是章标题。"""
    doc = Document()
    doc.add_paragraph("第一章 招标公告......2")
    doc.add_paragraph("第二章 投标人须知…………5")
    doc.add_paragraph("第一章 招标公告")
    doc.add_paragraph("真正的章正文。")
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == ["第一章 招标公告"]


def test_trailing_punct_or_long_line_not_chapter_title():
    """正文引用句（句读结尾/超长）不误判为章标题；全非章标题时仍抛 NoSectionError。"""
    doc = Document()
    doc.add_paragraph("第一章 总则的规定如下：")
    doc.add_paragraph("本章依据《第一章 招标公告》格式执行，" + "补充说明" * 20)
    with pytest.raises(NoSectionError):
        split_sections(doc)


def test_section_not_chapter_title():
    """「第一节/第1节」是 Heading 2 层级，不按章标题兜底识别为顶层节。"""
    doc = Document()
    doc.add_paragraph("第一节 评标办法前附表")
    doc.add_paragraph("第二节 评标办法正文")
    with pytest.raises(NoSectionError):
        split_sections(doc)


def test_chapter_number_variants_recognized():
    """汉字/阿拉伯/全角数字章号均识别（含零/〇：第一百零一章）；超长行排除。"""
    doc = Document()
    doc.add_paragraph("第1章 总则")
    doc.add_paragraph("第１２章 附则")  # 全角数字
    doc.add_paragraph("第十章 争议解决")
    doc.add_paragraph("第一百零一章 特别规定")
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == [
        "第1章 总则", "第１２章 附则", "第十章 争议解决", "第一百零一章 特别规定",
    ]


def test_word_auto_toc_tab_pagenum_not_chapter_title():
    """Word 自动目录：点线是制表符前导符样式，p.text 为「章标题\\t页码」——
    不含点线字符，必须按 tab+数字结尾排除，否则目录区产生幻影节。"""
    doc = Document()
    doc.add_paragraph("第一章 招标公告\t2")
    doc.add_paragraph("第二章 投标人须知\t15")
    doc.add_heading("第一章 招标公告", level=1)
    doc.add_paragraph("真正的章正文。")
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == ["第一章 招标公告"]
    assert sections[0]["para_start"] == 2


def test_toc_style_paragraph_not_chapter_title():
    """TOC 目录样式段落（.doc→docx 转换后样式通常保留）即使文本干净也排除。"""
    doc = Document()
    st = doc.styles.add_style("toc 1", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("第一章 招标公告", style=st)
    doc.add_paragraph("第二章 投标人须知")  # Normal，正常识别
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == ["第二章 投标人须知"]


def test_heading2_chapter_title_promoted_to_section():
    """Heading 2 样式的「第X章」段落也被提升为顶层节（章是重写单元，与
    heading 级别无关——有意行为，防止后人当 bug 修掉）。"""
    doc = Document()
    doc.add_heading("第一章 总则", level=2)
    doc.add_paragraph("正文。")
    sections = split_sections(doc)
    assert len(sections) == 1
    assert sections[0]["title"] == "第一章 总则"


def test_adjacent_plain_chapter_titles():
    """邻接的普通文本章标题：para_end == para_start，无正文段。"""
    doc = Document()
    doc.add_paragraph("第一章 招标公告")
    doc.add_paragraph("第二章 投标人须知")
    doc.add_paragraph("须知正文。")
    sections = split_sections(doc)
    assert [s["title"] for s in sections] == ["第一章 招标公告", "第二章 投标人须知"]
    assert sections[0]["para_end"] == sections[0]["para_start"]
    assert sections[0]["word_count"] == 0
