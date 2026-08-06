import {
  getNotificationResults,
  type NotificationItem,
  type NotificationResult,
} from '@/services/c-notification-service';
import { useEffect, useState } from 'react';

interface Props {
  /** 当前展开的 notification；变化时重新拉结果。null 时不渲染。 */
  notification: NotificationItem | null;
  onClose: () => void;
  onOpenDetail: (r: NotificationResult) => void;
}

const LS_READ = 'notif:result-read';

function loadRead(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(LS_READ) || '[]'));
  } catch {
    return new Set();
  }
}

function saveRead(s: Set<string>) {
  localStorage.setItem(LS_READ, JSON.stringify([...s]));
}

export function NotificationResultsModal({
  notification,
  onClose,
  onOpenDetail,
}: Props) {
  const [list, setList] = useState<NotificationResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [readSet, setReadSet] = useState<Set<string>>(() => loadRead());

  useEffect(() => {
    if (!notification) {
      setList([]);
      return;
    }
    setLoading(true);
    getNotificationResults(notification.id)
      .then(({ list }) => setList(list))
      .finally(() => setLoading(false));
  }, [notification]);

  if (!notification) return null;

  const markRead = (id: string) => {
    setReadSet((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      saveRead(next);
      return next;
    });
  };

  const handleClickItem = (r: NotificationResult) => {
    markRead(r.id);
    onOpenDetail(r);
  };

  const handleClickSource = (e: React.MouseEvent, r: NotificationResult) => {
    e.stopPropagation();
    markRead(r.id);
    // 不阻止默认行为：让浏览器正常打开新标签
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40">
      <div className="w-[860px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="min-w-0">
            <div className="font-semibold truncate">
              {notification.site_display} · 共{' '}
              {list.length || notification.result_count} 条结果
            </div>
            <div className="text-xs text-gray-500 truncate">
              {notification.publish_range}
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-700"
              aria-label="返回"
              title="返回"
            >
              ×
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-2">
          {loading && (
            <div className="p-8 text-center text-sm text-gray-400">加载中…</div>
          )}
          {!loading && list.length === 0 && (
            <div className="p-12 text-center text-sm text-gray-400">
              暂无结果数据
            </div>
          )}
          {!loading &&
            list.map((r) => {
              const read = readSet.has(r.id);
              return (
                <div
                  key={r.id}
                  className={`px-3 py-2.5 cursor-pointer rounded-lg border-b last:border-b-0 transition-colors ${
                    read
                      ? 'bg-gray-50 hover:bg-gray-100'
                      : 'hover:bg-blue-50/50 border-l-2 border-l-blue-500'
                  }`}
                  onClick={() => handleClickItem(r)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div
                      className={`text-sm font-medium line-clamp-2 flex-1 ${
                        read ? 'text-gray-400' : 'text-gray-800'
                      }`}
                    >
                      {r.title || '(无标题)'}
                    </div>
                    {read && (
                      <span className="text-xs px-1.5 py-0.5 bg-gray-200 text-gray-500 rounded shrink-0">
                        已读
                      </span>
                    )}
                  </div>
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-xs text-gray-400">
                      {r.publish_date || '未知日期'}
                    </span>
                    {r.source_url && (
                      <a
                        href={r.source_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => handleClickSource(e, r)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        原文 ↗
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
        </div>

        <div className="flex justify-end px-5 py-3 border-t">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            返回
          </button>
        </div>
      </div>
    </div>
  );
}
