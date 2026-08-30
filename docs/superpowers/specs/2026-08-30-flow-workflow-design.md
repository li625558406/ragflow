# C 端「流程」页签设计文档

日期：2026-08-30
状态：已与用户确认设计方向，待出实施计划

## 1. 背景与目标

在 C 端对话页（`web/src/pages/c-chat/index.tsx`，现有 5 个模块页签：对话/协作/工具/收藏/标书）新增第 6 个页签「流程」，实现一个**多角色串行协作工作流**：一份文件在发起人 → 领导 → 处理人之间流转，各节点用 AI（复用 C 端对话智能体）处理文件、领导批注意见，多轮完善后归档。

**本质**：这不是严格审批流，而是**以文件为主体的完善循环**——核心动作是"上传新版本 / 写意见 / AI 处理 / 流转到下一节点"，节点内的"意见 → 修改 → 再提交"循环不限次数，节点负责人满意才放行。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|--------|------|
| 角色定义 | 系统注册用户（发起流程时从用户列表选领导和处理人） |
| 流程结构 | 固定链：角色1 → 领导 → 角色2 → 角色1汇总 → 归档，不可配置 |
| 驳回机制 | 灵活：领导/汇总审核可写批注意见 + 退回上一节点，修改后可再次提交，可多轮 |
| 批注形式 | 文字意见 + 版本上传（不做在线行内批注） |
| AI 指令 | 自由输入，复用 C 端对话智能体，不预设模板 |
| AI 集成 | 方案 A：流程详情页内嵌精简版 C 端对话组件，多轮对话+流式，AI 回复可一键存为新版本 |
| 主视图 | **文件为主视图**：中间大区预览当前版本，右侧版本时间线为叙事主线，意见和 AI 记录挂在版本节点上 |
| 可见范围 | 仅参与人（发起人/领导/处理人）可见自己参与的流程 |
| 归档 | 归档只是流程终态，最终文件+全部历史留在流程详情内，不写知识库/文件中心 |

## 3. 流程状态机

状态字段 `status` 表示**当前文件在谁手上**：

```
initiator（角色1处理中）
   │ 提交
   ▼
leader（领导审批中）──退回──▶ initiator
   │ 同意/通过
   ▼
handler（角色2处理中）◀──退回── summary（角色1汇总中）
   │ 提交                        │ 提交（来自 handler）
   ▼                            │
summary ──同意/归档──▶ archived（已归档）
                     cancelled（发起人任意阶段可作废）
```

- 每个节点内：负责人可不限次数地 [写意见] [上传新版本] [AI 处理]，满意后执行 [提交下一节点] 或 [退回]。
- 退回目标：上一节点（leader→initiator、summary→handler）。
- 作废：仅发起人可操作，任意非终态均可，只改状态不清数据。

## 4. 数据模型（4 张新表）

```
flow_instance        流程实例
  id, title,
  initiator_id, leader_id, handler_id,   -- 三个参与人 user_id
  status,                                 -- initiator/leader/handler/summary/archived/cancelled
  current_version_id,                     -- 当前最新版本
  create_time, update_time

flow_version         文件版本（核心表）
  id, flow_id, version_no,               -- 版本号递增
  file_name, file_path(MinIO), file_type, file_size,
  source,                                 -- manual_upload / ai_output
  created_by, node_status,                -- 产生该版本时的流程状态
  create_time

flow_comment         批注意见
  id, flow_id, version_id,               -- 意见针对某版本
  user_id, content, create_time

flow_ai_chat         AI 处理记录
  id, flow_id, version_id,               -- 输入版本
  output_version_id,                      -- 产出版本（存为新版本后回填，可空）
  instruction, response,                  -- 指令与回复摘要
  session_id,                             -- 关联的对话会话
  create_time
```

- 表结构同步写入 `api/db/db_models.py` + 项目初始化脚本，保证新环境部署迁移安全。
- 版本乐观锁：`submit`/`archive` 等状态变更带 `current_version_id` 条件更新，防并发冲突。

