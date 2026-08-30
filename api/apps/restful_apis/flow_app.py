#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""C端流程（文件流转工作流）REST API

路由前缀：/api/v1/flow

端点：
  - POST   /flow                                    创建流程（发起人上传 v1 文件）
  - GET    /flow/list?scope=                        流程列表（todo/initiated/joined/all）
  - GET    /flow/<flow_id>                          流程详情（版本/批注/AI记录/视角）
  - POST   /flow/<flow_id>/version                  追加新版本（人工上传）
  - GET    /flow/<flow_id>/version/<version_id>/download  下载某版本文件
  - POST   /flow/<flow_id>/comment                  添加批注意见（通知其他参与人）
  - POST   /flow/<flow_id>/ai-record                记录一次 AI 处理（回复可落为新版本）
  - POST   /flow/<flow_id>/submit                   流转（next）/ 退回（return）
  - POST   /flow/<flow_id>/archive                  归档（仅汇总节点发起人）
  - POST   /flow/<flow_id>/cancel                   作废（仅发起人）
"""
import hashlib
import logging
import os
import time
import uuid
from urllib.parse import quote

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.db_models import FlowInstance, User
from api.db.services.flow_service import (
    FlowActionService,
    FlowAiChatService,
    FlowCommentService,
    FlowInstanceService,
    FlowVersionService,
    FlowWorkflow,
    _bucket_of,
    notify_flow_event,
    notify_target_of,
)
from api.utils.api_utils import get_json_result
from common import settings
from common.misc_utils import thread_pool_exec

manager = Blueprint("rest_flow_app", __name__)

logger = logging.getLogger(__name__)

_SCOPES = ("todo", "initiated", "joined", "all")


def _err(msg: str, code: int = 100):
    return get_json_result(code=code, message=msg)


def _flow_dict(flow_id: str):
    return FlowInstanceService.get_flow(flow_id)


def _require_participant(flow) -> dict:
    if not flow:
        raise LookupError("流程不存在")
    if not FlowWorkflow.can_view(flow, current_user.id):
        raise PermissionError("无权访问该流程")
    return flow


def _require_owner(flow) -> dict:
    _require_participant(flow)
    if FlowWorkflow.owner_of_current(flow) != current_user.id:
        raise PermissionError("当前不在你的节点上，无法操作")
    return flow


def _safe_filename(name: str) -> str:
    """剥掉路径分隔符与不可打印控制字符，避免 object name 被前端文件名注入子目录；超长时保留扩展名截断。"""
    base = os.path.basename((name or "").replace("\\", "/"))
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    if not cleaned:
        return "unnamed"
    if len(cleaned) > 200:
        root, ext = os.path.splitext(cleaned)
        digest = hashlib.md5(cleaned.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{root[:160]}_{digest}{ext}"
    return cleaned


def _others_of(flow: dict, me: str) -> list:
    return [uid for uid in (flow["initiator_id"], flow["leader_id"], flow["handler_id"]) if uid != me]


def _nickname_of(uid: str) -> str:
    """通知文案用昵称展示，查不到时退回原始 id。"""
    try:
        u = User.get_or_none(User.id == uid)
        return (u.nickname or uid) if u else uid
    except Exception:
        return uid


def _action_error(e: Exception):
    """业务异常分级：PermissionError→403 / RuntimeError(乐观锁冲突)→409 / ValueError→100。"""
    if isinstance(e, PermissionError):
        return _err(str(e), 403)
    if isinstance(e, RuntimeError):
        return _err(str(e), 409)
    if isinstance(e, ValueError):
        return _err(str(e))
    return None


# ── 1. 创建流程 ────────────────────────────────────────────────────
@manager.route("/flow", methods=["POST"])  # noqa: F821
@login_required
async def create_flow():
    try:
        form = await request.form
        files = await request.files
        title = (form.get("title") or "").strip()
        leader_id = (form.get("leader_id") or "").strip()
        handler_id = (form.get("handler_id") or "").strip()
        file = files.get("file")
        if not title:
            return _err("标题不能为空", 101)
        if not leader_id:
            return _err("请选择领导", 101)
        if not handler_id:
            return _err("请选择处理人", 101)
        # 初始文件可选：不传则创建无版本的流程，由后续节点补传
        blob = None
        file_name = ""
        if file is not None and file.filename:
            # Quart FileStorage.read() 是同步方法（返回 bytes），放线程池避免阻塞事件循环
            blob = await thread_pool_exec(file.read)
            if not blob:
                return _err("文件内容为空", 101)
            file_name = _safe_filename(file.filename)
        if leader_id == current_user.id or handler_id == current_user.id:
            return _err("领导和处理人不能是发起人自己", 101)
        if leader_id == handler_id:
            return _err("领导和处理人不能是同一个人", 101)
        for uid, label in ((leader_id, "领导"), (handler_id, "处理人")):
            u = User.get_or_none(User.id == uid)
            if not u or u.status != "1":
                return _err(f"所选{label}不存在或已停用", 101)

        flow = FlowInstanceService.insert(
            title=title,
            initiator_id=current_user.id,
            leader_id=leader_id,
            handler_id=handler_id,
            status="initiator",
            current_version_id="",
        )
        flow_id = flow.id

        if blob is None:
            return get_json_result(data={"id": flow_id, "version": None})

        object_name = f"flow/{flow_id}/v1_{file_name}"
        try:
            await thread_pool_exec(settings.STORAGE_IMPL.put, _bucket_of(flow.__data__), object_name, blob)
        except Exception as e:
            logger.exception("flow storage put failed, rollback flow row %s", flow_id)
            FlowInstance.delete().where(FlowInstance.id == flow_id).execute()
            return _err(f"文件存储失败: {e}")
        flow_dict = _flow_dict(flow_id)
        try:
            version = FlowVersionService.add_version(
                flow_dict, object_name, file_name, file.mimetype or "", len(blob),
                "manual_upload", current_user.id,
            )
        except Exception as e:
            logger.exception("flow add_version failed, rollback flow row %s", flow_id)
            FlowInstance.delete().where(FlowInstance.id == flow_id).execute()
            return _err(f"版本记录失败: {e}")
        return get_json_result(data={"id": flow_id, "version": version})
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 2. 流程列表 ────────────────────────────────────────────────────
@manager.route("/flow/list", methods=["GET"])  # noqa: F821
@login_required
async def list_flows():
    try:
        scope = request.args.get("scope", "all")
        if scope not in _SCOPES:
            return _err(f"非法 scope: {scope}，可选值 todo/initiated/joined/all", 101)
        items, total = FlowInstanceService.list_for_user(current_user.id, scope)
        return get_json_result(data={"list": items, "total": total})
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 3. 流程详情 ────────────────────────────────────────────────────
@manager.route("/flow/<flow_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        return get_json_result(data={
            "flow": flow,
            "versions": FlowVersionService.list_by_flow(flow_id),
            "comments": FlowCommentService.list_by_flow(flow_id),
            "ai_chats": FlowAiChatService.list_by_flow(flow_id),
            "viewer": {
                "is_owner": FlowWorkflow.owner_of_current(flow) == current_user.id,
                "is_initiator": flow["initiator_id"] == current_user.id,
            },
        })
    except LookupError as e:
        return _err(str(e), 404)
    except PermissionError as e:
        return _err(str(e), 403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 3.1 流程参与人候选（所有启用用户，仅登录即可；发起流程选领导/处理人用） ──
@manager.route("/flow/candidates", methods=["GET"])  # noqa: F821
@login_required
async def flow_candidates():
    try:
        users = (
            User.select(User.id, User.nickname, User.email)
            .where(User.status == "1")
            .order_by(User.create_time)
        )
        return get_json_result(
            data={
                "list": [
                    {"id": u.id, "nickname": u.nickname or u.id}
                    for u in users
                ]
            }
        )
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 4. 追加新版本 ─────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/version", methods=["POST"])  # noqa: F821
@login_required
async def upload_version(flow_id: str):
    try:
        flow = _require_owner(_flow_dict(flow_id))
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        files = await request.files
        file = files.get("file")
        if file is None or not file.filename:
            return _err("请上传文件", 101)
        # 同 create_flow：FileStorage.read() 同步，放线程池
        blob = await thread_pool_exec(file.read)
        if not blob:
            return _err("文件内容为空", 101)

        file_name = _safe_filename(file.filename)
        # 外层先算一次 version_no 仅用于拼 object_name；add_version 内部会再算一次，
        # 无并发时两次结果一致（唯一索引 flow_id+version_no 兜底极端并发）。
        no = FlowVersionService.next_version_no(flow_id)
        object_name = f"flow/{flow_id}/v{no}_{file_name}"
        await thread_pool_exec(settings.STORAGE_IMPL.put, _bucket_of(flow), object_name, blob)
        version = FlowVersionService.add_version(
            flow, object_name, file_name, file.mimetype or "", len(blob),
            "manual_upload", current_user.id,
        )
        return get_json_result(data={"version": version})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 5. 下载版本文件 ───────────────────────────────────────────────
@manager.route("/flow/<flow_id>/version/<version_id>/download", methods=["GET"])  # noqa: F821
@login_required
async def download_version(flow_id: str, version_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        version = next((v for v in FlowVersionService.list_by_flow(flow_id) if v["id"] == version_id), None)
        if not version:
            return _err("版本不存在", 404)
        blob = await thread_pool_exec(settings.STORAGE_IMPL.get, _bucket_of(flow), version["file_path"])
        file_name = version["file_name"] or "file"
        return Response(
            blob,
            mimetype=version["file_type"] or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
        )
    except LookupError as e:
        return _err(str(e), 404)
    except PermissionError as e:
        return _err(str(e), 403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 6. 添加批注 ───────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/comment", methods=["POST"])  # noqa: F821
@login_required
async def add_comment(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        body = await request.get_json(silent=True) or {}
        content = (body.get("content") or "").strip()
        if not content:
            return _err("批注内容不能为空", 101)
        version_id = body.get("version_id") or flow["current_version_id"]
        if not version_id:
            return _err("流程暂无文件版本，无法批注", 101)
        # Word 式批注锚点：选中的原文选段 + 段落 index（均可空）
        anchor_text = (body.get("anchor_text") or "").strip()[:500]
        anchor_para = body.get("anchor_para")
        if anchor_para is not None and not isinstance(anchor_para, int):
            try:
                anchor_para = int(anchor_para)
            except (TypeError, ValueError):
                anchor_para = None

        comment = FlowCommentService.add_comment(
            flow_id, version_id, current_user.id, content,
            anchor_text=anchor_text, anchor_para=anchor_para,
        )
        others = _others_of(flow, current_user.id)
        try:
            notify_flow_event(
                flow, others,
                f"流程「{flow['title']}」有新批注",
                f"{_nickname_of(current_user.id)} 添加了批注意见",
            )
        except Exception as e:
            logger.warning("flow notify failed: %s", e)
        return get_json_result(data={"comment": comment})
    except LookupError as e:
        return _err(str(e), 404)
    except PermissionError as e:
        return _err(str(e), 403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 7. AI 处理记录 ────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/ai-record", methods=["POST"])  # noqa: F821
@login_required
async def add_ai_record(flow_id: str):
    try:
        flow = _require_owner(_flow_dict(flow_id))
        body = await request.get_json(silent=True) or {}
        instruction = (body.get("instruction") or "").strip()
        response = (body.get("response") or "").strip()
        if not instruction:
            return _err("指令内容不能为空", 101)
        if not response:
            return _err("AI 回复内容不能为空", 101)
        version_id = body.get("version_id") or flow["current_version_id"]
        if not version_id:
            return _err("流程暂无文件版本，无法记录 AI 处理", 101)
        session_id = body.get("session_id") or ""
        save_as_version = body.get("save_as_version", True)

        output_version_id = ""
        if save_as_version:
            # 秒级时间戳同秒多次保存会撞名覆盖，追加 8 位随机后缀兜底
            object_name = f"flow/{flow_id}/ai_{int(time.time())}_{uuid.uuid4().hex[:8]}.md"
            blob = response.encode("utf-8")
            await thread_pool_exec(settings.STORAGE_IMPL.put, _bucket_of(flow), object_name, blob)
            version = FlowVersionService.add_version(
                flow, object_name,
                f"AI产出_{time.strftime('%Y%m%d_%H%M%S')}.md",
                "text/markdown", len(blob), "ai_output", current_user.id,
            )
            output_version_id = version["id"]

        record = FlowAiChatService.add_record(
            flow_id, version_id, instruction, response, session_id, output_version_id,
        )
        return get_json_result(data={"record": record, "output_version_id": output_version_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 8. 流转 / 退回 ────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/submit", methods=["POST"])  # noqa: F821
@login_required
async def submit_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        body = await request.get_json(silent=True) or {}
        action = body.get("action")
        if action not in ("next", "return"):
            return _err("action 必须是 next 或 return", 101)
        updated = FlowActionService.submit(flow, current_user.id, action)
        try:
            notify_target_of(updated, action)
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


# ── 9. 归档 ───────────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/archive", methods=["POST"])  # noqa: F821
@login_required
async def archive_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        updated = FlowActionService.archive(flow, current_user.id)
        try:
            notify_target_of(updated, "archive")
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


# ── 10. 作废 ──────────────────────────────────────────────────────
@manager.route("/flow/<flow_id>/cancel", methods=["POST"])  # noqa: F821
@login_required
async def cancel_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        updated = FlowActionService.cancel(flow, current_user.id)
        try:
            notify_target_of(updated, "cancel")
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
