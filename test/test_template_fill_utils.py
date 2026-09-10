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
    assert any(it["addr"] == "cell:0:1:1:0" for it in cell_items)


def test_extract_docx_candidates(sample_docx):
    from rag.svr.template_fill.docx_utils import extract_docx_candidates
    cands = extract_docx_candidates(sample_docx)
    # 普通段落（无填写特征）被过滤
    assert all("无填写点" not in c["text"] for c in cands)
    assert any("项目名称" in c["text"] for c in cands)
    assert any(c["addr"] == "cell:0:1:1:0" for c in cands)


def test_apply_docx_placeholders(sample_docx):
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    out = apply_docx_placeholders(sample_docx, [
        {"addr": "para:0", "anchor": "____________", "key": "project_name"},
        {"addr": "cell:0:1:1:0", "anchor": "____年____月____日", "key": "sign_date"},
    ])
    from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert "{{project_name}}" in texts[0]
    # cell:1:1:0 扁平序号 = 2 正文段 + 4 单元格段中排第 6（idx 5），按 addr 定位更稳健
    by_addr = {it["addr"]: it["text"] for it in iter_docx_paragraphs(out)}
    assert "{{sign_date}}" in by_addr["cell:0:1:1:0"]
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

def test_docx_multi_table_same_position_addr_unique():
    """回归：addr 曾缺表序号（cell:<r>:<c>:<pi>），两个表格同 (r,c,p) 段落撞号——
    parse 按 index 选了表 A 的段落，validate/渲染按 addr 查到表 B 的段落，
    锚文本错位导致校验失败/替换落错表。addr 必须带表序号保证全局唯一。"""
    doc = Document()
    doc.add_paragraph("标题：")
    for i, tbl_texts in enumerate([["5.工程特征：", ""], ["2）道路工程：", ""]]):
        tbl = doc.add_table(rows=1, cols=2)
        for c, text in enumerate(tbl_texts):
            tbl.rows[0].cells[c].paragraphs[0].text = text
        if i == 0:
            doc.add_paragraph("中间分隔段")
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()

    from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
    items = iter_docx_paragraphs(data)
    addrs = [it["addr"] for it in items if it["addr"].startswith("cell:")]
    # 两表同位置段落 addr 不同
    assert len(addrs) == len(set(addrs))
    by_text = {it["text"]: it["addr"] for it in items if it["text"]}
    assert by_text["5.工程特征："] == "cell:0:0:0:0"
    assert by_text["2）道路工程："] == "cell:1:0:0:0"

    # 替换必须落到表 0 的段落，表 1 同位置不受影响
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    out = apply_docx_placeholders(data, [
        {"addr": "cell:0:0:0:0", "anchor": "5.工程特征：", "key": "engineering_characteristics"},
    ])
    out_texts = {it["addr"]: it["text"] for it in iter_docx_paragraphs(out)}
    assert out_texts["cell:0:0:0:0"] == "{{engineering_characteristics}}"
    assert out_texts["cell:1:0:0:0"] == "2）道路工程："


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


def _make_docx_with_hyperlink():
    """构造段落 = run("地址：") + run("____ 详见") + 尾部超链接("官网")，全程手工插
    XML，不依赖网络。锚文本 "：____" 跨前两个 run（不落在任何单一 run 内），
    强制触发跨 run 路径。

    复现原始 bug 的关键：python-docx 1.1+ 的 p.text 含超链接内文本，但 p.runs 不含，
    旧的整段重写路径会把 "官网" 复制进首 run → "地址{{k}}____ 详见官网官网"。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    p = doc.add_paragraph("地址：")
    p.add_run("____ 详见")
    hl = OxmlElement("w:hyperlink")
    hl.set(qn("r:id"), "rIdLink")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "官网"
    r.append(t)
    hl.append(r)
    p._p.append(hl)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_apply_docx_hyperlink_paragraph_no_duplicate(sample_docx):
    """含 w:hyperlink 的段落（锚文本跨界）必须走 run 拼接替换：替换后超链接文本
    只出现一次，占位符正确落位，且原超链接文本不被复制进正文 run。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    src = _make_docx_with_hyperlink()
    # 前置确认构造成功：p.text 含超链接文本（python-docx 1.1+ 行为）
    assert next(it["text"] for it in iter_docx_paragraphs(src)) == "地址：____ 详见官网"

    out = apply_docx_placeholders(src, [{"addr": "para:0", "anchor": "：____", "key": "url"}])
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert texts[0] == "地址{{url}} 详见官网"
    # 修复前此处为 2（超链接文本被复制进首 run），修复后必须为 1
    assert texts[0].count("官网") == 1
    assert "____" not in texts[0]


