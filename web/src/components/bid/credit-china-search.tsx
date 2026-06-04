import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getAuthorization } from '@/utils/authorization-util';
import { Scale, Search, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

interface CreditChinaResult {
  index: number;
  name: string;
  type: string;
  date: string;
}

export default function CreditChinaSearch() {
  // --- Form state ---
  const [keyword, setKeyword] = useState('');

  // --- State machine ---
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- Results ---
  const [results, setResults] = useState<CreditChinaResult[]>([]);
  const [total, setTotal] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const handleSearch = useCallback(() => {
    if (loading) return;
    if (!keyword.trim()) return;

    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      setResults([]);
      setTotal(0);

      try {
        const params = new URLSearchParams();
        params.set('keyword', keyword.trim());
        params.set('type', 'shixinheimingdan');
        params.set('page', '1');
        params.set('pageSize', '10');

        const resp = await fetch(
          `/api/v1/credit-china/search?${params.toString()}`,
          {
            headers: { Authorization: getAuthorization() },
          },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const json = await resp.json();
        if (json.code !== 0)
          throw new Error(json.message || `API error ${json.code}`);

        const data = json.data;
        setResults(data?.items || []);
        setTotal(data?.total || 0);
      } catch (e) {
        setError(e instanceof Error ? e.message : '查询失败');
      } finally {
        setLoading(false);
        setHasSearched(true);
      }
    }, 300);
  }, [keyword, loading]);

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
                <Scale className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                信用中国
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                查询全国严重失信主体名单信息
              </p>
            </div>

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              {/* 主体名称/统一社会信用代码 */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  主体名称/统一社会信用代码
                </label>
                <div className="relative">
                  <Input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="输入企业名称或统一社会信用代码..."
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

              {/* Search button */}
              <Button
                onClick={handleSearch}
                disabled={loading || !keyword.trim()}
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
                    查询中...
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    查询失信主体
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
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span className="text-sm font-semibold text-[#000000] truncate">
              {keyword || '信用中国'}
            </span>
          </div>

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
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl border border-[#E8E8E8] p-5 animate-pulse"
              >
                <div className="flex items-center gap-3 mb-3">
                  <div className="h-5 w-12 bg-[#F0F0F0] rounded-md" />
                  <div className="h-4 w-32 bg-[#F0F0F0] rounded" />
                </div>
                <div className="h-4 w-3/4 bg-[#F0F0F0] rounded mb-3" />
                <div className="h-4 w-1/2 bg-[#F0F0F0] rounded" />
              </div>
            ))}
          </div>
        ) : results.length > 0 ? (
          <div className="space-y-3">
            {results.map((r) => (
              <div
                key={r.index}
                className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5"
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="inline-flex items-center justify-center size-6 rounded-md bg-[#EFEFEF] text-xs font-medium text-[#525252] shrink-0">
                    {r.index}
                  </span>
                  <span className="text-sm font-semibold text-[#000000] truncate">
                    {r.name}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-[#525252]">
                  <span>类型: {r.type}</span>
                  <span className="text-[#D4D4D4]">|</span>
                  <span className="truncate">日期: {r.date}</span>
                </div>
              </div>
            ))}

            <div className="py-4 flex items-center justify-center">
              <span className="text-xs text-[#A3A3A3]">
                已显示全部 {results.length} 条结果
              </span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="size-16 rounded-2xl bg-[#F5F5F5] flex items-center justify-center mb-4">
              <X className="size-7 text-[#A3A3A3]" />
            </div>
            <p className="text-sm font-medium text-[#525252] mb-1">
              未找到失信主体记录
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
