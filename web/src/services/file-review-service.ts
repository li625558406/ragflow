import message from '@/components/ui/message';
import { Authorization } from '@/constants/authorization';
import api from '@/utils/api';
import { getAuthorization } from '@/utils/authorization-util';

/**
 * 文件审核成稿下载（带鉴权头取 Blob → 触发浏览器保存）。
 *
 * 为什么不能用 `window.open(url)` / `<a href=url>` 直链：
 * `GET /api/v1/file/review/<task_id>/<file_version>/download` 带 `@login_required`，
 * 而 `login_required` 只从 `request.headers["Authorization"]` 取用户
 * （`api/apps/__init__.py` 的 `_load_user`），拿不到用户就直接 401 —— 服务端**不**从
 * cookie 兜底。前端的 token 存在 localStorage、由请求拦截器手动挂头，而浏览器导航类
 * 请求（window.open / a.click 直链 / 地址栏）**不会**携带自定义请求头。
 * 二者相加 ⇒ 直链下载必然 401。
 *
 * 本仓库既有下载一律走「fetch 带 token 取 Blob + createObjectURL」：
 * `services/flow-service.ts` 的 `downloadVersionBlob`、`review-panel.tsx` 的
 * `responseType:'blob'`。本函数与它们同款，是 file_review 下载的唯一正确入口。
 */
export async function downloadFileReviewVersionBlob(
  taskId: string,
  fileVersion: string,
): Promise<Blob> {
  const resp = await fetch(api.fileReviewDownload(taskId, fileVersion), {
    headers: { [Authorization]: getAuthorization() },
  });
  if (resp.status === 401) {
    throw new Error('登录已过期，请重新登录后再下载');
  }
  // 后端错误路径统一走 envelope（HTTP 200 + {code, message}）；成功路径是**原始 docx
  // 字节**（Content-Type 为 wordprocessingml，不是 JSON）。故只能按「content-type 是
  // JSON 且 code 为非 0 数字」判错 —— 不能一律按非 2xx 判错，也不能把 JSON 一律当错误。
  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const body = await resp.json();
    if (body && typeof body.code === 'number' && body.code !== 0) {
      throw new Error(body?.message || `下载失败（code ${body.code}）`);
    }
  }
  if (!resp.ok) {
    throw new Error(`下载失败 ${resp.status}`);
  }
  return resp.blob();
}

/**
 * 一步到位：取 Blob → 触发浏览器保存。失败时弹提示，**不向调用方抛异常**
 * （调用方多为 onClick 回调，没有可用的错误出口）。
 *
 * 文件名沿用后端 Content-Disposition 的口径（`文件审核_<version>.docx`），
 * 不在前端另取名 —— 后端 T18 审查已把内部 file_id 从文件名里摘掉。
 */
export async function downloadFileReviewVersion(
  taskId: string,
  fileVersion: string,
): Promise<void> {
  try {
    const blob = await downloadFileReviewVersionBlob(taskId, fileVersion);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `文件审核_${fileVersion}.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 立刻 revoke 会让部分浏览器来不及读取；延迟释放（同 flow-detail 的既有做法）
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    message.error(e instanceof Error ? e.message : '下载失败，请稍后重试');
  }
}
