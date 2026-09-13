# 范本 AI 识别加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通范本 AI 识别与渲染对页眉/页脚/文本框/内容控件的覆盖，支持同段同形留白多点填写（occ），加固 LLM 输出解析，前端低置信文字警示。

**Architecture:** 以 `docx_utils._build_addr_map` 单点编址扩展为核心（新前缀 `hdr:`/`ftr:`/`:tx<k>:` 增量向后兼容），候选提取/预览/替换/标蓝四链路自动受益；occ（第 N 次出现）语义贯穿识别 merge、校验、替换三层；docxtpl 原生渲染 header/footer/文本框内 `{{key}}`，渲染引擎零改动。

**Tech Stack:** Python (python-docx 1.1.2 / pytest / asyncio), React + TypeScript (Vite)。

**设计文档:** `docs/superpowers/specs/2026-09-13-template-detect-hardening-design.md`

**测试文件:** 全部后端测试写入 `test/test_template_fill_utils.py`（识别纯函数与 docx 工具测试均在此文件，末尾追加）。

**运行测试:** 仓库根目录 `uv run pytest test/test_template_fill_utils.py -k <name> -v`

---

### Task 1: `_build_addr_map` 骨架重构 + 文本框编址

**Files:**
- Modify: `rag/svr/template_fill/docx_utils.py`（`_build_addr_map` 及新增模块级函数）
- Test: `test/test_template_fill_utils.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试（文本框编址 + Fallback 去重）**

在 `test/test_template_fill_utils.py` 末尾追加：

```python
# ---------- 范本识别加固（2026-09-13）：文本框/页眉页脚/内容控件编址 ----------

_MC_NS = ('xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
          'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"')


def _make_docx_with_textbox(txbx_text):
    """正文段落内嵌浮动文本框，mc:AlternateContent 双份存储（Choice+Fallback 同文）。
    直接拼 XML 绕开 python-docx 无文本框 API 的限制（项目运行时同样按 XML 层遍历）。"""
    from docx.oxml import parse_xml
    doc = Document()
    doc.add_paragraph("无填写点的普通段落")
    p = doc.add_paragraph("含文本框的容器段落")
    xml = (
        f'<w:r {_MC_NS}>'
        '<mc:AlternateContent>'
        f'<mc:Choice Requires="wps"><w:txbxContent>'
        f'<w:p><w:r><w:t>{txbx_text}</w:t></w:r></w:p>'
        '</w:txbxContent></mc:Choice>'
        f'<mc:Fallback><w:txbxContent>'
        f'<w:p><w:r><w:t>{txbx_text}</w:t></w:r></w:p>'
        '</w:txbxContent></mc:Fallback>'
        '</mc:AlternateContent>'
        '</w:r>'
    )
    p._p.append(parse_xml(xml))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_textbox_paragraph_addressed_and_fallback_deduped():
    from rag.svr.template_fill.docx_utils import extract_docx_candidates, iter_docx_paragraphs
    blob = _make_docx_with_textbox("单位名称：＿＿＿＿＿＿")
    items = iter_docx_paragraphs(blob)
    hits = [it for it in items if "单位名称" in it["text"]]
    # Choice + Fallback 双份只编址一次
    assert len(hits) == 1
    assert hits[0]["addr"] == "para:1:tx0:0"
    # 容器段落自身无填写特征，不进候选；文本框段落进候选
    cands = extract_docx_candidates(blob)
    assert [c["addr"] for c in cands] == ["para:1:tx0:0"]
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/test_template_fill_utils.py::test_textbox_paragraph_addressed_and_fallback_deduped -v`
Expected: FAIL（`para:1:tx0:0` 不存在——现编址不进文本框，`hits` 为空）

- [ ] **Step 3: 实现**

`rag/svr/template_fill/docx_utils.py` 改动两处。

3a. 模块级常量与新函数（放在 `PH_RE` 定义之后）：

```python
TXBX_DEPTH_LIMIT = 8  # 文本框嵌套深度上限：超限子树跳过（畸形 XML 防爆栈）


def _iter_txbx_content(el) -> list:
    """深度优先收集 el 下全部 w:txbxContent（文本框内容根元素）。

    mc:AlternateContent 只下钻 mc:Choice、跳过 mc:Fallback——Word 对浮动文本框
    常存双份（Choice=wps、Fallback=VML），双份都收会让同一段落重复进候选
    （anchor 反查「匹配到多处」歧义 + LLM token 浪费）。"""
    out = []

    def _walk(node):
        for child in node:
            tag = child.tag
            if tag == qn("mc:Fallback"):
                continue
            if tag == qn("w:txbxContent"):
                out.append(child)
                continue  # 框内段落由 _walk_txbx 逐段处理（更深嵌套文本框随之发现）
            _walk(child)

    _walk(el)
    return out
