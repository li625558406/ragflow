import { Loader2, Plus, Trash2, Users, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface Collaborator {
  id: string;
  user_id: string;
  role: string;
  granted_by: string;
  create_time: number;
}

interface Props {
  open: boolean;
  docId: string;
  docName: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onClose: () => void;
}

const ROLE_LABELS: Record<string, string> = {
  owner: '所有者',
  editor: '可编辑',
  viewer: '只读',
  commenter: '可评论',
};

const ROLE_OPTIONS = ['editor', 'viewer'];

export default function ShareDialog({
  open,
  docId,
  docName,
  apiFetch,
  onClose,
}: Props) {
  const [collaborators, setCollaborators] = useState<Collaborator[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [newUserId, setNewUserId] = useState('');
  const [newRole, setNewRole] = useState('editor');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');

  const loadCollaborators = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/collaborators`,
      );
      const result = await resp.json();
      if (result.code === 0) {
        setCollaborators(result.data || []);
      } else {
        setError(result.message || '加载失败');
      }
    } catch {
      setError('加载协作者列表失败');
    } finally {
      setLoading(false);
    }
  }, [docId, apiFetch]);

  useEffect(() => {
    if (open) {
      loadCollaborators();
      setError('');
      setNewUserId('');
      setAddError('');
    }
  }, [open, loadCollaborators]);

  const handleAdd = async () => {
    if (!newUserId.trim()) return;
    setAdding(true);
    setAddError('');
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/collaborators`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: newUserId.trim(), role: newRole }),
        },
      );
      const result = await resp.json();
      if (result.code === 0) {
        setNewUserId('');
        loadCollaborators();
      } else {
        setAddError(result.message || '添加失败');
      }
    } catch {
      setAddError('添加协作者失败');
    } finally {
      setAdding(false);
    }
  };

  const handleUpdateRole = async (userId: string, role: string) => {
    try {
      await apiFetch(
        `/api/v1/collaboration/documents/${docId}/collaborators/${userId}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role }),
        },
      );
      loadCollaborators();
    } catch {
      // silent
    }
  };

  const handleRemove = async (userId: string) => {
    try {
      await apiFetch(
        `/api/v1/collaboration/documents/${docId}/collaborators/${userId}`,
        { method: 'DELETE' },
      );
      loadCollaborators();
    } catch {
      // silent
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Users className="size-4 text-[#555555]" />
            <h2 className="text-lg font-semibold text-[#1A1A1A]">分享文档</h2>
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-black/70 hover:bg-black/[0.06]"
          >
            <X className="size-4" />
          </button>
        </div>
        <p className="text-sm text-[#8A8A8A] mb-4">管理「{docName}」的协作者</p>

        {/* Add collaborator */}
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            className="flex-1 px-3 py-1.5 text-sm border border-[#D4D4D4] rounded-lg focus:outline-none focus:border-indigo-400"
            placeholder="输入用户 ID..."
            value={newUserId}
            onChange={(e) => setNewUserId(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          />
          <select
            className="px-2 py-1.5 text-sm border border-[#D4D4D4] rounded-lg focus:outline-none focus:border-indigo-400 bg-white"
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          <button
            className="px-3 py-1.5 text-sm font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-1"
            onClick={handleAdd}
            disabled={adding || !newUserId.trim()}
          >
            {adding ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plus className="size-3.5" />
            )}
            添加
          </button>
        </div>
        {addError && (
          <p className="text-xs text-red-500 -mt-3 mb-3">{addError}</p>
        )}

        {/* Collaborator list */}
        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="size-5 text-[#A3A3A3] animate-spin" />
          </div>
        ) : error ? (
          <p className="text-sm text-red-500 text-center py-4">{error}</p>
        ) : collaborators.length === 0 ? (
          <p className="text-sm text-[#A3A3A3] text-center py-4">暂无协作者</p>
        ) : (
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {collaborators.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-[#F5F5F4]"
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-[#333333] truncate block">
                    {c.user_id}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <select
                    className="text-xs px-2 py-1 border border-[#D4D4D4] rounded focus:outline-none bg-white"
                    value={c.role}
                    onChange={(e) =>
                      handleUpdateRole(c.user_id, e.target.value)
                    }
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABELS[r]}
                      </option>
                    ))}
                  </select>
                  <button
                    className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-red-500 hover:bg-red-50"
                    onClick={() => handleRemove(c.user_id)}
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex justify-end mt-6">
          <button
            onClick={onClose}
            className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A]"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
