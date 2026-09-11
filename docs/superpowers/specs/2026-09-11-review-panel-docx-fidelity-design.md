# 文件审核 docx 保真渲染 + 范本预览性能优化 设计

- 日期：2026-09-11
- 状态：设计完成，待实施
- 关联：`2026-09-11-template-preview-docx-fidelity-design.md`（范本预览保真，本设计在其上做性能优化 + 复用高亮技术）

## 1. 背景与问题

1. **范本预览性能**：大文档（页数多/正文长）时 docx-preview 渲染后交互卡顿。两个主因：① SSE filling values 每次变化触发整树 `innerHTML` 快照重放 + 全文重扫重涂；② 所有分页 section 全量参与布局/绘制。
2. **文件审核格式**：`review-panel.tsx` 正文按 `/files/{id}/content` 段落 JSON 渲染（SimSun 模拟纸张），Word 真实字号/字体/颜色/表格样式丢失。用户要求适配原格式渲染，**批注功能必须保持**。

## 2. 已确认的决策

| 决策点 | 结论 |
|--------|------|
| 性能优化 | 增量高亮（values 变化只改已有 span）+ 分页 `content-visibility: auto` |
| 审核保真范围 | **只读路径**（c-chat 三处 + flow 版本查看，共用组件天然全覆盖） |
| 编辑模式 | **保持旧段落视图**（Lexical diff 模型依赖段落 JSON，docx-preview DOM 无对应模型；已拍板） |
| 批注功能 | AI 标注高亮/批注栏卡片/SVG 引线/点击互定位/手动批注增删/下载标注文档 全部保留 |
| 后端 | **零改动**：`GET /files/<file_id>`（file_api.py:279）已返回原始 blob |

## 3. 方案

### 3.1 性能优化（docx-highlight.ts + template-fill-live-preview.tsx）

- `applyDocxHighlight` 返回 `Map<key, HTMLSpanElement[]>`；新 `updateDocxHighlight(spans, values)` 就地更新 span 文本/样式（已填⇄未填双向切换），values 变化**零 DOM 重建**
- 新 `applyDocxPageLazy(container)`：渲染后给每个分页 `<section>` 设 `content-visibility: auto` + `contain-intrinsic-size: 794px 1123px`（屏外页跳过布局绘制）
- pristine 快照仅在 blob 重渲染时使用；组件 values effect 改调 updateDocxHighlight

### 3.2 审核保真（review-panel.tsx 只读分支）

- 条件：`content.file_type === 'docx'` 且非编辑态 → 新 hook `useFileBlob(fileId)` 拉 `GET /files/{id}` blob → `renderAsync` 渲染进正文列
- **标注/批注锚定**：现有 railItems 构建逻辑（段落文本匹配 + key 体系）不动；新增 `highlightDocxRanges(el, items)` 在 docx DOM 上按文本（去空白归一化 + anchor_start 消歧）Range 插入 `<mark data-anchor-key>`（严重度配色 + cursor-pointer）；与占位符同款「拼接全文 → 定位 → 从后往前替换」技术
- **防跨块误删**：匹配起止 `closest('p')` 不同则跳过（防 Range.deleteContents 破坏段落结构）→ 该项落未定位兜底
- 批注栏 measure 直接命中 `mark[data-anchor-key]`（复用现有查询），SVG 引线/卡片/互点击不变；定位失败的项过滤出 rail 归入 unmatched（新增 markedKeys 状态参与 railItems 过滤 + measure 依赖）
- 手动批注：选中文本悬浮入口照旧（docx DOM 无 `data-para-index` → anchor_para/anchor_start 为 null，仅锚 anchor_text，同现有可空契约）；已有批注同款 mark 高亮
- 容器点击委托 `mark[data-anchor-key]` → handleAnchorClick（与表格分支同款）
- 非 docx / blob 失败 / renderAsync 抛错 → 降级现段落渲染（轻提示）
- 下载标注文档、统计条、多文件 tab 等零改动

## 4. 文件改动清单

| 文件 | 改动 |
|------|------|
| `web/src/pages/c-chat/docx-highlight.ts` | applyDocxHighlight 返回 spans map + updateDocxHighlight + applyDocxPageLazy + highlightDocxRanges（审核用文本高亮） |
| `web/src/pages/c-chat/template-fill-live-preview.tsx` | 接增量更新 + page lazy |
| `web/src/utils/api.ts` | + `getFileBlob(id)` |
| `web/src/hooks/use-file-blob.ts` | 新建：blob 拉取 + JSON 错误体检测 |
| `web/src/pages/c-chat/review-panel.tsx` | 只读 docx 保真分支 + markedKeys 过滤 + 容器点击委托 + 降级 |

## 5. 边界与错误处理

| 场景 | 行为 |
|------|------|
| matched_text 找不到/跨段落 | 不插 mark → 项入 unmatched 兜底列表（现有 UI） |
| 同文本多处出现 | comment 用 anchor_start 容差优先；否则首个出现 |
| values 空串 | span 切回虚线槽位（双向） |
| 切换文件（fileId 变） | content/blob refetch（queryKey 含 id）→ 重渲染重锚定 |
| xlsx/pdf/doc 旧格式 | 走现段落渲染，零回归 |
| 编辑模式切只读 | loadedFileId===fileId 时 Lexical 分支优先，保真不介入 |

## 6. 验证方案

1. 大范本（百页级）填写：filling 事件到达不再卡顿；滚动顺滑；高亮实时更新正确
2. c-chat 文件审核：docx 字号/字体/表格保真；AI 标注高亮 + 批注栏 + 引线 + 点击互定位正常
3. 手动批注：选中正文 → 添加 → 展示/删除正常；重复文本锚定回看正常
4. flow owner 版本审阅：编辑态仍是旧段落视图 + 保存新版本链路不回归
5. 非 docx 文件、损坏 blob → 降级 + 轻提示
6. 成稿预览/inline 审阅（共用组件）行为一致

## 7. 部署

纯前端 build + SCP + nginx reload（后端零改动）。
