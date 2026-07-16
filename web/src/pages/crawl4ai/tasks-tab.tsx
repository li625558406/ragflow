import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Crawl4aiTask,
  deleteCrawl4aiTask,
  listCrawl4aiTasks,
  triggerCrawl4aiTask,
  updateCrawl4aiTask,
} from '@/services/crawl4ai-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TaskDialog } from './task-dialog';

export function TasksTab() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [dialogVisible, setDialogVisible] = useState(false);
  const [editingTask, setEditingTask] = useState<Crawl4aiTask | null>(null);

  const { data, isFetching } = useQuery({
    queryKey: ['crawl4aiTasks'],
    initialData: { list: [], total: 0 },
    refetchInterval: (query) => {
      const list = (query.state.data?.list ?? []) as Crawl4aiTask[];
      return list.some((x) => x.last_run_status === 'running') ? 5000 : false;
    },
    queryFn: async () => {
      const { data: res } = await listCrawl4aiTasks({
        page: 1,
        page_size: 100,
      });
      return res?.code === 0 ? res.data : { list: [], total: 0 };
    },
  });

  const tasks: Crawl4aiTask[] = data?.list ?? [];

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['crawl4aiTasks'] });
    queryClient.invalidateQueries({ queryKey: ['crawl4aiResults'] });
  }, [queryClient]);

  const triggerMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data: res } = await triggerCrawl4aiTask(id);
      return res;
    },
    onSuccess: (res: any) => {
      if (res?.code === 0) {
        message.success(t('crawl4ai.triggerSuccess'));
      } else {
        message.error(res?.message ?? t('crawl4ai.triggerFailed'));
      }
      refresh();
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      const { data: res } = await updateCrawl4aiTask(id, { enabled });
      return res;
    },
    onSuccess: () => refresh(),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data: res } = await deleteCrawl4aiTask(id);
      return res;
    },
    onSuccess: () => refresh(),
  });

  const handleDelete = useCallback(
    (id: string) => {
      if (window.confirm(t('common.deleteModalTitle'))) {
        deleteMutation.mutate(id);
      }
    },
    [deleteMutation, t],
  );

  const formatTs = (ts?: number | null) =>
    ts ? new Date(ts).toLocaleString() : '-';

  const statusColor = (s?: string) =>
    s === 'success'
      ? 'text-green-600'
      : s === 'fail'
        ? 'text-red-600'
        : s === 'running'
          ? 'text-yellow-600'
          : 'text-muted-foreground';

  return (
    <div className="size-full flex flex-col gap-4">
      <section className="flex justify-end">
        <Button
          onClick={() => {
            setEditingTask(null);
            setDialogVisible(true);
          }}
        >
          <Plus className="size-[1em]" />
          {t('crawl4ai.createTask')}
        </Button>
      </section>

      <section className="flex-1 overflow-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('crawl4ai.taskName')}</TableHead>
              <TableHead>{t('crawl4ai.site')}</TableHead>
              <TableHead className="max-w-[260px]">
                {t('crawl4ai.targetUrl')}
              </TableHead>
              <TableHead>{t('crawl4ai.lastRun')}</TableHead>
              <TableHead>{t('crawl4ai.lastRunSummary')}</TableHead>
              <TableHead>{t('common.action')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-center text-muted-foreground py-10"
                >
                  {isFetching ? t('crawl4ai.loading') : t('crawl4ai.noTasks')}
                </TableCell>
              </TableRow>
            )}
            {tasks.map((task) => {
              const summary = task.last_run_summary ?? {};
              return (
                <TableRow key={task.id}>
                  <TableCell className="font-medium">{task.name}</TableCell>
                  <TableCell>{task.site_id}</TableCell>
                  <TableCell className="max-w-[260px] truncate">
                    {task.target_url}
                  </TableCell>
                  <TableCell>
                    {task.last_run_status && (
                      <span className={statusColor(task.last_run_status)}>
                        [
                        {t(
                          `crawl4ai.run_${task.last_run_status}`,
                          task.last_run_status,
                        )}
                        ]{' '}
                      </span>
                    )}
                    {formatTs(task.last_run_time)}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {summary.items_new != null
                      ? t('crawl4ai.summaryText', {
                          pages: summary.pages ?? 0,
                          found: summary.items_found ?? 0,
                          added: summary.items_new ?? 0,
                        })
                      : '-'}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Switch
                        checked={!!task.enabled}
                        onCheckedChange={(checked) =>
                          toggleMutation.mutate({
                            id: task.id,
                            enabled: checked,
                          })
                        }
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        disabled={task.last_run_status === 'running'}
                        onClick={() => triggerMutation.mutate(task.id)}
                        title={t('crawl4ai.trigger')}
                      >
                        <Play className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setEditingTask(task);
                          setDialogVisible(true);
                        }}
                        title={t('common.edit')}
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(task.id)}
                        title={t('common.delete')}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </section>

      {dialogVisible && (
        <TaskDialog
          editingTask={editingTask}
          hideModal={() => {
            setDialogVisible(false);
            setEditingTask(null);
          }}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
