# api/apps/restful_apis/permission_app.py
import logging

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.utils.api_utils import get_json_result, get_data_error_result
from api.utils.permission_utils import (
    permission_required,
    get_cached_user_permissions,
    invalidate_user_permissions,
)
from api.db.services.permission_service import (
    PermissionRoleService,
    PermissionRolePermissionService,
    PermissionUserRoleService,
    get_users_with_roles,
)
from api.db.services.user_service import UserService
from common.constants import RetCode
from common.misc_utils import get_uuid

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
            perms = PermissionRolePermissionService.query(role_id=r.id)
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
        with PermissionRoleService.model._meta.database.atomic():
            PermissionUserRoleService.filter_delete([PermissionUserRoleService.model.role_id == role_id])
            PermissionRolePermissionService.filter_delete([PermissionRolePermissionService.model.role_id == role_id])
            PermissionRoleService.delete_by_id(role_id)
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
        keys = body.get("permission_keys") or []
        role = PermissionRoleService.get_or_none(id=role_id)
        if not role:
            return get_data_error_result(message="角色不存在", code=RetCode.DATA_ERROR)
        with PermissionRolePermissionService.model._meta.database.atomic():
            PermissionRolePermissionService.filter_delete(
                [PermissionRolePermissionService.model.role_id == role_id]
            )
            for k in keys:
                PermissionRolePermissionService.insert(role_id=role_id, permission_key=k)
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
        role_ids = body.get("role_ids") or []
        user = UserService.get_or_none(id=user_id)
        if not user:
            return get_data_error_result(message="用户不存在", code=RetCode.DATA_ERROR)
        with PermissionUserRoleService.model._meta.database.atomic():
            PermissionUserRoleService.filter_delete(
                [PermissionUserRoleService.model.user_id == user_id]
            )
            for rid in role_ids:
                PermissionUserRoleService.insert(user_id=user_id, role_id=rid)
        invalidate_user_permissions(user_id)
        return get_json_result()
    except Exception as e:
        logging.exception(e)
        return get_data_error_result(message=str(e))