```

3b. 整体替换 `_build_addr_map`（含 docstring）。保持存量正文编址逐字节不变：正文 `w:p` 仍 `para:<存量段号>`、顶层表格仍 `cell:<序号>`、嵌套表格仍 `:t<j>`、合并单元格去重不变。

> **返工要求（2026-09-13 质量审查 I-1）**：存量计数器与新区域隔离——`para:`/`cell:` 序号只随 body 直系 `w:p`/`w:tbl` 递增；文本框/页眉页脚/sdt 段落只消耗全文档扁平 `index`（排序用），不得挤占存量计数器；sdt 采用独立 `sdt:<k>:` 前缀（body 直系）或 `:sdt<k>:` 段（cell/文本框/嵌套内），不再「按正文规则编址不引入新前缀」。否则含新元素文档的存量 addr 整体错位、同形 anchor 静默错填，违反「存量模板行为完全不变」红线。

```python
def _build_addr_map(doc):
    """遍历文档全部可填写段落（正文/内容控件/表格/文本框，页眉页脚见 Task 2），
    返回 ({addr: Paragraph}, items)。

    编址（存量规则不变，新增前缀均为增量，旧 addr 永不复用）：
    - 正文段落 para:<idx>；表格内段落 cell:<tbl_no>:<row>:<col>:<para_idx>
      （tbl_no 只数顶层表格；嵌套表格在父单元格 addr 后追加 ":t<j>" 段递归）；
      合并单元格（gridSpan 横向合并）同一 tc 只按首现坐标编址一次。
    - 文本框段落 <父addr>:tx<k>:<pi>（k 为该段落内第 k 个文本框）；
      框内表格 <父addr>:tx<k>:cell:<t>:...；嵌套文本框继续追加 :tx<j> 段。
      mc:AlternateContent 只取 mc:Choice（见 _iter_txbx_content）。
    - w:sdt 内容控件独立前缀：body 直系 sdt:<k>:<pi>（内含表格 sdt:<k>:cell:...）；
      cell/文本框/嵌套内 sdt 追加 :sdt<k>: 段。新区域不消耗存量 para:/cell: 计数器。
    items: [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。
    """
    addr_map = {}
    items = []
    idx = 0        # 全文档扁平序号（items 排序用；新区域段落一并递增）
    para_seq = -1  # 存量正文段号：只随 body 直系 w:p 递增（I-1 红线：新区域不消耗）
    tbl_no = -1    # 存量顶层表格号：只随 body 直系 w:tbl 递增
    sdt_no = -1    # body 直系 sdt 序号（独立 sdt: 前缀）

    def _emit(p, addr):
        nonlocal idx
        addr_map[addr] = p
        items.append({"index": idx, "text": p.text, "addr": addr})
        idx += 1

    def _walk_sdt(sdt_el, prefix, depth):
        """内容控件展开：body 直系 → sdt:<k>:...；cell/文本框/嵌套内 →
        <父addr>:sdt<k>:...。内部段落/表格不消耗任何存量计数器；
        sdt 无 sdtContent（畸形）→ debug 日志跳过。"""
        if depth > TXBX_DEPTH_LIMIT:
            logger.warning("sdt nesting deeper than %d at %s, skipped",
                           TXBX_DEPTH_LIMIT, prefix)
            return
        content = sdt_el.find(qn("w:sdtContent"))
        if content is None:
            logger.debug("w:sdt without sdtContent at %s, skipped", prefix)
            return
        pi = 0
        tbl_k = 0
        sdt_k = 0
        for block in content.iterchildren():
            if block.tag == qn("w:p"):
                _walk_paragraph(Paragraph(block, doc), f"{prefix}:{pi}", depth)
                pi += 1
            elif block.tag == qn("w:tbl"):
                _walk_table(Table(block, doc), f"{prefix}:cell:{tbl_k}", depth)
                tbl_k += 1
            elif block.tag == qn("w:sdt"):
                _walk_sdt(block, f"{prefix}:sdt{sdt_k}", depth + 1)
                sdt_k += 1

    def _walk_table(tbl, prefix, depth=0):
        seen_tc = set()  # lxml 元素按底层 XML 节点判等：横向合并重复返回的 cell 去重
        for r, row in enumerate(tbl.rows):
            for c, cell in enumerate(row.cells):
                if cell._tc in seen_tc:
                    continue
                seen_tc.add(cell._tc)
                cell_addr = f"{prefix}:{r}:{c}"
                for pi, p in enumerate(cell.paragraphs):
                    _walk_paragraph(p, f"{cell_addr}:{pi}", depth)
                for j, sub in enumerate(cell.tables):
                    _walk_table(sub, f"{cell_addr}:t{j}", depth)
                for k, sdt in enumerate(cell._tc.findall(qn("w:sdt"))):
                    _walk_sdt(sdt, f"{cell_addr}:sdt{k}", depth)

    def _walk_paragraph(p, base_addr, txbx_depth=0):
        """编址段落自身及其内嵌文本框（正文/表格 cell/页眉页脚/文本框内通用）。"""
        _emit(p, base_addr)
        for k, txbx in enumerate(_iter_txbx_content(p._p)):
            _walk_txbx(txbx, f"{base_addr}:tx{k}", txbx_depth + 1)

    def _walk_txbx(txbx_el, prefix, depth):
        if depth > TXBX_DEPTH_LIMIT:
            logger.warning("textbox nesting deeper than %d at %s, skipped",
                           TXBX_DEPTH_LIMIT, prefix)
            return
        pi = 0
        tbl_k = 0
        sdt_k = 0
        for block in txbx_el.iterchildren():
            if block.tag == qn("w:p"):
                _walk_paragraph(Paragraph(block, doc), f"{prefix}:{pi}", depth)
                pi += 1
            elif block.tag == qn("w:tbl"):
                _walk_table(Table(block, doc), f"{prefix}:cell:{tbl_k}", depth)
                tbl_k += 1
            elif block.tag == qn("w:sdt"):
                _walk_sdt(block, f"{prefix}:sdt{sdt_k}", depth + 1)
                sdt_k += 1

    def _walk_body_blocks(parent_el):
        """body 直系块编址（存量计数器唯一递增点）：
        w:p → para:<para_seq>；w:tbl → cell:<tbl_no>:...；w:sdt → sdt:<sdt_no>:..."""
        nonlocal para_seq, tbl_no, sdt_no
        for block in parent_el.iterchildren():
            if block.tag == qn("w:p"):
                para_seq += 1
                _walk_paragraph(Paragraph(block, doc), f"para:{para_seq}")
            elif block.tag == qn("w:tbl"):
                tbl_no += 1
                _walk_table(Table(block, doc), f"cell:{tbl_no}")
            elif block.tag == qn("w:sdt"):
                sdt_no += 1
                _walk_sdt(block, f"sdt:{sdt_no}", 0)

    _walk_body_blocks(doc.element.body)
    return addr_map, items
