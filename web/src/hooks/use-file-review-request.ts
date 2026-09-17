import api from '@/utils/api';
import request from '@/utils/request';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  IFileReviewAnnotationUpdateResponse,
  IFileReviewFixResponse,
  IFileReviewState,
  IFileReviewTemplatesResponse,
} from './file-review-stream';
import { isRoundRunning } from './file-review-stream';

/** 范本列表：启用即拉一次，无轮询 */
export function useFileReviewTemplates(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['fileReview', 'templates'] as const,
    queryFn: async () => {
      const { data } = await request.get(api.fileReviewTemplates);
      return data as { code: number; data: IFileReviewTemplatesResponse };
    },
    enabled: opts?.enabled ?? true,
  });
}

/** 文件状态：reviewing/fixing 时 3s 函数式轮询，否则停（终态判定走 isRoundRunning） */
export function useFileReviewState(fileId: string) {
  return useQuery({
    queryKey: ['fileReview', 'state', fileId] as const,
    queryFn: async () => {
      const { data } = await request.get(api.fileReviewState(fileId));
      return data as { code: number; data: IFileReviewState };
    },
    enabled: !!fileId,
    refetchInterval: (query) => {
      const status = query.state.data?.data?.current?.status;
      return status && isRoundRunning(status) ? 3000 : false;
    },
  });
}

/** 发起一轮修复：成功时同步失效对应 file_id 的 state 缓存（fix 端点按 task_id 圈定，
 * 但返回值不含 file_id，故调用方必须透传 file_id 才能失效）。 */
export function useFixFileReview(fileId: string) {
  const invalidate = useInvalidateFileReview();
  return useMutation({
    mutationFn: async (params: {
      taskId: string;
      levels: string[];
      userQuery?: string;
    }) => {
      const { data } = await request.post(api.fileReviewFix(params.taskId), {
        data: { levels: params.levels, user_query: params.userQuery },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '修复发起失败');
      }
      return data.data as IFileReviewFixResponse;
    },
    onSuccess: () => invalidate(fileId),
  });
}

/** 人工置标注状态：成功时失效对应 file_id 的 state 缓存 */
export function useUpdateAnnotationStatus(fileId: string) {
  const invalidate = useInvalidateFileReview();
  return useMutation({
    mutationFn: async (params: {
      annotationId: string;
      status: 'open' | 'resolved' | 'wontfix';
    }) => {
      const { data } = await request.post(
        api.fileReviewAnnotationStatus(params.annotationId),
        { data: { status: params.status } },
      );
      if (data.code !== 0) {
        throw new Error(data.message || '标注状态更新失败');
      }
      return data.data as IFileReviewAnnotationUpdateResponse;
    },
    onSuccess: () => invalidate(fileId),
  });
}

/** 失效器：fix / annotation status 变更后必须调一次，让面板与进度卡重拉 state。
 * fix 端点的 response 不含 file_id（只有 task_id），故调用方必须把 file_id 传进来。
 * 仅失效 ['fileReview', 'state', fileId] 这一条，避免误冲掉其它文件的轮询。 */
function useInvalidateFileReview() {
  const queryClient = useQueryClient();
  return (fileId: string) => {
    queryClient.invalidateQueries({
      queryKey: ['fileReview', 'state', fileId],
    });
  };
}
