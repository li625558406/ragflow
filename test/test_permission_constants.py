# test/test_permission_constants.py
from api.constants import MODULE_PERMISSIONS, NORMAL_ROLE_PERMISSIONS


def test_module_permissions_nonempty():
    assert len(MODULE_PERMISSIONS) >= 5


def test_normal_role_permissions_are_valid_keys():
    for k in NORMAL_ROLE_PERMISSIONS:
        assert k in MODULE_PERMISSIONS, f"{k} 未在 MODULE_PERMISSIONS 中定义"


def test_keys_are_stable_snake_case():
    for k in MODULE_PERMISSIONS:
        assert k.isidentifier(), f"{k} 不是合法标识符"
