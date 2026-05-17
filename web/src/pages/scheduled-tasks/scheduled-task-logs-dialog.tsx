import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  fetchScheduledTaskLogs,
  stopScheduledTask,
} from '@/services/scheduled-task-service';
import { useMutation, useQuery } from '@tanstack/react-query';
import { RotateCw, Square } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  taskId: string;
  taskName: string;
  visible: boolean;
  hideModal: () => void;
}

export function ScheduledTaskLogsDialog({
  taskId,
  taskName,
  visible,
  hideModal,
}: Props) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  const {
    data,
    isFetching: loading,
    refetch,
  } = useQuery({
    queryKey: ['scheduledTaskLogs', taskId, { page, pageSize }],
    enabled: visible && !!taskId,
    initialData: { logs: [], total: 0 },
    refetchInterval: (query) => {
      const logs = (query.state.data?.logs ?? []) as any[];
      return logs.some((l: any) => l.status === 'running') ? 3000 : false;
    },
    queryFn: async () => {
      const { data: res } = await fetchScheduledTaskLogs(taskId, {
        page,
        items_per_page: pageSize,
      });
      return res?.data ?? { logs: [], total: 0 };
    },
  });

  const stopMutation = useMutation({
    mutationFn: async (runId: string) => {
      const { data: res } = await stopScheduledTask(runId);
      return res;
    },
    onSuccess: () => refetch(),
  });

  const logs = data?.logs ?? [];
  const total = data?.total ?? 0;

  const formatTs = (ts: number | null | undefined) => {
    if (!ts) return '-';
    return new Date(ts).toLocaleString();
  };

  const statusBadge = (status: string) => {
    switch (status) {
      case 'running':
        return (
          <span className="text-yellow-600">{t('scheduledTasks.running')}</span>
        );
      case 'success':
        return (
          <span className="text-green-600">{t('scheduledTasks.success')}</span>
        );
      case 'fail':
        return <span className="text-red-600">{t('scheduledTasks.fail')}</span>;
      default:
        return <span>{status}</span>;
    }
  };

  return (
    <Dialog open={visible} onOpenChange={hideModal}>
      <DialogContent className="sm:max-w-[900px] max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {t('scheduledTasks.log.title')} - {taskName}
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => refetch()}
              disabled={loading}
              title="Refresh"
            >
              <RotateCw
                className={`size-3.5 ${loading ? 'animate-spin' : ''}`}
              />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-auto min-h-0">
          {logs.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('scheduledTasks.log.startTime')}</TableHead>
                  <TableHead>{t('scheduledTasks.log.endTime')}</TableHead>
                  <TableHead>{t('scheduledTasks.log.duration')}</TableHead>
                  <TableHead>{t('scheduledTasks.status')}</TableHead>
                  <TableHead>{t('scheduledTasks.log.pid')}</TableHead>
                  <TableHead>{t('common.action')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((log: any) => (
                  <>
                    <TableRow
                      key={log.id}
                      className="cursor-pointer"
                      onClick={() =>
                        setExpandedRow((prev) =>
                          prev === log.id ? null : log.id,
                        )
                      }
                    >
                      <TableCell>{formatTs(log.start_time)}</TableCell>
                      <TableCell>{formatTs(log.end_time)}</TableCell>
                      <TableCell>
                        {log.duration != null
                          ? `${log.duration.toFixed(2)}s`
                          : '-'}
                      </TableCell>
                      <TableCell>{statusBadge(log.status)}</TableCell>
                      <TableCell>{log.pid ?? '-'}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          {log.status === 'running' && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-red-500 hover:text-red-700"
                              onClick={(e) => {
                                e.stopPropagation();
                                stopMutation.mutate(taskId);
                              }}
                              disabled={stopMutation.isPending}
                              title="Stop"
                            >
                              <Square className="size-3.5 mr-1 fill-current" />
                              Stop
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedRow((prev) =>
                                prev === log.id ? null : log.id,
                              );
                            }}
                          >
                            {expandedRow === log.id
                              ? t('common.viewLess')
                              : t('common.viewMore')}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                    {expandedRow === log.id && (
                      <TableRow key={`${log.id}-detail`}>
                        <TableCell colSpan={6} className="bg-gray-50 p-4">
                          <div className="space-y-3">
                            <div>
                              <h4 className="text-sm font-semibold mb-1">
                                {t('scheduledTasks.log.output')}
                              </h4>
                              <pre className="text-xs bg-gray-100 p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">
                                {log.output || '-'}
                              </pre>
                            </div>
                            <div>
                              <h4 className="text-sm font-semibold mb-1">
                                {t('scheduledTasks.log.error')}
                              </h4>
                              <pre className="text-xs bg-red-50 p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap text-red-700">
                                {log.error_msg || '-'}
                              </pre>
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                ))}
              </TableBody>
            </Table>
          ) : (
            !loading && (
              <div className="flex items-center justify-center h-40 text-gray-400">
                {t('scheduledTasks.log.noLogs')}
              </div>
            )
          )}
        </div>

        <div className="pt-3 border-t mt-3 flex items-center justify-between">
          <RAGFlowPagination
            total={total}
            currentPage={page}
            pageSize={pageSize}
            setCurrentPage={(p, ps) => {
              setPage(p);
              if (ps) setPageSize(ps);
            }}
          />
          <Button variant="outline" onClick={hideModal}>
            {t('common.close')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
