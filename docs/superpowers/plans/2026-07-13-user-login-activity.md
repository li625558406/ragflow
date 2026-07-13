# User Login Activity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin ability to view user login history via a login log table and a Drawer in the admin users page.

**Architecture:** New `user_login_log` Peewee model + `UserLoginLogService` + admin API endpoints in `admin/server/routes.py`. Login埋点 in existing `user_api.py`. Frontend: Sheet (Drawer) component in admin users page.

**Tech Stack:** Python/Peewee (backend), React/TypeScript/TanStack Query/Radix Sheet (frontend)

---

### Task 1: Add `UserLoginLog` Peewee model

**Files:**
- Modify: `api/db/db_models.py` (after `BidSyncLog` class, ~line 1725)

- [ ] **Step 1: Add the model class to db_models.py**

Insert after the `BidSyncLog` class (around line 1725):

```python
class UserLoginLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    email = CharField(max_length=255, null=False, index=True)
    nickname = CharField(max_length=100, null=True)
    login_time = DateTimeField(null=False, index=True)
    ip = CharField(max_length=45, null=True)
    device_type = CharField(max_length=32, default="web")
    device_name = CharField(max_length=255, null=True)
    login_channel = CharField(max_length=32, null=True)
    user_agent = TextField(null=True)
    status = CharField(max_length=1, default="1")

    class Meta:
        db_table = "user_login_log"
```

Note: The existing `init_database_tables()` function at line 696 auto-discovers all `DataBaseModel` subclasses and creates tables. No changes needed there — the new model will be picked up automatically.

- [ ] **Step 2: Verify the model is valid**

Run: `cd D:/AI/ragflow2 && python -c "from api.db.db_models import UserLoginLog; print(UserLoginLog._meta.db_table)"`
Expected: `user_login_log`

- [ ] **Step 3: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat: add UserLoginLog model for tracking user login history"
```

---

### Task 2: Create `UserLoginLogService`

**Files:**
- Create: `api/db/services/user_login_log_service.py`

- [ ] **Step 1: Create the service file**

```python
from datetime import datetime, timedelta

from peewee import fn

from api.db.db_models import DB, UserLoginLog
from api.db.services.common_service import CommonService
from common.time_utils import current_timestamp, datetime_format


