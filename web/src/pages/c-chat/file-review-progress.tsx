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
  /** 点击「下载成稿」时回调（参数：成稿 MinIO 对象名） */
  onPreviewDoc?: (minioPath: string) => void;
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
  const isRunning = status === 'reviewing' || status === 'fixing';
  const canFix = status === 'annotated' && (data?.fix_rounds_left ?? 0) > 0;
  const taskId = taskIdProp || data?.task_id || '';

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
          <span className="text-[#8C8C8C]">
            第 {current.round_no} 轮 ·{' '}
            {status === 'reviewing'
              ? '审核中'
              : status === 'fixing'
                ? '修复中'
                : status === 'annotated'
                  ? '已完成'
                  : status === 'done'
                    ? '已结束'
                    : status === 'failed'
                      ? '失败'
                      : status}
          </span>
        )}
        {canFix && (
          <span className="ml-2 text-[#8C8C8C]">
            剩余 {data.fix_rounds_left ?? 0} 轮
          </span>
        )}
      </div>
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
        {data.doc.object && data.doc.object !== fileId && (
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
            onClick={() => onPreviewDoc?.(data.doc.object)}
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
}: {
  picked: string[];
  onToggle: (l: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
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
