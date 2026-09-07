import api from '@/utils/api';
import request from '@/utils/request';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export interface TplPlaceholder {
  key: string;
  name: string;
  description: string;
  retrieval_query: string;
  fill_mode: 'llm' | 'param' | 'manual';
  required: boolean;
  addr: string;
  anchor: string;
  top_k: number;
}

export interface TplTemplateItem {
  id: string;
  name: string;
  description: string;
  file_type: 'docx' | 'xlsx';
  status: 'draft' | 'published' | 'disabled';
  latest_version: number;
  create_time: number;
}

export interface TplCandidate {
  index: number;
  text: string;
  addr: string;
}

// 获取模板填写列表
export function useListTemplateFill(params: {
  keyword?: string;
  status?: string;
  page: number;
  size: number;
}) {
  return useQuery({
    queryKey: ['templateFillList', params],
    queryFn: async () => {
      const searchParams = new URLSearchParams({
        page: String(params.page),
        size: String(params.size),
      });
      if (params.keyword) searchParams.set('keyword', params.keyword);
      if (params.status) searchParams.set('status', params.status);

      const { data } = await request.get(
        `${api.listTemplateFill}?${searchParams.toString()}`,
      );
      return data as { code: number; data: TplTemplateItem[]; total?: number };
    },
  });
}

// 获取模板详情（含占位符）
export function useTemplateFillDetail(id: string) {
  return useQuery({
    queryKey: ['templateFillDetail', id],
    queryFn: async () => {
      const { data } = await request.get(api.getTemplateFill(id));
      return data as {
        code: number;
        data: TplTemplateItem & {
          placeholders: TplPlaceholder[];
          render_ready: boolean;
        };
      };
    },
    enabled: !!id,
  });
}

// 获取模板预览（原文条目 + 占位符标注）
export function useTemplateFillPreview(id: string) {
  return useQuery({
    queryKey: ['templateFillPreview', id],
    queryFn: async () => {
      const { data } = await request.get(api.previewTemplateFill(id));
      return data as {
        code: number;
        data: {
          file_type: string;
          items: {
            index: number;
            text: string;
            addr: string;
            placeholder_key: string;
          }[];
        };
      };
    },
    enabled: !!id,
  });
}

function useInvalidateTemplateFill() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ['templateFillList'] });
    queryClient.invalidateQueries({ queryKey: ['templateFillDetail'] });
  };
}

// 上传模板（multipart）
export function useUploadTemplateFill() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async (payload: {
      file: File;
      name: string;
      description: string;
    }) => {
      const formData = new FormData();
      formData.append('file', payload.file);
      formData.append('name', payload.name);
      formData.append('description', payload.description);
      const { data } = await request.post(api.uploadTemplateFill, {
        data: formData,
      });
      if (data.code !== 0) {
        throw new Error(data.message || '上传失败');
      }
      return data.data as { id: string };
    },
    onSuccess: invalidate,
  });
}

// 占位符探测（LLM 建议）
export function useDetectTemplateFill() {
  return useMutation({
    mutationFn: async (templateId: string) => {
      const { data } = await request.post(api.detectTemplateFill, {
        data: { template_id: templateId },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '探测失败');
      }
      return data.data as {
        candidates: TplCandidate[];
        suggestions: TplPlaceholder[];
      };
    },
  });
}

// 保存占位符
export function useSaveTemplateFillPlaceholders() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async ({
      id,
      placeholders,
    }: {
      id: string;
      placeholders: TplPlaceholder[];
    }) => {
      const { data } = await request.post(
        api.saveTemplateFillPlaceholders(id),
        { data: { placeholders } },
      );
      if (data.code !== 0) {
        throw new Error(data.message || '保存失败');
      }
      return data.data as { id: string; placeholder_count: number };
    },
    onSuccess: invalidate,
  });
}

// 发布模板
export function usePublishTemplateFill() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await request.post(api.publishTemplateFill(id), {
        data: {},
      });
      if (data.code !== 0) {
        throw new Error(data.message || '发布失败');
      }
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}

// 停用模板
export function useDisableTemplateFill() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await request.post(api.disableTemplateFill(id), {
        data: {},
      });
      if (data.code !== 0) {
        throw new Error(data.message || '停用失败');
      }
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}
