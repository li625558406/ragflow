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
  refresh: () => Promise<void>;
}

const PAGE_SIZE = 50;

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

export function NotificationListModal({
  open,
  onClose,
  onOpenDetail,
  onOpenSettings,
  refresh,
}: Props) {
  const [list, setList] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getUnreadList(1, PAGE_SIZE)
      .then(({ list, total }) => {
        setList(list);
        setTotal(total);
      })
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  const handleMarkOneRead = async (id: string) => {
    await markOneRead(id);
    await refresh();
    setList((p) => p.filter((x) => x.id !== id));
    setTotal((t) => Math.max(0, t - 1));
  };

  const handleMarkAllRead = async () => {
    await markAllRead();
    await refresh();
    setList([]);
    setTotal(0);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[860px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold">未读通知 ({total})</span>
          <div className="flex items-center gap-3">
            <button
              className="text-xs text-gray-500 hover:text-gray-800"
              onClick={onOpenSettings}
            >
              订阅设置
            </button>
            <button
              className="text-xs text-blue-600 hover:text-blue-800 disabled:text-gray-300"
              disabled={list.length === 0}
              onClick={handleMarkAllRead}
            >
              全部已阅
            </button>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-700"
              aria-label="关闭"
              title="关闭"
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
              暂无未读通知
            </div>
          )}
          {!loading &&
            list.map((n) => (
              <div
                key={n.id}
                className="px-3 py-2.5 hover:bg-gray-50 cursor-pointer rounded-lg border-b last:border-b-0"
                onClick={() => onOpenDetail(n)}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${categoryMeta(n.category).className}`}
                    >
                      {categoryMeta(n.category).label}
                    </span>
                    <span className="text-xs text-gray-500">
                      {n.site_display}
                    </span>
                  </div>
                  <button
                    className="text-xs text-blue-600 hover:text-blue-800"
                    onClick={async (e) => {
                      e.stopPropagation();
                      await handleMarkOneRead(n.id);
                    }}
                  >
                    已阅
                  </button>
                </div>
                <div className="text-sm font-medium text-gray-800 line-clamp-2">
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
