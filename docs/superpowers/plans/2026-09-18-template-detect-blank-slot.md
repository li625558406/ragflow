# 范本AI识别加固——run层确定性切位 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 docx 范本 AI 识别链路中新增 run 层确定性切位器，LLM 降级为「位编号→语义」标注，根除下划线场景的拆碎片/漏识别/重复错位/提示语污染。

**Architecture:** 新增纯函数模块 `blank_slots.py`（runs 拼接文本坐标系切位），`docx_utils.extract_docx_candidates` 候选行附带 `slots` 键，`detector.py` 按「有位行走 V2 位编号契约 / 无位行走现有 V1 契约」分流，LLM 漏标的位由代码确定性兜底。下游渲染替换/前端/validate 零改动（anchor 契约不变）。

**Tech Stack:** Python 3.12 + python-docx 1.2.0 + pytest（`.venv`）+ asyncio。

**设计文档:** `docs/superpowers/specs/2026-09-18-template-detect-blank-slot-design.md`（实现前先读一遍）

**测试命令约定（Windows）:** `.venv/Scripts/python.exe -m pytest <file> -v`（必须用 `.venv`，不能用系统 python）

---

### Task 1: blank_slots.py —— 切位器核心（位判定 + 区间合并）

**Files:**
- Create: `rag/svr/template_fill/blank_slots.py`
- Test: `test/test_template_fill_blank_slots.py`

- [ ] **Step 1: 写失败测试（判定与合并全场景）**

创建 `test/test_template_fill_blank_slots.py`：

```python
"""blank_slots 切位器单测：run 层位判定与区间合并（对抗性全场景）。"""
import io

from docx import Document
from docx.oxml.ns import qn


def _add_runs(p, runs_spec):
    """runs_spec: [(text, underline)]，underline=True 时设格式下划线。"""
    for text, u in runs_spec:
        r = p.add_run(text)
        if u is not None:
            r.font.underline = u
    return p


def _para(runs_spec):
    doc = Document()
    p = doc.add_paragraph()
    _add_runs(p, runs_spec)
    return p


def _add_hyperlink(p, text):
    """段落内追加一个 w:hyperlink（模拟超链接 run 场景）。"""
    hl = p.part.relate_to(
        "https://example.com",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    el = p._p.makeelement(qn("w:hyperlink"), {qn("r:id"): hl})
    r = p._p.makeelement(qn("w:r"), {})
    t = p._p.makeelement(qn("w:t"), {})
    t.text = text
    r.append(t)
    el.append(r)
    p._p.append(el)


def test_underline_blank_run():
    """真实范本形态：下划线格式纯空格 run = blank 位。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("本招标项目", None), ("             ", True), ("，建设资金来自", None)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    s = slots[0]
    assert s["kind"] == "blank"
    assert s["text"] == "             "
    # runs 拼接文本坐标系：区间切片与 text 一致
    runs_text = "".join(r.text for r in p.runs)
    assert runs_text[s["start"]:s["end"]] == s["text"]


def test_underline_hint_run():
    """下划线格式「空格+（提示）+空格」run = hint 位，hint 去空白。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("本招标项目", None), ("    （招标项目名称） ", True), ("已由", None)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["kind"] == "hint"
    assert slots[0]["hint"] == "招标项目名称"


def test_char_underscores_no_format():
    """无格式的连续 _ 字符 = blank 位（手打下划线）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("项目名称：", None), ("____________", None)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["kind"] == "blank"
    assert slots[0]["text"] == "____________"


def test_solid_underlined_text_not_slot():
    """实心文字加下划线格式（标题词）不是位——防误报关键闸。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("第一章 ", None), ("招标公告", True), ("（加粗下划线标题）", None)])
    assert extract_paragraph_slots(p) == []


def test_underline_val_none_not_slot():
    """显式 val=none（font.underline=False）不是位。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("文字 ", None), ("   ", False), ("续", None)])
    assert extract_paragraph_slots(p) == []


def test_underline_inherited_not_slot():
    """underline=None（继承样式）不是位（首版只认 run 级显式设置）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("   ", None)])
    assert extract_paragraph_slots(p) == []


def test_adjacent_underline_runs_merged():
    """相邻下划线 run（中间无非空白文字）合并为一个位。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("编号：", None), ("（项目编号）", True), ("  ", True), ("___", None)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["kind"] == "hint"  # 合并组内有 hint 则整体 hint
    assert slots[0]["hint"] == "项目编号"
    runs_text = "".join(r.text for r in p.runs)
    assert runs_text[slots[0]["start"]:slots[0]["end"]] == "（项目编号）  ___"


def test_slots_separated_by_text_not_merged():
    """中间隔实心文字的位不合并，各自独立。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("    （甲） ", True), ("已由", None), (" （乙） ", True)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 2
    assert [s["hint"] for s in slots] == ["甲", "乙"]


def test_same_shape_blank_slots_both_kept():
    """同段两个同形纯空白位都保留（occ 由下游预分配）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("为", None), ("     ", True), ("，金额", None), ("     ", True), ("元。", None)])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 2
    assert slots[0]["start"] < slots[1]["start"]
    assert slots[0]["text"] == slots[1]["text"] == "     "


def test_hyperlink_paragraph_no_slots():
    """段落含超链接 → 不切位（坐标系不可靠，整行回退 V1）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    doc = Document()
    p = doc.add_paragraph()
    _add_runs(p, [("前缀", None), ("     ", True)])
    _add_hyperlink(p, "链接文字")
    assert extract_paragraph_slots(p) == []


def test_empty_paragraph_no_slots():
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    doc = Document()
    assert extract_paragraph_slots(doc.add_paragraph("")) == []


def test_oversize_slot_dropped():
    """超长留白 run（>500 字符）丢弃——下游 validate 会拒，源头不产脏位。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    p = _para([("x", None), (" " * 600, True)])
    assert extract_paragraph_slots(p) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag.svr.template_fill.blank_slots'`

