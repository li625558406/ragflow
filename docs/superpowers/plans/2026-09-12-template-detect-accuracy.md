# 范本 AI 识别准确性与格式保真改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 治理范本 AI 识别三类问题——标签/正文被选为 anchor（标蓝/覆盖）、跨 run 替换破坏段内格式，并提供 B端预览划选手动标记兜底。

**Architecture:** 后端两层：`detector.py` 在 `parse_detection_response` 内做 anchor 后处理（标签型收缩到留白/丢弃、实心 anchor 收缩或打 `low_confidence` 标记，随占位符 JSON 自然落库透传前端）；`docx_utils.py` 跨 run 替换从「整段重写」改为「只重写 anchor 覆盖的 run 区间」。前端纯增量：填写点表格 low_confidence 警示底色 + 详情页 Word 预览划选文字一键加行。

**Tech Stack:** Python (pytest 单测，正则后处理与 python-docx run 操作) / React + TypeScript（划选交互）

**Spec:** `docs/superpowers/specs/2026-09-12-template-detect-accuracy-design.md`

**关键事实（已核实，执行者无需再查）：**
- 预览接口 `GET /template/fill/<id>/preview`（`api/apps/restful_apis/template_api.py:474`）items 已含 `addr` 字段（`iter_docx_paragraphs` 直通），前端类型已声明——**本计划不动后端预览接口**。
- 占位符以 list[dict] JSON 落库（`TplTemplateVersionService.save_placeholders`），`validate_placeholders` 不拒绝额外字段——`low_confidence` 加进 item dict 即自动落库、详情接口自动返回。
- `extract_explicit_placeholders`（手动 `{{key}}` 直通）不经过 `parse_detection_response`，天然不受后处理影响，无需豁免。
- 现有测试集中在 `test/test_template_fill_utils.py`（含 hyperlink、多次出现等既有用例，改完必须全绿）。
- 命令：后端 `uv run pytest test/test_template_fill_utils.py -v`（仓库根目录）；lint `ruff check rag/svr/template_fill/`；前端 `cd web && npm run build`。

---

### Task 1: detector.py — anchor 识别后处理（治①标签变蓝 + ③原文被覆盖）

**Files:**
- Modify: `rag/svr/template_fill/detector.py`
- Test: `test/test_template_fill_utils.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试（后处理纯函数 + parse 集成）**

在 `test/test_template_fill_utils.py` 末尾追加（注意测试文本中的下划线是全角 `＿` U+FF3F、空格是全角 `　` U+3000，与真实范本一致）：

```python
# ---------- 识别后处理：标签/实心 anchor 收缩修正与低置信标记 ----------

def test_shrink_anchor_to_blank_finds_blank_after_label():
    from rag.svr.template_fill.detector import _shrink_anchor_to_blank
    line = "投标人名称：＿＿＿＿＿＿（盖章）"
    assert _shrink_anchor_to_blank("投标人名称：", line) == "＿＿＿＿＿＿"


def test_shrink_anchor_to_blank_no_blank_returns_empty():
    from rag.svr.template_fill.detector import _shrink_anchor_to_blank
    assert _shrink_anchor_to_blank("投标人名称：", "投标人名称：签字") == ""


def test_parse_label_anchor_shrunk_to_blank():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "投标人名称：＿＿＿＿＿＿", "addr": "para:0"}]
    raw = ('[{"line":0,"anchor":"投标人名称：","key":"bidder_name",'
           '"name":"投标人名称","retrieval_query":"","fill_mode":"llm","required":true}]')
    out = parse_detection_response(raw, cands)
    assert len(out) == 1
    assert out[0]["anchor"] == "＿＿＿＿＿＿"
    assert out[0]["low_confidence"] is False


def test_parse_label_anchor_without_blank_dropped():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "包括以下内容：", "addr": "para:0"}]
    raw = ('[{"line":0,"anchor":"包括以下内容：","key":"content",'
           '"name":"内容","retrieval_query":"","fill_mode":"llm","required":true}]')
    assert parse_detection_response(raw, cands) == []


def test_parse_solid_anchor_with_blank_in_line_shrunk():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "工程名称　　（填写完整名称）　开工日期", "addr": "para:0"}]
    raw = ('[{"line":0,"anchor":"工程名称","key":"project_name",'
           '"name":"工程名称","retrieval_query":"","fill_mode":"llm","required":true}]')
    out = parse_detection_response(raw, cands)
    assert len(out) == 1
    assert out[0]["anchor"] == "（填写完整名称）"


