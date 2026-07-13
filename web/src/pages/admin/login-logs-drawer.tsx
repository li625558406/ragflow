import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { getUserLoginLogs, getUserLoginStats } from '@/services/admin-service';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import LoginLogsStats from './login-logs-stats';
import LoginLogsTable from './login-logs-table';

interface LoginLogsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  email: string;
  nickname?: string;
}

const DATE_PRESETS = [
  { value: '', label: 'admin.loginLogsAllTime' },
  { value: '7', label: 'admin.loginLogs7Days' },
  { value: '30', label: 'admin.loginLogs30Days' },
];

export default function LoginLogsDrawer({
  open,
  onOpenChange,
  email,
  nickname,
}: LoginLogsDrawerProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [datePreset, setDatePreset] = useState('30');

  // Reset state when switching to a different user
  useEffect(() => {
    setPage(1);
    setDatePreset('30');
  }, [email]);

  const now = new Date();
  const startDate = datePreset
    ? new Date(now.getTime() - parseInt(datePreset) * 86400000)
        .toISOString()
        .split('T')[0]
    : undefined;

  const { data: statsData, isLoading: statsLoading } = useQuery({
    queryKey: ['user-login-stats', email],
    queryFn: async () => (await getUserLoginStats(email)).data.data,
    enabled: open,
  });

  const { data: logsData, isLoading: logsLoading } = useQuery({
    queryKey: ['user-login-logs', email, page, datePreset],
    queryFn: async () =>
      (await getUserLoginLogs(email, { page, size: 20, start_date: startDate }))
        .data.data,
    enabled: open,
  });

  const handleDateChange = (value: string) => {
    setDatePreset(value);
    setPage(1);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[480px] sm:w-[540px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>
            {t('admin.loginLogsTitle')} — {nickname || email}
          </SheetTitle>
        </SheetHeader>

        <div className="mt-4 space-y-4">
          <LoginLogsStats stats={statsData} isLoading={statsLoading} />

          <div className="flex items-center gap-2">
            <Select value={datePreset} onValueChange={handleDateChange}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATE_PRESETS.map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {t(label)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <LoginLogsTable
            logs={logsData?.logs}
            total={logsData?.total}
            page={page}
            size={20}
            onPageChange={setPage}
            isLoading={logsLoading}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
