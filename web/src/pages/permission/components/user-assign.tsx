import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import permissionService from '@/services/permission-service';

export default function UserAssign() {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const { data, refetch } = useQuery({
    queryKey: ['permissionUsers'],
    queryFn: async () => {
      const { data } = await permissionService.listUsers();
      return data.data ?? { items: [] };
    },
  });
  const rolesQuery = useQuery({
    queryKey: ['permissionRolesForAssign'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const roles = rolesQuery.data?.items ?? [];

  const users = data?.items ?? [];

  const toggle = (userId: string, roleId: string) =>
    setSelected((prev) => {
      const cur = prev[userId] ?? [];
      const next = cur.includes(roleId) ? cur.filter((x) => x !== roleId) : [...cur, roleId];
      return { ...prev, [userId]: next };
    });

  const save = async (userId: string) => {
    await permissionService.setUserRoles(userId, { role_ids: selected[userId] ?? [] });
    refetch();
  };

  return (
    <div className="border p-4 rounded-lg space-y-3">
      <h3>{t('permission.assignTo')}</h3>
      {users.map((u: any) => (
        <div key={u.id} className="flex items-center justify-between">
          <span>
            {u.nickname || u.email}
            {u.is_superuser ? ' ⭐' : ''}
          </span>
          <div className="flex gap-2 items-center">
            {roles.map((r: any) => (
              <Button key={r.id} size="sm" variant="ghost" onClick={() => toggle(u.id, r.id)}>
                {r.name}
              </Button>
            ))}
            <Button size="sm" onClick={() => save(u.id)}>
              {t('permission.save')}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
