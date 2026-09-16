# 文件审核节点设计（File Review Node）

日期：2026-09-16
状态：设计定稿，待实施
背景：用户在 C 端流程页新建流程上传文件后，希望以自然语言（如"审核投标书"）触发 LLM 对该文件做多轮审核——结合 LLM 内置写作规范 + 用户挂载 KB 检索，产出可点击的标注（anchor + 原文 + 问题 + 修改建议），用户在已有文件审核弹框（review-panel）查看 AI 与手动标注，针对标注触发 LLM 二次处理与修复，多轮叠加直至达标或达上限。

**核心原则（增量）**：本设计是 TemplateFill 的姊妹能力，骨架复用、命名空间独立、现有功能零改动。所有"复用"只发生在「接」与「并行调用」层，不修改被复用对象的内部接口与持久化结构。

---

## 1. 设计总览（与 TemplateFill 同构，独立命名空间）

```
入口层
├── C端对话：Agent FileReview 工具（与 TemplateFill 同级 tool，反问补参数：模板/级别）
└── C端流程：flow「文件审核」节点（与 TemplateFill 节点同形态，独立注册）

执行层（独立服务）
    FileReviewTaskService（detached 后台线程）
    状态机：idle → reviewing → annotated → fixing → re-reviewing → annotated → ... → done
    多轮上限默认3，用户可显式"再修一轮"扩展

展示层（复用）
    review-panel.tsx 零改动（已具备 AI 标注 + 手动批注 + 锚点 + 修复触发交互）
    file-review-progress.tsx（新文件，画布 SSE 进度卡，参照 template-fill-progress.tsx）

持久化层（独立命名空间）
    新增 file_review_round + file_review_annotation 两表
    Redis 键前缀 tpl_review:*
    与 TemplateFill 的 tpl_* / tpl_fill:* 完全解耦
```

**复用边界（硬约束）**：
- 不修改 `agent/component/template_fill.py` 任何代码
- 不修改 `web/src/pages/c-chat/review-panel.tsx` / `Annotation` / `MarginComment` 任何代码
- 不修改 `Annotation`/`MarginComment` 接口（type 字段新增值以**并列 enum**而非破坏性扩展，10.2 节明列扩展点）
- 不修改 `template_fill_*` 表/Redis 键/事件流
- KB 聚合复用 `rag/nlp/search.py` 的 retrieve 接口（已是公共接口），不复制实现

---

## 2. 节点与工具

### 2.1 画布节点 `FileReview`（`agent/component/file_review.py`，新增）

```python
class FileReview(Canvas):
    component_name = "FileReview"

    param_schema = {
        "file_id":        {"required": True,  "type": str},   # 上传文件 doc_id
        "template_id":    {"required": False, "type": str},   # 预置模板 id，留空走 LLM 意图匹配
        "custom_prompt":  {"required": False, "type": str},   # 用户自定义审核要求（最高优先级）
        "kb_ids":         {"required": False, "type": list},  # 检索 KB 列表，留空走任务级默认
        "max_rounds":     {"required": False, "type": int, "default": 3},
    }

    # 启动后台 detached task（与 TemplateFill.invoke 的 detach 模式同构，独立 task 类）
    # SSE 通道 file_review_progress 事件名独立
```

### 2.2 工具 `FileReviewTool`（`agent/tools/file_review.py`，新增）

```python
class FileReviewTool(ToolBase):
    # 与 TemplateFillTool 同形态
    # 用户说"审核这个投标书" → 反问 file_id / template_id
    # 反问完 → 调内部 RPC 启动画布节点同款 task
    # 返回 task_id 给对话层做"画布进度卡"挂载
```

**双入口复用同一 task**（与 TemplateFill 一致）：tool 入口与 node 入口最终都进 `FileReviewTaskService.submit(...)`，由 task_id 串起进度流与持久化。

---

## 3. 预置审核模板（5 套招标场景，LLM 意图匹配 + 用户可指定）

**预置模板存 DB**（`file_review_template` 表，新增，第4节详述），启动时若 `param.template_id` 未传，LLM 看用户原话+文件名首段选1套。

| template_id | 名称 | 适用文件类型 | 关键审查项 |
|---|---|---|---|
| `bid_doc_format` | 投标文件格式规范 | 投标响应 | 章节完整性、签字盖章页、目录/页码/页眉页脚、字体字号、行距 |
| `bid_response_complete` | 投标响应完整性 | 投标响应 | 招标点逐项应答、附件齐全、偏离表填写、应答索引 |
| `bid_substantive_clause` | 实质性条款合规 | 投标/合同 | 招标★号条款、废标项、否决项、付款/工期/违约金/质保等核心条款 |
| `bid_qualification` | 资质合规 | 资质证明 | 营业执照/资质等级/业绩/人员/财务审计报告有效期与一致性 |
| `bid_price_review` | 投标报价审核 | 报价文件 | 报价上限/下限、清单完整性、合算错误、单价合理性、税率一致 |

**优先级**：`custom_prompt` > 用户指定 `template_id` > LLM 意图匹配 > 默认走 `bid_doc_format`。

---

## 4. 数据模型（新增 3 表，前缀 `file_review_`）

> **实施修正（2026-09-16，已落地）**：3 个模型继承项目统一的 `DataBaseModel`（不是裸 `DB.Model`），
> 因此**不手写** `create_time`/`update_time`——框架 `BaseModel` 已提供
> `create_time`/`update_time`（`BigIntegerField`，13 位毫秒，插入/更新自动写）+
> `create_date`/`update_date`（`DateTimeField`，由 `_normalize_data` 从毫秒派生），
> 并自带 `to_dict()` / `to_human_model_dict()` / `query()` 等 service 层依赖的助手。
> `tenant_id` 统一为 `CharField(32, null=False, default="", index=True)`（`""` = 系统预置）。
> 大文本用 `MediumTextField`（MEDIUMTEXT 16MB）而非 `TextField`——TEXT 上限 64KB 会**静默截断**，
> 项目已有 `flow_ai_chat.template_fill_events` 同因事故。规格中标注 LONGTEXT 的列按 MEDIUMTEXT 落地（足够且更省）。
> 预置模板由 `_ensure_file_review_templates()` 按**预置 ID 集合**判定补齐（幂等自愈，失败返回错误文本，
> 由 `migrate_db` 在恢复日志级别后输出——`migrate_db` 开头的 `logging.disable(ERROR)` 会吞掉窗口内的 ERROR 日志）。

### 4.1 `file_review_template` 预置模板表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(64) PK | 同 template_id（如 `bid_doc_format`） |
| name | varchar(128) | 中文显示名 |
| description | text | 适用场景说明 |
| system_prompt | LONGTEXT | 注入 LLM 的系统提示词（含审核要点清单） |
| user_prompt_template | LONGTEXT | 用户提示词模板（含 `{user_query}` `{file_excerpt}` `{references}` 占位） |
| annotation_types | JSON | 该模板关注的问题类型（`format/completeness/clause/qualification/price/other` 子集） |
| enabled | tinyint | 是否启用 |
| tenant_id | varchar(32) idx NOT NULL default '' | 租户（系统预置行 `''`，全员可见） |
| created_by | varchar(32) | 预置行写哨兵 `system`（非真实 user_id） |
| create_time / create_date / update_time / update_date | — | 框架 `BaseModel` 自动维护，不手写 |

预置 5 套模板以 migration 形式随 `db_models.py` 的 `migrate_db` 写入；用户可复制后修改。