def test_apply_docx_hyperlink_anchor_absent_is_noop():
    """含超链接的段落上锚文本不存在（且跨界拼不上）时返回 no-op，超链接文本不受影响。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    src = _make_docx_with_hyperlink()
    out = apply_docx_placeholders(src, [{"addr": "para:0", "anchor": "不存在的锚", "key": "k"}])
    texts = [it["text"] for it in iter_docx_paragraphs(out)]
    assert texts[0] == "地址：____ 详见官网"
    assert "{{k}}" not in texts[0]


def test_apply_docx_dirty_entry_missing_fields_skipped(sample_docx):
    """LLM 脏输入：addr/anchor/key 任一缺失或为空时跳过该条，不抛 KeyError，
    其余合法条目正常替换。"""
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(sample_docx, [
        {"addr": "para:0", "key": "no_anchor"},                 # 缺 anchor
        {"addr": "para:0", "anchor": "____________"},            # 缺 key
        {"anchor": "____年", "key": "no_addr"},                  # 缺 addr
        {"addr": "para:0", "anchor": "", "key": "empty_anchor"},  # anchor 为空
        {"addr": "cell:0:1:1:0", "anchor": "____年____月____日", "key": "sign_date"},
    ])
    items = iter_docx_paragraphs(out)
    texts = [it["text"] for it in items]
    by_addr = {it["addr"]: it["text"] for it in items}
    assert "{{sign_date}}" in by_addr["cell:0:1:1:0"]
    assert "{{no_anchor}}" not in "".join(texts)
    assert "{{no_addr}}" not in "".join(texts)
    assert "{{empty_anchor}}" not in "".join(texts)


# ---------- xlsx 工具 ----------

def _make_xlsx(sheets: dict):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_iter_xlsx_cells():
    from rag.svr.template_fill.xlsx_utils import iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"], [None, "空行跳过"]], "签章页": [["签字", "（）"]]})
    items = iter_xlsx_cells(blob)
    addrs = [it["addr"] for it in items]
    assert "封面!A1" in addrs and "封面!B1" in addrs
    assert "封面!A2" not in addrs  # None 值跳过
    assert "签章页!B1" in addrs


def test_apply_xlsx_placeholders():
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"]]})
    out = apply_xlsx_placeholders(blob, [
        {"sheet": "封面", "coord": "B1", "addr": "封面!B1", "anchor": "________", "key": "project_name"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"


def test_apply_xlsx_dirty_entry_skipped():
    """LLM 脏输入：sheet/coord/anchor/key 任一缺失或为空时跳过该条不抛异常，
    其余合法条目正常替换。"""
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"], ["日期", "____年____月____日"]]})
    out = apply_xlsx_placeholders(blob, [
        {"sheet": "封面", "coord": "B1", "anchor": "________"},                       # 缺 key
        {"sheet": "封面", "coord": "B1", "key": "no_anchor"},                          # 缺 anchor
        {"sheet": "封面", "anchor": "________", "key": "no_coord"},                    # 缺 coord
        {"coord": "B1", "anchor": "________", "key": "no_sheet"},                      # 缺 sheet
        {"sheet": "封面", "coord": "B1", "anchor": "", "key": "empty_anchor"},         # anchor 为空
        {"sheet": "封面", "coord": "B1", "anchor": "________", "key": ""},             # key 为空
        {"sheet": "封面", "coord": "B1", "anchor": "________", "key": "project_name"},  # 合法
        {"sheet": "封面", "coord": "B2", "anchor": "____年", "key": "sign_date"},       # 合法
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"
    assert texts["封面!B2"] == "{{sign_date}}____月____日"  # anchor "____年" 整体被替换


def test_apply_xlsx_bad_sheet_or_coord_skipped():
    """不存在的 sheet 名 / 非法 coord（openpyxl 会抛异常）跳过该条，不中断整批。"""
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"]]})
    out = apply_xlsx_placeholders(blob, [
        {"sheet": "不存在的页", "coord": "B1", "anchor": "________", "key": "gone1"},   # KeyError 路径
        {"sheet": "封面", "coord": "不是坐标", "anchor": "________", "key": "gone2"},   # ValueError 路径
        {"sheet": "封面", "coord": "B1", "anchor": "________", "key": "project_name"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"
    assert "{{gone1}}" not in "".join(texts.values())
    assert "{{gone2}}" not in "".join(texts.values())


def test_apply_xlsx_range_coord_skipped():
    """对抗：coord 传 range 语法（如 "A1:B2"）时 openpyxl 合法接受并返回 cell 元组，
    后续 .value 会抛 AttributeError 中断整批。修复后该条被静默跳过，其余条目正常替换。"""
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"], ["日期", "____年____月____日"]]})
    out = apply_xlsx_placeholders(blob, [
        {"sheet": "封面", "coord": "A1:B2", "anchor": "________", "key": "gone_range"},
        {"sheet": "封面", "coord": "B1", "anchor": "________", "key": "project_name"},
        {"sheet": "封面", "coord": "B2", "anchor": "____年____月____日", "key": "sign_date"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"
    assert texts["封面!B2"] == "{{sign_date}}"
    assert "{{gone_range}}" not in "".join(texts.values())


def test_apply_xlsx_addr_fallback():
    """addr 兜底：条目缺 sheet/coord 但 addr 为 "<sheet>!<coord>" 时按最后一个 "!" 拆分定位。"""
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"]]})
    out = apply_xlsx_placeholders(blob, [
        {"addr": "封面!B1", "anchor": "________", "key": "project_name"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"


def test_apply_xlsx_explicit_sheet_coord_over_addr():
    """显式 sheet/coord 优先于 addr 兜底：addr 写错不影响按显式字段定位。"""
    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"]]})
    out = apply_xlsx_placeholders(blob, [
        {"sheet": "封面", "coord": "B1", "addr": "不存在页!Z99", "anchor": "________", "key": "project_name"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"


def test_extract_xlsx_skips_formula_cells():
    """以 "=" 开头的公式单元格不进候选（防止 LLM 把公式文本当填写点、渲染时覆盖公式），
    普通文本格正常提取。"""
    from openpyxl import Workbook

    from rag.svr.template_fill.xlsx_utils import extract_xlsx_candidates
    wb = Workbook()
    ws = wb.active
    ws.title = "封面"
    ws["A1"] = "=SUM(A2:A3)"          # 公式文本，恰好含 "____" 也不该被提取
    ws["A2"] = 10
    ws["A3"] = 20
    ws["B1"] = "合计：____元"          # 普通文本填写点
    ws["B2"] = "=IF(A2>5, \"____\", \"\")"  # 公式结果含填写特征同样排除
    buf = io.BytesIO()
    wb.save(buf)
    cands = extract_xlsx_candidates(buf.getvalue())
    texts = [c["text"] for c in cands]
    assert all(not t.startswith("=") for t in texts)
    assert any("合计：____元" in t for t in texts)


def test_apply_xlsx_preserves_cell_style():
    """对抗：替换只改 value，单元格样式（字体加粗/填充色）必须保留。"""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders, iter_xlsx_cells
    blob = _make_xlsx({"封面": [["项目名称", "________"]]})
    # 给 B1 设样式后重新序列化
    wb = load_workbook(io.BytesIO(blob))
    ws = wb["封面"]
    ws["B1"].font = ws["B1"].font.copy(bold=True)
    ws["B1"].fill = PatternFill(fill_type="solid", start_color="FFFF00")
    buf = io.BytesIO()
    wb.save(buf)
    styled = buf.getvalue()

    out = apply_xlsx_placeholders(styled, [
        {"sheet": "封面", "coord": "B1", "anchor": "________", "key": "project_name"},
    ])
    texts = {it["addr"]: it["text"] for it in iter_xlsx_cells(out)}
    assert texts["封面!B1"] == "{{project_name}}"
    wb2 = load_workbook(io.BytesIO(out))
    cell = wb2["封面"]["B1"]
    assert cell.font.bold is True
    assert cell.fill.start_color.rgb == "00FFFF00" or cell.fill.start_color.rgb == "FFFF00"


# ---------- detector：LLM 填写点识别解析/校验（纯函数部分） ----------

CANDS = [
    {"index": 0, "text": "项目名称：____________", "addr": "para:0"},
    {"index": 2, "text": "投标单位（　　　）", "addr": "para:2"},
    {"index": 4, "text": "签字日期：____年____月____日", "addr": "para:4"},
]


def test_parse_detection_response_valid():
    from rag.svr.template_fill.detector import parse_detection_response
    raw = '```json\n[{"line": 0, "anchor": "____________", "key": "Project Name", "name": "项目名称", "description": "投标项目全称", "retrieval_query": "项目名称 概况", "fill_mode": "llm", "required": true}, {"line": 4, "anchor": "____年____月____日", "key": "sign_date", "name": "签字日期", "description": "", "retrieval_query": "", "fill_mode": "manual", "required": false}]\n```'
    out = parse_detection_response(raw, CANDS)
    assert len(out) == 2
    assert out[0]["key"] == "project_name"  # 归一化：小写+下划线
    assert out[0]["addr"] == "para:0"
    assert out[0]["top_k"] == 6
    # fill_mode 代码层强制 llm：LLM 输出 manual 也被规整（识别产物统一 AI 填写）
    assert all(it["fill_mode"] == "llm" for it in out)


def test_parse_detection_response_drops_invalid_anchor():
    from rag.svr.template_fill.detector import parse_detection_response
    raw = '[{"line": 0, "anchor": "不存在的锚", "key": "x", "name": "X", "fill_mode": "llm", "required": true}]'
    assert parse_detection_response(raw, CANDS) == []


def test_parse_detection_response_dedupes_keys():
    from rag.svr.template_fill.detector import parse_detection_response
    raw = '[{"line": 0, "anchor": "____________", "key": "date", "name": "A", "fill_mode": "llm", "required": true},{"line": 4, "anchor": "____年", "key": "date", "name": "B", "fill_mode": "manual", "required": false}]'
    out = parse_detection_response(raw, CANDS)
    assert [it["key"] for it in out] == ["date", "date_2"]


def test_parse_detection_response_garbage_returns_empty():
    from rag.svr.template_fill.detector import parse_detection_response
    assert parse_detection_response("我不明白你的意思", CANDS) == []


def test_validate_placeholders():
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders([
        {"key": "project_name", "name": "项目名称", "addr": "para:0", "anchor": "____________",
         "fill_mode": "llm", "required": True, "retrieval_query": "q", "description": "", "top_k": 6},
    ], CANDS)
    assert ok and err == ""
    ok, err = validate_placeholders([
        {"key": "Bad Key!", "name": "x", "addr": "para:0", "anchor": "____________", "fill_mode": "llm",
         "required": True, "retrieval_query": "", "description": "", "top_k": 6},
    ], CANDS)
    assert not ok and "key" in err
    # anchor 不在 addr 对应文本中 → 拒绝
    ok, err = validate_placeholders([
        {"key": "a", "name": "x", "addr": "para:0", "anchor": "瞎写的", "fill_mode": "llm",
         "required": True, "retrieval_query": "", "description": "", "top_k": 6},
    ], CANDS)
    assert not ok and "anchor" in err


# ---------- detector 对抗性用例 ----------

def test_parse_detection_response_non_int_line_skipped():
    """对抗：line 为字符串 "0" 或 None 的项必须被安全跳过（不抛异常），其余项正常保留。"""
    from rag.svr.template_fill.detector import parse_detection_response
    raw = '[{"line": "0", "anchor": "____________", "key": "a", "name": "A"},' \
          '{"line": null, "anchor": "____年", "key": "b", "name": "B"},' \
          '{"line": 4, "anchor": "____年____月____日", "key": "c", "name": "C"}]'
    out = parse_detection_response(raw, CANDS)
    assert [it["key"] for it in out] == ["c"]


def test_parse_detection_response_noisy_surroundings():
    """对抗：JSON 数组前后有大量噪音文本（含代码块/说明）仍能解析。"""
    from rag.svr.template_fill.detector import parse_detection_response
    raw = "好的，我分析了这份模板。以下是识别结果：\n```json\n" \
          '[{"line": 0, "anchor": "____________", "key": "project_name", "name": "项目名称"}]' \
          "\n```\n以上共 1 个填写点，请确认。"
    out = parse_detection_response(raw, CANDS)
    assert len(out) == 1 and out[0]["key"] == "project_name"


def test_parse_detection_response_oversize_anchor_dropped():
    """对抗：超长 anchor（>500 字符）在 parse 阶段即被丢弃——约束选在 parse：
    识别阶段就该拦住异常项，避免脏数据流入人工确认/apply 链路。"""
    from rag.svr.template_fill.detector import parse_detection_response
    long_anchor = "长" * 501
    raw = f'[{{"line": 0, "anchor": "{long_anchor}", "key": "big", "name": "B"}},' \
          '{"line": 0, "anchor": "____________", "key": "ok", "name": "O"}]'
    out = parse_detection_response(raw, CANDS)
    assert [it["key"] for it in out] == ["ok"]


def test_validate_placeholders_rejects_oversize_key():
    """对抗：超过 64 字符的合法 snake_case key 在 validate 阶段被拒
    （parse 的 normalize 不截断长度，长度约束统一收口在 validate）。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders([
        {"key": "k" * 65, "name": "x", "addr": "para:0", "anchor": "____________", "fill_mode": "llm",
         "required": True, "retrieval_query": "", "description": "", "top_k": 6},
    ], CANDS)
    assert not ok and "key" in err


