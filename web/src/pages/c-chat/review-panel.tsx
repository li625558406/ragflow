import { Sheet, SheetContent } from '@/components/ui/sheet';
import api from '@/utils/api';
import request from '@/utils/next-request';
import {
  AlertCircle,
  AlertTriangle,
  ChevronRight,
  Download,
  FileText,
  Info,
  Loader2,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

// ── Types ──

export interface Annotation {
  matched_text: string;
  type: string;
  severity: 'high' | 'medium' | 'low';
  issue: string;
  suggestion: string;
}

interface Paragraph {
  index: number;
  text: string;
  type: 'heading' | 'paragraph' | 'table' | 'image';
  page: number;
  heading_level?: number;
}

interface FileContent {
  filename: string;
  file_type: string;
  paragraphs: Paragraph[];
}

interface ReviewPanelProps {
  open: boolean;
  onClose: () => void;
  fileId: string;
  fileName: string;
  annotations: Annotation[];
  inline?: boolean;
}

// ── Severity config ──

const SEVERITY_CONFIG: Record<
  string,
  {
    bg: string;
    border: string;
    icon: typeof AlertTriangle;
    label: string;
    textColor: string;
  }
> = {
  high: {
    bg: '#FFF2F0',
    border: '#FF4D4F',
    icon: AlertCircle,
    label: '高',
    textColor: '#FF4D4F',
  },
  medium: {
    bg: '#FFF7E6',
    border: '#FA8C16',
    icon: AlertTriangle,
    label: '中',
    textColor: '#FA8C16',
  },
  low: {
    bg: '#F0F5FF',
    border: '#1890FF',
    icon: Info,
    label: '低',
    textColor: '#1890FF',
  },
};

const TYPE_LABELS: Record<string, string> = {
  policy_violation: '政策违规',
  format_error: '格式错误',
  logic_defect: '逻辑缺陷',
  info_missing: '信息缺失',
  risk_warning: '风险提示',
};

// ── Safe HTML sanitizer (defense-in-depth for table content) ──

function sanitizeTableHtml(html: string): string {
  // Strip <script>, <iframe>, <object>, <embed> tags and on* event handlers
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<iframe[\s\S]*?<\/iframe>/gi, '')
    .replace(/<object[\s\S]*?<\/object>/gi, '')
    .replace(/<embed[\s\S]*?>/gi, '')
    .replace(/\s+on\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\s+on\w+\s*=\s*'[^']*'/gi, '')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, '');
}

// ── Paragraph matcher ──

function matchAnnotation(
  paragraphText: string,
  annotation: Annotation,
): boolean {
  const target = annotation.matched_text?.trim();
  if (!target || target.length < 2) return false;
  return paragraphText.includes(target);
}

function findMatchingAnnotation(
  paragraphText: string,
  annotations: Annotation[],
): Annotation | undefined {
  // Find longest match (most specific)
  let best: Annotation | undefined;
  let bestLen = 0;
  for (const ann of annotations) {
    if (
      matchAnnotation(paragraphText, ann) &&
      (ann.matched_text?.length || 0) > bestLen
    ) {
      best = ann;
      bestLen = ann.matched_text?.length || 0;
    }
  }
  return best;
}

// ── Inline annotation highlight ──

function highlightText(text: string, annotation: Annotation) {
  const target = annotation.matched_text?.trim();
  if (!target || !text.includes(target)) {
    return <span>{text}</span>;
  }
  const parts = text.split(target);
  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && (
            <mark className="bg-yellow-200 text-yellow-900 rounded-sm px-0.5">
              {target}
            </mark>
          )}
        </span>
      ))}
    </>
  );
}

// ── Component ──

