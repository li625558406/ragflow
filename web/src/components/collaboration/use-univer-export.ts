import type { FUniver } from '@univerjs/presets';
import { useCallback, useState } from 'react';

interface Options {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  univerAPIRef: React.MutableRefObject<FUniver | null>;
}

/**
 * docx 导入导出 hook。
 *
 * 导出路径：调 FUniver 官方 SDK 的 exportDocument (若存在)，拿到 Blob，
 * POST 到后端 /exported-file 存储，再触发浏览器下载。
 * 若 SDK 不支持 exportDocument，退化到上传 JSON 快照（前端用 Univer 自身可还原）。
 *
 * PDF 路径：当前 Univer 社区版 SDK 可能不支持 PDF 导出，退化方案是
 * 提示用户先用 Word/LibreOffice 把 docx 另存为 PDF。
 */
export function useUniverExport({ docId, apiFetch, univerAPIRef }: Options) {
  const [busy, setBusy] = useState(false);

  const exportDocx = useCallback(async () => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setBusy(true);
    try {
      const fDoc = api.getActiveDocument?.();
      if (!fDoc) throw new Error('No active document');

      let blob: Blob;
      if (typeof api.exportDocument === 'function') {
        const result = await api.exportDocument({ format: 'docx' });
        blob =
          result instanceof Blob
            ? result
            : new Blob([result as BlobPart], {
                type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              });
      } else {
        // 退化：上传 JSON 快照
        const json = fDoc.save?.() ?? fDoc.getSnapshot?.();
        if (!json) throw new Error('save() returned empty');
        blob = new Blob([JSON.stringify(json, null, 2)], {
          type: 'application/json',
        });
        console.warn(
          '[useUniverExport] FUniver.exportDocument not available, falling back to JSON snapshot',
        );
      }

      // 上传到后端
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/exported-file?format=docx`,
        { method: 'POST', body: blob },
      );
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message || 'Upload failed');

      // 触发浏览器下载
      window.open(
        `/api/v1/collaboration/documents/${docId}/exported-file`,
        '_blank',
      );
    } finally {
      setBusy(false);
    }
  }, [docId, apiFetch]);

  const exportPdf = useCallback(async () => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setBusy(true);
    try {
      // 若 SDK 支持 PDF 直接导出则用
      if (typeof api.exportDocument === 'function') {
        try {
          const result = await api.exportDocument({ format: 'pdf' });
          if (result) {
            const blob =
              result instanceof Blob
                ? result
                : new Blob([result as BlobPart], { type: 'application/pdf' });
            await apiFetch(
              `/api/v1/collaboration/documents/${docId}/exported-file?format=pdf`,
              { method: 'POST', body: blob },
            );
            window.open(
              `/api/v1/collaboration/documents/${docId}/exported-file`,
              '_blank',
            );
            return;
          }
        } catch (e) {
          console.warn(
            '[useUniverExport] SDK PDF export failed, falling back to prompt',
            e,
          );
        }
      }
      // 退化：提示用户
      window.alert(
        '当前 Univer 版本不支持 PDF 直出。请先导出 Word，再用 Word/LibreOffice 另存为 PDF。',
      );
    } finally {
      setBusy(false);
    }
  }, [docId, apiFetch]);

  const importDocx = useCallback(async (file: File) => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setBusy(true);
    try {
      const buf = await file.arrayBuffer();
      if (typeof api.importDocument === 'function') {
        await api.importDocument({ format: 'docx', data: buf });
      } else {
        throw new Error(
          '当前 Univer 版本不支持 docx 导入。请先导出 JSON 或手动粘贴内容。',
        );
      }
    } finally {
      setBusy(false);
    }
  }, []);

  return { busy, exportDocx, exportPdf, importDocx };
}
