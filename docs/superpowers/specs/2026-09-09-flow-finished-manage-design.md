# C 端「已结束流程」维护页设计文档

日期：2026-09-09
状态：已与用户确认设计，待出实施计划

## 1. 背景与目标

C 端「流程」页签（`web/src/pages/c-chat/flow/`）现有左侧列表只有 待我处理/我发起的/我参与 三个视角，已结束（archived/cancelled）的流程与进行中流程混在一起，发起人缺少对已结束流程的统一维护入口。

本设计新增一个**流程页签内的独立管理视图**：发起人可查看自己相关的已结束流程列表，并支持 查看 / 软删除（可恢复）/ 再次发起 / 重新激活。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|--------|------|
| 页面位置 | 流程页签内独立管理视图，整区切换，可返回工作台 |
| 能力范围 | 查看 + 软删除 + 再次发起 + 重新激活 |
| 软删除语义 | 只打标记，DB 与 MinIO 文件全保留；列表默认隐藏 |
| 回收站 | 管理页筛选项含「回收站」，可查看软删流程并一键恢复；不提供彻底删除入口 |
| 重新激活语义 | 状态回到发起人节点（initiator），current_version_id 不变，全部历史保留 |
| 再次发起 | 不新增后端端点，前端复用 POST /flow，预填标题/参与人/最终版本文件（仅 doc/docx） |
| 常规列表行为 | 上线后 待我处理/我发起的/我参与 只显示进行中流程，终态流程统一去管理页 |
| 后端方案 | 方案 A：扩展现有 GET /flow/list + 新增 delete/restore/reactivate 三个动作端点 |
| 操作权限 | 删除/恢复/重新激活/再次发起预填 仅发起人；「我参与的」视角仅可查看 |

## 3. 数据模型

`flow_instance` 表新增 2 个字段（`api/db/db_models.py` + 项目初始化脚本同步，保证迁移部署安全；存量行取默认值）：

```
deleted       SmallInt  默认 0     -- 软删标记：0 正常 / 1 已软删
deleted_time  BigInteger 可空      -- 软删时间（毫秒时间戳）
```

不新增表；版本/批注/AI记录随主表状态存在，软删不清理任何数据。

## 4. 后端

### 4.1 列表查询（FlowInstanceService.list_for_user 扩展）

签名扩展为 `list_for_user(user_id, scope, status=None)`，返回 `(items, total)` 不变：

| 查询场景 | 规则 |
|---|---|
| 常规 scope（todo/initiated/joined/all） | 统一加 `deleted=0` 且排除终态（archived/cancelled），常规列表只留进行中 |
| 管理页 status=finished | `deleted=0` 且 status ∈ (archived, cancelled)，限 scope=initiated/joined 语义（我发起的/我参与的） |
| 管理页 status=archived / cancelled | `deleted=0` 且 status 等于该值 |
| 管理页 status=deleted（回收站） | 只查 `deleted=1` 且 `initiator_id=当前用户`（回收站只看自己删的） |

`todo` 角标轮询走同一查询，软删/终态自然消失。

### 4.2 新增动作端点（均 `@login_required`，仅发起人）

```
POST /flow/<flow_id>/delete       软删除
POST /flow/<flow_id>/restore      恢复
POST /flow/<flow_id>/reactivate   重新激活
```

- **delete**：仅终态（archived/cancelled）且 `deleted=0` 可删；置 `deleted=1, deleted_time=now`。非发起人 → PermissionError；进行中 → ValueError。
- **restore**：仅 `deleted=1` 可恢复；置 `deleted=0, deleted_time=None`。重复恢复幂等返回成功。
- **reactivate**：仅终态且 `deleted=0` 可激活；`status → initiator`，`current_version_id` 不变；leader/handler 账号已删除/禁用时拒绝并提示；成功后复用现有通知系统告知 leader/handler「流程已重新激活」。
- 所有状态变更使用**前置状态条件更新**（乐观锁，沿用现有 submit/archive 模式），并发冲突返回「流程状态已变化」。
- 复用现有 `_require_participant` + 发起人校验；错误码沿用 `_err` 约定。

