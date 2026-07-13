import { Card, CardContent } from '@/components/ui/card';
import {
  LucideActivity,
  LucideCalendar,
  LucideClock,
  LucideMonitor,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface LoginLogStatsProps {
  stats:
    | {
        total: number;
        recent_7d: number;
        last_login_time: string | null;
        common_device: string | null;
      }
    | undefined;
  isLoading: boolean;
}

export default function LoginLogsStats({
  stats,
  isLoading,
}: LoginLogStatsProps) {
  const { t } = useTranslation();

  const cards = [
    {
      label: t('admin.loginLogsTotal'),
      value: stats?.total ?? '-',
      icon: LucideClock,
    },
    {
      label: t('admin.loginLogsRecent7d'),
      value: stats?.recent_7d ?? '-',
      icon: LucideActivity,
    },
    {
      label: t('admin.loginLogsLastLogin'),
      value: stats?.last_login_time ? stats.last_login_time.split(' ')[0] : '-',
      icon: LucideCalendar,
    },
    {
      label: t('admin.loginLogsCommonDevice'),
      value: stats?.common_device ?? '-',
      icon: LucideMonitor,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3">
      {cards.map(({ label, value, icon: Icon }) => (
        <Card key={label}>
          <CardContent className="p-3 flex items-center gap-3">
            <Icon className="size-4 text-text-tertiary shrink-0" />
            <div className="min-w-0">
              <div className="text-xs text-text-tertiary">{label}</div>
              <div className="text-sm font-medium truncate">
                {isLoading ? '...' : String(value)}
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
