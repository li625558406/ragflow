import type {
  FlowDetail,
  FlowInstanceItem,
  FlowScope,
  FlowVersionItem,
} from '@/pages/c-chat/flow/flow-types';

const BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('Authorization') || '';
  return { Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}');
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}${path}${path.includes('?') ? '&' : '?'}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`flow api ${resp.status}`);
  const body = await resp.json();
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0) {
      throw new Error(body.message || `flow api code ${body.code}`);
    }
    return body.data as T;
  }
  return body as T;
}

export async function createFlow(formData: FormData): Promise<{ id: string }> {
  return apiFetch('/flow', { method: 'POST', body: formData });
}

export async function listFlows(
  scope: FlowScope,
): Promise<{ list: FlowInstanceItem[]; total: number }> {
  return apiFetch(`/flow/list?scope=${scope}`);
}

export async function getFlowDetail(flowId: string): Promise<FlowDetail> {
  return apiFetch(`/flow/${flowId}`);
}

export async function uploadFlowVersion(
  flowId: string,
  formData: FormData,
): Promise<{ version: FlowVersionItem }> {
  return apiFetch(`/flow/${flowId}/version`, {
    method: 'POST',
    body: formData,
  });
}

export function flowVersionDownloadUrl(
  flowId: string,
  versionId: string,
): string {
  const uid = getUserInfo().id || '';
  return `${BASE}/flow/${flowId}/version/${versionId}/download?user_id=${encodeURIComponent(uid)}`;
}

/** 带鉴权下载版本文件为 Blob（预览/转 File 给 AI 用） */
export async function downloadVersionBlob(
  flowId: string,
  versionId: string,
): Promise<Blob> {
  const resp = await fetch(flowVersionDownloadUrl(flowId, versionId), {
    headers: authHeaders(),
  });
  if (resp.status === 401) throw new Error('登录已过期，请重新登录后再下载');
  // 后端错误路径统一走 envelope（HTTP 200 + {code, message}）。用户可能上传
  // application/json 的正常版本文件，不能按 content-type 判错——只把「code 为
  // 非 0 数字」的响应体当错误 envelope，正常 JSON 文件内容不会长这样。
  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const body = await resp.json();
    if (body && typeof body.code === 'number' && body.code !== 0) {
      throw new Error(body?.message || `下载失败（code ${body.code}）`);
    }
    // 非 envelope 的 JSON：还原为 application/json Blob 供预览/下载
    return new Blob([JSON.stringify(body)], { type: 'application/json' });
  }
  if (!resp.ok) throw new Error(`download failed ${resp.status}`);
  return resp.blob();
}

export async function addFlowComment(
  flowId: string,
  content: string,
  versionId?: string,
  anchor?: {
    anchorText?: string;
    anchorPara?: number | null;
    anchorStart?: number | null;
  },
): Promise<{ comment: unknown }> {
  return apiFetch(`/flow/${flowId}/comment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content,
      version_id: versionId,
      anchor_text: anchor?.anchorText || '',
      anchor_para: anchor?.anchorPara ?? null,
      anchor_start: anchor?.anchorStart ?? null,
    }),
  });
}

/** 删除手动批注（后端校验：仅批注作者本人） */
export async function deleteFlowComment(
  flowId: string,
  commentId: string,
): Promise<{ id: string }> {
  return apiFetch(`/flow/${flowId}/comment/${commentId}/delete`, {
    method: 'POST',
  });
}

/** 段落内 run 级格式（snake_case 与后端 docx 写入契约一致） */
export interface FlowDocRun {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  superscript?: boolean;
  subscript?: boolean;
  color?: string;
  bg_color?: string;
  font?: string;
  size?: number;
}

/** Word 式编辑文档：改写/新增/删除段落（后端按 para_index 定位 docx 同步增删改，存为新版本）。
 * align/indent/headingLevel 为块级属性：仅相对基线变化时携带，后端只应用已提供的键 */
export interface FlowDocEditOps {
  edits: Array<{
    paraIndex: number;
    newText: string;
    runs?: FlowDocRun[];
    align?: string;
    indent?: number;
    headingLevel?: number | null;
  }>;
  deletes: number[];
  inserts: Array<{
    afterParaIndex: number;
    newText: string;
    runs?: FlowDocRun[];
    align?: string;
    indent?: number;
    headingLevel?: number;
  }>;
}

export async function editFlowDocument(
  flowId: string,
  versionId: string,
  ops: FlowDocEditOps,
): Promise<{ version: FlowVersionItem }> {
  return apiFetch(`/flow/${flowId}/document/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      version_id: versionId,
      edits: ops.edits.map((e) => ({
        para_index: e.paraIndex,
        new_text: e.newText,
        ...(e.runs ? { runs: e.runs } : {}),
        ...(e.align ? { align: e.align } : {}),
        ...(e.indent ? { indent: e.indent } : {}),
        ...(e.headingLevel !== undefined
          ? { heading_level: e.headingLevel }
          : {}),
      })),
      deletes: ops.deletes,
      inserts: ops.inserts.map((i) => ({
        after_para_index: i.afterParaIndex,
        new_text: i.newText,
        ...(i.runs ? { runs: i.runs } : {}),
        ...(i.align ? { align: i.align } : {}),
        ...(i.indent ? { indent: i.indent } : {}),
        ...(i.headingLevel !== undefined
          ? { heading_level: i.headingLevel }
          : {}),
      })),
    }),
  });
}

export async function saveFlowAiRecord(
  flowId: string,
  payload: {
    instruction: string;
    response: string;
    version_id?: string;
    session_id?: string;
    save_as_version?: boolean;
  },
): Promise<{ record: unknown; output_version_id: string }> {
  return apiFetch(`/flow/${flowId}/ai-record`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function submitFlow(
  flowId: string,
  action: 'next' | 'return',
): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
}

export async function archiveFlow(
  flowId: string,
): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/archive`, { method: 'POST' });
}

export async function cancelFlow(
  flowId: string,
): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/cancel`, { method: 'POST' });
}

export interface FlowCandidate {
  id: string;
  nickname: string;
}

export async function listCandidates(): Promise<{ list: FlowCandidate[] }> {
  return apiFetch('/flow/candidates');
}
