import { History, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface VersionInfo {
  current_version: number;
  has_ydoc: boolean;
  update_time: number | string | null;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
}

function formatTime(t: number | string | null): string {
  if (!t) return '—';
  let ms = Number(t);
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1e12) ms *= 1000; // 秒级时间戳兼容
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

export default function VersionHistoryPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions`,
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        setInfo(result.data);
      }
    } catch (e) {
      console.error('Failed to load versions:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) load();
  }, [docId, open, load]);

  const handleRestore = async () => {
    if (restoring || !info) return;
    if (!window.confirm('确认恢复到最近保存的版本？当前未保存的更改将丢失。'))
      return;
    setRestoring(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions/${info.current_version || 0}/restore`,
        { method: 'POST' },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      if (result.code === 0) {
        window.location.reload();
      } else {
        alert(result.message || '恢复失败');
      }
    } catch (e) {
      console.error('Restore failed:', e);
    } finally {
      setRestoring(false);
    }
  };

  if (!open) return null;

  return (
    <div className="w-full h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5 text-stone-700">
          <History className="size-3.5" />
          <span className="text-xs font-semibold">版本历史</span>
        </div>
        <button
          className="size-6 flex items-center justify-center rounded text-stone-400 hover:text-stone-700 hover:bg-stone-100"
          onClick={onToggle}
        >
          <X className="size-3.5" />
        </button>
      </div>
      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="text-xs text-stone-400 text-center py-6">
            加载中...
          </div>
        ) : info ? (
          <div className="space-y-3">
            <div className="border border-stone-200 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-stone-800">
                  当前版本
                </span>
                <span className="text-xs text-stone-500 tabular-nums">
                  v{info.current_version || 0}
                </span>
              </div>
              <div className="text-[10px] text-stone-400 mt-1">
                最近保存: {formatTime(info.update_time)}
              </div>
            </div>
            {info.has_ydoc ? (
              <button
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium text-stone-700 border border-stone-200 rounded-lg hover:bg-stone-50 disabled:opacity-50 transition-colors"
                onClick={handleRestore}
                disabled={restoring}
              >
                <RotateCcw className="size-3.5" />
                {restoring ? '恢复中...' : '恢复到最近保存状态'}
              </button>
            ) : (
              <p className="text-[10px] text-stone-400 text-center">
                暂无可恢复的保存状态
              </p>
            )}
            <p className="text-[10px] text-stone-400 leading-relaxed">
              文档每次保存会递增版本号。恢复操作将丢弃当前未保存的本地更改，并重新加载页面。
            </p>
          </div>
        ) : (
          <div className="text-xs text-stone-400 text-center py-6">
            无版本信息
          </div>
        )}
      </div>
    </div>
  );
}
