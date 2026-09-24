# 范本填写检索增强：二档全文降级 + 用户输入实体分析 — 设计文档

- 日期：2026-09-24
- 状态：设计已确认（口头），待实施
- 方案选型：方案 A（检索阶段内联降级），已否决 B（生成后二轮补填）与 C（A+B 都做）

## 1. 背景与问题

范本填写（FillTemplate 画布节点 / 对话工具）现状管线：每个填写点用
`retrieval_query or name or key` 构造检索词逐槽 ES 向量检索 → LLM 按证据批量产值，
无证据输出 null → missing → default_value 兜底/留空交人工。

两个断层：

1. **检索不到即留空**：填写点逐槽检索无命中时直接走留空，没有降级手段。
   典型场景：填写点检索词（如「工程概况」）在用户发布的 KB 文档里有对应内容，
   但用填写点名称查向量不中。
2. **用户原话里的信息没有被结构化利用**：用户输入如
   「根据市政房建竞争性谈判文件，帮我生成一份“莆美镇华龙社区办公场所装修项目”
   竞争性谈判文件，采购人：福建省漳州市云霄县莆美镇华龙社区居民委员会，
   代理：福建品辰工程项目管理有限公司，采购预算625856.53元，最高限价566709.07元」
   ——其中明确给出的字段值（采购人/代理/预算/限价）目前只作为 prompt 背景参数，
   不直填、不参与检索；描述性内容（「市政房建竞争性谈判文件」）也没有被识别为
   KB 检索语境。

目标：用户输入理解要**灵活、高可信**——LLM 分析原话，区分「直填值」与「KB 检索
语境」；检索要有**两级兜底**——第一档按填写点检索，检索不到降级为全文宽检索，
LLM 基于匹配内容做上下文编写。

## 2. 总体数据流（方案 A）

```
用户原话
  │
  ├─ ① 实体分析 LLM（画布节点确认阶段，新增 1 次调用，与 predict_changed_fields 并行）
  │     输入：用户原话 + 该范本填写点清单
  │     输出：direct（原话明确给出的字段值→填写点key）
  │           entities（项目名称等核心实体 + __context__ 检索语境）
  │
  ├─ ② 确认卡：direct 值作为 candidates[].direct_value 预填输入框
  │     → 用户确认后走既有 direct_values 通道（零改动）
  │
  └─ ③ executor：第一档逐槽检索 → 证据为空的槽用「核心实体+填写点名称」二档宽检索
        → 命中片段标记 source="fulltext" 并入证据
        → LLM 产值（fulltext 字段允许归纳编写，仍禁编造）
```

改动文件：

| 文件 | 改动 |
|------|------|
| `rag/svr/template_fill/executor.py` | 新增 `extract_entities`；`_retrieve_all`/`retrieve_all_shared` 二档降级；`_build_msg`/`GENERATE_SYSTEM` fulltext 适配；`CANVAS_RESERVED_KEYS` 追加 `_entities` |
| `agent/component/template_fill.py` | 画布节点调 `extract_entities`（与预判并行）；`_confirm_changed_fields` 弹卡条件扩展 + candidates 带 `direct_value` + 兜底分支带预填；`_canvas_task_params` 写 `_entities` |
| `api/apps/restful_apis/template_api.py` | 零改动（`create_fill_task` 剥离逻辑基于 `CANVAS_RESERVED_KEYS` 自动跟随） |
| `web/src/pages/c-chat/template-fill-confirm-card.tsx` | 全量模式渲染 `direct_value`（增量模式已有该机制，仅确认类型定义与文案） |

## 3. 实体分析 LLM

### 3.1 extract_entities（executor 新增纯函数）

签名：`extract_entities(tenant_id, query, placeholders) -> {"direct": dict, "entities": dict}`

- **一次 LLM 调用同时完成**：实体抽取 + 语义匹配填写点 + 分类（直填 vs KB 语境）。
- 输入：用户需求原话（`_clean_for_prompt` 截断）+ 范本 llm 填写点清单
  （key/name/description，同 `_build_msg` 清洗口径）。
- 输出 JSON 契约：
  ```json
  {
    "direct": {"采购人": "福建省漳州市云霄县莆美镇华龙社区居民委员会", ...},
    "entities": {"项目名称": "莆美镇华龙社区办公场所装修项目",
                 "__context__": "市政房建竞争性谈判文件", ...}
  }
  ```
- **高可信闸（prompt 硬约束）**：
  - `direct` 只输出原话中**明确给出值**且能语义对应填写点的项；拿不准一律不输出。
  - 描述性/背景性内容（如「根据市政房建竞争性谈判文件」）归入 `entities` 固定键
    `__context__`，作检索语境，**永不直填**。
  - key 必须来自给定填写点清单（编造 key 代码端二次过滤丢弃）。
- 代码端防御：
  - direct 值过 `_apply_constraints`（类型/最大长度约束闸）；非 str 值 str 归一或丢弃。
  - 实体值截断（上限 `PARAM_VAL_MAX=100`）；原话截断（`QUERY_MAX` 同级）。
- **失败兜底**：LLM 异常/超时/JSON 不可解析 → `{"direct": {}, "entities": {}}`
  （不阻塞主流程，行为退化为现状）。

### 3.2 画布节点调用点（template_fill.py）

