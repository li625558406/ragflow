import dayjs from 'dayjs';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
} from '@/hooks/use-template-fill-request';
import { FillResultTable } from './fill-result-table';
import { TASK_STATUS_LABEL, TASK_STATUS_VARIANT } from './status';

interface TaskDetailDrawerProps {
  taskId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
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

            {/* 逐格表：占位符 → 值/状态/证据（公共组件，测试填写对话框共用） */}
            <div>
              <p className="mb-1 text-sm font-medium">
                填写明细（共 {placeholders.length} 格）
              </p>
              {!task.values ? (
                <div className="flex flex-col items-center justify-center rounded border py-10 text-sm text-muted-foreground">
                  <div className="mb-3 h-6 w-6 animate-spin rounded-full border-2 border-primary border-b-transparent" />
                  等待执行
                </div>
              ) : (
                <FillResultTable
                  placeholders={placeholders}
                  values={task.values?.render}
                  cells={task.values?.cells}
                  evidence={task.evidence}
                />
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
