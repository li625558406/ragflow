# 权限管控（角色制 RBAC）设计

- 日期：2026-08-25
- 范围：B 端运营控制台 + C 端投标助手，模块/菜单级权限
- 状态：已确认方案，待实现

## 1. 背景与目标

项目目前只有 RAGFlow 原生的两级权限（数据池 `me/team` 可见性 + `User.is_superuser` 标记），**没有角色/模块**级权限控制。需要一套简易权限管控：

- 有「超级管理员」，能分配权限
- 每个用户根据权限查看不同模块/功能
- 前端隐藏无权限入口 + 后端接口强制校验（双层）

### 关键决策（已与用户对齐）

| 决策点 | 选择 |
|--------|------|
| 管控范围 | B 端 + C 端都要 |
| 权限粒度 | 仅模块/菜单级（页面可见性） |
| 分配方式 | 角色制 RBAC |
| 强制层级 | 前端隐藏 + 后端鉴权 |
| 方案 | 方案1：独立分层 RBAC |
| 权限点来源 | 后端常量表 + 初始化 seed（前后端共用同一套 key） |
| 管理入口 | B 端新增「权限管理」页（仅超管可见） |
| 存量用户兜底 | 默认挂「普通用户」角色（未挂角色的用户按普通用户对待） |

## 2. 权限点定义（单一来源：后端常量表）

后端 `api/constants/permission.py` 定义 `MODULE_PERMISSIONS`（`dict[str, str]`，key → 中文名），
初始化时 seed 进 `permission_role_permission`。前端 `web/src/constants/permission.ts` 引用同一套 key。

### B 端（对应 `web/src/layouts/components/global-navbar.tsx` 的 `menuItems` + 路由）

| key | 模块 | 路由 |
|-----|------|------|
| `bid` | 标讯管理（首页） | `/` |
| `dataset` | 知识库 | `/datasets` |
| `chat` | 对话 | `/chats` |
| `search` | 搜索 | `/searches` |
| `agent` | Agent 画布/流程 | `/agents`、`/agent` |
| `memory` | 记忆 | `/memories` |
| `file` | 文件 | `/files` |
| `crawler` | 智能采集 | `/smart-crawler` |
| `user_setting` | 用户设置 | `/user-setting` |

### C 端（投标助手，独立路由）

| key | 模块 | 路由 |
|-----|------|------|
| `home` | C 端着陆页 | `/` |
| `c_chat` | 投标助手对话 | `/home` |

> 注：本仓库 B 端主应用挂在 `/`（`routes.tsx` root-layout + `GlobalNavbar`），与 CLAUDE.md 所述 ADMIN_PREFIX 机制需以 `routes.tsx` / 页面实况为准。**权限点（key）为本设计的核心；具体路由→key 绑定在 writing-plans/实现阶段逐一对照 `routes.tsx` 与目标页面后再定稿，不在此硬写死勾选对应关系**（避免 key 与真实路由错位）。

### 管理

| key | 模块 | 说明 |
|-----|------|------|
| `permission_manage` | 权限管理页 | 仅超管使用，B 端新增 |

> 首个实现可先落地「B 端 9 项 + C 端 2 项 + permission_manage」；后续新增模块只需在常量表加一项 key 并给角色勾选，无需改动表结构。

## 3. 数据模型（3 张新表，Peewee）

新增到 `api/db/db_models.py`，作为 `DataBaseModel` 子类（`init_database_tables()` 会自动建表）。

### `permission_role`（角色）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | CharField(32) PK | uuid |
| `name` | CharField(100) unique | 角色名，如「普通用户」「标讯专员」 |
| `description` | TextField nullable | 角色描述 |
| `builtin` | Boolean default=False | 是否内置（超级管理员 / 普通用户为内置，内置角色不可删除） |
| `create_date` / `update_date` | DateTimeField | BaseModel 提供 |

### `permission_role_permission`（角色-权限点）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | CharField(32) PK | uuid |
| `role_id` | CharField(32) index | → permission_role.id |
| `permission_key` | CharField(64) index | 权限点 key（如 `bid`） |

