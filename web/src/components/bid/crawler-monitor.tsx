import DynamicIcon from '@/components/dynamic-icon';
import bidService from '@/services/bid-service';
import { useEffect, useMemo, useState } from 'react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SiteRow {
  site_id: string;
  name: string;
  detect_interval: number;
  last_check: number | null;
  last_check_ago: number | null;
  status: 'ok' | 'stale' | 'crawling' | 'never' | 'error';
  is_crawling: boolean;
  total_items: number;
  items_24h: number;
  latest_publish: string | null;
}

interface DetectorInfo {
  last_run: string | null;
  total_sites: number;
  enabled_sites: number;
  triggered: number;
  errors: number;
}

interface RecentRun {
  type: 'detector' | 'nightly';
  start_time: string | null;
  status: string;
  duration: number;
}

interface SummaryInfo {
  total_items: number;
  items_24h: number;
  items_7d: number;
  latest_publish: string | null;
  crawling_now: number;
  stale_sites: number;
}

interface CrawlerStatsData {
  detector: DetectorInfo;
  sites: SiteRow[];
  recent_runs: RecentRun[];
  summary: SummaryInfo;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; bg: string }
> = {
  ok: { label: '正常', color: 'text-emerald-700', bg: 'bg-emerald-50' },
  stale: { label: '滞后', color: 'text-amber-700', bg: 'bg-amber-50' },
  crawling: { label: '爬取中', color: 'text-blue-700', bg: 'bg-blue-50' },
  error: { label: '异常', color: 'text-red-700', bg: 'bg-red-50' },
  never: { label: '未探测', color: 'text-neutral-500', bg: 'bg-neutral-100' },
};

function formatAgo(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}秒前`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}小时前`;
  return `${Math.round(seconds / 86400)}天前`;
}

function formatInterval(sec: number): string {
  if (sec < 60) return `${sec}s`;
  return `${Math.round(sec / 60)}min`;
}

