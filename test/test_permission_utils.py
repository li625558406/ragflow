# test/test_permission_utils.py
import asyncio
import sys
import types
from unittest.mock import patch, MagicMock
import api.utils.permission_utils as pu
from common.constants import RetCode

# superuser_required 运行时才 `from api.apps import current_user`，直接导入会触发
# init_settings→Redis（本机不可用）。参照 test_permission_app.py 注入最小桩。
if "api.apps" not in sys.modules:
    _stub_apps = types.ModuleType("api.apps")
    _stub_apps.current_user = None
    sys.modules["api.apps"] = _stub_apps


def _make_user(is_superuser):
    u = MagicMock()
    u.is_superuser = is_superuser
    return u


def test_superuser_always_allowed():
    assert pu.permission_allowed(True, set(), "anything") is True
    assert pu.permission_allowed(True, {"bid"}, "crawler") is True


def test_normal_user_denied_without_key():
    assert pu.permission_allowed(False, {"bid"}, "crawler") is False


def test_normal_user_allowed_with_key():
    assert pu.permission_allowed(False, {"bid", "crawler"}, "crawler") is True


def test_required_key_undefined_in_set():
    # 对抗性：权限集含无关 key，仍拒绝
    assert pu.permission_allowed(False, {"dataset"}, "bid") is False


def test_cache_miss_fetches_db_and_sets(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value=None)
    mocker.patch.object(pu.REDIS_CONN, "set_obj")
    with patch("api.utils.permission_utils.get_user_permission_keys", return_value={"bid"}) as mk:
        assert pu.get_cached_user_permissions("u1") == {"bid"}
        pu.REDIS_CONN.set_obj.assert_called_once()


def test_cache_hit_skips_db(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value='["bid","crawler"]')
    with patch("api.utils.permission_utils.get_user_permission_keys", side_effect=AssertionError("不应查 DB")):
        assert pu.get_cached_user_permissions("u1") == {"bid", "crawler"}


# ── superuser_required 对抗用例 ──────────────────────────────────────

def _handler_registered(is_async):
    if is_async:
        @pu.superuser_required
        async def handler(x, y=0):
            return {"ok": x + y}
    else:
        @pu.superuser_required
        def handler(x, y=0):
            return {"ok": x + y}
    return handler


def test_superuser_required_unauthenticated(mocker):
    mocker.patch("api.apps.current_user", None)
    res = asyncio.run(_handler_registered(True)(1))
    assert res["code"] == RetCode.UNAUTHORIZED


def test_superuser_required_forbidden_for_normal_user(mocker):
    mocker.patch("api.apps.current_user", _make_user(False))
    mocker.patch.object(pu, "get_cached_super_role", return_value=False)
    for is_async in (True, False):
        res = asyncio.run(_handler_registered(is_async)(1))
        assert res["code"] == RetCode.FORBIDDEN


def test_superuser_required_falsy_superuser_flag_denied(mocker):
    # 对抗性：is_superuser 为 0/None 等假值也必须拒绝（且无超管角色）
    mocker.patch.object(pu, "get_cached_super_role", return_value=False)
    for falsy in (0, None, False, ""):
        mocker.patch("api.apps.current_user", _make_user(falsy))
        res = asyncio.run(_handler_registered(True)(1))
        assert res["code"] == RetCode.FORBIDDEN


def test_superuser_required_allows_superuser_and_passthrough(mocker):
    mocker.patch("api.apps.current_user", _make_user(True))
    # async 透传参数与返回值
    assert asyncio.run(_handler_registered(True)(2, y=3)) == {"ok": 5}
    # sync 透传
    assert asyncio.run(_handler_registered(False)(2, y=3)) == {"ok": 5}


def test_superuser_required_truthy_variants_allowed(mocker):
    # 对抗性：is_superuser=1（DB 整型）也必须放行
    mocker.patch("api.apps.current_user", _make_user(1))
    assert asyncio.run(_handler_registered(True)(1)) == {"ok": 1}


def test_superuser_required_handler_exception_not_swallowed(mocker):
    mocker.patch("api.apps.current_user", _make_user(True))

    @pu.superuser_required
    async def boom():
        raise ValueError("boom")

    try:
        asyncio.run(boom())
        raised = False
    except ValueError:
        raised = True
    assert raised


# ── is_superadmin（账号标志 OR 超级管理员角色）───────────────────────

def test_is_superadmin_account_flag_short_circuits(mocker):
    # 对抗性：账号标志为真时不得触发角色查询（Redis/DB 都不该被碰）
    mocker.patch.object(pu.REDIS_CONN, "get", side_effect=AssertionError("不应碰缓存"))
    with patch("api.utils.permission_utils.user_has_super_role", side_effect=AssertionError("不应查角色")):
        assert pu.is_superadmin(_make_user(1)) is True
        assert pu.is_superadmin(_make_user(True)) is True


def test_is_superadmin_role_member_allowed(mocker):
    mocker.patch("api.apps.current_user", _make_user(False))
    mocker.patch.object(pu, "get_cached_super_role", return_value=True)
    assert asyncio.run(_handler_registered(True)(1)) == {"ok": 1}
    assert asyncio.run(_handler_registered(False)(1)) == {"ok": 1}


def test_is_superadmin_neither_channel_denied(mocker):
    mocker.patch("api.apps.current_user", _make_user(False))
    mocker.patch.object(pu, "get_cached_super_role", return_value=False)
    res = asyncio.run(_handler_registered(True)(1))
    assert res["code"] == RetCode.FORBIDDEN


# ── get_cached_super_role 缓存行为 ───────────────────────────────────

def test_super_role_cache_hit_one(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value="1")
    with patch("api.utils.permission_utils.user_has_super_role", side_effect=AssertionError("不应查 DB")):
        assert pu.get_cached_super_role("u1") is True


def test_super_role_cache_hit_zero(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value="0")
    with patch("api.utils.permission_utils.user_has_super_role", side_effect=AssertionError("不应查 DB")):
        assert pu.get_cached_super_role("u1") is False


def test_super_role_cache_miss_queries_db_and_sets(mocker):
    mocker.patch.object(pu.REDIS_CONN, "get", return_value=None)
    mocker.patch.object(pu.REDIS_CONN, "set_obj")
    with patch("api.utils.permission_utils.user_has_super_role", return_value=True) as mk:
        assert pu.get_cached_super_role("u1") is True
        mk.assert_called_once_with("u1")
        pu.REDIS_CONN.set_obj.assert_called_once()


def test_super_role_cache_read_error_falls_back_to_db(mocker):
    # 对抗性：Redis 挂掉时降级直查 DB，不能把异常抛给调用方
    mocker.patch.object(pu.REDIS_CONN, "get", side_effect=RuntimeError("redis down"))
    mocker.patch.object(pu.REDIS_CONN, "set_obj", side_effect=RuntimeError("redis down"))
    with patch("api.utils.permission_utils.user_has_super_role", return_value=False) as mk:
        assert pu.get_cached_super_role("u1") is False
        mk.assert_called_once_with("u1")
