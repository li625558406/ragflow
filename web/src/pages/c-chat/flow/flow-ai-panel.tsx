// web/src/pages/c-chat/flow/flow-ai-panel.tsx
// AI 处理面板：输入框直接复用 c-chat 原样抽取的 ChatInputBox（文件上传/拖拽/
// 粘贴/IME/语音/文件审核按钮交互与对话页完全一致）。
// flow 特有逻辑：附带当前版本文件开关、审阅目标（用户上传文件优先，否则当前版本）、
// 标注提取（structuredOutputRef）与存记录/存新版本。
import { Button } from '@/components/ui/button';
import { useHandleMessageInputChange } from '@/hooks/logic-hooks';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import {
  addFlowComment,
  downloadVersionBlob,
  saveFlowAiRecord,
} from '@/services/flow-service';
import api from '@/utils/api';
import { Bot, FileText } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ChatInputBox, { type UploadedDoc } from '../chat-input-box';
import ReviewPanel, { type Annotation } from '../review-panel';
import type {
  FlowAiChatItem,
  FlowCommentItem,
  FlowVersionItem,
} from './flow-types';

const NO_AGENT_HINT = '未配置对话智能体，请先在「对话」页签使用过智能体对话';

// 打字机占位（与 c-chat 同款文案与节奏）
const FULL_PLACEHOLDER =
  '请在此描述您的标书分析需求，例如：提取招标文件中的关键资质要求、分析评分标准的权重分布、对比各投标企业的技术方案优劣、检查合同条款中的潜在风险点...';

