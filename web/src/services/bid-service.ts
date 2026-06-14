import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import request from '@/utils/request';

const { bidProjects, bidProject, bidSyncLogs, bidStats, bidCrawlerStats } = api;

const methods = {
  listBidProjects: {
    url: bidProjects,
    method: 'get',
  },
  bidSyncLogs: {
    url: bidSyncLogs,
    method: 'get',
  },
  bidStats: {
    url: bidStats,
    method: 'get',
  },
  bidCrawlerStats: {
    url: bidCrawlerStats,
    method: 'get',
  },
} as const;

const bidService = registerNextServer<keyof typeof methods>(methods);

export const fetchBidProject = (id: number) => {
  return request.get(bidProject(id));
};

export const fetchBidProjectDetail = (id: number, publishTime: string) => {
  return request.get(api.bidProjectDetail(id), {
    params: { publish_time: publishTime },
  });
};

export const fetchBidProjectStructure = (id: number, publishTime: string) => {
  return request.get(api.bidProjectStructure(id), {
    params: { publish_time: publishTime },
  });
};

export const fetchBidProjectFiles = (id: number, publishTime: string) => {
  return request.get(api.bidProjectFiles(id), {
    params: { publish_time: publishTime },
  });
};

export const triggerBidParse = (id: number, kb_id: string) => {
  return request.post(api.bidProjectParse(id), { data: { kb_id } });
};

export const fetchBidParseStatus = (id: number) => {
  return request.get(api.bidProjectParseStatus(id));
};

export default bidService;
