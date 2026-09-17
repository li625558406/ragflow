# 范本填写：就地修改后的可见性修复（卡片消失 + 预览不变）

**日期**：2026-09-17
**目标**：解决用户实测报出的两个症状——①填写任务完成后页面整块空白，「查看填写内容」按钮消失，**刷新页面重新进入流程才出现**；②LLM 回执说已改，但「查看填写内容」的文件预览**文案没变**。

---

## 1. 背景

### 1.1 症状复现路径（用户原话）

1. 流程页发起范本填写 → 完成 → 卡片显示「本次增量更新 69 个字段…可在上方预览或下载成稿」
2. 用户在对话里说「石狮市交通建设投资有限责任公司 改成 石狮市李港aaa交通建设投资有限责任公司」
3. LLM 调 `FillTemplate(action=modify)` → 回执「已完成就地修改（未重新发起填写，原成稿的下载/预览链接不变）：范本中「招标人名称」在 4 处出现，字段 key 分别是 `tenderer_name`、`tenderer_name_2`…已全部由 A 改为 B」
4. **症状 A**：页面此时什么都没有了，成稿卡（含「查看填写内容」按钮）整块消失
5. **症状 B**：刷新后再点「查看填写内容」，预览里的文案**仍是 A**

### 1.2 这是一个症状、三个独立的根因

两个症状看似一件事，实际横跨**三条互不相干的链路**。逐个查证（不是猜）：

| # | 症状 | 根因所在层 | 根因 |
|---|---|---|---|
| 1 | B：改了「别的字段」 | 工具语义层 | `detail` 的 keyword 只能匹配中文名/key/锚文本；用户给的是一段**值**，模型定位不到 → 挑了个名字听起来像的 `招标人名称`（4 个同名 key），而该段文字实际属于 `project_owner（项目业主）` |
| 2 | B：改对了也看不出来 | 前端渲染层 | 「查看填写内容」预览**不是成稿**，是「模板工作副本 + 前端 values 覆盖」；`modify` 不发任何 `template_fill_progress` 事件 → 前端 values 永远停在改前 |
| 3 | B：下载到的也不是新稿 | 存储桥接层 | 成稿有**两份**：真源 `{template_id}/v{ver}_result_{task_id}.ext` 与派生副本 `{tenant}-downloads/tplfill-{task_id}`；`modify` 只写了前者 |
| 4 | A：卡片整块消失 | 前端状态层 | `handleSend` **无条件**清空 `templateFillRef` / `templateFillEventsRef` / `lastTemplateFill`；非填写轮（modify 轮）没有任何事件能把它们填回来 |

---

## 2. 逐条根因与修复

### 2.1 症状 A：非填写轮清空了范本卡快照

**机制**：`flow-ai-panel.tsx` 的 `handleSend` 在每次发送时执行

```ts
templateFillRef.current = undefined;
templateFillEventsRef.current = [];
setLastTemplateFill(null);
```

这在「新一轮填写」语义下是对的（本轮事件会重新填充），但 `modify` 轮**一个 `template_fill_progress` 事件都不发**——它走的是工具回执，不是 SSE 进度管道。于是三处快照同时被清空且无人回填 → `TemplateFillProgress` 拿到 `undefined` → 返回 `null` → 成稿卡整块消失。

**为什么刷新能恢复**：刷新走历史回放 `parseAndReplay`，从**更早那条**落库记录里重放出了成稿卡。这条线索反过来证明「数据一直在，只是被本地状态清掉了」，排除了「后端没下发」的歧义。

**修复**（`flow-ai-panel.tsx`，删除 3 行 + 注释）：

- **不在发送时清空范本快照**。成稿卡的可见性不该由「发了一条消息」决定。
- 语义上不丢东西：真正产生新填写的一轮，`streamState.templateFill` 会在同一 commit 内先经 `[streamState.templateFill]` effect 覆盖状态（该 effect 声明在报告 effect 之前，同一 commit 内先跑）。
- `templateFillEventsRef` 同理保留：本轮若无新事件，落库的 `template_fill_events` 沿用上一轮快照，「改完刷新」也能从**本条**记录重放出卡片，而不是跳过它去找更早那条。
- 对照保留：`fileReviewRef.current = null` **不动**——文件审核确实每轮重新发起，与范本填写的「改一份既有成稿」语义不同。

