import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Clock,
  Download,
  FileText,
  MessageSquare,
  Save,
  Star,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

interface FavoriteItem {
  id: string;
  title: string;
  message_ids: string[];
  created_at: string;
  updated_at: string;
}

interface FavoriteMessage {
  role: string;
  content: string;
  reference?: any;
}

interface FavoriteDetail extends FavoriteItem {
  messages_data: FavoriteMessage[];
  agent_id: string | null;
  conversation_id: string | null;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

/* ── Content sanitization ── */

function stripThinkTags(text: string): string {
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '');
}

/** Convert markdown to clean plain text. */
function stripMarkdown(text: string): string {
  let s = text;

  // <think> tags
  s = stripThinkTags(s);

  // Code blocks — remove ``` fences, keep code content
  s = s.replace(
    /```\w*\n([\s\S]*?)```/g,
    (_, code) => '\n' + code.trim() + '\n',
  );

  // Inline code
  s = s.replace(/`([^`]+)`/g, '$1');

  // Bold + italic
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '$1');
  s = s.replace(/___(.+?)___/g, '$1');

  // Bold
  s = s.replace(/\*\*(.+?)\*\*/g, '$1');
  s = s.replace(/__(.+?)__/g, '$1');

  // Italic
  s = s.replace(/\*(.+?)\*/g, '$1');
  s = s.replace(/_(.+?)_/g, '$1');

  // Strikethrough
  s = s.replace(/~~(.+?)~~/g, '$1');

  // Links: [text](url) → text
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');

  // Images: ![alt](url) → remove
  s = s.replace(/!\[[^\]]*\]\([^)]+\)/g, '');

  // Headings: remove # markers, keep text
  s = s.replace(/^#{1,6}\s+/gm, '');

  // Unordered list markers → bullet
  s = s.replace(/^(\s*)[-*+]\s+/gm, '$1• ');

  // Numbered list: keep numbering as-is
  s = s.replace(/^(\s*\d+)\.\s+/gm, '$1. ');

  // Horizontal rules
  s = s.replace(/^(-{3,}|\*{3,}|_{3,})\s*$/gm, '────────────────');

  return s.trim();
}

/** Merge messages array into a single clean text string. */
function extractContent(messages: FavoriteMessage[]): string {
  if (!messages || messages.length === 0) return '';
  if (messages.length === 1 && messages[0].role === 'merged') {
    return stripMarkdown(messages[0].content || '');
  }
  return messages
    .map((m) => {
      const roleLabel = m.role === 'user' ? '【用户】' : '【助手】';
      return `${roleLabel}\n\n${stripMarkdown(m.content || '')}\n`;
    })
    .join('\n');
}

/* ── Word download (from already-clean text) ── */

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ── Markdown table parsing (tables pass through stripMarkdown as-is) ── */

/** Extract headers, rows, and alignments from a markdown table string. */
function parseMarkdownTable(tableText: string): {
  headers: string[];
  rows: string[][];
  aligns: string[];
} | null {
  const lines = tableText.trim().split('\n');
  if (lines.length < 2) return null;

  const parseRow = (line: string) =>
    line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());

  const headers = parseRow(lines[0]);
  if (headers.length === 0) return null;

  const aligns = parseRow(lines[1]).map((cell) => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return 'left';
  });

  const rows = lines.slice(2).map(parseRow);

  return { headers, rows, aligns };
}

/** Render a parsed table as Word-compatible HTML. */
function renderWordTable(
  headers: string[],
  rows: string[][],
  aligns: string[],
): string {
  const border = 'border:1px solid #999;';
  const td = (a: string) =>
    `padding:3pt 6pt;${border}font-size:10pt;text-align:${a};`;
  const th = (a: string) =>
    `padding:3pt 6pt;${border}font-size:10pt;font-weight:bold;text-align:${a};background-color:#f5f5f5;`;

  const thead = `<tr>${headers
    .map(
      (h, i) => `<th style="${th(aligns[i] || 'left')}">${escapeHtml(h)}</th>`,
    )
    .join('')}</tr>`;

  const tbody = rows
    .map(
      (row) =>
        `<tr>${row
          .map(
            (cell, i) =>
              `<td style="${td(aligns[i] || 'left')}">${escapeHtml(cell)}</td>`,
          )
          .join('')}</tr>`,
    )
    .join('');

  return `<table style="border-collapse:collapse;margin:10pt 0;width:100%;${border}">
<thead>${thead}</thead>
<tbody>${tbody}</tbody>
</table>`;
}

/* ── Process a block of non-table text lines → HTML ── */

