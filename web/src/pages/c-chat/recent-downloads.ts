export interface DownloadInfo {
  doc_id: string;
  filename: string;
  name?: string;
  mime_type?: string;
  size?: number;
  url?: string;
}

interface MessageLike {
  downloads?: DownloadInfo[];
}

/**
 * 收集会话内最近 N 张成稿卡（默认2），按消息顺序取末尾、新卡在前。
 * 在线消息（WorkflowFinished 合并）与历史恢复消息共用同一 msg.downloads 形态。
 */
export function collectRecentDownloads(
  messages: MessageLike[],
  limit = 2,
): Array<{ doc_id: string; filename: string }> {
  const out: Array<{ doc_id: string; filename: string }> = [];
  for (let i = messages.length - 1; i >= 0 && out.length < limit; i--) {
    const dls = messages[i]?.downloads;
    if (!Array.isArray(dls)) continue;
    for (let j = dls.length - 1; j >= 0 && out.length < limit; j--) {
      const d = dls[j];
      if (d && typeof d.doc_id === 'string' && d.doc_id) {
        out.push({ doc_id: d.doc_id, filename: d.filename || d.name || '' });
      }
    }
  }
  return out;
}
