# api/apps/restful_apis/permission_app.py
import logging
from datetime import datetime

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.utils.api_utils import get_json_result, get_data_error_result
from api.utils.permission_utils import (
    permission_required,
    get_cached_user_permissions,
    invalidate_user_permissions,
)
from api.db.db_models import (
    DB,
    User,
    UserToken,
    PermissionRole,
    PermissionRolePermission,
    PermissionUserRole,
)
from api.db.services.permission_service import (
    PermissionRoleService,
    PermissionUserRoleService,
    get_users_with_roles,
)
from api.db.services.user_service import UserService
from common.constants import RetCode
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format

manager = Blueprint("rest_permission_app", __name__)


async def _json():
    return await request.get_json(force=True)


@manager.route("/permission/me", methods=["GET"])
@login_required
async def get_my_permissions():
    try:
        keys = get_cached_user_permissions(current_user.id)
        return get_json_result(data={
            "permissions": sorted(keys),
            "is_superuser": bool(current_user.is_superuser),
        })
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles", methods=["GET"])
@login_required
@permission_required("permission_manage")
async def list_roles():
    try:
        # 注意：CommonService.get_all 仅在 reverse 非 None 时才应用 order_by，故须显式传 reverse=False
        roles = PermissionRoleService.get_all(order_by="create_time", reverse=False)
        items = []
        for r in roles:
            perms = PermissionRolePermission.select().where(
                PermissionRolePermission.role_id == r.id
            )
            items.append({
                "id": r.id,
                "name": r.name,
                "description": r.description or "",
                "builtin": r.builtin,
                "permissions": [p.permission_key for p in perms],
            })
        return get_json_result(data={"items": items})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles", methods=["POST"])
@login_required
@permission_required("permission_manage")
async def create_role():
    try:
        body = await _json()
        name = (body.get("name") or "").strip()
        if not name:
            return get_data_error_result(message="角色名不能为空", code=RetCode.ARGUMENT_ERROR)
        if PermissionRoleService.get_or_none(name=name):
            return get_data_error_result(message="角色名已存在", code=RetCode.ARGUMENT_ERROR)
        # 注意：CommonService.insert 返回的是 save() 的 rows（int），非模型实例，
        # 故这里显式构造 id，不能依赖 insert 返回值的 .id
        role_id = get_uuid()
        PermissionRoleService.insert(id=role_id, name=name, description=body.get("description", ""), builtin=False)
        return get_json_result(data={"id": role_id})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def update_role(role_id):
    try:
        body = await _json()
        updates = {}
        if "name" in body:
            updates["name"] = (body["name"] or "").strip()
        if "description" in body:
            updates["description"] = body["description"]
        if updates:
            PermissionRoleService.update_by_id(role_id, updates)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>", methods=["DELETE"])
