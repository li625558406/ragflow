# 文件审核编辑器升级：Lexical 整篇编辑（替换手写 contentEditable）— 设计文档

日期：2026-08-31
状态：设计已确认，待实施
关联：`docs/superpowers/specs/2026-08-30-flow-workflow-design.md`（流程页签）、`CHANGE.md` 2026-08-31 三条文件审核迭代

## 1. 背景与目标

当前「文件审核」正文编辑为手写 contentEditable + DOM diff（review-panel.tsx `collectPaperOps`），已知痛点：

- 无撤销/重做（浏览器原生 undo 与 React 受控渲染互相干扰）
- 中文输入法 composition 期间脆弱（输入中不能有任何重渲染）
- 跨段删除/并段、全选等编辑行为的边界要自己兜底
- 编辑期间不能动纸张 vdom（丢光标），状态管理受限

**目标（用户确认）**：交互体验升级——用成熟富文本框架替换手写 contentEditable，获得可靠的撤销/重做、IME 安全、原生回车分段/退格并段。**不要求**格式保真（run 级样式仍按整段替换）。

**非目标**：
- 表格/图片保持只读原子块（用户确认），后端表格编辑不在本期
- 不引入 OnlyOffice/Collabora 等独立文档服务（重型方案已否决）
- 后端 `/flow/<id>/document/edit` 接口零改动

## 2. 选型结论

| 方案 | 结论 |
|------|------|
| **A. Lexical 0.23.1（选定）** | 项目已有 `lexical` + `@lexical/react` + `@lexical/table`（agent 提示词编辑器、协作 yjs-provider 在用），零新增依赖；编辑器托管 DOM，天然解决撤销/IME/光标问题 |
| B. TipTap (ProseMirror) | 需新增 ~10 包，项目变双富文本栈，维护成本高 |
| C. 继续打磨手写方案 | 撤销栈/IME 坑深，正是要摆脱的路径 |

## 3. 组件架构

新文件 `web/src/pages/c-chat/docx-paragraph-editor.tsx`：

| 单元 | 职责 |
|------|------|
| `DocxParagraphNode extends ParagraphNode` | 文档正文段；属性 `__paraIndex`；`createDOM` 输出 `data-para-index` |
| `DocxHeadingNode extends HeadingNode` | 标题段；同上 |
| `AtomicBlockNode extends DecoratorNode` | table/image 只读原子块；属性 `__paraIndex`、`__kind`、`__payload`（表格 HTML/图片说明）；`createDOM` 输出 `data-para-index` 且 `contenteditable=false`；`decorate()` 返回 React 片段——渲染现有表格 HTML（`sanitizeTableHtml` + `highlightInTableHtml` + `highlightInTableByAnchor` + `handleSelectTableAnn` 点击联动，逻辑从 review-panel 原样搬入） |
| `HighlightTextNode extends TextNode` | 高亮片段；属性 `__anchorKey`；`createDOM` 输出 `data-anchor-key` + 底色/下划线 inline style |
| `DocxParagraphEditor` 组件 | 入口 props：`content`（段落列表）、`canEdit`、`targetsByPara`、`onAnchorClick`、`onDirtyChange`、`editorRef`（向父级暴露 LexicalEditor 实例，供父级调用 diff） |
| `collectEditorOps(editor, content)` | 模型级 diff，输出与现有后端契约一致的 `{edits, deletes, inserts, count}` |
| 表格辅助函数（`sanitizeTableHtml` / `highlightInTableHtml` / `highlightInTableByAnchor` / `normalizeForMatch` 等） | 从 review-panel.tsx 抽到独立模块（如 `web/src/pages/c-chat/docx-view-utils.tsx`），AtomicBlockNode 的 decorate 与 review-panel 只读静态分支共用同一份实现 |

### 3.1 para_index 的关键设计

`paraIndex` 仅在**初始加载**（`importJSON` / 初始化构建）时赋值。Lexical 内部克隆节点（回车分段、拆分文本）走无参构造 → 新实例 `paraIndex === undefined`，天然区隔「原文段落」与「新增段落」。diff 不再解析 DOM，直接遍历 `editor.read(() => $getRoot().getChildren())`。

## 4. 渲染分工

- **可编辑模式**（`canEdit`：当前节点负责人 + reviewSource==='version'）：整篇渲染 `DocxParagraphEditor`（A4 纸张样式套在编辑器根节点上，宋体/页边距/阴影不变），表格/图片以原子块嵌入
- **只读模式**（非负责人 / 手动上传附件 / 无版本）：**保持现有静态渲染完全不动**——批注高亮、引线、表格锚点零风险
- ErrorBoundary 包裹编辑器，渲染异常时回退静态只读渲染

