# 文件审核弹框表格可编辑 — 设计文档

日期：2026-09-01
状态：已与用户对齐，待实施

## 1. 背景与问题

C端流程页签「文件审核」弹框使用 Lexical 富文本编辑器（`web/src/pages/c-chat/docx-paragraph-editor.tsx`）实现 Word 式正文编辑（run 级格式落盘，2026-08-31 迭代）。表格与图片被实现为只读原子块 `AtomicBlockNode`（`contenteditable=false`，HTML 经 `dangerouslySetInnerHTML` 渲染），因此文本可编辑而表格内容不可编辑。

只读是当时的有意取舍：diff 契约（`docx-diff.ts`）把表格当原子段（只记 seen 不参与 diff），后端（`api/apps/restful_apis/flow_app.py::edit_document`）对表格段落定位返回「不支持编辑」。

## 2. 需求（已确认）

- 表格**单元格内可改文字**，并支持 run 级格式（加粗/斜体/下划线/删除线/上下标/颜色/底色/字体/字号），工具栏在单元格内同样生效
- **不**支持表格结构编辑（不增删行列、不增删表格）
- 表格内现有**批注高亮与编辑共存**：命中批注目标的文字继续染色、可点击联动右侧批注卡片
- 图片维持只读原子块，不变

## 3. 方案选型

| 方案 | 结论 |
|------|------|
| A. `@lexical/table` 表格节点体系（表格→TableNode/TableRowNode/TableCellNode，格内为普通 Lexical 段落） | ✅ 采用 |
| B. 原子块内嵌 React contentEditable 自研单元格 | 否：脱离 Lexical 模型，undo/工具栏/diff/格式落盘全部另接一套 |
| C. 整表 HTML 替换（后端删旧建新） | 否：python-docx 从 HTML 重建表格无法保真还原合并单元格/列宽/表头样式 |

`@lexical/table@0.23.1` 已在 `web/package.json` 依赖中，当前编辑器未注册其节点，无版本风险。

## 4. 设计

### 4.1 编辑器层（`docx-paragraph-editor.tsx`）

**节点模型**：
- 新增 `DocxTableNode extends TableNode`（`@lexical/table`），携带 `paraIndex`（原文表格段落号，初始灌入时赋值；`importJSON/exportJSON` 均需序列化该字段）
- 行列由 `TableRowNode`/`TableCellNode` 表达，格内为普通段落节点（正文段的 run 格式模型原样适用）
- `AtomicBlockNode` 保留但只承载 `kind=image` 与畸形 HTML 的只读降级展示；正常表格不再走该路径
- editor nodes 注册 `DocxTableNode/TableRowNode/TableCellNode`，挂载 `@lexical/table` 的 `TablePlugin` 获得表格选区支持

**HTML→Lexical 灌入**（`buildInitialContent` 替换 table 分支）：
- `DOMParser` 解析后端返回的表格 HTML：`<tr>`→TableRowNode，`<td>/<th>`→TableCellNode（`th` 记录表头状态）
- `colspan/rowspan` 写入 TableCellNode 对应属性；构建逻辑网格映射，被合并覆盖的格位不建独立格
- 格内文本命中批注目标 → 与正文段同一机制拆 `HighlightTextNode` 染色；`targetsByPara` 扩展为按 `(paraIndex, row, col)` 索引（cellKey 即该三元组）
- 单元格 CSS 对齐（`text-align`）写入格内段落 format；字符样式（粗体表头等）解析为 TextNode format，视觉与原 HTML 一致
- 畸形/解析失败 → 降级回只读 AtomicBlockNode 展示，不阻塞整个文档打开

**编辑能力复用（零额外编辑器代码）**：
- 格内即普通 Lexical 段落 → `DocxToolbar` run 级格式按钮、undo/redo、`PastePlugin` 粘贴降级、`ClickPlugin` 批注高亮点击联动（`data-anchor-key` closest 查找在格内同样生效）
- TableCellNode `createDOM` 套用现有表格 CSS（边框/内边距/表头底色），视觉与现只读 HTML 一致

### 4.2 diff 与保存契约（`docx-diff.ts` + `flow_app.py`）

