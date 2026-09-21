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
  - GET    /flow/list?scope=&status=                流程列表（scope: todo/initiated/joined/all；status: finished/archived/cancelled/deleted 管理页过滤）
  - GET    /flow/<flow_id>                          流程详情（版本/批注/AI记录/视角）
  - POST   /flow/<flow_id>/version                  追加新版本（人工上传）
  - GET    /flow/<flow_id>/version/<version_id>/download  下载某版本文件
  - POST   /flow/<flow_id>/version/<version_id>/delete    删除版本（仅领导，批注一并删）
  - POST   /flow/<flow_id>/comment                  添加批注意见（通知其他参与人）
  - POST   /flow/<flow_id>/ai-record                记录一次 AI 处理（回复可落为新版本）
  - POST   /flow/<flow_id>/document/edit            编辑文档段落（存为新版本，仅当前节点负责人）
  - POST   /flow/<flow_id>/submit                   流转（next）/ 退回（return）
  - POST   /flow/<flow_id>/archive                  归档（仅汇总节点发起人）
  - POST   /flow/<flow_id>/cancel                   作废（仅发起人）
  - POST   /flow/<flow_id>/soft-delete              软删除（仅发起人；仅终态；可恢复）
  - POST   /flow/<flow_id>/restore                  回收站恢复（仅发起人）
  - POST   /flow/<flow_id>/reactivate               重新激活（仅发起人；状态回 initiator）
