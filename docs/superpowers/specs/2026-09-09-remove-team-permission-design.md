# 移除团队权限隔离设计

**日期**：2026-09-09
**状态**：已批准（用户确认方案 A）
**关联**：权限管控 RBAC 设计 `docs/superpowers/specs/2026-08-25-permission-rbac-design.md`（后续写权限管控的承接方）

---

## 1. 背景与目标

RAGFlow 原生「团队」体系通过 `user_tenant` 表实现跨租户资源共享：用户以 NORMAL 角色加入他人租户后，可见该租户下 `permission='team'` 的知识库/智能体/对话助手/搜索应用，文件与文档访问由 `check_kb_team_permission` / `check_file_team_permission` 拦截。

本项目实际使用中团队机制没有被用作权限模型，反而造成资源互相不可见。用户要求：**去掉团队隔离，任何账号配置的知识库、智能体、上传的文件全部互相可见**；权限管控后续统一由 /permission 页面的 RBAC（角色+权限点+`@permission_required`）承接。

### 已确认的需求边界

1. **读操作全局放开**：所有登录用户可见系统内全部知识库、智能体、对话助手、搜索应用、文件/文档（含 `permission='me'` 的 KB）。
2. **写操作 owner-only**：编辑/删除/移动/上传仅限资源所有者本人；他人资源只读。
3. **不动数据模型**：`user_tenant` 表、KB 的 `permission` 字段保留（成为死路径），不做数据迁移。
4. **不动前端**：B端团队成员页、新建知识库的 me/team 权限选择器保留不动。

## 2. 现状机制（探索结论）

| 机制 | 位置 | 现行为 |
|---|---|---|
| 团队成员关系 | `TenantService.get_joined_tenants_by_user_id`（api/db/services/user_service.py:202） | 查 `user_tenant` 中 role=NORMAL 的已加入租户 |
| KB 列表 | `dataset_api_service.py:399-401` → `KnowledgebaseService.get_list` | 只查「自己 + 已加入团队」且 `permission='team'` 的 KB |
| 检索全量 KB | `dataset_api_service.py:1085-1090` | `UserTenantService.query` + `get_by_tenant_ids`（同上过滤） |
| 智能体列表 | `agent_api.py:432-449` → `UserCanvasService.get_by_tenant_ids` | 只查「自己 + 已加入团队」；`owner_ids` 参数做 joined 校验 |
| 对话助手列表 | `chat_api.py:361,370` → `DialogService.get_by_tenant_ids` | 只查「自己 + 已加入团队」 |
| 搜索应用列表 | `search_api.py:82,84` → `SearchService.get_by_tenant_ids` | 不传 owner_ids 时传空列表 = 只看自己 |
| KB 可访问性 | `KnowledgebaseService.accessible`（knowledgebase_service.py:481） | join `user_tenant` 判断是否为 owner/成员，用于 get_dataset、检索摘要、对话配置选 KB |
| 文档上传门禁 | `check_kb_team_permission`（api/common/check_team_permission.py:25），调用点 document_api.py:440 | owner 或团队成员可上传 |
| 文件删除/移动门禁 | `check_file_team_permission`（同文件:40），调用点 file_api_service.py:443（删除）、491（移动） | owner 或文件关联 KB 的团队成员 |
| 文件下载门禁 | 同函数，调用点 file_api_service.py:595（`get_file_content`） | 同上（**读操作**） |
| 写端点 owner 校验 | KB 更新/删除 `dataset_api_service.py:255`、智能体 `agent_api.py:559,720,752`、对话助手 `chat_api.py` | 已是 owner-only，天然满足「只读+自用」 |

## 3. 方案（已批准：方案 A 显式全局化查询）

原则：**逐点显式修改**，让「全局可见」在代码里可读、可回滚（git revert 即可恢复团队隔离）。否决了「让 joined tenants 返回全部租户」（语义污染、仍绕不开 permission 过滤）与「单租户化」（迁移风险大、模型配置互相覆盖）。

### 3.1 后端改动清单