def test_validate_placeholders_anchor_length_boundary():
    """对抗：anchor 长度约束在 validate 与 parse（MAX_ANCHOR_LEN=500）对齐——
    恰好 500 通过（含 membership 校验需真实落在候选文本内），501 拒绝且报错可读。"""
    from rag.svr.template_fill.detector import MAX_ANCHOR_LEN, validate_placeholders
    anchor_500 = "长" * MAX_ANCHOR_LEN
    cands = [{"index": 0, "addr": "para:0", "text": "前缀" + anchor_500}]
    base = {"key": "k", "name": "n", "addr": "para:0", "fill_mode": "llm"}
    ok, err = validate_placeholders([{**base, "anchor": anchor_500}], cands)
    assert ok and err == ""
    ok, err = validate_placeholders([{**base, "anchor": "长" * (MAX_ANCHOR_LEN + 1)}], cands)
    assert not ok and "anchor" in err and str(MAX_ANCHOR_LEN) in err


def test_validate_placeholders_non_dict_item_no_crash():
    """对抗：items 元素非 dict（字符串/None）不抛异常，返回校验失败。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders(["不是字典"], CANDS)
    assert not ok and err
    ok, err = validate_placeholders([None], CANDS)
    assert not ok and err


# ---------- validate_placeholders：空 addr 按 anchor 反查推导（手动添加行） ----------

def _manual_item(**kw):
    base = {"key": "m", "name": "手动", "addr": "", "anchor": "项目名称",
            "fill_mode": "manual", "required": True}
    base.update(kw)
    return base


def test_validate_empty_addr_unique_anchor_hit_backfills():
    """空 addr + anchor 恰好命中 1 个候选：通过，且 addr 被回填进 item。"""
    from rag.svr.template_fill.detector import validate_placeholders
    item = _manual_item(anchor="项目名称")
    ok, err = validate_placeholders([item], CANDS)
    assert ok and err == ""
    assert item["addr"] == "para:0"  # 回填生效，落库前地址已补齐


def test_validate_empty_addr_anchor_not_found_rejected():
    """空 addr + anchor 未命中任何候选：拒绝且报错面向用户。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders([_manual_item(anchor="不存在的锚")], CANDS)
    assert not ok and "未找到" in err


