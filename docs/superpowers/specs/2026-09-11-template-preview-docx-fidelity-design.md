# 范本预览 Word 格式保真渲染设计（docx-preview）

- 日期：2026-09-11
- 状态：设计完成，待实施
- 关联：`docs/superpowers/specs/2026-09-07-template-fill-design.md`（范本填写系统）、`docs/superpowers/specs/2026-09-08-template-fill-flow-design.md`（实时预览抽屉）

## 1. 背景与问题

C端「查看范本 / 实时预览 / 查看填写内容」共用组件 `web/src/pages/c-chat/template-fill-live-preview.tsx`，渲染的是后端 `/template/fill/<id>/preview`（template_api.py:474）返回的**纯文本段落条目**（`iter_docx_paragraphs`）。Word 的字号、加粗、颜色、表格、页面排版在数据源头就已丢失，用户明确要求：查看范本时文本内容渲染不能丢失 Word 格式（文字大小、文字格式等）。

**现成出口**：`/template/fill/<id>/file?kind=original|render`（template_api.py:503，`login_required`）返回原始 docx 二进制；前端 `api.downloadTemplateFill(id, kind)`（utils/api.ts:475）已有 URL 封装。

## 2. 已确认的决策（用户拍板）

| 决策点 | 结论 |
|--------|------|
| 渲染方案 | **docx-preview**（前端 `renderAsync` 直接渲染原始 docx），保真度最高 |
| AI 填入高亮 | **保留**：蓝色填入值 + 虚线待填槽位，改为 DOM 后处理实现 |
| 改动侧 | **纯前端**，后端零改动 |
| 范围 | C端实时预览抽屉（c-chat 与 flow 共用）；xlsx 分支保持现状；B端范本库详情页（template-fill/detail.tsx）本次不动 |

## 3. 技术要点

### 3.1 数据链路

```
原始 docx blob ← useTemplateFillFile(id) ← GET api.downloadTemplateFill(id, 'original')（responseType: blob）
                                        └─ JSON 错误检测复用 downloadTemplateFillResult 模式（blob.type 含 application/json → parse message 抛错）
file_type ← 沿用现有 useTemplateFillPreview(id)（仅取 file_type 字段；xlsx 分支继续用它返回的 items 渲染）
```

### 3.2 渲染与快照

- 挂载 / 范本切换（template_id 变化）时：fetch blob → `renderAsync(blob, containerEl)`（docx-preview，默认选项即保真：分页、页眉页脚、字号、表格）
- 渲染成功后把 `containerEl.innerHTML` 存入 ref 作为 **pristine 快照**
- `values` 变化时（SSE filling 事件，频率低）：`containerEl.innerHTML = pristine 快照` 重放 → 执行高亮后处理。不做增量 diff，整段替换简单可靠
- docx-preview 样式由库注入 `<style>`，快照重放不影响样式层

### 3.3 高亮后处理（核心难点：占位符跨 run 拆分）

Word 内部 `{{placeholder}}` 常被拆成多个 `w:r` run，docx-preview 渲染后对应多个相邻 `<span>`。处理算法：

```
applyHighlight(container, values):
  1. TreeWalker(NodeFilter.SHOW_TEXT) 收集全部文本节点（有序）
  2. 拼接全文 fullText，同时记录每个节点在 fullText 中的 [start, end) 偏移
  3. PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g 扫描 fullText（与后端同口径）
  4. 对每个匹配 [s, e)：
     - 用 document.createRange() 定位：先按偏移映射找到起始/结束节点与节点内偏移
       （边界情形：匹配起止落在节点边界时顺移到相邻节点内 0/长度处，保证 range 端点合法）
     - range.deleteContents() 后 range.insertNode(替换 span)：
       · key 有非空值 → 蓝色值 span：bg #EFF4FF、text #1a66fb、rounded、
         title=key；**不强制字号**（继承 Word 上下文字号，区别于旧版 text-xs 硬编码）
       · 未填 → 虚线槽位 span：dashed border #1a66fb/60、bg #EFF4FF、显示 key、
         title="key（等待 AI 填入）"
     - 匹配间 lastIndex 推进；Range 端点基于修改前的偏移映射一次性算好，
       从后往前替换（或每次替换后重扫）均可——采用「从后往前」避免偏移失效
```

约束与已知边界：
- 占位符跨**段落/表格单元格**不存在（docxtpl 约束占位符在单段落内），只处理同一段落内跨 run 的情形
- `deleteContents` 跨多个 inline span 后可能留下空 span，无视觉影响
- 空串值 = 未填（同旧版口径：`v !== undefined && v !== ''`）
- 值内容为纯文本（textContent 赋值），无 XSS 面

### 3.4 降级兜底

blob fetch 失败或 `renderAsync` 抛错（文件损坏等）→ 降级回**现有纯文本段落渲染**（items 链路），并在顶部展示轻提示。preview items 本来就还在拉（file_type 来源），降级零额外成本。

## 4. 文件改动清单

| 类型 | 文件 | 改动 |
|------|------|------|
| 依赖 | `web/package.json` | + `docx-preview` |
| hook | `web/src/hooks/use-template-fill-request.ts` | + `useTemplateFillFile(id)`（blob 拉取 + JSON 错误检测 + enabled 控制） |
| 组件 | `web/src/pages/c-chat/template-fill-live-preview.tsx` | docx 分支重写：renderAsync + 快照重放 + applyHighlight；降级兜底；xlsx 分支与抽屉外壳不动 |
| 工具 | 高亮后处理函数放同文件或 `web/src/pages/c-chat/docx-highlight.ts` | `applyDocxHighlight(container, values)` 纯函数 |

## 5. 边界与错误处理

| 场景 | 行为 |
|------|------|
| 范本切换（liveTplId 变） | blob refetch + 重新 renderAsync + 快照重建 |
| SSE values 批量更新 | 快照重放 + 重涂（整段 innerHTML 替换，频率低，性能可接受） |
| docx 文件损坏 / renderAsync 抛错 | 降级纯文本渲染 + 顶部轻提示 |
| blob 返回 JSON 错误体 | parse message 抛错 → 同上降级 |
| 模板含图片 | docx-preview 以 base64 内嵌渲染，天然支持 |
| 抽屉半屏宽 vs A4 页宽 | 容器 `overflow-auto`，页面水平滚动可看全；不做缩放（保真优先） |
| Esc 关闭 / 布局腾位联动 | 现有外壳逻辑零改动 |

## 6. 验证方案

1. **基础保真**：上传带字号/加粗/颜色/表格/页眉的 docx 范本 → 预览打开与 Word 打开观感一致
2. **实时高亮**：发起填写 → filling 事件到达 → 对应槽位变蓝色值（继承上下文字号）；未填槽位虚线
3. **对抗性**：占位符手工在 Word 中拆成多段 run（如选中 `{{ph}}` 中间几个字符改色）→ 仍能整段命中替换；空值 key → 虚线槽位；损坏 docx → 降级文案渲染；xlsx 范本 → 走旧渲染不回归
4. **双入口**：c-chat 对话页与 flow AI 面板两处入口行为一致（共用组件，天然一致）
5. **刷新回放**：filled 后「查看填写内容」回看蓝色值正常（values 经 template_fill_events 重放）

## 7. 部署

纯前端改动：`npm run build` → tar → SCP → `rm -rf dist/*`（保 inode）→ 解压 → nginx reload。需先 `npm install docx-preview`（外网依赖，走 10808 代理）。
