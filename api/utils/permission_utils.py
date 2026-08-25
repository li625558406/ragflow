# api/utils/permission_utils.py
import json
import logging
from functools import wraps
from inspect import iscoroutinefunction

from common import settings  # 必须先导入 settings，避免 redis_conn 循环导入

from rag.utils.redis_conn import REDIS_CONN

from api.constants.permission import PERMISSION_CACHE_PREFIX, PERMISSION_CACHE_TTL
# get_user_permission_keys 必须是模块级导入（而非函数内局部导入），
# 这样测试才能 patch("api.utils.permission_utils.get_user_permission_keys") 拦截缓存未命中时的 DB 回调。
# 已验证 permission_service 只依赖 db_models/common_service/api.constants.permission，不形成循环导入。
from api.db.services.permission_service import get_user_permission_keys


def permission_allowed(is_superuser: bool, permissions: set, required_key: str) -> bool:
    """纯逻辑判定：超管直接放行；否则看 required_key 是否在权限点集合。"""
    if is_superuser:
        return True
    return required_key in permissions


def get_cached_user_permissions(user_id: str) -> set:
    cache_key = f"{PERMISSION_CACHE_PREFIX}{user_id}"
    try:
        cached = REDIS_CONN.get(cache_key)
        if cached is not None:
            return set(json.loads(cached))  # set_obj 存的是 json.dumps，读回必须 json.loads（见 connector_api.py:482）
    except Exception as e:
        logging.warning("permission cache get failed: %s", e)
    keys = get_user_permission_keys(user_id)
    try:
        REDIS_CONN.set_obj(cache_key, sorted(keys), PERMISSION_CACHE_TTL)
    except Exception as e:
        logging.warning("permission cache set failed: %s", e)
    return keys


def invalidate_user_permissions(user_id: str) -> None:
    try:
        REDIS_CONN.delete(f"{PERMISSION_CACHE_PREFIX}{user_id}")
    except Exception:
        pass


def permission_required(permission_key: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from api.apps import current_user
            from api.utils.api_utils import get_json_result
            from common.constants import RetCode

            user = current_user
            if not user:
                return get_json_result(code=RetCode.UNAUTHORIZED, message="未登录")
            if not permission_allowed(bool(user.is_superuser), get_cached_user_permissions(user.id), permission_key):
                return get_json_result(code=RetCode.FORBIDDEN, message="无权限访问该模块")
            if iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator
