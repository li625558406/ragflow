import { FileText, FileUp, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import { Textarea } from '@/components/ui/textarea';
import {
  useDetectTemplateFillAsync,
  useUploadTemplateFill,
} from '@/hooks/use-template-fill-request';

interface UploadWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // 全部文件上传并提交识别后回调（参数为最后一个模板 id）
  onSaved?: (id: string) => void;
}

const TEMPLATE_FILE_RE = /\.(docx|doc|xlsx)$/i;

type ItemStatus =
  | 'pending'
  | 'uploading'
  | 'detecting'
  | 'submitted'
  | 'failed';

interface UploadItem {
  file: File;
  status: ItemStatus;
  error?: string;
  templateId?: string;
}

const STATUS_TEXT: Record<ItemStatus, string> = {
  pending: '待上传',
  uploading: '上传中…',
  detecting: 'AI 识别中…',
  submitted: '已提交识别',
  failed: '失败',
};

const STATUS_CLS: Record<ItemStatus, string> = {
  pending: 'text-muted-foreground',
  uploading: 'text-muted-foreground',
  detecting: 'text-state-warning',
  submitted: 'text-state-success',
  failed: 'text-state-error',
};

function formatFileSize(size: number): string {
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

export function UploadWizard({
  open,
  onOpenChange,
  onSaved,
}: UploadWizardProps) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [step1Error, setStep1Error] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const multiFile = items.length > 1;
  const uploadMut = useUploadTemplateFill();
  const detectAsyncMut = useDetectTemplateFillAsync();

  // 打开时重置全部状态：必须用 effect——受控 Dialog 从 props 切 open
  // 不触发 Radix 的 onOpenChange，靠回调重置会残留上次的状态
  useEffect(() => {
    if (open) {
      setItems([]);
      setName('');
      setDescription('');
      setStep1Error('');
      setUploading(false);
    }
  }, [open]);

  const patchItem = (index: number, patch: Partial<UploadItem>) => {
    setItems((prev) =>
      prev.map((it, i) => (i === index ? { ...it, ...patch } : it)),
    );
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length === 0) return;
    // 每次选择替换整个列表
    setItems(picked.map((file) => ({ file, status: 'pending' as const })));
    setStep1Error('');
    setName(picked[0].name.replace(/\.[^.]+$/, ''));
    setDescription('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const openFilePicker = () => {
    // 先清空 value，保证重新选择同一文件时 onChange 也能触发
    if (fileInputRef.current) fileInputRef.current.value = '';
    fileInputRef.current?.click();
  };

  const removeFile = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
  };

  // 顺序逐个：上传建模板 → 提交后台 AI 识别（串行防 .doc 转换挤占容器资源）；
  // 单个失败标记后继续下一个，最后汇总。识别结果由后台线程自动保存为填写点，
  // 进度在列表 detect_status 列轮询展示，向导无需等待 LLM 完成。
  const startUpload = async () => {
    const queue = items;
    if (queue.length === 0) return;
    for (let i = 0; i < queue.length; i++) {
      if (queue[i].status === 'submitted') continue;
      const f = queue[i];
      if (!TEMPLATE_FILE_RE.test(f.file.name)) {
        patchItem(i, {
          status: 'failed',
          error: '仅支持 .docx / .doc / .xlsx',
        });
        continue;
      }
      const tplName =
        (i === 0 ? name.trim() : '') || f.file.name.replace(/\.[^.]+$/, '');
      patchItem(i, { status: 'uploading', error: undefined });
      let tplId = '';
      try {
        const res = await uploadMut.mutateAsync({
          file: f.file,
          name: tplName,
          description: i === 0 ? description.trim() : '',
        });
        tplId = res.id;
        patchItem(i, { templateId: tplId, status: 'detecting' });
      } catch (e) {
        patchItem(i, {
          status: 'failed',
          error: e instanceof Error ? e.message : '上传失败',
        });
        continue;
      }
      try {
        await detectAsyncMut.mutateAsync(tplId);
        patchItem(i, { status: 'submitted' });
      } catch (e) {
        // 模板已建成，仅识别提交失败：仍算部分成功，提示到详情手动识别
        patchItem(i, {
          status: 'failed',
          error: e instanceof Error ? e.message : '识别提交失败',
        });
      }
    }
    setUploading(false);
    const failCount = queue.filter((it) => it.status === 'failed').length;
    const okCount = queue.length - failCount;
    if (failCount === 0) {
      message.success(
        `已上传 ${okCount} 个模板，AI 识别进行中，可在列表查看进度`,
      );
    } else {
      message.warning(
        `上传 ${okCount} 个成功，${failCount} 个失败，失败原因见列表`,
      );
    }
    onOpenChange(false);
    onSaved?.(queue[queue.length - 1]?.templateId ?? '');
  };

  const hasPending = items.some((it) => it.status === 'pending');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>上传模板</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm">模板文件（.docx / .doc / .xlsx）</label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,.doc,.xlsx"
              multiple
              className="hidden"
              onChange={handleFileChange}
            />
            {items.length === 0 ? (
              <div
                role="button"
                tabIndex={0}
                onClick={openFilePicker}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openFilePicker();
                  }
                }}
                className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 cursor-pointer transition-colors hover:border-primary/60 hover:bg-muted/50"
              >
                <FileUp className="h-8 w-8 text-muted-foreground" />
                <p className="text-sm text-text-primary">点击选择模板文件</p>
                <p className="text-xs text-muted-foreground">
                  支持 .docx / .doc / .xlsx，不超过 20MB；可多选，上传后自动进行
                  AI 识别
                </p>
              </div>
            ) : (
              <div className="rounded-lg border">
                {items.map((it, i) => (
                  <div
                    key={`${it.file.name}:${it.file.size}`}
                    className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0"
                  >
                    <FileText className="h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p
                        className="truncate text-sm text-text-primary"
                        title={it.file.name}
                      >
                        {it.file.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(it.file.size)}
                      </p>
                    </div>
                    {it.error && (
                      <span
                        className="max-w-[180px] truncate text-xs text-state-error"
                        title={it.error}
                      >
                        {it.error}
                      </span>
                    )}
                    <span
                      className={`shrink-0 text-xs ${STATUS_CLS[it.status]}`}
                    >
                      {STATUS_TEXT[it.status]}
                    </span>
                    {!uploading && it.status !== 'submitted' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0"
                        onClick={() => removeFile(i)}
                        aria-label={`移除 ${it.file.name}`}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm">
              模板名称{multiFile && '（其余模板自动取文件名）'}
            </label>
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setStep1Error('');
              }}
              placeholder="默认取文件名"
            />
          </div>
          {!multiFile && (
            <div className="flex flex-col gap-1.5">
              <label className="text-sm">说明</label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="模板用途说明（选填）"
                rows={3}
              />
            </div>
          )}
          {step1Error && <p className="text-sm text-red-500">{step1Error}</p>}
          <p className="text-xs text-muted-foreground">
            上传后自动进行 AI
            识别填写点，识别结果自动保存；可在此处关闭弹框，在列表查看识别进度，点模板名进入详情确认修改
          </p>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={uploading}
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button disabled={uploading || !hasPending} onClick={startUpload}>
            {uploading ? '上传中…' : '开始上传'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
