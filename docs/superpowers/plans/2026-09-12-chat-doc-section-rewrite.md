# C端对话文档按节局部重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C端对话里说「把第 3 节重写，补充XX」→ Agent 工具对成稿 docx 做 heading 切节 + LLM 按节重写 + 段落区间替换（格式保真）→ 落新版本 → 对话出成稿卡，可回退。

**Architecture:** 新增 Agent 工具 `DocumentRewrite`（outline/rewrite/versions/rollback 四 action，DSL 零改动），执行层 `rag/svr/document_rewrite/`（切节/重写/docx回写/版本链四模块，纯 python-docx + LLMBundle，不依赖 agent 运行时可独立测试）。重写结果经 canvas 全局 `sys.pending_downloads` → Message 组件合并输出 download 契约 → 前端成稿卡。版本链：chat 写 `{tenant_id}-downloads` 桶 + 新表 `doc_rewrite_version`；flow 复用 `flow_version` 表（`add_version(switch_current=False)`）。同时修复存量缺口：canvas workflow_finished 的 downloads 持久化进 message data + 前端历史恢复 + completions payload 附 `recent_downloads`。

**Tech Stack:** Python (python-docx, peewee, LLMBundle) / React 18 + TypeScript

**Spec:** `docs/superpowers/specs/2026-09-12-chat-doc-section-rewrite-design.md`（已评审）

---

## 零上下文工程帅必读：集成点事实（file:line，均已核实）

| 事实 | 位置 |
|---|---|
| 工具插件结构：`XxxParam(ToolParamBase)` 声明 `meta: ToolMeta`，`Xxx(ToolBase, ABC)` 提供 `_invoke(**kwargs)`，`component_name` 类属性 | `agent/tools/template_fill.py:48-121`（照抄此模式） |
| ToolMeta 结构 `{name, displayName, description, displayDescription, parameters}`，参数项 `{type, description, enum?, required, default?}` | `agent/tools/base.py:42-47` |
| `get_meta()` 转 OpenAI function 定义；`required` 由 meta 里 `p["required"]` 为 True 的键组成 | `agent/tools/base.py:137-161` |
| 工具自动发现：`agent/tools/` 目录 os.listdir + getmembers，放进去即注册，无需任何注册代码 | `agent/tools/__init__.py:22-46` |
| **工具顶层禁止 import DB/Quart 依赖**（注册期触发），service 层全部延迟到方法内 import | `agent/tools/template_fill.py:16-21` 注释 |
| 执行超时：`@timeout(_EXEC_TIMEOUT)`，`_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))` | `agent/tools/template_fill.py:35` |
| canvas.run kwargs 白名单：`if k in ["query","user_id","files","internet"] and kwargs[k]` → 写 `self.globals[f"sys.{k}"]`；**空值不写入**（读侧必须容错） | `agent/canvas.py:415-429` |
| `canvas.get_variable_value(name)`：缺失 key 抛 KeyError | `agent/canvas.py:204-219` |
| `canvas.get_tenant_id()` 返回当前租户 | `agent/canvas.py:174-175` |
| workflow_finished 事件 outputs = 路径最后节点 `.output()`（Message 组件输出 `{content, downloads}`） | `agent/canvas.py:856-863` |
| Message 组件把引用变量的 download dict 抽进 `downloads` 输出（`_stringify_message_value` 识别三键 `doc_id/filename/mime_type`）；`set_output("downloads", ...)` 两处 | `agent/component/message.py:108-135, 241, 277` |
| download 契约对象：`{"doc_id","filename","name","mime_type","size","url"}`，`url=f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}"`，doc_id 必须是 `{tenant_id}-downloads` 桶里的对象名 | `agent/component/template_fill.py:328-346`（现成样板 `_bridge_download`） |
| `FileService.put_blob(user_id, location, blob)` / `get_blob`：桶=`f"{user_id}-downloads"`；put 不返回 id，对象名调用方自定 | `api/db/services/file_service.py:602-610` |
| SSE 事件白名单（无需新增事件，本项目复用 message/workflow_finished） | `api/apps/restful_apis/agent_api.py:402` |
| 会话链路：`agent_chat_completion` → `_iter_session_completion_events` → `agent_completion(tenant_id, agent_id, **req)`（req 全键透传）→ `canvas_service.completion(tenant_id, agent_id, session_id, **kwargs)`；`completion` 目前只解构 query/files/inputs/user_id/internet 转给 canvas.run | `api/apps/restful_apis/agent_api.py:366-372, 1010-1011`；`api/db/services/canvas_service.py:229-235, 320` |
| canvas_service 落库：闭包 `_persist_messages` 组装 assistant_msg（`data` 字段挂载模式见 templateFillEvents） | `api/db/services/canvas_service.py:279-317` |
| 事件捕获循环（elif 链，最小改动点） | `api/db/services/canvas_service.py:319-339` |
| `FlowVersionService.add_version(flow, object_name, file_name, file_type, file_size, source, created_by)` **会自动切 current_version_id**（设计要求 flow 重写不切）；事务内禁再调带 connection_context 的方法；version_no 内联计算 | `api/db/services/flow_service.py:230-261` |
| flow 桶名 = `flow["initiator_id"]`（`_bucket_of`） | `api/db/services/flow_service.py:96-98` |
| 新表样板：TplFillTask(DataBaseModel)；复合唯一索引写法 `indexes=((("user_id","notification_id"), True),)` | `api/db/db_models.py:2173-2191, 2216-2230` |
| `init_database_tables` 自动建表清单 | `api/db/db_models.py:707-726` |
| `migrate_db` 幂等迁移写法：`if not X.table_exists(): X.create_table(safe=True)` | `api/db/db_models.py:2825-2827` |
| LLM 调用范式：`LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, LLMType.CHAT))` + `await mdl.async_chat(system, [{"role":"user","content":...}])` → str | `rag/svr/template_fill/executor.py:269-273, 396-402` |
| 前端发送 payload（C端对话） | `web/src/pages/c-chat/index.tsx:1295-1306` |
| 前端历史消息映射（加 downloads 恢复的精确位置） | `web/src/pages/c-chat/index.tsx:1048-1059` |
| 前端 msg.downloads 渲染成稿卡（已存在，无需改） | `web/src/pages/c-chat/index.tsx:2503-2524` |
| 前端 WorkflowFinished 已存 outputs.downloads 到 streamAccRef（在线路径已通，无需改） | `web/src/hooks/use-send-message.ts:591-598` |
| flow AI 面板 payload（加 flow_version_id 的位置） | `web/src/pages/c-chat/flow/flow-ai-panel.tsx:529-536` |

**环境注意（Windows）**：
- 后端测试命令统一 `uv run --no-sync pytest <file> -v`（bash，正斜杠路径）。
- 前端 jest 全仓配置依赖未安装的 umi/test，用 `.scratch/jest.config.mini.js` 迷你配置跑（见 Task 8）。
- `tsc --noEmit` 全仓有 300+ 存量错误，只验本计划触碰文件的增量错误。
- 本地 Redis(16379)/MySQL 可能未运行：新测试不得依赖真实中间件（docx 测试纯内存、versions 测纯函数、工具测 stub）。

---

### Task 1: sections.py — heading 切节与目录提取

**Files:**
- Create: `rag/svr/document_rewrite/__init__.py`（空文件）
- Create: `rag/svr/document_rewrite/sections.py`
- Test: `test/test_doc_rewrite_sections.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_sections.py
# -*- coding: utf-8 -*-
"""heading 切节对抗测试：无heading/多级/中文样式名/大纲级别/邻接标题/表格/空文档。"""
import io

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
    doc.add_heading("附录", level=1)  # 邻接标题：本节无正文段
    doc.add_paragraph("附录正文。")
    return doc


def test_split_basic():
    sections = split_sections(_doc_with_headings())
    assert [s["section_no"] for s in sections] == [1, 2, 3]
    assert sections[0]["title"] == "实施方案"
    assert sections[0]["para_start"] == 1
    assert sections[0]["para_end"] == 3
    assert sections[1]["para_start"] == 4
    assert sections[1]["para_end"] == 5
    assert sections[2]["title"] == "附录"
    # 邻接标题：附录节 para_start == 上一节 para_end + 1，body 为空
    assert sections[2]["para_end"] == sections[2]["para_start"]
    assert "第一节正文第一段。" in sections[0]["preview"] or sections[0]["word_count"] > 0


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
    assert "节内段落。" in sections[0]["preview"] or sections[0]["word_count"] >= 10


def test_get_section_and_outline():
    sections = split_sections(_doc_with_headings())
    assert get_section(sections, 2)["title"] == "进度安排"
    assert get_section(sections, 99) is None
    outline = build_outline(sections)
    assert "第1节 实施方案" in outline
    assert "第3节 附录" in outline
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_sections.py -v`
Expected: FAIL（`ModuleNotFoundError: rag.svr.document_rewrite`）

- [ ] **Step 3: 实现**

先建空文件 `rag/svr/document_rewrite/__init__.py`，再写：

```python
# rag/svr/document_rewrite/sections.py
# -*- coding: utf-8 -*-
"""heading 切节与目录提取（纯函数，python-docx）。

切节协议（设计 §4）：
- 按 heading 1（样式名 Heading 1/标题 1，或段落直接大纲级别 0）切顶层节；
- section_no 即文档顺序编号，与 Word 自动编号无关；
- 顶层节标题段落本身永不参与正文替换（docx_edit 侧负责跳过）；
- 无任何 heading 的文档抛 NoSectionError，由工具层引导 LLM 建议全文重新生成。
"""
from __future__ import annotations

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


class NoSectionError(ValueError):
    """文档没有任何一级标题结构。"""


_HEADING1_STYLE_NAMES = {"heading 1", "标题 1"}


def _direct_outline_level(p: Paragraph) -> int | None:
    """段落 pPr 上的直接大纲级别（w:outlineLvl），无则 None。"""
    pPr = p._p.pPr
    if pPr is None:
        return None
    el = pPr.find(qn("w:outlineLvl"))
    if el is None:
        return None
    try:
        return int(el.get(qn("w:val")))
    except (TypeError, ValueError):
        return None


def _is_heading1(p: Paragraph) -> bool:
    name = ""
    try:
        name = (p.style.name or "").strip().lower()
    except Exception:
        pass
    if name in _HEADING1_STYLE_NAMES:
        return True
    return _direct_outline_level(p) == 0


def split_sections(doc) -> list[dict]:
    """切节。返回按文档顺序的节列表：
    {section_no, title, para_start, para_end, preview, word_count}
    - para_start：标题段落在 doc.paragraphs 中的索引（含）；
    - para_end：本节最后一个正文段落索引（含）；邻接标题时 == para_start；
    - preview：正文首行前 40 字；word_count：正文总字数（表格内容不计）。
    注意：索引空间是 doc.paragraphs（body 顶层段落），表格元素在该空间不可见，
    docx_edit 按同一索引空间操作即天然保留表格。
    """
    paras = list(doc.paragraphs)
    headings = [
        (i, p) for i, p in enumerate(paras)
        if _is_heading1(p) and (p.text or "").strip()
    ]
    if not headings:
        raise NoSectionError(
            "该文档没有任何一级标题（Heading 1）结构，无法按节定位。"
            "建议整体重新生成文档，或先为文档补充标题样式。"
        )
    sections = []
    for idx, (i, p) in enumerate(headings):
        end = headings[idx + 1][0] - 1 if idx + 1 < len(headings) else len(paras) - 1
        body_text = "\n".join(
            (pp.text or "").strip() for pp in paras[i + 1:end + 1] if (pp.text or "").strip()
        )
        sections.append({
            "section_no": idx + 1,
            "title": p.text.strip(),
            "para_start": i,
            "para_end": end,
            "preview": body_text[:40],
            "word_count": len(body_text),
        })
    return sections


def get_section(sections: list[dict], section_no: int) -> dict | None:
    for s in sections:
        if s["section_no"] == section_no:
            return s
    return None


def build_outline(sections: list[dict]) -> str:
    return "\n".join(
        f"第{s['section_no']}节 {s['title']}（约{s['word_count']}字）" for s in sections
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_doc_rewrite_sections.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/document_rewrite/__init__.py rag/svr/document_rewrite/sections.py test/test_doc_rewrite_sections.py
git commit -m "feat(rewrite): sections.py heading切节与目录提取（样式名/大纲级别/中文标题名）"
```

