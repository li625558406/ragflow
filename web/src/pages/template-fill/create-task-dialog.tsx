import { useQuery } from '@tanstack/react-query';
import { Plus, Search, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';

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
import { Label } from '@/components/ui/label';
import message from '@/components/ui/message';
import { useCreateTemplateFillTask } from '@/hooks/use-template-fill-request';
import { Routes } from '@/routes';
import { listDataset } from '@/services/knowledge-service';

interface CreateTaskDialogProps {
  templateId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface ParamRow {
  id: number;
  name: string;
  value: string;
}

export function CreateTaskDialog({
  templateId,
  open,
  onOpenChange,
}: CreateTaskDialogProps) {
  const navigate = useNavigate();
  const [kbKeyword, setKbKeyword] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [paramRows, setParamRows] = useState<ParamRow[]>([]);
  const nextIdRef = useRef(1);

  const createMut = useCreateTemplateFillTask();

  // 知识库列表：对话框打开时才拉取（一次拉全量，前端搜索过滤）
  const { data: kbs = [], isLoading: kbLoading } = useQuery({
    queryKey: ['templateFillKbList'],
    queryFn: async () => {
      const { data } = await listDataset({ page: 1, page_size: 9999 });
      if (data?.code !== 0) {
        throw new Error(data?.message || '知识库列表加载失败');
      }
      return (data?.data ?? []) as { id: string; name: string }[];
    },
    enabled: open,
  });

  const filteredKbs = kbKeyword.trim()
    ? kbs.filter((kb) =>
        (kb.name || '').toLowerCase().includes(kbKeyword.trim().toLowerCase()),
      )
    : kbs;

  const toggleKb = (id: string, checked: boolean) => {
    setKbIds((prev) =>
      checked ? [...prev, id] : prev.filter((k) => k !== id),
    );
  };

  const addParamRow = () => {
    setParamRows((prev) => [
      ...prev,
      { id: nextIdRef.current++, name: '', value: '' },
    ]);
  };

  const updateParamRow = (id: number, patch: Partial<ParamRow>) => {
    setParamRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, ...patch } : r)),
    );
  };

  const removeParamRow = (id: number) => {
    setParamRows((prev) => prev.filter((r) => r.id !== id));
  };

  const handleSubmit = () => {
    if (kbIds.length === 0) return;
    // 组装 params：参数名为空的行忽略
    const params: Record<string, string> = {};
    paramRows.forEach((row) => {
      const key = row.name.trim();
      if (key) params[key] = row.value;
    });
    createMut.mutate(
      { template_id: templateId, kb_ids: kbIds, params },
      {
        onSuccess: () => {
          message.success('填写任务已发起');
          onOpenChange(false);
          navigate(Routes.TemplateFillTasks);
        },
        onError: (err) =>
          message.error(err instanceof Error ? err.message : '发起失败'),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-auto">
        <DialogHeader>
          <DialogTitle>发起填写</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* 知识库多选 */}
          <div className="space-y-1.5">
            <Label>选择知识库 *</Label>
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pl-8"
                placeholder="搜索知识库名称"
                value={kbKeyword}
                onChange={(e) => setKbKeyword(e.target.value)}
              />
            </div>
            <div className="max-h-56 overflow-auto rounded border">
              {kbLoading ? (
                <div className="flex items-center justify-center py-6">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-b-transparent" />
                </div>
              ) : filteredKbs.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  {kbs.length === 0 ? '暂无知识库' : '未匹配到知识库'}
                </p>
              ) : (
                filteredKbs.map((kb) => (
                  <label
                    key={kb.id}
                    className="flex cursor-pointer items-center gap-2 border-b px-3 py-2 text-sm last:border-b-0 hover:bg-muted/40"
                  >
                    <Checkbox
                      checked={kbIds.includes(kb.id)}
                      onCheckedChange={(v) => toggleKb(kb.id, v === true)}
                    />
                    <span className="truncate">{kb.name}</span>
                  </label>
                ))
              )}
            </div>
            {kbIds.length === 0 && (
              <p className="text-xs text-muted-foreground">
                请选择至少一个知识库
              </p>
            )}
          </div>

          {/* params 键值对 */}
          <div className="space-y-1.5">
            <Label>填写参数（可选）</Label>
            {paramRows.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                模板中 fill_mode 为 param 的填写点需要在此提供参数值
              </p>
            ) : (
              <div className="space-y-2">
                {paramRows.map((row) => (
                  <div key={row.id} className="flex items-center gap-2">
                    <Input
                      className="flex-1"
                      placeholder="参数名"
                      value={row.name}
                      onChange={(e) =>
                        updateParamRow(row.id, { name: e.target.value })
                      }
                    />
                    <Input
                      className="flex-1"
                      placeholder="参数值"
                      value={row.value}
                      onChange={(e) =>
                        updateParamRow(row.id, { value: e.target.value })
                      }
                    />
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      title="删除该行"
                      onClick={() => removeParamRow(row.id)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
            <Button size="sm" variant="outline" onClick={addParamRow}>
              <Plus className="size-4" />
              添加参数
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={kbIds.length === 0 || createMut.isPending}
            onClick={handleSubmit}
          >
            {createMut.isPending ? '发起中…' : '发起填写'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
