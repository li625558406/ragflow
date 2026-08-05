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
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  RefreshCw,
  XCircle,
} from 'lucide-react';
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
    queryFn: () =>
      listFailedDocs({ page: failedPage, page_size: 20, status: failedStatus }),
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
            <RefreshCw
              className={`size-4 ${autoRefresh ? 'animate-spin' : ''}`}
            />
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
                  {ov.rate_per_min.toFixed(1)}
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
                    <TableHead>{t('crawl4ai.parseMonitor.colMsg')}</TableHead>
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
