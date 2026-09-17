# 范本填写：已填字段中文名可见 + 按旧值匹配字段

**日期**：2026-09-17
**目标**：解决用户痛点「第一轮 LLM 把范本填完了，我说『把 XX 改成 YY』，LLM 识别不到」——根因有两层：已填字段的**中文名在整条链路上没有任何展示位**，且**匹配规则不允许按旧值定位**。

---

## 1. 背景

### 1.1 症状复现路径

1. 用户在 C 端对话 / 流程页签发起范本填写 → LLM 填完 244 个字段 → 成稿 done
2. 用户想改其中一项，说「把『港里』改成『李港』」
3. LLM 抽不出任何 patch → `intent=noop` → 节点走「轻量复用」原样重渲染 baseline
4. 用户感知为「LLM 完全没听见」

### 1.2 根因两层

**数据层（前端不可见）**：终态只下发 `unfilled`（未填充字段 `[{key,name,required}]`）。已填字段的 `name`（中文名）在任何事件、任何端点、任何 UI 位置都不存在：

- `filled` SSE 事件只有 `values`（key→值）
- 成稿卡片只列「⚠ N 个填写点未填充」
- 实时预览里已填值只有 `title=key`（英文），未填槽位显示英文 key

结果：用户看不到字段叫什么，也就**无法在对话里准确指代**。

**匹配层（LLM 不可用）**：`PATCH_EXTRACT_SYSTEM` 虽已把「当前已填值 current」喂给 LLM，但规则文字只允许按 `name` / `key` 定位、**禁止按 current 定位**。用户最自然的说法（复述旧值）恰好是唯一被禁的那条路径。

### 1.3 第一性原理

用户指代一个字段只有三种可能：**说中文名**、**说英文 key**、**复述已填的旧值**。前两条已被支持，第三条被误伤禁用。同时「可见性」是「可指代」的前提——中文名不上屏，前两条路径对用户也等于不存在。

---

## 2. 设计决策

### 决策 1：`derive_filled` 用「减法」构造，互补性由构造保证

```python
def derive_filled(placeholders, values) -> list[dict]:
    unfilled_keys = {it["key"] for it in derive_unfilled(placeholders, values)}
    return [{"key": it["key"], "name": it.get("name") or it["key"]}
            for it in (placeholders or [])
            if it.get("key") and it["key"] not in unfilled_keys]
```

- 取「有 key 的填写点 − 未填充集合」，**判空真源只有 `derive_unfilled` 一处**。若两处各写一遍谓词，任一漂移都会让 `key→中文名` 映射静默漏项 —— 漏项时 LivePreview 悄悄回落英文 key，用户看不出错但用不了。
- 第二段过滤写法与 `derive_unfilled` **逐字一致**，失败模式（非 dict 项、`values` 非 dict 真值）随之同构。
- **不下发 value**：前端从已下发的 `values` join。`values` 本身已 30–80KB（244 项），`filled` 仅约 13KB，重复传输无意义。
- **不改 `derive_unfilled`**（5 个既有测试 + 3 个调用方；改判空/加 isinstance 防护属行为变更，不在本次范围）。已知弱点记为遗留。

### 决策 2：下发协议（与 `unfilled` 完全同构）

| 项 | 口径 |
|---|---|
| 字段名 | `filled`（注意与模板行的 `status:'filled'` 同名但语义无关：那是任务阶段，这是字段清单） |
| 元素 | `[{key: str, name: str}]`，**保 placeholders 文档序** |
| 下发时机 | 仅终态（`done`/`partial`），与 `unfilled` 同条件 |
| 空值 | 空列表 → **不下发该键**（沿用 `or None`） |
| 缺省语义 | 前端 `if (d.filled)` —— 缺省不清旧值 |
| 落库 | **不落 Redis run snapshot**（API 从 placeholders+values 现算，避免第二真源） |

### 决策 3：key→name 映射由 `filled ∪ unfilled` 合并派生