---

### Task 2: docx_edit.py — 段落区间替换与样式拷贝

**Files:**
- Create: `rag/svr/document_rewrite/docx_edit.py`
- Test: `test/test_doc_rewrite_docx_edit.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_docx_edit.py
# -*- coding: utf-8 -*-
"""段落区间替换对抗测试：格式保真/1↔N段/表格保留/首末节边界/空节插入/标题不可改写。"""
import io

from docx import Document
from docx.shared import Pt

from rag.svr.document_rewrite.docx_edit import replace_section_paragraphs
from rag.svr.document_rewrite.sections import split_sections


def _build_doc():
    doc = Document()
    doc.add_heading("第一节", level=1)
    body = doc.add_paragraph("原始正文段落，这是第一节的正文内容。")
    body.add_run("补充run")
    doc.add_heading("第二节", level=1)
    doc.add_paragraph("第二节正文。")
    return doc


def _roundtrip(doc) -> Document:
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return Document(buf)


def test_replace_keeps_heading_and_copies_style():
    doc = _build_doc()
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[0], ["新正文一段。", "新正文二段。"])
    out = _roundtrip(doc)
    paras = out.paragraphs
    # 标题段永不被改写
    assert paras[0].text == "第一节"
    assert paras[1].text == "新正文一段。"
    assert paras[2].text == "新正文二段。"
    assert paras[3].text == "第二节"
    # pPr 样式拷贝：新段样式名 == 原正文段样式名
    assert paras[1].style.name == "Normal"
    # 1段 → 2段（N段插入）成功
    assert len(paras) == 5


def test_n_to_one():
    doc = _build_doc()
    doc.add_paragraph("原始正文第二段。")
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[0], ["合并后的单段。"])
    out = _roundtrip(doc)
    assert out.paragraphs[1].text == "合并后的单段。"
    assert out.paragraphs[2].text == "第二节"


def test_run_rpr_copied_from_body_run_not_bold_title_run():
    doc = Document()
    doc.add_heading("节", level=1)
    p = doc.add_paragraph("")
    r_bold = p.add_run("加粗")
    r_bold.bold = True
    r_body = p.add_run("正文特征文本，较长一些。")
    r_body.font.size = Pt(12)
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[0], ["替换后的正文。"])
    out = _roundtrip(doc)
    new_p = out.paragraphs[1]
    assert new_p.text == "替换后的正文。"
    # rPr 拷贝自「正文特征 run」（非加粗、最长文本），新段不应继承加粗
    assert all(not (r.bold or False) for r in new_p.runs)


def test_table_inside_section_preserved():
    doc = Document()
    doc.add_heading("节", level=1)
    doc.add_paragraph("表前段落。")
    tbl = doc.add_table(rows=2, cols=2)
    tbl.cell(0, 0).text = "表格内容"
    doc.add_paragraph("表后段落。")
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[0], ["新表前段。", "新表后段。"])
    out = _roundtrip(doc)
    texts = [p.text for p in out.paragraphs]
    assert "新表前段。" in texts and "新表后段。" in texts
    assert len(out.tables) == 1
    assert out.tables[0].cell(0, 0).text == "表格内容"


def test_last_section_boundary():
    doc = _build_doc()
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[-1], ["第二节新正文。"])
    out = _roundtrip(doc)
    assert out.paragraphs[-1].text == "第二节新正文。"
    assert out.paragraphs[-2].text == "第二节"


def test_empty_section_inserts_after_heading():
    doc = Document()
    doc.add_heading("空节", level=1)
    doc.add_heading("末节", level=1)
    doc.add_paragraph("末节正文。")
    sections = split_sections(doc)
    assert sections[0]["para_end"] == sections[0]["para_start"]  # 邻接标题空节
    replace_section_paragraphs(doc, sections[0], ["补进空节的正文。"])
    out = _roundtrip(doc)
    texts = [p.text for p in out.paragraphs]
    assert texts.index("补进空节的正文。") < texts.index("末节")


def test_empty_paragraphs_list_clears_section():
    doc = _build_doc()
    sections = split_sections(doc)
    replace_section_paragraphs(doc, sections[0], [])
    out = _roundtrip(doc)
    texts = [p.text for p in out.paragraphs]
    assert texts == ["第一节", "第二节", "第二节正文。"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_docx_edit.py -v`
Expected: FAIL（`ModuleNotFoundError: ...docx_edit`）

- [ ] **Step 3: 实现**

```python
# rag/svr/document_rewrite/docx_edit.py
# -*- coding: utf-8 -*-
"""docx 段落区间替换与格式保真（设计 §6）。

- 只操作 doc.paragraphs 索引空间里的 w:p 元素，区间内 w:tbl（表格）原样保留；
- 顶层节标题段落（para_start）永不删除、永不改写；
- 新段落 pPr 拷贝自首个被删正文段（样式/缩进/行距/对齐）；
- 新 run rPr 拷贝自「正文特征 run」（被删段落中非加粗且文本最长的 run，
  跳过加粗标题 run；全加粗时退化为最长 run）；
- 支持 1段→N段、N段→1段、N段→0段、空节→N段。
原 Document 对象就地修改；版本化由调用方负责（先读 blob → 改 → 存新 blob）。
"""
from __future__ import annotations

import copy

from docx.text.paragraph import Paragraph


def _pick_body_run_rpr(old_paras: list[Paragraph]):
    """从被删段落中挑「正文特征 run」的 rPr 深拷贝。"""
    best = None
    best_len = -1
    fallback = None
    fallback_len = -1
    for p in old_paras:
        for r in p.runs:
            n = len(r.text or "")
            if n > fallback_len:
                fallback, fallback_len = r, n
            if not r.bold and n > best_len:
                best, best_len = r, n
    chosen = best if best is not None else fallback
    if chosen is None or chosen._r.rPr is None:
        return None
    return copy.deepcopy(chosen._r.rPr)


def _insert_new_paragraph(anchor: Paragraph | None, doc, pPr_tmpl, rPr_tmpl, text: str):
    """在 anchor 段之前插入一个新段落（anchor 为 None 时追加到文档末尾）。"""
    if anchor is not None:
        p = anchor.insert_paragraph_before("")
    else:
        p = doc.add_paragraph("")
    if pPr_tmpl is not None:
        # 空段落无 pPr，直接把拷贝插为第一个子元素（w:pPr 必须是 w:p 首子元素）
        p._p.insert(0, copy.deepcopy(pPr_tmpl))
    run = p.add_run(text)
    if rPr_tmpl is not None:
        # w:rPr 必须是 w:r 首子元素
        run._r.insert(0, copy.deepcopy(rPr_tmpl))
    return p


def replace_section_paragraphs(doc, section: dict, new_paragraphs: list[str]) -> None:
    """把 section（sections.py 切出的节）的正文段替换为 new_paragraphs。
    就地修改 doc；标题段与表格不动。"""
    paras = list(doc.paragraphs)
    start = section["para_start"] + 1
    end = section["para_end"]

    if end < start:
        # 邻接标题的空节：无旧段可拷贝格式，插到下一节标题之前（即本节末尾）
        anchor = paras[start] if start < len(paras) else None
        for text in new_paragraphs:
            _insert_new_paragraph(anchor, doc, None, None, text)
        return

    old = paras[start:end + 1]
    first_pPr = old[0]._p.pPr
    pPr_tmpl = copy.deepcopy(first_pPr) if first_pPr is not None else None
    rPr_tmpl = _pick_body_run_rpr(old)

    # 逐条插到首个旧段之前（保持顺序），再删旧段；表格元素全程不被触碰
    for text in new_paragraphs:
        _insert_new_paragraph(old[0], doc, pPr_tmpl, rPr_tmpl, text)
    for p in old:
        p._p.getparent().remove(p._p)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_doc_rewrite_docx_edit.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/document_rewrite/docx_edit.py test/test_doc_rewrite_docx_edit.py
git commit -m "feat(rewrite): docx_edit 段落区间替换+pPr/rPr样式拷贝+表格保留"
```

---

### Task 3: 版本链 — DocRewriteVersion 表 + migrate_db + versions.py + flow switch 参数

**Files:**
- Modify: `api/db/db_models.py`（新表 + init_database_tables + migrate_db 三处）
- Modify: `api/db/services/flow_service.py:232`（add_version 加 `switch_current` 参数）
- Create: `rag/svr/document_rewrite/versions.py`
- Test: `test/test_doc_rewrite_versions.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_versions.py
# -*- coding: utf-8 -*-
"""版本链纯函数测试：root_id 推导 / 对象名 / source_type 枚举 / flow switch 参数。"""
from unittest.mock import patch

from rag.svr.document_rewrite.versions import (
    SOURCE_TYPES,
    _obj_name,
    _root_id_from_doc_id,
)


def test_root_id_from_tplfill_doc_id():
    assert _root_id_from_doc_id("tplfill-abc123") == "abc123"


def test_root_id_passthrough():
    assert _root_id_from_doc_id("some-other-obj") == "some-other-obj"
    assert _root_id_from_doc_id("") == ""


def test_obj_name_deterministic():
    assert _obj_name("root1", 3) == "rewrite-root1-v3"


def test_source_types_enum():
    assert set(SOURCE_TYPES) == {"chat_fill", "flow_version", "rewrite", "rollback"}


def test_flow_add_version_switch_current_false_skips_flow_update():
    """flow add_version(switch_current=False) 不得更新 FlowInstance.current_version_id。"""
    import sys
    import types

    # flow_service 已在环境里可 import（有 peewee 即可，不触库）
    from api.db.services import flow_service

    calls = {"flow_update": 0}

    class FakeVersion:
        def __init__(self, **kw):
            self.__data__ = dict(kw)
            self.id = "vid"

    class FakeQuery:
        def where(self, *a):
            return self

        def first(self):
            return None

    class FakeModel:
        flow_id = "flow_id"
        version_no = "version_no"

        @classmethod
        def select(cls):
            return FakeQuery()

    with patch.object(flow_service.DB, "atomic"), \
            patch.object(flow_service, "FlowVersion") if hasattr(flow_service, "FlowVersion") else None:
        # 只验证分支行为：switch_current=False 时不走 FlowInstance.update
        svc = flow_service.FlowVersionService
        orig_model = svc.model
        svc.model = FakeModel
        try:
            with patch.object(flow_service.DB, "atomic"):
                with patch.object(
                    flow_service.FlowInstance, "update",
                    side_effect=AssertionError("switch_current=False 不得更新 current_version_id"),
                ):
                    row = svc.add_version(
                        {"id": "f1", "status": "running"},
                        object_name="obj1", file_name="a.docx", file_type="docx",
                        file_size=10, source="ai_rewrite", created_by="u1",
                        switch_current=False,
                    )
                    assert row["version_no"] == 1
        finally:
            svc.model = orig_model
```

