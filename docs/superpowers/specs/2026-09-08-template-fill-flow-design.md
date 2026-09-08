# 模板填写进度流式 + 流程页签适配 设计文档

日期：2026-09-08
状态：已确认（用户批准）
前置：模板填写系统 P1-P3 已上线（见 CHANGE.md 2026-09-07/09-08 各条）；本设计 = 方案 A（进度流式，对话与流程共用）+ 方案 C 增量（流程版本内容注入轻端点）

## 1. 背景与目标

TemplateFill 画布节点（多范本、检索/产值/渲染）目前全程无过程反馈：多范本 3~8 分钟内前端零输出，成稿只在节点结束时一次性出现。用户两大诉求：

1. **进度流式**：选定范本 → 展示在 UI；LLM 逐批产值 → 进度可见；单范本成稿 → 即时预览/下载，不等其他范本。
2. **流程适配（主目标）**：在 C端「流程」页签的 AI 对话里自然语言触发模板填写，成稿确认后进入流程版本时间线。

非目标（明确排除）：
- LLM 逐字流式生成文档正文（破坏 docxtpl 模板保真度，与固定模板产品定位冲突）
- flow 后端原生模板填写环节动作（不加 flow_* 表、不加状态机节点）
- B端任务详情页改动（已有进度条）

## 2. 现状事实（调研结论）

- 流程页签 = c-chat 页内模块（`web/src/pages/c-chat/flow/flow-panel.tsx` / `flow-detail.tsx` / `flow-ai-panel.tsx`）；FlowAiPanel 复用 `use-send-message.ts` 的 SSE 管道与 `ChatInputBox`，agent_id 取 `localStorage('ragflow_agent_id')`。
- 流程后端（`api/apps/restful_apis/flow_app.py` + `api/db/services/flow_service.py`）与画布**零耦合**（无 canvas_id/dialogue_id，仅 FlowAiChat.session_id 弱关联）。
- FanOut 已验证完整事件管道：组件 `_event_queue`（asyncio.Queue）→ `canvas.py` run 循环 drain（当前**仅对 component_name=="fanout"**）→ `decorate(event, data)` 同名 SSE → 前端 `use-send-message.ts` 消费（`fanout_meta`/`message`/`node_finished` 等）。
- ReviewPanel（`c-chat/review-panel.tsx`）为对话与流程共用预览组件；c-chat 下载条目渲染 `msg.downloads`（`_extract_downloads` 契约：doc_id/filename/mime_type/url/name）。
- TemplateFill 组件（`agent/component/template_fill.py`）：`_invoke_async` 流程 = 选型 → `retrieve_all_shared` 共享检索 → `asyncio.gather` 并行 `_fill_one`（产值+渲染）；`_fill_one` 返回 `{doc_id, filename, mime_type, size}`，产物落 `{tenant_id}-downloads` bucket。**无任何进度上报。**
- 流程版本文件：FlowVersion blob 存 MinIO；`uploadVersionAsDocument`（flow-ai-panel.tsx）已有「版本 blob → document → ReviewPanel」通道。

## 3. 架构设计

### 3.1 进度事件通道（后端）

**TemplateFill 组件**新增 `self._event_queue: asyncio.Queue`（FanOut 同款），在 `_invoke_async` 关键节点推送：

| 时机 | event 名 | data 契约 |
|---|---|---|
| 选型完成 | `template_fill_progress` | `{stage:"selected", templates:[{template_id, name, slot_count}]}` |
| 每范本检索/产值推进 | `template_fill_progress` | `{stage:"filling", template_id, name, done, total}`（done/total 为该范本已填写槽位/总槽位，批次粒度） |
| 单范本渲染成功 | `template_fill_progress` | `{stage:"filled", template_id, name, download:{doc_id, filename, mime_type, size, url, name}}`（url/name 由组件直接按 Message._with_download_url 同款规则补齐） |
| 单范本失败降级 | `template_fill_progress` | `{stage:"failed", template_id, name, error}` |
| 全部完成/取消 | `template_fill_progress` | `{stage:"done"}` / `{stage:"cancelled"}` |

推送即 `await self._event_queue.put({"event": "template_fill_progress", "data": {...}})`，**不改 executor 内部逻辑**（进度回调从 `_fill_one`/`generate_values` 现有返回与批次循环处取数）。

**canvas.py drain 泛化**：现仅 `component_name.lower()=="fanout"` 时 drain，改为对任意组件检查 `getattr(cpn_obj, "_event_queue", None)`（FanOut 行为不变，向后兼容；事件名保持组件裸名）。

**is_canceled 检查点**：批次循环与范本并行循环内检查（复用 `check_if_canceled`），取消时推 `stage:"cancelled"` 后快速返回。

### 3.2 前端消费（两处共用）

