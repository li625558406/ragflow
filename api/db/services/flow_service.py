# api/db/services/flow_service.py
"""C端流程：状态机纯逻辑（FlowWorkflow）+ DB 服务（后续任务追加）。

状态含义：status = 当前文件在谁手上。
流转链：initiator → leader → handler → summary → archived；leader/summary 可退回上一节点。
"""
import logging
import time
from datetime import datetime

from api.db.db_models import (
    DB,
    FlowAiChat,
    FlowComment,
    FlowInstance,
    FlowVersion,
    Notification,
)
from api.db.services.common_service import CommonService
from api.db.services.notification_service import NotificationUserService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format

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


def _bucket_of(flow: dict) -> str:
    """文件统一存发起人的 bucket（RAGFlow 惯例：bucket = user_id）。"""
    return flow["initiator_id"]


class FlowInstanceService(CommonService):
    model = FlowInstance

    @classmethod
    @DB.connection_context()
    def get_flow(cls, flow_id: str):
        row = cls.model.get_or_none(cls.model.id == flow_id)
        return row.__data__ if row else None

    @classmethod
    @DB.connection_context()
    def list_for_user(cls, user_id: str, scope: str):
        """scope: todo=待我处理 / initiated=我发起 / joined=我参与 / all=同 joined。"""
        base = (
            (cls.model.initiator_id == user_id)
            | (cls.model.leader_id == user_id)
            | (cls.model.handler_id == user_id)
        )
        q = cls.model.select().where(base)
        if scope == "todo":
            q = q.where(
                cls.model.status.not_in(["archived", "cancelled"])
                & (
                    ((cls.model.status == "initiator") & (cls.model.initiator_id == user_id))
                    | ((cls.model.status == "leader") & (cls.model.leader_id == user_id))
                    | ((cls.model.status == "handler") & (cls.model.handler_id == user_id))
                    | ((cls.model.status == "summary") & (cls.model.initiator_id == user_id))
                )
            )
        elif scope == "initiated":
            q = q.where(cls.model.initiator_id == user_id)
        items = [r.__data__ for r in q.order_by(cls.model.update_time.desc())]
        return items, len(items)


class FlowVersionService(CommonService):
    model = FlowVersion

    @classmethod
    @DB.connection_context()
    def next_version_no(cls, flow_id: str) -> int:
        last = (
            cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.version_no.desc())
            .first()
        )
        return (last.version_no + 1) if last else 1

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> list:
        return [
            r.__data__
            for r in cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.version_no.asc())
        ]

    @classmethod
    @DB.connection_context()
    def add_version(cls, flow: dict, object_name: str, file_name: str, file_type: str,
                    file_size: int, source: str, created_by: str) -> dict:
        # cls.insert 自动填充 id + create/update 时间戳，model.create 不会填 id，故统一走 insert
        with DB.atomic():
            v = cls.insert(
                flow_id=flow["id"],
                version_no=cls.next_version_no(flow["id"]),
                file_name=file_name,
                file_path=object_name,
                file_type=file_type,
                file_size=file_size,
                source=source,
                created_by=created_by,
                node_status=flow["status"],
            )
            FlowInstance.update(
                current_version_id=v.id,
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            ).where(FlowInstance.id == flow["id"]).execute()
        return v.__data__


class FlowCommentService(CommonService):
    model = FlowComment

    @classmethod
    @DB.connection_context()
    def add_comment(cls, flow_id: str, version_id: str, user_id: str, content: str) -> dict:
        c = cls.insert(flow_id=flow_id, version_id=version_id, user_id=user_id, content=content)
        return c.__data__

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> list:
        return [
            r.__data__
            for r in cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.create_time.asc())
        ]


class FlowAiChatService(CommonService):
    model = FlowAiChat

    @classmethod
    @DB.connection_context()
    def add_record(cls, flow_id: str, version_id: str, instruction: str,
                   response: str, session_id: str = "", output_version_id: str = "") -> dict:
        rec = cls.insert(
            flow_id=flow_id,
            version_id=version_id,
            instruction=instruction,
            response=response,
            session_id=session_id,
            output_version_id=output_version_id,
        )
        return rec.__data__

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> list:
        return [
            r.__data__
            for r in cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.create_time.asc())
        ]


