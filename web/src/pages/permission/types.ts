// 权限管理页（/permission）共用类型，与后端 permission_app.py 返回结构对应。

export interface PermissionRole {
  id: string;
  name: string;
  description: string;
  /** 模块权限点 key 数组（如 ['bid', 'dataset']） */
  permissions: string[];
  /** 是否内置角色（内置角色不可删除） */
  builtin: boolean;
}

export interface PermissionUser {
  id: string;
  email: string;
  nickname: string;
  is_superuser: boolean;
  /** 已挂角色名数组（后端返回角色名，非 id） */
  roles: string[];
}