def test_parse_solid_anchor_no_blank_in_line_low_confidence_kept():
    from rag.svr.template_fill.detector import parse_detection_response
    # 已填范本现值：整行无留白特征，anchor 合法保留但打低置信
    cands = [{"index": 0, "text": "合同金额为人民币壹佰万元整", "addr": "para:0"}]
    raw = ('[{"line":0,"anchor":"壹佰万元整","key":"amount",'
           '"name":"金额","retrieval_query":"","fill_mode":"llm","required":true}]')
    out = parse_detection_response(raw, cands)
    assert len(out) == 1
    assert out[0]["anchor"] == "壹佰万元整"
    assert out[0]["low_confidence"] is True


def test_parse_blank_anchor_no_low_confidence():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "编号：＿＿＿＿＿＿", "addr": "para:0"}]
    raw = ('[{"line":0,"anchor":"＿＿＿＿＿＿","key":"code",'
           '"name":"编号","retrieval_query":"","fill_mode":"llm","required":true}]')
    out = parse_detection_response(raw, cands)
    assert len(out) == 1
    assert out[0]["anchor"] == "＿＿＿＿＿＿"
    assert out[0]["low_confidence"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k "shrink or label_anchor or solid_anchor or blank_anchor" -v`
Expected: FAIL（`_shrink_anchor_to_blank` 不存在 / `low_confidence` 字段不存在）

- [ ] **Step 3: 实现 detector.py 后处理**

3a. 在 `_HINT_ANCHOR_RE` 定义之后（`detector.py:40` 附近）追加：

```python
# 留白标记特征（收缩修正的目标形态）：下划线串（含全角＿）/ 连续空格（含全角　）/
# 括号提示（如"（投标人名称）"）。anchor 本身含留白标记 = 无需修正。
_BLANK_MARK_RE = re.compile(r"[_＿]{2,}|[ \u3000]{2,}|[（(][^（）()]*[）)]")


def _shrink_anchor_to_blank(anchor: str, line_text: str) -> str:
    """标签/实心 anchor 收缩修正：在 anchor 结束位置之后找第一个留白标记串，
    返回该串作为新 anchor；找不到返回空串。治两类识别错误：
    ① LLM 把字段标签（"投标人名称："）选为 anchor → 收缩到标签后的留白；
    ③ LLM 把正文原文选为 anchor → 同行有留白时收缩到留白，避免原文被覆盖。"""
    pos = line_text.find(anchor)
    if pos < 0:
        return ""
    m = _BLANK_MARK_RE.search(line_text, pos + len(anchor))
    return m.group(0) if m else ""
```

3b. `parse_detection_response` 中，在

```python
        cand = cand_map.get(line)
        if cand is None or not anchor or anchor not in cand["text"]:
            continue
```

之后、`key = normalize_key(...)` 之前插入：

```python
        # 标签/实心 anchor 后处理（治①③，prompt 只是引导，代码层兜底）：
        # 标签型 → 收缩到标签后的留白串，收缩失败丢弃（标签绝不能整体被替换）；
        # 实心（无留白特征）→ 同行有留白特征则收缩，失败保留并打低置信；
        # 整行无留白特征 → 已填范本现值是合法 anchor，保留并打低置信警示。
        low_confidence = False
        if _LABEL_ANCHOR_RE.fullmatch(anchor):
            shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
            if not shrunk:
                continue
            anchor = shrunk
        elif not _BLANK_MARK_RE.search(anchor):
            if _BLANK_MARK_RE.search(cand["text"]):
                shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
                if shrunk:
                    anchor = shrunk
                else:
                    low_confidence = True
            else:
                low_confidence = True
```

3c. 同函数 `out.append({...})` 字典末尾（`"top_k": 6,` 之后）加一项：

```python
            "low_confidence": low_confidence,
```

3d. `DETECT_SYSTEM` 规则 1 改为（整行替换）：

```
1. anchor 必须是该行原文的精确子串，禁止改写；一行可有多个填写点（拆成多个元素）。anchor 只能选留白标记（下划线串/连续空格/括号提示）或已填写的现值本身，禁止选字段标签（如"申请人："这类冒号结尾引导词）或正文叙述文字。
```

- [ ] **Step 4: 跑测试确认通过（含既有用例回归）**

Run: `uv run pytest test/test_template_fill_utils.py -v`
Expected: 全部 PASS（既有 `test_parse_detection_response_*` 用例不受影响——它们断言字段值而非整字典相等；若发现按 `== [{...}]` 断言完整字典的用例需同步补 `low_confidence: False`）

- [ ] **Step 5: lint + 提交**

```bash
ruff check rag/svr/template_fill/detector.py
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "fix(template): 识别后处理治标签/实心anchor误选——收缩到留白+low_confidence警示"
```

---

### Task 2: docx_utils.py — 跨 run 区间替换保格式（治②格式错乱）

**Files:**
- Modify: `rag/svr/template_fill/docx_utils.py:125-146`（`_replace_in_paragraph`）
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试（跨 run 格式保真）**

在测试文件末尾追加：

```python
# ---------- 跨 run 区间替换（格式保真） ----------

def _doc_with_runs(texts, bolds=None):
    """构造单段落多 run 文档：texts 为各 run 文本，bolds 为对应加粗标记。"""
    buf = io.BytesIO()
    doc = Document()
    p = doc.add_paragraph()
    for k, t in enumerate(texts):
        r = p.add_run(t)
        if bolds and bolds[k]:
            r.bold = True
    doc.save(buf)
    return buf.getvalue()


def test_cross_run_replace_preserves_outside_runs():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    blob = _doc_with_runs(["AB", "CD", "EF"], [False, False, True])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "BC", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert [r.text for r in p.runs] == ["A{{k}}", "D", "EF"]
    assert p.runs[2].bold  # 区间外 run 的格式完整保留


def test_cross_run_replace_span_three_runs_clears_middle():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    blob = _doc_with_runs(["AB", "CD", "EF"])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "BCDE", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert [r.text for r in p.runs] == ["A{{k}}", "", "F"]


def test_cross_run_replace_anchor_at_end_single_tail_run():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    blob = _doc_with_runs(["AB", "CD"])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "CD", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert "".join(r.text for r in p.runs) == "AB{{k}}"


def test_cross_run_replace_anchor_whole_paragraph():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    blob = _doc_with_runs(["AB", "CD"])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "ABCD", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert "".join(r.text for r in p.runs) == "{{k}}"


def test_cross_run_replace_multiple_occurrences_across_runs():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    blob = _doc_with_runs(["A_", "_A"])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "_", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert "".join(r.text for r in p.runs) == "AZZA"


def test_cross_run_replace_anchor_equals_replacement_no_loop():
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    # 手动占位符直通场景：anchor == repl，必须幂等且不死循环
    blob = _doc_with_runs(["AB", "{{k}}", "CD"])
    out = apply_docx_placeholders(blob, [{"addr": "para:0", "anchor": "{{k}}", "key": "k"}])
    p = Document(io.BytesIO(out)).paragraphs[0]
    assert "".join(r.text for r in p.runs) == "AB{{k}}CD"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k "cross_run" -v`
Expected: FAIL（`test_cross_run_replace_preserves_outside_runs` 断言 run 切分不符——现实现整段重写进首 run；其余用例部分可能侥幸通过，以第一条 FAIL 为准）

- [ ] **Step 3: 实现区间替换**

3a. 在 `_replace_via_run_concat` 之后新增两个函数：

```python
def _locate_run_span(runs: list, start: int, end: int) -> tuple:
    """在 run 文本拼接坐标系里返回覆盖 [start, end) 的
    (首run下标 i, 尾run下标 j, anchor在首run内偏移, anchor在尾run内偏移)。"""
    pos = 0
    i = j = -1
    off_i = off_j = 0
    for idx, r in enumerate(runs):
        n = len(r.text)
        if i < 0 and pos + n > start:
            i, off_i = idx, start - pos
        if pos + n >= end:
            j, off_j = idx, end - pos
            break
        pos += n
    if i < 0 or j < 0:  # 理论不可达（anchor 必在拼接串覆盖范围内），兜底尾 run
        i, j, off_i, off_j = len(runs) - 1, len(runs) - 1, len(runs[-1].text), len(runs[-1].text)
    return i, j, off_i, off_j


def _replace_cross_run_in_place(p: Paragraph, anchor: str, repl: str) -> bool:
    """跨 run 区间替换（格式保真）：只重写 anchor 覆盖的 run 区间——
    首 run 保留 anchor 前文本并接替换值，尾 run 保留 anchor 后文本，中间 run 清空；
    区间外 run 原样不动（段内其他位置格式完整保留）。
    按替换前出现次数循环保持 str.replace「全部替换」语义，且不重扫替换产物
    （repl 含 anchor，如手动占位符 anchor==repl 场景，不会死循环）。"""
    runs = p.runs
    remaining = "".join(r.text for r in runs).count(anchor)
    replaced = False
    while remaining > 0:
        remaining -= 1
        joined = "".join(r.text for r in runs)
        start = joined.find(anchor)
        if start < 0:
            break
        end = start + len(anchor)
        i, j, off_i, off_j = _locate_run_span(runs, start, end)
        if i == j:
            runs[i].text = runs[i].text[:off_i] + repl + runs[i].text[off_j:]
        else:
            runs[i].text = runs[i].text[:off_i] + repl
            for mid in range(i + 1, j):
                runs[mid].text = ""
            runs[j].text = runs[j].text[off_j:]
        replaced = True
    return replaced
```

3b. `_replace_in_paragraph` 末尾的整段重写分支：

```python
    runs = p.runs
    if not runs:
        return False
    runs[0].text = p.text.replace(anchor, repl)
    for r in runs[1:]:
        r.text = ""
    return True
```

替换为：

```python
    if not p.runs:
        return False
    return _replace_cross_run_in_place(p, anchor, repl)
```

3c. 同步更新文件头部注释第 9-10 行（「跨 run 替换取舍」段）：把「普通段落跨 run 时整段重写进首 run、清空其余（牺牲段内混合格式）」改为「普通段落跨 run 时只重写 anchor 覆盖的 run 区间，区间外格式保留（`_replace_cross_run_in_place`）」。

- [ ] **Step 4: 跑全量测试确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -v`
Expected: 全部 PASS。既有 `test_apply_docx_anchor_twice_in_same_paragraph`（同 run 多次出现）、`test_apply_docx_hyperlink_paragraph_no_duplicate`（超链接路径未动）必须仍绿。

- [ ] **Step 5: lint + 提交**

```bash
ruff check rag/svr/template_fill/docx_utils.py
git add rag/svr/template_fill/docx_utils.py test/test_template_fill_utils.py
git commit -m "fix(template): docx跨run替换改区间重写——区间外run格式保留治格式错乱"
```

---

### Task 3: 前端 — low_confidence 警示底色

**Files:**
- Modify: `web/src/hooks/use-template-fill-request.ts:6-19`（`TplPlaceholder` 接口）
- Modify: `web/src/pages/template-fill/placeholder-table.tsx:192`（`<TableRow>`）

- [ ] **Step 1: 类型加字段**

`TplPlaceholder` 接口 `default_source` 行之后加：

```typescript
  /** 识别后处理标记：anchor 为实心文字且无法收缩到留白，需人工重点核对 */
  low_confidence?: boolean;
```

- [ ] **Step 2: 表格行加警示底色**

`placeholder-table.tsx` 中 `<TableRow key={i}>` 改为：

```tsx
<TableRow key={i} className={row.low_confidence ? 'bg-amber-50' : undefined}>
```

- [ ] **Step 3: 提交**

```bash
git add web/src/hooks/use-template-fill-request.ts web/src/pages/template-fill/placeholder-table.tsx
git commit -m "feat(template): 填写点表格low_confidence行警示底色"
```

---

### Task 4: 前端 — B端详情页预览划选手动标记

**Files:**
- Modify: `web/src/pages/template-fill/detail.tsx`（docx 预览分支，约 337-349 行）

- [ ] **Step 1: 加划选状态与处理函数**

在 `const [testOpen, setTestOpen] = useState(false);` 之后加：

```tsx
  // 划选标记候选：在预览段落中选中文字后记录 {addr, 文本}，段落旁浮出标记按钮
  const [mark, setMark] = useState<{ addr: string; text: string } | null>(null);
```

在 `removeRow` 函数之后加：

```tsx
  // 预览划选：mouseup 时若非折叠选区完整落在该段落内则记为候选
  const handleParaMouseUp = (
    item: { addr: string },
    e: React.MouseEvent<HTMLParagraphElement>,
  ) => {
    const sel = window.getSelection();
    const text = (sel?.toString() ?? '').trim();
    const p = e.currentTarget;
    if (
      !sel ||
      sel.isCollapsed ||
      !text ||
      !p.contains(sel.anchorNode) ||
      !p.contains(sel.focusNode)
    ) {
      setMark(null);
      return;
    }
    setMark({ addr: item.addr, text });
  };

  // 划选确认：追加一行填写点（key 自动生成占位，anchor/addr 取自选区）
  const addMarkedRow = () => {
    if (!mark) return;
    const keys = new Set(placeholders.map((r) => r.key.trim()));
    let key = 'field';
    let n = 2;
    while (keys.has(key)) {
      key = `field_${n}`;
      n += 1;
    }
    setPlaceholders((prev) => [
      ...prev,
      { ...emptyPlaceholder(), key, addr: mark.addr, anchor: mark.text },
    ]);
    setMark(null);
    window.getSelection()?.removeAllRanges();
  };
```

- [ ] **Step 2: 改 docx 预览渲染（段落可划选 + 标记按钮）**

docx 分支（`previewItems.map((item) => (<p ...>...</p>))` 那段）整体替换为：

```tsx
              <div className="max-h-[65vh] space-y-1 overflow-auto">
                {previewItems.map((item) => (
                  <div key={item.index} className="flex items-start gap-1">
                    <p
                      className={`flex-1 px-2 py-1 text-sm leading-6 ${
                        item.placeholder_key ? 'bg-yellow-100' : ''
                      }`}
                      onMouseUp={(e) => handleParaMouseUp(item, e)}
                    >
                      {renderTextWithPlaceholders(item.text)}
                    </p>
                    {mark?.addr === item.addr && (
                      <Button
                        size="sm"
                        className="shrink-0"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={addMarkedRow}
                      >
                        标记为填写点
                      </Button>
                    )}
                  </div>
                ))}
              </div>
```

说明：`onMouseDown` preventDefault 防止点击按钮时浏览器清除选区；`mark.addr === item.addr` 在合并单元格重复 addr 时按钮可能出现多处，点任意一处即完成（`addMarkedRow` 会清 `mark`），可接受。xlsx 分支不动。

- [ ] **Step 3: 构建验证**

Run: `cd web && npm run build`
Expected: 构建成功无 TS 错误

- [ ] **Step 4: 提交**

```bash
git add web/src/pages/template-fill/detail.tsx
git commit -m "feat(template): B端范本预览划选文字一键标记为填写点"
```

---

### Task 5: 收尾 — 文档登记与验证

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）
- Modify: `CLAUDE.md:59`（参考表该行状态更新）

- [ ] **Step 1: 全量回归**

```bash
uv run pytest test/test_template_fill_utils.py test/test_template_api_routes.py -v
ruff check rag/svr/template_fill/
```
Expected: 全部 PASS，无 lint 报错

- [ ] **Step 2: CHANGE.md 顶部追加条目**

```markdown
## 2026-09-12 范本AI识别准确性与格式保真改造

**主题**：治理范本 AI 识别三类问题（标签变蓝/格式错乱/原文被覆盖）+ 划选手动标记

**核心变更**：
- detector.py 识别后处理：标签型 anchor 收缩到留白（失败丢弃）、实心 anchor 收缩或打 low_confidence，prompt 同步强化
- docx_utils.py 跨 run 替换改区间重写（_replace_cross_run_in_place），区间外 run 格式保留
- B端范本详情页：low_confidence 行警示底色 + Word 预览划选文字一键标记为填写点（预览接口本就透传 addr，无后端改动）

**遗留**：未部署；部署时 rag/svr/template_fill/ 两个后端文件成套 SCP + docker restart，前端需 npm run build + SCP + nginx reload

**设计**：docs/superpowers/specs/2026-09-12-template-detect-accuracy-design.md
```

- [ ] **Step 3: CLAUDE.md 参考表该行状态更新**

把「设计完成未实施」改为「已完成编码，未部署」。

- [ ] **Step 4: 代码审查**

按项目规范做一轮代码审查（dispatch code-reviewer subagent 或人工 review diff），重点：后处理不影响手动 `{{key}}` 直通与已填范本 detected 派生；区间替换对 hyperlink 段落路径零影响。

- [ ] **Step 5: 提交文档**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 范本识别准确性+格式保真改造迭代记录"
```

---

## 部署约束（用户明确指示后执行，禁止自动部署）

1. 后端成套 SCP（缺一会报错）：`rag/svr/template_fill/detector.py` + `rag/svr/template_fill/docx_utils.py` → `docker restart docker-ragflow-cpu-1`
2. 前端：`npm run build` → dist.tar.gz SCP → 容器内解压（`rm -rf dist/*` 保 inode）→ `nginx -s reload`
3. 冒烟：容器内 `python -c "from rag.svr.template_fill.detector import parse_detection_response; from rag.svr.template_fill.docx_utils import apply_docx_placeholders; print('ok')"`
