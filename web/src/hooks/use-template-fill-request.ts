import api from '@/utils/api';
import { downloadFileFromBlob } from '@/utils/file-util';
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
  /** 默认值基线：空串/undefined=无；source 标记来源 */
  default_value?: string;
  default_source?: 'detected' | 'auto' | 'manual' | '';
}

export interface TplTemplateItem {
  id: string;
  name: string;
  description: string;
  file_type: 'docx' | 'xlsx';
  status: 'draft' | 'published' | 'disabled';
  latest_version: number;
  create_time: number;
  // 后台 AI 识别状态（detect-async 链路）：none | running | done | failed
  detect_status?: 'none' | 'running' | 'done' | 'failed';
  detect_error?: string;
}

export interface TplCandidate {
  index: number;
  text: string;
  addr: string;
}

// 获取模板填写列表
// 列表中存在「识别中」的模板时启用 3s 轮询，全部结束自动停止
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
      return data as {
        code: number;
        data: TplTemplateItem[];
        total_datasets?: number;
      };
    },
    refetchInterval: (query) => {
      const rows = query.state.data?.data ?? [];
      return rows.some((it) => it.detect_status === 'running') ? 3000 : false;
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
            sheet?: string;
            coord?: string;
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
    // save_placeholders 会重建 render 文件，已挂载的预览缓存需要一起失效
    queryClient.invalidateQueries({ queryKey: ['templateFillPreview'] });
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

// 触发后台 AI 识别：立即返回 running，结果自动保存为填写点，
// 进度经列表 detect_status 字段轮询展示
export function useDetectTemplateFillAsync() {
  return useMutation({
    mutationFn: async (templateId: string) => {
      const { data } = await request.post(api.detectTemplateFillAsync, {
        data: { template_id: templateId },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '识别任务提交失败');
      }
      return data.data as { status: string };
    },
    onSuccess: useInvalidateTemplateFill(),
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

// 删除模板（仅 draft/disabled 且无填写任务记录，守卫在 service 层）
export function useDeleteTemplateFill() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await request.delete(api.deleteTemplateFill(id));
      if (data.code !== 0) {
        throw new Error(data.message || '删除失败');
      }
      return data as { code: number };
    },
    onSuccess: invalidate,
  });
}

// 批量删除模板：部分失败不影响其余，逐条返回失败原因
export interface TplBatchDeleteResult {
  deleted: string[];
  failed: { id: string; message: string }[];
}

export function useBatchDeleteTemplateFill() {
  const invalidate = useInvalidateTemplateFill();

  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data } = await request.post(api.batchDeleteTemplateFill, {
        data: { ids },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '批量删除失败');
      }
      return data.data as TplBatchDeleteResult;
    },
    onSuccess: invalidate,
  });
}

// ── 填写任务（P2）──────────────────────────────────────────────

// 填写任务行，字段以后端 TplFillTask.to_dict 为准（snake_case）
export interface TplFillTaskItem {
  id: string;
  template_id: string;
  template_version_id?: string;
  status: string;
  source: string;
  kb_ids?: string[];
  params?: Record<string, string>;
  values?: { cells?: unknown; render?: unknown } | null;
  evidence?: Record<string, { query: string; chunks: string[] }> | null;
  result_file_id?: string;
  error?: string;
  create_time?: number;
}

// 进行中的状态集合：列表/详情据此决定是否继续轮询
const RUNNING = ['pending', 'retrieving', 'generating', 'rendering'];

function useInvalidateTemplateFillTask() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ['templateFillTaskList'] });
    // 前缀匹配，连同 ['templateFillTask', taskId] 详情缓存一起失效
    queryClient.invalidateQueries({ queryKey: ['templateFillTask'] });
  };
}

// 获取填写任务列表（存在进行中任务时 3s 函数式轮询）
export function useListTemplateFillTasks(params: {
  status?: string;
  page: number;
  size: number;
}) {
  return useQuery({
    queryKey: ['templateFillTaskList', params],
    queryFn: async () => {
      const searchParams = new URLSearchParams({
        page: String(params.page),
        size: String(params.size),
      });
      if (params.status) searchParams.set('status', params.status);

      const { data } = await request.get(
        `${api.listTemplateFillTasks}?${searchParams.toString()}`,
      );
      return data as {
        code: number;
        data: TplFillTaskItem[];
        total_datasets?: number;
      };
    },
    refetchInterval: (query) =>
      (query.state.data?.data ?? []).some((t) => RUNNING.includes(t.status))
        ? 3000
        : false,
  });
}

// 获取单个填写任务详情（进行中时 3s 轮询）
export function useGetTemplateFillTask(taskId: string) {
  return useQuery({
    queryKey: ['templateFillTask', taskId],
    queryFn: async () => {
      const { data } = await request.get(api.getTemplateFillTask(taskId));
      return data as { code: number; data: TplFillTaskItem };
    },
    enabled: !!taskId,
    refetchInterval: (query) => {
      const status = query.state.data?.data?.status;
      return status && RUNNING.includes(status) ? 3000 : false;
    },
  });
}