def test_validate_empty_addr_anchor_ambiguous_rejected():
    """空 addr + anchor 多处命中（"____" 在 para:0 与 para:4 都出现）：歧义拒绝，防回填错位。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders([_manual_item(anchor="____")], CANDS)
    assert not ok and "匹配到" in err and "2" in err


def test_validate_empty_addr_empty_anchor_rejected():
    """空 addr + 空 anchor：`"" in text` 恒真会命中全部候选，须先行拦截为缺少锚文本。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders([_manual_item(anchor="")], CANDS)
    assert not ok and "锚文本" in err


def test_validate_empty_addr_case_sensitive_match():
    """anchor 反查为精确子串匹配、区分大小写，与 addr 非空分支的 membership 校验一致。"""
    from rag.svr.template_fill.detector import validate_placeholders
    cands = [{"index": 0, "addr": "para:0", "text": "Project Name: ______"}]
    item = _manual_item(anchor="Project Name")
    ok, err = validate_placeholders([item], cands)
    assert ok and item["addr"] == "para:0"
    ok, err = validate_placeholders([_manual_item(anchor="project name")], cands)
    assert not ok and "未找到" in err


def test_validate_non_empty_addr_keeps_original_logic():
    """addr 非空走原逻辑：定位不存在 / anchor 不在该定位文本中均拒绝，不做反查回填。"""
    from rag.svr.template_fill.detector import validate_placeholders
    item = {"key": "a", "name": "x", "addr": "para:9", "anchor": "项目名称",
            "fill_mode": "llm", "required": True}
    ok, err = validate_placeholders([item], CANDS)
    assert not ok and "不存在" in err
    assert item["addr"] == "para:9"  # 未被反查改写
    item2 = {"key": "b", "name": "x", "addr": "para:0", "anchor": "____年",  # "____年" 只在 para:4
             "fill_mode": "llm", "required": True}
    ok, err = validate_placeholders([item2], CANDS)
    assert not ok and "不在" in err