### 4.2 `file_review_round` 审核轮次表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(64) PK | UUID |
| task_id | varchar(64) | 画布运行 id（与 TemplateFill 的 canvas_task_id 同空间概念）；单列索引已省略，由下方复合索引左前缀覆盖 |
| file_id | varchar(64) idx | 文件 doc_id |
| round_no | int | 1 / 2 / 3（> 1 即 re-reviewing 语义，无需独立 status） |
| template_id | varchar(64) | 本轮所用模板 |
| user_query | LONGTEXT | 本轮用户原话（修复轮 = "修高中级别问题"等） |
| status | varchar(16) | `reviewing` / `annotated` / `fixing` / `failed` / `done` |
| file_version | varchar(64) | `v1` / `v2` / `v3`（MinIO 文件后缀） |
| minio_path | varchar(256) | 本轮 MinIO 路径（`tpl_review/{task_id}/{file_id}/v1.docx`） |
| summary | LONGTEXT | 本轮 summary（高/中/低问题计数 + 整体评价） |
| llm_raw | MEDIUMTEXT | LLM 输出原始 JSON（兜底，用于复盘/debug；200 条标注中文约 80KB，TEXT 会静默截断） |
| error | text | 失败原因 |
| tenant_id (idx) / created_by | — | 同 4.1；审计时间由框架自动维护 |
| `Meta.indexes` | — | `(task_id, round_no)` 复合索引 |

### 4.3 `file_review_annotation` 标注表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(64) PK | UUID |
| round_id | varchar(64) idx | 关联 file_review_round.id |
| task_id | varchar(64) idx | 画布运行 id（冗余便于直接查文件级历史） |
| file_id | varchar(64) idx | 文件 doc_id |
| file_version | varchar(64) | 标注锚定的版本（v1/v2/v3） |
| anchor | LONGTEXT | 锚点 JSON：docx `{p_hash, offset, run_index}` / xlsx `{sheet, cell}` |
| matched_text | text | 原文匹配片段（用于回放定位校验） |
| type | varchar(32) | `format` / `completeness` / `clause` / `qualification` / `price` / `other` |
| severity | varchar(16) | `high` / `medium` / `low`（值来自 LLM，留余量防 `DataError 1406`） |
| issue | text | 问题描述 |
| suggestion | text | 修改建议 |
| source | varchar(8) | `ai` / `manual` |
| status | varchar(16) | `open` / `fixed` / `new` / `wontfix` |
| prev_annotation_id | varchar(64) null | 多轮关联：第二轮 annotation 若对应第一轮已被修复的 → 填第一轮 id，`status=fixed` |
| tenant_id (idx) / created_by | — | 同 4.1；审计时间由框架自动维护 |
| `Meta.indexes` | — | `(file_id, file_version)` 复合索引；`task_id` 单列索引保留（复合索引不含它） |

**annotation 持久化策略（全量而非快照）**：
- 不只存轮次快照，每条标注独立入库（用户/产品高频诉求：「这文件历史上都有什么问题」「第二轮新增了几个问题」）
- 多轮关联 `prev_annotation_id`（LLM 二次审视时识别"上一轮已存在 → fixed；新出现 → new；仍存 → open"）
- review-panel 查 `WHERE file_id=? AND file_version=?` 即可拿到该版本全部标注（AI + manual）

**Annotation 接口扩展（非破坏性）**：
- `type` 字段追加并列枚举（不删旧值）
- review-panel.tsx 的 `Annotation` 类型新增可选字段 `prev_annotation_id` / `status` / `file_version`，旧数据缺字段默认 undefined
- review-panel 现有组件代码 0 改动，**只在 annotation 渲染处加可选字段回退**（如 status=undefined 时按 open 渲染）

---

## 5. 状态机（多轮上限 3）

