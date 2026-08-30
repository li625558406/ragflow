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
  independent_task?: {
    last_crawled_at: number | null;
    result_count: number;
    last_status: string;
    site_display: string;
  };
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
  request.post(api.collectionDetectReset, { data: { site_id } });

export const disableDetect = (site_id: string) =>
  request.post(api.collectionDetectDisable, { data: { site_id } });

export const enableDetect = (site_id: string) =>
  request.post(api.collectionDetectEnable, { data: { site_id } });

export const triggerDetect = (site_id: string) =>
  request.post(api.collectionDetectTrigger, { data: { site_id } });

export const installDetect = (interval_seconds = 60) =>
  // kb_id 已废弃 —— 探测器不再消费 kb_id,爬虫脚本按 site_id 查 crawler_task 表自动获取
  request.post(api.collectionDetectInstall, { data: { interval_seconds } });

// ---------------------------------------------------------------------------
// 解析监控 (Parse monitor)
// ---------------------------------------------------------------------------

export interface ParseMonitorOverview {
  now: number;
  cached_at: number;
  states: Record<string, number>;
  total: number;
  running: number;
  done: number;
  failed: number;
  backlog: number;
  done_last_1h: number;
  rate_per_min: number;
  eta_sec: number;
}

export interface ReparseBatchItem {
  ts: number;
  total: number;
  success: number;
  failed: number;
  skipped: number;
  duration_sec: number;
  first_errors: Array<{ doc_id: string; msg: string }>;
}

export interface ReparseBatchList {
  list: ReparseBatchItem[];
  now: number;
}

export interface FailedDocRow {
  id: string;
  kb_id: string;
  kb_name: string;
  name: string;
  run: string;
  progress: number;
  progress_msg: string;
  reason_key: string;
  reason: string;
  reason_color: 'amber' | 'gray' | 'red' | 'orange';
  update_time: number;
  process_begin_at: number;
}

export interface FailedDocList {
  list: FailedDocRow[];
  total: number;
  page: number;
  page_size: number;
}

export const fetchParseMonitorOverview = () =>
  request.get(api.collectionParseMonitorOverview);

export const fetchReparseBatches = () =>
  request.get(api.collectionParseMonitorBatches);

export const listFailedDocs = (params: {
  page?: number;
  page_size?: number;
  status?: string; // 'fail' | 'stuck' | '' (all)
  kb_id?: string;
  reason_key?: string; // 'embedding_api' | 'unsupported_filetype' | ... | 'other' | ''
}) => request.get(api.collectionParseMonitorFailedDocs, { params });

export interface RerunFailedResult {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  duration_sec: number;
  first_errors: { doc_id: string; msg: string }[];
}

export const rerunFailedDocs = (body: {
  reason_key?: string;
  kb_id?: string;
  limit?: number;
}) => request.post(api.collectionParseMonitorRerunFailed, { data: body });
