import { BidSelect } from '@/components/bid-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getAuthorization } from '@/utils/authorization-util';
import { Search, ShieldAlert, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';
const SELECT_CLASS =
  'h-9 text-xs text-[#000000] bg-white border border-[#D4D4D4] hover:border-[#A3A3A3] focus:border-[#000000] rounded-lg transition-all';

interface ShixinResult {
  index: number;
  name: string;
  reg_date: string;
  case_code: string;
}

const PROVINCE_OPTIONS = [
  { label: '全部省份', value: '0' },
  { label: '北京', value: '11' },
  { label: '天津', value: '12' },
  { label: '河北', value: '13' },
  { label: '山西', value: '14' },
  { label: '内蒙古', value: '15' },
  { label: '辽宁', value: '21' },
  { label: '吉林', value: '22' },
  { label: '黑龙江', value: '23' },
  { label: '上海', value: '31' },
  { label: '江苏', value: '32' },
  { label: '浙江', value: '33' },
  { label: '安徽', value: '34' },
  { label: '福建', value: '35' },
  { label: '江西', value: '36' },
  { label: '山东', value: '37' },
  { label: '河南', value: '41' },
  { label: '湖北', value: '42' },
  { label: '湖南', value: '43' },
  { label: '广东', value: '44' },
  { label: '广西', value: '45' },
  { label: '海南', value: '46' },
  { label: '重庆', value: '50' },
  { label: '四川', value: '51' },
  { label: '贵州', value: '52' },
  { label: '云南', value: '53' },
  { label: '西藏', value: '54' },
  { label: '陕西', value: '61' },
  { label: '甘肃', value: '62' },
  { label: '青海', value: '63' },
  { label: '宁夏', value: '64' },
  { label: '新疆', value: '65' },
];

export default function ShixinSearch() {
  // --- Form state ---
  const [name, setName] = useState('');
  const [cardNum, setCardNum] = useState('');
  const [province, setProvince] = useState('0');

  // --- State machine ---
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- Results ---
  const [results, setResults] = useState<ShixinResult[]>([]);
  const [total, setTotal] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const provinceLabel =
    PROVINCE_OPTIONS.find((p) => p.value === province)?.label || '全国';

  const handleSearch = useCallback(() => {
    if (loading) return;
    if (!name.trim() && !cardNum.trim()) return;

    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      setResults([]);
      setTotal(0);

      try {
        const params = new URLSearchParams();
        if (name.trim()) params.set('name', name.trim());
        if (cardNum.trim()) params.set('card_num', cardNum.trim());
        params.set('province', province);

        const resp = await fetch(
          `/api/v1/court/shixin/search?${params.toString()}`,
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
  }, [name, cardNum, province, loading]);

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
                <ShieldAlert className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                失信查询
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                查询全国法院失信被执行人名单信息
              </p>
            </div>

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              {/* 姓名/名称 */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  被执行人姓名/名称
                </label>
                <div className="relative">
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="输入企业名称或个人姓名..."
                    className={`${INPUT_CLASS} w-full`}
                  />
                  {name && (
                    <button
                      onClick={() => setName('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#000000] transition-colors"
                    >
                      <X className="size-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* 证件号 + 省份 */}
              <div className="grid grid-cols-2 gap-3 mb-4">
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    身份证号/统一社会信用代码
                  </label>
                  <Input
                    value={cardNum}
                    onChange={(e) => setCardNum(e.target.value)}
                    placeholder="选填"
                    className={`${INPUT_CLASS} w-full`}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    省份范围
                  </label>
                  <BidSelect
                    value={province}
                    onChange={(val) => setProvince(val)}
                    options={PROVINCE_OPTIONS}
                    allowClear={false}
                    className={`${SELECT_CLASS} w-full`}
                  />
                </div>
              </div>

              {/* Search button */}
              <Button
                onClick={handleSearch}
                disabled={loading || (!name.trim() && !cardNum.trim())}
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
                    查询失信记录
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
              {name || cardNum || '失信查询'}
            </span>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-md bg-[#F5F5F5] text-xs font-medium text-[#525252] shrink-0">
              {provinceLabel}
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
                  <span>立案日期: {r.reg_date}</span>
                  <span className="text-[#D4D4D4]">|</span>
                  <span className="truncate">案号: {r.case_code}</span>
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
              未找到失信记录
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