- [ ] **Step 3: 实现 blank_slots.py**

创建 `rag/svr/template_fill/blank_slots.py`：

```python
"""run 层确定性切位：从段落 runs 中切出填写位区间（纯函数，可独立单测）。

填写位 = 下划线格式（w:u）留白 run 或字符下划线串，边界在 run 层精确已知；
LLM 识别降级为「位编号→语义」标注，不再自选 anchor。坐标系为 runs 拼接文本
（与 docx_utils._replace_cross_run_in_place 同坐标系）。

第一性原理：识别 = 「哪里是填写位」（几何/格式，确定性）+「这个位是什么字段」
（语义，LLM 擅长）。本模块只负责前者。"""
import re

from docx.oxml.ns import qn

# 字符下划线：连续下划线字符 ≥2（不要求格式，兼容手打下划线）
_CHAR_UNDERLINE_RE = re.compile(r"[_＿]{2,}")
# 下划线格式 run 的文本须匹配留白特征（防实心文字+下划线格式误判为位）：
# 纯空白/下划线，或 空白+括号提示+空白
_BLANK_RUN_RE = re.compile(r"^[\s_＿]*$")
_HINT_RUN_RE = re.compile(r"^\s*[（(]([^（）()]*)[））]\s*$")
# 单个位长度上限：与 detector.MAX_ANCHOR_LEN 同量级，超长位下游 validate 必拒，源头不产
_MAX_SLOT_LEN = 500


def _classify_underline_text(text: str) -> tuple:
    """下划线格式 run 文本分类 → (kind, hint)。非留白特征返回 ("", "")。"""
    if _BLANK_RUN_RE.fullmatch(text):
        return "blank", ""
    m = _HINT_RUN_RE.fullmatch(text)
    if m:
        return "hint", m.group(1).strip()[:100]  # 沿用现有 name 100 上限
    return "", ""


def extract_paragraph_slots(p) -> list:
    """从段落切出填写位区间，返回 [{"start","end","text","kind","hint"}]。

    - 坐标系：p.runs 文本拼接（与替换层一致）；python-docx 1.2.0 的 p.text
      含超链接文本而 p.runs 不含，故段落含 w:hyperlink 时坐标系不可靠，
      整段不切位（返回 []，该行回退 LLM V1 路径）。
    - 位判定两种来源：下划线格式（bool(font.underline) 真值：None/False 无、
      True/枚举有）且文本匹配留白特征；或连续下划线字符 ≥2。
    - 相邻位段（中间只隔空白文本）合并为一个位；组内有 hint 则整体 kind=hint。
    """
    if p._p.findall(f".//{qn('w:hyperlink')}"):
        return []
    runs = p.runs
    segs = []  # [start, end, kind, hint]
    pos = 0
    for r in runs:
        t = r.text or ""
        if not t:
            continue
        if bool(r.font.underline):
            kind, hint = _classify_underline_text(t)
            if kind:
                segs.append([pos, pos + len(t), kind, hint])
                pos += len(t)
                continue
        for m in _CHAR_UNDERLINE_RE.finditer(t):
            segs.append([pos + m.start(), pos + m.end(), "blank", ""])
        pos += len(t)
    if not segs:
        return []
    runs_text = "".join(r.text for r in runs)
    segs.sort(key=lambda s: (s[0], s[1]))
    merged = [segs[0]]
    for s in segs[1:]:
        prev = merged[-1]
        gap_blank = not runs_text[prev[1]:s[0]].strip()
        if s[0] <= prev[1] or (gap_blank and s[0] >= prev[1]):
            prev[1] = max(prev[1], s[1])
            if s[2] == "hint" and not prev[3]:
                prev[2], prev[3] = "hint", s[3]
        else:
            merged.append(s)
    out = []
    for start, end, kind, hint in merged:
        if end - start > _MAX_SLOT_LEN:
            continue
        out.append({"start": start, "end": end,
                    "text": runs_text[start:end], "kind": kind, "hint": hint})
    return out
```

