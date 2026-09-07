import dayjs from 'dayjs';
import { ChevronDown } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import message from '@/components/ui/message';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  downloadTemplateFillResult,
  useGetTemplateFillTask,
  useRetryTemplateFillTask,
  useTemplateFillDetail,
  type TplFillTaskItem,
  type TplPlaceholder,
} from '@/hooks/use-template-fill-request';
import { CELL_STATUS, TASK_STATUS_LABEL, TASK_STATUS_VARIANT } from './status';

interface TaskDetailDrawerProps {
  taskId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Task 9 类型放宽为 unknown，这里做安全 narrow：字符串原样，对象 JSON 化，其余 String()
function formatValue(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

// 证据 chunk 可能是 JSON 字符串（doc_name/similarity/content）也可能是纯文本
function parseChunk(chunk: unknown): {
  docName: string;
  similarity: string;
  content: string;
} {
  let raw: unknown = chunk;
  if (typeof chunk === 'string') {
    try {
      raw = JSON.parse(chunk);
    } catch {
      return { docName: '', similarity: '', content: chunk.slice(0, 120) };
    }
  }
  const obj = asRecord(raw);
  if (obj) {
    return {
      docName: typeof obj.doc_name === 'string' ? obj.doc_name : '',
      similarity: obj.similarity != null ? String(obj.similarity) : '',
      content: typeof obj.content === 'string' ? obj.content.slice(0, 120) : '',
    };
  }
  return { docName: '', similarity: '', content: String(raw).slice(0, 120) };
}

function EvidenceSection({ chunks }: { chunks: unknown }) {
  const list = Array.isArray(chunks) ? chunks.slice(0, 3) : [];
  if (list.length === 0) {
    return <p className="text-xs text-muted-foreground">无检索证据</p>;
  }
  return (
    <Collapsible>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-blue-600 hover:underline">
        检索证据（{list.length}）
        <ChevronDown className="size-3" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 space-y-2 rounded bg-muted/40 p-2">
          {list.map((chunk, i) => {
            const { docName, similarity, content } = parseChunk(chunk);
            return (
              <div key={i} className="text-xs">
                <div className="flex items-center gap-2 text-muted-foreground">
                  {docName && <span className="font-medium">{docName}</span>}
                  {similarity && <span>相似度 {similarity}</span>}
                </div>
                <p className="mt-0.5 leading-5">{content}</p>
              </div>
            );
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// 逐格一行：占位符名 + 值 + 状态 + 证据展开
function CellRow({
  placeholder,
  renderValue,
  cellStatus,
  chunks,
}: {
  placeholder: TplPlaceholder | undefined;
  renderValue: unknown;
  cellStatus: string;
  chunks: unknown;
}) {
  const statusMeta = CELL_STATUS[cellStatus];
  const label = statusMeta?.label ?? '未知';
  const cls = statusMeta?.cls ?? '';
  const key = placeholder?.key ?? '';
  return (
    <div className="border-b py-2.5 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {placeholder?.name || key || '（未知填写点）'}
          </p>
          {key && (
            <p className="font-mono text-xs text-muted-foreground">{key}</p>
          )}
        </div>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${cls}`}>
          {label}
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm">
        {renderValue == null || renderValue === '' ? (
          <span className="text-muted-foreground">-</span>
        ) : (
          formatValue(renderValue)
        )}
      </p>
      <div className="mt-1.5">
        <EvidenceSection chunks={chunks} />
      </div>
    </div>
  );
}

export function TaskDetailDrawer({
  taskId,
  open,
  onOpenChange,
}: TaskDetailDrawerProps) {
  // taskId 为空（未打开）时 hook 自身 enabled: false，不发请求
  const { data: taskRes, isLoading } = useGetTemplateFillTask(
    open ? taskId : '',
  );
  const task: TplFillTaskItem | undefined = taskRes?.data;

  // 占位符清单来自任务对应的范本（key → 中文名映射）
  const { data: tplRes } = useTemplateFillDetail(task?.template_id ?? '');
  const placeholders = tplRes?.data?.placeholders ?? [];

  const retryMut = useRetryTemplateFillTask();

  // values/cells 在 pending 态可能整体缺失，全部做安全 narrow
  const renderMap = asRecord(task?.values?.render);
  const cellsMap = asRecord(task?.values?.cells);

  const handleRetry = () => {
    retryMut.mutate(taskId, {
      onSuccess: () => message.success('已重新发起填写任务'),
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '重试失败'),
    });
  };

  const handleDownload = () => {
    // 范本详情里有 file_type，xlsx 范本下载 xlsx 后缀
    const ext = tplRes?.data?.file_type === 'xlsx' ? 'xlsx' : 'docx';
    downloadTemplateFillResult(taskId, ext).catch((err) =>
      message.error(err instanceof Error ? err.message : '下载失败'),
    );
  };

  const canDownload = task?.status === 'done' || task?.status === 'partial';
  const canRetry = task?.status === 'failed' || task?.status === 'partial';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-[560px] max-w-full flex-col gap-0 overflow-y-auto sm:max-w-[560px]"
      >
        <SheetHeader className="pb-2">
          <SheetTitle>任务详情</SheetTitle>
        </SheetHeader>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-b-transparent" />
          </div>
        ) : !task ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            任务不存在或已被删除
          </div>
        ) : (
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto pb-6">
            {/* 概要信息 */}
            <div className="space-y-2 rounded border p-3">
              <div className="flex items-center gap-2">
                <Badge
                  variant={TASK_STATUS_VARIANT[task.status] ?? 'secondary'}
                >
                  {TASK_STATUS_LABEL[task.status] ?? task.status}
                </Badge>
                {canDownload && (
                  <Button size="sm" variant="outline" onClick={handleDownload}>
                    下载生成稿
                  </Button>
                )}
                {canRetry && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={retryMut.isPending}
                    onClick={handleRetry}
                  >
                    重试
                  </Button>
                )}
              </div>
              {task.status === 'failed' && task.error && (
                <p className="break-words text-sm text-red-500">{task.error}</p>
              )}
              <div className="space-y-0.5 text-xs text-muted-foreground">
                <p className="font-mono">任务 ID：{task.id}</p>
                <p className="font-mono">
                  范本：{tplRes?.data?.name || task.template_id}
                </p>
                <p>
                  关联知识库：{task.kb_ids?.length ?? 0} 个 ｜ 参数：
                  {Object.keys(task.params ?? {}).length} 个
                </p>
                {task.create_time ? (
                  <p>
                    创建时间：
                    {dayjs.unix(task.create_time).format('YYYY-MM-DD HH:mm')}
                  </p>
                ) : null}
              </div>
            </div>

            {/* 逐格表：占位符 → 值/状态/证据 */}
            <div>
              <p className="mb-1 text-sm font-medium">
                填写明细（共 {placeholders.length} 格）
              </p>
              {!task.values ? (
                <div className="flex flex-col items-center justify-center rounded border py-10 text-sm text-muted-foreground">
                  <div className="mb-3 h-6 w-6 animate-spin rounded-full border-2 border-primary border-b-transparent" />
                  等待执行
                </div>
              ) : placeholders.length === 0 ? (
                <p className="rounded border py-8 text-center text-sm text-muted-foreground">
                  范本暂无填写点配置
                </p>
              ) : (
                <div>
                  {placeholders.map((ph) => {
                    const cell = cellsMap?.[ph.key];
                    const cellStatus = typeof cell === 'string' ? cell : '未知';
                    return (
                      <CellRow
                        key={ph.key}
                        placeholder={ph}
                        renderValue={renderMap?.[ph.key]}
                        cellStatus={cellStatus}
                        chunks={task.evidence?.[ph.key]?.chunks}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
