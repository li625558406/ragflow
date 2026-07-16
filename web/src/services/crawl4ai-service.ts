import api from '@/utils/api';
import request from '@/utils/request';

export interface Crawl4aiTask {
  id: string;
  name: string;
  description?: string;
  site_id: string;
  target_url: string;
  page_url_template?: string;
  start_page?: number;
  max_pages?: number;
  extraction_schema?: Record<string, any>;
  detail_config?: Record<string, any>;
  headers?: Record<string, any>;
  output_targets?: string[];
  kb_id?: string;
  parser_id?: string;
  enabled?: boolean;
  last_run_time?: number | null;
  last_run_status?: string;
  last_run_summary?: Record<string, any>;
}

export interface Crawl4aiResult {
  id: string;
  task_id: string;
  site_id: string;
  title: string;
  source_url: string;
  publish_date?: string;
  markdown?: string;
  extracted_json?: Record<string, any>;
  attachments?: Array<{
    file_name: string;
    file_url: string;
    kb_doc_id?: string;
    status?: string;
  }>;
  status: string;
  kb_doc_id?: string;
  error_msg?: string;
  crawled_at?: number;
}

export const listCrawl4aiTasks = (params: Record<string, any>) =>
  request.get(api.listCrawl4aiTasks, { params });

export const createCrawl4aiTask = (data: Record<string, any>) =>
  request.post(api.createCrawl4aiTask, { data });

export const updateCrawl4aiTask = (id: string, data: Record<string, any>) =>
  request.put(api.updateCrawl4aiTask(id), { data });

export const deleteCrawl4aiTask = (id: string) =>
  request.delete(api.deleteCrawl4aiTask(id));

export const triggerCrawl4aiTask = (id: string) =>
  request.post(api.triggerCrawl4aiTask(id));

export const getCrawl4aiTaskStatus = (id: string) =>
  request.get(api.getCrawl4aiTaskStatus(id));

export const listCrawl4aiResults = (params: Record<string, any>) =>
  request.get(api.listCrawl4aiResults, { params });

export const getCrawl4aiResult = (id: string) =>
  request.get(api.getCrawl4aiResult(id));

export const listCrawl4aiSites = () => request.get(api.listCrawl4aiSites);
