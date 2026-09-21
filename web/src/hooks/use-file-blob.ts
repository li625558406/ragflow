import { downloadFileReviewVersionBlob } from '@/services/file-review-service';
import api from '@/utils/api';
import request from '@/utils/request';
import { useQuery } from '@tanstack/react-query';

// 获取文件原始 blob（GET /files/{id}，docx-preview 保真渲染用）。
// 错误体是 JSON（code != 0）时 blob 里装的是错误信息而非文件，
// parse message 抛错（照 downloadTemplateFillResult 口径）。
export function useFileBlob(fileId: string) {
  return useQuery({
    queryKey: ['fileBlob', fileId],
    queryFn: async () => {
      const res = await request.get(api.getFileBlob(fileId), {
        responseType: 'blob',
      });
      const blob = res.data as Blob;
      if (!blob || blob.size === 0) {
        throw new Error('文件为空');
      }
      if (blob.type.includes('application/json')) {
        let message = '文件获取失败';
        try {
          message = JSON.parse(await blob.text()).message || message;
        } catch {
          // 保留默认错误文案
        }
        throw new Error(message);
      }
      return blob;
    },
    enabled: !!fileId,
  });
}

// 审核成稿版本 blob（frv-{task_id}-{version}，经 download 端点取原始 docx 字节，
// 错误口径同 downloadFileReviewVersionBlob：JSON envelope 抛 message）。供
// ReviewPanel 在任务有落盘成稿时保真渲染「修复后文档」。(task_id, version) 一经
// 产出不可变（回退/新轮都产生新 version 名），允许 5min 内重挂不重复拉取。
export function useReviewVersionBlob(taskId: string, fileVersion: string) {
  return useQuery({
    queryKey: ['fileReviewVersionBlob', taskId, fileVersion] as const,
    queryFn: () => downloadFileReviewVersionBlob(taskId, fileVersion),
    enabled: !!taskId && !!fileVersion,
    staleTime: 5 * 60 * 1000,
  });
}
