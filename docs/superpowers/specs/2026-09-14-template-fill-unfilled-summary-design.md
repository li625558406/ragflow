# 范本填写未填充汇总与成稿内定位跳转 — 设计文档

- 日期：2026-09-14
- 状态：设计完成，未实施
- 入口：C端流程页签 AI 面板（与 c-chat 对话页共用同一套组件，一处改动两端生效）

## 1. 背景与目标

范本填写（TemplateFill）执行中，LLM 检索不到值的字段已按现状**留空不阻塞**（executor
`missing` 路径 + 默认值兜底后仍缺则成稿留空），但用户事后不知道**哪些**字段没填，
需要在成稿中逐个翻找。

本次目标：

1. 第一轮填充结束（任务终态 `done`）后，把成稿中留空的填写点汇总展示在对话界面；
2. 每个未填字段可点击，跳转到「查看填写内容」预览抽屉并**滚动定位到文档中该占位符的位置**；
3. 必填（`required=true`）与选填视觉区分：必填红色 +「必填」徽标，选填灰色弱化。

非目标：手动补填未填字段并重渲染成稿（后续迭代）；B端填写任务页适配（端点增量字段天然下发，B端前端不读）。

## 2. 现状要点（依赖事实）

- 占位符清单含 `required`（B端 AI 识别产出，detector 默认 `true`）与 `default_value`。
- 任务终态 `tpl_fill_task.values` DB 行已存最终 `render` 值映射（`template_api.py`
  `build_progress_payload` 的快照缺失回退源），**未填列表可读取时即时派生，无需新增持久化**。
- Redis 终态快照 `_write_snapshot(status="done", values=...)` 只存 values，不含未填信息。
- 前端 `TemplateFillProgress` 的 filled 行已有「查看填写内容」按钮 → `TemplateFillLivePreview`
  抽屉；docx 保真渲染后全部占位符（含未填的虚线槽位）都在 `DocxPlaceholderSpans`
  （key → span[]）映射里，定位滚动技术上现成。
- `use-template-fill-task-poll.ts` 断连重连轮询走
  `GET /template/fill/fill-task/<task_id>/progress` 端点，终态合并 `values`/`download`。

## 3. 方案选型

| 方案 | 结论 |
|---|---|
| **A. 后端终态派生 + 事件/端点透传（采纳）** | 数据权威（从最终 values 派生，与成稿一致）；刷新/重连后不丢；改动小 |
| B. 前端本地派生（slots − values） | required 仍需后端下发；空串口径前端要猜；历史恢复缺 selected 事件，伪省事 |
| C. 只走 SSE 不持久化 | 刷新后汇总消失，「跳转查看」恰是用户回头补内容时用的，体验硬伤 |

采纳 A 的轻量变体：**不新增任何持久化**，读取时派生。

## 4. 后端设计

### 4.1 派生纯函数（`rag/svr/template_fill/executor.py`）

```python
def derive_unfilled(placeholders: list[dict], values: dict) -> list[dict]:
    """成稿留空的填写点 → [{key, name, required}]，按占位符清单顺序。"""
    return [{"key": it["key"], "name": it.get("name") or it["key"],
             "required": bool(it.get("required", True))}
            for it in placeholders
            if it.get("key") and not str(values.get(it["key"]) or "").strip()]
```

判定口径：**最终值为 None / 空串 / 纯空白 = 未填充**。统一覆盖三条留空路径
（LLM 无产值、直填空串清空、白名单外无默认值）。用 `str(...).strip()` 判空而非
truthy 判断，防止 `"0"`、`"false"` 等 falsy 但有效值被误判未填。

`required` 缺失时兜底 `True`，与 detector 默认口径一致。

### 4.2 接入点 1：canvas 观察者（`agent/component/template_fill.py`）

`_invoke_async` 观察循环 `row.status == "done"` 且桥接成功的分支：

- `unfilled = derive_unfilled(cand["_placeholders"], (row.values or {}).get("render") or {})`
- 非空时 `filled` 事件增加 `"unfilled": unfilled` 字段；为空（全部填满）不下发该字段。
- 数据源用 DB 行 `row.values["render"]`（终态权威），不依赖 Redis 快照存活。

### 4.3 接入点 2：progress 端点（`api/apps/restful_apis/template_api.py`）

`build_progress_payload` 增加同款派生（纯函数内，便于对抗测试）：

- 保持纯函数：新增可选入参 `placeholders`，由端点层按
  `task.template_version_id` 查询范本版本后传入（版本行缺失/无占位符 → 不派生）；
  values 优先快照、回退 DB render（与既有 `values` 字段同口径）。
- 响应增加 `"unfilled"` 字段（仅 done/partial 终态且派生非空时）。
- 断连重连轮询由此拿到汇总，**刷新/重连后不丢**。
- B端同端点增量字段，B端前端不读即无影响。

