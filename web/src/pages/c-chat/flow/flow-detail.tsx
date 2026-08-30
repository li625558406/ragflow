// web/src/pages/c-chat/flow/flow-detail.tsx
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  addFlowComment,
  archiveFlow,
  cancelFlow,
  downloadVersionBlob,
  getFlowDetail,
  listCandidates,
  submitFlow,
  uploadFlowVersion,
} from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FileText, MessageSquare, User } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import FlowAiPanel from './flow-ai-panel';
import type { FlowVersionItem } from './flow-types';

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

function canPreview(
  fileType: string,
  fileName: string,
): 'pdf' | 'image' | 'text' | null {
  const ft = (fileType || '').toLowerCase();
  const fn = (fileName || '').toLowerCase();
  if (ft.includes('pdf') || fn.endsWith('.pdf')) return 'pdf';
  if (ft.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg)$/.test(fn)) {
    return 'image';
  }
  if (
    ft.startsWith('text/') ||
    ft.includes('json') ||
    ft.includes('markdown') ||
    /\.(md|txt|json|csv|log)$/.test(fn)
  ) {
    return 'text';
  }
  return null;
}

export default function FlowDetail({
  flowId,
  onChanged,
}: {
  flowId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  );
  const [commentText, setCommentText] = useState('');
  const [busy, setBusy] = useState(false);
  const [previewText, setPreviewText] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
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

  const commentsOf = useMemo(() => {
    if (!data || !selectedVersion) return [];
    return (data.comments ?? []).filter(
      (c) => c.version_id === selectedVersion.id,
    );
  }, [data, selectedVersion]);

  // 预览资源：选中版本 id 变化时加载（带 cancelled 防竞态；url 需 revoke）
  // 注意：鉴权走 Authorization header，浏览器原生 src 请求会 401，因此
  // pdf/image 也必须 fetch Blob 后用 objectURL 展示，不能用后端 URL 直连。
  const selectedId = selectedVersion?.id ?? null;
  const versionsRef = useRef<FlowVersionItem[]>([]);
  versionsRef.current = data?.versions ?? [];

  useEffect(() => {
    if (!selectedId) return;
    const v = versionsRef.current.find((x) => x.id === selectedId);
    if (!v) return;
    const kind = canPreview(v.file_type, v.file_name);
    if (kind === 'text') {
      setPreviewUrl('');
      let cancelled = false;
      setPreviewText('');
      downloadVersionBlob(flowId, selectedId)
        .then((b) => b.text())
        .then((txt) => {
          if (!cancelled) setPreviewText(txt);
        })
        .catch(() => {
          if (!cancelled) setPreviewText('（文本内容加载失败）');
        });
      return () => {
        cancelled = true;
      };
    }
    if (kind === 'pdf' || kind === 'image') {
      setPreviewText('');
      let url = '';
      let cancelled = false;
      downloadVersionBlob(flowId, selectedId)
        .then((b) => {
          if (cancelled) return;
          url = URL.createObjectURL(b);
          setPreviewUrl(url);
        })
        .catch(() => {
          if (!cancelled) setPreviewUrl('');
        });
      return () => {
        cancelled = true;
        if (url) URL.revokeObjectURL(url);
      };
    }
    setPreviewText('');
    setPreviewUrl('');
  }, [flowId, selectedId]);

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

  const handleAddComment = () => {
    const content = commentText.trim();
    if (!content || !selectedVersion) return;
    doAction(async () => {
      await addFlowComment(flowId, content, selectedVersion.id);
      setCommentText('');
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
              variant="outline"
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
                className="hidden"
                onChange={(e) => {
                  handleUploadFile(e.target.files?.[0] ?? null);
                  e.target.value = '';
                }}
              />
              <Button
                size="sm"
                variant="outline"
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
          <div className="min-h-0 flex-1 overflow-auto rounded-lg bg-[#FAFAFA] p-3">
            {selectedVersion ? (
              <PreviewArea
                version={selectedVersion}
                text={previewText}
                url={previewUrl}
                onDownload={() => handleDownload(selectedVersion)}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-[#999]">
                暂无版本文件
              </div>
            )}
          </div>

          {isOwner && !terminal && (
            <FlowAiPanel
              flowId={flowId}
              version={selectedVersion}
              aiChats={data.ai_chats ?? []}
              comments={commentsOf}
              commentAuthors={Object.fromEntries(nicknameMap)}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
                onChanged();
              }}
            />
          )}

          {/* 批注区 */}
          <div className="shrink-0 rounded-lg border border-[#F0F0F0] bg-white p-3">
            <div className="mb-2 flex items-center gap-1 text-sm font-medium">
              <MessageSquare className="h-4 w-4 text-[#1a66fb]" />
              批注（v{selectedVersion?.version_no ?? '-'}）
            </div>
            <div className="max-h-36 space-y-2 overflow-y-auto">
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
            {!terminal && selectedVersion && (
              <div className="mt-2 flex items-end gap-2">
                <Textarea
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  placeholder="填写批注意见（挂到当前版本）…"
                  className="min-h-[60px] flex-1 text-sm"
                />
                <Button
                  size="sm"
                  disabled={busy || !commentText.trim()}
                  onClick={handleAddComment}
                >
                  发表批注
                </Button>
              </div>
            )}
          </div>
        </div>

        {/* 右：版本时间线 */}
        <div className="flex w-64 shrink-0 flex-col rounded-lg border border-[#F0F0F0] bg-white">
          <div className="border-b border-[#F0F0F0] px-3 py-2 text-sm font-medium">
            版本记录
          </div>
          <div className="flex-1 overflow-y-auto">
            {(data.versions ?? []).map((v) => {
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
                  <div className="mt-1 flex items-center justify-between text-xs text-[#888]">
                    <span>
                      {v.source === 'ai_output' ? 'AI 产出' : '人工上传'}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownload(v);
                      }}
                      className="flex items-center gap-0.5 hover:text-[#1a66fb]"
                    >
                      <Download className="h-3 w-3" />
                      下载
                    </button>
                  </div>
                  <div className="mt-0.5 text-[10px] text-[#aaa]">
                    {new Date(v.create_time).toLocaleString()}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function PreviewArea({
  version,
  text,
  url,
  onDownload,
}: {
  version: FlowVersionItem;
  text: string;
  url: string;
  onDownload: () => void;
}) {
  const kind = canPreview(version.file_type, version.file_name);
  if (kind === 'pdf') {
    return url ? (
      <iframe
        src={url}
        className="h-full min-h-[420px] w-full rounded-lg border border-[#EEE]"
        title={version.file_name}
      />
    ) : (
      <div className="flex h-full items-center justify-center text-sm text-[#999]">
        预览加载中…
      </div>
    );
  }
  if (kind === 'image') {
    return (
      <div className="flex h-full items-center justify-center">
        {url && (
          <img
            src={url}
            alt={version.file_name}
            className="max-h-full max-w-full rounded-lg"
          />
        )}
      </div>
    );
  }
  if (kind === 'text') {
    return (
      <pre className="whitespace-pre-wrap rounded-lg bg-white p-4 text-xs leading-5 text-[#333]">
        {text || '加载中…'}
      </pre>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-[#999]">
      <span>该格式不支持在线预览（{version.file_name}）</span>
      <Button size="sm" variant="outline" onClick={onDownload}>
        下载查看
      </Button>
    </div>
  );
}
