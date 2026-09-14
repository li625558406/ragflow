# 范本填写确认卡填写项点击定位跳转 — 设计文档

- 日期：2026-09-14
- 状态：设计完成，未实施
- 入口：C端流程页签 AI 面板确认卡（与 c-chat 对话页共用同一套组件，一处改动两端生效）

## 1. 背景与目标

范本填写挂起确认时（`confirm_pending` SSE），前端渲染「填写字段确认」卡片
（`template-fill-confirm-card.tsx`）：每个范本列出全部 LLM 填写点候选
（checkbox + 字段名 + 默认值 + 直填输入框），用户调整后点「确认并继续填写」。

现状痛点：用户面对一串字段名（如「项目名称」「投标保证金」），不知道它们在
范本文档中的哪个位置、上下文是什么，只能凭名字猜着勾选/直填。

本次目标：确认卡中每个填写项的**字段名文字可点击**，点击后打开「查看填写内容」
预览抽屉并**滚动定位到文档中该占位符的位置**（滚动居中 + 蓝色 outline 闪烁 2s）。

非目标：定位后自动勾选/回填任何决策；后端任何改动。

## 2. 现状要点（依赖事实）

- 上一功能「未填充汇总与定位跳转」（已部署 2026-09-14）已打通完整定位链路：
  `TemplateFillProgress` 的 `liveTarget` state（`{template_id, focusKey?}`）→
  `TemplateFillLivePreview` 的 `focusKey` prop → `focusPlaceholder` 按
  `data-ph-key` 查 span 滚动闪烁。本设计**纯前端接线复用，后端零改动**。
- 确认卡渲染在 `TemplateFillProgress` 内部（`template-fill-progress.tsx:58-67`），
  父组件已持有 `setLiveTarget` 与 LivePreview 实例——只需给卡片传回调。
- 确认阶段范本行为 selected 状态，「查看范本」预览本来就可用（未开填时占位符
  显示为虚线待填槽位），定位目标必然存在。
- 候选结构 `ITemplateFillCandidate = {key, name, default_value}`，`key` 与
  docx span 的 `data-ph-key` 同一命名空间（detector 校验 `[a-z][a-z0-9_]*`）。

## 3. 方案选型

| 方案 | 结论 |
|---|---|
| **A. 父组件传回调（采纳）** | 复用既有 liveTarget/LivePreview 生命周期；卡片保持纯展示、不知道预览存在；改动 2 文件各几行 |
| B. 卡片自管预览 | 确认卡自己维护 liveTarget 并渲染第二个 LivePreview 实例——抽屉重复渲染、与成稿行预览互相抢占，状态两份 |
| C. 全局 store/Context | liveTarget 提升到 Zustand/Context——为一个调用点引入全局状态，过度设计 |

## 4. 前端设计（纯前端，2 文件）

### 4.1 父组件（`web/src/pages/c-chat/template-fill-progress.tsx`）

给 `TemplateFillConfirmCard` 传新 prop：

```tsx
      <TemplateFillConfirmCard
        key={...}
        pending={mergedState.pendingConfirm}
        onSubmitted={onConfirmSubmitted}
        onLocate={(templateId, key) =>
          setLiveTarget({ template_id: templateId, focusKey: key })
        }
      />
```

其余零改动——`liveTarget`/`liveTpl`/`TemplateFillLivePreview` 生命周期原样复用。

### 4.2 确认卡（`web/src/pages/c-chat/template-fill-confirm-card.tsx`）

- 新增可选 prop `onLocate?: (templateId: string, key: string) => void`。
- 候选行字段名 `<span>` 改为 `<button type="button">`：
  - 可点击样式：hover 出虚线下划线（`hover:underline decoration-dotted underline-offset-2`），
    文字色保持既有逻辑（勾选/直填黑色 `#000000`、未勾选灰 `#8C8C8C` + 划线）——
    划线语义是「不交给 AI 填」，与定位无关，二者不冲突；
  - `title="定位到文档中的「{name}」"`；
  - `onClick` 调 `onLocate?.(t.template_id, c.key)` 并 `e.stopPropagation()`。
- `onLocate` 未传时字段名保持纯文本 span（向后兼容其他调用点）。
- 已提交态 / 超时态：保持现有灰/蓝字只读单行渲染，**不加**定位入口
  （这两个态已无字段列表）。

## 5. 边界与降级

| 场景 | 行为 |
|---|---|
| 正常确认阶段 | 点字段名 → 抽屉打开 → docx 保真渲染完成后滚动居中 + `#1a66fb` outline 闪烁 2s |
| 抽屉已开时重复点同一字段 | 不重复滚动（`focusDoneRef` 防重）；关闭抽屉再点重新定位（组件重挂载重置） |
| key 不在文档中 | 静默跳过，抽屉照常打开（既有 focusPlaceholder 语义） |
| xlsx / docx 渲染降级 | 走 `data-ph-key` 文本路径定位（既有兜底） |
| `onLocate` 未传 | 字段名纯文本，行为与现在完全一致 |
| 划线（未勾选）字段 | 仍可点击定位——点开看内容再决定勾不勾，合理 |

## 6. 测试

1. jest（确认卡）：点击字段名触发 `onLocate` 且参数为 `(template_id, key)`；
   `onLocate` 未传时字段名不渲染为 button。
2. 定位链路本身（focusPlaceholder / focusKey effect / data-ph-key）已被
   未填充汇总功能覆盖，不重复测试。
3. 手工冒烟：发起含默认值字段的范本填写 → confirm_pending 卡出现 → 点字段名 →
   抽屉定位闪烁 → 关闭抽屉再点同字段 → 重新定位。

## 7. 部署

纯前端 2 文件：`npm run build` + tar + nginx reload。**后端零改动、无需重启、
无数据库/Redis 变更**；旧前端无此入口，新前端不依赖任何新后端字段，无互锁。
