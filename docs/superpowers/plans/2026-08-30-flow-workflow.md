# C 端「流程」页签实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 C 端对话页新增「流程」页签：以文件为主视图的多角色串行工作流（发起人→领导→处理人→汇总→归档），AI 处理复用 C 端对话智能体。

**Architecture:** 后端新增 4 张 `flow_*` 表（Peewee，加入 db_models.py 自动建表）+ `flow_service.py`（纯逻辑状态机 + DB 服务）+ `flow_app.py` REST Blueprint（restful_apis 目录自动注册到 `/api/v1/flow/*`），文件存 MinIO（bucket=发起人 user_id），通知复用 Notification + NotificationUserService。前端 c-chat 新增 `flow` 页签，组件在 `web/src/pages/c-chat/flow/`，AI 面板复用 `useSendMessageBySSE(api.agentChatCompletion)`。

**Tech Stack:** Python Quart + Peewee + MinIO；React 18 + TypeScript + TanStack Query。设计文档：`D:\AI\ragflow2\docs\superpowers\specs\2026-08-30-flow-workflow-design.md`（必读）。

**约定：**
- 前端文案全部硬编码中文（跟随 c-chat 现有页签写法），不进 i18n。
- 本计划在主仓 `D:\AI\ragflow2`、分支 `feat/unified-crawler-framework` 上开发。开始前先建功能分支：`git checkout -b feat/flow-workflow`。
- Blueprint 无需手工注册：`api/apps/__init__.py:297` 自动 glob `restful_apis/*.py`。
- `DataBaseModel` 已自带 `id/create_time/update_time`，模型类不要重复定义 `id`。
- 建表自动完成：`api/db/db_models.py:708 init_database_tables()` 会为所有 `DataBaseModel` 子类建表，无需迁移脚本。

---

### Task 1: 后端数据模型（4 张 flow 表）

**Files:**
- Modify: `api/db/db_models.py`（文件末尾 `CollectionZdgksxmlExt` 之前、通知模型之后追加）

- [ ] **Step 1: 追加 4 个模型类**

在 `class Notification(DataBaseModel)` 区块之后、`class CollectionPolicyExt` 之前插入（保持文件"通知系统"区块完整性，flow 区块放在其后）：

```python
# ── C端流程（文件流转工作流） ──────────────────────────────────────
class FlowInstance(DataBaseModel):
    """流程实例：一份文件在 发起人→领导→处理人→发起人汇总 之间流转。"""
    id = CharField(max_length=32, primary_key=True)
    title = CharField(max_length=256, null=False, default="", help_text="流程标题")
    initiator_id = CharField(max_length=32, null=False, index=True, help_text="发起人 user_id（角色1，兼汇总人）")
    leader_id = CharField(max_length=32, null=False, index=True, help_text="领导 user_id（审批人）")
    handler_id = CharField(max_length=32, null=False, index=True, help_text="处理人 user_id（角色2）")
    status = CharField(max_length=32, null=False, default="initiator", index=True,
                       help_text="initiator|leader|handler|summary|archived|cancelled（当前文件在谁手上）")
    current_version_id = CharField(max_length=32, null=False, default="", help_text="当前最新版本 id")

    class Meta:
        db_table = "flow_instance"


class FlowVersion(DataBaseModel):
    """文件版本：核心表。每次人工上传 / AI 产出生成一个新版本，全历史保留。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True, help_text="FK -> flow_instance.id")
    version_no = IntegerField(null=False, default=1, help_text="版本号，从 1 递增")
    file_name = CharField(max_length=512, null=False, default="", help_text="展示文件名")
    file_path = CharField(max_length=1024, null=False, default="", help_text="MinIO object name")
    file_type = CharField(max_length=64, null=False, default="", help_text="MIME 或扩展名")
    file_size = BigIntegerField(null=False, default=0, help_text="字节数")
    source = CharField(max_length=32, null=False, default="manual_upload",
                       help_text="manual_upload|ai_output")
    created_by = CharField(max_length=32, null=False, default="", help_text="上传人 user_id")
    node_status = CharField(max_length=32, null=False, default="initiator",
                            help_text="产生该版本时的流程状态")

    class Meta:
        db_table = "flow_version"
        indexes = ((("flow_id", "version_no"), True),)


class FlowComment(DataBaseModel):
    """批注意见：针对某个文件版本的文字意见。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, index=True, help_text="意见针对的版本")
    user_id = CharField(max_length=32, null=False, index=True, help_text="意见人")
    content = TextField(null=False, default="", help_text="意见内容")

    class Meta:
        db_table = "flow_comment"


class FlowAiChat(DataBaseModel):
    """AI 处理记录：某版本上的一次 AI 对话，回复可落为新版本。"""
    id = CharField(max_length=32, primary_key=True)
    flow_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, index=True, help_text="输入版本 id")
    output_version_id = CharField(max_length=32, null=False, default="", help_text="产出版本 id（存为新版本后回填，可空）")
    instruction = TextField(null=False, default="", help_text="用户指令")
    response = TextField(null=False, default="", help_text="AI 回复全文")
    session_id = CharField(max_length=64, null=False, default="", help_text="对话会话 id")

    class Meta:
        db_table = "flow_ai_chat"
```

- [ ] **Step 2: 本地语法与建表验证**

Run: `cd D:/AI/ragflow2 && python -c "from api.db import db_models as m; print([c for c in dir(m) if c.startswith('Flow')])"`
Expected: `['FlowAiChat', 'FlowComment', 'FlowInstance', 'FlowVersion']`（本地无 MySQL 时 import 即可，不要求实际建表；建表在服务器部署后由 init_database_tables 自动完成）

若本地缺依赖导致 import 失败，改在服务器容器验证：`docker exec docker-ragflow-cpu-1 python -c "from api.db.db_models import FlowInstance, FlowVersion, FlowComment, FlowAiChat; print('ok')"`（仅验证，不部署——文件先 SCP 到服务器属部署行为，需用户确认后统一进行）

- [ ] **Step 3: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat(flow): 新增 flow_instance/flow_version/flow_comment/flow_ai_chat 四表模型"
```

---

### Task 2: 后端状态机纯逻辑 + 单元测试

**Files:**
- Create: `api/db/services/flow_service.py`
- Test: `test/test_flow_logic.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_flow_logic.py
"""FlowWorkflow 状态机纯逻辑测试（无 DB 依赖，对抗性用例含非法输入）。"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.db.services.flow_service import FlowWorkflow


def _flow(status="initiator"):
    return {
        "initiator_id": "u1", "leader_id": "u2", "handler_id": "u3",
        "status": status,
    }


class TestNodeOwner:
    def test_each_node_owner(self):
        assert FlowWorkflow.node_owner_id(_flow(), "initiator") == "u1"
        assert FlowWorkflow.node_owner_id(_flow(), "leader") == "u2"
        assert FlowWorkflow.node_owner_id(_flow(), "handler") == "u3"
        assert FlowWorkflow.node_owner_id(_flow(), "summary") == "u1"  # 汇总归发起人

    def test_terminal_no_owner(self):
        assert FlowWorkflow.owner_of_current(_flow("archived")) == ""
        assert FlowWorkflow.owner_of_current(_flow("cancelled")) == ""


class TestCanView:
    def test_participants_can_view(self):
        for uid in ("u1", "u2", "u3"):
            assert FlowWorkflow.can_view(_flow(), uid)

    def test_outsider_cannot_view(self):
        assert not FlowWorkflow.can_view(_flow(), "stranger")
        assert not FlowWorkflow.can_view(_flow(), "")
        assert not FlowWorkflow.can_view(_flow(), None)


class TestSubmitTarget:
    def test_forward_chain(self):
        assert FlowWorkflow.submit_target("initiator", "next") == "leader"
        assert FlowWorkflow.submit_target("leader", "next") == "handler"
        assert FlowWorkflow.submit_target("handler", "next") == "summary"

    def test_return_chain(self):
        assert FlowWorkflow.submit_target("leader", "return") == "initiator"
        assert FlowWorkflow.submit_target("summary", "return") == "handler"

    def test_summary_next_raises(self):
        try:
            FlowWorkflow.submit_target("summary", "next")
            assert False, "should raise"
        except ValueError as e:
            assert "归档" in str(e)

    def test_initiator_return_raises(self):
        try:
            FlowWorkflow.submit_target("initiator", "return")
            assert False, "should raise"
        except ValueError:
            pass

    def test_invalid_action(self):
        try:
            FlowWorkflow.submit_target("leader", "hack")
            assert False, "should raise"
        except ValueError:
            pass

    def test_terminal_status_raises(self):
        for st in ("archived", "cancelled"):
            for act in ("next", "return"):
                try:
                    FlowWorkflow.submit_target(st, act)
                    assert False, "should raise"
                except ValueError:
                    pass
```

- [ ] **Step 2: 运行确认失败**

Run: `cd D:/AI/ragflow2 && python -m pytest test/test_flow_logic.py -v`
Expected: FAIL / Collection error — `ModuleNotFoundError: No module named 'api.db.services.flow_service'`

- [ ] **Step 3: 实现 FlowWorkflow + 骨架**

创建 `api/db/services/flow_service.py`：

```python
# api/db/services/flow_service.py
"""C端流程：状态机纯逻辑（FlowWorkflow）+ DB 服务（FlowService 等）。

