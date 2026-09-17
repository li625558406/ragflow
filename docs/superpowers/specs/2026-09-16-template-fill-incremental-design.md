# 范本填写增量模式（同范本有 done → 走增量而非从头填充）

**日期**：2026-09-16
**作者**：Claude（MiniMax-M3）
**目标**：解决用户痛点「确认并继续填写后又从头开始了」——画布 TemplateFill 节点每次触发都是无状态重启，从空基线重跑全流程；同范本已有 done 成稿时，应当走「增量修改」而非「全部重填」。

---

## 1. 背景

### 1.1 第一性原理

范本填写完成后产出成稿。后续操作（如「批文名称及编号 填写成 李港」）的**第一性意图**是「修改这一处」而不是「全部重填」。当前实现把范本填写当成全量任务跑：

- 用户 round-1 填写 244 个字段 → done
- 用户 round-2 说「改一个字段」 → 节点再次触发，重新选范本 + 重新 AI 预判 + 重新检索 244 项 + 重新渲染 → 体验与成本都差

### 1.2 现有设计的问题

`agent/component/template_fill.py::_invoke_async`（v0）是 stateless 的：

1. 每次触发都从空基线开始计算 values
2. `_canvas_task_params` 只下发 `_direct_values` / `_changed_keys` / `_retrieve_skip_keys` / `_user_file_text` 四个保留键，没有基线概念
3. `TplFillTaskService.latest_done` 已经存在（`api/db/services/template_fill_service.py:539`），但**只**被 `agent/tools/template_fill.py::_modify` 调用（对话内 modify 工具）；画布节点从未用过

`executor._merge_default_values` 的合并优先级（合并先后顺序）：

```
LLM/检索产值 → 直填 → default_value（B 端手动标注的默认值）
```

baseline 在这个链条里没有位置——所以即使外部传入 baseline，也不会在 default_value 之前兜回上次内容。

### 1.3 对抗性场景

| 场景 | 期望 | 当前实现 |
|---|---|---|
| round-2 改 1 个字段 | 确认卡只列 1 项，task 只检索该项 | ❌ 列 244 项 + 检索 244 项 |
| round-2 完全不改，问「文档里都有啥」 | 沿用上次成稿，summary 说「未涉及」 | ❌ 重跑全量，summary 误导 |
| round-2 说「全部重填」 | 走全量（与首次相同） | ✅ 现状正确 |
| 范本升级后 baseline 还在 | 不能跨版本混用 baseline → 走全量 | ❌ baseline 永远应用（潜在 bug） |

---

## 2. 目标

- **同范本有 done baseline + 用户 query 涉及具体字段** → 走增量：确认卡只列本次要改的项，task 只检索该项，其余沿用 baseline
- **同范本有 done baseline + 用户 query 不涉及本范本** → 轻量复用：summary 说「本次未涉及，沿用上次填写值」，渲染产物与上次同值（不再检索重抽）
- **同范本有 done baseline + 用户明确说「全部重填」** → 全量重填（与首次相同）
- **无 done baseline** → 全量重填（现状行为）

---

## 3. 设计

### 3.1 新增保留键：`_baseline_values`

`executor.split_canvas_params` 解析 `_baseline_values` 字典（key=str, value=str），生成 `opts["baseline_values"]`。值是 `tpl_fill_task.values["render"]`——上次成稿的真实填写值，不是 B 端 `default_value` 提示。

### 3.2 合并优先级（在 `_run_task` 中）

```
LLM/检索产值 → 直填 (_direct_values) → baseline_values → default_value
```

baseline 仅对 **missing** 字段兜回（用户已经在 baseline 上填的、但本轮没被 LLM/直填覆盖的字段，必须原样保留；不能让「增量」丢上次成果回退到 default_value 提示）。空字符串视为无值，不覆盖 default。

### 3.3 增量意图识别：1 次 LLM

新增 `executor.extract_patch_values(tenant_id, placeholders, baseline_values, query, should_cancel)`：

- 输入：用户原话 + 上次填写值 + 填写点清单（key/中文名/当前值）
- 输出：`{"intent": "patch"|"refill"|"fill_unfilled"|"noop", "direct": {key: 新值}, "changed": [key, ...]}`
- 失败 / 解析异常 → 兜底 `noop`
- 中文名/key 必须能在占位符清单里匹配上——匹配不上、含糊的字段一律忽略（防编造 key）

四种 intent 语义：

| intent | 含义 | 节点处理 |
|---|---|---|
| `patch` | 用户原话指定了字段 + 新值 | 走增量确认（只列 patch items） |
| `fill_unfilled` | 用户想补全留空字段 | 走增量确认（changed = 所有留空字段） |
| `refill` | 用户明确要求全部重填 | `baselines.pop(tid)` + 走全量 |
| `noop` | 用户原话不涉及本范本字段 | 保留 baseline + 走轻量复用（task 仍 spawn，但 LLM 槽为空，baseline 兜回所有 missing，渲染产物与上次同值） |

### 3.4 节点增量分支：`incremental_overrides`

`_confirm_changed_fields` 接收新参数 `incremental_overrides: dict[tid, {candidates, predicted, fallback_changed, fallback_values}]`：

- 该范本走 overrides → 跳过 default_map 构造（确认卡只列 patch items，不列全量 244 项）
- 兜底 decisions 用 `fallback_changed`/`fallback_values`（用户没改/超时走 LLM 抽出的 patch）
- 用户确认载荷的 `valid` 集合用 overrides 的 candidates key（不是全量填写点），前端只能勾选 patch items
- 全量范本行为完全不变（无 incremental_overrides → 走原 default_map 路径）

