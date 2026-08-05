import { markDelivered } from '@/hooks/use-unread-notifications';
import type { NotificationItem } from '@/services/c-notification-service';
import { markOneRead } from '@/services/c-notification-service';

interface Props {
  item: NotificationItem;
  onClose: () => void;
  onViewDetail: () => void;
}

export function NotificationModal({ item, onClose, onViewDetail }: Props) {
  const handleLater = () => {
    markDelivered(item.id);
    onClose();
  };
  const handleViewAndRead = async () => {
    await markOneRead(item.id);
    markDelivered(item.id);
    onViewDetail();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[480px] bg-white rounded-xl shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="flex items-center gap-2">
            <span className="text-xl">🔔</span>
            <span className="font-semibold">检测到新采集结果</span>
          </div>
          <button
            onClick={handleLater}
            className="text-gray-400 hover:text-gray-700"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4 space-y-2">
          <div className="flex gap-4 text-sm">
            <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded">
              {item.category}
            </span>
            <span className="text-gray-700">站点：{item.site_display}</span>
          </div>
          <div className="text-sm text-gray-600">
            新增 <b>{item.result_count}</b> 条，发布时间 {item.publish_range}
          </div>
          <div className="bg-gray-50 rounded p-3 text-sm space-y-1 max-h-[200px] overflow-y-auto">
            {item.summary.split('\n').map((line, i) => (
              <div key={i}>• {line}</div>
            ))}
            {item.result_count > 3 && (
              <div className="text-gray-400">+{item.result_count - 3} 更多</div>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t">
          <button
            onClick={handleLater}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            稍后查看
          </button>
          <button
            onClick={handleViewAndRead}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            查看详情并已阅
          </button>
        </div>
      </div>
    </div>
  );
}