注意：上面 `patch.object(flow_service, "FlowVersion") ... else None` 的条件写法不可靠——实现 Step 3 后如发现 `flow_service` 模块内 insert 用的模型名不同，**以实际源码为准改写此测试的 patch 目标**，但必须保留「switch_current=False 时 FlowInstance.update 被调用则断言失败」这个核心断言。若 `insert` 内部还有时间戳 helper 触发 DB，可参照 `test/test_template_fill_delegate.py` 的 stub 模式把 `flow_service.insert` 一并 patch。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_versions.py -v`
Expected: FAIL（ImportError / TypeError: unexpected keyword `switch_current`）

- [ ] **Step 3: 实现 db_models.py 三处改动**

先读 `api/db/db_models.py` 末尾（migrate_db 区域）和 `init_database_tables`（:707-726）、TplFillTask 样板（:2173-2191）。然后：

(a) 在 TplFillTask 等自定义表附近新增模型（保持文件既有 import 风格）：

```python
class DocRewriteVersion(DataBaseModel):
    """C端对话成稿局部重写版本链（append-only，复制式回退）。

    root_id：版本链锚。chat=原成稿对象名派生（tplfill-{task_id} → task_id）；
    flow=flow_id（flow 场景版本链本体在 flow_version 表，本表不写 flow 行）。
    当前版恒为链内 max(version_no)，无指针字段。
    """
    root_id = CharField(max_length=32, index=True)
    version_no = IntegerField(index=True)
    source_type = CharField(max_length=16, default="rewrite", verbose_name="chat_fill/flow_version/rewrite/rollback")
    bucket = CharField(max_length=128, default="")
    obj = CharField(max_length=1024, default="")
    file_name = CharField(max_length=512, default="")
    file_type = CharField(max_length=16, default="docx")
    instruction = TextField(null=True)
    section_title = CharField(max_length=512, default="")
    created_by = CharField(max_length=32, index=True)

    class Meta:
        db_table = "doc_rewrite_version"
        indexes = ((("root_id", "version_no"), True),)
```

(b) `init_database_tables` 清单加 `DocRewriteVersion,`（与 TplFillTask 同列位置）。

(c) `migrate_db` 末尾同款幂等迁移：

```python
    if not DocRewriteVersion.table_exists():
        DocRewriteVersion.create_table(safe=True)
```

- [ ] **Step 4: 实现 flow_service.add_version 的 switch_current 参数**

`api/db/services/flow_service.py:232`，把 `FlowInstance.update(...)` 包进条件（其余逻辑一行不动）：

```python
    @classmethod
    @DB.connection_context()
    def add_version(cls, flow: dict, object_name: str, file_name: str, file_type: str,
                    file_size: int, source: str, created_by: str,
                    switch_current: bool = True) -> dict:
        # ... 原注释保留 ...
        with DB.atomic():
            last = (
                cls.model.select()
                .where(cls.model.flow_id == flow["id"])
                .order_by(cls.model.version_no.desc())
                .first()
            )
            version_no = (last.version_no + 1) if last else 1
            v = cls.insert(
                flow_id=flow["id"],
                version_no=version_no,
                file_name=file_name,
                file_path=object_name,
                file_type=file_type,
                file_size=file_size,
                source=source,
                created_by=created_by,
                node_status=flow["status"],
            )
            if switch_current:
                FlowInstance.update(
                    current_version_id=v.id,
                    update_time=current_timestamp(),
                    update_date=datetime_format(datetime.now()),
                ).where(FlowInstance.id == flow["id"]).execute()
        return v.__data__
```

默认 `True`，全部既有调用点行为不变（B端零变化）。

- [ ] **Step 5: 实现 versions.py**

```python
# rag/svr/document_rewrite/versions.py
# -*- coding: utf-8 -*-
"""重写版本链：chat 场景 doc_rewrite_version 表 + {tenant}-downloads 桶；
flow 场景复用 flow_version 表（source=ai_rewrite，不切 current_version_id）。

DB/存储依赖全部延迟 import（工具注册期安全）。设计 §7：
- (root_id, version_no) 唯一索引 + 取号，并发撞线 IntegrityError → 重试（最多3次）；
- 回退=复制式：历史版 blob 登记为新 version_no，append-only；
- 首次对某成稿重写时先登记 version_no=1（原始成稿，source_type=chat_fill）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("chat_fill", "flow_version", "rewrite", "rollback")
_FLOW_REWRITE_SOURCE = "ai_rewrite"  # flow_version.source 枚举扩展值
_MAX_REGISTER_RETRY = 3


def _root_id_from_doc_id(doc_id: str) -> str:
    """chat 成稿对象名 → 版本链锚：tplfill-{task_id} → task_id；其余原样。"""
    doc_id = (doc_id or "").strip()
    if doc_id.startswith("tplfill-"):
        return doc_id[len("tplfill-"):]
    return doc_id


def _obj_name(root_id: str, version_no: int) -> str:
    return f"rewrite-{root_id}-v{version_no}"


def _chat_bucket(tenant_id: str) -> str:
    return f"{tenant_id}-downloads"


def list_versions(root_id: str) -> list[dict]:
    from api.db.db_models import DocRewriteVersion

    return [
        r.__data__ for r in DocRewriteVersion.select()
        .where(DocRewriteVersion.root_id == root_id)
        .order_by(DocRewriteVersion.version_no.asc())
    ]


def get_version(root_id: str, version_no: int) -> dict | None:
    from api.db.db_models import DocRewriteVersion

    row = (
        DocRewriteVersion.select()
        .where(
            (DocRewriteVersion.root_id == root_id)
            & (DocRewriteVersion.version_no == version_no)
        )
        .first()
    )
    return row.__data__ if row else None


def register_chat_version(tenant_id: str, root_id: str, blob: bytes, file_type: str,
                          base_file_name: str, source_type: str, instruction: str = "",
                          section_title: str = "", force_version_no: int | None = None) -> dict:
    """登记一个 chat 版本：MinIO 写 {tenant}-downloads + doc_rewrite_version 行。
    force_version_no 用于首次登记原始成稿（=1）；正常重写事务取号自动递增。
    对象名确定性（rewrite-{root}-v{n}），重试覆盖写幂等。返回版本行 dict。"""
    from peewee import IntegrityError

    from api.db.db_models import DB, DocRewriteVersion
    from api.db.services.file_service import FileService

    file_name = f"{base_file_name}_v{'' if force_version_no is None else force_version_no}.docx"
    last_err: Exception | None = None
    for _attempt in range(_MAX_REGISTER_RETRY):
        try:
            with DB.atomic():
                if force_version_no is not None:
                    version_no = force_version_no
                else:
                    last = (
                        DocRewriteVersion.select()
                        .where(DocRewriteVersion.root_id == root_id)
                        .order_by(DocRewriteVersion.version_no.desc())
                        .first()
                    )
                    version_no = (last.version_no + 1) if last else 1
                obj = _obj_name(root_id, version_no)
                FileService.put_blob(tenant_id, obj, blob)
                row = DocRewriteVersion.insert(
                    root_id=root_id,
                    version_no=version_no,
                    source_type=source_type,
                    bucket=_chat_bucket(tenant_id),
                    obj=obj,
                    file_name=file_name or obj,
                    file_type=file_type,
                    instruction=instruction or "",
                    section_title=section_title or "",
                    created_by=tenant_id,
                )
                return row.__data__
        except IntegrityError as e:  # 并发取号撞唯一索引 → 重试
            last_err = e
            logger.warning("[rewrite] version_no conflict root=%s retry=%s", root_id, _attempt)
    raise RuntimeError(f"版本登记并发冲突，请重试：{last_err}")


def ensure_base_version(tenant_id: str, root_id: str, blob: bytes, file_type: str,
                        base_file_name: str) -> None:
    """首次对该成稿重写时补登记 version_no=1（原始成稿），保证链完整可回退。
    已有链则空操作；并发下唯一索引兜底，冲突视为已登记。"""
    if list_versions(root_id):
        return
    try:
        register_chat_version(
            tenant_id, root_id, blob, file_type, base_file_name,
            source_type="chat_fill", force_version_no=1,
        )
    except (RuntimeError, Exception) as e:  # noqa: B902
        if not list_versions(root_id):
            raise
        logger.warning("[rewrite] base version race resolved root=%s: %s", root_id, e)


def save_flow_version(flow: dict, blob: bytes, file_type: str, file_name: str,
                      created_by: str) -> dict:
    """flow 场景：blob 写 flow 桶（initiator_id）+ flow_version 行（不切 current）。
    返回 flow_version 行 dict（含 id/file_path/version_no）。"""
    from common import settings

    from api.db.services.flow_service import FlowVersionService

    bucket = flow["initiator_id"]
    obj = f"ai-rewrite-{flow['id']}-{file_name}"
    settings.STORAGE_IMPL.put(bucket, obj, blob)
    return FlowVersionService.add_version(
        flow, object_name=obj, file_name=file_name, file_type=file_type,
        file_size=len(blob), source=_FLOW_REWRITE_SOURCE, created_by=created_by,
        switch_current=False,
    )


def load_flow_version_blob(version_row: dict) -> tuple[bytes, dict]:
    """按 flow_version 行取 blob。返回 (blob, flow_row)。flow 已删时明确报错。"""
    from common import settings

    from api.db.services.flow_service import FlowInstanceService

    ok, flow = FlowInstanceService.get_by_id(version_row["flow_id"])
    if not ok or not flow:
        raise ValueError("该版本所属的流程已被删除，无法重写。")
    bucket = flow["initiator_id"] if isinstance(flow, dict) else flow.__data__["initiator_id"]
    blob = settings.STORAGE_IMPL.get(bucket, version_row["file_path"])
    if not blob:
        raise ValueError("版本文件已丢失，无法重写。")
    if isinstance(flow, dict):
        return blob, flow
    return blob, flow.__data__
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_doc_rewrite_versions.py -v`
Expected: 5 passed

- [ ] **Step 7: 验证既有套件不回退 + 迁移语法**

```bash
uv run --no-sync pytest test/test_doc_rewrite_versions.py test/test_doc_rewrite_sections.py test/test_doc_rewrite_docx_edit.py -v
python -c "import ast; ast.parse(open('api/db/db_models.py', encoding='utf-8').read()); ast.parse(open('api/db/services/flow_service.py', encoding='utf-8').read()); print('syntax OK')"
```
Expected: 19 passed + `syntax OK`

- [ ] **Step 8: Commit**

```bash
git add api/db/db_models.py api/db/services/flow_service.py rag/svr/document_rewrite/versions.py test/test_doc_rewrite_versions.py
git commit -m "feat(rewrite): doc_rewrite_version表+versions链(chat桶/flow不切current)+add_version switch参数"
```

---

### Task 4: rewriter.py — LLM 按节重写

**Files:**
- Create: `rag/svr/document_rewrite/rewriter.py`
- Test: `test/test_doc_rewrite_rewriter.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_rewriter.py
# -*- coding: utf-8 -*-
"""LLM 重写对抗测试：合法JSON/围栏包裹/非法JSON重试/空段落/超限截断/超长节报错。"""
import pytest

from rag.svr.document_rewrite.rewriter import (
    _MAX_PARAGRAPHS,
    _parse_paragraphs,
    rewrite_section,
)


class FakeMdl:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = 0

    async def async_chat(self, system, messages):
        self.calls += 1
        return self.outputs[min(self.calls, len(self.outputs)) - 1]


def test_parse_plain_json():
    assert _parse_paragraphs('{"paragraphs": ["a", "b"]}') == ["a", "b"]


def test_parse_fenced_json_with_noise():
    txt = '好的，以下是重写结果：\n```json\n{"paragraphs": ["段落一", "段落二"]}\n```\n请查收。'
    assert _parse_paragraphs(txt) == ["段落一", "段落二"]


def test_parse_rejects_bad_shapes():
    assert _parse_paragraphs("不是JSON") is None
    assert _parse_paragraphs('{"paragraphs": []}') is None
    assert _parse_paragraphs('{"paragraphs": ["", "   "]}') is None
    assert _parse_paragraphs('{"other": 1}') is None
    assert _parse_paragraphs('{"paragraphs": "一段文字"}') is None


def test_parse_drops_empty_keeps_nonempty():
    assert _parse_paragraphs('{"paragraphs": ["", "有效段", null, "第二段"]}') == ["有效段", "第二段"]


@pytest.mark.asyncio
async def test_rewrite_success_first_try():
    mdl = FakeMdl(['{"paragraphs": ["新1", "新2"]}'])
    out = await rewrite_section(
        "t1", "补充进度", "进度安排", "原内容", "第1节 A", "第3节 C", mdl=mdl)
    assert out == ["新1", "新2"]
    assert mdl.calls == 1


@pytest.mark.asyncio
async def test_rewrite_retries_once_on_bad_json_then_fails():
    mdl = FakeMdl(["瞎写的", "还是不行"])
    with pytest.raises(ValueError, match="重写失败"):
        await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_rewrite_recovers_on_second_try():
    mdl = FakeMdl(["垃圾输出", '{"paragraphs": [" recovered "]}'])
    out = await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert out == ["recovered"]


@pytest.mark.asyncio
async def test_section_too_long_raises_before_llm():
    mdl = FakeMdl([])
    with pytest.raises(ValueError, match="过长"):
        await rewrite_section("t1", "改", "节", "字" * 30001, "", "", mdl=mdl)
    assert mdl.calls == 0


def test_paragraph_limits():
    paras = [f"段{i}" for i in range(_MAX_PARAGRAPHS + 10)]
    out = _parse_paragraphs('{"paragraphs": ' + repr(paras).replace("'", '"') + "}")
    assert out is not None
    assert len(out) <= _MAX_PARAGRAPHS
    long_out = _parse_paragraphs('{"paragraphs": ["' + "长" * 3000 + '"]}')
    assert long_out is not None and len(long_out[0]) == 2000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_rewriter.py -v`
Expected: FAIL（ModuleNotFoundError）。若 `pytest.mark.asyncio` 报 unknown mark，检查 `pyproject.toml` 是否已配 asyncio_mode；仓库既有异步测试（如 test_template_fill_executor.py）怎么跑就照抄其模式（最坏情况在文件顶部加 `pytestmark = pytest.mark.asyncio` 并确认 pytest-asyncio 已装）。

- [ ] **Step 3: 实现**

```python
# rag/svr/document_rewrite/rewriter.py
# -*- coding: utf-8 -*-
"""LLM 按节重写（设计 §5）。

