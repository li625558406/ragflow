# 解析监控 Tab 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 智能采集 页面新增「解析监控」Tab，长期监控 Document 解析状态分布、吞吐/ETA、bulk_reparse 批次历史、失败文档列表。

**Architecture:** 后端复用 `collection_app.py` blueprint 新增 3 个端点，Redis 60s TTL 缓存对齐前端轮询；前端 `parse-monitor-tab.tsx` 新组件挂到 crawl4ai 第 4 个 Tab。`_bulk_reparse_zombies.py` 在 `main()` 末尾将批次摘要 LPUSH 到 Redis List 供前端读取，避免后端读容器内 log 文件。

**Tech Stack:** Python Quart + Peewee + Redis（后端） / React 18 + TanStack Query + shadcn/ui + Tailwind（前端）

**Spec:** `docs/superpowers/specs/2026-08-04-parse-monitor-design.md`

**测试约定：** 本项目 `api/apps/restful_apis/` 无单元测试基础设施（与探测监控端点一致），采用「容器内 curl + SQL 验证」冒烟测试代替 TDD。每个后端任务末尾给出验证命令。

---

## 文件结构

| 类型 | 路径 | 责任 |
|------|------|------|
| 改 | `api/apps/restful_apis/collection_app.py` | 新增 3 个 `/collection/parse-monitor/*` 端点 + Redis 缓存辅助 |
| 改 | `rag/svr/_bulk_reparse_zombies.py` | `main()` 末尾 LPUSH 批次摘要到 `parse_monitor:batches` |
| 改 | `web/src/utils/api.ts` | 新增 3 个 URL 常量 |
| 改 | `web/src/services/collection-service.ts` | 新增 3 个 fetch 函数 + TS 类型 |
| 新 | `web/src/pages/crawl4ai/parse-monitor-tab.tsx` | 解析监控 Tab 主组件 |
| 改 | `web/src/pages/crawl4ai/index.tsx` | 挂载第 4 个 TabsTrigger / TabsContent |
| 改 | `web/src/locales/zh.ts` | 新增 `crawl4ai.parseMonitor.*` keys |

---

## Task 1: 后端 — overview 端点 + Redis 缓存

**Files:**
- Modify: `api/apps/restful_apis/collection_app.py`

- [ ] **Step 1: 顶部新增 fn 导入**

定位到 `import peewee` 一行（约 32 行），在其后新增：

```python
from peewee import fn
```

- [ ] **Step 2: 在 `collection_app.py` 末尾追加 overview 端点**

完整代码（粘贴到文件末尾）：

