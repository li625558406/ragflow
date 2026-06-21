import { AgentStatusChip } from '@/components/agent-status-chip';
import AppDownloadDialog from '@/components/app-download-dialog';
import BidPanel from '@/components/bid';
import ChapteredMarkdown from '@/components/chaptered-markdown';
import CollaborationPanel from '@/components/collaboration';
import CreateDocumentDialog from '@/components/collaboration/create-document-dialog';
import DynamicIcon from '@/components/dynamic-icon';
import FavoriteDialog from '@/components/favorite-dialog';
import FavoritePanel from '@/components/favorite-panel';
import {
  FileUpload,
  FileUploadDropzone,
  FileUploadItem,
  FileUploadItemDelete,
  FileUploadItemMetadata,
  FileUploadItemPreview,
  FileUploadItemProgress,
  FileUploadList,
  FileUploadTrigger,
  type FileUploadProps,
} from '@/components/file-upload';
import { MarkdownErrorBoundary } from '@/components/markdown-error-boundary';
import { ReferenceDocumentList } from '@/components/next-message-item/reference-document-list';
import { ReferenceImageList } from '@/components/next-message-item/reference-image-list';
import PdfSheet from '@/components/pdf-drawer';
import { useClickDrawer } from '@/components/pdf-drawer/hooks';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import ToolsPanel from '@/components/tools';
import { Textarea } from '@/components/ui/textarea';
import {
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  CircleStop,
  Copy,
  Download,
  FileText,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Paperclip,
  Plus,
  RefreshCw,
  Send,
  Smartphone,
  Square,
  Star,
  Trash2,
  Upload,
  X,
} from 'lucide-react';