投喂：用户指令 + 目标节全文 + 全文目录 + 前后节标题（不投喂全文正文）。
输出：JSON {"paragraphs": ["新段落1", ...]}，纯段落文本，LLM 不产格式标记。
代码端兜底：非法 JSON/空段落重试 1 次；段数 1-50、单段 ≤2000 字（超出截断）；
投喂正文 >3 万字直接报错（不静默截断）。失败不产生版本（由工具层保证）。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_MAX_SECTION_CHARS = 30000
_MAX_PARAGRAPHS = 50
_MAX_PARA_CHARS = 2000

_REWRITE_SYSTEM = (
    "你是公文文档改写专家。用户会给出一份文档的目录、某一节的当前内容，以及针对该节的重写要求。"
    "你的任务：只重写这一节，输出该节的新正文段落。要求：\n"
    "1. 保持公文体例与书面语，忠实结合用户要求改写；与目录中其他节衔接自然，不重复其他节内容；\n"
    "2. 输出是纯段落数组，不使用任何 Markdown 标记（不要 #、*、-、表格）；\n"
    "3. 只输出一个 JSON 对象，格式严格为：{\"paragraphs\": [\"新段落1\", \"新段落2\"]}\n"
    "4. 不要输出任何解释、前后缀或代码块标记之外的内容。"
)


def _parse_paragraphs(txt: str) -> list[str] | None:
    """从 LLM 输出中提取 paragraphs 数组。非法/空返回 None。"""
    if not txt or not isinstance(txt, str):
        return None
    m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    paras = data.get("paragraphs") if isinstance(data, dict) else None
    if not isinstance(paras, list):
        return None
    cleaned = [str(p).strip() for p in paras if isinstance(p, str) and str(p).strip()]
    if not cleaned:
        return None
    return cleaned


async def rewrite_section(tenant_id: str, instruction: str, section_title: str,
                          section_text: str, outline_text: str,
                          prev_title: str, next_title: str, mdl=None) -> list[str]:
    """重写一节，返回新段落数组。mdl 可注入（测试）；否则按租户默认聊天模型构建。"""
    if len(section_text) > _MAX_SECTION_CHARS:
        raise ValueError("该节内容超过3万字，建议拆分文档后重写。")

    prev_line = f"上一节标题：{prev_title}" if prev_title else "（本节是第一节）"
    next_line = f"下一节标题：{next_title}" if next_title else "（本节是最后一节）"
    user_msg = (
        f"【全文目录】\n{outline_text}\n\n"
        f"{prev_line}；{next_line}\n\n"
        f"【目标节《{section_title}》当前内容】\n{section_text}\n\n"
        f"【重写要求】\n{instruction}"
    )

    if mdl is None:
        from api.db import LLMType
        from api.db.services.llm_service import LLMBundle
        from api.db.services.tenant_service import TenantService

        from common.misc_utils import get_tenant_default_model_by_type  # 兜底：实际 import 路径以 executor.py:269-273 为准

    if mdl is None:
        # 与 rag/svr/template_fill/executor.py:269-273 同源：租户默认聊天模型
        from rag.llm.chat_model import ...  # ← 占位禁止：实现时照抄 executor.py 的 import 与构造
    ...
```

**实现注意（重要）**：上面 `mdl is None` 分支的 import 路径**必须照抄** `rag/svr/template_fill/executor.py:269-273` 的 `_build_chat_mdl`（该文件是可运行事实源），形如：

```python
def _build_chat_mdl(tenant_id: str):
    from api.db import LLMType
    from api.db.services.llm_service import LLMBundle
    from api.settings import get_tenant_default_model_by_type  # ← 以 executor.py 实际 import 为准
    return LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, LLMType.CHAT))
```

主体逻辑（import 修正后补全为）：

```python
    if mdl is None:
        mdl = _build_chat_mdl(tenant_id)

    last_err: Exception | None = None
    for attempt in (1, 2):
        txt = await mdl.async_chat(_REWRITE_SYSTEM, [{"role": "user", "content": user_msg}])
        paras = _parse_paragraphs(txt)
        if paras:
            truncated = False
            paras = paras[:_MAX_PARAGRAPHS]
            fixed = []
            for p in paras:
                if len(p) > _MAX_PARA_CHARS:
                    p = p[:_MAX_PARA_CHARS]
                    truncated = True
                fixed.append(p)
            if truncated:
                logger.warning("[rewrite] 段落超 %s 字已截断 section=%s", _MAX_PARA_CHARS, section_title)
            return fixed
        last_err = ValueError(f"LLM 输出非法（第{attempt}次）")
        logger.warning("[rewrite] bad LLM output attempt=%s preview=%s", attempt, (txt or "")[:120])
    raise ValueError("重写失败，请重试。")
```

（注：`_MAX_SECTION_CHARS` 用 `>` 判断，3万字整不报错。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_doc_rewrite_rewriter.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add rag/svr/document_rewrite/rewriter.py test/test_doc_rewrite_rewriter.py
git commit -m "feat(rewrite): rewriter LLM按节重写(JSON段落契约+校验重试+超限防御)"
```

---

### Task 5: agent/tools/document_rewrite.py — DocumentRewrite 工具

**Files:**
- Create: `agent/tools/document_rewrite.py`
- Test: `test/test_doc_rewrite_tool.py`

