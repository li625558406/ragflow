import { FileText, FileUp, X } from 'lucide-react';
import { useRef, useState } from 'react';

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import {
  useDetectTemplateFill,
  useSaveTemplateFillPlaceholders,
  useUploadTemplateFill,
  type TplPlaceholder,
} from '@/hooks/use-template-fill-request';
import {
  collectRowErrors,
  emptyPlaceholder,
  PlaceholderTable,
  trimRows,
} from './placeholder-table';

const FILL_MODE_LABEL: Record<TplPlaceholder['fill_mode'], string> = {
  llm: 'AI填写',
  param: '参数',
  manual: '人工',
};

interface UploadWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (id: string) => void;
  // 一次选中多个文件时回调（AI 识别为逐模板环节，多文件转交批量上传建草稿）
  onBatchFiles?: (files: File[]) => void;
}

const TEMPLATE_FILE_RE = /\.(docx|doc|xlsx)$/i;

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
  onBatchFiles,
}: UploadWizardProps) {
  const [step, setStep] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [step1Error, setStep1Error] = useState('');
  const [templateId, setTemplateId] = useState('');
  // 上传成功时记录文件指纹（name+size），用于识别「换文件」场景触发重新上传
  const [uploadedFileKey, setUploadedFileKey] = useState('');
  const [placeholders, setPlaceholders] = useState<TplPlaceholder[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, boolean>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMut = useUploadTemplateFill();
  const detectMut = useDetectTemplateFill();
  const saveMut = useSaveTemplateFillPlaceholders();

  // 对话框每次打开时重置全部状态
  const handleOpenChange = (next: boolean) => {
    if (next) {
      setStep(1);
      setFile(null);
      setName('');
      setDescription('');
      setStep1Error('');
      setTemplateId('');
      setUploadedFileKey('');
      setPlaceholders([]);
      setRowErrors({});
      detectMut.reset();
    }
    onOpenChange(next);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    // 多选：转交批量上传（父组件切弹框），本向导只处理单文件 AI 识别流程
    if (files.length > 1) {
      onBatchFiles?.(files);
      return;
    }
    const f = files[0];
    setFile(f);
    setStep1Error('');
    if (f && !name) {
      // 默认模板名取文件名去扩展
      setName(f.name.replace(/\.[^.]+$/, ''));
    }
  };

  const openFilePicker = () => {
    // 先清空 value，保证重新选择同一文件时 onChange 也能触发
    if (fileInputRef.current) fileInputRef.current.value = '';
    fileInputRef.current?.click();
  };

  const clearFile = (e: React.MouseEvent) => {
    e.stopPropagation();
    setFile(null);
    setStep1Error('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // detect 建议与现有手动行（addr 为空，detect 建议必带 addr）合并：
  // 建议在前、手动行在后，key 重复的手动行丢弃，避免重新识别静默覆盖手动添加的行
  const applySuggestions = (suggestions: TplPlaceholder[]) => {
    if (suggestions.length === 0) return;
    setPlaceholders((prev) => {
      const sugKeys = new Set(suggestions.map((s) => s.key));
      const manual = prev.filter((r) => !r.addr && !sugKeys.has(r.key.trim()));
      return [...suggestions, ...manual];
    });
  };

  const goStep2 = () => {
    if (!file) {
      setStep1Error('请选择 .docx / .doc / .xlsx 模板文件');
      return;
    }
    if (!TEMPLATE_FILE_RE.test(file.name)) {
      setStep1Error('仅支持 .docx / .doc / .xlsx 文件');
      return;
    }
    if (!name.trim()) {
      setStep1Error('请填写模板名称');
      return;
    }
    setStep1Error('');
    // 从 Step2 回退后再前进：文件未更换时直接跳转，不重复上传模板
    const fileKey = `${file.name}:${file.size}`;
    if (templateId && uploadedFileKey === fileKey) {
      setStep(2);
      return;
    }
    uploadMut.mutate(
      { file, name: name.trim(), description: description.trim() },
      {
        onSuccess: (data) => {
          setTemplateId(data.id);
          setUploadedFileKey(fileKey);
          setPlaceholders([]);
          setStep(2);
          detectMut.mutate(data.id, {
            onSuccess: (res) => {
              applySuggestions(res.suggestions);
            },
            onError: (err) => {
              message.error(
                err instanceof Error
                  ? err.message
                  : 'AI 识别失败，可手动添加填写点',
              );
            },
          });
        },
        onError: (err) => {
          message.error(err instanceof Error ? err.message : '上传失败');
        },
      },
    );
  };

  const updateRow = (index: number, patch: Partial<TplPlaceholder>) => {
    setPlaceholders((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
    // 编辑即清除该行错误标记
    setRowErrors((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((k) => {
        if (k.startsWith(`${index}-`)) delete next[k];
      });
      return next;
    });
  };

  const removeRow = (index: number) => {
    setPlaceholders((prev) => prev.filter((_, i) => i !== index));
    setRowErrors({});
  };

  const goStep3 = () => {
    const rows = trimRows(placeholders);
    setPlaceholders(rows);
    if (rows.length === 0) {
      message.error('请至少添加一个填写点');
      return;
    }
    const errors = collectRowErrors(rows);
    setRowErrors(errors);
    if (Object.keys(errors).length > 0) {
      message.error('存在格式不正确的填写点，请检查标红字段');
      return;
    }
    setStep(3);
  };

  const saveConfig = () => {
    const rows = trimRows(placeholders);
    setPlaceholders(rows);
    const errors = collectRowErrors(rows);
    setRowErrors(errors);
    if (Object.keys(errors).length > 0) {
      message.error('存在格式不正确的填写点，请返回修改');
      setStep(2);
      return;
    }
    saveMut.mutate(
      { id: templateId, placeholders: rows },
      {
        onSuccess: (res) => {
          message.success(`已保存 ${res.placeholder_count} 个填写点`);
          onOpenChange(false);
          onSaved?.(templateId);
        },
        onError: (err) => {
          message.error(err instanceof Error ? err.message : '保存失败');
        },
      },
    );
  };

  const detecting = detectMut.isPending;
  const detectEmpty =
    !detecting && !detectMut.error && detectMut.data?.suggestions.length === 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>上传模板</DialogTitle>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {['1 上传文件', '2 AI 识别', '3 确认保存'].map((label, i) => (
              <span
                key={label}
                className={
                  step === i + 1
                    ? 'font-medium text-text-primary'
                    : 'text-muted-foreground'
                }
              >
                {label}
                {i < 2 && <span className="ml-2">→</span>}
              </span>
            ))}
          </div>
        </DialogHeader>

        {step === 1 && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm">
                模板文件（.docx / .doc / .xlsx）
              </label>
              <input
                ref={fileInputRef}
                type="file"
                accept=".docx,.doc,.xlsx"
                multiple
                className="hidden"
                onChange={handleFileChange}
              />
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
                {file ? (
                  <div className="flex w-full items-center gap-3">
                    <FileText className="h-8 w-8 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p
                        className="truncate text-sm text-text-primary"
                        title={file.name}
                      >
                        {file.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(file.size)}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-primary">
                      重新选择
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0"
                      onClick={clearFile}
                      aria-label="清除选择"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <FileUp className="h-8 w-8 text-muted-foreground" />
                    <p className="text-sm text-text-primary">
                      点击选择模板文件
                    </p>
                    <p className="text-xs text-muted-foreground">
                      支持 .docx / .doc / .xlsx，不超过
                      20MB；可多选，多文件将批量上传为草稿
                    </p>
                  </>
                )}
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm">模板名称</label>
              <Input
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setStep1Error('');
                }}
                placeholder="默认取文件名"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm">说明</label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="模板用途说明（选填）"
                rows={3}
              />
            </div>
            {step1Error && <p className="text-sm text-red-500">{step1Error}</p>}
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-3">
            {detecting && (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <div className="mr-3 h-6 w-6 animate-spin rounded-full border-2 border-primary border-b-transparent" />
                AI 识别中…
              </div>
            )}
            {detectEmpty && (
              <p className="text-sm text-amber-600">
                未识别到填写点，请在下方手动添加
              </p>
            )}
            {!detecting && detectMut.error && (
              <div className="flex items-center justify-between">
                <p className="text-sm text-red-500">
                  {detectMut.error instanceof Error
                    ? detectMut.error.message
                    : 'AI 识别失败'}
                  ，可手动添加填写点
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={detecting}
                  onClick={() => detectMut.mutate(templateId)}
                >
                  重新识别
                </Button>
              </div>
            )}
            {!detecting && (
              <div className="max-h-[50vh] overflow-auto">
                <PlaceholderTable
                  rows={placeholders}
                  errors={rowErrors}
                  onUpdate={updateRow}
                  onRemove={removeRow}
                />
              </div>
            )}
            {!detecting && (
              <div className="flex items-center gap-3">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    setPlaceholders((prev) => [...prev, emptyPlaceholder()])
                  }
                >
                  添加填写点
                </Button>
                <span className="text-xs text-muted-foreground">
                  手动添加行无需定位，锚文本须为模板中已有的原文片段
                </span>
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              共 {placeholders.length} 个填写点，请确认后保存：
            </p>
            <div className="max-h-[50vh] overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>key</TableHead>
                    <TableHead>中文名</TableHead>
                    <TableHead>填写方式</TableHead>
                    <TableHead>必填</TableHead>
                    <TableHead>锚文本</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {placeholders.map((row, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">
                        {row.key}
                      </TableCell>
                      <TableCell>{row.name}</TableCell>
                      <TableCell>{FILL_MODE_LABEL[row.fill_mode]}</TableCell>
                      <TableCell>{row.required ? '是' : '否'}</TableCell>
                      <TableCell>
                        <span
                          className="block max-w-[240px] truncate text-sm text-muted-foreground"
                          title={row.anchor}
                        >
                          {row.anchor}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        <DialogFooter>
          {step === 1 && (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                取消
              </Button>
              <Button disabled={uploadMut.isPending} onClick={goStep2}>
                {uploadMut.isPending ? '上传中…' : '下一步'}
              </Button>
            </>
          )}
          {step === 2 && (
            <>
              <Button variant="outline" onClick={() => setStep(1)}>
                上一步
              </Button>
              <Button disabled={detecting} onClick={goStep3}>
                下一步
              </Button>
            </>
          )}
          {step === 3 && (
            <>
              <Button variant="outline" onClick={() => setStep(2)}>
                上一步
              </Button>
              <Button disabled={saveMut.isPending} onClick={saveConfig}>
                {saveMut.isPending ? '保存中…' : '保存配置'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
