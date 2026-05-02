import api, { restAPIv1 } from '@/utils/api';
import request from '@/utils/request';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export interface AnalysisTemplate {
  id: string;
  name: string;
  doc_type: string;
  dimensions: string[];
  prompt_templates?: string;
  prompt_template?: string;  // 兼容旧字段
  llm_id?: string;
  is_system: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface AnalysisTemplateListResponse {
  data: AnalysisTemplate[];
  total: number;
}

export interface CreateTemplateParams {
  name: string;
  doc_type: string;
  dimensions?: string[];
  prompt_templates?: string;
  prompt_template?: string;  // 兼容旧字段
  llm_id?: string;
}

export interface UpdateTemplateParams {
  name?: string;
  doc_type?: string;
  dimensions?: string[];
  prompt_templates?: string;
  prompt_template?: string;  // 兼容旧字段
  llm_id?: string;
}

export interface ListTemplatesParams {
  doc_type?: string;
  page?: number;
  page_size?: number;
}

// 获取分析模板列表
export function useListAnalysisTemplates(params: ListTemplatesParams = {}, enabled: boolean = true) {
  return useQuery({
    queryKey: ['analysisTemplates', params],
    queryFn: async () => {
      const searchParams = new URLSearchParams();
      if (params.doc_type) searchParams.set('doc_type', params.doc_type);
      if (params.page) searchParams.set('page', String(params.page));
      if (params.page_size) searchParams.set('page_size', String(params.page_size));

      const url = `${api.listAnalysisTemplates}?${searchParams.toString()}`;
      const { data } = await request.get(url);
      return data as AnalysisTemplateListResponse;
    },
    enabled,
  });
}

// 获取单个模板
export function useGetAnalysisTemplate(templateId: string) {
  return useQuery({
    queryKey: ['analysisTemplate', templateId],
    queryFn: async () => {
      const { data } = await request.get(api.getAnalysisTemplate(templateId));
      return data.data as AnalysisTemplate;
    },
    enabled: !!templateId,
  });
}

// 创建模板
export function useCreateAnalysisTemplate() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (params: CreateTemplateParams) => {
      const { data } = await request.post(api.createAnalysisTemplate, { data: params });
      if (data.code !== 0) {
        throw new Error(data.message || '创建失败');
      }
      return data.data as AnalysisTemplate;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysisTemplates'] });
    },
  });
}

// 更新模板
export function useUpdateAnalysisTemplate() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ templateId, params }: { templateId: string; params: UpdateTemplateParams }) => {
      const { data } = await request.put(api.updateAnalysisTemplate(templateId), { data: params });
      if (data.code !== 0) {
        throw new Error(data.message || '更新失败');
      }
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysisTemplates'] });
      queryClient.invalidateQueries({ queryKey: ['analysisTemplate'] });
    },
  });
}

// 删除模板
export function useDeleteAnalysisTemplate() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (templateId: string) => {
      await request.delete(api.deleteAnalysisTemplate(templateId));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['analysisTemplates'] });
    },
  });
}