| # | 改动点 | 读/写 | 改法 |
|---|---|---|---|
| 1 | KB 列表 `dataset_api_service.py:399-401` | 读 | 跳过 `get_joined_tenants_by_user_id`，`KnowledgebaseService.get_list` 租户参数为 `None` 时不做租户与 permission 过滤 |
| 2 | 检索全量 KB `dataset_api_service.py:1085-1090` | 读 | 不再经 `UserTenantService.query`，全局取 KB（requested_kb_ids 校验同样全局） |
| 3 | 智能体列表 `agent_api.py:432-449` | 读 | `UserCanvasService.get_by_tenant_ids` 租户参数为 `None` 时不过滤；`owner_ids` 不再做 joined 授权校验 |
| 4 | 对话助手列表 `chat_api.py:361,370` | 读 | `DialogService.get_by_tenant_ids` 同上 |
| 5 | 搜索应用列表 `search_api.py:82,84` | 读 | `SearchService.get_by_tenant_ids` 同上（空列表改为全局） |
| 6 | `KnowledgebaseService.accessible` | 读 | 去掉 `user_tenant` join，改为「KB 存在（status=VALID）即通过」 |
| 7 | `check_kb_team_permission` | 写 | 函数体改为 owner-only：`return kb["tenant_id"] == other`。唯一调用点文档上传（document_api.py:440）收紧为仅 owner |
| 8 | `check_file_team_permission` | 写 | 函数体改为 owner-only：`return file["tenant_id"] == other`，覆盖删除（443）、移动（491） |
| 9 | `file_api_service.get_file_content`:595 | 读 | 移除 `check_file_team_permission` 校验，仅保留文件存在性检查（下载全局放开） |

Service 层约定：`get_by_tenant_ids` / `get_list` / `get_all_kb_by_tenant_ids` 系列**签名保留**，租户 ID 列表参数为 `None` 时跳过租户与 `permission` 过滤条件；调用方逐个改为传 `None`。不删函数、不改函数名，保证改动最小可回滚。

### 3.2 明确不改的边界

- 写端点自身的 owner 校验（KB 更新/删除、智能体更新/删除、对话助手更新/删除）保持现状
- `user_account_service.py:327`（「我的智能体」计数）保持只查自己
- 团队邀请/加入团队的接口与 B端团队成员页：保留（死路径）
- 前端所有页面：不动
- `accessible4deletion`（creator 校验）：不动，已符合 owner-only

## 4. 数据流示例（改后）

```
账号 B 打开知识库列表
  → get_list(None, B) → 返回系统内全部 KB（含 A 创建的、permission='me' 的）
账号 B 查看/检索 A 的 KB、下载 A 的文件 → 存在性检查通过 → 正常返回
账号 B 尝试更新/删除 A 的 KB、上传文档到 A 的 KB、移动/删除 A 的文件
  → owner 校验（kb.tenant_id != B）→ 拒绝
账号 B 把 A 的 KB 配进自己的对话助手 → accessible 存在性通过 → 配置成功、检索正常
```

## 5. 风险与错误处理

- **跨租户模型配置**：检索/对话他人 KB 时 embedding/rerank 用当前用户租户的模型配置——与原团队共享行为一致，非新增风险。
- **读面扩大**：原「团队可见」扩大为「所有人可见」，是需求本意；`permission='me'` 的 KB 也全局可见。
- **原团队场景回归**：已加入团队的用户列表行为变化（更多资源可见），不报错即可。
- **空租户列表边界**：原实现空 joined 列表时部分查询退化为只看自己（如 search_api），改后统一为全局，需覆盖该边界测试。

## 6. 测试

- **对抗性（核心）**：账号 A 创建 KB/智能体/对话助手/搜索应用/文件 → 账号 B：列表可见、详情可看、文件可下载、A 的 KB 可配进 B 的对话助手并可检索；B 尝试更新/删除 A 的 KB、上传文档到 A 的 KB、移动/删除 A 的文件 → 全部拒绝。
- **边界**：`permission='me'` 的 KB 全局可见；`owner_ids` 传任意/伪造值不报错；未加入任何团队的新账号列表非空（全局）；空列表、分页参数为 0。
- **回归**：A 邀请 B 进团队后原有路径不报错；B端团队成员页正常打开；文档上传/解析链路正常。
