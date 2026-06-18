import {
  CDialog,
  CDialogContent,
  CDialogDescription,
  CDialogFooter,
  CDialogHeader,
  CDialogTitle,
} from '@/components/c-dialog';
import { Input } from '@/components/ui/input';
import { Lightbulb } from 'lucide-react';
import { useEffect, useState } from 'react';

function generateTitle(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `收藏_${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

interface FavoriteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (title: string) => void;
  messageCount: number;
}

export default function FavoriteDialog({
  open,
  onOpenChange,
  onConfirm,
  messageCount,
}: FavoriteDialogProps) {
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = () => {
    const trimmed = title.trim();
    setSubmitting(true);
    onConfirm(trimmed || generateTitle());
    setTitle('');
  };

  // Reset state when dialog opens — using useEffect instead of
  // onOpenChange because Radix does not fire onOpenChange when the
  // parent controls the `open` prop programmatically.
  useEffect(() => {
    if (open) {
      setTitle('');
      setSubmitting(false);
    }
  }, [open]);

  return (
    <CDialog open={open} onOpenChange={onOpenChange}>
      <CDialogContent className="sm:max-w-[420px]">
        <CDialogHeader>
          <CDialogTitle>保存收藏</CDialogTitle>
          <CDialogDescription>
            已选择 {messageCount} 条消息，请输入收藏标题
          </CDialogDescription>
        </CDialogHeader>
        <div className="py-4 space-y-3">
          <Input
            placeholder="输入收藏标题..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleConfirm();
            }}
            autoFocus
            className="bg-white border-[#E8E8E6] rounded-xl h-10 text-sm text-[#1A1A1A] placeholder:text-[#B0B0B0] focus:border-[#1A1A1A] focus:ring-0"
          />
          <div className="flex items-start gap-2 px-1">
            <Lightbulb className="size-3.5 text-[#B0B0B0] shrink-0 mt-px" />
            <p className="text-xs text-[#8A8A8A] leading-relaxed">
              留空将自动生成标题，如：{generateTitle()}
            </p>
          </div>
        </div>
        <CDialogFooter>
          <button
            onClick={() => onOpenChange(false)}
            className="px-4 py-2.5 text-sm text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={submitting}
            className="px-5 py-2.5 text-sm font-medium bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] disabled:opacity-50 transition-colors"
          >
            {submitting ? '保存中...' : '确认保存'}
          </button>
        </CDialogFooter>
      </CDialogContent>
    </CDialog>
  );
}