class UserLoginLogService(CommonService):
    model = UserLoginLog

    @classmethod
    @DB.connection_context()
    def create_log(cls, user, request, login_channel="password", device_type="web", device_name=None):
        """Create a login log entry after successful login."""
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.headers.get("X-Real-IP", "")
            or request.remote_addr
        ) if request else None
        user_agent = request.headers.get("User-Agent", "") if request else ""
        timestamp = current_timestamp()
        now = datetime.now()
        cls.insert(
            id=None,  # auto-generated
            user_id=user.id,
            email=user.email,
            nickname=user.nickname,
            login_time=now,
            ip=ip,
            device_type=device_type,
            device_name=device_name,
            login_channel=login_channel,
            user_agent=user_agent[:512] if user_agent else None,
            status="1",
            create_time=timestamp,
            create_date=now,
            update_time=timestamp,
            update_date=now,
        )

    @classmethod
    @DB.connection_context()
    def get_user_logs(cls, user_id, page=1, size=20, start_date=None, end_date=None, device_type=None):
        """Get paginated login logs for a user."""
        conditions = [cls.model.user_id == user_id, cls.model.status == "1"]
        if start_date:
            conditions.append(cls.model.login_time >= datetime.strptime(start_date, "%Y-%m-%d"))
        if end_date:
            conditions.append(cls.model.login_time <= datetime.strptime(end_date, "%Y-%m-%d 23:59:59"))
        if device_type:
            conditions.append(cls.model.device_type == device_type)

        query = (
            cls.model
            .select()
            .where(*conditions)
            .order_by(cls.model.login_time.desc())
        )
        total = query.count()
        logs = [log.to_dict() for log in query.paginate(page, size)]
        return {"logs": logs, "total": total, "page": page, "size": size}

    @classmethod
    @DB.connection_context()
    def get_user_stats(cls, user_id):
        """Get login statistics for a user."""
        total = cls.model.select().where(
            cls.model.user_id == user_id, cls.model.status == "1"
        ).count()

        seven_days_ago = datetime.now() - timedelta(days=7)
        recent_count = cls.model.select().where(
            cls.model.user_id == user_id,
            cls.model.status == "1",
            cls.model.login_time >= seven_days_ago,
        ).count()

        last_login = (
            cls.model
            .select(cls.model.login_time)
            .where(cls.model.user_id == user_id, cls.model.status == "1")
            .order_by(cls.model.login_time.desc())
            .first()
        )
        last_login_time = last_login.login_time.strftime("%Y-%m-%d %H:%M") if last_login else None

        # Most common device type
        device_row = (
            cls.model
            .select(cls.model.device_type, fn.COUNT(cls.model.id).alias("cnt"))
            .where(cls.model.user_id == user_id, cls.model.status == "1")
            .group_by(cls.model.device_type)
            .order_by(fn.COUNT(cls.model.id).desc())
            .first()
        )
        common_device = device_row.device_type if device_row else None

        return {
            "total": total,
            "recent_7d": recent_count,
            "last_login_time": last_login_time,
            "common_device": common_device,
        }

    @classmethod
    @DB.connection_context()
    def cleanup_expired(cls, days=90):
        """Delete login logs older than the specified number of days."""
        cutoff = datetime.now() - timedelta(days=days)
        return cls.model.delete().where(cls.model.login_time < cutoff).execute()
```

- [ ] **Step 2: Verify import works**

Run: `cd D:/AI/ragflow2 && python -c "from api.db.services.user_login_log_service import UserLoginLogService; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/db/services/user_login_log_service.py
git commit -m "feat: add UserLoginLogService for login log CRUD and stats"
```

---

### Task 3: Add login埋点 to `user_api.py`

**Files:**
- Modify: `api/apps/restful_apis/user_api.py`

- [ ] **Step 1: Add import**

At the top of the file (after the existing imports, around line 50), add:

```python
from api.db.services.user_login_log_service import UserLoginLogService
```

- [ ] **Step 2: Add埋点 to password login**

In the `login()` function, after `login_user(user)` (line 133) and before `user.update_time = ...` (line 134), add:

```python
        try:
            UserLoginLogService.create_log(
                user, request,
                login_channel="password",
                device_type=device_type,
                device_name=device_name,
            )
        except Exception as e:
            logging.warning(f"Failed to write login log: {e}")
```

- [ ] **Step 3: Add埋点 to OAuth callback (existing user login)**

In the `oauth_callback()` function, there are two login paths. Add after each `login_user(user)` call:

**Path 1 — new user registration login (after line 260 `login_user(user)`):**

```python
                try:
                    UserLoginLogService.create_log(
                        user, request,
                        login_channel=channel,
                        device_type="web",
                        device_name=f"OAuth-{channel}",
                    )
                except Exception as e:
                    logging.warning(f"Failed to write login log: {e}")
```

**Path 2 — existing user login (after line 277 `login_user(user)`):**

```python
        try:
            UserLoginLogService.create_log(
                user, request,
                login_channel=channel,
                device_type="web",
                device_name=f"OAuth-{channel}",
            )
        except Exception as e:
            logging.warning(f"Failed to write login log: {e}")