- [ ] **Step 4: 运行测试确认全部通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/blank_slots.py test/test_template_fill_blank_slots.py
git commit -m "feat(template-fill): 新增 run 层确定性切位器 blank_slots"
```

---

### Task 2: docx_utils.extract_docx_candidates 附带 slots

**Files:**
- Modify: `rag/svr/template_fill/docx_utils.py:398-404`（extract_docx_candidates）
- Test: `test/test_template_fill_utils.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_utils.py` 末尾追加（复用文件顶部已有的 `io`/`Document` import 和 `_make_docx` 风格，新增带格式 run 的构造助手）：

```python
def _make_docx_with_underline_runs(para_specs):
    """para_specs: [ [(text, underline_or_None), ...] ]，每项一段。"""
    doc = Document()
    for runs_spec in para_specs:
        p = doc.add_paragraph()
        for text, u in runs_spec:
            r = p.add_run(text)
            if u is not None:
                r.font.underline = u
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_docx_candidates_slots_attached():
    from rag.svr.template_fill.docx_utils import extract_docx_candidates
    blob = _make_docx_with_underline_runs([
        [("本招标项目", None), ("    （招标项目名称） ", True), ("已由", None),
         (" （审批机关） ", True)],
    ])
    cands = extract_docx_candidates(blob)
    assert len(cands) == 1
    slots = cands[0]["slots"]
    assert [s["kind"] for s in slots] == ["hint", "hint"]
    assert slots[0]["hint"] == "招标项目名称"
    assert slots[1]["hint"] == "审批机关"


def test_extract_docx_candidates_slot_line_included_without_hint_re():
    """有位的行即使不命中 FILL_HINT_RE 也入候选（结构性修漏识别）。
    注意间隙用 3 空格：6+ 空格会命中 FILL_HINT_RE 的 \\S[ \\u3000]{6,}\\S，测不出 slots 兜底。"""
    from rag.svr.template_fill.docx_utils import extract_docx_candidates
    blob = _make_docx_with_underline_runs([
        [("合同金额", None), ("   ", True), ("万元，工期", None), ("   ", True), ("天。", None)],
    ])
    cands = extract_docx_candidates(blob)
    assert len(cands) == 1
    assert len(cands[0]["slots"]) == 2


