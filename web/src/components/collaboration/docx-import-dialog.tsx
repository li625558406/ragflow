import { Loader2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onImported: () => void;
  onClose: () => void;
  folderId?: string | null;
}

export default function DocxImportDialog({
  apiFetch,
  onImported,
  onClose,
  folderId,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.docx')) {
      setError('仅支持 .docx 格式文件');
      return;
    }
    setUploading(true);
    setError('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      if (folderId) {
        formData.append('folder_id', folderId);
      }
      const resp = await apiFetch('/api/v1/collaboration/documents/import', {
        method: 'POST',
        body: formData,
      });
      const result = await resp.json();
      if (result.code === 0) {
        onImported();
      } else {
        setError(result.message || '导入失败');
      }
    } catch (e) {
      console.error('导入失败:', e);
      setError('导入失败，请重试');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} />
      <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
        <h2 className="text-lg font-semibold text-[#1A1A1A]">导入 Word 文档</h2>
        <p className="text-sm text-[#8A8A8A] mt-1">
          上传 .docx 文件，自动转换为可编辑文档
        </p>

        {/* Drop zone */}
        <div
          className={`mt-4 border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
            dragOver
              ? 'border-indigo-400 bg-indigo-50'
              : 'border-[#D4D4D4] bg-[#F5F5F4]'
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file) handleFile(file);
          }}
        >
          {uploading ? (
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="size-8 text-indigo-500 animate-spin" />
              <span className="text-sm text-[#8A8A8A]">正在解析文档...</span>
            </div>
          ) : (
            <>
              <Upload className="size-8 text-[#A3A3A3] mx-auto mb-2" />
              <p className="text-sm text-[#555555]">
                拖拽 .docx 文件到此处，或点击选择
              </p>
              <button
                className="mt-2 text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                onClick={() => fileRef.current?.click()}
              >
                选择文件
              </button>
            </>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
              e.target.value = '';
            }}
          />
        </div>

        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}

        <div className="flex justify-end mt-6">
          <button
            onClick={onClose}
            className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A]"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
