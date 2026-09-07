# 模板填写系统 P1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定 Word/Excel 模板的治理能力落地——3 张 tpl_ 表 + 模板管理 API + B 端「模板库」页签（上传向导 / 占位符确认 / 发布 / 预览），生成带 `{{占位符}}` 的工作副本供 P2 执行引擎渲染。

**Architecture:** 模板是独立资产（不放知识库）。上传原件存 MinIO（bucket=template_id），LLM 识别填写点建议 → B 端人工确认 → python-docx/openpyxl 把锚文本替换为 `{{key}}` 生成 render_file。REST API 放 `api/apps/restful_apis/` 自动注册到 `/api/v1`。P1 不含执行引擎与任务（tpl_fill_task 仅建表）。

**Tech Stack:** Peewee (DataBaseModel/CommonService)、Quart Blueprint (`@manager.route` + `@login_required`)、python-docx、openpyxl（均为已有依赖，**P1 不引入 docxtpl**）、`settings.STORAGE_IMPL`（MinIO）、LLMBundle（租户默认 chat 模型）、React 18 + shadcn/ui + TanStack Query。

**关键参照文件（写码前先读）：**
- API 风格范本：`api/apps/restful_apis/analysis_template_api.py`（最简 Blueprint）
- ORM 范本：`api/db/db_models.py:2061`（CollectionPolicyExt）；`migrate_db` 在同文件 :2539
- 段落遍历范本：`api/apps/restful_apis/flow_app.py:355`（`_build_para_map`，w:p/w:tbl 遍历规则）
- 前端范本：`web/src/pages/analysis-templates/index.tsx` + `web/src/hooks/use-analysis-template-request.ts`
- 测试范本：`test/test_flow_doc_table_edit.py`（stub-load 模式）

**约定：**
- 工作目录 `D:\AI\ragflow2`，当前分支 `feat/unified-crawler-framework`，**不部署服务器、不重启 Docker**。
- 后端验证：`uv run ruff check <file>` + `uv run pytest test/<file> -v`（纯工具类测试不需要本机 MySQL/ES）。
- 前端验证：`cd web && npx tsc --noEmit`（涉及文件 0 error）。
- 前端文案只用中文，只加 `web/src/locales/zh.ts`。

---

## File Structure

```
api/db/db_models.py                          # 修改：+3 表 + migrate_db 兜底
api/db/services/template_fill_service.py     # 新建：TplTemplateService / TplTemplateVersionService
api/apps/restful_apis/template_api.py        # 新建：模板管理 REST API
rag/svr/template_fill/__init__.py            # 新建：空包
rag/svr/template_fill/docx_utils.py          # 新建：docx 段落遍历/候选提取/占位符替换
rag/svr/template_fill/xlsx_utils.py          # 新建：xlsx 单元格遍历/占位符替换
rag/svr/template_fill/detector.py            # 新建：LLM 填写点识别 prompt + 解析校验
test/test_template_fill_utils.py             # 新建：docx/xlsx/detector 单测
test/test_template_api_routes.py             # 新建：API 模块 stub-load 冒烟测试
web/src/utils/api.ts                         # 修改：+URL 常量
web/src/hooks/use-template-fill-request.ts   # 新建：React Query hooks + types
web/src/pages/template-fill/index.tsx        # 新建：模板列表页
web/src/pages/template-fill/upload-wizard.tsx# 新建：三步上传向导
web/src/pages/template-fill/detail.tsx       # 新建：详情（占位符编辑+预览）
web/src/routes.tsx                           # 修改：+路由枚举与注册
web/src/layouts/components/global-navbar.tsx # 修改：+顶部菜单项
web/src/locales/zh.ts                        # 修改：+header.templateFill + templateFill 块
```

---

### Task 1: ORM 三张表 + migrate_db 兜底

**Files:**
- Modify: `api/db/db_models.py`（表定义加在 `CollectionPersonnelExt` 之后约 :2095；migrate_db 兜底加在 :2617 附近的 create_table 区域）

- [ ] **Step 1: 添加三个模型类**

在 `CollectionPersonnelExt` 类定义之后追加（`create_time/create_date/update_time/update_date` 由 `BaseModel` 提供，勿重复定义；`created_by` 各表自定义）：

```python
class TplTemplate(DataBaseModel):
    """模板填写-模板主表。"""
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=256, index=True)
    description = LongTextField(null=True, default="")
    file_type = CharField(max_length=16, default="docx", help_text="docx | xlsx")
    status = CharField(max_length=16, default="draft", index=True, help_text="draft | published | disabled")
    latest_version = IntegerField(default=0)
    tenant_id = CharField(max_length=32, index=True)
    created_by = CharField(max_length=32, null=True, default="")

    class Meta:
        db_table = "tpl_template"


class TplTemplateVersion(DataBaseModel):
    """模板填写-模板版本表（占位符清单随版本原子演进）。"""
    id = CharField(max_length=32, primary_key=True)
    template_id = CharField(max_length=32, index=True)
    version = IntegerField(default=1)
    original_filename = CharField(max_length=256, default="")
    original_file_id = CharField(max_length=256, default="", help_text="MinIO object name, bucket=template_id")
    render_file_id = CharField(max_length=256, default="", help_text="带 {{占位符}} 的工作副本")
    placeholders = ListField(null=True)

    class Meta:
        db_table = "tpl_template_version"


class TplFillTask(DataBaseModel):
    """模板填写-填写任务表（P2 执行引擎使用，P1 仅建表）。"""
    id = CharField(max_length=32, primary_key=True)
    template_id = CharField(max_length=32, index=True)
    template_version_id = CharField(max_length=32, index=True)
    kb_ids = ListField(null=True)
    params = JSONField(null=True)
    status = CharField(max_length=24, default="pending", index=True)
    values = JSONField(null=True)
    evidence = JSONField(null=True)
    result_file_id = CharField(max_length=256, default="")
    error = LongTextField(null=True, default="")
    source = CharField(max_length=16, default="web", help_text="web | chat | flow")
    flow_instance_id = CharField(max_length=32, null=True, default="")
    tenant_id = CharField(max_length=32, index=True)
    created_by = CharField(max_length=32, null=True, default="")

    class Meta:
        db_table = "tpl_fill_task"
```