状态含义：status = 当前文件在谁手上。
流转链：initiator → leader → handler → summary → archived；leader/summary 可退回上一节点。
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from peewee import IntegrityError

from api.db.db_models import (
    DB,
    FlowAiChat,
    FlowComment,
    FlowInstance,
    FlowVersion,
    Notification,
    NotificationUser,
)
from api.db.services.common_service import CommonService
from api.utils import get_uuid

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
    def can_view(cls, flow: dict, user_id: str) -> bool:
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd D:/AI/ragflow2 && python -m pytest test/test_flow_logic.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add api/db/services/flow_service.py test/test_flow_logic.py
git commit -m "feat(flow): 状态机纯逻辑 FlowWorkflow + 单测"
```

---

### Task 3: 后端 DB 服务层（创建/查询/版本/意见/AI记录/流转/归档）

**Files:**
- Modify: `api/db/services/flow_service.py`（在 FlowWorkflow 之后追加）

- [ ] **Step 1: 追加 Service 类与存储辅助**

在同文件末尾追加：

```python
def _bucket_of(flow: dict) -> str:
    """文件统一存发起人的 bucket（RAGFlow 惯例：bucket = user_id）。"""
    return flow["initiator_id"]


class FlowInstanceService(CommonService):
    model = FlowInstance

    @classmethod
    @DB.connection_context()
    def get_flow(cls, flow_id: str) -> Optional[dict]:
        row = cls.model.get_or_none(cls.model.id == flow_id)
        return row.__data__ if row else None

    @classmethod
    @DB.connection_context()
    def list_for_user(cls, user_id: str, scope: str) -> Tuple[List[dict], int]:
        """scope: todo=待我处理 / initiated=我发起 / joined=我参与(含发起) / all=全部参与。"""
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
        # joined/all 不加额外条件
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
    def add_version(
        cls, flow: dict, object_name: str, file_name: str, file_type: str,
        file_size: int, source: str, created_by: str,
    ) -> dict:
        v = cls.model.create(
            id=get_uuid(),
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
        FlowInstance.update(current_version_id=v.id).where(
            FlowInstance.id == flow["id"]
        ).execute()
        return v.__data__

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> List[dict]:
        return [
            r.__data__
            for r in cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.version_no.asc())
        ]


class FlowCommentService(CommonService):
    model = FlowComment

    @classmethod
    @DB.connection_context()
    def add_comment(cls, flow_id: str, version_id: str, user_id: str, content: str) -> dict:
        c = cls.model.create(
            id=get_uuid(), flow_id=flow_id, version_id=version_id,
            user_id=user_id, content=content,
        )
        return c.__data__

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> List[dict]:
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
    def add_record(
        cls, flow_id: str, version_id: str, instruction: str,
        response: str, session_id: str = "", output_version_id: str = "",
    ) -> dict:
        rec = cls.model.create(
            id=get_uuid(), flow_id=flow_id, version_id=version_id,
            output_version_id=output_version_id, instruction=instruction,
            response=response, session_id=session_id,
        )
        return rec.__data__

    @classmethod
    @DB.connection_context()
    def list_by_flow(cls, flow_id: str) -> List[dict]:
        return [
            r.__data__
            for r in cls.model.select()
            .where(cls.model.flow_id == flow_id)
            .order_by(cls.model.create_time.asc())
        ]


