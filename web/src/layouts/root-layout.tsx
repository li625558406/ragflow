import PermissionGuard from '@/components/permission/permission-guard';
import {
  getRequiredPermission,
  isSuperuserOnlyPath,
} from '@/constants/permission';
import { usePermission } from '@/hooks/use-permission';
import { Navigate, Outlet, useLocation } from 'react-router';
import { Header } from './components/header';

export function RootLayoutContainer({ children }: React.PropsWithChildren) {
  return (
    <div className="size-full grid grid-rows-[auto_1fr] grid-cols-1 grid-flow-col">
      <Header className="px-5 py-4" />

      <main className="size-full overflow-hidden">{children}</main>
    </div>
  );
}

function RouteGuard() {
  const { pathname } = useLocation();
  const { isSuperuser, loading } = usePermission();
  // 仅超管模块（范本库/智能体/记忆/智能采集/用户管理）：非超管直达一律重定向首页
  if (isSuperuserOnlyPath(pathname)) {
    if (loading) return null;
    if (!isSuperuser) return <Navigate to="/" replace />;
    return <Outlet />;
  }
  const required = getRequiredPermission(pathname);
  if (!required) return <Outlet />;
  return (
    <PermissionGuard permission={required}>
      <Outlet />
    </PermissionGuard>
  );
}

export default function RootLayout() {
  return (
    <RootLayoutContainer>
      <RouteGuard />
    </RootLayoutContainer>
  );
}
