from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole


def test_models_have_expected_tables():
    assert PermissionRole._meta.table_name == "permission_role"
    assert PermissionRolePermission._meta.table_name == "permission_role_permission"
    assert PermissionUserRole._meta.table_name == "permission_user_role"