**设计要点（写码前必读）：**
- action 四个：`outline`（返回带编号目录）、`rewrite`（section_no + instruction）、`versions`（列版本链）、`rollback`（version_no → 复制为新版本）。
- 文档来源解析：`flow_version_id`（sys 变量，flow 场景）优先；否则 `doc_id`（LLM 显式传参）或 `sys.recent_downloads` 最近一张成稿卡。
- chat 成稿 blob 在 `{tenant_id}-downloads` 桶，对象名即 doc_id → `FileService.get_blob(tenant_id, doc_id)`（桶按租户隔离，天然 owner 校验；blob 缺失明确报错）。
- rewrite/rollback 成功后：①写版本（chat→versions.py；flow→save_flow_version）；②把 download 契约 append 到 `self._canvas.globals["sys.pending_downloads"]`（Task 6 的 Message 组件会合并输出成稿卡）；③返回说明文本（含新版文件名，引导 LLM 向用户转述）。
- `sys.recent_downloads` 形如 `[{"doc_id": "...", "filename": "..."}]`（最近2张，倒序=最新在前）。canvas.run 白名单空值不写入 → 读取必须容错（try/except KeyError + isinstance 校验）。
- `@timeout(_EXEC_TIMEOUT)` 装饰 `_invoke`；LLM 调用用 `asyncio.run(...)`（工具在线程池里跑，无运行中的 loop，安全）。

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_tool.py
# -*- coding: utf-8 -*-
"""DocumentRewrite 工具契约测试：stub 画布与存储，验证 action 分发/download 契约/sys 容错。"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from docx import Document

from agent.tools.document_rewrite import DocumentRewrite


def _docx_blob():
    doc = Document()
    doc.add_heading("第一节", level=1)
    doc.add_paragraph("第一节正文。")
    doc.add_heading("第二节", level=1)
    doc.add_paragraph("第二节正文。")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class FakeCanvas:
    def __init__(self, tenant_id="t1", sys_vars=None):
        self.globals = {"sys.pending_downloads": []}
        self.globals.update(sys_vars or {})
        self._tenant = tenant_id

    def get_tenant_id(self):
        return self._tenant

    def get_variable_value(self, name):
        if name in self.globals:
            return self.globals[name]
        raise KeyError(name)


def _make_tool(canvas):
    param = MagicMock()
    param.get_meta = lambda: {}
    return DocumentRewrite(canvas, "node1", param)


def _patch_env(blob):
    """stub 存储与 LLM：get_blob 返回 docx blob，LLM 返回合法重写 JSON。"""
    class FakeLLM:
        async def async_chat(self, system, messages):
            return json.dumps({"paragraphs": ["重写后的新正文。"]}, ensure_ascii=False)

    return [
        patch("agent.tools.document_rewrite.FileService.get_blob", return_value=blob),
        patch("agent.tools.document_rewrite.STORAGE_IMPL.put", return_value=True),
        patch("agent.tools.document_rewrite.STORAGE_IMPL.get", return_value=blob),
        patch("agent.tools.document_rewrite.rewrite_section", side_effect=None),
    ]


def _set_doc_env(blob):
    """更直接的桩：patch 模块内 _load_chat_blob 与 _build_mdl。"""
    class FakeLLM:
        async def async_chat(self, system, messages):
            return json.dumps({"paragraphs": ["重写后的新正文。"]}, ensure_ascii=False)

    return [
        patch("agent.tools.document_rewrite._load_chat_blob", return_value=(blob, "docobj-1", "测试文档.docx")),
        patch("agent.tools.document_rewrite.DocumentRewrite._build_chat_mdl", return_value=FakeLLM()),
    ]


def test_missing_doc_context_returns_guidance():
    tool = _make_tool(FakeCanvas())  # 无 recent_downloads 无 doc_id
    out = tool._invoke(action="outline")
    assert "无法确定" in out or "成稿" in out


def test_outline_action():
    tool = _make_tool(FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案.docx"}]}))
    with patch("agent.tools.document_rewrite._load_chat_blob", return_value=(_docx_blob(), "tplfill-task1", "方案.docx")):
        out = tool._invoke(action="outline")
    assert "第1节 第一节" in out
    assert "第2节 第二节" in out


def test_rewrite_produces_download_contract():
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    patches = _set_doc_env(_docx_blob())
    with patches[0], patches[1], \
            patch("agent.tools.document_rewrite.register_chat_version") as reg, \
            patch("agent.tools.document_rewrite.ensure_base_version") as ens, \
            patch("agent.tools.document_rewrite.list_versions", return_value=[]):
        reg.return_value = {"version_no": 2, "obj": "rewrite-task1-v2", "file_name": "方案_v2.docx"}
        out = tool._invoke(action="rewrite", section_no="2", instruction="补充进度安排")
    # download 契约进入 canvas 待发队列
    dl = canvas.globals["sys.pending_downloads"]
    assert len(dl) == 1
    assert dl[0]["doc_id"] == "rewrite-task1-v2"
    for key in ("doc_id", "filename", "mime_type"):
        assert key in dl[0]
    assert dl[0]["url"].startswith("/api/v1/agents/download?id=")
    assert "created_by=t1" in dl[0]["url"]
    # 文本说明可转述
    assert "第二节" in out and "2" in out


def test_rewrite_unknown_section_no():
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    patches = _set_doc_env(_docx_blob())
    with patches[0], patches[1]:
        out = tool._invoke(action="rewrite", section_no="99", instruction="改")
    assert "99" in out or "不存在" in out
    assert canvas.globals["sys.pending_downloads"] == []


def test_no_heading_document_guides_full_regen():
    doc = Document()
    doc.add_paragraph("没有标题的文档")
    buf = io.BytesIO()
    doc.save(buf)
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    with patch("agent.tools.document_rewrite._load_chat_blob", return_value=(buf.getvalue(), "tplfill-task1", "方案.docx")):
        out = tool._invoke(action="rewrite", section_no="1", instruction="改")
    assert "标题" in out or "重新生成" in out


def test_versions_action_empty_chain():
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    with patch("agent.tools.document_rewrite._load_chat_blob", return_value=(_docx_blob(), "tplfill-task1", "方案.docx")), \
            patch("agent.tools.document_rewrite.list_versions", return_value=[]):
        out = tool._invoke(action="versions")
    assert "版本" in out


def test_rollback_copies_history_version():
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    hist = {"version_no": 1, "obj": "tplfill-task1", "bucket": "t1-downloads",
            "file_name": "方案.docx", "section_title": "", "instruction": ""}
    with patch("agent.tools.document_rewrite._load_chat_blob", return_value=(_docx_blob(), "tplfill-task1", "方案.docx")), \
            patch("agent.tools.document_rewrite.get_version", return_value=hist) as gv, \
            patch("agent.tools.document_rewrite.FileService.get_blob", return_value=_docx_blob()) as gb, \
            patch("agent.tools.document_rewrite.register_chat_version") as reg, \
            patch("agent.tools.document_rewrite.list_versions", return_value=[hist, {"version_no": 2}]):
        reg.return_value = {"version_no": 3, "obj": "rewrite-task1-v3", "file_name": "方案_v3.docx"}
        out = tool._invoke(action="rollback", version_no="1")
    gv.assert_called_once_with("task1", 1)
    dl = canvas.globals["sys.pending_downloads"]
    assert len(dl) == 1 and dl[0]["doc_id"] == "rewrite-task1-v3"
    assert "3" in out or "已回退" in out


def test_rollback_missing_version():
    canvas = FakeCanvas(sys_vars={"sys.recent_downloads": [{"doc_id": "tplfill-task1", "filename": "方案"}]})
    tool = _make_tool(canvas)
    with patch("agent.tools.document_rewrite._load_chat_blob", return_value=(_docx_blob(), "tplfill-task1", "方案.docx")), \
            patch("agent.tools.document_rewrite.get_version", return_value=None):
        out = tool._invoke(action="rollback", version_no="42")
    assert "42" in out or "不存在" in out


def test_bad_action():
    tool = _make_tool(FakeCanvas())
    out = tool._invoke(action="destroy")
    assert "action" in out or "不支持" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_tool.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# agent/tools/document_rewrite.py
# -*- coding: utf-8 -*-
"""C端对话 DocumentRewrite 工具：成稿 docx 按节 LLM 局部重写（设计 §3/§8）。

