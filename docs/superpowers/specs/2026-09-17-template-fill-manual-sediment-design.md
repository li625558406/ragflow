# 范本填写：写回范本库改按钮触发（停止自动沉淀默认值）

**日期**：2026-09-17
**目标**：解决用户痛点「范本只要被 LLM 填写过一次，下一轮新流程再填这个范本，产出的就是上一次的内容——占位符像是被覆盖掉了」。

---

## 1. 背景

### 1.1 症状与真实机制

用户描述为「范本中的占位符被替换了」，但**范本文件与 `{{key}}` 占位符从未被改写**：

- 成稿按任务独立（`result_file_id = v{ver}_result_{task.id}.{ext}`），不覆盖范本版本文件
- 识别阶段写入的 `{{key}}` 占位符也只存在于 render 工作副本，与本批次无关

真正发生的是 **`placeholders[].default_value` 被无条件沉淀**：

| 位置 | 行为 |
|---|---|
| `rag/svr/template_fill/executor.py` 第 ⑦ 步 | 每轮完成后把**全部非空产值**写进版本行 `placeholders[].default_value`（`default_source="auto"`） |
| `agent/tools/template_fill.py` 的 `modify` | 同上，写 `patch` 的全部键 |

### 1.2 连锁反应（为什么下一轮会「被上一轮内容占满」）

1. 首轮沉淀后，244 个字段**全部带默认值** → `_confirm_changed_fields` 的触发条件 `default_map`（有非空 `default_value` 的 llm 槽位）非空 → 确认卡出现
2. 而 AI 预判「本轮有变化」的字段通常只有少数几项 → 用户只勾选少数
3. 未勾选字段在值合并链路上走到 `_merge_default_values`（`default_value` 兜底）→ **用上一轮的值回填**
4. 检索 + LLM 几乎不跑 → 用户看到「新流程直接给出上轮成果」

值合并优先级（`executor.py`）：`LLM/检索 → direct_values（直填）→ baseline_values（增量上轮真实成稿值，仅 missing）→ _merge_default_values（default_value 兜底，仅 missing）`。

增量路径（`latest_done` baseline）不读 `default_value`，**不受本改动影响**。

### 1.3 第一性原理

「默认值」这一机制的**正当用途**是「同一范本在组织内的稳定取值」（如招标人名称、报建编号），它应当由**人**在确认后显式沉淀；而「本轮 LLM 恰好填了什么」只是**这一次的结果**，不是范本的属性。把后者自动写进前者，等于让「本次运行」静默改写「范本基线」——既是数据污染，也让「新流程按新输入重新填」这一基本预期失效。

故：**去掉自动沉淀，改由用户在成稿行点按钮才写回**。

---

## 2. 设计决策

### 决策 1：按钮语义 —— 只沉淀默认值，不动范本文件

点按钮只做一件事：把本轮显式确认/改动的字段值写进版本行 `placeholders[].default_value`（`default_source="auto"`）。范本文件、render 工作副本、`{{key}}` 占位符**原样不动**。下一轮 LLM 有值仍用新值，没值才兜这个默认值。

### 决策 2：位置 —— C端成稿行（对话页 + 流程页签共用组件）

`template-fill-progress.tsx` 被 `pages/c-chat/index.tsx` 与 `pages/c-chat/flow/flow-detail.tsx` 复用，按钮加在共享组件里即天然两处生效。

不用 `extraAction?.(dl)` 槽（`:241`，只有 flow 传「存为流程版本」）。位置放在成稿行 `<a>下载</a>`（带 `ml-auto`）**之后**，天然靠右，形状即 `[下载] [写回范本库]`。

必须是**独立子组件** `TemplateFillSedimentButton`：模板行在 `templates.map` 回调里渲染，回调内不能用 `useState`（`TemplateFillFilledList` 已是同款先例）。

