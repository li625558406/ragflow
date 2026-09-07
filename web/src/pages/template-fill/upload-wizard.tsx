import { Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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

const KEY_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

const FILL_MODE_LABEL: Record<TplPlaceholder['fill_mode'], string> = {
  llm: 'AI填写',
  param: '参数',
  manual: '人工',
};

// 新增空行的默认值（top_k 给检索兜底默认值）
function emptyPlaceholder(): TplPlaceholder {
  return {
    key: '',
    name: '',
    description: '',
    retrieval_query: '',
    fill_mode: 'llm',
    required: true,
    addr: '',
    anchor: '',
    top_k: 5,
  };
}

interface UploadWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (id: string) => void;
}

export function UploadWizard({
  open,
  onOpenChange,
  onSaved,
}: UploadWizardProps) {
  const [step, setStep] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [step1Error, setStep1Error] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [placeholders, setPlaceholders] = useState<TplPlaceholder[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, boolean>>({});

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
      setPlaceholders([]);
      setRowErrors({});
      detectMut.reset();
    }
    onOpenChange(next);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setStep1Error('');
    if (f && !name) {
      // 默认模板名取文件名去扩展
      setName(f.name.replace(/\.[^.]+$/, ''));
    }
  };

  const goStep2 = () => {
    if (!file) {
      setStep1Error('请选择 .docx 或 .xlsx 模板文件');
      return;
    }
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (ext !== '.docx' && ext !== '.xlsx') {
      setStep1Error('仅支持 .docx / .xlsx 文件');
      return;
    }
    if (!name.trim()) {
      setStep1Error('请填写模板名称');
      return;
    }
    setStep1Error('');
    uploadMut.mutate(
      { file, name: name.trim(), description: description.trim() },
      {
        onSuccess: (data) => {
          setTemplateId(data.id);
          setPlaceholders([]);
          setStep(2);
          detectMut.mutate(data.id, {
            onSuccess: (res) => {
              if (res.suggestions.length > 0) {
                setPlaceholders(res.suggestions);
              }
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

  // 行级校验：key 格式、name、anchor 非空；返回是否有错
  const validateRows = () => {
    const errors: Record<string, boolean> = {};
    placeholders.forEach((row, i) => {
      if (!row.key.trim() || !KEY_PATTERN.test(row.key.trim())) {
        errors[`${i}-key`] = true;
      }
      if (!row.name.trim()) {
        errors[`${i}-name`] = true;
      }
      if (!row.anchor.trim()) {
        errors[`${i}-anchor`] = true;
      }
    });
    setRowErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const goStep3 = () => {
    if (placeholders.length === 0) {
      message.error('请至少添加一个填写点');
      return;
    }
    if (!validateRows()) {
      message.error('存在格式不正确的填写点，请检查标红字段');
      return;
    }
    setStep(3);
  };

  const saveConfig = () => {
    if (!validateRows()) {
      message.error('存在格式不正确的填写点，请返回修改');
      setStep(2);
      return;
    }
    saveMut.mutate(
      { id: templateId, placeholders },
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

  const errCls = (field: string) =>
    rowErrors[field] ? 'border-red-500 focus-visible:ring-red-500' : '';

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
              <label className="text-sm">模板文件（.docx / .xlsx）</label>
              <Input
                type="file"
                accept=".docx,.xlsx"
                onChange={handleFileChange}
              />
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
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>key（小写字母开头）</TableHead>
                      <TableHead>中文名</TableHead>
                      <TableHead>检索词</TableHead>
                      <TableHead className="w-[110px]">填写方式</TableHead>
                      <TableHead className="w-[60px]">必填</TableHead>
                      <TableHead>锚文本</TableHead>
                      <TableHead className="w-[40px]" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {placeholders.map((row, i) => (
                      <TableRow key={i}>
                        <TableCell>
                          <Input
                            className={errCls(`${i}-key`)}
                            value={row.key}
                            onChange={(e) =>
                              updateRow(i, { key: e.target.value })
                            }
                            placeholder="如 project_name"
                          />
                        </TableCell>
                        <TableCell>
                          <Input
                            className={errCls(`${i}-name`)}
                            value={row.name}
                            onChange={(e) =>
                              updateRow(i, { name: e.target.value })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <Input
                            value={row.retrieval_query}
                            onChange={(e) =>
                              updateRow(i, { retrieval_query: e.target.value })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <Select
                            value={row.fill_mode}
                            onValueChange={(v) =>
                              updateRow(i, {
                                fill_mode: v as TplPlaceholder['fill_mode'],
                              })
                            }
                          >
                            <SelectTrigger className="w-[100px]">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="llm">AI填写</SelectItem>
                              <SelectItem value="param">参数</SelectItem>
                              <SelectItem value="manual">人工</SelectItem>
                            </SelectContent>
                          </Select>
                        </TableCell>
                        <TableCell className="text-center">
                          <Checkbox
                            checked={row.required}
                            onCheckedChange={(checked) =>
                              updateRow(i, { required: checked === true })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <span
                            className={`block max-w-[160px] truncate text-sm ${
                              rowErrors[`${i}-anchor`]
                                ? 'text-red-500'
                                : 'text-muted-foreground'
                            }`}
                            title={row.anchor}
                          >
                            {row.anchor || '（手动添加，请补锚文本）'}
                          </span>
                        </TableCell>
                        <TableCell>
                          <Button
                            size="icon-xs"
                            variant="ghost"
                            onClick={() => removeRow(i)}
                            title="删除该填写点"
                          >
                            <Trash2 className="size-[1em] text-red-500" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {!detecting && (
              <div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    setPlaceholders((prev) => [...prev, emptyPlaceholder()])
                  }
                >
                  添加填写点
                </Button>
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