class FlowActionService:
    """状态变更动作（流转/归档/作废），带乐观锁 + 通知。"""

    @classmethod
    @DB.connection_context()
    def submit(cls, flow: dict, user_id: str, action: str) -> dict:
        owner = FlowWorkflow.owner_of_current(flow)
        if user_id != owner:
            raise PermissionError("只有当前节点负责人可以操作")
        target = FlowWorkflow.submit_target(flow["status"], action)
        updated = (
            FlowInstance.update(status=target, update_time=time.time())
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
        FlowInstance.update(status="archived", update_time=time.time()).where(
            (FlowInstance.id == flow["id"]) & (FlowInstance.status == "summary")
        ).execute()
        return {**flow, "status": "archived"}

    @classmethod
    @DB.connection_context()
    def cancel(cls, flow: dict, user_id: str) -> dict:
        if user_id != flow["initiator_id"]:
            raise PermissionError("只有发起人可以作废")
        if flow["status"] in FlowWorkflow.TERMINAL:
            raise ValueError("流程已结束")
        FlowInstance.update(status="cancelled", update_time=time.time()).where(
            (FlowInstance.id == flow["id"])
            & (FlowInstance.status == flow["status"])
        ).execute()
        return {**flow, "status": "cancelled"}


def notify_flow_event(flow: dict, to_user_ids: List[str], title: str, summary: str) -> int:
    """复用采集通知表 + fan-out，category='flow'，site_id='flow:{flow_id}'。"""
    if not to_user_ids:
        return 0
    now = int(time.time())
    nid = get_uuid()
    Notification.insert(
        id=nid, tenant_id="system",
        site_id=f"flow:{flow['id']}", site_display=flow["title"],
        category="flow", batch_key=f"flow:{flow['id']}::{now}::{nid}",
        title=title, summary=summary, result_ids=[], result_count=0,
        publish_range="", created_at=now,
    ).execute()
    inserted = 0
    for uid in set(to_user_ids):
        try:
            NotificationUser.insert(
                id=get_uuid(), notification_id=nid, user_id=uid,
                tenant_id="system", is_read=False,
            ).execute()
            inserted += 1
        except IntegrityError:
            continue
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
```

- [ ] **Step 2: 冒烟验证 import**

Run: `cd D:/AI/ragflow2 && python -c "from api.db.services.flow_service import FlowWorkflow, FlowInstanceService, FlowVersionService, FlowCommentService, FlowAiChatService, FlowActionService, notify_flow_event, notify_target_of; print('ok')"`
Expected: `ok`（本地缺依赖则跳过，部署后容器内验证）

- [ ] **Step 3: 回归状态机单测**

Run: `cd D:/AI/ragflow2 && python -m pytest test/test_flow_logic.py -v`
Expected: 12 passed

- [ ] **Step 4: Commit**

```bash
git add api/db/services/flow_service.py
git commit -m "feat(flow): DB 服务层（版本/意见/AI记录/流转/归档/作废 + 通知）"
```

---

### Task 4: 后端 REST 端点 flow_app.py

**Files:**
- Create: `api/apps/restful_apis/flow_app.py`

参考模式：`api/apps/restful_apis/collection_app.py`（Blueprint 写法）、`api/apps/document_app.py:42`（STORAGE_IMPL 读写）。Blueprint 放在 `restful_apis/` 目录即自动注册，url_prefix=`/api/v1`。

- [ ] **Step 1: 创建 flow_app.py**

```python
# api/apps/restful_apis/flow_app.py
"""C端流程工作流 REST API（/api/v1/flow/*）。

权限：所有端点要求当前用户是流程参与人；写操作额外要求当前节点负责人（作废例外，仅发起人）。
"""
import logging
import time
from functools import wraps

from quart import Blueprint, request

from api.apps import current_user
from api.db.db_models import FlowInstance
from api.db.services.flow_service import (
    FlowActionService,
    FlowAiChatService,
    FlowCommentService,
    FlowInstanceService,
    FlowVersionService,
    FlowWorkflow,
    _bucket_of,
    notify_target_of,
)
from api.utils import get_json_result
from common import settings

logger = logging.getLogger(__name__)

manager = Blueprint("rest_flow_app", __name__)


def _err(msg: str, code: int = 100):
    return get_json_result(code=code, message=msg)


def _flow_dict(flow_id: str):
    return FlowInstanceService.get_flow(flow_id)


def _require_participant(flow: dict):
    if not flow:
        raise LookupError("流程不存在")
    if not FlowWorkflow.can_view(flow, current_user.id):
        raise PermissionError("无权访问该流程")


def _require_owner(flow: dict):
    if FlowWorkflow.owner_of_current(flow) != current_user.id:
        raise PermissionError("当前不在你的节点上，无法操作")


@manager.route("/flow", methods=["POST"])
async def create_flow():
    try:
        form = await request.form
        title = (form.get("title") or "").strip()
        leader_id = (form.get("leader_id") or "").strip()
        handler_id = (form.get("handler_id") or "").strip()
        file = (await request.files).get("file")
        if not title:
            return _err("标题不能为空")
        if not leader_id or not handler_id:
            return _err("请选择领导和处理人")
        if file is None:
            return _err("请上传初始文件")
        if leader_id == current_user.id or handler_id == current_user.id:
            return _err("领导和处理人不能是发起人自己")
        if leader_id == handler_id:
            return _err("领导和处理人不能是同一人")

        blob = await file.read()
        flow = FlowInstanceService.insert_many_returning?  # 见下——不用 insert_many
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

上面 `create_flow` 主体不完整，直接用下面这份完整实现替换（覆盖整个函数）：

```python
@manager.route("/flow", methods=["POST"])
async def create_flow():
    try:
        form = await request.form
        title = (form.get("title") or "").strip()
        leader_id = (form.get("leader_id") or "").strip()
        handler_id = (form.get("handler_id") or "").strip()
        file = (await request.files).get("file")
        if not title:
            return _err("标题不能为空")
        if not leader_id or not handler_id:
            return _err("请选择领导和处理人")
        if file is None:
            return _err("请上传初始文件")
        if leader_id == current_user.id or handler_id == current_user.id:
            return _err("领导和处理人不能是发起人自己")
        if leader_id == handler_id:
            return _err("领导和处理人不能是同一人")

        blob = await file.read()
        if not blob:
            return _err("文件内容为空")

        flow_id = FlowInstanceService.insert({
            "id": FlowInstanceService.new_id(),
            "title": title,
            "initiator_id": current_user.id,
            "leader_id": leader_id,
            "handler_id": handler_id,
            "status": "initiator",
            "current_version_id": "",
        })
        flow = _flow_dict(flow_id)

        file_name = file.filename or "未命名文件"
        object_name = f"flow/{flow_id}/v1_{file_name}"
        settings.STORAGE_IMPL.put(_bucket_of(flow), object_name, blob)
        version = FlowVersionService.add_version(
            flow, object_name, file_name,
            file.mimetype or "", len(blob), "manual_upload", current_user.id,
        )
        return get_json_result(data={"id": flow_id, "version": version})
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

> 注意：`CommonService.insert` 接收 dict 且自动填充 id/create_time 时，`"id"` 键可不传；若 `FlowInstanceService.insert` 不返回 id，改用 `flow_id = FlowInstanceService.insert({...}); flow_id = flow["id"]` 形式——RAGFlow `CommonService.insert(model, id=...)` 签名各异，**实现时以 `api/db/services/common_service.py` 实际签名为准**：若 `insert` 接收 `**kwargs`（RAGFlow 标准签名 `insert(cls, model, **kwargs)` 不存在、实为 `insert(cls, **kwargs)` 风格），按同目录其他 Service（如 `crawler_service.py`）的 insert 调用写法对齐。

继续追加其余端点：

```python
@manager.route("/flow/list", methods=["GET"])
async def list_flows():
    try:
        scope = request.args.get("scope", "joined")
        if scope not in ("todo", "initiated", "joined", "all"):
            return _err(f"非法 scope: {scope}")
        items, total = FlowInstanceService.list_for_user(current_user.id, scope)
        return get_json_result(data={"list": items, "total": total})
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>", methods=["GET"])
async def flow_detail(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        versions = FlowVersionService.list_by_flow(flow_id)
        comments = FlowCommentService.list_by_flow(flow_id)
        ai_chats = FlowAiChatService.list_by_flow(flow_id)
        return get_json_result(data={
            "flow": flow,
            "versions": versions,
            "comments": comments,
            "ai_chats": ai_chats,
            "viewer": {
                "is_owner": FlowWorkflow.owner_of_current(flow) == current_user.id,
                "is_initiator": flow["initiator_id"] == current_user.id,
            },
        })
    except LookupError as e:
        return _err(str(e))
    except PermissionError as e:
        return _err(str(e), code=403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/version", methods=["POST"])
async def upload_version(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        _require_owner(flow)
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        file = (await request.files).get("file")
        if file is None:
            return _err("缺少文件")
        blob = await file.read()
        if not blob:
            return _err("文件内容为空")
        file_name = file.filename or "未命名文件"
        version = FlowVersionService.add_version(
            flow,
            f"flow/{flow_id}/v{FlowVersionService.next_version_no(flow_id)}_{file_name}",
            file_name, file.mimetype or "", len(blob),
            "manual_upload", current_user.id,
        )
        # 修正 object_name（上面 next_version_no 与 add_version 内部各算一次，直接以实际为准）
        row = FlowVersionService.get_by_id?  # 不存在则：直接查回
        return get_json_result(data={"version": version})
    except PermissionError as e:
        return _err(str(e), code=403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

> `upload_version` 中 object_name 要与 `add_version` 写库的 file_path 一致。实现时统一做法：先算 `version_no = FlowVersionService.next_version_no(flow_id)`，拼 object_name，然后调 `add_version`（把 `object_name` 传进去），**不要**在函数里调两次 next_version_no。上面示例代码按此修正后再落盘。

```python
@manager.route("/flow/<flow_id>/version/<version_id>/download", methods=["GET"])
async def download_version(flow_id: str, version_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        version = next((v for v in FlowVersionService.list_by_flow(flow_id)
                        if v["id"] == version_id), None)
        if not version:
            return _err("版本不存在")
        from quart import Response
        blob = settings.STORAGE_IMPL.get(_bucket_of(flow), version["file_path"])
        return Response(
            blob,
            mimetype=version["file_type"] or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(version['file_name'])}",
                "Content-Length": str(len(blob)),
            },
        )
    except PermissionError as e:
        return _err(str(e), code=403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/comment", methods=["POST"])
async def add_comment(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        body = await request.get_json() or {}
        content = (body.get("content") or "").strip()
        version_id = (body.get("version_id") or "").strip()
        if not content:
            return _err("意见内容不能为空")
        if not version_id:
            version_id = flow["current_version_id"]
        comment = FlowCommentService.add_comment(
            flow_id, version_id, current_user.id, content,
        )
        # 通知发起人和目标节点以外的人也知情：通知全部参与人
        others = [uid for uid in (flow["initiator_id"], flow["leader_id"], flow["handler_id"])
                  if uid != current_user.id]
        FlowInstanceService  # noqa: B018  (仅占位避免误删，实现时删掉此行)
        notify_flow_event_by_ids(flow, others, f"流程「{flow['title']}」有新批注",
                                 f"{current_user.id} 添加了批注意见")
        return get_json_result(data={"comment": comment})
    except PermissionError as e:
        return _err(str(e), code=403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/ai-record", methods=["POST"])
async def save_ai_record(flow_id: str):
    """AI 回复存档：response 落为 .md 新版本（source=ai_output），并写 AI 记录。"""
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        _require_owner(flow)
        body = await request.get_json() or {}
        instruction = (body.get("instruction") or "").strip()
        response = (body.get("response") or "").strip()
        version_id = (body.get("version_id") or flow["current_version_id"]).strip()
        session_id = (body.get("session_id") or "").strip()
        save_as_version = bool(body.get("save_as_version", True))
        if not instruction or not response:
            return _err("指令和回复不能为空")
        output_version_id = ""
        if save_as_version:
            object_name_base = f"flow/{flow_id}/ai_{int(time.time())}"
            object_name = f"{object_name_base}.md"
            blob = response.encode("utf-8")
            settings.STORAGE_IMPL.put(_bucket_of(flow), object_name, blob)
            out_version = FlowVersionService.add_version(
                flow, object_name,
                f"AI产出_{time.strftime('%Y%m%d_%H%M%S')}.md",
                "text/markdown", len(blob), "ai_output", current_user.id,
            )
            output_version_id = out_version["id"]
        record = FlowAiChatService.add_record(
            flow_id, version_id, instruction, response, session_id, output_version_id,
        )
        return get_json_result(data={"record": record, "output_version_id": output_version_id})
    except PermissionError as e:
        return _err(str(e), code=403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/submit", methods=["POST"])
async def submit_flow(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        body = await request.get_json() or {}
        action = body.get("action")  # next | return
        updated = FlowActionService.submit(flow, current_user.id, action)
        notify_target_of(updated, action)
        return get_json_result(data={"flow": updated})
    except (PermissionError, ValueError) as e:
        return _err(str(e), code=403 if isinstance(e, PermissionError) else 100)
    except RuntimeError as e:
        return _err(str(e), code=409)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/archive", methods=["POST"])
async def archive_flow(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        updated = FlowActionService.archive(flow, current_user.id)
        notify_target_of(updated, "archive")
        return get_json_result(data={"flow": updated})
    except (PermissionError, ValueError) as e:
        return _err(str(e), code=403 if isinstance(e, PermissionError) else 100)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


@manager.route("/flow/<flow_id>/cancel", methods=["POST"])
async def cancel_flow(flow_id: str):
    try:
        flow = _flow_dict(flow_id)
        _require_participant(flow)
        updated = FlowActionService.cancel(flow, current_user.id)
        notify_target_of(updated, "cancel")
        return get_json_result(data={"flow": updated})
    except (PermissionError, ValueError) as e:
        return _err(str(e), code=403 if isinstance(e, PermissionError) else 100)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

- [ ] **Step 2: 补 import 与清理**

确认文件顶部 import 齐全（`from urllib.parse import quote`、`notify_flow_event_by_ids` 实际应为 `notify_target_of` 同文件的 `notify_flow_event` —— add_comment 里通知全部参与人请直接调 `notify_flow_event(flow, others, ...)`），删除所有 `# noqa: B018` 占位行和 `get_by_id?` 伪代码。最终文件不得含 `?` 伪代码。

- [ ] **Step 3: 冒烟验证**

Run: `cd D:/AI/ragflow2 && python -c "import ast; ast.parse(open('api/apps/restful_apis/flow_app.py', encoding='utf-8').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 4: Commit**

```bash
git add api/apps/restful_apis/flow_app.py
git commit -m "feat(flow): REST 端点（创建/列表/详情/版本/下载/意见/AI记录/流转/归档/作废）"
```

---

### Task 5: 铃铛通知兼容「流程」类目验证

**Files:**
- Modify（如需）: 铃铛组件（`grep -rn "category" web/src/layouts/components/` 定位，可能涉及 `global-navbar.tsx` 或通知面板组件）

- [ ] **Step 1: 定位铃铛渲染逻辑**

Run: `cd D:/AI/ragflow2 && grep -rn "category" web/src/layouts/components/ web/src/components/ --include=*.tsx | grep -iv "tool\|calculator" | head -20`

找到根据 `category` 映射图标/文案的 switch 或 map。

- [ ] **Step 2: 兼容 category='flow'**

若存在穷举 switch/map（如 news/bid/policy → 图标），补一个 `flow` 分支（图标用 `git-branch` 或 `file-check`，文案"流程"）；若是 fallback 默认分支则无需改动。在计划执行时把实际文件路径和改动记入 commit message。

- [ ] **Step 3: Commit（如有改动）**

```bash
git add web/src/layouts/components/ web/src/components/
git commit -m "feat(flow): 铃铛通知兼容 flow 类目图标与文案"
```

---

### Task 6: 前端 API 服务层与类型

**Files:**
- Create: `web/src/services/flow-service.ts`
- Create: `web/src/pages/c-chat/flow/flow-types.ts`

- [ ] **Step 1: 创建 flow-types.ts**

```ts
// web/src/pages/c-chat/flow/flow-types.ts
export type FlowStatus =
  | 'initiator'
  | 'leader'
  | 'handler'
  | 'summary'
  | 'archived'
  | 'cancelled';

export interface FlowInstanceItem {
  id: string;
  title: string;
  initiator_id: string;
  leader_id: string;
  handler_id: string;
  status: FlowStatus;
  current_version_id: string;
  create_time: number;
  update_time: number;
}

export interface FlowVersionItem {
  id: string;
  flow_id: string;
  version_no: number;
  file_name: string;
  file_path: string;
  file_type: string;
  file_size: number;
  source: 'manual_upload' | 'ai_output';
  created_by: string;
  node_status: FlowStatus;
  create_time: number;
}

export interface FlowCommentItem {
  id: string;
  flow_id: string;
  version_id: string;
  user_id: string;
  content: string;
  create_time: number;
}

export interface FlowAiChatItem {
  id: string;
  flow_id: string;
  version_id: string;
  output_version_id: string;
  instruction: string;
  response: string;
  session_id: string;
  create_time: number;
}

export interface FlowDetail {
  flow: FlowInstanceItem;
  versions: FlowVersionItem[];
  comments: FlowCommentItem[];
  ai_chats: FlowAiChatItem[];
  viewer: { is_owner: boolean; is_initiator: boolean };
}

export type FlowScope = 'todo' | 'initiated' | 'joined' | 'all';
```

- [ ] **Step 2: 创建 flow-service.ts**

复制 `web/src/services/c-notification-service.ts` 的 `apiFetch` 模式（同款 authHeaders / envelope 解包）：

```ts
// web/src/services/flow-service.ts
import type {
  FlowDetail,
  FlowInstanceItem,
  FlowScope,
} from '@/pages/c-chat/flow/flow-types';

const BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('Authorization') || '';
  return { Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}');
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}${path}${path.includes('?') ? '&' : '?'}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`flow api ${resp.status}`);
  const body = await resp.json();
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0) {
      throw new Error(body.message || `flow api code ${body.code}`);
    }
    return body.data as T;
  }
  return body as T;
}