import { RealtimeAudioButton } from '@/components/realtime-audio-button';
import {
  CendTooltip,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { MessageType } from '@/constants/chat';
import {
  useHandleMessageInputChange,
  useSelectDerivedMessages,
} from '@/hooks/logic-hooks';
import { useCancelConversation } from '@/hooks/use-agent-request';
import {
  MessageEventType,
  useSendMessageBySSE,
} from '@/hooks/use-send-message';
import type {
  Docagg,
  IMessage,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';
import { IAnswer } from '@/interfaces/database/chat';
import {
  getLatestError,
  useFindMessageReference,
} from '@/pages/agent/chat/use-send-agent-message';
import { AgentChatContext } from '@/pages/agent/context';
import api from '@/utils/api';
import { markdownToBodyHtml } from '@/utils/markdown-to-word';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { v4 as uuid } from 'uuid';

interface Agent {
  id: string;
  title: string;
  dataset_ids?: string[];
  canvas?: string;
}

interface Session {
  id: string;
  name: string;
  update_time: number;
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === 'function'
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // clipboard API unavailable; fall through to execCommand
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export default function CChat() {
  const navigate = useNavigate();

  // ── Auth state (C-side) ──
  const [token, setToken] = useState(
    () => localStorage.getItem('Authorization') || '',
  );
  const [userInfo, setUserInfo] = useState<Record<string, string> | null>(
    () => {
      try {
        return JSON.parse(localStorage.getItem('userInfo') || 'null');
      } catch {
        return null;
      }
    },
  );

  // ── CSS injection (C-side) ──
  useEffect(() => {
    const styleId = 'c-chat-styles';
    if (document.getElementById(styleId)) return;
    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      .cs-bg { background: #FFFFFF; }
      .cs-sidebar-bg { background: #FFFFFF; }
      .cs-scrollbar::-webkit-scrollbar { width: 4px; }
      .cs-scrollbar::-webkit-scrollbar-thumb { background: #A3A3A3; border-radius: 4px; }
      .cs-input-ring { transition: border-color 0.15s ease, background-color 0.15s; }
      .cs-input-ring:focus-within { border-color: #000000; background: white; }
      .cs-typewriter-cursor::after { content: '|'; animation: cs-blink 1s step-end infinite; color: #000000; }
      @keyframes cs-blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
      @keyframes cs-msg-in { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
      .cs-msg-enter { animation: cs-msg-in 0.35s ease-out; }
      @keyframes cs-input-focus-in { from { transform: scale(0.98); opacity: 0.6; } to { transform: scale(1); opacity: 1; } }
      .cs-input-enter { animation: cs-input-focus-in 0.4s ease-out; }
      @keyframes cs-page-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
      .cs-page-enter { animation: cs-page-in 0.3s cubic-bezier(0.22, 1, 0.36, 1) both; }
      @keyframes cs-card-pop { from { opacity: 0; transform: scale(0.92) translateY(6px); } to { opacity: 1; transform: scale(1) translateY(0); } }
      .cs-card-enter { animation: cs-card-pop 0.4s cubic-bezier(0.22, 1, 0.36, 1) both; }
      .cs-card-d1 { animation-delay: 0.05s; }
      .cs-card-d2 { animation-delay: 0.12s; }
      .cs-card-d3 { animation-delay: 0.19s; }
      .cs-card-d4 { animation-delay: 0.26s; }
      @keyframes cs-float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
      .cs-float { animation: cs-float 3.5s ease-in-out infinite; }
      @keyframes cs-glow { 0%,100% { box-shadow: 0 0 0 0 rgba(0,0,0,0.05); } 50% { box-shadow: 0 0 0 14px rgba(0,0,0,0); } }
      .cs-glow { animation: cs-glow 2.8s ease-in-out infinite; }
      .cs-card-hover:hover .cs-card-icon { animation: cs-icon-pop 0.35s cubic-bezier(0.34,1.56,0.64,1); }
      @keyframes cs-icon-pop { 0% { transform: scale(1); } 40% { transform: scale(1.18); } 100% { transform: scale(1); } }
      @keyframes cs-list-in { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: translateX(0); } }
      .cs-list-enter { animation: cs-list-in 0.3s cubic-bezier(0.22, 1, 0.36, 1) both; }
      .cs-list-d0 { animation-delay: 0.03s; }
      .cs-list-d1 { animation-delay: 0.06s; }
      .cs-list-d2 { animation-delay: 0.09s; }
      .cs-list-d3 { animation-delay: 0.12s; }
      .cs-list-d4 { animation-delay: 0.15s; }
      .cs-list-d5 { animation-delay: 0.18s; }
      .cs-list-d6 { animation-delay: 0.21s; }
      .cs-list-d7 { animation-delay: 0.24s; }
      @keyframes cs-row-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
      .cs-row-enter { animation: cs-row-in 0.25s cubic-bezier(0.22, 1, 0.36, 1) both; }
      .cs-row-d0 { animation-delay: 0.02s; }
      .cs-row-d1 { animation-delay: 0.04s; }
      .cs-row-d2 { animation-delay: 0.06s; }
      .cs-row-d3 { animation-delay: 0.08s; }
      .cs-row-d4 { animation-delay: 0.10s; }
      .cs-row-d5 { animation-delay: 0.12s; }
      .cs-row-d6 { animation-delay: 0.14s; }
      .cs-row-d7 { animation-delay: 0.16s; }
      .cs-row-d8 { animation-delay: 0.18s; }
      .cs-row-d9 { animation-delay: 0.20s; }
      .msg-content table {
        width: 100%;
        box-sizing: border-box;
        border-collapse: collapse;
      }
      .msg-content th,
      .msg-content td {
        padding: 6px 13px;
        border: 1px solid #d1d9e0;
      }
      .msg-content td:hover {
        background: #f2f2f22a;
      }
      .msg-content tr:nth-child(even) {
        background-color: #f2f2f22a;
      }
      .msg-content caption {
        color: #a3a3a3;
        font-size: 14px;
        line-height: 1.25;
        font-weight: 600;
        margin-bottom: 6px;
      }
      .msg-content em {
        color: var(--accent-primary, #4F6DEE);
        font-style: normal;
      }
      .msg-content a {
        color: #4F6DEE;
        text-decoration: underline;
      }
    `;
    document.head.appendChild(style);
    return () => document.getElementById(styleId)?.remove();
  }, []);

  // ── Agent / Session state (C-side) ──
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  const [currentAgentPrologue, setCurrentAgentPrologue] = useState<string>('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(),
  );
  const [collapsedMessages, setCollapsedMessages] = useState<Set<string>>(
    new Set(),
  );
  const pendingSendRef = useRef(false);
  const loadingSessionRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const isAtBottomVirtuosoRef = useRef(true);
  const [newSessionKey, setNewSessionKey] = useState(0);
  const [enableInternet] = useState(false);
  const [audioInputValue, setAudioInputValue] = useState<string | null>(null);

  // File upload state (C-side)
  const [uploadedFiles, setUploadedFiles] = useState<
    Array<{
      id: string;
      name: string;
      mime_type: string;
      created_by: string;
      size: number;
      extension: string;
    }>
  >([]);
  const [files, setFiles] = useState<File[]>([]);

  // ── B-side chat hooks ──
  const { handleInputChange, value, setValue } = useHandleMessageInputChange();
  const {
    send,
    answerList,
    streamState,
    done,
    stopOutputMessage,
    setDone,
    resetAnswerList,
  } = useSendMessageBySSE(api.agentChatCompletion, {
    excludeFanOutFromContent: false,
  });
  const { cancelConversation } = useCancelConversation();
  const taskId = answerList[0]?.task_id;

  // Cache node events so they persist after stream completion clears answerList
  const cachedNodeEventsRef = useRef<Record<string, Array<any>>>({});

  // Prevent double-send: lock acquired synchronously before async state updates
  const sendingLockRef = useRef(false);
  // Track IME composition so we don't block send while user is composing Chinese
  const composingRef = useRef(false);
  const [composing, setComposing] = useState(false);
  // Track whether the user explicitly stopped generation
  const [stoppedByUser, setStoppedByUser] = useState(false);

  // Debounce timers for session list / message loading
  const sessionLoadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const messageLoadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  // Extract node events from answerList for thinking timeline
  const nodeEventsByMsgId = useMemo(() => {
    const map: Record<string, Array<any>> = {};
    if (!answerList.length) {
      // Return cached events when answerList is empty (stream completed)
      return { ...cachedNodeEventsRef.current };
    }
    for (const evt of answerList) {
      if (
        evt.event !== MessageEventType.Message &&
        evt.event !== MessageEventType.MessageEnd
      ) {
        const mid = (evt as any).message_id;
        if (mid) {
          if (!map[mid]) map[mid] = [];
          map[mid].push(evt);
        }
      }
    }
    // Update cache with fresh events
    cachedNodeEventsRef.current = map;
    return map;
  }, [answerList, newSessionKey]);

  const stopConversation = useCallback(() => {
    console.log(
      '[STOP] stopConversation called, setting stoppedByUser=true, taskId=',
      taskId,
    );
    setStoppedByUser(true);
    stopOutputMessage();
    if (taskId) {
      cancelConversation(taskId);
    }
  }, [stopOutputMessage, cancelConversation, taskId]);
  const { findReferenceByMessageId } = useFindMessageReference(answerList);
  const {
    clickDocumentButton,
    visible: drawerVisible,
    hideModal: hideDrawer,
    documentId: drawerDocumentId,
    selectedChunk: drawerSelectedChunk,
  } = useClickDrawer();
  const {
    derivedMessages,
    scrollRef,
    removeLatestMessage,
    addNewestOneQuestion,
    addNewestOneAnswer,
    setDerivedMessages,
    scrollToBottom,
  } = useSelectDerivedMessages();

  const sendLoading = !done;

  // Get node events for the latest message that has them (for input-area chip).
  // During the window between the first NodeStarted and the first Message event,
  // the answer hasn't been created yet — fall back to any node events in the map.
  const latestNodeEvents = useMemo(() => {
    for (let i = derivedMessages.length - 1; i >= 0; i--) {
      const msg = derivedMessages[i];
      const events = nodeEventsByMsgId[msg.id || ''];
      if (events && events.length > 0) {
        return { messageId: msg.id || '', events };
      }
    }
    // Answer not created yet (no content), but node events exist for the
    // current stream — surface them anyway so the task bar shows early.
    // Only active during streaming; when idle (sendLoading=false) a new
    // session may have cleared cachedNodeEventsRef without the useMemo
    // re-running, so we must not surface stale events.
    const ids = Object.keys(nodeEventsByMsgId);
    if (ids.length > 0 && sendLoading) {
      return { messageId: ids[0], events: nodeEventsByMsgId[ids[0]] };
    }
    return null;
  }, [derivedMessages, nodeEventsByMsgId, sendLoading]);

  // ── Process SSE events into messages ──
  // streamState is already RAF-throttled (60fps foreground / 2fps background)
  // by useSendMessageBySSE, so we can render directly without additional
  // throttling or findMessageFromList recomputation.

  const answerListRef = useRef(answerList);
  answerListRef.current = answerList;

  useEffect(() => {
    if (done) {
      console.log(
        '[EFFECT.STREAM] done=true, skipping (streamState.content.length=',
        streamState.content?.length || 0,
        ')',
      );
      return;
    }

    const answer = streamState.content || getLatestError(answerListRef.current);
    if (!answer) {
      console.log(
        '[EFFECT.STREAM] done=false but no answer (content empty, no error)',
      );
      return;
    }

    console.log(
      '[EFFECT.STREAM] updating message id=',
      streamState.id,
      'contentLen=',
      answer.length,
    );
    addNewestOneAnswer({
      answer: answer ?? '',
      attachment: streamState.attachment as any,
      downloads: streamState.downloads,
      id: streamState.id,
    } as IAnswer);
  }, [streamState, addNewestOneAnswer, done]);

  // ── Prologue is shown as intro text in the welcome screen, not auto-added as a message
  // This keeps the input centered until the user explicitly starts a conversation.

  // ── UI state (C-side) ──
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const [mainView, setMainView] = useState<
    'chat' | 'collaboration' | 'tools' | 'bid' | 'favorites'
  >('chat');
  const [tabResetKeys, setTabResetKeys] = useState<Record<string, number>>({});
  const [collabDialogOpen, setCollabDialogOpen] = useState(false);
  const [collabMessage, setCollabMessage] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [favoriteMode, setFavoriteMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<Set<string>>(
    new Set(),
  );
  const [favoriteDialogOpen, setFavoriteDialogOpen] = useState(false);
  const [isSaveAllFavorites, setIsSaveAllFavorites] = useState(false);
  const [panelRefreshToken, setPanelRefreshToken] = useState(0);

  // ── Click outside agent dropdown ──
  const agentDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!agentDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        agentDropdownRef.current &&
        !agentDropdownRef.current.contains(e.target as Node)
      ) {
        setAgentDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [agentDropdownOpen]);

  const msgSelectKey = (msg: { role: string; id: string }) =>
    `${msg.role}_${msg.id}`;

  const collapseScrollTargetRef = useRef<string | null>(null);

  const toggleMessageCollapse = (msgId: string) => {
    setCollapsedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) {
        next.delete(msgId);
        collapseScrollTargetRef.current = null;
      } else {
        next.add(msgId);
        collapseScrollTargetRef.current = msgId;
      }
      return next;
    });
  };

  // After collapse, scroll to the collapsed message row
  useEffect(() => {
    const targetId = collapseScrollTargetRef.current;
    if (!targetId) return;
    if (virtuosoRef.current) {
      const idx = derivedMessages.findIndex((m) => m.id === targetId);
      if (idx >= 0) {
        requestAnimationFrame(() => {
          virtuosoRef.current?.scrollToIndex({
            index: idx,
            behavior: 'smooth',
            align: 'start',
          });
        });
      }
    }
    collapseScrollTargetRef.current = null;
  }, [collapsedMessages, derivedMessages]);

  const toggleMessagePair = (msgIndex: number) => {
    setSelectedMessageIds((prev) => {
      const next = new Set(prev);
      const msg = derivedMessages[msgIndex];
      if (!msg?.id) return prev;
      const key = msgSelectKey(msg);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const [downloadOpen, setDownloadOpen] = useState(false);

  // Reset the current tab's content when re-clicking the active tab
  const handleTabClick = (tabKey: typeof mainView) => {
    if (mainView === tabKey) {
      setTabResetKeys((prev) => ({
        ...prev,
        [tabKey]: (prev[tabKey] || 0) + 1,
      }));
    }
    setMainView(tabKey);
  };

  const getTabResetKey = (tabKey: string) =>
    `${tabKey}-${tabResetKeys[tabKey] || 0}`;

  // Typewriter placeholder
  const FULL_PLACEHOLDER =
    '请在此描述您的标书分析需求，例如：提取招标文件中的关键资质要求、分析评分标准的权重分布、对比各投标企业的技术方案优劣、检查合同条款中的潜在风险点...';
  const [typewriterText, setTypewriterText] = useState('');
  const [typewriterIdx, setTypewriterIdx] = useState(0);
  const [typewriterForward, setTypewriterForward] = useState(true);
  const hasMessages = derivedMessages.length > 0;

  useEffect(() => {
    if (hasMessages) return;
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
  }, [typewriterIdx, typewriterForward, hasMessages]);

  // ── Auth ──
  const clearAuth = useCallback(() => {
    setToken('');
    setUserInfo(null);
    localStorage.removeItem('Authorization');
    localStorage.removeItem('token');
    localStorage.removeItem('userInfo');
    localStorage.removeItem('ragflow_agent_id');
  }, []);

  const apiFetch = useCallback(
    async (url: string, options: RequestInit = {}) => {
      const resp = await fetch(url, {
        ...options,
        headers: {
          ...options.headers,
          Authorization: token,
        },
      });
      if (resp.status === 401) {
        showToast('登录已过期，请重新登录');
        clearAuth();
        throw new Error('Unauthorized');
      }
      return resp;
    },
    [token, clearAuth],
  );

  useEffect(() => {
    if (token) {
      fetch('/api/v1/users/me', {
        headers: { Authorization: token },
      })
        .then((r) => {
          if (r.status !== 200) {
            clearAuth();
          }
        })
        .catch(() => {
          clearAuth();
        });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fix scroll
  useEffect(() => {
    const root = document.getElementById('root');
    if (root) {
      root.style.height = '100vh';
      root.style.overflow = 'hidden';
    }
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    return () => {
      if (root) {
        root.style.height = '';
        root.style.overflow = '';
      }
      document.documentElement.style.overflow = '';
      document.body.style.overflow = '';
    };
  }, []);

  // ── Agent management ──
  const switchAgent = useCallback(
    (agentId: string) => {
      loadingSessionRef.current = null;
      pendingSendRef.current = false;
      setCurrentAgentId(agentId);
      setCurrentSessionId(null);
      setIsLoadingSession(false);
      setValue('');
      setUploadedFiles([]);
      stopOutputMessage();
      setDone(true);
      resetAnswerList();
      setDerivedMessages([]);
      setSessions([]);
      localStorage.setItem('ragflow_agent_id', agentId);

      apiFetch(`/api/v1/agents/${agentId}`)
        .then((r) => r.json())
        .then((result) => {
          if (result.code === 0 && result.data) {
            const dsl = result.data.dsl;
            if (dsl && dsl.graph && dsl.graph.nodes) {
              const beginNode = dsl.graph.nodes.find(
                (n: any) => n.type === 'beginNode',
              );
              const prologue = beginNode?.data?.form?.prologue || '';
              setCurrentAgentPrologue(prologue);
            } else {
              setCurrentAgentPrologue('');
            }
          }
        })
        .catch(() => {
          setCurrentAgentPrologue('');
        });

      const userId =
        userInfo?.id || userInfo?.user_id || userInfo?.email || 'current';
      apiFetch(
        `/api/v1/agents/${agentId}/sessions?exp_user_id=${userId}&orderby=update_time&desc=true`,
      )
        .then((r) => r.json())
        .then((result) => {
          if (result.code === 0 && result.data) {
            setSessions(
              (result.data || []).map((s: any) => ({
                id: s.id,
                name: s.name || '新对话',
                update_time: s.update_time || s.create_date || Date.now(),
              })),
            );
          }
        })
        .catch(() => {});
    },
    [
      apiFetch,
      userInfo,
      setDerivedMessages,
      setValue,
      stopOutputMessage,
      setDone,
      resetAnswerList,
    ],
  );

  // Load agents
  useEffect(() => {
    if (!token) return;
    apiFetch('/api/v1/agents?page_size=100')
      .then((r) => r.json())
      .then((result) => {
        if (result.code !== 0) throw new Error(result.message);
        const list: Agent[] = result.data?.canvas || [];
        setAgents(list);
        if (list.length > 0) {
          const savedId = localStorage.getItem('ragflow_agent_id');
          const targetId =
            savedId && list.find((a) => a.id === savedId)
              ? savedId
              : list[0].id;
          switchAgent(targetId);
        }
      })
      .catch((e) => {
        console.error('加载智能体列表失败:', e);
        showToast('加载智能体列表失败');
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const loadSessions = useCallback(
    async (agentId?: string) => {
      const aid = agentId || currentAgentId;
      if (!aid) return;

      if (sessionLoadTimerRef.current) {
        clearTimeout(sessionLoadTimerRef.current);
      }

      sessionLoadTimerRef.current = setTimeout(async () => {
        sessionLoadTimerRef.current = null;
        try {
          const userId =
            userInfo?.id || userInfo?.user_id || userInfo?.email || 'current';
          const resp = await apiFetch(
            `/api/v1/agents/${aid}/sessions?exp_user_id=${userId}&orderby=update_time&desc=true`,
          );
          const result = await resp.json();
          if (result.code === 0 && result.data) {
            setSessions(
              (result.data || []).map((s: any) => ({
                id: s.id,
                name: s.name || '新对话',
                update_time: s.update_time || s.create_date || Date.now(),
              })),
            );
          }
        } catch (e) {
          console.warn('加载会话列表失败:', e);
        }
      }, 300);
    },
    [currentAgentId, userInfo, apiFetch],
  );

  const loadSessionMessages = useCallback(
    async (sessionId: string) => {
      // Clear any pending message load
      if (messageLoadTimerRef.current) {
        clearTimeout(messageLoadTimerRef.current);
      }

      loadingSessionRef.current = sessionId;
      setDerivedMessages([]);
      setIsLoadingSession(true);

      messageLoadTimerRef.current = setTimeout(async () => {
        messageLoadTimerRef.current = null;
        try {
          const resp = await apiFetch(
            `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
          );
          // If user clicked "new analysis" while loading, discard stale result
          if (loadingSessionRef.current !== sessionId) {
            return;
          }
          const result = await resp.json();
          if (result.code !== 0) throw new Error(result.message);
          const data = result.data;

          const rawMessages: any[] = data.messages || data.message || [];

          const mapped = rawMessages.map((m: any) => {
            let content = m.content || m.answer || '';

            // Convert ⋐...⋐ delimiters to <think>...</think> tags so they
            // go through the same replaceThinkToSection pipeline as real-time
            // streaming messages, producing identical visual output.
            content = content.replace(/⋐([\s\S]*?)⋐/g, '<think>$1</think>');

            // Convert legacy emoji think markers (🧠 ... 🤔) stored by older
            // canvas_service.py to proper <think> tags.
            content = content.replace(/🧠([\s\S]*?)🤔/g, '<think>$1</think>');

            // Convert per-message reference array to IReferenceObject
            let reference: IReferenceObject | undefined;
            const msgRef = m.reference;
            if (Array.isArray(msgRef) && msgRef.length > 0) {
              const chunks: Record<string, IReferenceChunk> = {};
              const docAggs: Record<string, Docagg> = {};
              msgRef.forEach((chunk: any, idx: number) => {
                chunks[idx] = {
                  id: chunk.id || String(idx),
                  content: chunk.content || '',
                  document_id: chunk.document_id || '',
                  document_name: chunk.document_name || '',
                  dataset_id: chunk.dataset_id || '',
                  image_id: chunk.image_id || '',
                  similarity: 0,
                  vector_similarity: 0,
                  term_similarity: 0,
                  positions: Array.isArray(chunk.positions)
                    ? chunk.positions
                    : [],
                } as IReferenceChunk;
                const docId = chunk.document_id;
                if (docId) {
                  if (!docAggs[docId]) {
                    docAggs[docId] = {
                      doc_id: docId,
                      doc_name: chunk.document_name || '',
                      count: 1,
                      url: '',
                    };
                  } else {
                    docAggs[docId].count++;
                  }
                }
              });
              reference = { chunks, doc_aggs: docAggs };
            }

            return {
              id: m.id || uuid(),
              role: m.role || 'assistant',
              content,
              reference,
              data: m.data,
            };
          }) as IMessage[];

          // Handle top-level reference (raw to_dict() format)
          // reference can be: array [{chunks, doc_aggs}, ...], dict with chunks key,
          // or dict with numeric keys {0: {...}, 1: {...}}
          const rawRef = data.reference;
          if (rawRef && typeof rawRef === 'object') {
            let refList: any[];
            if (Array.isArray(rawRef)) {
              refList = rawRef;
            } else if ('chunks' in rawRef) {
              refList = [rawRef];
            } else {
              refList = Object.entries(rawRef)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([, v]) => v);
            }
            const assistantIdxs = mapped
              .map((m, i) => (i !== 0 && m.role !== 'user' ? i : -1))
              .filter((i) => i >= 0);
            for (
              let j = 0;
              j < assistantIdxs.length && j < refList.length;
              j++
            ) {
              const mi = assistantIdxs[j];
              if (!mapped[mi].reference && refList[j]?.chunks) {
                const chunks: Record<string, IReferenceChunk> = {};
                const docAggs: Record<string, Docagg> = {};
                const rawChunks = refList[j].chunks;
                if (typeof rawChunks === 'object') {
                  Object.values(rawChunks).forEach((val: any, idx: number) => {
                    chunks[idx] = {
                      id: val.chunk_id || val.id || String(idx),
                      content: val.content_with_weight || val.content || '',
                      document_id: val.doc_id || val.document_id || '',
                      document_name: val.docnm_kwd || val.document_name || '',
                      dataset_id: val.kb_id || val.dataset_id || '',
                      image_id: val.image_id || val.img_id || '',
                      similarity: val.similarity || 0,
                      vector_similarity: val.vector_similarity || 0,
                      term_similarity: val.term_similarity || 0,
                      positions: Array.isArray(val.positions)
                        ? val.positions
                        : val.position_int || [],
                    } as IReferenceChunk;
                    const docId = val.doc_id || val.document_id;
                    if (docId && !docAggs[docId]) {
                      docAggs[docId] = {
                        doc_id: docId,
                        doc_name: val.docnm_kwd || val.document_name || '',
                        count: 1,
                        url: '',
                      };
                    }
                  });
                }
                if (refList[j].doc_aggs) {
                  Object.entries(refList[j].doc_aggs).forEach(
                    ([key, val]: [string, any]) => {
                      docAggs[key] = {
                        doc_id: val.doc_id || key,
                        doc_name: val.doc_name || '',
                        count: val.count || 0,
                        url: val.url || '',
                      };
                    },
                  );
                }
                if (Object.keys(chunks).length > 0) {
                  mapped[mi].reference = { chunks, doc_aggs: docAggs } as any;
                }
              }
            }
          }

          setDerivedMessages(mapped);
        } catch (e) {
          console.error('加载消息失败:', e);
          showToast('加载消息失败');
        } finally {
          setIsLoadingSession(false);
        }
      }, 200);
    },
    [currentAgentId, apiFetch, setDerivedMessages],
  );

  const switchSession = useCallback(
    (sessionId: string) => {
      pendingSendRef.current = false;
      setMainView('chat');
      setCurrentSessionId(sessionId);
      setValue('');
      setUploadedFiles([]);
      setFiles([]);
      stopOutputMessage();
      setDone(true);
      resetAnswerList();
      loadSessionMessages(sessionId);
    },
    [
      loadSessionMessages,
      setValue,
      stopOutputMessage,
      setDone,
      resetAnswerList,
    ],
  );

  const createNewSession = useCallback(() => {
    loadingSessionRef.current = null; // cancel any in-flight loadSessionMessages
    pendingSendRef.current = false;
    setMainView('chat');
    setCurrentSessionId(null);
    setIsLoadingSession(false);
    setValue('');
    setUploadedFiles([]);
    setFiles([]);
    stopOutputMessage();
    setDone(true);
    resetAnswerList();
    cachedNodeEventsRef.current = {}; // clear stale node events from previous session
    setCollapsedMessages(new Set());
    setNewSessionKey((k) => k + 1);
    setDerivedMessages(
      currentAgentPrologue
        ? [
            {
              id: uuid(),
              role: MessageType.Assistant,
              content: currentAgentPrologue,
            } as IMessage,
          ]
        : [],
    );
  }, [
    currentAgentPrologue,
    setDerivedMessages,
    setValue,
    stopOutputMessage,
    setDone,
    resetAnswerList,
  ]);

  const deleteSession = useCallback(
    async (sessionId: string) => {
      try {
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
          { method: 'DELETE' },
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        if (currentSessionId === sessionId) {
          setCurrentSessionId(null);
          setValue('');
          setUploadedFiles([]);
          setFiles([]);
          stopOutputMessage();
          setDone(true);
          resetAnswerList();
          setDerivedMessages([]);
        }
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      } catch (e: any) {
        showToast('删除失败: ' + e.message);
      }
    },
    [currentAgentId, currentSessionId, apiFetch, setDerivedMessages],
  );

  // ── File upload (C-side) ──
  const handleFileUpload: NonNullable<FileUploadProps['onUpload']> =
    useCallback(
      async (uploadFiles, { onProgress, onSuccess, onError }) => {
        for (const file of uploadFiles) {
          try {
            onProgress(file, 0);
            const formData = new FormData();
            formData.append('file', file);

            const resp = await fetch('/api/v1/documents/upload', {
              method: 'POST',
              headers: { Authorization: token },
              body: formData,
            });

            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const result = await resp.json();
            if (result.code === 0 && result.data) {
              const fileData = Array.isArray(result.data)
                ? result.data
                : [result.data];
              setUploadedFiles((prev) => [...prev, ...fileData]);
              onProgress(file, 100);
              onSuccess(file);
            } else {
              throw new Error(result.message || 'Unknown error');
            }
          } catch (e: any) {
            onError(file, e);
            showToast('文件上传失败: ' + e.message);
          }
        }
      },
      [token],
    );

  const removeUploadedFile = useCallback((file: File) => {
    setUploadedFiles((prev) => prev.filter((f) => f.name !== file.name));
  }, []);

  const handleFileReject = useCallback((_file: File, message: string) => {
    showToast(
      message === 'File too large'
        ? '文件超过50MB限制'
        : message === 'Maximum 10 files allowed'
          ? '最多上传10个文件'
          : message,
    );
  }, []);

  // ── Send message (B-side hooks based) ──
  const sendMessage = useCallback(
    async (query: string, sessionId: string | null, _msgId?: string) => {
      void _msgId;
      const currentFiles = [...uploadedFiles];
      const res = await send({
        agent_id: currentAgentId,
        query,
        session_id: sessionId,
        stream: true,
        files: currentFiles,
        internet: enableInternet,
      });

      if (
        res &&
        (res?.response.status !== 200 || (res?.data as any)?.code !== 0)
      ) {
        // cancel loading
        setValue(query);
        removeLatestMessage();
      }
    },
    [
      currentAgentId,
      uploadedFiles,
      send,
      setValue,
      removeLatestMessage,
      enableInternet,
    ],
  );

  const handlePressEnter = useCallback(async () => {
    console.log(
      '[SEND] handlePressEnter called, sendLoading=',
      sendLoading,
      'done=',
      done,
      'stoppedByUser=',
      stoppedByUser,
    );
    setStoppedByUser(false);
    // During IME composition, the controlled `value` hasn't been updated yet
    // — pull the real text directly from the DOM textarea
    const query =
      (composingRef.current
        ? textareaRef.current?.value?.trim()
        : value.trim()) || value.trim();
    if (sendingLockRef.current || !query || sendLoading) {
      console.log(
        '[SEND] blocked: sendingLock=',
        sendingLockRef.current,
        'query=',
        !!query,
        'sendLoading=',
        sendLoading,
      );
      return;
    }
    sendingLockRef.current = true;
    // Auto-release after 1s in case send() fails synchronously
    // (normal path: sendLoading→true→false resets it via the effect below)
    setTimeout(() => {
      sendingLockRef.current = false;
    }, 1000);

    // ── Instant feedback: show user message + loading skeleton before
    // any async work (session creation can take 1-2 seconds) ──
    const msgId = uuid();
    const currentFiles = [...uploadedFiles];

    addNewestOneQuestion({
      id: msgId,
      content: query,
      role: MessageType.User,
      files: currentFiles.length > 0 ? currentFiles : undefined,
    } as IMessage);

    setValue('');
    setUploadedFiles([]);
    setFiles([]);
    setDone(false); // triggers sendLoading → loading skeleton appears
    setTimeout(() => {
      virtuosoRef.current?.scrollToIndex({
        index: derivedMessages.length - 1,
        behavior: 'auto',
      });
    }, 50);

    let sessionId = currentSessionId;
    if (!sessionId) {
      try {
        const userId =
          userInfo?.id || userInfo?.user_id || userInfo?.email || 'current';
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions?user_id=${userId}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: query.slice(0, 30) }),
          },
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        sessionId = result.data.id;
        setCurrentSessionId(sessionId);
        setSessions((prev) => [
          {
            id: sessionId!,
            name: query.slice(0, 30),
            update_time: Date.now() / 1000,
          },
          ...prev,
        ]);
      } catch (e: any) {
        showToast('创建对话失败: ' + e.message);
        setDone(true); // hide loading skeleton
        setDerivedMessages((prev) => prev.filter((m) => m.id !== msgId));
        setValue(query); // restore input so user can retry
        return;
      }
    }

    sendMessage(query, sessionId, msgId);
  }, [
    value,
    sendLoading,
    currentSessionId,
    currentAgentId,
    userInfo,
    apiFetch,
    uploadedFiles,
    addNewestOneQuestion,
    setValue,
    sendMessage,
    scrollToBottom,
    setDone,
    setDerivedMessages,
  ]);

  // Once send() starts (sendLoading→true), release the debounce lock —
  // the sendLoading guard in handlePressEnter will block re-entry from here.
  useEffect(() => {
    if (sendLoading) {
      sendingLockRef.current = false;
    }
  }, [sendLoading]);

  // Fill textarea with transcribed text (no auto-send)
  useEffect(() => {
    if (audioInputValue !== null) {
      setValue(audioInputValue);
      setAudioInputValue(null);
      textareaRef.current?.focus();
    }
  }, [audioInputValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-send after regenerate sets inputValue
  useEffect(() => {
    if (pendingSendRef.current && value.trim() && !sendLoading && done) {
      pendingSendRef.current = false;
      handlePressEnter();
    }
  }, [value, sendLoading, done, handlePressEnter]);

  const handleLogout = () => {
    clearAuth();
  };

  // Redirect if no token
  useEffect(() => {
    if (!token) {
      navigate('/login');
    }
  }, [token, navigate]);

  // ── AgentChatContext value ──
  const agentChatContextValue = useMemo(
    () =>
      ({
        showLogSheet: () => {},
        setLastSendLoadingFunc: () => {},
        setDerivedMessages,
      }) as any,
    [setDerivedMessages],
  );

  // Stabilize Virtuoso props: prevent unnecessary internal recalculations
  // when latestNodeEvents changes but messages haven't.
  const handleFollowOutput = useCallback((isAtBottom: boolean) => {
    isAtBottomVirtuosoRef.current = isAtBottom;
    return isAtBottom ? 'auto' : false;
  }, []);

  const lastMsgRole = derivedMessages[derivedMessages.length - 1]?.role;
  const showSkeleton =
    sendLoading &&
    derivedMessages.length > 0 &&
    lastMsgRole === MessageType.User;

  const virtuosoComponents = useMemo(
    () => ({
      Header: () => <div className="h-6" />,
      Footer: () => (
        <>
          {showSkeleton && (
            <div className="flex justify-start cs-msg-enter gap-2 items-start max-w-[80rem] mx-auto mb-4">
              <RAGFlowAvatar
                name="标"
                avatar=""
                className="size-7 shrink-0 mt-0.5"
              />
              <div className="max-w-[85%]">
                <div className="bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md">
                  <div className="flex items-center gap-2 text-[#525252] text-sm py-1">
                    <Loader2
                      className="w-4 h-4 animate-spin text-[#A3A3A3]"
                      strokeWidth={3}
                    />
                    <span>正在生成中...</span>
                  </div>
                </div>
              </div>
            </div>
          )}
          {!sendLoading && stoppedByUser && (
            <div className="flex justify-start gap-2 items-start max-w-[80rem] mx-auto mb-4">
              <RAGFlowAvatar
                name="标"
                avatar=""
                className="size-7 shrink-0 mt-0.5"
              />
              <div className="max-w-[85%]">
                <div className="bg-white border border-[#F0F0F0] px-4 py-2.5 rounded-2xl rounded-bl-md">
                  <div className="flex items-center gap-1.5 text-xs text-[#A3A3A3]">
                    <CircleStop className="w-3.5 h-3.5" strokeWidth={2} />
                    <span>已停止生成</span>
                  </div>
                </div>
              </div>
            </div>
          )}
          <div ref={scrollRef} />
        </>
      ),
    }),
    [showSkeleton, scrollRef, sendLoading, stoppedByUser],
  );

  // ── Render ──
  if (!token) {
    return null;
  }

  const currentAgent = agents.find((a) => a.id === currentAgentId);
  const chatTitle = currentAgent?.title || '标书分析助手';

  return (
    <AgentChatContext.Provider value={agentChatContextValue}>
      <div className="h-screen flex flex-col cs-bg overflow-hidden">
        {/* Top Navigation Bar */}
        <header className="h-14 bg-white border-b border-[#D4D4D4] flex items-center px-6 shrink-0">
          {/* Left: Logo */}
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#14B8A6] flex items-center justify-center">
              <FileText className="w-4 h-4 text-white" strokeWidth={2} />
            </div>
            <span className="text-sm font-bold text-[#000000] hidden sm:inline">
              标书分析助手
            </span>
          </div>

          {/* Center: Module Tabs */}
          <div className="flex-1 flex justify-center">
            <div className="flex items-center gap-1 bg-[#EAEAEA] rounded-lg p-1">
              {(
                [
                  {
                    key: 'chat',
                    label: '对话',
                    icon: 'message-circle',
                  },
                  {
                    key: 'collaboration',
                    label: '协作',
                    icon: 'users',
                  },
                  {
                    key: 'tools',
                    label: '工具',
                    icon: 'wrench',
                  },
                  {
                    key: 'favorites',
                    label: '收藏',
                    icon: 'bookmark',
                  },
                  {
                    key: 'bid',
                    label: '标书',
                    icon: 'scroll-text',
                  },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => handleTabClick(tab.key)}
                  className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    mainView === tab.key
                      ? 'bg-white text-[#000000]'
                      : 'text-[#333333] hover:text-[#000000]'
                  }`}
                >
                  <DynamicIcon
                    name={tab.icon}
                    className="w-4 h-4"
                    strokeWidth={1.5}
                  />
                  <span className="hidden sm:inline">{tab.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Right: Download + User */}
          <div className="flex items-center gap-3">
            <CendTooltip title="下载App">
              <button
                onClick={() => setDownloadOpen(true)}
                className="flex items-center gap-1.5 text-sm font-medium text-[#0F172A] hover:text-[#0369A1] px-3 py-1.5 rounded-lg border border-[#E2E8F0] hover:border-[#0369A1] bg-white transition-colors cursor-pointer"
              >
                <Smartphone className="size-4" />
                <span className="hidden sm:inline">下载App</span>
              </button>
            </CendTooltip>
            <div className="w-8 h-8 rounded-lg bg-[#F59E0B] text-white flex items-center justify-center text-sm font-bold">
              {(userInfo?.nickname || userInfo?.email || 'U')[0].toUpperCase()}
            </div>
            <div className="hidden md:block">
              <div className="text-sm font-medium text-[#000000]">
                {userInfo?.nickname || userInfo?.email || ''}
              </div>
            </div>
            <CendTooltip title="退出登录">
              <button
                onClick={handleLogout}
                className="text-[#525252] hover:text-[#000000] transition-colors p-1.5 rounded-lg hover:bg-[#EAEAEA]"
              >
                <LogOut className="w-4 h-4" strokeWidth={1.5} />
              </button>
            </CendTooltip>
          </div>
        </header>

        {/* Main body: Sidebar + Content */}
        <div className="flex-1 flex min-h-0">
          {/* Mobile sidebar overlay */}
          {sidebarOpen && (
            <div
              className="fixed inset-0 bg-black/40 z-40 md:hidden"
              onClick={() => setSidebarOpen(false)}
            />
          )}

          {/* Sidebar */}
          {mainView === 'chat' && (
            <aside
              className={`fixed md:static inset-y-0 left-0 z-50 md:z-auto flex flex-col shrink-0 bg-white border-r border-[#D4D4D4] transition-all duration-300 ease-in-out overflow-hidden ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'} ${sidebarCollapsed ? 'w-0 border-r-0' : 'w-56'}`}
            >
              {/* Mobile close */}
              <div className="md:hidden h-12 flex items-center justify-between px-4 border-b border-[#D4D4D4] shrink-0">
                <span className="text-sm font-bold text-[#000000]">
                  标书分析助手
                </span>
                <button
                  className="text-[#333333] hover:text-[#000000]"
                  onClick={() => setSidebarOpen(false)}
                >
                  <X className="w-5 h-5" strokeWidth={2} />
                </button>
              </div>

              {/* New session button */}
              <div className="px-3 pt-3 pb-2">
                <button
                  onClick={() => {
                    setMainView('chat');
                    createNewSession();
                    setSidebarOpen(false);
                  }}
                  className="w-full flex items-center justify-center gap-2 bg-[#000000] hover:bg-[#000000] text-white py-2 rounded-lg transition-colors font-medium text-sm"
                >
                  <Plus className="w-4 h-4" strokeWidth={2} />
                  新对话
                </button>
              </div>

              {/* Agent selector */}
              {agents.length > 0 && (
                <div className="px-3 pb-2">
                  <div className="relative" ref={agentDropdownRef}>
                    <button
                      onClick={() => setAgentDropdownOpen(!agentDropdownOpen)}
                      className="w-full flex items-center justify-between bg-[#EAEAEA] text-[#000000] text-sm rounded-lg px-3 py-1.5 border border-[#D4D4D4] hover:border-[#000000] transition truncate"
                    >
                      <span className="truncate">
                        {agents.find((a) => a.id === currentAgentId)?.title ||
                          '选择智能体...'}
                      </span>
                      <ChevronDown
                        className={`w-3.5 h-3.5 shrink-0 ml-1.5 transition-transform ${agentDropdownOpen ? 'rotate-180' : ''}`}
                        strokeWidth={2}
                      />
                    </button>
                    {agentDropdownOpen && (
                      <div
                        className="absolute top-full left-0 right-0 mt-1 bg-white border border-[#D4D4D4] rounded-lg shadow-[0_4px_16px_rgba(0,0,0,0.08)] z-20 max-h-52 overflow-y-auto py-1"
                        style={{ scrollbarWidth: 'thin' }}
                      >
                        {agents.map((a) => (
                          <button
                            key={a.id}
                            onClick={() => {
                              switchAgent(a.id);
                              loadSessions(a.id);
                              setAgentDropdownOpen(false);
                            }}
                            className={`w-full text-left px-3 py-2 text-sm transition-colors truncate ${
                              a.id === currentAgentId
                                ? 'bg-[#EAEAEA] text-[#000000] font-medium'
                                : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
                            }`}
                          >
                            {a.title || '未命名智能体'}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Session list header */}
              <div className="px-4 pb-2">
                <span className="text-[#525252] text-[15px] font-semibold tracking-widest uppercase">
                  历史对话
                </span>
              </div>
              <div
                className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4"
                style={{ scrollbarWidth: 'none' }}
              >
                {sessions.length === 0 ? (
                  <div className="text-center text-[#525252]/40 text-xs py-10">
                    暂无对话
                  </div>
                ) : (
                  sessions.map((s, idx) => (
                    <div
                      key={s.id}
                      className={`cs-list-enter cs-list-d${Math.min(idx, 7)} w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group ${
                        s.id === currentSessionId
                          ? 'bg-[#EAEAEA] text-[#000000]'
                          : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
                      }`}
                      onClick={() => {
                        switchSession(s.id);
                        setSidebarOpen(false);
                      }}
                    >
                      <div
                        className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                          s.id === currentSessionId
                            ? 'bg-white'
                            : 'bg-[#EAEAEA]'
                        }`}
                      >
                        <MessageSquare
                          className={`w-4 h-4 ${s.id === currentSessionId ? 'text-[#000000]' : 'text-[#525252]'}`}
                          strokeWidth={1.5}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium truncate">
                            {s.name}
                          </span>
                          <button
                            className="hidden items-center justify-center w-5 h-5 rounded text-[#525252] hover:text-red-500 shrink-0 group-hover:flex"
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteTarget({ id: s.id, name: s.name });
                              setDeleteDialogOpen(true);
                            }}
                          >
                            <Trash2 className="w-3 h-3" strokeWidth={2} />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </aside>
          )}

          {/* Delete session confirmation */}
          {deleteDialogOpen && (
            <div className="fixed inset-0 z-50 flex items-center justify-center">
              <div
                className="fixed inset-0 bg-black/40"
                onClick={() => setDeleteDialogOpen(false)}
              />
              <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
                <h2 className="text-lg font-semibold text-[#1A1A1A]">
                  确认删除此对话？
                </h2>
                <p className="text-sm text-[#8A8A8A] mt-2">
                  确定要删除对话「{deleteTarget?.name}」吗？此操作不可撤销。
                </p>
                <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6">
                  <button
                    onClick={() => {
                      setDeleteTarget(null);
                      setDeleteDialogOpen(false);
                    }}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A] mt-2 sm:mt-0"
                  >
                    取消
                  </button>
                  <button
                    onClick={() => {
                      if (deleteTarget) {
                        deleteSession(deleteTarget.id);
                        setDeleteTarget(null);
                        setDeleteDialogOpen(false);
                      }
                    }}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 bg-red-700 hover:bg-red-800 text-white"
                  >
                    确认删除
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Sidebar toggle (desktop only) */}
          {mainView === 'chat' && (
            <CendTooltip title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}>
              <button
                onClick={() => setSidebarCollapsed((c) => !c)}
                className="shrink-0 self-start mt-6 -ml-3.5 z-10 size-7 hidden md:flex items-center justify-center rounded-full border-2 border-[#D4D4D4] bg-white text-[#525252] hover:text-[#000000] hover:border-[#A3A3A3] hover:shadow-[0_2px_8px_rgba(0,0,0,0.12)] transition-all cursor-pointer"
              >
                {sidebarCollapsed ? (
                  <ChevronRight className="size-3.5" strokeWidth={2} />
                ) : (
                  <ChevronRight
                    className="size-3.5 rotate-180"
                    strokeWidth={2}
                  />
                )}
              </button>
            </CendTooltip>
          )}

          {/* Main Content Area */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Chat View */}
            <div
              key={getTabResetKey('chat')}
              className={
                mainView === 'chat'
                  ? 'cs-page-enter flex-1 flex flex-col min-h-0 relative'
                  : 'hidden'
              }
            >
              {/* Header */}
              <div className="h-14 bg-white border-b border-[#D4D4D4] flex items-center px-4 shrink-0">
                <button
                  className="md:hidden mr-2 p-1.5 rounded-lg hover:bg-[#EAEAEA] transition"
                  onClick={() => setSidebarOpen(true)}
                >
                  <Menu className="w-5 h-5 text-[#333333]" strokeWidth={2} />
                </button>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-[#000000]">
                    {chatTitle}
                  </h2>
                  <span className="w-1.5 h-1.5 rounded-full bg-[#2ec4b6] animate-pulse" />
                </div>
                <div className="flex-1" />
                {derivedMessages.length > 0 && (
                  <button
                    onClick={() => {
                      setIsSaveAllFavorites(true);
                      setFavoriteDialogOpen(true);
                    }}
                    className="flex items-center gap-1.5 text-sm font-semibold text-[#525252] hover:text-[#000000] px-2 py-1 rounded-lg hover:bg-[#EAEAEA] transition-colors cursor-pointer"
                  >
                    <Bookmark className="size-4" strokeWidth={2} />
                    收藏对话
                  </button>
                )}
              </div>

              {/* Messages Area + Input */}
              {derivedMessages.length === 0 && !isLoadingSession ? (
                /* Empty state */
                <div className="flex-1 flex items-center justify-center min-h-0 px-4">
                  <div className="w-full max-w-3xl cs-input-enter">
                    <div className="max-w-md mx-auto">
                      {/* Floating icon with glow */}
                      <div className="text-center mb-8">
                        <div className="cs-float cs-glow w-16 h-16 bg-white border border-[#EBEBEB] rounded-2xl mx-auto flex items-center justify-center mb-5">
                          <MessageSquare
                            className="w-7 h-7 text-[#1A1A1A]"
                            strokeWidth={1}
                          />
                        </div>
                        <h2 className="text-[15px] font-semibold text-[#1A1A1A] tracking-tight">
                          选择或创建一个对话开始分析
                        </h2>
                        <p className="text-[13px] text-[#8A8A8A] mt-1.5">
                          上传招标文件至知识库后，即可在此进行智能问答
                        </p>
                      </div>

                      {/* Feature cards */}
                      <div className="grid grid-cols-2 gap-3">
                        {[
                          {
                            icon: 'file-search-2',
                            label: '招标文件解析',
                            color: '#2ec4b6',
                            bg: '#e6f9f7',
                          },
                          {
                            icon: 'bar-chart-3',
                            label: '智能评分分析',
                            color: '#f59e0b',
                            bg: '#fef9e7',
                          },
                          {
                            icon: 'file-search-2',
                            label: '关键信息提取',
                            color: '#4f8ce8',
                            bg: '#eef4ff',
                          },
                          {
                            icon: 'shield-check',
                            label: '合规性检查',
                            color: '#8b5cf6',
                            bg: '#f3f0ff',
                          },
                        ].map((card, idx) => (
                          <div
                            key={card.label}
                            className={`cs-card-enter cs-card-d${idx + 1} group flex items-center gap-3 px-4 py-3.5 rounded-xl border border-[#F0F0F0] bg-white hover:border-[#D4D4D4] hover:shadow-[0_4px_20px_rgba(0,0,0,0.05)] hover:-translate-y-0.5 transition-all duration-300 cursor-pointer`}
                          >
                            <div
                              className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 group-hover:scale-110 transition-transform duration-300"
                              style={{ backgroundColor: card.bg }}
                            >
                              <DynamicIcon
                                name={card.icon}
                                className="w-4 h-4"
                                strokeWidth={1.5}
                                color={card.color}
                              />
                            </div>
                            <span className="text-xs font-medium text-[#555555] group-hover:text-[#1A1A1A] group-hover:scale-105 transition-all duration-300">
                              {card.label}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                    {/* File chips + Input */}
                    <div className="mt-5">
                      <FileUpload
                        value={files}
                        onValueChange={setFiles}
                        onUpload={handleFileUpload}
                        className="w-full"
                        disabled={sendLoading}
                        multiple
                        maxFiles={10}
                        maxSize={50 * 1024 * 1024}
                        onFileReject={handleFileReject}
                      >
                        <FileUploadDropzone
                          tabIndex={-1}
                          onClick={(event) => event.preventDefault()}
                          className="absolute top-0 left-0 z-0 flex size-full items-center justify-center rounded-none border-none bg-background/50 p-0 opacity-0 pointer-events-none backdrop-blur transition-opacity duration-200 ease-out data-[dragging]:z-10 data-[dragging]:opacity-100 data-[dragging]:pointer-events-auto"
                        >
                          <div className="flex flex-col items-center gap-1 text-center">
                            <div className="flex items-center justify-center rounded-full border p-2.5">
                              <Upload className="size-6 text-muted-foreground" />
                            </div>
                            <p className="font-medium text-sm text-foreground">
                              拖拽文件到此处上传
                            </p>
                            <p className="text-muted-foreground text-xs">
                              最多上传10个文件，每个不超过50MB
                            </p>
                          </div>
                        </FileUploadDropzone>

                        <div
                          className="cs-input-ring relative flex flex-col gap-2 bg-[#FFFFFF] border border-[#D4D4D4] rounded-2xl px-4 py-3"
                          onDragOver={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                          }}
                          onDrop={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            const fileInput = e.currentTarget
                              .closest('[data-slot="file-upload"]')
                              ?.querySelector(
                                'input[type="file"]',
                              ) as HTMLInputElement;
                            if (fileInput && e.dataTransfer.files.length > 0) {
                              const dt = new DataTransfer();
                              for (const f of e.dataTransfer.files)
                                dt.items.add(f);
                              fileInput.files = dt.files;
                              fileInput.dispatchEvent(
                                new Event('change', { bubbles: true }),
                              );
                            }
                          }}
                          onPaste={(e) => {
                            const items = e.clipboardData?.items;
                            if (!items) return;
                            const fileItems: File[] = [];
                            for (let i = 0; i < items.length; i++) {
                              const file = items[i].getAsFile?.();
                              if (file) fileItems.push(file);
                            }
                            if (!fileItems.length) return;
                            e.preventDefault();
                            e.stopPropagation();
                            const fileInput = e.currentTarget
                              .closest('[data-slot="file-upload"]')
                              ?.querySelector(
                                'input[type="file"]',
                              ) as HTMLInputElement;
                            if (fileInput) {
                              const dt = new DataTransfer();
                              for (const f of fileItems) dt.items.add(f);
                              fileInput.files = dt.files;
                              fileInput.dispatchEvent(
                                new Event('change', { bubbles: true }),
                              );
                            }
                          }}
                        >
                          {files.length > 0 && (
                            <FileUploadList
                              orientation="horizontal"
                              className="overflow-x-auto px-0 py-1"
                            >
                              {files.map((file, index) => (
                                <FileUploadItem
                                  key={index}
                                  value={file}
                                  className="max-w-none w-fit p-1 pr-4 gap-1.5 rounded-lg border border-[#E8E8E8]"
                                >
                                  <FileUploadItemPreview className="size-6 [&>svg]:size-3.5 [&>svg]:text-[#525252]">
                                    <FileUploadItemProgress variant="fill" />
                                  </FileUploadItemPreview>
                                  <FileUploadItemMetadata
                                    size="sm"
                                    className="[&_span:first-child]:text-[#000000]"
                                  />
                                  <FileUploadItemDelete asChild>
                                    <button
                                      className="-top-1 -right-1 absolute size-4 shrink-0 cursor-pointer rounded-full bg-gray-200 hover:bg-gray-300 flex items-center justify-center"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        removeUploadedFile(file);
                                      }}
                                    >
                                      <X className="size-2.5" />
                                    </button>
                                  </FileUploadItemDelete>
                                </FileUploadItem>
                              ))}
                            </FileUploadList>
                          )}
                          <Textarea
                            ref={textareaRef}
                            value={value}
                            onChange={handleInputChange}
                            onCompositionStart={() => {
                              composingRef.current = true;
                              setComposing(true);
                            }}
                            onCompositionEnd={(
                              e: React.CompositionEvent<HTMLTextAreaElement>,
                            ) => {
                              composingRef.current = false;
                              setComposing(false);
                              const finalValue = (
                                e.target as HTMLTextAreaElement
                              ).value;
                              setValue(finalValue);
                            }}
                            onKeyDown={(
                              e: React.KeyboardEvent<HTMLTextAreaElement>,
                            ) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handlePressEnter();
                              }
                            }}
                            placeholder={typewriterText}
                            className="min-h-[72px] w-full p-0 overflow-auto !outline-none !border-transparent !bg-transparent !shadow-none !ring-transparent !ring-offset-transparent !text-[#000000] cs-typewriter-cursor"
                            style={{ color: '#000000' }}
                            autoSize={{ minRows: 3, maxRows: 10 }}
                            autoFocus
                          />
                          <div className="flex items-center justify-end gap-2">
                            <div className="shrink-0 w-9 h-9 flex items-center justify-center">
                              <RealtimeAudioButton
                                onTranscript={(val) => setAudioInputValue(val)}
                                testId="c-chat-audio-toggle"
                              />
                            </div>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <FileUploadTrigger asChild>
                                  <button
                                    disabled={sendLoading}
                                    className="shrink-0 w-9 h-9 flex items-center justify-center text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                                  >
                                    <Paperclip
                                      className="w-4 h-4"
                                      strokeWidth={2}
                                    />
                                  </button>
                                </FileUploadTrigger>
                              </TooltipTrigger>
                              <TooltipContent
                                side="top"
                                className="bg-[#1A1A1A] text-[#F5F5F4] border-[#333333] text-xs px-3 py-1.5 rounded-lg shadow-lg"
                              >
                                <p>上传文件（最多10个，每个不超过50MB）</p>
                              </TooltipContent>
                            </Tooltip>
                            {!sendLoading ? (
                              <button
                                onClick={handlePressEnter}
                                disabled={!value.trim() && !composing}
                                className="shrink-0 size-9 flex items-center justify-center bg-[#1A1A1A] hover:bg-[#333333] text-white rounded-full transition-colors disabled:opacity-30 disabled:cursor-not-allowed relative z-10"
                              >
                                <Send className="w-4 h-4" strokeWidth={2} />
                              </button>
                            ) : (
                              <button
                                onClick={stopConversation}
                                className="shrink-0 size-9 flex items-center justify-center bg-white border-2 border-[#1A1A1A] text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-full transition-colors relative z-10"
                              >
                                <Square
                                  className="w-3 h-3"
                                  fill="currentColor"
                                  stroke="none"
                                />
                              </button>
                            )}
                          </div>
                        </div>
                      </FileUpload>
                    </div>
                  </div>
                </div>
              ) : (
                /* Non-empty state: messages + bottom input */
                <>
                  {isLoadingSession ? (
                    <div className="flex-1 flex items-center justify-center">
                      <Loader2
                        className="w-7 h-7 animate-spin text-[#000000] mb-4"
                        strokeWidth={3}
                      />
                      <p className="text-[#333333] text-sm">加载对话中...</p>
                    </div>
                  ) : (
                    <Virtuoso
                      ref={virtuosoRef}
                      key={newSessionKey}
                      className="flex-1 px-4 lg:px-6 pb-4"
                      style={{ overflowX: 'hidden', scrollbarWidth: 'thin' }}
                      data={derivedMessages}
                      followOutput={handleFollowOutput}
                      initialTopMostItemIndex={Math.max(
                        0,
                        derivedMessages.length - 1,
                      )}
                      itemContent={(index, msg) => {
                        const i = index;
                        const isLast = i === derivedMessages.length - 1;
                        const streaming = isLast && sendLoading;
                        const refs: IReferenceObject | undefined =
                          findReferenceByMessageId(msg.id) ??
                          (msg.reference as any);

                        if (msg.role === MessageType.User) {
                          const userName =
                            userInfo?.nickname || userInfo?.email || 'U';
                          return (
                            <div className="max-w-[80rem] mx-auto mb-4">
                              <div className="flex justify-end cs-msg-enter gap-2 items-start">
                                <div className="max-w-[85%]">
                                  {favoriteMode && (
                                    <div className="flex justify-end mb-1.5">
                                      <button
                                        className={`shrink-0 size-5 rounded border-2 flex items-center justify-center transition-colors ${
                                          selectedMessageIds.has(
                                            msgSelectKey(msg),
                                          )
                                            ? 'bg-[#6366f1] border-[#6366f1] text-white'
                                            : 'border-[#A3A3A3] bg-white hover:border-[#6366f1]'
                                        }`}
                                        onClick={() => toggleMessagePair(i)}
                                      >
                                        {selectedMessageIds.has(
                                          msgSelectKey(msg),
                                        ) && (
                                          <Check
                                            className="w-3 h-3"
                                            strokeWidth={3}
                                          />
                                        )}
                                      </button>
                                    </div>
                                  )}
                                  <div className="bg-[#000000] text-white px-4 py-2.5 rounded-2xl rounded-br-md text-[16px] leading-relaxed tracking-wider">
                                    {msg.content}
                                    {msg.files &&
                                      (msg.files as any[]).length > 0 && (
                                        <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-white/20">
                                          {(msg.files as any[]).map(
                                            (f: any) => (
                                              <span
                                                key={f.id}
                                                className="inline-flex items-center gap-1 px-2 py-0.5 bg-white/15 rounded-md text-[11px] text-white/90"
                                              >
                                                <FileText
                                                  className="w-3 h-3 shrink-0"
                                                  strokeWidth={2}
                                                />
                                                {f.name}
                                              </span>
                                            ),
                                          )}
                                        </div>
                                      )}
                                  </div>
                                  {msg.content && (
                                    <div className="mt-1 flex justify-end">
                                      <button
                                        className="flex items-center gap-1 px-2.5 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                                        onClick={async () => {
                                          const ok = await copyToClipboard(
                                            msg.content || '',
                                          );
                                          if (ok) {
                                            setCopiedIndex(i);
                                            setTimeout(
                                              () => setCopiedIndex(null),
                                              2000,
                                            );
                                          }
                                        }}
                                      >
                                        {copiedIndex === i ? (
                                          <Check
                                            className="w-3.5 h-3.5"
                                            strokeWidth={2}
                                          />
                                        ) : (
                                          <Copy
                                            className="w-3.5 h-3.5"
                                            strokeWidth={2}
                                          />
                                        )}
                                        {copiedIndex === i ? '已复制' : '复制'}
                                      </button>
                                      <CendTooltip title="重新生成">
                                        <button
                                          className="flex items-center gap-1 px-2.5 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                                          disabled={sendLoading}
                                          onClick={() => {
                                            setValue(msg.content);
                                            // Remove this and later messages, then re-send
                                            setDerivedMessages((prev) =>
                                              prev.slice(0, i),
                                            );
                                            pendingSendRef.current = true;
                                          }}
                                        >
                                          <RefreshCw
                                            className="w-3.5 h-3.5"
                                            strokeWidth={2}
                                          />
                                          重新生成
                                        </button>
                                      </CendTooltip>
                                    </div>
                                  )}
                                </div>
                                <div className="size-7 rounded-lg bg-[#F59E0B] text-white flex items-center justify-center text-xs font-bold shrink-0">
                                  {userName[0].toUpperCase()}
                                </div>
                              </div>
                            </div>
                          );
                        }

                        // Assistant message
                        const isCurrentlyThinking =
                          streaming &&
                          /<think>/.test(msg.content || '') &&
                          !/<\/think>/.test(msg.content || '');

                        const isSelected =
                          favoriteMode &&
                          selectedMessageIds.has(msgSelectKey(msg));
                        return (
                          <div className="max-w-[80rem] mx-auto mb-4">
                            <div
                              data-msg-id={msg.id}
                              className="flex justify-start cs-msg-enter gap-2 items-start"
                            >
                              {favoriteMode && (
                                <button
                                  className={`shrink-0 size-5 mt-1 rounded border-2 flex items-center justify-center transition-colors ${
                                    isSelected
                                      ? 'bg-[#6366f1] border-[#6366f1] text-white'
                                      : 'border-[#A3A3A3] bg-white hover:border-[#6366f1]'
                                  }`}
                                  onClick={() => toggleMessagePair(i)}
                                >
                                  {isSelected && (
                                    <Check
                                      className="w-3 h-3"
                                      strokeWidth={3}
                                    />
                                  )}
                                </button>
                              )}
                              <RAGFlowAvatar
                                name="标"
                                avatar=""
                                className="size-7 shrink-0 mt-0.5"
                              />
                              <div className="max-w-[85%]">
                                <div className="bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md text-[15px] leading-relaxed tracking-wider text-[#000000]">
                                  {collapsedMessages.has(msg.id || '') &&
                                  !streaming ? (
                                    <div className="text-[#555555]">
                                      {(msg.content || '')
                                        .replace(
                                          /<think\b[^>]*>[\s\S]*?<\/think>/gi,
                                          '',
                                        )
                                        // eslint-disable-next-line no-useless-escape
                                        .replace(/[#*`>_~\[\]]/g, '')
                                        .split('\n')[0]
                                        .slice(0, 120)}
                                      ...
                                    </div>
                                  ) : (
                                    <div className="msg-content text-[#000000]">
                                      <MarkdownErrorBoundary>
                                        <ChapteredMarkdown
                                          content={msg.content || ''}
                                          loading={streaming}
                                          reference={refs}
                                          clickDocumentButton={
                                            clickDocumentButton
                                          }
                                        />
                                      </MarkdownErrorBoundary>
                                    </div>
                                  )}
                                  {streaming && (
                                    <span className="inline-block w-2 h-4 bg-[#000000] ml-1 animate-pulse rounded-sm" />
                                  )}
                                  {streaming && isCurrentlyThinking && (
                                    <div className="flex items-center gap-2 text-[#525252] text-xs py-1">
                                      <Loader2
                                        className="w-3.5 h-3.5 animate-spin text-[#A3A3A3]"
                                        strokeWidth={3}
                                      />
                                      <span>正在思考中...</span>
                                    </div>
                                  )}
                                  {isLast &&
                                    (console.log(
                                      '[RENDER.INDICATOR] isLast=true streaming=',
                                      streaming,
                                      'stoppedByUser=',
                                      stoppedByUser,
                                      'sendLoading=',
                                      sendLoading,
                                      'msgHasContent=',
                                      !!(msg.content || ''),
                                    ),
                                    null)}
                                  {!streaming && isLast && stoppedByUser && (
                                    <div className="flex items-center gap-1.5 mt-2 pt-2 border-t border-[#E5E5E5] text-xs text-[#A3A3A3]">
                                      <CircleStop
                                        className="w-3.5 h-3.5"
                                        strokeWidth={2}
                                      />
                                      <span>已停止生成</span>
                                    </div>
                                  )}
                                </div>
                                {/* Reference sources */}
                                {!streaming && refs && (
                                  <div className="mt-3 space-y-2">
                                    <ReferenceImageList
                                      referenceChunks={refs.chunks}
                                      messageContent={msg.content || ''}
                                    />
                                    {Object.values(refs.doc_aggs || {}).length >
                                      0 && (
                                      <div className="border-t border-gray-200 pt-2">
                                        <button
                                          className="flex items-center gap-1 text-xs text-gray-500 mb-1.5 font-medium hover:text-gray-700 transition-colors w-full"
                                          onClick={() => {
                                            setExpandedSections((prev) => {
                                              const next = new Set(prev);
                                              if (next.has(msg.id)) {
                                                next.delete(msg.id);
                                              } else {
                                                next.add(msg.id);
                                              }
                                              return next;
                                            });
                                          }}
                                        >
                                          <ChevronRight
                                            className="w-3 h-3 transition-transform"
                                            style={{
                                              transform: expandedSections.has(
                                                msg.id,
                                              )
                                                ? 'rotate(90deg)'
                                                : 'rotate(0deg)',
                                            }}
                                            strokeWidth={2}
                                          />
                                          引用来源 (
                                          {
                                            Object.values(refs.doc_aggs || {})
                                              .length
                                          }
                                          )
                                        </button>
                                        {expandedSections.has(msg.id) && (
                                          <ReferenceDocumentList
                                            list={Object.values(
                                              refs.doc_aggs || {},
                                            )}
                                          />
                                        )}
                                      </div>
                                    )}
                                  </div>
                                )}
                                {/* Downloads */}
                                {!streaming &&
                                  msg.downloads &&
                                  (msg.downloads as any[]).length > 0 && (
                                    <div className="mt-2 space-y-1.5">
                                      {(msg.downloads as any[]).map(
                                        (dl: any, di: number) => (
                                          <a
                                            key={di}
                                            href={dl.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="flex items-center gap-2 px-3 py-2 bg-[#F5F5F5] border border-[#E5E5E5] rounded-lg text-xs text-[#000000] hover:bg-[#EAEAEA] transition-colors"
                                          >
                                            <Download
                                              className="w-3.5 h-3.5 shrink-0"
                                              strokeWidth={2}
                                            />
                                            <span className="truncate">
                                              {dl.name || '下载文件'}
                                            </span>
                                          </a>
                                        ),
                                      )}
                                    </div>
                                  )}
                                {/* Copy + Collab buttons on assistant messages */}
                                {!streaming && msg.content && (
                                  <div className="mt-2 flex justify-end gap-1">
                                    <button
                                      className="flex items-center gap-1 px-2.5 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                                      onClick={() =>
                                        toggleMessageCollapse(msg.id || '')
                                      }
                                    >
                                      <ChevronDown
                                        className={`w-3.5 h-3.5 transition-transform ${
                                          collapsedMessages.has(msg.id || '')
                                            ? ''
                                            : 'rotate-180'
                                        }`}
                                        strokeWidth={2}
                                      />
                                      {collapsedMessages.has(msg.id || '')
                                        ? '展开'
                                        : '收起'}
                                    </button>
                                    <button
                                      className="flex items-center gap-1 px-2.5 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                                      onClick={async () => {
                                        const ok = await copyToClipboard(
                                          msg.content || '',
                                        );
                                        if (ok) {
                                          setCopiedIndex(i);
                                          setTimeout(
                                            () => setCopiedIndex(null),
                                            2000,
                                          );
                                        }
                                      }}
                                    >
                                      {copiedIndex === i ? (
                                        <Check
                                          className="w-3.5 h-3.5"
                                          strokeWidth={2}
                                        />
                                      ) : (
                                        <Copy
                                          className="w-3.5 h-3.5"
                                          strokeWidth={2}
                                        />
                                      )}
                                      {copiedIndex === i ? '已复制' : '复制'}
                                    </button>
                                    <button
                                      className="flex items-center gap-1 px-2.5 py-1 text-xs text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                                      onClick={() => {
                                        setCollabMessage(msg.content);
                                        setCollabDialogOpen(true);
                                      }}
                                    >
                                      <FileText
                                        className="w-3.5 h-3.5"
                                        strokeWidth={2}
                                      />
                                      协作
                                    </button>
                                    <button
                                      className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg transition-colors text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA]"
                                      onClick={() => {
                                        setSelectedMessageIds(
                                          new Set([msgSelectKey(msg)]),
                                        );
                                        setIsSaveAllFavorites(false);
                                        setFavoriteDialogOpen(true);
                                      }}
                                    >
                                      <Star
                                        className="w-3.5 h-3.5"
                                        fill="none"
                                        strokeWidth={2}
                                      />
                                      收藏
                                    </button>
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      }}
                      components={virtuosoComponents}
                    />
                  )}

                  {/* Stopped indicator — OUTSIDE Virtuoso so it always renders */}
                  {!sendLoading && stoppedByUser && (
                    <div className="flex justify-start gap-2 items-start max-w-[80rem] mx-auto mb-4 px-4 lg:px-6">
                      <RAGFlowAvatar
                        name="标"
                        avatar=""
                        className="size-7 shrink-0 mt-0.5"
                      />
                      <div className="max-w-[85%]">
                        <div className="bg-white border border-[#F0F0F0] px-4 py-2.5 rounded-2xl rounded-bl-md">
                          <div className="flex items-center gap-1.5 text-xs text-[#A3A3A3]">
                            <CircleStop
                              className="w-3.5 h-3.5"
                              strokeWidth={2}
                            />
                            <span>已停止生成</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Floating agent status chip — overlays messages on the left */}
                  {latestNodeEvents && (
                    <div className="absolute left-1 top-1/2 -translate-y-1/2 z-50 max-w-[200px]">
                      <AgentStatusChip
                        eventList={latestNodeEvents.events}
                        isRunning={sendLoading}
                      />
                    </div>
                  )}

                  <div className="px-3 lg:px-4 pb-3 lg:pb-4 shrink-0">
                    <div className="max-w-3xl mx-auto">
                      <FileUpload
                        value={files}
                        onValueChange={setFiles}
                        onUpload={handleFileUpload}
                        className="w-full"
                        disabled={sendLoading}
                        multiple
                        maxFiles={10}
                        maxSize={50 * 1024 * 1024}
                        onFileReject={handleFileReject}
                      >
                        <FileUploadDropzone
                          tabIndex={-1}
                          onClick={(event) => event.preventDefault()}
                          className="absolute top-0 left-0 z-0 flex size-full items-center justify-center rounded-none border-none bg-background/50 p-0 opacity-0 pointer-events-none backdrop-blur transition-opacity duration-200 ease-out data-[dragging]:z-10 data-[dragging]:opacity-100 data-[dragging]:pointer-events-auto"
                        >
                          <div className="flex flex-col items-center gap-1 text-center">
                            <div className="flex items-center justify-center rounded-full border p-2.5">
                              <Upload className="size-6 text-muted-foreground" />
                            </div>
                            <p className="font-medium text-sm text-foreground">
                              拖拽文件到此处上传
                            </p>
                            <p className="text-muted-foreground text-xs">
                              最多上传10个文件，每个不超过50MB
                            </p>
                          </div>
                        </FileUploadDropzone>

                        <div
                          className="cs-input-ring relative flex flex-col gap-2 bg-white border border-[#E8E8E8] rounded-2xl px-3 py-2 shadow-[0_1px_6px_rgba(63,91,141,0.04)] transition-all duration-200 hover:border-[#D0D0D0] hover:shadow-[0_2px_12px_rgba(63,91,141,0.06)]"
                          onDragOver={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                          }}
                          onDrop={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            const fileInput = e.currentTarget
                              .closest('[data-slot="file-upload"]')
                              ?.querySelector(
                                'input[type="file"]',
                              ) as HTMLInputElement;
                            if (fileInput && e.dataTransfer.files.length > 0) {
                              const dt = new DataTransfer();
                              for (const f of e.dataTransfer.files)
                                dt.items.add(f);
                              fileInput.files = dt.files;
                              fileInput.dispatchEvent(
                                new Event('change', { bubbles: true }),
                              );
                            }
                          }}
                          onPaste={(e) => {
                            const items = e.clipboardData?.items;
                            if (!items) return;
                            const fileItems: File[] = [];
                            for (let i = 0; i < items.length; i++) {
                              const file = items[i].getAsFile?.();
                              if (file) fileItems.push(file);
                            }
                            if (!fileItems.length) return;
                            e.preventDefault();
                            e.stopPropagation();
                            const fileInput = e.currentTarget
                              .closest('[data-slot="file-upload"]')
                              ?.querySelector(
                                'input[type="file"]',
                              ) as HTMLInputElement;
                            if (fileInput) {
                              const dt = new DataTransfer();
                              for (const f of fileItems) dt.items.add(f);
                              fileInput.files = dt.files;
                              fileInput.dispatchEvent(
                                new Event('change', { bubbles: true }),
                              );
                            }
                          }}
                        >
                          {files.length > 0 && (
                            <FileUploadList
                              orientation="horizontal"
                              className="overflow-x-auto px-0 py-1"
                            >
                              {files.map((file, index) => (
                                <FileUploadItem
                                  key={index}
                                  value={file}
                                  className="max-w-none w-fit p-1 pr-4 gap-1.5 rounded-lg border border-[#E8E8E8]"
                                >
                                  <FileUploadItemPreview className="size-6 [&>svg]:size-3.5 [&>svg]:text-[#525252]">
                                    <FileUploadItemProgress variant="fill" />
                                  </FileUploadItemPreview>
                                  <FileUploadItemMetadata
                                    size="sm"
                                    className="[&_span:first-child]:text-[#000000]"
                                  />
                                  <FileUploadItemDelete asChild>
                                    <button
                                      className="-top-1 -right-1 absolute size-4 shrink-0 cursor-pointer rounded-full bg-gray-200 hover:bg-gray-300 flex items-center justify-center"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        removeUploadedFile(file);
                                      }}
                                    >
                                      <X className="size-2.5" />
                                    </button>
                                  </FileUploadItemDelete>
                                </FileUploadItem>
                              ))}
                            </FileUploadList>
                          )}
                          <Textarea
                            ref={textareaRef}
                            value={value}
                            onChange={handleInputChange}
                            onCompositionStart={() => {
                              composingRef.current = true;
                              setComposing(true);
                            }}
                            onCompositionEnd={(
                              e: React.CompositionEvent<HTMLTextAreaElement>,
                            ) => {
                              composingRef.current = false;
                              setComposing(false);
                              const finalValue = (
                                e.target as HTMLTextAreaElement
                              ).value;
                              setValue(finalValue);
                            }}
                            onKeyDown={(
                              e: React.KeyboardEvent<HTMLTextAreaElement>,
                            ) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handlePressEnter();
                              }
                            }}
                            placeholder={
                              hasMessages
                                ? '继续输入您的问题...'
                                : typewriterText
                            }
                            className={`min-h-[72px] w-full p-0 overflow-auto !outline-none !border-transparent !bg-transparent !shadow-none !ring-transparent !ring-offset-transparent !text-[#000000]${hasMessages ? '' : ' cs-typewriter-cursor'}`}
                            style={{ color: '#000000' }}
                            autoSize={{ minRows: 3, maxRows: 10 }}
                          />
                          <div className="flex items-center justify-end gap-2">
                            {!sendLoading ? (
                              <>
                                <div className="shrink-0 w-9 h-9 flex items-center justify-center">
                                  <RealtimeAudioButton
                                    onTranscript={(val) =>
                                      setAudioInputValue(val)
                                    }
                                    testId="c-chat-audio-toggle"
                                  />
                                </div>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <FileUploadTrigger asChild>
                                      <button
                                        disabled={sendLoading}
                                        className="shrink-0 w-9 h-9 flex items-center justify-center text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                                      >
                                        <Paperclip
                                          className="w-4 h-4"
                                          strokeWidth={2}
                                        />
                                      </button>
                                    </FileUploadTrigger>
                                  </TooltipTrigger>
                                  <TooltipContent
                                    side="top"
                                    className="bg-[#1A1A1A] text-[#F5F5F4] border-[#333333] text-xs px-3 py-1.5 rounded-lg shadow-lg"
                                  >
                                    <p>上传文件（最多10个，每个不超过50MB）</p>
                                  </TooltipContent>
                                </Tooltip>
                                <button
                                  onClick={handlePressEnter}
                                  disabled={!value.trim() && !composing}
                                  className="shrink-0 size-9 flex items-center justify-center bg-[#1A1A1A] hover:bg-[#333333] text-white rounded-full transition-colors disabled:opacity-30 disabled:cursor-not-allowed relative z-10"
                                >
                                  <Send className="w-4 h-4" strokeWidth={2} />
                                </button>
                              </>
                            ) : (
                              <button
                                onClick={stopConversation}
                                className="shrink-0 size-9 flex items-center justify-center bg-white border-2 border-[#1A1A1A] text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-full transition-colors relative z-10"
                              >
                                <Square
                                  className="w-3 h-3"
                                  fill="currentColor"
                                  stroke="none"
                                />
                              </button>
                            )}
                          </div>
                        </div>
                      </FileUpload>
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* Favorite batch action bar */}
            {favoriteMode && selectedMessageIds.size > 0 && (
              <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-5 py-3 bg-white border border-[#E5E5E5] rounded-xl shadow-lg">
                <span className="text-sm text-[#525252]">
                  已选择 {selectedMessageIds.size} 条消息
                </span>
                <button
                  className="px-4 py-1.5 text-sm font-medium text-white bg-[#6366f1] hover:bg-[#4F46E5] rounded-lg transition-colors"
                  onClick={() => {
                    setIsSaveAllFavorites(false);
                    setFavoriteDialogOpen(true);
                  }}
                >
                  保存选中
                </button>
                <button
                  className="px-4 py-1.5 text-sm text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-lg transition-colors"
                  onClick={() => {
                    setFavoriteMode(false);
                    setSelectedMessageIds(new Set());
                  }}
                >
                  取消
                </button>
              </div>
            )}

            {/* Collaboration View */}
            <div
              key={getTabResetKey('collaboration')}
              className={
                mainView === 'collaboration'
                  ? 'cs-page-enter flex-1 flex flex-col min-h-0'
                  : 'hidden'
              }
            >
              <CollaborationPanel
                apiFetch={apiFetch}
                refreshToken={panelRefreshToken}
              />
            </div>

            {/* Tools View */}
            <div
              key={getTabResetKey('tools')}
              className={
                mainView === 'tools'
                  ? 'cs-page-enter flex-1 flex flex-col min-h-0'
                  : 'hidden'
              }
            >
              <ToolsPanel />
            </div>

            {/* Bid View */}
            <div
              key={getTabResetKey('bid')}
              className={
                mainView === 'bid'
                  ? 'cs-page-enter flex-1 flex flex-col min-h-0'
                  : 'hidden'
              }
            >
              <BidPanel apiFetch={apiFetch} />
            </div>

            {/* Favorites View */}
            <div
              key={getTabResetKey('favorites')}
              className={
                mainView === 'favorites'
                  ? 'cs-page-enter flex-1 flex flex-col min-h-0'
                  : 'hidden'
              }
            >
              <FavoritePanel
                apiFetch={apiFetch}
                refreshToken={panelRefreshToken}
              />
            </div>
          </div>
        </div>

        <CreateDocumentDialog
          open={collabDialogOpen}
          onOpenChange={setCollabDialogOpen}
          messageContent={collabMessage}
          agentId={currentAgentId || undefined}
          apiFetch={apiFetch}
          onCreated={() => {
            setPanelRefreshToken((t) => t + 1);
            showToast('文档已创建');
          }}
        />

        <FavoriteDialog
          open={favoriteDialogOpen}
          onOpenChange={(open) => {
            if (!open) setIsSaveAllFavorites(false);
            setFavoriteDialogOpen(open);
          }}
          messageCount={
            isSaveAllFavorites
              ? derivedMessages.length
              : selectedMessageIds.size
          }
          onConfirm={(title) => {
            const msgs = isSaveAllFavorites
              ? derivedMessages
              : derivedMessages.filter((m) =>
                  selectedMessageIds.has(msgSelectKey(m as any)),
                );
            // Strip <think> tags then convert markdown → Word body HTML for storage
            const stripThink = (t: string) =>
              t.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '');
            const mergedContent = msgs
              .map((m) => {
                const roleLabel = m.role === 'user' ? '【用户】' : '【助手】';
                return `${roleLabel}\n\n${stripThink(m.content || '')}\n`;
              })
              .join('\n');
            const wordHtml = markdownToBodyHtml(mergedContent);
            apiFetch('/api/v1/favorite/save', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                title,
                message_ids: msgs.map((m) => m.id || ''),
                messages_data: [{ role: 'merged', content: wordHtml }],
                agent_id: currentAgentId || null,
                conversation_id: currentSessionId || null,
              }),
            })
              .then((resp) => resp.json())
              .then((result) => {
                if (result.code === 0) {
                  setFavoriteMode(false);
                  setSelectedMessageIds(new Set());
                  setIsSaveAllFavorites(false);
                  setFavoriteDialogOpen(false);
                  setPanelRefreshToken((t) => t + 1);
                  showToast('收藏已保存');
                } else {
                  showToast(result.message || '保存失败');
                }
              })
              .catch(() => {
                showToast('保存失败');
              });
          }}
        />

        {drawerVisible && drawerDocumentId && (
          <PdfSheet
            visible={drawerVisible}
            hideModal={hideDrawer}
            documentId={drawerDocumentId}
            chunk={drawerSelectedChunk}
          />
        )}
      </div>

      <AppDownloadDialog open={downloadOpen} onOpenChange={setDownloadOpen} />
    </AgentChatContext.Provider>
  );
}

// ── Sub-components ──

// ── Utility ──
function showToast(message: string) {
  const toast = document.createElement('div');
  toast.className =
    'fixed top-6 left-1/2 -translate-x-1/2 bg-[#F0FDF4] text-[#16A34A] px-5 py-3 rounded-xl text-sm z-[9999] transition-all font-medium border border-[#BBF7D0] shadow-[0_4px_24px_rgba(22,163,74,0.1)]';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}
