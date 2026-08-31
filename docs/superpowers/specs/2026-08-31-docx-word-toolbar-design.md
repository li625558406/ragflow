# 文件审核弹框：Word 式工具栏（格式保真落盘）— 设计文档

日期：2026-08-31
状态：设计已确认，待实施
关联：`docs/superpowers/specs/2026-08-31-docx-lexical-editor-design.md`（Lexical 编辑器，已上线）、CHANGE.md 2026-08-31

## 1. 背景与目标

文件审核弹框的正文编辑已是 Lexical 整篇编辑（撤销/重做/IME 安全/回车分段），但无格式能力：无工具栏，保存契约为「整段纯文本替换」，run 级格式全部丢失。

**目标（用户确认）**：
- 编辑器顶部增加 Word ribbon 简化风工具栏，控件全面（约 15 组）、质感高级（项目色系自绘、tooltip、激活态）
- 工具栏套用的格式（加粗/颜色/字号/字体等）**保存后写入 docx 新版本**（后端契约扩展 + python-docx 逐 run 写入）
- 「已修改 N 处 + 保存/放弃」吸顶保存栏整合进工具栏右侧

**非目标**：
- 插入表格/图片、查找替换、行距（二期）
- 独立文档服务（OnlyOffice 等，已否决）
- 只读模式（静态渲染分支）出现工具栏

## 2. 方案选型结论

| 方案 | 结论 |
|------|------|
| **A. 自研工具栏 + Lexical 命令层 + 后端 runs 契约（选定）** | shadcn/radix 组件拼装，质感 100% 可控，前端零新增依赖，后端契约向后兼容 |
| B. 移植 lexical-playground ToolbarPlugin | 深度依赖 playground 自定义节点体系，剥离成本高；样式难贴项目色系；后端链路一点不省 |
| C. OnlyOffice 等重型文档服务 | 已否决（独立服务部署重） |

## 3. 整体架构与文件清单

```
前端（web/src/pages/c-chat/）
├── docx-toolbar.tsx          新增：工具栏 UI + 命令 dispatch + 激活态（portal 到纸张上方吸顶容器）
├── docx-format-utils.ts      新增：style 串解析/合并、runs 抽取、格式签名（纯函数，可单测）
├── docx-paragraph-editor.tsx 修改：LexicalComposer 内挂 DocxToolbar（portal）+ readEditorBlocks 抽 runs
├── docx-diff.ts              修改：EditorBlock 增 runs + fmtSig；diff 升级「文本+格式」双比较
├── docx-diff.test.ts         修改：新增 runs/fmtSig 用例
├── review-panel.tsx          修改：删除旧吸顶保存栏（整合进工具栏右侧）；保存 payload 增 runs
└── flow-service.ts           修改：editFlowDocument 增加 runs 字段转换（camel→snake）

后端
├── api/apps/restful_apis/flow_app.py   修改：/document/edit 解析可选 runs
└── api/db/services/flow_service.py     修改：python-docx 逐 run 写入；无 runs 兜底走现有整段替换
```

## 4. 工具栏 UI（Word ribbon 简化风）

吸顶在纸张上方，仅编辑模式渲染；浅灰底 + 白面板 + 分组分隔线；全部控件带 tooltip 与激活态高亮（项目蓝 #1a66fb）。

| 分组 | 控件 | 实现方式 |
|------|------|----------|
| 历史 | 撤销 / 重做 | HistoryPlugin undo/redo 命令 |
| 样式 | 正文 / H2 / H3 / H4 下拉 | 节点 replace（DocxParagraphNode ↔ DocxHeadingNode）；改原文段标题层级 = edit 原段 |
| 字体 | 字体族下拉：宋体/黑体/楷体/仿宋/微软雅黑/Times New Roman/Arial | TextNode `__style` 串（font-family） |
| 字号 | 字号下拉：中文字号（初号42pt…五号10.5pt…九号9pt 常用档） | style 串（font-size, pt） |
| 格式 | B / I / U / S / 上标 / 下标 | `FORMAT_TEXT_COMMAND` 内建 format 位 |
| 颜色 | 字体颜色 / 背景高亮（Popover 色板 + 清除） | style 串（color / background-color） |
| 段落 | 左/中/右/两端对齐、有序/无序列表、缩进增/减 | `FORMAT_ELEMENT_COMMAND` |
| 操作 | 清除格式 | format 位清零 + style 清空 |
| 右侧 | 「已修改 N 处」胶囊 + 放弃修改 + 保存（保存中禁用） | 替代旧吸顶保存栏 |

