import api.db.services.permission_service as svc
import types
from unittest.mock import patch


def test_get_user_permission_keys_union():
    with patch.object(svc, "get_user_role_ids", return_value=["r1", "r2"]), \
         patch.object(svc, "roles_permission_keys", side_effect=lambda ids: {"bid", "crawler"} if ids == ["r1", "r2"] else set()):
        assert svc.get_user_permission_keys("u1") == {"bid", "crawler"}


def test_get_user_permission_keys_fallback_to_normal():
    with patch.object(svc, "get_user_role_ids", return_value=[]), \
         patch.object(svc, "get_normal_role_ids", return_value=["n1"]), \
         patch.object(svc, "roles_permission_keys", side_effect=lambda ids: {"bid", "c_chat"} if ids == ["n1"] else set()):
        assert svc.get_user_permission_keys("u1") == {"bid", "c_chat"}


def test_roles_permission_keys_empty_ids():
    assert svc.roles_permission_keys([]) == set()


def test_get_user_permission_keys_no_fallback_when_role_assigned_but_empty():
    # 用户挂了角色但权限映射为空 -> 不应回退到普通用户（最小权限）
    with patch.object(svc, "get_user_role_ids", return_value=["restricted_role"]), \
         patch.object(svc, "get_normal_role_ids", return_value=["n1"]), \
         patch.object(svc, "roles_permission_keys", side_effect=lambda ids: {"bid", "c_chat"} if ids == ["n1"] else set()):
        assert svc.get_user_permission_keys("u1") == set()


# ── user_has_super_role（超管角色成员判定）───────────────────────────

def _make(role=None, membership=None):
    """patch PermissionRole/PermissionUserRole.get_or_none 的最小桩。"""
    if role is not None:
        role = types.SimpleNamespace(id="super_role_id")
    return (
        patch.object(svc.PermissionRole, "get_or_none", return_value=role),
        patch.object(svc.PermissionUserRole, "get_or_none", return_value=membership),
    )


def test_user_has_super_role_true():
    role, member = _make(role=object(), membership=object())
    with role as r, member as m:
        assert svc.user_has_super_role("u1") is True
        assert m.call_count == 1


def test_user_has_super_role_no_membership():
    role, member = _make(role=object(), membership=None)
    with role, member:
        assert svc.user_has_super_role("u1") is False


def test_user_has_super_role_missing_role_is_false():
    # 对抗性：内置超级管理员角色不存在（异常库）时必须返回 False 而非抛错
    role, member = _make(role=None, membership=None)
    with role, member as m:
        assert svc.user_has_super_role("u1") is False
        m.assert_not_called()