- [ ] **Step 2: migrate_db 末尾加建表兜底**

在 `migrate_db()` 末尾已有的 `Xxx.create_table(safe=True)` 区域（约 :2617）追加：

```python
    TplTemplate.create_table(safe=True)
    TplTemplateVersion.create_table(safe=True)
    TplFillTask.create_table(safe=True)
```

- [ ] **Step 3: 静态检查**

Run: `uv run ruff check api/db/db_models.py`
Expected: 无 error（`init_database_tables` 反射自动建新表，无需改它）

- [ ] **Step 4: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat(template-fill): 新增 tpl_template/tpl_template_version/tpl_fill_task 三表"
```

---

### Task 2: docx_utils — 段落遍历 / 候选提取 / 占位符替换（TDD）

**Files:**
- Create: `rag/svr/template_fill/__init__.py`（空文件）
- Create: `rag/svr/template_fill/docx_utils.py`
- Test: `test/test_template_fill_utils.py`

段落定位 `addr` 约定：正文段落 `para:<idx>`；表格内段落 `cell:<row>:<col>:<para_idx>`。`index` 为全文档扁平序号（含表格内段落），供 LLM 识别用。锚文本替换优先在单 run 内完成；跨 run 时整段重写进首 run（牺牲段内混合格式，P1 可接受，注释说明）。

- [ ] **Step 1: 写失败测试**

`test/test_template_fill_utils.py`（纯 python-docx 构造，不依赖 DB/ES）：

```python
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
    assert "{{sign_date}}" in texts[3]
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
    from rag.svr.template_fill.docx_utils import apply_docx_placeholders
    assert apply_docx_placeholders(sample_docx, []) == sample_docx
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k docx`
Expected: FAIL `ModuleNotFoundError: No module named 'rag.svr.template_fill'`

- [ ] **Step 3: 实现 docx_utils.py**

```python
"""docx 模板工具：段落遍历（含表格内段落）、填写点候选提取、锚文本→占位符替换。

addr 定位约定：正文段落 para:<idx>；表格内段落 cell:<row>:<col>:<para_idx>。
index 为全文档扁平序号，与 addr 一一对应（合并单元格会在多处重复出现同一 addr，
替换按"锚文本存在才替换"幂等，重复 addr 无副作用）。
"""
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# 常见填写点特征：下划线空位 / 中文括号空位 / 【】空位 / ×× 占位 / "：" 结尾（冒号后留白）
FILL_HINT_RE = re.compile(r"(_{2,}|（\s*）|\(\s*\)|【\s*】|×{2,}|XX{1,}|xx{1,}|填写|：\s*$|:\s*$)")


def iter_docx_paragraphs(file_bytes: bytes) -> list:
    """返回 [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。"""
    doc = Document(io.BytesIO(file_bytes))
    items, idx = [], 0
    for block in doc.element.body.iterchildren():
        if block.tag == qn("w:p"):
            items.append({"index": idx, "text": Paragraph(block, doc).text, "addr": f"para:{idx}"})
            idx += 1
        elif block.tag == qn("w:tbl"):
            tbl = Table(block, doc)
            for r, row in enumerate(tbl.rows):
                for c, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        items.append({"index": idx, "text": p.text, "addr": f"cell:{r}:{c}:{pi}"})
                        idx += 1
    return items


def extract_docx_candidates(file_bytes: bytes) -> list:
    """提取疑似含填写点的段落（供 LLM 识别，降低 token）。"""
    return [it for it in iter_docx_paragraphs(file_bytes) if it["text"].strip() and FILL_HINT_RE.search(it["text"])]


def _replace_in_paragraph(p: Paragraph, anchor: str, repl: str) -> bool:
    if anchor not in p.text:
        return False
    for run in p.runs:
        if anchor in run.text:
            run.text = run.text.replace(anchor, repl)
            return True
    # 锚文本跨 run：整段重写进首 run、清空其余（P1 取舍：牺牲段内混合格式）
    runs = p.runs
    if not runs:
        return False
    runs[0].text = p.text.replace(anchor, repl)
    for r in runs[1:]:
        r.text = ""
    return True


