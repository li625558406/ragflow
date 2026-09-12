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