def test_normalize_key():
    """归一化：大写/空格/特殊字符 → snake_case；全非法字符兜底为 field。"""
    from rag.svr.template_fill.detector import normalize_key
    assert normalize_key("Project Name") == "project_name"
    assert normalize_key("  Sign-Date! ") == "sign_date"
    assert normalize_key("___") == "field"
    assert normalize_key("") == "field"


# ---------- service：_sanitize_filename 纯函数（不依赖 DB/MinIO） ----------
# 说明：api.db.db_models 的 DB 对象为懒连接，import 链不会立即触库，
# 故直接 `from api.db.services.template_fill_service import _sanitize_filename`
# 模块级直测即可，无需 stub-load。

def _sanitize(name, max_len=128):
    from api.db.services.template_fill_service import _sanitize_filename
    return _sanitize_filename(name, max_len)


def test_sanitize_forward_slash_takes_basename():
    assert _sanitize("a/b/c.docx") == "c.docx"


def test_sanitize_backslash_takes_basename():
    """Windows 路径分隔符：统一按 '/' 处理后取最后一段，防止 '/' 进 MinIO 当目录前缀。"""
    assert _sanitize("a\\b\\c.docx") == "c.docx"


def test_sanitize_empty_returns_fallback():
    """空串/纯空白/None 兜底 'template'，避免空 object 名片段。"""
    assert _sanitize("") == "template"
    assert _sanitize("   ") == "template"
    assert _sanitize(None) == "template"


