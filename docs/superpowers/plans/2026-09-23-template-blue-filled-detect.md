# 范本蓝色已填值识别 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docx 范本中蓝色字体已填值被 AI 识别为填写点（kind="blue" slot），旧值自动沉淀为该点 default_value，下次填写由 AI 换新值。

**Architecture:** 扩展既有 V2 切位管线：`blank_slots.py` 在 run 层新增蓝色 run 簇切位（B 通道占优判定，blue 优先于下划线），`detector.py` 对 blue 位豁免「不派生默认值」硬闸（default_value=位文本）+ LLM 漏标兜底。下游确认/渲染/默认值消费链零改动。

**Tech Stack:** Python 3.12 / python-docx（`font.color.rgb` → `str(RGBColor)` 6位hex）/ pytest（必须用 `.venv`，见仓库 CLAUDE.md）。

**设计稿:** `docs/superpowers/specs/2026-09-23-template-blue-filled-detect-design.md`

**测试运行方式（重要）:** 在仓库根 `D:/AI/ragflow2` 执行 `.venv/Scripts/python.exe -m pytest <file> -v`。

---

### Task 1: blank_slots 蓝色判定与基础切位

**Files:**
- Modify: `rag/svr/template_fill/blank_slots.py`（新增 `_is_blue_run` + 主扫描循环 blue 分支）
- Test: `test/test_template_fill_blank_slots.py`

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_blank_slots.py` 顶部 import 区追加：

```python
from docx.shared import RGBColor


def _add_colored_runs(p, runs_spec):
    """runs_spec: [(text, underline, rgb)]，rgb 为 (r,g,b) 元组或 None。"""
    for text, u, rgb in runs_spec:
        r = p.add_run(text)
        if u is not None:
            r.font.underline = u
        if rgb is not None:
            r.font.color.rgb = RGBColor(*rgb)
    return p
```

文件末尾追加测试：

```python
def test_blue_value_run_slotted():
    """蓝色实心文字 run = blue 位（已填值标记）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("本招标项目", None, None),
        ("福建省厦门市", None, (0x00, 0x00, 0xFF)),
        ("，建设单位", None, None),
    ])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    s = slots[0]
    assert s["kind"] == "blue"
    assert s["text"] == "福建省厦门市"
    assert s["hint"] == ""
    runs_text = "".join(r.text for r in p.runs)
    assert runs_text[s["start"] : s["end"]] == s["text"]


def test_blue_multi_run_cluster_one_slot():
    """同一值被 Word 拆成相邻多个蓝色 run → 成簇切一个位。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("李", None, (0x00, 0x70, 0xC0)),
        ("港", None, (0x00, 0x70, 0xC0)),
    ])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["text"] == "李港"
    assert slots[0]["kind"] == "blue"


def test_blue_whitespace_run_not_slotted():
    """对抗：蓝色格式打在纯空白上不算已填值（无实心内容）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("前文", None, None),
        ("   ", None, (0x00, 0x00, 0xFF)),
        ("后文", None, None),
    ])
    assert extract_paragraph_slots(p) == []


def test_blue_channel_dominance_threshold():
    """蓝色判定 = B 通道占优 ≥40：常见蓝全命中，黑/灰/红/绿不命中。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    blue_cases = [(0x00, 0x00, 0xFF), (0x00, 0x70, 0xC0), (0x44, 0x72, 0xC4),
                  (0x1F, 0x4E, 0x79), (0x80, 0x80, 0xFF)]
    for rgb in blue_cases:
        p = _add_colored_runs(_para([]), [("值", None, rgb)])
        slots = extract_paragraph_slots(p)
        assert len(slots) == 1 and slots[0]["kind"] == "blue", f"{rgb} 应判蓝"

    non_blue_cases = [(0x00, 0x00, 0x00), (0x80, 0x80, 0x80), (0xFF, 0x00, 0x00),
                      (0x00, 0xB0, 0x50), (0xFF, 0xFF, 0x00)]
    for rgb in non_blue_cases:
        p = _add_colored_runs(_para([]), [("值", None, rgb)])
        assert extract_paragraph_slots(p) == [], f"{rgb} 不应判蓝"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v -k blue`
Expected: 4 个新用例 FAIL（切不出 blue 位 / `KeyError` / 断言空列表失败）

- [ ] **Step 3: 实现**

`rag/svr/template_fill/blank_slots.py`：在 `_CHAR_UNDERLINE_RE` 定义之后追加：

```python
# 蓝色已填值判定：rgb 直值 B 通道严格占优（B - max(R,G) ≥ 40）。覆盖纯蓝/
# 0070C0/主题蓝 4472C4/深蓝 1F4E79 等，排除黑灰（差 0）/红/绿。仅主题色无
# rgb 直值 → 保守不判（宁漏不误）；空白文本不算值。
_BLUE_DIFF = 40


