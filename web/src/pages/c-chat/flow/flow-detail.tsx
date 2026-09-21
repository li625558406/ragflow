// web/src/pages/c-chat/flow/flow-detail.tsx
import ChapteredMarkdown from '@/components/chaptered-markdown';
import { Button } from '@/components/ui/button';
import type { ITemplateFillDownload } from '@/hooks/template-fill-stream';
import {
  downloadFileReviewVersion,
  downloadFileReviewVersionBlob,
} from '@/services/file-review-service';
import {
  archiveFlow,
  deleteFlow,
  deleteFlowVersion,
  downloadVersionBlob,
  getFlowDetail,
  listCandidates,
  submitFlow,
  uploadFlowVersion,
} from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  Download,
  Eye,
  FileText,
  MessageSquare,
  MessagesSquare,
  Trash2,
  User,
} from 'lucide-react';
import type { ReactNode } from 'react';
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import FileReviewProgress from '../file-review-progress';
import ReviewPanel from '../review-panel';
import TemplateFillProgress from '../template-fill-progress';
import FlowAiPanel, { type FlowReviewControl } from './flow-ai-panel';
import type {
  FlowAiChatItem,
  FlowLiveChat,
  FlowVersionItem,
} from './flow-types';
import {
  FLOW_STEPS,
  relTime,
  STATUS_BADGE,
  STATUS_COLOR,
  STATUS_LABEL,
  statusStepIndex,
} from './flow-utils';

const HOLDER_FIELD: Record<
  string,
  'initiator_id' | 'leader_id' | 'handler_id'
> = {
  initiator: 'initiator_id',
  leader: 'leader_id',
  handler: 'handler_id',
  summary: 'initiator_id',
};

const TERMINAL_STATUS = new Set(['archived', 'cancelled']);

/** 版本时间线每页条数（倒序展示，超出部分点「查看更多」加载） */
const VERSION_PAGE_SIZE = 5;

/**
 * LLM 输出适配：think 标签前后补空行。
 * MarkdownContent 把 <think> 转为 <section> HTML 块后，CommonMark 规定 HTML 块
 * 持续到空行结束——若 `</think># 标题` 之间无空行，标题会被吞进块内渲染成字面文本。
 */
const normalizeLlmMarkdown = (text: string) =>
  text.replace(/<think>/g, '\n\n<think>').replace(/<\/think>/g, '</think>\n\n');

