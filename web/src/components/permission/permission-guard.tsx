import { usePermission } from '@/hooks/use-permission';
import { Suspense } from 'react';
import { Navigate } from 'react-router';

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
  // 懒加载路由 chunk 在权限 loading→loaded 同步更新后才首次渲染，
  // 没有 Suspense 边界会抛 "suspended while responding to synchronous input"（刷新报错、菜单进入正常）
  return <Suspense fallback={null}>{children}</Suspense>;
}
