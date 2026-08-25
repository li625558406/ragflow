import { Navigate } from 'react-router';
import { usePermission } from '@/hooks/use-permission';

export default function PermissionGuard({
  permission,
  children,
}: {
  permission: string;
  children: React.ReactNode;
}) {
  const { hasPermission, loading } = usePermission();
  if (loading) return null;
  if (!hasPermission(permission)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
