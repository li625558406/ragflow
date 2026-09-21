import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useFileBlob } from '@/hooks/use-file-blob';
import type { FlowDocRun } from '@/services/flow-service';
import api from '@/utils/api';
import request from '@/utils/next-request';
import { renderAsync } from 'docx-preview';
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
  Pencil,
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
import {
  applyDocxPageLazy,
  highlightDocxRanges,
  instantFocusScroll,
  type DocxHighlightItem,
} from './docx-highlight';
import DocxParagraphEditor, { collectEditorOps } from './docx-paragraph-editor';
import { parseTableCells, type TableCellInfo } from './docx-table-utils';
import {
  highlightInTableByAnchor,
  highlightInTableHtml,
  normalizeForMatch,
  sanitizeTableHtml,
} from './docx-view-utils';
import { FixActions, FixDiffView } from './review-fix-diff';

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
  /** 批注级别 high/medium/low（存量无值视为 medium=一般） */
  severity?: string;
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
    /** 批注级别 high/medium/low（创建时选择，默认 medium） */
    severity?: string;
  }) => Promise<void> | void;
  /** 删除手动批注（仅作者自己的批注显示删除按钮）；不传则不启用 */
  onDeleteComment?: (commentId: string) => Promise<void> | void;
  /** 删除 AI 批注（物理删 DB 行，后端与状态修改同闸）；不传则不显示删除按钮 */
  onDeleteAnnotation?: (annotationId: string) => Promise<void> | void;
  /** 当前登录用户 id（判断批注删除按钮可见性） */
  currentUserId?: string;
  /** 是否开放正文编辑（整篇 contentEditable，Word 式改字/回车分段/退格并段） */
  canEdit?: boolean;
  /** 提交文档改动（改写/新增/删除段落，保存为新版本后由父级刷新预览） */
  onEditDocument?: (ops: {
    edits: Array<{ paraIndex: number; newText: string }>;
    deletes: number[];
    inserts: Array<{ afterParaIndex: number; newText: string }>;
    tableEdits: Array<{
      paraIndex: number;
      row: number;
      col: number;
      newText: string;
      runs?: FlowDocRun[];
    }>;
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

// 人工批注专属绿色系：AI 批注按级别配色（红/琥珀/蓝），人工批注整体绿色
// （正文 mark / 边栏卡 / 列表条目三处统一）——绿=人工、彩=AI 一眼区分。
// 级别（严重/一般/提示）在人工卡上仅保留徽标，不再占用卡片主色。
const MANUAL_STYLE = { border: '#67C23A', bg: '#F6FFED', text: '#388E3C' };

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

/** 跨行摘录的分行归一化（matched_text 含 \n 且非空归一化行 ≥2 才可走序列通道） */
function multilineLineNorms(matchedText: string): string[] {
  if (!matchedText || !matchedText.includes('\n')) return [];
  return matchedText
    .split('\n')
    .map((l) => normalizeForMatch(l))
    .filter((l) => l.length >= 2);
}

/**
 * 跨行摘录序列匹配：LLM 摘录常为多行拼接（matched_text 含 \n），单段 includes
 * 必然失配。按行（过滤空行）在段落序列上滑窗：连续 N 段中第 i 段包含第 i 行
 * （normalizeForMatch 同口径），与后端修复轮 _apply_multiline_patch 连续段落
 * 序列定位同构。唯命中闸：命中序列数 ≠1 → 返回 -1（宁可未定位不错位）。
 * 返回基段落（首行所在段）index。
 */
function matchMultiline(
  paragraphs: { index: number; text: string }[],
  matchedText: string,
): number {
  const lines = multilineLineNorms(matchedText);
  if (lines.length < 2) return -1;
  const paraNorms = paragraphs.map((p) => normalizeForMatch(p.text));
  let base = -1;
  let hits = 0;
  for (let b = 0; b + lines.length <= paraNorms.length; b++) {
    let ok = true;
    for (let i = 0; i < lines.length; i++) {
      if (!paraNorms[b + i].includes(lines[i])) {
        ok = false;
        break;
      }
    }
    if (ok) {
      hits++;
      if (hits > 1) return -1;
      base = paragraphs[b].index;
    }
  }
  return hits === 1 ? base : -1;
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

// 批注栏宽度：抽屉改半屏后收窄，给正文纸张留更多空间
const RAIL_W = 210;

// ── Cards ──

function AiCard({
  num,
  ann,
  unmatched,
  selected,
  onSelect,
  canDelete,
  onDelete,
  fileId,
}: {
  num: number;
  ann: Annotation;
  unmatched?: boolean;
  selected: boolean;
  onSelect: () => void;
  canDelete?: boolean;
  onDelete?: () => void;
  /** 有值且 status='fixed' 时展示回退/确认操作（数据来自 state 轮询） */
  fileId?: string;
}) {
  const cfg = SEVERITY_CONFIG[ann.severity] || SEVERITY_CONFIG.low;
  const Icon = cfg.icon;
  const issue = ann.issue || ann.problem || ann.description || '';
  const suggestion = ann.suggestion || ann.recommendation || ann.advice || '';
  const annType = ann.type || ann.category || '';
  const mt = getMatchedText(ann);
  const hasPatch = !!(ann.patch && (ann.patch.find || ann.patch.replace));
  return (
    <div
      id={`annotation-${num}`}
      onClick={onSelect}
      className={`group cursor-pointer rounded-md p-2.5 text-xs transition-all duration-300 ${
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
        <span className="ml-auto shrink-0 rounded bg-[#1a66fb] px-1 py-px text-[10px] font-bold text-white">
          AI
        </span>
        {/* 修复标记：fixed=AI 修复轮已修（绿）；resolved+有补丁=已确认保留 */}
        {ann.status === 'fixed' && (
          <span className="shrink-0 rounded bg-[#67C23A] px-1 py-px text-[10px] font-bold text-white">
            已修复
          </span>
        )}
        {ann.status === 'resolved' && hasPatch && (
          <span className="shrink-0 rounded bg-[#388E3C] px-1 py-px text-[10px] font-bold text-white">
            已确认
          </span>
        )}
        {canDelete && onDelete && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (window.confirm('确定删除这条 AI 批注？删除后不可恢复。'))
                onDelete();
            }}
            title="删除批注"
            className="shrink-0 rounded p-0.5 text-[#bbb] opacity-0 transition-opacity hover:bg-[#FFF2F0] hover:text-[#FF4D4F] focus:opacity-100 group-hover:opacity-100"
          >
            <Trash2 className="h-3 w-3" strokeWidth={2} />
          </button>
        )}
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
      {hasPatch && <FixDiffView patch={ann.patch} />}
      {ann.status === 'fixed' && fileId && !!ann.id && (
        // stopPropagation：卡片 onClick 是定位跳转，不能让按钮点击触发它
        <div onClick={(e) => e.stopPropagation()}>
          <FixActions fileId={fileId} annotationId={String(ann.id)} />
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
  // 级别配色与 AI 卡同源 SEVERITY_CONFIG；存量无 severity 视为 medium=一般
  const scfg =
    SEVERITY_CONFIG[comment.severity || 'medium'] || SEVERITY_CONFIG.medium;
  return (
    <div
      onClick={onSelect}
      className={`group cursor-pointer rounded-md p-2.5 text-xs transition-all duration-300 ${
        selected ? 'ring-2 ring-[#1a66fb] shadow-lg' : ''
      }`}
      style={{
        backgroundColor: MANUAL_STYLE.bg,
        border: `1px solid #D9F2DC`,
        borderLeft: `3px solid ${MANUAL_STYLE.border}`,
      }}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <MessageSquare
          className="w-3.5 h-3.5 shrink-0"
          style={{ color: MANUAL_STYLE.text }}
          strokeWidth={2}
        />
        <span
          className="truncate font-semibold"
          style={{ color: MANUAL_STYLE.text }}
        >
          {author || comment.user_id || '批注'}
        </span>
        <span
          className="shrink-0 rounded px-1 py-px text-[10px] font-semibold"
          style={{ color: scfg.textColor, backgroundColor: scfg.bg }}
        >
          {{ high: '严重', medium: '一般', low: '提示' }[
            comment.severity || 'medium'
          ] || '一般'}
        </span>
        <span
          className="shrink-0 rounded px-1 py-px text-[10px] font-bold text-white"
          style={{ backgroundColor: MANUAL_STYLE.border }}
        >
          人工
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
  onDeleteAnnotation,
  currentUserId,
  canEdit,
  onEditDocument,
}: ReviewPanelProps) {
  const [content, setContent] = useState<FileContent | null>(null);
  // 当前 content 实际对应的 fileId：保存后 fileId 变化→编辑器重挂载早于新内容
  // 到达，用 loadedFileId 门控，避免编辑器以旧文档段落为基线冻结
  const [loadedFileId, setLoadedFileId] = useState('');
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
    severity: string;
  } | null>(null);
  const [submittingComment, setSubmittingComment] = useState(false);
  // AI 批注删除的乐观移除：成功后父级经 state 端点刷新会自然去掉该行，
  // removedIds 只覆盖「刷新到达前」的窗口；失败回滚。跨所有来源一致生效
  // （annotationMap / unmatched / 边栏卡 / 批注列表全走 visibleAnnotations）。
  const [removedAnnIds, setRemovedAnnIds] = useState<Set<string>>(new Set());
  const visibleAnnotations = useMemo(
    () => annotations.filter((a) => !(a.id && removedAnnIds.has(String(a.id)))),
    [annotations, removedAnnIds],
  );
  // 「其他批注」兜底提示：点击未定位条目（批注列表区）时给出无法定位说明 + 高亮
  const [unmatchedHintKey, setUnmatchedHintKey] = useState<string | null>(null);
  // Word 式整篇编辑：Lexical 编辑器承载正文，保存时按模型 diff 出
  // 改写/新增/删除三类操作；dirty 为改动处数，resetKey 用于放弃修改时重挂载
  const editorRef = useRef<LexicalEditor | null>(null);
  const diffTimer = useRef<number | undefined>(undefined);
  const [dirty, setDirty] = useState(0);
  const [resetKey, setResetKey] = useState(0);
  const [savingEdits, setSavingEdits] = useState(false);
  const [editError, setEditError] = useState('');
  // 工具栏 portal 宿主：吸顶空容器 ref 回调，DocxToolbar 渲染到该 DOM
  const [toolbarHost, setToolbarHost] = useState<HTMLElement | null>(null);
  const toolbarHostRef = useCallback((el: HTMLDivElement | null) => {
    setToolbarHost(el);
  }, []);
  // Word 式引线布局：锚点坐标 + 卡片 top + 画布尺寸
  const wrapRef = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<{
    cards: Record<string, number>;
    anchors: Record<string, { x: number; y: number }>;
    w: number;
    h: number;
  }>({ cards: {}, anchors: {}, w: 0, h: 0 });

  // ── docx 只读保真（docx-preview）：只读路径渲染原始文件，Word 字号/字体/
  // 表格样式/字色不丢；AI 标注/手动批注经 highlightDocxRanges 锚定
  // mark[data-anchor-key]，批注栏/引线/未定位兜底全部沿用。
  // 可编辑文件默认也进保真预览（原色审阅），点「编辑文档」才切 Lexical 旧段落
  // 编辑视图（纯文本模型，颜色/格式必然丢失），保存/放弃后回到保真预览。
  const [userEditing, setUserEditing] = useState(false);
  const editing =
    canEdit && onEditDocument && loadedFileId === fileId && userEditing;
  // 切换文件/页签后回到保真预览（编辑态只对当前已加载文件有效）
  useEffect(() => {
    setUserEditing(false);
    setUnmatchedHintKey(null);
  }, [fileId]);
  const docxFidelityCandidate = content?.file_type === 'docx' && !editing;
  const docxWrapRef = useRef<HTMLDivElement | null>(null);
  const [docxRenderFailed, setDocxRenderFailed] = useState(false);
  const [markedKeys, setMarkedKeys] = useState<Set<string>>(new Set());
  const {
    data: docxBlob,
    isLoading: docxBlobLoading,
    error: docxBlobError,
  } = useFileBlob(docxFidelityCandidate ? fileId : '');
  const docxFidelity = Boolean(
    docxFidelityCandidate && docxBlob && !docxBlobError && !docxRenderFailed,
  );
  // 容器挂载代数：renderAsync 的渲染产物不在 React state 里，任何原因导致的
  // 容器重挂（open 切换 return null、loading 闪断、文件切换）都必须重跑渲染，
  // 否则容器空白。ref callback 里无法直接进 effect deps，用 epoch 状态桥接。
  const [docxEpoch, setDocxEpoch] = useState(0);
  const docxWrapRefCb = useCallback((el: HTMLDivElement | null) => {
    docxWrapRef.current = el;
    if (el) setDocxEpoch((n) => n + 1);
  }, []);

  // 保真视图 fit-width：docx-preview 的 A4 页固定 794px 宽，文档列窄于「页+wrapper 内边距」
  // 时，docx-wrapper（flex 水平居中）对称溢出 + 外层 overflow-x-hidden ⇒ 页面两侧被裁。
  // docx-preview 无缩放渲染选项，用 CSS zoom 等比缩小到列宽：zoom 影响布局（无需另做高度
  // 补偿），且 getBoundingClientRect / 元素坐标均按缩后几何返回 ⇒ 边栏锚点测量天然一致。
  // 只在渲染完成与窗口 resize 时调用——滚动/重测路径严禁调它，否则构成反馈循环。
  const fitDocxToColumn = useCallback(() => {
    const el = docxWrapRef.current;
    const parent = el?.parentElement;
    if (!el || !parent) return;
    // 先复位再测自然内容宽。不能用 scrollWidth：docx-wrapper 是 flex 水平居中，
    // 页宽超出列宽时向两侧对称溢出，scrollWidth 只计右侧溢出 ⇒ 系统性低估
    // （实测 854 被测成 764，缩放后页面仍超列宽）。width:max-content 让容器
    // 撑到完整内容宽（含 wrapper 左右 padding），offsetWidth 即真实自然宽。
    el.style.zoom = '1';
    el.style.width = 'max-content';
    el.style.maxWidth = 'none';
    const pw = el.offsetWidth;
    const cw = parent.clientWidth;
    if (!pw || pw <= cw + 1) {
      el.style.zoom = '';
      el.style.width = '';
      el.style.maxWidth = '';
      return;
    }
    const k = cw / pw;
    el.style.zoom = String(k);
    // 固定为自然宽 pw（数值上等于 max-content 结果），缩后恰好铺满列；
    // 溢出场景 auto margin 归 0，从左缘起完整可见。
    el.style.width = `${pw}px`;
  }, []);

  // Build annotation set keyed by paragraph index — supports multiple per paragraph
  // 每条批注只归属首个匹配段落：matched_text 常命中多个段落（章标题/重复短语），
  // 不去重会让同一条批注在多个段落各生成一张边栏卡（实测 25 条批注渲染 33 张卡，
  // 「已定位」统计超过总数），docx 高亮也随之重复标蓝。
  const annotationMap = useMemo(() => {
    if (!content) return new Map<number, Annotation[]>();
    const map = new Map<number, Annotation[]>();
    const claimed = new Set<Annotation>();
    // 前置通道：跨行摘录（matched_text 含 \n 且非空行 ≥2）走连续段落序列匹配。
    // 这类批注刻意不进下方逐段匹配 —— 其 matched_text 无法整段 includes，
    // 而 keyword 策略会把多行内容的关键词误配到单个段落（错位归属）。
    for (const ann of visibleAnnotations) {
      if (multilineLineNorms(getMatchedText(ann)).length < 2) continue;
      const base = matchMultiline(content.paragraphs, getMatchedText(ann));
      if (base >= 0) {
        claimed.add(ann);
        map.set(base, [ann]);
      }
    }
    for (const para of content.paragraphs) {
      const matches = visibleAnnotations.filter(
        (ann) => !claimed.has(ann) && matchAnnotation(para.text, ann),
      );
      if (matches.length > 0) {
        matches.sort(
          (a, b) => getMatchedText(b).length - getMatchedText(a).length,
        );
        matches.forEach((ann) => claimed.add(ann));
        map.set(para.index, matches);
      }
    }
    return map;
  }, [content, visibleAnnotations]);

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
            // 正文 mark 统一绿色（人工专属色），级别只留在卡片徽标
            color: MANUAL_STYLE.border,
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

  // 保真模式只渲染成功锚定 mark 的项（未命中的进底部兜底列表）；旧视图全量
  const activeRailItems = useMemo(
    () =>
      docxFidelity
        ? railItems.filter((it) => markedKeys.has(it.key))
        : railItems,
    [docxFidelity, railItems, markedKeys],
  );

  // blob 渲染 effect 取 railItems 快照用（railItems 晚于 blob 到达时兜底补锚）
  const railItemsRef = useRef<RailItem[]>(railItems);
  railItemsRef.current = railItems;

  // railItem → highlightDocxRanges 入参（与旧视图 targetsByPara 同源：首个 AI
  // 标注 + 首个手动批注的文本/颜色/锚点偏移）
  const toHighlightItems = (items: RailItem[]): DocxHighlightItem[] => {
    const out: DocxHighlightItem[] = [];
    for (const it of items) {
      const raw =
        it.kind === 'ai'
          ? getMatchedText(it.ann!)
          : (it.comment?.anchor_text || '').trim();
      if (!raw) continue;
      // 跨行摘录：完整分行走 highlightDocxRanges 序列通道（唯一连续段落序列
      // 才插 mark）；首行文本作 text 兜底（序列未命中时按单段常规匹配）
      const hasNl = raw.includes('\n');
      const text = hasNl
        ? raw
            .split('\n')
            .map((s) => s.trim())
            .find(Boolean) || ''
        : raw;
      if (!text) continue;
      out.push({
        text,
        lines: hasNl ? raw.split('\n') : undefined,
        key: it.key,
        color: it.color,
        start:
          it.kind === 'comment' ? (it.comment?.anchor_start ?? null) : null,
      });
    }
    return out;
  };

  // blob 到达：清容器 → renderAsync 保真渲染 → 屏外页懒渲染 → 按当前 railItems
  // 插入 mark[data-anchor-key]。渲染失败降级回旧段落视图。
  // deps 必须含 docxEpoch：渲染产物不留在 state 里，而容器可能被三件事重挂——
  // 关闭时 return null（重开时同 fileId blob 命中 query 缓存引用不变、content 仍在
  // state，effect 其他 deps 全不变）、loading 闪断（内容请求 setLoading(true) 卸载
  // body，与渲染竞争）、文件切换。ref 回调把每次容器挂载折算成 epoch 递增，
  // 任何重挂都强制重跑渲染，否则容器空白（版本历史二次查看白屏根因）。
  useEffect(() => {
    if (!docxFidelityCandidate || !docxBlob || !docxWrapRef.current) return;
    const el = docxWrapRef.current;
    setDocxRenderFailed(false);
    setMarkedKeys(new Set());
    el.innerHTML = '';
    renderAsync(docxBlob, el, undefined, { inWrapper: true, breakPages: true })
      .then(() => {
        if (!el.isConnected) return; // 容器已被重挂/卸载：丢弃本轮 stale 渲染产物
        fitDocxToColumn();
        applyDocxPageLazy(el);
        setMarkedKeys(
          highlightDocxRanges(el, toHighlightItems(railItemsRef.current)),
        );
      })
      .catch(() => {
        setDocxRenderFailed(true);
        setMarkedKeys(new Set());
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docxBlob, docxFidelityCandidate, docxEpoch]);

  // railItems 变化（annotations/comments 异步到达）：只补插新增 key 的 mark，
  // 已锚定的不重插；highlightDocxRanges 在 setState 外执行（StrictMode 下
  // updater 双调用会重复改 DOM）
  useEffect(() => {
    if (!docxFidelity || !docxWrapRef.current) return;
    const fresh = toHighlightItems(railItems).filter(
      (it) => !markedKeys.has(it.key),
    );
    if (!fresh.length) return;
    const added = highlightDocxRanges(docxWrapRef.current, fresh);
    if (!added.size) return;
    setMarkedKeys((prev) => new Set([...prev, ...added]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docxFidelity, railItems, markedKeys]);

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

  // 表格 diff 基线：与初始灌入共用 parseTableCells（同源）；解析失败的表不出现在
  // Map 里 → diffBlocks 自动跳过该表改动（保护）
  const tableBaselines = useMemo(() => {
    const m = new Map<number, TableCellInfo[]>();
    if (!content) return m;
    for (const p of content.paragraphs) {
      if (p.type !== 'table') continue;
      const cells = parseTableCells(p.text);
      if (cells.length) m.set(p.index, cells);
    }
    return m;
  }, [content]);

  // 未匹配到段落的项（边栏下方兜底展示）
  const unmatched = useMemo(() => {
    const matchedAi = new Set(
      activeRailItems
        .filter((i) => i.kind === 'ai')
        .map((i) => getMatchedText(i.ann!)),
    );
    const matchedCm = new Set(
      activeRailItems
        .filter((i) => i.kind === 'comment')
        .map((i) => i.comment!.id),
    );
    return {
      ai: visibleAnnotations.filter((a) => !matchedAi.has(getMatchedText(a))),
      comments: (comments || []).filter(
        (c) => (c.anchor_text || '').trim() && !matchedCm.has(c.id),
      ),
      plainComments: (comments || []).filter(
        (c) => !(c.anchor_text || '').trim(),
      ),
    };
  }, [activeRailItems, visibleAnnotations, comments]);

  // 批注列表条目（置顶列表区，与文档边栏批注同时存在）：已定位在前（级别
  // 高→中→低降序，同级别内保持文档序）、未定位在后；文本取问题描述（AI）/
  // 批注内容（人工），悬浮 title 给全文（含建议）
  const listEntries = useMemo(() => {
    const sevOf = (s: string) =>
      (['high', 'medium', 'low'].includes(s) ? s : 'medium') as
        | 'high'
        | 'medium'
        | 'low';
    const items: {
      key: string;
      num?: number;
      sev: 'high' | 'medium' | 'low';
      typeLabel: string;
      author: string;
      text: string;
      title: string;
      matched: boolean;
      source: 'ai' | 'human';
      /** AI 批注的后端 id（有值且传了 onDeleteAnnotation 才显示删除按钮） */
      annotationId?: string;
      /** 修复标记（仅展示）：fixed=已修复；resolved+有补丁=已确认保留 */
      fixState?: 'fixed' | 'confirmed';
      /** 人工批注 id + 批注人（传了 onDeleteComment 且是本人批注才显示删除按钮） */
      commentId?: string;
      commentUserId?: string;
    }[] = [];
    const fixStateOf = (a: Annotation): 'fixed' | 'confirmed' | undefined => {
      if (a.status === 'fixed') return 'fixed';
      const hasPatch = !!(a.patch && (a.patch.find || a.patch.replace));
      if (a.status === 'resolved' && hasPatch) return 'confirmed';
      return undefined;
    };
    for (const it of activeRailItems) {
      if (it.kind === 'ai') {
        const ann = it.ann!;
        const issue = ann.issue || ann.problem || ann.description || '';
        const suggestion =
          ann.suggestion || ann.recommendation || ann.advice || '';
        const annType = ann.type || ann.category || '';
        items.push({
          key: it.key,
          num: it.num,
          sev: sevOf(ann.severity),
          typeLabel: TYPE_LABELS[annType] || annType,
          author: 'AI',
          text: issue || getMatchedText(ann),
          title: suggestion ? `${issue}\n建议：${suggestion}` : issue,
          matched: true,
          source: 'ai',
          annotationId: ann.id ? String(ann.id) : undefined,
          fixState: fixStateOf(ann),
        });
      } else {
        const c = it.comment!;
        items.push({
          key: it.key,
          sev: sevOf(c.severity || 'medium'),
          typeLabel: '',
          author: commentAuthors?.[c.user_id || ''] || c.user_id || '批注',
          text: c.content,
          title: c.content,
          matched: true,
          source: 'human',
          commentId: c.id,
          commentUserId: c.user_id,
        });
      }
    }
    const matchedAiCount = activeRailItems.filter(
      (i) => i.kind === 'ai',
    ).length;
    unmatched.ai.forEach((ann, i) => {
      const issue = ann.issue || ann.problem || ann.description || '';
      const suggestion =
        ann.suggestion || ann.recommendation || ann.advice || '';
      const annType = ann.type || ann.category || '';
      items.push({
        key: `unmatched-ai-${i}`,
        num: matchedAiCount + i + 1,
        sev: sevOf(ann.severity),
        typeLabel: TYPE_LABELS[annType] || annType,
        author: 'AI',
        text: issue || getMatchedText(ann),
        title: suggestion ? `${issue}\n建议：${suggestion}` : issue,
        matched: false,
        source: 'ai',
        annotationId: ann.id ? String(ann.id) : undefined,
        fixState: fixStateOf(ann),
      });
    });
    for (const c of [...unmatched.comments, ...unmatched.plainComments]) {
      items.push({
        key: c.id,
        sev: sevOf(c.severity || 'medium'),
        typeLabel: '',
        author: commentAuthors?.[c.user_id || ''] || c.user_id || '批注',
        text: c.content,
        title: c.content,
        matched: false,
        source: 'human',
        commentId: c.id,
        commentUserId: c.user_id,
      });
    }
    // 级别降序 高→中→低：稳定排序（同级别内保持原文档序）；已定位整体在未定位之前
    const sevRank = { high: 0, medium: 1, low: 2 } as const;
    return [...items].sort(
      (a, b) =>
        (a.matched === b.matched ? 0 : a.matched ? -1 : 1) ||
        sevRank[a.sev] - sevRank[b.sev],
    );
  }, [activeRailItems, unmatched, commentAuthors]);

  // 常驻抽屉（非 inline 模式）：Esc 快捷关闭（与范本实时预览抽屉同款）
  useEffect(() => {
    if (inline || !open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [inline, open, onClose]);

  const handleAnchorClick = useCallback(
    (key: string) => {
      setSelectedKey(key);
      setUnmatchedHintKey(null);
      // 跳转文档中对应批注位置：mark 优先，表格内等无 mark 场景按段落兜底
      //（与布局测量同款取锚思路）。instantFocusScroll 强制真实渲染懒加载分页后
      // 瞬时居中——smooth 滚动会被上方懒渲染页高度漂移带偏。
      const it = railItems.find((i) => i.key === key);
      const el =
        wrapRef.current?.querySelector<HTMLElement>(
          `[data-anchor-key="${key}"]`,
        ) ??
        (it
          ? wrapRef.current?.querySelector<HTMLElement>(
              `[data-para-index="${it.paraIndex}"]`,
            )
          : null);
      if (el) {
        instantFocusScroll(el);
        el.classList.remove('ann-flash');
        void el.offsetWidth; // 重启动画
        el.classList.add('ann-flash');
      }
    },
    [railItems],
  );

  // Listen for annotation selection events (from table-HTML highlight clicks)
  useEffect(() => {
    const handler = (e: Event) => {
      const num = (e as CustomEvent).detail as number;
      handleAnchorClick(`ai-${num}`);
    };
    window.addEventListener('annotation-select', handler);
    return () => window.removeEventListener('annotation-select', handler);
  }, [handleAnchorClick]);

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
          setLoadedFileId(fileId);
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
      for (const it of activeRailItems) {
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
      const sorted = [...activeRailItems].sort(
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
    // 锚点坐标是「mark 视口位置 − wrap 视口位置」，而滚动发生在 wrap 内层的
    // overflow-auto 容器里：wrap 不动、mark 动 ⇒ 不随滚动重测，边栏卡必然错位。
    // 另外 content-visibility 懒渲染让页高在滚动中持续从估算值变真实值。
    // 用 document 级 capture 滚动监听（不依赖识别具体滚动容器）+ ResizeObserver
    // （页真实渲染改变 wrap 高度）双通道触发持续重测；rAF 节流。
    let scrollRaf = 0;
    const onScroll = () => {
      if (scrollRaf) return;
      scrollRaf = requestAnimationFrame(measure);
    };
    const onResize = () => {
      fitDocxToColumn();
      measure();
    };
    const ro = new ResizeObserver(() => measure());
    if (wrapRef.current) ro.observe(wrapRef.current);
    document.addEventListener('scroll', onScroll, true);
    const raf = requestAnimationFrame(measure);
    const t = setTimeout(measure, 120); // 字体/图片稳定后的二次校准
    window.addEventListener('resize', onResize);
    return () => {
      cancelAnimationFrame(raf);
      cancelAnimationFrame(scrollRaf);
      clearTimeout(t);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('scroll', onScroll, true);
      ro.disconnect();
    };
  }, [
    content,
    activeRailItems,
    open,
    fileId,
    fileName,
    visibleAnnotations,
    comments,
    fitDocxToColumn,
  ]);

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
        severity: draft.severity,
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

  // 删除 AI 批注：乐观移除（列表/边栏/统计即时减少），失败回滚并提示
  const handleDeleteAnnotation = useCallback(
    async (annotationId: string) => {
      if (!onDeleteAnnotation) return;
      setRemovedAnnIds((prev) => new Set(prev).add(annotationId));
      try {
        await onDeleteAnnotation(annotationId);
      } catch (e: any) {
        setRemovedAnnIds((prev) => {
          const next = new Set(prev);
          next.delete(annotationId);
          return next;
        });
        window.alert(e?.message || '删除失败，请稍后重试');
      }
    },
    [onDeleteAnnotation],
  );

  // 编辑器内容变化 → 防抖后 diff 出改动处数（模型级，不碰 DOM）
  const handleEditorDirty = useCallback(
    (blocks: EditorBlock[]) => {
      if (!canEdit || !content) return;
      window.clearTimeout(diffTimer.current);
      diffTimer.current = window.setTimeout(() => {
        const ops = diffBlocks(blocks, content.paragraphs, tableBaselines);
        if ('error' in ops) {
          setDirty(0);
          setEditError(ops.error || '当前改动无法保存');
          return;
        }
        setDirty(ops.count);
        setEditError('');
      }, 250);
    },
    [canEdit, content, tableBaselines],
  );

  // 保存：模型 diff 全部改动提交父级写新版本，成功后由新内容重挂载编辑器
  const handleSaveEdits = useCallback(async () => {
    if (!onEditDocument || savingEdits || !editorRef.current || !content)
      return;
    const ops = collectEditorOps(
      editorRef.current,
      content.paragraphs,
      tableBaselines,
    );
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
  }, [content, onEditDocument, savingEdits, tableBaselines]);

  // 放弃修改：丢弃浏览器侧改动并退出编辑，回到保真预览
  const handleDiscardEdits = useCallback(() => {
    window.clearTimeout(diffTimer.current);
    setDirty(0);
    setEditError('');
    setResetKey((k) => k + 1);
    setUserEditing(false);
  }, []);

  // Download annotated docx
  const handleDownload = async () => {
    if (!fileId || !visibleAnnotations.length) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const res = await request.post(
        api.annotateFile(fileId),
        { annotations: visibleAnnotations },
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
    for (const ann of visibleAnnotations) {
      if (bySeverity[ann.severity as keyof typeof bySeverity] !== undefined) {
        bySeverity[ann.severity as keyof typeof bySeverity]++;
      }
    }
    const matched = activeRailItems.filter((i) => i.kind === 'ai').length;
    return { matched, total: visibleAnnotations.length, bySeverity };
  }, [visibleAnnotations, activeRailItems]);

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
      `}</style>
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#E5E5E5] shrink-0">
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
            {/* 保真预览默认只读：显式进入编辑（Lexical 旧段落视图，格式/颜色会简化） */}
            {canEdit &&
              onEditDocument &&
              !editing &&
              loadedFileId === fileId && (
                <button
                  onClick={() => setUserEditing(true)}
                  className="flex items-center gap-1.5 text-xs font-medium text-[#1a66fb] hover:text-[#0f56e0] px-2.5 py-1.5 rounded-lg hover:bg-[#F0F5FF] transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" strokeWidth={2} />
                  编辑文档
                </button>
              )}
            {visibleAnnotations.length > 0 && (
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
            {/* 右上角关闭按钮（抽屉与 inline 模式均渲染），交互与范本预览抽屉一致 */}
            <button
              onClick={onClose}
              className="rounded p-1 text-[#8C8C8C] transition-colors hover:bg-[#F5F5F5] hover:text-[#000000]"
            >
              <X className="h-4 w-4" />
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
      {visibleAnnotations.length > 0 && (
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

      {/* Content：顶部批注列表区 + 下方文档（含边栏批注/引线）——列表展示与批注
          展示同时存在，点击列表项跳转到文档中对应批注位置（滚动容器与范本预览
          同款：min-h-0 flex-1 + overflow-x-hidden 防横向滚动条） */}
      <div
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-4"
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

        {/* 批注列表（与下方文档边栏批注同时存在）：已定位按文档序在前、未定位在后；
            点击已定位项跳转文档中对应批注位置（闪烁高亮），未定位项给兜底提示。
            条目样式与流程页签批注模块同款紧凑列表 */}
        {!loading && !error && content && listEntries.length > 0 && (
          <div className="mb-4 rounded-lg border border-[#E8E8E8] bg-white px-3 py-2.5">
            <div className="mb-2 text-sm font-bold text-[#1A1A1A]">
              📋 批注列表（{listEntries.length} 条）
              <span className="ml-1.5 text-[10px] font-normal text-[#8A8A8A]">
                点击条目跳转到文档中的批注位置
              </span>
            </div>
            {unmatchedHintKey && (
              <div className="mb-2 rounded-md border border-[#FFE58F] bg-[#FFFBE6] px-2.5 py-2 text-xs text-[#AD6800]">
                ⚠️
                该批注无法精确定位到原文位置（预览文本与审核抽取口径差异），请对照批注内容在文档中人工查找。
              </div>
            )}
            <div className="space-y-2">
              {listEntries.map((e) => {
                const cfg = SEVERITY_CONFIG[e.sev];
                const selected = e.matched
                  ? selectedKey === e.key
                  : unmatchedHintKey === e.key;
                return (
                  <div
                    key={e.key}
                    id={`list-${e.key}`}
                    onClick={() =>
                      e.matched
                        ? handleAnchorClick(e.key)
                        : setUnmatchedHintKey(e.key)
                    }
                    className={`cursor-pointer rounded-md bg-[#F7F8FA] px-2.5 py-1.5 transition-all duration-300 ${
                      selected ? 'ring-2 ring-[#1a66fb]' : ''
                    }`}
                    style={{
                      borderLeft: `3px ${e.matched ? 'solid' : 'dashed'} ${
                        e.source === 'ai' ? cfg.border : MANUAL_STYLE.border
                      }`,
                    }}
                  >
                    <div className="flex items-center gap-1.5 text-xs text-[#888]">
                      {e.num != null ? (
                        <span
                          className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
                          style={{ backgroundColor: cfg.border }}
                        >
                          {e.num}
                        </span>
                      ) : (
                        <MessageSquare
                          className="h-3.5 w-3.5 shrink-0"
                          style={{
                            color:
                              e.source === 'ai'
                                ? cfg.border
                                : MANUAL_STYLE.text,
                          }}
                          strokeWidth={2}
                        />
                      )}
                      <span
                        className="shrink-0 rounded px-1 py-px text-[10px] font-semibold"
                        style={{
                          color: cfg.textColor,
                          backgroundColor: cfg.bg,
                        }}
                      >
                        {cfg.label}
                      </span>
                      {/* 来源 chip 实底白字：AI 蓝 / 人工绿（与边栏卡徽标同色系） */}
                      <span
                        className={`shrink-0 rounded px-1 py-px text-[10px] font-bold text-white ${
                          e.source === 'ai' ? 'bg-[#1a66fb]' : 'bg-[#67C23A]'
                        }`}
                      >
                        {e.source === 'ai' ? 'AI' : '人工'}
                      </span>
                      {/* 修复小徽标：列表保持紧凑，回退/确认操作在边栏卡与进度卡 */}
                      {e.fixState === 'fixed' && (
                        <span className="shrink-0 rounded bg-[#67C23A] px-1 py-px text-[10px] font-bold text-white">
                          已修复
                        </span>
                      )}
                      {e.fixState === 'confirmed' && (
                        <span className="shrink-0 rounded bg-[#388E3C] px-1 py-px text-[10px] font-bold text-white">
                          已确认
                        </span>
                      )}
                      {e.typeLabel && (
                        <span className="shrink-0 text-[11px] text-[#666]">
                          {e.typeLabel}
                        </span>
                      )}
                      <span className="truncate">{e.author}</span>
                      {!e.matched && (
                        <span className="ml-auto shrink-0 text-[10px] font-medium text-[#FAAD14]">
                          未定位
                        </span>
                      )}
                      {e.source === 'ai' &&
                        e.annotationId &&
                        onDeleteAnnotation && (
                          <button
                            onClick={(ev) => {
                              ev.stopPropagation();
                              if (
                                window.confirm(
                                  '确定删除这条 AI 批注？删除后不可恢复。',
                                )
                              )
                                handleDeleteAnnotation(e.annotationId!);
                            }}
                            title="删除批注"
                            className={`shrink-0 rounded p-0.5 text-[#bbb] transition-colors hover:bg-[#FFF2F0] hover:text-[#FF4D4F] ${
                              e.matched ? 'ml-auto' : ''
                            }`}
                          >
                            <Trash2 className="h-3 w-3" strokeWidth={2} />
                          </button>
                        )}
                      {e.source === 'human' &&
                        e.commentId &&
                        onDeleteComment &&
                        e.commentUserId === currentUserId && (
                          <button
                            onClick={(ev) => {
                              ev.stopPropagation();
                              if (window.confirm('确定删除这条批注？'))
                                handleDeleteComment(e.commentId!);
                            }}
                            title="删除批注"
                            className={`shrink-0 rounded p-0.5 text-[#bbb] transition-colors hover:bg-[#FFF2F0] hover:text-[#FF4D4F] ${
                              e.matched ? 'ml-auto' : ''
                            }`}
                          >
                            <Trash2 className="h-3 w-3" strokeWidth={2} />
                          </button>
                        )}
                    </div>
                    <div
                      className="mt-0.5 line-clamp-2 whitespace-pre-wrap text-sm text-[#333]"
                      title={e.title}
                    >
                      {e.text}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {!loading && !error && content && (
          <div ref={wrapRef} className="relative flex items-start gap-4">
            {/* 正文列：Word 纸张式排版（A4 白纸 + 宋体 + 页边距 + 阴影） */}
            <div className="min-w-0 flex-1" onMouseUp={handleContentMouseUp}>
              {/* Word 工具栏吸顶宿主：始终渲染（编辑模式），工具栏 portal 进来 */}
              {editing && (
                <div
                  ref={toolbarHostRef}
                  className="sticky top-0 z-10 mx-auto mb-2 max-w-[794px]"
                />
              )}
              {editError && (
                <div className="mx-auto mb-2 max-w-[794px] text-xs text-[#FF4D4F]">
                  {editError}
                </div>
              )}
              {editing ? (
                /* 编辑态：旧纸张视图（Lexical 编辑器模型与保真 DOM 不兼容） */
                <div
                  key="view-editing"
                  className="mx-auto w-full max-w-[794px] border border-[#C9C9C9] bg-white px-[72px] py-[64px] shadow-[0_4px_24px_rgba(0,0,0,0.14)]"
                  style={{
                    fontFamily: "'SimSun', '宋体', 'Times New Roman', serif",
                  }}
                >
                  {/* 编辑态表格样式只作用于编辑器分支，避免泄漏到只读静态渲染 */}
                  <div className="[&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[13px] [&_td]:text-[#333333] [&_td]:align-top [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:font-bold">
                    <DocxParagraphEditor
                      key={`${loadedFileId}-${resetKey}`}
                      paragraphs={content.paragraphs}
                      targetsByPara={targetsByPara}
                      onAnchorClick={handleAnchorClick}
                      renderAtomic={renderAtomicBlock}
                      editorRef={editorRef}
                      onBlocksChange={handleEditorDirty}
                      toolbarPortal={toolbarHost}
                      dirty={dirty}
                      saving={savingEdits}
                      onSave={handleSaveEdits}
                      onDiscard={handleDiscardEdits}
                    />
                  </div>
                </div>
              ) : docxFidelity ? (
                /* 只读保真：docx-preview 渲染原始文件，mark[data-anchor-key] 点击跳批注。
                   key 必须保留：分支间根节点同为 div 时 React 会就地复用 DOM（不卸载），
                   残留的 docx 渲染产物会漏进编辑视图且 ref/epoch 不触发 */
                <div
                  key="view-fidelity"
                  className="min-w-0 flex-1 overflow-auto"
                >
                  <div
                    ref={docxWrapRefCb}
                    onClick={handleSelectTableAnn}
                    className="mx-auto w-full max-w-[900px]"
                  />
                </div>
              ) : docxFidelityCandidate && docxBlobLoading ? (
                <div
                  key="view-loading"
                  className="flex items-center justify-center py-20 text-sm text-[#8A8A8A]"
                >
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  正在加载原始文档…
                </div>
              ) : (
                /* 降级/非 docx：旧段落纸张视图 */
                <>
                  {docxFidelityCandidate &&
                    (docxBlobError || docxRenderFailed) && (
                      <div className="mx-auto mb-2 max-w-[794px] rounded bg-[#FFF7E8] px-3 py-2 text-xs text-[#FAAD14]">
                        格式渲染失败，已降级为纯文本预览
                      </div>
                    )}
                  <div
                    key="view-fallback"
                    className="mx-auto w-full max-w-[794px] border border-[#C9C9C9] bg-white px-[72px] py-[64px] shadow-[0_4px_24px_rgba(0,0,0,0.14)]"
                    style={{
                      fontFamily: "'SimSun', '宋体', 'Times New Roman', serif",
                    }}
                  >
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
                  </div>
                </>
              )}
            </div>

            {/* 右侧批注栏：卡片散布对齐锚点 + 引线（与顶部批注列表同时存在） */}
            <div className="relative shrink-0" style={{ width: RAIL_W }}>
              {activeRailItems.map((it) => {
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
                        fileId={fileId}
                        canDelete={!!onDeleteAnnotation && !!it.ann!.id}
                        onDelete={() =>
                          handleDeleteAnnotation(String(it.ann!.id))
                        }
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
                {activeRailItems.map((it) => {
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
                      severity: 'medium',
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
                {/* 批注级别：创建时选择，与 AI 审核级别同一套配色语义 */}
                <div className="mb-1.5 flex items-center gap-1">
                  <span className="shrink-0 text-[10px] text-[#8A8A8A]">
                    级别
                  </span>
                  {(
                    [
                      ['high', '严重'],
                      ['medium', '一般'],
                      ['low', '提示'],
                    ] as const
                  ).map(([val, label]) => {
                    const c = SEVERITY_CONFIG[val];
                    const active = draft.severity === val;
                    return (
                      <button
                        key={val}
                        onClick={() => setDraft({ ...draft, severity: val })}
                        className="rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors"
                        style={{
                          color: active ? '#fff' : c.textColor,
                          backgroundColor: active ? c.border : c.bg,
                        }}
                      >
                        {label}
                      </button>
                    );
                  })}
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

  // 右侧常驻抽屉：与范本实时预览抽屉完全同款（同类名同宽度同层级）。
  // 使用点为常挂载 + open 属性切换，open=false 时不渲染；无遮罩不挡对话，
  // 由使用方收缩主区腾位；animate-in 滑入，Esc 关闭。
  if (!open) return null;
  return (
    <div className="fixed right-0 top-0 z-40 flex h-full w-2/3 flex-col border-l border-[#E5E5E5] bg-white text-[#1A1A1A] shadow-[-8px_0_24px_rgba(0,0,0,0.08)] animate-in fade-in slide-in-from-right-4 duration-300">
      {innerContent}
    </div>
  );
}
