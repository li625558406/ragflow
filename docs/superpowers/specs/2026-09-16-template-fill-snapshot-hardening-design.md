# 范本填写快照化恢复 + 预览内存治理（加固增强）设计

日期：2026-09-16
状态：设计定稿，随本文件同步实施
背景：C 端流程页范本填写两大不稳定症状——①打开实时预览/查看成稿时浏览器标签页崩溃（OOM）；②填写完成后刷新页面，字段确认卡（confirm_pending）重现且可交互，可触发重复填写。

## 1. 根因

### 症状一：标签页崩溃
1. 实时预览（TemplateFillLivePreview）用 docx-preview 把整本工作副本**一次性全量渲染成 DOM**（200+ 页文档；`applyDocxPageLazy` 仅视觉懒显示，DOM 节点全建），单次渲染内存峰值极高。
2. 流程页常驻挂载（flow-panel keptIds）：每个 FlowDetail 实例各自持有 `liveTarget`（预览抽屉状态），切到别的流程时旧详情被 `hidden` 隐藏但**预览 DOM 树仍驻留**；切换多轮 = 多棵完整大文档 DOM 叠加 → OOM。
3. 超大文档无任何预判防线，打开即全量渲染。

### 症状二：刷新后确认卡重现
1. 挂起确认/选择等待态**只活在画布节点内存**（节点轮询 Redis 键；Redis 键只在用户提交后才存在），没有任何可查询的权威状态。
2. 前端刷新恢复唯一依据 = 落库事件序列（flow_ai_chat.template_fill_events）最后一条是否 `confirm_pending`——「猜尾巴」。两条漏事件路径：a) 提交确认后 2s 防抖未及落库即刷新；b) SSE 断连后由 per-task 轮询 hook 恢复进度，**轮询事件不落库**，事件序列停在 confirm_pending。
3. 结果：重放出可交互僵尸卡；600s nonce 窗口内再提交会触发重复填写（第二轮 canvas 启动）。

## 2. 设计总览（方案三：快照权威恢复）

**画布节点全程维护一个 Redis「运行快照键」，新端点按 task 查询它组装全量权威状态；前端刷新恢复 = 从事件序列轻量提取运行 id → 拉快照重建状态 → 未完结则轮询快照直到终态。事件序列降级为「运行 id 发现 + 旧数据兜底」，不再承担挂起态判定。**

### 2.1 后端：运行快照键（agent/component/template_fill.py）

- 键：`tpl_fill:run:{canvas_task_id}`（canvas_task_id = `canvas.task_id`，即 confirm_pending/select_pending 事件里的 task_id）。
- 值 JSON：
  ```json
  {
    "stage": "selected|select_pending|confirm_pending|filling|done",
    "templates": [{"template_id","name","slot_count"}],
    "tasks": {"<template_id>": "<fill_task_id>"},
    "pending": {"type":"select|confirm","nonce":"...","select_candidates":[...],
                "ai_selected":[...],"confirm_templates":[...]} | null,
    "updated_at": 1690000000000
  }
  ```
- 写入时机（新私有方法 `_write_run_snapshot`，Redis 故障只告警，与既有快照同口径）：推 `selected` 后、`select_pending` 进入、二次 `selected`、`confirm_pending` 进入、确认消费/超时（pending 置 null + stage=filling）、观察循环中每模板到终态、节点收口 `done`（终态快照 TTL 缩短 600s，供收尾 10 分钟内刷新恢复）。
- TTL：过程态 7200s；终态写后不再续期（600s 自然过期）。
- 注意：canvas_task_id 与 fill task_id 是两个 id 空间；快照键只按 canvas_task_id 索引，`tasks` 映射维护两空间对应关系。

### 2.2 后端：运行快照端点（api/apps/restful_apis/template_api.py）

