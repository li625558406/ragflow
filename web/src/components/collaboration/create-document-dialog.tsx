import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useState } from 'react';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  messageContent: string;
  agentId?: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onCreated: () => void;
}

export default function CreateDocumentDialog({
  open,
  onOpenChange,
  messageContent,
  agentId,
  apiFetch,
  onCreated,
}: Props) {
  const [name, setName] = useState('');
  const [fileType, setFileType] = useState<'docx' | 'pdf'>('docx');
  const [permission, setPermission] = useState<'me' | 'team'>('me');
  const [creating, setCreating] = useState(false);

  // Pre-fill name from first line or first 30 chars of content
  const handleOpen = (isOpen: boolean) => {
    if (isOpen) {
      const firstLine = messageContent
        .split('\n')[0]
        .replace(/^#+\s*/, '')
        .trim();
      setName(firstLine.slice(0, 60) || '未命名文档');
    }
    onOpenChange(isOpen);
  };

  const handleCreate = async () => {
    if (!name.trim() || creating) return;
    setCreating(true);
    try {
      const resp = await apiFetch('/api/v1/collaboration/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          markdown_content: messageContent,
          file_type: fileType,
          agent_id: agentId || '',
          permission,
        }),
      });
      const result = await resp.json();
      if (result.code === 0) {
        onCreated();
        onOpenChange(false);
      } else {
        console.error('创建文档失败:', result.message);
      }
    } catch (e) {
      console.error('创建文档失败:', e);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent className="sm:max-w-md !bg-white !border-stone-200 !shadow-[0_20px_60px_-12px_rgba(28,25,23,0.06)] !rounded-2xl">
        <DialogHeader className="!-mx-6 !-mt-6 !p-5 !border-b !border-stone-100">
          <DialogTitle className="!text-base !font-semibold !text-stone-900">
            创建协作文档
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <label className="block text-sm font-medium text-stone-700 mb-1.5">
              文档名称
            </label>
            <input
              type="text"
              className="w-full px-3 py-2.5 bg-stone-50 border border-stone-200 rounded-xl text-sm text-stone-900 placeholder:text-stone-300 focus:outline-none focus:border-indigo-300 focus:bg-white transition"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="输入文档名称"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-stone-700 mb-1.5">
              文件格式
            </label>
            <div className="flex gap-2">
              <button
                className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                  fileType === 'docx'
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                    : 'border-stone-200 text-stone-600 hover:bg-stone-50'
                }`}
                onClick={() => setFileType('docx')}
              >
                DOCX
              </button>
              <button
                className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                  fileType === 'pdf'
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                    : 'border-stone-200 text-stone-600 hover:bg-stone-50'
                }`}
                onClick={() => setFileType('pdf')}
              >
                PDF
              </button>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-stone-700 mb-1.5">
              可见范围
            </label>
            <div className="flex gap-2">
              <button
                className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                  permission === 'me'
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                    : 'border-stone-200 text-stone-600 hover:bg-stone-50'
                }`}
                onClick={() => setPermission('me')}
              >
                仅自己
              </button>
              <button
                className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                  permission === 'team'
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                    : 'border-stone-200 text-stone-600 hover:bg-stone-50'
                }`}
                onClick={() => setPermission('team')}
              >
                团队共享
              </button>
            </div>
          </div>
        </div>
        <DialogFooter className="!-mx-6 !-mb-6 !p-5 !border-t !border-stone-100">
          <button
            className="px-4 py-2.5 text-sm text-stone-500 hover:text-stone-700 transition-colors"
            onClick={() => onOpenChange(false)}
          >
            取消
          </button>
          <button
            className="px-5 py-2.5 text-sm font-medium bg-indigo-500 text-white rounded-xl hover:bg-indigo-600 transition-colors disabled:opacity-50 shadow-sm"
            onClick={handleCreate}
            disabled={!name.trim() || creating}
          >
            {creating ? '创建中...' : '创建文档'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
