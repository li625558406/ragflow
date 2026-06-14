import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import { getAuthorization } from '@/utils/authorization-util';
import {
  CheckCircle2,
  Download,
  FileText,
  HardHat,
  Loader2,
  Paperclip,
  Search,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

interface ConstructionItem {
  id: number;
  title: string;
  summary: string;
  publishTime: string;
  proviceCode: string;
  cityCode: string;
  countyCode: string;
  hasFile: number;
}

const PAGE_SIZE = 20;

export default function ConstructionList() {
  const [items, setItems] = useState<ConstructionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef(1);
  const [keyword, setKeyword] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>(() => {
    const today = new Date();
    return { from: today, to: today };
  });
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  function fmtDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  async function constructionFetch(url: string, params?: Record<string, any>) {
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

  const doFetch = useCallback(
    async (page: number) => {
      setLoading(true);
      setError(null);
      try {
        const json = await constructionFetch('construction/projects', {
          page: page,
          items_per_page: PAGE_SIZE,
          keyword: keyword || undefined,
          start_date: dateRange?.from
            ? `${fmtDate(dateRange.from)} 00:00:00`
            : undefined,
          end_date: dateRange?.to
            ? `${fmtDate(dateRange.to)} 23:59:59`
            : undefined,
        });
        const data = json.data;
        if (page === 1) {
          setItems(data.projects || []);
        } else {
          setItems((prev) => [...prev, ...(data.projects || [])]);
        }
        setTotal(data.total || 0);
        setHasSearched(true);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [keyword, dateRange],
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

  // --- Detail ---
  const [detailItem, setDetailItem] = useState<ConstructionItem | null>(null);
  const [detail, setDetail] = useState<Record<string, any> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const openDetail = async (item: ConstructionItem) => {
    setDetailItem(item);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    setParseStatus(null);
    try {
      const json = await constructionFetch(
        `construction/projects/${item.id}/detail`,
        { publish_time: item.publishTime },
      );
      setDetail(json.data || null);
      // Auto-trigger KB import after detail loaded
      triggerParse(item.id, item.publishTime);
    } catch (e: any) {
      setDetailError(e.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    setDetailItem(null);
    setDetail(null);
    setDetailError(null);
    setParseStatus(null);
  };

  function extractFileLinks(html: string): { name: string; href: string }[] {
    if (!html) return [];
    const re = /<a\s[^>]*href=["']([^"']+)["'][^>]*>([^<]*)<\/a>/gi;
    const links: { name: string; href: string }[] = [];
    let m;
    while ((m = re.exec(html)) !== null) {
      links.push({
        name:
          m[2].replace(/<[^>]*>/g, '').trim() || m[1].split('/').pop() || m[1],
        href: m[1],
      });
    }
    return links;
  }

  function getFileSuffix(name: string): string {
    const parts = name.split('.');
    return parts.length > 1 ? parts.pop()!.toLowerCase() : '';
  }

  function formatFileSize(size: number): string {
    if (!size) return '';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  const SUFFIX_COLORS: Record<string, string> = {
    pdf: 'bg-red-50 text-red-700',
    doc: 'bg-blue-50 text-blue-700',
    docx: 'bg-blue-50 text-blue-700',
    xls: 'bg-green-50 text-green-700',
    xlsx: 'bg-green-50 text-green-700',
    zip: 'bg-amber-50 text-amber-700',
    rar: 'bg-amber-50 text-amber-700',
    jpg: 'bg-purple-50 text-purple-700',
    jpeg: 'bg-purple-50 text-purple-700',
    png: 'bg-purple-50 text-purple-700',
  };

  // --- KB Import ---
  const [parseStatus, setParseStatus] = useState<{
    status: string;
    progress: number;
    progress_msg: string;
  } | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  const triggerParse = useCallback(
    async (projectId: number, publishTime: string) => {
      try {
        const resp = await constructionFetch(
          `construction/projects/${projectId}/parse`,
          {
            publish_time: publishTime,
          },
        );
        const data = resp.data || resp;
        if (data.status === 'done') {
          setParseStatus({
            status: 'done',
            progress: 1,
            progress_msg: '已导入知识库',
          });
          return;
        }
        setParseStatus({
          status: 'parsing',
          progress: 0,
          progress_msg: '准备导入...',
        });
        pollParseStatus(projectId);
      } catch {
        // silent — don't block detail viewing
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const pollParseStatus = useCallback(async (projectId: number) => {
    try {
      const json = await constructionFetch(
        `construction/projects/${projectId}/parse-status`,
      );
      const data = json.data || json;
      setParseStatus(data);
      if (data.status === 'parsing') {
        pollTimerRef.current = setTimeout(
          () => pollParseStatus(projectId),
          3000,
        );
      }
    } catch {
      // stop polling on error
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
                <HardHat className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                拟在建项目
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                搜索规划审批中的建设项目
              </p>
            </div>

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  关键词
                </label>
                <div className="relative">
                  <Input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="输入关键词（如：医院、公路）..."
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

              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  发布日期
                </label>
                <DatePickerWithRange
                  selected={dateRange}
                  onSelect={setDateRange}
                  className="w-full"
                />
              </div>

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
                    搜索项目
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
            {keyword || '拟在建项目'}
          </span>
          <span className="text-xs text-[#A3A3A3] shrink-0">
            共 <b className="text-[#000000]">{total}</b> 条结果
          </span>
          <button
            onClick={handleBackToSearch}
            className="inline-flex items-center gap-1 h-8 px-4 text-xs font-medium bg-[#000000] hover:bg-[#171717] text-white rounded-lg transition-all shrink-0"
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
                className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5 cursor-pointer hover:border-[#A3A3A3] transition"
                onClick={() => openDetail(item)}
              >
                <div
                  className="text-sm font-semibold text-[#000000] leading-snug line-clamp-2 mb-2"
                  dangerouslySetInnerHTML={{ __html: item.title }}
                />
                {item.summary && (
                  <div
                    className="text-xs text-[#525252] leading-relaxed line-clamp-2 mb-2"
                    dangerouslySetInnerHTML={{ __html: item.summary }}
                  />
                )}
                <div className="flex gap-x-3 text-xs text-[#525252]">
                  {item.publishTime && <span>{item.publishTime}</span>}
                  {item.hasFile === 1 && <span>有附件</span>}
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
              返回
            </Button>
          </div>
        )}
      </div>

      {/* Detail Modal */}
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
                  {detailItem.hasFile === 1 && ' \u00B7 有附件'}
                </p>
              </div>
              <button
                onClick={closeDetail}
                className="shrink-0 size-8 flex items-center justify-center rounded-lg text-[#A3A3A3] hover:text-[#000000] hover:bg-[#F5F5F5] transition-colors"
              >
                <X className="size-4" />
              </button>
            </div>

            {/* KB import progress bar */}
            {parseStatus?.status === 'parsing' && (
              <div className="shrink-0 px-6 py-3 border-b border-[#E8E8E8] bg-[#FAFAFA]">
                <div className="flex items-center gap-3">
                  <Loader2 className="size-4 animate-spin text-[#000000]" />
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-[#333]">
                        正在导入知识库并解析...
                      </span>
                      <span className="text-xs text-[#A3A3A3]">
                        {Math.round(parseStatus.progress * 100)}%
                      </span>
                    </div>
                    <div className="h-1.5 bg-[#E8E8E8] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[#000000] rounded-full transition-all duration-500"
                        style={{ width: `${parseStatus.progress * 100}%` }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* KB import done banner */}
            {parseStatus?.status === 'done' && (
              <div className="shrink-0 px-6 py-2.5 border-b border-[#E8E8E8] bg-[#F0FFF4]">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-[#16A34A]" />
                  <span className="text-xs font-medium text-[#16A34A]">
                    已导入知识库
                  </span>
                  <span className="text-xs text-[#A3A3A3]">
                    {parseStatus.progress_msg}
                  </span>
                </div>
              </div>
            )}

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
                  {/* Pre-compute HTML file links once */}
                  {(() => {
                    const htmlLinks = extractFileLinks(detail.content || '');
                    const hasHtmlLinks = htmlLinks.length > 0;
                    const projectFiles = detail.projectFiles || [];
                    const hasProjectFiles = projectFiles.length > 0;

                    return (
                      <>
                        {/* Construction company info */}
                        {detail.constructionCompany && (
                          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                            <div className="text-sm font-semibold mb-3">
                              建设单位
                            </div>
                            <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                              {detail.constructionCompany.name && (
                                <div className="col-span-2 py-1">
                                  <span className="text-[#999]">名称：</span>
                                  <span className="text-[#333]">
                                    {detail.constructionCompany.name}
                                  </span>
                                </div>
                              )}
                              {detail.constructionCompany.contactPerson && (
                                <div className="py-1">
                                  <span className="text-[#999]">联系人：</span>
                                  <span className="text-[#333]">
                                    {detail.constructionCompany.contactPerson}
                                  </span>
                                </div>
                              )}
                              {detail.constructionCompany.contactPhone && (
                                <div className="py-1">
                                  <span className="text-[#999]">电话：</span>
                                  <span className="text-[#333]">
                                    {detail.constructionCompany.contactPhone}
                                  </span>
                                </div>
                              )}
                              {detail.constructionCompany.address && (
                                <div className="col-span-2 py-1">
                                  <span className="text-[#999]">地址：</span>
                                  <span className="text-[#333]">
                                    {detail.constructionCompany.address}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Content HTML */}
                        {detail.content && (
                          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                            <div className="text-sm font-semibold mb-3">
                              正文内容
                            </div>
                            <div
                              className="text-xs leading-relaxed text-[#333333] [&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1 [&_p]:my-1 [&_table]:w-full [&_table]:text-xs [&_table]:border-collapse [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:p-1.5 [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:p-1.5 [&_th]:bg-[#FAFAFA] [&_a]:text-[#000000] [&_a]:underline"
                              dangerouslySetInnerHTML={{
                                __html: detail.content,
                              }}
                            />
                          </div>
                        )}

                        {/* Project files from API — prominent display */}
                        {hasProjectFiles && (
                          <div className="rounded-xl border-2 border-[#000000]/10 bg-[#FAFAFA] p-5">
                            <div className="flex items-center gap-2 mb-4">
                              <Paperclip className="size-4 text-[#000000]" />
                              <div className="text-sm font-semibold text-[#000000]">
                                项目附件
                              </div>
                              <span className="text-xs bg-[#000000] text-white px-2 py-0.5 rounded-full">
                                {projectFiles.length} 个文件
                              </span>
                            </div>
                            <div className="space-y-2">
                              {projectFiles.map((f: any, i: number) => {
                                const suffix = getFileSuffix(f.name || '');
                                const suffixColor =
                                  SUFFIX_COLORS[suffix] ||
                                  'bg-gray-50 text-gray-700';
                                const fileUrl =
                                  f.url || f.fileUrl || f.file_url || '';
                                return (
                                  <div
                                    key={
                                      f.projectFileID ||
                                      f.project_file_id ||
                                      f.name ||
                                      i
                                    }
                                    className="flex items-center justify-between p-3 rounded-lg border border-[#E8E8E8] hover:border-[#000000]/20 hover:bg-white transition"
                                  >
                                    <div className="flex items-center gap-3 min-w-0">
                                      <FileText className="size-4 text-[#525252] shrink-0" />
                                      <div className="min-w-0">
                                        <div className="text-sm font-medium text-[#333] truncate">
                                          {f.name}
                                        </div>
                                        <div className="flex items-center gap-2 mt-0.5">
                                          {suffix && (
                                            <span
                                              className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${suffixColor}`}
                                            >
                                              {suffix.toUpperCase()}
                                            </span>
                                          )}
                                          {f.size && (
                                            <span className="text-xs text-[#A3A3A3]">
                                              {formatFileSize(f.size)}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    </div>
                                    {fileUrl && (
                                      <a
                                        href={fileUrl}
                                        download
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="shrink-0 inline-flex items-center gap-1 h-8 px-3 text-xs font-medium bg-[#000000] hover:bg-[#171717] text-white rounded-lg transition"
                                      >
                                        <Download className="size-3.5" />
                                        下载
                                      </a>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}

                        {/* File links extracted from HTML content */}
                        {hasHtmlLinks && (
                          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                            <div className="flex items-center gap-2 mb-3">
                              <FileText className="size-4 text-[#525252]" />
                              <div className="text-sm font-semibold">
                                正文附件链接
                              </div>
                              <span className="text-xs bg-[#F5F5F5] text-[#525252] px-2 py-0.5 rounded-full">
                                {htmlLinks.length} 个
                              </span>
                            </div>
                            <div className="space-y-2">
                              {htmlLinks.map((link, i) => (
                                <a
                                  key={i}
                                  href={link.href}
                                  download
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="flex items-center justify-between p-3 rounded-lg border border-[#E8E8E8] hover:border-[#000000]/20 hover:bg-white transition text-xs"
                                >
                                  <div className="flex items-center gap-3 min-w-0">
                                    <FileText className="size-4 text-[#525252] shrink-0" />
                                    <span className="truncate text-[#333] font-medium">
                                      {link.name}
                                    </span>
                                  </div>
                                  <Download className="size-3.5 text-[#525252] shrink-0" />
                                </a>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Fallback: no content at all */}
                        {!detail.content &&
                          !detail.constructionCompany &&
                          !hasProjectFiles && (
                            <div className="py-8 text-center text-sm text-[#A3A3A3]">
                              暂无详情数据
                            </div>
                          )}
                      </>
                    );
                  })()}
                </div>
              ) : (
                <div className="py-8 text-center text-sm text-[#A3A3A3]">
                  暂无详情数据
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
