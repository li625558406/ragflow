# 范本填写取消链路补全 + 检索并发限流 设计文档

日期：2026-09-09
状态：已确认（用户批准）
前置：模板填写进度流式已上线（2026-09-08/09 部署）；本次为 2026-09-09 流程页签实测暴露的三个缺口收口

## 1. 背景与目标

2026-09-09 实测：流程页签触发百级填写点范本（招标文件专用本）填写，检索并发 6 路 × 大 KNN 查询（单次 22s→244s 持续恶化）打满磁盘（load 10.86、iowait 7-8%、IO 阻塞进程 7-8 个），用户无法停止——最终靠重启容器收场。三个缺口：

1. **前端「停止」不取消服务端**：`stopOutputMessage` 仅 abort 本地 SSE；服务端有现成取消键机制（Redis `{task_id}-cancel`，`canvas.is_canceled()` 轮询）但无暴露入口。
2. **取消检查点太粗**：只在组件入口/单范本入口/gather 后三处检查，批次产值循环与检索循环内无检查——设置取消键后仍要等当前范本全部批次跑完。
3. **检索无独立限流**：画布检索并发 6 路（RETRIEVAL_CONCURRENCY）是为 B 端槽级小查询调的；画布场景单查询是分钟级大 KNN，6 路并发互相拖慢。

非目标：不做服务端强制杀线程/取消在途 LLM 请求（asyncio 语义下不现实）；不改 B 端任务管道的并发默认值。

## 2. 设计

### 2.1 取消原语下沉（executor.py）

- 模块级新异常 `GenerateCancelled(Exception)`。
- `generate_values` / `retrieve_all_shared` / `_retrieve_all` 均加可选参数 `should_cancel: Callable[[], bool] | None = None`：
  - 检查点：`generate_values` 的 `_one` 获得信号量后 + `as_completed` 收集循环每轮；`retrieve_all_shared`/`_retrieve_all` 的 `_one` 获得信号量后。
  - 命中即抛 `GenerateCancelled`，向上传播由调用方捕获。
  - `should_cancel=None`（B 端 execute_task 默认）行为完全不变。

### 2.2 组件接线（template_fill.py）

- `_invoke_async` 调 `retrieve_all_shared` 与 `_fill_one`（其内 `generate_values`）时传 `should_cancel=lambda: self.check_if_canceled("...")`。
- `except GenerateCancelled` → 转现有 `_FillCancelled`（cancelled 事件推送/汇总逻辑复用，不改）。
- **检索限流**：模块级 `_RETRIEVAL_CONCURRENCY = int(os.getenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "2"))`，画布侧建共享信号量经新可选参数 `sem` 传入 `retrieve_all_shared`（惯例同 `generate_values` 的 sem：不传则自建默认）。B 端槽级小查询保持默认 6 不变。

### 2.3 取消端点（后端）

- `task_service.py` 在 `has_canceled`/`clear_canceled` 旁新增 `cancel_task(task_id)`：`REDIS_CONN.set(f"{task_id}-cancel", "x")`（与 canvas.cancel_task 同键约定）。
- `agent_api.py` 新端点 `POST /agents/tasks/<task_id>/cancel`（`@login_required`）：调 `cancel_task`，幂等返回 `{code:0}`。task_id 为服务端 `get_uuid()` 随机值且仅经 SSE 事件下发给发起会话的前端，伪造面可接受（与现有 message_id 同级）。

### 2.4 前端接线（use-send-message.ts）

- SSE 帧解析处（`val = JSON.parse(value?.data)`）：捕获 `val.task_id` 存入 `taskIdRef`（envelope 已含该字段，类型 `IMessageEvent` 已声明 task_id）。
- `stopOutputMessage`：若 `taskIdRef.current` 非空，fire-and-forget `POST /api/v1/agents/tasks/{task_id}/cancel`（带 Authorization，同文件现有 fetch 惯例），随后照旧 `abort()`。失败静默（console.warn），不阻断本地停止。
- c-chat 与 flow-ai-panel 共用该 hook，两处停止按钮同时生效。

## 3. 数据流（取消链路）

```
用户点停止 → stopOutputMessage
  → POST /agents/tasks/{task_id}/cancel → Redis {task_id}-cancel
  → abort() 本地 SSE（ UI 立即停）
服务端（下一个检查点，≤ 单批 LLM / 单次检索粒度）：
  generate_values._one / 收集循环 / retrieve_all_shared._one → GenerateCancelled
  → 组件转 _FillCancelled → SSE: cancelled 事件（连接已断则丢弃）→ 快速返回
```

## 4. 错误处理

- 取消竞态：批次已在途的 LLM 调用完成后结果被丢弃（as_completed 循环检查在收集侧），不产生半成品。
- B 端兼容：execute_task 不传 should_cancel/sem，路径零变化。
- 端点对不存在/已结束的 task_id：仍返回成功（幂等，键无人读即失效）。

## 5. 测试策略

- executor 单测：should_cancel 命中→GenerateCancelled、未命中回归、_retrieve_all/retrieve_all_shared 同参、sem 参数注入生效。
- 组件单测：GenerateCancelled→cancelled 事件、检索信号量传递。
- 端点测试：stub Redis 模式断言 set 调用与鉴权装饰。
- 前端：hook 现有 jest 临时配置回归；taskIdRef 捕获与 stop 调用为最小验证。
- 端到端（部署后）：触发填写→点停止→服务器日志出现 "has been canceled" 且检索停止。

## 6. 改动文件清单

| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `rag/svr/template_fill/executor.py` | GenerateCancelled + should_cancel（3 函数）+ sem 可选参数 |
| 后端 | `agent/component/template_fill.py` | should_cancel 接线 + 检索信号量（env 默认 2） |
| 后端 | `api/db/services/task_service.py` | cancel_task() |
| 后端 | `api/apps/restful_apis/agent_api.py` | cancel 端点 |
| 前端 | `web/src/hooks/use-send-message.ts` | taskIdRef 捕获 + stopOutputMessage 调取消接口 |
| 测试 | 对应 test 文件 | 新增/适配 |
