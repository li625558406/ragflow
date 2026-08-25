import api from '@/utils/api';
import request from '@/utils/next-request';
import { registerNextServer } from '@/utils/register-server';

const {
  permissionMe,
  permissionRoles,
  permissionRole,
  permissionRolePermissions,
  permissionUsers,
  permissionUserRoles,
} = api;

// registerNextServer 仅能处理「函数式 URL 与请求体不冲突」的方法：
// 其内部用同一个 config 同时解析 url(config) 和作为 data: config 发送，
// 无法把 URL 用的 id 与 JSON body 拆开。故函数式 URL 且有 body / 不希望带 body 的方法用 request 直接封装。
const methods = {
  myPermissions: { url: permissionMe, method: 'get' },
  listRoles: { url: permissionRoles, method: 'get' },
  createRole: { url: permissionRoles, method: 'post' },
  listUsers: { url: permissionUsers, method: 'get' },
} as const;

const permissionService = registerNextServer<keyof typeof methods>(methods);

// 函数式 URL + 请求体（或纯函数式 URL、不希望带 body 的 DELETE），挂到 default 导出上，
// 使调用保持 permissionService.setRolePermissions(...) / permissionService.deleteRole(...) 的形式。
(permissionService as any).updateRole = (id: string, data: any) =>
  request.put(permissionRole(id), { ...data });
(permissionService as any).setRolePermissions = (id: string, data: any) =>
  request.put(permissionRolePermissions(id), { ...data });
(permissionService as any).setUserRoles = (userId: string, data: any) =>
  request.put(permissionUserRoles(userId), { ...data });
(permissionService as any).deleteRole = (id: string) =>
  request.delete(permissionRole(id));

export default permissionService;