```
       用户上传 + 提问
            ↓
        ┌─idle────────────────┐
        │ FileReviewTool 反问 file_id/template_id（缺则补）│
        └──────────────┬──────┘
                       ↓ submit
            ┌─reviewing (round 1)──┐
            │ LLM 第一次审视：KB 聚合 → 标注生成 │
            └──────────────┬──────┘
                           ↓
                  ┌─annotated─┐
                  │ review-panel 显示 v1 标注 │
                  └──────┬─────┘
            ┌────────────┼────────────┐
            ↓            ↓            ↓
        用户手动标    用户点"修复"    用户结束
            ↓            ↓            ↓
        (存 manual   ┌─fixing────┐    done
         annotation) │ LLM 改 v1 → v2 │  (round_no=1 done)
                     │ 旧标注 status=fixed │
                     │ 新问题 status=new │
                     └──────┬─────┘
                            ↓ round_no=2
                  ┌─re-reviewing─┐
                  │ LLM 第二次审视 v2 │ ← status∈{open,new} 入 prompt
                  └──────┬──────┘
                         ↓
                    ┌─annotated─┐
                    │ review-panel 显示 v2 标注 │
                    └──────┬─────┘
              ┌─────────┼─────────┐
              ↓         ↓         ↓
          手动标     修复(→v3)    结束
              ↓         ↓         ↓
           (manual) ┌─fixing──┐  done
                    └────┬────┘
                         ↓ round_no=3
                    re-reviewing v3
                         ↓
                    annotated v3
              ┌─────────┼──────────┐
              ↓         ↓          ↓
          手动标     "再修一轮"    结束
              ↓         ↓          ↓
           (manual)  round_no++  done
                    (上限可扩展)

默认 max_rounds=3；第 3 轮 annotated 后无"修复"按钮，仅"再修一轮（手动启用 round 4）"
```

**轮次判定**：`SELECT MAX(round_no) FROM file_review_round WHERE task_id=? AND status IN ('done','annotated')`；等于 max_rounds 则禁用修复按钮。

---

## 6. KB 聚合策略（全检全取 + 按轮 token 预算）

> **实施修正（2026-09-16，T3 落地后同步）**：原草图为「api 层 async 函数内部做检索 + 截断」，
> 落地时按执行层解耦原则拆成两半，并以实际代码为准：
>
> - **检索 I/O 在 `rag/svr/file_review/executor.py`（T6）的 `retrieve_kb_chunks`**：调
>   `settings.retriever.retrieval(query, embd_mdl, tenant_ids, kb_ids, page=1, page_size=5)`，
>   取返回的 **ranks 容器**里的 `ranks["chunks"]` 再往下传。参数需 embd_mdl/tenant_ids，
>   是 async，而执行层跑在守护线程里，故包一层 `asyncio.run`。
> - **聚合 + 截断是纯函数 `rag/svr/file_review/kb_aggregator.py`（T3，已实现）**：
>   `aggregate_references(*, kb_chunks, budget) -> str`，不 import settings/Quart，可独立单测。
>   `kb_chunks` 传成 dict（即误把 ranks 容器整个传进来）会抛 `TypeError`——刻意的响亮失败，
>   避免"零参考"静默跑完。
>
> **只产片段、不产标题**（T3 质量审查后修正）：返回值是填进模板 `{references}` 占位符的
> **槽位内容**。5 套预置模板自己已写了 `用户需求：{user_query}` 与 `参考资料：\n{references}`
> 两行标题，槽位填充方再输出一遍标题，成稿 prompt 会渲染出「参考资料：→用户需求：→
> 参考资料：」的嵌套重复。故本函数**只输出编号片段**（`[i] doc=<名>\n<正文>`），无任何
> 标题行；无可用片段时返回空串，模板渲染出空的 `参考资料：` 段即为「本次没有参考资料」。
>
> **截断语义（已锁定，测试依赖）**：`budget` 是**返回串**的 token 上限，且按
> `"\n".join(候选)` 的真实输出串计数——不是各段 token 之和（`join` 会插入分隔符、BPE 在
> 段边界可能合并出不同 token，实测头部 sum=15 / join=17，旧口径在 budget=23 时放行了
> 25 token）。条目**整条进或整条不进**（半条标准条款截在句中比没有更易误导 LLM）；
> 超预算即 break，不跳过靠前大块去塞靠后小块（保持检索相关度排序）；编号按已输出顺序
> 连续递增。

```python
# rag/svr/file_review/kb_aggregator.py（纯函数，已实现）
def aggregate_references(*, kb_chunks: Iterable, budget: int) -> str:
    # chunk 为 dict（rag/nlp/search.py retrieval() 产出形状）：
    #   {"chunk_id","content_with_weight","doc_id","docnm_kwd","kb_id",...}
    # 正文回退 content_with_weight → content → text；文档名回退
    #   docnm_kwd → doc_name → doc_id → chunk_id → "?"
    # 非 dict / 无正文 → 跳过（不占编号不占预算）；返回串 token 数 <= budget
```

