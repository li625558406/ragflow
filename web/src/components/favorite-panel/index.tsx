import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CendTooltip } from '@/components/ui/tooltip';
import { downloadWord, markdownToBodyHtml } from '@/utils/markdown-to-word';
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  MessageSquare,
  Pencil,
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
  refreshToken?: number;
}

/* ── Content extraction ── */

function stripThinkTags(text: string): string {
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '');
}

/** Check if content looks like HTML (has tags). */
function isHtmlContent(content: string): boolean {
  return /<[a-zA-Z][^>]*>/.test(content);
}

/**
 * Extract Word HTML from stored messages.
 * New favorites: stored as Word HTML directly.
 * Old favorites (migrated): raw markdown → converted to Word HTML on load.
 */
function extractContent(messages: FavoriteMessage[]): string {
  if (!messages || messages.length === 0) return '';
  if (messages.length === 1 && messages[0].role === 'merged') {
    const raw = stripThinkTags(messages[0].content || '');
    if (!raw) return '';
    // If already Word HTML, return as-is; otherwise convert from markdown
    return isHtmlContent(raw) ? raw : markdownToBodyHtml(raw);
  }
  // Multi-message old format: merge with role labels then convert
  const merged = messages
    .map((m) => {
      const roleLabel = m.role === 'user' ? '【用户】' : '【助手】';
      return `${roleLabel}\n\n${stripThinkTags(m.content || '')}\n`;
    })
    .join('\n');
  return markdownToBodyHtml(merged);
}

/* ── Component ── */

