import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  disableDetect,
  enableDetect,
  fetchDetectActivity,
  fetchDetectStats,
  installDetect,
  listDetectState,
  resetDetect,
  triggerDetect,
  type DetectActivityItem,
  type DetectStateList,
  type DetectStateRow,
  type DetectStats,
} from '@/services/collection-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  Clock,
  Loader2,
  Power,
  PowerOff,
  RefreshCw,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const REASON_KEY_MAP: Record<string, string> = {
  changed: 'crawl4ai.detect.reasonChanged',
  unchanged: 'crawl4ai.detect.reasonUnchanged',
  empty: 'crawl4ai.detect.reasonEmpty',
  locked: 'crawl4ai.detect.reasonLocked',
  error: 'crawl4ai.detect.reasonError',
};

function formatDuration(
  sec: number,
  t: (k: string, opts?: any) => string,
): string {
  if (sec <= 0) return t('crawl4ai.detect.justNow');
  if (sec < 60)
    return t('crawl4ai.detect.inTime', {
      n: sec,
      unit: t('crawl4ai.detect.seconds'),
    });
  if (sec < 3600) {
    const m = Math.round(sec / 60);
    return t('crawl4ai.detect.inTime', {
      n: m,
      unit: t('crawl4ai.detect.minutes'),
    });
  }
  const h = Math.round(sec / 3600);
  return t('crawl4ai.detect.inTime', {
    n: h,
    unit: t('crawl4ai.detect.hours'),
  });
}

function formatAgo(sec: number, t: (k: string, opts?: any) => string): string {
  if (sec <= 0) return t('crawl4ai.detect.justNow');
  if (sec < 60)
    return t('crawl4ai.detect.agoTime', {
      n: sec,
      unit: t('crawl4ai.detect.seconds'),
    });
  if (sec < 3600) {
    const m = Math.round(sec / 60);
    return t('crawl4ai.detect.agoTime', {
      n: m,
      unit: t('crawl4ai.detect.minutes'),
    });
  }
  const h = Math.round(sec / 3600);
  return t('crawl4ai.detect.agoTime', {
    n: h,
    unit: t('crawl4ai.detect.hours'),
  });
}