```

- [ ] **Step 4: 运行新测试 + 存量回归**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "textbox or iter_docx or extract_docx or apply_docx or cross_run or colorize"`
Expected: 全部 PASS（新用例 + 存量编址/替换用例均绿——重构必须行为不变）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/docx_utils.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): 文本框编址进 _build_addr_map——tx前缀+AlternateContent Choice去重+sdt展开"
```

---

### Task 2: 页眉/页脚编址

**Files:**
- Modify: `rag/svr/template_fill/docx_utils.py`（`_build_addr_map` 末尾 `return` 前插入）
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def _make_docx_with_hf():
    """两节文档：第 1 节有显式页眉（含表格）+页脚；第 2 节 header 默认 linked。"""
    from docx.enum.section import WD_SECTION_START
    from docx.shared import Emu
    doc = Document()
    doc.add_paragraph("正文：________")
    h0 = doc.sections[0].header
    h0.is_linked_to_previous = False
    h0.paragraphs[0].text = "投标人（盖章）：____________"
    tbl = h0.add_table(rows=1, cols=1, width=Emu(4000000))
    tbl.rows[0].cells[0].paragraphs[0].text = "页眉表格：________"
    f0 = doc.sections[0].footer
    f0.is_linked_to_previous = False
    f0.paragraphs[0].text = "日期：____年____月____日"
    doc.add_section(WD_SECTION_START.NEW_PAGE)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_corrupt_hf_part_skips_without_killing_doc(monkeypatch):
    """单个 header/footer part 编址抛异常：跳过该 part（告警日志），不拖垮整文档。"""
    import rag.svr.template_fill.docx_utils as du

    def boom(hf_el, prefix, doc, walk_paragraph, walk_table):
        raise ValueError("corrupt part")

    monkeypatch.setattr(du, "_walk_hf_blocks", boom)
    items = du.iter_docx_paragraphs(_make_docx_with_hf())
    addrs = [it["addr"] for it in items]
    assert "para:0" in addrs  # 正文编址不受影响
    assert not any(a.startswith(("hdr:", "ftr:")) for a in addrs)  # 异常 part 整体跳过


def test_header_footer_addressed_and_linked_deduped():
    from rag.svr.template_fill.docx_utils import extract_docx_candidates, iter_docx_paragraphs
    items = iter_docx_paragraphs(_make_docx_with_hf())
    by_addr = {it["addr"]: it["text"] for it in items}
    assert "投标人（盖章）" in by_addr.get("hdr:0:0", "")
    assert "页眉表格" in by_addr.get("hdr:0:cell:0:0:0:0", "")
    assert "____年____月____日" in by_addr.get("ftr:0:0", "")
    # 第 2 节 header linked 到第 1 节（同一 part）→ 不重复编址
    assert sum(1 for a in by_addr if a.startswith("hdr:")) == 2  # 段落+表格段各一
    # 页眉页脚段落进候选
    cands = extract_docx_candidates(_make_docx_with_hf())
    addrs = {c["addr"] for c in cands}
    assert "hdr:0:0" in addrs and "ftr:0:0" in addrs
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/test_template_fill_utils.py::test_header_footer_addressed_and_linked_deduped -v`
Expected: FAIL（`hdr:0:0` 不存在）

- [ ] **Step 3: 实现**

在 `_build_addr_map` 的 `_walk_body_blocks(doc.element.body)` 之后、`return addr_map, items` 之前插入：

```python
    # 页眉/页脚：先判 linked 再访问 part/element——对无定义的 header 访问 .part
    # 会触发 _get_or_add_definition「自动补建定义」副作用。partname 字符串去重：
    # 多节/三类（default/first/even）共享同一 part 时只编址一次。
    seen_hf = set()
    hf_slots = (
        ("hdr", lambda s: s.header), ("hdr", lambda s: s.first_page_header),
        ("hdr", lambda s: s.even_page_header),
        ("ftr", lambda s: s.footer), ("ftr", lambda s: s.first_page_footer),
        ("ftr", lambda s: s.even_page_footer),
    )
    for sec_no, section in enumerate(doc.sections):
        for kind, get_hf in hf_slots:
            hf = get_hf(section)
            if hf is None or hf.is_linked_to_previous:
                continue
            partname = str(hf.part.partname)
            if partname in seen_hf:
                continue
            seen_hf.add(partname)
            try:
                _walk_hf_blocks(hf._element, f"{kind}:{sec_no}", doc,
                                _walk_paragraph, _walk_table)
            except Exception:
                # 单 part 编址失败（畸形节点等）跳过该 part，不拖垮整文档
                logger.exception("walk header/footer part %s failed, skipped", partname)
```

模块级新增（`_build_addr_map` 之前；提升到模块级以便测试 monkeypatch 打桩验证守卫）：

```python
def _walk_hf_blocks(hf_el, prefix, doc, walk_paragraph, walk_table):
    """页眉/页脚 part 编址：直系 w:p → <prefix>:<pi>（part 内局部序号）；
    直系 w:tbl → <prefix>:cell:<t>:...。hf_el 为该 part 根元素
    （python-docx 1.1.x _BaseHeaderFooter._element，is_linked_to_previous
    为 False 时必有定义）。walk_paragraph/walk_table 由调用方闭包注入。"""
    pi = 0
    tbl_no = 0
    for block in hf_el.iterchildren():
        if block.tag == qn("w:p"):
            walk_paragraph(Paragraph(block, doc), f"{prefix}:{pi}")
            pi += 1
        elif block.tag == qn("w:tbl"):
            walk_table(Table(block, doc), f"{prefix}:cell:{tbl_no}")
            tbl_no += 1
```