`web/src/hooks/use-send-message.ts` 新增 `template_fill_progress` 事件分支，写入 streamState 的 `templateFill` 结构（当前范本卡片列表 + 各范本进度 + 已就绪 downloads），随 rAF 节流 flush 进消息流。

渲染层（c-chat `index.tsx` 与 `flow-ai-panel.tsx` 各自的消息渲染处，逻辑同构）：
- `stage:"selected"` → 范本卡片行（范本名 + 填写点数徽标）
- `stage:"filling"` → 进度行「《范本X》填写中 60/196」
- `stage:"filled"` → 立即追加该范本的下载条目（复用现成 downloads 渲染：文件名点击 ReviewPanel 预览 + 下载链接），不等其他范本
- `stage:"failed"` → 降级文案行（与现有汇总语义一致）

### 3.3 流程上下文注入（方案 C 增量）

新端点 `GET /flow/<flow_id>/version/<version_id>/content`（flow_app.py，权限：流程三角色可见）：
- 读 FlowVersion blob → 复用现有文档文本提取（与 `uploadVersionAsDocument` 的 document 解析同源逻辑，服务端化）→ 返回纯文本（截断上限 20000 字，与 sys.file_content 2000 字片段策略分层：本端点供画布上传通道用，不截 2000）。

FlowAiPanel 侧：对话里触发模板填写时（检测消息意图或让用户勾选「附当前版本」，实现取**随消息自动附带当前版本**——流程对话的默认上下文），前端取该端点内容组装为画布上传文件通道（`sys.file_content` 同源），TemplateFill 的 `_user_file_evidence` 现有逻辑自动将其预置证据首位。

### 3.4 成稿 → 流程版本时间线

复用 flow_app 现有「AI 回复存为新版本」链路（ai-record / 版本上传），扩展两点：
- 支持成稿 docx blob 直接落 FlowVersion（来源标注 `ai_template_fill`，source_type 与 AI 产出一致）
- FlowAiPanel 消息流中 `stage:"filled"` 的下载条目旁提供「存为流程版本」按钮 → 调上述链路（先预览确认，用户点了才落版本——已确认的交互）

## 4. 数据流（流程场景全链路）

```
处理人在流程 AI 面板说「帮我填XX范本」
→ FlowAiPanel 随消息附带当前版本文本（3.3 端点）
→ 画布 Begin → TemplateFill：
   ① 选型完成 → SSE: selected 卡片 → 聊天流渲染范本卡片
   ② 检索+产值 → SSE: filling 进度 → 进度行实时刷新
   ③ 单范本渲染 → SSE: filled + download → 即时预览(ReviewPanel)/下载条
   ④ 用户点「存为流程版本」→ docx 落 FlowVersion → 版本时间线
```

c-chat 主对话同样触发同画布时，①②③ 直接生效（共用管道），④ 在 c-chat 中不出现（无流程上下文）。

## 5. 错误处理

- 单范本失败：推 `failed` 事件 + 降级汇总行（现有 `_fill_one` 降级语义不变），其余范本照常
- 全部失败：`stage:"done"` 前推各 failed，节点按现有语义报错
- 取消：`check_if_canceled` 命中即推 `cancelled` 并快速返回
- 版本内容端点：blob 解析失败返回空文本 + warning 日志（不阻断填写，回退纯 KB 检索）
- 「存为流程版本」失败：前端 toast，聊天流成稿条目保留可重试

## 6. 测试策略

- 后端组件单测：事件推送序列契约（selected→filling→filled/done 顺序与 data 完整性）、取消推 cancelled、单范本失败推 failed 不中断
- canvas drain 单测：非 FanOut 组件带 `_event_queue` 被 drain、FanOut 行为回归不变
- flow 端点测试：三角色权限、blob 解析失败回退、截断上限
- 前端：hook 事件分支对齐 FanOut 既有测试模式（如无可复用模式则以 hook 单测最小验证 streamState 变化）
- 端到端：容器内冒烟（事件经 SSE 到达）+ 用户画布实测

## 7. 改动文件清单（预估）

| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `agent/component/template_fill.py` | _event_queue + 5 类事件推送 + 取消检查 |
| 后端 | `agent/canvas.py` | drain 泛化（~5 行） |
| 后端 | `api/apps/restful_apis/flow_app.py` | 版本内容端点 + 成稿落版本扩展 |
| 前端 | `web/src/hooks/use-send-message.ts` | template_fill_progress 事件分支 |
| 前端 | `web/src/pages/c-chat/index.tsx` | 卡片/进度/成稿条渲染 |
| 前端 | `web/src/pages/c-chat/flow/flow-ai-panel.tsx` | 同上 + 附带当前版本 + 存为流程版本按钮 |
| 测试 | `test/test_template_fill_events.py` 等 | 新增 |
