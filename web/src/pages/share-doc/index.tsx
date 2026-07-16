import { FileText, Lock } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router';

interface SharedDocData {
  id: string;
  name: string;
  content: Record<string, unknown>;
  markdown_content: string;
  permission: string;
  has_password: boolean;
}

const BASE_URL = '/api/v1';

async function apiFetch(url: string, options?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${url}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
}

export default function ShareDocPage() {
  const { token } = useParams<{ token: string }>();
  const [doc, setDoc] = useState<SharedDocData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [needsPassword, setNeedsPassword] = useState(false);
  const [verifying, setVerifying] = useState(false);

  const loadDoc = useCallback(
    async (pwd?: string) => {
      if (!token) return;
      setLoading(true);
      setError('');
      try {
        // Prefer POST /verify for password-protected docs (no password in URL)
        if (pwd) {
          const resp = await apiFetch(`/share/doc/${token}/verify`, {
            method: 'POST',
            body: JSON.stringify({ password: pwd }),
          });
          const result = await resp.json();
          if (result.code === 0) {
            setDoc(result.data);
            setNeedsPassword(false);
          } else if (resp.status === 401 || result.code === 102) {
            // 102 = OPERATING_ERROR (wrong password)
            setError('密码错误');
            setNeedsPassword(true);
          } else {
            setError(result.message || '验证失败');
          }
        } else {
          // Initial access: try without password
          const resp = await apiFetch(`/share/doc/${token}`);
          const result = await resp.json();
          if (result.code === 0) {
            setDoc(result.data);
            setNeedsPassword(false);
          } else if (
            result.code === 102 &&
            result.message?.includes('Password required')
          ) {
            setNeedsPassword(true);
            setError('');
          } else {
            setError(result.message || '无法访问此文档');
          }
        }
      } catch {
        setError('加载失败，请检查链接是否正确');
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    loadDoc();
  }, [loadDoc]);

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;
    setVerifying(true);
    await loadDoc(password);
    setVerifying(false);
  };

  // Render Lexical content as plain markdown (simple fallback)
  const renderContent = () => {
    if (!doc) return null;
    // Use markdown content if available
    if (doc.markdown_content) {
      return doc.markdown_content.split('\n').map((line, i) => (
        <p key={i} className="text-sm text-stone-800 leading-relaxed mb-1">
          {line || '\u00A0'}
        </p>
      ));
    }
    return <p className="text-sm text-stone-400">文档内容为空</p>;
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <div className="w-5 h-5 border-2 border-stone-200 border-t-stone-400 rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <div className="text-center max-w-sm mx-auto p-6">
          <div className="size-12 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-3">
            <FileText className="size-6 text-red-400" />
          </div>
          <h2 className="text-lg font-semibold text-stone-800 mb-1">
            无法访问
          </h2>
          <p className="text-sm text-stone-500">{error}</p>
        </div>
      </div>
    );
  }

  if (needsPassword) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-stone-50">
        <form
          onSubmit={handlePasswordSubmit}
          className="bg-white border border-stone-200 rounded-2xl shadow-sm p-6 w-80 max-w-[90vw]"
        >
          <div className="size-12 rounded-full bg-amber-50 flex items-center justify-center mx-auto mb-3">
            <Lock className="size-6 text-amber-500" />
          </div>
          <h2 className="text-lg font-semibold text-stone-800 text-center mb-1">
            密码保护
          </h2>
          <p className="text-sm text-stone-500 text-center mb-4">
            此文档需要密码才能访问
          </p>
          <input
            type="password"
            className="w-full text-sm border border-stone-200 rounded-lg px-3 py-2 mb-3 focus:outline-none focus:border-stone-400"
            placeholder="输入密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
          {error && <p className="text-xs text-red-500 mb-3">{error}</p>}
          <button
            type="submit"
            className="w-full text-sm font-medium px-4 py-2 bg-stone-900 text-white rounded-lg hover:bg-stone-800 transition-colors disabled:opacity-50"
            disabled={verifying || !password.trim()}
          >
            {verifying ? '验证中...' : '确认'}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-stone-50">
      {/* Header */}
      <div className="bg-white border-b border-stone-200">
        <div className="max-w-4xl mx-auto px-6 py-3 flex items-center justify-between">
          <h1 className="text-sm font-semibold text-stone-900 truncate">
            {doc?.name || '分享文档'}
          </h1>
          <span className="text-[10px] px-2 py-0.5 bg-stone-100 text-stone-500 rounded">
            {doc?.permission === 'edit' ? '可编辑' : '仅查看'}
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-4xl mx-auto px-6 py-5">
        <div className="bg-white rounded-xl border border-stone-200 shadow-sm p-6">
          <div className="prose prose-sm max-w-none">{renderContent()}</div>
        </div>
      </div>
    </div>
  );
}
