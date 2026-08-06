import type { NotificationResult } from '@/services/c-notification-service';

interface Props {
  result: NotificationResult;
  onClose: () => void;
}

export function NotificationDetailDialog({ result, onClose }: Props) {
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40">
      <div className="w-[860px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold text-base truncate pr-4">
            {result.title || '(无标题)'}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <div className="flex flex-wrap gap-3 text-sm text-gray-600">
            {result.publish_date && (
              <span>发布时间：{result.publish_date}</span>
            )}
            {result.source_url && (
              <a
                href={result.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 hover:underline"
              >
                查看原文 ↗
              </a>
            )}
          </div>
          {result.markdown ? (
            <pre className="bg-gray-50 p-3 rounded text-sm whitespace-pre-wrap font-sans">
              {result.markdown}
            </pre>
          ) : (
            <div className="p-8 text-center text-sm text-gray-400">
              无正文内容
            </div>
          )}
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
