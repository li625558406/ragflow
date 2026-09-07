import { Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
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
import { type TplPlaceholder } from '@/hooks/use-template-fill-request';

export const KEY_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

// 新增空行的默认值（top_k 给检索兜底默认值）
export function emptyPlaceholder(): TplPlaceholder {
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

// 提交前统一 trim，保证「校验的值 = 提交的值」（校验与提交都用同一份归一化结果）
export function trimRows(rows: TplPlaceholder[]) {
  return rows.map((r) => ({
    ...r,
    key: r.key.trim(),
    name: r.name.trim(),
    anchor: r.anchor.trim(),
    retrieval_query: (r.retrieval_query || '').trim(),
  }));
}

// 行级校验：key 格式、name、anchor 非空；入参须已 trim
export function collectRowErrors(rows: TplPlaceholder[]) {
  const errors: Record<string, boolean> = {};
  rows.forEach((row, i) => {
    if (!row.key || !KEY_PATTERN.test(row.key)) {
      errors[`${i}-key`] = true;
    }
    if (!row.name) {
      errors[`${i}-name`] = true;
    }
    if (!row.anchor) {
      errors[`${i}-anchor`] = true;
    }
  });
  return errors;
}

interface PlaceholderTableProps {
  rows: TplPlaceholder[];
  errors: Record<string, boolean>;
  onUpdate: (index: number, patch: Partial<TplPlaceholder>) => void;
  onRemove: (index: number) => void;
  disabled?: boolean;
}

// 可编辑填写点表格（上传向导 Step2 与模板详情页共用）
export function PlaceholderTable({
  rows,
  errors,
  onUpdate,
  onRemove,
  disabled = false,
}: PlaceholderTableProps) {
  const errCls = (field: string) =>
    errors[field] ? 'border-red-500 focus-visible:ring-red-500' : '';

  return (
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
        {rows.map((row, i) => (
          <TableRow key={i}>
            <TableCell>
              <Input
                className={errCls(`${i}-key`)}
                value={row.key}
                onChange={(e) => onUpdate(i, { key: e.target.value })}
                placeholder="如 project_name"
                disabled={disabled}
              />
            </TableCell>
            <TableCell>
              <Input
                className={errCls(`${i}-name`)}
                value={row.name}
                onChange={(e) => onUpdate(i, { name: e.target.value })}
                disabled={disabled}
              />
            </TableCell>
            <TableCell>
              <Input
                value={row.retrieval_query}
                onChange={(e) =>
                  onUpdate(i, { retrieval_query: e.target.value })
                }
                disabled={disabled}
              />
            </TableCell>
            <TableCell>
              <Select
                value={row.fill_mode}
                onValueChange={(v) =>
                  onUpdate(i, { fill_mode: v as TplPlaceholder['fill_mode'] })
                }
                disabled={disabled}
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
                  onUpdate(i, { required: checked === true })
                }
                disabled={disabled}
              />
            </TableCell>
            <TableCell>
              {row.addr ? (
                <span
                  className={`block max-w-[160px] truncate text-sm ${
                    errors[`${i}-anchor`]
                      ? 'text-red-500'
                      : 'text-muted-foreground'
                  }`}
                  title={row.anchor}
                >
                  {row.anchor}
                </span>
              ) : (
                <Input
                  className={errCls(`${i}-anchor`)}
                  value={row.anchor}
                  onChange={(e) => onUpdate(i, { anchor: e.target.value })}
                  placeholder="模板中已有的原文片段"
                  disabled={disabled}
                />
              )}
            </TableCell>
            <TableCell>
              <Button
                size="icon-xs"
                variant="ghost"
                onClick={() => onRemove(i)}
                title="删除该填写点"
                disabled={disabled}
              >
                <Trash2 className="size-[1em] text-red-500" />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
