import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import request from '@/utils/request';

const {
  listScheduledTasks,
  createScheduledTask,
  updateScheduledTask: updateScheduledTaskApi,
  deleteScheduledTask,
  getScheduledTask,
  runScheduledTaskNow,
  listScheduledTaskLogs,
} = api;

const methods = {
  listScheduledTasks: {
    url: listScheduledTasks,
    method: 'get',
  },
  createScheduledTask: {
    url: createScheduledTask,
    method: 'post',
  },
  getScheduledTask: {
    url: getScheduledTask,
    method: 'get',
  },
  deleteScheduledTask: {
    url: deleteScheduledTask,
    method: 'delete',
  },
  runScheduledTaskNow: {
    url: runScheduledTaskNow,
    method: 'post',
  },
} as const;

const scheduledTaskService = registerNextServer<keyof typeof methods>(methods);

export const updateScheduledTask = (
  id: string,
  params: Record<string, any>,
) => {
  return request(updateScheduledTaskApi(id), { method: 'put', data: params });
};

export const toggleScheduledTask = (id: string, enabled: boolean) => {
  return request(api.toggleScheduledTask(id), {
    method: 'post',
    data: { enabled },
  });
};

export const stopScheduledTask = (id: string) => {
  return request(api.stopScheduledTask(id), { method: 'post' });
};

export const fetchScheduledTaskLogs = (
  taskId: string,
  params: { page?: number; items_per_page?: number },
) => {
  return request.get(listScheduledTaskLogs(taskId), { params });
};

/** Fetch the crawler state file contents */
export const fetchCrawlerState = (taskId: string) => {
  return request.get(api.fetchCrawlerState(taskId));
};

/** Update the crawler state file */
export const updateCrawlerState = (
  taskId: string,
  data: Record<string, any>,
) => {
  return request.put(api.updateCrawlerState(taskId), { data });
};

/** Fetch the list of available LLM models for image analysis */
export const fetchLlmModels = async (): Promise<any[]> => {
  const { data: res } = await request.get(api.myLlm);
  if (res?.code !== 0) return [];
  const llmMap: Record<string, any> = res.data ?? {};
  const models: any[] = [];
  for (const [fid, info] of Object.entries(llmMap)) {
    const llmList = (info as any)?.llm;
    if (Array.isArray(llmList)) {
      for (const llm of llmList) {
        models.push({
          fid,
          llm_name: llm.llm_name ?? llm.name,
          name: `${llm.llm_name ?? llm.name}`,
        });
      }
    }
  }
  return models;
};

/** Fetch the list of knowledge bases */
export const fetchKnowledgeBases = async (): Promise<any[]> => {
  const { data: res } = await request.get(api.kbList, {
    params: { page: 1, page_size: 9999 },
  });
  if (res?.code !== 0) return [];
  return res.data ?? [];
};

export default scheduledTaskService;
