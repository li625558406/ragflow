import JSEncrypt from 'jsencrypt';
import { useCallback, useEffect, useRef, useState } from 'react';
import HighLightMarkdown from '@/components/highlight-markdown';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import request from '@/utils/request';
import { useQuery } from '@tanstack/react-query';

const RSA_PUBLIC_KEY =
  '-----BEGIN PUBLIC KEY-----MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOOUEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVKRNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs2wIDAQAB-----END PUBLIC KEY-----';

interface Agent {
  id: string;
  title: string;
  dataset_ids?: string[];
}

interface Session {
  id: string;
  name: string;
  update_time: number;
}

interface Reference {
  // 兼容多种字段名
  id?: string;
  chunk_id?: string;
  docnm_kwd?: string;
  document_name?: string;
  document_id?: string;
  positions?: number[][];
  content_with_weight?: string;
  content?: string;
  image_id?: string;
  img_id?: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  references?: Reference[];
}

function utf8ToBase64(str: string) {
  return btoa(unescape(encodeURIComponent(str)));
}

function rsaEncrypt(password: string): string {
  const encryptor = new JSEncrypt();
  encryptor.setPublicKey(RSA_PUBLIC_KEY);
  const encrypted = encryptor.encrypt(utf8ToBase64(password));
  if (!encrypted) throw new Error('加密失败');
  return encrypted;
}

function stripBlockChars(text: string) {
  // Strip block drawing characters and ␐ (U+23C0) used for thinking delimiters
  return (text || '').replace(
    /[\u23C0\u2580-\u259F\u25A0-\u25FF\u2800-\u28FF]/g,
    '',
  );
}

