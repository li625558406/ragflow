import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
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
  CollectionCategoryStat,
  CollectionResult,
  fetchCollectionStats,
  listCollectionResults,
} from '@/services/collection-service';
import { listCrawl4aiSites } from '@/services/crawl4ai-service';
import { useQuery } from '@tanstack/react-query';
import { useDebounce } from 'ahooks';
import { RotateCcw, Search } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CATEGORY_COLORS } from './field-labels';
import { ResultDetailDialog } from './result-detail-dialog';

const ALL = '__all__';

const STATUS_COLORS: Record<string, string> = {
  raw: 'bg-yellow-500/15 text-yellow-600',
  kb_uploaded: 'bg-green-500/15 text-green-600',
  failed: 'bg-red-500/15 text-red-600',
};

export function ResultsTab() {
  const { t } = useTranslation();

  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState(ALL);
  const [siteId, setSiteId] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [detailId, setDetailId] = useState<string | null>(null);

  const debouncedKeyword = useDebounce(keyword, { wait: 500 });

  const { data: sites = [] } = useQuery<string[]>({
    queryKey: ['crawl4aiSites'],
    queryFn: async () => {
      const { data: res } = await listCrawl4aiSites();
      return res?.code === 0 ? (res.data ?? []) : [];
    },
  });

  // 分类统计（用于类型下拉 + Badge 显示）
  const { data: stats = [] } = useQuery<CollectionCategoryStat[]>({
    queryKey: ['collectionStats'],
    queryFn: async () => {
      const { data: res } = await fetchCollectionStats();
      return res?.code === 0 ? (res.data?.list ?? []) : [];
    },
  });

  const { data, isFetching } = useQuery({
    queryKey: [
      'collectionResults',
      {
        debouncedKeyword,
        category,
        siteId,
        status,
        startDate,
        endDate,
        page,
        pageSize,
      },
    ],
    initialData: { list: [], total: 0 },
    queryFn: async () => {
      const { data: res } = await listCollectionResults({
        page,
        page_size: pageSize,
        keyword: debouncedKeyword || undefined,
        category: category === ALL ? undefined : category,
        site_id: siteId === ALL ? undefined : siteId,
        status: status === ALL ? undefined : status,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
      });
      return res?.code === 0 ? res.data : { list: [], total: 0 };
    },
  });

  const results: CollectionResult[] = data?.list ?? [];
  const total = data?.total ?? 0;

  const resetFilters = useCallback(() => {
    setKeyword('');
    setCategory(ALL);
    setSiteId(ALL);
    setStatus(ALL);
    setStartDate('');
    setEndDate('');
    setPage(1);
  }, []);

  const formatTs = (ts?: number) => (ts ? new Date(ts).toLocaleString() : '-');

  return (
    <div className="size-full flex flex-col gap-4">
      {/* 过滤器 */}
      <section className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            className="pl-8 w-56"
            placeholder={t('crawl4ai.searchPlaceholder')}
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Select
          value={category}
          onValueChange={(v) => {
            setCategory(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder={t('crawl4ai.category')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('crawl4ai.allCategories')}</SelectItem>
            {stats.map((s) => (
              <SelectItem
                key={s.category || 'empty'}
                value={s.category || 'empty'}
              >
                {s.category_label || s.category || '-'} ({s.count})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={siteId}
          onValueChange={(v) => {
            setSiteId(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder={t('crawl4ai.site')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('crawl4ai.allSites')}</SelectItem>
            {sites.map((s: string) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-36">
            <SelectValue placeholder={t('crawl4ai.status')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('crawl4ai.allStatus')}</SelectItem>
            <SelectItem value="raw">{t('crawl4ai.statusRaw')}</SelectItem>
            <SelectItem value="kb_uploaded">
              {t('crawl4ai.statusKbUploaded')}
            </SelectItem>
            <SelectItem value="failed">{t('crawl4ai.statusFailed')}</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="date"
          className="w-36"
          value={startDate}
          onChange={(e) => {
            setStartDate(e.target.value);
            setPage(1);
          }}
        />
        <span className="text-muted-foreground text-sm">-</span>
        <Input
          type="date"
          className="w-36"
          value={endDate}
          onChange={(e) => {
            setEndDate(e.target.value);
            setPage(1);
          }}
        />
        <Button variant="ghost" size="icon" onClick={resetFilters}>
          <RotateCcw className="size-4" />
        </Button>
      </section>

      {/* 结果表格 */}
      <section className="flex-1 overflow-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-[100px]">
                {t('crawl4ai.category')}
              </TableHead>
              <TableHead className="min-w-[300px]">
                {t('crawl4ai.resultTitle')}
              </TableHead>
              <TableHead>{t('crawl4ai.site')}</TableHead>
              <TableHead>{t('crawl4ai.publishDate')}</TableHead>
              <TableHead>{t('crawl4ai.status')}</TableHead>
              <TableHead>{t('crawl4ai.crawledAt')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {results.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-center text-muted-foreground py-10"
                >
                  {isFetching ? t('crawl4ai.loading') : t('crawl4ai.noResults')}
                </TableCell>
              </TableRow>
            )}
            {results.map((r) => (
              <TableRow
                key={r.id}
                className="cursor-pointer"
                onClick={() => setDetailId(r.id)}
              >
                <TableCell>
                  {r.category_label && (
                    <Badge
                      variant="secondary"
                      className={CATEGORY_COLORS[r.category] ?? ''}
                    >
                      {r.category_label}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="font-medium max-w-[420px] truncate">
                  {r.title}
                </TableCell>
                <TableCell className="max-w-[200px] truncate text-muted-foreground">
                  {r.site_display || r.site_id}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {r.publish_date || '-'}
                </TableCell>
                <TableCell>
                  <Badge
                    variant="secondary"
                    className={STATUS_COLORS[r.status] ?? ''}
                  >
                    {t(`crawl4ai.status_${r.status}`, r.status)}
                  </Badge>
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {formatTs(r.crawled_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <footer className="pb-5">
        <RAGFlowPagination
          total={total}
          current={page}
          pageSize={pageSize}
          onChange={(p: number, ps?: number) => {
            setPage(p);
            if (ps) setPageSize(ps);
          }}
        />
      </footer>

      {detailId && (
        <ResultDetailDialog
          resultId={detailId}
          hideModal={() => setDetailId(null)}
        />
      )}
    </div>
  );
}