def test_extract_docx_candidates_char_underscore_slots():
    """普通特征行（字符下划线）同样产出切位结果（kind=blank）。"""
    from rag.svr.template_fill.docx_utils import extract_docx_candidates
    cands = extract_docx_candidates(_make_docx(["项目名称：____________", "无填写点的普通段落"]))
    by_text = {c["text"]: c for c in cands}
    assert by_text["项目名称：____________"]["slots"][0]["kind"] == "blank"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "slots"`
Expected: FAIL — `KeyError: 'slots'`

- [ ] **Step 3: 修改 extract_docx_candidates**

替换 `docx_utils.py` 中现有 `extract_docx_candidates`（398-404 行）为：

```python
def extract_docx_candidates(file_bytes: bytes) -> list:
    """提取疑似含填写点的段落（供 LLM 识别，降低 token）。
    含手动占位符 {{key}} 的段落无条件纳入（用户显式标注，不经特征猜测）。
    附带 slots：run 层切位结果（blank_slots.extract_paragraph_slots），供
    detector V2「位编号→语义」契约；有位的行即使不命中 FILL_HINT_RE 也纳入
    （结构性修漏识别）。slots 恒为 list（无位=[]），调用方以真值判 V1/V2 分流。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots
    addr_map, items = _build_addr_map(Document(io.BytesIO(file_bytes)))
    out = []
    for it in items:
        text = it["text"]
        if not text.strip():
            continue
        p = addr_map.get(it["addr"])
        slots = extract_paragraph_slots(p) if p is not None else []
        if not slots and not (FILL_HINT_RE.search(text) or PH_RE.search(text)):
            continue
        it["slots"] = slots
        out.append(it)
    return out
```

注意：`iter_docx_paragraphs` 保持原样不动（其他调用方零影响）。

- [ ] **Step 4: 运行本文件全部测试（防回归）**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v`
Expected: 全部 passed（含既有 ~194 个用例）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/docx_utils.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): 候选行附带 run 层切位 slots，有位行无条件入候选"
```

---

### Task 3: detector.py —— V2 契约（DETECT_SYSTEM_V2 + parse_slot_response + 兜底）

**Files:**
- Modify: `rag/svr/template_fill/detector.py`
- Test: `test/test_template_fill_utils.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_utils.py` 末尾追加：

```python
# ---- detector V2「位编号→语义」契约 ----

def _slot_cand():
    return {
        "index": 7,
        "addr": "para:7",
        "text": "本招标项目    （招标项目名称） 已由 （审批机关） 以批准建设",
        "slots": [
            {"start": 5, "end": 17, "text": "    （招标项目名称） ", "kind": "hint", "hint": "招标项目名称"},
            {"start": 20, "end": 27, "text": " （审批机关） ", "kind": "hint", "hint": "审批机关"},
        ],
    }


def test_parse_slot_response_valid():
    from rag.svr.template_fill.detector import parse_slot_response
    cand = _slot_cand()
    raw = '[{"line":7,"slot":1,"key":"project_name","name":"招标项目名称","required":true},' \
          '{"line":7,"slot":2,"key":"approve_org","name":"审批机关","required":true}]'
    items, covered = parse_slot_response(raw, [cand])
    assert len(items) == 2
    assert covered == {(7, 1), (7, 2)}
    # anchor 由切位区间精确切片，LLM 无权决定
    assert items[0]["anchor"] == "    （招标项目名称） "
    assert items[0]["addr"] == "para:7"
    assert items[0]["_anchor_pos"] == 5
    assert items[1]["anchor"] == " （审批机关） "


def test_parse_slot_response_invalid_slot_dropped():
    from rag.svr.template_fill.detector import parse_slot_response
    raw = '[{"line":7,"slot":9,"key":"x","name":"x"},' \
          '{"line":7,"slot":"1","key":"y","name":"y"},' \
          '{"line":"7","slot":1,"key":"z","name":"z"}]'
    items, covered = parse_slot_response(raw, [_slot_cand()])
    assert items == []
    assert covered == set()


def test_parse_slot_response_duplicate_slot_first_wins():
    from rag.svr.template_fill.detector import parse_slot_response
    raw = '[{"line":7,"slot":1,"key":"first_key","name":"甲"},' \
          '{"line":7,"slot":1,"key":"second_key","name":"乙"}]'
    items, covered = parse_slot_response(raw, [_slot_cand()])
    assert len(items) == 1
    assert items[0]["key"] == "first_key"
    assert covered == {(7, 1)}


def test_parse_slot_response_garbage_returns_empty():
    from rag.svr.template_fill.detector import parse_slot_response
    items, covered = parse_slot_response("不是JSON", [_slot_cand()])
    assert items == [] and covered == set()


def test_slot_fallback_items_hint_and_blank():
    from rag.svr.template_fill.detector import slot_fallback_items
    cand = {
        "index": 3, "addr": "para:3", "text": "业主为        ，金额      万元",
        "slots": [
            {"start": 3, "end": 11, "text": "        ", "kind": "blank", "hint": ""},
            {"start": 14, "end": 20, "text": "      ", "kind": "blank", "hint": ""},
        ],
    }
    items = slot_fallback_items([cand], covered=set())
    assert len(items) == 2
    assert items[0]["key"] == "blank_1" and items[1]["key"] == "blank_2"
    assert items[0]["name"] == "未命名填写位"
    assert items[0]["low_confidence"] is True
    assert items[0]["anchor"] == "        "
    assert items[0]["_anchor_pos"] == 3


def test_slot_fallback_items_skips_covered_and_uses_hint():
    from rag.svr.template_fill.detector import slot_fallback_items
    cand = _slot_cand()
    items = slot_fallback_items([cand], covered=set())  # 未覆盖 → 兜底生成
    assert len(items) == 2
    assert items[0]["name"] == "招标项目名称"
    assert items[0]["low_confidence"] is False  # hint 位名字高可信
    # 覆盖后不再生成
    assert slot_fallback_items([cand], covered={(cand["index"], 1), (cand["index"], 2)}) == []


def test_merge_v2_fallback_same_shape_slots_occ_no_overflow():
    """同段两个同形空白位 + 各一条条目 → occ 预分配 1/2，无溢出丢弃。"""
    from rag.svr.template_fill.detector import _merge_detection
    cand = {
        "index": 3, "addr": "para:3", "text": "业主为        ，金额      万元",
        "slots": [
            {"start": 3, "end": 11, "text": "        ", "kind": "blank", "hint": ""},
            {"start": 14, "end": 20, "text": "      ", "kind": "blank", "hint": ""},
        ],
    }
    a = {"key": "owner", "addr": "para:3", "anchor": "        ", "_anchor_pos": 3,
         "low_confidence": False, "name": "业主", "line": 3}
    b = {"key": "amount", "addr": "para:3", "anchor": "      ", "_anchor_pos": 14,
         "low_confidence": False, "name": "金额", "line": 3}
    # 两个不同 anchor 文本：不同组，无 occ；再验证同形场景
    merged = _merge_detection([], [a, b], candidates=[cand])
    assert len(merged) == 2
    # cand2 文本 "甲     乙   "：anchor "   " 实际出现于 pos 1 与 pos 7（非重叠计数 2 处）
    same_a = {"key": "x1", "addr": "para:3", "anchor": "   ", "_anchor_pos": 1,
              "low_confidence": False, "name": "甲", "line": 3}
    same_b = {"key": "x2", "addr": "para:3", "anchor": "   ", "_anchor_pos": 7,
              "low_confidence": False, "name": "乙", "line": 3}
    cand2 = {"index": 3, "addr": "para:3", "text": "甲     乙   ", "slots": []}
    merged2 = _merge_detection([], [same_a, same_b], candidates=[cand2])
    assert len(merged2) == 2
    occs = sorted(it["occ"] for it in merged2 if "occ" in it)
    assert occs == [1, 2]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "slot"`
Expected: FAIL — `ImportError: cannot import name 'parse_slot_response'`

- [ ] **Step 3: 实现 V2 契约**

在 `detector.py` 的 `DETECT_SYSTEM`（105 行附近）之后追加：

```python
# V2「位编号→语义」契约：候选行的填写位由 blank_slots 在 run 层确定性切出，
# LLM 只标注字段语义、引用位编号，不再自选 anchor（根除拆碎片/漏识别/错位）。
DETECT_SYSTEM_V2 = """你是文档模板分析专家。用户给出固定模板中疑似需要填写的行（行号\\t文本），
每行下方列出该行已切分好的填写位：位[n]=「位文本」。填写位是模板留空待填的位置（下划线、空白、括号提示）。
请为每个填写位标注字段语义。输出 JSON 数组，每个元素：
{"line": 行号, "slot": 位序号, "key": "snake_case英文标识", "name": "中文字段名", "description": "给填写模型的说明", "retrieval_query": "适合检索的查询词", "required": true或false}
规则：
1. 每个填写位都必须标注，一行多位拆成多个元素；括号里的提示语去掉括号就是中文字段名（如 位文本=「 （项目审批、核准或备案机关名称）」→ name=项目审批、核准或备案机关名称）
2. key 全局唯一、snake_case、字母开头、不超过32字符；同一含义出现多次（如多个日期）也分别标注，系统自动区分位置
3. required 按招标文件惯例判断（项目名称/招标人/金额/日期等核心信息为 true，次要信息为 false）
4. 找不到任何填写位输出 []。只输出 JSON 数组，不要输出其它文字。"""
```

在 `parse_detection_response` 之后追加两个函数：

```python
def parse_slot_response(raw: str, candidates: list) -> tuple:
    """解析 V2 输出 → (items, covered)。covered = 已标注 (行号, 位序号) 集合，
    供兜底与跨块去重。anchor 由切位区间精确切片，LLM 无权决定 anchor；
    line/slot 引用非法（非整数、查无此位）直接丢弃该项。"""
    arr = _extract_json_array(raw)
    if arr is None:
        return [], set()
    slot_map = {}
    for c in candidates:
        for i, s in enumerate(c.get("slots") or [], start=1):
            slot_map[(c["index"], i)] = (c, s)
    out, covered = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        line, slot_no = it.get("line"), it.get("slot")
        if not isinstance(line, int) or isinstance(line, bool):
            continue
        if not isinstance(slot_no, int) or isinstance(slot_no, bool):
            continue
        if (line, slot_no) in covered:
            continue  # 同一 slot 被标注两次取第一个
        hit = slot_map.get((line, slot_no))
        if hit is None:
            continue
        cand, slot = hit
        covered.add((line, slot_no))
        key = normalize_key(it.get("key") or it.get("name") or "field")
        key = key[:KEY_MAX_LEN].rstrip("_") or "field"
        out.append({
            "key": key,
            "name": str(it.get("name") or key)[:100],
            "description": str(it.get("description") or ""),
            "retrieval_query": str(it.get("retrieval_query") or ""),
            "fill_mode": "llm",
            "required": bool(it.get("required", True)),
            "addr": cand["addr"],
            "anchor": slot["text"],
            "line": line,
            "top_k": 6,
            "low_confidence": False,
            "_anchor_pos": slot["start"],  # 切位精确偏移，供 occ 预分配排序
        })
    return out, covered


def slot_fallback_items(candidates: list, covered: set) -> list:
    """LLM 未标注的 slot 确定性兜底（结构性修漏识别）：每位恰好一条。
    hint 位 name=括号提示（不低置信——名字高可信，仅 key 机器生成）；
    blank 位 name=未命名填写位（低置信，B端确认时人工改名）。
    key=blank_{序号} 全局递增，确定性唯一；不派生默认值由下游 derive 链路
    天然保证（anchor 是纯留白/hint，derive_default_from_anchor 判空）。
    数学性质：位区间互不重叠 → occ 预分配不溢出、不撞车。"""
    out = []
    seq = 0
    for c in candidates:
        for i, s in enumerate(c.get("slots") or [], start=1):
            if (c["index"], i) in covered:
                continue
            seq += 1
            hint = (s.get("hint") or "").strip()
            if s.get("kind") == "hint" and hint:
                name, low = hint[:100], False
            else:
                name, low = "未命名填写位", True
            out.append({
                "key": f"blank_{seq}",
                "name": name,
                "description": "识别自括号提示的填写点" if not low else "识别兜底填写点，请确认字段名",
                "retrieval_query": hint if not low else "",
                "fill_mode": "llm",
                "required": True,
                "addr": c["addr"],
                "anchor": s["text"],
                "line": c["index"],
                "top_k": 6,
                "low_confidence": low,
                "_anchor_pos": s["start"],
            })
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "slot"`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): detector V2 位编号语义契约与确定性兜底"
```

---

### Task 4: detect_fill_points 分流 V1/V2

**Files:**
- Modify: `rag/svr/template_fill/detector.py:308-331`（_detect_chunked 旁新增 _detect_slot_chunked）、`detector.py:432-458`（detect_fill_points）
- Test: `test/test_template_fill_utils.py`

- [ ] **Step 1: 写失败测试（分流逻辑，LLM 打桩）**

在 `test/test_template_fill_utils.py` 末尾追加：

```python
def test_detect_fill_points_routes_v1_v2(monkeypatch):
    """有位行走 V2、无位行走 V1，兜底补漏，合并进 _merge_detection。
    _get_chat_model 打桩绕开 DB/LLM（实现见 Task 4 Step 3）。"""
    import asyncio

    from rag.svr.template_fill import detector

    v1_cand = {"index": 1, "addr": "para:1", "text": "项目名称：____________", "slots": []}
    v2_cand = _slot_cand()  # index=7, addr=para:7, 两个 hint 位

    captured = {}

    def fake_v1(chat, file_type, candidates):
        captured["v1"] = [c["index"] for c in candidates]
        return [], 0

    async def fake_v2(chat, candidates):
        captured["v2"] = [c["index"] for c in candidates]
        items = [{
            "key": "project_name", "name": "招标项目名称", "description": "",
            "retrieval_query": "", "fill_mode": "llm", "required": True,
            "addr": "para:7", "anchor": "    （招标项目名称） ", "line": 7,
            "top_k": 6, "low_confidence": False, "_anchor_pos": 5,
        }]
        return items, {(7, 1)}, 0

    class _FakeModel:
        async def async_chat(self, system, messages):
            return "[]"

    monkeypatch.setattr(detector, "_get_chat_model", lambda tid: _FakeModel(), raising=False)
    monkeypatch.setattr(detector, "_detect_chunked", fake_v1)
    monkeypatch.setattr(detector, "_detect_slot_chunked", fake_v2, raising=False)

    merged = asyncio.run(detector.detect_fill_points(
        "tenant", "docx", [v1_cand, v2_cand]))
    assert captured["v1"] == [1]
    assert captured["v2"] == [7]
    keys = sorted(it["key"] for it in merged)
    # 位1 由 LLM 标注；位2 未覆盖 → 兜底（blank_1 序号跨 cand 递增）
    assert "project_name" in keys
    assert "blank_1" in keys
    # 无 V1 撞车：兜底项 anchor 与 LLM 项不同位
    assert len(merged) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "routes_v1_v2"`
Expected: FAIL（旧 `detect_fill_points` 无 V2 分流/未抽 `_get_chat_model`，实测触发 DB/LLM 相关异常或断言失败——失败原因与新功能缺失相关即为正确红）

- [ ] **Step 3: 实现 _detect_slot_chunked 与分流**

在 `detector.py` 的 `_detect_chunked` 之后追加：

```python
async def _detect_slot_chunked(chat, candidates: list) -> tuple:
    """V2 分块并发识别（位编号→语义）。输入每行附位列表，输出 (items, covered, failed)。
    covered 用全局扁平 index 作行键，跨块无碰撞。"""
    chunks = [candidates[i:i + DETECT_CHUNK_SIZE]
              for i in range(0, len(candidates), DETECT_CHUNK_SIZE)]
    sem = asyncio.Semaphore(DETECT_CONCURRENCY)

    async def run_one(chunk: list) -> tuple:
        lines = []
        for c in chunk:
            lines.append(f'{c["index"]}\t{c["text"]}')
            for i, s in enumerate(c.get("slots") or [], start=1):
                lines.append(f'位[{i}]=「{s["text"]}」')
        user_msg = "行与填写位：\n" + "\n".join(lines)
        async with sem:
            ans = await chat(DETECT_SYSTEM_V2, [{"role": "user", "content": user_msg}])
        return parse_slot_response(ans, chunk)

    results = await asyncio.gather(*(run_one(c) for c in chunks), return_exceptions=True)
    items, covered, failed = [], set(), 0
    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            logger.warning("slot detect chunk failed: %s", r)
            continue
        its, cov = r
        items.extend(its)
        covered |= cov
    return items, covered, failed
```

把 `detect_fill_points` 改为（完整替换原函数体；模型获取抽成模块级 `_get_chat_model` 便于单测打桩）：

```python
def _get_chat_model(tenant_id: str):
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType
    model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    return LLMBundle(tenant_id, model_config)


async def detect_fill_points(tenant_id: str, file_type: str, candidates: list) -> list:
    """识别填写点 = 手动占位符直通 + V1（无位行 LLM 选 anchor）+ V2（有位行
    位编号语义标注 + 兜底）合并（失败抛异常，由 API 层转错误响应）。
    仅此处涉及 LLM/DB（延迟 import，保证纯函数部分无运行时依赖、可独立单测）。"""
    if not candidates:
        return []
    explicit = extract_explicit_placeholders(candidates)
    chat_mdl = _get_chat_model(tenant_id)

    async def _chat(system, messages):
        return await chat_mdl.async_chat(system, messages)

    with_slots = [c for c in candidates if c.get("slots")]
    without_slots = [c for c in candidates if not c.get("slots")]
    llm_items, covered, failed = [], set(), 0
    v1_items, failed1 = await _detect_chunked(_chat, file_type, without_slots)
    llm_items.extend(v1_items)
    failed += failed1
    if with_slots:
        v2_items, covered, failed2 = await _detect_slot_chunked(_chat, with_slots)
        llm_items.extend(v2_items)
        llm_items.extend(slot_fallback_items(with_slots, covered))
        failed += failed2
    # xlsx 不做 occ 预分配：apply_xlsx_placeholders 是 replace-all 语义不识别 occ，
    # 同格重复占位符预分配会串值覆盖——回到旧「去重丢弃、单 key replace-all」语义
    # candidates 透传：occ 预分配按实际出现次数封顶，超界组内溢出项丢弃而非判死整次识别
    merged = _merge_detection(explicit, llm_items, preassign_occ=(file_type != "xlsx"),
                              candidates=candidates)
    if failed:
        if not merged:
            raise RuntimeError(f"AI 识别失败：{failed} 个分块全部失败")
        logger.warning("detect: %d 个分块失败，返回部分合并结果（%d 项）", failed, len(merged))
    return merged
```

注意：原 `detect_fill_points` 内联的模型获取逻辑被 `_get_chat_model` 取代，docstring 中「延迟 import」语义保留在 `_get_chat_model` 内。

- [ ] **Step 4: 运行全部 detector 相关测试**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): detect_fill_points 按 V1/V2 分流，漏标位确定性兜底"
```

---

### Task 5: 真实范本语料验证（确定性部分，不调 LLM）

**Files:**
- Create: `.scratch/validate_blank_slots_corpus.py`（临时脚本，不提交）

- [ ] **Step 1: 写验证脚本**

```python
# -*- coding: utf-8 -*-
"""真实范本语料验证：切位器在真实文件上的覆盖与形态统计（确定性，不调 LLM）。"""
from docx import Document

from rag.svr.template_fill.blank_slots import extract_paragraph_slots
from rag.svr.template_fill.docx_utils import extract_docx_candidates

PATH = r"F:\投标项目\投标资料\最新招标文件标准范本\福建省信息化工程项目招标文件示范文本（试行）.docx"

cands = extract_docx_candidates(open(PATH, "rb").read())
with_slots = [c for c in cands if c["slots"]]
kinds = {}
for c in with_slots:
    for s in c["slots"]:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
total_slots = sum(kinds.values())
print(f"候选行: {len(cands)}，含位行: {len(with_slots)}，总位: {total_slots}，分布: {kinds}")
# 抽样打印：用户报告的「已由」句
for c in with_slots:
    if "已由" in c["text"] and "批准建设" in c["text"]:
        print("\n样本行:", repr(c["text"][:60]))
        for s in c["slots"]:
            print(f'  位[{c["slots"].index(s)+1}] kind={s["kind"]} hint={s["hint"]!r} text={s["text"]!r}')
        break
# 对抗性断言：区间互不重叠、text 与候选文本切片一致
for c in with_slots:
    runs_text_ok = all(c["text"].count(s["text"]) >= 1 for s in c["slots"])
    assert runs_text_ok, f"anchor 不在候选文本内: {c['addr']}"
    spans = [(s["start"], s["end"]) for s in c["slots"]]
    assert spans == sorted(spans) and all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), c["addr"]
print("\n全部区间互不重叠且 anchor 均在候选文本内: OK")
```

- [ ] **Step 2: 运行并人工核对**

Run: `PYTHONPATH=. .venv/Scripts/python.exe .scratch/validate_blank_slots_corpus.py`
Expected: 「已由」句切出 ≥3 个 hint 位（招标项目名称/审批机关/批文名称等），hint 文本与括号提示一致，区间校验 OK。若总位数为 0，回到 Task 1 排查 underline 判定。

.spec/.doc 语料（高速公路/水运等示范文本为 .doc）需经容器 LibreOffice 转换后才能验证 w:u 保留情况，属部署时人工抽查项（见文末部署节），不在本任务内。

- [ ] **Step 3: Commit（无源码变更则跳过）**

验证脚本属临时草稿，按项目规则留在 `.scratch/` 不提交。

---

### Task 6: 全量回归 + 文档收口

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）
- Modify: `CLAUDE.md`（参考表追加设计文档行）

- [ ] **Step 1: 全量后端测试**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py test/test_template_fill_blank_slots.py test/test_template_api_routes.py test/test_template_fill_service.py test/test_template_fill_executor.py test/test_template_fill_tool.py -v`
Expected: 全部 passed（含既有用例零回归；若 template_api_routes 有 candidates 结构断言需补 slots 键兼容，属预期小修）

- [ ] **Step 2: ruff 检查**

Run: `.venv/Scripts/python.exe -m ruff check rag/svr/template_fill/blank_slots.py rag/svr/template_fill/docx_utils.py rag/svr/template_fill/detector.py`
Expected: 无错误

- [ ] **Step 3: CHANGE.md 追加条目**

在 `CHANGE.md` 顶部追加（日期 2026-09-18）：

```markdown
## 2026-09-18 范本AI识别加固：run层确定性切位 + LLM语义标注

**主题**：修下划线场景识别四症状（拆碎片/漏识别/重复错位/提示语污染）

**核心变更**：
- 新增 `rag/svr/template_fill/blank_slots.py`：run 层切位器——下划线格式（w:u）留白 run + 字符下划线串确定性切出填写位区间（runs 拼接文本坐标系，与替换层一致）；含超链接段落整段回退
- `docx_utils.extract_docx_candidates` 候选行附带 `slots` 键；有位行即使不命中 FILL_HINT_RE 也入候选（结构性修漏识别）
- `detector.py` 分流：有位行走 DETECT_SYSTEM_V2「位编号→语义」契约（LLM 不再自选 anchor）；无位行走现有 V1 契约（存量范本零影响）；LLM 漏标位确定性兜底（hint→中文名不低置信，blank→未命名低置信，key=blank_N）
- V2 条目不派生默认值（hint 从源头阻断提示语污染）；下游渲染/前端/validate 零改动

**遗留**：underline=None 继承样式不识别（首版）；xlsx 不适用；兜底 key（blank_N）可读性一般依赖 B端人工改名
**部署**：未部署。后端 3 文件成套 SCP（blank_slots.py 新增 / docx_utils.py / detector.py）+ 容器重启 + import 冒烟
```

- [ ] **Step 4: CLAUDE.md 参考表追加**

在项目 `CLAUDE.md` 参考文档表格中追加一行：

```markdown
| 范本识别 run 层切位 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-18-template-detect-blank-slot-design.md` | ★ 下划线场景识别加固：run 层确定性切位（w:u 留白 run+字符下划线）+ LLM 降级为位编号语义标注 + 漏标位确定性兜底，根除拆碎片/漏识别/重复错位/提示语污染；实施计划 docs/superpowers/plans/2026-09-18-template-detect-blank-slot.md（状态见 CHANGE.md 2026-09-18 条目） |
```

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs(template-fill): 范本识别 run 层切位迭代记录与参考表登记"
```

---

## 部署（独立于计划，需用户明确授权）

后端 3 文件成套 SCP（`blank_slots.py` 新增 + `docx_utils.py` + `detector.py`）→ `docker restart docker-ragflow-cpu-1` → import 冒烟：

```bash
docker exec docker-ragflow-cpu-1 python -c "
from rag.svr.template_fill.blank_slots import extract_paragraph_slots
from rag.svr.template_fill.docx_utils import extract_docx_candidates
from rag.svr.template_fill.detector import parse_slot_response, slot_fallback_items
print('all imports OK')
"
```

部署后在 B端上传真实范本（信息化工程 docx）触发 AI 识别，人工核对确认清单：位数量、中文名质量、无碎片/无重复/无提示语默认值。