二者按判空口径**穷尽且互斥**，故合并即全量，**无需新增 `key_names` 字段、不改 `selected` 事件** —— 否则引入两个 name 真源，各自随版本漂移。

已知边界：`filling` 阶段两者都未到达 → 预览暂无中文名（回落英文 key）。对真实用途（终态后「把 XX 改成 YY」）无影响。

### 决策 4：预览中文名穿透 `names` 可选参数

`stylePlaceholderSpan` 是**唯一**写 `title` 的地方，且被 `updateDocxHighlight` 每次 values 变化重跑 → 「渲染完再遍历 span 补 title」会被覆盖，是假降级。必须把 `names` 作为**可选参数穿透** `applyDocxHighlight` / `updateDocxHighlight`：

| 场景 | 正文 | `title` |
|---|---|---|
| 已填 + 有 name | 值 | `中文名（key）` |
| 已填 + 无 name | 值 | `key`（旧行为） |
| 未填 + 有 name | **中文名** | `中文名（等待 AI 填入）` |
| 未填 + 无 name | key | `key（等待 AI 填入）` |

- `span.dataset.phKey = key` **绝不能动**（`focusPlaceholder` 定位链路依赖）；另存 `dataset.phName` 供排查/断言。
- 纯函数 `describePlaceholderSpan(value, name, key)` 抽出承载四象限，DOM 副作用留在 `stylePlaceholderSpan`（本仓库 DOM 测试脚手架已腐坏，纯函数可测）。
- 判空抽成导出纯函数 `isPlaceholderFilled(value)`，口径对齐后端 `derive_unfilled`（`v is None or not str(v).strip()`）：**纯空白算未填**。原先前端用 `v !== undefined && v !== ''` 把纯空白算已填——本需求把 unfilled 清单摆到卡片上之后，同一字段会「卡片列为未填充、预览渲染成无虚线框的空白槽」，两侧自相矛盾且点击定位失去落点。保真路径 `stylePlaceholderSpan`、文本降级路径 `renderText` 同改一处判据。
- **不碰 `highlightDocxRanges`**（review-panel / B端保真预览链路）。

### 决策 5：卡片 UI —— 未填展开醒目，已填折叠收起

- 现有「未填充汇总条」**不动**（待办：默认展开、黄标、必填红标）。
- 新增「已填充 N 个填写点」**折叠区**：默认收起、中性色、**条件挂载**（展开才建 DOM，不是 `hidden`/`max-h-0` —— 244 项常挂 DOM 无意义）。
- 每行：中文名 + `：` + 值（CSS `truncate`，不做 JS 切片以免破坏复制），`title` 带 `${name}（${key}）：${value}`；点击 `setLiveTarget({template_id, focusKey: key})` 复用既有定位链路。
- React key 用 `f.key`（唯一性由 `validate_placeholders` / `_merge_detection` 保证）。
- 折叠组件必须是**独立子组件**（在 `templates.map` 回调里不能用 `useState`）。

### 决策 6：按旧值匹配 —— prompt 放宽 + `current_ok` 确定性闸

`current_ok` 是每项一个布尔，与 `current` 同轮算出。三种失效情形一律 false：

```python
# ① 太短：单字符值（是/无/男/女/0）
len(c) >= MIN_CURRENT_MATCH_LEN          # = 2
# ② 歧义：精确重复 或 互相包含
and len(c) < DEFAULT_HINT_MAX
and counts.get(c, 0) == 1
and c not in ambiguous                   # a in b or b in a → 双方入 ambiguous
# ③ 截断：current 被 _clean_for_prompt 截到 DEFAULT_HINT_MAX
```

