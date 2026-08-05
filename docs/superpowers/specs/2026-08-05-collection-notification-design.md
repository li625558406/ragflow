# 智能采集结果通知系统设计

- **创建日期**：2026-08-05
- **作者**：Claude（brainstorming 协作）
- **状态**：待用户最终审阅 → 待 writing-plans
- **覆盖面**：C 端所有登录账号（铃铛/Modal/浏览器原生/订阅偏好都在 C 端 c-chat 页面）；B 端仅做数据维护和全局配置（管理员视角）

---

## 1 目标

让"智能采集系统"探测到新结果时，及时通知所有相关账号。用户在 **C 端（标书分析助手 / c-chat）** 看到：

1. c-chat 顶部 Header 右侧的铃铛 + 未读红点
2. 浏览器原生弹窗即时提醒（页面打开时）
3. 强制 Modal 弹框，列出本轮新增的标题/类型/站点/发布时间，支持【稍后查看】或【查看详情并已阅】
4. 通知中心下拉查看历史、跳转原文、按类型过滤、管理订阅偏好

**B 端** 仅做数据维护与全局配置（管理员视角，§8 详述）。

## 2 非目标（YAGNI）

- 不做离线推送（无 Service Worker / Push API / VAPID）
- 不做邮件 / 微信公众号 / 短信通道
- 不做 B 端铃铛 / Modal（B 端只做管理后台，触达层全在 C 端）
- 不做单条逐条通知（强制按"检测轮次 + 站点"聚合）
- 不做跨租户广播（仅在租户内向所有用户广播）

## 3 三层触达

| 层 | 触发时机 | 形态 | 用户操作 |
|---|---|---|---|
| 铃铛 + 红点 | 始终可见 | 顶部图标 + 未读数 | 点击展开下拉列表 |
| 浏览器原生 Notification | 前端轮询发现未读数增加 | 系统级原生通知 | 点击 → 弹 Modal |
| 强制 Modal | 未读数从 0→N 或增量 > 0 | 页面中央弹框 | 【稍后查看】/【查看详情并已阅】 |

**多 Tab 协同**：用 `BroadcastChannel('notifications')` 同步未读数；Modal 一次只在 1 个 Tab 弹。

**已弹去重**：前端 localStorage `notif:delivered` 列表记录本会话已弹过的 notification_id，不重复弹（会话级清理）。

## 4 通知生成机制（方案 B：独立扫描器）

**位置**：`rag/svr/notification_generator.py` + `rag/svr/crawler_engine/register_notification_task.py`

**调度**：注册到 detector 用的 meta-task 调度循环，`interval_seconds=120`（与 detector 60s 错峰）。

**核心流程**（每 120s 一次）：

```
拉启用站点列表 from crawler_state
for site_id in sites:
    watermark = GET Redis(notif:wm:{site_id})
                fallback = SELECT MAX(crawled_at) FROM notification WHERE site_id=?
    new_results = SELECT * FROM crawler_result
                  WHERE site_id=? AND crawled_at > watermark
                  ORDER BY crawled_at ASC
    if not new_results: continue

    buckets = group_by_minute(new_results, key=crawled_at // 60000)
    for bucket in buckets:
        batch_key = f"{site_id}::{bucket.minute_key}"
        if exists(notification WHERE batch_key=?): continue  # 幂等

        INSERT notification(...);
        user_ids = match_subscribers(site_id, category)  # 无订阅表记录 = 全订阅
        BULK INSERT notification_user(notification_id, user_id, is_read=False);

    SET Redis(notif:wm:{site_id}) = max(crawled_at in this round)

清理：DELETE FROM notification WHERE created_at < now - 30d
      DELETE FROM notification_user WHERE notification_id NOT IN (SELECT id FROM notification)
```

**关键设计**：
- **幂等**：`batch_key` 唯一索引；扫描器重启重跑不重复。
- **watermark 兜底**：Redis 不可用时从 notification 表 MAX 恢复，零丢失。
- **不耦合 detector**：只读 crawler_result 表。
- **并发保护**：Redis 分布式锁 `notif:scan:lock`（TTL=110s），防双进程。
- **单 site 失败**：try/except 记日志，不阻塞其他 site。

## 5 数据模型（3 张新表）

### 5.1 `notification`（通知主体）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | CharField PK | UUID 去 `-` |
| tenant_id | CharField index | 租户 |
| site_id | CharField index | 站点 |
| category | CharField index | bid/policy/news/personnel/other/objection（众数） |
| batch_key | CharField unique | `{site_id}::{minute}` 幂等键 |
| title | CharField 256 | `"{site_display} 检测到 {N} 条新结果"` |
| summary | TextField | 前 3 条标题，`\n` 分隔 |
| result_ids | JSONField | `["id1","id2",...]` 指向 crawler_result.id |
| result_count | IntegerField | 数量 |
| publish_range | CharField 64 | `"2026-08-05 ~ 2026-08-05"` |
| created_at | BigIntegerField index | ms 时间戳 |

