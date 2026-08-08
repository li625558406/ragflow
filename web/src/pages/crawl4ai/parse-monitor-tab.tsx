import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import message from '@/components/ui/message';
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
  rerunFailedDocs,
  type FailedDocList,
  type FailedDocRow,
  type ParseMonitorOverview,
  type ReparseBatchList,
  type RerunFailedResult,
} from '@/services/collection-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  RefreshCw,
  RotateCw,
  XCircle,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const REFRESH_MS = 60_000;

// 失败原因下拉选项 (key 与后端 _FAILURE_REASON_MAP 对齐)
const REASON_OPTIONS: { key: string; label: string }[] = [
  { key: 'embedding_api', label: 'Embedding 服务调用失败' },
  { key: 'unsupported_filetype', label: '不支持的文件类型' },
  { key: 'ocr_error', label: 'OCR/图片解析失败' },
  { key: 'timeout', label: '解析超时' },
  { key: 'internal_error', label: '解析内部错误' },
  { key: 'download_failed', label: '文件下载失败' },
  { key: 'other', label: '其他错误' },
  { key: 'empty', label: '无错误信息' },
];

const REASON_COLOR_CLASS: Record<string, string> = {
  amber: 'text-amber-600',
  gray: 'text-muted-foreground',
  red: 'text-red-600',
  orange: 'text-orange-600',
};

// 滑动窗口批量重跑参数
const RERUN_BATCH_SIZE = 20; // 每批最多处理多少条 (后端硬上限 200)
const RERUN_BATCH_DELAY_MS = 10_000; // 批次之间的等待，给 task_executor 消化时间
const RERUN_MAX_BATCHES = 500; // 安全熔断，避免无限循环 (500*20 = 10000 条上限)