同步更新 `_build_addr_map` docstring 第二条为页眉页脚说明（见 Task 1 代码注释中的完整编址描述，补「页眉/页脚段落 hdr:<sec>:<pi> / ftr:<sec>:<pi>；part 内表格 …:cell:<t>:…；linked 节与共享 part 只编址一次」）。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "header_footer or corrupt or textbox or iter_docx"`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/docx_utils.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): 页眉页脚编址——hdr/ftr前缀+linked跳过+partname去重"
```

---

### Task 3: 内容控件（w:sdt）编址验证

Task 1 返工后 `_build_addr_map` 对 body 直系 `w:sdt` 以独立 `sdt:<k>:` 前缀编址（不消耗存量 `para:` 计数器），sdt 嵌 sdt 以 `:sdt<k>:` 段递归。本任务只补测试锁定行为。

**Files:**
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写测试（应直接通过）**

```python
def _make_docx_with_sdt():
    from docx.oxml import parse_xml
    doc = Document()
    doc.add_paragraph("普通段落")
    xml = (
        f'<w:sdt {_MC_NS}>'
        '<w:sdtPr><w:id w:val="1"/></w:sdtPr>'
        '<w:sdtContent>'
        '<w:p><w:r><w:t>内容控件里的日期：____年____月____日</w:t></w:r></w:p>'
        '</w:sdtContent>'
        '</w:sdt>'
    )
    doc.element.body.append(parse_xml(xml))
    doc.add_paragraph("sdt 之后的段落：____")  # 验证 sdt 不挤占存量 para 计数器
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_sdt_wrapped_paragraph_addressed():
    """body 直系 w:sdt：内部段落 sdt:0:0 独立前缀；存量 para 计数器不被挤占（I-1）。"""
    from rag.svr.template_fill.docx_utils import extract_docx_candidates, iter_docx_paragraphs
    items = iter_docx_paragraphs(_make_docx_with_sdt())
    hits = [it for it in items if "内容控件里的日期" in it["text"]]
    assert len(hits) == 1
    assert hits[0]["addr"] == "sdt:0:0"
    after = [it for it in items if it["text"].startswith("sdt 之后的段落")]
    assert len(after) == 1 and after[0]["addr"] == "para:1"  # sdt 未消耗 para 计数器
    cands = extract_docx_candidates(_make_docx_with_sdt())
    assert any("内容控件里的日期" in c["text"] for c in cands)
```

- [ ] **Step 2: 运行验证**

Run: `uv run pytest test/test_template_fill_utils.py::test_sdt_wrapped_paragraph_addressed -v`
Expected: PASS（Task 1 返工后已实现；若 FAIL 检查 `_walk_body_blocks` 的 `w:sdt` 分支与 `para_seq` 计数器隔离）

- [ ] **Step 3: Commit**

```bash
git add test/test_template_fill_utils.py
git commit -m "test(template-fill): 锁定 w:sdt 内容控件编址行为"
```

---

### Task 4: occ 替换层（docx_utils）

**Files:**
- Modify: `rag/svr/template_fill/docx_utils.py`（`_nth_index` 新增；`_replace_in_paragraph` / `_replace_cross_run_in_place` / `_replace_via_run_concat` / `apply_docx_placeholders` 加 occ 参数）
- Test: `test/test_template_fill_utils.py`（追加）

语义约定：`occ=None`（缺省/存量数据）= 现行为不变（单 run 全替换 / 跨 run 复刻 str.replace 全替换）；`occ=N`（正整数）= 只替换段落文本中第 N 次非重叠出现，越界 no-op 返回 False。

- [ ] **Step 1: 写失败测试**

```python
# ---------- occ 同段同形留白（第 N 次出现定位） ----------


def test_apply_same_shape_blanks_occ_positional():
    blob = _make_docx(["甲方：＿＿＿ 乙方：＿＿＿ 丙方：＿＿＿"])
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(blob, [
        {"addr": "para:0", "anchor": "＿＿＿", "key": "party_a", "occ": 1},
        {"addr": "para:0", "anchor": "＿＿＿", "key": "party_b", "occ": 2},
        {"addr": "para:0", "anchor": "＿＿＿", "key": "party_c", "occ": 3},
    ])
    assert iter_docx_paragraphs(out)[0]["text"] == "甲方：{{party_a}} 乙方：{{party_b}} 丙方：{{party_c}}"


def test_apply_occ_beyond_count_is_noop():
    blob = _make_docx(["姓名：＿＿ 电话：＿＿"])
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(blob, [
        {"addr": "para:0", "anchor": "＿＿", "key": "x", "occ": 3},
    ])
    assert "＿＿" in iter_docx_paragraphs(out)[0]["text"]


def test_apply_legacy_no_occ_replaces_all_in_run():
    """occ 缺省 = 存量 replace-all 语义，行为与加固前完全一致。"""
    blob = _make_docx(["姓名：＿＿ 电话：＿＿"])
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(blob, [
        {"addr": "para:0", "anchor": "＿＿", "key": "x"},
    ])
    assert iter_docx_paragraphs(out)[0]["text"] == "姓名：{{x}} 电话：{{x}}"


def test_apply_occ_across_runs_picks_nth():
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("AA")
    p.add_run("BB")
    p.add_run("AA")
    buf = io.BytesIO()
    doc.save(buf)
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(buf.getvalue(), [
        {"addr": "para:0", "anchor": "AA", "key": "second", "occ": 2},
    ])
    assert iter_docx_paragraphs(out)[0]["text"] == "AABB{{second}}"


def test_apply_occ_spanning_runs_falls_to_cross_run():
    """occ 目标出现跨 run（单 run 计数找不到）→ 落到跨 run 路径。"""
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("A_")
    p.add_run("_B")
    buf = io.BytesIO()
    doc.save(buf)
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders, iter_docx_paragraphs
    out = apply_docx_placeholders(buf.getvalue(), [
        {"addr": "para:0", "anchor": "__", "key": "span", "occ": 1},
    ])
    assert iter_docx_paragraphs(out)[0]["text"] == "A{{span}}B"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "occ"`
