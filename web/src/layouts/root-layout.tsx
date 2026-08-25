import { Outlet, useLocation } from 'react-router';
import { Header } from './components/header';
import PermissionGuard from '@/components/permission/permission-guard';
import { getRequiredPermission } from '@/constants/permission';

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
