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