const TABLE_BLOCK_RE = /\|.+\|\r?\n\|[-:| ]+\|\r?\n(?:\|.+\|\r?\n?)+/g;

function processTextLines(lines: string[]): string[] {
  const parts: string[] = [];
  let paraLines: string[] = [];

  function flushPara() {
    if (paraLines.length > 0) {
      parts.push(
        `<p style="font-size:11pt;line-height:1.8;margin:0 0 8pt 0;text-align:justify;">${paraLines
          .map(escapeHtml)
          .join('<br/>')}</p>`,
      );
      paraLines = [];
    }
  }

  for (const line of lines) {
    const trimmed = line.trim();

    if (/^【(?:用户|助手)】$/.test(trimmed)) {
      flushPara();
      parts.push(
        `<h2 style="font-size:14pt;font-weight:bold;margin-top:14pt;margin-bottom:6pt;color:#1a1a1a;">${escapeHtml(
          trimmed.replace(/【(.+)】/, '$1'),
        )}</h2>`,
      );
      continue;
    }

    if (/^─{8,}$/.test(trimmed)) {
      flushPara();
      parts.push(
        '<hr style="border:none;border-top:1px solid #ccc;margin:12pt 0;" />',
      );
      continue;
    }

    if (!trimmed) {
      flushPara();
      continue;
    }

    paraLines.push(line);
  }
  flushPara();

  return parts;
}

function cleanTextToWordHtml(text: string, title: string): string {
  // Normalize line endings
  text = text.replace(/\r\n/g, '\n');

  const parts: string[] = [];
  parts.push(
    `<h1 style="font-size:18pt;font-weight:bold;margin-bottom:12pt;color:#1a1a1a;">${escapeHtml(title)}</h1>`,
  );

  // Split into table / non-table blocks so markdown tables become Word tables
  TABLE_BLOCK_RE.lastIndex = 0;
  let cursor = 0;
  let m: RegExpExecArray | null;
  while ((m = TABLE_BLOCK_RE.exec(text)) !== null) {
    // Text before this table
    if (m.index > cursor) {
      const before = text.slice(cursor, m.index).split('\n');
      parts.push(...processTextLines(before));
    }
    // Table
    const table = parseMarkdownTable(m[0]);
    if (table) {
      parts.push(renderWordTable(table.headers, table.rows, table.aligns));
    } else {
      // Fallback: render as regular text
      parts.push(...processTextLines(m[0].split('\n')));
    }
    cursor = m.index + m[0].length;
  }
  // Remaining text after last table
  if (cursor < text.length) {
    const after = text.slice(cursor).split('\n');
    parts.push(...processTextLines(after));
  }

  return `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>${escapeHtml(title)}</title>
<!--[if gte mso 9]><xml>
<w:WordDocument>
  <w:View>Print</w:View>
  <w:Zoom>100</w:Zoom>
  <w:DoNotOptimizeForBrowser/>
</w:WordDocument>
</xml><![endif]-->
<style>
@page { size: A4; margin: 2cm; }
body {
  font-family: "Microsoft YaHei", "宋体", SimSun, sans-serif;
  font-size: 11pt;
  color: #333;
  line-height: 1.8;
}
</style>
</head>
<body>${parts.join('\n')}</body>
</html>`;
}