- **激活态**：订阅 selection 变化（防抖 100ms），`editor.read` 读当前 format 位与 style 串高亮按钮；无选中/无焦点时格式命令按钮置灰，历史与段落级命令保持可用
- **挂载形态**：DocxToolbar 在 `LexicalComposer` 内渲染（需要 editor context），经 React portal 输出到 review-panel 提供的吸顶容器 DOM（沿用 flow-detail commentPortal 的 portal 模式）
- **批注高亮共存**：背景高亮命令对 HighlightTextNode（批注 UI）跳过，保持批注锚点视觉稳定；粗/斜等 format 位正常应用

## 5. runs 抽取与 diff 升级

**EditorBlock 扩展**：
```ts
interface EditorBlock {
  paraIndex?: number;
  kind: 'text' | 'table' | 'image';
  text: string;
  runs?: Run[];      // 新增：仅 text 块
  fmtSig?: string;   // 新增：runs 样式签名（JSON 串），文本相同时比较用
}
interface Run {
  text: string;
  bold?: boolean; italic?: boolean; underline?: boolean; strike?: boolean;
  superscript?: boolean; subscript?: boolean;
  color?: string;    // #RRGGBB
  bg_color?: string; // #RRGGBB
  font?: string;     // 字体族名
  size?: number;     // pt
}
```

**抽取规则**（readEditorBlocks）：
- 遍历块内 TextNode：format 位读 `getFormat()`；font/color/size/bg 从 `__style` 串解析（docx-format-utils.parseStyle/mergeStyle）
- 相邻 run 样式相同则合并
- **HighlightTextNode**：剔除其 background（UI 标注不落盘）；format 位正常导出
- runs 全默认（无任何格式）→ 不携带 runs/fmtSig，行为与旧版完全一致

**diffBlocks 双比较**：
- 文本不同 → edit（带 runs，若非全默认）
- 文本相同但 fmtSig 不同 → edit（纯改格式也触发保存）
- deletes/inserts 逻辑不变；inserts 同样可带 runs

## 6. 契约与后端写入

**请求契约**（`POST /flow/<flow_id>/document/edit`，向后兼容）：
```json
{
  "edits":   [{ "para_index": 0, "new_text": "…", "runs": [ { "text": "加粗字", "bold": true, "color": "#FF0000", "size": 14, "font": "黑体" } ] }],
  "inserts": [{ "after_para_index": -1, "new_text": "…", "runs": [...] }],
  "deletes": [1]
}
```

**python-docx 写入**（flow_service 新增 `_apply_runs` / service 层等价函数）：
- 清空段落现有 run → 逐 run `add_run(text)` 按属性写入
- bold/italic/underline → `run.bold` 等；strike → `run.font.strike`
- superscript/subscript → `run.font.superscript / subscript`
- color → `run.font.color.rgb`（正则 `^#[0-9A-Fa-f]{6}$` 校验，非法 400）
- font → `run.font.name` + `rPr.rFonts w:eastAsia`（中文字体必须设 eastAsia）
- size → `run.font.size = Pt(n)`
- **兼容**：runs 缺省/空 → 现有「整段替换保留首 run 样式」路径不动
- 约束沿用：≤200 处、单段 ≤20000 字、全部定位成功才动手、存为新版本 source=manual_edit

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| 颜色值非法 | 前端色板只出合法值；后端正则兜底 400 |
| runs 畸形 JSON | 400 `runs 格式非法`，前端显示现有 editError |
| 后端为旧版本（未部署新契约） | 忽略未知字段 → 格式丢失但文本正常保存（降级不报错） |
| 工具栏点击无选区 | 格式命令按钮置灰；段落级/历史命令保持可用 |
| 保存失败 | 现有 editError 机制不变，编辑器内容保留 |

## 8. 测试

1. **单测**（docx-format-utils / docx-diff.test.ts）：style 串解析与合并、runs 抽取剔除批注高亮背景、相邻同样式 run 合并、fmtSig 变化触发 edit、全默认格式不携带 runs
2. **后端冒烟**：构造带 runs 的 edits 调 `/document/edit` → 下载 docx 用 python-docx 断言 bold/color/size/font(eastAsia)/superscript 落盘
3. **浏览器 E2E**（dev 9222，测试2 流程）：工具栏改字 + 加粗 + 变色 + 改字号 + 对齐 → 激活态正确 → 保存 → 新版本 → 下载核验格式落盘；旧纯文本编辑回归；只读模式无工具栏

## 9. 已知取舍

- 段落级格式（对齐/缩进/列表）保存到 docx 由段落 pPr 承载：本期 python-docx 写 run 级格式，段落对齐写入 `paragraph.alignment`；列表/缩进落盘为后续增强（编辑期间视觉正确，落盘暂按普通段落）
- 字号下拉用 pt 值，不还原 Word 中文字号名称映射的复杂对应
- 批注锚点 anchor_start 在格式改写后可能漂移（与现状一致，靠容差消歧）
