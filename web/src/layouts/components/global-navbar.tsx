import { useId, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';

import { LucideHouse, type LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { supportsCssAnchor } from '@/utils/css-support';
import { usePermission } from '@/hooks/use-permission';

const PathMap = {
  [Routes.Datasets]: [Routes.Datasets, Routes.DatasetBase],
  [Routes.Chats]: [Routes.Chats, Routes.Chat],
  [Routes.Searches]: [Routes.Searches, Routes.Search],
  [Routes.Agents]: [Routes.Agents, Routes.AgentTemplates],
  [Routes.Memories]: [Routes.Memories, Routes.Memory, Routes.MemoryMessage],
  [Routes.Files]: [Routes.Files],
  [Routes.Crawl4ai]: [Routes.Crawl4ai],
} as const;
const menuItems: Array<{
  path: string;
  name: string;
  permission?: string;
  icon?: LucideIcon;
  'data-testid'?: string;
}> = [
  { path: Routes.Root, name: 'header.Root', icon: LucideHouse, permission: 'bid' },
  { path: Routes.Datasets, name: 'header.dataset', permission: 'dataset' },
  {
    path: Routes.Chats,
    name: 'header.chat',
    'data-testid': 'nav-chat',
    permission: 'chat',
  },
  {
    path: Routes.Searches,
    name: 'header.search',
    'data-testid': 'nav-search',
    permission: 'search',
  },
  {
    path: Routes.Agents,
    name: 'header.flow',
    'data-testid': 'nav-agent',
    permission: 'agent',
  },
  { path: Routes.Memories, name: 'header.memories', permission: 'memory' },
  { path: Routes.Files, name: 'header.fileManager', permission: 'file' },
  { path: Routes.Crawl4ai, name: 'header.crawl4ai', permission: 'crawler' },
];

const GlobalNavbar = supportsCssAnchor
  ? () => {
      const { t } = useTranslation();
      const { hasPermission } = usePermission();
      const { pathname } = useLocation();
      const navbarAnchorNamePrefix = useId().replace(/:/g, '');

      const activePath = useMemo(() => {
        return (
          Object.keys(PathMap).find((x: string) =>
            PathMap[x as keyof typeof PathMap].some((y: string) =>
              pathname.includes(y),
            ),
          ) || pathname
        );
      }, [pathname]);

      const activePathAnchorName = `--${navbarAnchorNamePrefix}${activePath === Routes.Root ? '-root' : activePath.replace('/', '-')}`;

      const hasAnyActive = useMemo(
        () => menuItems.some(({ path }) => path === activePath),
        [activePath],
      );

      return (
        <nav>
          <ul className="relative flex items-center p-1 bg-bg-card rounded-full border border-border-button">
            {menuItems
              .filter((it) => !it.permission || hasPermission(it.permission))
              .map(({ path, name, icon: Icon, ...props }) => {
              const isActive = path === activePath;
              const anchorName = `--${navbarAnchorNamePrefix}${path === Routes.Root ? '-root' : path.replace('/', '-')}`;

              return (
                <li key={path} className="relative" style={{ anchorName }}>
                  <Link
                    {...props}
                    to={path}
                    className={cn(
                      'h-10 px-6 text-base inline-flex items-center justify-center',
                      'hover:text-current focus-visible:text-current rounded-full transition-all',
                      isActive && '!text-bg-base',
                    )}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {Icon && <Icon className="size-6 stroke-[1.5]" />}
                    <span className={cn(Icon && 'sr-only')}>{t(name)}</span>
                  </Link>
                </li>
              );
            })}

            <li
              className={cn(
                'absolute -z-[1] bg-text-primary border-b-2 border-b-accent-primary rounded-full opacity-0',
                'transition-all',
                hasAnyActive && 'opacity-100',
              )}
              role="presentation"
              style={{
                top: 'anchor(top)',
                left: 'anchor(left)',
                width: 'anchor-size(width)',
                height: 'anchor-size(height)',
                positionAnchor: activePathAnchorName,
              }}
            />
          </ul>
        </nav>
      );
    }
  : () => {
      const { t } = useTranslation();
      const { hasPermission } = usePermission();
      const { pathname } = useLocation();

      const activePath = useMemo(() => {
        return (
          Object.keys(PathMap).find((x: string) =>
            PathMap[x as keyof typeof PathMap].some((y: string) =>
              pathname.includes(y),
            ),
          ) || pathname
        );
      }, [pathname]);

      return (
        <nav>
          <ul className="flex items-center p-1 bg-bg-card rounded-full border border-border-button">
            {menuItems
              .filter((it) => !it.permission || hasPermission(it.permission))
              .map(({ path, name, icon: Icon, ...props }) => {
              const isActive = path === activePath;

              return (
                <li key={path}>
                  <Link
                    {...props}
                    to={path}
                    className={cn(
                      'h-10 px-6 text-base inline-flex items-center justify-center',
                      'hover:text-current focus-visible:text-current rounded-full transition-all',
                      isActive &&
                        '!text-bg-base bg-text-primary border-b-2 border-b-accent-primary',
                    )}
                    aria-label={t(name)}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {Icon ? (
                      <Icon className="size-6 stroke-[1.5]" />
                    ) : (
                      <span>{t(name)}</span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      );
    };

export default GlobalNavbar;
