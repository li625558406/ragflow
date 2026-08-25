import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import permissionService from '@/services/permission-service';
import type { ModulePermissionKey } from '@/constants/permission';

export interface PermissionState {
  permissions: string[];
  isSuperuser: boolean;
  loading: boolean;
}

export const useMyPermissions = (): PermissionState => {
  const { data, isLoading } = useQuery({
    queryKey: ['myPermissions'],
    staleTime: 5 * 60 * 1000,
    retry: 2,
    queryFn: async () => {
      const { data } = await permissionService.myPermissions();
      return data.data ?? { permissions: [], is_superuser: false };
    },
  });
  return {
    permissions: data?.permissions ?? [],
    isSuperuser: !!data?.is_superuser,
    loading: isLoading,
  };
};

export const usePermission = () => {
  const { permissions, isSuperuser, loading } = useMyPermissions();
  const permSet = useMemo(() => new Set(permissions), [permissions]);
  const hasPermission = useCallback(
    (key: ModulePermissionKey | string) => isSuperuser || permSet.has(key),
    [isSuperuser, permSet],
  );
  return { hasPermission, permissions, isSuperuser, loading };
};