def test_sanitize_trailing_dot_kept_as_is():
    """'file.' 清洗后 stem 非空但 ext 为空 → 不走扩展名保留分支，原样保留。"""
    assert _sanitize("file.") == "file."


def test_sanitize_hidden_file_dotfile_kept():
    """.hidden：rpartition 后 stem 为空 → 不截断分支命中，dotfile 原样保留。"""
    assert _sanitize(".hidden") == ".hidden"


def test_sanitize_oversize_chinese_name_truncated():
    """恰好超长（129 个汉字）→ 截断到 128，无扩展名按硬截断处理。"""
    name = "标" * 129
    out = _sanitize(name)
    assert out == "标" * 128
    assert len(out) == 128


def test_sanitize_path_traversal_reduced_to_basename():
    """对抗：'../../etc/passwd' 型穿越只留 base 名，无法借 object name 逃出 bucket 前缀。"""
    assert _sanitize("../../etc/passwd") == "passwd"
    assert _sanitize("..\\..\\windows\\system32\\config") == "config"
    # 整串都是点/斜杠组合时清洗后为空 → 兜底
    assert _sanitize("../../") == "template"


def test_sanitize_ext_exactly_10_chars_preserved_on_truncate():
    """扩展名恰好 10 字符（边界内）：超长截断时保留 '.xxxxxxxxxx' 扩展名。"""
    ext = "x" * 10
    name = "a" * 200 + "." + ext
    out = _sanitize(name)
    assert out == "a" * (128 - 10 - 1) + "." + ext
    assert len(out) == 128
    assert out.endswith("." + ext)


