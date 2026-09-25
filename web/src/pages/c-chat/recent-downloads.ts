import type { ITemplateFillState } from '@/hooks/template-fill-stream';

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

/** filename 清洗：剥控制字符 + 截断 120 字符，防下游 prompt 注入/超长负载 */
function sanitizeDownloadFilename(raw: string): string {
  return (
    raw
      // eslint-disable-next-line no-control-regex -- 控制字符正是要清洗的目标
      .replace(/[\u0000-\u001f\u007f]/g, '')
      .slice(0, 120)
  );
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
        const raw = d.filename || d.name || '';
        out.push({ doc_id: d.doc_id, filename: sanitizeDownloadFilename(raw) });
      }
    }
  }
  return out;
}

/**
 * 收集范本填写成稿卡的 download 契约（流程页签 DocumentRewrite 定位目标用）。
 * 仅取 status='filled' 且带 doc_id 的模板行（filling/failed 行无成稿可定位）；
 * 倒序输出（后填的范本在前），limit 默认 2 与 collectRecentDownloads 对齐。
 * 无成稿返回 [] —— 调用方据此回落流程版本（flow_version_id）。
 */
export function collectRecentFillDownloads(
  state?: ITemplateFillState,
  limit = 2,
): Array<{ doc_id: string; filename: string }> {
  const out: Array<{ doc_id: string; filename: string }> = [];
  const templates = state?.templates;
  if (!Array.isArray(templates)) return out;
  for (let i = templates.length - 1; i >= 0 && out.length < limit; i--) {
    const t = templates[i];
    const docId = t?.download?.doc_id;
    if (t?.status !== 'filled' || typeof docId !== 'string' || !docId) continue;
    const raw = t.download?.filename || t.download?.name || '';
    out.push({ doc_id: docId, filename: sanitizeDownloadFilename(raw) });
  }
  return out;
}
