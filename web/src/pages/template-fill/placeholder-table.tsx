import { Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

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
    default_value: '',
    default_source: '' as const,
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

// 默认值单元格：受控 Input 失焦即保存，右侧展示来源徽标（手动/沉淀/识别）
function DefaultValueCell({
  row,
  index,
  onUpdate,
  onSave,
  saving,
  disabled,
  persistedKeys,
}: {
  row: TplPlaceholder;
  index: number;
  onUpdate: (index: number, patch: Partial<TplPlaceholder>) => void;
  onSave: (key: string, value: string) => void;
  saving: boolean;
  disabled?: boolean;
  persistedKeys?: Set<string>;
}) {
  const [val, setVal] = useState(row.default_value || '');
  // 服务端数据回流（保存配置成功 invalidate 详情）时同步本地输入框
  useEffect(() => setVal(row.default_value || ''), [row.default_value]);
  const commit = () => {
    const v = val.trim();
    // 值未变化不做任何写
    if ((row.default_value || '') === v) return;
    // 先回写共享 rows state（后端保存成功后 default_source 置 manual，本地同步置 manual 保持徽标一致）：
    // 1) 「保存配置」全量 POST 时携带最新 default_value，消除端点在途与全量保存的竞态
    // 2) 新增行（key 未落库）的编辑不丢，等「保存配置」一并落库
    if (!row.key) return;
    onUpdate(index, { default_value: v, default_source: 'manual' });
    // key 未持久化（服务端还没有该填写点）时只回写本地，不调即时保存端点
    if (persistedKeys && !persistedKeys.has(row.key)) return;
    onSave(row.key, v);
  };
  return (
    <div className="flex items-center gap-1">
      <Input
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          // 中文 IME 合成态的 Enter（确认候选词）不触发失焦提交，避免丢字
          if (
            e.key === 'Enter' &&
            !(e.nativeEvent as KeyboardEvent).isComposing
          ) {
            (e.target as HTMLInputElement).blur();
          }
        }}
        placeholder="空=无默认值"
        className="h-7 text-xs"
        disabled={saving || disabled}
      />
      {row.default_source === 'manual' && (
        <span className="shrink-0 rounded bg-[#EFF4FF] px-1 text-[10px] text-[#1a66fb]">
          手动
        </span>
      )}
      {row.default_source === 'auto' && (
        <span className="shrink-0 rounded bg-[#F0F9EB] px-1 text-[10px] text-[#52c41a]">
          沉淀
        </span>
      )}
      {row.default_source === 'detected' && (
        <span className="shrink-0 rounded bg-[#FFF7E6] px-1 text-[10px] text-[#FA8C16]">
          识别
        </span>
      )}
    </div>
  );
}

interface PlaceholderTableProps {
  rows: TplPlaceholder[];
  errors: Record<string, boolean>;
  onUpdate: (index: number, patch: Partial<TplPlaceholder>) => void;
  onRemove: (index: number) => void;
  disabled?: boolean;
  /** 传入模板 id 才显示默认值列（无 id 的复用场景如上传向导不渲染） */
  templateId?: string;
  /** 默认值保存回调（单 key 即时保存） */
  onSaveDefault?: (key: string, value: string) => void;
  savingDefault?: boolean;
  /** 服务端已持久化的填写点 key 集合；默认值 commit 时未持久化的 key 只回写本地不调即时保存端点 */
  persistedKeys?: Set<string>;
}

// 可编辑填写点表格（上传向导 Step2 与模板详情页共用）
export function PlaceholderTable({
  rows,
  errors,
  onUpdate,
  onRemove,
  disabled = false,
  templateId,
  onSaveDefault,
  savingDefault = false,
  persistedKeys,
}: PlaceholderTableProps) {
  const errCls = (field: string) =>
    errors[field] ? 'border-red-500 focus-visible:ring-red-500' : '';

  // 无模板 id 时默认值列整体隐藏（无法调保存端点）
  const showDefaults = !!templateId && !!onSaveDefault;

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
          {showDefaults && <TableHead className="w-[180px]">默认值</TableHead>}
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
            {showDefaults && (
              <TableCell>
                <DefaultValueCell
                  row={row}
                  index={i}
                  onUpdate={onUpdate}
                  onSave={onSaveDefault!}
                  saving={savingDefault}
                  disabled={disabled}
                  persistedKeys={persistedKeys}
                />
              </TableCell>
            )}
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