export default function FlowAiPanel({
  flowId,
  version,
  aiChats,
  comments,
  commentAuthors,
  onSaved,
}: {
  flowId: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  comments: FlowCommentItem[];
  commentAuthors: Record<string, string>;
  onSaved: () => void;
}) {
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // 发送前置阶段（建会话/传附件）期间的锁，防止并发二次发送
  const [sending, setSending] = useState(false);
  // 审阅模式：ReviewPanel 展示文件段落 + 智能体返回的标注
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewFileId, setReviewFileId] = useState('');
  const [reviewFileName, setReviewFileName] = useState('');
  const [reviewPreparing, setReviewPreparing] = useState(false);
  // flow 特有：未手动上传文件时，发送自动附带当前版本文件
  const [attachFile, setAttachFile] = useState(true);
  // agent_id 与 c-chat 同源：localStorage（c-chat 发送时写入）
  const [agentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );

  const { handleInputChange, value, setValue } = useHandleMessageInputChange();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const instructionRef = useRef('');
  const sessionIdRef = useRef('');
  // ChatInputBox 内部上传完成的文档对象（发送时附带）
  const uploadedDocsRef = useRef<UploadedDoc[]>([]);
  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);

  // 打字机占位（hasMessages 恒 false，与 c-chat 空态一致）
  const [typewriterText, setTypewriterText] = useState('');
  const [typewriterIdx, setTypewriterIdx] = useState(0);
  const [typewriterForward, setTypewriterForward] = useState(true);
  useEffect(() => {
    const timer = setInterval(
      () => {
        if (typewriterForward) {
          if (typewriterIdx < FULL_PLACEHOLDER.length) {
            setTypewriterText(FULL_PLACEHOLDER.slice(0, typewriterIdx + 1));
            setTypewriterIdx((prev) => prev + 1);
          } else {
            setTypewriterForward(false);
          }
        } else {
          if (typewriterIdx > 0) {
            setTypewriterText(FULL_PLACEHOLDER.slice(0, typewriterIdx - 1));
            setTypewriterIdx((prev) => prev - 1);
          } else {
            setTypewriterForward(true);
          }
        }
      },
      typewriterForward ? 60 : 30,
    );
    return () => clearInterval(timer);
  }, [typewriterIdx, typewriterForward]);

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

  // 审阅标注：从智能体 structured output 提取（与 c-chat 同源）
  const annotations = useMemo<Annotation[]>(() => {
    const structured = structuredOutputRef.current as any;
    const anns = structured?.annotations;
    return Array.isArray(anns) && anns.length > 0 ? (anns as Annotation[]) : [];
    // ref 读取依赖 done/answerList 触发重算
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, answerList]);

  // 后端按 create_time 正序返回，取最后 3 条即最近记录
  const recentChats = aiChats.slice(-3);

  const busy = !done || sending;
  const responseText = streamState.content.trim();
  const hasContent = responseText.length > 0;

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

  // ChatInputBox 上传完成的文档对象同步到 ref（发送时读取，避免闭包过期）
  const handleUploadedDocsChange = useCallback((files: UploadedDoc[]) => {
    uploadedDocsRef.current = files;
    setUploadedDocs(files);
  }, []);

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

  // 发送：用户上传文件优先；否则按开关附带当前版本文件。
  // 发送语义与 c-chat handlePressEnter 一致（组合态从 DOM 取值、失败回填输入框）。
  const handleSend = useCallback(async () => {
    const query =
      (composingRef.current
        ? textareaRef.current?.value?.trim()
        : value.trim()) || value.trim();
    if (!query || busy) return;
    if (!agentId) {
      setError(NO_AGENT_HINT);
      return;
    }
    setError('');
    setSending(true);
    try {
      // 保存记录时要用（发送后输入框即清空）
      instructionRef.current = query;
      setValue('');

      const ok = await ensureSession(query);
      if (!ok) {
        setValue(query);
        return;
      }

      const docs = uploadedDocsRef.current;
      let files: unknown[] = docs;
      if (files.length === 0 && attachFile && version) {
        try {
          const doc = await uploadVersionAsDocument();
          if (doc) files = [doc];
        } catch {
          // 附件上传失败不阻断发送，降级为无文件提问
          files = [];
        }
      }
      // 审阅模式下对齐 ReviewPanel 的目标文件
      if (reviewMode) {
        const target = (docs[0] ?? (files[0] as UploadedDoc | undefined)) as
          | UploadedDoc
          | undefined;
        if (target?.id) {
          setReviewFileId(target.id);
          setReviewFileName(target.name || '');
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
        setValue(query);
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
        setValue(query);
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
    reviewMode,
    send,
    setValue,
    uploadVersionAsDocument,
    value,
    version,
  ]);

  // 进入审阅模式：用户上传的文件优先，否则把当前版本上传为 document
  const toggleReview = useCallback(async () => {
    if (reviewMode) {
      setReviewMode(false);
      return;
    }
    if (!reviewFileId && uploadedDocsRef.current[0]) {
      setReviewFileId(uploadedDocsRef.current[0].id);
      setReviewFileName(uploadedDocsRef.current[0].name || '');
    }
    if (!reviewFileId && !uploadedDocsRef.current[0]) {
      if (!version || reviewPreparing) return;
      setError('');
      setReviewPreparing(true);
      try {
        const doc = await uploadVersionAsDocument();
        if (doc) {
          setReviewFileId(doc.id);
          setReviewFileName(doc.name);
        }
      } catch (e: any) {
        setError(e?.message || '审阅准备失败，请稍后重试');
        return;
      } finally {
        setReviewPreparing(false);
      }
    }
    setReviewMode(true);
  }, [
    reviewFileId,
    reviewMode,
    reviewPreparing,
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

  // Word 式手动批注：选中审阅正文后写入 flow 评论（带锚点），经 onSaved 刷新回显
  const handleAddAnchoredComment = useCallback(
    async (p: {
      content: string;
      anchorText: string;
      anchorPara: number | null;
    }) => {
      await addFlowComment(flowId, p.content, version?.id, {
        anchorText: p.anchorText,
        anchorPara: p.anchorPara,
      });
      onSaved();
    },
    [flowId, onSaved, version?.id],
  );

  return (
    <div className="shrink-0 rounded-lg border border-[#F0F0F0] bg-white px-4 py-3">
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
        {/* flow 特有：未手动上传文件时发送自动附带当前版本 */}
        {version && (
          <button
            onClick={() => setAttachFile((prev) => !prev)}
            className={`shrink-0 rounded-md border px-2 py-0.5 text-xs transition-colors ${
              attachFile
                ? 'border-[#BFD3F5] bg-[#F0F5FF] text-[#1a66fb]'
                : 'border-[#E8E8E8] bg-white text-[#8A8A8A] hover:text-[#525252]'
            }`}
            title="未手动上传文件时，发送自动附带当前版本文件作为 AI 上下文"
          >
            附带版本文件{attachFile ? '开' : '关'}
          </button>
        )}
        {/* 文件审核：从输入框工具栏挪出的醒目入口 */}
        {(!!version || uploadedDocs.length > 0) && (
          <button
            onClick={toggleReview}
            className={`flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              reviewMode
                ? 'border border-[#BFD3F5] bg-[#F0F5FF] text-[#1a66fb]'
                : 'bg-[#1a66fb] text-white hover:bg-[#0f56e0]'
            }`}
          >
            <FileText className="h-3.5 w-3.5" strokeWidth={2} />
            {reviewMode ? '关闭审核' : '文件审核'}
          </button>
        )}
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

      {/* 审阅面板：右侧独立抽屉（Sheet），与 c-chat 同一组件；含 Word 式边栏批注 */}
      <ReviewPanel
        open={reviewMode}
        onClose={() => setReviewMode(false)}
        fileId={reviewFileId}
        fileName={reviewFileName}
        annotations={annotations}
        comments={comments}
        commentAuthors={commentAuthors}
        onAddComment={handleAddAnchoredComment}
      />

      {/* 流式输出区 */}
      {(busy || hasContent) && (
        <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-[#F7F8FA] px-2 py-1.5 text-xs leading-relaxed text-[#333]">
          {streamState.content}
          {busy && <span className="animate-pulse">▌</span>}
        </pre>
      )}

      {/* 输入框：c-chat 原样组件 */}
      <div className="mt-2">
        <ChatInputBox
          value={value}
          setValue={setValue}
          handleInputChange={handleInputChange}
          textareaRef={textareaRef}
          composingRef={composingRef}
          sendLoading={busy}
          onSend={handleSend}
          onStop={stopOutputMessage}
          hasMessages={false}
          typewriterText={typewriterText}
          reviewMode={reviewMode}
          onToggleReview={toggleReview}
          // 审核入口已挪到标题行醒目按钮，隐藏输入框工具栏内的入口
          reviewAvailable={false}
          onUploadedFilesChange={handleUploadedDocsChange}
          autoFocus
        />
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
