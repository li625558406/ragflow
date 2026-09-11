# 流程对话保存自治存储设计（Flow Chat Save Decoupling）

- 日期：2026-09-11
- 状态：设计完成，待实施
- 关联：`docs/superpowers/specs/2026-08-30-flow-workflow-design.md`（流程页签）、`docs/superpowers/specs/2026-09-07-template-fill-design.md`（范本填写，依赖本设计的 template_fill_events 存储变更）

## 1. 背景与问题

流程页签的 AI 对话当前**借用对话页（c-chat）的 agent 会话体系**，耦合点有 4 处：

| # | 耦合点 | 位置 |
|---|--------|------|
| ① | 流程会话以「流程：xxx」出现在 c-chat 对话页签会话列表里，两边数据互相污染 | `agent_api.py` `POST /agents/{id}/sessions`，conversation 表 |
| ② | 多轮上下文完全依赖 conversation 表（canvas DSL 快照 + 消息历史） | `canvas_service.py:229 completion()` |
| ③ | 刷新回放范本填写进度要从 agent 会话消息里拉 `templateFillEvents` | `flow-ai-panel.tsx:206-241` |
| ④ | `sessionIdRef` 从全部 aiChats 恢复，多人操作时可能恢复到别人的会话 | `flow-ai-panel.tsx:117-124` |

关键底层事实：**画布多轮续聊靠 conversation 表里的 canvas DSL 快照**（`conv.dsl = str(canvas)`，canvas_service.py:303），且即使无 session_id 的无状态运行也会新建 conversation 行（canvas_service.py:253-263）。**完全绕开 conversation 表是伪命题**——除非改上游核心（已否决，见 §8）。

conversation 表自带 `source` 字段（现值 `"agent"`），是流程会话打标的天然挂点。

## 2. 目标与非目标

**目标**
1. 流程会话彻底不出现在对话页签（c-chat 会话列表过滤 + 存量打标）。
2. 对话的**权威存储归流程**：回显、范本进度回放、按操作人归属展示全部走 flow 自己的表；影子会话只是运行时缓存，可随时清理不丢数据。
3. 多轮追问续聊保留（同一操作人续接自己的会话，跨操作人各自独立）。

**非目标（明确不做）**
- 不改 `canvas_service.completion` 运行时逻辑（方案 C 已否决）。
- 不改 agent_id 的来源（仍读 `localStorage.ragflow_agent_id`，该依赖用户未列为痛点）。
- 不改发送链路（`/agents/chat/completion` 直连、前端自动保存、存版本流程均保持现状）。
- 不新建 flow_chat_session 表（YAGNI：`flow_ai_chat.session_id` 已承载续聊所需）。

## 3. 已确认的决策

| 决策点 | 结论 |
|--------|------|
| 对话可见性 | **全量可见**：流程内所有轮次对话对所有登录用户可见（`_require_viewer` 已开放读，含超管「全部流程」豁免），`user_id` 仅用于归属展示 |
| 存量记录归属 | `flow_ai_chat` 存量空 `user_id` 行迁移归属**流程发起人** |
| 存储方案 | 方案 B：flow 自持权威存储 + `source='flow'` 影子运行时会话 |

## 4. 数据模型

`flow_ai_chat` 扩展 2 列（`db_models.py` FlowAiChat + `migrate_db` 增量迁移，需纳入初始化脚本保证新部署可建表）：

```
user_id              CharField(32)  default ""   操作人归属（谁的对话，用于展示）
template_fill_events TextField    default ""   范本填写事件流 JSON（刷新回放用）
```

**影子会话**：流程对话在 conversation 表中创建为 `source='flow'` 的行。定位为纯运行时缓存（存 canvas DSL 快照 + 消息），对话页不可见、可随时清理；权威数据在 `flow_ai_chat`。

## 5. 后端改动

| # | 改动点 | 说明 |
|---|--------|------|
| 1 | 新端点 `POST /flow/<flow_id>/chat/session` | 为当前用户创建 `source='flow'` 影子会话。复用 `create_agent_session`（agent_api.py:232）的最小逻辑：取 agent DSL → Canvas reset → API4ConversationService.save（name=`流程：{flow_title}`、source=`flow`、user_id=当前用户）。权限 `login_required`（与读开放口径一致，能开面板就能建会话）。返回 `{session_id}`。**不改 agent_api.py 的列表路由**（过滤下沉 Service） |
| 2 | `add_ai_record`（flow_app.py:1052） | 落库时 `user_id = current_user.id`；请求体新增 `template_fill_events`（JSON 字符串，原样存储） |
| 3 | flow 详情接口（flow_app.py:281） | ai_chats 返回**不过滤**，每条带 `user_id`；新增 `chat_authors: {user_id: nickname}` 映射（与现有 `commentAuthors` 同款模式）供前端归属展示 |
| 4 | c-chat 会话列表过滤 | `API4ConversationService.get_list / get_names` 增加 `source != 'flow'` 条件（过滤下沉到 Service，agent_api.py 路由层不动）。MCP Server 若有同源列表逻辑一并排查 |