export async function createFlow(formData: FormData): Promise<{ id: string }> {
  return apiFetch('/flow', { method: 'POST', body: formData });
}

export async function listFlows(
  scope: FlowScope,
): Promise<{ list: FlowInstanceItem[]; total: number }> {
  return apiFetch(`/flow/list?scope=${scope}`);
}

export async function getFlowDetail(flowId: string): Promise<FlowDetail> {
  return apiFetch(`/flow/${flowId}`);
}

export async function uploadFlowVersion(
  flowId: string,
  formData: FormData,
): Promise<{ version: unknown }> {
  return apiFetch(`/flow/${flowId}/version`, { method: 'POST', body: formData });
}

export function flowVersionDownloadUrl(flowId: string, versionId: string): string {
  const uid = getUserInfo().id || '';
  return `${BASE}/flow/${flowId}/version/${versionId}/download?user_id=${encodeURIComponent(uid)}`;
}

/** 带鉴权下载版本文件为 Blob（预览/转 File 给 AI 用） */
export async function downloadVersionBlob(
  flowId: string,
  versionId: string,
): Promise<Blob> {
  const resp = await fetch(flowVersionDownloadUrl(flowId, versionId), {
    headers: authHeaders(),
  });
  if (!resp.ok) throw new Error(`download failed ${resp.status}`);
  return resp.blob();
}