**token 估算复用**：`common/token_utils.py` 的 `num_tokens_from_string`（公共工具，30+ 处调用点）。
（原草稿写的 `rag/llm/tokenizer.py` 不存在，属笔误。）

---

## 7. 文件多版本与修复机制（就地修改 + 增量记录）

**MinIO 路径**：`tpl_review/{task_id}/{file_id}/v{round_no}.docx`

**修复流程**（修复轮 LLM 改文件 + 标注）：
1. 下载当前版本（v_n）→ 解析（docx 用 python-docx，xlsx 用 openpyxl）
2. LLM 接收：v_n 全文 + status∈{open,new} 的 annotation + 用户本轮原话 + template prompt + KB references
3. LLM 输出 `[{find, replace, reason}]` patch 列表（find=精确原文匹配；replace=替换内容；reason=本条修复理由）
4. 后端严格 find-match（find 必须在原文中**唯一**出现；不唯一则保留该 annotation 并 error.log 记录，避免改错）
5. 应用 patch 生成 v_{n+1}，上传 MinIO
6. **annotation 状态更新**（在 `file_review_annotation` 表上 UPDATE）：
   - 本轮被改掉的（其 find/replace 在 v_{n+1} 不再存在）→ status=fixed，prev_annotation_id 指向上轮
   - 本轮新发现的 → INSERT，status=new
   - 上一轮仍 open 未改的 → 保留 status=open（LLM 可能选择不动）
7. 用户手动标 wontfix → 后续轮 prompt 排除该 annotation（不删，便于审计）

**回退机制**（不做 YAGNI）：
- 不做版本回退按钮（用户想回退直接换 v_n 重新审核一轮即可，避免双向同步复杂度）

---

## 8. SSE 流式事件（独立事件通道 `file_review_progress`）

```typescript
// web/src/hooks/file-review-stream.ts（新文件，与 template-fill-stream.ts 并列，零复用）
export interface IFileReviewEvent {
  stage:
    | 'started'        // task 创建
    | 'reviewing'      // 审视中（每 template_id 一条，可批量）
    | 'annotated'      // 本轮标注完成（含 summary/annotation 计数）
    | 'fixing'         // 修复中
    | 'file_version'   // 新版本生成（file_id/version）
    | 'done'           // 全部完成
    | 'failed'         // 失败
    | 'heartbeat';     // 等待期保活（与 TemplateFill 同款 30s）
  round_no?: number;
  template_id?: string;
  template_name?: string;
  // ... 与 TemplateFillEvent 类似，独立 type
}
```

事件归约 `applyFileReviewEvent` 与 `applyTemplateFillEvent` 完全独立（不互相 import）。SSE 端点 `GET /file/review/{task_id}/progress` 与 TemplateFill progress 同形态独立部署。

---

## 9. REST API（新增 5 端点，路径 `/file/review/*`）

| 端点 | 用途 |
|---|---|
| `POST /file/review/start` | 启动审核（tool 入口，task_id 由后端生成） |
| `GET /file/review/{task_id}/progress` | SSE 流式进度（独立事件名 `file_review_progress`） |
| `POST /file/review/{task_id}/fix` | 用户触发修复（body: `{levels: ["high","medium"], user_query: "..."}`） |
| `POST /file/review/{task_id}/annotation` | 手动加批注（body: `{anchor, matched_text, type, severity, issue, suggestion, file_version}`） |
| `POST /file/review/{task_id}/finish` | 显式结束（达到 3 轮上限或用户主动） |
| `GET /file/review/template/list` | 预置模板列表（前端节点配置下拉用） |
| `GET /file/review/{task_id}/annotations` | 查全部标注（review-panel 加载用，支持 `?file_version=v1` 过滤） |