组件四态：`idle`（「写回范本库」按钮，`title` 说明「沉淀为范本默认值，不改变范本文件本身」）→ `loading`（`Loader2` +「写回中…」）→ `done`（绿字「已写回范本库」）/ `empty`（灰字「本轮无可写回改动」）/ `error`（红字「写回失败」+ `title=错误文案`，可再点重试）。

按钮文案取短版「写回范本库」——成稿行是窄列，完整语义「（沉淀为默认值）」放 `title`。

### 决策 3：沉淀范围 = 本轮「用户/LLM 实际拍板」的 key 集合

画布节点 `_canvas_task_params`（`agent/component/template_fill.py`）已把本轮所有保留键写进 `tpl_fill_task.params`（JSONField，**无需迁移**）。两个键合起来即白名单：

```
only_keys = set(params["_changed_keys"]) | set(params["_direct_values"])
```

`_changed_keys` 是确认后走检索+LLM 的白名单、`_direct_values` 是用户在卡里直填的值；`_llm_fill_items` 刻意把直填键从前者剔除，故**必须取并集**。

各场景落点：

| 场景 | `_changed_keys` | `_direct_values` | 白名单 → 沉淀 |
|---|---|---|---|
| 有确认卡（有默认值） | 勾选集合 | 直填值 | 勾选 ∪ 直填 |
| 无确认卡（首轮无默认值） | 全部 llm key | `{}` | 全部 llm key |
| 增量填写 | patch keys | LLM 抽出的 direct | patch ∪ direct |
| noop（轻量复用） | `[]` | `{}` | **空 → 不沉淀** |
| 非画布任务（chat/web） | 保留键缺失 | 缺失 | **拒绝**（决策 5） |

- 判据用 **key 是否存在**（`"_changed_keys" in params`），不用并集是否非空——noop 任务两者都在但为空集，必须落「写 0 个」而不是「无限制全写」。
- 「无确认卡 → 白名单 = 全部 llm key」是**有意结果**：首轮全量填写点按按钮即「把本轮成果存为基线」，用户按按钮本身就是授权。
- `override_keys` 语义保持独立：只传 `_direct_values` 的 key（用户手打的值可覆盖 `manual` 默认值；LLM 产的值不行）——与改造前 `executor.py` 口径一致。

### 决策 4：Service 加 `only_keys`，**必填、无默认值**

`_sediment_into_placeholders(placeholders, values, only_keys, override_keys=None)`：循环内 key 判空之后立刻 `if key not in only_keys: continue`。

- 去掉自动调用点后**生产调用方只剩新端点一处**，故 `only_keys` 设必填，彻底消灭「`None`（不限）vs `set()`（不写）」这个歧义雷——这正是本需求要根除的「静默全量写」类缺陷。
- 保留 `override_keys` 原语义与 `manual` 保护逻辑；不动 `MAX_ANCHOR_LEN` 截断、空值不沉淀等既有不变量。
- 返回值仍是 `bool`（是否发生变更）：端点据此回「已写回」/「本轮无可写回改动」。幂等重按返回 `False`。（`False` 有三种来源，前端无法区分——见 §7-③。）

### 决策 5：新端点 `POST /template/fill/fill-task/<task_id>/sediment`

放在 `api/apps/restful_apis/template_api.py`，紧邻 `get_fill_task_progress`，照抄其 owner 校验骨架。

