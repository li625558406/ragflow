# scripts/_smoke_permission.py — 容器内跑：检查蓝本注册 + seed 完成
from api.db.db_models import PermissionRole, PermissionRolePermission, PermissionUserRole

assert PermissionRole.table_exists(), "permission_role 未建表"
assert PermissionRolePermission.table_exists()
assert PermissionUserRole.table_exists()
print("permission tables OK")

normal = PermissionRole.get_or_none(name="普通用户")
assert normal, "普通用户角色未 seed"
print("seed OK:", normal.name)
print("smoke passed")
