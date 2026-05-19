import { useCallback, useEffect, useState } from 'react';

interface DocumentItem {
  id: string;
  name: string;
  file_type: string;
  agent_id: string;
  create_time: string;
  update_time: string;
}

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

interface Props {
  selectedId: string | null;
  onSelect: (doc: DocumentItem) => void;
  documents: DocumentItem[];
  loading: boolean;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onRefresh: () => void;
}

export default function DocumentList({
  selectedId,
  onSelect,
  documents,
  loading,
  apiFetch,
  onRefresh,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [rules, setRules] = useState<FormatRule[]>([]);
  const [showRules, setShowRules] = useState(false);
  const [applyingRule, setApplyingRule] = useState<string | null>(null);

  const loadRules = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/v1/collaboration/format-rules');
      const result = await resp.json();
      if (result.code === 0) {
        setRules(result.data || []);
      }
    } catch (e) {
      console.error('加载格式规则失败:', e);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  const handleRename = async (docId: string) => {
    if (!renameValue.trim()) {
      setRenamingId(null);
      return;
    }
    try {
      await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: renameValue.trim() }),
      });
      setRenamingId(null);
      onRefresh();
    } catch (e) {
      console.error('重命名失败:', e);
    }
  };

  const handleDelete = async (docId: string) => {
    if (!window.confirm('确定删除此文档？')) return;
    try {
      await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'DELETE',
      });
      if (selectedId === docId) {
        onSelect(null as unknown as DocumentItem);
      }
      onRefresh();
    } catch (e) {
      console.error('删除失败:', e);
    }
  };

  const handleApplyRule = async (docId: string, ruleId: string) => {
    setApplyingRule(ruleId);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/apply-rule`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule_id: ruleId }),
        },
      );
      if (resp.ok) {
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'document.docx';
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      console.error('应用规则失败:', e);
    } finally {
      setApplyingRule(null);
    }
  };

  return (
    <div className="w-64 flex-shrink-0 border-r border-stone-100 flex flex-col bg-stone-50/50">
      {/* Header */}
      <div className="px-3 py-3 border-b border-stone-100">
        <h3 className="text-sm font-semibold text-stone-900">文档列表</h3>
      </div>

      {/* Document List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-indigo-300 border-t-indigo-500 rounded-full animate-spin" />
          </div>
        ) : documents.length === 0 ? (
          <div className="text-center py-8 text-xs text-stone-400">
            暂无文档
          </div>
        ) : (
          <div className="py-1">
            {documents.map((doc) => (
              <div
                key={doc.id}
                className={`group px-3 py-2.5 cursor-pointer transition-colors ${
                  selectedId === doc.id
                    ? 'bg-white border-r-2 border-indigo-500'
                    : 'hover:bg-stone-100'
                }`}
                onClick={() => onSelect(doc)}
              >
                {renamingId === doc.id ? (
                  <input
                    type="text"
                    className="w-full px-2 py-1 text-xs border border-indigo-300 rounded focus:outline-none text-stone-900"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => handleRename(doc.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleRename(doc.id);
                      if (e.key === 'Escape') setRenamingId(null);
                    }}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-stone-900 truncate flex-1">
                        {doc.name}
                      </span>
                      <div className="hidden group-hover:flex items-center gap-0.5 ml-1">
                        {/* Rename */}
                        <button
                          className="p-0.5 text-stone-400 hover:text-stone-600"
                          title="重命名"
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(doc.id);
                            setRenameValue(doc.name);
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
                              d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
                            />
                          </svg>
                        </button>
                        {/* Delete */}
                        <button
                          className="p-0.5 text-stone-400 hover:text-red-500"
                          title="删除"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(doc.id);
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
                    <div className="flex items-center gap-1 mt-0.5">
                      <span className="text-[10px] text-stone-400 font-mono uppercase">
                        {doc.file_type}
                      </span>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Format Rules Section */}
      <div className="border-t border-stone-100">
        <button
          className="w-full px-3 py-2 text-xs text-stone-500 hover:text-stone-700 hover:bg-stone-100 flex items-center gap-1.5 transition-colors"
          onClick={() => setShowRules(!showRules)}
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
              d="M4 6h16M4 12h16M4 18h7"
            />
          </svg>
          格式规则
          <svg
            className={`w-3 h-3 ml-auto transition-transform ${showRules ? 'rotate-180' : ''}`}
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
        {showRules && (
          <div className="px-2 pb-2">
            {rules.length === 0 ? (
              <p className="text-[10px] text-stone-400 px-1">暂无格式规则</p>
            ) : (
              rules.map((rule) => (
                <div
                  key={rule.id}
                  className="flex items-center justify-between px-1 py-1 text-xs"
                >
                  <span className="text-stone-600 truncate flex-1">
                    {rule.name}
                  </span>
                  {selectedId && (
                    <button
                      className="text-[10px] text-indigo-500 hover:text-indigo-700 disabled:opacity-30 px-1"
                      disabled={applyingRule === rule.id}
                      onClick={() => handleApplyRule(selectedId!, rule.id)}
                    >
                      {applyingRule === rule.id ? '...' : '应用'}
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
