# test/test_permission_utils.py
from unittest.mock import patch, MagicMock
import api.utils.permission_utils as pu


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
