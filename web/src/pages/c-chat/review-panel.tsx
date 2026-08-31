import { Button } from '@/components/ui/button';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import api from '@/utils/api';
import request from '@/utils/next-request';
import type { LexicalEditor } from 'lexical';
import {
  AlertCircle,
  AlertTriangle,
  ChevronRight,
  Download,
  FileText,
  Info,
  Loader2,
  MessageSquare,
  Plus,
  Trash2,
  X,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { diffBlocks, type EditorBlock } from './docx-diff';
import DocxParagraphEditor, { collectEditorOps } from './docx-paragraph-editor';
import {
  highlightInTableByAnchor,
  highlightInTableHtml,
  normalizeForMatch,
  sanitizeTableHtml,
} from './docx-view-utils';

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

/** 手动批注（flow 评论，可带 Word 式锚点） */
export interface MarginComment {
  id: string;
  content: string;
  anchor_text?: string;
  anchor_para?: number | null;
  /** 锚点选段在段落归一化文本中的起始偏移（消歧重复文本） */
  anchor_start?: number | null;
  user_id?: string;
  create_time?: number;
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
  /** 手动批注列表（带锚点的 flow 评论，渲染到正文边栏） */
  comments?: MarginComment[];
  /** 手动批注作者映射 user_id → nickname */
  commentAuthors?: Record<string, string>;
  /** 提交手动批注（选中文本后写入）；不传则不启用手动批注入口 */
  onAddComment?: (p: {
    content: string;
    anchorText: string;
    anchorPara: number | null;
    anchorStart?: number | null;
  }) => Promise<void> | void;
  /** 删除手动批注（仅作者自己的批注显示删除按钮）；不传则不启用 */
  onDeleteComment?: (commentId: string) => Promise<void> | void;
  /** 当前登录用户 id（判断批注删除按钮可见性） */
  currentUserId?: string;
  /** 是否开放正文编辑（整篇 contentEditable，Word 式改字/回车分段/退格并段） */
  canEdit?: boolean;
  /** 提交文档改动（改写/新增/删除段落，保存为新版本后由父级刷新预览） */
  onEditDocument?: (ops: {
    edits: Array<{ paraIndex: number; newText: string }>;
    deletes: number[];
    inserts: Array<{ afterParaIndex: number; newText: string }>;
  }) => Promise<void> | void;
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

// ── Paragraph matcher ──

// Get matched_text from annotation, supporting field name aliases
function getMatchedText(ann: Annotation): string {
  return (ann.matched_text || ann.text || ann.quote || '').trim();
}

/**
 * 在元素内查找锚点文本（跨文本节点、忽略空白差异），返回匹配文字末尾的矩形。
 * 用于表格段落等没有高亮标记 (data-anchor-key) 的批注定位，避免卡片堆到段落顶部。
 * startOffset：锚点文本在段落归一化文本中的起始偏移（创建批注时记录），
 * 用于在重复文本时命中正确的那一处；找不到时回退首个出现位置。
 */
function findTextEndRect(
  root: HTMLElement,
  text: string,
  startOffset?: number | null,
): DOMRect | null {
  const norm = text.replace(/\s+/g, '');
  if (!norm) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: { node: Text; start: number }[] = [];
  let acc = '';
  let n = walker.nextNode() as Text | null;
  while (n) {
    nodes.push({ node: n, start: acc.length });
    acc += (n.nodeValue || '').replace(/\s+/g, '');
    n = walker.nextNode() as Text | null;
  }
  // 优先按记录的起始偏移查找（容差 4 字符，吸收 trim 误差）
  let idx =
    startOffset != null && startOffset > 4
      ? acc.indexOf(norm, startOffset - 4)
      : -1;
  if (idx < 0) idx = acc.indexOf(norm);
  if (idx < 0) return null;
  const endIdx = idx + norm.length;
  // 定位归一化 endIdx 所在的文本节点，并映射回原始偏移
  for (const { node, start } of nodes) {
    const raw = node.nodeValue || '';
    const len = raw.replace(/\s+/g, '').length;
    if (endIdx > start && endIdx <= start + len) {
      let cnt = 0;
      let off = raw.length;
      for (let j = 0; j < raw.length; j++) {
        if (cnt >= endIdx - start) {
          off = j;
          break;
        }
        if (!/\s/.test(raw[j])) cnt++;
      }
      try {
        const r = document.createRange();
        r.setStart(node, Math.max(0, off - 1));
        r.setEnd(node, off);
        const rects = r.getClientRects();
        if (rects.length) return rects[rects.length - 1];
      } catch {
        /* range 越界等异常时回退 */
      }
    }
  }
  return null;
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

// ── Inline annotation highlight（Word 式：正文高亮 + data-anchor-key 供引线锚定） ──

interface HighlightTarget {
  text: string;
  color: string;
  key: string;
}

function renderHighlighted(
  text: string,
  targets: HighlightTarget[],
  onSelect: (key: string) => void,
): React.ReactNode {
  if (!targets.length) return text;
  let segments: React.ReactNode[] = [text];
  for (const t of targets) {
    if (!t.text) continue;
    const next: React.ReactNode[] = [];
    for (const seg of segments) {
      if (typeof seg !== 'string' || !seg.includes(t.text)) {
        next.push(seg);
        continue;
      }
      const parts = seg.split(t.text);
      parts.forEach((p, i) => {
        next.push(p);
        if (i < parts.length - 1) {
          next.push(
            <mark
              key={`${t.key}-${i}`}
              data-anchor-key={t.key}
              className="cursor-pointer rounded-sm px-0.5"
              style={{
                backgroundColor: t.color + '22',
                borderBottom: `2px solid ${t.color}`,
                color: 'inherit',
              }}
              onClick={() => onSelect(t.key)}
            >
              {t.text}
            </mark>,
          );
        }
      });
    }
    segments = next;
  }
  return <>{segments}</>;
}

// ── Margin rail item ──

interface RailItem {
  key: string;
  paraIndex: number;
  kind: 'ai' | 'comment';
  ann?: Annotation;
  num?: number;
  comment?: MarginComment;
  color: string;
}

const RAIL_W = 270;

// ── Cards ──

function AiCard({
  num,
  ann,
  unmatched,
  selected,
  onSelect,
}: {
  num: number;
  ann: Annotation;
  unmatched?: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const cfg = SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low;
  const Icon = cfg.icon;
  const issue = ann.issue || ann.problem || ann.description || '';
  const suggestion = ann.suggestion || ann.recommendation || ann.advice || '';
  const annType = ann.type || ann.category || '';
  const mt = getMatchedText(ann);
  return (
    <div
      id={`annotation-${num}`}
      onClick={onSelect}
      className={`cursor-pointer rounded-md p-2.5 text-xs transition-all duration-300 ${
        selected ? 'ring-2 ring-[#1a66fb] shadow-lg' : ''
      } ${unmatched ? 'opacity-75' : ''}`}
      style={{
        backgroundColor: cfg.bg,
        borderLeft: `3px ${unmatched ? 'dashed' : 'solid'} ${cfg.border}`,
      }}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className="font-bold text-[11px] shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-white"
          style={{ backgroundColor: cfg.border, opacity: unmatched ? 0.6 : 1 }}
        >
          {num}
        </span>
        <Icon
          className="w-3.5 h-3.5 shrink-0"
          style={{ color: cfg.textColor }}
          strokeWidth={2}
        />
        <span className="font-semibold" style={{ color: cfg.textColor }}>
          {cfg.label} {TYPE_LABELS[annType] || annType || '问题'}
          {unmatched ? '（未定位）' : ''}
        </span>
      </div>
      {mt && (
        <div className="text-[#666] mb-1 leading-relaxed border-l-2 border-[#D4D4D4] pl-2">
          📄 {mt.substring(0, 120)}
          {mt.length > 120 ? '...' : ''}
        </div>
      )}
      {issue && <p className="text-[#333333] leading-relaxed mb-1">{issue}</p>}
      {suggestion && (
        <div className="flex items-start gap-1 text-[#525252]">
          <ChevronRight
            className="w-3 h-3 mt-0.5 shrink-0 text-[#1a66fb]"
            strokeWidth={2}
          />
          <span>{suggestion}</span>
        </div>
      )}
    </div>
  );
}

function CommentCard({
  comment,
  author,
  selected,
  onSelect,
  canDelete,
  onDelete,
}: {
  comment: MarginComment;
  author?: string;
  selected: boolean;
  onSelect: () => void;
  canDelete?: boolean;
  onDelete?: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={`group cursor-pointer rounded-md bg-white p-2.5 text-xs transition-all duration-300 ${
        selected ? 'ring-2 ring-[#1a66fb] shadow-lg' : ''
      }`}
      style={{ border: '1px solid #E5E5E5', borderLeft: '3px solid #1a66fb' }}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <MessageSquare
          className="w-3.5 h-3.5 shrink-0 text-[#1a66fb]"
          strokeWidth={2}
        />
        <span className="truncate font-semibold text-[#1a66fb]">
          {author || comment.user_id || '批注'}
        </span>
        {comment.create_time ? (
          <span className="ml-auto shrink-0 text-[10px] text-[#aaa]">
            {new Date(comment.create_time).toLocaleDateString()}
          </span>
        ) : null}
        {canDelete && onDelete && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (window.confirm('确定删除这条批注？')) onDelete();
            }}
            title="删除批注"
            className="shrink-0 rounded p-0.5 text-[#bbb] opacity-0 transition-opacity hover:bg-[#FFF2F0] hover:text-[#FF4D4F] focus:opacity-100 group-hover:opacity-100"
          >
            <Trash2 className="h-3 w-3" strokeWidth={2} />
          </button>
        )}
      </div>
      <div className="whitespace-pre-wrap leading-relaxed text-[#333]">
        {comment.content}
      </div>
    </div>
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
  comments,
  commentAuthors,
  onAddComment,
  onDeleteComment,
  currentUserId,
  canEdit,
  onEditDocument,
}: ReviewPanelProps) {
  const [content, setContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // 手动批注：选中文本后的悬浮入口 + 批注输入框
  const [pendingSel, setPendingSel] = useState<{
    x: number;
    y: number;
    text: string;
    paraIndex: number | null;
    anchorStart: number | null;
  } | null>(null);
  const [draft, setDraft] = useState<{
    x: number;
    y: number;
    text: string;
    paraIndex: number | null;
    anchorStart: number | null;
    note: string;
  } | null>(null);
  const [submittingComment, setSubmittingComment] = useState(false);
  // Word 式整篇编辑：Lexical 编辑器承载正文，保存时按模型 diff 出
  // 改写/新增/删除三类操作；dirty 为改动处数，resetKey 用于放弃修改时重挂载
  const editorRef = useRef<LexicalEditor | null>(null);
  const diffTimer = useRef<number | undefined>(undefined);
  const [dirty, setDirty] = useState(0);
  const [resetKey, setResetKey] = useState(0);
  const [savingEdits, setSavingEdits] = useState(false);
  const [editError, setEditError] = useState('');
  // Word 式引线布局：锚点坐标 + 卡片 top + 画布尺寸
  const wrapRef = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<{
    cards: Record<string, number>;
    anchors: Record<string, { x: number; y: number }>;
    w: number;
    h: number;
  }>({ cards: {}, anchors: {}, w: 0, h: 0 });

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

  // 边栏锚定项：AI 标注 + 带锚点的手动批注，按段落归组
  const railByPara = useMemo(() => {
    const map = new Map<number, RailItem[]>();
    const push = (idx: number, item: RailItem) => {
      const arr = map.get(idx) || [];
      arr.push(item);
      map.set(idx, arr);
    };
    if (content) {
      let num = 0;
      for (const para of content.paragraphs) {
        for (const ann of annotationMap.get(para.index) || []) {
          num += 1;
          push(para.index, {
            key: `ai-${num}`,
            paraIndex: para.index,
            kind: 'ai',
            ann,
            num,
            color: (SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low)
              .border,
          });
        }
      }
      for (const c of comments || []) {
        const at = (c.anchor_text || '').trim();
        if (!at) continue;
        let idx = -1;
        if (c.anchor_para != null) {
          const p = content.paragraphs.find((pp) => pp.index === c.anchor_para);
          if (p && matchAnnotation(p.text, { matched_text: at } as Annotation))
            idx = p.index;
        }
        if (idx < 0) {
          const p = content.paragraphs.find((pp) =>
            matchAnnotation(pp.text, { matched_text: at } as Annotation),
          );
          if (p) idx = p.index;
        }
        if (idx >= 0) {
          push(idx, {
            key: `cm-${c.id}`,
            paraIndex: idx,
            kind: 'comment',
            comment: c,
            color: '#1a66fb',
          });
        }
      }
    }
    return map;
  }, [content, annotationMap, comments]);

  // 边栏项扁平列表（按锚点 Y 排序前的稳定顺序 = 段落顺序）
  const railItems = useMemo(
    () => Array.from(railByPara.values()).flat(),
    [railByPara],
  );

  // 段落高亮目标（首个 AI 标注 + 首个手动批注）
  const targetsByPara = useMemo(() => {
    const m = new Map<number, HighlightTarget[]>();
    for (const [idx, items] of railByPara) {
      const ts: HighlightTarget[] = [];
      const firstAi = items.find((i) => i.kind === 'ai');
      if (firstAi?.ann && getMatchedText(firstAi.ann)) {
        ts.push({
          text: getMatchedText(firstAi.ann),
          color: firstAi.color,
          key: firstAi.key,
        });
      }
      const firstCm = items.find((i) => i.kind === 'comment');
      if (firstCm?.comment?.anchor_text?.trim()) {
        ts.push({
          text: firstCm.comment.anchor_text.trim(),
          color: firstCm.color,
          key: firstCm.key,
        });
      }
      if (ts.length) m.set(idx, ts);
    }
    return m;
  }, [railByPara]);

  // 未匹配到段落的项（边栏下方兜底展示）
  const unmatched = useMemo(() => {
    const matchedAi = new Set(
      railItems
        .filter((i) => i.kind === 'ai')
        .map((i) => getMatchedText(i.ann!)),
    );
    const matchedCm = new Set(
      railItems.filter((i) => i.kind === 'comment').map((i) => i.comment!.id),
    );
    return {
      ai: annotations.filter((a) => !matchedAi.has(getMatchedText(a))),
      comments: (comments || []).filter(
        (c) => (c.anchor_text || '').trim() && !matchedCm.has(c.id),
      ),
      plainComments: (comments || []).filter(
        (c) => !(c.anchor_text || '').trim(),
      ),
    };
  }, [railItems, annotations, comments]);

  // Listen for annotation selection events (from table-HTML highlight clicks)
  useEffect(() => {
    const handler = (e: Event) => {
      const num = (e as CustomEvent).detail as number;
      const key = `ai-${num}`;
      setSelectedKey(key);
      document
        .getElementById(`rail-${key}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    window.addEventListener('annotation-select', handler);
    return () => window.removeEventListener('annotation-select', handler);
  }, []);

  const handleAnchorClick = useCallback((key: string) => {
    setSelectedKey(key);
    document
      .getElementById(`rail-${key}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

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

  // 关闭/切换文件时清掉选区浮层与编辑态
  useEffect(() => {
    if (!open) {
      setPendingSel(null);
      setDraft(null);
      setDirty(0);
      setEditError('');
    }
  }, [open, fileId]);

  // Word 式布局测量：锚点 Y → 卡片 top（防重叠堆叠）+ 画布尺寸
  useLayoutEffect(() => {
    if (!content || !open) return;
    const measure = () => {
      const wrap = wrapRef.current;
      if (!wrap) return;
      const wrapRect = wrap.getBoundingClientRect();
      const anchors: Record<string, { x: number; y: number }> = {};
      for (const it of railItems) {
        const mark = wrap.querySelector<HTMLElement>(
          `[data-anchor-key="${it.key}"]`,
        );
        const para = wrap.querySelector<HTMLElement>(
          `[data-para-index="${it.paraIndex}"]`,
        );
        // 无高亮标记时（如表格内批注），按锚点文本在段落 DOM 内搜索，
        // 锚定到匹配文字末尾而不是整个段落（表格）顶部
        let r: DOMRect | null | undefined = mark?.getBoundingClientRect();
        if (!r && para) {
          const text =
            it.kind === 'ai'
              ? getMatchedText(it.ann!)
              : it.comment?.anchor_text || '';
          const start = it.kind === 'comment' ? it.comment?.anchor_start : null;
          r =
            (text ? findTextEndRect(para, text, start) : null) ??
            (para.getBoundingClientRect() as DOMRect);
        }
        if (!r) continue;
        anchors[it.key] = {
          x: r.right - wrapRect.left,
          y: r.top - wrapRect.top + (mark ? r.height / 2 : 8),
        };
      }
      const tops: Record<string, number> = {};
      let prevBottom = -Infinity;
      const sorted = [...railItems].sort(
        (a, b) => (anchors[a.key]?.y ?? 0) - (anchors[b.key]?.y ?? 0),
      );
      const wrapH = wrap.offsetHeight;
      for (const it of sorted) {
        const a = anchors[it.key];
        if (!a) continue;
        const cardEl = wrap.querySelector<HTMLElement>(
          `[data-card-key="${it.key}"]`,
        );
        const h = cardEl?.offsetHeight ?? 60;
        let top = Math.max(a.y - 8, prevBottom + 8, 0);
        top = Math.max(0, Math.min(top, Math.max(wrapH - h - 8, 0)));
        tops[it.key] = top;
        prevBottom = top + h;
      }
      setLayout((prev) => {
        const same =
          JSON.stringify(prev.cards) === JSON.stringify(tops) &&
          JSON.stringify(prev.anchors) === JSON.stringify(anchors) &&
          prev.w === wrap.offsetWidth &&
          prev.h === wrap.offsetHeight;
        return same
          ? prev
          : { cards: tops, anchors, w: wrap.offsetWidth, h: wrap.offsetHeight };
      });
    };
    const raf = requestAnimationFrame(measure);
    const t = setTimeout(measure, 120); // 字体/图片稳定后的二次校准
    window.addEventListener('resize', measure);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
      window.removeEventListener('resize', measure);
    };
  }, [content, railItems, open, fileId, fileName, annotations, comments]);

  // ── 手动批注：选中文本 → 悬浮「添加批注」→ 输入 → 提交 ──

  const handleContentMouseUp = useCallback(
    (e: React.MouseEvent) => {
      if (!onAddComment) return;
      const mouseX = e.clientX;
      const mouseY = e.clientY;
      setTimeout(() => {
        const s = window.getSelection();
        if (!s || s.isCollapsed || s.rangeCount === 0) {
          setPendingSel(null);
          return;
        }
        const text = s.toString().trim();
        if (text.length < 2) {
          setPendingSel(null);
          return;
        }
        const node = s.anchorNode;
        const el =
          node?.nodeType === 3
            ? node.parentElement
            : (node as HTMLElement | null);
        const paraEl = el?.closest('[data-para-index]') as HTMLElement | null;
        const paraIndex = paraEl
          ? parseInt(paraEl.dataset.paraIndex || '', 10)
          : NaN;
        // 坐标换成相对正文容器（wrapRef, relative），按钮用 absolute 定位，
        // 避免 fixed 在抽屉 transform 容器内基准偏移、被文档宽度挡住
        const wr = wrapRef.current?.getBoundingClientRect();
        // 锚定选区末尾的文本矩形（表格里 getClientRects 按单元格/行拆分，
        // 最后一个即选中文案结尾），比鼠标坐标更精确
        const range = s.getRangeAt(0);
        const rects = Array.from(range.getClientRects()).filter(
          (r) => r.width > 0 || r.height > 0,
        );
        const endRect =
          rects[rects.length - 1] ?? range.getBoundingClientRect();
        const anchorX = endRect?.right ?? mouseX;
        const anchorY = endRect?.bottom ?? mouseY;
        const x = anchorX - (wr?.left ?? 0);
        const y = anchorY - (wr?.top ?? 0);
        const maxX = (wr?.width ?? window.innerWidth) - 120;
        const maxY = (wr?.height ?? window.innerHeight) - 60;
        // 选区起点在段落归一化文本中的偏移（消歧表格内重复文本）
        let anchorStart: number | null = null;
        if (paraEl) {
          try {
            const pre = document.createRange();
            pre.selectNodeContents(paraEl);
            pre.setEnd(range.startContainer, range.startOffset);
            anchorStart = pre.toString().replace(/\s+/g, '').length;
          } catch {
            anchorStart = null;
          }
        }
        // 按钮贴着鼠标松开位置（选中文案旁边），并钳制在容器内
        setPendingSel({
          x: Math.max(8, Math.min(x, maxX)),
          y: Math.max(8, Math.min(y + 12, maxY)),
          text: text.slice(0, 300),
          paraIndex: Number.isFinite(paraIndex) ? paraIndex : null,
          anchorStart,
        });
      }, 0);
    },
    [onAddComment],
  );

  const submitComment = useCallback(async () => {
    if (!draft || !onAddComment || !draft.note.trim()) return;
    setSubmittingComment(true);
    try {
      await onAddComment({
        content: draft.note.trim(),
        anchorText: draft.text,
        anchorPara: draft.paraIndex,
        anchorStart: draft.anchorStart,
      });
      setDraft(null);
      setPendingSel(null);
      window.getSelection()?.removeAllRanges();
    } finally {
      setSubmittingComment(false);
    }
  }, [draft, onAddComment]);

  const handleDeleteComment = useCallback(
    async (commentId: string) => {
      if (!onDeleteComment) return;
      try {
        await onDeleteComment(commentId);
      } catch (e: any) {
        window.alert(e?.message || '删除失败，请稍后重试');
      }
    },
    [onDeleteComment],
  );

  // 编辑器内容变化 → 防抖后 diff 出改动处数（模型级，不碰 DOM）
  const handleEditorDirty = useCallback(
    (blocks: EditorBlock[]) => {
      if (!canEdit || !content) return;
      window.clearTimeout(diffTimer.current);
      diffTimer.current = window.setTimeout(() => {
        const ops = diffBlocks(blocks, content.paragraphs);
        if ('error' in ops) {
          setDirty(0);
          setEditError(ops.error || '当前改动无法保存');
          return;
        }
        setDirty(ops.count);
      }, 250);
    },
    [canEdit, content],
  );

  // 保存：模型 diff 全部改动提交父级写新版本，成功后由新内容重挂载编辑器
  const handleSaveEdits = useCallback(async () => {
    if (!onEditDocument || savingEdits || !editorRef.current || !content)
      return;
    const ops = collectEditorOps(editorRef.current, content.paragraphs);
    if ('error' in ops) {
      setEditError(ops.error || '当前改动无法保存');
      return;
    }
    if (!ops.count) {
      setDirty(0);
      return;
    }
    setSavingEdits(true);
    setEditError('');
    try {
      await onEditDocument(ops);
      setDirty(0);
    } catch (e: any) {
      setEditError(e?.message || '保存失败，请稍后重试');
    } finally {
      setSavingEdits(false);
    }
  }, [content, onEditDocument, savingEdits]);

  // 放弃修改：重挂载纸张，丢弃浏览器侧的 DOM 改动
  const handleDiscardEdits = useCallback(() => {
    window.clearTimeout(diffTimer.current);
    setDirty(0);
    setEditError('');
    setResetKey((k) => k + 1);
  }, []);

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
    const matched = railItems.filter((i) => i.kind === 'ai').length;
    return { matched, total: annotations.length, bySeverity };
  }, [annotations, railItems]);

  const handleSelectTableAnn = useCallback(
    (e: React.MouseEvent) => {
      const a = (e.target as HTMLElement).closest(
        'a[data-anchor-key], mark[data-anchor-key]',
      ) as HTMLAnchorElement | null;
      if (a) {
        e.preventDefault();
        handleAnchorClick(a.dataset.anchorKey!);
      }
    },
    [handleAnchorClick],
  );

  // 编辑器内表格/图片原子块的渲染（与静态分支同一套高亮/批注逻辑）
  const renderAtomicBlock = useCallback(
    ({
      paraIndex,
      kind,
      html,
    }: {
      paraIndex: number;
      kind: 'table' | 'image';
      html: string;
    }) => {
      if (kind === 'image') {
        return (
          <div className="py-1 text-[13px] italic text-[#8A8A8A]">{html}</div>
        );
      }
      const firstAi = (railByPara.get(paraIndex) || []).find(
        (i) => i.kind === 'ai',
      );
      let tableHtml = html;
      if (firstAi?.ann) {
        tableHtml = highlightInTableHtml(
          tableHtml,
          getMatchedText(firstAi.ann),
          firstAi.color,
          firstAi.key,
        );
      }
      // 手动批注：表格内与正文同款 <mark> 高亮（anchor_start 消歧）
      for (const it of railByPara.get(paraIndex) || []) {
        if (it.kind !== 'comment') continue;
        const at = (it.comment?.anchor_text || '').trim();
        if (!at) continue;
        tableHtml = highlightInTableByAnchor(
          tableHtml,
          at,
          it.comment?.anchor_start,
          it.color,
          it.key,
        );
      }
      return (
        <div
          className="text-xs overflow-x-auto [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:text-[#1A1A1A] [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[#333333]"
          onClick={handleSelectTableAnn}
          dangerouslySetInnerHTML={{ __html: sanitizeTableHtml(tableHtml) }}
        />
      );
    },
    [railByPara, handleSelectTableAnn],
  );

  const innerContent = (
    <>
      <style>{`
        .ann-flash {
          animation: annFlash 1.5s ease-out;
        }
        @keyframes annFlash {
          0% { box-shadow: 0 0 0 3px rgba(26,102,251,0.6); transform: scale(1.02); }
          50% { box-shadow: 0 0 0 6px rgba(26,102,251,0.2); transform: scale(1); }
          100% { box-shadow: 0 0 0 0 transparent; transform: scale(1); }
        }
        /* 文件审核抽屉：自定义滑入滑出动画（覆盖 tailwindcss-animate 默认） */
        @keyframes reviewDrawerIn {
          from { transform: translateX(102%); }
          to { transform: translateX(0); }
        }
        @keyframes reviewDrawerOut {
          from { transform: translateX(0); }
          to { transform: translateX(102%); }
        }
        .review-drawer[data-state='open'] {
          animation: reviewDrawerIn 1s cubic-bezier(0.22, 1, 0.36, 1) both;
          will-change: transform;
        }
        .review-drawer[data-state='closed'] {
          animation: reviewDrawerOut 0.6s cubic-bezier(0.55, 0, 0.55, 0.2) both;
          will-change: transform;
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
                className="flex items-center gap-1.5 text-xs font-medium text-[#1a66fb] hover:text-[#0f56e0] px-2.5 py-1.5 rounded-lg hover:bg-[#F0F5FF] transition-colors disabled:opacity-50"
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
            {/* Sheet 模式下组件自带右上角关闭按钮，避免重复；inline 模式无自带按钮 */}
            {inline && (
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg hover:bg-[#F5F5F5] transition"
              >
                <X className="w-4 h-4 text-[#8A8A8A]" strokeWidth={2} />
              </button>
            )}
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
                    ? 'bg-[#1a66fb] text-white'
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

      {/* Content：正文列 + 右侧批注栏（Word 式）+ SVG 引线 */}
      <div
        className={`overflow-y-auto px-5 py-4 ${inline ? 'flex-1' : 'h-[calc(100vh-130px)]'}`}
        onScroll={() => setPendingSel(null)}
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
          <div ref={wrapRef} className="relative flex items-start gap-4">
            {/* 正文列：Word 纸张式排版（A4 白纸 + 宋体 + 页边距 + 阴影） */}
            <div className="min-w-0 flex-1" onMouseUp={handleContentMouseUp}>
              {/* 统一保存栏：有改动时吸顶显示 */}
              {dirty > 0 && (
                <div className="sticky top-0 z-10 mx-auto mb-2 flex max-w-[794px] items-center justify-between rounded-lg border border-[#1a66fb] bg-[#F0F5FF] px-3 py-2 shadow-sm">
                  <span className="text-xs text-[#1a66fb]">
                    已修改 {dirty} 处（保存将存为新版本）
                  </span>
                  <div className="flex gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 px-2.5 text-xs"
                      disabled={savingEdits}
                      onClick={handleDiscardEdits}
                    >
                      放弃修改
                    </Button>
                    <Button
                      size="sm"
                      className="h-7 px-2.5 text-xs"
                      disabled={savingEdits}
                      onClick={handleSaveEdits}
                    >
                      {savingEdits ? '保存中…' : '保存'}
                    </Button>
                  </div>
                </div>
              )}
              {editError && (
                <div className="mx-auto mb-2 max-w-[794px] text-xs text-[#FF4D4F]">
                  {editError}
                </div>
              )}
              <div
                className="mx-auto w-full max-w-[794px] border border-[#C9C9C9] bg-white px-[72px] py-[64px] shadow-[0_4px_24px_rgba(0,0,0,0.14)]"
                style={{
                  fontFamily: "'SimSun', '宋体', 'Times New Roman', serif",
                }}
              >
                {canEdit && onEditDocument ? (
                  <DocxParagraphEditor
                    key={`${fileId}-${resetKey}`}
                    paragraphs={content.paragraphs}
                    targetsByPara={targetsByPara}
                    onAnchorClick={handleAnchorClick}
                    renderAtomic={renderAtomicBlock}
                    editorRef={editorRef}
                    onBlocksChange={handleEditorDirty}
                  />
                ) : (
                  <div className="space-y-2">
                    {content.paragraphs.map((para) => {
                      const targets = targetsByPara.get(para.index) || [];
                      const firstAi = (railByPara.get(para.index) || []).find(
                        (i) => i.kind === 'ai',
                      );

                      let paraElement: React.ReactNode;
                      if (para.type === 'heading') {
                        const HeadingTag = (
                          para.heading_level && para.heading_level <= 3
                            ? `h${para.heading_level + 1}`
                            : 'h3'
                        ) as 'h2' | 'h3' | 'h4';
                        paraElement = (
                          <HeadingTag className="text-[15px] font-bold text-[#1A1A1A] mt-5 mb-2">
                            {renderHighlighted(
                              para.text,
                              targets,
                              handleAnchorClick,
                            )}
                          </HeadingTag>
                        );
                      } else if (para.type === 'table') {
                        let tableHtml = para.text;
                        if (firstAi?.ann) {
                          tableHtml = highlightInTableHtml(
                            tableHtml,
                            getMatchedText(firstAi.ann),
                            firstAi.color,
                            firstAi.key,
                          );
                        }
                        // 手动批注：表格内与正文同款 <mark> 高亮（anchor_start 消歧）
                        for (const it of railByPara.get(para.index) || []) {
                          if (it.kind !== 'comment') continue;
                          const at = (it.comment?.anchor_text || '').trim();
                          if (!at) continue;
                          tableHtml = highlightInTableByAnchor(
                            tableHtml,
                            at,
                            it.comment?.anchor_start,
                            it.color,
                            it.key,
                          );
                        }
                        paraElement = (
                          <div
                            className="text-xs overflow-x-auto [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:text-[#1A1A1A] [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[#333333]"
                            onClick={handleSelectTableAnn}
                            dangerouslySetInnerHTML={{
                              __html: sanitizeTableHtml(tableHtml),
                            }}
                          />
                        );
                      } else if (para.type === 'image') {
                        paraElement = (
                          <div className="py-1 text-[13px] italic text-[#8A8A8A]">
                            {renderHighlighted(
                              para.text,
                              targets,
                              handleAnchorClick,
                            )}
                          </div>
                        );
                      } else {
                        paraElement = (
                          <p
                            className="text-[14px] leading-[2] text-justify text-[#333333]"
                            style={{ textIndent: '2em' }}
                          >
                            {renderHighlighted(
                              para.text,
                              targets,
                              handleAnchorClick,
                            )}
                          </p>
                        );
                      }

                      return (
                        <div
                          key={para.index}
                          data-para-index={para.index}
                          className="relative py-0.5"
                        >
                          {paraElement}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* 右侧批注栏 */}
            <div className="relative shrink-0" style={{ width: RAIL_W }}>
              {railItems.map((it) => {
                const top = layout.cards[it.key];
                return (
                  <div
                    key={it.key}
                    id={`rail-${it.key}`}
                    data-card-key={it.key}
                    className="absolute left-0 w-full"
                    style={{
                      top: top ?? 0,
                      visibility: top === undefined ? 'hidden' : 'visible',
                    }}
                  >
                    {it.kind === 'ai' ? (
                      <AiCard
                        num={it.num!}
                        ann={it.ann!}
                        selected={selectedKey === it.key}
                        onSelect={() => handleAnchorClick(it.key)}
                      />
                    ) : (
                      <CommentCard
                        comment={it.comment!}
                        author={commentAuthors?.[it.comment!.user_id || '']}
                        selected={selectedKey === it.key}
                        onSelect={() => handleAnchorClick(it.key)}
                        canDelete={
                          !!onDeleteComment &&
                          it.comment!.user_id === currentUserId
                        }
                        onDelete={() => handleDeleteComment(it.comment!.id)}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* SVG 引线层 */}
            {layout.w > 0 && (
              <svg
                className="pointer-events-none absolute left-0 top-0"
                width={layout.w}
                height={layout.h}
              >
                {railItems.map((it) => {
                  const a = layout.anchors[it.key];
                  const top = layout.cards[it.key];
                  if (!a || top === undefined) return null;
                  const x2 = layout.w - RAIL_W;
                  const y2 = top + 16;
                  const startX = Math.min(a.x + 4, x2 - 16);
                  const d = `M ${startX} ${a.y} C ${startX + (x2 - startX) * 0.4} ${a.y}, ${x2 - (x2 - startX) * 0.4} ${y2}, ${x2} ${y2}`;
                  return (
                    <path
                      key={it.key}
                      d={d}
                      fill="none"
                      stroke={it.color}
                      strokeWidth={1.2}
                      strokeOpacity={0.65}
                    />
                  );
                })}
              </svg>
            )}

            {/* 手动批注浮层：挂在正文容器内（absolute），贴着选中文案 */}
            {pendingSel && onAddComment && !draft && (
              <div
                className="absolute z-[9999]"
                style={{ left: pendingSel.x, top: pendingSel.y }}
              >
                <button
                  onClick={() =>
                    setDraft({
                      x: Math.max(
                        8,
                        Math.min(pendingSel.x - 100, (layout.w || 800) - 310),
                      ),
                      y: Math.min(pendingSel.y + 8, (layout.h || 800) - 220),
                      text: pendingSel.text,
                      paraIndex: pendingSel.paraIndex,
                      anchorStart: pendingSel.anchorStart,
                      note: '',
                    })
                  }
                  className="flex items-center gap-1 rounded-full border border-[#D6E2FF] bg-white px-2.5 py-1.5 text-xs font-medium text-[#1a66fb] shadow-lg hover:bg-[#F0F5FF]"
                >
                  <Plus className="h-3 w-3" strokeWidth={2.5} />
                  添加批注
                </button>
              </div>
            )}
            {draft && (
              <div
                className="absolute z-[10000] w-72 rounded-xl border border-[#E5E5E5] bg-white p-2.5 shadow-xl"
                style={{ left: draft.x, top: draft.y }}
              >
                <div className="mb-1.5 truncate rounded border-l-2 border-[#1a66fb] bg-[#F5F8FF] px-1.5 py-0.5 text-[10px] text-[#666]">
                  锚点：{draft.text}
                </div>
                <Textarea
                  autoFocus
                  value={draft.note}
                  onChange={(e) => setDraft({ ...draft, note: e.target.value })}
                  placeholder="输入批注内容…"
                  className="min-h-[60px] text-xs"
                />
                <div className="mt-1.5 flex justify-end gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-xs"
                    onClick={() => setDraft(null)}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    className="h-7 px-2.5 text-xs"
                    disabled={!draft.note.trim() || submittingComment}
                    onClick={submitComment}
                  >
                    {submittingComment ? '提交中…' : '确定'}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* 未定位 AI 标注 + 未定位/普通手动批注（边栏兜底列表） */}
        {!loading &&
          !error &&
          content &&
          (unmatched.ai.length > 0 ||
            unmatched.comments.length > 0 ||
            unmatched.plainComments.length > 0) && (
            <div className="mt-6 pt-4 border-t-2 border-[#E8E8E8]">
              <div className="text-sm font-bold text-[#1A1A1A] mb-3">
                📋 其他批注（
                {unmatched.ai.length +
                  unmatched.comments.length +
                  unmatched.plainComments.length}{' '}
                条）
              </div>
              <div className="space-y-2">
                {unmatched.ai.map((ann, i) => (
                  <AiCard
                    key={`unmatched-ai-${i}`}
                    num={
                      railItems.filter((x) => x.kind === 'ai').length + i + 1
                    }
                    ann={ann}
                    unmatched
                    selected={false}
                    onSelect={() => {}}
                  />
                ))}
                {[...unmatched.comments, ...unmatched.plainComments].map(
                  (c) => (
                    <CommentCard
                      key={c.id}
                      comment={c}
                      author={commentAuthors?.[c.user_id || '']}
                      selected={false}
                      onSelect={() => {}}
                      canDelete={
                        !!onDeleteComment && c.user_id === currentUserId
                      }
                      onDelete={() => handleDeleteComment(c.id)}
                    />
                  ),
                )}
              </div>
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
        className="review-drawer max-w-full overflow-hidden bg-white p-0 text-[#1A1A1A]"
        style={{ width: '72vw', maxWidth: '1200px' }}
      >
        {innerContent}
      </SheetContent>
    </Sheet>
  );
}
