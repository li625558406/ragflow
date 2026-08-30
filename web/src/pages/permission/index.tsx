import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  MODULE_PERMISSIONS,
  type ModulePermissionKey,
} from '@/constants/permission';
import { cn } from '@/lib/utils';
import permissionService from '@/services/permission-service';
import { useQuery } from '@tanstack/react-query';
import {
  LucideCrown,
  LucideMoreVertical,
  LucidePencil,
  LucideSearch,
  LucideShieldCheck,
  LucideTrash2,
  LucideUserCheck,
  LucideUserPlus,
  LucideUsers,
  type LucideIcon,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import RoleForm from './components/role-form';
import UserAssign from './components/user-assign';
import type { PermissionRole, PermissionUser } from './types';

const KpiStat = ({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: number;
  tone?: string;
}) => (
  <div className="flex items-center gap-3 rounded-md border border-border-button bg-bg-card p-4">
    <div
      className={cn(
        'flex size-10 shrink-0 items-center justify-center rounded-md bg-bg-card text-text-primary',
        tone,
      )}
    >
      <Icon className="size-5" />
    </div>
    <div className="min-w-0">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="truncate text-xs text-text-secondary">{label}</div>
    </div>
  </div>
);

export default function PermissionManage() {
  const { t } = useTranslation();
  const [roleSearch, setRoleSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<PermissionRole | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PermissionRole | null>(null);

  const rolesQuery = useQuery({
    queryKey: ['permissionRoles'],
    queryFn: async () => {
      const { data } = await permissionService.listRoles();
      return data.data ?? { items: [] };
    },
  });
  const usersQuery = useQuery({
    queryKey: ['permissionUsers'],
    queryFn: async () => {
      const { data } = await permissionService.listUsers();
      return data.data ?? { items: [] };
    },
  });
  const refetchRoles = rolesQuery.refetch;
  const refetchUsers = usersQuery.refetch;

  const roles = useMemo(
    () => (rolesQuery.data?.items ?? []) as PermissionRole[],
    [rolesQuery.data],
  );
  const users = useMemo(
    () => (usersQuery.data?.items ?? []) as PermissionUser[],
    [usersQuery.data],
  );

  const filteredRoles = useMemo(() => {
    const q = roleSearch.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter((r) => r.name.toLowerCase().includes(q));
  }, [roles, roleSearch]);

  const kpis = useMemo(
    () => ({
      roleCount: roles.length,
      userCount: users.length,
      superuserCount: users.filter((u) => u.is_superuser).length,
      assignedUserCount: users.filter((u) => (u.roles ?? []).length > 0).length,
    }),
    [roles, users],
  );

  // 每个角色名 → 挂该角色的用户数（角色表「用户数」列）
  const userCountByRoleName = useMemo(() => {
    const map: Record<string, number> = {};
    users.forEach((u) => {
      (u.roles ?? []).forEach((rn) => {
        map[rn] = (map[rn] ?? 0) + 1;
      });
    });
    return map;
  }, [users]);

  const openCreate = () => {
    setEditingRole(null);
    setDialogOpen(true);
  };
  const openEdit = (r: PermissionRole) => {
    setEditingRole(r);
    setDialogOpen(true);
  };

  const handleDelete = async (id: string) => {
    await (permissionService as any).deleteRole(id);
    setDeleteTarget(null);
    refetchRoles();
  };

  return (
    <div className="size-full overflow-y-auto px-5 py-5">
      <div className="space-y-6">
        {/* KPI 概览条 */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiStat
            icon={LucideShieldCheck}
            label={t('permission.roleCount')}
            value={kpis.roleCount}
            tone="bg-accent-primary/10 text-accent-primary"
          />
          <KpiStat
            icon={LucideUsers}
            label={t('permission.userCount')}
            value={kpis.userCount}
          />
          <KpiStat
            icon={LucideCrown}
            label={t('permission.superuserCount')}
            value={kpis.superuserCount}
            tone="bg-state-warning/10 text-state-warning"
          />
          <KpiStat
            icon={LucideUserCheck}
            label={t('permission.assignedUserCount')}
            value={kpis.assignedUserCount}
            tone="bg-state-success/10 text-state-success"
          />
        </div>

        {/* Card 1: 角色与权限管理 */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div className="space-y-1">
              <CardTitle>{t('permission.roleManage')}</CardTitle>
              <CardDescription>{t('permission.roleSubtitle')}</CardDescription>
            </div>
            <Button onClick={openCreate}>
              <LucideUserPlus className="size-4" />
              {t('permission.createRole')}
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between">
              <Input
                placeholder={t('permission.searchRole')}
                value={roleSearch}
                onChange={(e) => setRoleSearch(e.target.value)}
                prefix={<LucideSearch className="ms-2 me-1 size-[1em]" />}
                className="max-w-xs"
              />
              <span className="text-sm text-text-secondary">
                {t('permission.resultCount', { count: filteredRoles.length })}
              </span>
            </div>

            <div className="rounded-md border border-border-button">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('permission.roleName')}</TableHead>
                    <TableHead>{t('permission.permissionKeys')}</TableHead>
                    <TableHead className="w-24">
                      {t('permission.userCount')}
                    </TableHead>
                    <TableHead>{t('permission.description')}</TableHead>
                    <TableHead className="w-16" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredRoles.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-text-primary">
                            {r.name}
                          </span>
                          {r.builtin && (
                            <Badge variant="secondary">
                              {t('permission.builtin')}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        {r.permissions.length ? (
                          <div className="flex flex-wrap gap-1">
                            {r.permissions.map((k) => (
                              <Badge key={k} variant="outline">
                                {MODULE_PERMISSIONS[k as ModulePermissionKey] ??
                                  k}
                              </Badge>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-text-disabled">—</span>
                        )}
                      </TableCell>
                      <TableCell className="tabular-nums text-text-secondary">
                        {userCountByRoleName[r.name] ?? 0}
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate text-text-secondary">
                        {r.description || '—'}
                      </TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="border-0 text-text-secondary hover:text-text-primary"
                              aria-label={t('permission.actions')}
                            >
                              <LucideMoreVertical className="size-5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-40">
                            <DropdownMenuItem
                              className="gap-2 cursor-pointer"
                              onClick={() => openEdit(r)}
                            >
                              <LucidePencil className="size-4" />
                              <span>{t('permission.edit')}</span>
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className={cn(
                                'gap-2 cursor-pointer',
                                !r.builtin && 'text-state-error',
                              )}
                              disabled={r.builtin}
                              onClick={() => setDeleteTarget(r)}
                            >
                              <LucideTrash2 className="size-4" />
                              <span>{t('permission.deleteRole')}</span>
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                  {filteredRoles.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="h-24 text-center text-sm text-text-secondary"
                      >
                        {t('permission.empty')}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* Card 2: 用户角色分配 */}
        <Card>
          <CardHeader>
            <CardTitle>{t('permission.roleAssignTitle')}</CardTitle>
            <CardDescription>
              {t('permission.roleAssignSubtitle')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <UserAssign onSaved={refetchUsers} />
          </CardContent>
        </Card>

        <RoleForm
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editing={editingRole}
          roles={roles}
          onSaved={refetchRoles}
        />

        {/* 删除确认 */}
        <Dialog
          open={!!deleteTarget}
          onOpenChange={(o) => !o && setDeleteTarget(null)}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t('permission.deleteRole')}</DialogTitle>
              <DialogDescription>
                {t('permission.confirmDelete')}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
                {t('permission.cancel')}
              </Button>
              <Button
                variant="destructive"
                onClick={() => deleteTarget && handleDelete(deleteTarget.id)}
              >
                {t('permission.delete')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
