// web/src/pages/c-chat/flow/flow-detail.tsx
import { Button } from '@/components/ui/button';
import {
  archiveFlow,
  cancelFlow,
  deleteFlowVersion,
  downloadVersionBlob,
  getFlowDetail,
  listCandidates,
  submitFlow,
  uploadFlowVersion,
} from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChevronDown,
  Clock,
  Download,
  FileText,
  MessageSquare,
  Trash2,
  User,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FlowAiPanel from './flow-ai-panel';
import type { FlowAiChatItem, FlowVersionItem } from './flow-types';

const STATUS_TEXT: Record<string, string> = {
  initiator: '发起人处理中',
  leader: '领导审批中',
  handler: '处理人处理中',
  summary: '汇总审核中',
  archived: '已归档',
  cancelled: '已作废',
};

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

export default function FlowDetail({
  flowId,
  commentPortal,
  onChanged,
}: {
  flowId: string;
  /** 批注模块 portal 挂载点（外层左侧流程栏下方），不传则不渲染批注模块 */
  commentPortal?: HTMLElement | null;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const uploadInputRef = useRef<HTMLInputElement>(null);

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

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[#999]">
        加载中…
      </div>
    );
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
    <div className="flex h-full min-w-0 flex-col text-[#222]">
      {/* 状态条 */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[#F0F0F0] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="h-4 w-4 shrink-0 text-[#1a66fb]" />
          <span className="truncate text-base font-semibold">{flow.title}</span>
          <span className="ml-1 shrink-0 rounded-md bg-[#EFF4FF] px-2 py-0.5 text-xs text-[#1a66fb]">
            {STATUS_TEXT[flow.status] ?? flow.status}
          </span>
          {!terminal && holderId && (
            <span className="flex shrink-0 items-center gap-1 text-xs text-[#888]">
              <User className="h-3 w-3" />
              当前负责人：{holderName}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
            <ConversationView chats={data.ai_chats ?? []} />
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
            />
          )}

          {/* 批注区已移至外层左侧流程栏下方（commentPortal） */}
        </div>

        {/* 右：版本时间线 */}
        <div className="flex w-64 shrink-0 flex-col rounded-lg border border-[#F0F0F0] bg-white">
          <div className="border-b border-[#F0F0F0] px-3 py-2 text-sm font-medium">
            版本记录
          </div>
          <div className="flex-1 overflow-y-auto">
            {visibleVersions.map((v) => {
              const active = selectedVersion?.id === v.id;
              const cnt = (data.comments ?? []).filter(
                (c) => c.version_id === v.id,
              ).length;
              return (
                <div
                  key={v.id}
                  onClick={() => setSelectedVersionId(v.id)}
                  className={`cursor-pointer border-b border-[#F7F7F7] px-3 py-2 hover:bg-[#F7FAFF] ${
                    active ? 'bg-[#EFF4FF]' : ''
                  }`}
                >
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
                  <div className="mt-1 text-xs text-[#888]">
                    {v.source === 'ai_output' ? 'AI 产出' : '人工上传'}
                  </div>
                  {/* 醒目操作区：下载 + 删除（删除仅领导可用，其余置灰） */}
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownload(v);
                      }}
                      className="flex flex-1 items-center justify-center gap-1 rounded-md border border-[#BFD3F5] bg-[#F0F5FF] px-2 py-1 text-xs font-medium text-[#1a66fb] transition-colors hover:bg-[#E1EBFF]"
                    >
                      <Download className="h-3 w-3" strokeWidth={2.5} />
                      下载
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
                      className={`flex flex-1 items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                        isLeader && !terminal && !busy
                          ? 'border border-[#FBC2C2] bg-[#FFF1F0] text-[#E5484D] hover:bg-[#FFE4E2]'
                          : 'cursor-not-allowed border border-[#ECECEC] bg-[#F7F7F7] text-[#BBB]'
                      }`}
                    >
                      <Trash2 className="h-3 w-3" strokeWidth={2.5} />
                      删除
                    </button>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1 text-xs font-semibold text-[#444]">
                    <Clock className="h-3 w-3 shrink-0 text-[#1a66fb]" />
                    {new Date(v.create_time).toLocaleString()}
                  </div>
                </div>
              );
            })}
            {sortedVersions.length > visibleCount && (
              <button
                type="button"
                onClick={() => setVisibleCount((c) => c + VERSION_PAGE_SIZE)}
                className="flex w-full items-center justify-center gap-1 py-2 text-xs font-medium text-[#1a66fb] transition-colors hover:bg-[#F7FAFF]"
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
    </div>
  );
}

/** AI 对话记录视图：指令（右）+ 回复（左），含存版本标记 */
function ConversationView({ chats }: { chats: FlowAiChatItem[] }) {
  if (!chats.length) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[#999]">
        暂无对话记录，可在下方「AI 处理」输入指令
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
            <div className="max-w-[90%] whitespace-pre-wrap rounded-lg rounded-bl-sm border border-[#ECECEC] bg-white px-3 py-1.5 text-xs leading-relaxed text-[#333]">
              {c.response || '（无回复内容）'}
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
    </div>
  );
}
