import { History, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface VersionEntry {
  id: string;
  version: number;
  created_by: string;
  user_name: string;
  create_time: number;
}

interface VersionInfo {
  current_version: number;
  has_ydoc: boolean;
  update_time: number | string | null;
  versions: VersionEntry[];
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

function relativeTime(t: number): string {
  if (!t) return '';
  let ms = Number(t);
  if (!Number.isFinite(ms)) return '';
  if (ms < 1e12) ms *= 1000;
  const diff = Date.now() - ms;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)} 天前`;
  return '';
}

export default function VersionHistoryPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);

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

  const handleRestore = async (entry: VersionEntry) => {
    if (restoringId || !info) return;
    if (
      !window.confirm(
        `确认恢复到 v${entry.version}（${formatTime(entry.create_time)}）？\n` +
          '当前未保存的更改将丢失；当前状态会自动存为一条新快照便于反悔。',
      )
    )
      return;
    setRestoringId(entry.id);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions/${entry.id}/restore`,
        { method: 'POST' },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      if (result.code === 0) {
        // 恢复后主表内容已被覆盖，reload 是最稳妥的热更新方式。
        window.location.reload();
      } else {
        alert(result.message || '恢复失败');
      }
    } catch (e) {
      console.error('Restore failed:', e);
      alert('恢复失败，请稍后重试');
    } finally {
      setRestoringId(null);
    }
  };

  if (!open) return null;

  const versions = info?.versions ?? [];
  const currentVersion = info?.current_version ?? 0;

  return (
    <div className="w-full h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5 text-stone-700">
          <History className="size-3.5" />
          <span className="text-xs font-semibold">版本历史</span>
          {versions.length > 0 && (
            <span className="text-[10px] bg-stone-100 text-stone-500 px-1.5 py-0.5 rounded-full">
              {versions.length}
            </span>
          )}
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
        ) : versions.length === 0 ? (
          <div className="text-xs text-stone-400 text-center py-6">
            暂无历史版本
          </div>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-1.5 top-2 bottom-2 w-px bg-stone-200" />
            {versions.map((entry, idx) => {
              const isLatest = idx === 0;
              const isCurrent = entry.version === currentVersion;
              const rel = relativeTime(entry.create_time);
              return (
                <div key={entry.id} className="relative pl-5 py-2">
                  <div
                    className={`absolute left-0.5 top-3 size-2 rounded-full ring-1 ring-white ${
                      isCurrent ? 'bg-emerald-500' : 'bg-stone-300'
                    }`}
                  />
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-baseline gap-1.5 min-w-0">
                      <span className="text-xs font-medium text-stone-800 shrink-0">
                        v{entry.version}
                      </span>
                      {isCurrent && (
                        <span className="text-[10px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded-full shrink-0">
                          当前
                        </span>
                      )}
                      {isLatest && !isCurrent && (
                        <span className="text-[10px] text-stone-400 shrink-0">
                          最新保存
                        </span>
                      )}
                    </div>
                    {!isCurrent && (
                      <button
                        className="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-stone-600 border border-stone-200 rounded hover:bg-stone-50 disabled:opacity-50 transition-colors"
                        onClick={() => handleRestore(entry)}
                        disabled={restoringId !== null}
                        title={`恢复到 v${entry.version}`}
                      >
                        <RotateCcw className="size-3" />
                        {restoringId === entry.id ? '恢复中...' : '恢复'}
                      </button>
                    )}
                  </div>
                  <div className="text-[10px] text-stone-500 mt-0.5 truncate">
                    {entry.user_name || '未知用户'}
                  </div>
                  <div className="text-[10px] text-stone-400 mt-0.5 flex items-center gap-1.5">
                    <span>{formatTime(entry.create_time)}</span>
                    {rel && <span className="text-stone-300">· {rel}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!loading && versions.length > 0 && (
          <p className="text-[10px] text-stone-400 leading-relaxed mt-3 pt-3 border-t border-stone-100">
            每次保存会生成一条快照，系统最多保留最新 20
            条。恢复时当前状态会自动存档，便于反悔。
          </p>
        )}
      </div>
    </div>
  );
}
