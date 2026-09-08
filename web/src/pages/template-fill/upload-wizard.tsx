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
}: UploadWizardProps) {
  const [step, setStep] = useState(1);
  // 文件队列：支持多选，逐个走「上传 → AI 识别 → 确认保存」完整流程
  const [files, setFiles] = useState<File[]>([]);
  const [fileIndex, setFileIndex] = useState(0);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [step1Error, setStep1Error] = useState('');
  const [templateId, setTemplateId] = useState('');
  // 上传成功时记录文件指纹（name+size），用于识别「换文件」场景触发重新上传
  const [uploadedFileKey, setUploadedFileKey] = useState('');
  const [placeholders, setPlaceholders] = useState<TplPlaceholder[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, boolean>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const currentFile = files[fileIndex] ?? null;
  const multiFile = files.length > 1;

  const uploadMut = useUploadTemplateFill();
  const detectMut = useDetectTemplateFill();
  const saveMut = useSaveTemplateFillPlaceholders();

  // 对话框每次打开时重置全部状态
  const handleOpenChange = (next: boolean) => {
    if (next) {
      setStep(1);
      setFiles([]);
      setFileIndex(0);
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
    const picked = Array.from(e.target.files ?? []);
    if (picked.length === 0) return;
    // 每次选择替换整个列表，从第一个文件开始逐个走流程
    setFiles(picked);
    setFileIndex(0);
    setStep1Error('');
    setName(picked[0].name.replace(/\.[^.]+$/, ''));
    setDescription('');
  };

  const openFilePicker = () => {
    // 先清空 value，保证重新选择同一文件时 onChange 也能触发
    if (fileInputRef.current) fileInputRef.current.value = '';
    fileInputRef.current?.click();
  };

  const removeFile = (index: number) => {
    const next = files.filter((_, i) => i !== index);
    setFiles(next);
    // 移除后当前索引可能越界，收敛到最后一个有效位置
    setFileIndex((fi) => Math.min(fi, Math.max(0, next.length - 1)));
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

  // 上传指定文件并自动触发 AI 识别（单/多文件共用；desc 显式传参避免 state 异步旧值）
  const startUploadAndDetect = (f: File, tplName: string, desc: string) => {
    uploadMut.mutate(
      { file: f, name: tplName, description: desc },
      {
        onSuccess: (data) => {
          setTemplateId(data.id);
          setUploadedFileKey(`${f.name}:${f.size}`);
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

  const goStep2 = () => {
    if (!currentFile) {
      setStep1Error('请选择 .docx / .doc / .xlsx 模板文件');
      return;
    }
    if (!TEMPLATE_FILE_RE.test(currentFile.name)) {
      setStep1Error('仅支持 .docx / .doc / .xlsx 文件');
      return;
    }
    const tplName = name.trim() || currentFile.name.replace(/\.[^.]+$/, '');
    setName(tplName);
    setStep1Error('');
    // 从 Step2 回退后再前进：文件未更换时直接跳转，不重复上传模板
    const fileKey = `${currentFile.name}:${currentFile.size}`;
    if (templateId && uploadedFileKey === fileKey) {
      setStep(2);
      return;
    }
    startUploadAndDetect(currentFile, tplName, description.trim());
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
          const next = fileIndex + 1;
          if (next >= files.length) {
            // 全部文件处理完成
            message.success(`已保存 ${res.placeholder_count} 个填写点`);
            onOpenChange(false);
            onSaved?.(templateId);
            return;
          }
          // 自动进入下一个文件：重置逐文件状态，直接开始上传 + AI 识别
          const nf = files[next];
          const nextName = nf.name.replace(/\.[^.]+$/, '');
          message.success(`已保存 ${res.placeholder_count} 个填写点`);
          setFileIndex(next);
          setName(nextName);
          setDescription('');
          setTemplateId('');
          setUploadedFileKey('');
          setPlaceholders([]);
          setRowErrors({});
          detectMut.reset();
          startUploadAndDetect(nf, nextName, '');
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
            {multiFile && (
              <span className="ml-2 text-xs text-primary">
                第 {fileIndex + 1}/{files.length} 个文件
              </span>
            )}
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
              {files.length === 0 ? (
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
                    支持 .docx / .doc / .xlsx，不超过 20MB；可多选，将逐个走 AI
                    识别流程
                  </p>
                </div>
              ) : (
                <div className="rounded-lg border">
                  {files.map((f, i) => (
                    <div
                      key={`${f.name}:${f.size}`}
                      className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0"
                    >
                      <FileText className="h-4 w-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p
                          className="truncate text-sm text-text-primary"
                          title={f.name}
                        >
                          {f.name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {formatFileSize(f.size)}
                        </p>
                      </div>
                      <span className="shrink-0 text-xs text-primary">
                        重新选择
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0"
                        onClick={() => removeFile(i)}
                        aria-label={`移除 ${f.name}`}
                      >
                        <X className="h-4 w-4" />
                      </Button>
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
            {files.length <= 1 && (
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
              <Button
                disabled={files.length === 0 || uploadMut.isPending}
                onClick={goStep2}
              >
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
                {saveMut.isPending ? '保存中…' : '保存并继续'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