**刻意不做**：流式期间报告 `streamState.templateFill`（modify 轮为 `undefined`）会让卡片在生成中短暂隐藏。没有改成 `?? templateFillRef.current` —— 那超出本次批准范围，且流式期卡片闪一下不是用户报的症状。

### 2.2 症状 B 之一：预览渲染的是前端 state，不是成稿字节

**关键澄清**（决定了修复方向）：`template-fill-live-preview.tsx` 里的 docx 分支**不读成稿**。它读的是

```ts
useTemplateFillFile(tpl.template_id)   // → api.downloadTemplateFill(id, 'render')
```

—— 即范本**工作副本**（识别阶段把 anchor 替换成 `{{key}}` 的那份，含占位符）。屏幕上「填好的文字」完全由前端 `tpl.values` 经 `applyDocxHighlight` / `updateDocxHighlight` 覆盖上去。

**所以「只修字节」不可能修好这个症状**：成稿改了、values 没改，预览就还是旧文案。这条是第一性原理层面的判据——先确定「用户看到的字从哪来」，再决定改哪里。

**修复**（`use-template-fill-request.ts` + `template-fill-live-preview.tsx`）：

- 新增 `fetchTemplateFillTaskProgress(taskId)` → 拉 `templateFillTaskProgress` 端点（该端点的 values 取自 DB render，`modify` 已回写）。
- 预览打开时按 `task_id` 拉一次，**仅终态取用**（`TERMINAL_PROGRESS_STATUSES = ['done','partial']`）：流式期间 SSE 的 values 比这发请求更新鲜，不能被它压回去；`failed/cancelled` 没有成稿可取。
- 命中则整体覆盖 `values`；未命中/请求失败/返回 null → 回落卡片快照，**不清空预览**。
- `filled` / `unfilled` 清单按 **null 与否**判定而非 `??`：权威响应里 `null` 表示「空」（全填满 / 无留空），此时不能回落到卡片上那份可能过时的清单，否则已填槽位会悬浮显示上轮的中文名。
- `values` 用 `useMemo` 包裹：两者都空时裸 `|| {}` 每次渲染产新对象，会把下游 `updateDocxHighlight` effect 与 `filledCount` 的依赖打成「每渲染必变」。

**已知边界**（留待真实诉求）：只在「打开预览」这一刻取数。抽屉一直开着、用户在同屏对话里改字段的极端路径没有可用的「本轮是新轮」信号（成稿卡状态对象在 modify 轮里引用不变），需关掉重开一次才刷新。**不加轮询**——没有信号就轮询等于给所有打开的预览挂一个永久定时器。

### 2.3 症状 B 之二：下载桥接副本是过时的

**机制**：成稿存在两份，对象名与工具都不同：

| 角色 | bucket | object |
|---|---|---|
| 真源（`_storage_put` 写） | `{template_id}` | `v{ver}_result_{task.id}.{ext}` |
| 派生副本（卡片下载/预览走） | `{tenant_id}-downloads` | `tplfill-{task_id}` |

`modify` 原先只覆盖真源。卡片下载走 `/api/v1/agents/download?id=tplfill-…`，命中的是**没被更新的**派生副本。

实测字节差异（用户那份成稿）：
- `{template_id}/v1_result_….docx` → 219617 B，md5 `7c82405c…`（已含新值）
- `{tenant}-downloads/tplfill-….docx` → 219566 B，md5 `0f839570…`（仍是旧值）

**为什么不能靠 REST 层自愈**：`template_api._bridge_download` 有进程内记忆化 `_bridged_tasks: set[str]`，命中即**跳过 get+put**。所以「改完再读一次 progress」不会重桥——这个记忆化正是把「对象名确定、内容已过时」变成静默错误的放大器。