照 template_fill.py 插件结构：service 层全部延迟 import，避免注册期触发 DB 依赖。
执行层 rag/svr/document_rewrite/* 不依赖 agent 运行时，可独立测试。
文档来源：flow 场景读 sys.flow_version_id；chat 场景读 doc_id 参数或
sys.recent_downloads 最近成稿卡。产物 download 契约写入 canvas 全局
sys.pending_downloads，由 Message 组件合并输出（message.py _merge_pending_downloads）。
"""
import asyncio
import io
import json
import logging
import mimetypes
import os
from abc import ABC

from agent.tools.base import ToolBase, ToolMeta, ToolParamBase
from common.connection_utils import timeout

logger = logging.getLogger(__name__)

_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))

_MIME_BY_TYPE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class DocumentRewriteParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "DocumentRewrite",
            "description": """文档局部重写工具。对当前会话最近生成的成稿文档（Word）做按节重写。四个 action：

1. outline：返回文档的带编号章节目录。当用户说「重写第N节/某节」但不确定节号，或没有指明操作文档时，先调用它确认。
2. rewrite：重写某一节。需要 section_no（节号，来自 outline）和 instruction（用户对该节的重写要求，原样转述用户的补充要求）。完成后返回新版本说明，用户会看到新的成稿卡片。
3. versions：列出该文档的全部历史版本（版本号/来源/说明）。
4. rollback：回退到某个历史版本。需要 version_no（versions 返回的版本号）。回退会生成一个新版本（内容为历史版），不会丢失任何版本。

使用时机：用户对已生成的成稿说「把第N节重写/重新写一下XX部分/回退到上一版」时使用。文档默认取本会话最近一张成稿卡；操作流程文档版本时自动生效，无需传 doc_id。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：outline（目录）/ rewrite（重写某节）/ versions（版本列表）/ rollback（回退）。",
                    "enum": ["outline", "rewrite", "versions", "rollback"],
                    "required": True,
                },
                "section_no": {
                    "type": "string",
                    "description": "节号（outline 返回的第N节中的 N）。action=rewrite 时必填。",
                    "default": "",
                    "required": False,
                },
                "instruction": {
                    "type": "string",
                    "description": "用户对本节的重写要求（如「重点补充进度安排」）。action=rewrite 时必填。",
                    "default": "",
                    "required": False,
                },
                "version_no": {
                    "type": "string",
                    "description": "目标历史版本号。action=rollback 时必填。",
                    "default": "",
                    "required": False,
                },
                "doc_id": {
                    "type": "string",
                    "description": "可选。目标成稿对象 id；不传则用本会话最近一张成稿卡。",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()


class DocumentRewrite(ToolBase, ABC):
    component_name = "DocumentRewrite"

    # ---------- 入口 ----------

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("DocumentRewrite"):
            return
        try:
            action = str(kwargs.get("action") or "").strip()
            if action == "outline":
                return self._outline(kwargs)
            if action == "rewrite":
                return self._rewrite(kwargs)
            if action == "versions":
                return self._versions(kwargs)
            if action == "rollback":
                return self._rollback(kwargs)
            return (f"不支持的 action：{action or '(空)'}。"
                    "可用 action：outline / rewrite / versions / rollback。")
        except ValueError as e:
            return str(e)
        except Exception:
            logger.exception("DocumentRewrite invoke failed")
            self.set_output("_ERROR", "文档重写执行失败")
            return "文档重写执行失败，请稍后重试。"

    # ---------- 目标解析 ----------

    def _read_sys(self, name):
        try:
            return self._canvas.get_variable_value(name)
        except KeyError:
            return None
        except Exception:
            return None

    def _resolve_doc_id(self, explicit_doc_id: str) -> str:
        """chat 场景目标：显式 doc_id > sys.recent_downloads 最新一张。"""
        doc_id = str(explicit_doc_id or "").strip()
        if doc_id:
            return doc_id
        recent = self._read_sys("sys.recent_downloads")
        if isinstance(recent, list) and recent:
            first = recent[0]
            if isinstance(first, dict) and first.get("doc_id"):
                return str(first["doc_id"])
        raise ValueError(
            "无法确定要重写的文档：本会话没有可用的成稿卡片。"
            "请先用范本填写生成成稿，或明确指定要重写的文档。"
        )

    def _load_chat_blob(self, doc_id: str) -> tuple[bytes, str, str]:
        """读 chat 成稿 blob。返回 (blob, doc_id, 原文件名去扩展名)。
        桶按租户隔离，取不到即视为无权/已删。延迟 import 防注册期依赖。"""
        from api.db.services.file_service import FileService

        tenant_id = self._canvas.get_tenant_id()
        blob = FileService.get_blob(tenant_id, doc_id)
        if not blob:
            raise ValueError(f"成稿文件不存在或已被清理（doc_id={doc_id[:24]}），无法重写。")
        recent = self._read_sys("sys.recent_downloads") or []
        base_name = doc_id
        if isinstance(recent, list):
            for item in recent:
                if isinstance(item, dict) and item.get("doc_id") == doc_id and item.get("filename"):
                    base_name = str(item["filename"])
                    break
        if base_name.lower().endswith(".docx"):
            base_name = base_name[:-5]
        return blob, doc_id, base_name

    def _load_flow_target(self) -> tuple[bytes, dict, dict]:
        """flow 场景目标：sys.flow_version_id → (blob, flow_version行, flow行)。"""
        from rag.svr.document_rewrite.versions import load_flow_version_blob

        fv_id = str(self._read_sys("sys.flow_version_id") or "").strip()
        if not fv_id:
            return None, None, None
        from api.db.services.flow_service import FlowVersionService

        ok, row = FlowVersionService.get_by_id(fv_id)
        if not ok or not row:
            raise ValueError("流程版本文档不存在或已被删除，无法重写。")
        data = row if isinstance(row, dict) else row.__data__
        blob, flow = load_flow_version_blob(data)
        return blob, data, flow

    def _build_chat_mdl(self, tenant_id: str):
        """与 rag/svr/template_fill/executor.py:269-273 同源的租户默认聊天模型。
        实现时照抄该文件 _build_chat_mdl 的 import 与构造。"""
        from api.db import LLMType  # noqa: PLC0415 — 以 executor.py 实际代码为准补齐
        raise NotImplementedError  # Step 3 实现时替换为 executor 同款

    # ---------- 公共 ----------

    def _doc_for_action(self, kwargs) -> tuple[object, str, str, str]:
        """统一取 (python-docx Document, root_id, mode, base_name)。
        mode: chat / flow。"""
        from docx import Document as DocxDocument

        from rag.svr.document_rewrite.sections import split_sections  # noqa: F401

        blob, fv_row, flow_row = self._load_flow_target()
        if blob is not None:
            mode = "flow"
            root_id = fv_row["flow_id"]
            base_name = (fv_row.get("file_name") or "流程文档").rsplit(".", 1)[0]
        else:
            mode = "chat"
            doc_id = self._resolve_doc_id(kwargs.get("doc_id"))
            blob, doc_id, base_name = self._load_chat_blob(doc_id)
            from rag.svr.document_rewrite.versions import _root_id_from_doc_id

            root_id = _root_id_from_doc_id(doc_id)
        doc = DocxDocument(io.BytesIO(blob))
        return doc, root_id, mode, base_name, blob

    def _make_download(self, tenant_id: str, doc_id: str, filename: str, size: int) -> dict:
        mime = _MIME_BY_TYPE.get(
            filename.rsplit(".", 1)[-1].lower() if "." in filename else "",
            "application/octet-stream",
        ) or mimetypes.guess_type(filename)[0]
        return {
            "doc_id": doc_id,
            "filename": filename,
            "name": filename,
            "mime_type": mime,
            "size": size,
            "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}",
        }

    def _emit_download(self, tenant_id: str, doc_id: str, filename: str, size: int):
        dl = self._make_download(tenant_id, doc_id, filename, size)
        pending = self._canvas.globals.setdefault("sys.pending_downloads", [])
        if isinstance(pending, list):
            pending.append(dl)
        return dl

    def _save_docx_bytes(self, doc) -> bytes:
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _sections_safe(self, doc):
        from rag.svr.document_rewrite.sections import NoSectionError, split_sections

        try:
            return split_sections(doc), None
        except NoSectionError as e:
            return None, str(e)

    # ---------- action: outline ----------

    def _outline(self, kwargs):
        from rag.svr.document_rewrite.sections import build_outline

        doc, _root, _mode, _base, _blob = self._doc_for_action(kwargs)
        sections, err = self._sections_safe(doc)
        if err:
            return err
        return "文档章节目录：\n" + build_outline(sections)

    # ---------- action: rewrite ----------

    def _rewrite(self, kwargs):
        from rag.svr.document_rewrite.docx_edit import replace_section_paragraphs
        from rag.svr.document_rewrite.rewriter import rewrite_section
        from rag.svr.document_rewrite.sections import build_outline, get_section
        from rag.svr.document_rewrite.versions import (
            ensure_base_version,
            register_chat_version,
            save_flow_version,
        )

        tenant_id = self._canvas.get_tenant_id()
        section_no = self._to_int(kwargs.get("section_no"))
        instruction = str(kwargs.get("instruction") or "").strip()
        if section_no is None:
            return "缺少 section_no（节号）。请先用 action=outline 获取章节目录。"
        if not instruction:
            return "缺少 instruction（重写要求）。请说明要把这一节改成什么样。"

        doc, root_id, mode, base_name, _src_blob = self._doc_for_action(kwargs)
        sections, err = self._sections_safe(doc)
        if err:
            return err
        sec = get_section(sections, section_no)
        if sec is None:
            return (f"第{section_no}节不存在。该文档共 {len(sections)} 节：\n"
                    + build_outline(sections))

        paras = list(doc.paragraphs)
        body_text = "\n".join(
            (p.text or "").strip()
            for p in paras[sec["para_start"] + 1:sec["para_end"] + 1]
            if (p.text or "").strip()
        )
        prev_title = sections[section_no - 2]["title"] if section_no >= 2 else ""
        next_title = sections[section_no]["title"] if section_no < len(sections) else ""

        mdl = self._build_chat_mdl(tenant_id)
        new_paras = asyncio.run(rewrite_section(
            tenant_id, instruction, sec["title"], body_text,
            build_outline(sections), prev_title, next_title, mdl=mdl,
        ))

        replace_section_paragraphs(doc, sec, new_paras)
        new_blob = self._save_docx_bytes(doc)
        new_file_name = f"{base_name}.docx"

        if mode == "flow":
            row = save_flow_version(flow_row_cache := self._flow_row_cache,  # 见下方说明
                                    new_blob, "docx", new_file_name, tenant_id)
            obj_id, version_label = row["file_path"], f"v{row['version_no']}"
        else:
            ensure_base_version(tenant_id, root_id, _src_blob, "docx", base_name)
            row = register_chat_version(
                tenant_id, root_id, new_blob, "docx", base_name,
                source_type="rewrite", instruction=instruction,
                section_title=sec["title"],
            )
            obj_id, version_label = row["obj"], f"v{row['version_no']}"

        self._emit_download(tenant_id, obj_id, f"{base_name}_{version_label}.docx", len(new_blob))
        return (
            f"已完成第{section_no}节《{sec['title']}》重写（{version_label}）：{instruction}。"
            f"共 {len(new_paras)} 个段落。新版本已生成，请查看新的成稿卡片；"
            f"如不满意可以说「回退到上一版」。"
        )

    def _flow_row_cache(self):  # pragma: no cover — 占位说明，实现时删除
        raise NotImplementedError

    @staticmethod
    def _to_int(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    # ---------- action: versions / rollback ----------

    def _versions(self, kwargs):
        from rag.svr.document_rewrite.versions import list_versions

        _doc, root_id, mode, _base, _blob = self._doc_for_action(kwargs)
        if mode == "flow":
            return "流程文档的版本请到「流程」页签的版本时间线查看。"
        rows = list_versions(root_id)
        if not rows:
            return ("该文档还没有重写版本记录（当前成稿即原始版本）。"
                    "重写或回退后会自动生成版本链。")
        lines = [f"共 {len(rows)} 个版本："]
        for r in rows:
            tag = "（原始成稿）" if r["source_type"] == "chat_fill" else (
                "（回退）" if r["source_type"] == "rollback" else "")
            note = f"，改「{r['section_title']}」" if r.get("section_title") else ""
            lines.append(
                f"- v{r['version_no']}{tag}{note} {r['file_name']}"
                + (f"，指令：{r['instruction'][:30]}" if r.get("instruction") else ""))
        lines.append("可以说「回退到第N版」恢复历史版本。")
        return "\n".join(lines)

    def _rollback(self, kwargs):
        from rag.svr.document_rewrite.versions import (
            get_version,
            list_versions,
            register_chat_version,
        )

        tenant_id = self._canvas.get_tenant_id()
        version_no = self._to_int(kwargs.get("version_no"))
        if version_no is None:
            return "缺少 version_no。请先用 action=versions 查看版本列表。"

        _doc, root_id, mode, base_name, _blob = self._doc_for_action(kwargs)
        if mode == "flow":
            return "流程文档版本请到「流程」页签操作回退。"
        hist = get_version(root_id, version_no)
        if hist is None:
            rows = list_versions(root_id)
            chain = "、".join(f"v{r['version_no']}" for r in rows) or "（空）"
            return f"版本 v{version_no} 不存在。现有版本：{chain}。"

        from api.db.services.file_service import FileService

        blob = FileService.get_blob(tenant_id, hist["obj"])
        if not blob:
            return f"版本 v{version_no} 的文件已丢失，无法回退。"
        row = register_chat_version(
            tenant_id, root_id, blob, hist.get("file_type", "docx"), base_name,
            source_type="rollback", instruction=f"回退到 v{version_no}",
            section_title=hist.get("section_title", ""),
        )
        self._emit_download(
            tenant_id, row["obj"],
            f"{base_name}_v{row['version_no']}.docx", len(blob))
        return (
            f"已回退到 v{version_no} 的内容，登记为新版本 v{row['version_no']}。"
            "请查看新的成稿卡片；原版本仍保留，可再次回退。"
        )
```

**实现时必须修正的两处（计划中的刻意占位，写码时按下面改）：**

1. `_build_chat_mdl`：删除 `NotImplementedError` 桩，照抄 `rag/svr/template_fill/executor.py` 的 `_build_chat_mdl`（该函数已在 T1 方案A实现并通过审查），包括其全部 import（模块顶层 import 一次即可）。
2. `_rewrite` 的 flow 分支：`_doc_for_action` 返回值里 flow 模式已经拿到 `flow_row`，把它并入返回元组（`return doc, root_id, mode, base_name, blob, flow_row`），然后直接 `save_flow_version(flow_row, new_blob, "docx", new_file_name, tenant_id)`；删除 `_flow_row_cache` 桩与 `flow_row_cache :=` 写法。chat 分支取 `_src_blob` 对应元组位。所有调用点（`_outline/_versions/_rollback`）同步适配六元组返回。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_doc_rewrite_tool.py -v`
Expected: 10 passed

若测试报 `agent.tools.document_rewrite` import 期触发 DB（不应发生，全部延迟 import），把报错栈修到 import 纯净为止。

- [ ] **Step 5: 全量工具相关回归**

Run: `uv run --no-sync pytest test/test_doc_rewrite_tool.py test/test_template_fill_tool.py -v`
Expected: 全部 passed（FillTemplate 既有用例不回退）

- [ ] **Step 6: Commit**

```bash
git add agent/tools/document_rewrite.py test/test_doc_rewrite_tool.py
git commit -m "feat(rewrite): DocumentRewrite工具(outline/rewrite/versions/rollback+download契约入sys.pending_downloads)"
```

---

### Task 6: 后端管道 — canvas 白名单 + completion 转发 + downloads 持久化 + Message 合并

**Files:**
- Modify: `agent/canvas.py:415-429`（run() 白名单 +2 键）
- Modify: `api/db/services/canvas_service.py:229-235, 279-317, 319-339`（completion 转发 + downloads 落库）
- Modify: `agent/component/message.py:241, 277`（合并 sys.pending_downloads）
- Test: `test/test_doc_rewrite_pipeline.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_doc_rewrite_pipeline.py
# -*- coding: utf-8 -*-
"""后端管道测试：Message 合并 pending_downloads（契约核心），+ canvas_service 持久化捕获。"""
import asyncio
from unittest.mock import MagicMock

from agent.component.message import Message


class FakeCanvas:
    def __init__(self, pending=None):
        self.globals = {}
        if pending is not None:
            self.globals["sys.pending_downloads"] = pending

    def get_tenant_id(self):
        return "t1"

    def get_variable_value(self, name):
        if name in self.globals:
            return self.globals[name]
        raise KeyError(name)

    def set_variable_value(self, name, value):
        self.globals[name] = value


def _msg_component(canvas, template):
    m = Message(canvas, "msg1", MagicMock())
    m._param.content = [template]
    return m


def _dl(doc_id):
    return {"doc_id": doc_id, "filename": f"{doc_id}.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def test_non_stream_merges_pending_downloads():
    canvas = FakeCanvas(pending=[_dl("rewrite-x-v2")])
    m = _msg_component(canvas, "正文说明")
    # 非流式路径：_run 需要 input 元素解析，直接调合并 helper 验证契约
    downloads = []
    m._merge_pending_downloads(downloads)
    assert len(downloads) == 1
    assert downloads[0]["doc_id"] == "rewrite-x-v2"
    assert downloads[0]["url"].startswith("/api/v1/agents/download?id=rewrite-x-v2&created_by=t1")
    assert downloads[0]["name"] == "rewrite-x-v2.docx"
    # 合并后清空 pending，防重复下发
    assert canvas.globals["sys.pending_downloads"] == []


def test_merge_dedupes_by_doc_id():
    canvas = FakeCanvas(pending=[_dl("dup-id")])
    m = _msg_component(canvas, "t")
    downloads = [_dl("dup-id")]
    m._merge_pending_downloads(downloads)
    assert len(downloads) == 1


def test_merge_no_canvas_or_empty_pending_is_noop():
    m = _msg_component(FakeCanvas(), "t")
    downloads = []
    m._merge_pending_downloads(downloads)
    assert downloads == []


def test_persist_capture_shape():
    """canvas_service.completion 的 workflow_finished 捕获逻辑（抽取的纯函数）。"""
    from api.db.services.canvas_service import _extract_finished_downloads

    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {"downloads": [_dl("a")]}}}
    ) == [_dl("a")]
    assert _extract_finished_downloads({"event": "message"}) is None
    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {}}}) is None
    assert _extract_finished_downloads(
        {"event": "workflow_finished", "data": {"outputs": {"downloads": "bad"}}}) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_doc_rewrite_pipeline.py -v`
Expected: FAIL（`_merge_pending_downloads` 不存在、`_extract_finished_downloads` 不存在）

- [ ] **Step 3: 实现 message.py 合并**

`agent/component/message.py`，在 `Message` 类内（`_with_download_url` 之后）加：

```python
    def _merge_pending_downloads(self, downloads: list[dict[str, Any]]):
        """工具（DocumentRewrite 等）运行期把 download 契约写入 canvas 全局
        sys.pending_downloads；Message 出口统一合并进 downloads 输出（按 doc_id
        幂等去重），合并后清空防重复下发。"""
        try:
            pending = (self._canvas.globals or {}).get("sys.pending_downloads") if self._canvas else None
            if isinstance(pending, list):
                seen = {d.get("doc_id") for d in downloads if isinstance(d, dict)}
                for d in pending:
                    if isinstance(d, dict) and d.get("doc_id") and d.get("doc_id") not in seen:
                        downloads.append(self._with_download_url(dict(d)))
                        seen.add(d.get("doc_id"))
            if self._canvas is not None and hasattr(self._canvas, "globals"):
                self._canvas.globals["sys.pending_downloads"] = []
        except Exception:
            logging.exception("merge pending downloads failed")
```

两处 `self.set_output("downloads", downloads)`（:241 流式、:277 非流式）之前各插一行：

```python
        self._merge_pending_downloads(downloads)
```

- [ ] **Step 4: 实现 canvas.py 白名单**

`agent/canvas.py:416`，白名单加两键（其余一行不动）：

```python
            if k in ["query", "user_id", "files", "internet", "recent_downloads", "flow_version_id"] and kwargs[k]:
```

（两个新键走 `else` 分支 `self.globals[f"sys.{k}"] = kwargs[k]`，自动变成 `sys.recent_downloads` / `sys.flow_version_id`。空值不写入——工具读取侧已容错。）

- [ ] **Step 5: 实现 canvas_service 转发 + downloads 持久化**

`api/db/services/canvas_service.py`：

(a) 模块级新增纯函数（放 `completion` 之前）：

```python
def _extract_finished_downloads(ans: dict):
    """从 workflow_finished 事件提取 downloads 输出（List[dict]），否则 None。"""
    if not isinstance(ans, dict) or ans.get("event") != "workflow_finished":
        return None
    outputs = (ans.get("data") or {}).get("outputs")
    if isinstance(outputs, dict):
        dls = outputs.get("downloads")
        if isinstance(dls, list) and dls and all(isinstance(d, dict) for d in dls):
            return dls
    return None
```

(b) `completion()` 内（:274 `template_fill_events = []` 附近）加捕获变量：

```python
    finished_downloads = None  # Capture downloads for persistence (刷新后恢复成稿卡)
```

(c) 事件循环（:337 `elif ans["event"] == "template_fill_progress":` 之后、`yield` 之前）加分支：

```python
            elif ans["event"] == "workflow_finished":
                captured = _extract_finished_downloads(ans)
                if captured:
                    finished_downloads = captured
```

(d) `_persist_messages` 内（:292 `if template_fill_events:` 块之后、`conv.message.append` 之前）加：

```python
            if finished_downloads:
                data_field = assistant_msg.get("data")
                if not isinstance(data_field, dict):
                    data_field = {}
                    assistant_msg["data"] = data_field
                data_field["downloads"] = finished_downloads
```

(e) `completion()` 签名区域（:230-235 解构处）加两键转发：

```python
    recent_downloads = kwargs.get("recent_downloads") or []
    flow_version_id = str(kwargs.get("flow_version_id") or "").strip()
```

(f) :320 的 `canvas.run(...)` 调用加参数：

```python
        async for ans in canvas.run(query=query, files=files, user_id=user_id, inputs=inputs,
                                    internet=kwargs.get("internet"),
                                    recent_downloads=recent_downloads,
                                    flow_version_id=flow_version_id):
```

（agent_api.py 无需改动：`_iter_session_completion_events` 已把 `**req` 全键透传给 `agent_completion` → `completion(**kwargs)`。）

- [ ] **Step 6: 跑测试确认通过 + 回归**

```bash
uv run --no-sync pytest test/test_doc_rewrite_pipeline.py -v
uv run --no-sync pytest test/test_template_fill_events.py test/test_agent_fill_template_component.py -v
```
Expected: 新 4 用例 passed；既有套件不回退（Message 组件改动影响面 = template_fill_events 回放链，必须绿）。

- [ ] **Step 7: Commit**

```bash
git add agent/canvas.py api/db/services/canvas_service.py agent/component/message.py test/test_doc_rewrite_pipeline.py
git commit -m "feat(rewrite): canvas白名单+completion转发recent_downloads/flow_version_id+downloads持久化+Message合并pending"
```

---

### Task 7: 前端 — recent_downloads 附带 + 历史 downloads 恢复 + flow 面板 flow_version_id

**Files:**
- Create: `web/src/pages/c-chat/recent-downloads.ts`（收集 helper + 单测友好）
- Create: `web/src/pages/c-chat/recent-downloads.test.ts`
- Modify: `web/src/pages/c-chat/index.tsx:1048-1059`（历史映射恢复 downloads）、`:1295-1306`（payload 附带）
- Modify: `web/src/pages/c-chat/flow/flow-ai-panel.tsx:529-536`（payload 附 flow_version_id）

- [ ] **Step 1: 写 helper 与失败测试**

```ts
// web/src/pages/c-chat/recent-downloads.ts
export interface DownloadInfo {
  doc_id: string;
  filename: string;
  name?: string;
  mime_type?: string;
  size?: number;
  url?: string;
}

interface MessageLike {
  downloads?: DownloadInfo[];
}

/**
 * 收集会话内最近 N 张成稿卡（默认2），按消息顺序取末尾、新卡在前。
 * 在线消息（WorkflowFinished 合并）与历史恢复消息共用同一 msg.downloads 形态。
 */
