import CollaborationPanel from '@/components/collaboration';
import CreateDocumentDialog from '@/components/collaboration/create-document-dialog';
import {
  ConfirmDeleteDialog,
  ConfirmDeleteDialogNode,
} from '@/components/confirm-delete-dialog';
import Image from '@/components/image';
import MarkdownContent from '@/components/next-markdown-content';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import ToolsPanel from '@/components/tools';
import { BidList } from '@/pages/home/bid-list';

import { RealtimeAudioButton } from '@/components/realtime-audio-button';
import { MessageType } from '@/constants/chat';
import {
  useHandleMessageInputChange,
  useSelectDerivedMessages,
} from '@/hooks/logic-hooks';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import type {
  Docagg,
  IMessage,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';
import { IAnswer } from '@/interfaces/database/chat';
import {
  findMessageFromList,
  getLatestError,
  useFindMessageReference,
} from '@/pages/agent/chat/use-send-agent-message';
import { AgentChatContext } from '@/pages/agent/context';
import api from '@/utils/api';
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

function extractThinking(content: string): {
  thinking: string;
  cleanContent: string;
} {
  const match = content.match(/<think>([\s\S]*?)<\/think>/);
  if (match) {
    return {
      thinking: match[1].trim(),
      cleanContent: content.replace(/<think>[\s\S]*?<\/think>/g, '').trim(),
    };
  }
  return { thinking: '', cleanContent: content };
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
  const pendingSendRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
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
  const [isUploadingFile, setIsUploadingFile] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── B-side chat hooks ──
  const { handleInputChange, value, setValue } = useHandleMessageInputChange();
  const { send, answerList, done, stopOutputMessage } = useSendMessageBySSE(
    api.agentChatCompletion,
  );
  const { findReferenceByMessageId } = useFindMessageReference(answerList);
  const {
    derivedMessages,
    scrollRef,
    messageContainerRef,
    removeLatestMessage,
    addNewestOneQuestion,
    addNewestOneAnswer,
    setDerivedMessages,
    scrollToBottom,
  } = useSelectDerivedMessages();

  const sendLoading = !done;

  // ── Process SSE events into messages ──
  useEffect(() => {
    const { content, id, attachment, downloads } =
      findMessageFromList(answerList);
    const answer = content || getLatestError(answerList);

    if (answerList.length > 0) {
      addNewestOneAnswer({
        answer: answer ?? '',
        attachment: attachment as any,
        downloads,
        id: id,
      } as IAnswer);
    }
  }, [answerList, addNewestOneAnswer]);

  // ── Prologue is shown as intro text in the welcome screen, not auto-added as a message
  // This keeps the input centered until the user explicitly starts a conversation.

  // ── UI state (C-side) ──
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const [mainView, setMainView] = useState<
    'chat' | 'collaboration' | 'tools' | 'bid'
  >('chat');
  const [collabDialogOpen, setCollabDialogOpen] = useState(false);
  const [collabMessage, setCollabMessage] = useState('');

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
      setCurrentAgentId(agentId);
      setCurrentSessionId(null);
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
    [apiFetch, userInfo, setDerivedMessages],
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
    },
    [currentAgentId, userInfo, apiFetch],
  );

  const loadSessionMessages = useCallback(
    async (sessionId: string) => {
      try {
        setDerivedMessages([]);
        setIsLoadingSession(true);
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        const data = result.data;

        const rawMessages: any[] = data.message || [];

        const mapped: IMessage[] = rawMessages.map((m: any) => {
          let content = m.content || m.answer || '';
          let thinking = '';

          // Extract thinking from ␐...⋐ delimiters
          const thinkMatch = content.match(/⋐([\s\S]*?)⋐/);
          if (thinkMatch) {
            thinking = thinkMatch[1].trim();
            content = content.replace(/⋐[\s\S]*?⋐/, '').trim();
          }

          // Extract <think>...</think> tags
          const thinkTagMatch = content.match(/<think>([\s\S]*?)<\/think>/i);
          if (thinkTagMatch) {
            thinking = thinking || thinkTagMatch[1].trim();
            content = content.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
          }

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
            thinking: thinking || undefined,
            reference,
            data: m.data,
          };
        });

        // Handle top-level reference (raw to_dict() format)
        const rawRef = data.reference;
        if (rawRef && typeof rawRef === 'object' && !Array.isArray(rawRef)) {
          let refList: any[];
          if ('chunks' in rawRef) {
            refList = [rawRef];
          } else {
            refList = Object.entries(rawRef)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([, v]) => v);
          }
          const assistantIdxs = mapped
            .map((m, i) => (i !== 0 && m.role !== 'user' ? i : -1))
            .filter((i) => i >= 0);
          for (let j = 0; j < assistantIdxs.length && j < refList.length; j++) {
            const mi = assistantIdxs[j];
            if (!mapped[mi].reference && refList[j]?.chunks) {
              const chunks: Record<string, IReferenceChunk> = {};
              const docAggs: Record<string, Docagg> = {};
              const rawChunks = refList[j].chunks;
              if (typeof rawChunks === 'object') {
                Object.entries(rawChunks).forEach(
                  ([key, val]: [string, any]) => {
                    chunks[key] = {
                      id: val.chunk_id || val.id || key,
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
                  },
                );
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
    },
    [currentAgentId, apiFetch, setDerivedMessages],
  );

  const switchSession = useCallback(
    (sessionId: string) => {
      setMainView('chat');
      setCurrentSessionId(sessionId);
      loadSessionMessages(sessionId);
    },
    [loadSessionMessages],
  );

  const createNewSession = useCallback(() => {
    setMainView('chat');
    setCurrentSessionId(null);
    setDerivedMessages([]);
    if (currentAgentPrologue) {
      setDerivedMessages([
        {
          id: uuid(),
          role: MessageType.Assistant,
          content: currentAgentPrologue,
        } as IMessage,
      ]);
    }
  }, [currentAgentPrologue, setDerivedMessages]);

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
  const handleFileUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      setIsUploadingFile(true);
      try {
        for (const file of Array.from(files)) {
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
          } else {
            showToast('文件上传失败: ' + (result.message || '未知错误'));
          }
        }
      } catch (e: any) {
        showToast('文件上传失败: ' + e.message);
      } finally {
        setIsUploadingFile(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }
    },
    [token],
  );

  const removeUploadedFile = useCallback((fileId: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.id !== fileId));
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
    const query = value.trim();
    if (!query || sendLoading) return;

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
        return;
      }
    }

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

    sendMessage(query, sessionId, msgId);

    scrollToBottom();
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
  ]);

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
              <svg
                className="w-4 h-4 text-white"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
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
                    icon: 'M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z',
                  },
                  {
                    key: 'collaboration',
                    label: '协作',
                    icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
                  },
                  {
                    key: 'tools',
                    label: '工具',
                    icon: 'M11.42 15.17l-5.658 3.286a1 1 0 01-1.414-.386l-1.894-3.28a1 1 0 01.386-1.364l5.658-3.286a1 1 0 011.414.386l1.894 3.28a1 1 0 01-.386 1.364zM21.758 8.59l-5.658 3.286a1 1 0 01-1.414-.386l-1.894-3.28a1 1 0 01.386-1.364l5.658-3.286a1 1 0 011.414.386l1.894 3.28a1 1 0 01-.386 1.364z',
                  },
                  {
                    key: 'bid',
                    label: '标书',
                    icon: 'M9 2a1 1 0 000 2h2v2.1A7 7 0 005.1 11H3a1 1 0 100 2h2.1A7 7 0 0011 17.9V20H9a1 1 0 100 2h6a1 1 0 100-2h-2v-2.1A7 7 0 0018.9 13H21a1 1 0 100-2h-2.1A7 7 0 0013 6.1V4h2a1 1 0 100-2H9zm2 16.9V19h2v-.1a5 5 0 004.9-4.9H18a1 1 0 100-2h-.1A5 5 0 0013 7.1V7h-2v.1A5 5 0 007.1 12H7a1 1 0 100 2h.1A5 5 0 0011 18.9z',
                  },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setMainView(tab.key)}
                  className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    mainView === tab.key
                      ? 'bg-white text-[#000000]'
                      : 'text-[#333333] hover:text-[#000000]'
                  }`}
                >
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d={tab.icon}
                    />
                  </svg>
                  <span className="hidden sm:inline">{tab.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Right: User */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-[#F59E0B] text-white flex items-center justify-center text-sm font-bold">
              {(userInfo?.nickname || userInfo?.email || 'U')[0].toUpperCase()}
            </div>
            <div className="hidden md:block">
              <div className="text-sm font-medium text-[#000000]">
                {userInfo?.nickname || userInfo?.email || ''}
              </div>
            </div>
            <button
              onClick={handleLogout}
              className="text-[#525252] hover:text-[#000000] transition-colors p-1.5 rounded-lg hover:bg-[#EAEAEA]"
              title="退出登录"
            >
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                />
              </svg>
            </button>
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
              className={`fixed md:static inset-y-0 left-0 z-50 md:z-auto w-56 flex flex-col shrink-0 bg-white border-r border-[#D4D4D4] transition-transform duration-200 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}
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
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
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
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 4v16m8-8H4"
                    />
                  </svg>
                  新建分析
                </button>
              </div>

              {/* Agent selector */}
              <div className="px-3 pb-2">
                <div className="relative">
                  <button
                    onClick={() => setAgentDropdownOpen(!agentDropdownOpen)}
                    className="w-full flex items-center justify-between bg-[#EAEAEA] text-[#000000] text-sm rounded-lg px-3 py-1.5 border border-[#D4D4D4] hover:border-[#000000] transition truncate"
                  >
                    <span className="truncate">
                      {agents.find((a) => a.id === currentAgentId)?.title ||
                        '选择智能体...'}
                    </span>
                    <svg
                      className={`w-3.5 h-3.5 shrink-0 ml-1.5 transition-transform ${agentDropdownOpen ? 'rotate-180' : ''}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 9l-7 7-7-7"
                      />
                    </svg>
                  </button>
                  {agentDropdownOpen && (
                    <>
                      <div
                        className="fixed inset-0 z-10"
                        onClick={() => setAgentDropdownOpen(false)}
                      />
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
                    </>
                  )}
                </div>
              </div>

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
                        <svg
                          className={`w-4 h-4 ${s.id === currentSessionId ? 'text-[#000000]' : 'text-[#525252]'}`}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={1.5}
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                          />
                        </svg>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium truncate">
                            {s.name}
                          </span>
                          <ConfirmDeleteDialog
                            onOk={() => deleteSession(s.id)}
                            content={{
                              title: '确认删除此对话？',
                              node: <ConfirmDeleteDialogNode name={s.name} />,
                            }}
                          >
                            <button
                              className="hidden items-center justify-center w-5 h-5 rounded text-[#525252] hover:text-red-500 shrink-0 group-hover:flex"
                              onClick={(e) => {
                                e.stopPropagation();
                              }}
                            >
                              <svg
                                className="w-3 h-3"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  strokeWidth={2}
                                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                                />
                              </svg>
                            </button>
                          </ConfirmDeleteDialog>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </aside>
          )}

          {/* Main Content Area */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Chat View */}
            {mainView === 'chat' && (
              <div className="cs-page-enter flex-1 flex flex-col min-h-0">
                {/* Header */}
                <div className="h-14 bg-white border-b border-[#D4D4D4] flex items-center px-4 shrink-0">
                  <button
                    className="md:hidden mr-2 p-1.5 rounded-lg hover:bg-[#EAEAEA] transition"
                    onClick={() => setSidebarOpen(true)}
                  >
                    <svg
                      className="w-5 h-5 text-[#333333]"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M4 6h16M4 12h16M4 18h16"
                      />
                    </svg>
                  </button>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-[#000000]">
                      {chatTitle}
                    </h2>
                    <span className="w-1.5 h-1.5 rounded-full bg-[#2ec4b6] animate-pulse" />
                  </div>
                </div>

                {/* Messages Area + Input */}
                {derivedMessages.length === 0 && !isLoadingSession ? (
                  /* Empty state */
                  <div className="flex-1 flex items-center justify-center min-h-0 px-4">
                    <div className="w-full max-w-2xl cs-input-enter">
                      <div className="text-center mb-6">
                        <div className="w-14 h-14 bg-[#EAEAEA] rounded-2xl mx-auto flex items-center justify-center mb-4">
                          <svg
                            className="w-7 h-7 text-[#000000]"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={1.5}
                              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                            />
                          </svg>
                        </div>
                        <p className="text-[#333333] text-sm">
                          选择或创建一个对话开始分析
                        </p>
                        <p className="text-[#525252] text-xs mt-1">
                          上传招标文件至知识库后，即可在此进行智能问答
                        </p>
                        {currentAgentPrologue && (
                          <p className="text-[#525252] text-xs mt-2 max-w-md mx-auto leading-relaxed">
                            {currentAgentPrologue}
                          </p>
                        )}
                        <div className="grid grid-cols-2 gap-2.5 mt-5 max-w-sm mx-auto">
                          {[
                            {
                              icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
                              label: '招标文件解析',
                              color: '#2ec4b6',
                              bg: '#e6f9f7',
                            },
                            {
                              icon: 'M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z',
                              label: '智能评分分析',
                              color: '#f59e0b',
                              bg: '#fef9e7',
                            },
                            {
                              icon: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z',
                              label: '关键信息提取',
                              color: '#4f8ce8',
                              bg: '#eef4ff',
                            },
                            {
                              icon: 'M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z',
                              label: '合规性检查',
                              color: '#8b5cf6',
                              bg: '#f3f0ff',
                            },
                          ].map((card, idx) => (
                            <div
                              key={card.label}
                              className={`cs-card-enter cs-card-d${idx + 1} flex items-center gap-2 px-3 py-2.5 rounded-xl border border-[#E5E5E5] hover:border-[#000000] transition-colors cursor-pointer bg-white group`}
                            >
                              <div
                                className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-colors"
                                style={{ backgroundColor: card.bg }}
                              >
                                <svg
                                  className="w-4 h-4"
                                  fill="none"
                                  stroke={card.color}
                                  strokeWidth={1.5}
                                  viewBox="0 0 24 24"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d={card.icon}
                                  />
                                </svg>
                              </div>
                              <span className="text-xs font-medium text-[#1a1a1a] group-hover:text-[#000000] transition-colors">
                                {card.label}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                      {/* File chips + Input */}
                      <div>
                        {uploadedFiles.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mb-2">
                            {uploadedFiles.map((f) => (
                              <span
                                key={f.id}
                                className="inline-flex items-center gap-1 px-2 py-0.5 bg-[#F5F5F5] border border-[#E5E5E5] rounded-md text-[11px] text-[#000000]"
                              >
                                <svg
                                  className="w-3 h-3 text-[#525252]"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                  />
                                </svg>
                                {f.name}
                                <button
                                  onClick={() => removeUploadedFile(f.id)}
                                  className="ml-0.5 hover:text-red-500"
                                >
                                  <svg
                                    className="w-2.5 h-2.5"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      strokeWidth={2}
                                      d="M6 18L18 6M6 6l12 12"
                                    />
                                  </svg>
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="cs-input-ring flex flex-col gap-2 bg-[#FFFFFF] border border-[#D4D4D4] rounded-2xl px-4 py-3">
                          <textarea
                            ref={textareaRef}
                            value={value}
                            onChange={(e) => {
                              const el = e.target;
                              el.style.height = 'auto';
                              el.style.height =
                                Math.min(el.scrollHeight, 200) + 'px';
                              handleInputChange(e);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handlePressEnter();
                              }
                            }}
                            placeholder={typewriterText}
                            rows={3}
                            className="w-full bg-transparent outline-none resize-none text-sm leading-relaxed placeholder:text-[#A3A3A3] text-[#000000] cs-typewriter-cursor min-h-[72px]"
                            disabled={sendLoading}
                            autoFocus
                          />
                          <div className="flex items-center justify-end gap-2">
                            <div className="shrink-0 w-9 h-9 flex items-center justify-center">
                              <RealtimeAudioButton
                                onTranscript={(val) => setAudioInputValue(val)}
                                testId="c-chat-audio-toggle"
                              />
                            </div>
                            <button
                              onClick={() => fileInputRef.current?.click()}
                              disabled={sendLoading || isUploadingFile}
                              className="shrink-0 w-9 h-9 flex items-center justify-center text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                              title="上传文件"
                            >
                              {isUploadingFile ? (
                                <svg
                                  className="w-4 h-4 animate-spin"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                >
                                  <circle
                                    cx="12"
                                    cy="12"
                                    r="10"
                                    stroke="currentColor"
                                    strokeWidth="3"
                                    opacity="0.25"
                                  />
                                  <path
                                    d="M12 2a10 10 0 019.95 9"
                                    stroke="currentColor"
                                    strokeWidth="3"
                                    strokeLinecap="round"
                                  />
                                </svg>
                              ) : (
                                <svg
                                  className="w-4 h-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
                                  />
                                </svg>
                              )}
                            </button>
                            <button
                              onClick={handlePressEnter}
                              disabled={!value.trim()}
                              className="shrink-0 w-9 h-9 flex items-center justify-center bg-[#000000] hover:bg-[#1a1a1a] text-white rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed active:scale-95"
                            >
                              <svg
                                className="w-4 h-4"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  strokeWidth={2}
                                  d="M5 12h14M12 5l7 7-7 7"
                                />
                              </svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  /* Non-empty state: messages + bottom input */
                  <>
                    <div
                      className="flex-1 overflow-y-auto px-4 lg:px-6 py-4 cs-scrollbar"
                      style={{ scrollbarWidth: 'thin' }}
                      ref={messageContainerRef}
                    >
                      <div className="max-w-[80rem] mx-auto space-y-4">
                        {isLoadingSession ? (
                          <div className="flex flex-col items-center justify-center py-20">
                            <svg
                              className="w-7 h-7 animate-spin text-[#000000] mb-4"
                              viewBox="0 0 24 24"
                              fill="none"
                            >
                              <circle
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="3"
                                opacity="0.25"
                              />
                              <path
                                d="M12 2a10 10 0 019.95 9"
                                stroke="currentColor"
                                strokeWidth="3"
                                strokeLinecap="round"
                              />
                            </svg>
                            <p className="text-[#333333] text-sm">
                              加载对话中...
                            </p>
                          </div>
                        ) : (
                          derivedMessages.map((msg, i) => {
                            const isLast = i === derivedMessages.length - 1;
                            const streaming = isLast && sendLoading;
                            const refs: IReferenceObject | undefined =
                              findReferenceByMessageId(msg.id) ??
                              (msg.reference as any);

                            if (msg.role === MessageType.User) {
                              const userName =
                                userInfo?.nickname || userInfo?.email || 'U';
                              return (
                                <div
                                  key={msg.id || i}
                                  className="flex justify-end cs-msg-enter gap-2 items-start"
                                >
                                  <div className="max-w-[85%]">
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
                                                  <svg
                                                    className="w-3 h-3 shrink-0"
                                                    fill="none"
                                                    stroke="currentColor"
                                                    viewBox="0 0 24 24"
                                                  >
                                                    <path
                                                      strokeLinecap="round"
                                                      strokeLinejoin="round"
                                                      strokeWidth={2}
                                                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                                    />
                                                  </svg>
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
                                            try {
                                              await navigator.clipboard.writeText(
                                                msg.content,
                                              );
                                              setCopiedIndex(i);
                                              setTimeout(
                                                () => setCopiedIndex(null),
                                                2000,
                                              );
                                            } catch {
                                              // clipboard API may fail
                                            }
                                          }}
                                        >
                                          {copiedIndex === i ? (
                                            <svg
                                              className="w-3.5 h-3.5"
                                              fill="none"
                                              stroke="currentColor"
                                              viewBox="0 0 24 24"
                                            >
                                              <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                strokeWidth={2}
                                                d="M5 13l4 4L19 7"
                                              />
                                            </svg>
                                          ) : (
                                            <svg
                                              className="w-3.5 h-3.5"
                                              fill="none"
                                              stroke="currentColor"
                                              viewBox="0 0 24 24"
                                            >
                                              <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                strokeWidth={2}
                                                d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                                              />
                                            </svg>
                                          )}
                                          {copiedIndex === i
                                            ? '已复制'
                                            : '复制'}
                                        </button>
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
                                          title="重新生成"
                                        >
                                          <svg
                                            className="w-3.5 h-3.5"
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              strokeWidth={2}
                                              d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"
                                            />
                                          </svg>
                                          重新生成
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                  <div className="size-7 rounded-lg bg-[#F59E0B] text-white flex items-center justify-center text-xs font-bold shrink-0">
                                    {userName[0].toUpperCase()}
                                  </div>
                                </div>
                              );
                            }

                            // Assistant message
                            const { thinking, cleanContent } = extractThinking(
                              msg.content || '',
                            );
                            const displayContent = streaming
                              ? msg.content || ''
                              : cleanContent || msg.content;
                            const displayThinking = streaming
                              ? extractThinking(msg.content || '').thinking
                              : thinking;
                            const isCurrentlyThinking =
                              streaming &&
                              /<think>/.test(msg.content || '') &&
                              !/<\/think>/.test(msg.content || '');

                            return (
                              <div
                                key={msg.id || i}
                                className="flex justify-start cs-msg-enter gap-2 items-start"
                              >
                                <RAGFlowAvatar
                                  name="标"
                                  avatar=""
                                  className="size-7 shrink-0 mt-0.5"
                                />
                                <div className="max-w-[85%]">
                                  <div className="bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md text-[15px] leading-relaxed tracking-wider text-[#000000]">
                                    {displayThinking && (
                                      <ThinkingBlock text={displayThinking} />
                                    )}
                                    <div className="msg-content text-[#000000]">
                                      <MarkdownContent
                                        content={displayContent}
                                        loading={streaming}
                                        reference={refs}
                                      />
                                    </div>
                                    {streaming && (
                                      <span className="inline-block w-2 h-4 bg-[#000000] ml-1 animate-pulse rounded-sm" />
                                    )}
                                    {streaming && isCurrentlyThinking && (
                                      <div className="flex items-center gap-2 text-[#525252] text-xs py-1">
                                        <svg
                                          className="w-3.5 h-3.5 animate-spin text-[#A3A3A3]"
                                          viewBox="0 0 24 24"
                                          fill="none"
                                        >
                                          <circle
                                            cx="12"
                                            cy="12"
                                            r="10"
                                            stroke="currentColor"
                                            strokeWidth="3"
                                            opacity="0.25"
                                          />
                                          <path
                                            d="M12 2a10 10 0 019.95 9"
                                            stroke="currentColor"
                                            strokeWidth="3"
                                            strokeLinecap="round"
                                          />
                                        </svg>
                                        <span>正在思考中...</span>
                                      </div>
                                    )}
                                  </div>
                                  {/* Reference sources */}
                                  {!streaming &&
                                    refs?.chunks &&
                                    Object.keys(refs.chunks).length > 0 && (
                                      <details className="mt-2 group">
                                        <summary className="list-none cursor-pointer select-none">
                                          <span className="inline-flex items-center gap-1 text-[11px] text-[#525252] hover:text-[#000000] transition-colors">
                                            <svg
                                              className="w-3 h-3 transition-transform group-open:rotate-90"
                                              viewBox="0 0 20 20"
                                              fill="currentColor"
                                            >
                                              <path
                                                fillRule="evenodd"
                                                d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
                                                clipRule="evenodd"
                                              />
                                            </svg>
                                            引用来源 (
                                            {Object.keys(refs.chunks).length})
                                          </span>
                                        </summary>
                                        <div className="mt-1 space-y-0.5 max-h-44 overflow-y-auto pr-1">
                                          {Object.entries(refs.chunks).map(
                                            ([idx, chunk]) => {
                                              const doc = Object.values(
                                                refs.doc_aggs || {},
                                              ).find(
                                                (d) =>
                                                  d.doc_id ===
                                                  chunk.document_id,
                                              );
                                              return (
                                                <div
                                                  key={idx}
                                                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-[#FFFFFF] transition-colors"
                                                >
                                                  {chunk.image_id ? (
                                                    <Image
                                                      id={chunk.image_id}
                                                      className="w-7 h-7 rounded object-cover flex-shrink-0"
                                                    />
                                                  ) : (
                                                    <span className="w-7 h-7 flex items-center justify-center text-stone-300 flex-shrink-0">
                                                      <svg
                                                        viewBox="0 0 24 24"
                                                        className="w-4 h-4"
                                                        fill="none"
                                                        stroke="currentColor"
                                                        strokeWidth="1.5"
                                                      >
                                                        <path d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                                                      </svg>
                                                    </span>
                                                  )}
                                                  <div className="min-w-0 flex-1">
                                                    <div className="truncate text-[12px] text-[#1a1a1a] leading-tight">
                                                      {doc?.doc_name ||
                                                        chunk.document_name ||
                                                        `引用 #${Number(idx) + 1}`}
                                                    </div>
                                                    {(chunk as any).content && (
                                                      <div className="truncate text-[11px] text-[#525252] leading-tight mt-0.5">
                                                        {(
                                                          chunk as any
                                                        ).content.slice(0, 80)}
                                                      </div>
                                                    )}
                                                  </div>
                                                </div>
                                              );
                                            },
                                          )}
                                        </div>
                                      </details>
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
                                              <svg
                                                className="w-3.5 h-3.5 shrink-0"
                                                fill="none"
                                                stroke="currentColor"
                                                viewBox="0 0 24 24"
                                              >
                                                <path
                                                  strokeLinecap="round"
                                                  strokeLinejoin="round"
                                                  strokeWidth={2}
                                                  d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                                />
                                              </svg>
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
                                        onClick={async () => {
                                          try {
                                            await navigator.clipboard.writeText(
                                              msg.content,
                                            );
                                            setCopiedIndex(i);
                                            setTimeout(
                                              () => setCopiedIndex(null),
                                              2000,
                                            );
                                          } catch {
                                            // clipboard API may fail
                                          }
                                        }}
                                      >
                                        {copiedIndex === i ? (
                                          <svg
                                            className="w-3.5 h-3.5"
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              strokeWidth={2}
                                              d="M5 13l4 4L19 7"
                                            />
                                          </svg>
                                        ) : (
                                          <svg
                                            className="w-3.5 h-3.5"
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              strokeWidth={2}
                                              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                                            />
                                          </svg>
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
                                        <svg
                                          className="w-3.5 h-3.5"
                                          fill="none"
                                          stroke="currentColor"
                                          viewBox="0 0 24 24"
                                        >
                                          <path
                                            strokeLinecap="round"
                                            strokeLinejoin="round"
                                            strokeWidth={2}
                                            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                          />
                                        </svg>
                                        协作
                                      </button>
                                    </div>
                                  )}
                                </div>
                              </div>
                            );
                          })
                        )}
                        {/* Loading placeholder: show while waiting for first LLM response */}
                        {sendLoading &&
                          derivedMessages.length > 0 &&
                          derivedMessages[derivedMessages.length - 1]?.role ===
                            MessageType.User && (
                            <div className="flex justify-start cs-msg-enter gap-2 items-start">
                              <RAGFlowAvatar
                                name="标"
                                avatar=""
                                className="size-7 shrink-0 mt-0.5"
                              />
                              <div className="max-w-[85%]">
                                <div className="bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md">
                                  <div className="flex items-center gap-2 text-[#525252] text-sm py-1">
                                    <svg
                                      className="w-4 h-4 animate-spin text-[#A3A3A3]"
                                      viewBox="0 0 24 24"
                                      fill="none"
                                    >
                                      <circle
                                        cx="12"
                                        cy="12"
                                        r="10"
                                        stroke="currentColor"
                                        strokeWidth="3"
                                        opacity="0.25"
                                      />
                                      <path
                                        d="M12 2a10 10 0 019.95 9"
                                        stroke="currentColor"
                                        strokeWidth="3"
                                        strokeLinecap="round"
                                      />
                                    </svg>
                                    <span>正在生成中...</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                        <div ref={scrollRef} />
                      </div>
                    </div>

                    {/* Input Area (bottom) */}
                    <div className="bg-white border-t border-[#D4D4D4] p-3 lg:p-4 shrink-0">
                      <div className="max-w-3xl mx-auto">
                        {uploadedFiles.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mb-2">
                            {uploadedFiles.map((f) => (
                              <span
                                key={f.id}
                                className="inline-flex items-center gap-1 px-2 py-0.5 bg-[#F5F5F5] border border-[#E5E5E5] rounded-md text-[11px] text-[#000000]"
                              >
                                <svg
                                  className="w-3 h-3 text-[#525252]"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                  />
                                </svg>
                                {f.name}
                                <button
                                  onClick={() => removeUploadedFile(f.id)}
                                  className="ml-0.5 hover:text-red-500"
                                >
                                  <svg
                                    className="w-2.5 h-2.5"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      strokeWidth={2}
                                      d="M6 18L18 6M6 6l12 12"
                                    />
                                  </svg>
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="cs-input-ring flex flex-col gap-2 bg-[#FFFFFF] border border-[#D4D4D4] rounded-2xl px-3 py-2">
                          <textarea
                            ref={textareaRef}
                            value={value}
                            onChange={(e) => {
                              const el = e.target;
                              el.style.height = 'auto';
                              el.style.height =
                                Math.min(el.scrollHeight, 200) + 'px';
                              handleInputChange(e);
                            }}
                            onKeyDown={(e) => {
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
                            rows={3}
                            className={`w-full bg-transparent outline-none resize-none text-sm leading-relaxed placeholder:text-[#A3A3A3] text-[#000000] min-h-[72px]${hasMessages ? '' : ' cs-typewriter-cursor'}`}
                            disabled={sendLoading}
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
                                <button
                                  onClick={() => fileInputRef.current?.click()}
                                  disabled={isUploadingFile}
                                  className="shrink-0 w-9 h-9 flex items-center justify-center text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                                  title="上传文件"
                                >
                                  {isUploadingFile ? (
                                    <svg
                                      className="w-4 h-4 animate-spin"
                                      viewBox="0 0 24 24"
                                      fill="none"
                                    >
                                      <circle
                                        cx="12"
                                        cy="12"
                                        r="10"
                                        stroke="currentColor"
                                        strokeWidth="3"
                                        opacity="0.25"
                                      />
                                      <path
                                        d="M12 2a10 10 0 019.95 9"
                                        stroke="currentColor"
                                        strokeWidth="3"
                                        strokeLinecap="round"
                                      />
                                    </svg>
                                  ) : (
                                    <svg
                                      className="w-4 h-4"
                                      fill="none"
                                      stroke="currentColor"
                                      viewBox="0 0 24 24"
                                    >
                                      <path
                                        strokeLinecap="round"
                                        strokeLinejoin="round"
                                        strokeWidth={2}
                                        d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
                                      />
                                    </svg>
                                  )}
                                </button>
                                <button
                                  onClick={handlePressEnter}
                                  disabled={!value.trim()}
                                  className="shrink-0 w-9 h-9 flex items-center justify-center bg-[#000000] hover:bg-[#1a1a1a] text-white rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed active:scale-95"
                                >
                                  <svg
                                    className="w-4 h-4"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      strokeWidth={2}
                                      d="M5 12h14M12 5l7 7-7 7"
                                    />
                                  </svg>
                                </button>
                              </>
                            ) : (
                              <button
                                onClick={stopOutputMessage}
                                className="shrink-0 w-9 h-9 flex items-center justify-center bg-red-400 text-white rounded-xl hover:bg-red-500 transition active:scale-95"
                              >
                                <svg
                                  className="w-3.5 h-3.5"
                                  fill="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <rect
                                    x="6"
                                    y="6"
                                    width="12"
                                    height="12"
                                    rx="1"
                                  />
                                </svg>
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Collaboration View */}
            {mainView === 'collaboration' && (
              <div className="cs-page-enter flex-1 flex flex-col min-h-0">
                <CollaborationPanel apiFetch={apiFetch} />
              </div>
            )}

            {/* Tools View */}
            {mainView === 'tools' && (
              <div className="cs-page-enter flex-1 flex flex-col min-h-0">
                <ToolsPanel />
              </div>
            )}

            {/* Bid View */}
            {mainView === 'bid' && (
              <div className="cs-page-enter flex-1 flex flex-col min-h-0">
                <BidList setListLength={() => {}} />
              </div>
            )}
          </div>
        </div>

        <CreateDocumentDialog
          open={collabDialogOpen}
          onOpenChange={setCollabDialogOpen}
          messageContent={collabMessage}
          agentId={currentAgentId || undefined}
          apiFetch={apiFetch}
          onCreated={() => {}}
        />

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileUpload}
          accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json,.xml,.png,.jpg,.jpeg,.gif,.webp"
        />
      </div>
    </AgentChatContext.Provider>
  );
}

// ── Sub-components ──

function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="bg-[#EAEAEA] border border-[#D4D4D4] rounded-lg mb-2 overflow-hidden">
      <button
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[#000000] cursor-pointer hover:text-[#000000] transition w-full"
        onClick={() => setOpen(!open)}
      >
        <svg
          className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
          />
        </svg>
        <span className="italic font-medium">思考过程</span>
      </button>
      {open && (
        <pre className="px-3 pb-2 text-xs text-[#333333] italic leading-relaxed max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words m-0 font-[family-name:var(--font-mono)]">
          {text}
        </pre>
      )}
    </div>
  );
}

// ── Utility ──
function showToast(message: string) {
  const toast = document.createElement('div');
  toast.className =
    'fixed top-4 right-4 bg-[#000000] text-white px-5 py-3 rounded-lg text-sm z-[9999] transition-all font-medium';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}