```python
# ---------------------------------------------------------------------------
# 解析监控 (Parse monitor)
# ---------------------------------------------------------------------------

_PARSE_MONITOR_OVERVIEW_KEY = "parse_monitor:overview"
_PARSE_MONITOR_OVERVIEW_TTL = 60  # 与前端轮询节奏一致
_PARSE_MONITOR_BATCHES_KEY = "parse_monitor:batches"
_PARSE_MONITOR_BATCHES_MAX = 20


def _compute_parse_overview() -> Dict[str, Any]:
    """聚合 Document.run 分布 + 最近 1h 完成数 + 吞吐 + ETA."""
    from api.db.db_models import Document, TaskStatus

    @DB.connection_context()
    def _q() -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        cutoff_30min_ms = now_ms - 30 * 60 * 1000
        cutoff_1h_ms = now_ms - 60 * 60 * 1000

        # run 分布
        state_counts: Dict[str, int] = {s.value: 0 for s in TaskStatus}
        for row in (Document
                    .select(Document.run, fn.COUNT(Document.id).alias("n"))
                    .group_by(Document.run)):
            state_counts[row.run] = int(row.n)

        total = sum(state_counts.values())
        running = state_counts.get(TaskStatus.RUNNING.value, 0)
        done = state_counts.get(TaskStatus.DONE.value, 0)
        failed = state_counts.get(TaskStatus.FAIL.value, 0)

        # 积压: run=1 且 30 分钟未更新 (与 _bulk_reparse_zombies 筛选一致)
        backlog = (Document
                   .select(fn.COUNT(Document.id))
                   .where((Document.run == TaskStatus.RUNNING.value)
                          & (Document.update_time < cutoff_30min_ms))
                   .scalar()) or 0

        # 最近 1h 完成数
        done_last_1h = (Document
                        .select(fn.COUNT(Document.id))
                        .where((Document.run == TaskStatus.DONE.value)
                               & (Document.update_time > cutoff_1h_ms))
                        .scalar()) or 0

        rate_per_min = round(done_last_1h / 60.0, 2)
        eta_sec = int(backlog / (rate_per_min / 60.0)) if rate_per_min > 0 else 0

        return {
            "now": int(now_ms / 1000),
            "states": state_counts,
            "total": total,
            "running": running,
            "done": done,
            "failed": failed,
            "backlog": int(backlog),
            "done_last_1h": int(done_last_1h),
            "rate_per_min": rate_per_min,
            "eta_sec": eta_sec,
        }

    return _q()


def _get_parse_overview() -> Dict[str, Any]:
    """带 60s Redis 缓存的 overview; Redis 异常时直查 DB."""
    rc = _detect_redis()  # 复用现有 lazy import
    if rc is not None:
        try:
            raw = rc.get(_PARSE_MONITOR_OVERVIEW_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logging.warning("parse_monitor: overview cache read failed: %s", e)

    data = _compute_parse_overview()
    data["cached_at"] = data["now"]

    if rc is not None:
        try:
            rc.set_obj(_PARSE_MONITOR_OVERVIEW_KEY, data, exp=_PARSE_MONITOR_OVERVIEW_TTL)
        except Exception as e:
            logging.warning("parse_monitor: overview cache write failed: %s", e)
    return data


@manager.route("/collection/parse-monitor/overview", methods=["GET"])  # noqa: F821
@login_required
async def parse_monitor_overview():
    """文档解析状态分布 + 吞吐 + ETA."""
    try:
        data = _get_parse_overview()
        return get_json_result(data=data)
    except Exception as e:
        logging.error("parse_monitor: overview failed: %s", e, exc_info=True)
        return get_data_error_result(message=f"overview failed: {e}")
```

- [ ] **Step 3: 冒烟测试**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from api.apps.restful_apis.collection_app import _compute_parse_overview
import json
print(json.dumps(_compute_parse_overview(), ensure_ascii=False, indent=2))
"
```

预期：打印 JSON，包含 `states / total / backlog / rate_per_min / eta_sec` 字段，无异常。

- [ ] **Step 4: 提交**

```bash
git add api/apps/restful_apis/collection_app.py
git commit -m "feat(parse-monitor): backend overview endpoint with 60s Redis cache"
```

---

## Task 2: 后端 — reparse-batches 端点

**Files:**
- Modify: `api/apps/restful_apis/collection_app.py`（在 Task 1 追加的代码块之后继续追加）

- [ ] **Step 1: 追加 batches 端点**

```python
@manager.route("/collection/parse-monitor/reparse-batches", methods=["GET"])  # noqa: F821
@login_required
async def parse_monitor_reparse_batches():
    """最近 N 次 bulk_reparse 批次摘要 (从 Redis List 读取)."""
    rc = _detect_redis()
    items: List[Dict[str, Any]] = []
    if rc is not None and getattr(rc, "REDIS", None) is not None:
        try:
            raw_list = rc.REDIS.lrange(_PARSE_MONITOR_BATCHES_KEY, 0, _PARSE_MONITOR_BATCHES_MAX - 1)
            for raw in raw_list:
                try:
                    items.append(json.loads(raw))
                except Exception:
                    continue
        except Exception as e:
            logging.warning("parse_monitor: batches read failed: %s", e)
    return get_json_result(data={"list": items, "now": int(time.time())})
```

- [ ] **Step 2: 冒烟测试**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from api.apps.restful_apis.collection_app import parse_monitor_reparse_batches
print('import ok')
"
```

预期：打印 `import ok`，无 ImportError。

- [ ] **Step 3: 提交**

```bash
git add api/apps/restful_apis/collection_app.py
git commit -m "feat(parse-monitor): backend reparse-batches endpoint"
```

---

## Task 3: 后端 — failed-docs 端点

**Files:**
- Modify: `api/apps/restful_apis/collection_app.py`（继续追加）

- [ ] **Step 1: 追加 failed-docs 端点**