## 5. 后端

- 新增 `api/apps/restful_apis/flow_app.py`（REST Blueprint）+ `api/db/services/flow_service.py`。
- 文件存储复用现有 MinIO 上传服务；对话复用现有 chat 通道（前端直连，后端只存记录）。

REST 端点（挂在登录态下）：

```
POST   /flow                          创建流程（标题+领导+处理人+初始文件）
GET    /flow/list?scope=todo|initiated|joined   流程列表（待我处理/我发起/我参与）
GET    /flow/{id}                     详情（实例+版本+意见+AI记录）
POST   /flow/{id}/version             上传新版本
POST   /flow/{id}/comment             写批注意见
POST   /flow/{id}/ai-record           保存 AI 处理记录（含产出版本回填）
POST   /flow/{id}/submit              流转（action=next|return）
POST   /flow/{id}/archive             归档
POST   /flow/{id}/cancel              作废（仅发起人）
```

**权限**：所有端点校验当前用户 ∈ (initiator, leader, handler)；写操作额外校验"当前节点负责人"（作废例外，仅发起人）。

**通知**：流转/退回/新意见时，复用现有铃铛通知系统触达相关参与人。

## 6. 前端

- `c-chat/index.tsx` 页签数组新增 `key: 'flow', label: '流程'`（中文文案，只加 zh.ts）。
- 新增页面组件（`web/src/pages/c-chat/flow/`）：

```
┌─────────────────────────────────────────────┐
│ 状态条：● 领导审批中 · 张三 · 已进行 2 轮修改        │
├──────────────────────────────┬──────────────┤
│                              │  版本时间线     │
│   文件预览（当前选中版本，复用      │  ● v4 AI产出   │
│   document-viewer 能力）         │    └ 领导意见×2 │
│                              │  ● v3 角色1上传 │
│                              │  ● v2 AI产出   │
│                              │  ● v1 初始上传  │
├──────────────────────────────┴──────────────┤
│ 操作栏（随节点负责人变化）：                       │
│  [写意见] [AI处理] [上传修改版] [提交/退回] [归档/作废] │
└─────────────────────────────────────────────┘
```

- 左侧为流程列表（待我处理 / 我发起的 / 我参与的），未处理项带红点。
- AI 处理区：内嵌精简对话组件（复用现有 chat 能力），选中某版本作为上下文文件 + 自由输入指令；AI 回复可一键「存为新版本」（Markdown 文本落为 .md 文件版本）。
- 新增 `web/src/services/flow-service.ts` API 调用层。

## 7. 异常与边界

- AI 对话失败可重试，不影响版本数据；未点「存为新版本」不产生版本。
- 作废/退回只改状态，版本/意见/AI 记录全保留，可追溯。
- 上传非预期格式文件：版本表不做格式白名单，预览不支持的格式只提供下载。
- 并发提交：状态变更带版本条件更新，冲突方收到"流程状态已变化"提示后刷新。
- 参与人中有人被删除/禁用：流程详情只读并提示，不阻塞其他参与人查看。

## 8. 非目标（本期不做）

- 可配置流程模板 / 自定义节点
- 文档在线行内批注（划词批点）
- 多文件流程（一个流程绑定一个主文件）
- AI 产出 docx/PDF 格式保真（本期 AI 产出版本为 Markdown；上传人工版本支持任意格式）
- 归档文件进知识库/文件中心

## 9. 部署注意

后端成套 SCP：`db_models.py`、`flow_service.py`、`flow_app.py`、通知相关改动文件；前端 `npm run build` 走标准前端部署流程。部署后冒烟：import 全部新模块 + 创建一条测试流程走完全链路。

## 10. CHANGE.md

实施完成后按全局规则在项目 `CHANGE.md` 追加本次迭代记录，并同步项目 `CLAUDE.md` 参考文档表。
