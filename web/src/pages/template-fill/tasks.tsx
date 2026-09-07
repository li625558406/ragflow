import dayjs from 'dayjs';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
import {
  downloadTemplateFillResult,
  useListTemplateFillTasks,
  useRetryTemplateFillTask,
  type TplFillTaskItem,
} from '@/hooks/use-template-fill-request';

const PAGE_SIZE = 20;

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  retrieving: '检索中',
  generating: '生成中',
  rendering: '渲染中',
  done: '已完成',
  partial: '部分完成',
  failed: '失败',
};

// 状态 → Badge 配色（照 ui/badge 既有变体；done/partial/failed 着色，进行中中性）
const STATUS_VARIANT: Record<
  string,
  'default' | 'secondary' | 'success' | 'destructive' | 'outline'
> = {
  pending: 'secondary',
  retrieving: 'secondary',
  generating: 'default',
  rendering: 'default',
  done: 'success',
  partial: 'outline',
  failed: 'destructive',
};

const STATUS_OPTIONS = Object.entries(STATUS_LABEL);

// create_time 为后端秒级时间戳（BigInteger，time.time()），转本地显示
function formatCreateTime(ts?: number) {
  if (!ts) return '-';
  return dayjs.unix(ts).format('YYYY-MM-DD HH:mm');
}

export default function TemplateFillTasksPage() {
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const { data, isLoading } = useListTemplateFillTasks({
    status,
    page,
    size: PAGE_SIZE,
  });
  const retryMut = useRetryTemplateFillTask();

  const items = data?.data ?? [];
  const total = data?.total_datasets;
  const hasMore =
    total != null ? page * PAGE_SIZE < total : items.length >= PAGE_SIZE;

  // Task 11 接入详情抽屉后在此打开（当前占位）
  const handleDetail = () => {
    message.info('任务详情即将上线');
  };

  const handleDownload = (task: TplFillTaskItem) => {
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
    <Card className="bg-transparent border-none">
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
      <CardContent>
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
                      onClick={() => handleDetail()}
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
        <div className="mt-4 flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!hasMore}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
