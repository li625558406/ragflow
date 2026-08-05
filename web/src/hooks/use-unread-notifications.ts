// web/src/hooks/use-unread-notifications.ts
import { getUnreadCount } from '@/services/c-notification-service';
import { useCallback, useEffect, useRef, useState } from 'react';

const POLL_MS = 30_000;
const LS_DELIVERED = 'notif:delivered';

export function loadDelivered(): Set<string> {
  try {
    const raw = sessionStorage.getItem(LS_DELIVERED) || '[]';
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

export function markDelivered(id: string) {
  const s = loadDelivered();
  s.add(id);
  sessionStorage.setItem(LS_DELIVERED, JSON.stringify([...s]));
}

export function useUnreadNotifications() {
  const [count, setCount] = useState(0);
  const [prevCount, setPrevCount] = useState(0);
  const timerRef = useRef<number | null>(null);

  // Stable tick fn — can be called imperatively (refresh) or by the interval.
  const tick = useCallback(async () => {
    try {
      const { count: c } = await getUnreadCount();
      // prevCount is consumer-owned; the bell acknowledges via setPrevCount(count).
      setCount(c);
    } catch {
      // 静默
    }
  }, []);

  useEffect(() => {
    tick();
    timerRef.current = window.setInterval(tick, POLL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [tick]);

  const hasNew = count > prevCount;
  return { count, prevCount, hasNew, setPrevCount, refresh: tick };
}
