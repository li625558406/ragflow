# api/db/services/flow_service.py
"""C端流程：状态机纯逻辑（FlowWorkflow）+ DB 服务（后续任务追加）。

状态含义：status = 当前文件在谁手上。
流转链：initiator → leader → handler → summary → archived；leader/summary 可退回上一节点。
"""
import logging

logger = logging.getLogger(__name__)


class FlowWorkflow:
    """纯逻辑：节点归属与状态转移。无 DB 依赖，可独立单测。"""

    NODE_OWNER = {
        "initiator": "initiator_id",
        "leader": "leader_id",
        "handler": "handler_id",
        "summary": "initiator_id",
    }
    NODE_NEXT = {"initiator": "leader", "leader": "handler", "handler": "summary", "summary": None}
    NODE_PREV = {"leader": "initiator", "handler": "leader", "summary": "handler"}
    TERMINAL = ("archived", "cancelled")

    @classmethod
    def node_owner_id(cls, flow: dict, status: str) -> str:
        return flow.get(cls.NODE_OWNER[status], "")

    @classmethod
    def owner_of_current(cls, flow: dict) -> str:
        if flow["status"] in cls.TERMINAL:
            return ""
        return cls.node_owner_id(flow, flow["status"])

    @classmethod
    def can_view(cls, flow: dict, user_id) -> bool:
        return user_id in (flow["initiator_id"], flow["leader_id"], flow["handler_id"])

    @classmethod
    def submit_target(cls, status: str, action: str) -> str:
        if status in cls.TERMINAL:
            raise ValueError("流程已结束")
        if action == "next":
            nxt = cls.NODE_NEXT.get(status)
            if not nxt:
                raise ValueError("当前节点已是最后一步，请直接归档")
            return nxt
        if action == "return":
            prev = cls.NODE_PREV.get(status)
            if not prev:
                raise ValueError("当前节点不能退回")
            return prev
        raise ValueError(f"未知 action: {action}")
