import MarkdownContent from '@/components/next-markdown-content';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
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
import { MessageType } from '@/constants/chat';
import {
  ArrowLeft,
  Clock,
  Download,
  MessageSquare,
  Pencil,
  Star,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface FavoriteItem {
  id: string;
  title: string;
  message_ids: string[];
  created_at: string;
  updated_at: string;
}

interface FavoriteDetail extends FavoriteItem {
  messages_data: Array<{
    role: string;
    content: string;
    reference?: any;
  }>;
  agent_id: string | null;
  conversation_id: string | null;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

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
          setSelectedFavorite(result.data);
          setEditTitle(result.data.title);
        }
      } catch (e) {
        console.error('加载收藏详情失败:', e);
      } finally {
        setDetailLoading(false);
      }
    },
    [apiFetch],
  );

  const handleBack = () => {
    setSelectedFavorite(null);
    setEditingTitle(false);
  };

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

  const handleDelete = async () => {
    if (!selectedFavorite) return;
    try {
      const resp = await apiFetch(`/api/v1/favorite/${selectedFavorite.id}`, {
        method: 'DELETE',
      });
      const result = await resp.json();
      if (result.code === 0) {
        setSelectedFavorite(null);
        setDeleteDialogOpen(false);
        loadFavorites();
      }
    } catch (e) {
      console.error('删除收藏失败:', e);
    }
  };

  const handleDownload = async () => {
    if (!selectedFavorite) return;
    try {
      const resp = await apiFetch(
        `/api/v1/favorite/${selectedFavorite.id}/download`,
      );
      if (!resp.ok) return;
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${selectedFavorite.title}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('下载失败:', e);
    }
  };

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

  // ── Detail View ──
  if (selectedFavorite) {
    const messages = selectedFavorite.messages_data || [];
    return (
      <div className="flex-1 flex flex-col min-h-0 bg-[#FFFFFF]">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[#D4D4D4] shrink-0">
          <button
            onClick={handleBack}
            className="flex items-center gap-1 text-sm text-[#525252] hover:text-[#000000] transition-colors"
          >
            <ArrowLeft className="size-4" />
            返回
          </button>
          <div className="flex-1 flex items-center gap-2 min-w-0">
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
              <>
                <Star className="size-4 text-[#6366f1] fill-[#6366f1] shrink-0" />
                <h2 className="text-sm font-bold text-[#000000] truncate">
                  {selectedFavorite.title}
                </h2>
                <button
                  onClick={() => {
                    setEditTitle(selectedFavorite.title);
                    setEditingTitle(true);
                  }}
                  className="text-[#A3A3A3] hover:text-[#525252] transition-colors shrink-0"
                >
                  <Pencil className="size-3.5" />
                </button>
              </>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleDownload}
              className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
            >
              <Download className="size-3.5" />
              下载
            </button>
            <button
              onClick={() => setDeleteDialogOpen(true)}
              className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-red-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 className="size-3.5" />
              删除
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {detailLoading ? (
            <div className="flex items-center justify-center py-20">
              <div className="flex flex-col items-center gap-3">
                <div className="size-8 border-2 border-[#D4D4D4] border-t-[#000] rounded-full animate-spin" />
                <span className="text-sm text-[#A3A3A3]">加载中...</span>
              </div>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex items-center justify-center py-20">
              <span className="text-sm text-[#A3A3A3]">暂无消息内容</span>
            </div>
          ) : (
            messages.map((msg, i) => (
              <div
                key={i}
                className={`flex gap-3 ${
                  msg.role === MessageType.User
                    ? 'justify-end'
                    : 'justify-start'
                }`}
              >
                {msg.role !== MessageType.User && (
                  <RAGFlowAvatar name="标" className="size-7 shrink-0 mt-0.5" />
                )}
                <div
                  className={`max-w-[85%] ${
                    msg.role === MessageType.User
                      ? 'bg-[#000000] text-white px-4 py-2.5 rounded-2xl rounded-br-md'
                      : 'bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md'
                  }`}
                >
                  {msg.role === MessageType.User ? (
                    <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                  ) : (
                    <MarkdownContent
                      content={msg.content}
                      loading={false}
                      reference={msg.reference}
                      clickDocumentButton={() => {}}
                    />
                  )}
                </div>
                {msg.role === MessageType.User && (
                  <RAGFlowAvatar
                    name="U"
                    avatar={undefined}
                    className="size-7 shrink-0 mt-0.5"
                  />
                )}
              </div>
            ))
          )}
        </div>

        {/* Delete confirmation */}
        <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认删除</AlertDialogTitle>
              <AlertDialogDescription>
                确定要删除收藏「{selectedFavorite.title}」吗？此操作不可撤销。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleDelete}
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

  // ── List View ──
  const totalPages = Math.ceil(total / ITEMS_PER_PAGE);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[#FFFFFF]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#D4D4D4] shrink-0">
        <div className="flex items-center gap-2">
          <Star className="size-4 text-[#6366f1] fill-[#6366f1]" />
          <h2 className="text-sm font-bold text-[#000000]">收藏</h2>
          <span className="text-xs text-[#A3A3A3]">
            {total > 0 ? `共 ${total} 条` : ''}
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="flex flex-col items-center gap-3">
              <div className="size-8 border-2 border-[#D4D4D4] border-t-[#000] rounded-full animate-spin" />
              <span className="text-sm text-[#A3A3A3]">加载中...</span>
            </div>
          </div>
        ) : favorites.length === 0 ? (
          <div className="flex items-center justify-center py-20">
            <div className="flex flex-col items-center gap-3 text-[#A3A3A3]">
              <Star className="size-10" />
              <p className="text-sm">暂无收藏内容</p>
              <p className="text-xs">
                在对话中点击消息下方的"收藏"按钮来收藏内容
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-[#EAEAEA]">
            {favorites.map((fav) => (
              <button
                key={fav.id}
                onClick={() => handleSelect(fav.id)}
                className="w-full text-left px-4 py-3 hover:bg-[#F8F9FB] transition-colors"
              >
                <div className="flex items-start gap-3">
                  <Star className="size-4 text-[#6366f1] fill-[#6366f1] shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-medium text-[#000000] truncate">
                      {fav.title}
                    </h3>
                    <div className="flex items-center gap-3 mt-1 text-xs text-[#A3A3A3]">
                      <span className="flex items-center gap-1">
                        <MessageSquare className="size-3" />
                        {(fav.message_ids || []).length} 条消息
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="size-3" />
                        {formatTime(fav.updated_at || fav.created_at)}
                      </span>
                    </div>
                  </div>
                </div>
              </button>
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
  );
}
