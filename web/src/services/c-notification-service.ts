// web/src/services/c-notification-service.ts
const BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('Authorization') || '';
  return { 'Content-Type': 'application/json', Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}');
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}${path}${path.includes('?') ? '&' : '?'}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`notification api ${resp.status}`);
  return (await resp.json()) as T;
}

export interface NotificationItem {
  id: string;
  site_id: string;
  site_display: string;
  category: string;
  title: string;
  summary: string;
  result_ids: string[];
  result_count: number;
  publish_range: string;
  created_at: number;
  is_read: boolean;
}

export interface Subscription {
  site_ids: string[];
  categories: string[];
  browser_push: boolean;
  force_modal: boolean;
}

export async function getUnreadCount(): Promise<{ count: number }> {
  return apiFetch('/notifications/unread/count');
}

export async function getUnreadList(
  page = 1,
  pageSize = 20,
): Promise<{ list: NotificationItem[]; total: number }> {
  return apiFetch(`/notifications/unread?page=${page}&page_size=${pageSize}`);
}

export async function getNotificationDetail(
  id: string,
): Promise<NotificationItem & { markdown?: string; source_url?: string }> {
  return apiFetch(`/notifications/${id}`);
}

export async function markOneRead(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`/notifications/${id}/read`, { method: 'POST' });
}

export async function markAllRead(): Promise<{ updated: number }> {
  return apiFetch(`/notifications/read-all`, { method: 'POST' });
}

export async function batchRead(ids: string[]): Promise<{ updated: number }> {
  return apiFetch(`/notifications/batch-read`, {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export async function getSubscription(): Promise<Subscription> {
  return apiFetch(`/notifications/subscription`);
}

export async function putSubscription(
  sub: Partial<Subscription>,
): Promise<{ id: string }> {
  return apiFetch(`/notifications/subscription`, {
    method: 'PUT',
    body: JSON.stringify(sub),
  });
}
