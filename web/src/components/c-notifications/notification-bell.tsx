import { useNotificationPermission } from '@/hooks/use-notification-permission';
import { useUnreadNotifications } from '@/hooks/use-unread-notifications';
import {
  getUnreadList,
  type NotificationItem,
} from '@/services/c-notification-service';
import { useEffect, useState } from 'react';
import { NotificationDetailDialog } from './notification-detail-dialog';
import { NotificationDropdown } from './notification-dropdown';
import { NotificationModal } from './notification-modal';
import { NotificationSettingsDialog } from './notification-settings-dialog';

export function NotificationBell() {
  const { count, hasNew, setPrevCount } = useUnreadNotifications();
  const { isGranted, showNotification } = useNotificationPermission();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalItem, setModalItem] = useState<NotificationItem | null>(null);
  const [detailItem, setDetailItem] = useState<NotificationItem | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 有新通知时：弹浏览器原生 + 强制 Modal
  useEffect(() => {
    if (!hasNew || count === 0) return;
    let cancelled = false;
    (async () => {
      const { list } = await getUnreadList(1, 1);
      if (cancelled) return;
      const latest = list[0];
      if (!latest) return;
      if (isGranted) {
        showNotification(
          `${latest.site_display} 检测到 ${latest.result_count} 条新结果`,
          latest.summary,
          () => setModalOpen(true),
        );
      }
      setModalItem(latest);
      setModalOpen(true);
      setPrevCount(count);
    })();
    return () => {
      cancelled = true;
    };
  }, [hasNew, count, isGranted, showNotification, setPrevCount]);

  return (
    <div className="relative">
      <button
        onClick={() => setDropdownOpen((v) => !v)}
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
        >
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center bg-red-500 text-white text-[10px] rounded-full">
            {count > 99 ? '99+' : count}
          </span>
        )}
      </button>

      <NotificationDropdown
        open={dropdownOpen}
        onClose={() => setDropdownOpen(false)}
        onOpenDetail={(n) => {
          setDropdownOpen(false);
          setDetailItem(n);
        }}
        onOpenSettings={() => {
          setDropdownOpen(false);
          setSettingsOpen(true);
        }}
      />

      {modalOpen && modalItem && (
        <NotificationModal
          item={modalItem}
          onClose={() => setModalOpen(false)}
          onViewDetail={() => {
            setDetailItem(modalItem);
            setModalOpen(false);
          }}
        />
      )}

      {detailItem && (
        <NotificationDetailDialog
          item={detailItem}
          onClose={() => setDetailItem(null)}
        />
      )}

      {settingsOpen && (
        <NotificationSettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}
