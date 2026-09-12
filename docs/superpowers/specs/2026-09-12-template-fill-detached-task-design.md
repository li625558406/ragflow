# 范本填写后台化与断连重连（方案A）设计

日期：2026-09-12 ｜ 状态：已实施（未部署） ｜ 关联：2026-09-08-template-fill-flow-design.md（进度流式）、2026-09-09-cancel-retrieval（取消）、2026-09-10-default-baseline（确认基线）

## 1. 背景与根因

C端对话/流程的范本填写由 SSE 连接驱动：`canvas_service.completion()` 的 `async for canvas.run(...)` 是唯一执行引擎，客户端断连（刷新/崩溃/关页）→ GeneratorExit → 画布中止 → 填写作废（无成稿）。而 B端 P2 已有**脱离连接的后台执行器**（`tpl_fill_task` + `rag/svr/template_fill/executor.py` daemon 线程，含检索/LLM产值/渲染/落库/retry），能力与画布节点同源但从未被对话链路复用。

## 2. 目标 / 非目标

**目标**
1. 填写执行与连接解耦：断连、刷新、浏览器崩溃后任务在服务器跑完，成稿落库可取。
2. 断连重连：刷新后进度卡与实时预览恢复为实时跟踪（不再只是静态回放）。
3. 正常在线路径行为不变（确认卡、进度事件、成稿卡、实时预览全部保持现状）。

**非目标**
- 不改 B端 fill-task 现有行为；不做多端同时观看同一任务的广播（拉模型即可）；
- 不做「增量重写 docx 文件」（成稿仍是完成后一次性渲染）。

## 3. 架构与数据流

```
画布 TemplateFill 节点（确认环节不变，内联）
  └─ 为每个选中范本创建 tpl_fill_task 行 + 起 executor daemon 线程（复用 _spawn_fill_task 模式）
  └─ 节点降级为「观察者」：轮询各任务 Redis 进度快照 + DB 状态 → 组装 template_fill_progress 事件（形状不变）
        │
        ├─ 在线：SSE 照常下发（前端零感知）
        └─ 断连：节点轮询随画布中止（无副作用），daemon 线程继续跑完 → 结果落 DB/storage
前端刷新 → 历史消息重放出 task_id 集合 → 轮询 GET /template/fill/fill-task/{id}/progress
        └─ running: 恢复实时进度+实时预览值；filled: 本地合成成稿卡（下载走既有 fill-task/download）
```

## 4. 后端设计

### 4.1 任务创建（`agent/component/template_fill.py`）
- `_fill_one` 的检索+产值循环改为委托：节点内为每个选中范本 `TplFillTaskService.insert(...)`（`source='canvas'`，flow 场景写 `flow_instance_id`），起 daemon 线程跑 `executor.execute_task`（抽取 `template_api._spawn_fill_task` 为可复用函数，两端共用）。
- 画布节点的既有输入（`direct_values` D−C 直通、`params`、确认后的基线差异）经 `params`/任务行传给 executor——executor 已支持 `build_values`/`_merge_default_values`，缺口在实施计划中逐项比对补齐。
- 节点轮询（async，间隔 1.5s，与确认轮询同量级）：读 Redis 快照 + DB 状态，组装与现在**完全同形**的 `filling/filled/failed` 事件；全部任务到终态后节点产出 `download`/`content` 输出（多范本仍各自产成稿）。
- 取消：画布被用户停止（TaskCanceledException）时，节点把未终态任务置 `cancelled`（复用取消 Redis 键，executor 探针既有）。

### 4.2 进度快照（`rag/svr/template_fill/executor.py`）
- `execute_task` 挂 `on_progress` 回调：每批后写 Redis `tpl_fill_progress:{task_id}` = JSON `{status, done, total, values, error}`，TTL 24h。终态（filled/failed/cancelled）也写一次后不再删（靠 TTL 过期）。
- `values` 为累积快照（非增量），单范本上限 623 键，体积 ~50KB，Redis 压力可忽略。