## 6. 前端改动（flow-ai-panel.tsx / flow-service.ts / flow-types.ts）

| 现状 | 改后 |
|------|------|
| `ensureSession` POST `/api/v1/agents/{agentId}/sessions`（:357-392） | 改调 `POST /flow/{flowId}/chat/session` |
| `sessionIdRef` 从全部 aiChats 倒序找 session_id（:117-124） | 保持倒序逻辑，但后端 ai_chats 自此全量带 user_id；续聊取**自己的**最新记录（前端用 currentUserId 过滤一行） |
| 刷新回放：GET `/agents/{agentId}/sessions/{sessionId}` 拉消息重放 templateFillEvents（:206-241） | **整段删除**，改为从 ai_chats 最新一条带 `template_fill_events` 的记录 `replayTemplateFillEvents` 重放（数据源 flow 自己的表） |
| `saveFlowAiRecord` payload 只存文本 | 新增 `template_fill_events: JSON.stringify(templateFillRef.current 的事件序列)` |
| 中部对话区气泡（ConversationView / flow-detail） | 气泡显示操作人昵称（取 `chat_authors[user_id]`），多人对话交织时归属清晰 |

发送、自动保存、`record_id` 补建版本、存版本链路**零改动**。

## 7. 存量数据迁移（部署时一次性执行）

```sql
-- 1) 存量「流程：xxx」会话打标 → 对话页签立即消失
UPDATE conversation SET source = 'flow'
WHERE source = 'agent' AND name LIKE '流程：%';

-- 2) 存量 flow_ai_chat 空 user_id 归属流程发起人
UPDATE flow_ai_chat c JOIN flow_instance f ON c.flow_id = f.id
SET c.user_id = f.initiator_id
WHERE c.user_id = '';
```

注意：conversation 表为 RAGFlow 上游表，`name LIKE '流程：%'` 是前端 `ensureSession` 写死的命名约定（flow-ai-panel.tsx:375），打标前用 `SELECT COUNT(*)` 核对命中数。迁移脚本进项目初始化/迁移体系（不裸 SQL 手工跑生产，脚本落库备查）。

## 8. 边界与错误处理

| 场景 | 行为 |
|------|------|
| 影子会话被误删（对话页不可能，但 DB 清理可能） | 多轮上下文丢失、回退为无上下文新会话；**权威对话记录不丢**（flow_ai_chat），续聊自动新建影子会话 |
| `add_ai_record` 保存失败（现有路径） | 维持现状：前端报错 + 手动「仅存记录」兜底按钮 |
| 同一用户在两个流程分别对话 | 天然隔离：ai_chats 按 flow_id 隔离，各自恢复各自的 session_id |
| 多人先后操作同一流程 | 各自独立影子会话（user_id 区分），上下文互不串 |
| 范本填写进度回放 | 数据源从 agent 会话消息换为 flow_ai_chat.template_fill_events，replay 归约逻辑（前端 applyTemplateFillEvent）不变 |
| c-chat 删除会话接口 | DELETE `/agents/{id}/sessions/{sid}`（agent_api.py:295，唯一需要动该文件的地方）对 `source='flow'` 的会话拒绝删除（403，提示"流程会话请在流程中管理"），防止对话页侧误删流程运行时会话（防御性，正常已不可见） |

## 9. 验证方案

1. **单测/手工**：`add_ai_record` 带/不带 `template_fill_events`、`user_id` 落库正确；detail 返回 chat_authors。
2. **对抗性**：非流程参与人读对话（应可见）；伪造他人 user_id 存记录（应被服务端 `current_user.id` 覆盖）；`template_fill_events` 超长/畸形 JSON（存储不炸、replay 静默失败）；影子会话删除后追问（自动新建会话不报错）。
3. **联调**：A 用户对话 2 轮（含范本填写）→ 刷新回放正常 → B 用户接手对话不带 A 的上下文 → 对话页签全程不出现流程会话 → 存为新版本链路正常。
4. **存量迁移**：测试库演练 UPDATE 命中数核对；迁移后 c-chat 会话列表无「流程：」残留。

## 10. 部署清单（成套）

| 类型 | 文件 |
|------|------|
| ORM | `api/db/db_models.py` |
| Service | `api/db/services/canvas_service.py`（仅 API4ConversationService.get_list/get_names 过滤，若实现在 api_service.py 则为该文件） |
| REST | `api/apps/restful_apis/flow_app.py`、`api/apps/restful_apis/agent_api.py`（仅 DELETE 路由加 source 校验） |
| 前端 | `web/src/pages/c-chat/flow/flow-ai-panel.tsx`、`web/src/services/flow-service.ts`、`web/src/pages/c-chat/flow/flow-types.ts`、`web/src/pages/c-chat/flow/flow-detail.tsx`（对话区归属展示） |
| 迁移脚本 | 存量打标 + user_id 回填（进迁移体系） |

前后端需一起上线（detail 新增字段 + 前端消费；session 端点新路由）。
