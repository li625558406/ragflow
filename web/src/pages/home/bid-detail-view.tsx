import {
  fetchBidProjectDetail,
  fetchBidProjectFiles,
  fetchBidProjectStructure,
} from '@/services/bid-service';
import { getAuthorization } from '@/utils/authorization-util';
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  Download,
  Eye,
  FileText,
  Loader2,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

type DetailData = Record<string, any>;
type StructureData = Record<string, any>;
type TabKey = 'content' | 'structure' | 'files';

function formatJsonField(raw: any): string {
  if (!raw || raw === '""') return '-';
  try {
    // 防御：db 缓存写入不一致可能导致数据已是解析后的对象/数组
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (Array.isArray(parsed)) {
      return parsed
        .map((item: any) => {
          if (typeof item === 'string') return item;
          if (typeof item === 'object' && item.name) {
            // Format party info objects
            const parts = [item.name];
            if (item.contactName?.length)
              parts.push(`联系人: ${item.contactName.join(', ')}`);
            if (item.contactPhone?.length)
              parts.push(`电话: ${item.contactPhone.join(', ')}`);
            if (item.address?.length)
              parts.push(`地址: ${item.address.join(', ')}`);
            if (item.email?.length)
              parts.push(`邮箱: ${item.email.join(', ')}`);
            return parts.join(' | ');
          }
          return JSON.stringify(item, null, 2);
        })
        .join('\n');
    }
    if (typeof parsed === 'object') return JSON.stringify(parsed, null, 2);
    return String(parsed);
  } catch {
    return typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2);
  }
}

