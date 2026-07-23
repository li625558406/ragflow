import { CendTooltip } from '@/components/ui/tooltip';
import {
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  Folder,
  FolderPlus,
  NotebookText,
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
  /** 服务端直出的 owner 标识，比前端 UUID 比对更可靠 */
  is_owner?: boolean;
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
        className="group relative flex items-center gap-1.5 py-1.5 pr-1 hover:bg-[#EAEAEA] rounded-lg cursor-pointer transition-colors overflow-hidden"
        style={{ paddingLeft: `${10 + depth * 12}px` }}
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
        <span className="text-[13px] font-medium text-[#333333] truncate flex-1 min-w-0 pr-1">
          {node.name}
        </span>
        {/* 悬浮按钮 — 绝对定位 + 渐变蒙版，不压缩标题 */}
        <div
          className="absolute right-0 top-1/2 -translate-y-1/2 flex items-center gap-0.5 pl-8 pr-1 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity duration-150"
          style={{
            background:
              'linear-gradient(to left, #EAEAEA 55%, #EAEAEAcc 75%, #EAEAEA00 100%)',
          }}
        >
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
}) {
  // 用服务端返回的 is_owner 替代客户端 UUID 比对，避免多账号/team sharing 场景误判
  const isOwner = doc.is_owner === true;
  const isSelected = selectedId === doc.id;
  // 按钮渐变蒙版起点色：与行 hover/选中态背景色一致，保证视觉无缝
  const fadeFromColor = '#EAEAEA';

  return (
    <div
      role="button"
      tabIndex={0}
      className={`group relative w-full flex items-center gap-2 py-1.5 pr-1 rounded-lg cursor-pointer transition text-left overflow-hidden ${
        isSelected
          ? 'bg-[#EAEAEA] text-[#000000]'
          : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
      }`}
      style={{ paddingLeft: `${10 + depth * 12}px` }}
      onClick={() => onSelect(doc)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect(doc);
      }}
    >
      {/* 文件图标 — xlsx 用表格网格图标(绿)，docx 用笔记本图标(蓝)，
          轮廓差异最大化，size-4 也能瞬间分辨 */}
      {doc.file_type === 'xlsx' ? (
        <FileSpreadsheet
          className={`size-4 shrink-0 ${isSelected ? 'text-[#000000]' : 'text-emerald-600'}`}
        />
      ) : (
        <NotebookText
          className={`size-4 shrink-0 ${isSelected ? 'text-[#000000]' : 'text-blue-600'}`}
        />
      )}

      {/* 标题 + 元信息 — min-w-0 + flex-1 保证 truncate 生效 */}
      <div className="min-w-0 flex-1 pr-1">
        {renamingId === doc.id ? (
          <input
            type="text"
            className="w-full px-2 py-0.5 text-xs border border-indigo-300 rounded focus:outline-none text-stone-900"
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
          <>
            {/* 单行标题 — 不再与按钮共享 flex 行，避免被永久压缩 */}
            <span className="text-[13px] font-medium truncate block">
              {doc.name}
            </span>
            <div className="flex items-center gap-1 mt-0.5">
              <span className="text-[10px] text-[#A3A3A3] truncate">
                {doc.file_type.toUpperCase()}
              </span>
              {doc.permission === 'team' && (
                <span className="text-[9px] px-1 py-px rounded bg-[#fef3c7] text-[#d97706] border border-[#fde68a]">
                  团队
                </span>
              )}
              {!isOwner && (
                <span className="text-[9px] px-1 py-px rounded bg-[#EAEAEA] text-[#000000] border border-[#D4D4D4]">
                  共享
                </span>
              )}
            </div>
          </>
        )}
      </div>

      {/* 悬浮操作按钮组 — 绝对定位 + 渐变蒙版，不占用标题的 flex 空间。
          渐变 from 行背景色 → 透明，保证标题文字被自然遮罩、按钮浮在最上层。
          仅在 owner 且非重命名态显示。 */}
      {renamingId !== doc.id && isOwner && (
        <div
          className="absolute right-0 top-1/2 -translate-y-1/2 flex items-center gap-0.5 pl-10 pr-1 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity duration-150"
          style={{
            background: `linear-gradient(to left, ${fadeFromColor} 55%, ${fadeFromColor}cc 75%, ${fadeFromColor}00 100%)`,
          }}
        >
          <CendTooltip
            title={doc.permission === 'team' ? '团队共享中' : '仅自己可见'}
          >
            <button
              className={`w-5 h-5 flex items-center justify-center rounded ${
                doc.permission === 'team'
                  ? 'text-amber-500 hover:text-amber-600 hover:bg-amber-50'
                  : 'text-[#555555] hover:text-[#1A1A1A] hover:bg-black/[0.06]'
              }`}
              onClick={(e) => {
                e.stopPropagation();
                onTogglePermission(doc.id, doc.permission || 'me');
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
          </CendTooltip>
          <CendTooltip title="分享">
            <button
              className="w-5 h-5 flex items-center justify-center rounded text-[#555555] hover:text-indigo-600 hover:bg-indigo-50"
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
              className="w-5 h-5 flex items-center justify-center rounded text-[#555555] hover:text-[#1A1A1A] hover:bg-black/[0.06]"
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
              className="w-5 h-5 flex items-center justify-center rounded text-[#555555] hover:text-red-500 hover:bg-red-50"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(doc);
              }}
            >
              <Trash2 className="size-3" />
            </button>
          </CendTooltip>
        </div>
      )}
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
        />
      ))}
    </div>
  );
}