```python
@manager.route("/collection/parse-monitor/failed-docs", methods=["GET"])  # noqa: F821
@login_required
async def parse_monitor_failed_docs():
    """失败/卡死文档分页列表.

    查询条件: run='4' (FAIL) OR (run='1' AND update_time < now-30min)
    不返回 content_hash / location 等内容字段.
    """
    from api.db.db_models import Document, Knowledgebase, TaskStatus

    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 100)
    status_filter = (request.args.get("status", "") or "").strip()
    kb_filter = (request.args.get("kb_id", "") or "").strip()

    now_ms = int(time.time() * 1000)
    cutoff_30min_ms = now_ms - 30 * 60 * 1000

    @DB.connection_context()
    def _q() -> Dict[str, Any]:
        fail_cond = (Document.run == TaskStatus.FAIL.value)
        stuck_cond = ((Document.run == TaskStatus.RUNNING.value)
                      & (Document.update_time < cutoff_30min_ms))

        if status_filter == "fail":
            base_where = fail_cond
        elif status_filter == "stuck":
            base_where = stuck_cond
        else:
            base_where = fail_cond | stuck_cond

        if kb_filter:
            base_where = base_where & (Document.kb_id == kb_filter)

        total = (Document
                 .select(fn.COUNT(Document.id))
                 .where(base_where)
                 .scalar()) or 0

        query = (Document
                 .select(Document.id, Document.kb_id, Document.name,
                         Document.run, Document.progress, Document.progress_msg,
                         Document.update_time, Document.process_begin_at,
                         Knowledgebase.name.alias("kb_name"))
                 .join(Knowledgebase, on=(Document.kb_id == Knowledgebase.id), join_type="LEFT")
                 .where(base_where)
                 .order_by(Document.update_time.asc())
                 .limit(page_size)
                 .offset((page - 1) * page_size))

        rows = []
        for r in query:
            rows.append({
                "id": r.id,
                "kb_id": r.kb_id,
                "kb_name": getattr(r, "kb_name", "") or "",
                "name": r.name,
                "run": r.run,
                "progress": r.progress or 0,
                "progress_msg": (r.progress_msg or "")[:200],
                "update_time": r.update_time,
                "process_begin_at": r.process_begin_at,
            })
        return {"list": rows, "total": int(total), "page": page, "page_size": page_size}

    try:
        return get_json_result(data=_q())
    except Exception as e:
        logging.error("parse_monitor: failed-docs failed: %s", e, exc_info=True)
        return get_data_error_result(message=f"failed-docs failed: {e}")
```

- [ ] **Step 2: 冒烟测试**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from api.apps.restful_apis.collection_app import parse_monitor_failed_docs
print('import ok')
"
```

预期：打印 `import ok`，无 ImportError。

- [ ] **Step 3: 提交**

```bash
git add api/apps/restful_apis/collection_app.py
git commit -m "feat(parse-monitor): backend failed-docs paginated endpoint"
```

---

## Task 4: 脚本 — bulk_reparse 写批次摘要到 Redis

**Files:**
- Modify: `rag/svr/_bulk_reparse_zombies.py`

- [ ] **Step 1: 在 `reparse_one` 之后、`main` 之前追加辅助函数**

```python
_PARSE_MONITOR_BATCHES_KEY = "parse_monitor:batches"
_PARSE_MONITOR_BATCHES_MAX = 20


def _push_batch_summary(total, success, failed, skipped, duration_sec, errors):
    """bulk_reparse 结束后将摘要 LPUSH 到 Redis List, LTRIM 保留最近 20 条.

    Redis 异常不影响主流程 (脚本仍正常退出).
    """
    try:
        from rag.utils.redis_conn import REDIS_CONN
        if REDIS_CONN is None or getattr(REDIS_CONN, "REDIS", None) is None:
            return
        import json as _json
        import time as _time
        payload = {
            "ts": int(_time.time()),
            "total": int(total),
            "success": int(success),
            "failed": int(failed),
            "skipped": int(skipped),
            "duration_sec": round(float(duration_sec), 2),
            "first_errors": [
                {"doc_id": d[:8], "msg": m[:200]} for d, m in errors[:5]
            ],
        }
        REDIS_CONN.REDIS.lpush(_PARSE_MONITOR_BATCHES_KEY, _json.dumps(payload, ensure_ascii=False))
        REDIS_CONN.REDIS.ltrim(_PARSE_MONITOR_BATCHES_KEY, 0, _PARSE_MONITOR_BATCHES_MAX - 1)
        logging.info("bulk_reparse: pushed batch summary to redis (total=%d success=%d)", total, success)
    except Exception as e:
        logging.warning("bulk_reparse: push batch summary failed: %s", e)
