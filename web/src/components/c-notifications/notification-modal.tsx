import { markDeliveredBatch } from '@/hooks/use-unread-notifications';
import type { NotificationItem } from '@/services/c-notification-service';

interface Props {
  items: NotificationItem[];
  onClose: () => void;
  onViewAll: () => void;
}

const PREVIEW_MAX = 5;

const CATEGORY_META: Record<string, { label: string; className: string }> = {
  bid: { label: '招标采购', className: 'bg-blue-100 text-blue-700' },
  tender: { label: '招标公告', className: 'bg-cyan-100 text-cyan-700' },
  policy: { label: '政策法规', className: 'bg-purple-100 text-purple-700' },
  news: { label: '新闻动态', className: 'bg-amber-100 text-amber-700' },
  personnel: {
    label: '人事信息',
    className: 'bg-emerald-100 text-emerald-700',
  },
  objection: { label: '异议答复', className: 'bg-rose-100 text-rose-700' },
  other: { label: '其他', className: 'bg-gray-100 text-gray-700' },
};

function categoryMeta(c: string) {
  return (
    CATEGORY_META[c] || {
      label: c || '其他',
      className: 'bg-gray-100 text-gray-700',
    }
  );
}

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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[680px] bg-white rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="flex items-center gap-2">
            <span className="text-xl">🔔</span>
            <span className="font-semibold">
              检测到
              <span className="mx-1 text-blue-600 font-bold text-lg">
                {total}
              </span>
              条新采集结果
            </span>
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
                <span
                  className={`text-xs px-1.5 py-0.5 rounded ${categoryMeta(n.category).className}`}
                >
                  {categoryMeta(n.category).label}
                </span>
                <span className="text-xs text-gray-500">{n.site_display}</span>
              </div>
              <div className="text-sm font-medium text-gray-800 line-clamp-1">
                {n.site_display} 检测到
                <span className="mx-0.5 text-blue-600 font-bold">
                  {n.result_count}
                </span>
                条新结果
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