| 项 | 口径 |
|---|---|
| 权限 | `TplFillTaskService.get_owned(task_id, current_user.id)`，None → 「任务不存在」 |
| 状态闸 | 非 `done`/`partial` → 「填写尚未完成，暂不能写回范本库」 |
| 保留键闸 | `_changed_keys` 与 `_direct_values` **两者都缺** → 「该任务不支持写回范本库（缺少确认记录）」。故意不退化全量——那等于把自动沉淀的老 bug 从后门放回来 |
| 保留键契约 | 上面这道闸成立的前提是「`params` 带保留键 ⇔ 画布节点写入了用户确认决策」。该等价关系由 **REST 入口 `create_fill_task` 剥离 `CANVAS_RESERVED_KEYS`** 保证（见 §3），否则调用方可伪造出「有确认记录」的任务，既骗过本闸门把任意 key 写进 `default_value`，也骗过 executor 的 `is_canvas` 门控。**不用 `task.source == "canvas"` 做闸门**：`source` 同样取自请求体，一样可伪造 |
| 值来源 | `task.values.render`（DB 行权威），`values` 是 JSON 字符串时 `json.loads` 兜底 |
| 版本行 | `task.template_version_id`；为空（历史脏数据）回落 `TplTemplateVersionService.latest(task.template_id)`；仍无 → 「范本版本不存在」 |
| 写 0 个 | `only_keys` 为空 → 直接返回 `written=False`，**不碰 DB** |
| 脏数据 | `params` 非 dict / `_changed_keys` 非 list / `_direct_values` 非 dict / `values` 各种畸形 → 一律按空处理，不炸端点（`_changed_keys` 为字符串时**不逐字符当 key**，故显式 `isinstance` 判型而非复用 `split_canvas_params`） |
| 异常 | 沉淀抛异常 → `logger.exception` + 「写回失败，请重试」（不让 500 裸奔） |
| 响应 | `get_result(data={"ok": True, "written": bool})` |

### 决策 6：删除两处自动沉淀，不动其余链路

| 位置 | 动作 |
|---|---|
| `rag/svr/template_fill/executor.py` 第 ⑦ 步整段 | **删除**（连同注释） |
| `agent/tools/template_fill.py` 的 `modify` 内 sediment try/except | **删除**（连同注释） |

`_merge_default_values` 仍在、`default_value` 仍是兜底来源、确认卡触发条件不变——只是默认值从此**只由人工按钮产生**。

**行为变化（需向用户明示）**：自动沉淀停掉后 `default_map` 更常为空 → **确认卡出现的频率下降**（只在范本确实有默认值时才弹），首轮/未点过按钮的范本全量走检索+LLM。这正是用户要的「不点按钮就按当前流程的填写内容展示」。

### 决策 7：前端直调 async 函数（不走 useMutation）

`sedimentTemplateFillDefaults(taskId)` 照 `confirmTemplateFill` / `testTemplateFill` 的直调写法（`data.code !== 0` 抛 `Error(data.message || '写回失败')`）。按钮只有「一次 POST + 四态」，无缓存需要失效（沉淀结果不在前端读模型里），不引入 React Query。

---

## 3. 改动清单

### 后端（4 文件）

| 文件 | 改动 |
|---|---|
| `api/db/services/template_fill_service.py` | `_sediment_into_placeholders` 加必填 `only_keys` + 行内 `continue` 硬闸（docstring 说明「空集合 ≠ 不限制」）；`sediment_defaults` 透传；`find_running` 限定 `source="canvas"`（见 §7-①） |
| `rag/svr/template_fill/executor.py` | 删第 ⑦ 步整段；`_CANVAS_RESERVED_KEYS` → `CANVAS_RESERVED_KEYS`（升为跨模块契约常量，见 §7-①） |
| `agent/tools/template_fill.py` | 删 `modify` 内 sediment try/except；`_modify` docstring 补「不沉淀 + 改动字段不进白名单」 |
| `api/apps/restful_apis/template_api.py` | 新增 `POST /template/fill/fill-task/<task_id>/sediment`；`create_fill_task` 剥离 `CANVAS_RESERVED_KEYS`（见 §7-①） |

### 前端（3 文件）

| 文件 | 改动 |
|---|---|
| `web/src/utils/api.ts` | `sedimentTemplateFillTask(taskId)` 端点 |
| `web/src/hooks/use-template-fill-request.ts` | `sedimentTemplateFillDefaults(taskId)` 直调导出 |
| `web/src/pages/c-chat/template-fill-progress.tsx` | 新增 `TemplateFillSedimentButton` 子组件；成稿行下载 `<a>` 之后条件挂载（`t.task_id` 存在才渲染） |

### 测试

