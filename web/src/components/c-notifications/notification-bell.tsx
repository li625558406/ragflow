import { useNotificationPermission } from '@/hooks/use-notification-permission';
import {
  loadDelivered,
  useUnreadNotifications,
} from '@/hooks/use-unread-notifications';
import {
  getUnreadList,
  type NotificationItem,
  type NotificationResult,
} from '@/services/c-notification-service';
import { useCallback, useEffect, useState } from 'react';
import { NotificationDetailDialog } from './notification-detail-dialog';
import { NotificationListModal } from './notification-list-modal';
import { NotificationModal } from './notification-modal';
import { NotificationResultsModal } from './notification-results-modal';
import { NotificationSettingsDialog } from './notification-settings-dialog';

const FRESH_FETCH_SIZE = 50;

export function NotificationBell() {
  const { count, hasNew, setPrevCount, refresh } = useUnreadNotifications();
  const { isGranted, showNotification } = useNotificationPermission();

  // 一级：通知列表
  const [listModalOpen, setListModalOpen] = useState(false);
  // 二级：某通知下的结果列表
  const [resultsNotif, setResultsNotif] = useState<NotificationItem | null>(
    null,
  );
  // 三级：单条结果详情
  const [detailResult, setDetailResult] = useState<NotificationResult | null>(
    null,
  );
  // 汇总框（增量推送时弹）
  const [newItemsModalOpen, setNewItemsModalOpen] = useState(false);
  const [newItems, setNewItems] = useState<NotificationItem[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 新消息到达时触发铃铛摇动 + 徽章脉冲 (2s)
  const [isAnimating, setIsAnimating] = useState(false);

  // 当某通知下结果被全部查看 → 后端已标 is_read → 立即拉取真实未读数同步铃铛
  const handleAllResultsRead = useCallback(() => {
    refresh();
  }, [refresh]);

  // 增量触发：count 增加时拉全部未读，过滤本会话已 delivered 的，弹汇总框
  useEffect(() => {
    // count 归零（全部已阅/清空）时立即停止动画，否则会卡在跳动状态
    if (count === 0) {
      setIsAnimating(false);
      return;
    }
    if (!hasNew) return;
    setPrevCount(count);
    // 立即触发铃铛动画 (即使后续 getUnreadList 还没回来, 也先吸引视线)
    setIsAnimating(true);
    let cancelled = false;
    const animTimer = setTimeout(() => {
      if (!cancelled) setIsAnimating(false);
    }, 2000);
    (async () => {
      const { list } = await getUnreadList(1, FRESH_FETCH_SIZE);
      if (cancelled) return;
      const delivered = loadDelivered();
      const fresh = list.filter((n) => !delivered.has(n.id));
      if (fresh.length === 0) return;
      setNewItems(fresh);
      setNewItemsModalOpen(true);
      if (isGranted) {
        const firstFlow = fresh[0].category === 'flow';
        const firstSite = fresh[0].site_display || '采集结果';
        const title =
          firstFlow && fresh.length === 1
            ? fresh[0].title
            : fresh.length === 1
              ? `${firstSite} 检测到 ${fresh[0].result_count} 条新结果`
              : `${firstSite} 等 ${fresh.length} 条新通知`;
        const body = firstFlow
          ? fresh[0].summary
          : fresh
              .map((n) => n.title)
              .slice(0, 3)
              .join('\n');
        showNotification(title, body, () => setListModalOpen(true));
      }
    })();
    return () => {
      cancelled = true;
      clearTimeout(animTimer);
    };
  }, [hasNew, count, isGranted, showNotification, setPrevCount]);

  return (
    <div className="relative">
      <button
        onClick={() => setListModalOpen(true)}
        className="relative p-2 rounded-full hover:bg-gray-100"
        title="采集通知"
      >
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`origin-top ${isAnimating ? 'animate-bounce' : ''}`}
          style={isAnimating ? { animationDuration: '0.6s' } : undefined}
        >
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {count > 0 && (
          <>
            {/* 新消息到达时徽章外圈 ping 脉冲, 提醒用户视线 */}
            {isAnimating && (
              <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] rounded-full bg-red-500/70 animate-ping" />
            )}
            <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center bg-red-500 text-white text-[10px] rounded-full">
              {count > 99 ? '99+' : count}
            </span>
          </>
        )}
      </button>

      {newItemsModalOpen && newItems.length > 0 && (
        <NotificationModal
          items={newItems}
          onClose={() => setNewItemsModalOpen(false)}
          onViewAll={() => setListModalOpen(true)}
        />
      )}

      {/* 一级：通知列表 */}
      <NotificationListModal
        open={listModalOpen}
        onClose={() => setListModalOpen(false)}
        onOpenDetail={(n) => {
          // 不关列表，二级叠开
          setResultsNotif(n);
        }}
        onOpenSettings={() => {
          setListModalOpen(false);
          setSettingsOpen(true);
        }}
        refresh={refresh}
      />

      {/* 二级：某通知下的结果列表 */}
      <NotificationResultsModal
        notification={resultsNotif}
        onClose={() => setResultsNotif(null)}
        onOpenDetail={(r) => {
          // 不关二级，三级叠开
          setDetailResult(r);
        }}
        onAllRead={handleAllResultsRead}
      />

      {/* 三级：单条结果详情 */}
      {detailResult && (
        <NotificationDetailDialog
          result={detailResult}
          onClose={() => setDetailResult(null)}
        />
      )}

      {settingsOpen && (
        <NotificationSettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}