class FlowActionService:
    """状态变更动作（流转/归档/作废），带乐观锁 + 权限校验。"""

    @classmethod
    @DB.connection_context()
    def submit(cls, flow: dict, user_id: str, action: str) -> dict:
        if flow["status"] in FlowWorkflow.TERMINAL:
            raise ValueError("流程已结束")
        owner = FlowWorkflow.owner_of_current(flow)
        if user_id != owner:
            raise PermissionError("只有当前节点负责人可以操作")
        target = FlowWorkflow.submit_target(flow["status"], action)
        updated = (
            FlowInstance.update(
                status=target,
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where(
                (FlowInstance.id == flow["id"])
                & (FlowInstance.status == flow["status"])  # 乐观锁
            )
            .execute()
        )
        if not updated:
            raise RuntimeError("流程状态已变化，请刷新后重试")
        return {**flow, "status": target}

    @classmethod
    @DB.connection_context()
    def archive(cls, flow: dict, user_id: str) -> dict:
        if flow["status"] != "summary":
            raise ValueError("只有汇总节点可以归档")
        if user_id != flow["initiator_id"]:
            raise PermissionError("只有发起人可以归档")
        updated = (
            FlowInstance.update(
                status="archived",
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where((FlowInstance.id == flow["id"]) & (FlowInstance.status == "summary"))
            .execute()
        )
        if not updated:
            raise RuntimeError("流程状态已变化，请刷新后重试")
        return {**flow, "status": "archived"}

    @classmethod
    @DB.connection_context()
    def cancel(cls, flow: dict, user_id: str) -> dict:
        if user_id != flow["initiator_id"]:
            raise PermissionError("只有发起人可以作废")
        if flow["status"] in FlowWorkflow.TERMINAL:
            raise ValueError("流程已结束")
        updated = (
            FlowInstance.update(
                status="cancelled",
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where(
                (FlowInstance.id == flow["id"]) & (FlowInstance.status == flow["status"])
            )
            .execute()
        )
        if not updated:
            raise RuntimeError("流程状态已变化，请刷新后重试")
        return {**flow, "status": "cancelled"}


def notify_flow_event(flow: dict, to_user_ids: list, title: str, summary: str) -> int:
    """复用采集通知表 + fan-out，category='flow'，site_id='flow:{flow_id}'。"""
    if not to_user_ids:
        return 0
    now = int(time.time() * 1000)  # 毫秒，与存量通知体系一致
    nid = get_uuid()
    with DB.atomic():
        Notification.insert(
            id=nid, tenant_id="system",
            site_id=f"flow:{flow['id']}", site_display=flow["title"],
            category="flow", batch_key=f"flow:{flow['id']}::{now}::{nid}",
            title=title, summary=summary, result_ids=[], result_count=0,
            publish_range="", created_at=now,
        ).execute()
        inserted = NotificationUserService.fan_out(nid, list(set(to_user_ids)))
    return inserted


def notify_target_of(flow: dict, action: str, actor_name: str = "") -> int:
    """流转/退回后通知目标节点负责人；归档/作废通知全部参与人。"""
    status = flow["status"]
    if status in FlowWorkflow.TERMINAL:
        return notify_flow_event(
            flow,
            [flow["initiator_id"], flow["leader_id"], flow["handler_id"]],
            f"流程「{flow['title']}」已{'归档' if status == 'archived' else '作废'}",
            f"{actor_name or '发起人'} 完成了{'归档' if status == 'archived' else '作废'}操作",
        )
    target_uid = FlowWorkflow.owner_of_current(flow)
    verb = "退回到你处理" if action == "return" else "流转到你这处理"
    return notify_flow_event(
        flow, [target_uid],
        f"流程「{flow['title']}」需要你处理",
        f"{actor_name or '上一位参与人'} 将文件{verb}（当前节点：{status}）",
    )
