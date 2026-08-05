import request from '@/utils/request';

const PREFIX = '/api/v1/admin/notifications';

export interface AdminNotificationItem {
  id: string;
  site_id: string;
  site_display: string;
  category: string;
  batch_key: string;
  title: string;
  summary: string;
  result_ids: string[];
  result_count: number;
  publish_range: string;
  created_at: number;
  pushed_count?: number;
  read_count?: number;
}

export interface AdminNotificationStats {
  today_created: number;
  week_pushed: number;
  week_read: number;
  read_rate: number;
}

export interface AdminNotificationConfig {
  scan_interval: number;
  retention_days: number;
}

export async function adminListNotifications(params: {
  page?: number;
  page_size?: number;
  site_id?: string;
  category?: string;
}) {
  return request.get(PREFIX, { params });
}

export async function adminGetNotification(id: string) {
  return request.get(`${PREFIX}/${id}`);
}

export async function adminDeleteNotification(id: string) {
  return request.delete(`${PREFIX}/${id}`);
}

export async function adminStats() {
  return request.get(`${PREFIX}/stats`);
}

export async function adminGetConfig() {
  return request.get(`${PREFIX}/config`);
}

export async function adminPutConfig(config: Partial<AdminNotificationConfig>) {
  return request.put(`${PREFIX}/config`, { data: config });
}
