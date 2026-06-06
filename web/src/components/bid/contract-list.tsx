import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import { getAuthorization } from '@/utils/authorization-util';
import { FileText, Search, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

interface ContractItem {
  id: number;
  title: string;
  publishTime: string;
  projectMoney: string;
  hasFile: number;
  projectCycle: string[];
  partAInfo: { name: string; contactPhone: string[] }[];
  partBInfo: { name: string; contactPhone: string[] }[];
  contractStartDate: string;
  contractEndDate: string;
}

function fmtDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function contractFetch(url: string, params?: Record<string, any>) {
  const qs = params
    ? '?' +
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&')
    : '';
  const resp = await fetch(`/api/v1/bid/${url}${qs}`, {
    headers: { Authorization: getAuthorization() },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = await resp.json();
  if (json.code !== 0)
    throw new Error(json.message || `API error code=${json.code}`);
  return json;
}

const PAGE_SIZE = 20;

export default function ContractList() {
  const [items, setItems] = useState<ContractItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef(1);
  const [keyword, setKeyword] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>();
  const [partAName, setPartAName] = useState('');
  const [partBName, setPartBName] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const doFetch = useCallback(
    async (page: number) => {
      setLoading(true);
      setError(null);
      try {
        const json = await contractFetch('contracts', {
          page,
          items_per_page: PAGE_SIZE,
          keyword: keyword || undefined,
          start_date: dateRange?.from ? fmtDate(dateRange.from) : undefined,
          end_date: dateRange?.to ? fmtDate(dateRange.to) : undefined,
          part_a_name: partAName || undefined,
          part_b_name: partBName || undefined,
        });
        const data = json.data;
        if (page === 1) {
          setItems(data.contracts || []);
        } else {
          setItems((prev) => [...prev, ...(data.contracts || [])]);
        }
        setTotal(data.total || 0);
        setHasSearched(true);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [keyword, dateRange, partAName, partBName],
  );

  const handleSearch = useCallback(() => {
    if (loading) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      pageRef.current = 1;
      setItems([]);
      doFetch(1);
    }, 300);
  }, [loading, doFetch]);

  const loadMore = () => {
    pageRef.current += 1;
    doFetch(pageRef.current);
  };

  const handleBackToSearch = () => setHasSearched(false);

  // ================================================================
  // STATE 1: Search Hero (centered card)
  // ================================================================
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
                中标/合同
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                搜索中标结果和合同公告
              </p>
            </div>

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              <div className="grid grid-cols-2 gap-3 mb-4">
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    关键词
                  </label>
                  <div className="relative">
                    <Input
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      placeholder="输入关键词搜索..."
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

                <div className="col-span-2">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    发布日期
                  </label>
                  <DatePickerWithRange
                    selected={dateRange}
                    onSelect={setDateRange}
                    className="w-full"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    甲方名称
                  </label>
                  <Input
                    value={partAName}
                    onChange={(e) => setPartAName(e.target.value)}
                    placeholder="选填"
                    className={`${INPUT_CLASS} w-full`}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    乙方/中标方
                  </label>
                  <Input
                    value={partBName}
                    onChange={(e) => setPartBName(e.target.value)}
                    placeholder="选填"
                    className={`${INPUT_CLASS} w-full`}
                  />
                </div>
              </div>

              {/* Search button */}
              <Button
                onClick={handleSearch}
                disabled={loading}
                className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] mt-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <svg
                      className="size-4 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    搜索中...
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    搜索合同
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ================================================================
  // STATE 2: Search Results
  // ================================================================
  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white overflow-hidden">
      {/* Compact search bar */}
      <div className="shrink-0 px-6 py-3 border-b border-[#F0F0F0]">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-[#000000] truncate">
            {keyword || '合同搜索'}
          </span>
          <span className="text-xs text-[#A3A3A3] shrink-0">
            共 <b className="text-[#000000]">{total}</b> 条结果
          </span>
          <button
            onClick={handleBackToSearch}
            className="inline-flex items-center gap-1 h-8 px-3 text-xs font-medium text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-colors shrink-0"
          >
            修改条件
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="shrink-0 px-6 pt-3">
          <div className="bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
            <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">!</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[#FF4D4F]">查询失败</p>
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

      {/* Results */}
      <div className="flex-1 min-h-0 px-6 py-4 overflow-auto">
        {loading && items.length === 0 ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl border border-[#E8E8E8] p-5 animate-pulse"
              >
                <div className="h-4 w-3/4 bg-[#F0F0F0] rounded mb-3" />
                <div className="h-4 w-1/2 bg-[#F0F0F0] rounded" />
              </div>
            ))}
          </div>
        ) : items.length > 0 ? (
          <div className="space-y-3">
            {items.map((item, idx) => (
              <div
                key={`${item.id}-${idx}`}
                className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5 cursor-pointer hover:border-[#A3A3A3] transition"
              >
                <div
                  className="text-sm font-semibold text-[#000000] leading-snug line-clamp-2 mb-2"
                  dangerouslySetInnerHTML={{ __html: item.title }}
                />
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#525252]">
                  {item.projectMoney && <span>{item.projectMoney}</span>}
                  {item.publishTime && <span>{item.publishTime}</span>}
                  {item.partAInfo?.[0]?.name && (
                    <span>甲方: {item.partAInfo[0].name}</span>
                  )}
                  {item.partBInfo?.[0]?.name && (
                    <span>乙方: {item.partBInfo[0].name}</span>
                  )}
                  {item.contractStartDate && (
                    <span>
                      合同期: {item.contractStartDate} ~{' '}
                      {item.contractEndDate || '未知'}
                    </span>
                  )}
                </div>
              </div>
            ))}

            {items.length < total && (
              <div className="py-4 flex items-center justify-center">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={loadMore}
                  disabled={loading}
                  className="h-9 text-xs"
                >
                  {loading
                    ? '加载中...'
                    : `加载更多 (已显示 ${items.length}/${total})`}
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="size-16 rounded-2xl bg-[#F5F5F5] flex items-center justify-center mb-4">
              <X className="size-7 text-[#A3A3A3]" />
            </div>
            <p className="text-sm font-medium text-[#525252] mb-1">
              未找到匹配结果
            </p>
            <p className="text-xs text-[#A3A3A3] mb-4">请调整查询条件后重试</p>
            <Button
              onClick={handleBackToSearch}
              className="h-9 px-4 bg-[#000000] hover:bg-[#171717] text-white text-sm rounded-lg"
            >
              修改条件
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
