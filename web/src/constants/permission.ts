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