unique(role_id, permission_key)

### `permission_user_role`（用户-角色）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | CharField(32) PK | uuid |
| `user_id` | CharField(32) index | → user.id |
| `role_id` | CharField(32) index | → permission_role.id |

unique(user_id, role_id)

### 约定
- 一个用户可挂**多个角色** → 权限取**并集**（`get_user_permissions` 汇总所有角色的 `permission_key`）。
- 超管判定：`user.is_superuser == 1` **或** 挂了内置「超级管理员」角色 → 拥有全部权限（直接放行所有 `permission_required`）。
- 存量兜底：用户未挂任何角色时，`get_user_permissions` 回退到内置「普通用户」角色的权限（懒回退，不强制写关联表）。

## 4. 数据库迁移与 seed（遵循 CLAUDE.md）

放在 `migrate_db()` 末尾（参照 `CollectionPolicyExt`/`Notification` 的既有写法）：

```python
# 在新角色/权限模型定义后，于 migrate_db() 后段加：
if not PermissionRole.table_exists():
    PermissionRole.create_table(safe=True)
    logging.info("permission_role: table created")
if not PermissionRolePermission.table_exists():
    PermissionRolePermission.create_table(safe=True)
    logging.info("permission_role_permission: table created")
if not PermissionUserRole.table_exists():
    PermissionUserRole.create_table(safe=True)
    logging.info("permission_user_role: table created")
```

初始 seed（幂等，仅当不存在时写入）：
- 内置「超级管理员」角色：`builtin=True`，不显式勾权限（逻辑上视为具备全部）。
- 内置「普通用户」角色：`builtin=True`，勾选基础模块（如 `bid`、`chat`、`c_chat`、`user_setting`）。
- 若系统里已有用户，不强制写 `permission_user_role`（靠上述懒回退兜底）。

> 依赖 `api/apps/__init__.py` 启动链：`settings.init_settings()` → `init_database_tables()` → 自动建表 + `migrate_db()`。

## 5. 后端

### 5.1 新增文件

#### `api/utils/permission.py`
- `get_user_permissions(user_id) -> set[str]`：
  1. 查 `permission_user_role`（join `permission_role_permission`）得到权限点并集
  2. 若无角色 → 回退内置「普通用户」角色权限
  3. Redis 缓存 `perm:{user_id}`（TTL 可配，改角色时主动失效）
- `permission_required(key)` 装饰器：先判 `is_superuser` 放行 → 再 `key ∈ get_user_permissions(current_user.id)` → 否则抛 403。
  用法：`@manager.route(...) @login_required @permission_required('bid')`

> 权限计算放 `api/utils`，不放在 ORM service 层，避免与路由耦合；缓存失效在角色/用户角色变更接口里主动 DELETE。

### 5.2 新增 REST（`api/apps/restful_apis/permission_app.py`）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/permission/me` | 登录即可 | 当前用户权限点集合 + is_superuser（前端启动时拉取） |
| GET | `/permission/roles` | `permission_manage` | 角色列表（含勾选的权限点） |
| POST | `/permission/roles` | `permission_manage` | 新建角色 |
| PUT | `/permission/roles/{id}` | `permission_manage` | 改角色名/描述 |
| DELETE | `/permission/roles/{id}` | `permission_manage` | 删角色（内置角色禁止删） |
| PUT | `/permission/roles/{id}/permissions` | `permission_manage` | 给角色勾权限（整组覆盖） |
| PUT | `/permission/users/{user_id}/roles` | `permission_manage` | 给用户挂/卸角色（整组覆盖） |
| GET | `/permission/users/{user_id}` | `permission_manage` | 查某用户当前角色 |
| GET | `/permission/users` | `permission_manage` | 用户列表（用于挂角色，含已挂角色） |

### 5.3 存量接口加校验
对受控路由逐个加 `@permission_required(key)`，保证前端隐藏之外后端硬校验。首批覆盖 B 端 9 模块对应接口 + C 端接口。