### 3.5 任务下发：`_canvas_task_params` 新增 `baseline_values` 关键字

调用方传入 `baseline_values=baselines[tid]["values"]`，下传为 `params["_baseline_values"]`。executor 端 `split_canvas_params` 拆出后用于 merge priority。

### 3.6 Summary 文案分支

| 范本类型 | 文案 |
|---|---|
| 增量 patch | `《{name}》：本次增量更新 N 个字段（其余沿用上次填写值，共 M 个填写点）。` |
| 增量 noop（轻量复用） | `《{name}》：本次未涉及，沿用上次填写值（共 M 个填写点）。` |
| 全量 | `《{name}》：共 M 个填写点，AI 填充完成，未检索到值的填写点已留空。` |

### 3.7 baseline 校验：版本必须一致

`base.template_version_id != c["_ver"].id` → 跳过 baseline（baseline 不能跨版本混用——范本结构变了，placeholder key 对应的位置/语义可能变）。

---

## 4. 不变性 / 边界

1. baseline 仅在 **同租户 + 同范本 + 同版本** 的 done 任务上使用
2. baseline 不修改 task 行 `values` 字段——只是下发参数给 executor
3. 画布节点的输出契约不变（仍是 `download` 列表 + `content` 汇总）
4. 全量范本行为完全不变（无 baseline / intent=refill）
5. 事件流不变：`selected → filling → filled → done`（增量范本没有 confirm_pending 也行，但保留 `confirm_pending` 让用户能改 patch items）

---

## 5. 实施清单

| # | 类型 | 路径 | 改动 |
|---|---|---|---|
| 1 | 后端 | `rag/svr/template_fill/executor.py` | `_CANVAS_RESERVED_KEYS` 加 `_baseline_values`；`split_canvas_params` 拆 baseline；新增 `PATCH_EXTRACT_SYSTEM` 常量 + `extract_patch_values` 函数；`_run_task` 在 `_merge_default_values` 之前按 missing 兜回 baseline；refill 防御性清空 direct/changed |
| 2 | 后端 | `agent/component/template_fill.py` | `_invoke_async` 加 baseline 检测循环（latest_done）+ extract_patch_values 调用 + incremental_overrides/noop_tids 构建；`_confirm_changed_fields` 加 `incremental_overrides` kwarg；`_canvas_task_params` 加 `baseline_values` 关键字；summary 分支 |
| 3 | 测试 | `test/test_template_fill_executor.py` | 16 个用例：split_canvas_params 4 个 + extract_patch_values 8 个 + baseline 合并优先级 4 个 |
| 4 | 测试 | `test/test_agent_fill_template_component.py` | 6 个用例：baseline 检测/version mismatch/noop/refill/no baseline/summary 分支；`_Row` 扩展 `values` + `template_version_id` 字段；`FakeTaskService` 加 `latest_done` |
| 5 | 测试 | `test/test_template_fill_events.py` | `_TaskServiceStub` 加 `latest_done → None`（兼容旧测试期望无 baseline） |
| 6 | 文档 | `D:\AI\ragflow2\CHANGE.md` | 顶部新增本次迭代条目 |
| 7 | 文档 | `D:\AI\ragflow2\CLAUDE.md` | 参考表追加一行 |

---

## 6. 对抗性测试覆盖

- `test_extract_patch_values_invalid_intent_fallback_noop` — LLM 输出非法 intent → 兜底 noop
- `test_extract_patch_values_filters_unknown_keys` — direct/changed 出现清单外 key → 丢弃（防注入）
- `test_extract_patch_values_llm_exception_fallback_noop` — LLM 抛异常 → 兜底 noop
- `test_extract_patch_values_refill_drops_direct_and_changed` — refill 模式下 direct/changed 清空（防误用）
- `test_baseline_empty_string_skipped` — baseline 给空串视为无值（与 `_merge_default_values` 口径一致）
- `test_baseline_skipped_when_direct_overrides` — 直填优先于 baseline
- `test_incremental_baseline_version_mismatch_skips` — 跨版本 baseline 丢弃
- `test_noop_intent_uses_baseline_light_dup` — noop 范本保留 baseline 走轻量复用

---

## 7. 部署

后端 2 文件成套 SCP + 重启；前端无改动（确认卡 UI 已有 `incremental` 字段兼容，但当前未渲染——见遗留事项）。

**未部署**：需要用户确认。

---

## 8. 遗留事项

1. **前端确认卡 UI 未适配**：`incremental=True` 标记已下发，但前端 `confirm_templates` 渲染逻辑未读取该字段区分「全量卡 vs 增量卡」。当前前端会按全量卡渲染（字段名 + 默认值 + 勾选框），对增量场景可工作但视觉上看起来跟 244 项一样。后续可加 header 区分「本次增量更新 N 个字段」+ 默认折叠（用户已确认）。
2. **noop 仍 spawn task**：轻量复用走「task 仍 spawn + LLM 槽空 + baseline 兜回」路径，浪费一次任务行 + 渲染。可以优化为「直接复用 baseline 的 render 桶文件」，但需要重构 `_bridge_download` 接受 baseline row。预计下个迭代。
3. **跨版本 baseline 完全丢弃**：当前 baseline 与当前版本不一致时全部走全量，没有「老字段保留、新字段走全量」的混合策略。混合策略风险较高（结构变化语义就变），暂不实施。