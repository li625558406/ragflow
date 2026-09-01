// web/src/pages/c-chat/flow/flow-detail.tsx
import ChapteredMarkdown from '@/components/chaptered-markdown';
import { Button } from '@/components/ui/button';
import {
  archiveFlow,
  cancelFlow,
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
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReviewPanel from '../review-panel';
import FlowAiPanel from './flow-ai-panel';
import type {
  FlowAiChatItem,
  FlowLiveChat,
  FlowVersionItem,
} from './flow-types';
import {
  FLOW_STEPS,
  relTime,
  STATUS_BADGE,
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
}: {
  flowId: string;
  /** 批注模块 portal 挂载点（外层左侧流程栏下方），不传则不渲染批注模块 */
  commentPortal?: HTMLElement | null;
  /** 当前版本批注数变化时上报（供外层折叠开关展示角标） */
  onCommentsCount?: (count: number) => void;
  onChanged: () => void;
  /** 流程被删除后回调（外层清空选中态并刷新列表） */
  onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  // 进行中的一轮 AI 对话（发送后未保存前的流式状态）
  const [liveChat, setLiveChat] = useState<FlowLiveChat | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  // 版本文件只读查看（所有参与人可用）：版本转 document 后交给 ReviewPanel
  const [viewOpen, setViewOpen] = useState(false);
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

  const handleCancel = () => {
    if (!window.confirm('确定作废该流程？作废后不可恢复。')) return;
    doAction(() => cancelFlow(flowId));
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
            </span>
            {!terminal && holderId && (
              <span className="hidden shrink-0 items-center gap-1 rounded-full bg-[#F7F8FA] px-2 py-0.5 text-xs text-[#888] lg:flex">
                <User className="h-3 w-3" />
                当前负责人：{holderName}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
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
            {isInitiator && !terminal && (
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={handleCancel}
              >
                作废
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
            <ConversationView chats={data.ai_chats ?? []} live={liveChat} />
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
            />
          )}

          {/* 批注区已移至外层左侧流程栏下方（commentPortal） */}
        </div>

        {/* 右：版本时间线 */}
        <div className="flex w-64 shrink-0 flex-col rounded-lg border border-[#F0F0F0] bg-white">
          <div className="border-b border-[#F0F0F0] px-3 py-2 text-sm font-medium">
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
                          : v.source === 'ai_output'
                            ? 'border-[#1a66fb]'
                            : 'border-[#CCC]'
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
                          v.source === 'ai_output'
                            ? 'bg-[#EFF4FF] text-[#1a66fb]'
                            : 'bg-[#F2F3F5] text-[#888]'
                        }`}
                      >
                        {v.source === 'ai_output' ? 'AI 产出' : '人工上传'}
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
              <div className="mb-2 flex shrink-0 items-center gap-1 text-sm font-medium">
                <MessageSquare className="h-4 w-4 text-[#1a66fb]" />
                批注（v{selectedVersion?.version_no ?? '-'}）
              </div>
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
 * 已完成节点实心蓝 + 对勾（入场缩放弹出），当前节点描边 + 呼吸光圈，
 * 连线随进度填充；已作废流程全部节点置灰。
 */
function FlowStepper({ status }: { status: string }) {
  const currentIdx = statusStepIndex(status);
  return (
    <div className="flex items-center">
      {FLOW_STEPS.map((s, i) => {
        const done = i < currentIdx;
        const current = i === currentIdx;
        return (
          <Fragment key={s.key}>
            {i > 0 && (
              <span
                aria-hidden
                className="relative mx-1.5 h-0.5 w-7 shrink-0 overflow-hidden rounded bg-[#E8E8E8]"
              >
                <span
                  className={`absolute inset-y-0 left-0 rounded bg-[#1a66fb] transition-[width] duration-500 ease-out ${
                    i <= currentIdx ? 'w-full' : 'w-0'
                  }`}
                />
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-[18px] w-[18px] shrink-0 items-center justify-center">
                {current && (
                  <span
                    aria-hidden
                    className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#1a66fb] opacity-20 motion-reduce:animate-none"
                  />
                )}
                <span
                  className={`relative flex h-[18px] w-[18px] items-center justify-center rounded-full border-2 transition-colors duration-200 ${
                    done
                      ? 'border-[#1a66fb] bg-[#1a66fb]'
                      : current
                        ? 'border-[#1a66fb] bg-white'
                        : 'border-[#D8D8D8] bg-white'
                  }`}
                >
                  {done && (
                    <Check
                      className="h-2.5 w-2.5 text-white motion-reduce:animate-none animate-in zoom-in-50 duration-200"
                      strokeWidth={3.5}
                    />
                  )}
                  {current && (
                    <span className="h-1.5 w-1.5 rounded-full bg-[#1a66fb]" />
                  )}
                </span>
              </span>
              <span
                className={`whitespace-nowrap text-xs ${
                  done || current ? 'font-medium text-[#333]' : 'text-[#AAA]'
                }`}
              >
                {s.label}
              </span>
            </div>
          </Fragment>
        );
      })}
      {status === 'cancelled' && (
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
}: {
  chats: FlowAiChatItem[];
  live: FlowLiveChat | null;
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
            <div className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-[#EFF4FF] px-3 py-1.5 text-xs leading-relaxed text-[#1a3a6b]">
              {c.instruction}
            </div>
          </div>
          <div className="flex justify-start">
            <div className="max-w-[90%] rounded-lg rounded-bl-sm border border-[#ECECEC] bg-white px-3 py-1.5 text-xs leading-relaxed text-[#333]">
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
              <div className="max-w-[80%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-[#EFF4FF] px-3 py-1.5 text-xs leading-relaxed text-[#1a3a6b]">
                {live.instruction}
              </div>
            </div>
          )}
          <div className="flex justify-start">
            <div className="max-w-[90%] rounded-lg rounded-bl-sm border border-[#ECECEC] bg-white px-3 py-1.5 text-xs leading-relaxed text-[#333]">
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
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
