# api/db/services/permission_service.py
import logging

from peewee import IntegrityError

from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole
from api.db.services.common_service import CommonService
from api.constants import NORMAL_ROLE_NAME


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
    if not role_ids:
        return roles_permission_keys(get_normal_role_ids())
    return roles_permission_keys(role_ids)


def assign_normal_role(user_id: str) -> bool:
    """给用户挂内置「普通用户」角色（幂等；注册新用户/存量回填共用）。

    Returns:
        True 表示已挂上（含本来就有）；False 表示普通用户角色不存在。
    并发冲突（联合唯一 user_id+role_id）视为已挂上。
    """
    role = PermissionRole.get_or_none(PermissionRole.name == NORMAL_ROLE_NAME)
    if not role:
        logging.warning("assign_normal_role: 内置角色【%s】不存在，跳过", NORMAL_ROLE_NAME)
        return False
    if PermissionUserRole.get_or_none(user_id=user_id, role_id=role.id):
        return True
    try:
        from common.misc_utils import get_uuid  # 延迟导入，避免 db_models 循环导入
        PermissionUserRole.create(
            id=get_uuid(), user_id=user_id, role_id=role.id
        )
        return True
    except IntegrityError:
        # 并发下已被挂上，视为成功
        return True


def get_users_with_roles() -> list[dict]:
    """用户列表 + 已挂角色名，供权限管理页使用。

    注意：不能用跨表 select（PermissionUserRole.select(user_id, PermissionRole.name).join(...)），
    因为 join 模型的字段会挂到 r.permissionrole 子实例而非 r 本身，
    直接取 r.name 会报 AttributeError（'PermissionUserRole' object has no attribute 'name'）。
    这里改为两段查询：全量 user_role 行 + id→角色名映射。
    """
    from api.db.db_models import User
    # 过滤已软删除（status="0"）的用户，避免出现在用户管理列表中
    users = User.select(User.id, User.email, User.nickname, User.is_superuser).where(
        User.status == "1"
    ).order_by(User.create_time)
    user_role_rows = list(
        PermissionUserRole.select(PermissionUserRole.user_id, PermissionUserRole.role_id)
    )  # 物化：SelectQuery 惰性执行，重复迭代会重复跑查询
    role_ids = {r.role_id for r in user_role_rows}
    id_to_name = {}
    if role_ids:
        id_to_name = {
            r.id: r.name
            for r in PermissionRole.select(PermissionRole.id, PermissionRole.name).where(
                PermissionRole.id.in_(role_ids)
            )
        }
    role_map: dict[str, list[str]] = {}
    for r in user_role_rows:
        name = id_to_name.get(r.role_id)
        if name:
            role_map.setdefault(r.user_id, []).append(name)
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
