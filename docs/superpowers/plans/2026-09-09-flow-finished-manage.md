# C端「已结束流程」维护页 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 C 端「流程」页签新增已结束流程管理视图：查看 / 软删除（回收站可恢复）/ 再次发起 / 重新激活，同时常规列表只保留进行中流程。

**Architecture:** 后端在现有 `flow_service.py` / `flow_app.py` 上扩展——`flow_instance` 加 `deleted`/`deleted_time` 两字段（走 `migrate_db()` 幂等迁移），`list_for_user` 增加 `status` 过滤维度，`FlowActionService` 新增 soft_delete/restore/reactivate 三个带乐观锁的动作。前端在 `flow-panel.tsx` 加工作台/管理双视图切换，新建 `flow-manage.tsx` 表格管理页，`CreateFlowDialog` 抽为独立组件支持预填「再次发起」。

**Tech Stack:** Python Quart + Peewee + MySQL（后端）；React 18 + TanStack Query + Tailwind（前端）。

**Spec:** `docs/superpowers/specs/2026-09-09-flow-finished-manage-design.md`

**关键勘误（相对 spec）:** spec 中软删除端点写的是 `POST /flow/{id}/delete`，但该路由**已存在**且语义为硬删除（仅 cancelled、级联删数据+文件，见 `flow_app.py` 第 11 节）。本计划软删除改用 **`POST /flow/{id}/soft-delete`**，现有硬删除端点保持原样不动。已同步修订 spec。

**测试约定（沿用仓库现状）:** 本仓库 flow 相关测试均为**纯逻辑单测**（`test/test_flow_logic.py` 模式，不连 DB；DB 查询/端点集成测试缺基础设施）。新逻辑把「权限+前置状态」校验抽成纯函数 `FlowWorkflow.check_terminal_action` 供单测；DB 写入路径靠前端手动冒烟验证。

---

### Task 0: 修订 spec 端点命名

**Files:**
- Modify: `docs/superpowers/specs/2026-09-09-flow-finished-manage-design.md`

- [ ] **Step 1: 修正 4.2 节端点清单**

把 spec 4.2 节代码块中的：

```
POST /flow/<flow_id>/delete       软删除
```

改为：

```
POST /flow/<flow_id>/soft-delete  软删除（/delete 路由已被现有硬删除端点占用，保持不动）
```

- [ ] **Step 2: 修正 4.3 节描述**

4.3 节「三个新端点」改为「三个新端点（soft-delete/restore/reactivate）」。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-09-flow-finished-manage-design.md
git commit -m "docs(flow): spec 勘误——软删除端点改用 /soft-delete 避让现有硬删除路由"
```

---

### Task 1: flow_instance 加软删字段 + 幂等迁移

**Files:**
- Modify: `api/db/db_models.py:2251-2263`（FlowInstance 模型）
- Modify: `api/db/db_models.py` 末尾 `migrate_db()`（约 2592 行起）

- [ ] **Step 1: FlowInstance 模型加字段**

在 `class FlowInstance(DataBaseModel):` 的 `current_version_id = CharField(...)` 之后追加：

```python
    deleted = IntegerField(null=False, default=0, index=True,
                           help_text="软删标记：0正常 / 1已软删（回收站）")
    deleted_time = BigIntegerField(null=True, help_text="软删时间（毫秒时间戳）")
```

注意：用 `IntegerField` 而非 `SmallIntegerField`——db_models.py 的 peewee import 列表里没有 SmallIntegerField，不要新增 import。

- [ ] **Step 2: migrate_db() 追加迁移行**

在 `migrate_db()` 函数体末尾（最后一个 `alter_db_add_column(...)` 之后）追加两行，模式与既有行完全一致（重复执行时 `alter_db_add_column` 内部捕获 1060 Duplicate column 静默跳过，幂等安全）：

```python
    alter_db_add_column(migrator, "flow_instance", "deleted", IntegerField(null=False, default=0, index=True, help_text="软删标记：0正常 / 1已软删（回收站）"))
    alter_db_add_column(migrator, "flow_instance", "deleted_time", BigIntegerField(null=True, help_text="软删时间（毫秒时间戳）"))
```

- [ ] **Step 3: 验证模型可加载**

```bash
cd /d/AI/ragflow2 && python -c "from api.db.db_models import FlowInstance; print(FlowInstance.deleted, FlowInstance.deleted_time)"
```

Expected: 打印两个字段对象，无异常。

- [ ] **Step 4: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat(flow): flow_instance 增加 deleted/deleted_time 软删字段与幂等迁移"
```

---

### Task 2: 纯逻辑校验 check_terminal_action（TDD）

**Files:**
- Modify: `test/test_flow_logic.py`
- Modify: `api/db/services/flow_service.py`（`FlowWorkflow` 类内追加方法）

- [ ] **Step 1: 写失败测试**

在 `test/test_flow_logic.py` 末尾追加：

