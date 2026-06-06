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
import { useState } from 'react';

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
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = () => {
    const trimmed = title.trim();
    if (!trimmed) {
      setError('标题不能为空');
      return;
    }
    setSubmitting(true);
    onConfirm(trimmed);
    setTitle('');
    setError('');
  };

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setTitle('');
      setError('');
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
        <div className="py-4">
          <Input
            placeholder="输入收藏标题..."
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (error) setError('');
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleConfirm();
            }}
            className={error ? 'border-red-500' : ''}
            autoFocus
          />
          {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
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