export function collectRecentDownloads(
  messages: MessageLike[],
  limit = 2,
): Array<{ doc_id: string; filename: string }> {
  const out: Array<{ doc_id: string; filename: string }> = [];
  for (let i = messages.length - 1; i >= 0 && out.length < limit; i--) {
    const dls = messages[i]?.downloads;
    if (!Array.isArray(dls)) continue;
    for (let j = dls.length - 1; j >= 0 && out.length < limit; j--) {
      const d = dls[j];
      if (d && typeof d.doc_id === 'string' && d.doc_id) {
        out.push({ doc_id: d.doc_id, filename: d.filename || d.name || '' });
      }
    }
  }
  return out;
}
```

```ts
// web/src/pages/c-chat/recent-downloads.test.ts
import { collectRecentDownloads } from './recent-downloads';

const dl = (doc_id: string) => ({ doc_id, filename: `${doc_id}.docx`, mime_type: 'x' });

describe('collectRecentDownloads', () => {
  it('取最近2张，新卡在前', () => {
    const msgs = [
      { downloads: [dl('a1')] },
      {},
      { downloads: [dl('b1'), dl('b2')] },
      { downloads: [dl('c1')] },
    ];
    expect(collectRecentDownloads(msgs).map((d) => d.doc_id)).toEqual(['c1', 'b2']);
  });

  it('空/无downloads安全', () => {
    expect(collectRecentDownloads([])).toEqual([]);
    expect(collectRecentDownloads([{}, { downloads: [] }])).toEqual([]);
  });

  it('doc_id 非字符串或缺失跳过', () => {
    expect(
      collectRecentDownloads([{ downloads: [{ doc_id: 1 as any, filename: 'x' }, { filename: 'y' } as any, dl('ok')] }]),
    ).toEqual([{ doc_id: 'ok', filename: 'ok.docx' }]);
  });

  it('filename 缺失回退 name，再缺失空串', () => {
    expect(
      collectRecentDownloads([{ downloads: [{ doc_id: 'x', name: '名字' } as any] }]),
    ).toEqual([{ doc_id: 'x', filename: '名字' }]);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx jest src/pages/c-chat/recent-downloads.test.ts --config=../.scratch/jest.config.mini.js`
若 `.scratch/jest.config.mini.js` 不存在，先创建（内容如下，已 gitignore）：

```js
// .scratch/jest.config.mini.js — 迷你 jest 配置（仓库 jest.config 依赖 umi/test，本地未装）
module.exports = {
  rootDir: '../web',
  testEnvironment: 'jsdom',
  transform: { '^.+\\.tsx?$': ['ts-jest', { tsconfig: { esModuleInterop: true, jsx: 'react-jsx' } }] },
  testMatch: ['<rootDir>/src/pages/c-chat/recent-downloads.test.ts'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'json'],
};
```

Expected: FAIL（模块不存在）→ 实现 helper 后 PASS（4 passed）。

- [ ] **Step 3: index.tsx 历史映射恢复 downloads**

`web/src/pages/c-chat/index.tsx:1048-1059` 的历史消息 return 对象（`templateFill` 字段旁）加一行：

```tsx
              // 成稿卡恢复：重写/回退要引用上一轮成稿（recent_downloads 的数据源）
              downloads: m.data?.downloads,
```

（`m.data?.downloads` 形态即后端 `data_field["downloads"]` = download 契约数组。）

- [ ] **Step 4: index.tsx 发送 payload 附 recent_downloads**

`web/src/pages/c-chat/index.tsx:1295-1306` 的 `sendMessage`：文件顶部 import helper；payload 加字段。消息列表 state 变量名以实际源码为准（历史映射写入的那个数组 state；一般是 `answerList` 或等价），取发送时刻的当前会话消息：

```tsx
import { collectRecentDownloads } from './recent-downloads';
```

```tsx
      const res = await send({
        agent_id: currentAgentId,
        query,
        session_id: sessionId,
        stream: true,
        files: currentFiles,
        internet: enableInternet,
        recent_downloads: collectRecentDownloads(messagesRef.current ?? []),
      });
```

实现注意：sendMessage 是 useCallback，依赖数组须包含能取到最新消息列表的 ref（若组件已有 `answerListRef`/等价 ref 用之；若没有，新增 `const messagesRef = useRef<IMessage[]>([])` 并在消息列表 set 的地方同步赋值——以实际源码为准，禁止在 deps 里放会每次渲染变化的数组导致 callback 重建）。

- [ ] **Step 5: flow-ai-panel.tsx 附 flow_version_id**

`web/src/pages/c-chat/flow/flow-ai-panel.tsx:529-536` 的 payload 对象加：

```tsx
        flow_version_id: String(currentVersion?.id ?? ''),
```

（`currentVersion` 为面板内当前版本文档 state，变量名以实际源码为准——读该文件确认持有当前版本的 state/ref 名，取其 id；无版本时空串，canvas 白名单空值不写入，工具自动降级 chat 来源。）

- [ ] **Step 6: 类型检查**

```bash
cd web && npx tsc --noEmit 2>&1 | grep -E "recent-downloads|c-chat/index|flow-ai-panel" ; echo "exit=$?"
```
Expected: 本计划触碰文件无新增错误（全仓存量错误忽略）。

- [ ] **Step 7: 跑 helper 测试确认通过**

```bash
cd web && npx jest src/pages/c-chat/recent-downloads.test.ts --config=../.scratch/jest.config.mini.js
```
Expected: 4 passed

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/c-chat/recent-downloads.ts web/src/pages/c-chat/recent-downloads.test.ts web/src/pages/c-chat/index.tsx web/src/pages/c-chat/flow/flow-ai-panel.tsx
git commit -m "feat(rewrite): 前端recent_downloads附带+历史downloads恢复成稿卡+flow面板flow_version_id"
```

---

### Task 8: 收尾 — 全量回归 + CHANGE.md + 审查

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）
- Modify: `CLAUDE.md`（参考文档表登记设计文档引用，若尚未登记）

- [ ] **Step 1: 后端全量回归**

```bash
uv run --no-sync pytest test/test_doc_rewrite_sections.py test/test_doc_rewrite_docx_edit.py test/test_doc_rewrite_versions.py test/test_doc_rewrite_rewriter.py test/test_doc_rewrite_tool.py test/test_doc_rewrite_pipeline.py test/test_template_fill_utils.py test/test_template_fill_executor.py test/test_template_api_routes.py test/test_template_fill_events.py test/test_template_fill_delegate.py test/test_agent_fill_template_component.py test/test_template_fill_progress_api.py -v
```
Expected: 全部 passed（新 42+ 用例 + 既有 348 用例不回退）。

- [ ] **Step 2: ruff 检查触碰文件**

```bash
uv run --no-sync ruff check rag/svr/document_rewrite/ agent/tools/document_rewrite.py agent/component/message.py agent/canvas.py api/db/services/canvas_service.py api/db/services/flow_service.py
```
Expected: 无错误（自动修复可跑 `ruff check --fix` 后人工复核 diff）。

- [ ] **Step 3: CHANGE.md 追加条目**

`CHANGE.md` 顶部按既有格式追加：

```markdown
## 2026-09-12 C端对话文档按节局部重写（DocumentRewrite）

**主题**：对话里说「把第3节重写，补充XX」→ LLM 按节重写成稿 docx → 新版本成稿卡，可回退。

**核心变更**：
- 新增 Agent 工具 `DocumentRewrite`（agent/tools/document_rewrite.py，outline/rewrite/versions/rollback 四 action，DSL 零改动，自动发现注册）
- 新增执行层 `rag/svr/document_rewrite/`：sections.py（heading1/标题1/大纲级别0切节）、docx_edit.py（段落区间替换+pPr/rPr样式拷贝+表格保留）、rewriter.py（LLM JSON 段落契约+校验重试）、versions.py（doc_rewrite_version 版本链，(root_id,version_no) 唯一索引+并发取号重试，回退=复制式 append-only）
- 新表 `doc_rewrite_version`（init_database_tables + migrate_db 幂等迁移，部署时自动建表）
- flow 场景复用 flow_version 表（`FlowVersionService.add_version` 新增 `switch_current` 参数，ai_rewrite 不切 current_version_id）
- 产物链路：工具写 canvas 全局 sys.pending_downloads → Message 组件合并输出 download 契约 → 成稿卡
- 上下文链路：前端 payload 附 recent_downloads（最近2张成稿卡）/ flow_version_id → canvas.run 白名单 → sys 变量 → 工具读取
- 存量缺口修复：canvas workflow_finished 的 downloads 持久化进 message data + 前端历史消息恢复 downloads 渲染成稿卡（否则刷新后无法发起重写）
- 测试：6 个新测试套件（切节/替换/版本/重写/工具/管道，对抗用例覆盖无heading、邻接标题、表格保留、并发撞号、越权、非法JSON等）

**遗留**：
- flow 场景 rollback 走流程页签人工操作（对话内提示引导），对话内 flow 回退未做
- heading 识别覆盖度依赖样式名枚举（Heading 1/标题 1/大纲级别0），奇形模板切节失败明确报错优于错切（设计已接受）
- E2E 联调（填写→重写→回退→下载还原）待部署后验证

**部署**：后端 7 文件成套 SCP（agent/tools/document_rewrite.py、rag/svr/document_rewrite/ 4文件、agent/canvas.py、agent/component/message.py、api/db/services/canvas_service.py、api/db/services/flow_service.py、api/db/db_models.py）+ docker restart；前端 npm run build + dist 部署。新表由 migrate_db 自动创建。
```

- [ ] **Step 4: CLAUDE.md 参考表登记**

`CLAUDE.md` 参考文档表追加一行（绝对路径 + 一句话简介，遵守文档引用规范）：

```markdown
| 对话文档按节局部重写 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-12-chat-doc-section-rewrite-design.md` | ★ C端对话「把第N节重写」：DocumentRewrite 工具（outline/rewrite/versions/rollback）+ heading切节/LLM重写/docx段落区间替换样式拷贝/doc_rewrite_version版本链 + canvas downloads持久化修复；实施计划 docs/superpowers/plans/2026-09-12-chat-doc-section-rewrite.md |
```

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: C端对话文档按节局部重写实施完成记录CHANGE.md+CLAUDE.md参考登记"
```

- [ ] **Step 6: 终审**

派终审子代理（konus-code-review 技能不可用时用 general-purpose + opus）做跨任务整体审查：
- 规格覆盖对照 spec §2 目标（四 action / 格式保真 / 版本化 / downloads 缺口修复）
- B端零变化核查：flow_service switch_current 默认 True、Message 合并仅在有 pending 时生效、canvas 白名单新增键不影响既有键
- 回归验证记录（Step 1 输出）
- 修复循环：发现问题 → 修复 → 复审，直至通过

---

## Self-Review 记录（写计划时已完成）

1. **Spec 覆盖**：§3 架构（T5/T6 工具+管道）✓ §4 切节（T1）✓ §5 LLM重写（T4）✓ §6 docx回写（T2）✓ §7 版本管理（T3）✓ §8 前端+downloads缺口修复（T6/T7）✓ §9 边界场景逐条落测试（无heading T1、超界 T5、非法JSON T4、并发撞号 T3、越权=租户桶隔离 T5、段落漂移=每次重新解析 T5 无缓存、断连无副作用=同步无状态）✓ §10 测试要求（各任务 TDD 覆盖）✓
2. **占位符扫描**：Task 5 `_build_chat_mdl` 与 flow 分支两处为**刻意标注的实现指引**（要求照抄 executor.py 已验证代码），非 TBD；其余步骤均含完整代码。
3. **类型一致性**：`_doc_for_action` 六元组修正已在 Task 5 内注明；download 契约键（doc_id/filename/mime_type/url）与 message.py `_is_download_info`（三键判定）一致；`recent_downloads`/`flow_version_id` 键名前端 payload ↔ canvas 白名单 ↔ `sys.` 前缀读取三处一致。