export default function FlowDetail({
  flowId,
  commentPortal,
  visible = true,
  onCommentsCount,
  onChanged,
  onDeleted,
  onTplPreviewOpenChange,
  onReviewOpenChange,
  onBusyChange,
}: {
  flowId: string;
  /** 批注模块 portal 挂载点（外层左侧流程栏下方），不传则不渲染批注模块 */
  commentPortal?: HTMLElement | null;
  /** 本详情是否为当前选中流程（flow-panel 常驻挂载集透传）：隐藏实例强制收起
   *  实时预览（预览内存治理，设计 2026-09-16），保证至多一棵大文档 DOM 树 */
  visible?: boolean;
  /** 当前版本批注数变化时上报（供外层折叠开关展示角标） */
  onCommentsCount?: (count: number) => void;
  onChanged: () => void;
  /** 流程被删除后回调（外层清空选中态并刷新列表） */
  onDeleted?: () => void;
  /** 范本预览抽屉开/关上报（透传自 ConversationView）：外层收缩布局为抽屉腾位 */
  onTplPreviewOpenChange?: (open: boolean) => void;
  /** 版本文件审核抽屉开/关上报：外层收缩布局为抽屉腾位 */
  onReviewOpenChange?: (open: boolean) => void;
  /** AI 对话进行中上报：外层据此在切换流程时保持本详情挂载（防 SSE 中断丢状态） */
  onBusyChange?: (busy: boolean) => void;
}) {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  // 文件审核入口（状态在 FlowAiPanel 内部，经回调上报到这里渲染到顶部按钮行）
  const [reviewCtl, setReviewCtl] = useState<FlowReviewControl | null>(null);
  // 进行中的一轮 AI 对话（发送后未保存前的流式状态）
  const [liveChat, setLiveChat] = useState<FlowLiveChat | null>(null);
  // 对话进行中上报：外层据此在切换流程时保持本详情挂载（防 SSE 中断丢状态）
  const liveBusy = Boolean(liveChat?.busy);
  useEffect(() => {
    onBusyChange?.(liveBusy);
  }, [liveBusy, onBusyChange]);
  // 确认卡片提交回写函数（FlowAiPanel 上报）：提交成功后回写流式态，
  // 使归约器的 confirm_timeout 守卫（!submitted）生效，消除超时/提交竞态假象
  const [markConfirmSubmitted, setMarkConfirmSubmitted] = useState<
    (() => void) | null
  >(null);
  // 多范本选择卡片提交回写函数（FlowAiPanel 上报，语义同 confirm）
  const [markSelectSubmitted, setMarkSelectSubmitted] = useState<
    (() => void) | null
  >(null);
  // AI 范本填写成稿「存为流程版本」：成稿 blob（agents/download）→ flow 版本
  const [savingDocIds, setSavingDocIds] = useState<
    Record<string, 'saving' | 'saved' | 'error'>
  >({});
  // 范本预览抽屉开/关：打开时收起右侧「版本记录」栏给抽屉腾位
  const [tplPreviewOpen, setTplPreviewOpen] = useState(false);
  const saveDownloadAsVersion = useCallback(
    async (dl: ITemplateFillDownload) => {
      const docId = dl.doc_id || '';
      if (!docId || savingDocIds[docId] === 'saving') return;
      setSavingDocIds((p) => ({ ...p, [docId]: 'saving' }));
      try {
        // url 缺失直接失败：fetch('') 会请求当前页面把 index.html 存成版本
        if (!dl.url) throw new Error('download url missing');
        const resp = await fetch(dl.url, {
          headers: {
            Authorization: localStorage.getItem('Authorization') || '',
          },
        });
        if (!resp.ok) throw new Error(`download failed ${resp.status}`);
        const blob = await resp.blob();
        const fd = new FormData();
        fd.append(
          'file',
          new File([blob], dl.filename || '成稿.docx', {
            type: dl.mime_type || 'application/octet-stream',
          }),
        );
        fd.append('source', 'ai_template_fill');
        await uploadFlowVersion(flowId, fd);
        setSavingDocIds((p) => ({ ...p, [docId]: 'saved' }));
      } catch (e) {
        console.warn('save as flow version failed', e);
        setSavingDocIds((p) => ({ ...p, [docId]: 'error' }));
        return;
      }
      // 缓存失效放在 try 外：invalidate 抛错不应把本次保存标为 error，
      // 否则用户按「失败重试」会重复建版本
      await qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
    },
    [flowId, qc, savingDocIds],
  );
  // 文件审核成稿「存为流程版本」：审核（含修复轮）收口后，把成稿经 review 专用
  // download 端点取 Blob（@login_required 只认请求头，同 downloadFileReviewVersion）
  // 上传为 flow 版本 —— 版本记录 /「查看文件内容」随之可见修改后文件。
  // 与范本填写 saveDownloadAsVersion 同构；失败向上抛出，由进度卡把按钮翻转为
  // 「保存失败，重试」（卡片自管状态，此处不落本地 saving 集合）。
  const saveReviewAsVersion = useCallback(
    async (taskId: string, fileVersion: string) => {
      const blob = await downloadFileReviewVersionBlob(taskId, fileVersion);
      const fd = new FormData();
      fd.append(
        'file',
        new File([blob], `文件审核_${fileVersion}.docx`, {
          type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }),
      );
      fd.append('source', 'ai_file_review');
      await uploadFlowVersion(flowId, fd);
      await qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
    },
    [flowId, qc],
  );
  const uploadInputRef = useRef<HTMLInputElement>(null);
  // 版本文件只读查看（所有参与人可用）：版本转 document 后交给 ReviewPanel
  const [viewOpen, setViewOpen] = useState(false);
  // AI 面板「文件审核」抽屉开/关（FlowAiPanel 上报）
  const [aiReviewOpen, setAiReviewOpen] = useState(false);
  // 审核抽屉（版本查看 / AI 面板文件审核）任一打开即上报外层腾位联动，卸载时兜底关闭
  useEffect(() => {
    onReviewOpenChange?.(viewOpen || aiReviewOpen);
    return () => onReviewOpenChange?.(false);
  }, [viewOpen, aiReviewOpen, onReviewOpenChange]);
  const [viewPreparing, setViewPreparing] = useState(false);
  const [viewPendingId, setViewPendingId] = useState('');
  const [viewFileId, setViewFileId] = useState('');
  const [viewFileName, setViewFileName] = useState('');
  const [viewVersionId, setViewVersionId] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['flow-detail', flowId],
    queryFn: () => getFlowDetail(flowId),
  });

  // 参与人昵称映射（负责人/批注人展示用；与创建对话框共享候选数据）
  const { data: candidates } = useQuery({
    queryKey: ['flow-candidates'],
    queryFn: listCandidates,
    staleTime: 5 * 60_000,
  });
  const nicknameMap = useMemo(() => {
    const m = new Map<string, string>();
    (candidates?.list ?? []).forEach((u) => m.set(u.id, u.nickname));
    return m;
  }, [candidates]);

  const selectedVersion: FlowVersionItem | null = useMemo(() => {
    const versions = data?.versions ?? [];
    if (!versions.length) return null;
    // 默认勾选聚焦最新一条（版本号最大），而非 current_version_id——
    // 回退后 current 指向旧版本，默认应仍落在最新记录上；用户点击后以用户选择为准
    const newest = versions.reduce((a, b) =>
      b.version_no > a.version_no ? b : a,
    );
    if (!selectedVersionId) return newest;
    return versions.find((v) => v.id === selectedVersionId) ?? newest;
  }, [data, selectedVersionId]);

  // 版本倒序（最新在前）+ 分页展示：初始一页，点「查看更多」再加载一页
  const [visibleCount, setVisibleCount] = useState(VERSION_PAGE_SIZE);
  useEffect(() => {
    setVisibleCount(VERSION_PAGE_SIZE);
  }, [flowId]);
  // 切换流程后重置版本选择：回到「默认最新一条」，不带入上一流程的手动选择
  useEffect(() => {
    setSelectedVersionId(null);
  }, [flowId]);
  const sortedVersions = useMemo(
    () =>
      [...(data?.versions ?? [])].sort((a, b) => b.version_no - a.version_no),
    [data],
  );
  const visibleVersions = useMemo(
    () => sortedVersions.slice(0, visibleCount),
    [sortedVersions, visibleCount],
  );

  const commentsOf = useMemo(() => {
    if (!data || !selectedVersion) return [];
    return (data.comments ?? []).filter(
      (c) => c.version_id === selectedVersion.id,
    );
  }, [data, selectedVersion]);

  // 批注数上报给外层折叠开关
  useEffect(() => {
    onCommentsCount?.(commentsOf.length);
  }, [commentsOf.length, onCommentsCount]);

  if (isLoading) {
    return <DetailSkeleton />;
  }
  if (isError || !data) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-red-500">
        流程详情加载失败
      </div>
    );
  }

  const { flow, viewer } = data;
  const terminal = TERMINAL_STATUS.has(flow.status);
  const isOwner = !!viewer?.is_owner;
  const isInitiator = !!viewer?.is_initiator;
  // 版本删除权限：仅审核领导（后端同校验）；其余人按钮置灰
  const isLeader = !!viewer?.is_leader;
  const holderField = HOLDER_FIELD[flow.status];
  const holderId = holderField
    ? (flow[holderField] as string | undefined) || ''
    : '';
  const holderName = holderId ? nicknameMap.get(holderId) || holderId : '';

  const handleDownload = async (v: FlowVersionItem) => {
    try {
      const blob = await downloadVersionBlob(flowId, v.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = v.file_name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e: any) {
      setActionError(e?.message || '下载失败，请稍后重试');
    }
  };

  const doAction = async (fn: () => Promise<unknown>) => {
    setActionError('');
    setBusy(true);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
      onChanged();
    } catch (e: any) {
      setActionError(e?.message || '操作失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (action: 'next' | 'return') => {
    doAction(() => submitFlow(flowId, action));
  };

  const handleArchive = () => {
    doAction(() => archiveFlow(flowId));
  };

  const handleUploadFile = (file: File | null) => {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    doAction(() => uploadFlowVersion(flowId, fd));
  };

  /** 只读查看版本文件内容：下载 blob → 转 document → ReviewPanel 展示。
   * 所有参与人可用（不限于当前节点负责人），同一版本已加载时直接复用。 */
  const handleViewFile = async (v: FlowVersionItem) => {
    if (viewFileId && viewVersionId === v.id) {
      setViewOpen(true);
      return;
    }
    setViewPreparing(true);
    setViewPendingId(v.id);
    setActionError('');
    try {
      const blob = await downloadVersionBlob(flowId, v.id);
      const file = new File([blob], v.file_name, {
        type: v.file_type || 'application/octet-stream',
      });
      const fd = new FormData();
      fd.append('file', file);
      const resp = await fetch('/api/v1/documents/upload', {
        method: 'POST',
        headers: { Authorization: localStorage.getItem('Authorization') || '' },
        body: fd,
      });
      const result = await resp.json();
      const d = Array.isArray(result?.data) ? result.data[0] : result?.data;
      if (result?.code === 0 && d?.id) {
        setViewFileId(d.id);
        setViewFileName(v.file_name);
        setViewVersionId(v.id);
        setViewOpen(true);
        return;
      }
      setActionError(result?.message || '文件打开失败，请稍后重试');
    } catch (e: any) {
      setActionError(e?.message || '文件打开失败，请稍后重试');
    } finally {
      setViewPreparing(false);
      setViewPendingId('');
    }
  };

  /** 删除流程（仅发起人；仅已作废）：级联删版本/批注/AI记录，删后回到空态 */
  const handleDeleteFlow = () => {
    if (
      !window.confirm(
        `确定删除流程「${flow.title}」？版本、批注与对话记录将一并删除，删除后不可恢复。`,
      )
    )
      return;
    doAction(async () => {
      await deleteFlow(flowId);
      onDeleted?.();
    });
  };

  const handleDeleteVersion = (v: FlowVersionItem) => {
    if (
      !window.confirm(
        `确定删除版本 v${v.version_no}（${v.file_name}）？锚定该版本的批注将一并删除，删除后不可恢复。`,
      )
    )
      return;
    doAction(async () => {
      await deleteFlowVersion(flowId, v.id);
      // 删除的是当前选中版本时清空选中态，回落到最新版本
      setSelectedVersionId((prev) => (prev === v.id ? null : prev));
    });
  };

  return (
    <div
      key={flowId}
      className="flex h-full min-w-0 flex-col text-[#222] motion-reduce:animate-none animate-in fade-in slide-in-from-bottom-1 duration-200"
    >
      {/* 状态条：标题行 + 流程步骤条 */}
      <div className="shrink-0 border-b border-[#F0F0F0] px-4 pb-2.5 pt-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <FileText className="h-4 w-4 shrink-0 text-[#1a66fb]" />
            <span className="truncate text-base font-semibold">
              {flow.title}
            </span>
            <span
              className={`ml-1 shrink-0 rounded-md px-2 py-0.5 text-xs font-medium ${
                STATUS_BADGE[flow.status] ?? 'bg-[#EFF4FF] text-[#1a66fb]'
              }`}
            >
              {STATUS_LABEL[flow.status] ?? flow.status}
              {!terminal && holderName && `·${holderName}`}
            </span>
            {!terminal && holderId && (
              <span className="hidden shrink-0 items-center gap-1 rounded-full bg-[#F7F8FA] px-2 py-0.5 text-xs text-[#888] lg:flex">
                <User className="h-3 w-3" />
                当前负责人：{holderName}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {reviewCtl?.visible && (
              <Button
                size="sm"
                variant={reviewCtl.active ? 'outline' : 'default'}
                disabled={busy}
                onClick={reviewCtl.toggle}
                className={
                  reviewCtl.active
                    ? ''
                    : 'bg-[#1a66fb] font-semibold shadow-[0_2px_8px_rgba(26,102,251,0.45)] hover:bg-[#0f56e0] hover:shadow-[0_2px_10px_rgba(26,102,251,0.6)]'
                }
              >
                <FileText className="h-3.5 w-3.5" strokeWidth={2.5} />
                {reviewCtl.active ? '关闭审核' : '文件审核'}
              </Button>
            )}
            {isInitiator && flow.status === 'cancelled' && (
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={handleDeleteFlow}
              >
                删除流程
              </Button>
            )}
            {isOwner && !terminal && flow.status === 'summary' && (
              <Button size="sm" disabled={busy} onClick={handleArchive}>
                归档
              </Button>
            )}
            {isOwner && !terminal && flow.status !== 'summary' && (
              <>
                {flow.status !== 'initiator' && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => handleSubmit('return')}
                  >
                    退回上一节点
                  </Button>
                )}
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => handleSubmit('next')}
                >
                  提交下一节点
                </Button>
              </>
            )}
            {isOwner && !terminal && (
              <>
                <input
                  ref={uploadInputRef}
                  type="file"
                  accept=".doc,.docx"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    e.target.value = '';
                    if (f && !/\.(doc|docx)$/i.test(f.name)) {
                      window.alert('仅支持 doc/docx 格式的文档');
                      return;
                    }
                    handleUploadFile(f);
                  }}
                />
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => uploadInputRef.current?.click()}
                >
                  上传修改版
                </Button>
              </>
            )}
          </div>
        </div>
        {/* 流程步骤条：发起 → 领导审批 → 处理 → 汇总审核 → 归档 */}
        <div className="mt-2.5">
          <FlowStepper status={flow.status} />
        </div>
      </div>

      {actionError && (
        <div className="shrink-0 bg-red-50 px-4 py-1.5 text-xs text-red-500">
          {actionError}
        </div>
      )}

      {/* 中部：预览/批注 + 版本时间线 */}
      <div className="flex min-h-0 flex-1 gap-3 p-3">
        {/* 左：预览 + AI + 批注 */}
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          {/* 主区域：默认展示 AI 对话记录（文件预览收进「文件审核」抽屉） */}
          <div className="min-h-0 flex-1 overflow-auto rounded-lg bg-[#FAFAFA] p-3">
            <ConversationView
              chats={data.ai_chats ?? []}
              live={liveChat}
              authorNames={Object.fromEntries(nicknameMap)}
              visible={visible}
              onConfirmSubmitted={markConfirmSubmitted ?? undefined}
              onSelectSubmitted={markSelectSubmitted ?? undefined}
              onLivePreviewOpenChange={(open) => {
                setTplPreviewOpen(open);
                onTplPreviewOpenChange?.(open);
              }}
              reviewCtl={reviewCtl}
              onReviewSaveAsVersion={saveReviewAsVersion}
              extraAction={(dl) => {
                const st = savingDocIds[dl.doc_id || ''];
                return (
                  <button
                    disabled={st === 'saving' || st === 'saved' || !dl.doc_id}
                    onClick={() => saveDownloadAsVersion(dl)}
                    className={`ml-2 shrink-0 rounded px-2 py-0.5 transition-colors ${
                      st === 'saved'
                        ? 'bg-[#F0F9EB] text-[#67C23A]'
                        : st === 'error'
                          ? 'bg-[#FDE9E9] text-red-500'
                          : 'border border-[#BFD3F5] bg-[#F0F5FF] text-[#1a66fb] hover:bg-[#E3EDFF]'
                    }`}
                  >
                    {st === 'saving'
                      ? '保存中…'
                      : st === 'saved'
                        ? '已存版本'
                        : st === 'error'
                          ? '失败重试'
                          : '存为流程版本'}
                  </button>
                );
              }}
            />
          </div>

          {isOwner && !terminal && (
            <FlowAiPanel
              flowId={flowId}
              version={selectedVersion}
              aiChats={data.ai_chats ?? []}
              comments={commentsOf}
              commentAuthors={Object.fromEntries(nicknameMap)}
              isOwner={isOwner}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
                onChanged();
              }}
              onLiveChatChange={setLiveChat}
              onReviewControlChange={setReviewCtl}
              onReviewOpenChange={setAiReviewOpen}
              onConfirmSubmittedReady={setMarkConfirmSubmitted}
              onSelectSubmittedReady={setMarkSelectSubmitted}
            />
          )}

          {/* 批注区已移至外层左侧流程栏下方（commentPortal） */}
        </div>

        {/* 右：版本时间线（范本预览/文件审核抽屉打开时收起腾位） */}
        <div
          className={`flex shrink-0 flex-col overflow-hidden rounded-lg border border-[#F0F0F0] bg-white transition-all duration-300 ease-in-out ${
            tplPreviewOpen || viewOpen || aiReviewOpen ? 'w-0 border-0' : 'w-64'
          }`}
        >
          {/* 标题栏蓝底色，醒目区分（与左栏批注开关条同款色系） */}
          <div className="border-b border-[#D6E2FF] bg-[#EFF4FF] px-3 py-2 text-sm font-medium text-[#1a66fb]">
            版本记录
            <span className="ml-1 text-xs font-normal text-[#999]">
              {sortedVersions.length} 条
            </span>
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-2">
            {/* 时间线：左侧竖线 + 节点圆点 */}
            <div className="relative">
              {visibleVersions.length > 1 && (
                <span
                  aria-hidden
                  className="absolute bottom-3 left-[5px] top-3 w-px bg-[#ECECEC]"
                />
              )}
              {visibleVersions.map((v) => {
                const active = selectedVersion?.id === v.id;
                const cnt = (data.comments ?? []).filter(
                  (c) => c.version_id === v.id,
                ).length;
                return (
                  <div
                    key={v.id}
                    onClick={() => setSelectedVersionId(v.id)}
                    className={`group relative cursor-pointer rounded-lg py-2 pl-5 pr-2 transition-colors duration-150 motion-reduce:animate-none animate-in fade-in slide-in-from-right-1 fill-mode-both ${
                      active ? 'bg-[#F0F5FF]' : 'hover:bg-[#F7F8FA]'
                    }`}
                  >
                    {/* 节点圆点：选中实心蓝，AI 产出蓝描边，人工上传灰描边 */}
                    <span
                      aria-hidden
                      className={`absolute left-0 top-[15px] h-[11px] w-[11px] rounded-full border-2 bg-white transition-colors duration-150 ${
                        active
                          ? 'border-[#1a66fb] bg-[#1a66fb] shadow-[0_0_0_3px_rgba(26,102,251,0.15)]'
                          : v.source === 'manual_upload'
                            ? 'border-[#CCC]'
                            : 'border-[#1a66fb]'
                      }`}
                    />
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={`truncate text-sm font-medium ${
                          active ? 'text-[#1a66fb]' : 'text-[#222]'
                        }`}
                      >
                        v{v.version_no} {v.file_name}
                      </span>
                      {cnt > 0 && (
                        <span className="flex shrink-0 items-center gap-0.5 rounded-full bg-[#EFF4FF] px-1.5 text-[10px] text-[#1a66fb]">
                          <MessageSquare className="h-2.5 w-2.5" />
                          {cnt}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-xs text-[#888]">
                      <span
                        className={`rounded px-1 text-[10px] ${
                          v.source === 'manual_upload'
                            ? 'bg-[#F2F3F5] text-[#888]'
                            : 'bg-[#EFF4FF] text-[#1a66fb]'
                        }`}
                      >
                        {v.source === 'manual_upload'
                          ? '人工上传'
                          : v.source === 'ai_output'
                            ? 'AI 产出'
                            : 'AI 范本填写'}
                      </span>
                      <span className="truncate">{relTime(v.create_time)}</span>
                      <span className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100">
                        {/* 操作：查看 + 下载 + 删除（删除仅领导可用，其余置灰） */}
                        <button
                          type="button"
                          title="查看文件内容"
                          disabled={viewPreparing}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleViewFile(v);
                          }}
                          className={`cursor-pointer rounded-md p-1 text-[#1a66fb] transition-colors hover:bg-[#E1EBFF] disabled:cursor-wait ${
                            viewPreparing && viewPendingId === v.id
                              ? 'animate-pulse'
                              : ''
                          }`}
                        >
                          <Eye className="h-3.5 w-3.5" strokeWidth={2.5} />
                        </button>
                        <button
                          type="button"
                          title="下载该版本"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDownload(v);
                          }}
                          className="cursor-pointer rounded-md p-1 text-[#1a66fb] transition-colors hover:bg-[#E1EBFF]"
                        >
                          <Download className="h-3.5 w-3.5" strokeWidth={2.5} />
                        </button>
                        <button
                          type="button"
                          disabled={!isLeader || terminal || busy}
                          title={
                            terminal
                              ? '流程已结束，不可删除版本'
                              : isLeader
                                ? '删除该版本（锚定的批注一并删除）'
                                : '仅审核领导可删除版本'
                          }
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteVersion(v);
                          }}
                          className={`cursor-pointer rounded-md p-1 transition-colors ${
                            isLeader && !terminal && !busy
                              ? 'text-[#E5484D] hover:bg-[#FFE4E2]'
                              : 'cursor-not-allowed text-[#CCC]'
                          }`}
                        >
                          <Trash2 className="h-3.5 w-3.5" strokeWidth={2.5} />
                        </button>
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
            {sortedVersions.length > visibleCount && (
              <button
                type="button"
                onClick={() => setVisibleCount((c) => c + VERSION_PAGE_SIZE)}
                className="mt-1 flex w-full cursor-pointer items-center justify-center gap-1 rounded-md py-1.5 text-xs font-medium text-[#1a66fb] transition-colors hover:bg-[#F7FAFF]"
              >
                <ChevronDown className="h-3.5 w-3.5" />
                查看更多（剩余 {sortedVersions.length - visibleCount} 条）
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 批注模块：portal 到外层左侧流程栏（与流程列表平分高度） */}
      {commentPortal &&
        createPortal(
          <div className="flex h-full min-h-0 flex-col border-t border-[#F0F0F0] p-2 text-[#222]">
            <div className="flex min-h-0 flex-1 flex-col bg-white p-3">
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
                {commentsOf.length === 0 && (
                  <div className="text-xs text-[#999]">暂无批注</div>
                )}
                {commentsOf.map((c) => {
                  /* 级别徽标：与审核面板批注卡同口径（严重/一般/提示），存量无值视为一般 */
                  const sev = ['high', 'medium', 'low'].includes(
                    c.severity || '',
                  )
                    ? (c.severity as string)
                    : 'medium';
                  const sevStyle = {
                    high: { color: '#FF4D4F', bg: '#FFF2F0' },
                    medium: { color: '#FA8C16', bg: '#FFF7E6' },
                    low: { color: '#1890FF', bg: '#F0F5FF' },
                  }[sev] ?? { color: '#FA8C16', bg: '#FFF7E6' };
                  const sevLabel =
                    { high: '严重', medium: '一般', low: '提示' }[sev] ??
                    '一般';
                  return (
                    <div
                      key={c.id}
                      className="rounded-md bg-[#F7F8FA] px-2.5 py-1.5"
                    >
                      <div className="flex items-center justify-between text-xs text-[#888]">
                        <span className="flex min-w-0 items-center gap-1">
                          <span className="truncate">
                            {nicknameMap.get(c.user_id) || c.user_id}
                          </span>
                          <span
                            className="shrink-0 rounded px-1 py-px text-[10px] font-semibold"
                            style={{
                              color: sevStyle.color,
                              backgroundColor: sevStyle.bg,
                            }}
                          >
                            {sevLabel}
                          </span>
                        </span>
                        <span className="shrink-0">
                          {new Date(c.create_time).toLocaleString()}
                        </span>
                      </div>
                      <div className="mt-0.5 whitespace-pre-wrap text-sm text-[#333]">
                        {c.content}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>,
          commentPortal,
        )}

      {/* 版本文件只读查看（所有参与人可用）：不传批注增删/编辑回调，纯查看 + 批注边栏展示 */}
      <ReviewPanel
        open={viewOpen}
        onClose={() => setViewOpen(false)}
        fileId={viewFileId}
        fileName={viewFileName}
        annotations={[]}
        comments={commentsOf}
        commentAuthors={Object.fromEntries(nicknameMap)}
      />
    </div>
  );
}

/**
 * 流程步骤条：发起 → 领导审批 → 处理 → 汇总审核 → 归档。
 * 每个节点独立配色（发起蓝/领导紫/处理青/汇总橙/归档绿）：
 * 已完成节点实心 + 对勾（入场缩放弹出），当前节点描边 + 呼吸光圈，
 * 连线随进度填充为上一节点色，节点文字同色区分；已作废流程全部节点置灰。
 */
function FlowStepper({ status }: { status: string }) {
  const currentIdx = statusStepIndex(status);
  const cancelled = status === 'cancelled';
  return (
    <div className="flex items-center">
      {FLOW_STEPS.map((s, i) => {
        const done = i < currentIdx;
        const current = i === currentIdx;
        const color = cancelled
          ? '#C4C4C4'
          : STATUS_COLOR[s.key]?.main || '#1a66fb';
        return (
          <Fragment key={s.key}>
            {i > 0 && (
              <span
                aria-hidden
                className="relative mx-1.5 h-0.5 w-7 shrink-0 overflow-hidden rounded bg-[#E8E8E8]"
              >
                <span
                  className="absolute inset-y-0 left-0 rounded transition-[width] duration-500 ease-out"
                  style={{
                    width: i <= currentIdx && !cancelled ? '100%' : '0',
                    background:
                      STATUS_COLOR[FLOW_STEPS[i - 1].key]?.main || '#1a66fb',
                  }}
                />
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-[18px] w-[18px] shrink-0 items-center justify-center">
                {current && !cancelled && (
                  <span
                    aria-hidden
                    className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-20 motion-reduce:animate-none"
                    style={{ background: color }}
                  />
                )}
                <span
                  className="relative flex h-[18px] w-[18px] items-center justify-center rounded-full border-2 bg-white transition-colors duration-200"
                  style={{
                    borderColor: done || current ? color : '#D8D8D8',
                    background: done ? color : '#FFFFFF',
                  }}
                >
                  {done && (
                    <Check
                      className="h-2.5 w-2.5 text-white motion-reduce:animate-none animate-in zoom-in-50 duration-200"
                      strokeWidth={3.5}
                    />
                  )}
                  {current && !cancelled && (
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ background: color }}
                    />
                  )}
                </span>
              </span>
              <span
                className={`whitespace-nowrap text-xs ${
                  cancelled ? 'text-[#AAA]' : 'font-medium'
                }`}
                style={
                  cancelled
                    ? undefined
                    : { color: done || current ? color : '#AAAAAA' }
                }
              >
                {s.label}
              </span>
            </div>
          </Fragment>
        );
      })}
      {cancelled && (
        <span className="ml-3 rounded bg-[#FFF1F0] px-1.5 py-0.5 text-[10px] text-[#E5484D]">
          流程已作废
        </span>
      )}
    </div>
  );
}

/** 详情加载骨架屏 */
function DetailSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[#F0F0F0] px-4 pb-3 pt-3">
        <div className="flex items-center gap-2">
          <div className="h-5 w-56 animate-pulse rounded bg-[#F0F1F3]" />
          <div className="h-4 w-20 animate-pulse rounded bg-[#F2F3F5]" />
        </div>
        <div className="mt-3 flex items-center gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="flex items-center gap-1.5">
              <div className="h-[18px] w-[18px] animate-pulse rounded-full bg-[#F0F1F3]" />
              <div className="h-2.5 w-10 animate-pulse rounded bg-[#F4F5F7]" />
              {i < 4 && <div className="ml-1 h-0.5 w-7 bg-[#F2F3F5]" />}
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 gap-3 p-3">
        <div className="flex min-w-0 flex-1 animate-pulse rounded-lg bg-[#F5F6F8]" />
        <div className="w-64 shrink-0 animate-pulse rounded-lg bg-[#F5F6F8]" />
      </div>
    </div>
  );
}

/** AI 对话记录视图：指令（右）+ 回复（左），含存版本标记；live 为进行中的一轮流式对话 */
// canvas 运行期错误兜底消息（后端以「执行失败：」前缀落库）：气泡红底红字显式提示
const isErrorResponse = (t: string) => t.trimStart().startsWith('执行失败：');

// flow_ai_chat.file_review 字段解析："{file_id,task_id}" JSON 字符串 → 挂卡目标；
// 空串/畸形/缺键静默返 null（持久化是尽力而为，不因坏数据挂掉整个对话列表渲染）
const parseFileReview = (
  raw: string | undefined,
): { fileId: string; taskId: string } | null => {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed?.fileId && parsed?.taskId) return parsed;
  } catch {
    // 旧数据/畸形 JSON：静默忽略
  }
  return null;
};

// flow_ai_chat.files 字段解析："[{id,name}]" JSON 字符串 → 用户气泡附件 chip；
// 空串/畸形/空数组静默返空（发送时事实的尽力展示，坏数据不挂列表渲染）
const parseChatFiles = (
  raw: string | undefined,
): { id: string; name: string }[] => {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length) {
      return parsed.filter((f) => f?.id);
    }
  } catch {
    // 旧数据/畸形 JSON：静默忽略
  }
  return [];
};

