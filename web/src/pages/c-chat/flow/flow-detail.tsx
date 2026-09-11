// web/src/pages/c-chat/flow/flow-detail.tsx
import ChapteredMarkdown from '@/components/chaptered-markdown';
import { Button } from '@/components/ui/button';
import type { ITemplateFillDownload } from '@/hooks/template-fill-stream';
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
  onCommentsCount,
  onChanged,
  onDeleted,
  onTplPreviewOpenChange,
  onReviewOpenChange,
}: {
  flowId: string;
  /** 批注模块 portal 挂载点（外层左侧流程栏下方），不传则不渲染批注模块 */
  commentPortal?: HTMLElement | null;
  /** 当前版本批注数变化时上报（供外层折叠开关展示角标） */
  onCommentsCount?: (count: number) => void;
  onChanged: () => void;
  /** 流程被删除后回调（外层清空选中态并刷新列表） */
  onDeleted?: () => void;
  /** 范本预览抽屉开/关上报（透传自 ConversationView）：外层收缩布局为抽屉腾位 */
  onTplPreviewOpenChange?: (open: boolean) => void;
  /** 版本文件审核抽屉开/关上报：外层收缩布局为抽屉腾位 */
  onReviewOpenChange?: (open: boolean) => void;
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
  // 确认卡片提交回写函数（FlowAiPanel 上报）：提交成功后回写流式态，
  // 使归约器的 confirm_timeout 守卫（!submitted）生效，消除超时/提交竞态假象
  const [markConfirmSubmitted, setMarkConfirmSubmitted] = useState<
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
    if (!data) return null;
    const vid = selectedVersionId ?? data.flow.current_version_id;
    const versions = data.versions ?? [];
    return (
      versions.find((v) => v.id === vid) ??
      versions[versions.length - 1] ??
      null
    );
  }, [data, selectedVersionId]);

  // 版本倒序（最新在前）+ 分页展示：初始一页，点「查看更多」再加载一页
  const [visibleCount, setVisibleCount] = useState(VERSION_PAGE_SIZE);
  useEffect(() => {
    setVisibleCount(VERSION_PAGE_SIZE);
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
              onConfirmSubmitted={markConfirmSubmitted ?? undefined}
              onLivePreviewOpenChange={(open) => {
                setTplPreviewOpen(open);
                onTplPreviewOpenChange?.(open);
              }}
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
              flowTitle={flow.title}
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
                {commentsOf.map((c) => (
                  <div
                    key={c.id}
                    className="rounded-md bg-[#F7F8FA] px-2.5 py-1.5"
                  >
                    <div className="flex items-center justify-between text-xs text-[#888]">
                      <span className="truncate">
                        {nicknameMap.get(c.user_id) || c.user_id}
                      </span>
                      <span className="shrink-0">
                        {new Date(c.create_time).toLocaleString()}
                      </span>
                    </div>
                    <div className="mt-0.5 whitespace-pre-wrap text-sm text-[#333]">
                      {c.content}
                    </div>
                  </div>
                ))}
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
function ConversationView({
  chats,
  live,
  extraAction,
  onConfirmSubmitted,
  onLivePreviewOpenChange,
}: {
  chats: FlowAiChatItem[];
  live: FlowLiveChat | null;
  /** 成稿条目附加动作（存为流程版本按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
  /** 确认卡片提交成功后回调：回写流式态（FlowAiPanel 经 onConfirmSubmittedReady 上报的函数） */
  onConfirmSubmitted?: () => void;
  /** 范本预览抽屉开/关上报（供外层收缩布局腾位） */
  onLivePreviewOpenChange?: (open: boolean) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  // 流式回复增长时自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [live?.response]);

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
            </div>
          </div>
          <div className="flex justify-start">
            <div className="max-w-[90%] rounded-lg rounded-bl-sm border border-[#ECECEC] bg-white px-3 py-1.5 text-sm leading-relaxed text-[#333]">
              <ChapteredMarkdown
                content={normalizeLlmMarkdown(c.response) || '（无回复内容）'}
                loading={false}
              />
            </div>
          </div>
          <div className="flex items-center gap-2 px-1 text-[10px] text-[#aaa]">
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
              </div>
            </div>
          )}
          {/* 仅剩成稿条（回复已自动入库、response 为空）时不渲染空回复气泡 */}
          {(live.response || live.busy) && (
            <div className="flex justify-start">
              <div className="max-w-[90%] rounded-lg rounded-bl-sm border border-[#ECECEC] bg-white px-3 py-1.5 text-sm leading-relaxed text-[#333]">
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
                onConfirmSubmitted={onConfirmSubmitted}
                onLivePreviewOpenChange={onLivePreviewOpenChange}
                extraAction={(dl) => extraAction?.(dl)}
              />
            </div>
          ) : null}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