interface RerunProgress {
  active: boolean;
  reason_key?: string;
  batches_done: number;
  total_processed: number; // 累计尝试数 (每个 doc 算 1)
  total_success: number;
  total_failed: number;
  total_skipped: number;
  last_batch?: RerunFailedResult;
}

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
  const queryClient = useQueryClient();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [failedPage, setFailedPage] = useState(1);
  const [failedStatus, setFailedStatus] = useState<string>(''); // '' | 'fail' | 'stuck'
  const [failedReason, setFailedReason] = useState<string>(''); // '' | reason_key

  // ── 滑动窗口批量重跑状态 (在 failedQuery 之前声明，避免 TDZ) ──
  const rerunStopRequested = useRef(false);
  const [rerunProgress, setRerunProgress] = useState<RerunProgress>({
    active: false,
    batches_done: 0,
    total_processed: 0,
    total_success: 0,
    total_failed: 0,
    total_skipped: 0,
  });

  const overviewQuery = useQuery<ParseMonitorOverview>({
    queryKey: ['parse-monitor-overview'],
    queryFn: async () => {
      const { data: body } = await fetchParseMonitorOverview();
      return body?.data ?? ({} as ParseMonitorOverview);
    },
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const batchesQuery = useQuery<ReparseBatchList>({
    queryKey: ['parse-monitor-batches'],
    queryFn: async () => {
      const { data: body } = await fetchReparseBatches();
      return body?.data ?? ({ list: [], now: 0 } as ReparseBatchList);
    },
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  });

  const failedQuery = useQuery<FailedDocList>({
    queryKey: ['parse-monitor-failed', failedPage, failedStatus, failedReason],
    queryFn: async () => {
      const { data: body } = await listFailedDocs({
        page: failedPage,
        page_size: 20,
        status: failedStatus,
        reason_key: failedReason || undefined,
      });
      return (
        body?.data ??
        ({ list: [], total: 0, page: 1, page_size: 20 } as FailedDocList)
      );
    },
    // 重跑进行中时加速刷新，便于看到失败列表缩减
    refetchInterval: autoRefresh
      ? rerunProgress.active
        ? 5_000
        : REFRESH_MS
      : false,
  });

  // ── 滑动窗口批量重跑 ────────────────────────────────────────
  // 每次 POST /rerun-failed 处理最老的 RERUN_BATCH_SIZE 条 (run='4' 且按 reason 过滤)
  // 重置成 run='1' (RUNNING) 后它们就不再匹配筛选条件 → 下一次自动拿到下一批
  // 中间 sleep RERUN_BATCH_DELAY_MS，给 task_executor 消化的窗口
  const startRerun = async (reason_key?: string) => {
    if (rerunProgress.active) return;
    rerunStopRequested.current = false;
    setRerunProgress({
      active: true,
      reason_key,
      batches_done: 0,
      total_processed: 0,
      total_success: 0,
      total_failed: 0,
      total_skipped: 0,
    });

    let batches = 0;
    let processed = 0;
    let success = 0;
    let failed = 0;
    let skipped = 0;

    try {
      while (batches < RERUN_MAX_BATCHES) {
        if (rerunStopRequested.current) break;

        // 拉一批
        const res: any = await rerunFailedDocs({
          reason_key,
          limit: RERUN_BATCH_SIZE,
        });
        const body = res?.data;
        if (body?.code !== 0) {
          throw new Error(body?.message || 'rerun API error');
        }
        const batch: RerunFailedResult = body.data;
        batches += 1;
        processed += batch.total;
        success += batch.success;
        failed += batch.failed;
        skipped += batch.skipped;
        setRerunProgress({
          active: true,
          reason_key,
          batches_done: batches,
          total_processed: processed,
          total_success: success,
          total_failed: failed,
          total_skipped: skipped,
          last_batch: batch,
        });

        // 这一批扫到的总数 < 批大小 → 已经清空，可以停
        if (batch.total < RERUN_BATCH_SIZE) {
          break;
        }

        // 等下一批 (允许中断)
        await new Promise<void>((resolve) => {
          const t0 = Date.now();
          const tick = () => {
            if (
              rerunStopRequested.current ||
              Date.now() - t0 >= RERUN_BATCH_DELAY_MS
            ) {
              resolve();
            } else {
              setTimeout(tick, 250);
            }
          };
          tick();
        });
      }
      message.success(
        t('crawl4ai.parseMonitor.rerunDone', {
          success,
          failed,
          skipped,
          sec: '?',
        }),
      );
    } catch (e: any) {
      message.error(e?.message || 'rerun failed');
    } finally {
      setRerunProgress((p) => ({ ...p, active: false }));
      // 刷新三个面板
      queryClient.invalidateQueries({ queryKey: ['parse-monitor-failed'] });
      queryClient.invalidateQueries({ queryKey: ['parse-monitor-overview'] });
      queryClient.invalidateQueries({ queryKey: ['parse-monitor-batches'] });
    }
  };

  const stopRerun = () => {
    rerunStopRequested.current = true;
  };

  const handleRerunClick = () => {
    if (rerunProgress.active) {
      stopRerun();
      return;
    }
    const total = failedQuery.data?.total ?? 0;
    if (total <= 0) return;
    const reasonLabel =
      failedReason && REASON_OPTIONS.find((r) => r.key === failedReason)?.label;
    const confirmBody = reasonLabel
      ? t('crawl4ai.parseMonitor.rerunConfirmBodyReason', {
          total,
          reason: reasonLabel,
        })
      : t('crawl4ai.parseMonitor.rerunConfirmBody', { total });
    if (
      !window.confirm(
        `${t('crawl4ai.parseMonitor.rerunConfirmTitle')}\n\n${confirmBody}`,
      )
    )
      return;
    // 不 await：让循环在后台跑，UI 实时显示进度
    void startRerun(failedReason || undefined);
  };

  const ov = overviewQuery.data;
  const isLoading = overviewQuery.isLoading;
  const anyFetching =
    overviewQuery.isFetching ||
    batchesQuery.isFetching ||
    failedQuery.isFetching;

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
            <RefreshCw className="size-4" />
            {autoRefresh
              ? t('crawl4ai.parseMonitor.autoOn')
              : t('crawl4ai.parseMonitor.autoOff')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={anyFetching}
            onClick={() => {
              overviewQuery.refetch();
              batchesQuery.refetch();
              failedQuery.refetch();
            }}
          >
            <RefreshCw
              className={`size-4 ${anyFetching ? 'animate-spin' : ''}`}
            />
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
              <CardTitle>
                {t('crawl4ai.parseMonitor.throughputTitle')}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm grid grid-cols-3 gap-4">
              <div>
                <div className="text-muted-foreground">
                  {t('crawl4ai.parseMonitor.doneLast1h')}
                </div>
                <div className="text-2xl font-semibold">{ov.done_last_1h}</div>
              </div>
              <div>
                <div className="text-muted-foreground">
                  {t('crawl4ai.parseMonitor.rate')}
                </div>
                <div className="text-2xl font-semibold">
                  {(ov.rate_per_min ?? 0).toFixed(1)}
                  <span className="text-sm ml-1">docs/min</span>
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">
                  {t('crawl4ai.parseMonitor.eta')}
                </div>
                <div className="text-2xl font-semibold">
                  {ov.backlog > 0
                    ? formatEta(ov.eta_sec, t)
                    : t('crawl4ai.parseMonitor.noBacklog')}
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
                  <TableHead className="text-right">
                    {t('crawl4ai.parseMonitor.batchTotal')}
                  </TableHead>
                  <TableHead className="text-right">
                    {t('crawl4ai.parseMonitor.batchSuccess')}
                  </TableHead>
                  <TableHead className="text-right">
                    {t('crawl4ai.parseMonitor.batchFailed')}
                  </TableHead>
                  <TableHead className="text-right">
                    {t('crawl4ai.parseMonitor.batchSkipped')}
                  </TableHead>
                  <TableHead className="text-right">
                    {t('crawl4ai.parseMonitor.batchDuration')}
                  </TableHead>
                  <TableHead>
                    {t('crawl4ai.parseMonitor.batchErrors')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(batchesQuery.data?.list ?? []).map((b, i) => (
                  <TableRow key={i}>
                    <TableCell className="whitespace-nowrap">
                      {formatTs(b.ts)}
                    </TableCell>
                    <TableCell className="text-right">{b.total}</TableCell>
                    <TableCell className="text-right text-green-600">
                      {b.success}
                    </TableCell>
                    <TableCell className="text-right text-red-600">
                      {b.failed}
                    </TableCell>
                    <TableCell className="text-right">{b.skipped}</TableCell>
                    <TableCell className="text-right">
                      {b.duration_sec}s
                    </TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                      {b.first_errors
                        .map((e) => `${e.doc_id}: ${e.msg}`)
                        .join(' | ') || '-'}
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
        <CardHeader className="flex-row items-center justify-between gap-3 flex-wrap">
          <CardTitle>{t('crawl4ai.parseMonitor.failedTitle')}</CardTitle>
          <div className="flex items-center gap-2 flex-wrap">
            <Select
              value={failedStatus || 'all'}
              onValueChange={(v) => {
                setFailedStatus(v === 'all' ? '' : v);
                setFailedPage(1);
              }}
            >
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('crawl4ai.parseMonitor.statusAll')}
                </SelectItem>
                <SelectItem value="fail">
                  {t('crawl4ai.parseMonitor.statusFail')}
                </SelectItem>
                <SelectItem value="stuck">
                  {t('crawl4ai.parseMonitor.statusStuck')}
                </SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={failedReason || 'all'}
              onValueChange={(v) => {
                setFailedReason(v === 'all' ? '' : v);
                setFailedPage(1);
              }}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('crawl4ai.parseMonitor.reasonAll')}
                </SelectItem>
                {REASON_OPTIONS.map((r) => (
                  <SelectItem key={r.key} value={r.key}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant={rerunProgress.active ? 'destructive' : 'default'}
              size="sm"
              onClick={handleRerunClick}
              disabled={
                !rerunProgress.active && (failedQuery.data?.total ?? 0) <= 0
              }
              title={
                rerunProgress.active
                  ? `已处理 ${rerunProgress.total_processed} 条 (成功 ${rerunProgress.total_success} / 失败 ${rerunProgress.total_failed})，批次 ${rerunProgress.batches_done}，点击停止`
                  : ''
              }
            >
              <RotateCw
                className={`size-4 ${rerunProgress.active ? 'animate-spin' : ''}`}
              />
              {rerunProgress.active
                ? `${t('crawl4ai.parseMonitor.rerunRunning')} (${rerunProgress.total_processed})`
                : t('crawl4ai.parseMonitor.rerunBtn')}
            </Button>
          </div>
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
                    <TableHead>
                      {t('crawl4ai.parseMonitor.colStatus')}
                    </TableHead>
                    <TableHead className="text-right">
                      {t('crawl4ai.parseMonitor.colProgress')}
                    </TableHead>
                    <TableHead>
                      {t('crawl4ai.parseMonitor.colReason')}
                    </TableHead>
                    <TableHead>
                      {t('crawl4ai.parseMonitor.colUpdate')}
                    </TableHead>
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
                      <TableCell className="text-right">
                        {(d.progress || 0).toFixed(0)}%
                      </TableCell>
                      <TableCell
                        className="max-w-md truncate text-xs"
                        title={d.progress_msg || d.reason}
                      >
                        <span
                          className={
                            REASON_COLOR_CLASS[d.reason_color] ||
                            'text-muted-foreground'
                          }
                        >
                          {d.reason || '-'}
                        </span>
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
          {(value ?? 0).toLocaleString()}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {pct.toFixed(1)}%
        </div>
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
      <Button
        variant="outline"
        size="sm"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
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