**前端抽取**：`readEditorBlocks` 遇 `DocxTableNode` 时逐格产出：

```
{ paraIndex, kind: 'table', cell: {row, col}, text, runs, fmtSig }
```

- `runs` 抽取复用 `$extractRuns`（格内 HighlightTextNode 底色不落盘）
- 原文基线：前端从初始灌入的表格 HTML 自行解析 `cells: [{row, col, text}]`（零后端改动，基线与灌入同源）

**diff 规则**：
- 逐格比对：文本变 → `table_edit`；文本同但 run 签名变 → 纯格式 `table_edit`
- 新增 ops 类型 `table_edits: [{para_index, row, col, new_text, runs?}]`，与 `edits/deletes/inserts` 并列，计入 `count` 与 200 处上限
- 正文段落与表格格改动可混合出现在同一次保存
- 「删除表格报错」保护保留；表格不参与 deletes/inserts

**后端（`edit_document` 扩展）**：
- 解析校验 `table_edits`：`para_index` 定位到 `kind=table` 段落（`_build_para_map` 已含）；`row/col` 非负整数；`new_text` 允许空串（清空单元格合法，区别于正文段落）；runs 一致性校验与正文同规则；错误信息带具体格位
- 应用：`table.cell(row, col)` 定位 python-docx `_Cell`，复用 `_apply_runs`/`_replace_para_text` 写入第一段、清空 cell 内其余段；越界返回定位失败
- 事务语义不变：先全部定位成功再统一应用，存为新版本（source=manual_edit）

### 4.3 边界条件

| 场景 | 规则 |
|------|------|
| 合并单元格 | `row/col` 以 docx 逻辑网格坐标为准；前端对 colspan/rowspan 建同样网格映射，被覆盖格位不产生 edit；后端 `cell(r,c)` 对被覆盖格位返回同一 `_Cell` |
| 嵌套表格 | 不支持；灌入时忽略格内 `<table>`（按纯文本处理），diff 不产生其改动 |
| 格内多段落 | 允许（回车自然分段）；diff 按 cell 抽取全部段落；后端整串写入第一段、清空其余段 |
| 空单元格 | `new_text` 允许空串；表格与正文两套 ops 互不越界 |
| Ops 上限 | `table_edits` 计入 200 处上限 |
| 并发 | 与正文同语义：保存即新版本，定位失败报「文档可能已变化，请刷新」；不做行级锁 |
| 光标稳定 | 格内高亮重建沿用 targetsByPara useMemo 稳定 + 仅身份变化时重拆的既有约定 |

## 5. 错误处理

- `table_edits` 项格式非法（row/col 非整数/负数、runs 与文本不一致）→ 400，错误信息带 para_index+row+col
- `row/col` 越界或表格段定位失败 → 定位失败提示刷新
- 表格 HTML 畸形 → 灌入降级只读展示，不阻塞文档打开

## 6. 测试

**单测（`diffBlocks`）**：格文本变 / 纯格式变 / 多格混合 / 与正文段落混排 / trim 空串 / colspan 网格错位。

**对抗性用例**：空表、格全空、超长文本（>20000）、控制字符注入、runs 与文本不一致、row/col 负数与超大值、未闭合 HTML、嵌套表格、并发保存版本漂移。

**后端 pytest**：`table.cell(r,c)` 定位、越界报错、清空格、合并单元格语义、保存后新版本可下载。

**手动 E2E**：改字+加粗+保存 → 新版本时间线 → 重开内容格式一致；格内批注高亮点击联动右侧卡片。

## 7. 涉及文件

| 层 | 文件 |
|----|------|
| 前端编辑器 | `web/src/pages/c-chat/docx-paragraph-editor.tsx`（新节点/灌入/抽取） |
| 前端 diff | `web/src/pages/c-chat/docx-diff.ts`（table_edits 契约） |
| 前端宿主 | `web/src/pages/c-chat/review-panel.tsx`（表格渲染入口、targetsByPara 结构） |
| 后端 API | `api/apps/restful_apis/flow_app.py`（edit_document 解析与应用） |