def _is_blue_run(run) -> bool:
    if not (run.text or "").strip():
        return False
    rgb = run.font.color.rgb
    if rgb is None:
        return False
    hex6 = str(rgb)
    if len(hex6) != 6:
        return False
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    return b - max(r, g) >= _BLUE_DIFF
```

`extract_paragraph_slots` 主循环：在 `if bool(runs[i].font.underline):` 分支**之前**插入 blue 簇分支（位于 `if not t:` 之后）：

```python
        if _is_blue_run(runs[i]):
            j = i
            while j < n and _is_blue_run(runs[j]):
                j += 1
            ct = "".join((runs[k].text or "") for k in range(i, j))
            segs.append([pos, pos + len(ct), "blue", ""])
            pos += len(ct)
            i = j
            continue
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v`
Expected: 全部 PASS（含既有用例，回归零破坏）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/blank_slots.py test/test_template_fill_blank_slots.py
git commit -m "feat(template-fill): blank_slots 蓝色 run 切位——B通道占优判定+blue簇"
```

---

### Task 2: blue 合并边界与优先级

**Files:**
- Modify: `rag/svr/template_fill/blank_slots.py`（merge 循环 blue 隔离规则）
- Test: `test/test_template_fill_blank_slots.py`

- [ ] **Step 1: 写失败测试**

文件末尾追加：

```python
def test_blue_gap_whitespace_merged():
    """相邻 blue 段之间只隔空白 → 合并为一个值段。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("李", None, (0x00, 0x00, 0xFF)),
        (" ", None, None),
        ("港", None, (0x00, 0x00, 0xFF)),
    ])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["text"] == "李 港"
    assert slots[0]["kind"] == "blue"


def test_blue_underline_filled_slot_blue_wins():
    """下划线空位被填蓝字：blue 优先，成 blue 位而非被下划线簇吞掉。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("项目名称：", None, None),
        ("李港", True, (0x00, 0x70, 0xC0)),
        (" 地址：", None, None),
    ])
    slots = extract_paragraph_slots(p)
    assert len(slots) == 1
    assert slots[0]["kind"] == "blue"
    assert slots[0]["text"] == "李港"


def test_blue_and_blank_adjacent_stay_separate():
    """对抗：蓝值后紧跟下划线空位（同为留白格式/紧邻）→ 两个独立位，
    空位不得卷进 blue 值段。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [
        ("李港", None, (0x00, 0x00, 0xFF)),
        ("____", True, None),
        ("尾", None, None),
    ])
    slots = extract_paragraph_slots(p)
    kinds = [(s["kind"], s["text"]) for s in slots]
    assert ("blue", "李港") in kinds
    assert ("blank", "____") in kinds


def test_blue_over_500_skipped():
    """对抗：超长蓝值（>500 字符）不产位（与 _MAX_SLOT_LEN 同口径）。"""
    from rag.svr.template_fill.blank_slots import extract_paragraph_slots

    p = _add_colored_runs(_para([]), [("蓝" * 501, None, (0x00, 0x00, 0xFF))])
    assert extract_paragraph_slots(p) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v -k "gap or underline_filled or adjacent or over_500"`
Expected: `test_blue_gap_whitespace_merged`、`test_blue_and_blank_adjacent_stay_separate` FAIL（现 merge 逻辑会把 blue 并进相邻段或把空位并进 blue）；其余两个可能已过（blue 优先在 Task 1 已实现、封顶既有逻辑），如实记录即可

- [ ] **Step 3: 实现**

`extract_paragraph_slots` merge 循环，在 hint-hint 相邻独立分支之后、`gap_blank` 计算之前插入：

```python
        # blue 位只与 blue 合并：与 blank/hint 相邻/gap-blank 一律不并——
        # 蓝值后紧跟的下划线空位是另一个填写位，并入会把空位文本卷进默认值
        if (prev[2] == "blue") != (s[2] == "blue"):
            merged.append(s)
            continue
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/blank_slots.py test/test_template_fill_blank_slots.py
git commit -m "feat(template-fill): blue位合并边界——只与blue合并防空位卷入值段"
```

---

### Task 3: detector blue 位 default_value 派生

**Files:**
- Modify: `rag/svr/template_fill/detector.py:244-311`（`parse_slot_response`）
- Test: `test/test_template_fill_utils.py`

- [ ] **Step 1: 写失败测试**

`test/test_template_fill_utils.py` 文件末尾追加：