def apply_docx_placeholders(file_bytes: bytes, replacements: list) -> bytes:
    """replacements: [{"addr", "anchor", "key"}]，把 anchor 替换为 {{key}}。"""
    doc = Document(io.BytesIO(file_bytes))
    addr_map = {}
    idx = 0
    for block in doc.element.body.iterchildren():
        if block.tag == qn("w:p"):
            addr_map[f"para:{idx}"] = Paragraph(block, doc)
            idx += 1
        elif block.tag == qn("w:tbl"):
            tbl = Table(block, doc)
            for r, row in enumerate(tbl.rows):
                for c, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        addr_map[f"cell:{r}:{c}:{pi}"] = p
                        idx += 1
    for rep in replacements:
        p = addr_map.get(rep.get("addr"))
        if p is not None:
            _replace_in_paragraph(p, rep["anchor"], "{{%s}}" % rep["key"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -v -k docx`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/ test/test_template_fill_utils.py
git commit -m "feat(template-fill): docx 段落遍历与锚文本占位符替换工具"
```

---

### Task 3: xlsx_utils — 单元格遍历 / 占位符替换（TDD）

**Files:**
- Create: `rag/svr/template_fill/xlsx_utils.py`
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
import io as _io  # 文件顶部已 import io 则复用


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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k xlsx`
Expected: FAIL `No module named 'rag.svr.template_fill.xlsx_utils'`

- [ ] **Step 3: 实现 xlsx_utils.py**

```python
"""xlsx 模板工具：非空单元格遍历与占位符替换。addr 约定 "<sheet>!<coordinate>"（如 封面!B1）。"""
import io

from openpyxl import load_workbook


def iter_xlsx_cells(file_bytes: bytes) -> list:
    wb = load_workbook(io.BytesIO(file_bytes), data_only=False)
    items, idx = [], 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None or not str(cell.value).strip():
                    continue
                items.append({
                    "index": idx,
                    "text": str(cell.value),
                    "addr": f"{ws.title}!{cell.coordinate}",
                    "sheet": ws.title,
                    "coord": cell.coordinate,
                })
                idx += 1
    return items


def extract_xlsx_candidates(file_bytes: bytes) -> list:
    from rag.svr.template_fill.docx_utils import FILL_HINT_RE
    return [it for it in iter_xlsx_cells(file_bytes) if FILL_HINT_RE.search(it["text"])]


def apply_xlsx_placeholders(file_bytes: bytes, replacements: list) -> bytes:
    """replacements 元素含 sheet/coord/anchor/key；只改 cell.value，样式保留。"""
    wb = load_workbook(io.BytesIO(file_bytes))
    for rep in replacements:
        ws = wb[rep["sheet"]]
        cell = ws[rep["coord"]]
        val = cell.value
        if val is not None and rep["anchor"] in str(val):
            cell.value = str(val).replace(rep["anchor"], "{{%s}}" % rep["key"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/xlsx_utils.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): xlsx 单元格遍历与占位符替换工具"
```

---

### Task 4: detector — LLM 识别 prompt + 解析校验（TDD，LLM 仅接口不测）

**Files:**
- Create: `rag/svr/template_fill/detector.py`
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -v -k "detection or placeholders_"`
Expected: FAIL `No module named 'rag.svr.template_fill.detector'`

- [ ] **Step 3: 实现 detector.py**

```python
"""LLM 填写点识别：prompt 构造、响应解析与占位符清单校验（纯函数部分可独立单测）。"""
import json
import re

from rag.svr.template_fill.docx_utils import FILL_HINT_RE

FILL_MODES = ("llm", "param", "manual")
KEY_RE = re.compile(r"[^a-z0-9_]+")

DETECT_SYSTEM = """你是文档模板分析专家。用户给出固定模板中疑似需要填写的编号行（行号\\t文本）。
请识别其中所有"填写点"——模板留空、需要后续填写内容的位置。
输出 JSON 数组，每个元素：
{"line": 行号(int), "anchor": "该行原文中将被替换为占位符的精确子串", "key": "snake_case英文标识", "name": "中文字段名", "description": "给填写模型的说明", "retrieval_query": "适合去知识库检索的查询词", "fill_mode": "llm 或 manual", "required": true或false}
规则：
1. anchor 必须是该行原文的精确子串，禁止改写；一行可有多个填写点（拆成多个元素）。
2. 同一含义的填写点 key 全局唯一；日期类建议 key 如 sign_date。
3. 无法确定如何填写的位置用 fill_mode=manual。
4. 找不到任何填写点输出 []。只输出 JSON 数组，不要输出其它文字。"""


def normalize_key(key: str) -> str:
    k = KEY_RE.sub("_", str(key).strip().lower())
    return k.strip("_") or "field"


def parse_detection_response(raw: str, candidates: list) -> list:
    """解析 LLM 输出 → 校验后的建议清单。行号/锚文本不合法的项直接丢弃。"""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []
    cand_map = {c["index"]: c for c in candidates}
    out, used_keys = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        line, anchor = it.get("line"), str(it.get("anchor") or "")
        cand = cand_map.get(line)
        if cand is None or not anchor or anchor not in cand["text"]:
            continue
        key = normalize_key(it.get("key") or it.get("name") or "field")
        while key in used_keys:
            key = f"{key}_2"
        used_keys.add(key)
        mode = it.get("fill_mode") if it.get("fill_mode") in FILL_MODES else "llm"
        out.append({
            "key": key,
            "name": str(it.get("name") or key)[:100],
            "description": str(it.get("description") or ""),
            "retrieval_query": str(it.get("retrieval_query") or ""),
            "fill_mode": mode,
            "required": bool(it.get("required", True)),
            "addr": cand["addr"],
            "anchor": anchor,
            "line": line,
            "top_k": 6,
        })
    return out


def validate_placeholders(items: list, candidates: list) -> tuple:
    """人工确认后的占位符清单校验。返回 (ok, error_message)。"""
    cand_map = {c["addr"]: c for c in candidates}
    seen = set()
    for it in items:
        key = str(it.get("key") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            return False, f"非法 key: {key!r}（须为 snake_case，字母开头）"
        if key in seen:
            return False, f"key 重复: {key}"
        seen.add(key)
        if not str(it.get("name") or "").strip():
            return False, f"{key} 缺少中文名称"
        addr, anchor = it.get("addr"), str(it.get("anchor") or "")
        cand = cand_map.get(addr)
        if cand is None:
            return False, f"{key} 的定位 {addr!r} 不存在"
        if not anchor or anchor not in cand["text"]:
            return False, f"{key} 的 anchor 不在 {addr} 文本中"
        if it.get("fill_mode") not in FILL_MODES:
            return False, f"{key} 的 fill_mode 非法"
    return True, ""


async def detect_fill_points(tenant_id: str, file_type: str, candidates: list) -> list:
    """调用租户默认 chat 模型识别填写点（失败抛异常，由 API 层转错误响应）。"""
    from api.db import LLMType
    from api.db.services.llm_service import LLMBundle
    from api.db.services.tenant_llm_service import get_tenant_default_model_by_type

    if not candidates:
        return []
    model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT.value)
    chat_mdl = LLMBundle(tenant_id, model_config)
    numbered = "\n".join(f'{c["index"]}\t{c["text"]}' for c in candidates)
    user_msg = f"文件类型：{file_type}\n编号行：\n{numbered}"
    ans = await chat_mdl.async_chat(DETECT_SYSTEM, [{"role": "user", "content": user_msg}])
    return parse_detection_response(ans, candidates)
```

注意：若 `get_tenant_default_model_by_type` 的第二参在本仓要求 `LLMType.CHAT` 枚举而非 `.value`，以 `dialog_service.py:245-281` 的实际用法为准（该处直接传枚举成员）。实现时先读该段再定。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template-fill): LLM 填写点识别 prompt/解析/校验"
```

---

### Task 5: template_fill_service.py — Service 层

**Files:**
- Create: `api/db/services/template_fill_service.py`

- [ ] **Step 1: 实现 Service**

```python
"""模板填写：模板与版本 Service。存储 bucket=template_id，object name 带版本前缀。"""
import io
import logging

from api.db.db_models import DB, TplTemplate, TplTemplateVersion
from api.db.services.common_service import CommonService
from common import settings
from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


class TplTemplateService(CommonService):
    model = TplTemplate

    @classmethod
    @DB.connection_context()
    def get_list_page(cls, tenant_id: str, keyword: str = "", status: str = "", page: int = 1, size: int = 20):
        q = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if keyword:
            q = q.where(cls.model.name.contains(keyword))
        if status:
            q = q.where(cls.model.status == status)
        total = q.count()
        rows = q.order_by(cls.model.create_time.desc()).paginate(page, size)
        return [r.to_dict() for r in rows], total

    @classmethod
    @DB.connection_context()
    def get_owned(cls, template_id: str, tenant_id: str):
        """取租户内模板，不存在/越权返回 None。"""
        return cls.model.get_or_none(cls.model.id == template_id, cls.model.tenant_id == tenant_id)

    @classmethod
    @DB.connection_context()
    def set_status(cls, template_id: str, tenant_id: str, status: str):
        return cls.model.update(status=status).where(
            cls.model.id == template_id, cls.model.tenant_id == tenant_id).execute()


class TplTemplateVersionService(CommonService):
    model = TplTemplateVersion

    @classmethod
    @DB.connection_context()
    def latest(cls, template_id: str):
        return (cls.model.select()
                .where(cls.model.template_id == template_id)
                .order_by(cls.model.version.desc())
                .first())

    @classmethod
    def create_initial_version(cls, template_id: str, filename: str, blob: bytes):
        """上传后建 v1，原件入 MinIO。"""
        obj_name = f"v1_original_{filename}"
        settings.STORAGE_IMPL.put(template_id, obj_name, blob)
        return cls.insert(template_id=template_id, version=1,
                          original_filename=filename, original_file_id=obj_name, placeholders=[])

    @classmethod
    def save_placeholders(cls, template: dict, placeholders: list) -> dict:
        """人工确认后：生成带 {{key}} 的 render 副本入 MinIO，与 placeholders 原子落库。"""
        tpl_id = template["id"]
        ver = cls.latest(tpl_id)
        blob = settings.STORAGE_IMPL.get(tpl_id, ver.original_file_id)
        if template["file_type"] == "docx":
            from rag.svr.template_fill.docx_utils import apply_docx_placeholders
            render = apply_docx_placeholders(blob, placeholders)
            render_name = f"v{ver.version}_render.docx"
        else:
            from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders
            render = apply_xlsx_placeholders(blob, placeholders)
            render_name = f"v{ver.version}_render.xlsx"
        settings.STORAGE_IMPL.put(tpl_id, render_name, render)
        ver.render_file_id = render_name
        ver.placeholders = placeholders
        ver.save()
        return ver.to_dict()
```

说明：
- `STORAGE_IMPL.get(bucket, name)` 返回 `bytes`（`rag/utils/minio_conn.py`）。若实际返回别的类型，以 `document_api.py` 中 get 用法为准做 `bytes()` 归一。
- `insert()`（CommonService）自动生成 id + 时间戳，勿手工传。

- [ ] **Step 2: 静态检查**

Run: `uv run ruff check api/db/services/template_fill_service.py`
Expected: 无 error

- [ ] **Step 3: Commit**

```bash
git add api/db/services/template_fill_service.py
git commit -m "feat(template-fill): 模板与版本 Service（MinIO 存储 + render 副本生成）"
```

---

### Task 6: template_api.py — REST API

**Files:**
- Create: `api/apps/restful_apis/template_api.py`（放进目录即自动注册到 `/api/v1`，无需手动注册）
- Test: `test/test_template_api_routes.py`

- [ ] **Step 1: 实现 API**

```python
"""模板填写：模板管理 API（P1）。路由前缀 /api/v1/template/fill/*"""
import logging
import re

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.services.template_fill_service import TplTemplateService, TplTemplateVersionService
from api.utils.api_utils import get_error_data_result, get_result
from common import settings
from common.misc_utils import get_uuid
from rag.svr.template_fill.detector import detect_fill_points, validate_placeholders

logger = logging.getLogger(__name__)

manager = Blueprint("rest_template_fill_app", __name__)

MAX_TEMPLATE_SIZE = 20 * 1024 * 1024
PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def _file_type_of(filename: str):
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".xlsx"):
        return "xlsx"
    return None


def _extract_candidates(file_type: str, blob: bytes):
    if file_type == "docx":
        from rag.svr.template_fill.docx_utils import extract_docx_candidates
        return extract_docx_candidates(blob)
    from rag.svr.template_fill.xlsx_utils import extract_xlsx_candidates
    return extract_xlsx_candidates(blob)


async def _load_template(template_id: str):
    tpl = TplTemplateService.get_owned(template_id, current_user.id)
    if not tpl:
        return None, get_error_data_result("模板不存在")
    return tpl, None


@manager.route("/template/fill/upload", methods=["POST"])  # noqa: F821
@login_required
async def upload_template():
    files = await request.files
    form = await request.form
    file = files.get("file")
    if not file or not file.filename:
        return get_error_data_result("请上传 .docx 或 .xlsx 模板文件")
    file_type = _file_type_of(file.filename)
    if not file_type:
        return get_error_data_result("仅支持 .docx / .xlsx")
    blob = file.read()
    if not blob or len(blob) > MAX_TEMPLATE_SIZE:
        return get_error_data_result("文件为空或超过 20MB")
    name = (form.get("name") or file.filename.rsplit(".", 1)[0]).strip()[:256]
    tpl_id = get_uuid()
    TplTemplateService.insert(id=tpl_id, name=name,
                              description=(form.get("description") or "")[:2000],
                              file_type=file_type, status="draft", latest_version=1,
                              tenant_id=current_user.id, created_by=current_user.id)
    TplTemplateVersionService.create_initial_version(tpl_id, file.filename, blob)
    return get_result(data={"id": tpl_id, "name": name, "file_type": file_type, "status": "draft"})


@manager.route("/template/fill/list", methods=["GET"])  # noqa: F821
@login_required
async def list_templates():
    args = request.args
    rows, total = TplTemplateService.get_list_page(
        current_user.id, keyword=args.get("keyword", ""),
        status=args.get("status", ""), page=int(args.get("page", 1)), size=int(args.get("size", 20)))
    return get_result(data=rows, total=total)


@manager.route("/template/fill/detect", methods=["POST"])  # noqa: F821
@login_required
async def detect_placeholders():
    body = await request.get_json()
    tpl, err = await _load_template((body or {}).get("template_id", ""))
    if err:
        return err
    ver = TplTemplateVersionService.latest(tpl.id)
    blob = settings.STORAGE_IMPL.get(tpl.id, ver.original_file_id)
    candidates = _extract_candidates(tpl.file_type, blob)
    try:
        suggestions = await detect_fill_points(current_user.id, tpl.file_type, candidates)
    except Exception:
        logger.exception("detect fill points failed, template=%s", tpl.id)
        return get_error_data_result("AI 识别填写点失败，请重试或手动添加填写点")
    return get_result(data={"candidates": candidates, "suggestions": suggestions})


@manager.route("/template/fill/<template_id>/save-placeholders", methods=["POST"])  # noqa: F821
@login_required
async def save_placeholders(template_id: str):
    body = await request.get_json()
    items = (body or {}).get("placeholders")
    if not isinstance(items, list) or not items:
        return get_error_data_result("placeholders 不能为空")
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    blob = settings.STORAGE_IMPL.get(template_id, ver.original_file_id)
    candidates = _extract_candidates(tpl.file_type, blob)
    ok, err_msg = validate_placeholders(items, candidates)
    if not ok:
        return get_error_data_result(err_msg)
    TplTemplateVersionService.save_placeholders(tpl.to_dict(), items)
    return get_result(data={"id": template_id, "placeholder_count": len(items)})


@manager.route("/template/fill/<template_id>/publish", methods=["POST"])  # noqa: F821
@login_required
async def publish_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    if not ver or not ver.render_file_id:
        return get_error_data_result("请先保存填写点配置再发布")
    TplTemplateService.set_status(template_id, current_user.id, "published")
    return get_result()


@manager.route("/template/fill/<template_id>/disable", methods=["POST"])  # noqa: F821
@login_required
async def disable_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    TplTemplateService.set_status(template_id, current_user.id, "disabled")
    return get_result()


@manager.route("/template/fill/<template_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    data = tpl.to_dict()
    data["placeholders"] = ver.placeholders if ver else []
    data["render_ready"] = bool(ver and ver.render_file_id)
    return get_result(data=data)


@manager.route("/template/fill/<template_id>/preview", methods=["GET"])  # noqa: F821
@login_required
async def preview_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    obj_name = (ver.render_file_id or ver.original_file_id) if ver else ""
    if not obj_name:
        return get_error_data_result("模板文件缺失")
    blob = settings.STORAGE_IMPL.get(template_id, obj_name)
    if tpl.file_type == "docx":
        from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
        items = iter_docx_paragraphs(blob)
    else:
        from rag.svr.template_fill.xlsx_utils import iter_xlsx_cells
        items = iter_xlsx_cells(blob)
    for it in items:
        m = PLACEHOLDER_RE.search(it["text"])
        it["placeholder_key"] = m.group(1) if m else ""
    return get_result(data={"file_type": tpl.file_type, "items": items})


@manager.route("/template/fill/<template_id>/file", methods=["GET"])  # noqa: F821
@login_required
async def download_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    kind = request.args.get("kind", "original")
    ver = TplTemplateVersionService.latest(template_id)
    obj_name = (ver.render_file_id if kind == "render" else ver.original_file_id) if ver else ""
    if not obj_name:
        return get_error_data_result("文件不存在")
    blob = settings.STORAGE_IMPL.get(template_id, obj_name)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
        if tpl.file_type == "docx" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(blob, mimetype=mime,
                    headers={"Content-Disposition": f"attachment; filename=template_{template_id}.{tpl.file_type}"})
```

注意：`manager.route` 装饰器后面必须带 `# noqa: F821`（项目约定，`manager` 为运行时注入名）。

- [ ] **Step 2: 写 stub-load 冒烟测试**

`test/test_template_api_routes.py`（照搬 `test/test_flow_doc_table_edit.py` 的 stub 模式，避免拉起真实 DB/ES）：

```python
import importlib.util
import os
import sys
import types


def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _noop_decorator(*a, **kw):
    def deco(f):
        return f
    return deco


def _load_template_api():
    _make_stub_module("api.apps", current_user=None, login_required=_noop_decorator)
    _make_stub_module("api.utils", get_data_error_result=lambda *a, **k: None,
                      get_json_result=lambda *a, **k: None)
    _make_stub_module("api.utils.api_utils", get_error_data_result=lambda *a, **k: {"code": 1},
                      get_result=lambda *a, **k: {"code": 0})
    # quart 为真实依赖，无需 stub
    _make_stub_module("api.db.services.template_fill_service",
                      TplTemplateService=type("S", (), {}), TplTemplateVersionService=type("V", (), {}))
    _make_stub_module("common", settings=types.SimpleNamespace(STORAGE_IMPL=None))
    _make_stub_module("common.misc_utils", get_uuid=lambda: "x" * 32)
    _make_stub_module("rag.svr.template_fill.detector", detect_fill_points=None, validate_placeholders=None)
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                        "api", "apps", "restful_apis", "template_api.py"))
    spec = importlib.util.spec_from_file_location("template_api_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["template_api_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_template_api_module_loads_and_registers_routes():
    mod = _load_template_api()
    routes = {}
    for rule in mod.manager.deferred_functions:
        pass  # blueprint deferred; 直接断言关键 handler 存在
    for name in ("upload_template", "list_templates", "detect_placeholders", "save_placeholders",
                 "publish_template", "disable_template", "get_template", "preview_template",
                 "download_template"):
        assert hasattr(mod, name), f"缺少端点函数 {name}"
```

- [ ] **Step 3: 运行确认通过**

Run: `uv run pytest test/test_template_api_routes.py test/test_template_fill_utils.py -v`
Expected: 全部 passed

- [ ] **Step 4: Commit**

```bash
git add api/apps/restful_apis/template_api.py test/test_template_api_routes.py
git commit -m "feat(template-fill): 模板管理 REST API（上传/识别/保存/发布/预览/下载）"
```

---

### Task 7: 前端 — API 常量 + hooks

**Files:**
- Modify: `web/src/utils/api.ts`（在 364 行 `listAnalysisTemplates` 区域旁追加）
- Create: `web/src/hooks/use-template-fill-request.ts`

- [ ] **Step 1: api.ts 追加 URL 常量**

```ts
  // 模板填写
  uploadTemplateFill: `${restAPIv1}/template/fill/upload`,
  listTemplateFill: `${restAPIv1}/template/fill/list`,
  detectTemplateFill: `${restAPIv1}/template/fill/detect`,
  getTemplateFill: (id: string) => `${restAPIv1}/template/fill/${id}`,
  saveTemplateFillPlaceholders: (id: string) => `${restAPIv1}/template/fill/${id}/save-placeholders`,
  publishTemplateFill: (id: string) => `${restAPIv1}/template/fill/${id}/publish`,
  disableTemplateFill: (id: string) => `${restAPIv1}/template/fill/${id}/disable`,
  previewTemplateFill: (id: string) => `${restAPIv1}/template/fill/${id}/preview`,
  downloadTemplateFill: (id: string, kind: 'original' | 'render') =>
    `${restAPIv1}/template/fill/${id}/file?kind=${kind}`,
```

- [ ] **Step 2: 实现 hooks 文件**

```ts
import { useQuery, useQueryClient, useMutation, useQuery } from '@tanstack/react-query';
import api from '@/utils/api';
import request from '@/utils/request';

export interface TplPlaceholder {
  key: string;
  name: string;
  description: string;
  retrieval_query: string;
  fill_mode: 'llm' | 'param' | 'manual';
  required: boolean;
  addr: string;
  anchor: string;
  top_k: number;
}

export interface TplTemplateItem {
  id: string;
  name: string;
  description: string;
  file_type: 'docx' | 'xlsx';
  status: 'draft' | 'published' | 'disabled';
  latest_version: number;
  create_time: number;
}

export interface TplCandidate {
  index: number;
  text: string;
  addr: string;
}

export function useListTemplateFill(params: { keyword?: string; status?: string; page: number; size: number }) {
  return useQuery({
    queryKey: ['templateFillList', params],
    queryFn: async () => {
      const searchParams = new URLSearchParams({ page: String(params.page), size: String(params.size) });
      if (params.keyword) searchParams.set('keyword', params.keyword);
      if (params.status) searchParams.set('status', params.status);
      const { data } = await request.get(`${api.listTemplateFill}?${searchParams.toString()}`);
      return data as { code: number; data: TplTemplateItem[]; total?: number };
    },
  });
}

export function useTemplateFillDetail(id: string) {
  return useQuery({
    queryKey: ['templateFillDetail', id],
    queryFn: async () => {
      const { data } = await request.get(api.getTemplateFill(id));
      return data as { code: number; data: TplTemplateItem & { placeholders: TplPlaceholder[]; render_ready: boolean } };
    },
    enabled: !!id,
  });
}

export function useTemplateFillPreview(id: string) {
  return useQuery({
    queryKey: ['templateFillPreview', id],
    queryFn: async () => {
      const { data } = await request.get(api.previewTemplateFill(id));
      return data as { code: number; data: { file_type: string; items: { index: number; text: string; addr: string; placeholder_key: string }[] } };
    },
    enabled: !!id,
  });
}

function useInvalidateTemplateFill() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ['templateFillList'] });
    queryClient.invalidateQueries({ queryKey: ['templateFillDetail'] });
  };
}

export function useUploadTemplateFill() {
  const invalidate = useInvalidateTemplateFill();
  return useMutation({
    mutationFn: async (payload: { file: File; name: string; description: string }) => {
      const formData = new FormData();
      formData.append('file', payload.file);
      formData.append('name', payload.name);
      formData.append('description', payload.description);
      const { data } = await request.post(api.uploadTemplateFill, { data: formData });
      return data as { code: number; data: { id: string } };
    },
    onSuccess: invalidate,
  });
}

export function useDetectTemplateFill() {
  return useMutation({
    mutationFn: async (templateId: string) => {
      const { data } = await request.post(api.detectTemplateFill, { data: { template_id: templateId } });
      return data as { code: number; data: { candidates: TplCandidate[]; suggestions: TplPlaceholder[] } };
    },
  });
}

export function useSaveTemplateFillPlaceholders() {
  const invalidate = useInvalidateTemplateFill();
  return useMutation({
    mutationFn: async ({ id, placeholders }: { id: string; placeholders: TplPlaceholder[] }) => {
      const { data } = await request.post(api.saveTemplateFillPlaceholders(id), { data: { placeholders } });
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}

export function usePublishTemplateFill() {
  const invalidate = useInvalidateTemplateFill();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await request.post(api.publishTemplateFill(id));
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}

export function useDisableTemplateFill() {
  const invalidate = useInvalidateTemplateFill();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await request.post(api.disableTemplateFill(id));
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}
```

（`request.post(url)` 无 body 的用法若 axios 封装不接受，改为 `request.post(url, { data: {} })`，与仓内既有写法对齐。）

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 涉及文件 0 error

- [ ] **Step 4: Commit**

```bash
git add web/src/utils/api.ts web/src/hooks/use-template-fill-request.ts
git commit -m "feat(template-fill): 前端 API 常量与 React Query hooks"
```

---

### Task 8: 前端 — 列表页

**Files:**
- Create: `web/src/pages/template-fill/index.tsx`

- [ ] **Step 1: 实现列表页**（默认导出 `TemplateFillPage`，风格对齐 `pages/analysis-templates/index.tsx`：Card + shadcn Table + 输入搜索 + 状态筛选 Select + 「上传模板」按钮打开 `UploadWizard`；行操作：详情/发布（draft 且 render_ready）/禁用（published）。状态列中文映射：draft=草稿、published=已发布、disabled=已停用。详情跳转 `Routes.TemplateFillDetail` + id。）

页面骨架（完整实现按骨架补齐 handlers）：

```tsx
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useDisableTemplateFill, useListTemplateFill, usePublishTemplateFill, type TplTemplateItem } from '@/hooks/use-template-fill-request';
import { Routes } from '@/routes';
import { UploadWizard } from './upload-wizard';

const STATUS_LABEL: Record<string, string> = { draft: '草稿', published: '已发布', disabled: '已停用' };

export default function TemplateFillPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [wizardOpen, setWizardOpen] = useState(false);
  const { data, isLoading } = useListTemplateFill({ keyword, status, page, size: 20 });
  const publishMut = usePublishTemplateFill();
  const disableMut = useDisableTemplateFill();
  const items = data?.data ?? [];
  // 空态、loading、行渲染、分页按钮（上一页/下一页）按骨架实现
  return (
    <Card className="bg-transparent border-none">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-2xl">模板库</CardTitle>
          <Button onClick={() => setWizardOpen(true)}>上传模板</Button>
        </div>
        <div className="flex gap-2">
          <Input placeholder="搜索模板名称" value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1); }} className="w-64" />
          <Select value={status} onValueChange={(v) => { setStatus(v === 'all' ? '' : v); setPage(1); }}>
            <SelectTrigger className="w-32"><SelectValue placeholder="全部状态" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="draft">草稿</SelectItem>
              <SelectItem value="published">已发布</SelectItem>
              <SelectItem value="disabled">已停用</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>模板名称</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((it) => (
              <TableRow key={it.id}>
                <TableCell className="cursor-pointer" onClick={() => navigate(`${Routes.TemplateFillDetail}/${it.id}`)}>{it.name}</TableCell>
                <TableCell>{it.file_type === 'docx' ? 'Word' : 'Excel'}</TableCell>
                <TableCell>{STATUS_LABEL[it.status]}</TableCell>
                <TableCell>v{it.latest_version}</TableCell>
                <TableCell className="space-x-2">
                  {(it.status === 'draft' || it.status === 'disabled') && (
                    <Button size="sm" variant="outline" disabled={publishMut.isPending}
                      onClick={() => publishMut.mutate(it.id)}>发布</Button>
                  )}
                  {it.status === 'published' && (
                    <Button size="sm" variant="outline" disabled={disableMut.isPending}
                      onClick={() => disableMut.mutate(it.id)}>停用</Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex justify-end gap-2 mt-4">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
          <Button size="sm" variant="outline" disabled={items.length < 20} onClick={() => setPage(page + 1)}>下一页</Button>
        </div>
      </CardContent>
      <UploadWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </Card>
  );
}
```

- [ ] **Step 2: tsc 检查**

Run: `cd web && npx tsc --noEmit`
Expected: 本文件 0 error（zh.ts keys 与 Routes 在 Task 11 之前，此文件可能报 Routes/文案缺失——若报，先完成 Task 11 的枚举与 zh.ts 再复查）

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/template-fill/index.tsx
git commit -m "feat(template-fill): B端模板库列表页"
```

---

### Task 9: 前端 — 三步上传向导

**Files:**
- Create: `web/src/pages/template-fill/upload-wizard.tsx`

- [ ] **Step 1: 实现向导**（Dialog 内三步：① 选文件+名称+说明（`Input type="file"` accept=".docx,.xlsx"）→ `useUploadTemplateFill` ② 调 `useDetectTemplateFill` 显示识别进度与建议数量，把 suggestions 装入可编辑表格（每行：key/中文名/检索词/fill_mode 下拉/必填 checkbox/锚文本只读；支持删除行和「添加填写点」（从 candidates 下拉选行后填 anchor））③ 「保存配置」调 `useSaveTemplateFillPlaceholders`，成功后回调 `onSaved(id)`（父组件跳详情））

组件签名与状态机：

```tsx
interface UploadWizardProps { open: boolean; onOpenChange: (open: boolean) => void; onSaved?: (id: string) => void; }
// 内部状态：step: 1|2|3；file/name/description；templateId；candidates; placeholders: TplPlaceholder[]（初值=suggestions，可编辑）
// Step1 校验：file 必选且后缀 .docx/.xlsx；name 非空
// Step2：mount 时调 detect mutation（isPending 显示「AI 识别中…」）；suggestions 为空时提示「未识别到填写点，请在下方手动添加」
// Step3 保存前在**(待实现于 detail 页编辑的最终校验依赖后端 validate_placeholders)**——前端只做行级非空校验（key/name/anchor），其余交给后端错误信息展示
```

表格行内编辑用受控 `Input` + `Select`（llm=AI填写 / manual=人工），删除行按钮移除该项，「保存配置」按钮触发 save mutation。

- [ ] **Step 2: tsc 检查**

Run: `cd web && npx tsc --noEmit`
Expected: 0 error

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/template-fill/upload-wizard.tsx
git commit -m "feat(template-fill): 三步上传向导（上传→AI识别→确认保存）"
```

---

### Task 10: 前端 — 详情页（占位符编辑 + 预览）

**Files:**
- Create: `web/src/pages/template-fill/detail.tsx`

- [ ] **Step 1: 实现详情页**

默认导出 `TemplateFillDetailPage`，从 `useParams` 取 id，`useTemplateFillDetail` + `useTemplateFillPreview` 加载数据。布局两栏：

- 左侧：预览列表——遍历 `preview.items`，有 `placeholder_key` 的行用黄色底高亮并显示 `{{key}}` 徽标（`<span className="bg-yellow-100 px-1 rounded">{{key}}</span>`）；xlsx 时按 `sheet!coord` 前缀分组显示
- 右侧：占位符配置表（同向导 Step3 的编辑表格，初值取 `detail.placeholders`，可增删改）+「保存配置」「下载原件 / 下载工作副本」按钮（`window.open(api.downloadTemplateFill(id, kind))`）

保存成功后 invalidate（hooks 已带）并刷新预览（`queryClient.invalidateQueries(['templateFillPreview', id])`）。

- [ ] **Step 2: tsc 检查**

Run: `cd web && npx tsc --noEmit`
Expected: 0 error

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/template-fill/detail.tsx
git commit -m "feat(template-fill): 模板详情页（占位符编辑+高亮预览+下载）"
```

---

### Task 11: 前端 — 路由 / 菜单 / 中文文案

**Files:**
- Modify: `web/src/routes.tsx`
- Modify: `web/src/layouts/components/global-navbar.tsx`
- Modify: `web/src/locales/zh.ts`

- [ ] **Step 1: routes.tsx 加枚举与注册**

枚举区（:78 附近）：

```ts
  TemplateFill = '/template-fill',
  TemplateFillDetail = '/template-fill-detail',
```

注册：读 `routes.tsx` 中 `Routes.Permission` 的注册条目，紧邻它按**完全相同的 layout 包裹方式**复制两条：

```tsx
  {
    path: Routes.TemplateFill,
    Component: () => import('@/pages/template-fill'),
  },
  {
    path: `${Routes.TemplateFillDetail}/:id`,
    Component: () => import('@/pages/template-fill/detail'),
  },
```

- [ ] **Step 2: 顶部菜单项**

`global-navbar.tsx` 的 `menuItems` 数组（:44 附近，参照 `header.userManagement` 条目；P1 不挂 permission 字段，登录即可见）：

```ts
  {
    path: Routes.TemplateFill,
    name: 'header.templateFill',
  },
```

- [ ] **Step 3: zh.ts 文案**

`header` 块加：`templateFill: '模板库',`；顶层新增（对齐 `analysisTemplate` 块的组织方式）：

```ts
  templateFill: {
    title: '模板库',
    upload: '上传模板',
    searchPlaceholder: '搜索模板名称',
    name: '模板名称',
    type: '类型',
    status: '状态',
    version: '版本',
    actions: '操作',
    publish: '发布',
    disable: '停用',
    statusDraft: '草稿',
    statusPublished: '已发布',
    statusDisabled: '已停用',
    wizardUpload: '上传文件',
    wizardDetect: 'AI 识别填写点',
    wizardConfirm: '确认填写点',
    fileLabel: '模板文件（.docx / .xlsx）',
    descriptionLabel: '模板说明',
    detectRunning: 'AI 识别中…',
    detectEmpty: '未识别到填写点，请在下方手动添加',
    addPlaceholder: '添加填写点',
    phKey: '标识（key）',
    phName: '中文名称',
    phQuery: '知识库检索词',
    phMode: '填写方式',
    modeLlm: 'AI 填写',
    modeManual: '人工填写',
    phRequired: '必填',
    phAnchor: '锚文本',
    savePlaceholders: '保存配置',
    preview: '模板预览',
    placeholderCount: '填写点数量',
    downloadOriginal: '下载原件',
    downloadRender: '下载工作副本',
  },
```

（页面组件中的字面量统一替换为 `useGetTranslation({ keyPrefix: 'templateFill' })` 取 key，对齐 `analysis-templates` 页的 i18n 用法。）

- [ ] **Step 4: tsc 检查**

Run: `cd web && npx tsc --noEmit`
Expected: 0 error

- [ ] **Step 5: Commit**

```bash
git add web/src/routes.tsx web/src/layouts/components/global-navbar.tsx web/src/locales/zh.ts web/src/pages/template-fill/
git commit -m "feat(template-fill): 路由/顶部菜单/中文文案收口"
```

---

### Task 12: 全量回归 + 本地冒烟清单

- [ ] **Step 1: 后端回归**

```bash
uv run ruff check api/db/db_models.py api/db/services/template_fill_service.py api/apps/restful_apis/template_api.py rag/svr/template_fill/
uv run pytest test/test_template_fill_utils.py test/test_template_api_routes.py -v
```
Expected: ruff 0 error；pytest 全 passed

- [ ] **Step 2: 前端回归**

```bash
cd web && npx tsc --noEmit
```
Expected: 0 error（**不跑 npm run build——按项目约定构建只在部署时做**）

- [ ] **Step 3: 已有测试无回归**

```bash
uv run pytest test/ -k "template or flow_doc" -v
```
Expected: 无失败（analysis_template / flow_doc 相关存量测试不受影响）

- [ ] **Step 4: 人工冒烟清单（部署后，写入交付说明，不在本地执行）**

1. `docker exec docker-ragflow-cpu-1 python -c "from api.db.db_models import TplTemplate, TplTemplateVersion, TplFillTask; print(TplTemplate.table_exists(), TplTemplateVersion.table_exists(), TplFillTask.table_exists())"` → 三True
2. B 端顶部出现「模板库」菜单 → 上传一个含 `____` 与表格空格的 docx → AI 识别出建议 → 确认保存 → 预览看到黄色高亮 `{{key}}`
3. 下载工作副本，用 Word 打开确认 `{{key}}` 替换正确、样式未破坏
4. xlsx 模板走同样流程（合并单元格处确认只写锚点格）

- [ ] **Step 5: 最终 Commit（如有遗漏文件）+ 交付说明**

不部署、不 push。输出交付说明：改动文件清单 + 冒烟清单 + 部署时需成套 SCP 的文件（db_models.py、template_fill_service.py、template_api.py、rag/svr/template_fill/ 全目录、web/dist）。

---

## Self-Review 结论

- **Spec 覆盖**：设计文档 P1 范围 = 3 表（Task 1）+ 模板管理 API（Task 6）+ B 端页面（Task 8/9/10）+ 路由/菜单/文案（Task 11）+ 上传向导含 LLM 识别（Task 4/9）。测试填写/任务列表/执行引擎属 P2，不在本计划 ✓
- **占位符扫描**：Task 9/10 的 UI 代码以「骨架 + 明确实现指令」给出（表格行编辑等机械代码），所有数据结构、mutation、状态机、校验规则均已定义，无 TBD ✓
- **类型一致性**：`addr` 格式（`para:i` / `cell:r:c:p` / `sheet!coord`）、`TplPlaceholder` 字段、`placeholders` JSON 结构在 Task 2-10 间已交叉核对一致；`detect` 端点用 POST body（前端 hooks 与后端一致）✓