```

Note: OAuth callback is a redirect flow, not a JSON API. The `request` object is available from Quart's module-level import. We use try/except to never block login on logging failure.

- [ ] **Step 4: Commit**

```bash
git add api/apps/restful_apis/user_api.py
git commit -m "feat: add login log recording on password and OAuth login"
```

---

### Task 4: Add admin API endpoints for login logs

**Files:**
- Modify: `admin/server/routes.py` (add 2 new routes)
- Modify: `admin/server/services.py` (add `LoginLogMgr` class)

**IMPORTANT:** The admin server is a **separate Flask app** (not Quart) at `admin/server/` with Blueprint prefix `/api/v1/admin`. Admin endpoints like `adminListUsers` are defined here, not in `api/apps/restful_apis/`. The admin server imports directly from `api.db.services` and uses `flask_login` for auth.

- [ ] **Step 1: Add `LoginLogMgr` to `admin/server/services.py`**

Add import at the top of `services.py` (after existing imports, around line 34):

```python
from api.db.services.user_login_log_service import UserLoginLogService
```

Add the `LoginLogMgr` class at the end of the file:

```python
class LoginLogMgr:
    @staticmethod
    def get_user_logs(username, page=1, size=20, start_date=None, end_date=None, device_type=None):
        """Get paginated login logs for a user by email."""
        # Look up user_id from email
        from api.db.services.user_service import UserService
        users = UserService.query(email=username)
        if not users:
            raise AdminException(f"User {username} not found", 404)
        user_id = users[0].id
        return UserLoginLogService.get_user_logs(
            user_id=user_id, page=page, size=size,
            start_date=start_date, end_date=end_date, device_type=device_type,
        )

    @staticmethod
    def get_user_stats(username):
        """Get login statistics for a user by email."""
        from api.db.services.user_service import UserService
        users = UserService.query(email=username)
        if not users:
            raise AdminException(f"User {username} not found", 404)
        user_id = users[0].id
        return UserLoginLogService.get_user_stats(user_id)
```

- [ ] **Step 2: Add routes to `admin/server/routes.py`**

Add import at the top (after existing `services` import, line 28):

```python
from services import UserMgr, ServiceMgr, UserServiceMgr, SettingsMgr, ConfigMgr, EnvironmentsMgr, SandboxMgr, LoginLogMgr
```

Add two new routes after `get_user_agents` (after line 238):

```python
@admin_bp.route("/users/<username>/login-logs", methods=["GET"])
@login_required
@check_admin_auth
def get_user_login_logs(username):
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 20))
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        device_type = request.args.get("device_type")

        result = LoginLogMgr.get_user_logs(
            username=username, page=page, size=size,
            start_date=start_date, end_date=end_date, device_type=device_type,
        )
        return success_response(result)

    except AdminException as e:
        return error_response(e.message, e.code)
    except Exception as e:
        return error_response(str(e), 500)


@admin_bp.route("/users/<username>/login-stats", methods=["GET"])
@login_required
@check_admin_auth
def get_user_login_stats(username):
    try:
        stats = LoginLogMgr.get_user_stats(username)
        return success_response(stats)

    except AdminException as e:
        return error_response(e.message, e.code)
    except Exception as e:
        return error_response(str(e), 500)
```

Note: The admin server uses `success_response(data)` and `error_response(msg, code)` (not `get_json_result`). Pattern matches existing routes in this file.

- [ ] **Step 3: Commit**

```bash
git add admin/server/routes.py admin/server/services.py
git commit -m "feat: add admin API endpoints for user login logs and stats"
```

---

### Task 5: Add frontend API service functions

**Files:**
- Modify: `web/src/utils/api.ts` (add URL constants)
- Modify: `web/src/services/admin-service.ts` (add fetch functions)

- [ ] **Step 1: Add API URL constants to `api.ts`**

In `web/src/utils/api.ts`, find the admin section (around line 296, after `adminListUserAgents`) and add:

```typescript
  adminUserLoginLogs: (username: string) =>
    `${restAPIv1}/admin/users/${username}/login-logs`,
  adminUserLoginStats: (username: string) =>
    `${restAPIv1}/admin/users/${username}/login-stats`,
