import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { getAuthorization } from '@/utils/authorization-util';
import {
  Building2,
  Calendar,
  FileText,
  Loader2,
  MapPin,
  Search,
  Tag,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

const SELECT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] rounded-lg transition-all';

/* ------------------------------------------------------------------ */
/* Types                                                              */
/* ------------------------------------------------------------------ */

interface TenderItem {
  id: string;
  title: string;
  project_name: string;
  project_number: string;
  publish_time: string;
  announcement_type: string;
  announcement_type_code: number;
  bidding_stage: string;
  bidding_stage_code: number;
  procurement_method: string;
  procurement_method_code: number;
  industry_type: string;
  target_item_type: string;
  project_region_province: string;
  project_region_province_code: string;
  project_region_city: string;
  project_region_city_code: string;
  content_url: string;
  project_budget_amount: string;
  project_budget_amount_unit: string;
  total_amount: string;
  total_amount_unit: string;
  bid_document_start_time: string;
  bid_document_end_time: string;
  bidding_start_time: string;
  bidding_end_time: string;
  opening_bid_time: string;
  contract_num: string;
  purchase_agency: any[];
  win_candidate: any[];
  contacts_purchase_agency: any;
  contacts_win_candidate: any;
}

interface TenderSearchResponse {
  items: TenderItem[];
  total: number;
  from_cache: boolean;
  stale?: boolean;
  api_task_no?: string;
}

/* ------------------------------------------------------------------ */
/* Constants                                                          */
/* ------------------------------------------------------------------ */

const ANNOUNCEMENT_TYPES = [
  { value: '', labelKey: 'tenderAllTypes' },
  { value: '1', labelKey: 'tenderBidAnnouncement' },
  { value: '2', labelKey: 'tenderBidResult' },
];

const SEARCH_MODES = [
  { value: '2', labelKey: 'tenderFuzzy' },
  { value: '1', labelKey: 'tenderPrecise' },
];

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

function fmtDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function tenderFetch(
  body: Record<string, any>,
): Promise<TenderSearchResponse> {
  const resp = await fetch('/api/v1/bid/tender-search', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: getAuthorization(),
    },
    body: JSON.stringify(body),
  });
  if (resp.status === 429) {
    throw new Error('请求过于频繁，请稍后重试');
  }
  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`);
  }
  const json = await resp.json();
  if (json.code !== 0) {
    throw new Error(json.message || `API error code=${json.code}`);
  }
  return json.data;
}

function announcementBadge(type: string, t: (k: string) => string) {
  if (type === '招标公告') return t('tenderBadgeBid');
  if (type === '招标结果') return t('tenderBadgeWin');
  return type || t('tenderBadgeDefault');
}

/* ------------------------------------------------------------------ */
/* Component                                                          */
/* ------------------------------------------------------------------ */

export default function TenderSearch() {
  const { t } = useTranslation();

  /* ---------- Data ---------- */
  const [items, setItems] = useState<TenderItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const pageRef = useRef(1);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const listContainerRef = useRef<HTMLDivElement | null>(null);

  /* ---------- Search form ---------- */
  const [keyword, setKeyword] = useState('');
  const [searchMode, setSearchMode] = useState(2);
  const [announcementType, setAnnouncementType] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>({});
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  /* ---------- Data fetching ---------- */
  const doFetch = useCallback(
    async (page: number) => {
      if (page === 1) {
        setLoading(true);
      } else {
        setLoadingMore(true);
      }
      setError(null);
      setStale(false);
      try {
        const data = await tenderFetch({
          keyword: keyword.trim(),
          searchMode,
          page,
          pageSize: 10,
          announcementType: announcementType || undefined,
          publishStartTime: dateRange?.from
            ? fmtDate(dateRange.from)
            : undefined,
          publishEndTime: dateRange?.to ? fmtDate(dateRange.to) : undefined,
        });
        if (page === 1) {
          setItems(data.items || []);
        } else {
          setItems((prev) => [...prev, ...(data.items || [])]);
        }
        setTotal(data.total || 0);
        if (data.stale) setStale(true);
        setHasSearched(true);
      } catch (e: any) {
        if (page === 1) {
          setItems([]);
          setTotal(0);
        }
        setError(e.message);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [keyword, searchMode, announcementType, dateRange],
  );

  /* ---------- Search ---------- */
  const handleSearch = useCallback(() => {
    if (loading || keyword.trim().length < 2) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      pageRef.current = 1;
      setItems([]);
      doFetch(1);
    }, 300);
  }, [loading, keyword, doFetch]);

  /* ---------- Infinite scroll ---------- */
  const loadMore = useCallback(() => {
    if (loadingMore || items.length >= total) return;
    pageRef.current += 1;
    doFetch(pageRef.current);
  }, [loadingMore, items.length, total, doFetch]);

  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  useEffect(() => {
    if (!hasSearched) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          loadMoreRef.current();
        }
      },
      { threshold: 0.1 },
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasSearched]);

  const handleBack = () => {
    setHasSearched(false);
    setItems([]);
    setTotal(0);
    setError(null);
    setStale(false);
  };

  /* ================================================================ */
  /* STATE 1: Search Form                                             */
  /* ================================================================ */
  if (!hasSearched) {
    return (
      <div className="flex-1 overflow-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-12">
          <div className="w-full max-w-2xl">
            {/* Header */}
            <div className="text-center mb-8">
              <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
                <FileText className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                {t('tenderSearchTitle')}
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                {t('tenderSearchDescription')}
              </p>
            </div>

            {/* Error banner */}
            {error && (
              <div className="mb-4 bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
                <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">
                  !
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[#FF4D4F]">
                    {t('tenderQueryFailed')}
                  </p>
                  <p className="text-xs text-[#8C8C8C] mt-0.5 break-all">
                    {error}
                  </p>
                </div>
                <button
                  onClick={() => setError(null)}
                  className="shrink-0 text-[#A3A3A3] hover:text-[#000000] transition-colors"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )}

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              <div className="grid grid-cols-2 gap-3 mb-4">
                {/* Keyword (full width, required) */}
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    {t('tenderKeyword')}{' '}
                    <span className="text-[#FF4D4F]">*</span>
                  </label>
                  <div className="relative">
                    <Input
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      placeholder={t('tenderKeywordPlaceholder')}
                      className={`${INPUT_CLASS} w-full`}
                    />
                    {keyword && (
                      <button
                        onClick={() => setKeyword('')}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#000000] transition-colors"
                      >
                        <X className="size-3.5" />
                      </button>
                    )}
                  </div>
                </div>

                {/* Search Mode */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    {t('tenderSearchMode')}
                  </label>
                  <Select
                    value={String(searchMode)}
                    onValueChange={(v) => setSearchMode(Number(v))}
                  >
                    <SelectTrigger className={SELECT_CLASS}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SEARCH_MODES.map((m) => (
                        <SelectItem key={m.value} value={m.value}>
                          {t(m.labelKey)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Announcement Type */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    {t('tenderAnnouncementType')}
                  </label>
                  <Select
                    value={announcementType}
                    onValueChange={setAnnouncementType}
                  >
                    <SelectTrigger className={SELECT_CLASS}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ANNOUNCEMENT_TYPES.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {t(item.labelKey)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Date Range (full width) */}
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    {t('tenderPublishDate')}
                  </label>
                  <DatePickerWithRange
                    selected={dateRange}
                    onSelect={setDateRange}
                    className="w-full"
                  />
                </div>
              </div>

              {/* Search button */}
              <Button
                onClick={handleSearch}
                disabled={loading || keyword.trim().length < 2}
                className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] mt-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" strokeWidth={4} />
                    {t('tenderSearching')}
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    {t('tenderSearchBtn')}
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ================================================================ */
  /* STATE 2: Results                                                 */
  /* ================================================================ */
  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Compact top bar */}
      <div className="shrink-0 px-6 py-3 flex items-center gap-3 border-b border-[#F0F0F0]">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-sm font-semibold text-[#000000] truncate">
            {keyword}
          </span>
          <Button
            onClick={handleBack}
            className="h-8 px-3 bg-[#000000] hover:bg-[#171717] text-white text-xs font-medium rounded-lg transition-colors shrink-0"
          >
            {t('tenderBack')}
          </Button>
        </div>
        <span className="text-xs text-[#A3A3A3] shrink-0">
          {t('tenderTotal')} <b className="text-[#000000]">{total}</b>{' '}
          {t('tenderResults')}
        </span>
      </div>

      {/* Stale cache warning */}
      {stale && (
        <div className="shrink-0 px-6 py-2">
          <div className="bg-[#FFFBE6] border border-[#FFE58F] rounded-lg px-4 py-3 flex items-center gap-3">
            <span className="text-sm text-[#AD6800] shrink-0">
              {t('tenderStaleCache')}
            </span>
          </div>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="shrink-0 px-6 pb-2">
          <div className="bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
            <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">!</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[#FF4D4F]">
                {t('tenderQueryFailed')}
              </p>
              <p className="text-xs text-[#8C8C8C] mt-0.5 break-all">{error}</p>
            </div>
            <button
              onClick={() => setError(null)}
              className="shrink-0 text-[#A3A3A3] hover:text-[#000000] transition-colors"
            >
              <X className="size-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Results list */}
      <div
        ref={listContainerRef}
        className="flex-1 min-h-0 px-6 pb-4 overflow-auto"
      >
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="bg-white rounded-xl border border-[#E8E8E8] p-5 animate-pulse"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <div className="h-5 w-12 bg-[#F0F0F0] rounded-md" />
                    <div className="h-5 w-16 bg-[#F0F0F0] rounded-md" />
                  </div>
                  <div className="h-4 w-20 bg-[#F0F0F0] rounded" />
                </div>
                <div className="h-5 w-3/4 bg-[#F0F0F0] rounded mb-3" />
                <div className="h-4 w-1/2 bg-[#F0F0F0] rounded mb-3" />
                <div className="h-4 w-1/3 bg-[#F0F0F0] rounded" />
              </div>
            ))}
          </div>
        ) : items.length > 0 ? (
          <>
            <div className="space-y-3">
              {items.map((item, idx) => {
                const displayTitle = item.title || item.project_name || '';
                const pubDate = item.publish_time
                  ? item.publish_time.substring(0, 10)
                  : '';
                const region = [
                  item.project_region_province,
                  item.project_region_city,
                ]
                  .filter(Boolean)
                  .join(' ');
                const winLen = item.win_candidate?.length || 0;
                const agencyLen = item.purchase_agency?.length || 0;

                return (
                  <div
                    key={item.id || idx}
                    className="group bg-white rounded-xl border border-[#E8E8E8] p-5 transition-all duration-200 hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] hover:-translate-y-0.5"
                  >
                    {/* Row 1: Badges + date */}
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-[#F5F5F5] text-[#525252]">
                          {announcementBadge(item.announcement_type, t)}
                        </span>
                        {item.bidding_stage && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs text-[#A3A3A3] bg-[#FAFAFA] border border-[#E8E8E8]">
                            {item.bidding_stage}
                          </span>
                        )}
                        {item.procurement_method && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs text-[#A3A3A3] bg-[#FAFAFA] border border-[#E8E8E8]">
                            {item.procurement_method}
                          </span>
                        )}
                      </div>
                      {pubDate && (
                        <span className="text-xs text-[#A3A3A3] shrink-0">
                          {pubDate}
                        </span>
                      )}
                    </div>

                    {/* Row 2: Title */}
                    {displayTitle && (
                      <h3 className="text-[15px] font-semibold text-[#000000] leading-snug mb-2 line-clamp-2">
                        {displayTitle}
                      </h3>
                    )}

                    {/* Row 3: Project number + amounts */}
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[#525252] mb-3">
                      {item.project_number && (
                        <span className="font-mono text-[#A3A3A3]">
                          {item.project_number}
                        </span>
                      )}
                      {item.project_budget_amount && (
                        <span className="font-medium text-[#000000]">
                          {t('tenderBudget')} {item.project_budget_amount}
                          {item.project_budget_amount_unit || ''}
                        </span>
                      )}
                      {item.total_amount && (
                        <span className="font-medium text-[#000000]">
                          {t('tenderWinBid')} {item.total_amount}
                          {item.total_amount_unit || ''}
                        </span>
                      )}
                    </div>

                    {/* Row 4: Region + industry + contract */}
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[#525252]">
                      {region && (
                        <span className="inline-flex items-center gap-1 text-[#A3A3A3]">
                          <MapPin className="size-3" />
                          {region}
                        </span>
                      )}
                      {item.industry_type && (
                        <span className="inline-flex items-center gap-1">
                          <Tag className="size-3" />
                          {item.industry_type}
                        </span>
                      )}
                      {item.contract_num && (
                        <span className="text-[#A3A3A3]">
                          {t('tenderContractLabel')} {item.contract_num}
                        </span>
                      )}
                      {item.target_item_type && (
                        <span className="text-[#A3A3A3]">
                          {item.target_item_type}
                        </span>
                      )}
                    </div>

                    {/* Row 5: Win candidates */}
                    {winLen > 0 && (
                      <div className="mt-3 pt-3 border-t border-[#F0F0F0] flex flex-wrap items-center gap-2">
                        <span className="text-xs text-[#A3A3A3]">
                          {t('tenderWinCandidate')}
                        </span>
                        {item.win_candidate
                          .slice(0, 3)
                          .map((c: any, i: number) => (
                            <span
                              key={i}
                              className="inline-flex items-center gap-1 text-xs font-medium text-[#000000]"
                            >
                              <Building2 className="size-3 text-[#A3A3A3]" />
                              {c.companyName}
                              {c.amount && (
                                <span className="text-[#A3A3A3]">
                                  ({c.amount}
                                  {c.amountUnit || ''})
                                </span>
                              )}
                            </span>
                          ))}
                        {winLen > 3 && (
                          <span className="text-xs text-[#A3A3A3]">
                            +{winLen - 3}
                          </span>
                        )}
                      </div>
                    )}

                    {/* Row 6: Purchase agency */}
                    {agencyLen > 0 && (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="text-xs text-[#A3A3A3]">
                          {t('tenderAgency')}
                        </span>
                        {item.purchase_agency
                          .slice(0, 2)
                          .map((a: any, i: number) => (
                            <span key={i} className="text-xs text-[#525252]">
                              {a.companyName}
                            </span>
                          ))}
                        {agencyLen > 2 && (
                          <span className="text-xs text-[#A3A3A3]">
                            +{agencyLen - 2}
                          </span>
                        )}
                      </div>
                    )}

                    {/* Row 7: Dates info */}
                    {(item.opening_bid_time || item.bidding_end_time) && (
                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[#A3A3A3]">
                        {item.opening_bid_time && (
                          <span className="inline-flex items-center gap-1">
                            <Calendar className="size-3" />
                            {t('tenderBidOpening')} {item.opening_bid_time}
                          </span>
                        )}
                        {item.bidding_end_time && (
                          <span>
                            {t('tenderBidDeadline')} {item.bidding_end_time}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Infinite scroll sentinel */}
            <div
              ref={sentinelRef}
              className="py-6 flex items-center justify-center"
            >
              {loadingMore ? (
                <span className="text-xs text-[#A3A3A3]">
                  {t('tenderLoadingMore')}
                </span>
              ) : items.length >= total ? (
                <span className="text-xs text-[#A3A3A3]">
                  {t('tenderLoadComplete')}
                </span>
              ) : (
                <span className="text-xs text-[#A3A3A3]">
                  {t('tenderLoaded')} {items.length}/{total}
                </span>
              )}
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-20">
            <div className="size-16 rounded-2xl bg-[#F5F5F5] flex items-center justify-center mb-4">
              <X className="size-7 text-[#A3A3A3]" />
            </div>
            <p className="text-sm font-medium text-[#525252] mb-1">
              {t('tenderNoResults')}
            </p>
            <p className="text-xs text-[#A3A3A3] mb-4">
              {t('tenderNoResultsHint')}
            </p>
            <Button
              onClick={handleBack}
              className="h-9 px-4 bg-[#000000] hover:bg-[#171717] text-white text-sm rounded-lg"
            >
              {t('tenderBack')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
