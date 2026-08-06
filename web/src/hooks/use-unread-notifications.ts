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

export function markDeliveredBatch(ids: string[]) {
  if (!ids.length) return;
  const s = loadDelivered();
  ids.forEach((id) => s.add(id));
  sessionStorage.setItem(LS_DELIVERED, JSON.stringify([...s]));
}

export function useUnreadNotifications() {
  const [count, setCount] = useState(0);
  const [prevCount, setPrevCount] = useState(0);
  const timerRef = useRef<number | null>(null);

  // Stable tick fn — can be called imperatively (refresh) or by the interval.
  // mountedRef guards against setState-after-unmount for the in-flight fetch
  // (clearInterval only cancels future ticks, not the current one).
  const mountedRef = useRef(true);
  const tick = useCallback(async () => {
    try {
      const { count: c } = await getUnreadCount();
      if (!mountedRef.current) return;
      // prevCount is consumer-owned; the bell acknowledges via setPrevCount(count).
      setCount(c);
    } catch {
      // 静默
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    tick();
    timerRef.current = window.setInterval(tick, POLL_MS);
    return () => {
      mountedRef.current = false;
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [tick]);

  const hasNew = count > prevCount;
  return { count, prevCount, hasNew, setPrevCount, refresh: tick };
}
