import {
  FileSpreadsheet,
  FileText,
  FileUp,
  FolderPlus,
  Plus,
  Search,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import DocxImportDialog from './docx-import-dialog';
import FolderTree, { DocumentNode, FolderNode } from './folder-tree';

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
  collapsed = false,
}: Props) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<DocumentNode | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [query, setQuery] = useState('');
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [showNewDoc, setShowNewDoc] = useState(false);
  const [newDocName, setNewDocName] = useState('');
  const [showNewSheet, setShowNewSheet] = useState(false);
  const [newSheetName, setNewSheetName] = useState('');
  const createMenuRef = useRef<HTMLDivElement>(null);
  const currentUserId = getCurrentUserId();

  // Close create menu on outside click
  useEffect(() => {
    if (!showCreateMenu) return;
    const handler = (e: MouseEvent) => {
      if (
        createMenuRef.current &&
        !createMenuRef.current.contains(e.target as Node)
      ) {
        setShowCreateMenu(false);
      }
    };
    window.document.addEventListener('mousedown', handler);
    return () => window.document.removeEventListener('mousedown', handler);
  }, [showCreateMenu]);

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

  const handleCreateDoc = async () => {
    const name = newDocName.trim();
    if (!name) {
      setShowNewDoc(false);
      return;
    }
    try {
      const resp = await apiFetch('/api/v1/collaboration/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, markdown_content: '' }),
      });
      const result = await resp.json();
      setNewDocName('');
      setShowNewDoc(false);
      onRefresh();
      if (result.code === 0 && result.data?.id) {
        const node: DocumentNode = {
          id: result.data.id,
          name: result.data.name || '',
          file_type: result.data.file_type || '',
          folder_id: result.data.folder_id || null,
          created_by: result.data.created_by,
          update_time: result.data.update_time,
          create_time: result.data.create_time,
          permission: result.data.permission,
        };
        onSelect(node);
      }
    } catch (e) {
      console.error('创建文档失败:', e);
    }
  };

  const handleCreateSheet = async () => {
    const name = newSheetName.trim();
    if (!name) {
      setShowNewSheet(false);
      return;
    }
    try {
      const resp = await apiFetch(
        '/api/v1/collaboration/documents/spreadsheet',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        },
      );
      const result = await resp.json();
      setNewSheetName('');
      setShowNewSheet(false);
      onRefresh();
      if (result.code === 0 && result.data?.id) {
        const node: DocumentNode = {
          id: result.data.id,
          name: result.data.name || '',
          file_type: result.data.file_type || '',
          folder_id: result.data.folder_id || null,
          created_by: result.data.created_by,
          update_time: result.data.update_time,
          create_time: result.data.create_time,
          permission: result.data.permission,
        };
        onSelect(node);
      }
    } catch (e) {
      console.error('创建表格失败:', e);
    }
  };

  const q = query.trim().toLowerCase();
  const filteredDocuments = q
    ? documents.filter((d) => d.name.toLowerCase().includes(q))
    : documents;

  return (
    <div
      className={`shrink-0 border-r border-[#D4D4D4] bg-white flex flex-col transition-[width] duration-300 ease-in-out overflow-hidden ${
        collapsed ? 'w-0 border-r-0' : 'w-60'
      }`}
    >
      {/* Header + New button */}
      <div className="flex items-center justify-between px-3 pt-3 pb-1 whitespace-nowrap">
        <div className="flex items-center gap-2">
          <span className="text-[#333333] text-[13px] font-semibold tracking-widest uppercase">
            文档
          </span>
          {(filteredDocuments.length > 0 || folders.length > 0) && (
            <span className="text-[10px] text-[#8A8A8A]">
              {filteredDocuments.length + folders.length}
            </span>
          )}
        </div>
        <div className="relative">
          {showCreateMenu && (
            <div
              ref={createMenuRef}
              className="absolute top-full right-0 mt-1 w-36 bg-white border border-stone-200 rounded-lg shadow-lg py-1 z-50"
            >
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                onClick={() => {
                  setShowCreateMenu(false);
                  setShowNewDoc(true);
                }}
              >
                <FileText className="size-3.5" />
                新建文档
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                onClick={() => {
                  setShowCreateMenu(false);
                  setShowNewFolder(true);
                }}
              >
                <FolderPlus className="size-3.5" />
                新建文件夹
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                onClick={() => {
                  setShowCreateMenu(false);
                  setShowImport(true);
                }}
              >
                <FileUp className="size-3.5" />
                导入 Word
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                onClick={() => {
                  setShowCreateMenu(false);
                  setShowNewSheet(true);
                }}
              >
                <FileSpreadsheet className="size-3.5" />
                新建表格
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                onClick={() => {
                  setShowCreateMenu(false);
                  setShowImport(true);
                }}
              >
                <FileSpreadsheet className="size-3.5" />
                导入 Excel
              </button>
            </div>
          )}
          <button
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-white bg-stone-900 hover:bg-stone-700 rounded-lg transition-colors"
            onClick={() => setShowCreateMenu((v) => !v)}
          >
            <Plus className="size-3" />
            新建
          </button>
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

      {/* New doc input */}
      {showNewDoc && (
        <div className="px-3 pb-1">
          <input
            type="text"
            className="w-full px-2 py-1 text-xs border border-stone-300 rounded focus:outline-none focus:border-stone-500"
            placeholder="文档名称..."
            value={newDocName}
            autoFocus
            onChange={(e) => setNewDocName(e.target.value)}
            onBlur={handleCreateDoc}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateDoc();
              if (e.key === 'Escape') setShowNewDoc(false);
            }}
          />
        </div>
      )}

      {/* New spreadsheet input */}
      {showNewSheet && (
        <div className="px-3 pb-1">
          <input
            type="text"
            className="w-full px-2 py-1 text-xs border border-stone-300 rounded focus:outline-none focus:border-stone-500"
            placeholder="表格名称..."
            value={newSheetName}
            autoFocus
            onChange={(e) => setNewSheetName(e.target.value)}
            onBlur={handleCreateSheet}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateSheet();
              if (e.key === 'Escape') setShowNewSheet(false);
            }}
          />
        </div>
      )}

      {/* Search box */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-stone-300 pointer-events-none" />
          <input
            className="w-full pl-7 pr-2 py-1.5 text-xs bg-stone-50 border border-stone-200 rounded-lg outline-none focus:border-stone-400 placeholder:text-stone-300"
            placeholder="搜索文档..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Tree / Document List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading && filteredDocuments.length === 0 && folders.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-[#A3A3A3] border-t-[#000000] rounded-full animate-spin" />
          </div>
        ) : filteredDocuments.length === 0 && folders.length === 0 ? (
          <div className="text-center py-8 text-xs text-[#333333]">
            暂无文档
          </div>
        ) : (
          <FolderTree
            folders={folders}
            documents={filteredDocuments}
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
      </div>

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
