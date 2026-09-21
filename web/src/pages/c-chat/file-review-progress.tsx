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
import { FixActions, FixDiffView } from './review-fix-diff';

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

// 从 SSE 全量原始事件序列（send() 返回的 events）里提取 FileReview 节点产出的
// {fileId, taskId}。**必须**喂 res.events 而不是 answerList：hook 收尾时
// setDone(true) 与 resetAnswerList() 同帧批处理，done 态扫描 effect 只能见到空
// 列表（structured output 之所以用 ref 拦截就是同一原因）。轮询端点只认 file_id，
// 节点 inputs 是空 dict，file_id 的唯一来源是节点 outputs（2026-09-20 增补）。
export function extractFileReviewTarget(
  events: unknown[] | undefined,
): { fileId: string; taskId: string } | null {
  let fileId = '';
  let taskId = '';
  for (const evt of events ?? []) {
    const ev: any = evt as any;
    if (ev?.event !== 'node_finished') continue;
    const data = ev?.data ?? {};
    // component_name 是 DSL 节点显示名（如「FileReview:BraveLionsScan」），组件类型
    // 在 component_type 字段；component_name 精确等值保留作兜底
    const componentType = (data?.component_type ?? '').toString();
    const componentName = (data?.component_name ?? '').toString();
    if (componentType !== 'FileReview' && componentName !== 'FileReview')
      continue;
    const outputs = data?.outputs ?? {};
    const inputs = data?.inputs ?? {};
    if (outputs?.task_id) taskId = String(outputs.task_id);
    // file_id 权威来源是节点 outputs；inputs 两键为旧兜底
    const fileIdInput =
      outputs?.file_id ?? inputs?.file_id ?? inputs?.review_file_id;
    if (fileIdInput) fileId = String(fileIdInput);
    if (taskId && fileId) break;
  }
  return taskId && fileId ? { fileId, taskId } : null;
}

