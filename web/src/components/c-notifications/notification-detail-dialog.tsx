import {
  getNotificationDetail,
  type NotificationItem,
} from '@/services/c-notification-service';
import { useEffect, useState } from 'react';

interface Props {
  item: NotificationItem;
  onClose: () => void;
}

export function NotificationDetailDialog({ item, onClose }: Props) {
  const [detail, setDetail] = useState<
    NotificationItem & { markdown?: string; source_url?: string }
  >(item);

  useEffect(() => {
    getNotificationDetail(item.id).then((d) => setDetail(d));
  }, [item.id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[680px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold text-base">{detail.title}</span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700"
          >
            ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <div className="flex gap-3 text-sm text-gray-600">
            <span>类型：{detail.category}</span>
            <span>站点：{detail.site_display}</span>
            <span>发布时间：{detail.publish_range}</span>
          </div>
          {detail.source_url && (
            <a
              href={detail.source_url}
              target="_blank"
              rel="noreferrer"
              className="inline-block text-sm text-blue-600 hover:underline"
            >
              查看原文 →
            </a>
          )}
          {detail.summary && (
            <pre className="bg-gray-50 p-3 rounded text-sm whitespace-pre-wrap font-sans">
              {detail.summary}
            </pre>
          )}
          {detail.markdown && (
            <div className="text-sm text-gray-700 whitespace-pre-wrap">
              {detail.markdown}
            </div>
          )}
        </div>
        <div className="flex justify-end px-5 py-3 border-t">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
