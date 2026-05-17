import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import ListFilterBar from '@/components/list-filter-bar';
import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
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
  useGetPaginationWithRouter,
  useHandleSearchChange,
} from '@/hooks/logic-hooks';
import scheduledTaskService, {
  toggleScheduledTask,
  updateScheduledTask,
} from '@/services/scheduled-task-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDebounce } from 'ahooks';
import { Eye, FileJson2, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ScheduledTaskDialog } from './scheduled-task-dialog';
import { ScheduledTaskLogsDialog } from './scheduled-task-logs-dialog';
import { ScheduledTaskStateDialog } from './scheduled-task-state-dialog';

export default function ScheduledTasks() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { pagination, setPagination } = useGetPaginationWithRouter();
  const { searchString, handleInputChange } = useHandleSearchChange();
  const debouncedSearchString = useDebounce(searchString, { wait: 500 });

  const [dialogVisible, setDialogVisible] = useState(false);
  const [editingTask, setEditingTask] = useState<any>(null);
  const [logsDialog, setLogsDialog] = useState<{
    taskId: string;
    taskName: string;
  } | null>(null);
  const [stateDialog, setStateDialog] = useState<{
    taskId: string;
    taskName: string;
  } | null>(null);

  const { data, isFetching: loading } = useQuery({
    queryKey: [
      'scheduledTasks',
      {
        debouncedSearchString,
        page: pagination.page,
        pageSize: pagination.pageSize,
      },
    ],
    initialData: { tasks: [], total: 0 },
    refetchInterval: (query) => {
      const tasks = (query.state.data?.tasks ?? []) as any[];
      return tasks.some((t: any) => t.last_run_status === 'running')
        ? 5000
        : false;
    },
    queryFn: async () => {
      const { data: res } = await scheduledTaskService.listScheduledTasks(
        {
          params: {
            page: pagination.page,
            items_per_page: pagination.pageSize,
            name: debouncedSearchString || undefined,
          },
        },
        true,
      );
      return res?.data ?? { tasks: [], total: 0 };
    },
  });

  const tasks = data?.tasks ?? [];
  const total = data?.total ?? 0;

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['scheduledTasks'] });
  }, [queryClient]);

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      const { data: res } = await toggleScheduledTask(id, enabled);
      return res;
    },
    onSuccess: () => refresh(),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data: res } = await scheduledTaskService.deleteScheduledTask(id);
      return res;
    },
    onSuccess: () => refresh(),
  });

  const runNowMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data: res } = await scheduledTaskService.runScheduledTaskNow(id);
      return res;
    },
    onSuccess: () => refresh(),
  });

  const saveMutation = useMutation({
    mutationFn: async (values: Record<string, any>) => {
      if (editingTask) {
        const { data: res } = await updateScheduledTask(editingTask.id, values);
        return res;
      } else {
        const { data: res } =
          await scheduledTaskService.createScheduledTask(values);
        return res;
      }
    },
    onSuccess: () => {
      refresh();
      setDialogVisible(false);
      setEditingTask(null);
    },
  });

  const handleCreate = useCallback(() => {
    setEditingTask(null);
    setDialogVisible(true);
  }, []);

  const handleEdit = useCallback((task: any) => {
    setEditingTask(task);
    setDialogVisible(true);
  }, []);

  const handleDelete = useCallback(
    (id: string) => {
      if (window.confirm(t('common.deleteModalTitle'))) {
        deleteMutation.mutate(id);
      }
    },
    [deleteMutation, t],
  );

  const handleToggle = useCallback(
    (id: string, currentEnabled: boolean) => {
      toggleMutation.mutate({ id, enabled: !currentEnabled });
    },
    [toggleMutation],
  );

  const handleRunNow = useCallback(
    (task: any) => {
      runNowMutation.mutate(task.id, {
        onSuccess: (res: any) => {
          if (res?.code === 0) {
            message.success(t('common.submit'));
            setLogsDialog({ taskId: task.id, taskName: task.name });
          }
        },
      });
    },
    [runNowMutation, t],
  );

  const handleDialogOk = useCallback(
    async (values: Record<string, any>) => {
      await saveMutation.mutateAsync(values);
      return true;
    },
    [saveMutation],
  );

  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize: pageSize ?? pagination.pageSize });
    },
    [setPagination, pagination.pageSize],
  );

  const formatTs = (ts: number | null | undefined) => {
    if (!ts) return '-';
    return new Date(ts).toLocaleString();
  };

  return (
    <>
      {tasks.length || searchString ? (
        <article
          className="size-full flex flex-col"
          data-testid="scheduled-tasks-list"
        >
          <header className="px-5 pt-8 mb-4">
            <ListFilterBar
              title={t('scheduledTasks.title')}
              searchString={searchString}
              onSearchChange={handleInputChange}
              icon={'file'}
            >
              <Button onClick={handleCreate}>
                <Plus className="size-[1em]" />
                {t('scheduledTasks.createTask')}
              </Button>
            </ListFilterBar>
          </header>

          {tasks.length ? (
            <>
              <section className="flex-1 overflow-auto px-5">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('scheduledTasks.name')}</TableHead>
                      <TableHead>{t('scheduledTasks.scriptPath')}</TableHead>
                      <TableHead>{t('scheduledTasks.scheduleType')}</TableHead>
                      <TableHead>{t('scheduledTasks.status')}</TableHead>
                      <TableHead>{t('scheduledTasks.lastRun')}</TableHead>
                      <TableHead>{t('scheduledTasks.nextRun')}</TableHead>
                      <TableHead>{t('common.action')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tasks.map((task: any) => (
                      <TableRow key={task.id}>
                        <TableCell className="font-medium">
                          {task.name}
                        </TableCell>
                        <TableCell className="max-w-[200px] truncate">
                          {task.script_path}
                        </TableCell>
                        <TableCell>
                          {task.schedule_type === 'cron'
                            ? task.cron_expression
                            : `${t('scheduledTasks.intervalSeconds')}: ${task.interval_seconds}`}
                        </TableCell>
                        <TableCell>
                          {task.enabled ? (
                            <span className="text-green-600">
                              {t('scheduledTasks.enabled')}
                            </span>
                          ) : (
                            <span className="text-gray-400">
                              {t('scheduledTasks.disabled')}
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          {task.last_run_status && (
                            <span
                              className={
                                task.last_run_status === 'success'
                                  ? 'text-green-600'
                                  : task.last_run_status === 'fail'
                                    ? 'text-red-600'
                                    : 'text-yellow-600'
                              }
                            >
                              [{task.last_run_status}]{' '}
                            </span>
                          )}
                          {formatTs(task.last_run_time)}
                        </TableCell>
                        <TableCell>{formatTs(task.next_run_time)}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1">
                            <Switch
                              checked={task.enabled}
                              onCheckedChange={() =>
                                handleToggle(task.id, task.enabled)
                              }
                            />
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleRunNow(task)}
                              title={t('scheduledTasks.runNow')}
                            >
                              <Play className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() =>
                                setLogsDialog({
                                  taskId: task.id,
                                  taskName: task.name,
                                })
                              }
                              title={t('scheduledTasks.viewLogs')}
                            >
                              <Eye className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() =>
                                setStateDialog({
                                  taskId: task.id,
                                  taskName: task.name,
                                })
                              }
                              title={t('scheduledTasks.crawlerState')}
                            >
                              <FileJson2 className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleEdit(task)}
                              title={t('common.edit')}
                            >
                              <Pencil className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleDelete(task.id, task.name)}
                              title={t('common.delete')}
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>

              <footer className="mt-4 px-5 pb-5">
                <RAGFlowPagination
                  total={total}
                  currentPage={pagination.page}
                  pageSize={pagination.pageSize}
                  setCurrentPage={handlePageChange}
                />
              </footer>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <EmptyAppCard
                showIcon
                size="large"
                className="w-[480px] p-14"
                isSearch
                type={EmptyCardType.ScheduledTask}
              />
            </div>
          )}
        </article>
      ) : (
        !loading && (
          <article
            className="size-full flex items-center justify-center"
            data-testid="scheduled-tasks-empty"
          >
            <EmptyAppCard
              showIcon
              size="large"
              className="w-[480px] p-14"
              type={EmptyCardType.ScheduledTask}
              onClick={() => handleCreate()}
            />
          </article>
        )
      )}

      {dialogVisible && (
        <ScheduledTaskDialog
          visible={dialogVisible}
          editingTask={editingTask}
          loading={saveMutation.isPending}
          hideModal={() => {
            setDialogVisible(false);
            setEditingTask(null);
          }}
          onOk={handleDialogOk}
        />
      )}

      {logsDialog && (
        <ScheduledTaskLogsDialog
          taskId={logsDialog.taskId}
          taskName={logsDialog.taskName}
          visible
          hideModal={() => setLogsDialog(null)}
        />
      )}

      {stateDialog && (
        <ScheduledTaskStateDialog
          taskId={stateDialog.taskId}
          taskName={stateDialog.taskName}
          visible
          hideModal={() => setStateDialog(null)}
        />
      )}
    </>
  );
}
