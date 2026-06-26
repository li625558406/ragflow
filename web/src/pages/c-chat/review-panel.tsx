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
  // Allow extra fields from LLM (text, problem, recommendation, etc.)
  [key: string]: any;
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
  fileList?: Array<{ id: string; name: string }>;
  onFileChange?: (fileId: string, fileName: string) => void;
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

// Normalize text for fuzzy matching: strip HTML tags + remove punctuation/spaces/circled digits
function normalizeForMatch(text: string): string {
  return text
    .replace(/<[^>]+>/g, '') // strip HTML tags (table paragraphs)
    .replace(
      /[\s\u2460-\u24ff\u3000-\u303f\uff00-\uffef.,;:!?()[\]{}'"，。、；：！？（）【】《》""''—…·•°≥≤/\\-]/g,
      '',
    ); // strip CJK + ASCII punctuation + circled digits ①②③
}

// Get matched_text from annotation, supporting field name aliases
function getMatchedText(ann: Annotation): string {
  return (ann.matched_text || ann.text || ann.quote || '').trim();
}

function matchAnnotation(
  paragraphText: string,
  annotation: Annotation,
): boolean {
  const target = getMatchedText(annotation);
  if (!target || target.length < 2) return false;
  // Strategy 1: exact match
  if (paragraphText.includes(target)) return true;
  // Strategy 2: HTML-stripped match (for table paragraphs)
  const cleanPara = paragraphText.replace(/<[^>]+>/g, '');
  if (cleanPara.includes(target)) return true;
  // Strategy 3: normalized full match (strip all punctuation)
  const normPara = normalizeForMatch(paragraphText);
  const normTarget = normalizeForMatch(target);
  if (normTarget.length >= 4 && normPara.includes(normTarget)) return true;
  // Strategy 4: keyword match — extract 2-3 key phrases (8+ chars) from target
  // and check if at least 2 appear in the paragraph
  const keywords = [];
  // Split by common delimiters and take meaningful chunks
  const chunks = target
    .split(/[，。、；：的且在持有满足进行评价以下含]/)
    .filter((c) => c.length >= 6);
  for (const chunk of chunks.slice(0, 4)) {
    const normChunk = normalizeForMatch(chunk);
    if (normChunk.length >= 4 && normPara.includes(normChunk)) {
      keywords.push(chunk);
    }
  }
  if (keywords.length >= 2) return true;
  return false;
}

// Inject <mark> highlight into table HTML for matched text
function highlightInTableHtml(
  html: string,
  annotation: Annotation,
  color: string,
  num?: number,
): string {
  const target = getMatchedText(annotation);
  if (!target) return html;
  const markStyle = `background:${color}22;border-bottom:2px solid ${color};border-radius:2px;padding:0 1px;`;
  const wrap = (text: string) =>
    num
      ? `<a href="#annotation-${num}" style="text-decoration:none;color:inherit;"><mark style="${markStyle}">${text}</mark></a>`
      : `<mark style="${markStyle}">${text}</mark>`;

  // Strategy 1: exact text in HTML
  if (html.includes(target)) {
    return html.replace(target, wrap(target));
  }

  // Strategy 2: find longest chunk that exists in HTML
  const chunks = target
    .split(/[，。、；：的且在持有满足进行评价以下含\n]/)
    .filter((c) => c.length >= 5);
  let result = html;
  let replaced = false;
  // Sort by length descending — replace longest chunks first
  chunks.sort((a, b) => b.length - a.length);
  for (const chunk of chunks) {
    if (result.includes(chunk)) {
      result = result.replace(chunk, wrap(chunk));
      replaced = true;
    }
  }
  if (replaced) return result;

  // Strategy 3: normalized — strip ①②③ and punctuation, find in stripped HTML
  html.replace(/<[^>]+>/g, '');
  const normTarget = normalizeForMatch(target);
  if (normTarget.length >= 6) {
    // Try first 15 chars of normalized target as substring search in clean HTML
    const shortTarget = normTarget.substring(0, 15);
    if (shortTarget.length >= 5) {
      // Find the corresponding original text in the HTML
      const chunks2 = target
        .split(/[,，。、；：\s]/)
        .filter((c) => c.length >= 4);
      for (const chunk of chunks2) {
        if (result.includes(chunk)) {
          result = result.replace(chunk, wrap(chunk));
          replaced = true;
        }
      }
    }
  }

  return replaced ? result : html;
}

// ── Inline annotation highlight ──

function highlightText(
  text: string,
  annotation: Annotation,
  color: string = '#FF4D4F',
  num?: number,
) {
  const target = getMatchedText(annotation);
  if (!target || !text.includes(target)) {
    return <span>{text}</span>;
  }
  const parts = text.split(target);
  const scrollToAnn = () => {
    if (!num) return;
    const event = new CustomEvent('annotation-select', { detail: num });
    window.dispatchEvent(event);
    document
      .getElementById(`annotation-${num}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && (
            <mark
              className="rounded-sm px-0.5 py-0.5 cursor-pointer"
              style={{
                backgroundColor: color + '22',
                borderBottom: `2px solid ${color}`,
                color: 'inherit',
              }}
              onClick={scrollToAnn}
            >
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
  fileList,
  onFileChange,
}: ReviewPanelProps) {
  const [content, setContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [selectedAnn, setSelectedAnn] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  // Build annotation set keyed by paragraph index — supports multiple per paragraph
  const annotationMap = useMemo(() => {
    if (!content) return new Map<number, Annotation[]>();
    const map = new Map<number, Annotation[]>();
    for (const para of content.paragraphs) {
      const matches = annotations.filter((ann) =>
        matchAnnotation(para.text, ann),
      );
      if (matches.length > 0) {
        matches.sort(
          (a, b) => getMatchedText(b).length - getMatchedText(a).length,
        );
        map.set(para.index, matches);
      }
    }
    return map;
  }, [content, annotations]);

  // Listen for annotation selection events (from highlight clicks)
  useEffect(() => {
    const handler = (e: Event) => {
      const num = (e as CustomEvent).detail as number;
      setSelectedAnn(num);
    };
    window.addEventListener('annotation-select', handler);
    return () => window.removeEventListener('annotation-select', handler);
  }, []);

  // Debug: log annotation matching status
  useEffect(() => {
    if (annotations.length > 0 && content) {
      const matchedTexts = new Set<string>();
      content.paragraphs.forEach((p) => {
        const matched = annotationMap.get(p.index) || [];
        matched.forEach((a) => matchedTexts.add(getMatchedText(a)));
      });
    }
  }, [annotations, content, annotationMap]);

  // Fetch file content when panel opens
  useEffect(() => {
    if (!open || !fileId) return;
    let cancelled = false;

    setLoading(true);
    setError(null);

    request
      .get(api.getFileContent(fileId), { params: { _t: Date.now() } })
      .then((res: any) => {
        if (cancelled) return;
        if (res?.data?.code === 0) {
          setContent(res.data.data);
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
      <style>{`
        .ann-flash {
          animation: annFlash 1.5s ease-out;
        }
        @keyframes annFlash {
          0% { box-shadow: 0 0 0 3px rgba(63,91,141,0.6); transform: scale(1.02); }
          50% { box-shadow: 0 0 0 6px rgba(63,91,141,0.2); transform: scale(1); }
          100% { box-shadow: 0 0 0 0 transparent; transform: scale(1); }
        }
        :target[id^="annotation-"] {
          animation: annFlash 1.5s ease-out;
        }
      `}</style>
      {/* Header */}
      <div className="px-5 py-3 border-b border-[#F0F0F0] shrink-0">
        <div className="flex items-center justify-between mb-1">
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
                  <Loader2
                    className="w-3.5 h-3.5 animate-spin"
                    strokeWidth={2}
                  />
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
        {/* File tabs if multiple files */}
        {fileList && fileList.length > 1 && (
          <div className="flex gap-1 mt-2 overflow-x-auto">
            {fileList.map((f) => (
              <button
                key={f.id}
                onClick={() => onFileChange?.(f.id, f.name)}
                className={`shrink-0 text-xs px-2.5 py-1 rounded-md transition-colors cursor-pointer truncate max-w-[150px] ${
                  f.id === fileId
                    ? 'bg-[#3F5B8D] text-white'
                    : 'bg-[#F5F5F5] text-[#525252] hover:bg-[#EAEAEA]'
                }`}
              >
                {f.name}
              </button>
            ))}
          </div>
        )}
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

        {!loading &&
          !error &&
          content &&
          (() => {
            let annCounter = 0;
            const numberedByPara = new Map<
              number,
              Array<{ num: number; ann: Annotation }>
            >();
            for (const para of content.paragraphs) {
              const anns = annotationMap.get(para.index) || [];
              if (anns.length > 0)
                numberedByPara.set(
                  para.index,
                  anns.map((a) => ({ num: ++annCounter, ann: a })),
                );
            }
            const allNumbered = Array.from(numberedByPara.values()).flat();
            const matchedSet = new Set(
              allNumbered.map((n) => getMatchedText(n.ann)),
            );
            const unmatched = annotations.filter(
              (a) => !matchedSet.has(getMatchedText(a)),
            );

            return (
              <>
                <div className="space-y-3">
                  {content.paragraphs.map((para) => {
                    const paraAnns = numberedByPara.get(para.index) || [];
                    const firstAnn = paraAnns[0]?.ann || null;
                    const sevConfig = firstAnn
                      ? SEVERITY_CONFIG[firstAnn.severity] ||
                        SEVERITY_CONFIG.low
                      : null;
                    const sevColor = sevConfig?.border || '#FF4D4F';

                    let paraElement: React.ReactNode;
                    if (para.type === 'heading') {
                      const HeadingTag =
                        para.heading_level && para.heading_level <= 3
                          ? (`h${para.heading_level + 1}` as keyof JSX.IntrinsicElements)
                          : 'h3';
                      paraElement = (
                        <HeadingTag className="text-sm font-bold text-[#1A1A1A] mt-4 mb-1">
                          {firstAnn
                            ? highlightText(
                                para.text,
                                firstAnn,
                                sevColor,
                                paraAnns[0].num,
                              )
                            : para.text}
                        </HeadingTag>
                      );
                    } else if (para.type === 'table') {
                      let tableHtml = para.text;
                      paraAnns.forEach(({ num, ann }) => {
                        tableHtml = highlightInTableHtml(
                          tableHtml,
                          ann,
                          (SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low)
                            .border,
                          num,
                        );
                      });
                      paraElement = (
                        <div
                          className="text-xs overflow-x-auto [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:text-[#1A1A1A] [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[#333333]"
                          onClick={(e) => {
                            const a = (e.target as HTMLElement).closest(
                              'a[href^="#annotation-"]',
                            );
                            if (a) {
                              e.preventDefault();
                              const n = parseInt(
                                a
                                  .getAttribute('href')!
                                  .replace('#annotation-', ''),
                              );
                              window.dispatchEvent(
                                new CustomEvent('annotation-select', {
                                  detail: n,
                                }),
                              );
                              document
                                .getElementById(`annotation-${n}`)
                                ?.scrollIntoView({
                                  behavior: 'smooth',
                                  block: 'center',
                                });
                            }
                          }}
                          dangerouslySetInnerHTML={{
                            __html: sanitizeTableHtml(tableHtml),
                          }}
                        />
                      );
                    } else if (para.type === 'image') {
                      paraElement = (
                        <div className="text-xs text-[#8A8A8A] italic py-1">
                          {firstAnn
                            ? highlightText(
                                para.text,
                                firstAnn,
                                sevColor,
                                paraAnns[0].num,
                              )
                            : para.text}
                        </div>
                      );
                    } else {
                      paraElement = (
                        <p className="text-xs leading-relaxed text-[#333333]">
                          {firstAnn
                            ? highlightText(
                                para.text,
                                firstAnn,
                                sevColor,
                                paraAnns[0].num,
                              )
                            : para.text}
                        </p>
                      );
                    }

                    return (
                      <div
                        key={para.index}
                        data-para-index={para.index}
                        className="px-2 py-0.5"
                      >
                        {paraElement}
                      </div>
                    );
                  })}
                </div>

                {/* All annotation cards at the bottom */}
                {(allNumbered.length > 0 || unmatched.length > 0) && (
                  <div className="mt-6 pt-4 border-t-2 border-[#E8E8E8]">
                    <div className="text-sm font-bold text-[#1A1A1A] mb-3">
                      📋 批注列表（{allNumbered.length + unmatched.length} 条）
                    </div>
                    <div className="space-y-2">
                      {allNumbered.map(({ num, ann }) => {
                        const cfg =
                          SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low;
                        const Icon = cfg.icon;
                        const issue =
                          ann.issue || ann.problem || ann.description || '';
                        const suggestion =
                          ann.suggestion ||
                          ann.recommendation ||
                          ann.advice ||
                          '';
                        const annType = ann.type || ann.category || '';
                        const mt = getMatchedText(ann);
                        return (
                          <div
                            key={`ann-${num}`}
                            id={`annotation-${num}`}
                            className={`flex items-start gap-2 rounded-md p-2.5 text-xs transition-all duration-300 ${selectedAnn === num ? 'ring-2 ring-[#3F5B8D] shadow-lg scale-[1.02] brightness-105 z-10' : ''}`}
                            style={{
                              backgroundColor: cfg.bg,
                              borderLeft: `3px solid ${cfg.border}`,
                            }}
                          >
                            <span
                              className="font-bold text-[11px] shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-white"
                              style={{ backgroundColor: cfg.border }}
                            >
                              {num}
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5 mb-1">
                                <Icon
                                  className="w-3.5 h-3.5 shrink-0"
                                  style={{ color: cfg.textColor }}
                                  strokeWidth={2}
                                />
                                <span
                                  className="font-semibold"
                                  style={{ color: cfg.textColor }}
                                >
                                  {cfg.label}{' '}
                                  {TYPE_LABELS[annType] || annType || '问题'}
                                </span>
                              </div>
                              {mt && (
                                <div className="text-[#666] mb-1 leading-relaxed border-l-2 border-[#D4D4D4] pl-2">
                                  📄 {mt.substring(0, 120)}
                                  {mt.length > 120 ? '...' : ''}
                                </div>
                              )}
                              {issue && (
                                <p className="text-[#333333] leading-relaxed mb-1">
                                  {issue}
                                </p>
                              )}
                              {suggestion && (
                                <div className="flex items-start gap-1 text-[#525252]">
                                  <ChevronRight
                                    className="w-3 h-3 mt-0.5 shrink-0 text-[#3F5B8D]"
                                    strokeWidth={2}
                                  />
                                  <span>{suggestion}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                      {unmatched.map((ann, i) => {
                        const num = allNumbered.length + i + 1;
                        const cfg =
                          SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low;
                        const Icon = cfg.icon;
                        const issue =
                          ann.issue || ann.problem || ann.description || '';
                        const suggestion =
                          ann.suggestion ||
                          ann.recommendation ||
                          ann.advice ||
                          '';
                        const annType = ann.type || ann.category || '';
                        const mt = getMatchedText(ann);
                        return (
                          <div
                            key={`unmatched-${i}`}
                            id={`annotation-${num}`}
                            className={`flex items-start gap-2 rounded-md p-2.5 text-xs opacity-75 transition-all duration-300 ${selectedAnn === num ? 'ring-2 ring-[#3F5B8D] shadow-lg scale-[1.02] brightness-105 z-10 opacity-100' : ''}`}
                            style={{
                              backgroundColor: cfg.bg,
                              borderLeft: `3px dashed ${cfg.border}`,
                            }}
                          >
                            <span
                              className="font-bold text-[11px] shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-white opacity-60"
                              style={{ backgroundColor: cfg.border }}
                            >
                              {num}
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5 mb-1">
                                <Icon
                                  className="w-3.5 h-3.5 shrink-0"
                                  style={{ color: cfg.textColor }}
                                  strokeWidth={2}
                                />
                                <span
                                  className="font-semibold"
                                  style={{ color: cfg.textColor }}
                                >
                                  {cfg.label}{' '}
                                  {TYPE_LABELS[annType] || annType || '问题'}
                                  （未定位）
                                </span>
                              </div>
                              {mt && (
                                <div className="text-[#666] mb-1 leading-relaxed border-l-2 border-[#D4D4D4] pl-2">
                                  📄 {mt.substring(0, 120)}
                                  {mt.length > 120 ? '...' : ''}
                                </div>
                              )}
                              {issue && (
                                <p className="text-[#333333] leading-relaxed mb-1">
                                  {issue}
                                </p>
                              )}
                              {suggestion && (
                                <div className="flex items-start gap-1 text-[#525252]">
                                  <ChevronRight
                                    className="w-3 h-3 mt-0.5 shrink-0 text-[#3F5B8D]"
                                    strokeWidth={2}
                                  />
                                  <span>{suggestion}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </>
            );
          })()}

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
