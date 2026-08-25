export enum PermissionRole {
  Me = 'me',
  Team = 'team',
}

// ── 模块级权限点（与后端 api/constants.py 的 MODULE_PERMISSIONS 一一对应）──
export type ModulePermissionKey =
  | 'bid'
  | 'dataset'
  | 'chat'
  | 'search'
  | 'agent'
  | 'memory'
  | 'file'
  | 'crawler'
  | 'user_setting'
  | 'home'
  | 'c_chat'
  | 'permission_manage';

export const MODULE_PERMISSIONS: Record<ModulePermissionKey, string> = {
  bid: '标讯管理',
  dataset: '知识库',
  chat: '对话',
  search: '搜索',
  agent: 'Agent 画布/流程',
  memory: '记忆',
  file: '文件',
  crawler: '智能采集',
  user_setting: '用户设置',
  home: 'C 端着陆页',
  c_chat: '投标助手对话',
  permission_manage: '权限管理',
};

// ── 前端路径前缀 → 模块权限点映射（路由守卫用；最长匹配，仅匹配完整路径段）──
export const MODULE_PATH_PERMISSION: Array<[string, string]> = [
  ['/datasets', 'dataset'],
  ['/dataset', 'dataset'],
  ['/chats', 'chat'],
  ['/chat', 'chat'],
  ['/searches', 'search'],
  ['/search', 'search'],
  ['/agents', 'agent'],
  ['/agent', 'agent'],
  ['/memories', 'memory'],
  ['/memory', 'memory'],
  ['/files', 'file'],
  ['/smart-crawler', 'crawler'],
  ['/permission', 'permission_manage'],
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