export default function CChat() {
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

  // 引用徽章点击处理
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (target.classList.contains('cite-badge')) {
        const index = parseInt(target.getAttribute('data-cite-index') || '0', 10);
        if (index > 0) {
          const card = document.getElementById(`ref-${index - 1}`);
          if (card) {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.add('border-[#0F2340]/30', 'bg-[#0F2340]/5');
            setTimeout(() => card.classList.remove('border-[#0F2340]/30', 'bg-[#0F2340]/5'), 2000);
          }
        }
      }
    };
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, []);

  // 注入引用徽章样式
  useEffect(() => {
    const styleId = 'c-chat-citation-styles';
    if (document.getElementById(styleId)) return;
    const style = document.createElement('style');
    style.id = styleId;
    style.textContent = `
      .cite-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 18px;
        height: 18px;
        padding: 0 4px;
        font-size: 11px;
        font-weight: 600;
        line-height: 1;
        border-radius: 4px;
        background: #eff3f8;
        color: #1e3a5f;
        vertical-align: super;
        margin: 0 1px;
        cursor: pointer;
        transition: all 0.15s;
      }
      .cite-badge:hover {
        background: #1e3a5f;
        color: #fff;
      }
    `;
    document.head.appendChild(style);
    return () => document.getElementById(styleId)?.remove();
  }, []);

  // Chat state
  const [agents, setAgents] = useState<Agent[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(
    null,
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingContent, setThinkingContent] = useState('');
  const [fullContent, setFullContent] = useState('');
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Login form state
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  // Chat input
  const [inputValue, setInputValue] = useState('');

  // UI state
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Main view state: 'chat' or 'analysis'
  const [mainView, setMainView] = useState<'chat' | 'analysis'>('chat');

  // Analysis results state
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [availableDocs, setAvailableDocs] = useState<Array<{id: string, name: string}>>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

  // Ref state
  const [expandedRefs, setExpandedRefs] = useState<Set<number>>(new Set());

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

  // Auto-scroll
  useEffect(() => {
    const area = messagesEndRef.current?.parentElement;
    if (!area) return;
    const isNearBottom =
      area.scrollHeight - area.scrollTop - area.clientHeight < 150;
    if (isNearBottom) {
      area.scrollTop = area.scrollHeight;
    }
  }, [messages, fullContent, thinkingContent]);

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
          const targetId = savedId && list.find((a) => a.id === savedId)
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

  const switchAgent = useCallback(
    (agentId: string) => {
      setCurrentAgentId(agentId);
      setCurrentSessionId(null);
      setMessages([]);
      setSessions([]);
      setAvailableDocs([]); // 清空文档列表
      setSelectedDocId(null);
      localStorage.setItem('ragflow_agent_id', agentId);
      // 加载该智能体的历史会话
      const userId =
        userInfo?.id || userInfo?.user_id || userInfo?.email || 'current';
      apiFetch(`/api/v1/agents/${agentId}/sessions?exp_user_id=${userId}&orderby=update_time&desc=true`)
        .then((r) => r.json())
        .then((result) => {
          if (result.code === 0 && result.data) {
            setSessions(
              (result.data || []).map((s: any) => ({
                id: s.id,
                name: s.name || '新对话',
                update_time: Date.now() / 1000,
              })),
            );
          }
        })
        .catch(() => {});
    },
    [apiFetch, userInfo],
  );

  // 获取知识库中已分析的文档列表
  const loadAnalyzedDocuments = useCallback(async () => {
    if (!currentAgentId) return;

    setLoadingDocs(true);
    try {
      // 获取智能体详情，找到关联的知识库
      const agentResp = await apiFetch(`/api/v1/agents/${currentAgentId}`);
      const agentResult = await agentResp.json();
      if (agentResult.code !== 0) {
        console.error('获取智能体详情失败:', agentResult.message);
        return;
      }

      const agentData = agentResult.data;
      const datasetIds = agentData.dataset_ids || [];

      // 如果智能体有关联知识库，使用那些知识库；否则使用所有知识库
      let datasetsToCheck = datasetIds;
      if (datasetIds.length === 0) {
        // 获取所有知识库列表
        const kbResp = await apiFetch('/api/v1/datasets?page_size=100');
        const kbResult = await kbResp.json();
        if (kbResult.code === 0 && kbResult.data) {
          datasetsToCheck = (kbResult.data || []).map((d: any) => d.id);
        }
      }

      if (datasetsToCheck.length === 0) {
        setAvailableDocs([]);
        return;
      }

      // 遍历知识库，获取所有文档
      const allDocuments: any[] = [];
      for (const datasetId of datasetsToCheck) {
        try {
          const docsResp = await apiFetch(`/api/v1/datasets/${datasetId}/documents?page_size=1000`);
          const docsResult = await docsResp.json();
          if (docsResult.code === 0) {
            const dataObj = docsResult.data;
            const docs = dataObj?.docs || [];
            allDocuments.push(...docs);
          }
        } catch {
          // 忽略错误，继续处理其他知识库
        }
      }

      const documents = allDocuments;

      // 过滤出有分析结果的文档
      const analyzedDocs: Array<{id: string, name: string}> = [];
      for (const doc of documents) {
        try {
          const analysisResp = await apiFetch(`/api/v1/documents/${doc.id}/analysis`);
          const analysisResult = await analysisResp.json();
          if (analysisResult.code === 0 && analysisResult.data) {
            analyzedDocs.push({
              id: doc.id,
              name: doc.name || doc.doc_name || '未命名文档',
            });
          }
        } catch {
          // 忽略错误，继续处理其他文档
        }
      }

      setAvailableDocs(analyzedDocs);
      if (analyzedDocs.length > 0 && !selectedDocId) {
        setSelectedDocId(analyzedDocs[0].id);
      }
    } catch (e) {
      console.error('加载已分析文档失败:', e);
    } finally {
      setLoadingDocs(false);
    }
  }, [currentAgentId, apiFetch, selectedDocId]);

  // 当切换到分析结果视图时，加载已分析文档列表
  useEffect(() => {
    if (mainView === 'analysis') {
      loadAnalyzedDocuments();
    }
  }, [mainView, loadAnalyzedDocuments]);

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
              update_time: Date.now() / 1000,
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
        const resp = await apiFetch(
          `/api/v1/agents/${currentAgentId}/sessions/${sessionId}`,
        );
        const result = await resp.json();
        if (result.code !== 0) throw new Error(result.message);
        const data = result.data;

        // to_dict() 返回原始格式：message 数组 + 顶层 reference 对象
        const rawMessages: any[] = data.message || [];
        const rawRef = data.reference || {};

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

          return {
            role: m.role || 'assistant',
            content,
            thinking,
            references: [],
          };
        });

        // 处理顶层 reference 对象，附加到 assistant 消息
        // reference 结构: {chunks: {0: {...}, 1: {...}}, doc_aggs: {...}}
        if (rawRef && rawRef.chunks) {
          const chunksObj = rawRef.chunks;
          // chunks 可能是对象 {0: {...}, 1: {...}} 或数组
          const chunksList: any[] = Array.isArray(chunksObj)
            ? chunksObj
            : Object.values(chunksObj);

          // 只有一条引用记录，对应最后一条 assistant 消息
          if (chunksList.length > 0) {
            // 找到最后一条 assistant 消息
            for (let i = mapped.length - 1; i >= 0; i--) {
              if (mapped[i].role === 'assistant') {
                mapped[i].references = chunksList.map((c: any) => ({
                  id: c.chunk_id || c.id || '',
                  chunk_id: c.chunk_id || c.id || '',
                  docnm_kwd: c.docnm_kwd || c.document_name || '',
                  document_name: c.docnm_kwd || c.document_name || '',
                  positions: Array.isArray(c.positions) ? c.positions : c.position_int || [],
                  content_with_weight: c.content_with_weight || c.content || '',
                  content: c.content_with_weight || c.content || '',
                  image_id: c.image_id || c.img_id || '',
                  img_id: c.image_id || c.img_id || '',
                }));
                break;
              }
            }
          }
        }

        setMessages(mapped);
      } catch (e) {
        console.error('加载消息失败:', e);
        showToast('加载消息失败');
      }
    },
    [currentAgentId, apiFetch],
  );

  // 获取文档分析结果
  const fetchAnalysisResult = useCallback(async (documentId: string) => {
    try {
      const resp = await apiFetch(api.getDocumentAnalysis(documentId));
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        return result.data;
      }
      return null;
    } catch (e) {
      console.error('获取分析结果失败:', e);
      return null;
    }
  }, [apiFetch]);

  const { data: analysisResult, isLoading: analysisLoading } = useQuery({
    queryKey: ['documentAnalysis', selectedDocId],
    queryFn: () => selectedDocId ? fetchAnalysisResult(selectedDocId) : null,
    enabled: !!selectedDocId,
  });

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
    setMessages([]);
  }, []);

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
          { id: newId, name: query.slice(0, 30), update_time: Date.now() / 1000 },
          ...prev,
        ]);
        setMessages([]);
      } catch (e: any) {
        showToast('创建对话失败: ' + e.message);
        return;
      }
    }

    const userMsg: Message = { role: 'user', content: query };
    setMessages((prev) => [...prev, userMsg]);
    setInputValue('');

    setIsStreaming(true);
    setIsThinking(false);
    setThinkingContent('');
    setFullContent('');
    abortRef.current = new AbortController();

    const assistantMsg: Message = {
      role: 'assistant',
      content: '',
      references: [],
    };
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const resp = await apiFetch(
        '/api/v1/agents/chat/completion',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agent_id: currentAgentId,
            query,
            session_id: sessionId,
            stream: true,
          }),
          signal: abortRef.current.signal,
        },
      );

      const reader = resp.body!
        .pipeThrough(new TextDecoderStream())
        .getReader();
      let buffer = '';
      let localThinking = false;
      let localThinkingContent = '';
      let localFullContent = '';

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += value;
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') continue;

          try {
            const event = JSON.parse(data);
            if (event.event === 'message' && event.data) {
              if (event.data.start_to_think) {
                localThinking = true;
                localThinkingContent = '';
                setIsThinking(true);
                setThinkingContent('');
              }
              const chunk = event.data.content || '';
              if (localThinking) {
                localThinkingContent += chunk;
                setThinkingContent(localThinkingContent);
              } else {
                localFullContent += stripBlockChars(chunk);
                setFullContent(localFullContent);
              }
              if (event.data.end_to_think) {
                localThinking = false;
                setIsThinking(false);
              }
            }
            if (event.event === 'message_end' && event.data) {
              const ref = event.data.reference;
              if (ref?.chunks) {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last) {
                    updated[updated.length - 1] = {
                      ...last,
                      references: Object.values(ref.chunks) as Reference[],
                    };
                  }
                  return updated;
                });
              }
            }
          } catch {
            /* skip */
          }
        }
      }

      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last) {
          updated[updated.length - 1] = {
            ...last,
            content: stripBlockChars(localFullContent),
            thinking: stripBlockChars(localThinkingContent).trim(),
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
              e.name === 'AbortError'
                ? '(已停止)'
                : '请求失败: ' + e.message,
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
  ]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // --- Login ---
  const handleLogin = async () => {
    if (!email || !password) {
      setLoginError('请输入邮箱和密码');
      return;
    }
    setLoginLoading(true);
    setLoginError('');
    try {
      const encryptedPwd = rsaEncrypt(password);
      const resp = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: encryptedPwd }),
      });
      const authHeader =
        resp.headers.get('Authorization') ||
        resp.headers.get('authorization');
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message || '登录失败');

      const t = authHeader || result.data?.access_token;
      if (!t) throw new Error('登录响应中未获取到令牌');

      const authorization = t.startsWith('Bearer ') ? t : 'Bearer ' + t;
      const info = {
        email: result.data.email || email,
        nickname: result.data.nickname || result.data.name || email.split('@')[0],
        avatar: result.data.avatar,
      };
      setToken(authorization);
      setUserInfo(info);
      localStorage.setItem('Authorization', authorization);
      localStorage.setItem('token', result.data?.access_token || t.replace('Bearer ', ''));
      localStorage.setItem('userInfo', JSON.stringify(info));
    } catch (e: any) {
      setLoginError(e.message || '登录失败，请检查网络连接');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLogout = () => {
    clearAuth();
  };

  // --- Render ---
  if (!token) {
    return <LoginScreen {...{ email, password, loginError, loginLoading, setEmail, setPassword, handleLogin }} />;
  }

  const currentAgent = agents.find((a) => a.id === currentAgentId);
  const chatTitle = currentAgent?.title || '标书分析助手';

  const streamingContent = isThinking
    ? null
    : fullContent;

  return (
    <div className="h-screen flex bg-slate-100 overflow-hidden">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`fixed md:static inset-y-0 left-0 z-50 w-64 flex flex-col shrink-0 bg-[#0F2340] transition-transform duration-200 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
        {/* Sidebar header */}
        <div className="h-14 flex items-center justify-between px-4 border-b border-white/10 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-[#1E3A5F] rounded-lg flex items-center justify-center">
              <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <span className="text-sm font-bold text-white">标书分析助手</span>
          </div>
          <button className="md:hidden text-white/60 hover:text-white" onClick={() => setSidebarOpen(false)}>
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* New session button */}
        <div className="px-3 pt-3 pb-2">
          <button
            onClick={() => { setMainView('chat'); createNewSession(); setSidebarOpen(false); }}
            className="w-full flex items-center justify-center gap-2 bg-[#059669] hover:bg-[#047857] text-white py-2 rounded-lg transition font-medium text-sm"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            新建分析
          </button>
        </div>

        {/* Agent selector */}
        <div className="px-3 pb-2">
          <div className="relative">
            <select
              value={currentAgentId}
              onChange={(e) => {
                switchAgent(e.target.value);
                loadSessions(e.target.value);
              }}
              className="w-full bg-white/10 text-white text-sm rounded-lg px-3 py-1.5 outline-none appearance-none cursor-pointer border border-white/10 focus:border-white/25 transition pr-7"
            >
              <option value="" disabled className="text-slate-800">选择智能体...</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id} className="text-slate-800">{a.title || '未命名智能体'}</option>
              ))}
            </select>
            <svg className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/40 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {/* Session list */}
        <div className="px-3 py-1">
          <span className="text-white/30 text-[10px] font-semibold tracking-widest uppercase px-1">历史对话</span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-2" style={{ scrollbarWidth: 'none' }}>
          {sessions.length === 0 ? (
            <div className="text-center text-white/20 text-xs py-10">暂无对话</div>
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition text-sm ${
                  s.id === currentSessionId
                    ? 'bg-white/10 text-white'
                    : 'text-white/50 hover:bg-white/5 hover:text-white/80'
                }`}
                onClick={() => { switchSession(s.id); setSidebarOpen(false); }}
              >
                <svg className="w-3.5 h-3.5 shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                </svg>
                <span className="flex-1 truncate">{s.name}</span>
                <button
                  className="hidden w-5 h-5 items-center justify-center rounded text-white/30 hover:text-red-400 hover:bg-white/10 shrink-0 group-hover:flex"
                  onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))
          )}
        </div>

        {/* View toggle - Analysis Results or Back to Chat */}
        <div className="px-3 py-3 border-t border-white/10">
          {mainView === 'chat' ? (
            <div
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition text-sm bg-[#1E6EB5] hover:bg-[#1a5ca3] text-white font-medium shadow-md"
              onClick={() => { setMainView('analysis'); setSidebarOpen(false); }}
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <span className="flex-1">文档全量分析结果</span>
            </div>
          ) : (
            <div
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition text-sm bg-white/10 text-white font-medium"
              onClick={() => { setMainView('chat'); setSidebarOpen(false); }}
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
              <span className="flex-1">返回对话</span>
            </div>
          )}
        </div>

        {/* User section */}
        <div className="px-3 py-3 border-t border-white/10">
          <div className="flex items-center gap-2.5 px-1">
            <div className="w-7 h-7 bg-[#1E6EB5] text-white/80 rounded-full flex items-center justify-center text-xs font-bold shrink-0">
              {(userInfo?.nickname || userInfo?.email || 'U')[0].toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-white/80 truncate">{userInfo?.nickname || userInfo?.email || ''}</div>
            </div>
            <button onClick={handleLogout} className="text-white/30 hover:text-white/70 transition p-0.5 rounded" title="退出登录">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Chat View */}
        {mainView === 'chat' && (
          <>
            {/* Header */}
            <div className="h-14 bg-white border-b border-slate-200/80 flex items-center px-4 shrink-0">
              <button className="md:hidden mr-2 p-1.5 rounded-lg hover:bg-slate-100 transition" onClick={() => setSidebarOpen(true)}>
                <svg className="w-5 h-5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">{chatTitle}</h2>
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              </div>
            </div>

            {/* Messages Area */}
            <div className="flex-1 flex flex-col min-h-0 bg-white">
              <div className="flex-1 overflow-y-auto p-4 lg:p-6 space-y-4" style={{ scrollbarWidth: 'thin' }}>
                {messages.length === 0 ? (
                  <div className="text-center py-20">
                    <div className="w-14 h-14 bg-slate-100 rounded-2xl mx-auto flex items-center justify-center mb-4">
                      <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                      </svg>
                    </div>
                    <p className="text-slate-400 text-sm">选择或创建一个对话开始分析</p>
                    <p className="text-slate-300 text-xs mt-1">上传招标文件至知识库后，即可在此进行智能问答</p>
                  </div>
                ) : (
                  messages.map((msg, i) => {
                    const isLast = i === messages.length - 1;
                    const streaming = isLast && isStreaming;
                    if (msg.role === 'user') {
                      return (
                        <div key={i} className="flex justify-end">
                          <div className="max-w-[80%] lg:max-w-[70%] bg-[#0F2340] text-white px-4 py-2.5 rounded-2xl rounded-br-md text-sm leading-relaxed">
                            {msg.content}
                          </div>
                        </div>
                      );
                    }
                    const content = streaming ? streamingContent || '' : msg.content;
                    const thinking = streaming ? null : msg.thinking;
                    const refs = streaming ? null : msg.references;
                    const processedContent = processCitationMarkers(content, refs || undefined);
                    return (
                      <div key={i} className="flex justify-start">
                        <div className="max-w-[80%] lg:max-w-[70%]">
                          <div className="bg-white border border-slate-200/80 px-4 py-2.5 rounded-2xl rounded-bl-md text-sm leading-relaxed text-slate-800">
                            {thinking && <ThinkingBlock text={thinking} />}
                            <div className="msg-content text-slate-800">
                              <HighLightMarkdown>{processedContent}</HighLightMarkdown>
                            </div>
                            {streaming && (
                              <span className="inline-block w-2 h-4 bg-[#0F2340] ml-1 animate-pulse" />
                            )}
                            {streaming && isThinking && (
                              <div className="flex items-center gap-2 text-slate-400 text-xs py-1">
                                <svg className="w-3.5 h-3.5 animate-spin text-slate-300" viewBox="0 0 24 24" fill="none">
                                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
                                  <path d="M12 2a10 10 0 019.95 9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                                </svg>
                                <span>正在思考中...</span>
                              </div>
                            )}
                          </div>
                          {refs && refs.length > 0 && (
                            <ReferenceSection
                              refs={refs}
                              expanded={expandedRefs}
                              onToggle={(idx) =>
                                setExpandedRefs((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(idx)) next.delete(idx);
                                  else next.add(idx);
                                  return next;
                                })
                              }
                            />
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Input Area */}
              <div className="bg-white border-t border-slate-200/80 p-3 lg:p-4 shrink-0">
                <div className="max-w-3xl mx-auto">
                  <div className="flex items-end gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 focus-within:border-slate-300 focus-within:ring-2 focus-within:ring-slate-100 transition">
                    <textarea
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          sendMessage();
                        }
                      }}
                      placeholder="输入标书相关问题..."
                      rows={1}
                      className="flex-1 bg-transparent outline-none resize-none text-sm leading-relaxed max-h-32 py-1 placeholder:text-slate-400 text-slate-800"
                      disabled={isStreaming}
                    />
                    {!isStreaming ? (
                      <button
                        onClick={sendMessage}
                        disabled={!inputValue.trim()}
                        className="shrink-0 w-8 h-8 flex items-center justify-center bg-[#0F2340] text-white rounded-lg hover:bg-slate-800 transition disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M12 5l7 7-7 7" />
                        </svg>
                      </button>
                    ) : (
                      <button
                        onClick={stopStreaming}
                        className="shrink-0 w-8 h-8 flex items-center justify-center bg-red-500 text-white rounded-lg hover:bg-red-600 transition"
                      >
                        <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                          <rect x="6" y="6" width="12" height="12" rx="1" />
                        </svg>
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </>
        )}

        {/* Analysis Results View */}
        {mainView === 'analysis' && (
          <>
            {/* Header */}
            <div className="h-14 bg-white border-b border-slate-200/80 flex items-center px-4 shrink-0">
              <button className="md:hidden mr-2 p-1.5 rounded-lg hover:bg-slate-100 transition" onClick={() => setSidebarOpen(true)}>
                <svg className="w-5 h-5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">分析结果</h2>
              </div>
            </div>

            {/* Analysis Results Content */}
            <div className="flex-1 overflow-y-auto p-4 lg:p-6 bg-white">
              {loadingDocs ? (
                <div className="flex items-center justify-center py-20">
                  <div className="text-center">
                    <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-[#0F2340] mx-auto mb-3" />
                    <p className="text-slate-500 text-sm">加载文档列表中...</p>
                  </div>
                </div>
              ) : availableDocs.length === 0 ? (
                <div className="flex items-center justify-center py-20">
                  <div className="text-center max-w-md">
                    <div className="w-16 h-16 bg-slate-100 rounded-2xl mx-auto flex items-center justify-center mb-4">
                      <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </div>
                    <p className="text-slate-600 text-base mb-2">暂无已分析的文档</p>
                    <p className="text-slate-400 text-sm">请先在知识库文档列表中点击&quot;分析&quot;按钮进行文档分析</p>
                  </div>
                </div>
              ) : (
                <>
                  {/* 文档选择器 */}
                  <div className="mb-4 pb-4 border-b border-slate-200">
                    <label className="block text-sm font-medium text-slate-700 mb-2">
                      选择文档查看分析结果
                    </label>
                    <Select value={selectedDocId || ''} onValueChange={(v) => setSelectedDocId(v)}>
                      <SelectTrigger className="w-full max-w-md">
                        <SelectValue placeholder="选择文档..." />
                      </SelectTrigger>
                      <SelectContent>
                        {availableDocs.map((doc) => (
                          <SelectItem key={doc.id} value={doc.id}>
                            {doc.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* 分析结果内容 */}
                  {!selectedDocId ? (
                    <div className="text-center py-20">
                      <p className="text-slate-400">请选择一个文档查看分析结果</p>
                    </div>
                  ) : analysisLoading ? (
                    <div className="flex items-center justify-center py-20">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[#0F2340]" />
                    </div>
                  ) : !analysisResult ? (
                    <div className="text-center py-20">
                      <p className="text-slate-400">该文档暂无分析结果</p>
                    </div>
                  ) : analysisResult.status === 'failed' ? (
                    <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                      <p className="text-red-800">分析失败：{analysisResult.error_message || '未知错误'}</p>
                    </div>
                  ) : (
                    <div className="space-y-6">
                      {/* 分析状态和进度 */}
                      {(analysisResult.status === 'running' || analysisResult.status === 'pending') && (
                        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                          <div className="flex items-center gap-2 mb-2">
                            <div className="animate-spin rounded-full h-4 w-4 border-2 border-blue-500" />
                            <span className="text-sm font-medium text-blue-800">分析进行中...</span>
                          </div>
                          <div className="w-full bg-blue-200 rounded-full h-2 overflow-hidden">
                            <div
                              className="bg-blue-500 h-2 rounded-full transition-all duration-500"
                              style={{ width: `${analysisResult.progress || 0}%` }}
                            />
                          </div>
                        </div>
                      )}

                      {/* 分析结果 */}
                      {analysisResult.sections && analysisResult.sections.length > 0 ? (
                        <div className="space-y-4">
                          {analysisResult.sections.map((section: any, idx: number) => (
                            <div key={idx} className="border border-slate-200 rounded-lg overflow-hidden">
                              <div className="bg-slate-50 px-4 py-2 border-b border-slate-200">
                                <h3 className="text-sm font-semibold text-slate-800">{section.section_title}</h3>
                              </div>
                              <div className="p-4 bg-white">
                                {section.analyses && section.analyses.map((analysis: any, aIdx: number) => (
                                  <div key={aIdx} className="mb-4 last:mb-0">
                                    <div className="text-xs font-semibold text-[#0F2340] mb-1">
                                      {analysis.analysis_type === 'key_points' ? '关键要点' : analysis.analysis_type}
                                    </div>
                                    {analysis.success ? (
                                      <div className="text-sm text-slate-700 leading-relaxed">
                                        <HighLightMarkdown>{analysis.result}</HighLightMarkdown>
                                      </div>
                                    ) : (
                                      <div className="text-sm text-red-600 bg-red-50 p-2 rounded">
                                        分析失败：{analysis.error_message || '未知错误'}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-center py-20">
                          <p className="text-slate-400">分析结果为空</p>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// --- Sub-components ---

function LoginScreen({
  email,
  password,
  loginError,
  loginLoading,
  setEmail,
  setPassword,
  handleLogin,
}: {
  email: string;
  password: string;
  loginError: string;
  loginLoading: boolean;
  setEmail: (v: string) => void;
  setPassword: (v: string) => void;
  handleLogin: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-gradient-to-br from-[#0F2340] via-[#1E3A5F] to-[#2563EB] flex items-center justify-center">
      <div className="bg-white/95 backdrop-blur-xl rounded-2xl shadow-2xl p-10 w-full max-w-md border border-white/30">
        <div className="text-center mb-8">
          <div className="w-20 h-20 bg-[#1E3A5F] rounded-2xl mx-auto flex items-center justify-center mb-5 shadow-lg">
            <svg className="w-10 h-10 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-[#162E4D] tracking-wide">
            标书分析助手
          </h1>
          <div className="w-16 h-0.5 bg-[#059669] mx-auto mt-3 mb-2" />
          <p className="text-slate-500 text-sm">
            智能招标文件分析与决策支持系统
          </p>
        </div>

        {loginError && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm p-3 rounded-lg mb-4">
            {loginError}
          </div>
        )}

        <div className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              邮箱地址
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="请输入注册邮箱"
              className="w-full px-4 py-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-[#1E3A5F] focus:border-[#1E3A5F] outline-none transition text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              登录密码
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleLogin();
              }}
              placeholder="请输入密码"
              className="w-full px-4 py-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-[#1E3A5F] focus:border-[#1E3A5F] outline-none transition text-sm"
            />
          </div>
          <button
            onClick={handleLogin}
            disabled={loginLoading}
            className="w-full bg-[#1E3A5F] text-white py-3 rounded-lg hover:bg-[#0F2340] transition font-medium text-sm tracking-wide shadow-md hover:shadow-lg disabled:opacity-50"
          >
            {loginLoading ? '登录中...' : '登录系统'}
          </button>
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          RAGFlow Powered &middot; 安全可信
        </p>
      </div>
    </div>
  );
}

function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="bg-slate-50 border border-slate-200/60 rounded-lg mb-2 overflow-hidden">
      <button
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 cursor-pointer hover:text-slate-500 transition w-full"
        onClick={() => setOpen(!open)}
      >
        <svg
          className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
        <span className="italic">思考过程</span>
      </button>
      {open && (
        <pre className="px-3 pb-2 text-xs text-slate-400 italic leading-relaxed max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words m-0 font-[family-name:var(--font-mono)]">
          {text}
        </pre>
      )}
    </div>
  );
}

// 处理内容中的引用标记，替换为可点击徽章
function processCitationMarkers(content: string, refs: Reference[] | undefined): string {
  if (!refs || refs.length === 0 || !content) return content;

  // 建立 ID -> 序号的映射
  const idToIndex: Map<string, number> = new Map();
  refs.forEach((ref, idx) => {
    const refId = ref.id || ref.chunk_id || '';
    if (refId) idToIndex.set(refId, idx + 1);
  });

  // 替换 [ID: xxx] 格式的标记
  return content.replace(/\[ID:\s*([a-f0-9]+)\]/gi, (match, id) => {
    const index = idToIndex.get(id);
    if (index) {
      return `<span class="cite-badge" data-cite-index="${index}">${index}</span>`;
    }
    return match;
  });
}

function ReferenceSection({
  refs,
  expanded,
  onToggle,
}: {
  refs: Reference[];
  expanded: Set<number>;
  onToggle: (idx: number) => void;
}) {
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const [overlayImg, setOverlayImg] = useState<{
    url: string;
    doc: string;
    page: string;
  } | null>(null);

  useEffect(() => {
    if (highlightIdx !== null) {
      const timer = setTimeout(() => setHighlightIdx(null), 2000);
      return () => clearTimeout(timer);
    }
  }, [highlightIdx]);

  // Auto-expand when <= 3 refs
  useEffect(() => {
    if (refs.length <= 3) {
      onToggle(0); // trigger expand
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="mt-2.5 pt-2.5 border-t border-slate-100">
        <button
          className="flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-600 transition mb-2"
          onClick={() => onToggle(-1)}
        >
          <svg
            className={`w-3 h-3 transition-transform ${refs.length <= 3 || expanded.has(-1) ? 'rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          引用来源 ({refs.length})
        </button>
        <div
          className={`space-y-1.5 max-h-[300px] overflow-y-auto ${refs.length <= 3 || expanded.has(-1) ? '' : 'hidden'}`}
        >
          {refs.map((ref, idx) => {
            // 兼容多种字段名
            const docName = ref.docnm_kwd || ref.document_name || '未知文档';
            // positions 可能是 [[页码, ...], ...] 或 [页码, x, y, w, h]
            let page = '';
            if (ref.positions) {
              if (Array.isArray(ref.positions[0])) {
                page = String(ref.positions[0][0] || '');
              } else if (typeof ref.positions[0] === 'number') {
                page = String(ref.positions[0]);
              }
            }
            const snippet = (ref.content_with_weight || ref.content || '').slice(0, 120);
            const imageId = ref.image_id || ref.img_id || '';
            const imgUrl = imageId
              ? `/api/v1/documents/images/${imageId}`
              : '';

            return (
              <div
                key={idx}
                id={`ref-${idx}`}
                className={`flex gap-2.5 p-2.5 rounded-lg border hover:shadow-sm transition cursor-pointer ${
                  highlightIdx === idx
                    ? 'border-[#0F2340]/30 bg-[#0F2340]/5'
                    : 'border-slate-100 hover:border-slate-200 bg-slate-50/50'
                }`}
                onClick={() => {
                  if (imgUrl) setOverlayImg({ url: imgUrl, doc: docName, page: String(page) });
                }}
              >
                {imgUrl && (
                  <img
                    src={imgUrl}
                    className="w-12 h-16 rounded object-cover border border-slate-100 shrink-0 bg-slate-100"
                    loading="lazy"
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] font-semibold text-[#0F2340] bg-slate-100 px-1.5 py-0.5 rounded">
                      {idx + 1}
                    </span>
                    <span className="text-[11px] text-slate-500 truncate">
                      {docName}
                    </span>
                    {page ? (
                      <span className="text-[10px] text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded shrink-0">
                        P{page}
                      </span>
                    ) : null}
                  </div>
                  <div className="text-[11px] text-slate-400 leading-relaxed line-clamp-2">
                    {snippet}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Image overlay */}
      {overlayImg && (
        <div
          className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-8"
          onClick={() => setOverlayImg(null)}
        >
          <div className="bg-white rounded-2xl shadow-2xl max-w-3xl max-h-[90vh] overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
              <span className="text-sm font-medium text-slate-700">
                {overlayImg.doc}
                {overlayImg.page ? ` - 第${overlayImg.page}页` : ''}
              </span>
              <button
                onClick={() => setOverlayImg(null)}
                className="text-slate-400 hover:text-slate-600 transition p-1"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-4 flex items-center justify-center bg-slate-50">
              <img
                src={overlayImg.url}
                className="max-w-full max-h-[75vh] rounded border border-slate-200"
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// --- Utility ---
function showToast(message: string) {
  // Dynamic toast creation for non-React context
  const toast = document.createElement('div');
  toast.className =
    'fixed top-4 right-4 bg-red-600 text-white px-5 py-3 rounded-lg shadow-lg text-sm z-[9999] transition-all';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}