**修复**（`_modify`）：主成稿落盘后，**同名覆盖**派生副本。

- **不去 invalidate `_bridged_tasks`**：对象名是确定性的，`_modify` 直接写入即维持了该记忆化的不变式（「进程内已桥过 ⇒ 副本内容等于真源」），无需跨层去动 REST 层的私有集合（那是分层违规，且会在两处维护同一个不变式）。
- 失败**不当作修改失败**：主成稿已正确落盘，真源是权威。但必须在回执里讲明（`bridge_note`），否则用户会以为模型在编造「已修改」。
- 该 handler 记了日志，ruff 的 blind-except 对其本就豁免，故**不加** `# noqa` 注记（写了会被 RUF100 判为冗余指令）。

### 2.4 症状 B 之三：按「值」定位不到 key

**机制**：`_format_template_detail` 的 keyword 只匹配 中文名称 / 英文 key / 锚文本。用户说「把 **A** 改成 B」，A 是**值**；模型拿 A 去这三个通道找，找不到 → 只能按字面挑名称最像的 key → 4 个同名「招标人名称」被全改，而持有该值的 `project_owner` 一个没动。**LLM 的回执本身没撒谎**（它确实改了它认为的 4 个字段），用户看到的却是「没改对」。

**修复**（`agent/tools/template_fill.py`）：

- **新增第 4 个匹配通道**：`_current_render()` 取该范本最近一次 done 的 `values.render`，keyword 也在**当前成稿值**里匹配。命中项回显「当前值：…」，让模型自证。
- **查询时机收窄**：`current` 只在**带 keyword 时**查（值匹配是「把 XX 改成 YY」的定位入口；无 keyword 不需要，也免得给全量清单再加一次 DB 查询）。配单测 `test_detail_without_keyword_skips_current_lookup` 把「不查」固化成断言（用计数器，不靠 DB 桩的副作用）。
- **`task_id` 可显式钉住**：`detail` 与 `modify` 都接受 `task_id`——同范本多份成稿时，「最近那份」不一定在用户眼前。
- **提示行按 `shown_current` 门控**：只有**真的列出了当前值**才说「用上面『当前值』对应的 key 调 modify」。`cur` 非空但一条值都没显示（全未填 / 查询降级）时，那句提示会让模型去找不存在的东西——这是消息一致性缺陷，不是文案洁癖。
- **keyword 回显截断**（`_MAX_KEYWORD_CHARS = 100`）：值匹配通道**鼓励**用户把一整段原文当 keyword 传，原样回显等于把这段长文本再复制一遍进上下文。
- **工具描述**（LLM 可见，3 处）：`keyword` 参数描述补第 4 通道；`task_id` 描述补 `modify` 用途；正文 `使用时机` 段补「用户给的是**内容而不是字段名**时，先 `detail(keyword=那段内容)` 拿真正持有该值的 key 再 modify，不要因为中文名听起来像就挑一个」。

**已知局限**（写进代码注释与下面 §3）：`_current_render` 未显式钉 `task_id` 时取 `latest_done`，粒度是**租户内的范本**。本来该由「当前流程」收窄，但 `flow_instance_id` 全库写空串（死字段），没有可用信号。缓解：回执必带 `task_id` 与本地化的生成时间，让误选**可见**且可由用户带 `task_id` 纠正。

---

## 3. 设计决策与权衡

### 决策 1：为什么修 4 处而不是「修一处 + 上报限制」

4 个根因分属 4 层（工具语义 / 前端渲染 / 存储桥接 / 前端状态），**任意一处不修，症状 B 都仍然成立**：
- 只修字节 → 预览读的是前端 values，还是旧文案
- 只修 values → 模型改错了字段，刷新后显示的仍是错值
- 只修定位 → 模型改对了，预览与下载都看不到

### 决策 2：状态层「不清」而不是「清完再填」