// 发起填写任务
export function useCreateTemplateFillTask() {
  const invalidate = useInvalidateTemplateFillTask();

  return useMutation({
    mutationFn: async (payload: {
      template_id: string;
      kb_ids: string[];
      params?: Record<string, string>;
      source?: string;
    }) => {
      const { data } = await request.post(api.createTemplateFillTask, {
        data: payload,
      });
      if (data.code !== 0) {
        throw new Error(data.message || '发起失败');
      }
      return data.data as { task_id: string; status: string };
    },
    onSuccess: invalidate,
  });
}

// 重试失败/部分完成的任务
export function useRetryTemplateFillTask(taskId?: string) {
  const invalidate = useInvalidateTemplateFillTask();

  return useMutation({
    mutationFn: async (id?: string) => {
      const target = id || taskId;
      if (!target) {
        throw new Error('缺少任务 ID');
      }
      const { data } = await request.post(api.retryTemplateFillTask(target), {
        data: {},
      });
      if (data.code !== 0) {
        throw new Error(data.message || '重试失败');
      }
      return data.data as { task_id: string; status: string };
    },
    onSuccess: invalidate,
  });
}

// 下载生成稿（blob，不走 hook；后端按模板 file_type 返回 docx/xlsx）
export async function downloadTemplateFillResult(
  taskId: string,
  ext: 'docx' | 'xlsx' = 'docx',
) {
  const res = await request.get(api.downloadTemplateFillTask(taskId), {
    responseType: 'blob',
  });
  const blob = res.data as Blob;
  if (!blob || blob.size === 0) {
    throw new Error('下载失败：文件为空');
  }
  // 后端出错时返回的是 JSON（code != 0），blob 里装的是错误信息而非文件
  if (blob.type.includes('application/json')) {
    let message = '下载失败';
    try {
      message = JSON.parse(await blob.text()).message || message;
    } catch {
      // 保留默认错误文案
    }
    throw new Error(message);
  }
  downloadFileFromBlob(blob, `fill_${taskId}.${ext}`);
}

// ── 测试填写（P2 Task 12：B端试跑出值+证据，不落任务）──────────────────

// 试跑结果（executor.dry_run 返回结构）：values=产值、cells=逐格状态、
// evidence=检索证据、partial=是否含待人工字段
export interface TemplateFillTestResult {
  values: Record<string, unknown>;
  cells: Record<string, string>;
  evidence: Record<string, { query: string; chunks: unknown[] }>;
  partial: boolean;
}

// 试跑是一次性同步动作（约 10-60 秒），不走 useMutation 缓存，
// UI 自管 loading（照 downloadTemplateFillResult 的直接导出 async 函数模式）
export async function testTemplateFill(
  templateId: string,
  payload: { kb_ids: string[]; params?: Record<string, string> },
): Promise<TemplateFillTestResult> {
  const { data } = await request.post(api.testTemplateFill(templateId), {
    data: payload,
  });
  if (data.code !== 0) {
    throw new Error(data.message || '试跑失败');
  }
  return data.data as TemplateFillTestResult;
}

// 更新默认值（单 key 即时保存；空串=清空）
// opts.invalidate 默认 true；模板详情页传 false —— 该页在 commit 时已把新值回写本地
// placeholders state，无需 refetch；若 invalidate 回流会整表覆盖，冲掉用户尚未保存的行编辑
export function useUpdateTemplateFillDefaults(opts?: { invalidate?: boolean }) {
  const { invalidate = true } = opts ?? {};
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      defaults,
    }: {
      id: string;
      defaults: Record<string, string>;
    }) => {
      // PUT 语义（覆盖式基线更新），走项目 request.put 封装
      const { data } = await request.put(api.updateTemplateFillDefaults(id), {
        data: { defaults },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '保存默认值失败');
      }
      return data.data as { id: string };
    },
    onSuccess: () => {
      if (invalidate) {
        queryClient.invalidateQueries({ queryKey: ['templateFillDetail'] });
      }
    },
  });
}

// ── 画布暂停确认（P2：confirm_pending SSE 事件 + Redis 唤醒）──────────────

// 画布范本填写暂停确认提交（Redis 唤醒画布节点继续）
// nonce 来自 confirm_pending 事件的 confirm_nonce，后端正则校验必填；
// decisions 结构：{ template_id: { changed: 勾选字段 key 数组, values: 直填值（仅非空项） } }
export async function confirmTemplateFill(
  taskId: string,
  nonce: string,
  decisions: Record<
    string,
    { changed: string[]; values: Record<string, string> }
  >,
): Promise<void> {
  const { data } = await request.post(api.confirmTemplateFill, {
    data: { task_id: taskId, nonce, decisions },
  });
  if (data.code !== 0) {
    throw new Error(data.message || '确认提交失败');
  }
}
