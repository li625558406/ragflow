// web/src/pages/c-chat/flow/flow-ai-panel.tsx
// AI 处理面板：输入框直接复用 c-chat 原样抽取的 ChatInputBox（文件上传/拖拽/
// 粘贴/IME/语音/文件审核按钮交互与对话页完全一致）。
// flow 特有逻辑：附带当前版本文件开关、审阅目标（用户上传文件优先，否则当前版本）、
// 标注提取（structuredOutputRef）与存记录/存新版本。
import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import { useHandleMessageInputChange } from '@/hooks/logic-hooks';
import type { ITemplateFillState } from '@/hooks/template-fill-stream';
import {
  parseTemplateFillEvents,
  replayTemplateFillEvents,
} from '@/hooks/template-fill-stream';
import { useDeleteFileReviewAnnotation } from '@/hooks/use-file-review-request';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import { useTemplateFillRunRecovery } from '@/hooks/use-template-fill-run-recovery';
import type { FlowDocRun } from '@/services/flow-service';
import {
  addFlowComment,
  createFlowChatSession,
  deleteFlowComment,
  downloadVersionBlob,
  editFileDocument,
  editFlowDocument,
  getFlowVersionContent,
  saveFlowAiRecord,
} from '@/services/flow-service';
import api from '@/utils/api';
import { getAuthorization } from '@/utils/authorization-util';
import request from '@/utils/request';
import { FileText } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ChatInputBox, { type UploadedDoc } from '../chat-input-box';
import { extractFileReviewTarget } from '../file-review-progress';
import { collectRecentFillDownloads } from '../recent-downloads';
import ReviewPanel, { type Annotation } from '../review-panel';
import type {
  FlowAiChatItem,
  FlowCommentItem,
  FlowLiveChat,
  FlowVersionItem,
} from './flow-types';

// flow 专属智能体在 flow-panel 顶栏统一选择（只列名称带「流程」的 agent），
// 经 props 下发到这里；此处只负责使用与切换时的会话重建
const NO_AGENT_HINT =
  '未找到名称带「流程」的智能体，请先在 B 端创建或重命名（名称需含「流程」二字）';

// 影子会话失效类错误：会话行被外部清理（后端路由层校验 "Session not found!"）
// 或会话与当前智能体绑定校验不过（"Session does not belong to the requested agent."）。
// 这类失败是永久性的——重试同一 session_id 必然再失败，必须清空引用让下一次
// 发送经 ensureSession 自动重建新会话（权威数据在 flow_ai_chat，会话行可再生）。
const isSessionMissingError = (msg: string) =>
  /session not found/i.test(msg) || /does not belong/i.test(msg);

// 文件审核入口控制：状态在面板内部，经回调上报给父级（flow-detail 顶部按钮行）渲染按钮
export type FlowReviewControl = {
  visible: boolean;
  active: boolean;
  toggle: () => void;
  /** T15：FileReviewProgress 点击「打开审核面板」时调用 —— 同步切换到指定 fileId
   *  并打开 review 抽屉。无 fileId 时退回 toggle 行为（用当前 reviewFileId）。
   *  实现位于面板内部，导出接口供中部对话区 <FileReviewProgress /> 复用。 */
  openWithFile?: (
    fileId: string,
    fileName?: string,
    /** 文件审核批注（进度卡 state.annotations，IFileReviewAnnotation 与
     *  ReviewPanel.Annotation 字段兼容直接透传）；不传则面板沿用原标注来源 */
    annotations?: Annotation[],
  ) => void;
};

