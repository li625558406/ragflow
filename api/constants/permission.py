# api/constants/permission.py
"""模块模块权限点常量表（单一来源，前端引用同一套 key）。"""

# 权限点 key -> 中文名（用于前端展示 + seed 进 permission_role_permission）
MODULE_PERMISSIONS = {
    "bid": "标讯管理",
    "dataset": "知识库",
    "chat": "对话",
    "search": "搜索",
    "agent": "Agent 画布/流程",
    "memory": "记忆",
    "file": "文件",
    "crawler": "智能采集",
    "user_setting": "用户设置",
    "home": "C 端着陆页",
    "c_chat": "投标助手对话",
    "permission_manage": "权限管理",
}

# 内置角色名
SUPER_ROLE_NAME = "超级管理员"
NORMAL_ROLE_NAME = "普通用户"

# 普通用户默认勾选的模块权限点
NORMAL_ROLE_PERMISSIONS = ["bid", "chat", "c_chat", "home", "user_setting"]

# 权限缓存 TTL（秒）与 key 前缀
PERMISSION_CACHE_TTL = 600
PERMISSION_CACHE_PREFIX = "perm:"
