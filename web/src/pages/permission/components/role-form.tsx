import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import permissionService from '@/services/permission-service';
import type { ModulePermissionKey } from '@/constants/permission';

export default function RoleForm({
  permissionKeys,
  onRefresh,
}: {
  permissionKeys: Record<ModulePermissionKey, string>;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [keys, setKeys] = useState<string[]>([]);

  const toggle = (k: string) =>
    setKeys((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  const submit = async () => {
    if (!name.trim()) return;
    const { data } = await permissionService.createRole({ name, description });
    if (!data || data.code !== 0 || !data.data?.id) return;
    await permissionService.setRolePermissions(data.data.id, { permission_keys: keys });
    setName(''); setDescription(''); setKeys([]);
    onRefresh();
  };

  return (
    <div className="space-y-3 border p-4 rounded-lg">
      <Input
        placeholder={t('permission.roleName')}
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <Input
        placeholder={t('permission.description')}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <div className="grid grid-cols-3 gap-2">
        {(Object.entries(permissionKeys) as [string, string][]).map(([k, label]) => (
          <label key={k} className="flex items-center gap-1 text-sm">
            <Checkbox checked={keys.includes(k)} onCheckedChange={() => toggle(k)} />
            {label}
          </label>
        ))}
      </div>
      <Button onClick={submit}>{t('permission.createRole')}</Button>
    </div>
  );
}