def test_sanitize_ext_11_chars_not_preserved_hard_truncate():
    """扩展名恰好 11 字符（越界）：不保留扩展名，硬截断到 128。"""
    ext = "x" * 11
    name = "a" * 200 + "." + ext
    out = _sanitize(name)
    assert out == "a" * 128
    assert "." not in out


def test_sanitize_custom_max_len_boundary():
    """自定义 max_len 边界：恰好等于 max_len 不截断；超 1 字符才截。"""
    assert _sanitize("a" * 20, max_len=20) == "a" * 20
    assert len(_sanitize("a" * 21, max_len=20)) == 20


# ---------- P2 renderer ----------

def _mk_docx_with_placeholder():
    """用 python-docx 造一个含 {{name}} 的 docx blob。"""
    from docx import Document
    doc = Document()
    doc.add_paragraph("项目名称：{{name}}")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


def test_render_docx_replaces_placeholder():
    from docx import Document as Docx

    from rag.svr.template_fill.renderer import render_docx
    out = render_docx(_mk_docx_with_placeholder(), {"name": "测试项目"})
    text = "\n".join(p.text for p in Docx(io.BytesIO(out)).paragraphs)
    assert "测试项目" in text and "{{" not in text


def test_render_docx_missing_value_renders_empty():
    """缺值占位符渲染为空串（人工二次加工留空），不再落【待人工】标记。"""
    from docx import Document as Docx

    from rag.svr.template_fill.renderer import render_docx
    out = render_docx(_mk_docx_with_placeholder(), {"name": ""})
    text = "\n".join(p.text for p in Docx(io.BytesIO(out)).paragraphs)
    assert "【待人工" not in text and "{{" not in text