```

- [ ] **Step 2: 在 `main()` 末尾追加调用**

定位到 `main()` 中 `if errors:` 块结束后（即整个函数最末尾），追加：

```python
    # 写批次摘要到 Redis, 供 /collection/parse-monitor/reparse-batches 端点读取
    _push_batch_summary(total, success, failed, skipped_no_tenant,
                        time.time() - t0, errors)
```

注意：即使 `total == 0`（early return 分支）也要写入，便于前端展示"脚本按时运行但无积压"。把 `main()` 中的 `if total == 0: ... return` 改为：

```python
    if total == 0:
        logging.info("nothing to do, exit")
        _push_batch_summary(0, 0, 0, 0, 0.0, [])
        return
```

- [ ] **Step 3: 冒烟测试**

```bash
docker exec docker-ragflow-cpu-1 python -c "
import sys; sys.path.insert(0, '/ragflow')
from common import settings; settings.init_settings()
from rag.utils.redis_conn import REDIS_CONN
from rag.svr._bulk_reparse_zombies import _push_batch_summary
_push_batch_summary(10, 8, 1, 1, 3.5, [('abc12345', 'test error')])
print(REDIS_CONN.REDIS.lrange('parse_monitor:batches', 0, -1))
"
```

预期：打印包含刚才写入的批次 JSON 的列表。

- [ ] **Step 4: 提交**

```bash
git add rag/svr/_bulk_reparse_zombies.py
git commit -m "feat(parse-monitor): bulk_reparse pushes batch summary to Redis"
```

---

## Task 5: 前端 — API URL + service 函数

**Files:**
- Modify: `web/src/utils/api.ts`
- Modify: `web/src/services/collection-service.ts`

- [ ] **Step 1: 在 api.ts 中追加 URL 常量**

定位到 `collectionDetectInstall: ...` 一行（约 441 行），在其后追加：

```typescript
  // parse monitor
  collectionParseMonitorOverview: `${restAPIv1}/collection/parse-monitor/overview`,
  collectionParseMonitorBatches: `${restAPIv1}/collection/parse-monitor/reparse-batches`,
  collectionParseMonitorFailedDocs: `${restAPIv1}/collection/parse-monitor/failed-docs`,
```

- [ ] **Step 2: 在 collection-service.ts 末尾追加类型与函数**

```typescript
// ---------------------------------------------------------------------------
// 解析监控 (Parse monitor)
// ---------------------------------------------------------------------------

export interface ParseMonitorOverview {
  now: number;
  cached_at: number;
  states: Record<string, number>;
  total: number;
  running: number;
  done: number;
  failed: number;
  backlog: number;
  done_last_1h: number;
  rate_per_min: number;
  eta_sec: number;
}

export interface ReparseBatchItem {
  ts: number;
  total: number;
  success: number;
  failed: number;
  skipped: number;
  duration_sec: number;
  first_errors: Array<{ doc_id: string; msg: string }>;
}

export interface ReparseBatchList {
  list: ReparseBatchItem[];
  now: number;
}

export interface FailedDocRow {
  id: string;
  kb_id: string;
  kb_name: string;
  name: string;
  run: string;
  progress: number;
  progress_msg: string;
  update_time: number;
  process_begin_at: number;
}

export interface FailedDocList {
  list: FailedDocRow[];
  total: number;
  page: number;
  page_size: number;
}

export const fetchParseMonitorOverview = () =>
  request.get(api.collectionParseMonitorOverview);

export const fetchReparseBatches = () =>
  request.get(api.collectionParseMonitorBatches);

export const listFailedDocs = (params: {
  page?: number;
  page_size?: number;
  status?: string; // 'fail' | 'stuck' | '' (all)
  kb_id?: string;
}) => request.get(api.collectionParseMonitorFailedDocs, { params });
```

- [ ] **Step 3: TS 检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit 2>&1 | head -30
```

预期：无新增 TS 错误（既有错误可忽略）。

- [ ] **Step 4: 提交**

```bash
git add web/src/utils/api.ts web/src/services/collection-service.ts
git commit -m "feat(parse-monitor): frontend API URLs + service functions"
```

---

## Task 6: 前端 — parse-monitor-tab 组件

