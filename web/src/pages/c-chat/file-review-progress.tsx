// 文件审核进度卡：c-chat 对话与 flow AI 对话区共用
// T11 交接契约：复用 useFileReviewState 拿数据，不自写 EventSource；轮询开关由 hook 内部控制。
// canFix 用 fix_rounds_left 判定（与 max_fix_rounds 配对来自服务端），禁止按 rounds.length 派生。
// 修复级别通过 FixLevelPopover 多选（严重/一般/提示），不硬编码预设按钮。
import type { IFileReviewAnnotation } from '@/hooks/file-review-stream';
import {
  useFileReviewState,
  useFixFileReview,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import { ChevronDown, Download, Eye, Loader2 } from 'lucide-react';
import { useState } from 'react';

// 状态中文：与服务端 ROUND_STATUS_CN **语义**对齐（措辞刻意不同，服务端用
// 「审核完成 / 已收口」面向 LLM，这里用「已完成 / 已结束」面向用户）。
// done 刻意叫「已结束」而不是「修复完成」——executor 在「没有待修复的问题」与
// 「该文件类型不支持自动修复」时同样以 done 收口，写「完成」会让用户以为文档被改过。
// 新增/改状态时两边都要过一眼，但不要为了「逐字相同」去改服务端文案。
const STATUS_CN: Record<string, string> = {
  reviewing: '审核中',
  fixing: '修复中',
  annotated: '已完成',
  done: '已结束',
  failed: '失败',
};

export default function FileReviewProgress({
  fileId,
  taskId: taskIdProp,
  onOpenReview,
  onPreviewDoc,
}: {
  /** 必填。轮询 / 失效 / fix 入参都从这里派生。 */
  fileId: string;
  /** 调用方已知 task_id 时显式传（来自 T7 节点 / T8 工具产出），
   *  修复端点需要的 task_id 优先取此；缺省回退到 state.data.task_id。
   *  推荐调用方总是传，避免与 state 端点的「最近一轮 task_id」语义耦合。 */
  taskId?: string;
  /** 点击「打开审核面板」时回调（参数：标注全集 + 当前成稿版本） */
  onOpenReview?: (
    annotations: IFileReviewAnnotation[],
    fileVersion: string,
  ) => void;
  /** 点击「下载成稿」时回调（参数：成稿版本号）。
   *  调用方**必须**调 `downloadFileReviewVersion(taskId, fileVersion)`
   *  （`@/services/file-review-service`）—— 它用 fetch 手挂 Authorization 取 Blob
   *  再 createObjectURL 下载。**禁止**改成 window.open / a[href] 直链：下载端点带
   *  `@login_required` 且只从请求头取用户，浏览器导航类请求不带自定义头 ⇒ 必 401。
   *  R-8：服务端不再下发 MinIO 对象名，这里只收版本号。 */
  onPreviewDoc?: (fileVersion: string) => void;
}) {
  // ── 数据 ─────────────────────────────────────────
  const state = useFileReviewState(fileId);
  const fixMutation = useFixFileReview(fileId);
  // 标注状态由面板触发，组件不在此处挂载；保留 import 以满足模块边界契约。
  void useUpdateAnnotationStatus;

  const data = state.data?.code === 0 ? state.data.data : null;

  // ── 派生（按 T11 交接契约：canFix 不自拼，用 fix_rounds_left） ──
  const current = data?.current ?? null;
  const status = current?.status ?? '';
  // stale：轮次自称在跑但线程已不存在（服务重启 / 崩溃）。判据在服务端
  // （Service.is_stale_running，含 60s 宽限），前端只消费，不自己按时间重算 ——
  // 两处各算一次就是等着宽限期口径漂移。
  const isStale = current?.stale === true;
  // 中断的轮次**不是**运行中：否则会永远转圈（服务端 3s 轮询已按同一条件停掉）。
  const isRunning = !isStale && (status === 'reviewing' || status === 'fixing');
  const statusLabel = isStale ? '已中断' : STATUS_CN[status] || status;
  // canFix 必须**镜像服务端闸门**，而不是自己发明更严的条件。
  // 服务端（Service 层 admit_fix_round）放行条件：stale 未命中、rounds[-1].status ∉
  // (reviewing, fixing)、spawn 未在跑、fix_rounds_left > 0、所选级别有待修项。
  // 这里叠一次前三条（服务端会给可读文案，前端叠是为了不给用户点必然失败的东西）。
  // 曾经写成 `status === 'annotated'` 是**错的**：修复轮的终态是 'done'
  // （executor._run_fix_round 每条收口路径都写 done），于是第 2 轮起按钮永久消失，
  // 「最多 3 轮修复」在 UI 侧实际只能触发 1 轮 —— 只有对话工具还能继续。
  // isStale 同理必须排除：中断轮次的 status 是 reviewing/fixing，但服务端 stale 闸门
  // 会拒绝一切 fix（唯一出路是重新发起审核），留着按钮等于给用户一个必然失败的入口。
  const canFix = !isStale && !isRunning && (data?.fix_rounds_left ?? 0) > 0;
  const taskId = taskIdProp || data?.task_id || '';
  // 服务端受理闸门拒绝时必须让用户看到原因：R-3 把「没有【严重】级别的待修复问题。
  // 待修复问题：共 3 条（严重 2 条、一般 1 条）」这类富文案下沉到 Service 层由 REST 与
  // 对话工具共用，但卡片这条路径此前只用了 isPending、从不渲染 error ⇒ 点「确认修复」
  // 被拒时界面毫无反馈（Popover 原地不动、无任何提示），富文案等于白下沉。
  const fixError =
    fixMutation.error instanceof Error ? fixMutation.error.message : '';

  // ── 修复级别选择 Popover ─────────────────────────
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const submitFix = () => {
    if (!taskId) return; // 防御：极端情况下 state 尚未拉到
    fixMutation.mutate(
      { taskId, levels: picked, userQuery: undefined },
      {
        onSuccess: () => {
          setPicking(false);
          setPicked([]);
        },
      },
    );
  };

  // ── 渲染 ─────────────────────────────────────────
  if (state.isError) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs text-[#8C8C8C]">
        加载失败，请稍后重试
      </div>
    );
  }
  if (state.isLoading && !data) {
    return (
      <div className="flex items-center gap-1 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs text-[#8C8C8C]">
        <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
      </div>
    );
  }
  if (!data || data.rounds.length === 0) {
    return null; // 还没产出轮次（不应该出现，组件挂在审核流程之后）
  }

  return (
    <div className="space-y-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs">
      <div className="font-medium text-[#000000]">
        文件审核{' '}
        {current && (
          <span className={isStale ? 'text-[#F5222D]' : 'text-[#8C8C8C]'}>
            第 {current.round_no} 轮 · {statusLabel}
          </span>
        )}
        {canFix && (
          <span className="ml-2 text-[#8C8C8C]">
            剩余 {data.fix_rounds_left ?? 0} 轮
          </span>
        )}
      </div>
      {isStale && (
        <div className="text-[#F5222D]">
          本轮已中断（服务重启或异常退出），不会再产出结果；如需继续请重新发起审核。
        </div>
      )}
      {/* 失败原因：此前卡片只渲染 summary，failed 轮只显示「失败」两个字、原因全丢
          （对话工具侧一直是渲染 error 的）—— 两处口径不一致，用户只能去问模型。 */}
      {status === 'failed' && current?.error && (
        <div className="text-[#F5222D]">失败原因：{current.error}</div>
      )}
      {current?.summary && (
        <div className="text-[#8C8C8C]">{current.summary}</div>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
          onClick={() => onOpenReview?.(data.annotations, data.doc.version)}
        >
          <Eye className="h-3 w-3" /> 打开审核面板（{data.doc.version || '原件'}
          ）
        </button>
        {data.doc.has_result && data.doc.version && (
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
            onClick={() => onPreviewDoc?.(data.doc.version)}
          >
            <Download className="h-3 w-3" /> 下载成稿
          </button>
        )}
        {canFix && (
          <button
            type="button"
            className="flex items-center gap-1 rounded bg-[#1a66fb] px-2 py-0.5 text-white hover:bg-[#1557d6]"
            onClick={() => setPicking(true)}
          >
            选择级别修复 <ChevronDown className="h-3 w-3" />
          </button>
        )}
      </div>
      {isRunning && (
        <div className="flex items-center text-[#8C8C8C]">
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          {status === 'reviewing' ? '正在审核…' : '正在修复…'}
        </div>
      )}
      {picking && (
        <FixLevelPopover
          picked={picked}
          onToggle={(l) =>
            setPicked((p) =>
              p.includes(l) ? p.filter((x) => x !== l) : [...p, l],
            )
          }
          onCancel={() => {
            setPicking(false);
            setPicked([]);
          }}
          onConfirm={submitFix}
          busy={fixMutation.isPending}
          error={fixError}
        />
      )}
    </div>
  );
}

function FixLevelPopover({
  picked,
  onToggle,
  onCancel,
  onConfirm,
  busy,
  error,
}: {
  picked: string[];
  onToggle: (l: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  /** 服务端拒绝原因（闸门 reason 文案）。失败时 Popover 不关闭，就地显示。 */
  error?: string;
}) {
  // 三选多；中文 label 与 T10 SEVERITY_CN 对齐（high→严重 / medium→一般 / low→提示）
  return (
    <div className="rounded border border-[#E5E5E5] bg-white p-2">
      <div className="mb-1 text-[#8C8C8C]">勾选要修复的级别：</div>
      <div className="flex flex-wrap gap-2">
        {[
          { v: 'high', l: '严重' },
          { v: 'medium', l: '一般' },
          { v: 'low', l: '提示' },
        ].map((opt) => (
          <label key={opt.v} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={picked.includes(opt.v)}
              onChange={() => onToggle(opt.v)}
              disabled={busy}
            />
            {opt.l}
          </label>
        ))}
      </div>
      {error && <div className="mt-2 text-[#F5222D]">{error}</div>}
      <div className="mt-2 flex justify-end gap-2">
        <button
          type="button"
          className="text-[#8C8C8C]"
          onClick={onCancel}
          disabled={busy}
        >
          取消
        </button>
        <button
          type="button"
          className="rounded bg-[#1a66fb] px-2 py-0.5 text-white disabled:opacity-50"
          onClick={onConfirm}
          disabled={busy || picked.length === 0}
        >
          {busy ? '提交中…' : '确认修复'}
        </button>
      </div>
    </div>
  );
}