- `test/test_template_fill_utils.py`：4 个既有 sediment 用例补 `only_keys` 实参 + 新增 6 个对抗用例（白名单硬闸 / 空集不写 / 幽灵 key 忽略 / override 与 only 的**与**关系 / 非字符串项 / placeholders 混入脏项）
- `test/test_template_fill_executor.py`：删除 `test_canvas_direct_values_sediment_override_keys`、`test_b_end_sediment_without_override_keys`（断言已删除的行为）；新增 `test_pipeline_does_not_auto_sediment`（画布 + B端各跑一遍完整 pipeline，断言零沉淀）
- `test/test_template_fill_tool.py`：`modify` 成功用例的沉淀断言改反回归（`calls["sediment"] is None`）
- `test/test_template_fill_sediment_api.py`（新，17 例）：越权 / 状态闸 / 保留键闸（含 `params` 非 dict）/ noop 空集 / 白名单并集与 override / manual 覆盖口径 / 截断 / 空白值 / values JSON 字符串 / values 畸形矩阵 / 保留键类型脏数据矩阵 / 版本回落与缺失 / 幂等 / 异常友好文案。沉淀语义用**真实** `_sediment_into_placeholders`（纯静态方法，不触库）在内存 placeholders 上跑，保证「端点算出的 only_keys 确实落到字段上」而非只测桩
- `test/test_template_api_routes.py`（新增 2 例，见 §7-①）：剥离保留键 + 只含保留键时落空 dict

前端（本批次新增，见 §7-③）：`web/src/pages/c-chat/__tests__/template-fill-sediment-button.test.tsx`（6 例：挂载口径 2 + 四态 4）。桩掉 `use-template-fill-request` 与 `template-fill-live-preview`（后者顶层 import docx-preview，重且无关）；真实沉淀语义由后端 sediment_api 套件覆盖，前端不重复造桩。

**合计 624 后端 + 76 前端全绿。**

---

## 4. 部署（**须用户明确指示**）

- 后端 **4 文件成套 SCP**：`api/db/services/template_fill_service.py`、`rag/svr/template_fill/executor.py`、`agent/tools/template_fill.py`、`api/apps/restful_apis/template_api.py` → `docker restart docker-ragflow-cpu-1` → import 冒烟。
- 前端 `npm run build` → `rm -rf dist/*` 就地解包（**禁用 `mv dist`**，会断 bind mount inode）→ `nginx -s reload`。
- 互锁：新前端打旧后端 → 端点 404，按钮报「写回失败」；旧前端 + 新后端 → 无按钮，自动沉淀已停。**前后端需同时上线**（旧前端在前端未更新期间会失去原先的自动沉淀能力，属预期）。

---

## 5. 遗留（本次不修）

1. `modify`（对话里说「把 X 改成 Y」）改动的字段**不进白名单**，点写回不会沉淀该字段——需要就下一轮再改一次。
2. 任务钉住旧版本而范本已升版时，写回落在旧版本行（不影响新版，也不报错）。
3. 「无确认卡 → 白名单 = 全部 llm key」在首轮会一次写入全量字段：按按钮即授权的有意设计，但如认为过宽，可后置为「只写有直填值的字段」。
4. 存量 `default_source="auto"` 默认值**不动**（用户明确选择「本次不动」）；B端可经 `PUT /template/fill/<id>/defaults {key: ""}` 手动清。
5. 按钮无「已写回」持久化态——刷新后回到可点态，重按幂等无害。

## 6. 明确不做

1. 不给按钮做持久化状态（不加 DB 列、不改 progress 端点）
2. 不清洗存量 `default_value`
3. 不动 `modify` 的值合并链路
4. 不动 `_merge_default_values` 优先级 / 确认卡触发条件 / 增量 baseline 链路
5. 不改 `filled`/`unfilled` 报文
6. 不加批量写回 / 一键清空默认值等衍生功能
7. 不在 `src/components/ui/` 下建组件、不引入 i18n / toast 库

---

## 7. 收口审查（`superpowers:code-reviewer`，2026-09-17）