**Files:**
- Create: `web/src/pages/crawl4ai/parse-monitor-tab.tsx`

- [ ] **Step 1: 新建组件文件**

```tsx
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  fetchParseMonitorOverview,
  fetchReparseBatches,
  listFailedDocs,
  type FailedDocList,
  type FailedDocRow,
  type ParseMonitorOverview,
  type ReparseBatchList,
} from '@/services/collection-service';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Clock, Loader2, RefreshCw, XCircle } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

const REFRESH_MS = 60_000;

function formatEta(sec: number, t: (k: string, opts?: any) => string): string {
  if (sec <= 0) return t('crawl4ai.parseMonitor.noRate');
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return t('crawl4ai.parseMonitor.etaHm', { h, m });
  return t('crawl4ai.parseMonitor.etaMin', { m });
}

function formatTs(ts: number): string {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
}

export function ParseMonitorTab() {
  const { t } = useTranslation();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [failedPage, setFailedPage] = useState(1);
  const [failedStatus, setFailedStatus] = useState<string>(''); // '' | 'fail' | 'stuck'

  const overviewQuery = useQuery<ParseMonitorOverview>({
    queryKey: ['parse-monitor-overview'],
    queryFn: fetchParseMonitorOverview,
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const batchesQuery = useQuery<ReparseBatchList>({
    queryKey: ['parse-monitor-batches'],
    queryFn: fetchReparseBatches,
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const failedQuery = useQuery<FailedDocList>({
    queryKey: ['parse-monitor-failed', failedPage, failedStatus],
    queryFn: () => listFailedDocs({ page: failedPage, page_size: 20, status: failedStatus }),
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const ov = overviewQuery.data;
  const isLoading = overviewQuery.isLoading;

  return (
    <div className="flex flex-col gap-4 h-full overflow-auto pr-1">
      {/* 顶部控制条 */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {t('crawl4ai.parseMonitor.subtitle')}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={autoRefresh ? 'default' : 'outline'}
            size="sm"
            onClick={() => setAutoRefresh((v) => !v)}
          >
            <RefreshCw className={`size-4 ${autoRefresh ? 'animate-spin' : ''}`} />
            {autoRefresh
              ? t('crawl4ai.parseMonitor.autoOn')
              : t('crawl4ai.parseMonitor.autoOff')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              overviewQuery.refetch();
              batchesQuery.refetch();
              failedQuery.refetch();
            }}
          >
            <RefreshCw className="size-4" />
            {t('crawl4ai.parseMonitor.refreshNow')}
          </Button>
        </div>
      </div>

      {/* 概览卡片 */}
      {isLoading || !ov ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <OverviewCard
              icon={<Clock className="size-4" />}
              label={t('crawl4ai.parseMonitor.running')}
              value={ov.running}
              total={ov.total}
              tone="blue"
            />
            <OverviewCard
              icon={<CheckCircle2 className="size-4" />}
              label={t('crawl4ai.parseMonitor.done')}
              value={ov.done}
              total={ov.total}
              tone="green"
            />
            <OverviewCard
              icon={<XCircle className="size-4" />}
              label={t('crawl4ai.parseMonitor.failed')}
              value={ov.failed}
              total={ov.total}
              tone="red"
            />
            <OverviewCard
              icon={<AlertTriangle className="size-4" />}
              label={t('crawl4ai.parseMonitor.backlog')}
              value={ov.backlog}
              total={ov.total}
              tone={ov.backlog > 0 ? 'red' : 'gray'}
            />
          </div>

          {/* 吞吐 + ETA */}
          <Card>
            <CardHeader>
              <CardTitle>{t('crawl4ai.parseMonitor.throughputTitle')}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm grid grid-cols-3 gap-4">
              <div>
                <div className="text-muted-foreground">{t('crawl4ai.parseMonitor.doneLast1h')}</div>
                <div className="text-2xl font-semibold">{ov.done_last_1h}</div>
              </div>
              <div>
                <div className="text-muted-foreground">{t('crawl4ai.parseMonitor.rate')}</div>
                <div className="text-2xl font-semibold">
                  {ov.rate_per_min.toFixed(1)}
                  <span className="text-sm ml-1">docs/min</span>
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">{t('crawl4ai.parseMonitor.eta')}</div>
                <div className="text-2xl font-semibold">
                  {ov.backlog > 0 ? formatEta(ov.eta_sec, t) : t('crawl4ai.parseMonitor.noBacklog')}
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* bulk_reparse 批次 */}
      <Card>
        <CardHeader>
          <CardTitle>{t('crawl4ai.parseMonitor.batchesTitle')}</CardTitle>
        </CardHeader>
        <CardContent>
          {batchesQuery.isLoading ? (
            <Loader2 className="size-5 animate-spin" />
          ) : (batchesQuery.data?.list ?? []).length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 text-center">
              {t('crawl4ai.parseMonitor.noBatches')}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('crawl4ai.parseMonitor.batchTs')}</TableHead>
                  <TableHead className="text-right">{t('crawl4ai.parseMonitor.batchTotal')}</TableHead>
                  <TableHead className="text-right">{t('crawl4ai.parseMonitor.batchSuccess')}</TableHead>
                  <TableHead className="text-right">{t('crawl4ai.parseMonitor.batchFailed')}</TableHead>
                  <TableHead className="text-right">{t('crawl4ai.parseMonitor.batchSkipped')}</TableHead>
                  <TableHead className="text-right">{t('crawl4ai.parseMonitor.batchDuration')}</TableHead>
                  <TableHead>{t('crawl4ai.parseMonitor.batchErrors')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(batchesQuery.data?.list ?? []).map((b, i) => (
                  <TableRow key={i}>
                    <TableCell className="whitespace-nowrap">{formatTs(b.ts)}</TableCell>
                    <TableCell className="text-right">{b.total}</TableCell>
                    <TableCell className="text-right text-green-600">{b.success}</TableCell>
                    <TableCell className="text-right text-red-600">{b.failed}</TableCell>
                    <TableCell className="text-right">{b.skipped}</TableCell>
                    <TableCell className="text-right">{b.duration_sec}s</TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                      {b.first_errors.map((e) => `${e.doc_id}: ${e.msg}`).join(' | ') || '-'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* 失败/卡死文档 */}
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>{t('crawl4ai.parseMonitor.failedTitle')}</CardTitle>
          <Select
            value={failedStatus || 'all'}
            onValueChange={(v) => {
              setFailedStatus(v === 'all' ? '' : v);
              setFailedPage(1);
            }}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('crawl4ai.parseMonitor.statusAll')}</SelectItem>
              <SelectItem value="fail">{t('crawl4ai.parseMonitor.statusFail')}</SelectItem>
              <SelectItem value="stuck">{t('crawl4ai.parseMonitor.statusStuck')}</SelectItem>
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent>
          {failedQuery.isLoading ? (
            <Loader2 className="size-5 animate-spin" />
          ) : (failedQuery.data?.list ?? []).length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 text-center">
              {t('crawl4ai.parseMonitor.noFailed')}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('crawl4ai.parseMonitor.colName')}</TableHead>
                    <TableHead>{t('crawl4ai.parseMonitor.colKb')}</TableHead>
                    <TableHead>{t('crawl4ai.parseMonitor.colStatus')}</TableHead>
                    <TableHead className="text-right">{t('crawl4ai.parseMonitor.colProgress')}</TableHead>
                    <TableHead>{t('crawl4ai.parseMonitor.colMsg')}</TableHead>
                    <TableHead>{t('crawl4ai.parseMonitor.colUpdate')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(failedQuery.data?.list ?? []).map((d: FailedDocRow) => (
                    <TableRow key={d.id}>
                      <TableCell className="max-w-xs truncate" title={d.name}>
                        {d.name}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {d.kb_name || d.kb_id.slice(0, 8)}
                      </TableCell>
                      <TableCell>
                        {d.run === '4' ? (
                          <span className="text-red-600">
                            {t('crawl4ai.parseMonitor.statusFail')}
                          </span>
                        ) : (
                          <span className="text-amber-600">
                            {t('crawl4ai.parseMonitor.statusStuck')}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">{(d.progress || 0).toFixed(0)}%</TableCell>
                      <TableCell
                        className="max-w-md truncate text-xs text-muted-foreground"
                        title={d.progress_msg}
                      >
                        {d.progress_msg || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {formatTs(Math.floor((d.update_time || 0) / 1000))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pagination
                page={failedPage}
                pageSize={failedQuery.data?.page_size ?? 20}
                total={failedQuery.data?.total ?? 0}
                onPage={setFailedPage}
                t={t}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function OverviewCard({
  icon,
  label,
  value,
  total,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  total: number;
  tone: 'blue' | 'green' | 'red' | 'gray';
}) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  const toneClass = {
    blue: 'text-blue-600',
    green: 'text-green-600',
    red: 'text-red-600',
    gray: 'text-gray-600',
  }[tone];
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {icon}
          {label}
        </div>
        <div className={`mt-2 text-3xl font-bold ${toneClass}`}>
          {value.toLocaleString()}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">{pct.toFixed(1)}%</div>
      </CardContent>
    </Card>
  );
}

function Pagination({
  page,
  pageSize,
  total,
  onPage,
  t,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  t: (k: string, opts?: any) => string;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-end gap-2 mt-3 text-sm">
      <span className="text-muted-foreground">
        {t('crawl4ai.parseMonitor.pageOf', { page, total: totalPages })}
      </span>
      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {t('crawl4ai.parseMonitor.prev')}
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={page >= totalPages}
        onClick={() => onPage(page + 1)}
      >
        {t('crawl4ai.parseMonitor.next')}
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: TS 检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit 2>&1 | grep -i 'parse-monitor' | head -20
```

