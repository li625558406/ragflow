import api from '@/utils/api';
import request from '@/utils/request';

export interface CollectionResult {
  id: string;
  task_id: string;
  tenant_id: string;
  site_id: string;
  site_display: string;
  site_name: string;
  site_domain: string;
  category: string;
  category_label: string;
  section_name?: string;
  title: string;
  source_url: string;
  publish_date: string;
  crawled_at: number;
  status: string;
  kb_doc_id: string;
  error_msg: string;
  ext?: Record<string, any>;
  markdown?: string;
  attachments?: Array<{
    file_name: string;
    file_url: string;
    file_suffix?: string;
    file_size?: number;
  }>;
}

export interface CollectionResultList {
  list: CollectionResult[];
  total: number;
}

export interface CollectionCategoryStat {
  category: string;
  category_label: string;
  count: number;
}

export const listCollectionResults = (params: {
  page?: number;
  page_size?: number;
  category?: string;
  site_id?: string;
  keyword?: string;
  start_date?: string;
  end_date?: string;
  with_ext?: string;
}) => request.get(api.collectionResults, { params });

export const getCollectionResult = (id: string) =>
  request.get(api.collectionResult(id));

export const fetchCollectionStats = () => request.get(api.collectionStats);

// ---------------------------------------------------------------------------
// 探测监控 (Detector monitor)
// ---------------------------------------------------------------------------

export interface DetectStateRow {
  site_id: string;
  name: string;
  site_url: string;
  category: string;
  enabled: boolean;
  detect_interval: number;
  detect_max_interval: number;
  detect_min_interval: number;
  detect_quiet_hours: string;
  next_run_at: number;
  next_run_in_sec: number;
  last_check: number;
  last_check_ago_sec: number;
  miss_count: number;
  cur_interval: number;
  last_sig: string;
  last_new_count: number;
  consecutive_errors: number;
  last_reason: string;
  last_error: string;
  last_enqueue_ok: boolean | null;
  status: string;
}

export interface DetectStateList {
  list: DetectStateRow[];
  total: number;
  now: number;
}

export interface DetectStats {
  total: number;
  buckets: Record<string, number>;
  avg_interval: number;
  now: number;
}

export interface DetectActivityItem {
  site_id: string;
  site_name: string;
  category: string;
  count: number;
  first_at_ms: number;
  last_at_ms: number;
  last_title: string;
}

export interface DetectActivity {
  now_ms: number;
  window: number;
  items: DetectActivityItem[];
  total_count: number;
}

export const listDetectState = (params: {
  page?: number;
  page_size?: number;
  category?: string;
  status?: string;
}) => request.get(api.collectionDetectState, { params });

export const fetchDetectStats = () => request.get(api.collectionDetectStats);

export const fetchDetectActivity = (window_sec = 3600, limit = 20) =>
  request.get(api.collectionDetectActivity, {
    params: { window: window_sec, limit },
  });

export const resetDetect = (site_id: string) =>
  request.post(api.collectionDetectReset, { site_id });

export const disableDetect = (site_id: string) =>
  request.post(api.collectionDetectDisable, { site_id });

export const enableDetect = (site_id: string) =>
  request.post(api.collectionDetectEnable, { site_id });

export const triggerDetect = (site_id: string) =>
  request.post(api.collectionDetectTrigger, { site_id });

export const installDetect = (interval_seconds = 60) =>
  // kb_id 已废弃 —— 探测器不再消费 kb_id,爬虫脚本按 site_id 查 crawler_task 表自动获取
  request.post(api.collectionDetectInstall, { interval_seconds });
