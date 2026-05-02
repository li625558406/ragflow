import api from '@/utils/api';
import request from '@/utils/request';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useEffect } from 'react';

export interface AnalysisItem {
  analysis_type: string;
  result: string;
  section_title: string;
  success: boolean;
  error_message?: string;
}

export interface AnalysisSection {
  section_title: string;
  analyses: AnalysisItem[];
}

export interface DocumentAnalysisResult {
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  template_name: string;
  sections: AnalysisSection[];
  error_message?: string;
}

export interface AnalyzeDocumentParams {
  template_id?: string;
  llm_id?: string;
}

// 触发文档分析
export function useAnalyzeDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      documentId,
      params = {},
    }: {
      documentId: string;
      params?: AnalyzeDocumentParams;
    }) => {
      const { data } = await request.post(api.analyzeDocument(documentId), { data: params });
      console.log('Analyze response:', data);
      if (data.code !== 0) {
        throw new Error(data.message || '启动分析失败');
      }
      return data.data as { task_id: string; status: string };
    },
    onSuccess: (_, { documentId }) => {
      queryClient.invalidateQueries({
        queryKey: ['documentAnalysis', documentId],
      });
    },
  });
}

// 获取文档分析结果
// 注意：taskId 参数用于构建查询 URL，但不是 queryKey 的一部分
// 这样 taskId 变化不会重置轮询状态
export function useGetDocumentAnalysis(documentId: string, taskId: string = '', enabled: boolean = true) {
  // 使用 ref 来存储最新的 taskId，确保 queryFn 总是使用最新的值
  const taskIdRef = useRef(taskId);

  // 更新 ref 当 taskId 变化时
  useEffect(() => {
    taskIdRef.current = taskId;
  }, [taskId]);

  return useQuery({
    queryKey: ['documentAnalysis', documentId],
    queryFn: async () => {
      // 使用 ref 中的最新 taskId
      const currentTaskId = taskIdRef.current;
      const url = currentTaskId
        ? `${api.getDocumentAnalysis(documentId)}?task_id=${currentTaskId}`
        : api.getDocumentAnalysis(documentId);
      const { data } = await request.get(url);
      // 如果返回错误码 102（分析结果不存在），返回 null 表示没有分析结果
      if (data.code === 102) {
        return null;
      }
      return data.data as DocumentAnalysisResult;
    },
    enabled: !!documentId && enabled,
    refetchInterval: (query) => {
      // 如果 enabled 为 false，不轮询
      if (!enabled) return false;

      const data = query.state.data;
      // 没有数据时不轮询
      if (!data) return false;

      // 如果正在分析，每 2 秒轮询一次
      if (data.status === 'pending' || data.status === 'running') {
        return 2000;
      }
      return false;
    },
    // 确保窗口聚焦时也重新获取
    refetchOnWindowFocus: true,
  });
}

// 删除文档分析结果
export function useDeleteDocumentAnalysis() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (documentId: string) => {
      await request.delete(api.deleteDocumentAnalysis(documentId));
    },
    onSuccess: (_, documentId) => {
      queryClient.invalidateQueries({
        queryKey: ['documentAnalysis', documentId],
      });
    },
  });
}

// 取消文档分析
export function useCancelDocumentAnalysis() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ documentId, taskId }: { documentId: string; taskId?: string }) => {
      const { data } = await request.post(api.cancelDocumentAnalysis(documentId), {
        data: { task_id: taskId || '' },
      });
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ['documentAnalysis', variables.documentId],
      });
    },
  });
}