Expected: FAIL（apply 忽略 occ 字段，party_b/party_c 落错位或 no-op）

- [ ] **Step 3: 实现**

3a. 模块级辅助（放在 `_has_link_or_field` 之前）：

```python
def _nth_index(text: str, sub: str, n: int) -> int:
    """sub 在 text 中第 n（1-based）次非重叠出现的起始下标；不足 n 次返回 -1。
    非重叠语义与 str.count/str.find 循环推进一致。"""
    start = -1
    for _ in range(n):
        start = text.find(sub, start + 1)
        if start < 0:
            return -1
    return start
```

3b. `_replace_in_paragraph` 整体替换（增加 occ 参数；单 run 快路径改为跨 run 累计定位）：

```python
def _replace_in_paragraph(p: Paragraph, anchor: str, repl: str, occ: int | None = None) -> bool:
    """段内替换锚文本为 repl。occ=None 为存量 replace-all 语义；occ=N 只替换
    段落文本中第 N 次出现（不足 N 次返回 False）。
    优先单 run 内完成；跨 run 时：普通段落只重写 anchor 覆盖的 run 区间，区间外
    格式保留（见 _replace_cross_run_in_place）；含超链接/域的段落走 run 拼接
    替换（见 _replace_via_run_concat），避免超链接文本被复制进正文 run。"""
    if not anchor:
        return False
    if anchor not in p.text:
        return False
    consumed = 0
    for run in p.runs:
        n = run.text.count(anchor)
        if n == 0:
            continue
        if occ is None:
            # 存量语义：首个含 anchor 的 run 内全替换
            run.text = run.text.replace(anchor, repl)
            return True
        if consumed + n >= occ:
            # 目标出现完整落在该 run 内（run 局部序 = occ - 前序 run 累计）
            start = _nth_index(run.text, anchor, occ - consumed)
            if start < 0:  # 理论不可达（count 已保证存在），兜底
                return False
            run.text = run.text[:start] + repl + run.text[start + len(anchor):]
            return True
        consumed += n
    # 目标出现跨 run（或 occ=None 且 anchor 不在任何单 run 中）→ 既有路径
    if _has_link_or_field(p):
        return _replace_via_run_concat(p, anchor, repl, occ)
    if not p.runs:
        return False
    return _replace_cross_run_in_place(p, anchor, repl, occ)
```

3c. `_replace_cross_run_in_place` 签名与扫描起点（函数体首部替换，循环体不变）：

```python
def _replace_cross_run_in_place(p: Paragraph, anchor: str, repl: str, occ: int | None = None) -> bool:
```

原 `runs = p.runs` 与 `remaining = ...` 之间插入 occ 处理，`remaining` 改为：

```python
    runs = p.runs
    joined_all = "".join(r.text for r in runs)
    total = joined_all.count(anchor)
    scan_from = 0
    if occ is not None:
        if occ > total:
            return False
        # 跳过前 occ-1 次出现：扫描起点推进到第 occ 次出现起点
        for _ in range(occ - 1):
            scan_from = joined_all.find(anchor, scan_from) + len(anchor)
        remaining = 1
    else:
        remaining = total
    replaced = False
```

（其后 `while remaining > 0:` 循环体原样保留；循环内原 `joined = "".join(...)` 保留。）

3d. `_replace_via_run_concat` 签名与替换行：

```python
def _replace_via_run_concat(p: Paragraph, anchor: str, repl: str, occ: int | None = None) -> bool:
```

原 `replaced = joined.replace(anchor, repl)` 改为：

```python
    if occ is None:
        replaced = joined.replace(anchor, repl)
    else:
        start = _nth_index(joined, anchor, occ)
        if start < 0:
            return False
        replaced = joined[:start] + repl + joined[start + len(anchor):]
```

3e. `apply_docx_placeholders` 调用处透传：

```python
        if p is not None:
            _replace_in_paragraph(p, anchor, f"{{{{{key}}}}}", rep.get("occ"))
```

- [ ] **Step 4: 运行测试（新用例 + 存量 replace-all 语义回归）**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "occ or cross_run or apply_docx"`
Expected: 全部 PASS（尤其存量 `test_cross_run_replace_multiple_occurrences_across_runs`、`test_apply_docx_placeholders` 必须仍绿）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/docx_utils.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): occ第N次出现定位替换——单run/跨run/拼接三路径支持，缺省保持存量replace-all"
```

---

### Task 5: occ 识别链（merge 分配 + validate 校验）

**Files:**
- Modify: `rag/svr/template_fill/detector.py`（`_merge_detection`、`validate_placeholders`）
- Test: `test/test_template_fill_utils.py`（追加）

**语义（与设计 §4 对齐）：**
- occ 仅在**同源**同 `(addr, anchor)` 组内分配：手动直通组（explicit）多项 → occ 1..n（确定性，不打低置信）；LLM 组多项 → occ 1..n 且 occ≥2 打 `low_confidence`（同形留白歧义需人工核对）。
- LLM 项与手动项同 `(addr, anchor)` → 不给 LLM 项分配 occ，merge 按存量逻辑手动优先丢弃（防 LLM 幻觉回显 `{{key}}` 挤爆校验）。
- 收缩撞车回退（`_orig_anchor`）时 `occ` 作废（pop）——occ 属于收缩后 anchor 的组。
- validate：`occ` 缺省放行；存在时须为正整数且 ≤ anchor 在候选文本中的实际出现次数；手动行（addr 反查分支）`occ ≠ 1` 拒绝。

- [ ] **Step 1: 写失败测试**

