import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { MultiSelect } from '@/components/ui/multi-select';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getAuthorization } from '@/utils/authorization-util';
import {
  ChevronDown,
  FileText,
  Loader2,
  MapPin,
  Search,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-colors';

const SELECT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] rounded-lg transition-colors appearance-none cursor-pointer pr-8';

/* ------------------------------------------------------------------ */
/* Types                                                              */
/* ------------------------------------------------------------------ */

interface PurchaseAgency {
  companyName: string;
  relateType: string;
  creditNo: string;
}

interface WinCandidate {
  bidSectionNumber: string;
  bidItemName: string;
  companyName: string;
  relateType: string;
  amount: string;
  amountUnit: string;
  creditNo: string;
}

interface ContactInfo {
  phone: string;
  relateType: string;
  contactPerson: string;
  email: string;
  projectManager?: string;
  projectManagerPhone?: string;
}

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
  procurement_method_code: string;
  industry_type: string;
  target_item_type: string;
  project_region_province: string;
  project_region_province_code: string;
  project_region_city: string;
  project_region_city_code: string;
  project_region_district: string;
  project_region_district_code: string;
  content_url: string;
  project_budget_amount: string;
  project_budget_amount_unit: string;
  total_amount: string;
  total_amount_unit: string;
  bid_document_start_time: string;
  bid_document_end_time: string;
  registration_start_time: string;
  registration_end_time: string;
  bidding_start_time: string;
  bidding_end_time: string;
  opening_bid_time: string;
  estimated_purchasing_time: string;
  contract_num: string;
  quotation_validity_start: string;
  quotation_validity_end: string;
  tender_document_price_amount: string;
  tender_document_price_unit: string;
  registration_fee_amount: string;
  registration_fee_unit: string;
  bidding_security_amount: string;
  bidding_security_unit: string;
  ca_payment_amount: string;
  ca_payment_unit: string;
  tender_agent_service_fee_amount: string;
  tender_agent_service_fee_unit: string;
  performance_security_amount: string;
  performance_security_unit: string;
  purchase_agency: PurchaseAgency[];
  win_candidate: WinCandidate[];
  contacts_purchase_agency: ContactInfo[];
  contacts_win_candidate: ContactInfo[];
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

const SEARCH_MODES = [
  { value: '2', label: '模糊搜索' },
  { value: '1', label: '精确搜索' },
];

const ANNOUNCEMENT_TYPES = [
  { value: 'all', label: '全部类型' },
  { value: '1', label: '招标公告' },
  { value: '2', label: '招标结果' },
];

const PROCUREMENT_METHODS = [
  { value: 'all', label: '全部' },
  { value: '1', label: '单一来源' },
  { value: '2', label: '框架协议' },
  { value: '3', label: '邀请招标' },
  { value: '4', label: '询价采购' },
  { value: '5', label: '竞争性谈判' },
  { value: '6', label: '竞争性磋商' },
  { value: '7', label: '竞价' },
  { value: '8', label: '公开招标' },
];

const SEARCH_TYPES = [
  { label: '标题', value: '1' },
  { label: '项目编号', value: '2' },
  { label: '采购单位', value: '3' },
  { label: '代理机构', value: '4' },
  { label: '候选企业', value: '5' },
  { label: '中标企业', value: '6' },
];

