# 智能采集结果通知系统设计

- **创建日期**：2026-08-05
- **作者**：Claude（brainstorming 协作）
- **状态**：待用户最终审阅 → 待 writing-plans
- **覆盖面**：B 端所有登录账号（C 端不在范围内）

---

## 1 目标

让"智能采集系统"探测到新结果时，及时通知所有相关账号。用户可以：

1. 在系统任意页面看到未读提示（铃铛 + 红点）
2. 浏览器原生弹窗即时提醒
3. 强制 Modal 弹框，列出本轮新增的标题/类型/站点/发布时间，支持【稍后查看】或【查看详情并已阅】
4. 在通知中心查看历史、跳转原文、按类型过滤、管理订阅偏好

## 2 非目标（YAGNI）

- 不做离线推送（无 Service Worker / Push API / VAPID）
- 不做邮件 / 微信公众号 / 短信通道
- 不做 C 端（c-chat）页面的通知（仅 B 端）
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

**鉴权**：项目现有 `login_required`，与 collection_app 一致。

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

## 7 前端架构

### 7.1 新增文件

```
web/src/
├── services/notification-service.ts
├── hooks/
│   ├── use-unread-notifications.ts         # 30s 轮询
│   └── use-notification-permission.ts      # 浏览器授权管理
├── components/notifications/
│   ├── notification-bell.tsx               # 铃铛 + 红点
│   ├── notification-dropdown.tsx           # 下拉列表
│   ├── notification-modal.tsx              # ★ 强制 Modal
│   ├── notification-detail-dialog.tsx      # 详情面板
│   └── notification-settings-dialog.tsx    # 订阅偏好
```

### 7.2 挂载

`NotificationBell` 挂到全局 Header（`web/src/layouts/components/header`），B 端所有页面可见。

### 7.3 触达层细节

**铃铛**：未读数 >99 显示 "99+"；下拉每条一行（图标+类型+标题截断+站点+相对时间）；行内【已阅】+ 点行进详情 + 【查看原文】跳 source_url；底部【全部已阅】/【查看全部】。

**浏览器原生**：进入页面请求授权（拒绝后不再问，记录 localStorage）；未读数增加时逐条弹（标题 + 前 3 条 body）；点击触发 Modal。

**强制 Modal**：
- 触发：use-unread-notifications 检测到增量 > 0
- 内容：标题、类型 badge、站点、新增条数、发布时间、前 3 条标题 + "+N 更多"
- 按钮【稍后查看】：关 Modal，**不**标已阅，下次新通知再弹，红点保留
- 按钮【查看详情并已阅】：开详情面板 + POST `/batch-read` 本批 ids
- 一次只展示最新 1 条聚合通知（已弹的 id 写入 localStorage，会话级去重）

### 7.4 订阅偏好入口

铃铛下拉右上角齿轮 → Settings Dialog：
- browser_push / force_modal 开关
- site_ids 多选（默认全选）
- categories 多选（默认全选）
- 保存 → PUT `/subscription`

## 8 配置项

| Key | 默认 | 说明 |
|---|---|---|
| NOTIFICATION_SCAN_INTERVAL | 120 | 扫描器间隔（秒） |
| NOTIFICATION_RETENTION_DAYS | 30 | 保留天数 |
| NOTIFICATION_BUCKET_MINUTES | 1 | 分桶粒度 |
| NOTIFICATION_POLL_INTERVAL_MS | 30000 | 前端轮询间隔 |
| NOTIFICATION_MODAL_MAX_ITEMS | 1 | Modal 一次最多展示条数 |

## 9 错误处理与边界

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

## 10 保留策略

| 数据 | TTL | 清理 |
|---|---|---|
| notification | 30 天 | 扫描器每轮检查 |
| notification_user | 30 天 | 级联删除 |
| notification_subscription | 永久 | 不清 |
| NotificationWatermark（Redis） | 永久 | 不清 |

"全部已阅"仅改 is_read，不影响保留。

## 11 对抗性测试

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

## 12 部署清单（成套 SCP）

| 类型 | 路径 |
|---|---|
| ORM | `api/db/db_models.py`（末尾新增 3 表 + migrate_db） |
| Service | `api/db/services/notification_service.py`（新） |
| REST API | `api/apps/restful_apis/notification_app.py`（新） |
| 蓝图注册 | `api/apps/__init__.py` |
| 扫描器 | `rag/svr/notification_generator.py`（新） |
| 调度注册 | `rag/svr/crawler_engine/register_notification_task.py`（新） + `__init__.py` 钩子 |
| 前端 service | `web/src/services/notification-service.ts`（新） |
| 前端 hooks | `use-unread-notifications.ts` / `use-notification-permission.ts`（新） |
| 前端组件 | `web/src/components/notifications/*.tsx`（5 个新） |
| 全局挂载 | `web/src/layouts/components/header/index.tsx` 加 `<NotificationBell />` |
| i18n | `web/src/locales/zh.ts` 加 `notifications.*` keys（不写 en.ts） |

**冒烟测试**：
```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import Notification, NotificationUser, NotificationSubscription
from api.db.services.notification_service import NotificationService, NotificationUserService, NotificationSubscriptionService
from rag.svr.notification_generator import scan_once
print("imports OK")
'
```

## 13 范围拆解（实施阶段）

本设计为单一 spec，但实施计划可分阶段（由 writing-plans 决定具体编排）：

- **Phase 1（数据层）**：3 张表 + Service + 迁移
- **Phase 2（扫描器）**：notification_generator + 调度注册 + 单跑验证
- **Phase 3（后端 API）**：notification_app + 鉴权 + 单测
- **Phase 4（前端基础）**：service + hooks + 铃铛 + 下拉
- **Phase 5（前端触达）**：Modal + 浏览器原生 + 详情面板
- **Phase 6（订阅偏好）**：Settings Dialog + PUT/GET
- **Phase 7（i18n + 冒烟 + 部署）**

## 14 不做的事（明确边界）

- 不动 crawler_result / detector / collection_app 任何既有逻辑
- 不引入 Service Worker / Web Push / VAPID
- 不做邮件、微信、短信通道
- 不做 C 端通知
- 不做单条逐条通知（强制聚合）
- 不做跨租户广播