```python
# ---------- occ 识别链：merge 分配 + validate 校验 ----------


def _mk_fill_item(key, anchor, addr="para:0"):
    return {"key": key, "name": key, "description": "", "retrieval_query": "",
            "fill_mode": "llm", "required": True, "addr": addr,
            "anchor": anchor, "line": 0, "top_k": 6}


def test_merge_llm_same_anchor_duplicates_get_occ_and_low_confidence():
    from rag.svr.template_fill.detector import _merge_detection
    merged = _merge_detection([], [_mk_fill_item("a", "＿＿"), _mk_fill_item("b", "＿＿")])
    assert [it.get("occ") for it in merged] == [1, 2]
    assert merged[0].get("low_confidence") in (None, False)
    assert merged[1]["low_confidence"] is True


def test_merge_explicit_duplicates_get_occ_without_low_confidence():
    from rag.svr.template_fill.detector import _merge_detection, extract_explicit_placeholders
    cands = [{"index": 0, "text": "{{name}} {{name}}", "addr": "para:0"}]
    explicit = extract_explicit_placeholders(cands)
    merged = _merge_detection(explicit, [])
    assert [it.get("occ") for it in merged] == [1, 2]
    assert all(not it.get("low_confidence") for it in merged)


def test_merge_llm_echo_of_explicit_anchor_still_dropped():
    """LLM 回显手动占位符同 anchor：手动优先丢弃，且不分配 occ（防 occ=2 挤爆校验）。"""
    from rag.svr.template_fill.detector import _merge_detection, extract_explicit_placeholders
    cands = [{"index": 0, "text": "编号：{{code}}", "addr": "para:0"}]
    explicit = extract_explicit_placeholders(cands)
    llm = [_mk_fill_item("code2", "{{code}}")]
    merged = _merge_detection(explicit, llm)
    assert [it["key"] for it in merged] == ["code"]
    assert all(it.get("occ") is None for it in merged)


def test_merge_shrink_rollback_strips_occ():
    """收缩撞车回退到 _orig_anchor 时 occ 作废。"""
    from rag.svr.template_fill.detector import _merge_detection
    a = _mk_fill_item("a", "＿＿")
    a["_orig_anchor"] = "甲方名称："
    b = _mk_fill_item("b", "＿＿")
    b["_orig_anchor"] = "乙方名称："
    merged = _merge_detection([], [a, b])
    by_key = {it["key"]: it for it in merged}
    assert by_key["b"]["anchor"] == "乙方名称："  # 回退保留
    assert "occ" not in by_key["b"]  # 回退项 occ 作废


def test_validate_occ_within_actual_count_passes():
    from rag.svr.template_fill.detector import validate_placeholders
    cands = [{"index": 0, "text": "甲：＿＿ 乙：＿＿", "addr": "para:0"}]
    ok, msg = validate_placeholders([_mk_fill_item("a", "＿＿", ) | {"occ": 2}], cands)
    assert ok, msg


def test_validate_occ_beyond_actual_count_rejected():
    from rag.svr.template_fill.detector import validate_placeholders
    cands = [{"index": 0, "text": "甲：＿＿", "addr": "para:0"}]
    ok, msg = validate_placeholders([_mk_fill_item("a", "＿＿") | {"occ": 2}], cands)
    assert not ok and "occ" in msg


def test_validate_manual_row_occ_gt_1_rejected():
    from rag.svr.template_fill.detector import validate_placeholders
    cands = [{"index": 0, "text": "甲：＿＿", "addr": "para:0"}]
    row = _mk_fill_item("a", "＿＿")
    row["addr"] = ""  # 手动行：靠 anchor 反查
    ok, msg = validate_placeholders([row | {"occ": 2}], cands)
    assert not ok


def test_validate_legacy_no_occ_still_passes():
    from rag.svr.template_fill.detector import validate_placeholders
    cands = [{"index": 0, "text": "甲：＿＿ 乙：＿＿", "addr": "para:0"}]
    ok, msg = validate_placeholders([_mk_fill_item("a", "＿＿")], cands)
    assert ok, msg
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "merge or validate"`
Expected: FAIL（occ 尚未实现：`occ` 键不存在 / 撞车项被丢弃）

- [ ] **Step 3: 实现**

3a. `_merge_detection` 整体替换：

```python
def _merge_detection(explicit: list, llm_items: list) -> list:
    """合并手动直通项与 LLM 识别项。
    - 同源同 (addr, anchor) 多份（同段同形留白 / 同一 {{key}} 出现多次）：
      预分配 occ（第 N 次出现定位，渲染层按次序落位）保留全部；LLM 组 occ≥2
      打低置信（同形留白歧义需人工核对），手动组确定性不打。
    - 跨源同 (addr, anchor)：仍手动优先丢弃——不给 LLM 项分配 occ，防 LLM
      幻觉回显 {{key}} 产生 occ=2 超界项挤爆 validate。
    - 收缩撞车回退：带 _orig_anchor 的项回退到原 anchor 保留并打低置信，
      occ 作废（属于收缩后 anchor 的组）。
    key 冲突按出现顺序加 _2/_3 后缀（作用域为整次识别）。"""
    merged, seen_anchor_pos, used_keys = [], set(), set()

    def _preassign_occ(items, mark_low_confidence: bool):
        totals, seq = {}, {}
        for it in items:
            p0 = (it["addr"], it["anchor"])
            totals[p0] = totals.get(p0, 0) + 1
        for it in items:
            p0 = (it["addr"], it["anchor"])
            if totals[p0] > 1:
                n = seq.get(p0, 0) + 1
                seq[p0] = n
                it["occ"] = n
                if mark_low_confidence and n > 1:
                    it.setdefault("low_confidence", True)

    explicit_pos = {(it["addr"], it["anchor"]) for it in explicit}
    _preassign_occ(explicit, mark_low_confidence=False)
    _preassign_occ(
        [it for it in llm_items if (it["addr"], it["anchor"]) not in explicit_pos],
        mark_low_confidence=True)

    for it in explicit + llm_items:
        orig_anchor = it.pop("_orig_anchor", None)
        pos = (it["addr"], it["anchor"])
        if pos in seen_anchor_pos:
            if orig_anchor and (it["addr"], orig_anchor) not in seen_anchor_pos:
                it.pop("occ", None)  # occ 属于收缩后 anchor 的组，回退后作废
                it["anchor"] = orig_anchor
                pos = (it["addr"], orig_anchor)
                it["low_confidence"] = True
            else:
                continue
        seen_anchor_pos.add(pos)
        base_key, key, n = it["key"], it["key"], 2
        while key in used_keys:
            key = f"{base_key}_{n}"
            n += 1
        used_keys.add(key)
        it["key"] = key
        merged.append(it)
    return merged
```