export async function addFlowComment(
  flowId: string,
  content: string,
  versionId?: string,
): Promise<{ comment: unknown }> {
  return apiFetch(`/flow/${flowId}/comment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, version_id: versionId }),
  });
}

export async function saveFlowAiRecord(
  flowId: string,
  payload: {
    instruction: string;
    response: string;
    version_id?: string;
    session_id?: string;
    save_as_version?: boolean;
  },
): Promise<{ record: unknown; output_version_id: string }> {
  return apiFetch(`/flow/${flowId}/ai-record`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function submitFlow(
  flowId: string,
  action: 'next' | 'return',
): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
}

export async function archiveFlow(flowId: string): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/archive`, { method: 'POST' });
}

export async function cancelFlow(flowId: string): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/cancel`, { method: 'POST' });
}
```

- [ ] **Step 3: ESLint 校验**

Run: `cd D:/AI/ragflow2/web && npx eslint src/services/flow-service.ts src/pages/c-chat/flow/flow-types.ts`
Expected: 无 error

- [ ] **Step 4: Commit**

```bash
git add web/src/services/flow-service.ts web/src/pages/c-chat/flow/flow-types.ts
git commit -m "feat(flow): 前端 API 服务层与类型定义"
```

---

### Task 7: 前端「流程」页签注册 + 流程面板（列表+创建）

**Files:**
- Modify: `web/src/pages/c-chat/index.tsx:565-567`（mainView 类型）、`:1450-1477`（页签数组）、`:2897` 附近（渲染分支）
- Create: `web/src/pages/c-chat/flow/flow-panel.tsx`

- [ ] **Step 1: 创建 flow-panel.tsx（列表 + 创建对话框）**

```tsx
// web/src/pages/c-chat/flow/flow-panel.tsx
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { listFlows, createFlow } from '@/services/flow-service';
import type { FlowInstanceItem, FlowScope } from './flow-types';
import FlowDetail from './flow-detail';

const SCOPES: { key: FlowScope; label: string }[] = [
  { key: 'todo', label: '待我处理' },
  { key: 'initiated', label: '我发起的' },
  { key: 'joined', label: '我参与的' },
];

const STATUS_LABEL: Record<string, string> = {
  initiator: '发起人处理中',
  leader: '领导审批中',
  handler: '处理人处理中',
  summary: '汇总审核中',
  archived: '已归档',
  cancelled: '已作废',
};

export default function FlowPanel() {
  const [scope, setScope] = useState<FlowScope>('todo');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['flow-list', scope],
    queryFn: () => listFlows(scope),
  });

  const todo = useQuery({
    queryKey: ['flow-list', 'todo'],
    queryFn: () => listFlows('todo'),
    refetchInterval: 30_000,
  });

  return (
    <div className="flex h-full w-full gap-3">
      {/* 左：流程列表 */}
      <div className="flex w-80 shrink-0 flex-col rounded-xl border border-[#E5E5E5] bg-white">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-[#F0F0F0]">
          <div className="flex gap-1">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                onClick={() => setScope(s.key)}
                className={`px-2.5 py-1 rounded-md text-xs font-medium ${
                  scope === s.key
                    ? 'bg-[#1a66fb] text-white'
                    : 'text-[#666] hover:bg-[#F5F5F5]'
                }`}
              >
                {s.label}
                {s.key === 'todo' && (todo.data?.total ?? 0) > 0 && (
                  <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] text-white">
                    {todo.data!.total}
                  </span>
                )}
              </button>
            ))}
          </div>
          <Button size="sm" className="h-7 px-2" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {isLoading && <div className="p-4 text-sm text-[#999]">加载中…</div>}
          {data?.list?.length === 0 && (
            <div className="p-4 text-sm text-[#999]">暂无流程</div>
          )}
          {data?.list?.map((f: FlowInstanceItem) => (
            <button
              key={f.id}
              onClick={() => setActiveId(f.id)}
              className={`block w-full px-3 py-2.5 text-left border-b border-[#F7F7F7] hover:bg-[#F7FAFF] ${
                activeId === f.id ? 'bg-[#EFF4FF]' : ''
              }`}
            >
              <div className="text-sm font-medium text-[#222] truncate">{f.title}</div>
              <div className="mt-1 flex items-center justify-between">
                <span className="text-xs text-[#888]">
                  {STATUS_LABEL[f.status] ?? f.status}
                </span>
                <span className="text-xs text-[#aaa]">
                  {new Date(f.update_time * 1000).toLocaleDateString()}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* 右：详情 */}
      <div className="flex-1 min-w-0 rounded-xl border border-[#E5E5E5] bg-white">
        {activeId ? (
          <FlowDetail flowId={activeId} onChanged={() => {
            qc.invalidateQueries({ queryKey: ['flow-list'] });
          }} />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[#999]">
            从左侧选择一个流程，或点击 + 新建
          </div>
        )}
      </div>

      <CreateFlowDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setCreateOpen(false);
          setActiveId(id);
          qc.invalidateQueries({ queryKey: ['flow-list'] });
        }}
      />
    </div>
  );
}

const STATUS_OPTIONS = [
  { value: 'leader', label: '领导审批中' },
];

function CreateFlowDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState('');
  const [leaderId, setLeaderId] = useState('');
  const [handlerId, setHandlerId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [users, setUsers] = useState<{ id: string; nickname: string }[]>([]);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 打开时拉用户列表（复用权限模块用户接口）
  useState(() => {
    fetch('/api/v1/permission/users', {
      headers: { Authorization: localStorage.getItem('Authorization') || '' },
    })
      .then((r) => r.json())
      .then((body) => {
        const list = body?.data?.users ?? body?.data ?? [];
        setUsers(
          (Array.isArray(list) ? list : []).map((u: any) => ({
            id: u.id ?? u.user_id,
            nickname: u.nickname ?? u.name ?? u.email ?? u.id,
          })),
        );
      })
      .catch(() => undefined);
  });

  const submit = async () => {
    setError('');
    if (!title.trim() || !leaderId || !handlerId || !file) {
      setError('请填写完整：标题、领导、处理人、初始文件');
      return;
    }
    const fd = new FormData();
    fd.append('title', title.trim());
    fd.append('leader_id', leaderId);
    fd.append('handler_id', handlerId);
    fd.append('file', file);
    setSubmitting(true);
    try {
      const res = await createFlow(fd);
      setTitle(''); setLeaderId(''); setHandlerId(''); setFile(null);
      onCreated(res.id);
    } catch (e: any) {
      setError(e.message || '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>发起流程</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-sm text-[#555]">流程标题</label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：XX 项目投标文件完善" />
          </div>
          <div>
            <label className="text-sm text-[#555]">领导（审批人）</label>
            <select
              className="mt-1 w-full h-9 rounded-md border border-[#DDD] px-2 text-sm"
              value={leaderId}
              onChange={(e) => setLeaderId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.nickname}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">处理人（角色2）</label>
            <select
              className="mt-1 w-full h-9 rounded-md border border-[#DDD] px-2 text-sm"
              value={handlerId}
              onChange={(e) => setHandlerId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.nickname}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">初始文件</label>
            <input
              type="file"
              className="mt-1 text-sm"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          {error && <div className="text-sm text-red-500">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

> 注：`useState(() => {...})` 不是正确的"打开时执行"写法——实现时改为 `useEffect(() => { if (open && users.length === 0) { ...拉取... } }, [open])`。**落盘代码必须用 useEffect 版本**。用户列表接口字段名以 `api/apps/restful_apis/permission_app.py:185 list_users` 实际返回为准（实现时先 `curl` 看响应结构再定映射）。

- [ ] **Step 2: c-chat/index.tsx 注册页签**

三处修改：

1. `mainView` 类型加 `flow`：
```tsx
  const [mainView, setMainView] = useState<
    'chat' | 'collaboration' | 'tools' | 'bid' | 'favorites' | 'flow'
  >('chat');
