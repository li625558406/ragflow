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
import message from '@/components/ui/message';
import { useUploadTemplateFill } from '@/hooks/use-template-fill-request';

const TEMPLATE_FILE_RE = /\.(docx|doc|xlsx)$/i;
const MAX_FILE_SIZE = 20 * 1024 * 1024;
// 单批上限：上传走同步串行请求，防一次选几百个文件拖死浏览器与后端
const MAX_BATCH = 20;

type ItemStatus = 'pending' | 'uploading' | 'done' | 'failed';

interface UploadItem {
  file: File;
  status: ItemStatus;
  error?: string;
}

const STATUS_TEXT: Record<ItemStatus, string> = {
  pending: '待上传',
  uploading: '上传中…',
  done: '成功',
  failed: '失败',
};

function formatFileSize(size: number): string {
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

// 逐个校验单个文件，返回错误文案（空串表示通过）
function validateFile(file: File): string {
  if (!TEMPLATE_FILE_RE.test(file.name)) {
    return '仅支持 .docx / .doc / .xlsx';
  }
  if (file.size > MAX_FILE_SIZE) {
    return '超过 20MB 上限';
  }
  return '';
}

export function BatchUploadDialog({
  open,
  onOpenChange,
  initialFiles,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // 从上传向导多选转交来的文件：打开时直接填入列表
  initialFiles?: File[] | null;
}) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMut = useUploadTemplateFill();

  // 每次打开时重置列表：有转交文件（上传向导多选）则填入，否则清空。
  // 必须用 effect——受控 Dialog 从 props 切 open 不触发 Radix 的 onOpenChange
  useEffect(() => {
    if (open) {
      setItems(
        initialFiles?.length
          ? initialFiles.map((file) => ({ file, status: 'pending' as const }))
          : [],
      );
      setUploading(false);
    }
  }, [open, initialFiles]);

  const handleOpenChange = (next: boolean) => {
    onOpenChange(next);
  };

  const addFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setItems((prev) => {
      const added: UploadItem[] = Array.from(files).map((file) => ({
        file,
        status: 'pending',
      }));
      const merged = [...prev, ...added];
      if (merged.length > MAX_BATCH) {
        message.warning(`单次最多上传 ${MAX_BATCH} 个文件`);
        return merged.slice(0, MAX_BATCH);
      }
      return merged;
    });
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removeItem = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
  };

  // 顺序逐个上传：upload 端点含 .doc→.docx 转换，串行避免并发转换挤占容器资源；
  // 单个失败标记后继续下一个，最后汇总结果
  const startUpload = async () => {
    setUploading(true);
    let okCount = 0;
    let failCount = 0;
    for (let i = 0; i < items.length; i++) {
      if (items[i].status === 'done') continue;
      const err = validateFile(items[i].file);
      if (err) {
        setItems((prev) =>
          prev.map((it, idx) =>
            idx === i ? { ...it, status: 'failed', error: err } : it,
          ),
        );
        failCount++;
        continue;
      }
      setItems((prev) =>
        prev.map((it, idx) =>
          idx === i ? { ...it, status: 'uploading', error: undefined } : it,
        ),
      );
      try {
        await uploadMut.mutateAsync({
          file: items[i].file,
          name: items[i].file.name.replace(/\.[^.]+$/, ''),
          description: '',
        });
        setItems((prev) =>
          prev.map((it, idx) => (idx === i ? { ...it, status: 'done' } : it)),
        );
        okCount++;
      } catch (e) {
        setItems((prev) =>
          prev.map((it, idx) =>
            idx === i
              ? {
                  ...it,
                  status: 'failed',
                  error: e instanceof Error ? e.message : '上传失败',
                }
              : it,
          ),
        );
        failCount++;
      }
    }
    setUploading(false);
    if (failCount === 0) {
      message.success(`已上传 ${okCount} 个模板（草稿状态）`);
    } else {
      message.warning(
        `上传 ${okCount} 个成功，${failCount} 个失败，失败原因见列表`,
      );
    }
  };

  const allDone = items.length > 0 && items.every((it) => it.status === 'done');
  // 没有待上传的文件（全部已完成或失败）时按钮不可用
  const hasPending = items.some((it) => it.status === 'pending');

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>批量上传模板</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".docx,.doc,.xlsx"
            multiple
            className="hidden"
            onChange={(e) => addFiles(e.target.files)}
          />
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return;
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 cursor-pointer transition-colors hover:border-primary/60 hover:bg-muted/50"
          >
            <FileUp className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-text-primary">
              点击选择模板文件（可多选）
            </p>
            <p className="text-xs text-muted-foreground">
              支持 .docx / .doc / .xlsx，单个不超过 20MB，单次最多 {MAX_BATCH}{' '}
              个
            </p>
          </div>
          {items.length > 0 && (
            <div className="max-h-[45vh] overflow-auto rounded-lg border">
              {items.map((it, i) => (
                <div
                  key={`${it.file.name}:${it.file.size}:${i}`}
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
                      className="max-w-[180px] truncate text-xs text-red-500"
                      title={it.error}
                    >
                      {it.error}
                    </span>
                  )}
                  <span
                    className={
                      it.status === 'done'
                        ? 'shrink-0 text-xs text-state-success'
                        : it.status === 'failed'
                          ? 'shrink-0 text-xs text-state-error'
                          : 'shrink-0 text-xs text-muted-foreground'
                    }
                  >
                    {STATUS_TEXT[it.status]}
                  </span>
                  {!uploading && it.status !== 'done' && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0"
                      onClick={() => removeItem(i)}
                      aria-label={`移除 ${it.file.name}`}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            上传成功后模板为草稿状态，需逐个进入详情完成「AI 识别填写点 →
            确认保存」后才能发布
          </p>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={uploading}
            onClick={() => handleOpenChange(false)}
          >
            {allDone ? '关闭' : '取消'}
          </Button>
          <Button disabled={uploading || !hasPending} onClick={startUpload}>
            {uploading ? '上传中…' : '开始上传'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
