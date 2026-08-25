# api/db/services/permission_service.py
from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole
from api.db.services.common_service import CommonService
from api.constants.permission import NORMAL_ROLE_NAME


class PermissionRoleService(CommonService):
    model = PermissionRole


class PermissionRolePermissionService(CommonService):
    model = PermissionRolePermission


class PermissionUserRoleService(CommonService):
    model = PermissionUserRole


def get_user_role_ids(user_id: str) -> list[str]:
    rows = PermissionUserRole.select(PermissionUserRole.role_id).where(
        PermissionUserRole.user_id == user_id
    )
    return [r.role_id for r in rows]


def roles_permission_keys(role_ids: list[str]) -> set[str]:
    if not role_ids:
        return set()
    rows = PermissionRolePermission.select(PermissionRolePermission.permission_key).where(
        PermissionRolePermission.role_id.in_(role_ids)
    )
    return {r.permission_key for r in rows}


def get_normal_role_ids() -> list[str]:
    role = PermissionRole.get_or_none(PermissionRole.name == NORMAL_ROLE_NAME)
    return [role.id] if role else []


def get_user_permission_keys(user_id: str) -> set[str]:
    """用户权限点并集；未挂角色时回退到内置「普通用户」。"""
    role_ids = get_user_role_ids(user_id)
    keys = roles_permission_keys(role_ids)
    if not keys:
        role_ids = get_normal_role_ids()
        keys = roles_permission_keys(role_ids)
    return keys


def get_users_with_roles() -> list[dict]:
    """用户列表 + 已挂角色名，供权限管理页使用。"""
    from api.db.db_models import User
    users = User.select(User.id, User.email, User.nickname, User.is_superuser).order_by(User.create_time)
    role_map = {}
    rows = PermissionUserRole.select(
        PermissionUserRole.user_id, PermissionRole.name
    ).join(PermissionRole, on=(PermissionRole.id == PermissionUserRole.role_id))
    for r in rows:
        role_map.setdefault(r.user_id, []).append(r.name)
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "email": u.email,
            "nickname": u.nickname,
            "is_superuser": bool(u.is_superuser),
            "roles": role_map.get(u.id, []),
        })
    return result
