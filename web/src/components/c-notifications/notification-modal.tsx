import { markDeliveredBatch } from '@/hooks/use-unread-notifications';
import type { NotificationItem } from '@/services/c-notification-service';

interface Props {
  items: NotificationItem[];
  onClose: () => void;
  onViewAll: () => void;
}

const PREVIEW_MAX = 5;

export function NotificationModal({ items, onClose, onViewAll }: Props) {
  const total = items.length;
  const preview = items.slice(0, PREVIEW_MAX);
  const moreCount = Math.max(0, total - PREVIEW_MAX);

  const handleLater = () => {
    markDeliveredBatch(items.map((i) => i.id));
    onClose();
  };
  const handleViewAll = () => {
    markDeliveredBatch(items.map((i) => i.id));
    onViewAll();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[680px] bg-white rounded-xl shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="flex items-center gap-2">
            <span className="text-xl">🔔</span>
            <span className="font-semibold">检测到 {total} 条新采集结果</span>
          </div>
          <button
            onClick={handleLater}
            className="text-gray-400 hover:text-gray-700"
            aria-label="close"
          >
            ×
          </button>
        </div>

        <div className="px-5 py-4 space-y-2">
          {preview.map((n) => (
            <div
              key={n.id}
              className="border rounded-lg p-3 hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
                  {n.category}
                </span>
                <span className="text-xs text-gray-500">{n.site_display}</span>
              </div>
              <div className="text-sm font-medium text-gray-800 line-clamp-1">
                {n.title}
              </div>
              {n.publish_range && (
                <div className="text-xs text-gray-400 mt-1">
                  发布时间：{n.publish_range}
                </div>
              )}
            </div>
          ))}
          {moreCount > 0 && (
            <div className="text-center text-xs text-gray-400 py-1">
              +{moreCount} 更多
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t">
          <button
            onClick={handleLater}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            稍后查看
          </button>
          <button
            onClick={handleViewAll}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            查看全部 →
          </button>
        </div>
      </div>
    </div>
  );
}
