import {
  Check,
  Download,
  Loader2,
  MoreHorizontal,
  Save,
  Share2,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import MemberAvatars from './member-avatars';
import type { CollaborationWebSocketProvider } from './yjs-provider';

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

interface Props {
  docId: string;
  docName: string;
  saveStatus: SaveStatus;
  version: number | null;
  provider: CollaborationWebSocketProvider | null;
  /** 非协同模式(无 token)时展示手动保存入口 */
  showManualSave: boolean;
  onManualSave: () => void;
  onDownload: (type: 'docx' | 'pdf') => void;
  downloading: boolean;
  onOpenShare: () => void;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onRenamed: () => void;
}

const STATUS_META: Record<SaveStatus, { label: string; cls: string }> = {
  idle: { label: '', cls: 'text-stone-400' },
  saving: { label: '保存中…', cls: 'text-amber-600' },
  saved: { label: '已保存', cls: 'text-emerald-600' },
  error: { label: '保存失败', cls: 'text-red-600' },
};

export default function EditorHeader({
  docId,
  docName,
  saveStatus,
  version,
  provider,
  showManualSave,
  onManualSave,
  onDownload,
  downloading,
  onOpenShare,
  apiFetch,
  onRenamed,
}: Props) {
  const [showMore, setShowMore] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameValue, setNameValue] = useState(docName);
  const [displayName, setDisplayName] = useState(docName);
  const [renameError, setRenameError] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const renamingRef = useRef(false);

  // Sync external docName changes, but skip during active rename to avoid
  // overwriting the user's in-progress input.
  useEffect(() => {
    if (renamingRef.current) return;
    setDisplayName(docName);
    setNameValue(docName);
    setRenameError(false);
  }, [docName]);

  useEffect(() => {
    if (!showMore) return;
    const handler = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false);
      }
    };
    window.document.addEventListener('mousedown', handler);
    return () => window.document.removeEventListener('mousedown', handler);
  }, [showMore]);

  const startRename = () => {
    renamingRef.current = true;
    setNameValue(displayName);
    setRenameError(false);
    setRenaming(true);
  };

  const submitRename = async () => {
    // Guard: if Escape already cleared renaming, do nothing.
    if (!renamingRef.current) return;
    const name = nameValue.trim();
    renamingRef.current = false;
    setRenaming(false);
    if (!name || name === displayName) return;
    try {
      const resp = await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setDisplayName(name);
      setRenameError(false);
      onRenamed();
    } catch (e) {
      console.error('重命名失败:', e);
      setRenameError(true);
    }
  };

  const status = STATUS_META[saveStatus];

  return (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-stone-100 gap-3">
      {/* 左侧：文档名 + 保存状态 */}
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {renaming ? (
          <input
            className={`text-sm font-semibold text-stone-900 border rounded px-1.5 py-0.5 outline-none focus:border-stone-500 max-w-xs ${
              renameError ? 'border-red-400' : 'border-stone-300'
            }`}
            value={nameValue}
            autoFocus
            onChange={(e) => {
              setNameValue(e.target.value);
              setRenameError(false);
            }}
            onBlur={submitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submitRename();
              }
              if (e.key === 'Escape') {
                renamingRef.current = false;
                setNameValue(displayName);
                setRenaming(false);
                setRenameError(false);
              }
            }}
          />
        ) : (
          <h2
            className="text-sm font-semibold text-stone-900 truncate cursor-text hover:bg-stone-50 rounded px-1 -mx-1"
            title="点击重命名"
            onClick={startRename}
          >
            {displayName}
          </h2>
        )}
        <span
          className={`text-[10px] whitespace-nowrap flex items-center gap-0.5 ${status.cls}`}
        >
          {saveStatus === 'saving' && (
            <Loader2 className="size-2.5 animate-spin" />
          )}
          {saveStatus === 'saved' && <Check className="size-2.5" />}
          {status.label}
          {version !== null && (
            <span className="text-stone-300 ml-1">· v{version}</span>
          )}
        </span>
      </div>

      {/* 右侧：成员头像 + 分享 + 更多 */}
      <div className="flex items-center gap-2 shrink-0">
        {provider && <MemberAvatars provider={provider} />}
        <button
          className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-white bg-stone-900 hover:bg-stone-700 rounded-lg transition-colors"
          onClick={onOpenShare}
        >
          <Share2 className="size-3.5" />
          分享
        </button>
        <div className="relative" ref={moreRef}>
          <button
            className="size-7 flex items-center justify-center rounded-lg text-stone-500 hover:text-stone-900 hover:bg-stone-100 transition-colors"
            onClick={() => setShowMore((v) => !v)}
          >
            <MoreHorizontal className="size-4" />
          </button>
          {showMore && (
            <div className="absolute top-full right-0 mt-1 w-40 bg-white border border-stone-200 rounded-lg shadow-lg py-1 z-50">
              {showManualSave && (
                <button
                  className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                  onClick={() => {
                    setShowMore(false);
                    onManualSave();
                  }}
                >
                  <Save className="size-3.5" />
                  手动保存
                </button>
              )}
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2 disabled:opacity-50"
                disabled={downloading}
                onClick={() => {
                  setShowMore(false);
                  onDownload('docx');
                }}
              >
                <Download className="size-3.5" />
                导出 Word (.docx)
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2 disabled:opacity-50"
                disabled={downloading}
                onClick={() => {
                  setShowMore(false);
                  onDownload('pdf');
                }}
              >
                <Download className="size-3.5" />
                导出 PDF (.pdf)
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
