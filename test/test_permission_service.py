import api.db.services.permission_service as svc
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