export default function FavoritePanel({ apiFetch, refreshToken }: Props) {
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
  const [collapsed, setCollapsed] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    title: string;
  } | null>(null);

  const [content, setContent] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const ITEMS_PER_PAGE = 20;

  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  const loadFavorites = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetchRef.current(
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
  }, [page]);

  useEffect(() => {
    loadFavorites();
  }, [loadFavorites]);

  // Refresh when parent signals (e.g. after save from chat)
  useEffect(() => {
    if (refreshToken !== undefined && refreshToken > 0) {
      loadFavorites();
    }
  }, [refreshToken]);

  const handleSelect = useCallback(async (id: string) => {
    setDetailLoading(true);
    try {
      const resp = await apiFetchRef.current(`/api/v1/favorite/${id}`);
      const result = await resp.json();
      if (result.code === 0) {
        const fav = result.data;
        setSelectedFavorite(fav);
        setEditTitle(fav.title);
        setContent(extractContent(fav.messages_data || []));
      }
    } catch (e) {
      console.error('加载收藏详情失败:', e);
    } finally {
      setDetailLoading(false);
    }
  }, []);

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

  const handleRename = async (id: string) => {
    if (!renameValue.trim()) {
      setRenamingId(null);
      return;
    }
    try {
      const resp = await apiFetch(`/api/v1/favorite/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: renameValue.trim() }),
      });
      const result = await resp.json();
      if (result.code === 0) {
        setRenamingId(null);
        loadFavorites();
        if (selectedFavorite?.id === id) {
          setSelectedFavorite((prev) =>
            prev ? { ...prev, title: renameValue.trim() } : null,
          );
        }
      }
    } catch (e) {
      console.error('重命名失败:', e);
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
      <div
        className={`shrink-0 border-r border-[#E8E8E6] bg-white flex flex-col transition-[width] duration-300 ease-in-out overflow-hidden ${
          collapsed ? 'w-0 border-r-0' : 'w-56'
        }`}
      >
        <div className="flex items-center gap-2 px-4 pt-4 pb-2 whitespace-nowrap">
          <span className="text-[#555555] text-[15px] font-semibold tracking-widest uppercase">
            收藏列表
          </span>
          {total > 0 && (
            <span className="text-xs text-[#8A8A8A]">共 {total} 条</span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading && favorites.length === 0 ? (
            <div className="flex items-center justify-center py-20">
              <div className="flex flex-col items-center gap-3">
                <div className="size-8 border-2 border-[#E8E8E6] border-t-[#000] rounded-full animate-spin" />
                <span className="text-sm text-[#8A8A8A]">加载中...</span>
              </div>
            </div>
          ) : favorites.length === 0 ? (
            <div className="flex items-center justify-center py-20 px-4">
              <div className="flex flex-col items-center gap-3 text-[#8A8A8A]">
                <Star className="size-10" />
                <p className="text-sm text-center">暂无收藏内容</p>
                <p className="text-xs text-center">
                  在对话中点击消息下方的"收藏"按钮来收藏内容
                </p>
              </div>
            </div>
          ) : (
            <div className="px-2 space-y-0.5">
              {favorites.map((fav, idx) => (
                <button
                  key={fav.id}
                  onClick={() => handleSelect(fav.id)}
                  className={`cs-list-enter cs-list-d${Math.min(idx, 7)} w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group whitespace-nowrap ${
                    selectedFavorite?.id === fav.id
                      ? 'bg-[#F5F5F4] text-[#1A1A1A]'
                      : 'text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A]'
                  }`}
                >
                  <div
                    className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                      selectedFavorite?.id === fav.id
                        ? 'bg-white'
                        : 'bg-[#F5F5F4]'
                    }`}
                  >
                    <MessageSquare className="w-4 h-4" strokeWidth={1.5} />
                  </div>
                  <div className="min-w-0 flex-1">
                    {renamingId === fav.id ? (
                      <input
                        type="text"
                        className="w-full px-2 py-1 text-xs border border-[#1A1A1A] rounded focus:outline-none text-[#1A1A1A] bg-white"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => handleRename(fav.id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleRename(fav.id);
                          if (e.key === 'Escape') setRenamingId(null);
                        }}
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <div className="flex items-center justify-between">
                        <span className="text-[15px] font-medium truncate">
                          {fav.title}
                        </span>
                        <div className="opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity flex items-center gap-0.5 ml-1">
                          <CendTooltip title="重命名">
                            <button
                              className="w-7 h-7 flex items-center justify-center rounded text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] transition-colors"
                              onClick={(e) => {
                                e.stopPropagation();
                                setRenamingId(fav.id);
                                setRenameValue(fav.title);
                              }}
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                          </CendTooltip>
                          <CendTooltip title="删除">
                            <button
                              className="w-7 h-7 flex items-center justify-center rounded text-[#555555] hover:text-red-500 hover:bg-red-50 transition-colors"
                              onClick={(e) => {
                                e.stopPropagation();
                                setDeleteTarget({
                                  id: fav.id,
                                  title: fav.title,
                                });
                                setDeleteDialogOpen(true);
                              }}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </CendTooltip>
                        </div>
                      </div>
                    )}
                    <div className="text-[11px] text-[#8A8A8A] truncate">
                      {formatTime(fav.updated_at || fav.created_at)}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 px-4 py-3 border-t border-[#E8E8E6] shrink-0">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1 text-xs text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              上一页
            </button>
            <span className="text-xs text-[#8A8A8A]">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1 text-xs text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              下一页
            </button>
          </div>
        )}
      </div>

      {/* Toggle button — floats on sidebar edge */}
      <CendTooltip title={collapsed ? '展开侧边栏' : '收起侧边栏'}>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="shrink-0 self-start mt-6 -ml-3.5 z-10 size-7 flex items-center justify-center rounded-full border-2 border-[#E8E8E6] bg-white text-[#555555] hover:text-[#1A1A1A] hover:border-[#A3A3A3] hover:shadow-[0_2px_8px_rgba(0,0,0,0.12)] transition-all cursor-pointer"
        >
          {collapsed ? (
            <ChevronRight className="size-3.5" />
          ) : (
            <ChevronLeft className="size-3.5" />
          )}
        </button>
      </CendTooltip>

      {/* ── Right Content ── */}
      <div className="flex-1 flex min-w-0">
        {detailLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="size-8 border-2 border-[#E8E8E6] border-t-[#000] rounded-full animate-spin" />
              <span className="text-sm text-[#8A8A8A]">加载中...</span>
            </div>
          </div>
        ) : !selectedFavorite ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-[#8A8A8A]">
              <FileText className="size-12" />
              <p className="text-sm">请从左侧选择一个收藏</p>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-[#E8E8E6] shrink-0">
              <Star className="size-4 text-[#1A1A1A] fill-[#1A1A1A] shrink-0" />
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
                <CendTooltip title="点击编辑标题">
                  <h2
                    className="text-sm font-bold text-[#1A1A1A] truncate flex-1 cursor-pointer hover:text-[#1A1A1A] transition-colors"
                    onClick={() => {
                      setEditTitle(selectedFavorite.title);
                      setEditingTitle(true);
                    }}
                  >
                    {selectedFavorite.title}
                  </h2>
                </CendTooltip>
              )}
              <div className="flex items-center gap-1 ml-auto">
                <button
                  onClick={() =>
                    handleDownload(selectedFavorite.id, selectedFavorite.title)
                  }
                  disabled={downloadingId === selectedFavorite.id}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors"
                >
                  <Download className="size-4" />
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
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                >
                  <Trash2 className="size-4" />
                  删除
                </button>
              </div>
            </div>

            {/* Content area: Word HTML preview */}
            <div className="flex-1 overflow-y-auto">
              <div
                className="word-preview w-full max-w-5xl mx-auto p-6 text-[#555555] text-[11pt] leading-relaxed"
                dangerouslySetInnerHTML={{ __html: content }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      {deleteDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/40"
            onClick={() => {
              setDeleteTarget(null);
              setDeleteDialogOpen(false);
            }}
          />
          <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold text-[#1A1A1A]">确认删除</h2>
            <p className="text-sm text-[#8A8A8A] mt-2">
              确定要删除收藏「{deleteTarget?.title}」吗？此操作不可撤销。
            </p>
            <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6">
              <button
                onClick={() => {
                  setDeleteTarget(null);
                  setDeleteDialogOpen(false);
                }}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A] mt-2 sm:mt-0"
              >
                取消
              </button>
              <button
                onClick={handleDeleteConfirm}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 bg-red-700 hover:bg-red-800 text-white"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
