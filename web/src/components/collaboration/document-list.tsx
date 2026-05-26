import { useCallback, useState } from 'react';
import FormatRulePanel from './format-rule-panel';

interface DocumentItem {
  id: string;
  name: string;
  file_type: string;
  agent_id: string;
  create_time: string;
  update_time: string;
  created_by?: string;
  permission?: string;
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
  onApplyFormatRule?: (rule: FormatRule) => void;
  applyingRuleId?: string | null;
}

function getCurrentUserId(): string | null {
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || 'null');
    return userInfo?.id || userInfo?.user_id || null;
  } catch {
    return null;
  }
}

export default function DocumentList({
  selectedId,
  onSelect,
  documents,
  loading,
  apiFetch,
  onRefresh,
  onApplyFormatRule,
  applyingRuleId,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const currentUserId = getCurrentUserId();

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

  const handleTogglePermission = async (docId: string, current: string) => {
    const next = current === 'team' ? 'me' : 'team';
    try {
      await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ permission: next }),
      });
      onRefresh();
    } catch (e) {
      console.error('修改权限失败:', e);
    }
  };

  const handleApplyRule = useCallback(
    (rule: FormatRule) => {
      onApplyFormatRule?.(rule);
    },
    [onApplyFormatRule],
  );

  return (
    <div className="w-56 shrink-0 border-r border-[rgba(124,92,252,0.06)] bg-white flex flex-col">
      {/* Header */}
      <div className="px-4 pt-4 pb-2">
        <span className="text-[#9494b5] text-[10px] font-semibold tracking-widest uppercase">
          文档列表
        </span>
      </div>

      {/* Document List */}
      <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-[#c4b5fd] border-t-[#7c5cfc] rounded-full animate-spin" />
          </div>
        ) : documents.length === 0 ? (
          <div className="text-center py-8 text-xs text-[#9494b5]">
            暂无文档
          </div>
        ) : (
          <>
            {documents.map((doc) => (
              <button
                key={doc.id}
                className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group ${
                  selectedId === doc.id
                    ? 'bg-[#ede9fe] text-[#7c5cfc]'
                    : 'text-[#5a5a7a] hover:bg-[#f4f1fb] hover:text-[#1c1c2e]'
                }`}
                onClick={() => onSelect(doc)}
              >
                <div
                  className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                    selectedId === doc.id ? 'bg-white' : 'bg-[#f4f1fb]'
                  }`}
                >
                  <svg
                    className={`w-4 h-4 ${selectedId === doc.id ? 'text-[#7c5cfc]' : 'text-[#9494b5]'}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
                    />
                  </svg>
                </div>
                <div className="min-w-0 flex-1">
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
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate">
                        {doc.name}
                      </span>
                      <div className="hidden group-hover:flex items-center gap-0.5 ml-1">
                        {doc.created_by === currentUserId && (
                          <>
                            <button
                              className={`p-0.5 ${
                                doc.permission === 'team'
                                  ? 'text-amber-500 hover:text-amber-600'
                                  : 'text-stone-300 hover:text-stone-500'
                              }`}
                              title={
                                doc.permission === 'team'
                                  ? '团队共享中'
                                  : '仅自己可见'
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                handleTogglePermission(
                                  doc.id,
                                  doc.permission || 'me',
                                );
                              }}
                            >
                              {doc.permission === 'team' ? (
                                <svg
                                  className="w-3 h-3"
                                  fill="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM7.07 18.28c.43-.9 3.05-1.78 4.93-1.78s4.51.88 4.93 1.78C15.57 19.36 13.86 20 12 20s-3.57-.64-4.93-1.72zm11.29-1.45c-1.43-1.74-4.9-2.33-6.36-2.33s-4.93.59-6.36 2.33C4.62 15.49 4 13.82 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8c0 1.82-.62 3.49-1.64 4.83zM12 6c-1.94 0-3.5 1.56-3.5 3.5S10.06 13 12 13s3.5-1.56 3.5-3.5S13.94 6 12 6zm0 5c-.83 0-1.5-.67-1.5-1.5S11.17 8 12 8s1.5.67 1.5 1.5S12.83 11 12 11z" />
                                </svg>
                              ) : (
                                <svg
                                  className="w-3 h-3"
                                  fill="currentColor"
                                  viewBox="0 0 24 24"
                                >
                                  <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zM12 17c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z" />
                                </svg>
                              )}
                            </button>
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
                          </>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="flex items-center gap-1 mt-0.5">
                    <span className="text-[11px] text-[#9494b5] truncate">
                      {doc.file_type.toUpperCase()}
                    </span>
                    {doc.permission === 'team' && (
                      <span className="text-[9px] px-1 py-px rounded bg-[#fef3c7] text-[#d97706] border border-[#fde68a]">
                        团队
                      </span>
                    )}
                    {currentUserId && doc.created_by !== currentUserId && (
                      <span className="text-[9px] px-1 py-px rounded bg-[#ede9fe] text-[#7c5cfc] border border-[#ddd6fe]">
                        共享
                      </span>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </>
        )}
      </div>

      {/* Format Rules Section */}
      <FormatRulePanel
        apiFetch={apiFetch}
        selectedDocId={selectedId}
        onApplyRule={handleApplyRule}
        applyingRuleId={applyingRuleId}
      />
    </div>
  );
}