## 5. 批注高亮与既有交互兼容

- `HighlightPlugin`：仅在 `targetsByPara` 变化时执行一次（首次加载、批注/标注增删）——把段落文本按高亮目标（首个 AI 标注 + 首个手动批注，规则同现状）拆分为 `[普通 TextNode][HighlightTextNode][普通 TextNode]` 序列。**打字过程中绝不重拆**，高亮边界允许暂时陈旧，保存刷新后重新精确落位
- 点击联动：`editor.registerCommand(CLICK_COMMAND, ...)` 命中 `data-anchor-key` → 复用 `handleAnchorClick`
- 选字批注：现有 `handleContentMouseUp` 基于原生 `window.getSelection` + `closest('[data-para-index]')`，与 Lexical DOM 兼容，逻辑原样保留（含 anchor_start 归一化偏移、悬浮「添加批注」按钮、draft 输入框）
- 引线测量 effect（`[data-anchor-key]` / `[data-para-index]` 查询）不变
- 粘贴降级纯文本：拦截 `PASTE_COMMAND`，以 `text/plain` 插入，防外部富文本破坏段落结构

## 6. 保存 diff 细则

`collectEditorOps` 在 `editor.read()` 中遍历根级子节点：

1. 有 `paraIndex` 的段落/标题节点：`getTextContent().trim()` 对比原文 → 变化 = edit、空 = delete（并段/清空场景）
2. 无 `paraIndex` 的节点（回车新产生）→ insert，`afterParaIndex` = 前一个带 index 节点的 index（无则 -1 = 文档开头）
3. 原文 index 未出现在模型中（整块被删）→ delete
4. 原子块（table/image）缺失 → 返回错误「不支持删除表格/图片，请撤销该操作后保存」；放弃修改 = 编辑器按 resetKey 重挂载
5. 输入防抖 250ms 后调用同一 diff 函数更新「已修改 N 处」吸顶保存栏（200 段模型遍历开销可忽略）

保存链路不变：`onEditDocument(ops)` → `editFlowDocument`（flow-service.ts，零改动）→ 后端 `/document/edit`（零改动）→ 成功后父级刷新 content → 编辑器以新内容重建。

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| 保存失败 | 现有 `editError` 提示不变，编辑器内容保留 |
| 后端定位失败/版本漂移 | 提示刷新；用户确认后编辑器重挂载 |
| 全选误删含原子块 | diff 拦截报错，放弃修改走重挂载 |
| 编辑器渲染异常 | ErrorBoundary 回退静态只读渲染 |

## 8. 测试

1. **单测**（`collectEditorOps` 模型级 diff）：空段、重复文本、首段前插入（-1）、连续并段、原子块缺失报错、全空文档、200 段上限
2. **浏览器 E2E**（沿用现有验证路径，测试2 流程）：改字 + 回车加段 + 退格并段 + Ctrl+Z/Ctrl+Y 撤销重做 → 保存 → 新版本生成（source=manual_edit）→ 下载 docx 用 python-docx 核验三类操作落盘
3. **回归**：只读模式批注高亮/引线/表格锚点不受影响；编辑模式选字批注（含表格内选字）正常提交；`anchor_start` 消歧定位不受影响

## 9. 改动文件清单

| 文件 | 改动 |
|------|------|
| `web/src/pages/c-chat/docx-paragraph-editor.tsx` | 新增：4 个自定义节点 + 编辑器组件 + diff 函数 + HighlightPlugin |
| `web/src/pages/c-chat/docx-view-utils.tsx` | 新增：表格高亮/清洗辅助函数（review-panel 与 AtomicBlockNode 共用） |
| `web/src/pages/c-chat/review-panel.tsx` | 可编辑分支替换为 `DocxParagraphEditor`；`collectPaperOps`（DOM diff）删除，改调 `collectEditorOps`；表格渲染改引共享辅助函数（只读静态分支保留原实现） |
| `web/src/services/flow-service.ts` | 零改动 |
| `web/src/pages/c-chat/flow/flow-ai-panel.tsx` | 零改动 |
| 后端 | 零改动 |

## 10. 已知取舍

- run 级格式（局部加粗/颜色）仍不保真——目标明确为交互升级，格式保真留待后续（Lexical 已为富文本格式预留扩展空间）
- 高亮边界在编辑期间可能暂时陈旧（与现状行为一致）
- 撤销栈跨「保存」不清空：保存后编辑器重挂载，栈自然清空，无需额外处理