- 时机：`_confirm_changed_fields` 内，与 `executor.predict_changed_fields` 并行
  （asyncio.gather），共享取消探针；`GenerateCancelled` 同现状转 `_FillCancelled`。
- 每个选中范本各调一次（placeholders 不同）。

### 3.3 确认卡适配（_confirm_changed_fields）

- 全量模式 `confirm_templates[].candidates[]` 增带 `direct_value`（extract 的 direct
  映射按范本分组后注入；增量模式已有该字段，语义一致）。
- **弹卡条件扩展**：现状「无默认值字段→跳过确认」；新增「direct 非空→也弹卡」。
  即：`default_map` 与 `direct` 皆为空才跳过（维持现状行为）。
- 兜底分支（超时/Redis 异常/未确认）：fallback `values` 带 direct 预填值
  （与增量模式 `fallback_values` 同语义；valid 过滤已有）。

### 3.4 params 传递

- `CANVAS_RESERVED_KEYS` 追加 `"_entities"`：画布节点 `_canvas_task_params` 写入
  （dict[str,str]，含 `__context__`）。
- 不变式维持：「params 带保留键 ⇔ 画布节点写入了确认决策」——画布节点总是写
  `_entities`（含空 dict）；`create_fill_task` 的 REST 剥离基于该集合自动生效；
  `is_canvas` 判定（`any(k in params)`）自动跟随，无需改 REST 层。

## 4. 二档全文降级（executor 检索阶段）

`_retrieve_all` 与 `retrieve_all_shared` 两处同构改造：

1. 第一档 gather 完成后，收集**证据为空**的 llm 槽。
2. 触发条件：任务 params 带 `_entities`（仅画布链路启用；B端/REST/dry_run 无实体
   不做二档——避免无实体宽查放大噪声）。
3. 二档检索词：`核心实体值 + 填写点名称` 组合。实体值取法：`entities` 中
   **实体名**（键名，如「项目名称」）含项目/名称/标题语义的优先，最多取
   2~3 个实体值拼接，再拼 `name or key`；`__context__` 也参与拼接（排在实体值后）。
4. 二档检索参数：`top_k = min(原值×2, TOP_K_MAX=20)`；
   阈值 `FULLTEXT_SIMILARITY_THRESHOLD = 0.1`（低于现状 `SIMILARITY_THRESHOLD=0.2`，
   宽匹配）。`retrieve_slot` 增加可选 `similarity_threshold` 参数（默认值保持现状）。
5. 命中片段打标 `{"source": "fulltext"}` 并入该槽证据（该槽第一档为空，即槽内
   全部为二档片段）；evidence 同步。
6. 第二轮 gather 共享同一 `sem` 并发闸与 `ctx`（检索上下文复用）；取消探针/
   单槽失败降级语义与第一档完全一致（失败→该槽保持空证据）。
7. `retrieve_all_shared` 去重键 `(top_k, query)` 扩展为 `(tier, top_k, query)`
   防一二档同词碰撞。

### 4.1 LLM 产值 prompt 适配

- `_build_msg`：证据行对 `source == "fulltext"` 的片段改标签
  `[全文匹配 片段N]`（普通片段维持 `[片段N]`）。
- `GENERATE_SYSTEM` 追加一条规则：
  「标注[全文匹配]的字段，可基于证据上下文归纳编写；仍禁止无中生有。」
- 普通字段规则不变（只准依据证据作答）。

## 5. 边界与失败路径

| 场景 | 行为 |
|------|------|
| 用户原话无实体/抽取失败 | direct={}，确认卡条件回到现状；executor 无 `_entities` 不做二档（纯现状） |
| 二档也检索不到 | 槽证据保持空 → LLM 出 null → 现有 missing → 留空交人工 |
| 增量模式（有 baseline） | incremental_overrides 路径**不做**实体预填（patch 流程已有自己的 direct 抽取），互不干扰 |
| B端表单任务 / dry_run | 无 `_entities` 保留键 → 全部走现状，零行为变化 |
| direct 匹配的 key 不在 valid 集合 | 确认载荷消费处已有 valid 过滤，编造 key 自动丢弃 |
| 实体值超长 | prompt 输入前截断（复用 `_clean_for_prompt`，实体值上限 PARAM_VAL_MAX） |
| 二档槽位失败 | 与第一档同口径：warning + 该槽空证据，不中断整单 |

## 6. 测试

- `extract_entities` 对抗用例：原话无实体 / LLM 返回编造 key / 值非字符串 /
  超长原话 / JSON 畸形 / 直填与语境混合输入（用户给的示例原话作 fixture）。
- 二档触发：空槽才触发 / 有证据不触发 / 无 `_entities` 不触发 / 二档取消穿透 /
  fulltext 打标进 evidence / shared 版去重键含 tier 不碰撞 / 阈值与 top_k 放宽正确。
- 确认卡：direct_value 下发 / 无默认值范本因 direct 弹卡 / 兜底分支带预填 /
  valid 过滤编造 key / 前端全量模式渲染预填（vitest）。
- prompt 构建：fulltext 标签渲染、普通片段不受影响。

## 7. 明确不做（YAGNI）

- 不做生成后二轮补填（方案 B 已否决）。
- 不做 B端表单任务的二档（无实体来源，留待后续有需求再议）。
- 不新增 SSE 事件/进度阶段（status 仍 retrieving，前端进度卡零改动）。
- 不改存量范本识别、确认卡交互结构、unfilled/filled 派生口径。
