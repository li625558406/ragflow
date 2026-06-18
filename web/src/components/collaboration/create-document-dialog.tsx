import {
  CDialog,
  CDialogContent,
  CDialogFooter,
  CDialogHeader,
  CDialogTitle,
} from '@/components/c-dialog';
import { Lightbulb } from 'lucide-react';
import { useState } from 'react';

function generateTitle(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `文档_${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  messageContent: string;
  agentId?: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onCreated: () => void;
}

function cn(...classes: (string | boolean | undefined)[]) {
  return classes.filter(Boolean).join(' ');
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
  const [permission, setPermission] = useState<'me' | 'team'>('me');
  const [creating, setCreating] = useState(false);

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
    if (creating) return;
    const finalName = name.trim() || generateTitle();
    setCreating(true);
    try {
      const resp = await apiFetch('/api/v1/collaboration/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: finalName,
          markdown_content: messageContent.replace(
            /<think\b[^>]*>[\s\S]*?<\/think>/gi,
            '',
          ),
          file_type: 'docx',
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

  const segClasses = (selected: boolean) =>
    cn(
      'flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors',
      selected
        ? 'bg-[#F5F5F4] border-[#B0B0B0] text-[#1A1A1A]'
        : 'border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4]',
    );

  return (
    <CDialog open={open} onOpenChange={handleOpen}>
      <CDialogContent className="sm:max-w-md">
        <CDialogHeader>
          <CDialogTitle>创建协作文档</CDialogTitle>
        </CDialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <label className="block text-sm font-medium text-[#1A1A1A] mb-1.5">
              文档名称
            </label>
            <input
              type="text"
              className="w-full px-3 py-2.5 bg-white border border-[#E8E8E6] rounded-xl text-sm text-[#1A1A1A] placeholder:text-[#B0B0B0] focus:outline-none focus:border-[#1A1A1A] transition"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="输入文档名称"
            />
            <div className="flex items-start gap-2 px-1 mt-2">
              <Lightbulb className="size-3.5 text-[#B0B0B0] shrink-0 mt-px" />
              <p className="text-xs text-[#8A8A8A] leading-relaxed">
                留空将自动生成标题，如：{generateTitle()}
              </p>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-[#1A1A1A] mb-1.5">
              可见范围
            </label>
            <div className="flex gap-2">
              <button
                className={segClasses(permission === 'me')}
                onClick={() => setPermission('me')}
              >
                仅自己
              </button>
              <button
                className={segClasses(permission === 'team')}
                onClick={() => setPermission('team')}
              >
                团队共享
              </button>
            </div>
          </div>
        </div>
        <CDialogFooter>
          <button
            className="px-4 py-2.5 text-sm text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors"
            onClick={() => onOpenChange(false)}
          >
            取消
          </button>
          <button
            className="px-5 py-2.5 text-sm font-medium bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] disabled:opacity-50 transition-colors"
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? '创建中...' : '创建文档'}
          </button>
        </CDialogFooter>
      </CDialogContent>
    </CDialog>
  );
}