export default function ReviewPanel({
  open,
  onClose,
  fileId,
  fileName,
  annotations,
  inline = false,
}: ReviewPanelProps) {
  const [content, setContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  // Build an annotation set keyed by paragraph index for O(1) lookup
  const annotationMap = useMemo(() => {
    if (!content) return new Map<number, Annotation>();
    const map = new Map<number, Annotation>();
    for (const para of content.paragraphs) {
      const match = findMatchingAnnotation(para.text, annotations);
      if (match) {
        map.set(para.index, match);
      }
    }
    return map;
  }, [content, annotations]);

  // Fetch file content when panel opens
  useEffect(() => {
    if (!open || !fileId) return;
    let cancelled = false;

    setLoading(true);
    setError(null);

    request
      .get(api.getFileContent(fileId))
      .then((res: any) => {
        if (cancelled) return;
        if (res?.data?.code === 0) {
          const data = res.data.data;
          console.log('[ReviewPanel] file content response', {
            fileId,
            filename: data?.filename,
            file_type: data?.file_type,
            paragraph_count: data?.paragraphs?.length,
            types: data?.paragraphs?.map((p: any) => p.type),
            first_3_paragraphs: data?.paragraphs?.slice(0, 3).map((p: any) => ({
              index: p.index,
              type: p.type,
              text_preview: p.text?.substring(0, 80),
            })),
          });
          setContent(data);
        } else {
          setError(res?.data?.message || 'Failed to load file content');
        }
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(e?.message || 'Failed to load file content');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, fileId]);

  // Download annotated docx
  const handleDownload = async () => {
    if (!fileId || !annotations.length) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const res = await request.post(
        api.annotateFile(fileId),
        { annotations },
        { responseType: 'blob' },
      );
      // Check if the response is actually JSON (error) rather than blob
      if (
        res.data?.type === 'application/json' ||
        (typeof res.data === 'string' && res.data.startsWith('{'))
      ) {
        // Server returned JSON error despite blob request
        setDownloadError('服务端处理失败，请检查文件是否有效');
        return;
      }
      const blob = res.data;
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `annotated_${fileName || 'document.docx'}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (e: any) {
      const status = e?.response?.status;
      if (status === 400) {
        setDownloadError('请求参数有误，请重试');
      } else if (status === 413) {
        setDownloadError('文件过大，无法生成标注文档');
      } else if (status === 500) {
        setDownloadError('服务端错误，请稍后重试');
      } else if (e?.message === 'Network Error' || !e?.response) {
        setDownloadError('网络连接失败，请检查网络后重试');
      } else {
        setDownloadError('下载失败，请重试');
      }
    } finally {
      setDownloading(false);
    }
  };

  // Stats
  const stats = useMemo(() => {
    const bySeverity = { high: 0, medium: 0, low: 0 };
    for (const ann of annotations) {
      if (bySeverity[ann.severity as keyof typeof bySeverity] !== undefined) {
        bySeverity[ann.severity as keyof typeof bySeverity]++;
      }
    }
    const matched = annotationMap.size;
    return { matched, total: annotations.length, bySeverity };
  }, [annotations, annotationMap]);

  const innerContent = (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-[#F0F0F0] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <FileText
            className="w-4 h-4 text-[#525252] shrink-0"
            strokeWidth={2}
          />
          <h2 className="text-sm font-semibold text-[#1A1A1A] truncate">
            {fileName || '文件审核'}
          </h2>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {annotations.length > 0 && (
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="flex items-center gap-1.5 text-xs font-medium text-[#3F5B8D] hover:text-[#2E365A] px-2.5 py-1.5 rounded-lg hover:bg-[#F0F3FA] transition-colors disabled:opacity-50"
            >
              {downloading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" strokeWidth={2} />
              ) : (
                <Download className="w-3.5 h-3.5" strokeWidth={2} />
              )}
              下载标注文档
            </button>
          )}
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-[#F5F5F5] transition"
          >
            <X className="w-4 h-4 text-[#8A8A8A]" strokeWidth={2} />
          </button>
        </div>
      </div>

      {/* Download error toast */}
      {downloadError && (
        <div className="mx-5 mt-2 flex items-center gap-2 px-3 py-2 rounded-md bg-[#FFF2F0] border border-[#FFCCC7] text-xs text-[#FF4D4F]">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" strokeWidth={2} />
          <span className="flex-1">{downloadError}</span>
          <button
            onClick={() => setDownloadError(null)}
            className="shrink-0 p-0.5 hover:opacity-70"
          >
            <X className="w-3 h-3" strokeWidth={2} />
          </button>
        </div>
      )}

      {/* Annotation stats bar */}
      {annotations.length > 0 && (
        <div className="flex items-center gap-3 px-5 py-2.5 text-xs border-b border-[#F0F0F0] bg-[#FAFAFA]">
          <span className="text-[#8A8A8A]">
            共 {stats.total} 处标注，{stats.matched} 处已定位
          </span>
          <span className="text-[#E8E8E8]">|</span>
          <span
            className="flex items-center gap-1"
            style={{ color: SEVERITY_CONFIG.high.textColor }}
          >
            <AlertCircle className="w-3 h-3" strokeWidth={2} />高{' '}
            {stats.bySeverity.high}
          </span>
          <span
            className="flex items-center gap-1"
            style={{ color: SEVERITY_CONFIG.medium.textColor }}
          >
            <AlertTriangle className="w-3 h-3" strokeWidth={2} />中{' '}
            {stats.bySeverity.medium}
          </span>
          <span
            className="flex items-center gap-1"
            style={{ color: SEVERITY_CONFIG.low.textColor }}
          >
            <Info className="w-3 h-3" strokeWidth={2} />低{' '}
            {stats.bySeverity.low}
          </span>
        </div>
      )}

      {/* Content */}
      <div
        className={`overflow-y-auto px-5 py-4 ${inline ? 'flex-1' : 'h-[calc(100vh-130px)]'}`}
      >
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2
              className="w-6 h-6 animate-spin text-[#8A8A8A]"
              strokeWidth={2}
            />
            <span className="ml-2 text-sm text-[#8A8A8A]">加载文档内容...</span>
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center py-20">
            <div className="text-center">
              <AlertCircle
                className="w-8 h-8 text-[#FF4D4F] mx-auto mb-2"
                strokeWidth={1.5}
              />
              <p className="text-sm text-[#FF4D4F]">{error}</p>
            </div>
          </div>
        )}

        {!loading && !error && content && (
          <div className="space-y-3">
            {content.paragraphs.map((para) => {
              const annotation = annotationMap.get(para.index);
              const sevConfig = annotation
                ? SEVERITY_CONFIG[annotation.severity] || SEVERITY_CONFIG.low
                : null;

              let paraElement: React.ReactNode;

              if (para.type === 'heading') {
                const HeadingTag =
                  para.heading_level && para.heading_level <= 3
                    ? (`h${para.heading_level + 1}` as keyof JSX.IntrinsicElements)
                    : 'h3';
                paraElement = (
                  <HeadingTag className="text-sm font-bold text-[#1A1A1A] mt-4 mb-1">
                    {annotation
                      ? highlightText(para.text, annotation)
                      : para.text}
                  </HeadingTag>
                );
              } else if (para.type === 'table') {
                paraElement = (
                  <div
                    className="text-xs overflow-x-auto"
                    dangerouslySetInnerHTML={{
                      __html: sanitizeTableHtml(para.text),
                    }}
                  />
                );
              } else if (para.type === 'image') {
                paraElement = (
                  <div className="text-xs text-[#8A8A8A] italic py-1">
                    {annotation
                      ? highlightText(para.text, annotation)
                      : para.text}
                  </div>
                );
              } else {
                paraElement = (
                  <p className="text-xs leading-relaxed text-[#333333]">
                    {annotation
                      ? highlightText(para.text, annotation)
                      : para.text}
                  </p>
                );
              }

              return (
                <div
                  key={para.index}
                  data-para-index={para.index}
                  className={`relative rounded-md transition-all ${
                    annotation ? 'border-l-4 pl-3 pr-2 py-1.5' : 'px-2'
                  }`}
                  style={
                    annotation
                      ? {
                          backgroundColor: sevConfig!.bg,
                          borderLeftColor: sevConfig!.border,
                        }
                      : undefined
                  }
                >
                  {paraElement}

                  {/* Annotation bubble */}
                  {annotation &&
                    (() => {
                      const Icon = sevConfig!.icon;
                      return (
                        <div
                          className="mt-2 rounded-md p-2.5 text-xs"
                          style={{ backgroundColor: 'rgba(255,255,255,0.8)' }}
                        >
                          <div className="flex items-center gap-1.5 mb-1">
                            <Icon
                              className="w-3.5 h-3.5"
                              style={{ color: sevConfig!.textColor }}
                              strokeWidth={2}
                            />
                            <span
                              className="font-semibold"
                              style={{ color: sevConfig!.textColor }}
                            >
                              [{sevConfig!.label}]{' '}
                              {TYPE_LABELS[annotation.type] || annotation.type}
                            </span>
                          </div>
                          <p className="text-[#333333] leading-relaxed mb-1">
                            {annotation.issue}
                          </p>
                          {annotation.suggestion && (
                            <div className="flex items-start gap-1 mt-1 text-[#525252]">
                              <ChevronRight
                                className="w-3 h-3 mt-0.5 shrink-0 text-[#3F5B8D]"
                                strokeWidth={2}
                              />
                              <span>{annotation.suggestion}</span>
                            </div>
                          )}
                        </div>
                      );
                    })()}
                </div>
              );
            })}
          </div>
        )}

        {!loading && !error && !content && (
          <div className="flex items-center justify-center py-20">
            <p className="text-sm text-[#8A8A8A]">暂无内容</p>
          </div>
        )}
      </div>
    </>
  );

  if (inline) {
    return (
      <div className="flex flex-col h-full bg-[#FAFBFC] overflow-hidden">
        {innerContent}
      </div>
    );
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <SheetContent
        className="max-w-full p-0"
        style={{ width: '45vw', maxWidth: '600px' }}
      >
        {innerContent}
      </SheetContent>
    </Sheet>
  );
}