```python
class TestTerminalAction:
    """软删除/恢复/重新激活的权限+前置状态纯校验。"""

    def _terminal(self, status="archived", deleted=0):
        return {
            "initiator_id": "u1", "leader_id": "u2", "handler_id": "u3",
            "status": status, "deleted": deleted,
        }

    def test_non_initiator_rejected(self):
        import pytest
        for action in ("soft_delete", "restore", "reactivate"):
            with pytest.raises(PermissionError):
                FlowWorkflow.check_terminal_action(self._terminal(), "u2", action)
            with pytest.raises(PermissionError):
                FlowWorkflow.check_terminal_action(self._terminal(), "stranger", action)

    def test_soft_delete_requires_terminal(self):
        import pytest
        for st in ("initiator", "leader", "handler", "summary"):
            with pytest.raises(ValueError):
                FlowWorkflow.check_terminal_action(self._terminal(st), "u1", "soft_delete")
        FlowWorkflow.check_terminal_action(self._terminal("archived"), "u1", "soft_delete")
        FlowWorkflow.check_terminal_action(self._terminal("cancelled"), "u1", "soft_delete")

    def test_soft_delete_rejects_already_deleted(self):
        import pytest
        with pytest.raises(ValueError):
            FlowWorkflow.check_terminal_action(self._terminal(deleted=1), "u1", "soft_delete")

    def test_reactivate_requires_terminal_and_not_deleted(self):
        import pytest
        with pytest.raises(ValueError):
            FlowWorkflow.check_terminal_action(self._terminal("initiator"), "u1", "reactivate")
        with pytest.raises(ValueError):
            FlowWorkflow.check_terminal_action(self._terminal(deleted=1), "u1", "reactivate")
        FlowWorkflow.check_terminal_action(self._terminal("cancelled"), "u1", "reactivate")

    def test_restore_requires_deleted(self):
        FlowWorkflow.check_terminal_action(self._terminal(deleted=1), "u1", "restore")

    def test_unknown_action_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            FlowWorkflow.check_terminal_action(self._terminal(), "u1", "hack")
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/AI/ragflow2 && python -m pytest test/test_flow_logic.py::TestTerminalAction -v
```

Expected: FAIL，`AttributeError: ... no attribute 'check_terminal_action'`。

- [ ] **Step 3: 实现**

在 `api/db/services/flow_service.py` 的 `FlowWorkflow` 类内（`submit_target` 方法之后）追加：

```python
    @classmethod
    def check_terminal_action(cls, flow: dict, user_id, action: str) -> None:
        """软删除/恢复/重新激活的纯校验：仅发起人 + 前置状态。
        非法抛 PermissionError / ValueError；合法通过返回 None。
        restore 未删除时不在此报错——由服务层做幂等成功处理。"""
        if user_id != flow.get("initiator_id"):
            raise PermissionError("只有发起人可以操作")
        deleted = flow.get("deleted", 0)
        terminal = flow.get("status") in cls.TERMINAL
        if action == "soft_delete":
            if not terminal:
                raise ValueError("仅已结束的流程可以删除")
            if deleted:
                raise ValueError("流程已在回收站")
        elif action == "reactivate":
            if not terminal:
                raise ValueError("仅已结束的流程可以重新激活")
            if deleted:
                raise ValueError("回收站中的流程请先恢复后再重新激活")
        elif action == "restore":
            pass
        else:
            raise ValueError(f"未知 action: {action}")
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/AI/ragflow2 && python -m pytest test/test_flow_logic.py -v
```

Expected: 全部 PASS（含原有用例，确认无回归）。

- [ ] **Step 5: Commit**

```bash
git add api/db/services/flow_service.py test/test_flow_logic.py
git commit -m "feat(flow): FlowWorkflow.check_terminal_action 纯逻辑校验 + 对抗性单测"
```

---

### Task 3: list_for_user 扩展（常规列表剔除终态/软删 + status 管理页过滤）+ list 端点 status 参数

**Files:**
- Modify: `api/db/services/flow_service.py:106-129`（`FlowInstanceService.list_for_user`）
- Modify: `api/apps/restful_apis/flow_app.py:75,234-246`（`_SCOPES` 旁 + `list_flows`）

- [ ] **Step 1: 重写 list_for_user**

整体替换 `FlowInstanceService.list_for_user` 为：

```python
    LIST_TERMINAL = ("archived", "cancelled")

    @classmethod
    @DB.connection_context()
    def list_for_user(cls, user_id: str, scope: str, status: str = ""):
        """scope: todo=待我处理 / initiated=我发起 / joined=我参与 / all=同 joined。
        常规查询（status 为空）：统一剔除软删行与终态行——已结束流程统一走管理页。
        status 非空进入管理页查询（正交于 scope）：
          finished=未删除的终态 / archived / cancelled=对应终态 /
          deleted=回收站（仅本人软删的，scope 失效）。"""
        base = (
            (cls.model.initiator_id == user_id)
            | (cls.model.leader_id == user_id)
            | (cls.model.handler_id == user_id)
        )
        q = cls.model.select().where(base)
        not_deleted = cls.model.deleted == 0
        not_terminal = cls.model.status.not_in(cls.LIST_TERMINAL)
        if status == "deleted":
            # 回收站：只看自己软删的流程
            q = q.where((cls.model.initiator_id == user_id) & (cls.model.deleted == 1))
        elif status in ("finished", "archived", "cancelled"):
            terminal = (
                cls.model.status.in_(cls.LIST_TERMINAL)
                if status == "finished"
                else (cls.model.status == status)
            )
            q = q.where(not_deleted & terminal)
            if scope == "initiated":
                q = q.where(cls.model.initiator_id == user_id)
        elif scope == "todo":
            q = q.where(
                not_deleted
                & not_terminal
                & (
                    ((cls.model.status == "initiator") & (cls.model.initiator_id == user_id))
                    | ((cls.model.status == "leader") & (cls.model.leader_id == user_id))
                    | ((cls.model.status == "handler") & (cls.model.handler_id == user_id))
                    | ((cls.model.status == "summary") & (cls.model.initiator_id == user_id))
                )
            )
        elif scope == "initiated":
            q = q.where(
                not_deleted
                & not_terminal
                & (cls.model.initiator_id == user_id)
            )
        # joined / all：base 已含终态/软删过滤
        else:
            q = q.where(not_deleted & not_terminal)
        items = [r.__data__ for r in q.order_by(cls.model.update_time.desc())]
        return items, len(items)
```

