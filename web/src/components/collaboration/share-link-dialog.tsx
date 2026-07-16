import { Copy, Link, Lock, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

interface ShareData {
  document_id: string;
  token: string;
  permission: string;
  has_password: boolean;
  expires_at: number | null;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onClose: () => void;
}

export default function ShareLinkDialog({
  docId,
  apiFetch,
  open,
  onClose,
}: Props) {
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
    if (open) {
      loadShare();
      setCopied(false);
    }
  }, [open, loadShare]);

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

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Link className="size-4 text-[#555555]" />
            <h2 className="text-lg font-semibold text-[#1A1A1A]">分享链接</h2>
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-black/70 hover:bg-black/[0.06]"
          >
            <X className="size-4" />
          </button>
        </div>

        {share ? (
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
                  onChange={(e) =>
                    setPermission(e.target.value as 'view' | 'edit')
                  }
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
                    setExpiresDays(
                      e.target.value ? Number(e.target.value) : null,
                    )
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
                  onChange={(e) =>
                    setPermission(e.target.value as 'view' | 'edit')
                  }
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
                    setExpiresDays(
                      e.target.value ? Number(e.target.value) : null,
                    )
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
        )}
      </div>
    </div>
  );
}
