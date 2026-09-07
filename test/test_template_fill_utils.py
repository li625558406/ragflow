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
        {"addr": "cell:1:1:0", "anchor": "____年____月____日", "key": "sign_date"},
    ])
    items = iter_docx_paragraphs(out)
    texts = [it["text"] for it in items]
    by_addr = {it["addr"]: it["text"] for it in items}
    assert "{{sign_date}}" in by_addr["cell:1:1:0"]
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


def test_validate_placeholders_non_dict_item_no_crash():
    """对抗：items 元素非 dict（字符串/None）不抛异常，返回校验失败。"""
    from rag.svr.template_fill.detector import validate_placeholders
    ok, err = validate_placeholders(["不是字典"], CANDS)
    assert not ok and err
    ok, err = validate_placeholders([None], CANDS)
    assert not ok and err


def test_normalize_key():
    """归一化：大写/空格/特殊字符 → snake_case；全非法字符兜底为 field。"""
    from rag.svr.template_fill.detector import normalize_key
    assert normalize_key("Project Name") == "project_name"
    assert normalize_key("  Sign-Date! ") == "sign_date"
    assert normalize_key("___") == "field"
    assert normalize_key("") == "field"
