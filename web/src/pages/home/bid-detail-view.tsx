import {
  fetchBidProjectDetail,
  fetchBidProjectFiles,
  fetchBidProjectStructure,
} from '@/services/bid-service';
import {
  ArrowLeft,
  Database,
  Download,
  Eye,
  FileText,
  Loader2,
} from 'lucide-react';
import { useEffect, useState } from 'react';

type DetailData = Record<string, any>;
type StructureData = Record<string, any>;
type TabKey = 'content' | 'structure' | 'files';

function formatJsonField(raw: string | null | undefined): string {
  if (!raw || raw === '""') return '-';
  try {
    const parsed = JSON.parse(raw);
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
    return raw;
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
            <tr key={f.key} className="border-b border-[rgba(124,92,252,0.08)]">
              <td className="py-3 pr-4 text-[#6b6b8a] align-top whitespace-nowrap w-28">
                {f.label}
              </td>
              <td className="py-3 text-[#2d2d4a] whitespace-pre-wrap break-words">
                {f.json ? formatJsonField(val) : String(val)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FilesList({ files }: { files: any[] }) {
  if (!files || files.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-[#9b9bb5]">暂无附件</div>
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
      {files.map((f: any, idx: number) => {
        const suffix = f.file_suffix || f.suffix || '';
        const size = f.file_size || f.size;
        return (
          <div
            key={f.project_file_id || f.file_name || f.file_url || idx}
            className="flex items-center gap-3 p-3 rounded-lg border border-[rgba(124,92,252,0.08)] hover:bg-[#f4f1fb] transition-colors group"
          >
            <span className="text-lg">
              {suffixIcon[suffix.toLowerCase()] || '📎'}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[#2d2d4a] truncate group-hover:text-[#7c5cfc]">
                {f.file_name || f.name}
              </div>
              {size && (
                <div className="text-xs text-[#9b9bb5]">
                  {suffix.toUpperCase()} · {size} KB
                </div>
              )}
            </div>
            {f.kb_document_id && (
              <button
                onClick={() =>
                  window.open(`/web/document/get/${f.kb_document_id}`, '_blank')
                }
                className="flex items-center gap-1 text-xs text-[#7c5cfc] hover:text-[#6b4ce0] shrink-0"
                title="预览知识库文档"
              >
                <Eye className="size-3.5" />
                预览
              </button>
            )}
            {f.file_url && (
              <a
                href={f.file_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-xs text-[#7c5cfc] hover:text-[#6b4ce0] shrink-0"
                title="下载原始文件"
              >
                <Download className="size-3.5" />
                下载
              </a>
            )}
          </div>
        );
      })}
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

  // Fetch detail
  useEffect(() => {
    if (!projectId) return;
    setDetailLoading(true);
    setDetailError('');
    fetchBidProjectDetail(projectId, publishTime)
      .then((res: any) => setDetail(res?.data?.data ?? res?.data ?? null))
      .catch((err: any) =>
        setDetailError(
          err?.response?.data?.message || err?.message || '加载失败',
        ),
      )
      .finally(() => setDetailLoading(false));
  }, [projectId, publishTime]);

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
      .then((res: any) =>
        setFiles(res?.data?.data?.files ?? res?.data?.files ?? []),
      )
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
    <div className="flex-1 flex flex-col min-h-0 bg-[#f8f6f3]">
      {/* Header */}
      <div className="shrink-0 px-6 py-3 bg-white border-b border-[rgba(124,92,252,0.06)] flex items-center gap-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-[#5a5a7a] hover:text-[#7c5cfc] transition-colors"
        >
          <ArrowLeft className="size-4" />
          返回列表
        </button>
        <span className="text-[rgba(124,92,252,0.2)]">|</span>
        <h1 className="text-sm font-semibold text-[#1c1c2e] truncate">
          {projectTitle || `项目 #${projectId}`}
          {detail?.part_a_name && (
            <span className="font-normal text-[#6b6b8a] ml-2">
              — {detail.part_a_name}
            </span>
          )}
        </h1>
      </div>

      {/* Tabs */}
      <div className="shrink-0 px-6 bg-white border-b border-[rgba(124,92,252,0.06)]">
        <div className="flex gap-0">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors relative ${
                activeTab === tab.key
                  ? 'text-[#7c5cfc] after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-[#7c5cfc]'
                  : 'text-[#6b6b8a] hover:text-[#7c5cfc]'
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
          <div className="bg-white rounded-lg shadow-sm p-6">
            {detailLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#7c5cfc]" />
                <span className="ml-2 text-sm text-[#6b6b8a]">加载中...</span>
              </div>
            )}
            {detailError && !detailLoading && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
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
                        className="rounded-lg bg-[#f5f3fa] border border-[rgba(124,92,252,0.08)] p-3"
                      >
                        <div className="text-xs text-[#6b6b8a] mb-0.5">
                          {f.label}
                        </div>
                        <div className="text-sm font-medium text-[#2d2d4a] break-all">
                          {v}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* HTML content */}
                <div
                  className="text-sm leading-relaxed text-[#2d2d4a] [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mt-4 [&_h1]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-4 [&_h2]:mb-2 [&_p]:my-1.5 [&_table]:w-full [&_table]:text-xs [&_table]:border-collapse [&_td]:border [&_td]:border-[rgba(124,92,252,0.08)] [&_td]:p-1.5 [&_th]:border [&_th]:border-[rgba(124,92,252,0.08)] [&_th]:p-1.5 [&_th]:bg-[#f5f3fa] [&_th]:font-medium [&_img]:max-w-full [&_img]:h-auto [&_a]:text-[#7c5cfc] [&_a]:underline"
                  dangerouslySetInnerHTML={{
                    __html: detail.content_html || '',
                  }}
                />
              </>
            )}
            {!detail && !detailLoading && !detailError && (
              <div className="py-12 text-center text-sm text-[#9b9bb5]">
                暂无正文内容
              </div>
            )}
          </div>
        )}

        {/* Structure Tab */}
        {activeTab === 'structure' && (
          <div className="bg-white rounded-lg shadow-sm p-6">
            {structureLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#7c5cfc]" />
                <span className="ml-2 text-sm text-[#6b6b8a]">加载中...</span>
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
                        className="text-sm text-[#7c5cfc] hover:underline"
                      >
                        查看原始采集页面
                      </a>
                    )}
                  </div>
                )}
              </>
            )}
            {!structure && !structureLoading && (
              <div className="py-12 text-center text-sm text-[#9b9bb5]">
                暂无结构化数据
              </div>
            )}
          </div>
        )}

        {/* Files Tab */}
        {activeTab === 'files' && (
          <div className="bg-white rounded-lg shadow-sm p-6">
            {filesLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-[#7c5cfc]" />
                <span className="ml-2 text-sm text-[#6b6b8a]">加载中...</span>
              </div>
            )}
            {!filesLoading && <FilesList files={files} />}
          </div>
        )}
      </div>
    </div>
  );
}
