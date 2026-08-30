// web/src/pages/c-chat/flow/flow-ai-panel.tsx
// AI 处理面板：输入框交互复刻 C端对话页（IME 保护 + 圆形发送/停止按钮 + 审阅文档），
// 审阅复用 review-panel.tsx（inline 模式），标注来自智能体 structured output
// （structuredOutputRef），与 c-chat 的 reviewAnnotations 提取逻辑同源。
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import { downloadVersionBlob, saveFlowAiRecord } from '@/services/flow-service';
import api from '@/utils/api';
import { Bot, FileText, Send, Square } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReviewPanel, { type Annotation } from '../review-panel';
import type { FlowAiChatItem, FlowVersionItem } from './flow-types';

const NO_AGENT_HINT = '未配置对话智能体，请先在「对话」页签使用过智能体对话';

export default function FlowAiPanel({
  flowId,
  version,
  aiChats,
  onSaved,
}: {
  flowId: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  onSaved: () => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [attachFile, setAttachFile] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // 发送前置阶段（建会话/传附件）期间的锁，防止并发二次发送
  const [sending, setSending] = useState(false);
  // 审阅模式：ReviewPanel 展示当前版本文件段落 + 智能体返回的标注
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewFileId, setReviewFileId] = useState('');
  const [reviewFileName, setReviewFileName] = useState('');
  const [reviewPreparing, setReviewPreparing] = useState(false);
  // agent_id 与 c-chat 同源：localStorage（c-chat 发送时写入）
  const [agentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  const instructionRef = useRef('');
  const sessionIdRef = useRef('');
  // 中文输入法选词期间的回车不应触发发送（与 c-chat 一致）
  const composingRef = useRef(false);

  const {
    send,
    streamState,
    done,
    stopOutputMessage,
    resetAnswerList,
    answerList,
    structuredOutputRef,
  } = useSendMessageBySSE(api.agentChatCompletion, {
    excludeFanOutFromContent: false,
  });

  // 从流式事件中提取 session_id（多轮续聊依赖）
  useEffect(() => {
    const sid = answerList.find((e: any) => e?.session_id)?.session_id;
    if (sid) sessionIdRef.current = sid;
  }, [answerList]);

  // 卸载时中止进行中的 SSE 连接
  useEffect(() => {
    return () => stopOutputMessage();
  }, [stopOutputMessage]);

  // 审阅标注：从智能体 structured output 提取（与 c-chat Priority 1 同源）
  const annotations = useMemo<Annotation[]>(() => {
    const structured = structuredOutputRef.current as any;
    const anns = structured?.annotations;
    return Array.isArray(anns) && anns.length > 0 ? (anns as Annotation[]) : [];
    // ref 读取依赖 done/answerList 触发重算
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, answerList]);

  // 后端按 create_time 正序返回，取最后 3 条即最近记录
  const recentChats = aiChats.slice(-3);

  // 无会话时先建会话（与 c-chat 同款：POST /agents/{id}/sessions），
  // 否则后端走无状态 fresh run 路径，多轮对话没有上下文延续。
  const ensureSession = useCallback(
    async (query: string): Promise<boolean> => {
      if (sessionIdRef.current) return true;
      try {
        const userInfo = JSON.parse(
          localStorage.getItem('userInfo') || '{}',
        ) as { id?: string; user_id?: string; email?: string };
        const uid =
          userInfo?.id || userInfo?.user_id || userInfo?.email || 'current';
        const resp = await fetch(
          `/api/v1/agents/${agentId}/sessions?user_id=${encodeURIComponent(uid)}`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: localStorage.getItem('Authorization') || '',
            },
            body: JSON.stringify({ name: query.slice(0, 30) }),
          },
        );
        const result = await resp.json();
        if (result.code === 0 && result.data?.id) {
          sessionIdRef.current = result.data.id as string;
          return true;
        }
        setError(result.message || '创建会话失败');
        return false;
      } catch {
        setError('创建会话失败');
        return false;
      }
    },
    [agentId],
  );

  // 上传当前版本为 document（AI 附件与审阅面板共用），返回 {id, name}
  const uploadVersionAsDocument = useCallback(async (): Promise<{
    id: string;
    name: string;
  } | null> => {
    if (!version) return null;
    const blob = await downloadVersionBlob(flowId, version.id);
    const file = new File([blob], version.file_name, {
      type: version.file_type || 'application/octet-stream',
    });
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch('/api/v1/documents/upload', {
      method: 'POST',
      headers: {
        Authorization: localStorage.getItem('Authorization') || '',
      },
      body: fd,
    });
    const result = await resp.json();
    if (result.code === 0 && result.data) {
      const d = Array.isArray(result.data) ? result.data[0] : result.data;
      if (d?.id)
        return { id: d.id as string, name: d.name || version.file_name };
    }
    throw new Error(result.message || '文件上传失败');
  }, [flowId, version]);

  // 进入审阅模式：先把当前版本上传为 document，ReviewPanel 靠该 id 解析段落
  const enterReviewMode = useCallback(async () => {
    if (!version || reviewPreparing) return;
    setError('');
    setReviewPreparing(true);
    try {
      const doc = await uploadVersionAsDocument();
      if (doc) {
        setReviewFileId(doc.id);
        setReviewFileName(doc.name);
        setReviewMode(true);
      }
    } catch (e: any) {
      setError(e?.message || '审阅准备失败，请稍后重试');
    } finally {
      setReviewPreparing(false);
    }
  }, [reviewPreparing, uploadVersionAsDocument, version]);

  const busy = !done || sending;
  const responseText = streamState.content.trim();
  const hasContent = responseText.length > 0;

  const handleSend = useCallback(async () => {
    const query = instruction.trim();
    if (!query || busy || composingRef.current) return;
    if (!agentId) {
      setError(NO_AGENT_HINT);
      return;
    }
    setError('');
    setSending(true);
    try {
      // 保存记录时要用（发送后输入框即清空）
      instructionRef.current = query;
      setInstruction('');

      const ok = await ensureSession(query);
      if (!ok) {
        setInstruction(query);
        return;
      }

      // 附带当前版本文件：下载 blob → File → /documents/upload → 文档对象数组。
      // 审阅模式下强制附带（标注必须针对该文件）。
      let files: unknown[] = [];
      if ((attachFile || reviewMode) && version) {
        try {
          const doc = await uploadVersionAsDocument();
          if (doc) {
            files = [{ id: doc.id, name: doc.name }];
            if (reviewMode) {
              setReviewFileId(doc.id);
              setReviewFileName(doc.name);
            }
          }
        } catch {
          // 附件上传失败不阻断发送，降级为无文件提问
          files = [];
        }
      }

      let res: any = null;
      try {
        res = await send({
          agent_id: agentId,
          query,
          session_id: sessionIdRef.current,
          stream: true,
          files,
          internet: false,
        });
      } catch (e: any) {
        setError(e?.message || '发送失败，请检查网络后重试');
        return;
      }

      if (
        res &&
        (res.response.status !== 200 || (res.data as any)?.code !== 0)
      ) {
        setError(
          (res.data as any)?.message ||
            `请求失败（HTTP ${res.response.status}）`,
        );
      }
    } finally {
      setSending(false);
    }
  }, [
    agentId,
    attachFile,
    busy,
    ensureSession,
    flowId,
    instruction,
    reviewMode,
    send,
    uploadVersionAsDocument,
    version,
  ]);

  const handleSave = useCallback(
    async (asVersion: boolean) => {
      if (!hasContent || saving) return;
      setSaving(true);
      setError('');
      try {
        await saveFlowAiRecord(flowId, {
          instruction: instructionRef.current || '(见记录)',
          response: responseText,
          version_id: version?.id,
          session_id: sessionIdRef.current,
          save_as_version: asVersion,
        });
        resetAnswerList();
        onSaved();
      } catch (e: any) {
        setError(e?.message || '保存失败');
      } finally {
        setSaving(false);
      }
    },
    [
      flowId,
      hasContent,
      onSaved,
      resetAnswerList,
      responseText,
      saving,
      version?.id,
    ],
  );

  return (
    <div className="shrink-0 border-t border-[#F0F0F0] px-4 py-3">
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4 shrink-0 text-[#1668DC]" />
        <span className="shrink-0 text-sm font-medium">AI 处理</span>
        <span className="min-w-0 flex-1 truncate text-xs text-[#999]">
          （上下文：
          {version
            ? `v${version.version_no} ${version.file_name}`
            : '无上下文文件'}
          ）
        </span>
      </div>

      {/* 历史记录摘要（最近 3 条） */}
      {recentChats.length > 0 && (
        <div className="mt-2 max-h-20 space-y-1 overflow-y-auto rounded bg-[#FAFAFA] px-2 py-1.5">
          {recentChats.map((c) => (
            <div key={c.id} className="truncate text-xs text-[#999]">
              指令：{c.instruction} →{' '}
              {c.output_version_id ? '已存为新版本' : '未存版本'}
            </div>
          ))}
        </div>
      )}

      {!agentId && (
        <div className="mt-2 text-xs text-[#FAAD14]">{NO_AGENT_HINT}</div>
      )}
      {error && <div className="mt-2 text-xs text-red-500">{error}</div>}

      {/* 审阅面板（inline，复用 c-chat review-panel） */}
      {reviewMode && reviewFileId && (
        <div className="mt-2 h-[420px] overflow-hidden rounded-xl border border-[#E8E8E8]">
          <ReviewPanel
            open
            inline
            onClose={() => setReviewMode(false)}
            fileId={reviewFileId}
            fileName={reviewFileName}
            annotations={annotations}
          />
        </div>
      )}

      {/* 流式输出区 */}
      {(busy || hasContent) && (
        <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-[#F7F8FA] px-2 py-1.5 text-xs leading-relaxed text-[#333]">
          {streamState.content}
          {busy && <span className="animate-pulse">▌</span>}
        </pre>
      )}

      {/* 输入框（复刻 c-chat：白底圆角卡片 + 无边框 Textarea + 底部按钮行） */}
      <div
        className="mt-2 flex flex-col gap-2 rounded-2xl border border-[#D4D4D4] bg-[#FFFFFF] px-4 py-3 transition-colors focus-within:border-[#9CA3AF]"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      >
        <Textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={(e) => {
            composingRef.current = false;
            setInstruction((e.target as HTMLTextAreaElement).value);
          }}
          placeholder="输入 AI 处理指令，例如：审阅本文档并标注风险条款"
          rows={1}
          disabled={busy}
          className="min-h-[24px] w-full resize-none overflow-auto border-0 bg-transparent p-0 shadow-none outline-none ring-0 ring-offset-transparent focus-visible:ring-0 focus-visible:ring-offset-0"
          autoSize={{ minRows: 1, maxRows: 6 }}
        />
        <div className="flex items-center justify-end gap-2">
          {/* 附带当前版本文件 */}
          <label
            className={`flex shrink-0 cursor-pointer items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-all ${
              attachFile
                ? 'border-[#BFD3F5] bg-[#F0F5FF] text-[#1a66fb]'
                : 'border-[#E8E8E8] bg-white text-[#8A8A8A] hover:border-[#D4D4D4] hover:text-[#525252]'
            } ${!version ? 'pointer-events-none opacity-40' : ''}`}
            title="发送时自动附带当前版本文件作为 AI 上下文"
          >
            <input
              type="checkbox"
              className="hidden"
              checked={attachFile}
              disabled={!version}
              onChange={(e) => setAttachFile(e.target.checked)}
            />
            附带版本文件
          </label>
          {/* 审阅文档（复刻 c-chat 文件审核按钮） */}
          {version && (
            <button
              onClick={() => {
                if (reviewMode) {
                  setReviewMode(false);
                } else {
                  enterReviewMode();
                }
              }}
              disabled={reviewPreparing}
              className={`flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-50 ${
                reviewMode
                  ? 'border-[#3F5B8D] bg-[#F0F3FA] text-[#3F5B8D]'
                  : 'border-[#E8E8E8] bg-white text-[#8A8A8A] hover:border-[#D4D4D4] hover:text-[#525252]'
              }`}
            >
              <FileText className="h-3.5 w-3.5" strokeWidth={2} />
              {reviewPreparing
                ? '准备中…'
                : reviewMode
                  ? '收起审阅'
                  : '审阅文档'}
            </button>
          )}
          {/* 发送 / 停止（圆形按钮，同 c-chat） */}
          {busy ? (
            <button
              onMouseDown={(e) => {
                e.preventDefault();
                stopOutputMessage();
              }}
              className="flex size-9 shrink-0 items-center justify-center rounded-full border-2 border-[#1A1A1A] bg-white text-[#1A1A1A] transition-colors hover:bg-[#F5F5F4]"
            >
              <Square className="h-3.5 w-3.5 fill-current" strokeWidth={2} />
            </button>
          ) : (
            <button
              onMouseDown={(e) => {
                e.preventDefault();
                handleSend();
              }}
              disabled={!instruction.trim() || !agentId}
              className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[#1A1A1A] text-white transition-colors hover:bg-[#333333] disabled:cursor-not-allowed disabled:opacity-30"
            >
              <Send className="h-4 w-4" strokeWidth={2} />
            </button>
          )}
        </div>
      </div>

      {/* 保存动作（AI 回复完成后） */}
      {done && hasContent && (
        <div className="mt-2 flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={saving}
            onClick={() => handleSave(false)}
          >
            仅存记录
          </Button>
          <Button size="sm" disabled={saving} onClick={() => handleSave(true)}>
            存为新版本
          </Button>
        </div>
      )}
    </div>
  );
}