@login_required
@permission_required("permission_manage")
async def delete_role(role_id):
    try:
        role = PermissionRoleService.get_or_none(id=role_id)
        if not role:
            return get_data_error_result(message="角色不存在", code=RetCode.DATA_ERROR)
        if role.builtin:
            return get_data_error_result(message="内置角色不可删除", code=RetCode.FORBIDDEN)
        related_users = PermissionUserRoleService.query(role_id=role_id)
        # 事务内不能调 CommonService 的 insert/filter_delete（它们带 @DB.connection_context，
        # 退出时关连接会撞上未提交事务，报 "Attempting to close database while transaction is open"），
        # 必须用 peewee 模型直操作。
        with DB.atomic():
            PermissionUserRole.delete().where(PermissionUserRole.role_id == role_id).execute()
            PermissionRolePermission.delete().where(PermissionRolePermission.role_id == role_id).execute()
            PermissionRole.delete().where(PermissionRole.id == role_id).execute()
        for u in related_users:
            invalidate_user_permissions(u.user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/roles/<role_id>/permissions", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def set_role_permissions(role_id):
    try:
        body = await _json()
        # API 边界去重（保序），避免前端异常输入触发联合唯一约束冲突
        keys = list(dict.fromkeys(body.get("permission_keys") or []))
        role = PermissionRoleService.get_or_none(id=role_id)
        if not role:
            return get_data_error_result(message="角色不存在", code=RetCode.DATA_ERROR)
        # 同 delete_role：事务内直接用 peewee 模型操作，不走带 connection_context 的 Service 方法
        ts = current_timestamp()
        dt = datetime_format(datetime.now())
        with DB.atomic():
            PermissionRolePermission.delete().where(
                PermissionRolePermission.role_id == role_id
            ).execute()
            if keys:
                PermissionRolePermission.insert_many([
                    {
                        "id": get_uuid(),
                        "role_id": role_id,
                        "permission_key": k,
                        "create_time": ts,
                        "create_date": dt,
                        "update_time": ts,
                        "update_date": dt,
                    }
                    for k in keys
                ]).execute()
        # 失效相关用户缓存
        users = PermissionUserRoleService.query(role_id=role_id)
        for u in users:
            invalidate_user_permissions(u.user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/users", methods=["GET"])
@login_required
@permission_required("permission_manage")
async def list_users():
    try:
        return get_json_result(data={"items": get_users_with_roles()})
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/users/<user_id>/roles", methods=["PUT"])
@login_required
@permission_required("permission_manage")
async def set_user_roles(user_id):
    try:
        body = await _json()
        # API 边界去重（保序），避免前端异常输入触发联合唯一约束冲突
        role_ids = list(dict.fromkeys(body.get("role_ids") or []))
        user = UserService.get_or_none(id=user_id)
        if not user:
            return get_data_error_result(message="用户不存在", code=RetCode.DATA_ERROR)
        # 同 delete_role：事务内直接用 peewee 模型操作，不走带 connection_context 的 Service 方法
        ts = current_timestamp()
        dt = datetime_format(datetime.now())
        with DB.atomic():
            PermissionUserRole.delete().where(
                PermissionUserRole.user_id == user_id
            ).execute()
            if role_ids:
                PermissionUserRole.insert_many([
                    {
                        "id": get_uuid(),
                        "user_id": user_id,
                        "role_id": rid,
                        "create_time": ts,
                        "create_date": dt,
                        "update_time": ts,
                        "update_date": dt,
                    }
                    for rid in role_ids
                ]).execute()
        invalidate_user_permissions(user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))


@manager.route("/permission/users/<user_id>", methods=["DELETE"])
@login_required
@permission_required("permission_manage")
async def delete_user(user_id):
    """超级管理员软删除普通用户：置 status/is_active 为 "0" + 清会话 token。
    不清理业务数据（KB/对话/租户等保留，软删除可逆）；角色绑定保留。
    仅超管可操作（permission_manage 权限之外再收紧一层）。
    """
    try:
        if not bool(getattr(current_user, "is_superuser", False)):
            return get_data_error_result(message="仅超级管理员可删除用户", code=RetCode.FORBIDDEN)
        if user_id == current_user.id:
            return get_data_error_result(message="不能删除当前登录账号", code=RetCode.FORBIDDEN)
        user = UserService.get_or_none(id=user_id)
        # 已软删（status="0"）的用户在此处查不到匹配 → 同样走「用户不存在」，幂等拒绝重复删除
        if not user or user.status != "1":
            return get_data_error_result(message="用户不存在", code=RetCode.DATA_ERROR)
        if user.is_superuser:
            return get_data_error_result(message="不能删除超级管理员", code=RetCode.FORBIDDEN)

        # 同 delete_role：事务内直接用 peewee 模型操作，不走带 connection_context 的 Service 方法
        ts = current_timestamp()
        dt = datetime_format(datetime.now())
        with DB.atomic():
            # status="0"：_load_user 每请求都过滤 status=VALID → 已有会话立即失效；
            # is_active="0"：密码登录/OAuth 登录口校验 → 返回「账号已被禁用」；
            # 清空 legacy access_token：防止 reset 接口用 get_id() 铸造有效 token
            User.update(
                status="0", is_active="0", access_token="",
                update_time=ts, update_date=dt,
            ).where(User.id == user_id).execute()
            UserToken.delete().where(UserToken.user_id == user_id).execute()
        invalidate_user_permissions(user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))