function StructTable({ data }: { data: StructureData }) {
  const fields: { key: string; label: string; json?: boolean }[] = [
    { key: 'project_name', label: '项目名称' },
    { key: 'project_numbers', label: '项目编号', json: true },
    { key: 'section_codes', label: '标段编码', json: true },
    { key: 'budget_money', label: '预算金额', json: true },
    { key: 'bid_money', label: '中标金额', json: true },
    { key: 'bid_start_date', label: '开标日期' },
    { key: 'bid_start_address', label: '开标地址', json: true },
    { key: 'sign_up_stop_date', label: '报名截止日期' },
    { key: 'party_a_info', label: '甲方信息', json: true },
    { key: 'party_b_info', label: '乙方信息', json: true },
    { key: 'agency_info', label: '代理机构', json: true },
    { key: 'bid_companies', label: '投标企业', json: true },
  ];

  return (
    <table className="w-full text-sm border-collapse">
      <tbody>
        {fields.map((f) => {
          const val = data[f.key];
          if (!val || val === '' || val === '[]' || val === '{}') return null;
          return (
            <tr key={f.key} className="border-b border-[#F0F0F0] last:border-0">
              <td className="py-3 pr-6 text-[#A3A3A3] text-xs uppercase tracking-wider font-medium align-top whitespace-nowrap w-28">
                {f.label}
              </td>
              <td className="py-3 text-[#000000] text-sm whitespace-pre-wrap break-words leading-relaxed">
                {f.json ? formatJsonField(val) : String(val)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
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

function FilesList({
  files,
  contentHtml,
}: {
  files: any[];
  contentHtml?: string;
}) {
  // 从 HTML 正文提取附件链接，按文件名与 API 文件匹配
  const htmlLinks = extractFileLinks(contentHtml || '');
  const htmlMap = new Map(htmlLinks.map((l) => [l.name, l.href]));

  const matchedFiles = files.map((f: any) => {
    const name = f.file_name || f.name || '';
    // 优先用 file_url，其次从 HTML 链接匹配
    const url = f.file_url || htmlMap.get(name) || '';
    return { ...f, _downloadUrl: url };
  });

  // 去掉已匹配的，剩余作为额外链接
  const apiFileNames = new Set(files.map((f: any) => f.file_name || f.name));
  const extraLinks = htmlLinks.filter((l) => !apiFileNames.has(l.name));

  if (matchedFiles.length === 0 && extraLinks.length === 0) {
    return (
      <div className="py-12 text-center text-sm text-[#A3A3A3]">暂无附件</div>
    );
  }

  const suffixIcon: Record<string, string> = {
    pdf: '📄',
    doc: '📝',
    docx: '📝',
    xls: '📊',
    xlsx: '📊',
    zip: '📦',
    rar: '📦',
    jpg: '🖼️',
    png: '🖼️',
  };

  return (
    <div className="space-y-2">
      {matchedFiles.map((f: any, idx: number) => {
        const suffix = f.file_suffix || f.suffix || '';
        const size = f.file_size || f.size;
        const downloadUrl = f._downloadUrl;
        return (
          <div
            key={f.project_file_id || f.file_name || f.file_url || idx}
            onClick={() => {
              if (downloadUrl) {
                window.open(downloadUrl, '_blank', 'noopener,noreferrer');
              }
            }}
            className={`flex items-center gap-3 p-3.5 rounded-xl border border-[#F0F0F0] hover:border-[#D4D4D4] hover:shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all group bg-white ${downloadUrl ? 'cursor-pointer' : ''}`}
          >
            <span className="text-lg">
              {suffixIcon[suffix.toLowerCase()] || '📎'}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[#000000] truncate group-hover:text-[#000000]">
                {f.file_name || f.name}
              </div>
              {size && (
                <div className="text-xs text-[#525252]">
                  {suffix.toUpperCase()} · {size} KB
                </div>
              )}
            </div>
            {f.kb_document_id && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(
                    `/web/document/get/${f.kb_document_id}`,
                    '_blank',
                  );
                }}
                className="flex items-center gap-1 text-xs text-[#000000] hover:text-[#000000] shrink-0"
                title="预览知识库文档"
              >
                <Eye className="size-3.5" />
                预览
              </button>
            )}
          </div>
        );
      })}
      {/* HTML 正文提取的附件链接（无 API 文件匹配的） */}
      {extraLinks.length > 0 && (
        <>
          {matchedFiles.length > 0 && (
            <div className="text-xs text-[#A3A3A3] pt-2">正文附件链接</div>
          )}
          {extraLinks.map((l, i) => (
            <a
              key={`hl-${i}`}
              href={l.href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 p-3.5 rounded-xl border border-[#F0F0F0] hover:border-[#D4D4D4] hover:shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all group bg-white"
            >
              <span className="text-lg">📎</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-[#000000] truncate group-hover:text-[#000000]">
                  {l.name}
                </div>
              </div>
              <span className="flex items-center gap-1 text-xs text-[#525252] shrink-0">
                <Download className="size-3.5" />
                打开
              </span>
            </a>
          ))}
        </>
      )}
    </div>
  );
}

export function BidDetailView({
  projectId,
  publishTime,
  onBack,
  projectTitle,
}: {
  projectId: number;
  publishTime: string;
  onBack: () => void;
  projectTitle?: string;
}) {
  const [activeTab, setActiveTab] = useState<TabKey>('content');
  const [detail, setDetail] = useState<DetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [structure, setStructure] = useState<StructureData | null>(null);
  const [structureLoading, setStructureLoading] = useState(false);
  const [files, setFiles] = useState<any[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);

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
    async (projectId: number) => {
      try {
        const resp = await fetch(`/api/v1/bid/projects/${projectId}/parse`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: getAuthorization(),
          },
          body: JSON.stringify({}),
        });
        if (!resp.ok) return;
        const json = await resp.json();
        if (json.code !== 0) return;
        const data = json.data;
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
      const resp = await fetch(
        `/api/v1/bid/projects/${projectId}/parse-status`,
        { headers: { Authorization: getAuthorization() } },
      );
      if (!resp.ok) return;
      const json = await resp.json();
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

  // Fetch detail
  useEffect(() => {
    if (!projectId) return;
    setDetailLoading(true);
    setDetailError('');
    setParseStatus(null);
    fetchBidProjectDetail(projectId, publishTime)
      .then((res: any) => {
        const data = res?.data?.data ?? res?.data ?? null;
        setDetail(data);
        // Auto-trigger KB import after detail loaded
        if (data) triggerParse(projectId);
      })
      .catch((err: any) =>
        setDetailError(
          err?.response?.data?.message || err?.message || '加载失败',
        ),
      )
      .finally(() => setDetailLoading(false));
  }, [projectId, publishTime, triggerParse]);

  // Fetch structure when tab switches
  useEffect(() => {
    if (activeTab !== 'structure' || !projectId || structure) return;
    setStructureLoading(true);
    fetchBidProjectStructure(projectId, publishTime)
      .then((res: any) => setStructure(res?.data?.data ?? res?.data ?? null))
      .catch(() => {})
      .finally(() => setStructureLoading(false));
  }, [activeTab, projectId, publishTime, structure]);

  // Fetch files when tab switches
  useEffect(() => {
    if (activeTab !== 'files' || !projectId || files.length > 0) return;
    setFilesLoading(true);
    fetchBidProjectFiles(projectId, publishTime)
      .then((res: any) => {
        const filesData = res?.data?.data?.files ?? res?.data?.files ?? [];
        console.log(
          '[BidFiles] projectId=%s count=%d sample=%s',
          projectId,
          filesData.length,
          JSON.stringify(
            filesData.slice(0, 3).map((f: any) => ({
              name: f.file_name || f.name,
              file_url: f.file_url,
              file_suffix: f.file_suffix,
            })),
          ),
        );
        setFiles(filesData);
      })
      .catch(() => {})
      .finally(() => setFilesLoading(false));
  }, [activeTab, projectId, publishTime, files.length]);

  const NEWS_TYPE_MAP: Record<number, string> = {
    1: '招标',
    2: '中标',
    3: '合同',
  };
  const PURCHASE_TYPE_MAP: Record<string, string> = {
    '0': '其他',
    '1': '服务类',
    '2': '工程类',
    '3': '货物类',
  };

  const metaFields: {
    key: string;
    label: string;
    transform?: (v: any) => string;
  }[] = [
    {
      key: 'news_type_id',
      label: '资讯类型',
      transform: (v) => NEWS_TYPE_MAP[v] || '-',
    },
    { key: 'project_class_name', label: '项目类别' },
    {
      key: 'purchase_type_id',
      label: '采购方式',
      transform: (v) => PURCHASE_TYPE_MAP[v] || '-',
    },
    { key: 'project_money', label: '项目金额' },
    { key: 'industry_name', label: '行业' },
    { key: 'part_a_name', label: '甲方' },
    { key: 'part_b_name', label: '乙方' },
    { key: 'agent_name', label: '代理机构' },
  ];

  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    {
      key: 'content',
      label: '正文内容',
      icon: <FileText className="size-3.5" />,
    },
    {
      key: 'structure',
      label: '结构化数据',
      icon: <Database className="size-3.5" />,
    },
    {
      key: 'files',
      label: '附件列表',
      icon: <Download className="size-3.5" />,
    },
  ];

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[#F8F9FB]">
      {/* Header */}
      <div className="shrink-0 px-6 py-3 bg-white border-b border-[#E8E8E8] flex items-center gap-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] px-2 py-1 rounded-lg transition-all"
        >
          <ArrowLeft className="size-4" />
          返回列表
        </button>
        <span className="w-px h-4 bg-[#E8E8E8]" />
        <h1 className="text-sm font-semibold text-[#000000] truncate">
          {projectTitle || `项目 #${projectId}`}
          {detail?.part_a_name && (
            <span className="font-normal text-[#525252] ml-2">
              — {detail.part_a_name}
            </span>
          )}
        </h1>
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

      {/* Tabs */}
      <div className="shrink-0 px-6 pt-4 pb-0">
        <div className="flex gap-1 bg-[#F0F0F0] p-1 rounded-xl w-fit">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-all ${
                activeTab === tab.key
                  ? 'bg-white text-[#000000] shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                  : 'text-[#525252] hover:text-[#000000]'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-auto px-6 py-4">
        {/* Content Tab */}
        {activeTab === 'content' && (
          <div className="bg-white rounded-xl border border-[#E8E8E8] shadow-[0_1px_3px_rgba(0,0,0,0.03)] p-6">
            {detailLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#000000]" />
                <span className="ml-2 text-sm text-[#333333]">加载中...</span>
              </div>
            )}
            {detailError && !detailLoading && (
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                {detailError}
              </div>
            )}
            {detail && !detailLoading && !detailError && (
              <>
                {/* Metadata */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
                  {metaFields.map((f) => {
                    const raw = detail[f.key];
                    const v = f.transform ? f.transform(raw) : raw;
                    if (!v || v === '' || v === '-') return null;
                    return (
                      <div
                        key={f.key}
                        className="rounded-xl bg-[#FAFAFA] border border-[#F0F0F0] p-3.5 hover:border-[#D4D4D4] transition-all"
                      >
                        <div className="text-[11px] text-[#A3A3A3] mb-1 uppercase tracking-wider font-medium">
                          {f.label}
                        </div>
                        <div className="text-sm font-semibold text-[#000000] break-all">
                          {v}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* HTML content */}
                <div
                  className="text-sm leading-relaxed text-[#000000] [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mt-4 [&_h1]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_p]:my-1.5 [&_table]:w-full [&_table]:text-xs [&_table]:border-collapse [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:p-1.5 [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:p-1.5 [&_th]:bg-[#FFFFFF] [&_th]:font-medium [&_img]:max-w-full [&_img]:h-auto [&_a]:text-[#000000] [&_a]:underline"
                  dangerouslySetInnerHTML={{
                    __html: detail.content_html || '',
                  }}
                />
              </>
            )}
            {!detail && !detailLoading && !detailError && (
              <div className="py-12 text-center text-sm text-[#A3A3A3]">
                暂无正文内容
              </div>
            )}
          </div>
        )}

        {/* Structure Tab */}
        {activeTab === 'structure' && (
          <div className="bg-white rounded-xl border border-[#E8E8E8] shadow-[0_1px_3px_rgba(0,0,0,0.03)] p-6">
            {structureLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#000000]" />
                <span className="ml-2 text-sm text-[#333333]">加载中...</span>
              </div>
            )}
            {structure && !structureLoading && (
              <>
                <StructTable data={structure} />
                {structure.collect_url && (
                  <div className="mt-4 flex gap-4">
                    {structure.collect_url && (
                      <a
                        href={structure.collect_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-[#000000] hover:underline"
                      >
                        查看原始采集页面
                      </a>
                    )}
                  </div>
                )}
              </>
            )}
            {!structure && !structureLoading && (
              <div className="py-12 text-center text-sm text-[#A3A3A3]">
                暂无结构化数据
              </div>
            )}
          </div>
        )}

        {/* Files Tab */}
        {activeTab === 'files' && (
          <div className="bg-white rounded-xl border border-[#E8E8E8] shadow-[0_1px_3px_rgba(0,0,0,0.03)] p-6">
            {filesLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#000000]" />
                <span className="ml-2 text-sm text-[#333333]">加载中...</span>
              </div>
            )}
            {!filesLoading && (
              <FilesList files={files} contentHtml={detail?.content_html} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
