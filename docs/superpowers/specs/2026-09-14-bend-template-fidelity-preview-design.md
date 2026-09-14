# B端范本预览保真渲染设计（双模式）

日期：2026-09-14 ｜ 状态：已确认（双模式切换；xlsx 不动）

## 1. 问题与约束

B端范本详情页「模板预览」当前是纯文本段落列表（docx-preview 前的旧链路），字号/加粗/颜色/表格/排版全部丢失。用户要求预览格式与源文档一致。

第一性原理约束：C端范本预览保真（2026-09-11）已验证 docx-preview 渲染链路，基建可复用（`docx-highlight.ts` + `applyDocxPageLazy`）。但 B端预览独有「划选标记填写点」功能（划选文字 → 记为候选，带 addr），依赖纯文本段落 DOM；保真 DOM（docx-preview）中页眉/页脚/文本框渲染不完整，划选无法可靠映射回 addr——**不能直接替换，否则功能回退**。

用户裁定：**双模式切换**（保真默认 + 文本模式做划选标记），与 C端文件审核「编辑文档」显式切换同款交互先例；xlsx 预览不动。

## 2. 方案

### 2.1 保真视图（默认，仅 docx）

新组件 `web/src/pages/template-fill/fidelity-preview.tsx`：

- 拉取 **original 原件** blob：`request.get(api.downloadTemplateFill(id, 'original'), { responseType: 'blob' })`。注意与 C端 `useTemplateFillFile`（拉 render 工作副本、含 {{key}}）不同——B端预览目的是看源文档原貌；原件错误体是 JSON（code != 0）时解析 message 抛错（照 `downloadTemplateFillResult` 口径）。
- `renderAsync(blob, container, undefined, { inWrapper: true, breakPages: true })` + `applyDocxPageLazy` 屏外页懒渲染。
- 已注册填写点高亮：`highlightDocxRanges(container, placeholders.map(...))`——anchor 文本归一化匹配，琥珀色 `#f59e0b` 下划线标注。已知边界：按文本全局匹配（同形文本取首处），不按 addr 精确定位；页眉/页脚/文本框内 anchor 若 docx-preview 未渲染则不高亮（静默，右侧配置表仍可见）。
- 渲染失败 / blob 拉取失败 → `onRenderFailed()` 回调，父组件自动切文本模式 + 一次性 warning 提示。

### 2.2 双模式切换（`detail.tsx` docx 分支）

- 模式 state：`'fidelity'`（默认）| `'text'`；预览卡头切换按钮「文本模式」/「保真模式」。
- text 模式 = 现有段落列表 + 划选标记（**零改动**，含 handleParaMouseUp / mark 按钮）。
- fidelity 模式 = 新组件，无划选（底部说明「划选标记请切换文本模式」）。
- 渲染失败自动降级 text 模式。
- xlsx 分支维持现状（保真切换按钮不出现）。

## 3. 不做

- 不动 `use-template-fill-request.ts`（original 拉取逻辑内聚在新组件）。
- 不动 `docx-highlight.ts`（B端组件跨页 import，与 C端 live-preview 同款引用方式）。
- xlsx 不保真；不做保真视图内划选；不做 addr 级精确高亮。

## 4. 验收

1. `npm run build` 通过。
2. docx 范本：默认保真视图版式与源文档一致；已注册填写点 anchor 琥珀色高亮。
3. 切文本模式：划选标记填写点功能完好。
4. 渲染失败自动降级文本模式。
5. xlsx 范本预览不受影响。

## 5. 部署

纯前端：`detail.tsx` + 新组件（build + dist SCP）。