function downloadWord(title: string, content: string) {
  const html = cleanTextToWordHtml(content, title);
  const blob = new Blob(['\ufeff' + html], {
    type: 'application/msword;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${title}.doc`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ── Component ── */

export default function FavoritePanel({ apiFetch }: Props) {
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selectedFavorite, setSelectedFavorite] =
    useState<FavoriteDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    title: string;
  } | null>(null);

  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedContent, setSavedContent] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const ITEMS_PER_PAGE = 20;

  const loadFavorites = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/favorite/list?page=${page}&items_per_page=${ITEMS_PER_PAGE}`,
      );
      const result = await resp.json();
      if (result.code === 0) {
        setFavorites(result.data?.items || []);
        setTotal(result.data?.total || 0);
      }
    } catch (e) {
      console.error('加载收藏列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, page]);

  useEffect(() => {
    loadFavorites();
  }, [loadFavorites]);

  const handleSelect = useCallback(
    async (id: string) => {
      setDetailLoading(true);
      try {
        const resp = await apiFetch(`/api/v1/favorite/${id}`);
        const result = await resp.json();
        if (result.code === 0) {
          const fav = result.data;
          setSelectedFavorite(fav);
          setEditTitle(fav.title);
          const content = extractContent(fav.messages_data || []);
          setEditContent(content);
          setSavedContent(content);
        }
      } catch (e) {
        console.error('加载收藏详情失败:', e);
      } finally {
        setDetailLoading(false);
      }
    },
    [apiFetch],
  );

  const handleSaveTitle = async () => {
    if (!selectedFavorite || !editTitle.trim()) return;
    try {
      const resp = await apiFetch(`/api/v1/favorite/${selectedFavorite.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: editTitle.trim() }),
      });
      const result = await resp.json();
      if (result.code === 0) {
        setSelectedFavorite({ ...selectedFavorite, title: editTitle.trim() });
        setEditingTitle(false);
        loadFavorites();
      }
    } catch (e) {
      console.error('更新标题失败:', e);
    }
  };

  const handleSaveContent = async () => {
    if (!selectedFavorite) return;
    setSaving(true);
    try {
      const clean = stripMarkdown(editContent);
      const resp = await apiFetch(`/api/v1/favorite/${selectedFavorite.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages_data: [{ role: 'merged', content: clean }],
        }),
      });
      const result = await resp.json();
      if (result.code === 0) {
        setEditContent(clean);
        setSavedContent(clean);
        setSelectedFavorite({
          ...selectedFavorite,
          messages_data: [{ role: 'merged', content: clean }],
        });
      }
    } catch (e) {
      console.error('保存内容失败:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    try {
      const resp = await apiFetch(`/api/v1/favorite/${deleteTarget.id}`, {
        method: 'DELETE',
      });
      const result = await resp.json();
      if (result.code === 0) {
        if (selectedFavorite?.id === deleteTarget.id) {
          setSelectedFavorite(null);
        }
        setDeleteDialogOpen(false);
        setDeleteTarget(null);
        loadFavorites();
      }
    } catch (e) {
      console.error('删除收藏失败:', e);
    }
  };

  const handleDownload = useCallback(
    async (id: string, title: string) => {
      setDownloadingId(id);
      try {
        const resp = await apiFetch(`/api/v1/favorite/${id}`);
        const result = await resp.json();
        if (result.code === 0) {
          const content = extractContent(result.data.messages_data || []);
          downloadWord(title, content);
        }
      } catch (e) {
        console.error('下载失败:', e);
      } finally {
        setDownloadingId(null);
      }
    },
    [apiFetch],
  );

  // ── Auto-save ──
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSwitchingRef = useRef(false);

  useEffect(() => {
    if (isSwitchingRef.current) {
      isSwitchingRef.current = false;
      return;
    }
    if (!selectedFavorite || editContent === savedContent) return;

    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      handleSaveContent();
    }, 1500);

    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [editContent]);

  // Clear timer when switching favorites
  useEffect(() => {
    isSwitchingRef.current = true;
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
    }
  }, [selectedFavorite?.id]);

  const hasUnsavedChanges = editContent !== savedContent;

  const formatTime = (ts: string) => {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const totalPages = Math.ceil(total / ITEMS_PER_PAGE);

  return (
    <div className="flex-1 flex min-h-0 bg-white">
      {/* ── Left Sidebar ── */}
      <div className="shrink-0 w-56 border-r border-[#D4D4D4] flex flex-col">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[#D4D4D4] shrink-0">
          <Star className="size-4 text-[#6366f1] fill-[#6366f1]" />
          <h2 className="text-sm font-bold text-[#000000]">收藏</h2>
          {total > 0 && (
            <span className="text-xs text-[#A3A3A3]">共 {total} 条</span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="flex flex-col items-center gap-3">
                <div className="size-8 border-2 border-[#D4D4D4] border-t-[#000] rounded-full animate-spin" />
                <span className="text-sm text-[#A3A3A3]">加载中...</span>
              </div>
            </div>
          ) : favorites.length === 0 ? (
            <div className="flex items-center justify-center py-20 px-4">
              <div className="flex flex-col items-center gap-3 text-[#A3A3A3]">
                <Star className="size-10" />
                <p className="text-sm text-center">暂无收藏内容</p>
                <p className="text-xs text-center">
                  在对话中点击消息下方的"收藏"按钮来收藏内容
                </p>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-[#EAEAEA]">
              {favorites.map((fav) => (
                <div
                  key={fav.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleSelect(fav.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSelect(fav.id);
                  }}
                  className={`w-full text-left px-3 py-2.5 transition-colors group ${
                    selectedFavorite?.id === fav.id
                      ? 'bg-[#EAEAEA]'
                      : 'hover:bg-[#F8F9FB]'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <Star
                      className={`size-3.5 shrink-0 mt-0.5 ${
                        selectedFavorite?.id === fav.id
                          ? 'text-[#6366f1] fill-[#6366f1]'
                          : 'text-[#A3A3A3]'
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-[#000000] truncate">
                        {fav.title}
                      </h3>
                      <div className="flex items-center gap-2 mt-0.5 text-xs text-[#A3A3A3]">
                        <span className="flex items-center gap-0.5">
                          <MessageSquare className="size-3" />
                          {(fav.message_ids || []).length} 条
                        </span>
                        <span className="flex items-center gap-0.5">
                          <Clock className="size-3" />
                          {formatTime(fav.updated_at || fav.created_at)}
                        </span>
                      </div>
                    </div>
                  </div>
                  {/* Hover actions */}
                  <div className="flex items-center gap-0.5 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownload(fav.id, fav.title);
                      }}
                      disabled={downloadingId === fav.id}
                      className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded transition-colors"
                    >
                      <Download className="size-3" />
                      {downloadingId === fav.id ? '下载中...' : '下载'}
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteTarget({ id: fav.id, title: fav.title });
                        setDeleteDialogOpen(true);
                      }}
                      className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs text-red-500 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                    >
                      <Trash2 className="size-3" />
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 px-4 py-3 border-t border-[#D4D4D4] shrink-0">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              上一页
            </button>
            <span className="text-xs text-[#A3A3A3]">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              下一页
            </button>
          </div>
        )}
      </div>

      {/* ── Right Content ── */}
      <div className="flex-1 flex min-w-0">
        {detailLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="size-8 border-2 border-[#D4D4D4] border-t-[#000] rounded-full animate-spin" />
              <span className="text-sm text-[#A3A3A3]">加载中...</span>
            </div>
          </div>
        ) : !selectedFavorite ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-[#A3A3A3]">
              <FileText className="size-12" />
              <p className="text-sm">请从左侧选择一个收藏</p>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-[#D4D4D4] shrink-0">
              <Star className="size-4 text-[#6366f1] fill-[#6366f1] shrink-0" />
              {editingTitle ? (
                <div className="flex items-center gap-2 flex-1">
                  <Input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    className="h-8 text-sm flex-1"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSaveTitle();
                      if (e.key === 'Escape') setEditingTitle(false);
                    }}
                  />
                  <Button
                    size="sm"
                    onClick={handleSaveTitle}
                    className="h-8 text-xs"
                  >
                    保存
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setEditingTitle(false)}
                    className="h-8 text-xs"
                  >
                    取消
                  </Button>
                </div>
              ) : (
                <h2
                  className="text-sm font-bold text-[#000000] truncate flex-1 cursor-pointer hover:text-[#6366f1] transition-colors"
                  onClick={() => {
                    setEditTitle(selectedFavorite.title);
                    setEditingTitle(true);
                  }}
                  title="点击编辑标题"
                >
                  {selectedFavorite.title}
                </h2>
              )}
              <div className="flex items-center gap-1 ml-auto">
                <button
                  onClick={handleSaveContent}
                  disabled={saving || !hasUnsavedChanges}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg transition-colors disabled:opacity-100"
                  style={{
                    backgroundColor:
                      saving || hasUnsavedChanges ? '#6366f1' : '#10b981',
                    color: '#fff',
                    opacity: saving || hasUnsavedChanges ? undefined : 0.85,
                  }}
                >
                  {saving ? (
                    <>
                      <span className="size-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      保存中...
                    </>
                  ) : hasUnsavedChanges ? (
                    <>
                      <Save className="size-3.5" />
                      保存
                    </>
                  ) : (
                    <>
                      <svg
                        className="size-3.5"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                      已保存
                    </>
                  )}
                </button>
                <button
                  onClick={() =>
                    handleDownload(selectedFavorite.id, selectedFavorite.title)
                  }
                  disabled={downloadingId === selectedFavorite.id}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                >
                  <Download className="size-3.5" />
                  {downloadingId === selectedFavorite.id ? '下载中...' : '下载'}
                </button>
                <button
                  onClick={() => {
                    setDeleteTarget({
                      id: selectedFavorite.id,
                      title: selectedFavorite.title,
                    });
                    setDeleteDialogOpen(true);
                  }}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-red-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                >
                  <Trash2 className="size-3.5" />
                  删除
                </button>
              </div>
            </div>

            {/* Always-editable textarea */}
            <div className="flex-1 overflow-y-auto">
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="w-full max-w-3xl mx-auto block h-full p-6 text-base leading-relaxed resize-none focus:outline-none border-x border-[#EAEAEA]"
                placeholder="编辑内容..."
              />
            </div>
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除收藏「{deleteTarget?.title}」吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-red-500 hover:bg-red-600"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
