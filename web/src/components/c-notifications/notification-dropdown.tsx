import {
  getUnreadList,
  markAllRead,
  markOneRead,
  type NotificationItem,
} from '@/services/c-notification-service';
import { useEffect, useState } from 'react';

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenDetail: (n: NotificationItem) => void;
  onOpenSettings: () => void;
}

export function NotificationDropdown({
  open,
  onClose,
  onOpenDetail,
  onOpenSettings,
}: Props) {
  const [list, setList] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getUnreadList(1, 10)
      .then(({ list, total }) => {
        setList(list);
        setTotal(total);
      })
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  return (
    <div className="absolute right-0 top-12 w-[420px] bg-white rounded-lg shadow-2xl border border-gray-200 z-50">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <span className="font-semibold text-sm">未读通知 ({total})</span>
        <div className="flex gap-2">
          <button
            className="text-xs text-gray-500 hover:text-gray-800"
            onClick={onOpenSettings}
          >
            订阅设置
          </button>
          <button
            className="text-xs text-blue-600 hover:text-blue-800"
            onClick={async () => {
              await markAllRead();
              setList([]);
              setTotal(0);
            }}
          >
            全部已阅
          </button>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {loading && <div className="p-4 text-sm text-gray-400">加载中…</div>}
        {!loading && list.length === 0 && (
          <div className="p-8 text-center text-sm text-gray-400">
            暂无未读通知
          </div>
        )}
        {!loading &&
          list.map((n) => (
            <div
              key={n.id}
              className="px-4 py-3 hover:bg-gray-50 cursor-pointer border-b last:border-b-0"
              onClick={() => onOpenDetail(n)}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
                  {n.category}
                </span>
                <span className="text-xs text-gray-400">{n.site_display}</span>
              </div>
              <div className="text-sm font-medium text-gray-800 line-clamp-2">
                {n.title}
              </div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-xs text-gray-400">{n.publish_range}</span>
                <button
                  className="text-xs text-blue-600 hover:text-blue-800"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await markOneRead(n.id);
                    setList((p) => p.filter((x) => x.id !== n.id));
                    setTotal((t) => Math.max(0, t - 1));
                  }}
                >
                  已阅
                </button>
              </div>
            </div>
          ))}
      </div>
      <button
        onClick={onClose}
        className="block w-full py-2 text-xs text-gray-500 hover:bg-gray-50"
      >
        关闭
      </button>
    </div>
  );
}
