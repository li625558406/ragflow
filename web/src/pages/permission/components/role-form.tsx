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
import { MODULE_PERMISSIONS } from '@/constants/permission';
import permissionService from '@/services/permission-service';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PermissionRole } from '../types';

interface RoleFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing?: PermissionRole | null;
  roles: PermissionRole[];
  onSaved: () => void;
}

const RoleForm = ({
  open,
  onOpenChange,
  editing,
  roles,
  onSaved,
}: RoleFormProps) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [keys, setKeys] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  // 打开时按 editing 初始化表单，避免跨行残留
  useEffect(() => {
    if (open) {
      setName(editing?.name ?? '');
      setDescription(editing?.description ?? '');
      setKeys(editing?.permissions ?? []);
    }
  }, [open, editing]);

  const toggle = (k: string) =>
    setKeys((prev) =>
      prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k],
    );

  // 必填 + 重名校验（编辑时排除自身）
  const nameError = useMemo(() => {
    const trimmed = name.trim();
    if (!trimmed) return t('permission.roleNameRequired');
    if (roles.some((r) => r.name === trimmed && r.id !== editing?.id)) {
      return t('permission.duplicateRoleName');
    }
    return '';
  }, [name, roles, editing?.id, t]);

  const submit = async () => {
    if (nameError || !name.trim()) return;
    setSaving(true);
    try {
      if (editing) {
        await (permissionService as any).updateRole(editing.id, {
          name: name.trim(),
          description,
        });
        await (permissionService as any).setRolePermissions(editing.id, {
          permission_keys: keys,
        });
      } else {
        const { data } = await permissionService.createRole({
          name: name.trim(),
          description,
        });
        if (!data || data.code !== 0 || !data.data?.id) return;
        await (permissionService as any).setRolePermissions(data.data.id, {
          permission_keys: keys,
        });
      }
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {editing ? t('permission.editRole') : t('permission.createRole')}
          </DialogTitle>
          <DialogDescription>{t('permission.roleSubtitle')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Input
              placeholder={t('permission.roleName')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-invalid={!!nameError}
            />
            {nameError && (
              <p className="text-xs text-state-error">{nameError}</p>
            )}
          </div>

          <Input
            placeholder={t('permission.description')}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          <div className="space-y-2">
            <p className="text-sm font-medium">
              {t('permission.permissionKeys')}
            </p>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(MODULE_PERMISSIONS).map(([k, label]) => (
                <label key={k} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={keys.includes(k)}
                    onCheckedChange={() => toggle(k)}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('permission.cancel')}
          </Button>
          <Button onClick={submit} disabled={!!nameError || saving}>
            {t('permission.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RoleForm;