```

Note: These URLs follow the existing admin API pattern (`${restAPIv1}/admin/users/...`). The admin server Blueprint at `admin/server/routes.py` mounts at `/api/v1/admin`.

- [ ] **Step 2: Add service functions to `admin-service.ts`**

After the existing `adminListUserAgents` export (around line ~170), add:

```typescript
export const getUserLoginLogs = (
  username: string,
  params: {
    page?: number;
    size?: number;
    start_date?: string;
    end_date?: string;
    device_type?: string;
  } = {},
) =>
  request.get<ResponseData<AdminService.LoginLogList>>(
    api.adminUserLoginLogs(username),
    { params },
  );

export const getUserLoginStats = (username: string) =>
  request.get<ResponseData<AdminService.LoginLogStats>>(
    api.adminUserLoginStats(username),
  );
```

- [ ] **Step 3: Add TypeScript types to admin-service.ts or a separate types file**

Add these types in the `AdminService` namespace area or near the existing type exports:

```typescript
export namespace AdminService {
  // ... existing types ...

  export interface LoginLogList {
    logs: LoginLogItem[];
    total: number;
    page: number;
    size: number;
  }

  export interface LoginLogItem {
    id: string;
    login_time: string;
    ip: string | null;
    device_type: string;
    device_name: string | null;
    login_channel: string | null;
  }