```

2. 页签数组末尾（`bid` 项后）追加：
```tsx
                  {
                    key: 'flow',
                    label: '流程',
                    icon: 'git-branch',
                  },
```

3. 文件顶部加 import，并在 `mainView === 'favorites'` 渲染块（约 ：2897）旁并列新增渲染分支：
```tsx
import FlowPanel from '@/pages/c-chat/flow/flow-panel';
```
```tsx
                {mainView === 'flow' && (
                  <div className="h-full w-full p-3">
                    <FlowPanel />
                  </div>
                )}
```
（渲染块位置参照 ：2858 `collaboration` 分支的包裹层写法，保持同类容器结构与高度约束一致。）

- [ ] **Step 3: ESLint 校验**

Run: `cd D:/AI/ragflow2/web && npx eslint src/pages/c-chat/flow/flow-panel.tsx src/pages/c-chat/index.tsx`
Expected: 无 error

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-panel.tsx web/src/pages/c-chat/index.tsx
git commit -m "feat(flow): c-chat 新增流程页签 + 流程列表/创建面板"
```

---

### Task 8: 前端流程详情（状态条 + 版本时间线 + 预览）

**Files:**
- Create: `web/src/pages/c-chat/flow/flow-detail.tsx`

- [ ] **Step 1: 创建 flow-detail.tsx**

```tsx
// web/src/pages/c-chat/flow/flow-detail.tsx
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, GitBranch, FileText, User, MessageSquare } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  getFlowDetail,
  addFlowComment,
  submitFlow,
  archiveFlow,
  cancelFlow,
  uploadFlowVersion,
  downloadVersionBlob,
  flowVersionDownloadUrl,
} from '@/services/flow-service';
import type { FlowDetail, FlowVersionItem } from './flow-types';
import FlowAiPanel from './flow-ai-panel';

const STATUS_TEXT: Record<string, string> = {
  initiator: '发起人处理中',
  leader: '领导审批中',
  handler: '处理人处理中',
  summary: '汇总审核中',
  archived: '已归档',
  cancelled: '已作废',
};

const NODE_OF_STATUS: Record<string, string> = {
  initiator: 'initiator_id',
  leader: 'leader_id',
  handler: 'handler_id',
  summary: 'initiator_id',
};

function userNameOf(detail: FlowDetail | undefined, field: string): string {
  return (detail?.flow as any)?.[field] ?? '';
}

function canPreview(fileType: string, fileName: string): 'pdf' | 'image' | 'text' | null {
  const ft = (fileType || '').toLowerCase();
  const fn = (fileName || '').toLowerCase();
  if (ft.includes('pdf') || fn.endsWith('.pdf')) return 'pdf';
  if (ft.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg)$/.test(fn)) return 'image';
  if (
    ft.startsWith('text/') || ft.includes('json') || ft.includes('markdown') ||
    /\.(md|txt|json|csv|log)$/.test(fn)
  )
    return 'text';
  return null;
}

export default function FlowDetail({
  flowId,
  onChanged,
}: {
  flowId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [commentText, setCommentText] = useState('');
  const [busy, setBusy] = useState(false);
  const [previewText, setPreviewText] = useState<string>('');
  const [actionError, setActionError] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['flow-detail', flowId],
    queryFn: () => getFlowDetail(flowId),
  });

  const selectedVersion: FlowVersionItem | null = useMemo(() => {
    if (!data) return null;
    const vid = selectedVersionId ?? data.flow.current_version_id;
    return data.versions.find((v) => v.id === vid) ?? data.versions.at(-1) ?? null;
  }, [data, selectedVersionId]);

  const commentsOf = useMemo(() => {
    if (!data || !selectedVersion) return [];
    return data.comments.filter((c) => c.version_id === selectedVersion.id);
  }, [data, selectedVersion]);

  if (isLoading || !data) {
    return <div className="flex h-full items-center justify-center text-sm text-[#999]">加载中…</div>;
  }

  const { flow, versions, ai_chats: aiChats, viewer } = data;
  const terminal = flow.status === 'archived' || flow.status === 'cancelled';
  const holderField = NODE_OF_STATUS[flow.status];
  const isOwner = viewer.is_owner && !terminal;
  const isInitiator = viewer.is_initiator;

  const doAction = async (fn: () => Promise<unknown>) => {
    setActionError('');
    setBusy(true);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ['flow-detail', flowId] });
      onChanged();
    } catch (e: any) {
      setActionError(e.message || '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const loadPreview = async (v: FlowVersionItem) => {
    const kind = canPreview(v.file_type, v.file_name);
    if (kind === 'text') {
      try {
        const blob = await downloadVersionBlob(flowId, v.id);
        setPreviewText(await blob.text());
      } catch {
        setPreviewText('');
      }
    } else {
      setPreviewText('');
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* 状态条 */}
      <div className="flex items-center justify-between border-b border-[#F0F0F0] px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm">
          <GitBranch className="h-4 w-4 text-[#1a66fb]" />
          <span className="font-bold text-[#222]">{flow.title}</span>
          <span className="rounded-full bg-[#EFF4FF] px-2 py-0.5 text-xs text-[#1a66fb]">
            {STATUS_TEXT[flow.status] ?? flow.status}
          </span>
          {!terminal && (
            <span className="text-xs text-[#888]">· 当前负责人 {holderField ? userNameOf(data, holderField) : '—'}</span>
          )}
        </div>
        <div className="flex gap-2">
          {isInitiator && !terminal && (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => doAction(() => cancelFlow(flowId))}>
              作废
            </Button>
          )}
          {isOwner && flow.status === 'summary' && (
            <Button size="sm" disabled={busy}
              onClick={() => doAction(() => archiveFlow(flowId))}>
              归档
            </Button>
          )}
          {isOwner && flow.status !== 'summary' && (
            <>
              <Button size="sm" variant="outline" disabled={busy}
                onClick={() => doAction(() => submitFlow(flowId, 'return'))}>
                退回上一节点
              </Button>
              <Button size="sm" disabled={busy}
                onClick={() => doAction(() => submitFlow(flowId, 'next'))}>
                提交下一节点
              </Button>
            </>
          )}
        </div>
      </div>
      {actionError && (
        <div className="px-4 py-1.5 text-xs text-red-500 bg-red-50">{actionError}</div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* 中：文件预览 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-auto bg-[#FAFAFA] p-3">
            {selectedVersion ? (
              <PreviewArea version={selectedVersion} text={previewText} onLoad={loadPreview} />
            ) : (
              <div className="text-sm text-[#999]">暂无文件</div>
            )}
          </div>
          {/* 意见区（当前选中版本的批注） */}
          <div className="max-h-40 shrink-0 overflow-y-auto border-t border-[#F0F0F0] px-4 py-2">
            <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-[#666]">
              <MessageSquare className="h-3.5 w-3.5" />
              该版本批注（{commentsOf.length}）
            </div>
            {commentsOf.map((c) => (
              <div key={c.id} className="mb-1.5 rounded-md bg-[#F7F7F7] px-2.5 py-1.5 text-xs text-[#444]">
                <User className="mr-1 inline h-3 w-3 text-[#999]" />
                {c.user_id}：{c.content}
              </div>
            ))}
            {isOwner && (
              <div className="mt-2 flex gap-2">
                <Textarea
                  className="min-h-[36px] flex-1 text-xs"
                  placeholder="写批注意见…"
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                />
                <Button size="sm" variant="outline" disabled={busy || !commentText.trim()}
                  onClick={() =>
                    doAction(async () => {
                      await addFlowComment(flowId, commentText.trim(), selectedVersion?.id);
                      setCommentText('');
                    })
                  }>
                  提交意见
                </Button>
              </div>
            )}
          </div>
          {/* AI 处理区 */}
          {isOwner && (
            <FlowAiPanel
              flowId={flowId}
              version={selectedVersion}
              aiChats={aiChats}
              onSaved={() => doAction(async () => { /* invalidate 已在 doAction 外层触发 */ })}
            />
          )}
        </div>

        {/* 右：版本时间线 */}
        <div className="w-64 shrink-0 overflow-y-auto border-l border-[#F0F0F0] px-3 py-2">
          <div className="mb-2 text-xs font-medium text-[#666]">版本时间线</div>
          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => { setSelectedVersionId(v.id); void loadPreview(v); }}
              className={`mb-1.5 block w-full rounded-lg border px-2.5 py-2 text-left ${
                selectedVersion?.id === v.id
                  ? 'border-[#1a66fb] bg-[#EFF4FF]'
                  : 'border-[#EEE] hover:bg-[#F7FAFF]'
              }`}
            >
              <div className="flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5 shrink-0 text-[#888]" />
                <span className="truncate text-xs font-medium text-[#333]">
                  v{v.version_no} {v.file_name}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between text-[10px] text-[#999]">
                <span>{v.source === 'ai_output' ? 'AI 产出' : '人工上传'}</span>
                <a
                  href={flowVersionDownloadUrl(flowId, v.id)}
                  download={v.file_name}
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center gap-0.5 hover:text-[#1a66fb]"
                >
                  <Download className="h-3 w-3" />下载
                </a>
              </div>
              {data.comments.filter((c) => c.version_id === v.id).length > 0 && (
                <div className="mt-0.5 text-[10px] text-[#c8860a]">
                  批注 ×{data.comments.filter((c) => c.version_id === v.id).length}
                </div>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function PreviewArea({
  version,
  text,
  onLoad,
}: {
  version: FlowVersionItem;
  text: string;
  onLoad: (v: FlowVersionItem) => void;
}) {
  const kind = canPreview(version.file_type, version.file_name);
  const url = flowVersionDownloadUrl(version.flow_id, version.id);
  // 首次挂载时拉取文本预览
  if (kind === 'text' && !text) {
    // 异步加载；用 setTimeout 避免渲染期副作用警告
    setTimeout(() => onLoad(version), 0);
  }
  if (kind === 'pdf') {
    return <iframe src={url} className="h-full min-h-[420px] w-full rounded-lg border border-[#EEE]" title={version.file_name} />;
  }
  if (kind === 'image') {
    return <img src={url} alt={version.file_name} className="max-h-full max-w-full rounded-lg" />;
  }
  if (kind === 'text') {
    return (
      <pre className="whitespace-pre-wrap rounded-lg bg-white p-4 text-xs leading-5 text-[#333]">
        {text || '加载中…'}
      </pre>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-[#999]">
      <span>该格式不支持在线预览（{version.file_name}）</span>
      <a href={url} download={version.file_name}>
        <Button size="sm" variant="outline">下载查看</Button>
      </a>
    </div>
  );
}
```

