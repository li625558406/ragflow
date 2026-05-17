import ListFilterBar from '@/components/list-filter-bar';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useGetPaginationWithRouter } from '@/hooks/logic-hooks';
import { fetchScheduledTaskLogs } from '@/services/scheduled-task-service';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

export default function ScheduledTaskLogs() {
  const { taskId } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pagination, setPagination } = useGetPaginationWithRouter();
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  const { data, isFetching: loading } = useQuery({
    queryKey: ['scheduledTaskLogs', taskId, pagination],
    enabled: !!taskId,
    initialData: { logs: [], total: 0 },
    queryFn: async () => {
      const { data: res } = await fetchScheduledTaskLogs(taskId!, {
        page: pagination.page,
        items_per_page: pagination.pageSize,
      });
      return res?.data ?? { logs: [], total: 0 };
    },
  });

  const logs = data?.logs ?? [];
  const total = data?.total ?? 0;

  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize: pageSize ?? pagination.pageSize });
    },
    [setPagination, pagination.pageSize],
  );

  const toggleRow = useCallback((id: string) => {
    setExpandedRow((prev) => (prev === id ? null : id));
  }, []);

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
    <article className="size-full flex flex-col">
      <header>
        <ListFilterBar title={t('scheduledTasks.log.title')}>
          <Button variant="outline" onClick={() => navigate(-1)}>
            <ArrowLeft className="size-4 mr-1" />
            {t('common.back')}
          </Button>
        </ListFilterBar>
      </header>

      <section className="flex-1 overflow-auto">
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
                    onClick={() => toggleRow(log.id)}
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
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleRow(log.id);
                        }}
                      >
                        {expandedRow === log.id
                          ? t('common.viewLess')
                          : t('common.viewMore')}
                      </Button>
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
            <div className="flex items-center justify-center h-full text-gray-400">
              {t('scheduledTasks.log.noLogs')}
            </div>
          )
        )}
      </section>

      <footer>
        <RAGFlowPagination
          total={total}
          currentPage={pagination.page}
          pageSize={pagination.pageSize}
          setCurrentPage={handlePageChange}
        />
      </footer>
    </article>
  );
}