预期：无 parse-monitor-tab 相关报错。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/crawl4ai/parse-monitor-tab.tsx
git commit -m "feat(parse-monitor): frontend ParseMonitorTab component"
```

---

## Task 7: 前端 — 挂到 index.tsx + i18n

**Files:**
- Modify: `web/src/pages/crawl4ai/index.tsx`
- Modify: `web/src/locales/zh.ts`

- [ ] **Step 1: 修改 crawl4ai/index.tsx**

完整替换为：

```tsx
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DetectTab } from './detect-tab';
import { ParseMonitorTab } from './parse-monitor-tab';
import { ResultsTab } from './results-tab';
import { TasksTab } from './tasks-tab';

export default function Crawl4aiPage() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('results');

  return (
    <article className="size-full flex flex-col px-5 pt-8">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('crawl4ai.title')}</h1>
      </header>
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col min-h-0"
      >
        <TabsList className="w-fit">
          <TabsTrigger value="results">{t('crawl4ai.results')}</TabsTrigger>
          <TabsTrigger value="tasks">{t('crawl4ai.tasks')}</TabsTrigger>
          <TabsTrigger value="detect">{t('crawl4ai.detect.tab')}</TabsTrigger>
          <TabsTrigger value="parse-monitor">{t('crawl4ai.parseMonitor.tab')}</TabsTrigger>
        </TabsList>
        <TabsContent value="results" className="flex-1 min-h-0 mt-4">
          <ResultsTab />
        </TabsContent>
        <TabsContent value="tasks" className="flex-1 min-h-0 mt-4">
          <TasksTab />
        </TabsContent>
        <TabsContent value="detect" className="flex-1 min-h-0 mt-4">
          <DetectTab />
        </TabsContent>
        <TabsContent value="parse-monitor" className="flex-1 min-h-0 mt-4">
          <ParseMonitorTab />
        </TabsContent>
      </Tabs>
    </article>
  );
}
```

- [ ] **Step 2: 在 zh.ts 的 crawl4ai 块内追加 parseMonitor keys**

定位到 `crawl4ai: {` 块（约 2649 行），在 `detect: { ... }` 块结束的 `}` 之后、crawl4ai 闭合 `}` 之前追加：

```typescript
      parseMonitor: {
        tab: '解析监控',
        subtitle: '监控文档解析状态、吞吐、批量重解析历史与失败文档',
        autoOn: '自动刷新',
        autoOff: '已暂停',
        refreshNow: '立即刷新',
        running: '解析中',
        done: '已完成',
        failed: '失败',
        backlog: '积压',
        throughputTitle: '吞吐与预估',
        doneLast1h: '近 1 小时完成',
        rate: '平均速率',
        eta: '预计消化完',
        etaHm: '约 {{h}} 小时 {{m}} 分钟',
        etaMin: '约 {{m}} 分钟',
        noRate: '暂无吞吐数据',
        noBacklog: '无积压',
        batchesTitle: '批量重解析批次',
        noBatches: '暂无批次记录（脚本每 2 小时运行一次）',
        batchTs: '时间',
        batchTotal: '扫描',
        batchSuccess: '成功',
        batchFailed: '失败',
        batchSkipped: '跳过',
        batchDuration: '耗时',
        batchErrors: '错误摘要',
        failedTitle: '失败 / 卡死文档',
        statusAll: '全部',
        statusFail: '失败',
        statusStuck: '卡死',
        noFailed: '暂无失败/卡死文档',
        colName: '文件名',
        colKb: '知识库',
        colStatus: '状态',
        colProgress: '进度',
        colMsg: '错误信息',
        colUpdate: '更新时间',
        pageOf: '第 {{page}} / {{total}} 页',
        prev: '上一页',
        next: '下一页',
      },
