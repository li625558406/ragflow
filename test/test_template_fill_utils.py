import io

import pytest
from docx import Document


def _make_docx(paras, table=None):
    """paras: list[str]; table: list[list[str]] (1 表追加在末尾)"""
    doc = Document()
    for t in paras:
        doc.add_paragraph(t)
    if table:
        tbl = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, text in enumerate(row):
                tbl.rows[r].cells[c].paragraphs[0].text = text
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_docx():
    return _make_docx(
        ["项目名称：____________", "无填写点的普通段落"],
        table=[["投标单位", ""], ["日期", "____年____月____日"]],
    )


def test_iter_docx_paragraphs(sample_docx):
    from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
    items = iter_docx_paragraphs(sample_docx)
    texts = [it["text"] for it in items]
    assert texts[0] == "项目名称：____________"
    # 表格内段落扁平编号继续递增
    assert "日期" in texts
    cell_items = [it for it in items if it["addr"].startswith("cell:")]
    assert any(it["addr"] == "cell:1:1:0" for it in cell_items)


def test_extract_docx_candidates(sample_docx):
    from rag.svr.template_fill.docx_utils import extract_docx_candidates
    cands = extract_docx_candidates(sample_docx)
    # 普通段落（无填写特征）被过滤
    assert all("无填写点" not in c["text"] for c in cands)
    assert any("项目名称" in c["text"] for c in cands)
    assert any(c["addr"] == "cell:1:1:0" for c in cands)


def test_apply_docx_placeholders(sample_docx):
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    out = apply_docx_placeholders(sample_docx, [
        {"addr": "para:0", "anchor": "____________", "key": "project_name"},
        {"addr": "cell:1:1:0", "anchor": "____年____月____日", "key": "sign_date"},
    ])
    from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert "{{project_name}}" in texts[0]
    # cell:1:1:0 扁平序号 = 2 正文段 + 4 单元格段中排第 6（idx 5），按 addr 定位更稳健
    by_addr = {it["addr"]: it["text"] for it in iter_docx_paragraphs(out)}
    assert "{{sign_date}}" in by_addr["cell:1:1:0"]
    # 原 anchor 消失
    assert "____________" not in texts[0]


def test_apply_docx_anchor_not_found_is_noop(sample_docx):
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    out = apply_docx_placeholders(sample_docx, [
        {"addr": "para:0", "anchor": "不存在的锚文本", "key": "x"},
    ])
    from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
    assert "{{x}}" not in "".join(it["text"] for it in iter_docx_paragraphs(out))


def test_apply_docx_empty_replacements_returns_original(sample_docx):
    """空替换列表：文档内容等价（python-docx 重新写 zip 时时间戳精度为 2 秒，
    字节相等会在跨秒边界时随机失败，故按段落 (addr, text) 比对）。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(sample_docx, [])
    src_items = [(it["addr"], it["text"]) for it in iter_docx_paragraphs(sample_docx)]
    out_items = [(it["addr"], it["text"]) for it in iter_docx_paragraphs(out)]
    assert out_items == src_items


# ---------- 对抗性边界用例 ----------

def test_apply_docx_empty_document_no_crash():
    """空文档（无正文段落/表格）不崩溃。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    empty = _make_docx([])
    assert iter_docx_paragraphs(empty) == []
    out = apply_docx_placeholders(empty, [{"addr": "para:0", "anchor": "x", "key": "k"}])
    assert isinstance(out, bytes) and len(out) > 0


def test_apply_docx_anchor_twice_in_same_paragraph(sample_docx):
    """同一段落内锚文本出现两次：str.replace 语义应全部替换。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    src = _make_docx(["联系人：____ 电话：____"])
    out = apply_docx_placeholders(src, [
        {"addr": "para:0", "anchor": "____", "key": "v"},
    ])
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert "{{v}}" in texts[0]
    assert "____" not in texts[0]
    # 两次出现都被替换
    assert texts[0].count("{{v}}") == 2


def test_apply_docx_unknown_addr_is_ignored(sample_docx):
    """addr 不存在（越界/格式错）时静默跳过，不影响其他替换。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(sample_docx, [
        {"addr": "para:999", "anchor": "____________", "key": "gone"},
        {"addr": "bogus", "anchor": "____________", "key": "gone2"},
        {"addr": "para:0", "anchor": "____________", "key": "project_name"},
    ])
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert "{{project_name}}" in texts[0]
    assert "{{gone}}" not in "".join(texts)
    assert "{{gone2}}" not in "".join(texts)
