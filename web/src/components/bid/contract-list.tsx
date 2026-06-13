import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import { getAuthorization } from '@/utils/authorization-util';
import { FileText, Loader2, Search, X } from 'lucide-react';
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

interface ContractDetail {
  content: {
    title?: string;
    content?: string;
    projectMoney?: string;
    partAName?: string;
    partBName?: string;
    agentName?: string;
    industryName?: string;
    publishTime?: string;
    projectFiles?: { projectFileID: number; name: string }[];
  };
  structure: {
    projectName?: string;
    projectNumber?: string[];
    budgetMoney?: string[];
    bidMoney?: string[];
    partyAInfo?: {
      name: string;
      contactName: string[];
      contactPhone: string[];
      address: string[];
      email: string[];
    }[];
    partyBInfo?: {
      name: string;
      contactName: string[];
      contactPhone: string[];
      address: string[];
      email: string[];
    }[];
    agencyInfo?: {
      name: string;
      contactName: string[];
      contactPhone: string[];
      address: string[];
      email: string[];
    }[];
    bidCompany?: { name: string }[];
    siginUpStopDate?: string;
    bidStartDate?: string;
    bidStartAddress?: string[];
  };
}

function fmtDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function extractFileLinks(html: string): { name: string; href: string }[] {
  if (!html) return [];
  const re = /<a\s[^>]*href=["']([^"']+)["'][^>]*>([^<]*)<\/a>/gi;
  const links: { name: string; href: string }[] = [];
  let m;
  while ((m = re.exec(html)) !== null) {
    const href = m[1];
    const name =
      m[2].replace(/<[^>]*>/g, '').trim() || href.split('/').pop() || href;
    links.push({ name, href });
  }
  return links;
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
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000);
    return { from, to };
  });
  const [partAName, setPartAName] = useState('');
  const [partBName, setPartBName] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Detail modal state
  const [detailItem, setDetailItem] = useState<ContractItem | null>(null);
  const [detail, setDetail] = useState<ContractDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

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

  const openDetail = async (item: ContractItem) => {
    setDetailItem(item);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const json = await contractFetch(`projects/${item.id}/detail-v2`, {
        publish_time: item.publishTime,
      });
      setDetail(json.data || null);
    } catch (e: any) {
      setDetailError(e.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    setDetailItem(null);
    setDetail(null);
    setDetailError(null);
  };

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

            {/* Error banner (State 1) */}
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
                    <Loader2 className="size-4 animate-spin" strokeWidth={4} />
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
            className="inline-flex items-center gap-1 h-8 px-4 text-xs font-semibold bg-[#000000] hover:bg-[#171717] text-white rounded-lg transition-all shrink-0"
          >
            返回
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
                onClick={() => openDetail(item)}
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

      {/* ================================================================ */}
      {/* Detail Modal */}
      {/* ================================================================ */}
      {detailItem && (
        <div
          className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center pt-12 pb-12 overflow-auto"
          onClick={closeDetail}
        >
          <div
            className="bg-white rounded-xl shadow-2xl w-full max-w-3xl mx-4 max-h-[85vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="shrink-0 flex items-center justify-between px-6 py-4 border-b border-[#E8E8E8]">
              <div className="flex-1 min-w-0 mr-4">
                <h2
                  className="text-sm font-semibold text-[#000000] leading-snug line-clamp-2"
                  dangerouslySetInnerHTML={{ __html: detailItem.title }}
                />
                <p className="text-xs text-[#A3A3A3] mt-1">
                  {detailItem.publishTime}
                  {detailItem.projectMoney && ` · ${detailItem.projectMoney}`}
                </p>
              </div>
              <button
                onClick={closeDetail}
                className="shrink-0 size-8 flex items-center justify-center rounded-lg text-[#A3A3A3] hover:text-[#000000] hover:bg-[#F5F5F5] transition-colors"
              >
                <X className="size-4" />
              </button>
            </div>

            {/* Modal body */}
            <div className="flex-1 min-h-0 overflow-auto px-6 py-4">
              {detailLoading ? (
                <div className="flex items-center justify-center py-16">
                  <Loader2 className="size-6 animate-spin text-[#A3A3A3]" />
                </div>
              ) : detailError ? (
                <div className="bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3">
                  <p className="text-sm font-medium text-[#FF4D4F]">
                    获取详情失败
                  </p>
                  <p className="text-xs text-[#8C8C8C] mt-1">{detailError}</p>
                </div>
              ) : detail ? (
                <div className="space-y-4">
                  {/* Structured info */}
                  {detail.structure && (
                    <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                      <div className="text-sm font-semibold mb-3">
                        结构化信息
                      </div>
                      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                        {detail.structure.projectName && (
                          <div className="col-span-2 py-1">
                            <span className="text-[#999]">项目名称：</span>
                            <span className="text-[#333]">
                              {detail.structure.projectName}
                            </span>
                          </div>
                        )}
                        {detail.structure.projectNumber?.length > 0 && (
                          <div className="col-span-2 py-1">
                            <span className="text-[#999]">项目编号：</span>
                            <span className="text-[#333]">
                              {detail.structure.projectNumber.join(' / ')}
                            </span>
                          </div>
                        )}
                        {detail.structure.budgetMoney?.length > 0 && (
                          <div className="py-1">
                            <span className="text-[#999]">预算金额：</span>
                            <span className="text-[#333]">
                              {detail.structure.budgetMoney.join(' / ')}
                            </span>
                          </div>
                        )}
                        {detail.structure.bidMoney?.length > 0 && (
                          <div className="py-1">
                            <span className="text-[#999]">中标金额：</span>
                            <span className="text-[#1a1a1a] font-medium">
                              {detail.structure.bidMoney.join(' / ')}
                            </span>
                          </div>
                        )}
                        {detail.structure.siginUpStopDate && (
                          <div className="py-1">
                            <span className="text-[#999]">报名截止：</span>
                            <span className="text-[#333]">
                              {detail.structure.siginUpStopDate}
                            </span>
                          </div>
                        )}
                        {detail.structure.bidStartDate && (
                          <div className="py-1">
                            <span className="text-[#999]">开标日期：</span>
                            <span className="text-[#333]">
                              {detail.structure.bidStartDate}
                            </span>
                          </div>
                        )}
                      </div>

                      {/* Party A */}
                      {detail.structure.partyAInfo?.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-[#E8E8E8]">
                          <span className="text-xs font-medium text-[#333]">
                            甲方信息
                          </span>
                          {detail.structure.partyAInfo.map((p, i) => (
                            <div key={i} className="mt-1 text-xs text-[#666]">
                              <span className="font-medium">{p.name}</span>
                              {p.contactName?.length > 0 && (
                                <span>
                                  {' '}
                                  · 联系人: {p.contactName.join(', ')}
                                </span>
                              )}
                              {p.contactPhone?.length > 0 && (
                                <span> · {p.contactPhone.join(', ')}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Party B */}
                      {detail.structure.partyBInfo?.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-[#E8E8E8]">
                          <span className="text-xs font-medium text-[#333]">
                            乙方/中标方
                          </span>
                          {detail.structure.partyBInfo.map((p, i) => (
                            <div key={i} className="mt-1 text-xs text-[#666]">
                              <span className="font-medium">{p.name}</span>
                              {p.contactName?.length > 0 && (
                                <span>
                                  {' '}
                                  · 联系人: {p.contactName.join(', ')}
                                </span>
                              )}
                              {p.contactPhone?.length > 0 && (
                                <span> · {p.contactPhone.join(', ')}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Agency */}
                      {detail.structure.agencyInfo?.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-[#E8E8E8]">
                          <span className="text-xs font-medium text-[#333]">
                            代理机构
                          </span>
                          {detail.structure.agencyInfo.map((p, i) => (
                            <div key={i} className="mt-1 text-xs text-[#666]">
                              <span className="font-medium">{p.name}</span>
                              {p.contactName?.length > 0 && (
                                <span>
                                  {' '}
                                  · 联系人: {p.contactName.join(', ')}
                                </span>
                              )}
                              {p.contactPhone?.length > 0 && (
                                <span> · {p.contactPhone.join(', ')}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Bid companies */}
                      {detail.structure.bidCompany?.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-[#E8E8E8]">
                          <span className="text-xs font-medium text-[#333]">
                            投标企业
                          </span>
                          <div className="mt-1 text-xs text-[#666]">
                            {detail.structure.bidCompany
                              .map((c) => c.name)
                              .join(' / ')}
                          </div>
                        </div>
                      )}

                      {/* Attachments — inside structured info, visually prominent */}
                      {((): JSX.Element | null => {
                        const projectFiles = detail.content?.projectFiles || [];
                        const contentLinks = extractFileLinks(
                          detail.content?.content || '',
                        );
                        // Dedupe: prefer projectFiles
                        const pfNames = new Set(
                          projectFiles.map((f: any) => f.name),
                        );
                        const extraLinks = contentLinks.filter(
                          (l) => !pfNames.has(l.name),
                        );
                        if (
                          projectFiles.length === 0 &&
                          extraLinks.length === 0
                        )
                          return null;
                        return (
                          <div className="mt-3 pt-3 border-t-2 border-[#000000]">
                            <div className="text-xs font-bold mb-2 flex items-center gap-1.5 text-[#000000]">
                              <FileText className="size-3.5" />
                              附件列表 (
                              {projectFiles.length + extraLinks.length})
                            </div>
                            <div className="space-y-0.5">
                              {projectFiles.map((f: any, i: number) => (
                                <div
                                  key={`pf-${i}`}
                                  className="text-xs text-[#333] py-0.5 flex items-center gap-2 font-medium"
                                >
                                  <FileText className="size-3 text-[#000000] shrink-0" />
                                  {f.name}
                                </div>
                              ))}
                              {extraLinks.map((l, i) => (
                                <a
                                  key={`cl-${i}`}
                                  href={l.href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-xs text-[#333] py-0.5 flex items-center gap-2 font-medium hover:text-[#000000] hover:underline transition-colors"
                                >
                                  <FileText className="size-3 text-[#000000] shrink-0" />
                                  {l.name}
                                </a>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  {/* Content body — full height, no scrollbar */}
                  {detail.content?.content && (
                    <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                      <div className="text-sm font-semibold mb-3">正文内容</div>
                      <div
                        className="text-xs text-[#333] leading-relaxed bid-content-html"
                        dangerouslySetInnerHTML={{
                          __html: detail.content.content,
                        }}
                      />
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
