import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
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
    queryKey: ['permissionRoles'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const roles = rolesQuery.data?.items ?? [];
  const users = data?.items ?? [];

  // 后端返回的用户已有角色是「角色名」数组，这里映射成角色 id 以初始化选中态，
  // 避免管理员对已有角色的用户点一次“保存”就把其全部角色清空（初始 selected 为空数组会触发 filter_delete 全部删除）。
  // 注意：deps 必须用稳定的 query 结果（data / rolesQuery.data）而非 users/roles 数组字面量，
  // 否则加载中或加载失败时 ?? [] 每次渲染都是新数组，effect 每轮都重跑并 setSelected，导致无限重渲染循环。
  useEffect(() => {
    const users = data?.items ?? [];
    const roles = rolesQuery.data?.items ?? [];
    const roleIdByRoleName = roles.reduce(
      (acc: Record<string, string>, r: any) => {
        acc[r.name] = r.id;
        return acc;
      },
      {},
    );
    const init: Record<string, string[]> = {};
    users.forEach((u: any) => {
      init[u.id] = (u.roles ?? [])
        .map((rn: string) => roleIdByRoleName[rn])
        .filter(Boolean) as string[];
    });
    setSelected(init);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, rolesQuery.data]);

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
              <Button
                key={r.id}
                size="sm"
                variant={selected[u.id]?.includes(r.id) ? 'default' : 'ghost'}
                onClick={() => toggle(u.id, r.id)}
              >
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
