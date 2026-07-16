import { Copy, Loader2, Lock, Plus, Trash2, Users, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

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
  const [tab, setTab] = useState<'collaborators' | 'link'>('collaborators');
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
            <h2 className="text-lg font-semibold text-[#1A1A1A]">分享</h2>
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-black/70 hover:bg-black/[0.06]"
          >
            <X className="size-4" />
          </button>
        </div>
        <p className="text-sm text-[#8A8A8A] mb-4">管理「{docName}」的协作者</p>

        {/* Tab bar */}
        <div className="flex items-center gap-1 border-b border-stone-100 mb-3">
          {(
            [
              { key: 'collaborators', label: '协作者' },
              { key: 'link', label: '公开链接' },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                tab === t.key
                  ? 'border-stone-900 text-stone-900'
                  : 'border-transparent text-stone-400 hover:text-stone-700'
              }`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'collaborators' && (
          <>
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
              <p className="text-sm text-[#A3A3A3] text-center py-4">
                暂无协作者
              </p>
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
          </>
        )}

        {tab === 'link' && <ShareLinkTab docId={docId} apiFetch={apiFetch} />}

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

/* ─── ShareLinkTab: 公开链接管理 ──────────────────────────────────── */

interface ShareData {
  document_id: string;
  token: string;
  permission: string;
  has_password: boolean;
  expires_at: number | null;
}

function ShareLinkTab({
  docId,
  apiFetch,
}: {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}) {
  const [share, setShare] = useState<ShareData | null>(null);
  const [loading, setLoading] = useState(false);
  const [permission, setPermission] = useState<'view' | 'edit'>('view');
  const [password, setPassword] = useState('');
  const [expiresDays, setExpiresDays] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup copied timer on unmount
  useEffect(() => {
    return () => {
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
    };
  }, []);

  const loadShare = useCallback(async () => {
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/share`,
      );
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        setShare(result.data);
        setPermission(result.data.permission);
      }
    } catch (e) {
      console.error('Failed to load share:', e);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    loadShare();
  }, [loadShare]);

  const handleCreateOrUpdate = async () => {
    setLoading(true);
    try {
      const expiresAt = expiresDays
        ? Math.floor(Date.now() / 1000) + expiresDays * 86400
        : null;
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/share`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            permission,
            password: password || null,
            expires_at: expiresAt,
          }),
        },
      );
      const result = await resp.json();
      if (result.code === 0) {
        setShare(result.data);
      }
    } catch (e) {
      console.error('Failed to save share:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    setLoading(true);
    try {
      await apiFetch(`/api/v1/collaboration/documents/${docId}/share`, {
        method: 'DELETE',
      });
      setShare(null);
      setPassword('');
      setExpiresDays(null);
    } catch (e) {
      console.error('Failed to delete share:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = () => {
    if (!share) return;
    const url = `${window.location.origin}/share/doc/${share.token}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = setTimeout(() => setCopied(false), 2000);
    });
  };

  return share ? (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-3 py-2 bg-[#F5F5F4] text-[#555555]"
          value={`${window.location.origin}/share/doc/${share.token}`}
          readOnly
        />
        <button
          className="px-3 py-2 text-sm font-medium bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] transition-colors"
          onClick={handleCopy}
        >
          {copied ? '已复制' : <Copy className="size-4" />}
        </button>
      </div>

      <div className="flex items-center gap-2 text-xs text-[#8A8A8A]">
        <span className="px-2 py-0.5 bg-[#F5F5F4] rounded">
          {share.permission === 'edit' ? '可编辑' : '仅查看'}
        </span>
        {share.has_password && (
          <span className="px-2 py-0.5 bg-amber-50 text-amber-600 rounded flex items-center gap-1">
            <Lock className="size-2.5" />
            密码保护
          </span>
        )}
        {share.expires_at && (
          <span className="px-2 py-0.5 bg-[#F5F5F4] rounded">
            过期: {new Date(share.expires_at * 1000).toLocaleDateString()}
          </span>
        )}
      </div>

      <div className="space-y-2 pt-3 border-t border-[#E8E8E6]">
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">权限</span>
          <select
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            value={permission}
            onChange={(e) => setPermission(e.target.value as 'view' | 'edit')}
          >
            <option value="view">仅查看</option>
            <option value="edit">可编辑</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">密码</span>
          <input
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            type="password"
            placeholder="留空不设密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">过期</span>
          <select
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            value={expiresDays ?? ''}
            onChange={(e) =>
              setExpiresDays(e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">永不过期</option>
            <option value="1">1 天后</option>
            <option value="7">7 天后</option>
            <option value="30">30 天后</option>
          </select>
        </label>
      </div>

      <div className="flex gap-2 pt-1">
        <button
          className="flex-1 text-sm font-medium px-3 py-2 bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] transition-colors disabled:opacity-50"
          onClick={handleCreateOrUpdate}
          disabled={loading}
        >
          更新设置
        </button>
        <button
          className="text-sm px-3 py-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          onClick={handleDelete}
          disabled={loading}
        >
          删除链接
        </button>
      </div>
    </div>
  ) : (
    <div className="space-y-3">
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">权限</span>
          <select
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            value={permission}
            onChange={(e) => setPermission(e.target.value as 'view' | 'edit')}
          >
            <option value="view">仅查看</option>
            <option value="edit">可编辑</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">密码</span>
          <input
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            type="password"
            placeholder="留空不设密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-[#555555]">
          <span className="w-12 shrink-0">过期</span>
          <select
            className="flex-1 text-sm border border-[#D4D4D4] rounded-lg px-2 py-1"
            value={expiresDays ?? ''}
            onChange={(e) =>
              setExpiresDays(e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">永不过期</option>
            <option value="1">1 天后</option>
            <option value="7">7 天后</option>
            <option value="30">30 天后</option>
          </select>
        </label>
      </div>
      <button
        className="w-full text-sm font-medium px-3 py-2 bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] transition-colors disabled:opacity-50"
        onClick={handleCreateOrUpdate}
        disabled={loading}
      >
        {loading ? '创建中...' : '创建分享链接'}
      </button>
    </div>
  );
}
