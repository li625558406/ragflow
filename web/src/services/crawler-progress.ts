/**
 * Crawler task real-time progress WebSocket client.
 *
 * Connects to /api/v1/crawl4ai/tasks/<task_id>/ws and parses server messages
 * into typed events for the caller.
 */
import storage from '@/utils/authorization-util';

export interface TaskProgress {
  page: number;
  total_pages: number;
  new: number;
  scanned: number;
  ts?: number;
}

export interface TaskLog {
  level: 'info' | 'warning' | 'error';
  text: string;
  ts?: number;
}

export interface TaskDone {
  status: 'success' | 'fail' | 'skipped';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  summary: Record<string, any>;
  ts?: number;
}

export type ProgressMessage =
  | ({ type: 'progress' } & TaskProgress)
  | ({ type: 'log' } & TaskLog)
  | ({ type: 'done' } & TaskDone)
  | { type: 'history'; messages: ProgressMessage[] }
  | { type: 'ping' }
  | { type: 'error'; message: string };

export interface ProgressCallbacks {
  onProgress?: (p: TaskProgress) => void;
  onLog?: (l: TaskLog) => void;
  onDone?: (d: TaskDone) => void;
  onHistory?: (messages: ProgressMessage[]) => void;
  onError?: (message: string) => void;
  onConnectionChange?: (connected: boolean) => void;
}

function buildWsUrl(taskId: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const wsHost = (import.meta as any).env?.VITE_WS_HOST || '';
  const host = wsHost || location.host;
  // Strip "Bearer " prefix; backend accepts raw token
  const auth = storage.getAuthorization() || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : auth;
  return `${proto}//${host}/api/v1/crawl4ai/tasks/${encodeURIComponent(taskId)}/ws?token=${encodeURIComponent(token)}`;
}

export function connectTaskProgress(
  taskId: string,
  callbacks: ProgressCallbacks,
): () => void {
  let closed = false;
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const handleMessage = (raw: string) => {
    let msg: ProgressMessage;
    try {
      msg = JSON.parse(raw) as ProgressMessage;
    } catch {
      return;
    }
    switch (msg.type) {
      case 'progress':
        callbacks.onProgress?.(msg);
        break;
      case 'log':
        callbacks.onLog?.(msg);
        break;
      case 'done':
        callbacks.onDone?.(msg);
        break;
      case 'history':
        callbacks.onHistory?.(msg.messages ?? []);
        break;
      case 'error':
        callbacks.onError?.(msg.message);
        break;
      case 'ping':
        // heartbeat, ignore
        break;
    }
  };

  const connect = () => {
    if (closed) return;
    try {
      ws = new WebSocket(buildWsUrl(taskId));
    } catch (e) {
      console.error('[crawler-progress] WS construct failed', e);
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      callbacks.onConnectionChange?.(true);
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        handleMessage(ev.data);
      }
    };

    ws.onerror = (e) => {
      console.warn('[crawler-progress] WS error', e);
    };

    ws.onclose = () => {
      callbacks.onConnectionChange?.(false);
      ws = null;
      if (!closed) {
        scheduleReconnect();
      }
    };
  };

  const scheduleReconnect = () => {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 2000);
  };

  connect();

  // Return disconnect fn
  return () => {
    closed = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.onopen = null;
      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close();
      }
      ws = null;
    }
  };
}
