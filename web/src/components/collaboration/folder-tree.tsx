import { CendTooltip } from '@/components/ui/tooltip';
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderPlus,
  Pencil,
  Share2,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';

export interface FolderNode {
  id: string;
  name: string;
  parent_id: string | null;
  created_by?: string;
  sort_order?: number;
  create_time?: string;
}

export interface DocumentNode {
  id: string;
  name: string;
  file_type: string;
  agent_id?: string;
  folder_id: string | null;
  sort_order?: number;
  create_time?: string;
  update_time?: string;
  created_by?: string;
  permission?: string;
}

interface Props {
  folders: FolderNode[];
  documents: DocumentNode[];
  selectedId: string | null;
  onSelect: (doc: DocumentNode) => void;
  onRename: (docId: string) => void;
  onDelete: (doc: DocumentNode) => void;
  onTogglePermission: (docId: string, current: string) => void;
  onShare: (doc: DocumentNode) => void;
  onCreateFolder: (parentId: string | null) => void;
  onDeleteFolder: (folderId: string) => void;
  renamingId: string | null;
  renameValue: string;
  onRenameValueChange: (val: string) => void;
  onRenameSubmit: (docId: string) => void;
  onRenameCancel: () => void;
  currentUserId: string | null;
  collapsed?: boolean;
}

