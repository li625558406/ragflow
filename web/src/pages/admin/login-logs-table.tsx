import { Badge } from '@/components/ui/badge';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useTranslation } from 'react-i18next';

interface LoginLogItem {
  id: string;
  login_time: string;
  ip: string | null;
  device_type: string;
  device_name: string | null;
  login_channel: string | null;
}

interface LoginLogsTableProps {
  logs: LoginLogItem[] | undefined;
  total: number | undefined;
  page: number;
  size: number;
  onPageChange: (page: number) => void;
  isLoading: boolean;
}

export default function LoginLogsTable({
  logs,
  total,
  page,
  size,
  onPageChange,
  isLoading,
}: LoginLogsTableProps) {
  const { t } = useTranslation();

  return (
    <div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('admin.loginLogsTime')}</TableHead>
              <TableHead>IP</TableHead>
              <TableHead>{t('admin.loginLogsDevice')}</TableHead>
              <TableHead>{t('admin.loginLogsChannel')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="text-center py-8 text-text-tertiary"
                >
                  Loading...
                </TableCell>
              </TableRow>
            ) : !logs?.length ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="text-center py-8 text-text-tertiary"
                >
                  {t('common.noData')}
                </TableCell>
              </TableRow>
            ) : (
              logs.map((log) => (
                <TableRow key={log.id}>
                  <TableCell className="whitespace-nowrap">
                    {log.login_time}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {log.ip || '-'}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{log.device_type || '-'}</Badge>
                    {log.device_name && (
                      <span className="ml-1 text-xs text-text-tertiary">
                        {log.device_name}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{log.login_channel || '-'}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      {(total ?? 0) > size && (
        <div className="flex justify-end mt-3">
          <RAGFlowPagination
            current={page}
            pageSize={size}
            total={total ?? 0}
            onChange={(p) => onPageChange(p)}
          />
        </div>
      )}
    </div>
  );
}