- **为什么单字符必须禁用**：③ 的判据是「原话逐字包含该值」。而 `是/无/男/0` 在中文里几乎必然作为子串出现在任意原话中（「但是」「是否」「男女」）→ 必然误命中。**即使清单里唯一也禁用**，宁漏不误（漏了还有 name/key 两条路）。
- **为什么子串包含也要禁用**：真实表单项「数量 5 / 金额 50」「张三 / 张三丰」「朝阳区 / 朝阳区人民政府」。用户说「把 50 改成 60」时，A(current=`5`) 与 B(current=`50`) 都满足「逐字包含」，而 prompt ③ **不提供并列消歧规则** → LLM 可能落到 A、改错字段。故包含关系（任一方向，含精确相等）双方一律 false。
- **歧义计数基于清洗后字符串**：`counts` 建在 `_clean_for_prompt` 之后，故 `张\x00三` 与 `张三` 正确撞值（若建在原始值上会被当成两个不同值而双双放行）。
- **截断**：长值尾部不可复述 → `len >= DEFAULT_HINT_MAX` 一律 false（截断后长度恒为上限，无法区分「原本正好 100」与「被截断」）。
- **不把不可用的 current 清空**：会让 LLM 误判「该字段当前为空」，影响 noop / fill_unfilled 意图判断。
- **不调大 `DEFAULT_HINT_MAX`**：它同时是 `_default_hint`（产值 prompt）的 token 预算。
- prompt 文案必须与代码产生的三种成因**逐字一致**（曾出现文案称「太短」而代码从不为长度判 false 的错位，测试用 marker 守护）。
- prompt 定位优先级：① `name` → ② `key` → ③ 仅前两者都匹配不上时用 `current`（须 `current_ok=true` 且原话逐字包含该值）。
- 三层兜底不变：`direct` 值过 `_apply_constraints`、key 过 `valid` 白名单、**增量 patch 必经人工确认卡**。

---

## 3. 改动清单

### 后端

| 文件 | 改动 |
|---|---|
| `rag/svr/template_fill/executor.py` | 新增 `derive_filled`；新增常量 `MIN_CURRENT_MATCH_LEN=2`；`PATCH_EXTRACT_SYSTEM` 改写（加 `current_ok` 字段说明 + 定位优先级 + 使用条件，三种 false 成因与代码逐字一致）；`extract_patch_values` 先算 `counts` + `ambiguous`（子串包含）再拼 `current_ok` |
| `agent/component/template_fill.py` | 新增 `_render_of(row)` 归一化（非 dict render → `{}`，把崩溃转成「全部未填」）；`_unfilled_of` 改用 `_render_of`；新增 `_filled_of`；终态事件加 `if filled: ev["filled"] = filled` |
| `api/apps/restful_apis/template_api.py` | import `derive_filled`；`build_progress_payload` 同条件派生 `filled`；`build_run_snapshot_payload` item 骨架加 `"filled": None` + filled 分支派生 |

### 前端

| 文件 | 改动 |
|---|---|
| `web/src/hooks/template-fill-stream.ts` | `ITemplateFillFilled` 类型；3 处 `filled?` 类型槽；reducer filled 分支（仅 `filled` 阶段写入）；`buildStateFromRunSnapshot` 映射；`buildKeyNameMap` / `buildFilledRows` 纯函数 |
| `web/src/hooks/use-template-fill-task-poll.ts` | 终态 override 加 `...(d.filled ? { filled: d.filled } : {})` |
| `web/src/pages/c-chat/docx-highlight.ts` | `isPlaceholderFilled` 判空纯函数（对齐后端 strip 口径）；`describePlaceholderSpan` 纯函数；`names?` 穿透 `stylePlaceholderSpan` / `applyDocxHighlight` / `updateDocxHighlight` |
| `web/src/pages/c-chat/template-fill-live-preview.tsx` | `names` memo + `namesRef`；透传给 render effect 与 update effect；`renderText` 用中文名 + 同款判空 |
| `web/src/pages/c-chat/template-fill-progress.tsx` | 新增 `TemplateFillFilledList` 子组件；挂在未填充汇总之后 |

### 测试