结论：**1 Major + 5 Minor，全部处置**（4 处改码 + 1 处记入 docstring + 1 处放宽文案）。

### ① Major — 保留键闸可被伪造（已修）

审查指出：写回端点以 `params` 是否带保留键判定「该任务有确认记录」，但 REST 入参同样能带这些键 → 调用方可以给任意任务伪造出「有确认记录」，把任意 key 写进 `default_value`。

审查建议的修法（改用 `task.source == "canvas"`）**不成立**：`source` 也是请求体字段（`template_api.py` 的 `source=(source or "web")[:16]`），一样可伪造。

改为在**契约真正可能被破坏的地方**加固：`create_fill_task` 从 REST 入参剥离 `CANVAS_RESERVED_KEYS`，让「`params` 带保留键 ⇔ 画布节点写入了用户确认决策」成为不变式。

- 剥离**零副作用**：`validate_placeholders`（`rag/svr/template_fill/detector.py:231`）要求 key 匹配 `[a-z][a-z0-9_]{0,63}`，故 `_` 前缀键不可能是合法的 param 直取键。
- 顺带堵上同源的**既有**缺口：executor 的 `is_canvas` 门控此前同样可被 REST 调用方伪造，从而误用 LLM 白名单 / 直填覆盖 / 检索收窄。
- 常量随之从 `_CANVAS_RESERVED_KEYS` 改为 `CANVAS_RESERVED_KEYS`（跨模块契约，按项目惯例去掉下划线前缀），并在定义处与两个使用点写明该等价关系。

### ② Minor — `find_running` 会复用非画布任务（已修）

原实现只按 `template_id + tenant_id + 中间态` 查，与对话/B端任务撞车时画布节点会「复用」别的来源的中间态行，后果比审查描述的更重：

1. `is_canvas` 变 False → 画布在确认卡上的勾选/直填决策被**整批丢弃**，按全量 LLM 重跑；
2. 该行终态后成稿行的「写回范本库」**必然报**「缺少确认记录」。

已限定 `source="canvas"`（其唯一生产调用方即画布节点 `agent/component/template_fill.py`）。

### ③ Minor — `empty` 文案只描述了三分之一的成因（已修文案）

`written=False` 有三种来源（白名单为空 / 全部受 `manual` 保护 / 新值与现值相同），前端**无法区分**（设计明示返回值保持 `bool`，见 §6-1）。故放宽文案与 `title` 使其对三者都成立，而非硬猜一种误导用户。未改协议（改判因需要动 Service 返回类型，超出本批次范围）。

### ④ Minor — `_modify` docstring 与实际行为不符（已修）

删掉 sediment 调用后 docstring 仍暗示会沉淀。已改正，并显式写明「改动字段不进白名单」这一遗留（§5-1）。

### ⑤ Minor — 并发点按钮的丢更新（记为已知）

`select → 内存改 → save()` 整列覆盖、无 `for_update()`：两个任务并发点同一版本时，后写者的 `placeholders` 快照会盖掉先写者的新键。

判定为**可接受**：既有形态、概率低、后果自愈（再点一次即补），且 §6-4 明令不动该链路。按审查建议记入 `sediment_defaults` docstring 作为已知失败模式，并写明若将来要修的正确做法（`DB.atomic()` 内 `for_update()` 后再改）。

### ⑥ Minor — 前端测试缺失（已补）

审查指出原「无测试脚手架故不写组件测试」的结论**不成立**（`.scratch/jest.local.cjs` 已打通），且按钮四态是可测的。已补 `template-fill-sediment-button.test.tsx` 6 例（挂载口径 2 + 四态 4），见 §3。

### 附带修掉一处既存测试腐坏（与本批次无关）

`web/src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx` 在 `388ce463`「填写字段确认卡默认折叠」之后未同步更新，5 例**全红**（卡片默认收起，字段行与输入框尚未挂载）。抽 `expandCard()` 辅助函数修复。该腐坏非本批次引入（本批次未触碰该卡片文件）。