function TreeNode({
  node,
  depth,
  childDocuments,
  childFolders,
  allFolders,
  selectedId,
  onSelect,
  onRename,
  onDelete,
  onTogglePermission,
  onCreateFolder,
  onDeleteFolder,
  renamingId,
  renameValue,
  onRenameValueChange,
  onRenameSubmit,
  onRenameCancel,
  currentUserId,
  onShare,
  allDocuments,
}: {
  node: FolderNode;
  depth: number;
  childDocuments: DocumentNode[];
  childFolders: FolderNode[];
  allFolders: FolderNode[];
  allDocuments: DocumentNode[];
  selectedId: string | null;
  onSelect: (doc: DocumentNode) => void;
  onRename: (docId: string) => void;
  onDelete: (doc: DocumentNode) => void;
  onTogglePermission: (docId: string, current: string) => void;
  onShare: (doc: DocumentNode) => void;
  onCreateFolder: (parentId: string | null) => void;
  onDeleteFolder: (folderId: string) => void;
  renamingId: string | null;
  renameValue: string;
  onRenameValueChange: (val: string) => void;
  onRenameSubmit: (docId: string) => void;
  onRenameCancel: () => void;
  currentUserId: string | null;
}) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div>
      {/* Folder row */}
      <div
        className="group flex items-center gap-1.5 py-1.5 px-2 hover:bg-[#EAEAEA] rounded-lg cursor-pointer transition-colors"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="shrink-0 text-black/40">
          {expanded ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </span>
        <Folder className="size-3.5 shrink-0 text-amber-500" />
        <span className="text-[13px] font-medium text-[#333333] truncate flex-1 min-w-0">
          {node.name}
        </span>
        <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 shrink-0">
          <CendTooltip title="新建子文件夹">
            <button
              className="w-5 h-5 flex items-center justify-center rounded text-black/40 hover:text-black/70 hover:bg-black/[0.06]"
              onClick={(e) => {
                e.stopPropagation();
                onCreateFolder(node.id);
              }}
            >
              <FolderPlus className="size-3" />
            </button>
          </CendTooltip>
          <CendTooltip title="删除文件夹">
            <button
              className="w-5 h-5 flex items-center justify-center rounded text-black/40 hover:text-red-500 hover:bg-red-50"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteFolder(node.id);
              }}
            >
              <Trash2 className="size-3" />
            </button>
          </CendTooltip>
        </div>
      </div>

      {/* Children */}
      {expanded && (
        <div>
          {childFolders.map((f) => {
            const grandChildren = allFolders.filter(
              (ff) => ff.parent_id === f.id,
            );
            return (
              <TreeNode
                key={f.id}
                node={f}
                depth={depth + 1}
                childDocuments={allDocuments
                  .filter((d) => d.folder_id === f.id)
                  .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))}
                childFolders={grandChildren}
                allFolders={allFolders}
                allDocuments={allDocuments}
                selectedId={selectedId}
                onSelect={onSelect}
                onRename={onRename}
                onDelete={onDelete}
                onTogglePermission={onTogglePermission}
                onShare={onShare}
                onCreateFolder={onCreateFolder}
                onDeleteFolder={onDeleteFolder}
                renamingId={renamingId}
                renameValue={renameValue}
                onRenameValueChange={onRenameValueChange}
                onRenameSubmit={onRenameSubmit}
                onRenameCancel={onRenameCancel}
                currentUserId={currentUserId}
              />
            );
          })}
          {/* Documents in this folder */}
          {childDocuments.map((doc) => (
            <DocRow
              key={doc.id}
              doc={doc}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              onRename={onRename}
              onDelete={onDelete}
              onTogglePermission={onTogglePermission}
              onShare={onShare}
              renamingId={renamingId}
              renameValue={renameValue}
              onRenameValueChange={onRenameValueChange}
              onRenameSubmit={onRenameSubmit}
              onRenameCancel={onRenameCancel}
              currentUserId={currentUserId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DocRow({
  doc,
  depth,
  selectedId,
  onSelect,
  onRename,
  onDelete,
  onTogglePermission,
  onShare,
  renamingId,
  renameValue,
  onRenameValueChange,
  onRenameSubmit,
  onRenameCancel,
  currentUserId,
}: {
  doc: DocumentNode;
  depth: number;
  selectedId: string | null;
  onSelect: (doc: DocumentNode) => void;
  onRename: (docId: string) => void;
  onDelete: (doc: DocumentNode) => void;
  onTogglePermission: (docId: string, current: string) => void;
  onShare: (doc: DocumentNode) => void;
  renamingId: string | null;
  renameValue: string;
  onRenameValueChange: (val: string) => void;
  onRenameSubmit: (docId: string) => void;
  onRenameCancel: () => void;
  currentUserId: string | null;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      className={`group w-full flex items-center gap-2 px-3 py-2 rounded-xl cursor-pointer transition text-left ${
        selectedId === doc.id
          ? 'bg-[#EAEAEA] text-[#000000]'
          : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
      }`}
      style={{ paddingLeft: `${8 + depth * 14 + 24}px` }}
      onClick={() => onSelect(doc)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect(doc);
      }}
    >
      <div
        className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${
          selectedId === doc.id ? 'bg-white' : 'bg-[#EAEAEA]'
        }`}
      >
        <FileText
          className={`size-3.5 ${selectedId === doc.id ? 'text-[#000000]' : 'text-[#333333]'}`}
        />
      </div>
      <div className="min-w-0 flex-1">
        {renamingId === doc.id ? (
          <input
            type="text"
            className="w-full px-2 py-1 text-xs border border-indigo-300 rounded focus:outline-none text-stone-900"
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onBlur={() => onRenameSubmit(doc.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onRenameSubmit(doc.id);
              if (e.key === 'Escape') onRenameCancel();
            }}
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <div className="flex items-center justify-between">
            <span className="text-[14px] font-medium truncate">{doc.name}</span>
            <div className="opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity flex items-center gap-0.5 ml-1">
              {doc.created_by === currentUserId && (
                <>
                  <CendTooltip
                    title={
                      doc.permission === 'team' ? '团队共享中' : '仅自己可见'
                    }
                  >
                    <button
                      className={`w-6 h-6 flex items-center justify-center rounded ${
                        doc.permission === 'team'
                          ? 'text-amber-500 hover:text-amber-600 hover:bg-amber-50'
                          : 'text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4]'
                      }`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onTogglePermission(doc.id, doc.permission || 'me');
                      }}
                    >
                      {doc.permission === 'team' ? (
                        <svg
                          className="w-3.5 h-3.5"
                          fill="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM7.07 18.28c.43-.9 3.05-1.78 4.93-1.78s4.51.88 4.93 1.78C15.57 19.36 13.86 20 12 20s-3.57-.64-4.93-1.72zm11.29-1.45c-1.43-1.74-4.9-2.33-6.36-2.33s-4.93.59-6.36 2.33C4.62 15.49 4 13.82 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8c0 1.82-.62 3.49-1.64 4.83zM12 6c-1.94 0-3.5 1.56-3.5 3.5S10.06 13 12 13s3.5-1.56 3.5-3.5S13.94 6 12 6zm0 5c-.83 0-1.5-.67-1.5-1.5S11.17 8 12 8s1.5.67 1.5 1.5S12.83 11 12 11z" />
                        </svg>
                      ) : (
                        <svg
                          className="w-3.5 h-3.5"
                          fill="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zM12 17c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z" />
                        </svg>
                      )}
                    </button>
                  </CendTooltip>
                  <CendTooltip title="分享">
                    <button
                      className="w-6 h-6 flex items-center justify-center rounded text-[#555555] hover:text-indigo-600 hover:bg-indigo-50"
                      onClick={(e) => {
                        e.stopPropagation();
                        onShare(doc);
                      }}
                    >
                      <Share2 className="size-3" />
                    </button>
                  </CendTooltip>
                  <CendTooltip title="重命名">
                    <button
                      className="w-6 h-6 flex items-center justify-center rounded text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4]"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRename(doc.id);
                      }}
                    >
                      <Pencil className="size-3" />
                    </button>
                  </CendTooltip>
                  <CendTooltip title="删除">
                    <button
                      className="w-6 h-6 flex items-center justify-center rounded text-[#555555] hover:text-red-500 hover:bg-red-50"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(doc);
                      }}
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </CendTooltip>
                </>
              )}
            </div>
          </div>
        )}
        <div className="flex items-center gap-1 mt-0.5">
          <span className="text-[11px] text-[#A3A3A3] truncate">
            {doc.file_type.toUpperCase()}
          </span>
          {doc.permission === 'team' && (
            <span className="text-[9px] px-1 py-px rounded bg-[#fef3c7] text-[#d97706] border border-[#fde68a]">
              团队
            </span>
          )}
          {currentUserId && doc.created_by !== currentUserId && (
            <span className="text-[9px] px-1 py-px rounded bg-[#EAEAEA] text-[#000000] border border-[#D4D4D4]">
              共享
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function FolderTree({
  folders,
  documents,
  selectedId,
  onSelect,
  onRename,
  onDelete,
  onTogglePermission,
  onCreateFolder,
  onDeleteFolder,
  renamingId,
  renameValue,
  onRenameValueChange,
  onRenameSubmit,
  onRenameCancel,
  currentUserId,
  onShare,
  collapsed = false,
}: Props) {
  const rootFolders = folders.filter((f) => !f.parent_id);
  const rootDocuments = documents
    .filter((d) => !d.folder_id)
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));

  if (collapsed) return null;

  return (
    <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
      {rootFolders.map((f) => {
        const childFolders = folders.filter((ff) => ff.parent_id === f.id);
        const childDocs = documents
          .filter((d) => d.folder_id === f.id)
          .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
        return (
          <TreeNode
            key={f.id}
            node={f}
            depth={0}
            childDocuments={childDocs}
            childFolders={childFolders}
            allFolders={folders}
            allDocuments={documents}
            selectedId={selectedId}
            onSelect={onSelect}
            onRename={onRename}
            onDelete={onDelete}
            onTogglePermission={onTogglePermission}
            onShare={onShare}
            onCreateFolder={onCreateFolder}
            onDeleteFolder={onDeleteFolder}
            renamingId={renamingId}
            renameValue={renameValue}
            onRenameValueChange={onRenameValueChange}
            onRenameSubmit={onRenameSubmit}
            onRenameCancel={onRenameCancel}
            currentUserId={currentUserId}
          />
        );
      })}
      {rootDocuments.map((doc) => (
        <DocRow
          key={doc.id}
          doc={doc}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
          onRename={onRename}
          onDelete={onDelete}
          onTogglePermission={onTogglePermission}
          onShare={onShare}
          renamingId={renamingId}
          renameValue={renameValue}
          onRenameValueChange={onRenameValueChange}
          onRenameSubmit={onRenameSubmit}
          onRenameCancel={onRenameCancel}
          currentUserId={currentUserId}
        />
      ))}
    </div>
  );
}