function formatNumber(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function CrawlerMonitor() {
  const [data, setData] = useState<CrawlerStatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('all');

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await bidService.bidCrawlerStats();
      setData((res as any)?.data?.data ?? (res as any)?.data ?? null);
    } catch (e: any) {
      setError(e?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000); // auto-refresh every 60s
    return () => clearInterval(timer);
  }, []);

  const filteredSites = useMemo(() => {
    if (!data) return [];
    if (filter === 'all') return data.sites;
    return data.sites.filter((s) => s.status === filter);
  }, [data, filter]);

  const statusCounts = useMemo(() => {
    if (!data) return {};
    const counts: Record<string, number> = {};
    for (const s of data.sites) {
      counts[s.status] = (counts[s.status] || 0) + 1;
    }
    return counts;
  }, [data]);

  /* ---------- Render ---------- */

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-[#A3A3A3]">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 border-2 border-[#3F5B8D] border-t-transparent rounded-full animate-spin" />
          加载中...
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-500 mb-2">加载失败</p>
          <p className="text-sm text-[#A3A3A3] mb-3">{error}</p>
          <button
            onClick={fetchData}
            className="px-3 py-1.5 bg-[#3F5B8D] text-white text-sm rounded-lg"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="flex-1 overflow-y-auto p-5 space-y-5">
      {/* A. Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SummaryCard
          title="总数据量"
          value={formatNumber(data.summary.total_items)}
          icon="database"
          color="#3F5B8D"
        />
        <SummaryCard
          title="24h 新增"
          value={`+${formatNumber(data.summary.items_24h)}`}
          icon="trending-up"
          color="#16A34A"
        />
        <SummaryCard
          title="正在爬取"
          value={String(data.summary.crawling_now)}
          icon="loader"
          color="#2563EB"
          pulse={data.summary.crawling_now > 0}
        />
        <SummaryCard
          title="滞后站点"
          value={String(data.summary.stale_sites)}
          icon="alert-circle"
          color={data.summary.stale_sites > 0 ? '#D97706' : '#16A34A'}
        />
      </div>

      {/* B. Detector Heartbeat */}
      <div className="rounded-xl border border-[#E5E7EB] p-4 bg-white">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <DynamicIcon
              name="radio"
              className="w-4 h-4 text-[#3F5B8D]"
              strokeWidth={1.5}
            />
            <span className="text-sm font-semibold text-[#2E365A]">
              探测器心跳
            </span>
          </div>
          {data.detector.last_run && (
            <span className="text-xs text-[#A3A3A3]">
              上次运行: {data.detector.last_run}
            </span>
          )}
        </div>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <div className="text-xl font-bold text-[#2E365A]">
              {data.detector.total_sites}
            </div>
            <div className="text-xs text-[#A3A3A3]">总站点</div>
          </div>
          <div>
            <div className="text-xl font-bold text-[#3F5B8D]">
              {data.detector.enabled_sites}
            </div>
            <div className="text-xs text-[#A3A3A3]">已启用</div>
          </div>
          <div>
            <div className="text-xl font-bold text-emerald-600">
              {data.summary.items_7d > 0
                ? '运行中'
                : data.detector.last_run
                  ? '已运行'
                  : '未启动'}
            </div>
            <div className="text-xs text-[#A3A3A3]">状态</div>
          </div>
        </div>
        {data.summary.latest_publish && (
          <div className="mt-3 pt-3 border-t border-[#F0F0F0] text-xs text-[#A3A3A3]">
            数据最新发布时间:{' '}
            <span className="text-[#2E365A] font-medium">
              {data.summary.latest_publish}
            </span>
            <span className="ml-4">
              7天新增:{' '}
              <span className="text-[#2E365A] font-medium">
                {formatNumber(data.summary.items_7d)}
              </span>
            </span>
          </div>
        )}
      </div>

      {/* C. Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <FilterChip
          label="全部"
          count={data.sites.length}
          active={filter === 'all'}
          onClick={() => setFilter('all')}
        />
        <FilterChip
          label="正常"
          count={statusCounts['ok'] || 0}
          active={filter === 'ok'}
          onClick={() => setFilter('ok')}
        />
        <FilterChip
          label="爬取中"
          count={statusCounts['crawling'] || 0}
          active={filter === 'crawling'}
          onClick={() => setFilter('crawling')}
        />
        <FilterChip
          label="滞后"
          count={statusCounts['stale'] || 0}
          active={filter === 'stale'}
          onClick={() => setFilter('stale')}
        />
        <FilterChip
          label="未探测"
          count={statusCounts['never'] || 0}
          active={filter === 'never'}
          onClick={() => setFilter('never')}
        />
        <div className="flex-1" />
        <button
          onClick={fetchData}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#3F5B8D] border border-[#3F5B8D]/30 rounded-lg hover:bg-[#3F5B8D]/5 transition"
        >
          <DynamicIcon
            name="refresh-cw"
            className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`}
            strokeWidth={1.5}
          />
          刷新
        </button>
      </div>

      {/* D. Site Matrix Table */}
      <div className="rounded-xl border border-[#E5E7EB] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#F8FAFC] text-[#A3A3A3] text-xs uppercase tracking-wider">
                <th className="text-left px-3 py-2.5 font-medium">站点</th>
                <th className="text-center px-3 py-2.5 font-medium w-16">
                  间隔
                </th>
                <th className="text-center px-3 py-2.5 font-medium w-20">
                  数据量
                </th>
                <th className="text-center px-3 py-2.5 font-medium w-20">
                  24h新增
                </th>
                <th className="text-center px-3 py-2.5 font-medium w-28">
                  最新发布
                </th>
                <th className="text-center px-3 py-2.5 font-medium w-24">
                  上次探测
                </th>
                <th className="text-center px-3 py-2.5 font-medium w-20">
                  状态
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#F0F0F0]">
              {filteredSites.map((site) => {
                const sc = STATUS_CONFIG[site.status] || STATUS_CONFIG.never;
                return (
                  <tr
                    key={site.site_id}
                    className="hover:bg-[#FAFAFA] transition-colors"
                  >
                    <td className="px-3 py-2.5">
                      <div
                        className="font-medium text-[#2E365A] truncate max-w-[280px]"
                        title={site.name}
                      >
                        {site.name}
                      </div>
                      <div className="text-xs text-[#A3A3A3] font-mono">
                        {site.site_id}
                      </div>
                    </td>
                    <td className="text-center px-3 py-2.5 text-xs text-[#6B7280]">
                      {formatInterval(site.detect_interval)}
                    </td>
                    <td className="text-center px-3 py-2.5 text-xs">
                      {site.total_items > 0 ? (
                        <span className="font-semibold text-[#2E365A]">
                          {formatNumber(site.total_items)}
                        </span>
                      ) : (
                        <span className="text-neutral-400">-</span>
                      )}
                    </td>
                    <td className="text-center px-3 py-2.5 text-xs">
                      {site.items_24h > 0 ? (
                        <span className="font-medium text-emerald-600">
                          +{formatNumber(site.items_24h)}
                        </span>
                      ) : (
                        <span className="text-neutral-400">-</span>
                      )}
                    </td>
                    <td className="text-center px-3 py-2.5 text-xs text-[#6B7280]">
                      {site.latest_publish || (
                        <span className="text-neutral-400">-</span>
                      )}
                    </td>
                    <td className="text-center px-3 py-2.5 text-xs text-[#6B7280]">
                      {site.last_check_ago != null ? (
                        <span
                          title={new Date(
                            (site.last_check ?? 0) * 1000,
                          ).toLocaleString()}
                        >
                          {formatAgo(site.last_check_ago)}
                        </span>
                      ) : (
                        <span className="text-neutral-400">-</span>
                      )}
                    </td>
                    <td className="text-center px-3 py-2.5">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${sc.color} ${sc.bg}`}
                      >
                        {site.status === 'crawling' && (
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                        )}
                        {sc.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {filteredSites.length === 0 && (
          <div className="py-12 text-center text-sm text-[#A3A3A3]">
            无匹配站点
          </div>
        )}
      </div>

      {/* E. Recent Runs */}
      {data.recent_runs.length > 0 && (
        <div className="rounded-xl border border-[#E5E7EB] p-4 bg-white">
          <div className="flex items-center gap-2 mb-3">
            <DynamicIcon
              name="history"
              className="w-4 h-4 text-[#3F5B8D]"
              strokeWidth={1.5}
            />
            <span className="text-sm font-semibold text-[#2E365A]">
              最近运行记录
            </span>
          </div>
          <div className="space-y-1.5">
            {data.recent_runs.map((run, idx) => (
              <div key={idx} className="flex items-center gap-3 text-xs">
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    run.type === 'detector'
                      ? 'bg-[#3F5B8D]/10 text-[#3F5B8D]'
                      : 'bg-[#6B597F]/10 text-[#6B597F]'
                  }`}
                >
                  {run.type === 'detector' ? '探测' : '全量'}
                </span>
                <span className="text-[#6B7280]">
                  {run.start_time
                    ? new Date(run.start_time).toLocaleString()
                    : '-'}
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded ${
                    run.status === 'success'
                      ? 'bg-emerald-50 text-emerald-700'
                      : run.status === 'running'
                        ? 'bg-blue-50 text-blue-700'
                        : 'bg-red-50 text-red-700'
                  }`}
                >
                  {run.status === 'success'
                    ? '成功'
                    : run.status === 'running'
                      ? '运行中'
                      : '失败'}
                </span>
                {run.duration > 0 && (
                  <span className="text-[#A3A3A3]">{run.duration}s</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function SummaryCard({
  title,
  value,
  icon,
  color,
  pulse,
}: {
  title: string;
  value: string;
  icon: string;
  color: string;
  pulse?: boolean;
}) {
  return (
    <div className="rounded-xl border border-[#E5E7EB] p-4 bg-white flex items-start gap-3">
      <div
        className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
        style={{ backgroundColor: `${color}12` }}
      >
        <DynamicIcon
          name={icon}
          className="w-4 h-4"
          style={{ color }}
          strokeWidth={1.5}
        />
      </div>
      <div>
        <div className="text-xl font-bold text-[#2E365A] leading-tight">
          {value}
          {pulse && (
            <span
              className="inline-block w-1.5 h-1.5 rounded-full ml-1.5 animate-pulse"
              style={{ backgroundColor: color }}
            />
          )}
        </div>
        <div className="text-xs text-[#A3A3A3] mt-0.5">{title}</div>
      </div>
    </div>
  );
}

function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition ${
        active
          ? 'bg-[#3F5B8D] text-white'
          : 'bg-[#F5F5F5] text-[#6B7280] hover:bg-[#EAEAEA]'
      }`}
    >
      {label}
      {count > 0 && (
        <span
          className={`text-[10px] ${active ? 'bg-white/20' : 'bg-[#E0E0E0]'} px-1.5 py-0.5 rounded-full`}
        >
          {count}
        </span>
      )}
    </button>
  );
}