### 4.3 重连端点（`template_api.py`）
- `GET /template/fill/fill-task/{task_id}/progress`：owner 校验（同 get_fill_task）→ 合并 DB 行状态 + Redis 快照 → 返回 `{status, done, total, values, download, error}`。download 用既有 `/fill-task/{id}/download` 契约。
- 幂等只读，前端 2s 轮询；`status` 为终态即停止轮询。

### 4.4 成稿持久化（断连场景）
- 任务行 `result_file_id`/`values` 本就落 DB（权威）。
- 画布活着时成稿卡照旧由消息流落库；画布已死时**不做**线程回写会话消息（并发写 message JSON 有竞态风险）——刷新后由前端轮询合成成稿卡展示。卡片的下载/预览链接都指向 fill-task 端点，不依赖消息体。

### 4.5 事件契约扩展
- `filling/filled/failed/cancelled` 事件增加可选字段 `task_id`（confirm_pending 已有）。旧消息无此字段 → 重连逻辑不触发，完全兼容。

## 5. 前端设计

- `applyTemplateFillEvent`：记录 `t.task_id`（新增可选字段，归约已有浅拷贝换引用模式）。
- 新 hook `useTemplateFillTaskPoll`（template-fill-progress.tsx 内挂载）：
  - 仅历史恢复态（`!streaming`）且存在 `task_id` 的模板行进入 polling；
  - 拉 progress 端点：running → 更新 done/total + `values`（实时预览自动跟上）；终态 → 停轮询，filled 本地合成 download 展示成稿卡。
- 实时流式期间不轮询（SSE 为准）；SSE 与轮询同时产出时以 SSE 后到者为准（幂等合并，无状态冲突）。

## 6. 边界与对抗性场景

| 场景 | 行为 |
|---|---|
| 服务器重启，daemon 线程死、任务卡中间态 | progress 端点发现 `update_time` 超 10min 无变化且非终态 → 返回 `stalled`；前端显示「任务中断，可重试」（retry 端点既有） |
| 同一范本重复发起 | 节点创建前查 `exists_for` 非终态任务 → 复用该任务观察，不重复起线程 |
| Redis 不可用 | 快照写失败仅告警（executor 既有兜底风格）；轮询退化为读 DB 状态（无 values 实时值，进度数字仍更新） |
| 断连后任务失败 | 刷新后轮询见 failed + error → 红字展示，可 retry |
| 旧消息（无 task_id）| 走现有 templateFillEvents 静态回放，零变化 |
| 确认等待中断连 | 确认环节仍内联（画布死=确认作废），维持现状；P2 nonce 机制不变 |
| 多范本部分成功部分失败 | 各任务独立终态，事件逐范本下发，成稿卡逐个出现 |
| 越权 | progress/download 均 owner 校验（tenant_id 比对任务行） |

## 7. 测试要求

- executor：快照写入的对抗用例（Redis 异常、值超限、终态重复写）；pytest 既有 89 用例不回退。
- 端点：owner 越权 403、不存在任务 404、stalled 判定、Redis 挂掉降级。
- 节点：断连模拟（GeneratorExit 后任务终态落库）、取消置 cancelled、多范本混终态。
- 前端：`applyTemplateFillEvent` 对 task_id 的归约单测 + 轮询 hook 终态停轮询。
- E2E：发起填写 → 5s 内刷新 → 轮询恢复进度 → 完成看到成稿卡并可下载。

## 8. 工作量与风险

- 后端：任务委托改造（节点）、快照、端点、spawn 复用抽取 —— 约 5 个文件；前端：归约+轮询 hook+进度卡适配 —— 约 3 个文件。
- 主要风险：①节点与 executor 的参数/能力缺口（direct_values、evidence 口径）需实施时逐项对齐；②画布「观察者轮询」引入的任务行轮询频率对 DB 的压力（1.5s × 模板数，任务行有索引，量级可忽略）。