行为变更说明（保留在代码注释外的提交信息里）：原 `initiated`/`joined`/`all` 会混显终态流程，现在统一剔除——这是 spec 确认的行为变更；todo 原本就排除终态，仅补 `deleted=0`。

- [ ] **Step 2: list 端点接收 status 参数**

`flow_app.py` 中 `_SCOPES = (...)` 一行之后追加：

```python
_LIST_STATUS = ("finished", "archived", "cancelled", "deleted")
```

`list_flows` 端点整体替换为：

```python
@manager.route("/flow/list", methods=["GET"])  # noqa: F821
@login_required
async def list_flows():
    try:
        scope = request.args.get("scope", "all")
        if scope not in _SCOPES:
            return _err(f"非法 scope: {scope}，可选值 todo/initiated/joined/all", 101)
        status = (request.args.get("status") or "").strip()
        if status and status not in _LIST_STATUS:
            return _err(f"非法 status: {status}，可选值 finished/archived/cancelled/deleted", 101)
        items, total = FlowInstanceService.list_for_user(current_user.id, scope, status)
        return get_json_result(data={"list": items, "total": total})
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

同时更新文件头部 docstring 中 list 一行：

```
  - GET    /flow/list?scope=&status=                流程列表（scope: todo/initiated/joined/all；status: finished/archived/cancelled/deleted 管理页过滤）