"""
import json
import logging
import time
import uuid
from urllib.parse import quote

from agent.canvas import Canvas
from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.db_models import FlowInstance, User
from api.db.services.api_service import API4ConversationService
from api.db.services.canvas_service import UserCanvasService
from api.db.services.file_service import FileService
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
from api.db.services.user_canvas_version import UserCanvasVersionService
from api.utils.api_utils import get_json_result
from api.utils.docx_edit import (
    edit_docx_blob,
    parse_edit_payload,
    safe_filename as _safe_filename,
)
from common import settings
from common.misc_utils import get_uuid, thread_pool_exec

manager = Blueprint("rest_flow_app", __name__)

logger = logging.getLogger(__name__)

_SCOPES = ("todo", "initiated", "joined", "all", "admin")
_LIST_STATUS = ("finished", "archived", "cancelled", "deleted")
_ADMIN_STATUS = ("running", "archived", "cancelled", "deleted")


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


def _require_viewer(flow) -> dict:
    """读端点专用：参与人或超级管理员可读（超管「全部流程」视图）。
    写端点仍走 _require_participant/_require_owner，不放大超管写权限。"""
    if not flow:
        raise LookupError("流程不存在")
    if FlowWorkflow.can_view(flow, current_user.id):
        return flow
    if bool(getattr(current_user, "is_superuser", False)):
        return flow
    raise PermissionError("无权访问该流程")


def _require_owner(flow) -> dict:
    _require_participant(flow)
    if FlowWorkflow.owner_of_current(flow) != current_user.id:
        raise PermissionError("当前不在你的节点上，无法操作")
    return flow


# 版本来源白名单：manual_upload=发起/负责人手动上传；ai_template_fill=AI 范本填写成稿落版本
_VERSION_SOURCES = ("manual_upload", "ai_template_fill")


def _normalize_version_source(raw: str | None) -> str:
    return raw if raw in _VERSION_SOURCES else "manual_upload"


# 版本内容纯文本截断上限 + FileService.parse 输出的固定文件头前缀
_VERSION_CONTENT_MAX = 20000

_PARSE_HEADER_PREFIX = "\n -----------------\nFile: "


def _clean_version_text(text: str) -> str:
    """剥离 FileService.parse 输出的固定头并截断（_VERSION_CONTENT_MAX）。"""
    # 剥掉 parse 输出的固定文件头，只留正文
    if text and text.startswith(_PARSE_HEADER_PREFIX):
        head, sep, body = text.partition("Content as following: \n")
        text = body if sep else text
    return (text or "")[:_VERSION_CONTENT_MAX]


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
            return _err(f"非法 scope: {scope}，可选值 todo/initiated/joined/all/admin", 101)
        status = (request.args.get("status") or "").strip()
        allowed_status = _ADMIN_STATUS if scope == "admin" else _LIST_STATUS
        if status and status not in allowed_status:
            return _err(f"非法 status: {status}，可选值 {'/'.join(allowed_status)}", 101)
        if scope == "admin":
            # 全部流程：仅超级管理员，返回系统内所有用户的全部流程
            if not bool(getattr(current_user, "is_superuser", False)):
                return _err("仅超级管理员可查看全部流程", 403)
            items, total = FlowInstanceService.list_all(status)
        else:
            items, total = FlowInstanceService.list_for_user(current_user.id, scope, status)
        return get_json_result(data={"list": items, "total": total})
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 3. 流程详情 ────────────────────────────────────────────────────
@manager.route("/flow/<flow_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_flow(flow_id: str):
    try:
        flow = _require_viewer(_flow_dict(flow_id))
        return get_json_result(data={
            "flow": flow,
            "versions": FlowVersionService.list_by_flow(flow_id),
            "comments": FlowCommentService.list_by_flow(flow_id),
            "ai_chats": FlowAiChatService.list_by_flow(flow_id),
            "viewer": {
                "is_owner": FlowWorkflow.owner_of_current(flow) == current_user.id,
                "is_initiator": flow["initiator_id"] == current_user.id,
                "is_leader": flow["leader_id"] == current_user.id,
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
        # AI 面板「存为流程版本」与手动上传共用本端点；source 白名单防脏标注
        form = await request.form
        source = _normalize_version_source(form.get("source"))
        # 外层先算一次 version_no 仅用于拼 object_name；add_version 内部会再算一次，
        # 无并发时两次结果一致（唯一索引 flow_id+version_no 兜底极端并发）。
        no = FlowVersionService.next_version_no(flow_id)
        object_name = f"flow/{flow_id}/v{no}_{file_name}"
        await thread_pool_exec(settings.STORAGE_IMPL.put, _bucket_of(flow), object_name, blob)
        version = FlowVersionService.add_version(
            flow, object_name, file_name, file.mimetype or "", len(blob),
            source, current_user.id,
        )
        return get_json_result(data={"version": version})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 4.1 删除版本（仅领导；批注一并删，current 回退剩余最高版） ────
@manager.route("/flow/<flow_id>/version/<version_id>/delete", methods=["POST"])  # noqa: F821
@login_required
async def delete_version(flow_id: str, version_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        if flow["leader_id"] != current_user.id:
            return _err("仅审核领导可删除版本", 403)
        version = next(
            (v for v in FlowVersionService.list_by_flow(flow_id) if v["id"] == version_id), None
        )
        if not version:
            return _err("版本不存在", 404)
        result = FlowVersionService.delete_version(flow_id, version_id)
        # 存储对象 best-effort 清理：失败不影响版本删除结果（孤儿对象可后续清理）
        try:
            await thread_pool_exec(settings.STORAGE_IMPL.rm, _bucket_of(flow), result["file_path"])
        except Exception as e:
            logger.warning("flow version storage rm failed: %s", e)
        try:
            notify_flow_event(
                flow, _others_of(flow, current_user.id),
                f"流程「{flow['title']}」删除了版本 v{version['version_no']}",
                f"{_nickname_of(current_user.id)} 删除了版本 {version['file_name']}"
                + ("，当前版本已回退" if result["new_current_version_id"] else ""),
            )
        except Exception as e:
            logger.warning("flow notify failed: %s", e)
        return get_json_result(data={"id": version_id, "new_current_version_id": result["new_current_version_id"]})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


# ── 4.2 编辑文档段落（存为新版本） ────────────────────────────────
@manager.route("/flow/<flow_id>/document/edit", methods=["POST"])  # noqa: F821
@login_required
async def edit_document(flow_id: str):
    """Word 式正文编辑：按 /files/<id>/content 返回的 para_index 定位 docx 段落，
    支持三类操作——edits 改写段落文本 / deletes 删除段落 / inserts 新增段落
    （after_para_index=-1 表示插到文档开头，否则插到该段之后），全部基于原
    para_index，定位成功后统一应用，存为新版本（source=manual_edit）。
    表格支持 table_edits 单元格级改写（不可删除整表/不可增删行列）；图片为原子块不可改写。"""
    try:
        flow = _require_owner(_flow_dict(flow_id))
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        body = await request.get_json(silent=True) or {}
        version_id = body.get("version_id") or flow["current_version_id"]
        if not version_id:
            return _err("流程暂无文件版本，无法编辑", 101)

        try:
            parsed = parse_edit_payload(body)
        except ValueError as ve:
            return _err(str(ve), 101)

        version = next(
            (v for v in FlowVersionService.list_by_flow(flow_id) if v["id"] == version_id), None
        )
        if not version:
            return _err("版本不存在", 404)
        # 允许以任意版本为底稿编辑（版本记录中选中历史版本 → 文件审核 → 修改），
        # 结果以 add_version 增量落成最新版，不覆盖/回滚任何已有版本；
        # 并发防护由前端「文档已变化请刷新」提示 + 版本时间线只增不改语义保证

        blob = await thread_pool_exec(settings.STORAGE_IMPL.get, _bucket_of(flow), version["file_path"])
        if not blob:
            return _err("版本文件读取失败", 101)

        try:
            new_blob, root, converted = await thread_pool_exec(
                edit_docx_blob, blob, version["file_name"] or "document", parsed
            )
        except ValueError as ve:
            return _err(str(ve), 101)

        file_name = (
            f"{_safe_filename(root)}.docx" if converted else f"{_safe_filename(root)}_edited.docx"
        )
        no = FlowVersionService.next_version_no(flow_id)
        object_name = f"flow/{flow_id}/v{no}_{file_name}"
        await thread_pool_exec(settings.STORAGE_IMPL.put, _bucket_of(flow), object_name, new_blob)
        new_version = FlowVersionService.add_version(
            flow, object_name, file_name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            len(new_blob), "manual_edit", current_user.id,
        )
        return get_json_result(data={"version": new_version})
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
        flow = _require_viewer(_flow_dict(flow_id))
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


# ── 5.1 版本内容纯文本（三角色可读；模板填写证据注入等轻量用途） ──
@manager.route("/flow/<flow_id>/version/<version_id>/content", methods=["GET"])  # noqa: F821
@login_required
async def version_content(flow_id: str, version_id: str):
    """版本文本提取（服务端化）：供流程 AI 对话附带当前版本作填写证据。
    解析失败返回空文本 + warning，不阻断（前端回退纯 KB 检索）。"""
    try:
        flow = _require_viewer(_flow_dict(flow_id))
        version = next(
            (v for v in FlowVersionService.list_by_flow(flow_id) if v["id"] == version_id),
            None)
        if not version:
            return _err("版本不存在", 404)
        blob = await thread_pool_exec(
            settings.STORAGE_IMPL.get, _bucket_of(flow), version["file_path"])
        if not blob:
            return get_json_result(data={"content": ""})

        # current_user 是请求上下文 LocalProxy，不随 thread_pool_exec 线程传播，
        # 必须在线程外捕获 uid 再显式传入（parse 对显式 tenant_id 短路，不触碰 current_user）
        uid = current_user.id

        def _extract_text() -> str:
            try:
                # FileService.parse(filename, blob, img_base64, tenant_id)：
                # img_base64=False 避免图片文件走 base64 分支
                text = FileService.parse(version["file_name"], blob, False, uid)
            except Exception:
                logger.warning("flow version content parse failed: %s",
                               version["file_name"], exc_info=True)
                return ""
            return _clean_version_text(text)

        text = await thread_pool_exec(_extract_text)
        return get_json_result(data={"content": text})
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
        # 批注意见锚定的是文档内容（anchor_text/para）；文档可经对话上传进入流程
        # （不经版本通道，如文件审核目标），版本只是意见产生时的文档引用——
        # 为空时存空串视为「流程级意见」，不再以「暂无文件版本」拒绝
        version_id = body.get("version_id") or flow["current_version_id"] or ""
        # Word 式批注锚点：选中的原文选段 + 段落 index + 段落内起始偏移（均可空）
        anchor_text = (body.get("anchor_text") or "").strip()[:500]
        anchor_para = body.get("anchor_para")
        if anchor_para is not None and not isinstance(anchor_para, int):
            try:
                anchor_para = int(anchor_para)
            except (TypeError, ValueError):
                anchor_para = None
        anchor_start = body.get("anchor_start")
        if anchor_start is not None and not isinstance(anchor_start, int):
            try:
                anchor_start = int(anchor_start)
            except (TypeError, ValueError):
                anchor_start = None
        # 批注级别：high/medium/low，非法值兜底 medium（Service 层同款兜底双保险）
        severity = body.get("severity") or "medium"
        if severity not in ("high", "medium", "low"):
            severity = "medium"

        comment = FlowCommentService.add_comment(
            flow_id, version_id, current_user.id, content,
            anchor_text=anchor_text, anchor_para=anchor_para,
            anchor_start=anchor_start, severity=severity,
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


# ── 6.1 删除批注（仅批注作者本人） ────────────────────────────────
@manager.route("/flow/<flow_id>/comment/<comment_id>/delete", methods=["POST"])  # noqa: F821
@login_required
async def delete_comment(flow_id: str, comment_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        if flow["status"] in FlowWorkflow.TERMINAL:
            return _err("流程已结束")
        comment = FlowCommentService.get_comment(comment_id)
        if not comment or comment["flow_id"] != flow_id:
            return _err("批注不存在", 404)
        if comment["user_id"] != current_user.id:
            return _err("只能删除自己的批注", 403)
        FlowCommentService.delete_comment(comment_id)
        return get_json_result(data={"id": comment_id})
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
        save_as_version = body.get("save_as_version", True)
        record_id = body.get("record_id") or ""

        existing = None
        if record_id:
            existing = FlowAiChatService.get_record(record_id)
            if not existing or existing["flow_id"] != flow_id:
                return _err("AI 记录不存在", 404)
            if not save_as_version:
                # 回填更新模式（发送即存）：流式结束后把发送时预落的
                # 「（生成中…）」占位记录回填为最终回复/事件序列，不重复插记录。
                # 仅记录本人可回填；instruction/version 以发送时为准不覆盖。
                if existing.get("user_id") != current_user.id:
                    return _err("无权更新该记录", 403)
                response = (body.get("response") or "").strip()
                if not response:
                    return _err("AI 回复内容不能为空", 101)
                tpl_events = body.get("template_fill_events") or ""
                if not isinstance(tpl_events, str):
                    tpl_events = json.dumps(tpl_events, ensure_ascii=False)
                file_review = body.get("file_review") or ""
                if not isinstance(file_review, str):
                    file_review = json.dumps(file_review, ensure_ascii=False)
                # 列宽 CharField(255)：超长入参就地截断，防 DB 报错炸掉整条回填
                file_review = file_review[:255]
                FlowAiChatService.update_content(
                    record_id, response,
                    session_id=(body.get("session_id") or "").strip() or None,
                    template_fill_events=tpl_events or None,
                    file_review=file_review or None,
                )
                return get_json_result(
                    data={"record": FlowAiChatService.get_record(record_id),
                          "output_version_id": ""})
            instruction = existing["instruction"]
            response = existing["response"]
            version_id = existing["version_id"] or flow["current_version_id"]
            session_id = existing["session_id"]
        else:
            instruction = (body.get("instruction") or "").strip()
            response = (body.get("response") or "").strip()
            if not instruction:
                return _err("指令内容不能为空", 101)
            if not response:
                return _err("AI 回复内容不能为空", 101)
            version_id = body.get("version_id") or flow["current_version_id"]
            session_id = body.get("session_id") or ""
        user_id = current_user.id
        template_fill_events = body.get("template_fill_events") or ""
        if not isinstance(template_fill_events, str):
            template_fill_events = json.dumps(template_fill_events, ensure_ascii=False)
        # 随消息上传的附件（用户气泡 chip 展示用）：白名单只取 id/name，防注入/超大；
        # 上限 10 个、名称截 255。发送时事实——只随新增（预存占位）落库，回填不覆盖。
        raw_chat_files = body.get("files")
        chat_files: list = []
        if isinstance(raw_chat_files, list):
            for f in raw_chat_files[:10]:
                if isinstance(f, dict) and f.get("id"):
                    chat_files.append(
                        {"id": str(f["id"])[:64],
                         "name": str(f.get("name") or "")[:255]})
        files_json = json.dumps(chat_files, ensure_ascii=False) if chat_files else ""
        # 文件审核进度卡：记录级 {file_id,task_id}，刷新后历史气泡按它挂进度卡
        file_review = body.get("file_review") or ""
        if not isinstance(file_review, str):
            file_review = json.dumps(file_review, ensure_ascii=False)
        file_review = file_review[:255]
        # 无版本流程（创建时未带初始文件）允许记录 AI 处理：version_id 留空不锚定
        # 版本；范本填写成稿卡/后续上传会建出第一个版本，记录不因此丢失
        # （原实现直接报错「流程暂无文件版本」，导致对话与 template_fill_events
        # 不落库、刷新后成稿卡回放丢失）

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

        if existing:
            FlowAiChatService.set_output_version(record_id, output_version_id)
            record = FlowAiChatService.get_record(record_id)
        else:
            record = FlowAiChatService.add_record(
                flow_id, version_id, instruction, response, session_id, output_version_id,
                user_id=user_id, template_fill_events=template_fill_events,
                file_review=file_review, files=files_json,
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


# ── 11. 删除流程（仅发起人；仅已作废；级联删版本/批注/AI记录 + 存储对象清理） ──
@manager.route("/flow/<flow_id>/delete", methods=["POST"])  # noqa: F821
@login_required
async def delete_flow(flow_id: str):
    try:
        flow = _require_participant(_flow_dict(flow_id))
        paths = FlowActionService.delete_flow(flow, current_user.id)
        # 存储对象 best-effort 清理：失败不影响删除结果（孤儿对象可后续清理）
        bucket = _bucket_of(flow)
        for p in paths:
            try:
                await thread_pool_exec(settings.STORAGE_IMPL.rm, bucket, p)
            except Exception as e:
                logger.warning("flow storage rm failed: %s", e)
        return get_json_result(data={"id": flow_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))


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


# ── 7.1 流程对话影子会话（source='flow'，对话页签不可见） ──────────
@manager.route("/flow/<flow_id>/chat/session", methods=["POST"])  # noqa: F821
@login_required
async def create_flow_chat_session(flow_id: str):
    """为当前用户创建流程专属 agent 会话（conversation.source='flow'）。
    影子会话仅作画布多轮续聊的运行时缓存；权威对话记录在 flow_ai_chat。
    注：本端点用 _require_viewer（读级口径）而非 _require_participant——
    建影子会话性质近读（会话归属强制 current_user.id，无越权面）。"""
    try:
        flow = _require_viewer(_flow_dict(flow_id))
        body = await request.get_json(silent=True) or {}
        agent_id = (body.get("agent_id") or "").strip()
        if not agent_id:
            return _err("缺少 agent_id", 101)
        # 与 agent_api.create_agent_session（agent_api.py:232-268）同款初始化，
        # get_agent_dsl_with_release 返回 (cvs, dsl)，内部处理 agent 不存在（LookupError）
        cvs, dsl = UserCanvasService.get_agent_dsl_with_release(
            agent_id, False, current_user.id)
        canvas = Canvas(dsl, cvs.user_id, agent_id, canvas_id=cvs.id)
        canvas.globals["sys.user_id"] = current_user.id
        canvas.reset()
        session_id = get_uuid()
        conv = {
            "id": session_id,
            "name": f"流程：{flow['title']}"[:60],
            "dialog_id": cvs.id,
            "user_id": current_user.id,
            "exp_user_id": current_user.id,
            "message": [{"role": "assistant", "content": canvas.get_prologue()}],
            "source": "flow",
            "dsl": json.loads(str(canvas)),
            "reference": [],
            "version_title": UserCanvasVersionService.get_latest_version_title(cvs.id),
        }
        API4ConversationService.save(**conv)
        return get_json_result(data={"session_id": session_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
