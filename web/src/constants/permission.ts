export enum PermissionRole {
  Me = 'me',
  Team = 'team',
}

// ── 模块级权限点（与后端 api/constants.py 的 MODULE_PERMISSIONS 对应）──
// 注：范本库/智能体/记忆/智能采集/用户管理 5 模块为「仅超管」硬限制（is_superuser 判定，
// 不走角色勾选），不在此列表；见 SUPERUSER_ONLY_PREFIXES。
export type ModulePermissionKey =
  | 'bid'
  | 'dataset'
  | 'chat'
  | 'search'
  | 'file'
  | 'user_setting'
  | 'home'
  | 'c_chat'
  | 'hr_manage'
  | 'hr_finance';

export const MODULE_PERMISSIONS: Record<ModulePermissionKey, string> = {
  bid: '标讯管理',
  dataset: '知识库',
  chat: '对话',
  search: '搜索',
  file: '文件',
  user_setting: '用户设置',
  home: 'C 端着陆页',
  c_chat: '投标助手对话',
  hr_manage: '人事管理',
  hr_finance: '薪资财务',
};

// ── 仅超管可见的路径前缀（页面+路由硬限制；最长匹配，仅匹配完整路径段）──
export const SUPERUSER_ONLY_PREFIXES = [
  '/template-fill',
  '/agents',
  '/agent-templates',
  '/agent',
  '/agent-list',
  '/agent-log-page',
  '/agent/share',
  '/memories',
  '/memory',
  '/memory-message',
  '/memory-setting',
  '/smart-crawler',
  '/permission',
];

export const isSuperuserOnlyPath = (pathname: string): boolean =>
  SUPERUSER_ONLY_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );

// ── 前端路径前缀 → 模块权限点映射（路由守卫用；最长匹配，仅匹配完整路径段）──
export const MODULE_PATH_PERMISSION: Array<[string, string]> = [
  ['/datasets', 'dataset'],
  ['/dataset', 'dataset'],
  ['/chats', 'chat'],
  ['/chat', 'chat'],
  ['/searches', 'search'],
  ['/search', 'search'],
  ['/files', 'file'],
  ['/user-setting', 'user_setting'],
];

// 取当前路径所需的模块权限点（未匹配返回 undefined）。用完整路径段边界判断，
// 使 /datasets 不会误匹配 /dataset/... ；取最长前缀。
export const getRequiredPermission = (pathname: string): string | undefined => {
  const hit = MODULE_PATH_PERMISSION.filter(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  ).sort((a, b) => b[0].length - a[0].length)[0];
  return hit?.[1];
};