3b. `validate_placeholders`：在 addr 非空分支（`cand = cand_map.get(addr)` 的 membership 校验之后、`fill_mode` 校验之前）插入 occ 校验；手动行反查分支在唯一命中回填后同样校验：

```python
        occ = it.get("occ")
        if occ is not None:
            if not isinstance(occ, int) or isinstance(occ, bool) or occ < 1:
                return False, f"{key} 的 occ 非法（须为正整数）"
            actual = cand["text"].count(anchor)
            if occ > actual:
                return False, f"{key} 的锚文本出现次数不足（occ={occ}，实际{actual}）"
```

手动行反查分支（`if not addr:` 内、`it["addr"] = cand["addr"]` 回填后）插入：

```python
            occ = it.get("occ")
            if occ is not None and occ != 1:
                return False, f"{key} 的 occ 非法（手动添加行只支持 occ=1）"
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "merge or validate or occ"`
Expected: 全部 PASS（含存量 merge/validate 用例）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): occ同形留白识别链——同源组预分配occ+跨源手动优先+validate出现次数校验"
```

---

### Task 6: LLM 输出解析三级容错

**Files:**
- Modify: `rag/svr/template_fill/detector.py`（新增 `_extract_json_array`，`parse_detection_response` 改用它）
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# ---------- LLM 输出解析三级容错 ----------


def test_parse_bare_array_direct():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "姓名：＿＿", "addr": "para:0"}]
    items = parse_detection_response(
        '[{"line": 0, "anchor": "＿＿", "key": "name", "name": "姓名"}]', cands)
    assert len(items) == 1 and items[0]["key"] == "name"


def test_parse_fenced_array():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "姓名：＿＿", "addr": "para:0"}]
    raw = '```json\n[{"line": 0, "anchor": "＿＿", "key": "name", "name": "姓名"}]\n```'
    assert len(parse_detection_response(raw, cands)) == 1


def test_parse_array_with_surrounding_prose():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "姓名：＿＿", "addr": "para:0"}]
    raw = '识别结果如下：\n[{"line": 0, "anchor": "＿＿", "key": "name", "name": "姓名"}]\n以上。'
    assert len(parse_detection_response(raw, cands)) == 1


def test_parse_multiple_arrays_takes_balanced_first_via_greedy_fallback():
    """多数组输入贪婪正则会整体失败 → 返回 []（保守失败，不误采半截）。"""
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "姓名：＿＿", "addr": "para:0"}]
    raw = '[{"line": 0}] 中间文字 [{"line": 1}]'
    assert parse_detection_response(raw, cands) == []


def test_parse_garbage_returns_empty():
    from rag.svr.template_fill.detector import parse_detection_response
    cands = [{"index": 0, "text": "姓名：＿＿", "addr": "para:0"}]
    assert parse_detection_response("我无法完成该任务", cands) == []
    assert parse_detection_response(None, cands) == []
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "parse_"`
Expected: `test_parse_bare_array_direct` PASS（存量路径覆盖）、`test_parse_fenced_array` / `test_parse_array_with_surrounding_prose` 可能 PASS（贪婪正则恰好兜住）、`test_parse_multiple_arrays...` 需确认语义。若全部意外 PASS，仍需落地三级结构（贪婪回退是最后一级，直解优先减少误伤）——以 Step 4 全绿为准。

- [ ] **Step 3: 实现**

detector.py 新增（`parse_detection_response` 之前）：