**鉴权**：所有端点 login_required；task_id 所属 tenant 校验（复用 `current_user.id` + tenant_id 现有模式）。

---

## 10. 前端组件（新增 1 组件 + 1 reducer，零修改现有组件）

### 10.1 新增

| 文件 | 职责 |
|---|---|
| `web/src/hooks/file-review-stream.ts` | 事件类型 + 归约函数（独立于 template-fill-stream.ts） |
| `web/src/pages/c-chat/file-review-progress.tsx` | 画布进度卡（SSE 订阅 + 轮次摘要 + 跳 review-panel 按钮） |
| `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx` | 单元测试 |
| `web/src/hooks/use-file-review-request.ts` | API 调用封装（含 `fixFileReview` / `addAnnotation` / `finishReview`） |

### 10.2 复用（接，不改）

| 文件 | 复用方式 |
|---|---|
| `web/src/pages/c-chat/review-panel.tsx` | **零改动**，通过 props 传入 annotations（由 file-review-progress 加载 + 透传） |
| `web/src/pages/c-chat/annotation/annotation.tsx` | **零改动**，复用 AI + manual 标注渲染逻辑 |
| `web/src/pages/c-chat/annotation/margin-comment.tsx` | **零改动**，复用手动批注栏 |
| `flow-panel.tsx` | flow 节点接入时**新增**「文件审核」节点类型，不动 TemplateFill 节点 |
| `c-chat index.tsx` | 对话侧**新增** FileReviewTool 同形态接线，**不动** TemplateFillTool |

**review-panel.tsx 的 Annotation 数据契约**（已存在接口的扩展点）：
- Annotation 类型新增可选字段 `prev_annotation_id?: string` / `status?: 'open'|'fixed'|'new'|'wontfix'` / `file_version?: string`
- **组件内现有渲染分支零改动**：所有新增字段为 undefined 时走旧逻辑（review-panel 旧版本不感知多轮）
- **多轮状态颜色通过 file-review-progress 在外层包一层注入**：加载 annotations 后，按 `status` 给每个 annotation 注入 `className`（fixed→绿、new→黄、open→红、wontfix→灰），再传给 review-panel——review-panel 仅读 props 渲染，组件源码不动
- 总结：扩展点 ① 类型层（可选字段） ② 外层适配层（注入 className），review-panel/Annotation/MarginComment 内部 0 改动

### 10.3 节点编辑面板（B端）

- 在 agent/canvas 编辑器（`web/src/pages/agent/`）的节点调色板新增「文件审核」分组，与「范本填写」同级
- 节点配置表单：file_id（下拉选文件）、template_id（下拉选预置模板/留空 LLM 匹配）、custom_prompt（textarea）、kb_ids（多选）、max_rounds（number）

---

## 11. 与 TemplateFill 的对照（同构保证阅读一致）

| 维度 | TemplateFill | FileReview |
|---|---|---|
| 入口 | FillTemplate 工具 + TemplateFill 节点 | FileReview 工具 + FileReview 节点 |
| Task 表 | `tpl_fill_task` | `file_review_round`（每轮1行） |
| Detached task | `TplFillTaskService`（rag/svr/template_fill/executor.py） | `FileReviewTaskService`（rag/svr/file_review/executor.py） |
| SSE 事件 | `template_fill_progress` | `file_review_progress` |
| Redis 键前缀 | `tpl_fill:*` | `tpl_review:*` |
| 状态机 | selected→filling→filled→done | idle→reviewing→annotated→fixing→re-reviewing→done |
| KB 聚合 | 同 retrieve 接口（无复制） | 同 retrieve 接口（无复制） |
| 渲染器 | docxtpl/openpyxl | python-docx/openpyxl（patch 应用模式） |
| 进度卡组件 | `template-fill-progress.tsx` | `file-review-progress.tsx` |
| 流类型/归约 | `template-fill-stream.ts` | `file-review-stream.ts` |

---

## 12. 不做的事（YAGNI / 增量边界）