```

- [ ] **Step 3: 验证可导入 + 现有测试无回归**

```bash
cd /d/AI/ragflow2 && python -c "from api.db.services.flow_service import FlowInstanceService; print('ok')" && python -m pytest test/test_flow_logic.py -q
```

Expected: `ok` + 测试全过。

- [ ] **Step 4: Commit**

```bash
git add api/db/services/flow_service.py api/apps/restful_apis/flow_app.py
git commit -m "feat(flow): 列表支持 status 管理页过滤，常规列表剔除终态与软删流程"
```

---

### Task 4: FlowActionService 三动作 + flow_app 三端点（soft-delete / restore / reactivate）

**Files:**
- Modify: `api/db/services/flow_service.py`（`FlowActionService` 类内追加）
- Modify: `api/apps/restful_apis/flow_app.py`（文件末尾追加三端点 + docstring 补行）

- [ ] **Step 1: FlowActionService 追加三个方法**

在 `FlowActionService.delete_flow` 方法之后追加（注意：`flow_service.py` 顶部需在现有 `from api.db.db_models import (...)` 中补充导入 `User`）：

```python
    @classmethod
    @DB.connection_context()
    def soft_delete(cls, flow: dict, user_id: str) -> dict:
        """软删除：仅发起人、仅终态。置 deleted=1 + deleted_time，数据与文件全保留。"""
        FlowWorkflow.check_terminal_action(flow, user_id, "soft_delete")
        updated = (
            FlowInstance.update(
                deleted=1,
                deleted_time=current_timestamp(),
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where((FlowInstance.id == flow["id"]) & (FlowInstance.deleted == 0))  # 乐观锁
            .execute()
        )
        if not updated:
            raise RuntimeError("流程状态已变化，请刷新后重试")
        return {**flow, "deleted": 1}

    @classmethod
    @DB.connection_context()
    def restore(cls, flow: dict, user_id: str) -> dict:
        """回收站恢复：仅发起人。已恢复时幂等返回成功。"""
        if user_id != flow["initiator_id"]:
            raise PermissionError("只有发起人可以操作")
        if not flow.get("deleted", 0):
            return {**flow, "deleted": 0}  # 幂等：已恢复直接成功
        FlowInstance.update(
            deleted=0,
            deleted_time=None,
            update_time=current_timestamp(),
            update_date=datetime_format(datetime.now()),
        ).where((FlowInstance.id == flow["id"]) & (FlowInstance.deleted == 1)).execute()
        return {**flow, "deleted": 0}

    @classmethod
    @DB.connection_context()
    def reactivate(cls, flow: dict, user_id: str) -> dict:
        """重新激活：仅发起人、仅终态且未软删。状态回到 initiator，
        current_version_id 不变，历史版本/批注/AI 记录全保留。"""
        FlowWorkflow.check_terminal_action(flow, user_id, "reactivate")
        # 领导/处理人账号必须仍存在且启用
        for uid, label in ((flow["leader_id"], "领导"), (flow["handler_id"], "处理人")):
            u = User.get_or_none(User.id == uid)
            if not u or u.status != "1":
                raise ValueError(f"原{label}账号不存在或已停用，无法重新激活")
        updated = (
            FlowInstance.update(
                status="initiator",
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
            .where(
                (FlowInstance.id == flow["id"])
                & (FlowInstance.status == flow["status"])  # 乐观锁：仍是进入时的终态
                & (FlowInstance.deleted == 0)
            )
            .execute()
        )
        if not updated:
            raise RuntimeError("流程状态已变化，请刷新后重试")
        return {**flow, "status": "initiator"}
```

`User` 导入：`flow_service.py` 第 11-18 行的 import 块改为包含 `User`：

```python
from api.db.db_models import (
    DB,
    FlowAiChat,
    FlowComment,
    FlowInstance,
    FlowVersion,
    Notification,
    User,
)
```

- [ ] **Step 2: flow_app.py 追加三个端点**

文件头部 docstring 端点清单末尾（`- POST /flow/<flow_id>/cancel` 行后）补三行：

```
  - POST   /flow/<flow_id>/soft-delete              软删除（仅发起人；仅终态；可恢复）
  - POST   /flow/<flow_id>/restore                  回收站恢复（仅发起人）
  - POST   /flow/<flow_id>/reactivate               重新激活（仅发起人；状态回 initiator）
```

文件末尾（`delete_flow` 端点之后）追加：

```python
# ── 12. 软删除流程（仅发起人；仅终态；数据与文件保留，可从回收站恢复） ──
@manager.route("/flow/<flow_id>/soft-delete", methods=["POST"])  # noqa: F821
@login_required
async def soft_delete_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        FlowActionService.soft_delete(flow, current_user.id)
        return get_json_result(data={"id": flow_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 13. 回收站恢复（仅发起人；幂等） ──────────────────────────────
@manager.route("/flow/<flow_id>/restore", methods=["POST"])  # noqa: F821
@login_required
async def restore_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        FlowActionService.restore(flow, current_user.id)
        return get_json_result(data={"id": flow_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 14. 重新激活（仅发起人；仅终态；状态回 initiator，历史全保留） ──
@manager.route("/flow/<flow_id>/reactivate", methods=["POST"])  # noqa: F821
@login_required
async def reactivate_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        updated = FlowActionService.reactivate(flow, current_user.id)
        try:
            notify_flow_event(
                updated, [updated["leader_id"], updated["handler_id"]],
                f"流程「{updated['title']}」已重新激活",
                f"{_nickname_of(current_user.id)} 重新发起了该流程，当前在发起人节点处理",
            )
        except Exception as e:
            logger.warning("flow notify failed: %s", e)
        return get_json_result(data={"flow": updated})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

- [ ] **Step 3: 验证可导入 + 全量 flow 测试**

```bash
cd /d/AI/ragflow2 && python -c "
import sys, types
from importlib.util import module_from_spec, spec_from_file_location
def _noop(*a, **kw):
    def deco(f): return f
    return deco
mod = types.ModuleType('api.apps'); mod.current_user = None; mod.login_required = _noop
sys.modules['api.apps'] = mod
spec = spec_from_file_location('flow_app_check', 'api/apps/restful_apis/flow_app.py')
m = module_from_spec(spec); sys.modules['flow_app_check'] = m; spec.loader.exec_module(m)
print('flow_app import ok')
" && python -m pytest test/test_flow_logic.py test/test_flow_version_source.py -q
```

Expected: `flow_app import ok` + 测试全过。

- [ ] **Step 4: Commit**

```bash
git add api/db/services/flow_service.py api/apps/restful_apis/flow_app.py
git commit -m "feat(flow): 新增软删除/恢复/重新激活动作与端点（含参与人校验与通知）"
```

---

### Task 5: 前端 types + service 层

**Files:**
- Modify: `web/src/pages/c-chat/flow/flow-types.ts`
- Modify: `web/src/services/flow-service.ts`

- [ ] **Step 1: flow-types.ts 加字段与类型**

`FlowInstanceItem` 接口的 `update_time: number;` 之后追加：

```ts
  /** 软删标记：0 正常 / 1 回收站（常规列表不会出现 1） */
  deleted?: number;
  deleted_time?: number | null;
```

文件末尾追加：

```ts
/** 已结束流程管理页：视角与状态筛选 */
export type FlowManageScope = 'initiated' | 'joined';
export type FlowFinishedFilter = 'finished' | 'archived' | 'cancelled' | 'deleted';
```

- [ ] **Step 2: flow-service.ts 扩展**

顶部 type 导入行加入 `FlowFinishedFilter`：

```ts
import type {
  FlowDetail,
  FlowFinishedFilter,
  FlowInstanceItem,
  FlowScope,
  FlowVersionItem,
} from '@/pages/c-chat/flow/flow-types';
```

`listFlows` 整体替换为：

```ts
export async function listFlows(
  scope: FlowScope,
  status?: FlowFinishedFilter,
): Promise<{ list: FlowInstanceItem[]; total: number }> {
  return apiFetch(`/flow/list?scope=${scope}${status ? `&status=${status}` : ''}`);
}
```

文件末尾（`listCandidates` 之后）追加三个动作函数：

```ts
/** 软删除已结束流程（后端校验：仅发起人；仅终态；可恢复） */
export async function softDeleteFlow(flowId: string): Promise<{ id: string }> {
  return apiFetch(`/flow/${flowId}/soft-delete`, { method: 'POST' });
}

/** 回收站恢复（后端校验：仅发起人；幂等） */
export async function restoreFlow(flowId: string): Promise<{ id: string }> {
  return apiFetch(`/flow/${flowId}/restore`, { method: 'POST' });
}

/** 重新激活已结束流程（后端校验：仅发起人；状态回发起人节点） */
export async function reactivateFlow(
  flowId: string,
): Promise<{ flow: FlowInstanceItem }> {
  return apiFetch(`/flow/${flowId}/reactivate`, { method: 'POST' });
}
```

注意：现有 `deleteFlow`（硬删除）**保持不动**，FlowDetail 内已作废流程的删除按钮继续用它。

- [ ] **Step 3: Commit**（TS 编译验证统一放在 Task 8）

```bash
git add web/src/pages/c-chat/flow/flow-types.ts web/src/services/flow-service.ts
git commit -m "feat(flow-web): 列表 status 过滤参数与软删/恢复/重新激活 API"
```

---

### Task 6: CreateFlowDialog 抽为独立组件 + 支持预填

**Files:**
- Create: `web/src/pages/c-chat/flow/create-flow-dialog.tsx`
- Modify: `web/src/pages/c-chat/flow/flow-panel.tsx`（删除内部定义、改 import；约 306-449 行）

- [ ] **Step 1: 新建 create-flow-dialog.tsx**

把 flow-panel.tsx 中 `CreateFlowDialog` 组件整体搬出，加 `initial` 预填支持：

```tsx
// web/src/pages/c-chat/flow/create-flow-dialog.tsx
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  createFlow,
  listCandidates,
  type FlowCandidate,
} from '@/services/flow-service';
import { useEffect, useState } from 'react';

/** 「再次发起」预填：标题/参与人 + 可选初始文件（仅 doc/docx 版本可预填） */
export interface CreateFlowInitial {
  title?: string;
  leaderId?: string;
  handlerId?: string;
  file?: File | null;
}

export default function CreateFlowDialog({
  open,
  onClose,
  onCreated,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  initial?: CreateFlowInitial | null;
}) {
  const [title, setTitle] = useState('');
  const [leaderId, setLeaderId] = useState('');
  const [handlerId, setHandlerId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [users, setUsers] = useState<FlowCandidate[]>([]);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    // 每次打开重置上次残留，并应用「再次发起」预填
    setTitle(initial?.title || '');
    setLeaderId(initial?.leaderId || '');
    setHandlerId(initial?.handlerId || '');
    setFile(initial?.file || null);
    setError('');
    listCandidates()
      .then((res) => setUsers(res.list ?? []))
      .catch(() => setUsers([]));
  }, [open, initial]);

  const submit = async () => {
    setError('');
    if (!title.trim() || !leaderId || !handlerId) {
      setError('请填写完整：标题、领导、处理人');
      return;
    }
    if (leaderId === handlerId) {
      setError('领导和处理人不能是同一人');
      return;
    }
    const fd = new FormData();
    fd.append('title', title.trim());
    fd.append('leader_id', leaderId);
    fd.append('handler_id', handlerId);
    if (file) {
      fd.append('file', file);
    }
    setSubmitting(true);
    try {
      const res = await createFlow(fd);
      setTitle('');
      setLeaderId('');
      setHandlerId('');
      setFile(null);
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
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：XX 项目投标文件完善"
            />
          </div>
          <div>
            <label className="text-sm text-[#555]">领导（审批人）</label>
            <select
              className="mt-1 h-9 w-full rounded-md border border-[#DDD] px-2 text-sm"
              value={leaderId}
              onChange={(e) => setLeaderId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.nickname}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">处理人（角色2）</label>
            <select
              className="mt-1 h-9 w-full rounded-md border border-[#DDD] px-2 text-sm"
              value={handlerId}
              onChange={(e) => setHandlerId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.nickname}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">
              初始文件
              <span className="ml-1 text-xs text-[#999]">
                （可选，仅支持 doc/docx，创建后可在详情页上传）
              </span>
            </label>
            {file && (
              <div className="mt-1 flex items-center gap-2 rounded-md bg-[#F0F5FF] px-2 py-1 text-xs text-[#1a66fb]">
                <span className="truncate">{file.name}</span>
                <button
                  type="button"
                  className="ml-auto shrink-0 cursor-pointer text-[#999] hover:text-[#E5484D]"
                  onClick={() => setFile(null)}
                >
                  移除
                </button>
              </div>
            )}
            <input
              type="file"
              accept=".doc,.docx"
              className="mt-1 text-sm"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                if (f && !/\.(doc|docx)$/i.test(f.name)) {
                  setError('初始文件仅支持 doc/docx 格式');
                  e.target.value = '';
                  return;
                }
                setError('');
                setFile(f);
              }}
            />
          </div>
          {error && <div className="text-sm text-red-500">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: flow-panel.tsx 改用新组件**

flow-panel.tsx 中：
1. import 区追加 `import CreateFlowDialog from './create-flow-dialog';`
2. 删除本地 `function CreateFlowDialog({...}) {...}` 整个定义（原 306-449 行）
3. 删除因此不再使用的 import（`Input`、`createFlow`、`listCandidates`、`FlowCandidate`、`useEffect` 若仅 CreateFlowDialog 用到则一并清；`useEffect` 仍被组件主体使用则保留——删后跑 `npx tsc --noEmit` 按 noUnusedLocals 报错清即可）

- [ ] **Step 3: 类型检查**

```bash
cd /d/AI/ragflow2/web && npx tsc --noEmit
```

Expected: 无错误（若有未使用 import 报错，按报错清理）。

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/flow/create-flow-dialog.tsx web/src/pages/c-chat/flow/flow-panel.tsx
git commit -m "refactor(flow-web): CreateFlowDialog 抽为独立组件并支持再次发起预填"
```

---

### Task 7: flow-manage.tsx 管理视图 + flow-panel 入口

**Files:**
- Create: `web/src/pages/c-chat/flow/flow-manage.tsx`
- Modify: `web/src/pages/c-chat/flow/flow-panel.tsx`

- [ ] **Step 1: 新建 flow-manage.tsx**

```tsx
// web/src/pages/c-chat/flow/flow-manage.tsx
// 已结束流程管理视图：查看 / 再次发起 / 重新激活 / 软删除（回收站可恢复）。
import { Button } from '@/components/ui/button';
import {
  getFlowDetail,
  listCandidates,
  listFlows,
  reactivateFlow,
  restoreFlow,
  softDeleteFlow,
  downloadVersionBlob,
} from '@/services/flow-service';
import type { FlowCandidate } from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Eye,
  RotateCcw,
  Copy,
  Trash2,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import CreateFlowDialog, {
  type CreateFlowInitial,
} from './create-flow-dialog';
import FlowDetail from './flow-detail';
import type {
  FlowFinishedFilter,
  FlowInstanceItem,
  FlowManageScope,
} from './flow-types';
import { relTime, STATUS_BADGE, STATUS_LABEL } from './flow-utils';

const FILTERS: { key: FlowFinishedFilter; label: string }[] = [
  { key: 'finished', label: '全部' },
  { key: 'archived', label: '已归档' },
  { key: 'cancelled', label: '已作废' },
  { key: 'deleted', label: '回收站' },
];

export default function FlowManage({ onBack }: { onBack: () => void }) {
  const [scope, setScope] = useState<FlowManageScope>('initiated');
  const [filter, setFilter] = useState<FlowFinishedFilter>('finished');
  const [viewFlowId, setViewFlowId] = useState<string | null>(null);
  const [reinitiate, setReinitiate] = useState<{
    initial: CreateFlowInitial;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['flow-manage', scope, filter],
    queryFn: () => listFlows(scope, filter),
  });

  // id → 昵称映射（表格展示领导/处理人名字）
  const { data: usersData } = useQuery({
    queryKey: ['flow-candidates'],
    queryFn: listCandidates,
    staleTime: 5 * 60_000,
  });
  const userMap = useMemo(() => {
    const m = new Map<string, string>();
    (usersData?.list ?? []).forEach((u: FlowCandidate) => m.set(u.id, u.nickname));
    return m;
  }, [usersData]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['flow-manage'] });
    qc.invalidateQueries({ queryKey: ['flow-list'] });
    qc.invalidateQueries({ queryKey: ['flow-list-todo-badge'] });
  };

  const runAction = async (f: FlowInstanceItem, act: 'delete' | 'restore' | 'reactivate') => {
    setActionError('');
    setBusyId(f.id);
    try {
      if (act === 'delete') await softDeleteFlow(f.id);
      if (act === 'restore') await restoreFlow(f.id);
      if (act === 'reactivate') await reactivateFlow(f.id);
      refresh();
    } catch (e: any) {
      setActionError(e.message || '操作失败');
    } finally {
      setBusyId(null);
    }
  };

  /** 再次发起：预填标题/参与人；最终版本为 doc/docx 时预填为初始文件 */
  const startReinitiate = async (f: FlowInstanceItem) => {
    setActionError('');
    let file: File | null = null;
    try {
      if (f.current_version_id) {
        const detail = await getFlowDetail(f.id);
        const last = detail.versions[detail.versions.length - 1];
        if (last && /\.(doc|docx)$/i.test(last.file_name)) {
          const blob = await downloadVersionBlob(f.id, last.id);
          file = new File([blob], last.file_name, {
            type: last.file_type || undefined,
          });
        }
      }
    } catch {
      // 预填文件失败不阻断，走手动上传
    }
    setReinitiate({
      initial: { title: f.title, leaderId: f.leader_id, handlerId: f.handler_id, file },
    });
  };

  // ── 详情查看态：整区切换为 FlowDetail ──
  if (viewFlowId) {
    return (
      <div className="flex h-full w-full flex-col gap-2">
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 rounded-full"
            onClick={() => {
              setViewFlowId(null);
              refresh();
            }}
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            返回列表
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
          <FlowDetail
            flowId={viewFlowId}
            onChanged={refresh}
            onDeleted={() => {
              setViewFlowId(null);
              refresh();
            }}
          />
        </div>
      </div>
    );
  }

  const list = data?.list ?? [];
  const inTrash = filter === 'deleted';

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
      {/* 顶栏：返回 + 视角 + 筛选 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-[#F0F0F0] px-4 py-2.5">
        <button
          type="button"
          onClick={onBack}
          className="flex cursor-pointer items-center gap-1 text-sm font-medium text-[#444] transition-colors hover:text-[#1a66fb]"
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <span className="text-sm font-semibold text-[#222]">已结束流程</span>

        <div className="ml-4 flex overflow-hidden rounded-lg border border-[#E5E5E5]">
          {(
            [
              { key: 'initiated', label: '我发起的' },
              { key: 'joined', label: '我参与的' },
            ] as { key: FlowManageScope; label: string }[]
          ).map((s) => (
            <button
              key={s.key}
              onClick={() => setScope(s.key)}
              className={`cursor-pointer px-3 py-1 text-xs transition-colors ${
                scope === s.key
                  ? 'bg-[#1a66fb] text-white'
                  : 'bg-white text-[#666] hover:bg-[#F7F8FA]'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <span className="text-xs text-[#999]">筛选</span>
          {FILTERS.map((x) => (
            <button
              key={x.key}
              onClick={() => setFilter(x.key)}
              className={`cursor-pointer rounded-full px-2.5 py-1 text-xs transition-colors ${
                filter === x.key
                  ? 'bg-[#EFF4FF] font-medium text-[#1a66fb]'
                  : 'text-[#666] hover:bg-[#F7F8FA]'
              }`}
            >
              {x.label}
            </button>
          ))}
        </div>
      </div>

      {/* 表格 */}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        {isLoading && (
          <div className="p-6 text-center text-sm text-[#999]">加载中…</div>
        )}
        {isError && !isLoading && (
          <div className="p-6 text-center text-sm text-red-500">
            加载失败，请稍后重试
          </div>
        )}
        {!isLoading && !isError && list.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-[#F2F6FF]">
              <Archive className="h-5 w-5 text-[#1a66fb]" />
            </div>
            <div className="text-sm text-[#666]">
              {inTrash ? '回收站为空' : '暂无已结束的流程'}
            </div>
          </div>
        )}
        {!isLoading && !isError && list.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#F0F0F0] text-left text-xs text-[#999]">
                <th className="px-4 py-2 font-normal">标题</th>
                <th className="px-3 py-2 font-normal">状态</th>
                <th className="px-3 py-2 font-normal">领导</th>
                <th className="px-3 py-2 font-normal">处理人</th>
                <th className="px-3 py-2 font-normal">最后更新</th>
                <th className="px-4 py-2 text-right font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((f: FlowInstanceItem) => (
                <tr
                  key={f.id}
                  className="border-b border-[#F7F7F7] transition-colors hover:bg-[#FAFBFC]"
                >
                  <td className="max-w-[280px] truncate px-4 py-2.5 font-medium text-[#222]">
                    {f.title}
                  </td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs ${
                        STATUS_BADGE[f.status] ?? 'bg-[#F2F2F2] text-[#666]'
                      }`}
                    >
                      {STATUS_LABEL[f.status] ?? f.status}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-[#666]">
                    {userMap.get(f.leader_id) || f.leader_id}
                  </td>
                  <td className="px-3 py-2.5 text-[#666]">
                    {userMap.get(f.handler_id) || f.handler_id}
                  </td>
                  <td className="px-3 py-2.5 text-[#999]">
                    {relTime(f.update_time)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="inline-flex items-center gap-1">
                      <ActionBtn
                        title="查看"
                        disabled={busyId === f.id}
                        onClick={() => setViewFlowId(f.id)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </ActionBtn>
                      {scope === 'initiated' && !inTrash && (
                        <>
                          <ActionBtn
                            title="再次发起"
                            disabled={busyId === f.id}
                            onClick={() => startReinitiate(f)}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </ActionBtn>
                          <ActionBtn
                            title="重新激活"
                            disabled={busyId === f.id}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `确定重新激活流程「${f.title}」？激活后回到发起人节点继续流转。`,
                                )
                              )
                                runAction(f, 'reactivate');
                            }}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </ActionBtn>
                          <ActionBtn
                            title="删除（可在回收站恢复）"
                            disabled={busyId === f.id}
                            danger
                            onClick={() => {
                              if (
                                window.confirm(
                                  `确定删除流程「${f.title}」？删除后移入回收站，可随时恢复。`,
                                )
                              )
                                runAction(f, 'delete');
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </ActionBtn>
                        </>
                      )}
                      {scope === 'initiated' && inTrash && (
                        <>
                          <ActionBtn
                            title="恢复"
                            disabled={busyId === f.id}
                            onClick={() => runAction(f, 'restore')}
                          >
                            <ArchiveRestore className="h-3.5 w-3.5" />
                          </ActionBtn>
                          <ActionBtn
                            title="查看"
                            disabled={busyId === f.id}
                            onClick={() => setViewFlowId(f.id)}
                          >
                            <Eye className="h-3.5 w-3.5" />
                          </ActionBtn>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {actionError && (
          <div className="px-4 py-2 text-sm text-red-500">{actionError}</div>
        )}
      </div>

      <CreateFlowDialog
        open={!!reinitiate}
        initial={reinitiate?.initial}
        onClose={() => setReinitiate(null)}
        onCreated={(id) => {
          setReinitiate(null);
          refresh();
          setViewFlowId(id);
        }}
      />
    </div>
  );
}

/** 操作列图标按钮 */
function ActionBtn({
  title,
  onClick,
  disabled,
  danger,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`cursor-pointer rounded-md p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        danger
          ? 'text-[#999] hover:bg-[#FFF1F0] hover:text-[#E5484D]'
          : 'text-[#666] hover:bg-[#EFF4FF] hover:text-[#1a66fb]'
      }`}
    >
      {children}
    </button>
  );
}
```

- [ ] **Step 2: flow-panel.tsx 接入管理视图**

1. import 区追加：

```tsx
import { Archive } from 'lucide-react';   // 与既有 lucide-react import 合并
import FlowManage from './flow-manage';
```

2. 组件内 `const [createOpen, setCreateOpen] = useState(false);` 之后追加视图状态：

```tsx
  // 工作台 / 已结束流程管理 双视图
  const [view, setView] = useState<'workbench' | 'manage'>('workbench');
```

3. 顶栏「+」按钮（`<Button size="icon" ... onClick={() => setCreateOpen(true)}>`）之前插入入口按钮：

```tsx
            <Button
              size="icon"
              variant="outline"
              title="已结束流程"
              className="h-7 w-7 shrink-0 rounded-full border-[#E5E5E5] text-[#666] transition-transform hover:text-[#1a66fb] active:scale-90"
              onClick={() => setView('manage')}
            >
              <Archive className="h-4 w-4" />
            </Button>
```

4. 渲染区改造：把现有「左列表 + 批注区」`<div className="flex w-80 ...">` 与「右详情」`<div className="min-w-0 flex-1 ...">` 两个块整体包进 `{view === 'workbench' && (<> ... </>)}`，并在其后追加：

```tsx
      {view === 'manage' && (
        <div className="min-w-0 flex-1">
          <FlowManage onBack={() => setView('workbench')} />
        </div>
      )}
```

注意：`<CreateFlowDialog .../>` 与外层根 `<div ref={rootRef} className="flex h-full w-full gap-3">` 保持不变；FlowDetail 的 `onChanged`/`onDeleted` 回调里对 `['flow-list']` 的失效逻辑保持原样。

- [ ] **Step 3: 类型检查**

```bash
cd /d/AI/ragflow2/web && npx tsc --noEmit
```

Expected: 无错误。

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-manage.tsx web/src/pages/c-chat/flow/flow-panel.tsx
git commit -m "feat(flow-web): 已结束流程管理视图（筛选/查看/再次发起/重新激活/回收站软删恢复）"
```

---

### Task 8: 全量验证 + 文档收尾

**Files:**
- Modify: `CHANGE.md`
- Modify: `CLAUDE.md`（参考文档表）

- [ ] **Step 1: 后端全量测试 + lint**

```bash
cd /d/AI/ragflow2 && python -m pytest test/test_flow_logic.py test/test_flow_version_source.py test/test_flow_doc_table_edit.py -q && ruff check api/db/services/flow_service.py api/apps/restful_apis/flow_app.py api/db/db_models.py
```

Expected: 测试全过，ruff 无报错（line-length=200 内）。

- [ ] **Step 2: 前端类型检查**

```bash
cd /d/AI/ragflow2/web && npx tsc --noEmit
```

Expected: 无错误。（不做 `npm run build`——遵守「本地开发不自动构建，部署时才构建」约定。）

- [ ] **Step 3: CHANGE.md 追加迭代记录**

在 `CHANGE.md` 顶部追加（保持现有条目格式）：

```markdown
## 2026-09-09 流程页签：已结束流程维护页

**改动主题**：C端流程页签新增「已结束流程」管理视图，发起人可统一维护走完的流程。

**核心变更点**：
- flow_instance 新增 deleted/deleted_time 软删字段（migrate_db 幂等迁移）
- GET /flow/list 新增 status 过滤（finished/archived/cancelled/deleted）；常规列表（todo/initiated/joined/all）剔除终态与软删行
- 新端点：POST /flow/{id}/soft-delete、/restore、/reactivate（仅发起人，乐观锁；reactivate 校验领导/处理人账号并通知）
- 前端：流程页签新增管理视图（视角切换 + 状态筛选 + 表格操作：查看/再次发起/重新激活/删除）；CreateFlowDialog 抽独立组件支持预填；回收站可恢复
- 设计文档 docs/superpowers/specs/2026-09-09-flow-finished-manage-design.md；实施计划 docs/superpowers/plans/2026-09-09-flow-finished-manage.md

**遗留事项**：未部署；彻底删除入口维持 FlowDetail 内原硬删除（仅已作废）不变。
```

- [ ] **Step 4: CLAUDE.md 参考文档表补一行**

在项目 `CLAUDE.md` 参考文档表「范本填写取消链路设计」行后追加：

```markdown
| 已结束流程维护页 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-09-flow-finished-manage-design.md` | ★ 流程页签管理视图：已结束流程查看/软删除(回收站可恢复)/再次发起/重新激活；flow_instance 软删字段 + list status 过滤 + 3 动作端点；实施计划 docs/superpowers/plans/2026-09-09-flow-finished-manage.md（已完成编码，未部署） |
```

（若实施未全部完成，「已完成编码」按实际状态措辞。）

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 已结束流程维护页迭代记录与参考文档登记"
```

---

## 手动冒烟（编码完成后，用户本地验证）

前置：本地 dev 环境可跑（前端 `npm run dev`，后端 docker 或本地服务）。

1. 用 A 账号发起流程 → 流转到终态（归档或作废）→ 工作台「我发起的」不再显示该流程
2. 点左上「已结束流程」图标 → 管理页可见该流程（全部/已归档/已作废筛选正确）
3. 点「查看」→ FlowDetail 正常展示版本时间线 → 返回列表
4. 「删除」→ 确认 → 列表消失 → 切「回收站」筛选可见 → 「恢复」→ 回到「全部」
5. 「重新激活」→ 确认 → 工作台「待我处理」出现该流程（发起人节点）→ 详情历史完整
6. 「再次发起」→ 弹窗预填标题/领导/处理人（最终版本 doc/docx 时初始文件已预填且可移除）→ 创建成功
7. 用 B 账号（领导或处理人）登录 → 管理页「我参与的」可见、操作列只有「查看」→ 收到重新激活通知

## 部署清单（用户指示后执行）

后端成套 SCP：`api/db/db_models.py`、`api/db/services/flow_service.py`、`api/apps/restful_apis/flow_app.py` → `docker restart docker-ragflow-cpu-1`（迁移随启动自动执行，幂等）；前端 `npm run build` 标准部署。冒烟：容器内 `python -c "from api.db.services.flow_service import FlowActionService; print('ok')"` + 走一遍 删除→恢复→重新激活。
