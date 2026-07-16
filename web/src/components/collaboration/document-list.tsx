import { CendTooltip } from '@/components/ui/tooltip';
import { FileUp, FolderPlus } from 'lucide-react';
import { useCallback, useState } from 'react';
import DocxImportDialog from './docx-import-dialog';
import FolderTree, { DocumentNode, FolderNode } from './folder-tree';
import FormatRulePanel from './format-rule-panel';

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

interface Props {
  selectedId: string | null;
  onSelect: (doc: DocumentNode) => void;
  documents: DocumentNode[];
  folders: FolderNode[];
  loading: boolean;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onRefresh: () => void;
  onCreateFolder: (name: string, parentId: string | null) => Promise<void>;
  onDeleteFolder: (folderId: string) => Promise<void>;
  onShare: (doc: DocumentNode) => void;
  onApplyFormatRule?: (rule: FormatRule) => void;
  applyingRuleId?: string | null;
  collapsed?: boolean;
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
  folders,
  loading,
  apiFetch,
  onRefresh,
  onCreateFolder,
  onDeleteFolder,
  onShare,
  onApplyFormatRule,
  applyingRuleId,
  collapsed = false,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<DocumentNode | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const currentUserId = getCurrentUserId();

  const handleRenameStart = (docId: string) => {
    const doc = documents.find((d) => d.id === docId);
    if (doc) {
      setRenamingId(docId);
      setRenameValue(doc.name);
    }
  };

  const handleRenameSubmit = async (docId: string) => {
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

  const handleRenameCancel = () => {
    setRenamingId(null);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    try {
      await apiFetch(`/api/v1/collaboration/documents/${deleteTarget.id}`, {
        method: 'DELETE',
      });
      if (selectedId === deleteTarget.id) {
        onSelect(null as unknown as DocumentNode);
      }
      setDeleteTarget(null);
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

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) {
      setShowNewFolder(false);
      return;
    }
    await onCreateFolder(newFolderName.trim(), null);
    setNewFolderName('');
    setShowNewFolder(false);
  };

  const handleApplyRule = useCallback(
    (rule: FormatRule) => {
      onApplyFormatRule?.(rule);
    },
    [onApplyFormatRule],
  );

  return (
    <div
      className={`shrink-0 border-r border-[#D4D4D4] bg-white flex flex-col transition-[width] duration-300 ease-in-out overflow-hidden ${
        collapsed ? 'w-0 border-r-0' : 'w-60'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-4 pb-2 whitespace-nowrap">
        <div className="flex items-center gap-2">
          <span className="text-[#333333] text-[13px] font-semibold tracking-widest uppercase">
            文档
          </span>
          {(documents.length > 0 || folders.length > 0) && (
            <span className="text-[10px] text-[#8A8A8A]">
              {documents.length + folders.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-0.5">
          <CendTooltip title="导入 Word">
            <button
              className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
              onClick={() => setShowImport(true)}
            >
              <FileUp className="size-3.5" />
            </button>
          </CendTooltip>
          <CendTooltip title="新建文件夹">
            <button
              className="w-6 h-6 flex items-center justify-center rounded text-black/40 hover:text-amber-500 hover:bg-amber-50 transition-colors"
              onClick={() => setShowNewFolder(true)}
            >
              <FolderPlus className="size-3.5" />
            </button>
          </CendTooltip>
        </div>
      </div>

      {/* New folder input */}
      {showNewFolder && (
        <div className="px-3 pb-2">
          <input
            type="text"
            className="w-full px-2 py-1 text-xs border border-amber-300 rounded focus:outline-none focus:border-amber-400"
            placeholder="文件夹名称..."
            value={newFolderName}
            autoFocus
            onChange={(e) => setNewFolderName(e.target.value)}
            onBlur={handleCreateFolder}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateFolder();
              if (e.key === 'Escape') setShowNewFolder(false);
            }}
          />
        </div>
      )}

      {/* Tree / Document List */}
      {loading && documents.length === 0 && folders.length === 0 ? (
        <div className="flex items-center justify-center py-8">
          <div className="w-5 h-5 border-2 border-[#A3A3A3] border-t-[#000000] rounded-full animate-spin" />
        </div>
      ) : documents.length === 0 && folders.length === 0 ? (
        <div className="text-center py-8 text-xs text-[#333333]">暂无文档</div>
      ) : (
        <FolderTree
          folders={folders}
          documents={documents}
          selectedId={selectedId}
          onSelect={onSelect}
          onRename={handleRenameStart}
          onDelete={setDeleteTarget}
          onTogglePermission={handleTogglePermission}
          onShare={onShare}
          onCreateFolder={(parentId) => {
            const name = prompt('文件夹名称:');
            if (name?.trim()) {
              onCreateFolder(name.trim(), parentId);
            }
          }}
          onDeleteFolder={(folderId) => {
            if (confirm('确定要删除此文件夹吗？其中的文档将移至根目录。')) {
              onDeleteFolder(folderId);
            }
          }}
          renamingId={renamingId}
          renameValue={renameValue}
          onRenameValueChange={setRenameValue}
          onRenameSubmit={handleRenameSubmit}
          onRenameCancel={handleRenameCancel}
          currentUserId={currentUserId}
          collapsed={collapsed}
        />
      )}

      {/* Import dialog */}
      {showImport && (
        <DocxImportDialog
          apiFetch={apiFetch}
          onImported={() => {
            setShowImport(false);
            onRefresh();
          }}
          onClose={() => setShowImport(false)}
        />
      )}

      {/* Format Rules Section */}
      <FormatRulePanel
        apiFetch={apiFetch}
        selectedDocId={selectedId}
        onApplyRule={handleApplyRule}
        applyingRuleId={applyingRuleId}
      />

      {/* Delete confirmation */}
      {!!deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/40"
            onClick={() => setDeleteTarget(null)}
          />
          <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold text-[#1A1A1A]">确认删除</h2>
            <p className="text-sm text-[#8A8A8A] mt-2">
              确定要删除文档「{deleteTarget?.name}」吗？此操作不可撤销。
            </p>
            <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6">
              <button
                onClick={() => setDeleteTarget(null)}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A] mt-2 sm:mt-0"
              >
                取消
              </button>
              <button
                onClick={handleDeleteConfirm}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 bg-red-700 hover:bg-red-800 text-white"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