function StatusBadge({ status, t }: { status: string; t: any }) {
  const map: Record<string, { label: string; cls: string }> = {
    due: {
      label: t('crawl4ai.detect.statusDue'),
      cls: 'bg-blue-100 text-blue-700',
    },
    active: {
      label: t('crawl4ai.detect.statusActive'),
      cls: 'bg-green-100 text-green-700',
    },
    cold: {
      label: t('crawl4ai.detect.statusCold'),
      cls: 'bg-gray-100 text-gray-600',
    },
    error: {
      label: t('crawl4ai.detect.statusError'),
      cls: 'bg-amber-100 text-amber-700',
    },
    auto_disabled: {
      label: t('crawl4ai.detect.statusAutoDisabled'),
      cls: 'bg-red-100 text-red-700',
    },
    manual_disabled: {
      label: t('crawl4ai.detect.statusManualDisabled'),
      cls: 'bg-red-100 text-red-700',
    },
    never_probed: {
      label: t('crawl4ai.detect.statusNeverProbed'),
      cls: 'bg-gray-100 text-gray-500',
    },
  };
  const meta = map[status] || {
    label: status,
    cls: 'bg-gray-100 text-gray-600',
  };
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

export function DetectTab() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data: stateData, isFetching } = useQuery<DetectStateList>({
    queryKey: ['detectState'],
    initialData: { list: [], total: 0, now: 0 },
    refetchInterval: 10000,
    queryFn: async () => {
      const { data: res } = await listDetectState({
        page: 1,
        page_size: 200,
      });
      return res?.code === 0 ? res.data : { list: [], total: 0, now: 0 };
    },
  });

  const { data: statsData } = useQuery<DetectStats>({
    queryKey: ['detectStats'],
    initialData: { total: 0, buckets: {}, avg_interval: 0, now: 0 },
    refetchInterval: 15000,
    queryFn: async () => {
      const { data: res } = await fetchDetectStats();
      return res?.code === 0
        ? res.data
        : { total: 0, buckets: {}, avg_interval: 0, now: 0 };
    },
  });

  // 近 1h 活动流 — 10s 轮询; 新出现的 site_id 触发 2s 绿色高亮淡出
  const { data: activityData } = useQuery<{
    items: DetectActivityItem[];
    total_count: number;
  }>({
    queryKey: ['detectActivity'],
    initialData: { items: [], total_count: 0 },
    refetchInterval: 10000,
    queryFn: async () => {
      const { data: res } = await fetchDetectActivity(3600, 20);
      return res?.code === 0
        ? {
            items: res.data?.items ?? [],
            total_count: res.data?.total_count ?? 0,
          }
        : { items: [], total_count: 0 };
    },
  });

  // 闪烁: 记录上次见过的 site_id 集合, 新出现的进入 flashing 集合 2s
  const prevActivityIdsRef = useRef<Set<string>>(new Set());
  const [flashingIds, setFlashingIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    const curIds = new Set((activityData?.items ?? []).map((i) => i.site_id));
    if (prevActivityIdsRef.current.size === 0) {
      // 首次加载, 不闪
      prevActivityIdsRef.current = curIds;
      return;
    }
    const newIds = new Set<string>();
    curIds.forEach((id) => {
      if (!prevActivityIdsRef.current.has(id)) newIds.add(id);
    });
    // 既有 site 的新增 count 也闪 (通过 last_at_ms 变化判断)
    if (newIds.size > 0) {
      setFlashingIds(newIds);
      const t = setTimeout(() => setFlashingIds(new Set()), 2000);
      prevActivityIdsRef.current = curIds;
      return () => clearTimeout(t);
    }
    prevActivityIdsRef.current = curIds;
  }, [activityData]);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['detectState'] });
    queryClient.invalidateQueries({ queryKey: ['detectStats'] });
  }, [queryClient]);

  const resetMutation = useMutation({
    mutationFn: async (site_id: string) => (await resetDetect(site_id)).data,
    onSuccess: (res: any) => {
      if (res?.code === 0) message.success(t('crawl4ai.detect.resetSuccess'));
      else message.error(res?.message ?? t('crawl4ai.detect.opFailed'));
      refresh();
    },
  });

  const triggerMutation = useMutation({
    mutationFn: async (site_id: string) => (await triggerDetect(site_id)).data,
    onSuccess: (res: any) => {
      if (res?.code === 0) message.success(t('crawl4ai.detect.triggerSuccess'));
      else message.error(res?.message ?? t('crawl4ai.detect.opFailed'));
      refresh();
    },
  });

  const disableMutation = useMutation({
    mutationFn: async (site_id: string) => (await disableDetect(site_id)).data,
    onSuccess: (res: any) => {
      if (res?.code === 0) message.success(t('crawl4ai.detect.disableSuccess'));
      else message.error(res?.message ?? t('crawl4ai.detect.opFailed'));
      refresh();
    },
  });

  const enableMutation = useMutation({
    mutationFn: async (site_id: string) => (await enableDetect(site_id)).data,
    onSuccess: (res: any) => {
      if (res?.code === 0) message.success(t('crawl4ai.detect.enableSuccess'));
      else message.error(res?.message ?? t('crawl4ai.detect.opFailed'));
      refresh();
    },
  });

  const installMutation = useMutation({
    mutationFn: async () => {
      // kb_id 已废弃 —— 探测器不再消费 kb_id, 爬虫脚本按 site_id 查 crawler_task 表自动获取
      return (await installDetect(60)).data;
    },
    onSuccess: (res: any) => {
      if (res?.code === 0) message.success(t('crawl4ai.detect.installSuccess'));
      else message.error(res?.message ?? t('crawl4ai.detect.installFailed'));
      refresh();
    },
  });

  const rowsRaw: DetectStateRow[] = stateData?.list ?? [];
  // 异常优先: auto_disabled → error → manual_disabled → 其余按原顺序 (due 优先)
  const statusOrder: Record<string, number> = {
    auto_disabled: 0,
    error: 1,
    manual_disabled: 2,
    due: 3,
    active: 4,
    cold: 5,
    never_probed: 6,
  };
  const rows = [...rowsRaw].sort(
    (a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9),
  );
  const buckets = statsData?.buckets ?? {};
  const avgInterval = statsData?.avg_interval ?? 0;
  const activityItems = activityData?.items ?? [];
  const activityTotal = activityData?.total_count ?? 0;

  const stats = [
    {
      key: 'total',
      label: t('crawl4ai.detect.statsTotal'),
      val: statsData?.total ?? 0,
      cls: 'text-gray-700',
      alert: false,
    },
    {
      key: 'due',
      label: t('crawl4ai.detect.statsDue'),
      val: buckets.due ?? 0,
      cls: 'text-blue-600',
      alert: false,
    },
    {
      key: 'active',
      label: t('crawl4ai.detect.statsActive'),
      val: buckets.active ?? 0,
      cls: 'text-green-600',
      alert: false,
    },
    {
      key: 'cold',
      label: t('crawl4ai.detect.statsCold'),
      val: buckets.cold ?? 0,
      cls: 'text-gray-500',
      alert: false,
    },
    {
      key: 'error',
      label: t('crawl4ai.detect.statsError'),
      val: buckets.error ?? 0,
      cls: 'text-amber-600',
      alert: (buckets.error ?? 0) > 0,
    },
    {
      key: 'auto_disabled',
      label: t('crawl4ai.detect.statsAutoDisabled'),
      val: buckets.auto_disabled ?? 0,
      cls: 'text-red-600',
      alert: (buckets.auto_disabled ?? 0) > 0,
    },
    {
      key: 'manual_disabled',
      label: t('crawl4ai.detect.statsManualDisabled'),
      val: buckets.manual_disabled ?? 0,
      cls: 'text-red-600',
      alert: (buckets.manual_disabled ?? 0) > 0,
    },
    {
      key: 'never_probed',
      label: t('crawl4ai.detect.statsNeverProbed'),
      val: buckets.never_probed ?? 0,
      cls: 'text-gray-400',
      alert: false,
    },
  ];

  return (
    <div className="flex flex-col h-full gap-3">
      {/* 顶部统计 + 安装按钮 */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {stats.map((s) => (
          <div
            key={s.key}
            className={`flex items-center gap-1.5 rounded border px-2.5 py-1.5 ${
              s.alert ? 'bg-red-50 border-red-300 animate-pulse' : 'bg-card'
            }`}
          >
            <span className="text-muted-foreground">{s.label}</span>
            <span className={`font-semibold ${s.cls}`}>{s.val}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 rounded border bg-card px-2.5 py-1.5">
          <span className="text-muted-foreground">
            {t('crawl4ai.detect.statsAvgInterval')}
          </span>
          <span className="font-semibold text-purple-600">{avgInterval}s</span>
        </div>
        <div className="ml-auto flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={isFetching}
          >
            <RefreshCw
              className={`w-4 h-4 mr-1 ${isFetching ? 'animate-spin' : ''}`}
            />
            刷新
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => installMutation.mutate()}
            disabled={installMutation.isPending}
          >
            {installMutation.isPending ? (
              <Loader2 className="w-4 h-4 mr-1 animate-spin" />
            ) : (
              <Activity className="w-4 h-4 mr-1" />
            )}
            {t('crawl4ai.detect.install')}
          </Button>
        </div>
      </div>

      {/* 近 1h 活动流 */}
      <div className="rounded-md border bg-card px-3 py-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1.5">
          <Activity className="w-3.5 h-3.5" />
          <span>最近 1 小时活动</span>
          {activityTotal > 0 && (
            <span className="text-green-600 font-medium">
              合计 +{activityTotal} 条
            </span>
          )}
        </div>
        {activityItems.length === 0 ? (
          <div className="text-xs text-muted-foreground py-1">暂无近期采集</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {activityItems.map((it) => {
              const flashing = flashingIds.has(it.site_id);
              return (
                <TooltipProvider key={it.site_id} delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span
                        className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs border cursor-default ${
                          flashing
                            ? 'bg-green-100 border-green-400 text-green-800'
                            : 'bg-green-50 border-green-200 text-green-700'
                        } transition-colors duration-500`}
                      >
                        <span className="font-medium">
                          {it.site_name || it.site_id}
                        </span>
                        <span className="font-semibold">+{it.count}</span>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="max-w-[300px] text-xs">
                        {it.last_title || '(无标题)'}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              );
            })}
          </div>
        )}
      </div>

      {/* 站点列表 */}
      <div className="flex-1 min-h-0 rounded-md border overflow-auto">
        <Table>
          <TableHeader className="sticky top-0 bg-card z-10">
            <TableRow>
              <TableHead className="min-w-[200px]">
                {t('crawl4ai.detect.site')}
              </TableHead>
              <TableHead className="w-[90px]">
                {t('crawl4ai.detect.category')}
              </TableHead>
              <TableHead className="w-[110px]">
                {t('crawl4ai.detect.status')}
              </TableHead>
              <TableHead className="w-[80px] text-right">
                {t('crawl4ai.detect.missCount')}
              </TableHead>
              <TableHead className="w-[90px] text-right">
                {t('crawl4ai.detect.curInterval')}
              </TableHead>
              <TableHead className="w-[110px]">
                {t('crawl4ai.detect.nextRunIn')}
              </TableHead>
              <TableHead className="w-[110px]">
                {t('crawl4ai.detect.lastCheck')}
              </TableHead>
              <TableHead className="w-[80px] text-right">
                {t('crawl4ai.detect.lastNewCount')}
              </TableHead>
              <TableHead className="w-[100px]">
                {t('crawl4ai.detect.lastReason')}
              </TableHead>
              <TableHead className="w-[200px] text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={10}
                  className="text-center text-muted-foreground py-8"
                >
                  暂无探测数据。点击右上角 "{t('crawl4ai.detect.install')}"
                  注册探测器 meta-task。
                </TableCell>
              </TableRow>
            )}
            {rows.map((r) => {
              const reasonKey =
                REASON_KEY_MAP[r.last_reason] || r.last_reason || '';
              const disabled =
                r.status === 'auto_disabled' || r.status === 'manual_disabled';
              const isError = r.consecutive_errors > 0 || r.status === 'error';
              const rowCls =
                r.status === 'auto_disabled'
                  ? 'bg-red-50'
                  : isError
                    ? 'bg-amber-50'
                    : '';
              return (
                <TableRow key={r.site_id} className={rowCls}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      {r.status === 'auto_disabled' && (
                        <AlertTriangle className="w-3.5 h-3.5 text-red-600 flex-shrink-0" />
                      )}
                      <div className="min-w-0">
                        <div className="font-medium truncate max-w-[280px]">
                          {r.name}
                        </div>
                        <div className="text-xs text-muted-foreground font-mono">
                          {r.site_id}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-xs">{r.category}</TableCell>
                  <TableCell>
                    <StatusBadge status={r.status} t={t} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {r.miss_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-xs">
                    {r.cur_interval > 0 ? `${r.cur_interval}s` : '-'}
                  </TableCell>
                  <TableCell className="text-xs">
                    {r.next_run_at > 0
                      ? formatDuration(r.next_run_in_sec, t)
                      : '-'}
                  </TableCell>
                  <TableCell className="text-xs">
                    {r.last_check > 0
                      ? formatAgo(r.last_check_ago_sec, t)
                      : t('crawl4ai.detect.neverChecked')}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {r.last_new_count > 0 ? (
                      <span className="text-green-600">
                        +{r.last_new_count}
                      </span>
                    ) : (
                      '0'
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {reasonKey ? t(reasonKey) : '-'}
                    {r.consecutive_errors > 0 && (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="ml-1 inline-flex items-center text-amber-600">
                              <AlertTriangle className="w-3 h-3" />×
                              {r.consecutive_errors}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p className="max-w-[300px] text-xs">
                              {r.last_error || '-'}
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="inline-flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2"
                        title={t('crawl4ai.detect.actionTrigger')}
                        onClick={() => triggerMutation.mutate(r.site_id)}
                      >
                        <Zap className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2"
                        title={t('crawl4ai.detect.actionReset')}
                        onClick={() => resetMutation.mutate(r.site_id)}
                      >
                        <RefreshCw className="w-3.5 h-3.5" />
                      </Button>
                      {disabled ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-green-600"
                          title={t('crawl4ai.detect.actionEnable')}
                          onClick={() => enableMutation.mutate(r.site_id)}
                        >
                          <Power className="w-3.5 h-3.5" />
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-red-600"
                          title={t('crawl4ai.detect.actionDisable')}
                          onClick={() => disableMutation.mutate(r.site_id)}
                        >
                          <PowerOff className="w-3.5 h-3.5" />
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <div className="text-xs text-muted-foreground flex items-center gap-2">
        <Clock className="w-3 h-3" />
        页面每 10s 自动刷新；统计每 15s 刷新
      </div>
    </div>
  );
}