### 5.2 `notification_user`（用户未读记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | CharField PK | UUID |
| notification_id | CharField index | FK→notification |
| user_id | CharField index | 接收者 |
| tenant_id | CharField index | |
| is_read | BooleanField default=False | 是否已阅 |
| read_at | BigIntegerField null | 已阅时间 |

复合唯一：`(user_id, notification_id)`。

**会话内去重**：浏览器原生 / Modal 本会话是否弹过，由前端 localStorage 维护 `notif:delivered` 列表（key=notification_id，会话级清理），不进后端字段。

### 5.3 `notification_subscription`（订阅偏好）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | CharField PK | |
| user_id | CharField index | |
| tenant_id | CharField index | |
| site_ids | JSONField | `[]` = 全订阅 |
| categories | JSONField | `[]` = 全订阅 |
| browser_push | BooleanField default=True | 启用浏览器原生 |
| force_modal | BooleanField default=True | 启用强制 Modal |

**MVP 语义**：无 subscription 记录的用户视为"全订阅 + 默认开启两层触达"，上线即覆盖全员。

**零迁移风险**：3 张均为新表，不动 crawler_result 或任何既有表；同步进 ragflow 初始化脚本。

## 6 后端 REST API

新 Blueprint `api/apps/restful_apis/notification_app.py`，前缀 `/api/v1/notifications`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/unread` | 当前用户未读列表（分页 20/页，DESC） |
| GET | `/unread/count` | `{count}`，前端 30s 轮询用 |
| GET | `/{id}` | 通知详情（含 result_ids 列表 + 标题 + source_url） |
| POST | `/{id}/read` | 标记单条已阅 |
| POST | `/read-all` | 一键全部已阅 |
| POST | `/batch-read` | body `{ids:[]}` 批量已阅（Modal "查看详情并已阅"） |
| GET | `/subscription` | 取订阅偏好 |
| PUT | `/subscription` | 更新偏好 |

**鉴权**：
- C 端调用时走 c-chat 现有 `apiFetch`（`Authorization` header + 401 自动跳 `/login`），前端带 `user_id` 参数，后端从 token 推断 `tenant_id`
- B 端管理接口（§8）走现有 `login_required` + 管理员角色校验

**未读列表 SQL**：
```sql
SELECT n.*, nu.is_read, nu.read_at
FROM notification_user nu
JOIN notification n ON nu.notification_id = n.id
WHERE nu.user_id = :uid AND nu.tenant_id = :tid
  AND nu.is_read = 0