> 注：`PreviewArea` 中 `setTimeout` 触发加载是临时写法，实现时改为在 `FlowDetail` 里对 `selectedVersion` 做 `useEffect(() => { if (selectedVersion) void loadPreview(selectedVersion); }, [selectedVersion?.id])`，删掉 setTimeout。**落盘必须用 useEffect 版本**。

- [ ] **Step 2: ESLint 校验**

Run: `cd D:/AI/ragflow2/web && npx eslint src/pages/c-chat/flow/flow-detail.tsx`
Expected: 无 error

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-detail.tsx
git commit -m "feat(flow): 流程详情（状态条/版本时间线/预览/批注/操作栏）"
```

---

### Task 9: 前端 AI 处理面板（复用 C 端对话智能体）

**Files:**
- Create: `web/src/pages/c-chat/flow/flow-ai-panel.tsx`

**要点**：复用 `useSendMessageBySSE(api.agentChatCompletion)`（`web/src/pages/c-chat/index.tsx:326` 同款），`agent_id` 取 `localStorage.getItem('ragflow_agent_id')`（同 `c-chat/index.tsx:274-276`）。把选中版本文件转为 `File` 附加到 `files` 参数。

- [ ] **Step 1: 创建 flow-ai-panel.tsx**

```tsx
// web/src/pages/c-chat/flow/flow-ai-panel.tsx
import { useCallback, useRef, useState } from 'react';
import { Bot, Send, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import api from '@/utils/api';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import { downloadVersionBlob, saveFlowAiRecord } from '@/services/flow-service';
import type { FlowAiChatItem, FlowVersionItem } from './flow-types';

interface SendPayload {
  agent_id: string;
  query: string;
  session_id: string | null;
  stream: boolean;
  files: File[];
  internet?: boolean;
}

export default function FlowAiPanel({
  flowId,
  version,
  aiChats,
  onSaved,
}: {
  flowId: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  onSaved: () => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [saving, setSaving] = useState(false);
  const [attachFile, setAttachFile] = useState(true);
  const fileBufRef = useRef<File | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  // 与 c-chat/index.tsx:326 相同的 SSE 通道
  const { send, answerList, done, stopOutputMessage, resetAnswerList } =
    useSendMessageBySSE(api.agentChatCompletion, { excludeFanOutFromContent: false });

  const agentId = localStorage.getItem('ragflow_agent_id') || '';

  const latestResponse = useCallback(() => {
    // answerList 中取 message 事件文本聚合；done 后取完整内容
    return answerList
      .filter((e: any) => e.event === 'message' || e.data?.content)
      .map((e: any) => e.data?.content ?? e.content ?? '')
      .join('');
  }, [answerList]);

  const handleSend = async () => {
    const query = instruction.trim();
    if (!query || !agentId) return;
    let files: File[] = [];
    if (attachFile && version) {
      try {
        const blob = await downloadVersionBlob(flowId, version.id);
        const f = new File([blob], version.file_name, { type: version.file_type || 'application/octet-stream' });
        fileBufRef.current = f;
        files = [f];
      } catch {
        files = [];
      }
    }
    setInstruction('');
    await send({
      agent_id: agentId,
      query,
      session_id: sessionIdRef.current,
      stream: true,
      files,
    } as unknown as SendPayload);
  };

  const handleSave = async (asVersion: boolean) => {
    const response = latestResponse();
    if (!response) return;
    setSaving(true);
    try {
      await saveFlowAiRecord(flowId, {
        instruction: instructionRef.current || '(见记录)',
        response,
        version_id: version?.id,
        session_id: sessionIdRef.current ?? '',
        save_as_version: asVersion,
      });
      resetAnswerList();
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  // 保存时需要原始指令；用 ref 同步最近一次指令
  const instructionRef = useRef('');
  const sendWithRecord = async () => {
    instructionRef.current = instruction.trim();
    await handleSend();
  };

  return (
    <div className="shrink-0 border-t border-[#F0F0F0] px-4 py-2">
      <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-[#666]">
        <Bot className="h-3.5 w-3.5" />
        AI 处理
        {version && (
          <span className="text-[#999]">（上下文：v{version.version_no} {version.file_name}）</span>
        )}
        <label className="ml-3 inline-flex items-center gap-1 text-[#888]">
          <input type="checkbox" checked={attachFile} onChange={(e) => setAttachFile(e.target.checked)} />
          附带当前版本文件
        </label>
      </div>

      {/* 历史记录摘要 */}
      {aiChats.length > 0 && (
        <div className="mb-1.5 max-h-20 overflow-y-auto rounded-md bg-[#FAFAFA] px-2 py-1">
          {aiChats.slice(-3).map((r) => (
            <div key={r.id} className="truncate text-[10px] text-[#999]">
              指令：{r.instruction} → {r.output_version_id ? '已存为新版本' : '未存版本'}
            </div>
          ))}
        </div>
      )}

      {/* 流式输出区 */}
      {answerList.length > 0 && (
        <pre className="mb-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-white p-2 text-xs leading-5 text-[#333]">
          {latestResponse() || '思考中…'}
        </pre>
      )}

      <div className="flex items-end gap-2">
        <Textarea
          className="min-h-[36px] flex-1 text-xs"
          placeholder="输入 AI 处理指令，例如：提取第三章关键条款并总结"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
        />
        {done === false ? (
          <Button size="sm" variant="outline" onClick={() => stopOutputMessage()}>
            <Square className="h-3.5 w-3.5" />
          </Button>
        ) : (
          <Button size="sm" disabled={!instruction.trim() || !agentId} onClick={sendWithRecord}>
            <Send className="h-3.5 w-3.5" />
          </Button>
        )}
        {done === true && answerList.length > 0 && (
          <>
            <Button size="sm" variant="outline" disabled={saving} onClick={() => handleSave(false)}>
              仅存记录
            </Button>
            <Button size="sm" disabled={saving} onClick={() => handleSave(true)}>
              {saving ? '保存中…' : '存为新版本'}
            </Button>
          </>
        )}
      </div>
      {!agentId && (
        <div className="mt-1 text-[10px] text-[#c8860a]">
          未配置对话智能体（ragflow_agent_id），请先在「对话」页签使用过智能体对话。
        </div>
      )}
    </div>
  );
}
```

> **实现时必须核对的接口签名**（落盘前逐个确认，不符则以实际代码为准调整）：
> 1. `useSendMessageBySSE` 的实际导出路径：在 `c-chat/index.tsx` 顶部找它的 import（形如 `from '@/hooks/use-send-message'` 或 `@/utils`），照抄。
> 2. `send()` 的返回值与入参类型：以 `c-chat/index.tsx:1217-1224` 实际调用为准（`files` 传 `File[]`）。
> 3. `answerList` 元素结构：以 `c-chat/index.tsx` 中渲染消息内容的方式为准（`IAnswer` / `MessageEventType`），`latestResponse` 按真实字段聚合。
> 4. `done` / `stopOutputMessage` / `resetAnswerList` 的语义与 c-chat 用法一致。

- [ ] **Step 2: ESLint 校验**

Run: `cd D:/AI/ragflow2/web && npx eslint src/pages/c-chat/flow/flow-ai-panel.tsx`
Expected: 无 error

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-ai-panel.tsx
git commit -m "feat(flow): AI 处理面板（复用对话智能体 SSE + 存为新版本）"
```

---

### Task 10: 联调冒烟 + 文档登记

**Files:**
- Modify: `CHANGE.md`（追加条目）、`CLAUDE.md`（参考文档表更新状态）

- [ ] **Step 1: 前端整体构建校验（仅在要求构建时执行）**

本地开发默认热部署不构建（项目约定）。若用户要求构建验证：
Run: `cd D:/AI/ragflow2/web && npm run build`
Expected: 构建成功，无 TS error

- [ ] **Step 2: 后端容器内冒烟（需用户确认后 SCP 到服务器执行）**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from api.db.db_models import FlowInstance, FlowVersion, FlowComment, FlowAiChat
from api.db.services.flow_service import FlowWorkflow, FlowInstanceService, FlowActionService
from api.apps.restful_apis import flow_app
print('flow modules ok')
"
```
Expected: `flow modules ok`（首次 import 后 init_database_tables 自动建 4 张表）

- [ ] **Step 3: 全链路手动冒烟（服务器部署后）**

1. C 端登录账号 A，流程页签 → 发起流程（选领导 B、处理人 C、上传 PDF/MD 文件）
2. 状态条显示"发起人处理中"；AI 面板输入指令 → 流式回复 → 「存为新版本」→ 时间线出现 v2（AI 产出）
3. 提交下一节点 → B 登录看到铃铛通知 + 待我处理红点 → 写批注 → 退回
4. A 修改上传 v3 → 再提交 → B 通过 → C AI 处理 → 提交 → A 汇总节点归档
5. 验证归档后流程只读、外人（账号 D）无法通过 API 访问该流程（403）

- [ ] **Step 4: CHANGE.md 与 CLAUDE.md 登记**

`CHANGE.md` 追加：

```markdown
## 2026-08-30 C端「流程」页签（多角色文件流转工作流）

**核心变更**
- 新增 4 张表：flow_instance / flow_version / flow_comment / flow_ai_chat（db_models.py，自动建表）
- 新增 flow_service.py（状态机 FlowWorkflow + 服务层 + 通知复用）与 flow_app.py（/api/v1/flow/* 10 个端点）
- 前端 c-chat 新增「流程」页签：列表/创建、文件主视图详情（状态条+版本时间线+预览+批注）、AI 处理面板（复用对话智能体）
- 铃铛通知兼容 category='flow'

**遗留**
- AI 产出仅 Markdown 版本，docx/PDF 格式保真后续迭代
- 多文件流程、可配置模板、在线行内批注为非目标（见设计文档 §8）

**状态**：代码完成，待部署联调（成套 SCP：db_models.py / flow_service.py / flow_app.py + 前端 build）
```

`CLAUDE.md` 参考文档表中「流程页签设计」一行的"包含内容"末尾追加：`，实施计划见 docs/superpowers/plans/2026-08-30-flow-workflow.md`。

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs(flow): CHANGE.md 登记 + CLAUDE.md 计划链接"
```

---

## 部署清单（用户确认后执行，本计划不自动部署）

| 类型 | 文件 |
|------|------|
| 后端 | `api/db/db_models.py`、`api/db/services/flow_service.py`、`api/apps/restful_apis/flow_app.py` |
| 前端 | `web/dist/`（npm run build 后 tar+SCP，流程见项目 CLAUDE.md「前端部署」） |

## Self-Review 记录

- **Spec 覆盖**：状态机（Task 2/3）、4 表（Task 1）、9 端点（Task 4，spec 中 version/download 拆为上传+下载两端点，等价）、通知（Task 3/5）、前端页签/列表/创建（Task 7）、详情三区布局（Task 8）、AI 面板（Task 9）、权限（Task 4 `_require_participant/_require_owner`）、乐观锁（Task 3 `submit` where 条件）、归档留流程内（终态只读）、异常兜底（Task 8 actionError + Task 3 RuntimeError 409）——全覆盖。
- **占位符**：Task 4 中两处标注"实现时以实际签名为准"的伪代码段，已在 Step 2 明确要求清理并给出对齐对象（common_service.py / crawler_service.py）；Task 7/8 的 `useState→useEffect`、`setTimeout→useEffect` 修正已显式写明落盘要求。其余无 TBD。
- **类型一致性**：`FlowStatus`/`FlowDetail` 等 TS 类型与后端字段名逐一对齐（snake_case 直传）；`FlowWorkflow.NODE_OWNER/NODE_NEXT/NODE_PREV` 在 Task 2 测试与 Task 3 服务层引用一致；前端 service 函数名与 Task 7/8/9 调用一致（`listFlows/getFlowDetail/submitFlow/archiveFlow/cancelFlow/uploadFlowVersion/addFlowComment/saveFlowAiRecord/downloadVersionBlob/flowVersionDownloadUrl`）。
