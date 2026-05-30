import CollaborationPanel from '@/components/collaboration';
import CreateDocumentDialog from '@/components/collaboration/create-document-dialog';
import Image from '@/components/image';
import MarkdownContent from '@/components/next-markdown-content';
import ToolsPanel from '@/components/tools';
import { BidList } from '@/pages/home/bid-list';

import type {
  Docagg,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';

// 默认开场白
const DEFAULT_PROLOGUE = '你好！我是你的助理，有什么可以帮到你的吗？';

interface Agent {
  id: string;
  title: string;
  dataset_ids?: string[];
  canvas?: string; // JSON string of the graph/canvas
}

interface Session {
  id: string;
  name: string;
  update_time: number;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  references?: IReferenceObject | null;
  attachment?: Record<string, unknown>;
  downloads?: Array<{ name?: string; url?: string }>;
  files?: Array<{
    id: string;
    name: string;
    mime_type: string;
    size: number;
    extension: string;
  }>;
}

function stripBlockChars(text: string) {
  // Strip block drawing characters and ␐ (U+23C0) used for thinking delimiters
  return (text || '').replace(
    /[\u23C0\u2580-\u259F\u25A0-\u25FF\u2800-\u28FF]/g,
    '',
  );
}

// Process accumulated SSE event list into message content (mirrors agent canvas findMessageFromList)
interface ChatEvent {
  event: string;
  message_id?: string;
  data: Record<string, unknown>;
}

function findContentFromEvents(events: ChatEvent[]) {
  const messageEvents = events.filter((x) => x.event === 'message');
  let content = '';
  let startIndex = -1;
  let endIndex = -1;

  messageEvents.forEach((x, idx) => {
    const { data } = x;
    const chunk = (data.content as string) || '';
    if (data.start_to_think === true) {
      content += '<think>' + chunk;
      startIndex = idx;
      return;
    }
    if (data.end_to_think === true) {
      endIndex = idx;
      content += chunk + '</think>';
      return;
    }
    content += chunk;
  });

  // Ensure closing </think> if think block was started but never ended
  const lastIdx = messageEvents.length - 1;
  if (startIndex >= 0 && startIndex <= lastIdx && endIndex === -1) {
    content += '</think>';
  }

  // Extract attachment/downloads from workflow_finished
  const workflowFinished = events.find((x) => x.event === 'workflow_finished');
  const outputs = (workflowFinished?.data?.outputs || {}) as Record<
    string,
    unknown
  >;

  return {
    content,
    attachment: (outputs.attachment || {}) as Record<string, unknown>,
    downloads: (outputs.downloads || []) as Array<{
      name?: string;
      url?: string;
    }>,
  };
}

// Extract references from message_end events, return IReferenceObject
function findRefsFromEvents(events: ChatEvent[]): IReferenceObject | null {
  const msgEndEvents = events.filter((e) => e.event === 'message_end');
  console.log(
    '[findRefsFromEvents] total events:',
    events.length,
    'message_end events:',
    msgEndEvents.length,
  );
  for (const e of events) {
    if (e.event === 'message_end') {
      const hasRef = !!(e.data as any)?.reference;
      const ref = (e.data as any)?.reference;
      console.log(
        '[findRefsFromEvents] message_end hasRef:',
        hasRef,
        'ref keys:',
        ref ? Object.keys(ref) : 'null',
        'chunks count:',
        ref?.chunks ? Object.keys(ref.chunks).length : 0,
      );
    }
    if (e.event === 'message_end' && (e.data as any)?.reference) {
      const rawRef = (e.data as any).reference;
      const chunks: Record<string, IReferenceChunk> = {};
      if (rawRef.chunks) {
        for (const [key, val] of Object.entries(rawRef.chunks)) {
          const c = val as any;
          chunks[key] = {
            id: c.chunk_id || c.id || key,
            content: c.content_with_weight || c.content || '',
            document_id: c.document_id || '',
            document_name: c.docnm_kwd || c.document_name || '',
            dataset_id: c.dataset_id || '',
            image_id: c.image_id || c.img_id || '',
            similarity: c.similarity || 0,
            vector_similarity: c.vector_similarity || 0,
            term_similarity: c.term_similarity || 0,
            positions: Array.isArray(c.positions)
              ? c.positions
              : c.position_int || [],
          } as IReferenceChunk;
        }
      }
      const docAggs: Record<string, Docagg> = {};
      if (rawRef.doc_aggs) {
        for (const [key, val] of Object.entries(rawRef.doc_aggs)) {
          const d = val as any;
          docAggs[key] = {
            doc_id: d.doc_id || key,
            doc_name: d.doc_name || '',
            count: d.count || 0,
            url: d.url || '',
          };
        }
      }
      return { chunks, doc_aggs: docAggs };
    }
  }
  return null;
}

// Extract latest error from event list
function findErrorFromEvents(events: ChatEvent[]): string | null {
  const last = events[events.length - 1];
  if (
    last &&
    (last.data as any)?.code !== undefined &&
    (last.data as any).code !== 0
  ) {
    return (last.data as any).message || '请求失败';
  }
  const outputs = (last?.data as any)?.outputs;
  if (outputs?._ERROR) return outputs._ERROR as string;
  return null;
}

export default function CChat() {
  const navigate = useNavigate();

  // Auth state
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

  // 注入登录样式
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
      /* Page enter */
      @keyframes cs-page-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
      .cs-page-enter { animation: cs-page-in 0.3s cubic-bezier(0.22, 1, 0.36, 1) both; }
      /* Card pop enter */
      @keyframes cs-card-pop { from { opacity: 0; transform: scale(0.92) translateY(6px); } to { opacity: 1; transform: scale(1) translateY(0); } }
      .cs-card-enter { animation: cs-card-pop 0.4s cubic-bezier(0.22, 1, 0.36, 1) both; }
      .cs-card-d1 { animation-delay: 0.05s; }
      .cs-card-d2 { animation-delay: 0.12s; }
      .cs-card-d3 { animation-delay: 0.19s; }
      .cs-card-d4 { animation-delay: 0.26s; }
      /* List item enter */
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
      /* Table row enter */
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
    `;
    document.head.appendChild(style);
    return () => document.getElementById(styleId)?.remove();
  }, []);

  // Chat state
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  const [currentAgentPrologue, setCurrentAgentPrologue] = useState<string>('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingContent, setThinkingContent] = useState('');
  const [fullContent, setFullContent] = useState('');
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Chat input
  const [inputValue, setInputValue] = useState('');

  // Typewriter placeholder effect
  const FULL_PLACEHOLDER =
    '请在此描述您的标书分析需求，例如：提取招标文件中的关键资质要求、分析评分标准的权重分布、对比各投标企业的技术方案优劣、检查合同条款中的潜在风险点...';
  const [typewriterText, setTypewriterText] = useState('');
  const [typewriterIdx, setTypewriterIdx] = useState(0);
  const [typewriterForward, setTypewriterForward] = useState(true);

  const hasMessages = messages.length > 0;

  useEffect(() => {
    if (hasMessages) return;
    const timer = setInterval(
      () => {
        if (typewriterForward) {
          if (typewriterIdx < FULL_PLACEHOLDER.length) {
            setTypewriterText(FULL_PLACEHOLDER.slice(0, typewriterIdx + 1));
            setTypewriterIdx((prev) => prev + 1);
          } else {
            // Pause at the end, then start deleting
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

  // UI state
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);

  // Main view state: 'chat' or 'collaboration' or 'tools'
  const [mainView, setMainView] = useState<
    'chat' | 'collaboration' | 'tools' | 'bid'
  >('chat');

  // Collaboration state
  const [collabDialogOpen, setCollabDialogOpen] = useState(false);
  const [collabMessage, setCollabMessage] = useState('');

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

  // Verify token on mount
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

  // Fix scroll: override global.less overflow:hidden for chat page
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

  // Auto-scroll — always scroll on new messages, gentle during streaming
  const lastMsgCountRef = useRef(0);
  useEffect(() => {
    const area = messagesEndRef.current?.parentElement?.parentElement;
    if (!area) return;
    const isNewMessage = messages.length > lastMsgCountRef.current;
    lastMsgCountRef.current = messages.length;
    const isNearBottom =
      area.scrollHeight - area.scrollTop - area.clientHeight < 200;
    if (isNewMessage || isNearBottom) {
      area.scrollTo({
        top: area.scrollHeight,
        behavior: isNewMessage ? 'smooth' : 'auto',
      });
    }
  }, [messages, fullContent, thinkingContent]);

  const switchAgent = useCallback(
    (agentId: string) => {
      setCurrentAgentId(agentId);
      setCurrentSessionId(null);
      setMessages([]);
      setSessions([]);
      localStorage.setItem('ragflow_agent_id', agentId);

      // 获取智能体详情，提取开场白
      apiFetch(`/api/v1/agents/${agentId}`)
        .then((r) => r.json())
        .then((result) => {
          if (result.code === 0 && result.data) {
            // DSL 直接在 result.data.dsl 中
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

      // 加载该智能体的历史会话
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
    [apiFetch, userInfo],
  );

  // Load agents when logged in
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
        setMessages([]);
        setIsLoadingSession(true);
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        const data = result.data;

        // to_dict() 返回原始格式：message 数组 + 顶层 reference 对象
        const rawMessages: any[] = data.message || [];

        const mapped: Message[] = rawMessages.map((m: any) => {
          // 思考内容可能用 ␐...⋐ 或 <think>...</think> 包裹
          let content = stripBlockChars(m.content || m.answer || '');
          let thinking = '';

          // 提取 ␐...⋐ 中的思考内容
          const thinkMatch = content.match(/⋐([\s\S]*?)⋐/);
          if (thinkMatch) {
            thinking = stripBlockChars(thinkMatch[1]).trim();
            content = content.replace(/⋐[\s\S]*?⋐/, '').trim();
          }

          // 提取 <think>...</think> 中的思考内容
          const thinkTagMatch = content.match(/<think>([\s\S]*?)<\/think>/i);
          if (thinkTagMatch) {
            thinking = stripBlockChars(thinkTagMatch[1]).trim();
            content = content.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
          }

          // Convert per-message reference array to IReferenceObject
          // Backend _normalize_agent_session puts reference inside each message as array
          let references: IReferenceObject | null = null;
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
            references = { chunks, doc_aggs: docAggs };
          }

          return {
            role: m.role || 'assistant',
            content,
            thinking,
            references,
          };
        });

        // Handle top-level reference (raw to_dict() format from single-session endpoint)
        // Backend _normalize_agent_session is only applied on list endpoint,
        // single GET returns raw data where reference is top-level, not per-message.
        const rawRef = data.reference;
        console.log(
          '[loadSessionMessages] rawRef type:',
          typeof rawRef,
          'isArray:',
          Array.isArray(rawRef),
          'keys:',
          rawRef && typeof rawRef === 'object' && !Array.isArray(rawRef)
            ? Object.keys(rawRef)
            : 'N/A',
        );
        if (rawRef && typeof rawRef === 'object' && !Array.isArray(rawRef)) {
          let refList: any[];
          if ('chunks' in rawRef) {
            refList = [rawRef];
          } else {
            refList = Object.entries(rawRef)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([, v]) => v);
          }
          // Zip with non-user messages (excluding first message), same as backend
          const assistantIdxs = mapped
            .map((m, i) => (i !== 0 && m.role !== 'user' ? i : -1))
            .filter((i) => i >= 0);
          for (let j = 0; j < assistantIdxs.length && j < refList.length; j++) {
            const mi = assistantIdxs[j];
            if (!mapped[mi].references && refList[j]?.chunks) {
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
                mapped[mi].references = { chunks, doc_aggs: docAggs };
              }
            }
          }
        }

        const refCount = mapped.filter(
          (m) => m.role !== 'user' && m.references,
        ).length;
        const assistantCount = mapped.filter((m) => m.role !== 'user').length;
        console.log(
          '[loadSessionMessages] messages with refs:',
          refCount,
          '/',
          assistantCount,
          'assistant messages',
        );
        setMessages(mapped);
      } catch (e) {
        console.error('加载消息失败:', e);
        showToast('加载消息失败');
      } finally {
        setIsLoadingSession(false);
      }
    },
    [currentAgentId, apiFetch],
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
    // 不创建会话，只清空当前状态
    // 会话在用户发送第一条消息时创建，标题用问题截取
    setCurrentSessionId(null);
    // 如果有开场白，显示开场白消息；否则显示默认开场白
    const prologueToShow = currentAgentPrologue || DEFAULT_PROLOGUE;
    setMessages([
      {
        role: 'assistant',
        content: prologueToShow,
      },
    ]);
  }, [currentAgentPrologue]);

  const deleteSession = useCallback(
    async (sessionId: string) => {
      if (!confirm('确认删除此对话？')) return;
      try {
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
          { method: 'DELETE' },
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        if (currentSessionId === sessionId) {
          setCurrentSessionId(null);
          setMessages([]);
        }
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      } catch (e: any) {
        showToast('删除失败: ' + e.message);
      }
    },
    [currentAgentId, currentSessionId, apiFetch],
  );

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

  const sendMessage = useCallback(async () => {
    const query = inputValue.trim();
    if (!query || isStreaming) return;

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
        const newId: string = result.data.id;
        sessionId = newId;
        setCurrentSessionId(sessionId);
        setSessions((prev) => [
          {
            id: newId,
            name: query.slice(0, 30),
            update_time: Date.now() / 1000,
          },
          ...prev,
        ]);
        setMessages([]);
      } catch (e: any) {
        showToast('创建对话失败: ' + e.message);
        return;
      }
    }

    const currentFiles = [...uploadedFiles];
    const userMsg: Message = {
      role: 'user',
      content: query,
      files: currentFiles.length > 0 ? currentFiles : undefined,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInputValue('');
    setUploadedFiles([]);

    setIsStreaming(true);
    setIsThinking(false);
    setThinkingContent('');
    setFullContent('');
    abortRef.current = new AbortController();

    const assistantMsg: Message = {
      role: 'assistant',
      content: '',
      references: null,
    };
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const resp = await apiFetch('/api/v1/agents/chat/completion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: currentAgentId,
          query,
          session_id: sessionId,
          stream: true,
          files: currentFiles,
        }),
        signal: abortRef.current.signal,
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      // Use EventSourceParserStream for robust SSE parsing (same as agent canvas)
      const reader = resp
        .body!.pipeThrough(new TextDecoderStream())
        .pipeThrough(new EventSourceParserStream())
        .getReader();

      const events: ChatEvent[] = [];
      let localFullContent = '';
      let localThinkingContent = '';
      let inThink = false;

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        try {
          const event: ChatEvent = JSON.parse(value?.data || '');
          events.push(event);

          if (event.event === 'message' && event.data) {
            const chunk = (event.data.content as string) || '';

            if (event.data.start_to_think) {
              inThink = true;
              localThinkingContent = '';
              setIsThinking(true);
            } else if (event.data.end_to_think) {
              inThink = false;
              setIsThinking(false);
            } else {
              // Regular content chunk
              if (inThink) {
                localThinkingContent += chunk;
                setThinkingContent(localThinkingContent);
              } else {
                localFullContent += stripBlockChars(chunk);
                setFullContent(localFullContent);
              }
            }
          }
        } catch {
          // skip malformed event
        }
      }

      // Process accumulated events (same logic as agent canvas findMessageFromList)
      const { content, attachment, downloads } = findContentFromEvents(events);
      const refs = findRefsFromEvents(events);
      console.log(
        '[handleSendMessage] refs result:',
        refs
          ? `chunks=${Object.keys(refs.chunks).length} docs=${Object.keys(refs.doc_aggs).length}`
          : 'NULL',
      );
      const errorMsg = findErrorFromEvents(events);

      // Extract thinking from <think> tags in content
      const thinkMatch = content.match(/<think>([\s\S]*?)<\/think>/);
      const thinking = thinkMatch ? thinkMatch[1].trim() : '';
      // Remove think tags from display content, keep rest
      const cleanContent = content
        .replace(/<think>[\s\S]*?<\/think>/g, '')
        .trim();

      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last) {
          updated[updated.length - 1] = {
            ...last,
            content: errorMsg || cleanContent,
            thinking: thinking,
            references: refs,
            attachment: attachment,
            downloads: downloads,
          };
        }
        return updated;
      });
    } catch (e: any) {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last) {
          updated[updated.length - 1] = {
            ...last,
            content:
              e.name === 'AbortError' ? '(已停止)' : '请求失败: ' + e.message,
          };
        }
        return updated;
      });
    } finally {
      setIsStreaming(false);
      setIsThinking(false);
    }
  }, [
    inputValue,
    isStreaming,
    currentSessionId,
    currentAgentId,
    apiFetch,
    userInfo,
    uploadedFiles,
  ]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleLogout = () => {
    clearAuth();
  };

  // Redirect to login if no token (must be in effect, not render)
  useEffect(() => {
    if (!token) {
      navigate('/login');
    }
  }, [token, navigate]);

  // --- Render ---
  if (!token) {
    return null;
  }

  const currentAgent = agents.find((a) => a.id === currentAgentId);
  const chatTitle = currentAgent?.title || '标书分析助手';

  const streamingContent = isThinking ? null : fullContent;

  return (
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

        {/* Sidebar - only visible on chat tab */}
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

            {/* Agent selector — custom monochrome dropdown */}
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
                        s.id === currentSessionId ? 'bg-white' : 'bg-[#EAEAEA]'
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
                        <button
                          className="hidden items-center justify-center w-5 h-5 rounded text-[#525252] hover:text-red-500 shrink-0 group-hover:flex"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteSession(s.id);
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

              {/* Messages Area + Input — centered when empty */}
              {messages.length === 0 && !isLoadingSession ? (
                /* Empty state: vertically centered input */
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
                      {/* Feature highlight cards */}
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
                      <div className="cs-input-ring flex items-end gap-2 bg-[#FFFFFF] border border-[#D4D4D4] rounded-2xl px-4 py-3">
                        <textarea
                          value={inputValue}
                          onChange={(e) => {
                            setInputValue(e.target.value);
                            const el = e.target;
                            el.style.height = 'auto';
                            el.style.height =
                              Math.min(el.scrollHeight, 200) + 'px';
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                              e.preventDefault();
                              sendMessage();
                            }
                          }}
                          placeholder={typewriterText}
                          rows={3}
                          className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed placeholder:text-[#A3A3A3] text-[#000000] cs-typewriter-cursor min-h-[72px]"
                          disabled={isStreaming}
                          autoFocus
                        />
                        <button
                          onClick={() => fileInputRef.current?.click()}
                          disabled={isStreaming || isUploadingFile}
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
                          onClick={sendMessage}
                          disabled={!inputValue.trim()}
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
              ) : (
                /* Non-empty state: messages + bottom input */
                <>
                  <div
                    className="flex-1 overflow-y-auto px-4 lg:px-6 py-4 cs-scrollbar"
                    style={{ scrollbarWidth: 'thin' }}
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
                        messages.map((msg, i) => {
                          const isLast = i === messages.length - 1;
                          const streaming = isLast && isStreaming;
                          if (msg.role === 'user') {
                            return (
                              <div
                                key={i}
                                className="flex justify-end cs-msg-enter"
                              >
                                <div className="max-w-[85%] bg-[#000000] text-white px-4 py-2.5 rounded-2xl rounded-br-md text-[16px] leading-relaxed tracking-wider">
                                  {msg.content}
                                  {msg.files && msg.files.length > 0 && (
                                    <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-white/20">
                                      {msg.files.map((f) => (
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
                                      ))}
                                    </div>
                                  )}
                                </div>
                              </div>
                            );
                          }
                          const content = streaming
                            ? streamingContent || ''
                            : msg.content;
                          const thinking = streaming ? null : msg.thinking;
                          const refs = streaming ? null : msg.references;
                          if (!streaming && msg.role !== 'user') {
                            console.log(
                              '[render msg] idx:',
                              i,
                              'role:',
                              msg.role,
                              'refs:',
                              refs
                                ? `chunks=${Object.keys(refs.chunks).length}`
                                : 'null',
                              'content citations:',
                              (msg.content || '').match(/\[ID:\d+\]/g)
                                ?.length || 0,
                            );
                          }
                          return (
                            <div
                              key={i}
                              className="flex justify-start cs-msg-enter"
                            >
                              <div className="max-w-[85%]">
                                <div className="bg-white border border-[#D4D4D4] px-4 py-2.5 rounded-2xl rounded-bl-md text-[15px] leading-relaxed tracking-wider text-[#000000]">
                                  {thinking && (
                                    <ThinkingBlock text={thinking} />
                                  )}
                                  <div className="msg-content text-[#000000]">
                                    <MarkdownContent
                                      content={content}
                                      loading={streaming}
                                      reference={refs || undefined}
                                    />
                                  </div>
                                  {streaming && (
                                    <span className="inline-block w-2 h-4 bg-[#000000] ml-1 animate-pulse rounded-sm" />
                                  )}
                                  {streaming && isThinking && (
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
                                                d.doc_id === chunk.document_id,
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
                                                  {chunk.content && (
                                                    <div className="truncate text-[11px] text-[#525252] leading-tight mt-0.5">
                                                      {chunk.content.slice(
                                                        0,
                                                        80,
                                                      )}
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
                                          // clipboard API may fail in non-HTTPS contexts
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
                      <div ref={messagesEndRef} />
                    </div>
                  </div>

                  {/* Input Area (bottom, non-empty state) */}
                  <div className="bg-white border-t border-[#D4D4D4] p-3 lg:p-4 shrink-0">
                    <div className="max-w-3xl mx-auto">
                      {/* File chips */}
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
                      <div className="cs-input-ring flex items-end gap-2 bg-[#FFFFFF] border border-[#D4D4D4] rounded-2xl px-3 py-2">
                        <textarea
                          value={inputValue}
                          onChange={(e) => {
                            setInputValue(e.target.value);
                            const el = e.target;
                            el.style.height = 'auto';
                            el.style.height =
                              Math.min(el.scrollHeight, 200) + 'px';
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                              e.preventDefault();
                              sendMessage();
                            }
                          }}
                          placeholder={
                            hasMessages ? '继续输入您的问题...' : typewriterText
                          }
                          rows={3}
                          className={`flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed placeholder:text-[#A3A3A3] text-[#000000] min-h-[72px]${hasMessages ? '' : ' cs-typewriter-cursor'}`}
                          disabled={isStreaming}
                        />
                        {!isStreaming ? (
                          <>
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
                              onClick={sendMessage}
                              disabled={!inputValue.trim()}
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
                            onClick={stopStreaming}
                            className="shrink-0 w-9 h-9 flex items-center justify-center bg-red-400 text-white rounded-xl hover:bg-red-500 transition active:scale-95"
                          >
                            <svg
                              className="w-3.5 h-3.5"
                              fill="currentColor"
                              viewBox="0 0 24 24"
                            >
                              <rect x="6" y="6" width="12" height="12" rx="1" />
                            </svg>
                          </button>
                        )}
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
  );
}

// --- Sub-components ---

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

// --- Utility ---
function showToast(message: string) {
  // Dynamic toast creation for non-React context
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
