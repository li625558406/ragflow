// web/src/hooks/use-notification-permission.ts
import { useEffect, useState } from 'react';

const LS_DENIED = 'notif:permission:denied';

export function useNotificationPermission() {
  const [granted, setGranted] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied',
  );

  useEffect(() => {
    if (typeof Notification === 'undefined') return;
    let cancelled = false;
    if (
      Notification.permission === 'default' &&
      !localStorage.getItem(LS_DENIED)
    ) {
      Notification.requestPermission().then((p) => {
        if (!cancelled) setGranted(p);
      });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const isGranted = granted === 'granted';

  const showNotification = (
    title: string,
    body: string,
    onClick?: () => void,
  ) => {
    if (!isGranted) return;
    try {
      const n = new Notification(title, { body });
      if (onClick) n.onclick = onClick;
    } catch {
      // 静默
    }
  };

  return { isGranted, showNotification };
}
