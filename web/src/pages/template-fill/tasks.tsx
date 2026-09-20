import dayjs from 'dayjs';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
// 通用分页：总数 + 页码 + 每页条数选择（10/20/50/100）
import message from '@/components/ui/message';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
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
import {
  downloadTemplateFillResult,
  useListTemplateFillTasks,
  useRetryTemplateFillTask,
  type TplFillTaskItem,
} from '@/hooks/use-template-fill-request';
import {
  TASK_STATUS_LABEL as STATUS_LABEL,
  TASK_STATUS_VARIANT as STATUS_VARIANT,
} from './status';
import { TaskDetailDrawer } from './task-detail-drawer';

const STATUS_OPTIONS = Object.entries(STATUS_LABEL);

// create_time 为后端秒级时间戳（BigInteger，time.time()），转本地显示
function formatCreateTime(ts?: number) {
  if (!ts) return '-';
  return dayjs.unix(ts).format('YYYY-MM-DD HH:mm');
}

export default function TemplateFillTasksPage() {
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const { data, isLoading } = useListTemplateFillTasks({
    status,
    page,
    size: pageSize,
  });
  const retryMut = useRetryTemplateFillTask();
  // 详情抽屉：'' 表示未打开（抽屉内 hook 对空 taskId 不发请求）
  const [detailTaskId, setDetailTaskId] = useState('');

  const items = data?.data ?? [];
  const total = data?.total_datasets;

  const handleDetail = (taskId: string) => {
    setDetailTaskId(taskId);
  };

  const handleDownload = (task: TplFillTaskItem) => {
    // 后端 list 未带 file_type，xlsx 范本生成的文件名后缀可能不准（详情抽屉内按范本 file_type 下载）
    downloadTemplateFillResult(task.id).catch((err) =>
      message.error(err instanceof Error ? err.message : '下载失败'),
    );
  };

  const handleRetry = (task: TplFillTaskItem) => {
    retryMut.mutate(task.id, {
      onSuccess: () => message.success('已重新发起填写任务'),
      onError: (err) =>
        message.error(err instanceof Error ? err.message : '重试失败'),
    });
  };

  return (
    // main（root-layout）是 overflow-hidden，页面自带内部滚动容器防裁切
    <Card className="flex size-full flex-col overflow-hidden bg-transparent border-none">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-2xl">填写任务</CardTitle>
        </div>
        <div className="flex gap-2">
          <Select
            value={status || 'all'}
            onValueChange={(v) => {
              setStatus(v === 'all' ? '' : v);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-32">
              <SelectValue placeholder="全部状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              {STATUS_OPTIONS.map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-b-transparent" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <p>暂无填写任务，从范本详情页发起</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>任务ID</TableHead>
                <TableHead>范本</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="w-[240px]">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((task) => (
                <TableRow key={task.id}>
                  <TableCell className="font-mono text-xs">
                    {task.id.slice(0, 8)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {task.template_id.slice(0, 8)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[task.status] ?? 'secondary'}>
                      {STATUS_LABEL[task.status] ?? task.status}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatCreateTime(task.create_time)}</TableCell>
                  <TableCell className="space-x-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleDetail(task.id)}
                    >
                      详情
                    </Button>
                    {(task.status === 'done' || task.status === 'partial') && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownload(task)}
                      >
                        下载
                      </Button>
                    )}
                    {(task.status === 'failed' ||
                      task.status === 'partial') && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={retryMut.isPending}
                        onClick={() => handleRetry(task)}
                      >
                        重试
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <div className="px-6 pb-4">
        <RAGFlowPagination
          total={total ?? 0}
          current={page}
          pageSize={pageSize}
          onChange={(p, ps) => {
            if (ps !== pageSize) {
              setPage(1);
              setPageSize(ps);
            } else {
              setPage(p);
            }
          }}
        />
      </div>
      <TaskDetailDrawer
        taskId={detailTaskId}
        open={!!detailTaskId}
        onOpenChange={(o) => !o && setDetailTaskId('')}
      />
    </Card>
  );
}