- `test/test_template_fill_executor.py`：`derive_filled` 9 例（含分区穷尽/互斥反漂移、脏 values 对抗）+ `current_ok` 11 例（唯一值可用 / 精确重复 / 单字符 / 子串包含双向 / 清洗后撞值 / 截断边界 / 空值 / prompt marker 守护 / 约束兜底）
- `test/test_template_fill_delegate.py`：`TestFilledOf` 6 例（镜像 `TestUnfilledOf`）
- `test/test_template_fill_events.py`：6 例（全填/部分填/全空/failed、畸形 render 耦合）
- `test/test_template_fill_progress_api.py` / `test_template_fill_run_snapshot.py`：11 例
- `web/src/hooks/__tests__/template-fill-stream.test.ts`：+25 例（reducer 写入/阶段隔离/缺省不清/空数组边界/replay 往返/snapshot 映射/两个纯函数/重复 key 契约）
- `web/src/hooks/__tests__/use-template-fill-task-poll.test.ts`：+5 例（终态带 filled/缺省不清/空数组/竞态/SSE 优先）
- `web/src/pages/c-chat/__tests__/docx-highlight-describe.test.ts`：10 例（四象限 + 空白串/null/"0" 边界）

**合计 257 后端 + 75 前端全绿。**

---

## 4. 部署（**已完成 2026-09-17**）

**纯增量字段，前后端可独立部署**：旧前端忽略 `filled`；新前端遇旧后端不下发即回落英文 key，无互锁。

已执行：后端 3 文件成套 SCP + md5 逐一核对 + 容器重启 + import 冒烟；前端 `npm run build` + 就地解包 + nginx reload。**生产数据验证**：容器内以端点同路径跑真实 DB 全部 18 条 done/partial 任务，`filled ∪ unfilled` 恰好覆盖有 key 的填写点集合、零重叠零缺口（典型 `107+137=244`）。详见 CHANGE.md 2026-09-17 条目「部署实测」。

- 后端 **3 文件成套 SCP**：`rag/svr/template_fill/executor.py`、`agent/component/template_fill.py`、`api/apps/restful_apis/template_api.py` → `docker restart docker-ragflow-cpu-1`
- 前端 `npm run build` → `rm -rf dist/*` 解包（**禁用 `mv dist`**，会断 bind mount inode）→ `nginx -s reload`

---

## 5. 遗留（本次不修）

1. `derive_unfilled` 不过滤 `isinstance(it, dict)`；`values` 非 dict 真值时 `(values or {}).get` 会抛 —— 只在测试里固化现状，防后续静默改变失败模式。（调用侧已由 `_render_of` 归一挡掉真实路径）
2. `DEFAULT_HINT_MAX=100` 截断 `current` → 长值字段无法按旧值定位（设计上接受，改大会挤占产值 prompt 预算）。
3. 值歧义（精确重复 / 互相包含 / 单字符）时按旧值定位不可用，只能按 name/key。
4. **`filled` 的长期可恢复性依赖 Redis run snapshot，不能只靠事件回放**：`template_fill_events` 有 64KB 截断挽救（只保头部完整事件），而终态事件位于尾部、体积最大（`values` 30–80KB + 本次新增 `filled` 约 13KB），最易被截掉；超 600s 终态 snapshot TTL 后，历史回放会同时丢 `unfilled`/`filled`（预览回落英文 key）。非本批次引入。
5. 「单字符一律禁用」是**保守过杀**：确实存在「全表只有一个人名字段当前值是这个单字」的场合，此时按旧值定位本可成功。取「宁漏不误」——用户改说中文名即可。

## 6. 明确不做

1. 不加 `key_names` 全量字段、不改 `selected` 事件
2. 不给 `filled` 加上限/截断/分页/搜索
3. 不加 `filled_count`（`t.filled.length` 够）
4. `filled` 不落 Redis run snapshot
5. 不新增端点、不改 `unfilled` 既有 shape
6. 不动 `highlightDocxRanges` / review-panel 链路
7. 不做内联中文名徽标（会改变 Word 排版/槽宽）
8. 不加「代码侧确定性预匹配 current」路径（把语义判断做成正则，收益低误伤高）