ORDER BY n.created_at DESC
LIMIT 20 OFFSET :offset
```

**跨租户隔离**：所有查询带 `tenant_id` 过滤；API 层校验 `notification.tenant_id == current_user.tenant_id`。

## 7 前端架构（C 端为主，B 端管理后台为辅）

### 7.1 现状关键事实

- C 端无 `tenant_id` 概念；前端只发 `user_id`（从 `localStorage.userInfo.id` 取），后端从 Authorization token 推断 `tenant_id`
- C 端不用 i18n，所有文案硬编码中文
- c-chat header 在 `web/src/pages/c-chat/index.tsx:1435-1526` 内联（不是独立组件），铃铛需挂到该 header 右侧操作区
- C 端鉴权：每个请求 `headers.Authorization = localStorage.getItem('Authorization')`，401 自动清 token 跳 `/login`
- c-landing（未登录着陆页）不挂铃铛
- B 端已用 shadcn / Dialog / DropdownMenu / Tabs 等组件，C 端可复用 `components/ui/`

### 7.2 C 端新增文件

```
web/src/
├── services/c-notification-service.ts        # C 端 API 客户端（走 c-chat apiFetch 风格）
├── hooks/
│   ├── use-unread-notifications.ts           # 30s 轮询
│   └── use-notification-permission.ts        # 浏览器授权管理
├── components/c-notifications/               # C 端专用，避免与 B 端组件库混淆
│   ├── notification-bell.tsx                 # 铃铛 + 红点
│   ├── notification-dropdown.tsx             # 下拉列表（Popover）
│   ├── notification-modal.tsx                # ★ 强制 Modal
│   ├── notification-detail-dialog.tsx        # 详情面板（Dialog）
│   └── notification-settings-dialog.tsx      # 订阅偏好（Dialog）
```

### 7.3 挂载位置

`NotificationBell` 挂到 `c-chat/index.tsx` 内联 header（行 1435-1526）右侧操作区，紧挨用户头像之前：
```
[下载 App] [🔔铃铛+红点] [用户首字母头像] [用户名] [退出]
```
仅在 `/home`（c-chat）路由显示；c-landing 不显示。

### 7.4 触达层细节（C 端）

**铃铛**：未读数 >99 显示 "99+"；下拉每条一行（图标+类型+标题截断+站点+相对时间）；行内【已阅】+ 点行进详情 + 【查看原文】跳 source_url；底部【全部已阅】/【订阅设置】。

**浏览器原生**：进入 c-chat 时请求授权（拒绝后不再问，记录 localStorage `notif:permission:denied`）；未读数增加时逐条弹（标题 + 前 3 条 body）；点击触发 Modal。

**强制 Modal**：
- 触发：use-unread-notifications 检测到增量 > 0
- 内容：标题、类型 badge、站点、新增条数、发布时间、前 3 条标题 + "+N 更多"
- 按钮【稍后查看】：关 Modal，**不**标已阅，下次新通知再弹，红点保留
- 按钮【查看详情并已阅】：开详情面板 + POST `/batch-read` 本批 ids
- 一次只展示最新 1 条聚合通知（已弹的 id 写入 localStorage `notif:delivered`，会话级去重）

### 7.5 订阅偏好入口（C 端）

铃铛下拉右上角齿轮 → `NotificationSettingsDialog`：
- browser_push / force_modal 开关
- site_ids 多选（默认全选）
- categories 多选（默认全选）
- 保存 → PUT `/subscription`

### 7.6 C 端 API 调用规范

- 所有请求带 `Authorization` header（走 c-chat 现有 `apiFetch` 风格，401 自动跳 `/login`）
- 请求参数带 `user_id`（C 端从 localStorage.userInfo.id 取）
- 后端从 token 推断 `tenant_id`，前端不发
- 文案硬编码中文，不进 i18n

## 8 B 端管理后台（数据维护 + 全局配置）

B 端在「智能采集」页面下新增一个 **"通知管理"** Tab（或独立菜单），仅管理员可见，包含：

### 8.1 数据维护（列表 + 操作）

- 表格展示全租户最近 30 天 `notification` 记录（不分用户维度，看主体）
- 列：created_at / site_display / category / result_count / 已推用户数 / 已读用户数 / 操作
- 操作：查看详情（result_ids 列表）、强制删除一条通知（级联删 notification_user）
- 筛选：site_id / category / 时间范围
- 分页：50/页

### 8.2 用户触达统计

- 顶部统计卡片：今日生成通知数 / 本周已读率 / 未读 Top10 用户
- 帮助管理员判断通知系统是否健康

### 8.3 全局配置（写入 settings 表或环境变量）

| 配置 | 默认 | 说明 |
|---|---|---|
| 通知系统启用总开关 | true | 关闭后扫描器空跑，C 端无新通知 |
| 默认 browser_push | true | 新用户默认是否启用浏览器原生 |
| 默认 force_modal | true | 新用户默认是否启用强制 Modal |
| 扫描间隔（秒） | 120 | NOTIFICATION_SCAN_INTERVAL |
| 保留天数 | 30 | NOTIFICATION_RETENTION_DAYS |

### 8.4 B 端新增 API（管理员鉴权）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/admin/notifications` | 全租户通知列表（带分页/筛选） |
| GET | `/admin/notifications/{id}` | 详情 |
| DELETE | `/admin/notifications/{id}` | 强制删除（级联） |
| GET | `/admin/notifications/stats` | 触达统计 |
| GET | `/admin/notifications/config` | 取全局配置 |
| PUT | `/admin/notifications/config` | 更新全局配置 |

复用项目现有 `manager_required` 或 `admin_required` 装饰器。

### 8.5 B 端新增文件

```
web/src/
├── services/admin-notification-service.ts        # B 端管理 API 客户端
├── pages/crawl4ai/
│   └── notification-admin-tab.tsx                # "通知管理" Tab（挂在 crawl4ai Tabs 内）
```

## 9 配置项

| Key | 默认 | 说明 |
|---|---|---|
| NOTIFICATION_SCAN_INTERVAL | 120 | 扫描器间隔（秒） |
| NOTIFICATION_RETENTION_DAYS | 30 | 保留天数 |
| NOTIFICATION_BUCKET_MINUTES | 1 | 分桶粒度 |
| NOTIFICATION_POLL_INTERVAL_MS | 30000 | 前端轮询间隔 |
| NOTIFICATION_MODAL_MAX_ITEMS | 1 | Modal 一次最多展示条数 |

## 10 错误处理与边界