// flow_ai_chat.file_review 字段解析（与 flow-detail.parseFileReview 同构）：
// "{fileId,taskId}" JSON 字符串 → {fileId, taskId}；空串/畸形静默返 null
const parseRecordFileReview = (
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

// 打字机占位（与 c-chat 同款文案与节奏）
const FULL_PLACEHOLDER =
  '请在此描述您的标书分析需求，例如：提取招标文件中的关键资质要求、分析评分标准的权重分布、对比各投标企业的技术方案优劣、检查合同条款中的潜在风险点...';

/** template_fill_events 落库为 JSON 字符串；为空/畸形时静默返回 undefined（回放是尽力而为）。
 *  截断损坏（旧 TEXT 64KB 落库上限）由 parseTemplateFillEvents 挽救：丢尾部残缺
 *  事件、保住前面的进度/产值/成稿事件。 */
function parseAndReplay(raw: unknown) {
  const events = parseTemplateFillEvents(raw);
  if (!events) return undefined;
  try {
    const restored = replayTemplateFillEvents(events);
    // 挂起卡（字段确认/范本选择）liveness 的「旧数据退化路径」：仅当挂起事件是
    // 最后一条时保留交互态。新数据的权威判定在运行快照恢复 hook（useTemplateFillRunRecovery，
    // 设计 2026-09-16）——有 canvas run id 时由快照整体覆盖本重放态；本启发式只为
    // 无 run id 的历史记录兜底（行为与快照方案上线前一致），不再承担新数据判定。
    if (restored && events.length) {
      const lastStage = (events[events.length - 1] as { stage?: string })
        ?.stage;
      if (lastStage !== 'select_pending') delete restored.pendingSelect;
      if (lastStage !== 'confirm_pending') delete restored.pendingConfirm;
    }
    return restored;
  } catch {
    return undefined;
  }
}

export default function FlowAiPanel({
  flowId,
  agentId = '',
  version,
  aiChats,
  comments,
  commentAuthors,
  isOwner,
  onSaved,
  onLiveChatChange,
  onReviewControlChange,
  onReviewOpenChange,
  onConfirmSubmittedReady,
  onSelectSubmittedReady,
}: {
  flowId: string;
  /** 流程专属智能体 id（flow-panel 顶栏统一选择下发，空=未找到流程智能体） */
  agentId?: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  comments: FlowCommentItem[];
  commentAuthors: Record<string, string>;
  /** 当前用户是否为流程当前节点负责人（开放正文段落编辑） */
  isOwner?: boolean;
  onSaved: () => void;
  /** 进行中对话（指令+流式回复）变化时上报，供中部对话区实时展示 */
  onLiveChatChange?: (live: FlowLiveChat | null) => void;
  /** 文件审核入口状态变化时上报，供父级在顶部按钮行渲染入口按钮 */
  onReviewControlChange?: (ctl: FlowReviewControl | null) => void;
  /** 文件审核抽屉开/关上报：外层收缩布局为抽屉腾位（与范本预览抽屉同款联动） */
  onReviewOpenChange?: (open: boolean) => void;
  /** 确认卡片提交回写函数上报（与 onReviewControlChange 同款模式）：
   *  确认卡片渲染在中部对话区（ConversationView），提交成功后经此把 submitted
   *  回写进本面板的流式累积态，供归约器 confirm_timeout 守卫判断 */
  onConfirmSubmittedReady?: (fn: (() => void) | null) => void;
  /** 多范本选择卡片提交回写函数上报（同 onConfirmSubmittedReady 模式） */
  onSelectSubmittedReady?: (fn: (() => void) | null) => void;
}) {
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // 发送前置阶段（建会话/传附件）期间的锁，防止并发二次发送
  const [sending, setSending] = useState(false);
  // 审阅模式：ReviewPanel 展示文件段落 + 智能体返回的标注
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewFileId, setReviewFileId] = useState('');
  const [reviewFileName, setReviewFileName] = useState('');
  // AI 批注删除（硬删 DB 行）：成功后失效该 file 的 state 缓存，进度卡计数同步刷新
  const delAnnotation = useDeleteFileReviewAnnotation(reviewFileId);
  const [reviewPreparing, setReviewPreparing] = useState(false);
  // 审核目标来源：version = 流程版本文件（可编辑段落，存新版本）；
  // upload = 用户手动上传附件（doc/docx 可编辑，产出新文件自动进附件队列）
  const [reviewSource, setReviewSource] = useState<'version' | 'upload' | ''>(
    '',
  );
  // 编辑产出文件注入 ChatInputBox 附件队列（nonce 变化生效一次）
  const [queueInjectDoc, setQueueInjectDoc] = useState<{
    doc: UploadedDoc;
    removeId?: string;
    nonce: number;
  } | null>(null);
  // 来源为版本的审阅 document 对应的流程版本 id：版本切换（存为流程版本/
  // 回退/编辑保存新版本）后旧 document 作废，toggleReview 重传当前版本
  const [reviewFromVersionId, setReviewFromVersionId] = useState('');
  // flow 特有：未手动上传文件时，发送自动附带当前版本文件
  const [attachFile, setAttachFile] = useState(true);
  // agent_id 流程页签独立选择：flow-panel 顶栏统一选好经 props 下发（2026-09-21
  // 与对话页解耦，不再读 c-chat 写入的 ragflow_agent_id）。切换 agent 时必须
  // 弃旧会话——会话行与 agent 绑定（"Session does not belong to the requested
  // agent."），置空让下次发送经 ensureSession 自动重建
  const prevAgentIdRef = useRef(agentId);
  useEffect(() => {
    if (prevAgentIdRef.current !== agentId) {
      prevAgentIdRef.current = agentId;
      sessionIdRef.current = '';
    }
  }, [agentId]);
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
  // 「一个流程只能审核一个文件」的绑定源 + 对话修复的 task_id 注入源：流程内最近
  // 一条携带 file_review 的 AI 记录（从尾往前扫，空/畸形记录跳过）。发送守卫据此
  // 拦截不同文件的新审核；taskId 经 inputs.review_task_id 送入 Begin，供
  // FileReviewTool 的 fix/status 回退解析（节点产出的 task_id 不进 LLM 上下文）。
  const boundFileReview = useMemo<{
    fileId: string;
    taskId: string;
  } | null>(() => {
    for (let i = aiChats.length - 1; i >= 0; i--) {
      const fr = parseRecordFileReview(aiChats[i].file_review);
      if (fr?.fileId && fr?.taskId)
        return { fileId: fr.fileId, taskId: fr.taskId };
    }
    return null;
  }, [aiChats]);
  const instructionRef = useRef('');
  // 会话续接：只恢复【自己】保存记录里的 session_id（多人操作各自独立续聊）
  const sessionIdRef = useRef('');
  // ChatInputBox 内部上传完成的文档对象（发送时附带）
  const uploadedDocsRef = useRef<UploadedDoc[]>([]);
  // 2026-09-21：uploadedDocs state 已删——唯一读者是审核入口显示判定（现恒 true），
  // 上传队列只经下方 ref 同步（发送时读取，避免闭包过期）
  // 流式期间持续累积回复内容：send() 结束时 hook 会 resetAnswerList 清空
  // streamState，这里兜住完整回复供完成后展示/保存
  const contentRef = useRef('');
  // 范本填写进度快照：流式期间随 onLiveChatChange 上报；send() 结束 hook 会清空
  // streamState，用 ref 兜住供完成后（completed 态）继续展示成稿条
  const templateFillRef = useRef<ITemplateFillState | undefined>(undefined);
  const [completed, setCompleted] = useState<FlowLiveChat | null>(null);
  // 成稿条持久快照：自动保存成功清空 completed 后，仍用最近一次 finished 的
  // templateFill 上报，成稿条（含「存为流程版本」按钮）不随之消失
  const [lastTemplateFill, setLastTemplateFill] =
    useState<ITemplateFillState | null>(null);
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
    markConfirmSubmitted,
    markSelectSubmitted,
  } = useSendMessageBySSE(api.agentChatCompletion, {
    excludeFanOutFromContent: false,
  });

  // 确认卡片提交回写函数上报：挂载就绪上报、卸载清空（与 onReviewControlChange 同款生命周期）
  useEffect(() => {
    onConfirmSubmittedReady?.(markConfirmSubmitted);
    return () => onConfirmSubmittedReady?.(null);
  }, [onConfirmSubmittedReady, markConfirmSubmitted]);

  // 多范本选择卡片提交回写函数上报（语义同上）
  useEffect(() => {
    onSelectSubmittedReady?.(markSelectSubmitted);
    return () => onSelectSubmittedReady?.(null);
  }, [onSelectSubmittedReady, markSelectSubmitted]);

  useEffect(() => {
    if (streamState.templateFill) {
      templateFillRef.current = streamState.templateFill;
      setLastTemplateFill(streamState.templateFill);
    }
  }, [streamState.templateFill]);

  // 会话续接：只恢复【自己】保存记录里的 session_id（多人操作各自独立续聊）。
  // aiChats 异步到达，挂载后首次到位时恢复一次。
  const sessionRestoredRef = useRef(false);
  useEffect(() => {
    if (sessionRestoredRef.current || aiChats.length === 0) return;
    sessionRestoredRef.current = true;
    for (let i = aiChats.length - 1; i >= 0; i--) {
      if (aiChats[i].session_id && aiChats[i].user_id === currentUserId) {
        sessionIdRef.current = aiChats[i].session_id;
        break;
      }
    }
  }, [aiChats, currentUserId]);

  // 刷新恢复：从本流程已保存记录的 template_fill_events 重放范本填写进度
  // （数据源为 flow 自持存储，不再依赖 agent 会话消息）。仅挂载时恢复一次。
  // 记录被重放所用的原始事件序列（供运行快照恢复 hook 发现 canvas run id）
  const [replayEvents, setReplayEvents] = useState<unknown>(undefined);
  const replayRestoredRef = useRef(false);
  useEffect(() => {
    if (replayRestoredRef.current || aiChats.length === 0) return;
    replayRestoredRef.current = true;
    if (lastTemplateFill) return;
    // 回放取最新带事件的记录（纯文本轮之后刷新仍还原更早的模板进度，与旧 agent 会话回放行为一致）
    for (let i = aiChats.length - 1; i >= 0; i--) {
      const restored = parseAndReplay(aiChats[i].template_fill_events);
      if (restored) {
        templateFillRef.current = restored;
        setLastTemplateFill(restored);
        setReplayEvents(aiChats[i].template_fill_events);
        break;
      }
    }
    // 仅挂载后 aiChats 首次到位时恢复一次
  }, [aiChats, lastTemplateFill]);

  // 运行快照权威恢复（设计 2026-09-16）：重放态发现 canvas run id → 拉运行快照，
  // 存在则整体覆盖重放态（未完结持续轮询），键过期则挂起卡标灰；无 run id 的
  // 旧数据透出重放态本身（行为退化为现状）
  const recoveredTemplateFill = useTemplateFillRunRecovery(
    replayEvents,
    lastTemplateFill ?? undefined,
  );

  // 从流式事件中提取 session_id（多轮续聊依赖）
  useEffect(() => {
    const sid = answerList.find((e: any) => e?.session_id)?.session_id;
    if (sid) sessionIdRef.current = sid;
  }, [answerList]);

  // ── T15：FileReview 节点产出的 {file_id, task_id} 落到 live.fileReview ──
  // 与 T14 c-chat 同形态：在 answerList 里扫 component_name=FileReview 的 node_finished
  // 事件，取 outputs.task_id + inputs.file_id，挂到当前 live 上经 onLiveChatChange
  // 上报到 flow-detail 的 ConversationView，由其在 TemplateFillProgress 同位置挂
  // <FileReviewProgress />。组件内部 useFileReviewState 自管轮询，本面板不引入
  // 新 SSE / streamState.fileReview / setFileReviewState —— 仅固化链路里已在跑
  // 的 task_id + file_id，保证刷新后进度卡可继续轮询恢复。
  //
  // 持久化策略：因流式上报 effect 会反复用「新对象」覆盖 live，fileReview 不能直接
  // 存在对象上 —— 改存 ref，再在每次 onLiveChatChange 报告前合并（同一轮任务 id
  // 不重复切换时合并幂等）。同时新一轮发送（handleSend）必须清空，避免旧轮残留。
  // ⚠️ 此 effect 只在**中止路径**生效（中止不 resetAnswerList）：正常收尾时
  // setDone(true) 与 resetAnswerList 同帧批处理，done 态下 answerList 已空，
  // 正常路径的提取在 handleSend 里走 res.events（extractFileReviewTarget）。
  const fileReviewRef = useRef<{ fileId: string; taskId: string } | null>(null);
  useEffect(() => {
    if (!done) return;
    let fileId = '';
    let taskId = '';
    for (const evt of answerList) {
      const ev: any = evt as any;
      const data = ev?.data ?? {};
      // component_name 是 DSL 节点显示名（如「FileReview:BraveLionsScan」），组件类型
      // 在 component_type 字段——按显示名精确匹配永远扫不到（生产实测事件两字段即此形态）
      const componentType = (data?.component_type ?? '').toString();
      const componentName = (data?.component_name ?? '').toString();
      if (componentType !== 'FileReview' && componentName !== 'FileReview')
        continue;
      const outputs = data?.outputs ?? {};
      const inputs = data?.inputs ?? {};
      if (outputs?.task_id) taskId = String(outputs.task_id);
      // file_id 权威来源是节点 outputs（inputs 是空 dict）；inputs 两键为旧兜底
      const fileIdInput =
        outputs?.file_id ?? inputs?.file_id ?? inputs?.review_file_id;
      if (fileIdInput) fileId = String(fileIdInput);
      if (taskId && fileId) break;
    }
    if (!taskId || !fileId) return;
    // 幂等：同 taskId 不重复切换（避免 render 抖动）
    if (
      fileReviewRef.current &&
      fileReviewRef.current.taskId === taskId &&
      fileReviewRef.current.fileId === fileId
    ) {
      return;
    }
    fileReviewRef.current = { fileId, taskId };
    // 立刻上报一次，让中部对话区立即出现进度卡
    onLiveChatChange?.({
      instruction: instructionRef.current,
      response: contentRef.current,
      busy: false,
      templateFill: templateFillRef.current,
      fileReview: fileReviewRef.current,
    });
  }, [done, answerList, onLiveChatChange]);

  // 范本填写原始事件序列（template_fill_progress 的 data 载荷）：随自动保存/手动保存
  // 落到 flow_ai_chat.template_fill_events；发送新一轮时清空
  const templateFillEventsRef = useRef<unknown[]>([]);
  // 发送即存：本轮预落库的记录 id（完成自动保存时按它回填更新，不重复插记录）。
  // 无它则流式期间刷新页面，本轮指令与进度全部丢失
  const pendingRecordIdRef = useRef('');
  // 本轮手动上传附件（发送起点快照）：流式上报/完成上报时挂到 live.files 供
  // 用户气泡渲染附件 chip；自动保存时随记录落库（发送时事实，不回填覆盖）。
  // 必须在 handleSend 里从 manualDocs 同步快照赋值——ChatInputBox 清队列后
  // uploadedDocsRef 恒为空，流式期间再读会丢
  const liveFilesRef = useRef<{ id: string; name: string }[] | undefined>(
    undefined,
  );
  // 事件增量同步防抖定时器：流式期间把事件序列持续写入预存记录，
  // 中途刷新后历史重放（parseAndReplay）才有数据可恢复——否则事件只在
  // 最终回填时落库，刷新即丢全部范本进度
  const eventsSyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const events = answerList
      .filter((e: any) => e?.event === 'template_fill_progress')
      .map((e: any) => e.data);
    if (events.length === 0) return;
    templateFillEventsRef.current = events;
    const rid = pendingRecordIdRef.current;
    if (!rid) return;
    if (eventsSyncTimerRef.current) clearTimeout(eventsSyncTimerRef.current);
    eventsSyncTimerRef.current = setTimeout(() => {
      eventsSyncTimerRef.current = null;
      // 回填/失败标记已消费该记录（pendingRecordIdRef 清空）后不再覆盖最终内容
      if (pendingRecordIdRef.current !== rid) return;
      saveFlowAiRecord(flowId, {
        record_id: rid,
        response: '（生成中…）',
        session_id: sessionIdRef.current,
        template_fill_events: templateFillEventsRef.current,
        save_as_version: false,
      }).catch(() => {
        // 静默失败：仅影响刷新重放完整度，最终回填会写入全量事件
      });
    }, 2000);
  }, [answerList, flowId]);

  // 对话状态上报（供中部对话区实时展示）：
  // - 发送中（sending || !done）：busy=true，response 取实时流式内容
  // - 结束后：send() 尾部 resetAnswerList 会清空 streamState，
  //   用 contentRef 兜住完整回复转成 completed 展示
  // - 保存/重新发送时清空 contentRef + completed，上报回落 null
  // - T15：fileReview 字段由 fileReviewRef 合并到每次上报，避免主上报 effect
  //   反复重置 live 对象时把 FileReview 节点派生的 {fileId,taskId} 吞掉
  useEffect(() => {
    if (streamState.content) contentRef.current = streamState.content;
    const fileReview = fileReviewRef.current ?? undefined;
    if (sending || !done) {
      onLiveChatChange?.({
        instruction: instructionRef.current,
        response: streamState.content,
        busy: true,
        // 新轮刚起流式时 streamState.templateFill 必为空：沿用上一轮终态
        // （templateFillRef，handleSend 刻意不清它）兜底。若上报 undefined，
        // flow-detail 的 `live.templateFill?.templates?.length` 判空 → 进度卡
        // 卸载 → 卡内打开着的「查看填写内容」抽屉连同 liveTarget/整棵 docx
        // DOM 一起销毁（用户实测：发送一条普通消息，右侧预览瞬间变白底），
        // 轮次结束后卡片虽由 ref 装回，抽屉已不可恢复。与下方 completed
        // 分支的 templateFillRef 兜底同构。
        templateFill: streamState.templateFill ?? templateFillRef.current,
        fileReview,
        files: liveFilesRef.current,
      });
      return;
    }
    if (contentRef.current.trim()) {
      const next: FlowLiveChat = {
        instruction: instructionRef.current,
        response: contentRef.current,
        busy: false,
        templateFill: templateFillRef.current,
        fileReview,
        files: liveFilesRef.current,
      };
      setCompleted((prev) => (prev ? prev : next));
      onLiveChatChange?.(completed ?? next);
    } else {
      // completed 清空（自动/手动保存成功）后，仍用最近一次成稿快照上报，
      // 保证成稿条与「存为流程版本」按钮持续可见可用；刷新后上报快照恢复态
      //（recoveredTemplateFill 含运行快照轮询的权威覆盖）
      // 2026-09-20：无范本快照但有 fileReview 时同样不能上报 null——null 会把
      // live 整个清空，文件审核进度卡随 auto-save 完成瞬间消失（用户实测进度卡
      // 「从来不出现」的最后一环：出现过一瞬即被收尾分支抹掉）。instruction/
      // response 置空防与已入库历史气泡重复（同 recoveredTemplateFill 分支口径）。
      // fileReview 落库后（auto-save → onSaved → ai_chats refetch）历史气泡会按
      // record.file_review 自己挂卡，此时 live 分支不再上报 fileReview 让位历史卡
      // ——否则同一条进度卡出现两遍（live 一张 + 历史一张）。
      const frTakenOver =
        !!fileReview &&
        aiChats.some((c) => {
          if (!c.file_review) return false;
          try {
            const parsed = JSON.parse(c.file_review);
            return parsed?.taskId === fileReview.taskId;
          } catch {
            return false;
          }
        });
      const liveFileReview = frTakenOver ? undefined : fileReview;
      onLiveChatChange?.(
        completed ??
          (recoveredTemplateFill
            ? {
                instruction: '',
                response: '',
                busy: false,
                templateFill: recoveredTemplateFill,
                fileReview: liveFileReview,
              }
            : liveFileReview
              ? {
                  instruction: '',
                  response: '',
                  busy: false,
                  templateFill: templateFillRef.current,
                  fileReview: liveFileReview,
                }
              : null),
      );
    }
  }, [
    streamState.content,
    streamState.templateFill,
    done,
    sending,
    onLiveChatChange,
    completed,
    recoveredTemplateFill,
    aiChats,
  ]);

  // 自动保存：一轮对话流式结束后，自动将指令+回复写入流程记录（不建版本），
  // 无需手动点「仅存记录」；「存为新版本」随后可基于该记录补建版本（不重复插记录）。
  // 发送即存模式：本轮已预落「（生成中…）」占位记录（pendingRecordIdRef）→ 回填更新
  // 同一条，不重复插记录。失败时保留 contentRef，手动「仅存记录」按钮兜底。
  useEffect(() => {
    if (!done || sending || autoSavedRef.current) return;
    const text = contentRef.current.trim();
    if (!text) return;
    autoSavedRef.current = true;
    (async () => {
      try {
        const preSavedId = pendingRecordIdRef.current;
        pendingRecordIdRef.current = '';
        // 本轮若有文件审核进度卡，随记录落库（刷新后历史气泡按它恢复挂卡）
        const frJson = fileReviewRef.current
          ? JSON.stringify(fileReviewRef.current)
          : undefined;
        const res = (await saveFlowAiRecord(
          flowId,
          preSavedId
            ? {
                record_id: preSavedId,
                response: text,
                session_id: sessionIdRef.current,
                template_fill_events: templateFillEventsRef.current,
                file_review: frJson,
                save_as_version: false,
              }
            : {
                instruction: instructionRef.current || '(见记录)',
                response: text,
                version_id: version?.id,
                session_id: sessionIdRef.current,
                template_fill_events: templateFillEventsRef.current,
                file_review: frJson,
                save_as_version: false,
              },
        )) as { record?: { id?: string } };
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
  // 2026-09-20：文件审核进度卡「打开审核面板」时传入 file_review 批注
  // （state 端点 annotations）—— 本轮批注产自 FileReview 节点而非智能体
  // structured output，原逻辑面板恒空（用户实测看不到批注）。
  // 批注带 fileId 绑定（与 c-chat frPanelAnnotations 同构）：切文件自动失效，
  // 防上一个文件的历史批注串显到新文件面板。
  const [fileReviewAnnotations, setFileReviewAnnotations] = useState<{
    fileId: string;
    annotations: Annotation[];
  } | null>(null);
  const annotations = useMemo<Annotation[]>(() => {
    if (
      fileReviewAnnotations &&
      fileReviewAnnotations.fileId === reviewFileId &&
      fileReviewAnnotations.annotations.length > 0
    ) {
      return fileReviewAnnotations.annotations;
    }
    const structured = structuredOutputRef.current as any;
    const anns = structured?.annotations;
    return Array.isArray(anns) && anns.length > 0 ? (anns as Annotation[]) : [];
    // ref 读取依赖 done/answerList 触发重算
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, answerList, fileReviewAnnotations, reviewFileId]);

  // 重开面板恢复历史批注：面板批注原先只在 live 轮透传，刷新/重开后透传与
  // structured output 来源均已清空 → 面板恒空（用户实测「审核完重开批注不见」）。
  // 批注真源在 file_review 系统存库，按 fileId 拉 state 端点兜底恢复；
  // live 透传 / structured output 优先级不变，仅在两者皆空时才拉。
  useEffect(() => {
    if (!reviewMode || !reviewFileId) return;
    if (
      fileReviewAnnotations &&
      fileReviewAnnotations.fileId === reviewFileId &&
      fileReviewAnnotations.annotations.length > 0
    ) {
      return; // live 透传批注在场，不覆盖
    }
    const structured = structuredOutputRef.current as any;
    if (
      Array.isArray(structured?.annotations) &&
      structured.annotations.length > 0
    ) {
      return; // structured output 批注在场，不覆盖
    }
    let alive = true;
    (async () => {
      try {
        const { data } = await request.get(api.fileReviewState(reviewFileId));
        const anns = data?.data?.annotations;
        if (
          alive &&
          data?.code === 0 &&
          Array.isArray(anns) &&
          anns.length > 0
        ) {
          setFileReviewAnnotations({
            fileId: reviewFileId,
            annotations: anns as Annotation[],
          });
        }
      } catch {
        // 静默：面板仍可打开，仅无历史批注
      }
    })();
    return () => {
      alive = false;
    };
  }, [reviewMode, reviewFileId, fileReviewAnnotations]);

  const busy = !done || sending;
  // 结束后 streamState 被 hook reset，从 contentRef 兜底取完整回复
  const responseText = (streamState.content || contentRef.current).trim();
  const hasContent = responseText.length > 0;

  // 无会话时经 flow 后端建影子会话（source='flow'，对话页签不可见），
  // 否则后端走无状态 fresh run 路径，多轮对话没有上下文延续。
  const ensureSession = useCallback(async (): Promise<boolean> => {
    if (sessionIdRef.current) return true;
    try {
      const result = await createFlowChatSession(flowId, agentId);
      if (result?.session_id) {
        sessionIdRef.current = result.session_id;
        return true;
      }
      setError('创建会话失败');
      return false;
    } catch (e: any) {
      setError(e?.message || '创建会话失败');
      return false;
    }
  }, [agentId, flowId]);

  // ChatInputBox 上传完成的文档对象同步到 ref（发送时读取，避免闭包过期）
  const handleUploadedDocsChange = useCallback((files: UploadedDoc[]) => {
    uploadedDocsRef.current = files;
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

  // 上传范本填写成稿为 document（文件审核兜底目标）：流程无版本、无历史审核时，
  // 「上一个操作员的最新产出」= 历史范本填写轮的最新 done 成稿（templateFillRef
  // 经 template_fill_events 重放已含 filled 行的 download.url）。下载走 fetch 带
  // Authorization（/agents/download 无鉴权直链会乱码，同 downloadTemplateFillResult
  // 口径），再上传为 document 换取 file id。无成稿/下载或上传失败返回 null。
  const uploadTemplateFillResultAsDocument = useCallback(async () => {
    const templates = templateFillRef.current?.templates;
    if (!templates?.length) return null;
    // 从尾向前找：多范本/多轮时取事件序列上最新产出的成稿
    const dl = [...templates]
      .reverse()
      .find((t) => t.status === 'filled' && t.download?.url)?.download;
    if (!dl?.url) return null;
    const resp = await fetch(dl.url, {
      headers: { Authorization: getAuthorization() },
    });
    if (!resp.ok) return null;
    const blob = await resp.blob();
    const file = new File(
      [blob],
      dl.filename || dl.name || '范本填写成稿.docx',
      {
        type: dl.mime_type || 'application/octet-stream',
      },
    );
    const fd = new FormData();
    fd.append('file', file);
    const up = await fetch('/api/v1/documents/upload', {
      method: 'POST',
      headers: {
        Authorization: localStorage.getItem('Authorization') || '',
      },
      body: fd,
    });
    const result = await up.json();
    if (result.code === 0 && result.data) {
      const d = Array.isArray(result.data) ? result.data[0] : result.data;
      if (d?.id) return d as { id: string; name: string };
    }
    return null;
  }, []);

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
    // 「一个流程只能审核一个文件」守卫：流程内已有审核绑定（历史记录 / 本轮实时
    // 产出）时，再上传**另一个**文件发送会被整条拦截——审核任务链按文件独立
    // （task_id / 轮次 / 成稿对象名都挂 file_id，见 agent/component/file_review.py），
    // 同一流程出现第二条链会让进度卡与修复轮互相打架。只拦用户手动上传的不同
    // 文件：版本自动附带路径每次上传 id 都轮换且语义上仍是流程自身文档，不在
    // 拦截范围（否则每轮带附件的普通对话都发不出去）。
    // 手动上传队列必须在此**同步快照**：setSending(true) 让 ChatInputBox 在
    // sendLoading 上升沿清空队列并回传 onUploadedFilesChange([])（chat-input-box
    // 清队列 effect），把本 ref 一并清空；而下方发请求前还有预存占位记录的 await
    // ——await 之后再读 ref 恒为空 → files 为空 → 不注入 review_file_id，
    // FileReview 节点报「未指定待审核文件」（新流程无版本时无任何兜底路径）。
    const manualDocs = [...uploadedDocsRef.current];
    // 附件 chip 数据同样取发送起点同步快照（发送时事实）：live 气泡与落库共用
    liveFilesRef.current = manualDocs.length
      ? manualDocs.map((d) => ({ id: d.id, name: d.name || '' }))
      : undefined;
    const manualUploadId = manualDocs[0]?.id;
    const boundFrId =
      boundFileReview?.fileId || fileReviewRef.current?.fileId || '';
    if (manualUploadId && boundFrId && manualUploadId !== boundFrId) {
      message.error(
        '一个流程只能审核一个文件：当前流程已发起过文件审核，如需审核其他文件请新建流程',
      );
      return;
    }
    setError('');
    setSending(true);
    try {
      // 保存记录时要用（发送后输入框即清空）
      instructionRef.current = query;
      // 新一轮发送：清空上一轮兜底内容、完成态与已存记录
      contentRef.current = '';
      // 范本填写快照**不清**：成稿卡的可见性不该由「发了一条消息」决定。只有真正
      // 产生新填写的一轮才会替换它（streamState.templateFill → templateFillRef）。
      // 反例（2026-09-17 实测）：填写完成后用户说「把 XX 改成 YY」→ 走 FillTemplate
      // action=modify，一个 template_fill_progress 事件都不发 → 清空后无人回填，
      // 成稿卡整块消失（含「查看填写内容」按钮），刷新页面重新进入流程才因历史
      // 重放又从更早那条记录里长回来。
      // 事件序列同理保留：本轮若无新事件，落库的 template_fill_events 沿用上一轮
      // 快照，「改完刷新」也能重放出成稿卡（而不是跳过本条记录去找更早那条）。
      // T15：上一轮 FileReview 节点产出已落到中部卡，新一轮不应继续挂着旧 task_id
      fileReviewRef.current = null;
      // 上一轮未触发的增量同步定时器作废（pendingRecordIdRef 置空后守卫也会拦，这里直接清）
      if (eventsSyncTimerRef.current) {
        clearTimeout(eventsSyncTimerRef.current);
        eventsSyncTimerRef.current = null;
      }
      setCompleted(null);
      // 新一轮发送：停掉刷新恢复的运行快照轮询（否则旧运行的快照态会在新一轮
      // 结束保存后压过本轮状态）——恢复链路只服务「刷新后未发送」的场景
      setReplayEvents(undefined);
      setLastRecord(null);
      autoSavedRef.current = false;
      setValue('');

      const ok = await ensureSession();
      if (!ok) {
        setValue(query);
        return;
      }

      // 发送即存：立刻落一条「生成中」占位记录，等待确认卡/流式期间刷新页面
      // 本轮指令不丢；完成后自动保存按 pendingRecordIdRef 回填更新同一条记录。
      // 预存失败降级为原行为（仅完成后自动保存），不阻断发送
      pendingRecordIdRef.current = '';
      try {
        const presave = (await saveFlowAiRecord(flowId, {
          instruction: query,
          response: '（生成中…）',
          version_id: version?.id,
          session_id: sessionIdRef.current,
          save_as_version: false,
          files: liveFilesRef.current,
        })) as { record?: { id?: string } };
        if (presave?.record?.id) pendingRecordIdRef.current = presave.record.id;
      } catch {
        // 降级：不预存
      }
      // 发送失败/HTTP 错误时把占位记录标记为未完成，避免永远停留在「生成中…」
      const markPendingFailed = async () => {
        const rid = pendingRecordIdRef.current;
        pendingRecordIdRef.current = '';
        if (!rid) return;
        try {
          await saveFlowAiRecord(flowId, {
            record_id: rid,
            response: '（本轮未完成，无回复内容）',
            save_as_version: false,
          });
        } catch {
          // 回填失败忽略：记录停留在「生成中」占位
        }
      };

      // 用发送起点的同步快照（ref 此时已被 ChatInputBox 清空，见 handleSend 顶部注释）
      const docs = manualDocs;
      let files: unknown[] = docs;
      if (files.length === 0 && attachFile && version) {
        // 轻量通道：服务端提取版本纯文本 → 小 txt 文件上传（免每次整份 docx
        // blob 上传 + 画布重复解析）；失败静默回退原 uploadVersionAsDocument。
        // 审阅模式不走轻量通道：txt 无 docx 段落结构，会污染审阅目标
        // （ReviewPanel 展示 txt 段落但编辑落回版本 docx，段落错位损坏文档）
        // Word 版本不走轻量通道：txt 同样无 docx 结构，不能作为文件审核目标
        // （审核执行器仅支持 .docx），必须上传版本原件并取其 id 作 review_file_id
        const isWordVersion = /\.(docx|doc)$/i.test(version.file_name || '');
        if (!reviewMode && !isWordVersion) {
          try {
            const text = await getFlowVersionContent(flowId, version.id);
            if (text) {
              const fd = new FormData();
              fd.append(
                'file',
                new File([text], `${version.file_name}.txt`, {
                  type: 'text/plain',
                }),
              );
              const resp = await fetch('/api/v1/documents/upload', {
                method: 'POST',
                headers: {
                  Authorization: localStorage.getItem('Authorization') || '',
                },
                body: fd,
              });
              const result = await resp.json();
              if (result.code === 0 && result.data) {
                const d = Array.isArray(result.data)
                  ? result.data[0]
                  : result.data;
                // 传完整上传响应对象（含 mime_type）：canvas.get_files_async 依赖
                if (d?.id) files = [d];
              }
            }
          } catch {
            // 轻通道失败 → 走下方回退
          }
        }
        if (files.length === 0) {
          try {
            const doc = await uploadVersionAsDocument();
            if (doc) files = [doc];
          } catch {
            // 附件上传失败不阻断发送，降级为无文件提问
            files = [];
          }
        }
      }
      // 审阅模式下对齐 ReviewPanel 的目标文件（并记录来源：手动上传只读，版本文件可编辑）
      if (reviewMode) {
        const target = (docs[0] ?? (files[0] as UploadedDoc | undefined)) as
          | UploadedDoc
          | undefined;
        if (target?.id) {
          const fromUpload = docs.length > 0;
          setReviewFileId(target.id);
          setReviewFileName(target.name || '');
          setReviewSource(fromUpload ? 'upload' : 'version');
          if (!fromUpload) setReviewFromVersionId(version?.id ?? '');
        }
      }

      let res: any = null;
      try {
        // 文件审核契约：待审核文件 id 必须经 inputs.review_file_id 送入 Begin——
        // FileReview 节点/工具都只认 Begin 的这个输出，画布会丢弃 files 里的上传 id。
        // 无附件不发该键（空值只会换来节点报「未指定待审核文件」）。
        // 对话修复注入：流程里节点产出的 task_id 只到前端、不进 LLM 上下文，把已知
        // 绑定经 inputs.review_task_id 送入 Begin，FileReviewTool 的 fix/status 回退
        // 解析它——用户说「修复严重问题」「查下进度」无需提供 task_id。无附件也注入
        // （纯文本对话修复正是主场景）；本轮实时 fileReviewRef 已在发送起点被清空，
        // 这里只读落库绑定（保存后 onSaved → ai_chats refetch 会及时补上）。
        const firstFileId = (files[0] as { id?: string } | undefined)?.id;
        const reviewInputs: Record<string, { value: string; type: string }> =
          {};
        if (firstFileId) {
          reviewInputs.review_file_id = { value: firstFileId, type: 'line' };
        }
        if (boundFileReview?.taskId) {
          reviewInputs.review_task_id = {
            value: boundFileReview.taskId,
            type: 'line',
          };
        }
        // 重写目标优先级（最新产物优先）：本会话最新范本成稿卡 > 流程版本文档。
        // 有成稿卡时传 recent_downloads 并把 flow_version_id 置空——后端
        // DocumentRewrite 对流程版本目标无条件优先（_doc_for_action），必须以
        // 缺参让位它才能降级到 chat 来源；无成稿卡回落流程版本（现状行为）。
        const fillDownloads = collectRecentFillDownloads(
          recoveredTemplateFill ?? templateFillRef.current,
        );
        res = await send({
          agent_id: agentId,
          query,
          session_id: sessionIdRef.current,
          stream: true,
          files,
          inputs: Object.keys(reviewInputs).length ? reviewInputs : undefined,
          internet: false,
          // 最近范本成稿卡契约：sys.recent_downloads 供 DocumentRewrite 定位重写目标
          recent_downloads: fillDownloads.length ? fillDownloads : undefined,
          // 当前流程版本文档：sys.flow_version_id 供 flow 场景 DocumentRewrite 定位重写目标（无版本/有成稿卡空串→工具降级 chat 来源）
          flow_version_id: fillDownloads.length
            ? ''
            : String(version?.id ?? ''),
        });
        // hook 收尾时同步 flush+reset（React 批处理），streamState 一次性清空；
        // 尾包 message 与 [DONE] 同帧到达时 RAF flush 未跑过，contentRef 拿不到
        // 内容，自动保存会因空文本静默跳过——用 send 返回的最终累积回复兜底
        const finalText = (res?.content as string | undefined)?.trim();
        if (finalText && !contentRef.current.trim()) {
          contentRef.current = finalText;
        }
        // 同理，done 事件（含成稿文件下载契约）与 [DONE] 同帧到达时不会进入
        // answerList → templateFillEventsRef 缺 done，落库后回放永远停在
        // 「填写中 x/y」、无预览/下载/存为流程版本入口——用全量原始事件重建
        const finalTplEvents = ((res?.events as any[] | undefined) ?? [])
          .filter((e: any) => e?.event === 'template_fill_progress')
          .map((e: any) => e.data);
        if (finalTplEvents.length > 0) {
          templateFillEventsRef.current = finalTplEvents;
        }
        // T15 进度卡（2026-09-20 根修）：done 与 resetAnswerList 同帧批处理，
        // answerList 态扫描 effect 只能见到空列表——必须从 res.events 全量原始
        // 事件里提取（与上方 finalTplEvents / c-chat downloads 回填同款模式）。
        // 立即上报一次，让中部对话区在流结束后立刻出现进度卡。
        const fr = extractFileReviewTarget(res?.events as any[] | undefined);
        if (fr) {
          fileReviewRef.current = fr;
          onLiveChatChange?.({
            instruction: instructionRef.current,
            response: contentRef.current,
            busy: false,
            templateFill: templateFillRef.current,
            fileReview: fr,
          });
        }
        // canvas 运行期错误的兜底消息（后端 message 事件带 error 标记）：气泡文本
        // 照常展示 + toast 显式提醒，避免用户只看到一段普通回复没意识到执行失败
        const errEvent = ((res?.events as any[] | undefined) ?? []).find(
          (e: any) => e?.event === 'message' && e?.data?.error,
        );
        if (errEvent) {
          message.error(
            typeof errEvent.data.content === 'string'
              ? errEvent.data.content
              : '本轮执行失败，请查看对话内容',
          );
        }
      } catch (e: any) {
        const msg = e?.message || '发送失败，请检查网络后重试';
        if (isSessionMissingError(msg)) sessionIdRef.current = '';
        setError(msg);
        setValue(query);
        await markPendingFailed();
        return;
      }

      if (
        res &&
        (res.response.status !== 200 || (res.data as any)?.code !== 0)
      ) {
        const msg =
          (res.data as any)?.message ||
          `请求失败（HTTP ${res.response.status}）`;
        // 影子会话被外部删除时后端在此返回（HTTP 200 + code!=0 + "Session not found!"），
        // 清空引用让下一次发送自动重建会话，避免每轮发送都失败且无法自愈
        if (isSessionMissingError(msg)) sessionIdRef.current = '';
        setError(msg);
        setValue(query);
        await markPendingFailed();
      }
    } finally {
      setSending(false);
    }
  }, [
    agentId,
    attachFile,
    boundFileReview,
    busy,
    ensureSession,
    flowId,
    recoveredTemplateFill,
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
    // 审阅 document 来自旧版本（如「存为流程版本」后 current 已切换）→ 作废，
    // 重走下方当前版本上传，避免文件审核打开旧文件
    if (
      reviewFileId &&
      reviewSource === 'version' &&
      version &&
      reviewFromVersionId !== version.id
    ) {
      setReviewFileId('');
      setReviewFileName('');
      setReviewSource('');
    }
    if (!reviewFileId && uploadedDocsRef.current[0]) {
      setReviewFileId(uploadedDocsRef.current[0].id);
      setReviewFileName(uploadedDocsRef.current[0].name || '');
      setReviewSource('upload');
    }
    if (!reviewFileId && !uploadedDocsRef.current[0]) {
      if (reviewPreparing) return;
      if (!version) {
        // 无版本流程：打开流程内最近的审核文件（对话直传上传通道的 document，
        // 与 openWithFile 同一语义）；再退一步用上一操作员的范本填写成稿兜底
        // （历史轮最新 done 成稿上传为 document，见 uploadTemplateFillResultAsDocument）；
        // 连成稿都没有时才引导先上传
        if (!boundFileReview?.fileId) {
          if (reviewPreparing) return;
          setError('');
          setReviewPreparing(true);
          try {
            const doc = await uploadTemplateFillResultAsDocument();
            if (doc) {
              setReviewFileId(doc.id);
              setReviewFileName(doc.name);
              setReviewSource('upload');
              setReviewMode(true);
              return;
            }
          } catch {
            // 成稿下载/上传失败不阻断，落到下方引导文案
          } finally {
            setReviewPreparing(false);
          }
          message.error('请先上传流程版本，或在对话中上传文件后再发起审核');
          return;
        }
        setReviewFileId(boundFileReview.fileId);
        setReviewFileName('');
        setReviewSource('upload');
        setReviewMode(true);
        return;
      }
      setError('');
      setReviewPreparing(true);
      try {
        const doc = await uploadVersionAsDocument();
        if (doc) {
          setReviewFileId(doc.id);
          setReviewFileName(doc.name);
          setReviewSource('version');
          setReviewFromVersionId(version.id);
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
    reviewFromVersionId,
    reviewMode,
    reviewPreparing,
    reviewSource,
    boundFileReview,
    uploadTemplateFillResultAsDocument,
    uploadVersionAsDocument,
    version,
  ]);

  // 文件审核入口状态上报：父级据此在顶部按钮行渲染/更新按钮；卸载时清空
  // 2026-09-21：始终显示——无版本流程点击时回退到流程内最近审核文件（对话直传
  // 那份），不再因「无版本且无待上传」隐藏入口（用户实测按钮消失的根因）
  const reviewVisible = true;
  useEffect(() => {
    onReviewControlChange?.({
      visible: reviewVisible,
      active: reviewMode,
      toggle: () => {
        void toggleReview();
      },
      // T15：FileReviewProgress 回调入口 —— 指定 fileId 切换并打开 review 抽屉
      // （与 toggle 的「使用当前 reviewFileId」行为差异：upload 流必给 fileId，
      //  否则版本文档场景用 reviewFileId 兜底）
      openWithFile: (
        fileId: string,
        fileName?: string,
        annotations?: Annotation[],
      ) => {
        setReviewFileId(fileId);
        if (fileName) setReviewFileName(fileName);
        // 文件审核批注优先（本轮批注在 file_review 系统，不在 structured output）；
        // 空/未传视为「无本轮批注」，仍绑定 fileId —— restore effect 据此按 file_id 拉 state 端点恢复历史批注
        setFileReviewAnnotations({
          fileId,
          annotations: annotations && annotations.length > 0 ? annotations : [],
        });
        setReviewSource('upload');
        setReviewFromVersionId('');
        setReviewMode(true);
      },
    });
  }, [onReviewControlChange, reviewVisible, reviewMode, toggleReview]);
  useEffect(() => {
    return () => onReviewControlChange?.(null);
  }, [onReviewControlChange]);

  // 文件审核抽屉开/关上报（腾位联动，与范本预览抽屉同款）；卸载时兜底关闭
  useEffect(() => {
    onReviewOpenChange?.(reviewMode);
    return () => onReviewOpenChange?.(false);
  }, [reviewMode, onReviewOpenChange]);

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
          template_fill_events: templateFillEventsRef.current,
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

  // Word 式手动批注：选中审阅正文后写入 flow 评论（带锚点+级别），经 onSaved 刷新回显
  const handleAddAnchoredComment = useCallback(
    async (p: {
      content: string;
      anchorText: string;
      anchorPara: number | null;
      anchorStart?: number | null;
      severity?: string;
    }) => {
      await addFlowComment(flowId, p.content, version?.id, {
        anchorText: p.anchorText,
        anchorPara: p.anchorPara,
        anchorStart: p.anchorStart ?? null,
        severity: p.severity,
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

  // Word 式正文编辑，按审核目标来源分派：
  // - version：后端按 para_index 同步增删改段落并存新版本（source=manual_edit，
  //   .doc 先转 docx），刷新流程详情后把新版本重新上传为 document，预览即切到新内容
  // - upload：对话直传附件走 /files/<id>/edit，产出全新文件对象（原文件不变），
  //   自动注入附件队列——下条消息即可让 LLM 分析编辑后的内容
  const handleEditDocument = useCallback(
    async (ops: {
      edits: Array<{ paraIndex: number; newText: string }>;
      deletes: number[];
      inserts: Array<{ afterParaIndex: number; newText: string }>;
      tableEdits: Array<{
        paraIndex: number;
        row: number;
        col: number;
        newText: string;
        runs?: FlowDocRun[];
      }>;
    }) => {
      if (reviewSource === 'upload') {
        if (!reviewFileId) throw new Error('无文件，无法编辑');
        const res = await editFileDocument(reviewFileId, reviewFileName, ops);
        setQueueInjectDoc({
          doc: { id: res.file_id, name: res.file_name },
          removeId: reviewFileId,
          nonce: Date.now(),
        });
        setReviewFileId(res.file_id);
        setReviewFileName(res.file_name);
        message.success('已生成编辑版，已加入附件队列');
        return;
      }
      if (!version) throw new Error('无版本文件，无法编辑');
      const res = await editFlowDocument(flowId, version.id, ops);
      onSaved();
      const doc = await uploadVersionAsDocument(res.version);
      if (doc) {
        setReviewFileId(doc.id);
        setReviewFileName(doc.name);
        setReviewSource('version');
        setReviewFromVersionId(res.version.id);
      }
    },
    [
      flowId,
      onSaved,
      reviewFileId,
      reviewFileName,
      reviewSource,
      uploadVersionAsDocument,
      version,
    ],
  );

  // 标题行内容（上下文 / 附带版本文件）：
  // 经 ChatInputBox 的 leftSlot 渲染在发送按钮同一行，不再单独占一行
  const titleRow = (
    <>
      <span className="flex min-w-0 flex-1 items-center">
        {version ? (
          <span
            className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border border-[#BFD3F5] bg-[#F0F5FF] px-2 py-0.5"
            title={`发送时自动附带当前版本文件作为 AI 上下文：v${version.version_no} ${version.file_name}`}
          >
            <FileText
              className="size-3 shrink-0 text-[#1a66fb]"
              strokeWidth={2}
            />
            <span className="shrink-0 text-[11px] font-semibold text-[#1a66fb]">
              上下文
            </span>
            <span className="truncate text-[11px] font-medium text-[#1a66fb]">
              v{version.version_no} {version.file_name}
            </span>
          </span>
        ) : (
          <span className="truncate text-xs text-[#999]">（无上下文文件）</span>
        )}
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
      {/* 文件审核入口已上移到流程详情顶部按钮行（经 onReviewControlChange 上报） */}
    </>
  );

  return (
    <div className="shrink-0">
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
        onDeleteAnnotation={(id) =>
          delAnnotation.mutateAsync(id).then(() => undefined)
        }
        currentUserId={currentUserId}
        canEdit={
          !!isOwner &&
          (reviewSource === 'version'
            ? !!version
            : reviewSource === 'upload' && /\.(docx?)$/i.test(reviewFileName))
        }
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
          injectDoc={queueInjectDoc}
          accept=".doc,.docx"
          autoFocus
          leftSlot={titleRow}
        />
      </div>

      {/* 保存动作：回复完成后自动已存记录；hasContent 为 true 说明自动保存
          失败，补显手动「仅存记录」兜底 */}
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
        </div>
      )}
    </div>
  );
}