```python
def _extract_json_array(raw: str):
    """三级容错抽取 LLM 输出中的 JSON 数组：
    1. 裸数组直解（规整输出最快路径）；
    2. 剥 ``` 代码围栏后直解；
    3. 贪婪正则 [.*] 回退（存量语义，多数组等畸形输入在此保守失败）。
    全部失败返回 None（调用方按 0 项处理，上层置 failed 不静默）。"""
    raw = (raw or "").strip()
    candidates = [raw]
    fenced = re.match(r"^```[\w-]*\s*(.*?)\s*```$", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            arr = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(arr, list):
            return arr
    return None
```

`parse_detection_response` 开头改为：

```python
def parse_detection_response(raw: str, candidates: list) -> list:
    """解析 LLM 输出 → 校验后的建议清单。行号/锚文本不合法的项直接丢弃。"""
    arr = _extract_json_array(raw)
    if arr is None:
        return []
    cand_map = {c["index"]: c for c in candidates}
```

（删除原 `m = re.search(...)` 至 `if not isinstance(arr, list): return []` 五行；其余逐项校验不变。）

- [ ] **Step 4: 运行测试**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "parse"`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "fix(template-fill): LLM识别输出三级容错解析——裸数组直解/剥围栏/贪婪回退"
```

---

### Task 7: 前端低置信徽标 + 0 候选文案 + 注释同步

**Files:**
- Modify: `web/src/pages/template-fill/placeholder-table.tsx`（锚文本列加徽标）
- Modify: `api/apps/restful_apis/template_api.py:65-67`（`_ZERO_CANDIDATES_MSG`）
- Modify: `rag/svr/template_fill/renderer.py:68-69`（`_colorize_placeholder_runs` docstring 覆盖面描述）

- [ ] **Step 1: 前端徽标**

Read `web/src/pages/template-fill/placeholder-table.tsx`，找到表头 `锚文本` 对应的 TableCell（渲染 `row.anchor` 的那一格）。在该格内容最前面插入：

```tsx
{row.low_confidence && (
  <span
    title="该项由 AI 收缩修正或存在同形留白，请核对锚文本是否落在正确空位"
    className="mr-1 inline-block rounded bg-amber-100 px-1 py-0.5 text-xs text-amber-700"
  >
    低置信
  </span>
)}
```

- [ ] **Step 2: 0 候选文案**

`template_api.py` 的 `_ZERO_CANDIDATES_MSG` 整体替换为：

```python
_ZERO_CANDIDATES_MSG = ("未从模板中识别到任何疑似填写位置（已扫描正文、表格、页眉、页脚、文本框）。"
                        "若模板由旧版 .doc 转换而来，可能存在格式丢失，建议用 Word 另存为 .docx 后重新上传；"
                        "或在正文中手写 {{字段名}} 占位符后重试，也可到详情页手动添加填写点")
```

- [ ] **Step 3: renderer 注释同步**

`renderer.py:68-69` 的注释「覆盖面与 docx_utils._build_addr_map 一致（正文段落 + 表格 cell 含嵌套表格），占位符只会出现在这些位置」改为：

```python
覆盖面与 docx_utils._build_addr_map 一致（正文/内容控件/表格 cell 含嵌套表格/文本框/页眉/页脚），占位符只会出现在这些位置。
```

- [ ] **Step 4: 前端构建验证**

Run: `cd web && npm run build`
Expected: 构建成功无 TS 报错

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/template-fill/placeholder-table.tsx api/apps/restful_apis/template_api.py rag/svr/template_fill/renderer.py
git commit -m "feat(template-fill): 低置信文字徽标+0候选文案覆盖面说明+标蓝注释同步"
```

---

### Task 8: 全量回归 + 文档收尾

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）
- Modify: `CLAUDE.md`（参考表该设计文档状态更新）

- [ ] **Step 1: 后端全量回归**

Run: `uv run pytest test/test_template_fill_utils.py test/test_template_api_routes.py -v`
Expected: 全部 PASS（含 detect worker 全链路 monkeypatch 用例）

Run: `uv run pytest test/ -k "template" -q`
Expected: 全部 PASS

- [ ] **Step 2: 容器内冒烟（可选，本机有 Docker 时）**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from rag.svr.template_fill.docx_utils import _build_addr_map, extract_docx_candidates
from rag.svr.template_fill.detector import detect_fill_points, validate_placeholders
print('imports OK')
"
```

- [ ] **Step 3: CHANGE.md 增量条目（顶部追加）**

```markdown
## 2026-09-13 范本 AI 识别加固

**主题**：识别覆盖面扩展 + 同形留白多点 + 解析与警示加固

**核心变更**：
- `_build_addr_map` 单点编址扩展：页眉/页脚（hdr:/ftr: 前缀，linked 跳过 + partname 去重）、文本框（:tx<k>: 后缀，mc:AlternateContent 只取 Choice）、body 直系 w:sdt 递归展开——候选提取/B端预览/替换/标蓝四链路自动受益，docxtpl 原生渲染新区域 {{key}}
- occ 语义：同段同形留白不再静默丢弃，同源组预分配 occ（第 N 次出现定位），替换三路径（单 run/跨 run/拼接）支持；缺省 occ=None 保持存量 replace-all
- LLM 输出解析三级容错（裸数组直解 → 剥围栏 → 贪婪回退）
- 前端低置信「低置信」文字徽标（title 说明收缩修正/同形留白）+ 0 候选文案标注覆盖面

**遗留**：
- 已填现值型（段落无留白特征不进 FILL_HINT_RE 候选）仍不识别（设计文档记为已知边界）
- LLM 同段幻觉重复同一 anchor 且实际只出现 1 次时，occ 校验拒绝整批（保守失败，罕见）
- C 端 docx-preview 保真预览对新区域的展示未专项适配

**部署**：未部署。后端成套 SCP：docx_utils.py / detector.py / template_api.py / renderer.py；前端 placeholder-table.tsx（npm run build + dist SCP）。无数据库变更。
```

- [ ] **Step 4: CLAUDE.md 参考表状态更新**

将 `2026-09-13-template-detect-hardening-design.md` 行末「设计完成（2026-09-13，未实施）」改为「已完成编码+测试（2026-09-13，未部署，后端 4 文件成套 SCP + 前端 build）」。

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 范本AI识别加固迭代记录与参考表状态更新"
```

---

## 已知边界（实施者必读）

1. **occ 与贪婪全替换的边界**：LLM 幻觉性地对同一段落同一 anchor 输出 2 项而文本实际只有 1 次出现时，validate 会拒绝整批识别（保守失败优于静默错位）。跨源回显 `{{key}}` 已通过 merge 手动优先策略拦截（Task 5）。
2. **linked header 副作用防御顺序**：必须先判 `is_linked_to_previous` 再访问 `hf.part` / `hf._element`（后者对无定义 header 会自动补建 part）。
3. **存量兼容红线**：任何用例失败若涉及存量 addr（para:/cell:）或 replace-all 行为变化，一律视为回归，必须修实现而不是改测试。
4. 页眉/页脚内文本框由 `_walk_paragraph` 通用逻辑天然覆盖（hf 段落也走 `_walk_paragraph`），无需额外代码；但无专项测试（构造成本高），依赖 Task 1/2 单元组合。
