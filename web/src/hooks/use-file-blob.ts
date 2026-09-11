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