def test_render_xlsx_by_addr():
    from openpyxl import Workbook, load_workbook

    from rag.svr.template_fill.renderer import render_xlsx
    wb = Workbook(); ws = wb.active; ws.title = "封面"; ws["B1"] = "{{name}}"
    buf = io.BytesIO(); wb.save(buf)
    out = render_xlsx(buf.getvalue(), {"name": "测试项目"}, {"name": "封面!B1"})
    ws2 = load_workbook(io.BytesIO(out))["封面"]
    assert ws2["B1"].value == "测试项目"


def test_render_xlsx_bad_addr_skipped():
    """addr 非法 → 跳过该格不抛异常，且好格不被误伤。"""
    from openpyxl import Workbook, load_workbook

    from rag.svr.template_fill.renderer import render_xlsx
    wb = Workbook(); ws = wb.active; ws.title = "S1"; ws["B2"] = "keep"
    buf = io.BytesIO(); wb.save(buf)
    dirty = {"a": "不存在的表!ZZ99",       # sheet 不存在 → KeyError
             "b": "S1!not-a-coord",        # coord 非法 → ValueError/IndexError
             "c": "S1!",                   # coord 空串 → IndexError
             "d": "S1!A1:B2",              # range 语法 → AttributeError
             "e": "S1!A1048577",           # 行越界 → ValueError
             "ok": "S1!B2"}                # 好格正常写
    out = render_xlsx(buf.getvalue(), dict.fromkeys(dirty, "x"), dirty)
    ws2 = load_workbook(io.BytesIO(out))["S1"]
    assert ws2["B2"].value == "x"          # 好格被写入
    assert ws2.max_row <= 2 and ws2.max_column <= 2  # 脏格全部未落值


def test_render_dispatch():
    from rag.svr.template_fill.renderer import render
    blob = _mk_docx_with_placeholder()
    out = render("docx", blob, {"name": "X"})
    assert out and out != blob


# ---------- detector：anchor 派生默认值（纯函数） ----------

def test_derive_default_from_anchor_blank_markers():
    from rag.svr.template_fill.detector import derive_default_from_anchor
    assert derive_default_from_anchor("") == ""
    assert derive_default_from_anchor(None) == ""
    assert derive_default_from_anchor("______") == ""
    assert derive_default_from_anchor("＿＿＿＿") == ""   # 全角下划线
    assert derive_default_from_anchor("　　") == ""       # 全角空格
    assert derive_default_from_anchor("---") == ""
    assert derive_default_from_anchor("………") == ""
    assert derive_default_from_anchor("N/A") == ""
    assert derive_default_from_anchor("▁▁▁▁") == ""   # 下八分之一块
    assert derive_default_from_anchor("＊＊＊") == ""  # 全角星号
    assert derive_default_from_anchor("⋯⋯") == ""     # 中点省略号
    assert derive_default_from_anchor("none") == ""   # 小写 none 同样视为留空标记


def test_derive_default_from_anchor_filled_values():
    from rag.svr.template_fill.detector import derive_default_from_anchor
    assert derive_default_from_anchor("XX建设工程有限公司") == "XX建设工程有限公司"
    assert derive_default_from_anchor("2026-09-10") == "2026-09-10"
    assert derive_default_from_anchor("  100万元  ") == "100万元"
    assert derive_default_from_anchor("____2026") == "____2026"  # 混合内容：含数字不误判为留空


def test_derive_default_from_anchor_truncates_and_strips_ctrl():
    """超长截断对齐 MAX_ANCHOR_LEN；控制字符剥离（防污染 docx XML）。"""
    from rag.svr.template_fill.detector import MAX_ANCHOR_LEN, derive_default_from_anchor
    assert len(derive_default_from_anchor("A" * 501)) == MAX_ANCHOR_LEN
    assert derive_default_from_anchor("值\x00\x01名") == "值名"