改动量：2 个后端文件；无 DB 变更、无 Redis 结构变更，满足迁移部署约束。

## 5. 前端设计

### 5.1 类型与状态（`web/src/hooks/template-fill-stream.ts`）

- `ITemplateFillTemplate` 新增 `unfilled?: Array<{ key: string; name: string; required: boolean }>`。
- 归约器 `filled` 分支：事件带 `unfilled` 才写入，**整体替换不合并**（一次性终态数据，
  重放/后到者胜幂等）。

### 5.2 断连重连轮询（`web/src/hooks/use-template-fill-task-poll.ts`）

终态 `done` 合并分支：响应带 `unfilled` 则并入 override（与 `values` 同样的
「缺省不下键」防御，避免清掉 SSE 已累积数据）。

### 5.3 成稿行内联汇总（`web/src/pages/c-chat/template-fill-progress.tsx`）

```
《XX投标函》  [👁 查看填写内容]  [下载]
 ⚠ 3 个填写点未填充：项目名称(必填) · 投标保证金(必填) · 联系电话
```

- filled 行下方追加一行内联汇总；无 `unfilled` 或列表为空不渲染（老任务/全填满自然消失）。
- 每个字段名可点击 → 打开该范本 LivePreview 并定位；必填红色文字 +「必填」徽标，选填灰色。
- 点击回调：`setLiveTpl({ template_id, focusKey: key })`（原 `liveTplId` string 扩为对象，
  存 id 不存快照的既有惯例不变）。

### 5.4 跳转定位（`web/src/pages/c-chat/template-fill-live-preview.tsx`）

- 新增可选 prop `focusKey?: string`：
  - **docx 保真路径**：渲染 + `applyDocxHighlight` 完成后，从 `placeholderSpansRef`
    取该 key 首个 span（未填占位符本就以虚线槽位形态在映射里），
    `scrollIntoView({ block: 'center', behavior: 'smooth' })` + 闪烁动画
    （临时 outline class，约 2s 后移除）。
  - **纯文本降级 / xlsx**：`renderText` 占位符 span 与 xlsx 行加 `data-ph-key`
    属性，querySelector 定位后同款滚动闪烁。
  - **定位失败**（key 不在文档中 / 抽屉仍在加载）→ 渲染完成后执行一次；仍失败静默跳过，
    抽屉照常打开。
- focus 只执行一次：effect 依赖渲染完成态 + focusKey，「已定位」ref 防重复滚动；
  用户手动滚动不受干扰。

改动文件：前端 4 个；组件 c-chat 与 flow AI 面板共用，一处改动两端生效。

## 6. 边界与降级

| 场景 | 行为 |
|---|---|
| 旧任务 / 快照缺失 | `build_progress_payload` 从 DB render 派生；派生不出不下发字段，前端不显示 |
| 占位符无 key | 派生跳过（与既有 `placeholder missing key, skipped` 口径一致） |
| 任务 failed / cancelled | 不派生——成稿不存在，跳转无意义，现有失败文案不变 |
| docx 渲染降级为文本 | 定位走 `data-ph-key` 兜底路径 |
| 同 key 多处出现 | 定位第一处 |
| 点击时抽屉加载中 | focus 等渲染完成后执行；文档无该 key 静默跳过 |
| 事件重放 / 历史恢复 | `unfilled` 终态整体数据，归约器后到者胜，幂等 |

## 7. 测试（对抗性）

1. `derive_unfilled`：空 placeholders → `[]`；values 全空 → 全量；值为 `"  "` → 未填；
   值为 `"0"` / `"false"` → 已填（防 falsy 误杀）；同 key 大小写变体不误配；
   key 含 `{{}}` 残留 / Unicode 控制字符 → 原样透传不炸。
2. `build_progress_payload`：快照 None 退化 DB render 派生；`task.values` 非 dict
   → 不派生不报错；`unfilled` 与 `values`/`download` 共存序列化正确。
3. canvas 事件透传：done 分支带 unfilled；failed/cancelled 不带。
4. 前端归约：filled 事件无 `unfilled` 不清旧值；poll override 与 SSE 双到幂等；
   focusKey 对不存在 key 静默。

## 8. 部署

- 后端 3 文件成套 SCP：`rag/svr/template_fill/executor.py`、
  `api/apps/restful_apis/template_api.py`、`agent/component/template_fill.py`。
- 前端 `npm run build` + tar 部署。
- 均为增量字段，**前后端可独立部署**：旧前端忽略新字段，新前端遇旧后端不下发即不显示，无互锁。
- 纯增量，无回滚风险；回滚 = 还原 3 个后端文件 + 前端旧 build。
