import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Lightbulb } from 'lucide-react';
import { useState } from 'react';

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

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setTitle('');
      setSubmitting(false);
    }
    onOpenChange(open);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>保存收藏</DialogTitle>
          <DialogDescription>
            已选择 {messageCount} 条消息，请输入收藏标题
          </DialogDescription>
        </DialogHeader>
        <div className="py-4 space-y-3">
          <Input
            placeholder="输入收藏标题..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleConfirm();
            }}
            autoFocus
          />
          <div className="flex items-start gap-2 px-1">
            <Lightbulb className="size-3.5 text-[#A3A3A3] shrink-0 mt-px" />
            <p className="text-xs text-[#A3A3A3] leading-relaxed">
              留空将自动生成标题，如：{generateTitle()}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={submitting}
            className="bg-[#000000] text-white hover:bg-[#333333]"
          >
            {submitting ? '保存中...' : '确认保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