```python
def test_parse_slot_response_blue_default_value():
    """blue 位豁免「不派生默认值」硬闸：default_value = 位文本（旧值即默认值）；
    其余位维持 "" 不回归。"""
    from rag.svr.template_fill.detector import parse_slot_response
    cand = _slot_cand()
    cand["slots"].append({"start": 30, "end": 36, "text": "福建省厦门市",
                          "kind": "blue", "hint": ""})
    raw = '[{"line":7,"slot":1,"key":"project_name","name":"招标项目名称"},' \
          '{"line":7,"slot":3,"key":"city","name":"城市"}]'
    items, covered = parse_slot_response(raw, [cand])
    assert covered == {(7, 1), (7, 3)}
    assert items[0]["default_value"] == ""  # hint 位硬闸不回归
    assert items[1]["default_value"] == "福建省厦门市"
    assert items[1]["anchor"] == "福建省厦门市"
    assert items[1]["_anchor_pos"] == 30


def test_parse_slot_response_blue_slot_text_corrupt_dropped():
    """对抗：slot 文本不再是候选原文子串（坐标系腐坏）→ blue 位同样丢弃，
    不静默产脏 anchor/默认值。"""
    from rag.svr.template_fill.detector import parse_slot_response
    cand = _slot_cand()
    cand["slots"].append({"start": 30, "end": 36, "text": "不存在的旧值",
                          "kind": "blue", "hint": ""})
    raw = '[{"line":7,"slot":3,"key":"city","name":"城市"}]'
    items, covered = parse_slot_response(raw, [cand])
    assert items == [] and covered == set()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "blue_default_value or corrupt_dropped"`
Expected: `test_parse_slot_response_blue_default_value` FAIL（blue 位 default_value 为 `""`）；`corrupt_dropped` 可能已过（既有 membership 防御），如实记录

- [ ] **Step 3: 实现**

`rag/svr/template_fill/detector.py` `parse_slot_response` 产物 dict 中：

```python
            # V2 硬闸（设计 §4.5：V2 条目一律不派生默认值）：显式带空 default_value，
            # _merge_defaults 分支1按「键存在」触发 → 无论 anchor 形态都跳过派生。
            # 用既有序列化字段而非新标记键：B端默认值列/显式清空态本就理解 ""，
            # 不向 API/DB JSON 泄漏内部标记；下游 default_map 按 truthy 过滤，"" 天然不触发。
            "default_value": "",
```

改为：

```python
            # V2 硬闸（设计 §4.5）：留白/hint 位一律不派生默认值（切出的位无值可派生），
            # 显式带空 default_value，_merge_defaults 分支1按「键存在」触发跳过派生。
            # 例外：blue 位（蓝色已填值）位文本本身就是旧值 → default_value = 位文本
            # （来源=detected，下次填写 default_map 兜底，AI 换新值时被整体替换）。
            # 用既有序列化字段而非新标记键：B端默认值列/显式清空态本就理解 ""，
            # 不向 API/DB JSON 泄漏内部标记；下游 default_map 按 truthy 过滤，"" 天然不触发。
            "default_value": slot["text"] if slot.get("kind") == "blue" else "",
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v`
Expected: 全部 PASS（既有用例回归零破坏）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): parse_slot_response blue位默认值派生=位文本"
```

---

### Task 4: slot_fallback_items blue 兜底

**Files:**
- Modify: `rag/svr/template_fill/detector.py:314-352`（`slot_fallback_items`）
- Test: `test/test_template_fill_utils.py`

- [ ] **Step 1: 写失败测试**

`test/test_template_fill_utils.py` 文件末尾追加：

```python
def test_slot_fallback_items_blue_default_value():
    """LLM 漏标 blue 位 → 兜底成点：name=已填填写点、低置信、default_value=位文本
    （值是确定性的，不因 LLM 漏标丢失）。"""
    from rag.svr.template_fill.detector import slot_fallback_items
    cand = _slot_cand()
    cand["slots"].append({"start": 30, "end": 36, "text": "福建省厦门市",
                          "kind": "blue", "hint": ""})
    items = slot_fallback_items([cand], covered=set())
    blue = [it for it in items if it["anchor"] == "福建省厦门市"]
    assert len(blue) == 1
    it = blue[0]
    assert it["name"] == "已填填写点"
    assert it["low_confidence"] is True
    assert it["default_value"] == "福建省厦门市"
    assert it["fill_mode"] == "llm"


