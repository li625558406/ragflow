import React, { Suspense, lazy } from 'react';
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';

function TokenTransferPage() {
  return (
    <script
      dangerouslySetInnerHTML={{
        __html: `
          const token = localStorage.getItem('Authorization') || '';
          const userInfo = localStorage.getItem('userInfo') || '';
          const bareToken = localStorage.getItem('token') || '';
          window.parent.postMessage({
            type: 'ragflow-token',
            Authorization: token,
            token: bareToken,
            userInfo: userInfo,
          }, '*');
        `,
      }}
    />
  );
}

const fallback = (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[1px]">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-white/70 border-t-transparent" />
  </div>
);

const cRouteConfig: RouteObject[] = [
  {
    path: '/',
    Component: lazy(() => import('./pages/c-landing')),
  },
  {
    path: '/chat',
    Component: lazy(() => import('./pages/c-chat')),
  },
  {
    path: '/token-transfer.html',
    Component: TokenTransferPage as any,
  },
  {
    path: '*',
    element: <Navigate to="/" replace />,
  },
].map((route) => {
  if (route.Component && typeof route.Component === 'function') {
    const Original = route.Component as React.LazyExoticComponent<any>;
    const isLazy = (Original as any)._payload !== undefined;
    if (isLazy) {
      route.Component = function Wrapped(props: any) {
        return (
          <Suspense fallback={fallback}>
            <Original {...props} />
          </Suspense>
        );
      } as any;
    }
  }
  return route;
});

const cRouters = createBrowserRouter(cRouteConfig);

export { cRouters };