```

- [ ] **Step 3: TS 检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit 2>&1 | head -20
```

预期：无新增错误。

- [ ] **Step 4: 构建**

```bash
cd D:/AI/ragflow2/web && npm run build
```

预期：build success，输出 `dist/`。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/crawl4ai/index.tsx web/src/locales/zh.ts
git commit -m "feat(parse-monitor): mount tab + zh i18n keys"
```

---

## Task 8: 部署冒烟

**Files:** 无（仅部署 + 验证）

- [ ] **Step 1: SCP 后端文件**

```bash
scp -i "D:/AI/konus-key.pem" -o StrictHostKeyChecking=no \
  D:/AI/ragflow2/api/apps/restful_apis/collection_app.py \
  D:/AI/ragflow2/rag/svr/_bulk_reparse_zombies.py \
  root@47.98.102.55:/tmp/

ssh -i "D:/AI/konus-key.pem" root@47.98.102.55 "
  cp /tmp/collection_app.py /home/bid-agent-konus/ragflow2/api/apps/restful_apis/collection_app.py
  cp /tmp/_bulk_reparse_zombies.py /home/bid-agent-konus/ragflow2/rag/svr/_bulk_reparse_zombies.py
"
```

- [ ] **Step 2: 重启 ragflow 容器**

```bash
ssh -i "D:/AI/konus-key.pem" root@47.98.102.55 "docker restart docker-ragflow-cpu-1"
```

- [ ] **Step 3: 部署前端 dist**

```bash
cd D:/AI/ragflow2/web
tar -czf dist.tar.gz dist/
scp -i "D:/AI/konus-key.pem" -o StrictHostKeyChecking=no dist.tar.gz root@47.98.102.55:/home/bid-agent-konus/ragflow2/web/
ssh -i "D:/AI/konus-key.pem" root@47.98.102.55 "
  cd /home/bid-agent-konus/ragflow2/web
  rm -rf dist/* dist/.[!.]* 2>/dev/null
  tar -xzf dist.tar.gz
  rm -f dist.tar.gz
  docker exec docker-ragflow-cpu-1 nginx -s reload
"
```

- [ ] **Step 4: 后端冒烟**

```bash
ssh -i "D:/AI/konus-key.pem" root@47.98.102.55 "
  docker exec docker-ragflow-cpu-1 python -c '
from api.apps.restful_apis.collection_app import (
    parse_monitor_overview, parse_monitor_reparse_batches, parse_monitor_failed_docs
)
print(\"imports ok\")
'
"
```

预期：打印 `imports ok`。

- [ ] **Step 5: 端到端冒烟（浏览器）**

打开 `https://47.98.102.55/5d41402abc4b2a76b9719d911017c592/crawl4ai`，点击「解析监控」Tab：
- 4 张概览卡片显示数值，与 `SELECT run, COUNT(*) FROM document GROUP BY run` SQL 结果一致
- 吞吐区显示 docs/min 与 ETA
- 批次表显示「暂无批次记录」或最近批次（cron 每 2 小时跑一次）
- 失败文档表可按状态下拉筛选、可翻页

- [ ] **Step 6: 手动触发一次 bulk_reparse 写入批次**

```bash
ssh -i "D:/AI/konus-key.pem" root@47.98.102.55 "
  docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/_bulk_reparse_zombies.py 2>&1 | tail -20
"
```

刷新前端批次表，应看到新行（即使 total=0 也会写入）。

---

## 验收清单

- [ ] 概览卡片数值与 SQL 一致
- [ ] ETA 在 backlog>0 时显示，否则显示「无积压」
- [ ] 批次表在脚本运行后能看到新行
- [ ] 失败文档表可筛选、可翻页
- [ ] 自动刷新开关可关闭（关闭后 60s 内不再发请求，可查 Network 面板）
- [ ] Redis 异常时端点不报错
