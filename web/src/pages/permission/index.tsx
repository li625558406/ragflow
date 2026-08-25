import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import permissionService from '@/services/permission-service';
import { MODULE_PERMISSIONS, type ModulePermissionKey } from '@/constants/permission';
import RoleForm from './components/role-form';
import UserAssign from './components/user-assign';

export default function PermissionManage() {
  const { t } = useTranslation();
  const { data, refetch } = useQuery({
    queryKey: ['permissionRoles'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const roles = data?.items ?? [];

  const handleDeleteRole = async (id: string) => {
    await permissionService.deleteRole(id);
    refetch();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('permission.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <RoleForm permissionKeys={MODULE_PERMISSIONS} onRefresh={refetch} />
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th>{t('permission.roleName')}</th>
              <th>{t('permission.permissionKeys')}</th>
              <th>{t('permission.description')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {roles.map((r: any) => (
              <tr key={r.id} className="border-t">
                <td>{r.name}{r.builtin ? `（${t('permission.roles')}）` : ''}</td>
                <td>
                  {(r.permissions ?? []).map((k: string) => (
                    <span key={k} className="mr-1 inline-block rounded bg-bg-card px-2 py-1 text-xs">
                      {MODULE_PERMISSIONS[k as ModulePermissionKey] ?? k}
                    </span>
                  ))}
                </td>
                <td>{r.description}</td>
                <td>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={r.builtin}
                    onClick={() => handleDeleteRole(r.id)}
                  >
                    {t('permission.deleteRole')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <UserAssign />
      </CardContent>
    </Card>
  );
}
