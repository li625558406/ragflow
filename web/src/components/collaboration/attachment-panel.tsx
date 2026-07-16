import { Download, Paperclip, Trash2, Upload, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

interface AttachmentData {
  id: string;
  document_id: string;
  file_name: string;
  file_size: number;
  mime_type: string;
  uploader_id: string;
  create_time: number;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function AttachmentPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [attachments, setAttachments] = useState<AttachmentData[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadAttachments = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/attachments`,
      );
      const result = await resp.json();
      if (result.code === 0) {
        setAttachments(result.data || []);
      }
    } catch (e) {
      console.error('Failed to load attachments:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) {
      loadAttachments();
    }
  }, [docId, open, loadAttachments]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/attachments`,
        {
          method: 'POST',
          body: formData,
        },
      );
      const result = await resp.json();
      if (result.code === 0) {
        loadAttachments();
      }
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDownload = async (attachment: AttachmentData) => {
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/attachments/${attachment.id}`,
      );
      if (resp.ok) {
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = window.document.createElement('a');
        a.href = url;
        a.download = attachment.file_name;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      console.error('Download failed:', e);
    }
  };

  const handleDelete = async (attachmentId: string) => {
    try {
      await apiFetch(
        `/api/v1/collaboration/documents/${docId}/attachments/${attachmentId}`,
        { method: 'DELETE' },
      );
      loadAttachments();
    } catch (e) {
      console.error('Delete failed:', e);
    }
  };

  if (!open) return null;

  return (
    <div className="w-72 shrink-0 border-l border-stone-200 flex flex-col bg-white h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5">
          <Paperclip className="size-3.5 text-stone-500" />
          <span className="text-xs font-semibold text-stone-700">附件</span>
          {attachments.length > 0 && (
            <span className="text-[10px] bg-stone-900 text-white px-1.5 py-0.5 rounded-full">
              {attachments.length}
            </span>
          )}
        </div>
        <button
          className="text-stone-400 hover:text-stone-700"
          onClick={onToggle}
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Upload button */}
      <div className="px-3 pt-3 pb-2">
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleUpload}
        />
        <button
          className="w-full text-xs px-3 py-1.5 border border-dashed border-stone-300 rounded-lg text-stone-500 hover:text-stone-700 hover:border-stone-400 transition-colors flex items-center justify-center gap-1"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          <Upload className="size-3" />
          {uploading ? '上传中...' : '上传文件'}
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-3">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 border-2 border-stone-200 border-t-stone-400 rounded-full animate-spin" />
          </div>
        ) : attachments.length === 0 ? (
          <div className="text-center py-8 text-xs text-stone-400">
            暂无附件
          </div>
        ) : (
          attachments.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-2 py-2 border-b border-stone-50 group"
            >
              <div className="flex-1 min-w-0">
                <p className="text-xs text-stone-800 truncate">{a.file_name}</p>
                <p className="text-[10px] text-stone-400">
                  {formatSize(a.file_size)}
                </p>
              </div>
              <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                <button
                  className="p-1 text-stone-400 hover:text-stone-700"
                  onClick={() => handleDownload(a)}
                >
                  <Download className="size-3" />
                </button>
                <button
                  className="p-1 text-stone-400 hover:text-red-600"
                  onClick={() => handleDelete(a.id)}
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
