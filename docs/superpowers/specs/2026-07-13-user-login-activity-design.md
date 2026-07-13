# 用户登录活动管理 — 设计文档

> 日期：2026-07-13
> 状态：已批准

## 背景

管理员需要查看用户的登录情况，了解用户活跃度。目前系统只有 `User.last_login_time` 记录最后一次登录，无法追溯历史。

## 需求

- 管理员在用户管理页面可查看任意用户的登录历史
- 记录每次登录的时间、IP、设备、登录方式
- 展示登录统计（总次数、近期活跃、常用设备）
- 登录日志保留 90 天，自动清理

## 设计概述

新建 `user_login_log` 表，在现有登录流程中埋点写入日志，用户列表页增加「登录记录」按钮弹出 Drawer 展示历史和统计。

---

## 1. 数据库

### `user_login_log` 表

```sql
CREATE TABLE user_login_log (
    id            CHAR(32)     PRIMARY KEY,
    user_id       CHAR(32)     NOT NULL,
    email         VARCHAR(255) NOT NULL,
    nickname      VARCHAR(100) NULL,
    login_time    DATETIME     NOT NULL,
    ip            VARCHAR(45)  NULL,
    device_type   VARCHAR(32)  DEFAULT 'web',
    device_name   VARCHAR(255) NULL,
    login_channel VARCHAR(32)  NULL,
    user_agent    VARCHAR(512) NULL,
    status        CHAR(1)      DEFAULT '1',
    create_time   BIGINT       NULL,
    create_date   DATETIME     NULL,

    INDEX idx_user_id (user_id),
    INDEX idx_login_time (login_time),
    INDEX idx_email (email)
);
```

**设计要点**：
- `email`/`nickname` 冗余存储，日志保留登录时的值，不受后续修改影响
- `device_type`: `web` / `mobile` / `api`
- `login_channel`: `password` / `wechat` / `github` 等
- `status`: `1`=成功, `0`=失败（第一版先只记录成功）
- Peewee 模型放在 `api/db/db_models.py` 末尾
- 初始化脚本中建表（遵循项目迁移规范）

---

## 2. 后端

### 2.1 登录埋点

在 `api/apps/restful_apis/user_api.py` 的登录成功后写入日志：

- `login()` 函数（密码登录，约 line 133 `login_user(user)` 之后）
- `oauth_login()` 函数（OAuth 登录，约 line 260 `login_user(user)` 之后）

记录字段：user_id, email, nickname, login_time, ip, device_type, device_name, login_channel, user_agent。

IP 获取优先级：`X-Forwarded-For` → `X-Real-IP` → `request.remote_addr`

### 2.2 新增 API 端点

新建文件 `api/apps/restful_apis/user_activity_api.py`，注册到 Blueprint。

| 端点 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/users/{user_id}/login-logs` | GET | 某用户的登录日志（分页） | superuser |
| `/users/login-stats` | GET | 所有用户登录统计 | superuser |

**`GET /users/{user_id}/login-logs` 查询参数**：
- `page` (int, default=1)
- `size` (int, default=20)
- `start_date` (str, optional, format YYYY-MM-DD)
- `end_date` (str, optional, format YYYY-MM-DD)
- `device_type` (str, optional)

**返回**：
```json
{
  "code": 0,
  "data": {
    "logs": [
      {
        "id": "xxx",
        "login_time": "2026-07-13 10:30:00",
        "ip": "192.168.1.100",
        "device_type": "web",
        "device_name": "Chrome 126 on Windows",
        "login_channel": "password"
      }
    ],
    "total": 42,
    "page": 1,
    "size": 20
  }
}
```

**`GET /users/login-stats` 返回**：
```json
{
  "code": 0,
  "data": {
    "total_logins_7d": 156,
    "active_users_7d": 23,
    "users": [
      {
        "user_id": "xxx",
        "email": "a@b.com",
        "nickname": "张三",
        "login_count": 12,
        "last_login_time": "2026-07-13 10:30:00"
      }
    ]
  }
}
```

### 2.3 Service 层

新建 `api/db/services/user_login_log_service.py`：
- `UserLoginLogService.create_log(user, request)` — 创建登录日志
- `UserLoginLogService.get_by_user(user_id, page, size, filters)` — 分页查询
- `UserLoginLogService.get_user_stats(user_id)` — 单用户统计
- `UserLoginLogService.cleanup_expired(days=90)` — 清理过期记录

---

## 3. 前端

### 3.1 交互流程

1. 管理员进入 `admin/users.tsx` 用户管理页
2. 每行操作列增加「登录记录」按钮（LucideClock 图标）
3. 点击后右侧滑出 Drawer

### 3.2 Drawer 布局

```
┌──────────────────────────────────────────┐
│  登录记录 — 张三 (a@b.com)         [X] │
├──────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│  │ 总登录 │ │ 7天登录│ │ 最近登录│ │ 常用设备│  │
│  │  42   │ │  12   │ │7/13   │ │ web   │  │
│  └──────┘ └──────┘ └──────┘ └──────┘  │
├──────────────────────────────────────────┤
│  日期: [最近30天 ▼]     设备: [全部 ▼]  │
├──────────────────────────────────────────┤
│  时间              IP              设备  登录方式 │
│  07-13 10:30  192.168.1.100  Chrome  密码    │
│  07-12 09:15  10.0.0.5       iPhone  微信    │
│  ...                              分页     │
└──────────────────────────────────────────┘
```

### 3.3 组件结构

| 文件 | 说明 |
|------|------|
| `web/src/pages/admin/users.tsx` | 修改：增加「登录记录」按钮列 |
| `web/src/pages/admin/login-logs-drawer.tsx` | 新建：Drawer 容器 |
| `web/src/pages/admin/login-logs-stats.tsx` | 新建：统计卡片 |
| `web/src/pages/admin/login-logs-table.tsx` | 新建：登录历史表格 |

### 3.4 前端 Service

新增 `getUserLoginLogs(userId, params)` 和 `getUserLoginStats()` 到现有 user 相关 service 文件中。

---

## 4. 数据清理

在现有定时任务框架中（`scheduled_task`）添加 90 天清理任务，或通过 `user_login_log_service.cleanup_expired(90)` 手动/定期执行。

---

## 5. 改动范围汇总

| 类型 | 文件 | 操作 |
|------|------|------|
| DB 模型 | `api/db/db_models.py` | 新增 UserLoginLog 模型 |
| DB 初始化 | 初始化脚本 | 新增建表语句 |
| Service | `api/db/services/user_login_log_service.py` | 新建 |
| API | `api/apps/restful_apis/user_activity_api.py` | 新建 |
| API 注册 | Blueprint 注册文件 | 新增蓝图 |
| 埋点 | `api/apps/restful_apis/user_api.py` | login()/oauth_login() 后写日志 |
| 前端页面 | `web/src/pages/admin/users.tsx` | 修改：增加按钮 |
| 前端组件 | `web/src/pages/admin/login-logs-drawer.tsx` | 新建 |
| 前端组件 | `web/src/pages/admin/login-logs-stats.tsx` | 新建 |
| 前端组件 | `web/src/pages/admin/login-logs-table.tsx` | 新建 |
| 前端 Service | 现有 user service 文件 | 新增 API 调用 |
| i18n | `zh.ts` / `en.ts` | 新增翻译 key |
