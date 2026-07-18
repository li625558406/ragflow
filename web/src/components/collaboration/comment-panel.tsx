import { MessageSquare, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface CommentData {
  id: string;
  document_id: string;
  user_id: string;
  user_name: string;
  parent_comment_id: string | null;
  anchor_block_key: string | null;
  anchor_offset_start: number | null;
  anchor_offset_end: number | null;
  content: string;
  resolved: boolean;
  create_time: number;
  update_time: number;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
}

function getInitials(name: string, defaultName: string): string {
  const n = name || defaultName;
  if (!n) return '?';
  if (/[\u4e00-\u9fff]/.test(n)) return n.slice(0, 2);
  return n.slice(0, 2).toUpperCase() || n[0]?.toUpperCase() || '?';
}

function formatTime(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function CommentPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [comments, setComments] = useState<CommentData[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<'all' | 'unresolved'>('all');
  const [newContent, setNewContent] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [replyContent, setReplyContent] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const loadComments = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/comments`,
      );
      const result = await resp.json();
      if (result.code === 0) {
        setComments(result.data || []);
      }
    } catch (e) {
      console.error('Failed to load comments:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) {
      loadComments();
    }
  }, [docId, open]);

  const handleCreate = async (parentId: string | null, content: string) => {
    if (!content.trim()) return;
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/comments`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: content.trim(),
            parent_comment_id: parentId,
          }),
        },
      );
      const result = await resp.json();
      if (result.code === 0) {
        if (parentId) {
          setReplyTo(null);
          setReplyContent('');
        } else {
          setNewContent('');
        }
        loadComments();
      }
    } catch (e) {
      console.error('Failed to create comment:', e);
    }
  };

  const handleEdit = async (commentId: string) => {
    if (!editContent.trim()) return;
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/comments/${commentId}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: editContent.trim() }),
        },
      );
      const result = await resp.json();
      if (result.code === 0) {
        setEditingId(null);
        loadComments();
      }
    } catch (e) {
      console.error('Failed to edit comment:', e);
    }
  };

  const handleDelete = async (commentId: string) => {
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/comments/${commentId}`,
        { method: 'DELETE' },
      );
      const result = await resp.json();
      if (result.code === 0) {
        loadComments();
      }
    } catch (e) {
      console.error('Failed to delete comment:', e);
    }
  };

  const handleResolve = async (commentId: string, resolved: boolean) => {
    try {
      const action = resolved ? 'unresolve' : 'resolve';
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/comments/${commentId}/${action}`,
        { method: 'POST' },
      );
      const result = await resp.json();
      if (result.code === 0) {
        loadComments();
      }
    } catch (e) {
      console.error('Failed to toggle resolve:', e);
    }
  };

  // Build threaded tree
  const rootComments = comments.filter((c) => !c.parent_comment_id);
  const childMap = new Map<string, CommentData[]>();
  for (const c of comments) {
    if (c.parent_comment_id) {
      const list = childMap.get(c.parent_comment_id) || [];
      list.push(c);
      childMap.set(c.parent_comment_id, list);
    }
  }

  const filteredRoots =
    filter === 'unresolved'
      ? rootComments.filter((c) => !c.resolved)
      : rootComments;

  const unresolvedCount = rootComments.filter((c) => !c.resolved).length;

  const renderComment = (comment: CommentData, depth: number) => {
    const children = childMap.get(comment.id) || [];
    const isEditing = editingId === comment.id;

    return (
      <div key={comment.id} className="group">
        <div
          className={`pt-3 ${depth > 0 ? 'pl-6 border-l-2 border-stone-100 ml-3' : ''}`}
        >
          <div className="flex items-start gap-2">
            <div className="size-6 rounded-full bg-stone-200 flex items-center justify-center text-[10px] font-semibold text-stone-600 shrink-0">
              {getInitials('', comment.user_name)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-stone-700">
                  {comment.user_name}
                </span>
                <span className="text-[10px] text-stone-400">
                  {formatTime(comment.create_time)}
                </span>
                {comment.resolved && (
                  <span className="text-[10px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
                    已解决
                  </span>
                )}
              </div>
              {isEditing ? (
                <div className="mt-1">
                  <textarea
                    className="w-full text-xs border border-stone-200 rounded p-1.5 resize-none focus:outline-none focus:border-stone-400"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    rows={2}
                  />
                  <div className="flex gap-1 mt-1">
                    <button
                      className="text-[10px] px-2 py-0.5 bg-stone-900 text-white rounded"
                      onClick={() => handleEdit(comment.id)}
                    >
                      保存
                    </button>
                    <button
                      className="text-[10px] px-2 py-0.5 text-stone-500 hover:text-stone-700"
                      onClick={() => setEditingId(null)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-stone-800 mt-0.5 whitespace-pre-wrap">
                  {comment.content}
                </p>
              )}
              {!isEditing && (
                <div className="flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  {depth < 2 && (
                    <button
                      className="text-[10px] text-stone-400 hover:text-stone-700"
                      onClick={() => {
                        setReplyTo(comment.id);
                        setReplyContent('');
                      }}
                    >
                      回复
                    </button>
                  )}
                  <button
                    className="text-[10px] text-stone-400 hover:text-stone-700"
                    onClick={() => {
                      setEditingId(comment.id);
                      setEditContent(comment.content);
                    }}
                  >
                    编辑
                  </button>
                  <button
                    className="text-[10px] text-stone-400 hover:text-red-600"
                    onClick={() => handleDelete(comment.id)}
                  >
                    删除
                  </button>
                  <button
                    className="text-[10px] text-stone-400 hover:text-emerald-600"
                    onClick={() => handleResolve(comment.id, comment.resolved)}
                  >
                    {comment.resolved ? '重新打开' : '解决'}
                  </button>
                </div>
              )}
              {replyTo === comment.id && (
                <div className="mt-2">
                  <textarea
                    className="w-full text-xs border border-stone-200 rounded p-1.5 resize-none focus:outline-none focus:border-stone-400"
                    value={replyContent}
                    onChange={(e) => setReplyContent(e.target.value)}
                    placeholder="写下回复..."
                    rows={2}
                    autoFocus
                  />
                  <div className="flex gap-1 mt-1">
                    <button
                      className="text-[10px] px-2 py-0.5 bg-stone-900 text-white rounded disabled:opacity-50"
                      disabled={!replyContent.trim()}
                      onClick={() => handleCreate(comment.id, replyContent)}
                    >
                      回复
                    </button>
                    <button
                      className="text-[10px] px-2 py-0.5 text-stone-500 hover:text-stone-700"
                      onClick={() => setReplyTo(null)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
        {children.map((child) => renderComment(child, depth + 1))}
      </div>
    );
  };

  if (!open) return null;

  return (
    <div className="w-full flex flex-col bg-white h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5">
          <MessageSquare className="size-3.5 text-stone-500" />
          <span className="text-xs font-semibold text-stone-700">评论</span>
          {unresolvedCount > 0 && (
            <span className="text-[10px] bg-stone-900 text-white px-1.5 py-0.5 rounded-full">
              {unresolvedCount}
            </span>
          )}
        </div>
        <button
          className="text-stone-400 hover:text-stone-700"
          onClick={onToggle}
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Filter tabs */}
      <div className="flex border-b border-stone-100 px-3">
        <button
          className={`text-xs py-1.5 mr-3 border-b-2 transition-colors ${
            filter === 'all'
              ? 'border-stone-900 text-stone-900 font-medium'
              : 'border-transparent text-stone-400 hover:text-stone-600'
          }`}
          onClick={() => setFilter('all')}
        >
          全部
        </button>
        <button
          className={`text-xs py-1.5 border-b-2 transition-colors ${
            filter === 'unresolved'
              ? 'border-stone-900 text-stone-900 font-medium'
              : 'border-transparent text-stone-400 hover:text-stone-600'
          }`}
          onClick={() => setFilter('unresolved')}
        >
          未解决 {unresolvedCount > 0 && `(${unresolvedCount})`}
        </button>
      </div>

      {/* Comments list */}
      <div className="flex-1 overflow-y-auto px-3">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 border-2 border-stone-200 border-t-stone-400 rounded-full animate-spin" />
          </div>
        ) : filteredRoots.length === 0 ? (
          <div className="text-center py-8 text-xs text-stone-400">
            暂无评论
          </div>
        ) : (
          filteredRoots.map((c) => renderComment(c, 0))
        )}
      </div>

      {/* New comment input */}
      <div className="border-t border-stone-100 p-3">
        <textarea
          className="w-full text-xs border border-stone-200 rounded-lg p-2 resize-none focus:outline-none focus:border-stone-400"
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          placeholder="添加评论..."
          rows={2}
        />
        <button
          className="mt-1.5 text-xs px-3 py-1 bg-stone-900 text-white rounded-lg disabled:opacity-50 hover:bg-stone-800 transition-colors"
          disabled={!newContent.trim()}
          onClick={() => handleCreate(null, newContent)}
        >
          评论
        </button>
      </div>
    </div>
  );
}