## 6. 前端

### 6.1 权限 store（`web/src/store/permission.ts`，Zustand）
- state：`permissions: Set<string>`、`isSuperuser: boolean`
- `fetchPermissions()`：登录后调 `GET /permission/me` 写入
- `hasPermission(key)`：`isSuperuser || permissions.has(key)`

### 6.2 hook / 工具
- `usePermission()`：读 store + 暴露 `hasPermission`
- 路由守卫 `PermissionGuard`：包在需要权限的 layout/route 外层，无权限渲染 403 页或重定向到有权限的首个页面。

### 6.3 菜单过滤
- `GlobalNavbar`（B 端）`menuItems` 每项加 `permission?: string`，渲染前 `hasPermission(x.permission)` 过滤。
- C 端 Navbar 同理。

### 6.4 权限管理页（B 端新增，仅超管可见）
- 入口：用户设置或导航新增「权限管理」，仅 `is_superuser || hasPermission('permission_manage')` 可见。
- 功能：角色列表 CRUD、给角色勾权限（权限点展示为勾选列）、给用户挂角色（用户下拉 + 已挂角色）。

## 7. i18n（项目约定：只中文，只加 zh.ts）

所有新增用户可见文案加进 `web/src/locales/zh.ts`，不同步 `en.ts`：
- 模块名（标讯管理、知识库、对话、搜索、Agent、记忆、文件、智能采集、用户设置、投标助手、着陆页、权限管理）
- 权限管理页文案（角色名、新建角色、勾选权限、挂角色等）
- 403 提示

## 8. 部署清单（成套 SCP，遵循 CLAUDE.md 智能采集部署约定）

| 类型 | 路径 |
|------|------|
| 模型 + 迁移 | `api/db/db_models.py`（新 3 表 + migrate_db seed） |
| 常量 | `api/constants/permission.py` |
| 权限逻辑 | `api/utils/permission.py` |
| REST | `api/apps/restful_apis/permission_app.py` |
| 前端 store/hook | `web/src/store/permission.ts`、`web/src/hooks/use-permission.ts` |
| 菜单/守卫 | `web/src/layouts/components/global-navbar.tsx`、`web/src/layouts/root-layout.tsx`（或路由守卫） |
| 权限管理页 | `web/src/pages/permission/*`、路由 `web/src/routes.tsx` |
| 常量 | `web/src/constants/permission.ts` |
| i18n | `web/src/locales/zh.ts` |

部署冒烟（容器内）：
```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole
print("imgs OK")
'
```

## 9. 边界与对抗性考虑

- **超管绕过**：`is_superuser=1` 必须随时放行所有接口，不能因角色表空而 403。
- **角色并集**：多角色权限取并集，删除某角色时仅移除其权限点。
- **内置角色保护**：`builtin` 角色禁止删除；「超级管理员」禁止改权限。
- **缓存一致性**：改角色/用户角色后必须失效 `perm:{user_id}`，否则前端权限滞后；失败需兜底查库。
- **空权限用户**：未挂角色回退「普通用户」，不能默认为 403 全无权限（会导致存量用户失联）。
- **越权访问**：前端隐藏只是 UX，后端 `permission_required` 才是防线；C 端接口同样要标 key。
- **并发**：挂多角色、改权限点走整组覆盖 + 事务，避免部分失败产生不一致。
- **C/B 端路由隔离**：判定 `ADMIN_PREFIX`（B 端）与 C 端路由，PoC 阶段先聚焦 B 端 9 模块，C 端 2 项纳入口径即可。

## 10. 验收标准

1. 超管登录 → 权限管理页可见，可建角色、勾权限、给用户挂角色。
2. 普通用户：只看到被授权模块的菜单；直达无权限路由被守卫拦截。
3. 老用户（无角色）：显示普通用户基础模块。
4. 后端：对无权限用户直调受控接口返回 403；超管直调全部通过。
5. 改角色权限后，目标用户下次刷新权限点生效（缓存失效）。