  export interface LoginLogStats {
    total: number;
    recent_7d: number;
    last_login_time: string | null;
    common_device: string | null;
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add web/src/utils/api.ts web/src/services/admin-service.ts
git commit -m "feat: add frontend API service for user login logs"
```

---

### Task 6: Create frontend Login Logs Drawer components

**Files:**
- Create: `web/src/pages/admin/login-logs-drawer.tsx`
- Create: `web/src/pages/admin/login-logs-stats.tsx`
- Create: `web/src/pages/admin/login-logs-table.tsx`

- [ ] **Step 1: Create `login-logs-stats.tsx`**

```tsx
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '@/components/ui/card';
import { LucideClock, LucideActivity, LucideCalendar, LucideMonitor } from 'lucide-react';

interface LoginLogStatsProps {
  stats: {
    total: number;
    recent_7d: number;
    last_login_time: string | null;
    common_device: string | null;
  } | undefined;
  isLoading: boolean;
}

export default function LoginLogsStats({ stats, isLoading }: LoginLogStatsProps) {
  const { t } = useTranslation();

  const cards = [
    {
      label: t('admin.loginLogsTotal'),
      value: stats?.total ?? '-',
      icon: LucideClock,
    },
    {
      label: t('admin.loginLogsRecent7d'),
      value: stats?.recent_7d ?? '-',
      icon: LucideActivity,
    },
    {
      label: t('admin.loginLogsLastLogin'),
      value: stats?.last_login_time ? stats.last_login_time.split(' ')[0] : '-',
      icon: LucideCalendar,
    },
    {
      label: t('admin.loginLogsCommonDevice'),
      value: stats?.common_device ?? '-',
      icon: LucideMonitor,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3">
      {cards.map(({ label, value, icon: Icon }) => (
        <Card key={label}>
          <CardContent className="p-3 flex items-center gap-3">
            <Icon className="size-4 text-text-tertiary shrink-0" />
            <div className="min-w-0">
              <div className="text-xs text-text-tertiary">{label}</div>
              <div className="text-sm font-medium truncate">{isLoading ? '...' : value}</div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Create `login-logs-table.tsx`**

```tsx
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';

interface LoginLogItem {
  id: string;
  login_time: string;
  ip: string | null;
  device_type: string;
  device_name: string | null;
  login_channel: string | null;
}

interface LoginLogsTableProps {
  logs: LoginLogItem[] | undefined;
  total: number | undefined;
  page: number;
  size: number;
  onPageChange: (page: number) => void;
  isLoading: boolean;
}

export default function LoginLogsTable({
  logs, total, page, size, onPageChange, isLoading,
}: LoginLogsTableProps) {
  const { t } = useTranslation();
  const totalPages = Math.ceil((total ?? 0) / size);

  return (
    <div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('admin.loginLogsTime')}</TableHead>
              <TableHead>IP</TableHead>
              <TableHead>{t('admin.loginLogsDevice')}</TableHead>
              <TableHead>{t('admin.loginLogsChannel')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={4} className="text-center py-8 text-text-tertiary">
                  Loading...
                </TableCell>
              </TableRow>
            ) : !logs?.length ? (
              <TableRow>
                <TableCell colSpan={4} className="text-center py-8 text-text-tertiary">
                  {t('common.noData')}
                </TableCell>
              </TableRow>
            ) : (
              logs.map((log) => (
                <TableRow key={log.id}>
                  <TableCell className="whitespace-nowrap">
                    {log.login_time}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {log.ip || '-'}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{log.device_type || '-'}</Badge>
                    {log.device_name && (
                      <span className="ml-1 text-xs text-text-tertiary">{log.device_name}</span>
                    )}
                  </TableCell>
                  <TableCell>{log.login_channel || '-'}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      {totalPages > 1 && (
        <div className="flex justify-end mt-3">
          <RAGFlowPagination
            currentPage={page}
            totalPages={totalPages}
            onPageChange={onPageChange}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create `login-logs-drawer.tsx`**

```tsx
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { getUserLoginLogs, getUserLoginStats } from '@/services/admin-service';
import LoginLogsStats from './login-logs-stats';
import LoginLogsTable from './login-logs-table';

interface LoginLogsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  email: string;
  nickname?: string;
}

const DATE_PRESETS = [
  { value: '', label: 'admin.loginLogsAllTime' },
  { value: '7', label: 'admin.loginLogs7Days' },
  { value: '30', label: 'admin.loginLogs30Days' },
];

export default function LoginLogsDrawer({
  open, onOpenChange, email, nickname,
}: LoginLogsDrawerProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [datePreset, setDatePreset] = useState('30');

  // Compute start_date from preset
  const now = new Date();
  const startDate = datePreset
    ? new Date(now.getTime() - parseInt(datePreset) * 86400000)
        .toISOString().split('T')[0]
    : undefined;

  const { data: statsData, isLoading: statsLoading } = useQuery({
    queryKey: ['user-login-stats', email],
    queryFn: () => getUserLoginStats(email),
    enabled: open,
  });

  const { data: logsData, isLoading: logsLoading } = useQuery({
    queryKey: ['user-login-logs', email, page, datePreset],
    queryFn: () =>
      getUserLoginLogs(email, {
        page,
        size: 20,
        start_date: startDate,
      }),
    enabled: open,
  });

  // Reset page when date filter changes
  const handleDateChange = (value: string) => {
    setDatePreset(value);
    setPage(1);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[480px] sm:w-[540px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>
            {t('admin.loginLogsTitle')} — {nickname || email}
          </SheetTitle>
        </SheetHeader>

        <div className="mt-4 space-y-4">
          <LoginLogsStats
            stats={statsData?.data}
            isLoading={statsLoading}
          />

          <div className="flex items-center gap-2">
            <Select value={datePreset} onValueChange={handleDateChange}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATE_PRESETS.map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {t(label)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <LoginLogsTable
            logs={logsData?.data?.logs}
            total={logsData?.data?.total}
            page={page}
            size={20}
            onPageChange={setPage}
            isLoading={logsLoading}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/admin/login-logs-drawer.tsx web/src/pages/admin/login-logs-stats.tsx web/src/pages/admin/login-logs-table.tsx
git commit -m "feat: add login logs drawer components with stats and table"
```

---

### Task 7: Add「登录记录」button to admin users page

**Files:**
- Modify: `web/src/pages/admin/users.tsx`

- [ ] **Step 1: Add imports**

At the top of `users.tsx`, add the Sheet import and the new component:

```tsx
import { LucideClock } from 'lucide-react';  // add to existing lucide-react import
import LoginLogsDrawer from './login-logs-drawer';
```

Add `LucideClock` to the existing lucide-react import on line 23-28.

- [ ] **Step 2: Add state for the Drawer**

Inside `AdminUserManagement()`, after the existing state declarations (around line 134), add:

```tsx
const [loginLogsOpen, setLoginLogsOpen] = useState(false);
const [loginLogsUser, setLoginLogsUser] = useState<AdminService.ListUsersItem | null>(null);
```

- [ ] **Step 3: Add the button in the actions column**

In the `columnHelper.display` for `actions` (around line 392-436), add a new button before the existing `LucideClipboardList` button:

```tsx
              <Button
                variant="transparent"
                size="icon"
                className="border-0"
                onClick={() => {
                  setLoginLogsUser(row.original);
                  setLoginLogsOpen(true);
                }}
              >
                <LucideClock />
              </Button>
```

- [ ] **Step 4: Add the Drawer component at the end of the JSX**

After the closing `</Card>` tag of the main card (before the dialog modals), add:

```tsx
      {loginLogsUser && (
        <LoginLogsDrawer
          open={loginLogsOpen}
          onOpenChange={setLoginLogsOpen}
          email={loginLogsUser.email}
          nickname={loginLogsUser.nickname}
        />
      )}
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/admin/users.tsx
git commit -m "feat: add login logs button and drawer to admin users page"
```

---

### Task 8: Add i18n translations

**Files:**
- Modify: `web/src/locales/en.ts`
- Modify: `web/src/locales/zh.ts`

- [ ] **Step 1: Add English translations**

In `en.ts`, add to the `admin:` section (after `changePassword: 'Change password'`, around line 2987):

```typescript
      loginLogsTitle: 'Login logs',
      loginLogsTotal: 'Total logins',
      loginLogsRecent7d: 'Last 7 days',
      loginLogsLastLogin: 'Last login',
      loginLogsCommonDevice: 'Common device',
      loginLogsTime: 'Time',
      loginLogsDevice: 'Device',
      loginLogsChannel: 'Method',
      loginLogsAllTime: 'All time',
      loginLogs7Days: 'Last 7 days',
      loginLogs30Days: 'Last 30 days',
      loginLogsNoData: 'No login records',
```

- [ ] **Step 2: Add Chinese translations**

In `zh.ts`, add to the corresponding section (after `changePassword: '修改密码'`, around line 1199):

```typescript
      loginLogsTitle: '登录记录',
      loginLogsTotal: '总登录次数',
      loginLogsRecent7d: '近7天登录',
      loginLogsLastLogin: '最近登录',
      loginLogsCommonDevice: '常用设备',
      loginLogsTime: '时间',
      loginLogsDevice: '设备',
      loginLogsChannel: '登录方式',
      loginLogsAllTime: '全部时间',
      loginLogs7Days: '最近7天',
      loginLogs30Days: '最近30天',
      loginLogsNoData: '暂无登录记录',
```

- [ ] **Step 3: Commit**

```bash
git add web/src/locales/en.ts web/src/locales/zh.ts
git commit -m "feat: add i18n translations for login logs feature"
```

---

### Task 9: Build verification

**Files:** None (verification only)

- [ ] **Step 1: Verify backend starts without errors**

Run: `cd D:/AI/ragflow2 && python -c "from api.db.db_models import UserLoginLog; from api.db.services.user_login_log_service import UserLoginLogService; print('Backend OK')"`
Expected: `Backend OK`

- [ ] **Step 2: Verify frontend builds without errors**

Run: `cd D:/AI/ragflow2/web && npx tsc --noEmit 2>&1 | head -20`
Expected: No TypeScript errors in the new files

- [ ] **Step 3: Final commit with any fixes**

```bash
git add -A
git commit -m "fix: address build issues from review"
```
