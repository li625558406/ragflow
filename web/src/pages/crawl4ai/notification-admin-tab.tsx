import {
  adminDeleteNotification,
  adminListNotifications,
  adminStats,
  type AdminNotificationItem,
  type AdminNotificationStats,
} from '@/services/admin-notification-service';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

export function NotificationAdminTab() {
  const { t } = useTranslation();
  const [list, setList] = useState<AdminNotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page] = useState(1);
  const [stats, setStats] = useState<AdminNotificationStats | null>(null);
  const [filter, setFilter] = useState({ site_id: '', category: '' });
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const resp: any = await adminListNotifications({
        page,
        page_size: 50,
        ...filter,
      });
      // umi-request: resp = Response, resp.data = {code, data, message} body, resp.data.data = 真正 payload
      const payload = resp?.data?.data;
      setList(payload?.list || []);
      setTotal(payload?.total || 0);
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    const resp: any = await adminStats();
    setStats(resp?.data?.data ?? null);
  };

  useEffect(() => {
    load();
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filter]);

  return (
    <div className="flex flex-col gap-4 h-full overflow-auto pr-1">
      <div className="grid grid-cols-3 gap-4 shrink-0">
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">
            {t('notifications.admin.statsTodayCreated')}
          </div>
          <div className="text-2xl font-semibold">
            {stats?.today_created ?? 0}
          </div>
        </div>
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">
            {t('notifications.admin.statsWeekPushed')}
          </div>
          <div className="text-2xl font-semibold">
            {stats?.week_pushed ?? 0}
          </div>
        </div>
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">
            {t('notifications.admin.statsReadRate')}
          </div>
          <div className="text-2xl font-semibold">
            {(((stats?.read_rate ?? 0) as number) * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="flex gap-2 items-end shrink-0">
        <div>
          <label className="text-xs">
            {t('notifications.admin.filterSite')}
          </label>
          <input
            value={filter.site_id}
            onChange={(e) =>
              setFilter((f) => ({ ...f, site_id: e.target.value }))
            }
            className="border rounded px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="text-xs">
            {t('notifications.admin.filterCategory')}
          </label>
          <select
            value={filter.category}
            onChange={(e) =>
              setFilter((f) => ({ ...f, category: e.target.value }))
            }
            className="border rounded px-2 py-1 text-sm"
          >
            <option value="">{t('notifications.admin.categoryAll')}</option>
            <option value="bid">{t('notifications.admin.categoryBid')}</option>
            <option value="policy">
              {t('notifications.admin.categoryPolicy')}
            </option>
            <option value="news">
              {t('notifications.admin.categoryNews')}
            </option>
            <option value="personnel">
              {t('notifications.admin.categoryPersonnel')}
            </option>
            <option value="other">
              {t('notifications.admin.categoryOther')}
            </option>
          </select>
        </div>
        <button
          onClick={load}
          className="px-3 py-1 text-sm bg-blue-600 text-white rounded"
        >
          {t('notifications.admin.refresh')}
        </button>
      </div>

      <table className="w-full border text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="border p-2 text-left">
              {t('notifications.admin.colTime')}
            </th>
            <th className="border p-2 text-left">
              {t('notifications.admin.colSite')}
            </th>
            <th className="border p-2 text-left">
              {t('notifications.admin.colCategory')}
            </th>
            <th className="border p-2 text-left">
              {t('notifications.admin.colCount')}
            </th>
            <th className="border p-2 text-left">
              {t('notifications.admin.colPushedRead')}
            </th>
            <th className="border p-2 text-left">
              {t('notifications.admin.colActions')}
            </th>
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr>
              <td colSpan={6} className="border p-4 text-center text-gray-400">
                {t('notifications.admin.loading')}
              </td>
            </tr>
          )}
          {!loading &&
            list.map((n) => (
              <tr key={n.id}>
                <td className="border p-2">
                  {new Date(n.created_at).toLocaleString()}
                </td>
                <td className="border p-2">{n.site_display}</td>
                <td className="border p-2">{n.category}</td>
                <td className="border p-2">{n.result_count}</td>
                <td className="border p-2">
                  {n.pushed_count ?? 0} / {n.read_count ?? 0}
                </td>
                <td className="border p-2">
                  <button
                    onClick={async () => {
                      if (!confirm(t('notifications.admin.confirmDelete')))
                        return;
                      await adminDeleteNotification(n.id);
                      load();
                      loadStats();
                    }}
                    className="text-red-600 hover:underline"
                  >
                    {t('notifications.admin.delete')}
                  </button>
                </td>
              </tr>
            ))}
          {!loading && list.length === 0 && (
            <tr>
              <td colSpan={6} className="border p-4 text-center text-gray-400">
                {t('notifications.admin.noData')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {total > 50 && (
        <div className="text-xs text-gray-500 text-center">
          {t('notifications.admin.totalHint', { total })}
        </div>
      )}
    </div>
  );
}