const TARGET_ITEM_TYPES = [
  { label: '预告', value: '1' },
  { label: '变更(招标公告)', value: '2' },
  { label: '澄清补遗(招标公告)', value: '3' },
  { label: '招标', value: '4' },
  { label: '变更(招标结果)', value: '5' },
  { label: '候选公示', value: '6' },
  { label: '中标成交', value: '7' },
  { label: '澄清答疑', value: '8' },
  { label: '合同', value: '9' },
  { label: '验收', value: '10' },
  { label: '废标流标终止', value: '11' },
  { label: '开标', value: '12' },
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

function announcementBadgeClass(type: string): string {
  if (type === '招标公告') return 'bg-[#E6F7FF] text-[#1677FF]';
  if (type === '招标结果') return 'bg-[#F6FFED] text-[#52C41A]';
  return 'bg-[#F5F5F5] text-[#525252]';
}

function friendlyError(raw: string): string {
  if (raw.includes('400') || raw.includes('Bad Request')) {
    return '搜索条件不符合接口要求，请尝试：修改关键词（避免过于通用的词如"上海""科技"）、调整筛选条件后重试';
  }
  if (raw.includes('429') || raw.includes('频繁')) {
    return '请求过于频繁，请稍后重试';
  }
  return raw;
}

/* ------------------------------------------------------------------ */
/* Custom Select Popover (no Radix, no popper flicker)                */
/* ------------------------------------------------------------------ */

function SelectPopover({
  value,
  onChange,
  options,
  placeholder = '请选择',
  className = '',
}: {
  value: string;
  onChange: (value: string) => void;
  options: { label: string; value: string }[];
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const selected = options.find((o) => String(o.value) === String(value));

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`${SELECT_CLASS} w-full flex items-center justify-between gap-1`}
      >
        <span
          className={
            selected ? 'text-[#000000] truncate' : 'text-[#A3A3A3] truncate'
          }
        >
          {selected?.label || placeholder}
        </span>
        <ChevronDown
          className={`size-3.5 text-[#A3A3A3] shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-white border border-[#E8E8E8] rounded-lg shadow-[0_8px_32px_rgba(0,0,0,0.10)] max-h-60 overflow-y-auto py-1">
          {options.map((o) => (
            <div
              key={o.value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className={`px-3 py-1.5 text-xs cursor-pointer transition-colors hover:bg-[#F5F5F5] ${
                String(o.value) === String(value)
                  ? 'bg-[#F5F5F5] text-[#000000] font-medium'
                  : 'text-[#525252]'
              }`}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Component                                                          */
/* ------------------------------------------------------------------ */

export default function TenderSearch() {
  /* ---------- Search form state ---------- */
  const [keyword, setKeyword] = useState('');
  const [searchMode, setSearchMode] = useState(2);
  const [announcementType, setAnnouncementType] = useState('all');
  const [procurementMethod, setProcurementMethod] = useState('all');
  const [searchType, setSearchType] = useState<string[]>([]);
  const [targetItemType, setTargetItemType] = useState<string[]>([]);
  const [provinceCode, setProvinceCode] = useState('');
  const [cityCode, setCityCode] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>({});

  /* ---------- Area options ---------- */
  const [provinceOptions, setProvinceOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [cityOptions, setCityOptions] = useState<
    { label: string; value: string }[]
  >([]);

  /* ---------- Results state ---------- */
  const [items, setItems] = useState<TenderItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [page, setPage] = useState(1);
  const [keywordError, setKeywordError] = useState(false);
  const pageSize = 10;

  /* ---------- Detail modal ---------- */
  const [detailItem, setDetailItem] = useState<TenderItem | null>(null);

  /* ---------- Load province options ---------- */
  useEffect(() => {
    fetch('/api/v1/bid/areas?parent_code=0&level=1', {
      headers: { Authorization: getAuthorization() },
    })
      .then((r) => r.json())
      .then((json) => {
        const list = json?.data ?? [];
        setProvinceOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => {});
  }, []);

  /* ---------- Load city options when province changes ---------- */
  useEffect(() => {
    if (!provinceCode) {
      setCityOptions([]);
      setCityCode('');
      return;
    }
    fetch(`/api/v1/bid/areas?parent_code=${provinceCode}`, {
      headers: { Authorization: getAuthorization() },
    })
      .then((r) => r.json())
      .then((json) => {
        const list = json?.data ?? [];
        setCityOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => setCityOptions([]));
  }, [provinceCode]);

  /* ---------- Data fetching ---------- */
  const doSearch = useCallback(
    async (pageIndex: number) => {
      setLoading(true);
      setError(null);
      setStale(false);
      try {
        const body: Record<string, any> = {
          keyword: keyword.trim(),
          searchMode,
          page: pageIndex,
          pageSize,
        };
        if (announcementType !== 'all')
          body.announcementType = announcementType;
        if (procurementMethod !== 'all')
          body.procurementMethod = procurementMethod;
        if (searchType.length > 0) body.searchType = searchType.join(',');
        if (targetItemType.length > 0)
          body.targetItemType = targetItemType.join(',');
        if (provinceCode) body.projectRegionProvinceCode = provinceCode;
        if (cityCode) body.projectRegionCityCode = cityCode;
        if (dateRange?.from) body.publishStartTime = fmtDate(dateRange.from);
        if (dateRange?.to) body.publishEndTime = fmtDate(dateRange.to);

        const data = await tenderFetch(body);
        setItems(data.items || []);
        setTotal(data.total || 0);
        if (data.stale) setStale(true);
        setHasSearched(true);
      } catch (e: any) {
        setItems([]);
        setTotal(0);
        setError(friendlyError(e.message));
      } finally {
        setLoading(false);
      }
    },
    [
      keyword,
      searchMode,
      announcementType,
      procurementMethod,
      searchType,
      targetItemType,
      provinceCode,
      cityCode,
      dateRange,
    ],
  );

  /* ---------- Handlers ---------- */
  const handleSearch = useCallback(() => {
    if (loading) return;
    if (keyword.trim().length < 2) {
      setKeywordError(true);
      return;
    }
    setKeywordError(false);
    setPage(1);
    doSearch(1);
  }, [loading, keyword, doSearch]);

  const handlePageChange = useCallback(
    (p: number) => {
      setPage(p);
      doSearch(p);
    },
    [doSearch],
  );

  const handleBack = () => {
    setHasSearched(false);
    setItems([]);
    setTotal(0);
    setError(null);
    setStale(false);
    setPage(1);
  };

  /* ================================================================ */
  /* STATE 1: Search Form                                             */
  /* ================================================================ */
  if (!hasSearched) {
    return (
      <div className="flex-1 overflow-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-12">
          <div className="w-full max-w-3xl">
            {/* Header */}
            <div className="text-center mb-8">
              <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
                <FileText className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                标讯搜索
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                搜索全国招标采购信息
              </p>
            </div>

            {/* Error banner */}
            {error && (
              <div className="mb-4 bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
                <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">
                  !
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[#FF4D4F]">查询失败</p>
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
              <div className="grid grid-cols-3 gap-3 mb-4">
                {/* Keyword (full width) */}
                <div className="col-span-3">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    关键词 <span className="text-[#FF4D4F]">*</span>
                  </label>
                  <div className="relative">
                    <Input
                      value={keyword}
                      onChange={(e) => {
                        setKeyword(e.target.value);
                        setKeywordError(false);
                      }}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      placeholder="输入至少2个字符的关键词..."
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
                  {keywordError && (
                    <p className="text-[11px] text-[#FF4D4F] mt-1">
                      请输入至少2个字符的关键词，避免使用过于通用的词（如&ldquo;上海&rdquo;&ldquo;科技&rdquo;）
                    </p>
                  )}
                </div>

                {/* Search Mode */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    搜索模式
                  </label>
                  <SelectPopover
                    value={String(searchMode)}
                    onChange={(v) => setSearchMode(Number(v))}
                    options={SEARCH_MODES}
                  />
                </div>

                {/* Announcement Type */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    公告类型
                  </label>
                  <SelectPopover
                    value={announcementType}
                    onChange={setAnnouncementType}
                    options={ANNOUNCEMENT_TYPES}
                  />
                </div>

                {/* Procurement Method */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    采购方式
                  </label>
                  <SelectPopover
                    value={procurementMethod}
                    onChange={setProcurementMethod}
                    options={PROCUREMENT_METHODS}
                  />
                </div>

                {/* Region: Province + City */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    省份
                  </label>
                  <SelectPopover
                    value={provinceCode}
                    onChange={(v) => {
                      setProvinceCode(v);
                      setCityCode('');
                    }}
                    options={[{ label: '全部', value: '' }, ...provinceOptions]}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    城市
                  </label>
                  <SelectPopover
                    value={cityCode}
                    onChange={setCityCode}
                    options={[{ label: '全部', value: '' }, ...cityOptions]}
                  />
                </div>

                {/* Search Type (multi-select, full width) */}
                <div className="col-span-3">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    查找范围
                  </label>
                  <MultiSelect
                    options={SEARCH_TYPES}
                    onValueChange={setSearchType}
                    defaultValue={searchType}
                    placeholder="全部（不限）"
                    maxCount={6}
                  />
                </div>

                {/* Target Item Type (multi-select, full width) */}
                <div className="col-span-3">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    招投标阶段
                  </label>
                  <MultiSelect
                    options={TARGET_ITEM_TYPES}
                    onValueChange={setTargetItemType}
                    defaultValue={targetItemType}
                    placeholder="全部（不限）"
                    maxCount={5}
                  />
                </div>

                {/* Date Range */}
                <div className="col-span-3">
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    发布日期
                  </label>
                  <DatePickerWithRange
                    selected={dateRange}
                    onSelect={(range) => setDateRange(range || {})}
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
                    搜索中...
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    搜索
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
  /* STATE 2: Results Table                                           */
  /* ================================================================ */
  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Top bar */}
      <div className="shrink-0 px-6 py-3 flex items-center gap-3 border-b border-[#F0F0F0]">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-sm font-semibold text-[#000000] truncate">
            {keyword}
          </span>
          <Button
            onClick={handleBack}
            className="h-8 px-3 bg-[#000000] hover:bg-[#171717] text-white text-xs font-medium rounded-lg transition-colors shrink-0"
          >
            返回
          </Button>
        </div>
        <span className="text-xs text-[#A3A3A3] shrink-0">
          共 <b className="text-[#000000]">{total}</b> 条结果
        </span>
      </div>

      {/* Stale cache warning */}
      {stale && (
        <div className="shrink-0 px-6 py-2">
          <div className="bg-[#FFFBE6] border border-[#FFE58F] rounded-lg px-4 py-3 flex items-center gap-3">
            <span className="text-sm text-[#AD6800] shrink-0">
              当前使用缓存数据，API可能已达到限额
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

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto px-6 pb-4">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-[#A3A3A3]" />
            <span className="ml-2 text-sm text-[#A3A3A3]">加载中...</span>
          </div>
        ) : items.length > 0 ? (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap text-xs">
                    公告标题
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[160px]">
                    公告类型
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[90px]">
                    招投标阶段
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[80px]">
                    采购方式
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[200px]">
                    项目区域
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[90px]">
                    发布时间
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[60px]">
                    原文地址
                  </TableHead>
                  <TableHead className="whitespace-nowrap text-xs w-[60px]">
                    操作
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => {
                  const pubDate = item.publish_time
                    ? item.publish_time.substring(0, 10)
                    : '';
                  const region = [
                    item.project_region_province,
                    item.project_region_city,
                  ]
                    .filter(Boolean)
                    .join(' ');

                  return (
                    <TableRow key={item.id}>
                      <TableCell className="text-xs max-w-[300px] truncate">
                        {item.title || item.project_name || '-'}
                      </TableCell>
                      <TableCell className="text-xs">
                        {item.announcement_type && (
                          <span
                            className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium ${announcementBadgeClass(item.announcement_type)}`}
                          >
                            {item.announcement_type}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-[#525252]">
                        {item.bidding_stage || '-'}
                      </TableCell>
                      <TableCell className="text-xs text-[#525252]">
                        {item.procurement_method || '-'}
                      </TableCell>
                      <TableCell className="text-xs text-[#525252]">
                        <span className="inline-flex items-center gap-1">
                          <MapPin className="size-3 text-[#A3A3A3]" />
                          {region || '-'}
                        </span>
                      </TableCell>
                      <TableCell className="text-xs text-[#A3A3A3] whitespace-nowrap">
                        {pubDate || '-'}
                      </TableCell>
                      <TableCell className="text-xs">
                        {item.content_url ? (
                          <a
                            href={item.content_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[#1677FF] hover:underline inline-flex items-center gap-1"
                          >
                            <span className="truncate max-w-[80px]">原文</span>
                          </a>
                        ) : (
                          <span className="text-[#D4D4D4]">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Button
                          onClick={() => setDetailItem(item)}
                          className="h-7 px-2.5 text-[11px] bg-[#F5F5F5] hover:bg-[#EAEAEA] text-[#000000] rounded-md border-0"
                        >
                          详情
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>

            {/* Pagination */}
            <div className="mt-4">
              <RAGFlowPagination
                current={page}
                pageSize={pageSize}
                total={total}
                onChange={handlePageChange}
                showSizeChanger={false}
              />
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-20">
            <div className="size-16 rounded-2xl bg-[#F5F5F5] flex items-center justify-center mb-4">
              <X className="size-7 text-[#A3A3A3]" />
            </div>
            <p className="text-sm font-medium text-[#525252] mb-1">
              未找到相关结果
            </p>
            <p className="text-xs text-[#A3A3A3] mb-4">
              请尝试修改关键词或扩大搜索范围
            </p>
            <Button
              onClick={handleBack}
              className="h-9 px-4 bg-[#000000] hover:bg-[#171717] text-white text-sm rounded-lg"
            >
              返回
            </Button>
          </div>
        )}
      </div>

      {/* ================================================================ */}
      {/* Detail Modal                                                     */}
      {/* ================================================================ */}
      {detailItem && (
        <div
          className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center pt-10 pb-10 overflow-y-auto"
          onClick={() => setDetailItem(null)}
        >
          <div
            className="bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 my-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#F0F0F0] sticky top-0 bg-white rounded-t-xl">
              <h3 className="text-base font-bold text-[#000000] truncate pr-4">
                {detailItem.title || detailItem.project_name || '项目详情'}
              </h3>
              <button
                onClick={() => setDetailItem(null)}
                className="shrink-0 size-8 flex items-center justify-center rounded-lg text-[#A3A3A3] hover:text-[#000000] hover:bg-[#F5F5F5] transition-colors"
              >
                <X className="size-4" />
              </button>
            </div>

            {/* Body */}
            <div className="px-6 py-5 space-y-5 max-h-[70vh] overflow-y-auto">
              {/* 基本信息 */}
              <Section title="基本信息">
                <DetailRow label="项目名称" value={detailItem.project_name} />
                <DetailRow
                  label="项目编号"
                  value={detailItem.project_number}
                  mono
                />
                <DetailRow label="公告标题" value={detailItem.title} />
                <DetailRow
                  label="公告类型"
                  value={detailItem.announcement_type}
                />
                <DetailRow
                  label="招投标阶段"
                  value={detailItem.bidding_stage}
                />
                <DetailRow
                  label="采购方式"
                  value={detailItem.procurement_method}
                />
                <DetailRow label="行业分类" value={detailItem.industry_type} />
                <DetailRow
                  label="标的物类型"
                  value={detailItem.target_item_type}
                />
                <DetailRow
                  label="合同编号"
                  value={detailItem.contract_num}
                  mono
                />
              </Section>

              {/* 区域信息 */}
              {(detailItem.project_region_province ||
                detailItem.project_region_city ||
                detailItem.project_region_district) && (
                <Section title="区域信息">
                  <DetailRow
                    label="省份"
                    value={detailItem.project_region_province}
                  />
                  <DetailRow
                    label="城市"
                    value={detailItem.project_region_city}
                  />
                  <DetailRow
                    label="区县"
                    value={detailItem.project_region_district}
                  />
                </Section>
              )}

              {/* 金额信息 */}
              {(detailItem.project_budget_amount ||
                detailItem.total_amount ||
                detailItem.bidding_security_amount) && (
                <Section title="金额信息">
                  <DetailRow
                    label="预算金额"
                    value={
                      detailItem.project_budget_amount
                        ? `${detailItem.project_budget_amount}${detailItem.project_budget_amount_unit || ''}`
                        : ''
                    }
                  />
                  <DetailRow
                    label="中标金额"
                    value={
                      detailItem.total_amount
                        ? `${detailItem.total_amount}${detailItem.total_amount_unit || ''}`
                        : ''
                    }
                  />
                  <DetailRow
                    label="标书售价"
                    value={
                      detailItem.tender_document_price_amount
                        ? `${detailItem.tender_document_price_amount}${detailItem.tender_document_price_unit || ''}`
                        : ''
                    }
                  />
                  <DetailRow
                    label="投标保证金"
                    value={
                      detailItem.bidding_security_amount
                        ? `${detailItem.bidding_security_amount}${detailItem.bidding_security_unit || ''}`
                        : ''
                    }
                  />
                  <DetailRow
                    label="代理服务费"
                    value={
                      detailItem.tender_agent_service_fee_amount
                        ? `${detailItem.tender_agent_service_fee_amount}${detailItem.tender_agent_service_fee_unit || ''}`
                        : ''
                    }
                  />
                  <DetailRow
                    label="履约保证金"
                    value={
                      detailItem.performance_security_amount
                        ? `${detailItem.performance_security_amount}${detailItem.performance_security_unit || ''}`
                        : ''
                    }
                  />
                </Section>
              )}

              {/* 时间信息 */}
              {(detailItem.publish_time ||
                detailItem.bid_document_start_time ||
                detailItem.bidding_start_time ||
                detailItem.opening_bid_time) && (
                <Section title="时间信息">
                  <DetailRow label="发布时间" value={detailItem.publish_time} />
                  <DetailRow
                    label="标书获取开始"
                    value={detailItem.bid_document_start_time}
                  />
                  <DetailRow
                    label="标书获取截止"
                    value={detailItem.bid_document_end_time}
                  />
                  <DetailRow
                    label="投标开始"
                    value={detailItem.bidding_start_time}
                  />
                  <DetailRow
                    label="投标截止"
                    value={detailItem.bidding_end_time}
                  />
                  <DetailRow
                    label="开标时间"
                    value={detailItem.opening_bid_time}
                  />
                </Section>
              )}

              {/* 采购/代理机构 */}
              {detailItem.purchase_agency?.length > 0 && (
                <Section title="采购/代理机构">
                  {detailItem.purchase_agency.map((a, i) => (
                    <div key={i} className="mb-2 last:mb-0">
                      <DetailRow label="企业名称" value={a.companyName} />
                      <DetailRow
                        label="关系类型"
                        value={
                          a.relateType === '1'
                            ? '采购'
                            : a.relateType === '2'
                              ? '代理'
                              : a.relateType
                        }
                      />
                      <DetailRow label="信用代码" value={a.creditNo} mono />
                    </div>
                  ))}
                </Section>
              )}

              {/* 中标/候选企业 */}
              {detailItem.win_candidate?.length > 0 && (
                <Section title="中标/候选企业">
                  {detailItem.win_candidate.map((c, i) => (
                    <div
                      key={i}
                      className="mb-2 last:mb-0 pb-2 last:pb-0 border-b border-[#F5F5F5] last:border-0"
                    >
                      <DetailRow label="企业名称" value={c.companyName} />
                      <DetailRow
                        label="标段编号"
                        value={c.bidSectionNumber}
                        mono
                      />
                      <DetailRow label="标段名称" value={c.bidItemName} />
                      <DetailRow
                        label="关系类型"
                        value={
                          c.relateType === '3'
                            ? '候选人'
                            : c.relateType === '4'
                              ? '中标人'
                              : c.relateType
                        }
                      />
                      <DetailRow
                        label="金额"
                        value={
                          c.amount ? `${c.amount}${c.amountUnit || ''}` : ''
                        }
                      />
                      <DetailRow label="信用代码" value={c.creditNo} mono />
                    </div>
                  ))}
                </Section>
              )}

              {/* 联系方式-采购/代理 */}
              {detailItem.contacts_purchase_agency?.length > 0 && (
                <Section title="联系方式（采购/代理）">
                  {detailItem.contacts_purchase_agency.map((c, i) => (
                    <div key={i} className="mb-2 last:mb-0">
                      <DetailRow label="联系人" value={c.contactPerson} />
                      <DetailRow label="电话" value={c.phone} />
                      <DetailRow label="邮箱" value={c.email} />
                    </div>
                  ))}
                </Section>
              )}

              {/* 联系方式-中标/候选 */}
              {detailItem.contacts_win_candidate?.length > 0 && (
                <Section title="联系方式（中标/候选）">
                  {detailItem.contacts_win_candidate.map((c, i) => (
                    <div key={i} className="mb-2 last:mb-0">
                      <DetailRow label="联系人" value={c.contactPerson} />
                      <DetailRow label="电话" value={c.phone} />
                      <DetailRow label="项目经理" value={c.projectManager} />
                      <DetailRow
                        label="项目经理电话"
                        value={c.projectManagerPhone}
                      />
                    </div>
                  ))}
                </Section>
              )}

              {/* 原文链接 */}
              {detailItem.content_url && (
                <Section title="原文链接">
                  <a
                    href={detailItem.content_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-[#1677FF] hover:underline break-all"
                  >
                    {detailItem.content_url}
                  </a>
                </Section>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Detail Modal Sub-Components                                        */
/* ------------------------------------------------------------------ */

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-[#000000] mb-2 pb-1.5 border-b border-[#F0F0F0]">
        {title}
      </h4>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string;
  value?: string;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="flex items-start text-xs">
      <span className="text-[#A3A3A3] w-24 shrink-0">{label}</span>
      <span
        className={`text-[#000000] flex-1 min-w-0 ${mono ? 'font-mono' : ''}`}
      >
        {value}
      </span>
    </div>
  );
}