### 4.3 flow_app.py 路由注册

`GET /flow/list` 新增可选参数 `status`（取值 finished/archived/cancelled/deleted，非法值返回 101）；三个新端点按现有路由风格注册。

## 5. 前端

### 5.1 flow-panel.tsx（工作台）

- 新增视图状态 `view: 'workbench' | 'manage'`，默认 workbench。
- 左列表顶栏「+」旁加「已结束流程」入口按钮（纯图标入口，本期不做数量角标，避免额外轮询）。
- 点击切换到管理视图，整区替换（左右布局让位）。

### 5.2 新组件 flow-manage.tsx（管理视图）

```
┌──────────────────────────────────────────────────────┐
│ [← 返回] 已结束流程   [我发起的|我参与的]  筛选: 全部/已归档/已作废/回收站 │
├──────────────────────────────────────────────────────┤
│ 表格：标题 | 状态 | 领导 | 处理人 | 最后更新 | 操作          │
│  操作列（我发起的）：查看 | 再次发起 | 重新激活 | 删除        │
│  操作列（我参与的）：查看                                │
│  操作列（回收站筛选下）：恢复 | 查看                        │
└──────────────────────────────────────────────────────┘
```

- 默认视角「我发起的」、默认筛选「全部（已结束未删除）」。
- 点「查看」→ 整区切换为现有 `FlowDetail`（复用，含返回按钮回管理页；FlowDetail 已支持终态只读展示）。
- 「再次发起」→ 复用 `CreateFlowDialog`：预填原标题、领导、处理人；原流程最新版本为 doc/docx 时自动 fetch 该版本（现有 download 端点）转 File 对象预填初始文件，其他格式（如 AI 产出 .md）不预填。
- 「重新激活」「删除」弹二次确认；「恢复」直接执行。
- 列表为空 / 回收站为空分别给空态文案。
- `web/src/services/flow-service.ts` 补 listFlows 的 status 参数与 3 个动作函数。

### 5.3 文案

前端文案只用中文，新增 key 只加 `zh.ts`，不同步 `en.ts`。

## 6. 异常与边界

- 并发：三个动作端点均带前置状态条件更新，冲突方收到「流程状态已变化」提示后刷新。
- 软删流程自动从 todo 角标、常规列表、通知等所有 `deleted=0` 查询消失。
- 重新激活后版本时间线保留全部历史，发起人从 initiator 节点继续正常流转。
- leader/handler 被禁用时：管理页可查看（详情只读，沿用现有逻辑），重新激活被拒绝并提示。
- 再次发起产生的新流程与原流程无 DB 关联字段（不加引用，YAGNI）。

## 7. 测试（对抗性）

后端单测（`test/` 下对应目录）：

- 非发起人调用 delete/restore/reactivate → 403/权限错误。
- 对进行中流程 delete/reactivate → 拒绝；对未删除流程 restore → 幂等成功；重复 delete → 拒绝。
- 并发 reactivate：两个请求只有一个成功。
- list 组合：status=deleted 只返回本人软删；常规 scope 不含终态与软删；status 非法值 → 101。
- reactivate 时 leader 被禁用 → 拒绝并提示。

前端：`npm run build` 通过 + 手动冒烟（发起 → 走完归档 → 工作台列表不再显示 → 管理页可见 → 删除 → 回收站可见 → 恢复 → 重新激活 → 详情历史完整）。

## 8. 非目标（本期不做）

- 彻底删除（物理清理 DB + MinIO 文件）
- 已结束流程导出/归档到知识库或文件中心
- 流程模板化、再次发起的流程间关联追溯
- 管理页搜索框（列表量小，筛选够用；后续可加）

## 9. 部署注意

后端成套 SCP：`api/db/db_models.py`、`api/db/services/flow_service.py`、`api/apps/restful_apis/flow_app.py` + 初始化脚本同步；前端 `npm run build` 标准部署。部署后冒烟：import 全部改动模块 + 走一遍 删除→恢复→重新激活 链路。

## 10. CHANGE.md

实施完成后按全局规则在项目 `CHANGE.md` 追加迭代记录，并同步项目 `CLAUDE.md` 参考文档表。