备选方案是保留清空、在报告 effect 里 `?? templateFillRef.current` 回落。否决理由：`templateFillRef` 就是被清的那个，等于绕一圈回到「不清」；而且会让**填写轮**在流式期间错误地显示上一轮的卡片（本轮进度事件还没到，回落会拿到旧 task_id）。「不清」是更小、语义更直接的不变式：**快照由产生它的那一轮负责替换**。

### 决策 3：权威取数只在「打开预览」时、只在终态

- **打开时**（不是常驻）：不引入新的持续开销。
- **只终态**：`filling` 时 SSE 更新鲜；`failed/cancelled` 无稿可取。
- **失败静默回落**：预览是只读辅助视图，取数失败不该让它变成错误页——落回卡片快照，最坏情况是显示改前内容（与修复前一致，不更差）。

---

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `agent/tools/template_fill.py` | `_current_render` 新增（值反查 + task_id 优先）；`_format_template_detail` 4 通道匹配 + `shown_current` 门控 + keyword 截断；`_modify` 桥接副本 + `task_id` 钉住 + 脏 values 兜底；工具描述 3 处 |
| `test/test_template_fill_tool.py` | 67 例（新增值匹配 6 / current 降级与脏数据 3 / modify 桥接与 pin 6 / detail 不查 current 1 等） |
| `web/src/hooks/use-template-fill-request.ts` | 新增 `fetchTemplateFillTaskProgress` + `TemplateFillProgressData` |
| `web/src/pages/c-chat/template-fill-live-preview.tsx` | 打开时拉权威 values（仅终态）+ filled/unfilled null 语义 + `useMemo` |
| `web/src/pages/c-chat/__tests__/template-fill-live-preview.test.tsx` | 新增 9 例 |
| `web/src/pages/c-chat/flow/flow-ai-panel.tsx` | `handleSend` 不再清空范本快照（3 行删除 + 注释） |

`web/src/pages/agent/*`、`web/src/constants/agent.tsx`、`web/src/locales/zh.ts` 的改动属上一批「画布范本填写工具补齐」，未提交，与本批无关。

---

## 5. 测试

- 后端 `test_template_fill_tool.py` **67 passed**；范本填写相关 10 个套件 **644 passed**。
- 前端 `--testPathPattern="template-fill"` **5 suites / 85 tests passed**。
- 对抗性覆盖（要点）：`latest_done` 返回脏形态矩阵（`values` 为字符串 / `render` 为标量 / `cells` 为字符串 / 全 None）不炸且降级正确；`task_id` 四种非法态（不存在 / 他人 / 别范本 / 非 done）逐一拒绝；桥接失败时主成稿仍在、回执含降级提示；keyword 为 5000 字符时输出被截断；值匹配为空串/未填时**永不命中**（防「空值匹配一切」）；权威响应 `filled: []` 时不得回落卡片的过时中文名。
- **暴露的既存生产 bug**：`_modify` 原 `dict(vals.get("cells") or {})` 在 `cells` 为字符串时抛 `ValueError`，异常冒到 `_invoke` 变成一句用户看不懂的「执行失败」。由 `test_modify_values_non_dict_render_does_not_crash` 击穿 → 加 isinstance 判型。**这是本轮唯一由测试新发现、而非由用户症状反推出来的缺陷。**

### 未覆盖项（需浏览器确认）

**修复 3（`flow-ai-panel.tsx` 的清空删除）没有单测。** 仓库既无渲染 `FlowAiPanel` 的测试，也未 mock `use-send-message`；补测需 ~7 个 mock 块并驱动完整 SSE 生命周期，为 3 行删除引入的脚手架成本远高于收益。该项**必须人肉验收**：流程里完成一次填写 → 说「把 XX 改成 YY」→ 观察卡片是否始终在场。

---

## 6. 遗留

1. **已打开的预览不随同屏 modify 刷新**（无「新轮」信号，见 §2.2）——需关闭重开。
2. **`latest_done` 是租户+范本粒度，不是「本次流程」粒度**（`flow_instance_id` 死字段，见 §2.4）。缓解是回执带 `task_id` 让误选可见；根治需先让该字段有值。
3. **修复 3 无自动化测试**，依赖人肉验收（见 §5）。