/** 用户气泡附件 chip 行（浅色主题，与气泡 bg-[#EFF4FF] 同层） */
const ChatFileChips = ({
  files,
}: {
  files: { id: string; name: string }[];
}) => {
  if (!files.length) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {files.map((f) => (
        <span
          key={f.id}
          className="inline-flex max-w-[240px] items-center gap-1 rounded-md bg-white/70 px-2 py-0.5 text-[11px] text-[#4a6285]"
          title={f.name}
        >
          <FileText className="h-3 w-3 shrink-0" strokeWidth={2} />
          <span className="truncate">{f.name}</span>
        </span>
      ))}
    </div>
  );
};

function ConversationView({
  chats,
  live,
  authorNames,
  extraAction,
  onConfirmSubmitted,
  onSelectSubmitted,
  onLivePreviewOpenChange,
  visible = true,
  reviewCtl,
  onReviewSaveAsVersion,
}: {
  chats: FlowAiChatItem[];
  live: FlowLiveChat | null;
  /** user_id -> 昵称（对话归属展示） */
  authorNames?: Record<string, string>;
  /** 成稿条目附加动作（存为流程版本按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
  /** 确认卡片提交成功后回调：回写流式态（FlowAiPanel 经 onConfirmSubmittedReady 上报的函数） */
  onConfirmSubmitted?: () => void;
  /** 多范本选择卡片提交成功后回调：回写流式态（同 onConfirmSubmitted 模式） */
  onSelectSubmitted?: () => void;
  /** 范本预览抽屉开/关上报（供外层收缩布局腾位） */
  onLivePreviewOpenChange?: (open: boolean) => void;
  /** 本详情是否为当前选中流程（FlowDetail 透传）：隐藏实例强制收起实时预览 */
  visible?: boolean;
  /** T15：FileReviewProgress 回调桥（FlowAiPanel 上报的 reviewCtl.openWithFile） */
  reviewCtl?: FlowReviewControl | null;
  /** 文件审核成稿「存为流程版本」回调（签名与卡片 onSaveAsVersion 一致，
   *  透传给已入库记录与 live 两处进度卡；不传则卡片不渲染该按钮） */
  onReviewSaveAsVersion?: (
    taskId: string,
    fileVersion: string,
  ) => Promise<void>;
  /** T15：FileReviewProgress 成稿预览 —— 本任务已改走专用 download 端点
   *  （GET /api/v1/file/review/<taskId>/<fileVersion>/download），不再需要父层透传回调。
   *  旧 prop 已删（无外部调用方）。 */
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  // 挂起确认卡出现信号（范本选择/字段确认）：卡在范本行上方，若不滚动用户可能
  // 完全看不到可操作入口，干等超时后误以为系统「自己执行」了
  const pendingCardSignal = live?.templateFill?.pendingSelect
    ? 'select'
    : live?.templateFill?.pendingConfirm
      ? 'confirm'
      : '';
  // 流式回复增长 / 挂起确认卡出现时自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [live?.response, pendingCardSignal]);

  if (!chats.length && !live) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#EFF4FF]">
          <MessagesSquare className="h-5 w-5 text-[#1a66fb]" />
        </div>
        <div className="text-sm text-[#666]">暂无对话记录</div>
        <div className="text-xs text-[#AAA]">
          可在下方「AI 处理」输入指令，让 AI 协助处理当前版本文档
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {chats.map((c) => (
        <div key={c.id} className="space-y-1.5">
          <div className="flex justify-end">
            <div className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-[#EFF4FF] px-3 py-1.5 text-sm leading-relaxed text-[#1a3a6b]">
              {c.instruction}
              <ChatFileChips files={parseChatFiles(c.files)} />
            </div>
          </div>
          <div className="flex justify-start">
            <div
              className={`max-w-[90%] rounded-lg rounded-bl-sm border px-3 py-1.5 text-sm leading-relaxed ${
                isErrorResponse(c.response)
                  ? 'border-red-300 bg-red-50 text-red-700'
                  : 'border-[#ECECEC] bg-white text-[#333]'
              }`}
            >
              <ChapteredMarkdown
                content={normalizeLlmMarkdown(c.response) || '（无回复内容）'}
                loading={false}
              />
            </div>
          </div>
          {/* 文件审核进度卡持久化：记录带 file_review（{file_id,task_id} JSON）
              时挂卡（组件内部自管轮询与数据），刷新/重进后历史回复的卡片恢复。
              live 态卡片由 ConversationView 底部 live 分支渲染，此处只管已入库记录。 */}
          {parseFileReview(c.file_review) && (
            <div className="max-w-[90%]">
              <FileReviewProgress
                fileId={parseFileReview(c.file_review)!.fileId}
                taskId={parseFileReview(c.file_review)!.taskId}
                onOpenReview={(annotations) => {
                  reviewCtl?.openWithFile?.(
                    parseFileReview(c.file_review)!.fileId,
                    '文件审核',
                    annotations as any,
                  );
                }}
                onPreviewDoc={(fileVersion) => {
                  const tid = parseFileReview(c.file_review)?.taskId;
                  if (!tid || !fileVersion) return;
                  void downloadFileReviewVersion(tid, fileVersion);
                }}
                onSaveAsVersion={onReviewSaveAsVersion}
              />
            </div>
          )}
          <div className="flex items-center gap-2 px-1 text-[10px] text-[#aaa]">
            {c.user_id && authorNames?.[c.user_id] && (
              <span className="rounded bg-[#F5F5F5] px-1 text-[#888]">
                {authorNames[c.user_id]}
              </span>
            )}
            <span>{new Date(c.create_time).toLocaleString()}</span>
            {c.output_version_id && (
              <span className="rounded bg-[#EFF4FF] px-1 text-[#1a66fb]">
                已存为新版本
              </span>
            )}
          </div>
        </div>
      ))}
      {/* 进行中的一轮：指令 + 流式回复（保存后并入上方正式记录） */}
      {live && (
        <div className="space-y-1.5">
          {live.instruction && (
            <div className="flex justify-end">
              <div className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-[#EFF4FF] px-3 py-1.5 text-sm leading-relaxed text-[#1a3a6b]">
                {live.instruction}
                <ChatFileChips files={live.files || []} />
              </div>
            </div>
          )}
          {/* 仅剩成稿条（回复已自动入库、response 为空）时不渲染空回复气泡 */}
          {(live.response || live.busy) && (
            <div className="flex justify-start">
              <div
                className={`max-w-[90%] rounded-lg rounded-bl-sm border px-3 py-1.5 text-sm leading-relaxed ${
                  isErrorResponse(live.response)
                    ? 'border-red-300 bg-red-50 text-red-700'
                    : 'border-[#ECECEC] bg-white text-[#333]'
                }`}
              >
                {live.response ? (
                  <ChapteredMarkdown
                    content={normalizeLlmMarkdown(live.response)}
                    loading={live.busy}
                  />
                ) : (
                  <span>
                    {live.busy ? '正在思考…' : '（无回复内容）'}
                    {live.busy && <span className="animate-pulse">▌</span>}
                  </span>
                )}
              </div>
            </div>
          )}
          {live.templateFill?.templates?.length ? (
            <div className="max-w-[90%]">
              <TemplateFillProgress
                state={live.templateFill}
                streaming={Boolean(live.busy)}
                onConfirmSubmitted={onConfirmSubmitted}
                onSelectSubmitted={onSelectSubmitted}
                onLivePreviewOpenChange={onLivePreviewOpenChange}
                forceClosedLivePreview={!visible}
                extraAction={(dl) => extraAction?.(dl)}
              />
            </div>
          ) : null}
          {/* T15：文件审核进度卡 —— 与 c-chat 共用同一组件（pages/c-chat/file-review-progress），
              流式期间 FileReview 节点产出 task_id 后由 FlowAiPanel 落到 live.fileReview。
              组件内部 useFileReviewState 自管 3s 轮询，不在此处引入新 SSE */}
          {live.fileReview ? (
            <div className="max-w-[90%]">
              <FileReviewProgress
                fileId={live.fileReview.fileId}
                taskId={live.fileReview.taskId}
                onOpenReview={(annotations) => {
                  // 复用 flow 审核入口面板：把 task_id 对应 fileId 推到 FlowAiPanel
                  // 内部并打开 reviewMode；fileName 用「文件审核」占位（具体成稿
                  // 文件名由 state.doc.file_name 给出，T16 联调时按需微调）。
                  // 2026-09-20：批注必须转发 —— file_review 批注在轮询 state 里，
                  // 面板原 annotations 来源（structured output）没有它，不转发
                  // 面板恒空（用户实测「打开审核面板看不到批注」）。
                  reviewCtl?.openWithFile?.(
                    live.fileReview!.fileId,
                    '文件审核',
                    annotations as any,
                  );
                }}
                onPreviewDoc={(fileVersion) => {
                  // R-8：服务端不下发对象名，预览/下载只凭 taskId + fileVersion
                  // 走专用 download 端点（不走 flow 的 downloadVersionBlob——
                  // 那条链路要求对象名匹配 FlowVersionService 记录，但审核成稿
                  // 是 task 维度的，不在 flow_version 表里）。
                  // 必须走 downloadFileReviewVersion（fetch 手挂 Authorization
                  // 取 Blob）：该端点带 @login_required 且只从请求头取用户，
                  // window.open 不带自定义头 → 必 401（同 c-chat/index.tsx）。
                  const tid = live.fileReview?.taskId;
                  if (!tid || !fileVersion) return;
                  void downloadFileReviewVersion(tid, fileVersion);
                }}
                onSaveAsVersion={onReviewSaveAsVersion}
              />
            </div>
          ) : null}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