def test_slot_fallback_hint_blank_default_unchanged():
    """回归闸：hint/blank 兜底位 default_value 仍为 ""。"""
    from rag.svr.template_fill.detector import slot_fallback_items
    cand = _slot_cand()
    items = slot_fallback_items([cand], covered=set())
    assert len(items) == 2
    assert all(it["default_value"] == "" for it in items)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v -k "fallback_blue or default_unchanged"`
Expected: `test_slot_fallback_items_blue_default_value` FAIL（现 blue 位走 else 分支 name=未命名填写位、default_value=""）；回归闸可能已过，如实记录

- [ ] **Step 3: 实现**

`slot_fallback_items` 循环体内：

```python
            hint = (s.get("hint") or "").strip()
            if s.get("kind") == "hint" and hint:
                name, low = hint[:100], False
            else:
                name, low = "未命名填写位", True
```

改为：

```python
            hint = (s.get("hint") or "").strip()
            default_value = ""
            if s.get("kind") == "blue":
                # blue 位兜底：值确定不丢，仅语义命名低置信（LLM 漏标）
                name, low, default_value = "已填填写点", True, s["text"]
            elif s.get("kind") == "hint" and hint:
                name, low = hint[:100], False
            else:
                name, low = "未命名填写位", True
```

同函数产物 dict 中 `"default_value": "",`（带「V2 硬闸（同 parse_slot_response）」注释那行）改为：

```python
                # V2 硬闸（同 parse_slot_response）：留白/hint 位不派生默认值；
                # blue 位例外——位文本即旧值，直接沉淀
                "default_value": default_value,
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_utils.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): slot_fallback blue位兜底——值沉淀+低置信语义"
```

---

### Task 5: V2 prompt 增补 blue 位说明

**Files:**
- Modify: `rag/svr/template_fill/detector.py:120-128`（`DETECT_SYSTEM_V2`）

- [ ] **Step 1: 修改 prompt**

`DETECT_SYSTEM_V2` 的规则末尾（`4. 找不到任何填写位输出 []...` 之后）追加一条：

```python
5. 位文本可能已是模板里预填好的旧值（蓝色字体标记的已填位），这样的位同样要标注字段语义（后续填写时会整体替换该旧值）。
```

- [ ] **Step 2: 运行全量回归**

Run: `.venv/Scripts/python.exe -m pytest test/test_template_fill_blank_slots.py test/test_template_fill_utils.py test/test_template_fill_executor.py test/test_template_fill_service.py test/test_template_fill_tool.py -v`
Expected: 全部 PASS

- [ ] **Step 3: Commit**

```bash
git add rag/svr/template_fill/detector.py
git commit -m "feat(template-fill): V2 prompt 增补蓝色已填位语义标注说明"
```

---

### Task 6: 全量回归 + 真实文件端到端验证

**Files:** 无新改动

- [ ] **Step 1: template_fill 全套件回归**

Run: `.venv/Scripts/python.exe -m pytest test/ -k template_fill -v`
Expected: 全部 PASS

- [ ] **Step 2: 真实段落端到端验证（本地 .venv 脚本）**

用 python-docx 造一个含「黑色标签 + 蓝色旧值 + 下划线空位」混合段落，直接跑 `extract_paragraph_slots` 确认 blue 位切出、坐标切片一致（候选封装 `extract_docx_candidates` 已有既有测试覆盖，此处只验切位层）：

脚本落盘 `.scratch/_blue_slots_check.py` 后执行：

```python
from docx import Document
from docx.shared import RGBColor
from rag.svr.template_fill.blank_slots import extract_paragraph_slots

doc = Document()
p = doc.add_paragraph()
for text, rgb, u in [("项目名称：", None, None), ("李港测试值", (0x00, 0x70, 0xC0), None), ("　", None, True)]:
    r = p.add_run(text)
    if rgb: r.font.color.rgb = RGBColor(*rgb)
    if u: r.font.underline = u
slots = extract_paragraph_slots(p)
for s in slots:
    print(s)
runs_text = "".join(r.text for r in p.runs)
for s in slots:
    assert runs_text[s["start"]:s["end"]] == s["text"]
kinds = {s["kind"] for s in slots}
assert "blue" in kinds and "blank" in kinds
print("OK")
```

Run: `cd D:/AI/ragflow2 && PYTHONPATH=. .venv/Scripts/python.exe .scratch/_blue_slots_check.py`
Expected: 打印 blue 位（text=李港测试值）与 blank 位，末尾 `OK`

- [ ] **Step 3: 最终 Commit（如有测试夹具补充）**

```bash
git add -A test/ rag/svr/template_fill/
git commit -m "test(template-fill): 蓝色已填值识别全量回归通过"
```

（无改动则跳过）

---

## 部署说明（不在本计划内执行）

按仓库约束：后端 2 文件（`blank_slots.py`、`detector.py`）成套 SCP + 容器重启；
存量范本需在详情页**重新识别**才生效；前端零改动。禁止自动部署。
