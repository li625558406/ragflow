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
  // 后端错误路径统一走 envelope（HTTP 200 + application/json），需按 content-type 识别
  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const body = await resp.json();
    throw new Error(
      body?.message || `下载失败（code ${body?.code ?? resp.status}）`,
    );
  }
  if (!resp.ok) throw new Error(`download failed ${resp.status}`);
  return resp.blob();
}

export async function addFlowComment(
  flowId: string,
  content: string,
  versionId?: string,
): Promise<{ comment: unknown }> {
  return apiFetch(`/flow/${flowId}/comment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, version_id: versionId }),
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