`GET /template/fill/fill-run/<canvas_task_id>/snapshot`（login_required）：
- 读运行快照键；不存在 → `{exists: false}`。
- 权限：`tasks` 映射中的 fill task 必须全部 `TplFillTaskService.get_owned(task_id, current_user.id)` 命中，否则按不存在处理（防越权探测）。
- 组装（纯函数 `build_run_snapshot_payload` 便于对抗测试）：逐模板读 DB 行 + Redis 进度快照 → status/done/total/values/error/stalled；终态 done 行复用 `_bridge_download` 桥接 download、按版本 placeholders 派生 unfilled（与现有 progress 端点同口径）。
- 响应：`{exists, stage, finished, templates:[{template_id,name,slot_count,status,done,total,values,unfilled,download,error,task_id}], pending:{...}|null}`。

### 2.3 前端：恢复链路重构

- `template-fill-stream.ts` 新增纯函数：
  - `findCanvasRunId(events)`：扫原始事件数组取 `confirm_pending/select_pending/heartbeat` 事件的 task_id（截断挽救保头，id 必在头部，健壮）。
  - `buildStateFromRunSnapshot(snap)`：快照 → `ITemplateFillState`（pending 卡带 nonce/candidates 原样映射；终态模板行带 download/unfilled/values）。
- 新 hook `use-template-fill-run-recovery.ts`：输入重放态；发现 canvas run id → 拉快照；`exists && !finished` 每 3s 轮询直至终态/stalled/键过期；快照可用则**整体替换**重放态，键过期（exists=false 且重放态有挂起卡）则把挂起卡标记 expired（不再显示可交互僵尸卡）。旧数据无 run id → 行为退化为现状（per-task 轮询兜底不变）。
- 接线：
  - flow-ai-panel.tsx：挂载恢复改为「parseAndReplay（保留，仅作兜底与 id 发现）→ run recovery hook 覆盖」；删除「lastStage === confirm_pending 才保留挂起卡」的猜测式判定。
  - c-chat index.tsx：历史消息重放后，对最新一条带 templateFill 的消息跑同一 hook，终态后回写该消息的 templateFill 状态。

### 2.4 前端：预览内存治理（症状一）

1. **隐藏详情强制收预览**：TemplateFillProgress 新增 `forceClosedLivePreview` prop（true 时 effect 清空 liveTarget）；flow-detail 接收 `visible` prop（flow-panel keptIds 渲染时传 `id === activeId`）透传。保证任意时刻至多一棵大文档 DOM 树。
2. **超大文档防线**：TemplateFillLivePreview 拿到 blob 后，`size > 2.5MB` 默认走纯文本渲染 + 顶部提示「文档较大，已用文本预览保障流畅」，提供「切换保真渲染」按钮显式覆盖（本地 state，不落库）。渲染失败降级链路保留不变。

## 3. 不做的事（YAGNI）

- 不改 fill 执行链路（executor/任务行/写库口径不动）。
- 不做 progress 端点全量替代 per-task 轮询（旧消息/无确认轮次的兜底路径保留）。
- 不做 docx 分页真虚拟化（收益有限、侵入 docx-preview 渲染模型）。
- c-chat 恢复仅覆盖最新一条模板消息（历史多轮回看维持重放）。

## 4. 测试与验收

- 后端：快照写入各 stage 调用点单测；端点 payload 纯函数对抗用例（键缺失/JSON 坏/任务越权/终态桥接失败/placeholder 脏数据/pending 残留）；`uv run --no-sync pytest` 全绿。
- 前端：hook/纯函数单测（jest 本地环境缺失时以 eslint + tsc 构建验证），build 通过。
- 验收路径：流程页发起多范本+确认轮填写 → 填写完成后刷新 → 无确认卡、成稿卡完整；填写中刷新 → 进度经快照轮询续播；挂起等待期刷新 → 卡保留且超时后自动灰；双流程各开预览后来回切换 → 仅活跃流程持有预览；大文档打开 → 文本预览兜底不崩。
