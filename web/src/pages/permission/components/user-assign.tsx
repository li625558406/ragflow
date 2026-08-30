import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useMyPermissions } from '@/hooks/use-permission';
import permissionService from '@/services/permission-service';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PermissionRole, PermissionUser } from '../types';

interface UserAssignProps {
  onSaved: () => void;
}

const UserAssign = ({ onSaved }: UserAssignProps) => {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [deleteTarget, setDeleteTarget] = useState<PermissionUser | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { isSuperuser } = useMyPermissions();

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

  const roles = useMemo(
    () => (rolesQuery.data?.items ?? []) as PermissionRole[],
    [rolesQuery.data],
  );
  const users = useMemo(() => (data?.items ?? []) as PermissionUser[], [data]);

  // 安全初始化：后端用户已挂角色是「角色名」数组，这里映射成角色 id 以初始化选中态，
  // 避免管理员对已有角色的用户点一次“保存”就把其全部角色清空。
  // deps 必须用稳定的 query 结果 data/rolesQuery.data（而非 users/roles 数组字面量），
  // 否则 ?? [] 每次渲染都是新数组，effect 每轮重跑导致无限重渲染。
  useEffect(() => {
    const u = (data?.items ?? []) as PermissionUser[];
    const r = (rolesQuery.data?.items ?? []) as PermissionRole[];
    const ridByName = r.reduce((acc: Record<string, string>, x) => {
      acc[x.name] = x.id;
      return acc;
    }, {});
    const init: Record<string, string[]> = {};
    u.forEach((item) => {
      init[item.id] = (item.roles ?? [])
        .map((rn) => ridByName[rn])
        .filter(Boolean) as string[];
    });
    setSelected(init);
    setDirty({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, rolesQuery.data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        (u.email ?? '').toLowerCase().includes(q) ||
        (u.nickname ?? '').toLowerCase().includes(q),
    );
  }, [users, search]);

  const toggle = (userId: string, roleId: string) => {
    setSelected((prev) => {
      const cur = prev[userId] ?? [];
      const next = cur.includes(roleId)
        ? cur.filter((x) => x !== roleId)
        : [...cur, roleId];
      return { ...prev, [userId]: next };
    });
    setDirty((prev) => ({ ...prev, [userId]: true }));
  };

  const save = async (userId: string) => {
    await (permissionService as any).setUserRoles(userId, {
      role_ids: selected[userId] ?? [],
    });
    setDirty((prev) => ({ ...prev, [userId]: false }));
    onSaved();
    refetch();
  };

  // 软删除用户（仅超管可用；失败时 next-request 拦截器自动 toast，无需在此提示）
  const handleDelete = async (userId: string) => {
    setDeleting(true);
    try {
      await (permissionService as any).deleteUser(userId);
      setDeleteTarget(null);
      onSaved();
      refetch();
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Input
          placeholder={t('permission.searchUser')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          prefix={<Search className="ms-2 me-1 size-[1em]" />}
          className="max-w-xs"
        />
        <span className="text-sm text-text-secondary">
          {t('permission.resultCount', { count: filtered.length })}
        </span>
      </div>

      <div className="rounded-md border border-border-button">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('permission.user')}</TableHead>
              <TableHead>{t('permission.currentRoles')}</TableHead>
              <TableHead>{t('permission.assignRole')}</TableHead>
              <TableHead className="w-24" />
              <TableHead className="w-20 text-right">
                {t('permission.actions')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((u) => {
              const curIds = selected[u.id] ?? [];
              const curRoleNames = roles
                .filter((r) => curIds.includes(r.id))
                .map((r) => r.name);
              return (
                <TableRow key={u.id}>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <RAGFlowAvatar name={u.nickname || u.email} isPerson />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-sm font-medium text-text-primary">
                            {u.email}
                          </span>
                          {u.is_superuser && (
                            <Badge variant="secondary" className="shrink-0">
                              {t('permission.superuser')}
                            </Badge>
                          )}
                        </div>
                        {u.nickname && (
                          <div className="truncate text-xs text-text-secondary">
                            {u.nickname}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    {curRoleNames.length ? (
                      <div className="flex flex-wrap gap-1">
                        {curRoleNames.map((name) => (
                          <Badge key={name} variant="secondary">
                            {name}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-text-disabled">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Popover>
                      <PopoverTrigger asChild>
                        <Button variant="outline" size="sm" className="gap-1.5">
                          {t('permission.assignRole')}
                          {curIds.length > 0 && (
                            <span className="inline-flex items-center justify-center rounded-full bg-accent-primary px-1.5 text-xs text-text-primary">
                              {curIds.length}
                            </span>
                          )}
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent align="end" className="w-56">
                        <div className="space-y-2">
                          <p className="text-sm font-medium">
                            {t('permission.assignRole')}
                          </p>
                          <div className="grid gap-1.5">
                            {roles.map((r) => (
                              <label
                                key={r.id}
                                className="flex cursor-pointer items-center gap-2 text-sm"
                              >
                                <Checkbox
                                  checked={curIds.includes(r.id)}
                                  onCheckedChange={() => toggle(u.id, r.id)}
                                />
                                <span>{r.name}</span>
                              </label>
                            ))}
                          </div>
                          {roles.length === 0 && (
                            <p className="text-xs text-text-secondary">
                              {t('permission.noRoles')}
                            </p>
                          )}
                        </div>
                      </PopoverContent>
                    </Popover>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      onClick={() => save(u.id)}
                      disabled={!dirty[u.id]}
                    >
                      {t('permission.save')}
                    </Button>
                  </TableCell>
                  <TableCell className="text-right">
                    {/* 仅超管可删普通用户；超管行不显示删除入口 */}
                    {isSuperuser && !u.is_superuser && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-state-error hover:text-state-error"
                        onClick={() => setDeleteTarget(u)}
                      >
                        {t('permission.delete')}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
            {filtered.length === 0 && (
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

      {/* 删除用户确认（软删除，被删用户将无法登录） */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('permission.deleteUser')}</DialogTitle>
            <DialogDescription>
              {t('permission.confirmDeleteUser')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
              {t('permission.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteTarget && handleDelete(deleteTarget.id)}
              disabled={deleting}
            >
              {t('permission.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default UserAssign;
