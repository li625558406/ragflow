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
  deleteFlowComment,
  downloadVersionBlob,
  editFlowDocument,
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
  FlowLiveChat,
  FlowVersionItem,
} from './flow-types';

const NO_AGENT_HINT = '未配置对话智能体，请先在「对话」页签使用过智能体对话';

// 打字机占位（与 c-chat 同款文案与节奏）
const FULL_PLACEHOLDER =
  '请在此描述您的标书分析需求，例如：提取招标文件中的关键资质要求、分析评分标准的权重分布、对比各投标企业的技术方案优劣、检查合同条款中的潜在风险点...';

export default function FlowAiPanel({
  flowId,
  flowTitle,
  version,
  aiChats,
  comments,
  commentAuthors,
  isOwner,
  onSaved,
  onLiveChatChange,
}: {
  flowId: string;
  /** 流程标题（agent 会话命名「流程：xxx」，便于在对话页签识别） */
  flowTitle: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  comments: FlowCommentItem[];
  commentAuthors: Record<string, string>;
  /** 当前用户是否为流程当前节点负责人（开放正文段落编辑） */
  isOwner?: boolean;
  onSaved: () => void;
  /** 进行中对话（指令+流式回复）变化时上报，供中部对话区实时展示 */
  onLiveChatChange?: (live: FlowLiveChat | null) => void;
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
  // 审核目标来源：version = 流程版本文件（可编辑段落）；upload = 用户手动上传（只读）
  const [reviewSource, setReviewSource] = useState<'version' | 'upload' | ''>(
    '',
  );
  // flow 特有：未手动上传文件时，发送自动附带当前版本文件
  const [attachFile, setAttachFile] = useState(true);
  // agent_id 与 c-chat 同源：localStorage（c-chat 发送时写入）
  const [agentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  // 当前登录用户 id（批注删除按钮仅对自己的批注显示）
  const [currentUserId] = useState(() => {
    try {
      const u = JSON.parse(localStorage.getItem('userInfo') || '{}') as {
        id?: string;
        user_id?: string;
        email?: string;
      };
      return u.id || u.user_id || u.email || '';
    } catch {
      return '';
    }
  });

  const { handleInputChange, value, setValue } = useHandleMessageInputChange();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const instructionRef = useRef('');
  // 会话续接：优先恢复本流程已保存记录里的 session_id（跨次进入保持多轮上下文）
  const sessionIdRef = useRef(
    (() => {
      for (let i = aiChats.length - 1; i >= 0; i--) {
        if (aiChats[i].session_id) return aiChats[i].session_id;
      }
      return '';
    })(),
  );
  // ChatInputBox 内部上传完成的文档对象（发送时附带）
  const uploadedDocsRef = useRef<UploadedDoc[]>([]);
  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);
  // 流式期间持续累积回复内容：send() 结束时 hook 会 resetAnswerList 清空
  // streamState，这里兜住完整回复供完成后展示/保存
  const contentRef = useRef('');
  const [completed, setCompleted] = useState<FlowLiveChat | null>(null);
  // 本轮是否已自动保存（每轮发送重置）
  const autoSavedRef = useRef(false);
  // 自动保存成功的记录（后续「存为新版本」基于它补建版本，不重复插记录）
  const [lastRecord, setLastRecord] = useState<{
    id: string;
    instruction: string;
    response: string;
    version_id: string;
  } | null>(null);

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

  // 对话状态上报（供中部对话区实时展示）：
  // - 发送中（sending || !done）：busy=true，response 取实时流式内容
  // - 结束后：send() 尾部 resetAnswerList 会清空 streamState，
  //   用 contentRef 兜住完整回复转成 completed 展示
  // - 保存/重新发送时清空 contentRef + completed，上报回落 null
  useEffect(() => {
    if (streamState.content) contentRef.current = streamState.content;
    if (sending || !done) {
      onLiveChatChange?.({
        instruction: instructionRef.current,
        response: streamState.content,
        busy: true,
      });
      return;
    }
    if (contentRef.current.trim()) {
      const next: FlowLiveChat = {
        instruction: instructionRef.current,
        response: contentRef.current,
        busy: false,
      };
      setCompleted((prev) => (prev ? prev : next));
      onLiveChatChange?.(completed ?? next);
    } else {
      onLiveChatChange?.(completed);
    }
  }, [streamState.content, done, sending, onLiveChatChange, completed]);

  // 自动保存：一轮对话流式结束后，自动将指令+回复写入流程记录（不建版本），
  // 无需手动点「仅存记录」；「存为新版本」随后可基于该记录补建版本（不重复插记录）。
  // 失败时保留 contentRef，手动「仅存记录」按钮兜底。
  useEffect(() => {
    if (!done || sending || autoSavedRef.current) return;
    const text = contentRef.current.trim();
    if (!text) return;
    autoSavedRef.current = true;
    (async () => {
      try {
        const res = (await saveFlowAiRecord(flowId, {
          instruction: instructionRef.current || '(见记录)',
          response: text,
          version_id: version?.id,
          session_id: sessionIdRef.current,
          save_as_version: false,
        })) as { record?: { id?: string } };
        if (res?.record?.id) {
          setLastRecord({
            id: res.record.id,
            instruction: instructionRef.current || '(见记录)',
            response: text,
            version_id: version?.id || '',
          });
        }
        // 已入正式记录：清空兜底内容与完成态，中部气泡回落到 ai_chats
        contentRef.current = '';
        instructionRef.current = '';
        setCompleted(null);
        resetAnswerList();
        onSaved();
      } catch (e: any) {
        setError(e?.message || '对话自动保存失败，可手动点击「仅存记录」');
      }
    })();
  }, [done, sending, flowId, version?.id, resetAnswerList, onSaved]);

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
  // 结束后 streamState 被 hook reset，从 contentRef 兜底取完整回复
  const responseText = (streamState.content || contentRef.current).trim();
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
            body: JSON.stringify({
              name: `流程：${flowTitle || query.slice(0, 30)}`.slice(0, 60),
            }),
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
    [agentId, flowTitle],
  );

  // ChatInputBox 上传完成的文档对象同步到 ref（发送时读取，避免闭包过期）
  const handleUploadedDocsChange = useCallback((files: UploadedDoc[]) => {
    uploadedDocsRef.current = files;
    setUploadedDocs(files);
  }, []);

  // 上传版本为 document（AI 附件与审阅面板共用）；不传参则用当前版本，
  // 编辑保存后传新版本以刷新预览。返回 {id, name}
  const uploadVersionAsDocument = useCallback(
    async (
      v?: FlowVersionItem | null,
    ): Promise<{
      id: string;
      name: string;
    } | null> => {
      const target = v ?? version;
      if (!target) return null;
      const blob = await downloadVersionBlob(flowId, target.id);
      const file = new File([blob], target.file_name, {
        type: target.file_type || 'application/octet-stream',
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
        // 返回完整上传响应对象（含 mime_type 等字段）：后端 canvas.get_files_async
        // 依赖 file["mime_type"]，只传 {id, name} 会在 SSE 输出前 KeyError 挂死
        if (d?.id) return d as { id: string; name: string };
      }
      throw new Error(result.message || '文件上传失败');
    },
    [flowId, version],
  );

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
      // 新一轮发送：清空上一轮兜底内容、完成态与已存记录
      contentRef.current = '';
      setCompleted(null);
      setLastRecord(null);
      autoSavedRef.current = false;
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
      // 审阅模式下对齐 ReviewPanel 的目标文件（并记录来源：手动上传只读，版本文件可编辑）
      if (reviewMode) {
        const target = (docs[0] ?? (files[0] as UploadedDoc | undefined)) as
          | UploadedDoc
          | undefined;
        if (target?.id) {
          setReviewFileId(target.id);
          setReviewFileName(target.name || '');
          setReviewSource(docs.length > 0 ? 'upload' : 'version');
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
      setReviewSource('upload');
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
          setReviewSource('version');
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
      if (saving) return;
      setSaving(true);
      setError('');
      try {
        if (asVersion && lastRecord) {
          // 回复已自动存为记录：基于记录补建版本，不重复插记录
          await saveFlowAiRecord(flowId, {
            instruction: lastRecord.instruction,
            response: lastRecord.response,
            record_id: lastRecord.id,
            version_id: lastRecord.version_id || version?.id,
            session_id: sessionIdRef.current,
            save_as_version: true,
          });
          setLastRecord(null);
          onSaved();
          return;
        }
        if (!hasContent) return;
        const res = (await saveFlowAiRecord(flowId, {
          instruction: instructionRef.current || '(见记录)',
          response: responseText,
          version_id: version?.id,
          session_id: sessionIdRef.current,
          save_as_version: asVersion,
        })) as { record?: { id?: string } };
        if (!asVersion && res?.record?.id) {
          // 仅存记录：记住记录 id，后续「存为新版本」基于它补建版本
          setLastRecord({
            id: res.record.id,
            instruction: instructionRef.current || '(见记录)',
            response: responseText,
            version_id: version?.id || '',
          });
        }
        if (asVersion) setLastRecord(null);
        // 已存入正式记录：清空兜底内容与完成态，中部气泡回落到 ai_chats
        contentRef.current = '';
        instructionRef.current = '';
        setCompleted(null);
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
      lastRecord,
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
      anchorStart?: number | null;
    }) => {
      await addFlowComment(flowId, p.content, version?.id, {
        anchorText: p.anchorText,
        anchorPara: p.anchorPara,
        anchorStart: p.anchorStart ?? null,
      });
      onSaved();
    },
    [flowId, onSaved, version?.id],
  );

  // 删除自己的手动批注，经 onSaved 刷新回显
  const handleDeleteComment = useCallback(
    async (commentId: string) => {
      await deleteFlowComment(flowId, commentId);
      onSaved();
    },
    [flowId, onSaved],
  );

  // Word 式正文编辑：后端按 para_index 同步增删改段落并存新版本（source=manual_edit，
  // .doc 先转 docx），刷新流程详情后把新版本重新上传为 document，预览即切到新内容
  const handleEditDocument = useCallback(
    async (ops: {
      edits: Array<{ paraIndex: number; newText: string }>;
      deletes: number[];
      inserts: Array<{ afterParaIndex: number; newText: string }>;
    }) => {
      if (!version) throw new Error('无版本文件，无法编辑');
      const res = await editFlowDocument(flowId, version.id, ops);
      onSaved();
      const doc = await uploadVersionAsDocument(res.version);
      if (doc) {
        setReviewFileId(doc.id);
        setReviewFileName(doc.name);
        setReviewSource('version');
      }
    },
    [flowId, onSaved, uploadVersionAsDocument, version],
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
        onDeleteComment={handleDeleteComment}
        currentUserId={currentUserId}
        canEdit={!!isOwner && reviewSource === 'version' && !!version}
        onEditDocument={handleEditDocument}
      />

      {/* 流式回复已实时展示在中部对话区（经 onLiveChatChange 上报），此处不再重复渲染 */}

      {/* 输入框：c-chat 原样组件（flow 场景固定约三行高度） */}
      <div className="mt-2 [&_textarea]:min-h-[68px]">
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
          accept=".doc,.docx"
          autoFocus
        />
      </div>

      {/* 保存动作：回复完成后自动已存记录，这里仅保留「存为新版本」；
          hasContent 为 true 说明自动保存失败，补显手动「仅存记录」兜底 */}
      {done && (hasContent || lastRecord) && (
        <div className="mt-2 flex justify-end gap-2">
          {hasContent && (
            <Button
              size="sm"
              variant="outline"
              disabled={saving}
              onClick={() => handleSave(false)}
            >
              仅存记录
            </Button>
          )}
          <Button size="sm" disabled={saving} onClick={() => handleSave(true)}>
            存为新版本
          </Button>
        </div>
      )}
    </div>
  );
}