- **不改 TemplateFill 任何代码/表/键/事件**——本设计与 TemplateFill 解耦是首要约束
- **不改 review-panel.tsx / Annotation / MarginComment 内部实现**——只通过 props 注入新数据契约，组件代码不动
- **不做文件版本回退**——用户想回退自行重审一轮，避免双向同步
- **不做 LLM 模型选择**——沿用全局默认 LLM（与 TemplateFill 同策略）
- **不做实时协作标注**——单用户多轮模型
- **不做模板编辑 UI（B 端）**——B 端模板编辑留 v2，本期仅写 DB 行 + migration
- **不做审核知识库自动沉淀**——LLM 学到的"招标规范"暂不进 KB（沉淀机制 v2 评估）
- **不做 docx 格式深度保护**——python-docx 默认保留段落/run 结构，不接 docxtpl（与 TemplateFill 路径分歧）
- **不做 annotation diff 视图**——历史标注按 file_version 切换即可

---

## 13. 测试与验收

### 后端

- 单元：FileReviewTaskService 各 stage 单测（启动/审视/标注生成/修复 patch 匹配/new 标注识别/wontfix 排除/失败兜底）
- 对抗：`find` 不唯一 → 跳过该 annotation 记 error；annotation JSON 坏 → 单条跳过整轮成功；KB 0 命中 → 空 references 仍可标注；docx 解析失败 → 失败态不挂起；高并发同 task 启动 → 幂等返回已有 task_id
- 集成：`uv run --no-sync pytest` 全绿，新文件 `test_file_review_service.py` / `test_file_review_api.py` / `test_file_review_executor.py`

### 前端

- 单元：file-review-stream.ts 归约函数 + use-file-review-request hook + file-review-progress 组件渲染各 stage
- 集成：build 通过，eslint 0 error
- 验收路径：
  1. 对话侧发"审核投标书"+ 上传文件 → 自动进入审核 → review-panel 显示 v1 标注
  2. 标 manual annotation → DB 落 manual 行
  3. 点"修复高中级别" → v2 生成 → 旧 high/medium 标 fixed → 新 high/medium 标 new → review-panel 切换显示 v2
  4. 第 3 轮后无修复按钮，点"再修一轮"扩展到 v4
  5. 流程页 FileReview 节点配置 → 跑通同上
  6. 刷新页面：标注历史完整不丢，正在审核的轮次经轮询恢复
  7. 并行 TemplateFill 节点运行不受影响（独立性验证）

### 部署约束

- **必须先后端再前端**：先 db_models.py migration + executor + API + node + tool SCP+重启；再前端 build
- 智能体 DSL JSON 模板无需变更（FileReview 节点注册后画布编辑器自动发现）

---

## 14. 关键文件清单（最终）

**后端新增**：
- `api/db/db_models.py` — `FileReviewTemplate` / `FileReviewRound` / `FileReviewAnnotation` 模型 + migrate_db 写预置 5 套
- `api/db/services/file_review_service.py` — CRUD + 任务编排
- `rag/svr/file_review/__init__.py` / `executor.py` / `patcher.py` / `kb_aggregator.py` — 执行层（无 Quart 依赖）
- `agent/component/file_review.py` — 画布节点
- `agent/tools/file_review.py` — 对话工具
- `api/apps/restful_apis/file_review_api.py` — REST 端点（含 SSE）

**前端新增**：
- `web/src/hooks/file-review-stream.ts`
- `web/src/hooks/use-file-review-request.ts`
- `web/src/pages/c-chat/file-review-progress.tsx`
- `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx`
- `web/src/locales/zh.ts` — 新增 key（en.ts 不动，本项目中文约定）

**后端/前端零修改确认清单**：
- `agent/component/template_fill.py` / `web/src/pages/c-chat/template-fill-*.tsx` / `template-fill-stream.ts` / `tpl_*` 表 / `tpl_fill:*` Redis 键 → 全部不动
- `web/src/pages/c-chat/review-panel.tsx` / `annotation/` / `margin-comment.tsx` → 内部代码不动，仅 props 契约兼容