export default function FileReviewProgress({
  fileId,
  taskId: taskIdProp,
  onOpenReview,
  onPreviewDoc,
  onSaveAsVersion,
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
  /** 点击「存为流程版本」时回调（参数：taskId + 成稿版本号）。
   *  flow 页签专属：把审核成稿上传为流程版本（版本记录 /「查看文件内容」随之可见
   *  修改后文件）。c-chat 无流程版本概念，不传即不渲染按钮。
   *  失败必须 reject——卡片据 resolve/reject 翻转按钮态（保存中/已存/失败重试）。 */
  onSaveAsVersion?: (taskId: string, fileVersion: string) => Promise<void>;
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

  // ── 修复轮结果反馈：AI 修复轮把标注翻成 status='fixed'（手动面板走
  // resolved/wontfix，互不污染）。fixed 是文件级累计；「确认保留」把 fixed 置为
  // resolved 且带 patch（有修复记录的确认），仍计入已修复；open 只统计 AI 批注
  // （人工批注不参与修复轮）。
  const fixedAnns = (data?.annotations ?? []).filter(
    (a) =>
      a.status === 'fixed' ||
      (a.status === 'resolved' && a.patch && (a.patch.find || a.patch.replace)),
  );
  const openAnns = (data?.annotations ?? []).filter(
    (a) => a.status === 'open' && a.source === 'ai',
  );
  // 只有存在修复轮（轮次 > 1）才展示结果区：首轮 annotated 没有任何「修复」语义。
  const hasFixRounds = (data?.rounds?.length ?? 0) > 1;
  const [showFixDetail, setShowFixDetail] = useState(false);

  const runSaveAsVersion = async (fileVersion: string) => {
    if (!onSaveAsVersion || !taskId || !fileVersion) return;
    if (saveStates[fileVersion] === 'saving') return;
    setSaveStates((p) => ({ ...p, [fileVersion]: 'saving' }));
    try {
      await onSaveAsVersion(taskId, fileVersion);
      setSaveStates((p) => ({ ...p, [fileVersion]: 'saved' }));
    } catch {
      setSaveStates((p) => ({ ...p, [fileVersion]: 'error' }));
    }
  };

  // ── 修复级别选择 Popover ─────────────────────────
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  // ── 存为流程版本（flow 页签专属）：按成稿版本记态，版本随新轮推进（v1→v2）时
  // 新版本无键自然回到可点击态，无需手动重置。
  const [saveStates, setSaveStates] = useState<
    Record<string, 'saving' | 'saved' | 'error'>
  >({});
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

  // 按钮态：仅当调用方提供回调且有成稿时才有意义（取值须在 data 判空之后）
  const docVersion = data.doc.version;
  const saveState = onSaveAsVersion
    ? (saveStates[docVersion] ?? 'idle')
    : 'idle';

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
      {hasFixRounds && (
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[#52C41A]">已修复 {fixedAnns.length} 项</span>
            <span className="text-[#8C8C8C]">未修复 {openAnns.length} 项</span>
            {(fixedAnns.length > 0 || openAnns.length > 0) && (
              <button
                type="button"
                className="flex items-center gap-0.5 text-[#1a66fb]"
                onClick={() => setShowFixDetail((v) => !v)}
              >
                {showFixDetail ? '收起明细' : '查看明细'}
                <ChevronDown
                  className={`h-3 w-3 transition-transform ${showFixDetail ? 'rotate-180' : ''}`}
                />
              </button>
            )}
          </div>
          {/* 未修复为 0 且确实修过：明说，别让用户猜「是不是压根没修」 */}
          {hasFixRounds && fixedAnns.length === 0 && openAnns.length === 0 && (
            <div className="text-[#8C8C8C]">没有待修复的问题。</div>
          )}
          {showFixDetail && (
            <div className="mt-1 space-y-1">
              {fixedAnns.map((a) => (
                <div key={a.id}>
                  <div className="flex items-start gap-1.5">
                    <SeverityTag severity={a.severity} fixed />
                    <span className="text-[#388E3C]">{a.issue}</span>
                  </div>
                  {/* 修复前/后对比 + 回退/确认保留：patch 为空（旧版修复无存档）时只隐藏 diff，操作仍可展示 */}
                  {a.patch && (a.patch.find || a.patch.replace) && (
                    <FixDiffView patch={a.patch} />
                  )}
                  {a.status === 'fixed' && (
                    <div className="ml-5">
                      <FixActions
                        fileId={fileId}
                        annotationId={a.id}
                        status="fixed"
                      />
                    </div>
                  )}
                  {a.status === 'resolved' && (
                    <div className="ml-5">
                      <span className="mr-2 text-[#52C41A]">已确认保留</span>
                      {a.patch && (a.patch.find || a.patch.replace) && (
                        <FixActions
                          fileId={fileId}
                          annotationId={a.id}
                          status="resolved"
                        />
                      )}
                    </div>
                  )}
                </div>
              ))}
              {openAnns.map((a) => (
                <div key={a.id}>
                  <div className="flex items-start gap-1.5">
                    <SeverityTag severity={a.severity} />
                    <span className="text-[#595959]">
                      {a.issue}
                      {/* open+patch=回退过的批注：标记已回退，可一键恢复 AI 修改 */}
                      {a.patch && (a.patch.find || a.patch.replace) && (
                        <span className="ml-1 rounded bg-[#8C8C8C] px-1 py-px text-[10px] font-bold text-white">
                          已回退
                        </span>
                      )}
                    </span>
                  </div>
                  {a.patch && (a.patch.find || a.patch.replace) && (
                    <div className="ml-5">
                      <FixActions
                        fileId={fileId}
                        annotationId={a.id}
                        status="open"
                      />
                    </div>
                  )}
                </div>
              ))}
              {openAnns.length > 0 && (
                <div className="text-[#8C8C8C]">
                  未修复项多为无法自动修改的类型（如目录/标题重复、需补充分值的纯插入），
                  可打开审核面板逐条查看或手动处理。
                </div>
              )}
            </div>
          )}
        </div>
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
        {data.doc.has_result && data.doc.version && onSaveAsVersion && (
          <button
            type="button"
            disabled={saveState === 'saving' || saveState === 'saved'}
            className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF] disabled:opacity-60"
            onClick={() => runSaveAsVersion(docVersion)}
          >
            {saveState === 'saving'
              ? '保存中…'
              : saveState === 'saved'
                ? '已存为流程版本'
                : saveState === 'error'
                  ? '保存失败，重试'
                  : '存为流程版本'}
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

// 级别徽标（严重/一般/提示）：与 review-panel SEVERITY_CONFIG 的中文与色系对齐
function SeverityTag({
  severity,
  fixed,
}: {
  severity: string;
  fixed?: boolean;
}) {
  const map: Record<string, { l: string; c: string }> = {
    high: { l: '严重', c: '#F5222D' },
    medium: { l: '一般', c: '#FA8C16' },
    low: { l: '提示', c: '#1a66fb' },
  };
  const it = map[severity] || { l: severity, c: '#8C8C8C' };
  return (
    <span
      className="mt-px shrink-0 rounded px-1 py-px text-[10px] font-bold leading-4 text-white"
      style={{ backgroundColor: fixed ? '#52C41A' : it.c }}
    >
      {it.l}
    </span>
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
