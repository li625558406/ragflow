import { Clock, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface AuditLogEntry {
  id: string;
  user_id: string;
  action: string;
  detail: Record<string, unknown>;
  ip_address: string | null;
  create_time: number;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
}

const ACTION_LABELS: Record<string, string> = {
  'document.update': '编辑文档',
  'document.delete': '删除文档',
  'comment.create': '添加评论',
  'comment.update': '编辑评论',
  'comment.delete': '删除评论',
  'comment.resolve': '解决评论',
  'share.create': '创建分享链接',
  'share.update': '更新分享链接',
  'share.delete': '删除分享链接',
  'attachment.upload': '上传附件',
  'attachment.delete': '删除附件',
  'version.restore': '恢复版本',
};

function formatTime(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
}

function formatDetail(action: string, detail: Record<string, unknown>): string {
  if (!detail || Object.keys(detail).length === 0) return '';
  if (action === 'document.update' && detail.fields) {
    const fields = detail.fields as string[];
    return `修改了: ${fields.join(', ')}`;
  }
  if (action === 'attachment.upload' || action === 'attachment.delete') {
    return `文件: ${detail.file_name || ''}`;
  }
  if (action === 'document.delete') {
    return `文档: ${detail.name || ''}`;
  }
  if (action === 'version.restore') {
    return `恢复到 v${detail.version || ''}`;
  }
  return '';
}

export default function AuditLogPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/audit-logs?page=1&page_size=50`,
      );
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        setLogs(result.data.logs || []);
        setTotal(result.data.total || 0);
      }
    } catch (e) {
      console.error('Failed to load audit logs:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) {
      loadLogs();
    }
  }, [docId, open, loadLogs]);

  if (!open) return null;

  return (
    <div className="w-full flex flex-col bg-white h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5">
          <Clock className="size-3.5 text-stone-500" />
          <span className="text-xs font-semibold text-stone-700">操作记录</span>
          {total > 0 && (
            <span className="text-[10px] bg-stone-100 text-stone-500 px-1.5 py-0.5 rounded-full">
              {total}
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

      {/* Log list */}
      <div className="flex-1 overflow-y-auto px-3">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 border-2 border-stone-200 border-t-stone-400 rounded-full animate-spin" />
          </div>
        ) : logs.length === 0 ? (
          <div className="text-center py-8 text-xs text-stone-400">
            暂无操作记录
          </div>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-1.5 top-2 bottom-2 w-px bg-stone-200" />
            {logs.map((log) => (
              <div key={log.id} className="relative pl-5 py-2">
                {/* Timeline dot */}
                <div className="absolute left-0.5 top-3 size-2 rounded-full bg-stone-300 ring-1 ring-white" />
                <div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-medium text-stone-700">
                      {log.user_id}
                    </span>
                    <span className="text-[10px] text-stone-400">
                      {ACTION_LABELS[log.action] || log.action}
                    </span>
                  </div>
                  {log.detail && formatDetail(log.action, log.detail) && (
                    <p className="text-[10px] text-stone-400 mt-0.5">
                      {formatDetail(log.action, log.detail)}
                    </p>
                  )}
                  <p className="text-[10px] text-stone-300 mt-0.5">
                    {formatTime(log.create_time)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