| 场景 | 处理 |
|---|---|
| 单 site 扫描失败 | try/except，记日志，继续下一 site |
| watermark Redis 不可用 | fallback notification 表 MAX 恢复 |
| crawler_result 千万级 | 复合索引 `idx_site_crawled`（迁移脚本加） |
| 并发扫描 | Redis 锁 `notif:scan:lock` TTL=110s |
| 订阅查询慢 | notification_subscription tenant_id+user_id 索引；未配置用户代码兜底默认值，不查全表 |
| 浏览器拒绝授权 | 静默降级，仅铃铛+Modal |
| 用户离线/未登录 | 生成时按 active 用户列表；下次登录时补生成 7 天内匹配订阅的通知 |
| 跨租户 | 所有查询带 tenant_id 过滤 |

## 11 保留策略

| 数据 | TTL | 清理 |
|---|---|---|
| notification | 30 天 | 扫描器每轮检查 |
| notification_user | 30 天 | 级联删除 |
| notification_subscription | 永久 | 不清 |
| NotificationWatermark（Redis） | 永久 | 不清 |

"全部已阅"仅改 is_read，不影响保留。

## 12 对抗性测试

### 后端 pytest

1. **幂等性**：同 site+watermark 跑两次，notification 不重复
2. **空结果**：无新增不写
3. **超大批**：单轮 5000 条，分桶不超过 1（同分钟聚合 1 条）
4. **watermark 恢复**：删 Redis key 后扫描，从 notification MAX 正确恢复
5. **并发**：两扫描器实例抢锁，只一个写入
6. **订阅过滤**：A 订阅 site_X，B 不订阅；只 A 收到 notification_user
7. **跨租户**：tenant_1 通知 tenant_2 用户查不到
8. **SQL 注入**：未读列表 keyword `' OR 1=1--`，Peewee 参数化无注入

### 前端 Vitest

- count 0→5 自动弹 Modal
- "稍后查看"不标已阅，下次新通知重弹
- "查看详情并已阅"调 batch-read
- 浏览器拒绝授权时静默降级

## 13 部署清单（成套 SCP）

| 类型 | 路径 |
|---|---|
| ORM | `api/db/db_models.py`（末尾新增 3 表 + migrate_db） |
| Service | `api/db/services/notification_service.py`（新） |
| C 端 REST API | `api/apps/restful_apis/notification_app.py`（新） |
| B 端管理 REST API | `api/apps/restful_apis/notification_app.py` 同文件追加 `/admin/notifications/*` |
| 蓝图注册 | `api/apps/__init__.py` |
| 扫描器 | `rag/svr/notification_generator.py`（新） |
| 调度注册 | `rag/svr/crawler_engine/register_notification_task.py`（新） + `__init__.py` 钩子 |
| C 端 service | `web/src/services/c-notification-service.ts`（新） |
| C 端 hooks | `use-unread-notifications.ts` / `use-notification-permission.ts`（新） |
| C 端组件 | `web/src/components/c-notifications/*.tsx`（5 个新） |
| C 端挂载 | `web/src/pages/c-chat/index.tsx` header（行 1435-1526）右侧加 `<NotificationBell />` |
| B 端管理 service | `web/src/services/admin-notification-service.ts`（新） |
| B 端管理页面 | `web/src/pages/crawl4ai/notification-admin-tab.tsx`（新）+ Tabs 注册 |
| i18n | 仅 B 端进 `web/src/locales/zh.ts` `notifications.*`；C 端文案硬编码不进 i18n |

**冒烟测试**：
```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import Notification, NotificationUser, NotificationSubscription
from api.db.services.notification_service import NotificationService, NotificationUserService, NotificationSubscriptionService
from rag.svr.notification_generator import scan_once
print("imports OK")
'
```

## 14 范围拆解（实施阶段）

本设计为单一 spec，但实施计划可分阶段（由 writing-plans 决定具体编排）：

- **Phase 1（数据层）**：3 张表 + Service + 迁移
- **Phase 2（扫描器）**：notification_generator + 调度注册 + 单跑验证
- **Phase 3（C 端后端 API）**：notification_app + 鉴权 + 单测
- **Phase 4（C 端前端基础）**：c-notification-service + hooks + 铃铛 + 下拉
- **Phase 5（C 端触达）**：Modal + 浏览器原生 + 详情面板
- **Phase 6（C 端订阅偏好）**：Settings Dialog + PUT/GET
- **Phase 7（B 端管理后台）**：admin-notification-service + admin Tab + 统计/配置
- **Phase 8（冒烟 + 部署）**

## 15 不做的事（明确边界）

- 不动 crawler_result / detector / collection_app 任何既有逻辑
- 不引入 Service Worker / Web Push / VAPID
- 不做邮件、微信、短信通道
- 不做 B 端铃铛 / Modal（触达层全在 C 端）
- 不做单条逐条通知（强制聚合）
- 不做跨租户广播
