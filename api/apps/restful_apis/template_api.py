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
"""模板填写：模板管理 API（P1）。路由前缀 /api/v1/template/fill/*"""
import asyncio
import io
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
import zipfile

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.db_models import DB
from api.db.services.template_fill_service import (
    TplFillTaskService,
    TplTemplateService,
    TplTemplateVersionService,
)
from api.utils.api_utils import get_error_data_result, get_result
from common import settings
from common.misc_utils import get_uuid
from rag.svr.template_fill.detector import detect_fill_points, validate_placeholders

logger = logging.getLogger(__name__)

manager = Blueprint("rest_template_fill_app", __name__)

MAX_TEMPLATE_SIZE = 20 * 1024 * 1024
PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 填写任务线程防重入：同一任务同时最多一个执行线程（spawn 时 add、线程 finally discard）
_running_lock = threading.Lock()
_running_tasks: set = set()

# 可重试的终态：仅失败/部分完成的任务允许重试
_RETRYABLE_STATUSES = ("failed", "partial")


def _spawn_fill_task(task_id: str):
    """起 daemon 线程跑填写 pipeline（executor 延迟 import，照本文件其他延迟 import 惯例）。
    线程内异常自行兜底，executor.execute_task 内部已把崩溃任务置 failed。"""
    with _running_lock:
        if task_id in _running_tasks:
            return
        _running_tasks.add(task_id)

    def _run():
        try:
            from rag.svr.template_fill.executor import execute_task
            execute_task(task_id)
        except Exception:  # executor 内部已兜底，此处是最后防线，防线程静默死
            logger.exception("fill task thread crashed, task_id=%s", task_id)
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    try:
        threading.Thread(target=_run, daemon=True, name=f"tpl-fill-{task_id[:8]}").start()
    except Exception:
        # 线程启动失败（如线程数耗尽）：add 已执行而线程内 finally 永不会跑，
        # task_id 会永久滞留集合导致 retry 恒报「任务正在执行中」。
        # 故锁内 discard + 任务行 CAS 置 failed（照 retry 复位写法，包连接上下文）供重试。
        with _running_lock:
            _running_tasks.discard(task_id)
        logger.exception("fill task thread start failed, task_id=%s", task_id)
        with DB.connection_context():
            TplFillTaskService.model.update(
                status="failed", error="任务调度失败：后台线程启动异常，请重试").where(
                TplFillTaskService.model.id == task_id,
                TplFillTaskService.model.status == "pending").execute()


def _file_type_of(filename: str):
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".xlsx"):
        return "xlsx"
    return None


def _is_legacy_doc(filename: str) -> bool:
    """是否为旧版 .doc（二进制 Word）文件。endswith(".doc") 不会误伤 .docx
    （"a.docx".endswith(".doc") 为 False）。"""
    return (filename or "").lower().endswith(".doc")


def _convert_doc_to_docx(blob: bytes) -> bytes:
    """旧版 .doc（二进制 Word）转 .docx。容器内有 LibreOffice；用独立
    UserInstallation 目录避免并发/首启 profile 锁冲突。容器 soffice 包装
    脚本未自设库路径，须显式注入 LD_LIBRARY_PATH（否则 soffice.bin 报
    libreglo.so cannot open shared object file, rc=127）。"""
    env = {**os.environ, "LD_LIBRARY_PATH": "/usr/lib/libreoffice/program"}
    with tempfile.TemporaryDirectory(prefix="tpl_doc_") as tmp:
        src = os.path.join(tmp, "input.doc")
        with open(src, "wb") as f:
            f.write(blob)
        profile = f"file://{tmp}/lo_profile_{uuid.uuid4().hex}"
        r = subprocess.run(
            ["soffice", "--headless", "--norestore", f"-env:UserInstallation={profile}",
             "--convert-to", "docx", "--outdir", tmp, src],
            capture_output=True, timeout=60, check=False, env=env)
        out = os.path.join(tmp, "input.docx")
        if not os.path.exists(out):
            logger.error("doc->docx convert failed: rc=%s stderr=%s",
                         r.returncode, (r.stderr or b"")[:500])
            raise RuntimeError("doc convert failed")
        with open(out, "rb") as f:
            return f.read()


def _extract_candidates(file_type: str, blob: bytes):
    if file_type == "docx":
        from rag.svr.template_fill.docx_utils import extract_docx_candidates
        return extract_docx_candidates(blob)
    from rag.svr.template_fill.xlsx_utils import extract_xlsx_candidates
    return extract_xlsx_candidates(blob)


def _load_latest_blob(template_id: str):
    """读取最新版本的原模板 blob；版本缺失/存储对象缺失统一在此收口。

    MinIO conn.get 对象不存在/读取失败返回 None，直接透传会给下游 500。
    返回 (blob, error_response)，成功时 error_response 为 None。
    """
    ver = TplTemplateVersionService.latest(template_id)
    if not ver:
        return None, get_error_data_result("模板版本缺失")
    blob = settings.STORAGE_IMPL.get(template_id, ver.original_file_id)
    if not blob:
        return None, get_error_data_result("模板文件缺失")
    return blob, None


async def _load_template(template_id: str):
    tpl = TplTemplateService.get_owned(template_id, current_user.id)
    if not tpl:
        return None, get_error_data_result("模板不存在")
    return tpl, None


@manager.route("/template/fill/upload", methods=["POST"])
@login_required
async def upload_template():
    files = await request.files
    form = await request.form
    file = files.get("file")
    if not file or not file.filename:
        return get_error_data_result("请上传 .docx / .doc / .xlsx 模板文件")
    file_type = _file_type_of(file.filename)
    is_legacy_doc = False
    if not file_type:
        if _is_legacy_doc(file.filename):
            # 旧版 .doc：后端转成 .docx 后以 docx 形态进入全链路（candidates/替换/预览/下载）
            is_legacy_doc = True
            file_type = "docx"
        else:
            return get_error_data_result("仅支持 .docx / .doc / .xlsx")
    # read 前先 seek 到尾部探实际大小，避免超大文件先整份读进内存（app 级 MAX_CONTENT_LENGTH 默认 1GB）
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size <= 0 or size > MAX_TEMPLATE_SIZE:
        return get_error_data_result("文件为空或超过 20MB")
    blob = file.read()
    if not blob or len(blob) > MAX_TEMPLATE_SIZE:
        return get_error_data_result("文件为空或超过 20MB")
    if is_legacy_doc:
        try:
            blob = _convert_doc_to_docx(blob)
        except Exception:
            logger.exception("legacy doc convert failed, filename=%s", file.filename)
            return get_error_data_result("旧版 .doc 转换失败，请用 Word 另存为 .docx 后重新上传")
        # docx 体积可能大于源文件，转换后复查一次，防止存储超限 blob 入库
        if len(blob) > MAX_TEMPLATE_SIZE:
            return get_error_data_result("文件为空或超过 20MB")
    # docx/xlsx 均为 zip 容器，轻量验证内容合法性，防后续解析抛 BadZipFile 500
    if not zipfile.is_zipfile(io.BytesIO(blob)):
        return get_error_data_result("文件已损坏或不是有效的 docx/xlsx 文件")
    name = (form.get("name") or "").strip()[:256] or file.filename.rsplit(".", 1)[0].strip()[:256]
    tpl_id = get_uuid()
    TplTemplateService.insert(id=tpl_id, name=name,
                              description=(form.get("description") or "")[:2000],
                              file_type=file_type, status="draft", latest_version=1,
                              tenant_id=current_user.id, created_by=current_user.id)
    TplTemplateVersionService.create_initial_version(tpl_id, file.filename, blob)
    return get_result(data={"id": tpl_id, "name": name, "file_type": file_type, "status": "draft"})


@manager.route("/template/fill/list", methods=["GET"])
@login_required
async def list_templates():
    # type=int：转换失败（如 ?page=abc）回退默认值，避免 int("abc") 500；负数/超界由 service 层钳制
    args = request.args
    rows, total = TplTemplateService.get_list_page(
        current_user.id, keyword=args.get("keyword", ""),
        status=args.get("status", ""), page=args.get("page", 1, type=int), size=args.get("size", 20, type=int))
    return get_result(data=rows, total=total)


@manager.route("/template/fill/detect", methods=["POST"])
@login_required
async def detect_placeholders():
    body = await request.get_json()
    tpl, err = await _load_template((body or {}).get("template_id", ""))
    if err:
        return err
    blob, err = _load_latest_blob(tpl.id)
    if err:
        return err
    try:
        candidates = _extract_candidates(tpl.file_type, blob)
    except Exception:
        logger.exception("extract candidates failed, template=%s", tpl.id)
        return get_error_data_result("模板文件损坏或无法解析")
    try:
        suggestions = await detect_fill_points(current_user.id, tpl.file_type, candidates)
    except Exception:
        logger.exception("detect fill points failed, template=%s", tpl.id)
        return get_error_data_result("AI 识别填写点失败，请重试或手动添加填写点")
    # LLM 可能返回字符串 "true"/"false"，统一规整为 bool，避免前端 truthy 判断出错
    for it in suggestions or []:
        req = it.get("required")
        it["required"] = req is True or (isinstance(req, str) and req.strip().lower() == "true")
    return get_result(data={"candidates": candidates, "suggestions": suggestions})


# 后台识别线程防重入：同一模板同时最多一个识别线程（与 _spawn_fill_task 同模式）。
# 进程重启后 _detecting 清空而 DB 可能残留 running → 重触发会重新入队自愈。
_detecting_lock = threading.Lock()
_detecting: set = set()


def _run_detect_task(template_id: str, tenant_id: str):
    """daemon 线程体：LLM 识别 → 校验 → 自动保存填写点 → 状态落 DB。

    仅对无已保存填写点的草稿模板开放（端点守卫），自动保存不会覆盖人工配置。
    所有失败路径必须置 failed（含线程级兜底），防模板永久卡 running；
    0 条识别结果也置 failed——列表显示「识别完成」但详情空白比显式失败更误导。
    """
    try:
        tpl = TplTemplateService.get_owned(template_id, tenant_id)
        if not tpl:
            TplTemplateService.set_detect_status(template_id, "failed", "模板不存在")
            return
        blob, err = _load_latest_blob(template_id)
        if err or not blob:
            TplTemplateService.set_detect_status(template_id, "failed", "模板文件缺失")
            return
        try:
            candidates = _extract_candidates(tpl.file_type, blob)
        except Exception:
            logger.exception("extract candidates failed, template=%s", template_id)
            TplTemplateService.set_detect_status(template_id, "failed", "模板文件损坏或无法解析")
            return
        try:
            # daemon 线程无事件循环，asyncio.run 新建 loop 跑异步 LLM 识别
            suggestions = asyncio.run(detect_fill_points(tenant_id, tpl.file_type, candidates))
        except Exception:
            logger.exception("detect fill points failed, template=%s", template_id)
            TplTemplateService.set_detect_status(template_id, "failed", "AI 识别失败，请重试或手动添加填写点")
            return
        for it in suggestions or []:
            req = it.get("required")
            it["required"] = req is True or (isinstance(req, str) and req.strip().lower() == "true")
        if not suggestions:
            TplTemplateService.set_detect_status(template_id, "failed", "未识别到填写点，请在详情页手动添加")
            return
        ok, err_msg = validate_placeholders(suggestions, candidates)
        if not ok:
            TplTemplateService.set_detect_status(template_id, "failed", f"识别结果校验未通过：{err_msg}")
            return
        svr_ok, info = TplTemplateVersionService.save_placeholders(tpl.to_dict(), suggestions)
        if not svr_ok:
            TplTemplateService.set_detect_status(template_id, "failed", str(info))
            return
        TplTemplateService.set_detect_status(template_id, "done")
    except Exception:
        logger.exception("background detect crashed, template=%s", template_id)
        try:
            TplTemplateService.set_detect_status(template_id, "failed", "识别线程异常，请重试")
        except Exception:  # 最后防线：状态写不进也不让线程静默死
            logger.exception("mark detect failed failed, template=%s", template_id)
    finally:
        with _detecting_lock:
            _detecting.discard(template_id)


@manager.route("/template/fill/detect-async", methods=["POST"])
@login_required
async def detect_placeholders_async():
    """触发后台 AI 识别：立即返回 running，前端列表轮询 detect_status 展示进度。

    仅限无已保存填写点的模板（上传向导的自动识别链路）：识别成功后自动保存
    为该模板填写点；已有填写点的模板请用同步 detect 端点在详情页人工确认合并，
    避免后台自动覆盖人工配置。重复触发幂等返回 running。
    """
    body = await request.get_json(silent=True) or {}
    tpl, err = await _load_template((body or {}).get("template_id", ""))
    if err:
        return err
    ver = TplTemplateVersionService.latest(tpl.id)
    if ver and ver.placeholders:
        return get_error_data_result("该模板已配置填写点，请到详情页使用「AI 识别」")
    with _detecting_lock:
        if tpl.id in _detecting:
            return get_result(data={"status": "running"})
        _detecting.add(tpl.id)
    try:
        TplTemplateService.set_detect_status(tpl.id, "running")
    except Exception:
        with _detecting_lock:
            _detecting.discard(tpl.id)
        raise
    try:
        threading.Thread(target=_run_detect_task, daemon=True,
                         name=f"tpl-detect-{tpl.id[:8]}",
                         args=(tpl.id, current_user.id)).start()
    except Exception:
        # 线程启动失败：回收标记并把状态置 failed，防卡 running
        with _detecting_lock:
            _detecting.discard(tpl.id)
        logger.exception("spawn detect thread failed, template=%s", tpl.id)
        TplTemplateService.set_detect_status(tpl.id, "failed", "识别任务启动失败，请重试")
        return get_error_data_result("识别任务启动失败，请重试")
    return get_result(data={"status": "running"})


@manager.route("/template/fill/<template_id>/save-placeholders", methods=["POST"])
@login_required
async def save_placeholders(template_id: str):
    body = await request.get_json()
    items = (body or {}).get("placeholders")
    if not isinstance(items, list) or not items:
        return get_error_data_result("placeholders 不能为空")
    tpl, err = await _load_template(template_id)
    if err:
        return err
    blob, err = _load_latest_blob(template_id)
    if err:
        return err
    # 已知双重读件：service.save_placeholders 会再 get+parse 一次，量级可接受
    try:
        candidates = _extract_candidates(tpl.file_type, blob)
    except Exception:
        logger.exception("extract candidates failed, template=%s", template_id)
        return get_error_data_result("模板文件损坏或无法解析")
    ok, err_msg = validate_placeholders(items, candidates)
    if not ok:
        return get_error_data_result(err_msg)
    svr_ok, info = TplTemplateVersionService.save_placeholders(tpl.to_dict(), items)
    if not svr_ok:
        return get_error_data_result(info)
    return get_result(data={"id": template_id, "placeholder_count": len(items)})


@manager.route("/template/fill/<template_id>/publish", methods=["POST"])
@login_required
async def publish_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    if tpl.status == "published":
        return get_result()  # 幂等：重复发布直接成功
    ver = TplTemplateVersionService.latest(template_id)
    if not ver or not ver.render_file_id:
        return get_error_data_result("请先保存填写点配置再发布")
    TplTemplateService.set_status(template_id, current_user.id, "published")
    return get_result()


@manager.route("/template/fill/<template_id>/disable", methods=["POST"])
@login_required
async def disable_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    if tpl.status == "draft":
        return get_error_data_result("草稿状态无需停用，可直接删除")
    TplTemplateService.set_status(template_id, current_user.id, "disabled")
    return get_result()


@manager.route("/template/fill/<template_id>", methods=["GET"])
@login_required
async def get_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    data = tpl.to_dict()
    data["placeholders"] = ver.placeholders if ver else []
    data["render_ready"] = bool(ver and ver.render_file_id)
    return get_result(data=data)


@manager.route("/template/fill/<template_id>", methods=["DELETE"])
@login_required
async def delete_template_endpoint(template_id: str):
    """删除模板：仅 draft/disabled 且无填写任务记录可删（守卫在 service 层）。"""
    ok, msg = TplTemplateService.delete_template(template_id, current_user.id)
    if not ok:
        return get_error_data_result(msg)
    return get_result()


BATCH_DELETE_MAX = 50


@manager.route("/template/fill/batch-delete", methods=["POST"])
@login_required
async def batch_delete_templates():
    """批量删除模板：逐个走 delete_template（守卫/事务复用单删），部分失败不影响其余。"""
    req = await request.get_json(silent=True) or {}
    ids = req.get("ids")
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(i, str) and i for i in ids)
    ):
        return get_error_data_result("ids 必须为非空字符串数组")
    if len(ids) > BATCH_DELETE_MAX:
        return get_error_data_result(f"单次最多删除 {BATCH_DELETE_MAX} 个模板")
    deleted, failed = [], []
    for tid in ids:
        ok, msg = TplTemplateService.delete_template(tid, current_user.id)
        if ok:
            deleted.append(tid)
        else:
            failed.append({"id": tid, "message": msg})
    return get_result(data={"deleted": deleted, "failed": failed})


@manager.route("/template/fill/<template_id>/preview", methods=["GET"])
@login_required
async def preview_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    obj_name = (ver.render_file_id or ver.original_file_id) if ver else ""
    if not obj_name:
        return get_error_data_result("模板文件缺失")
    blob = settings.STORAGE_IMPL.get(template_id, obj_name)
    if not blob:
        return get_error_data_result("文件不存在")
    try:
        if tpl.file_type == "docx":
            from rag.svr.template_fill.docx_utils import iter_docx_paragraphs
            items = iter_docx_paragraphs(blob)
        else:
            from rag.svr.template_fill.xlsx_utils import iter_xlsx_cells
            items = iter_xlsx_cells(blob)
    except Exception:
        logger.exception("preview parse failed, template=%s", template_id)
        return get_error_data_result("模板文件损坏或无法解析")
    for it in items:
        m = PLACEHOLDER_RE.search(it["text"])
        it["placeholder_key"] = m.group(1) if m else ""
    return get_result(data={"file_type": tpl.file_type, "items": items})


@manager.route("/template/fill/<template_id>/file", methods=["GET"])
@login_required
async def download_template(template_id: str):
    tpl, err = await _load_template(template_id)
    if err:
        return err
    kind = request.args.get("kind", "original")
    ver = TplTemplateVersionService.latest(template_id)
    obj_name = (ver.render_file_id if kind == "render" else ver.original_file_id) if ver else ""
    if not obj_name:
        return get_error_data_result("文件不存在")
    blob = settings.STORAGE_IMPL.get(template_id, obj_name)
    if not blob:
        return get_error_data_result("文件不存在")
    mime = DOCX_MIME if tpl.file_type == "docx" else XLSX_MIME
    return Response(blob, mimetype=mime,
                    headers={"Content-Disposition": f"attachment; filename=template_{template_id}.{tpl.file_type}"})


# ---------- 填写任务（P2+P3）：发起 / 列表 / 详情 / 重试 / 下载 ----------


@manager.route("/template/fill/fill-task", methods=["POST"])
@login_required
async def create_fill_task():
    """发起填写任务：校验链通过后落 pending 行，起后台线程跑 pipeline。
    template_version_id 钉住当前版本（published 后再改填写点会升版，历史任务按当时版本复现）。"""
    body = await request.get_json()
    body = body or {}
    template_id = (body.get("template_id") or "").strip()
    if not template_id:
        return get_error_data_result("template_id 不能为空")
    tpl, err = await _load_template(template_id)
    if err:
        return err
    if tpl.status != "published":
        return get_error_data_result("仅已发布的范本可发起填写")
    # kb_ids：只保留非空字符串项，过滤后仍为空则拒绝
    kb_ids = [k for k in (body.get("kb_ids") or []) if isinstance(k, str) and k.strip()]
    if not kb_ids:
        return get_error_data_result("kb_ids 不能为空")
    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        return get_error_data_result("params 必须为对象")
    source = body.get("source")
    if source is not None and not isinstance(source, str):
        return get_error_data_result("source 必须为字符串")
    ver = TplTemplateVersionService.latest(template_id)
    if not ver or not ver.render_file_id or not ver.placeholders:
        return get_error_data_result("该范本未配置填写点，请先完成填写点配置")
    task_id = get_uuid()
    TplFillTaskService.insert(
        id=task_id, template_id=template_id, template_version_id=ver.id,
        kb_ids=kb_ids, params=params, status="pending",
        source=(source or "web")[:16],
        flow_instance_id="", tenant_id=current_user.id, created_by=current_user.id)
    _spawn_fill_task(task_id)
    return get_result(data={"task_id": task_id, "status": "pending"})


@manager.route("/template/fill/<template_id>/test-fill", methods=["POST"])
@login_required
async def test_fill_template(template_id: str):
    """测试填写（B端试跑）：同 pipeline 前两步（检索+LLM 生成），不建任务、不渲染、
    不落 MinIO，同步等待直接返回 values/cells/evidence 供用户预览效果。
    耗时约 10-60 秒（Quart async 不阻塞 worker）。"""
    tpl, err = await _load_template(template_id)
    if err:
        return err
    if tpl.status != "published":
        return get_error_data_result("请先发布范本再试跑")
    body = await request.get_json()
    body = body or {}
    kb_ids = [k for k in (body.get("kb_ids") or []) if isinstance(k, str) and k.strip()]
    if not kb_ids:
        return get_error_data_result("请选择知识库")
    params = body.get("params")
    if not isinstance(params, dict):
        params = {}
    from rag.svr.template_fill.executor import dry_run
    try:
        data = await dry_run(current_user.id, template_id, kb_ids, params)
    except PermissionError:
        return get_error_data_result("知识库不属于当前租户")
    except ValueError as e:
        return get_error_data_result(str(e))
    except Exception:
        logger.exception("test fill failed, template=%s", template_id)
        return get_error_data_result("试跑失败，请重试")
    return get_result(data=data)


@manager.route("/template/fill/fill-task/list", methods=["GET"])
@login_required
async def list_fill_tasks():
    # type=int：转换失败（如 ?page=abc）回退默认值，避免 int("abc") 500；负数/超界由 service 层钳制
    args = request.args
    rows, total = TplFillTaskService.get_list_page(
        current_user.id, status=args.get("status", ""),
        page=args.get("page", 1, type=int), size=args.get("size", 20, type=int))
    return get_result(data=rows, total=total)


@manager.route("/template/fill/fill-task/<task_id>", methods=["GET"])
@login_required
async def get_fill_task(task_id: str):
    task = TplFillTaskService.get_owned(task_id, current_user.id)
    if not task:
        return get_error_data_result("任务不存在")
    return get_result(data=task.to_dict())


@manager.route("/template/fill/fill-task/<task_id>/retry", methods=["POST"])
@login_required
async def retry_fill_task(task_id: str):
    """重试失败/部分完成的任务：复位为 pending 后新起一轮 pipeline，
    values/evidence/result_file_id 由新一轮覆盖写（非增量修补）。"""
    task = TplFillTaskService.get_owned(task_id, current_user.id)
    if not task:
        return get_error_data_result("任务不存在")
    if task.status not in _RETRYABLE_STATUSES:
        return get_error_data_result("仅失败或部分完成的任务可重试")
    with _running_lock:
        if task_id in _running_tasks:
            return get_error_data_result("任务正在执行中")
    # 裸 update 必须显式包连接上下文（与全仓惯例一致）；where 带 status 白名单 CAS 复位，
    # 命中 0 行说明状态已被并发改走，拒绝重试
    with DB.connection_context():
        n = TplFillTaskService.model.update(status="pending", error="").where(
            TplFillTaskService.model.id == task_id,
            TplFillTaskService.model.status.in_(_RETRYABLE_STATUSES)).execute()
    if not n:
        return get_error_data_result("状态变更失败，请刷新重试")
    _spawn_fill_task(task_id)
    return get_result(data={"task_id": task_id, "status": "pending"})


@manager.route("/template/fill/fill-task/<task_id>/download", methods=["GET"])
@login_required
async def download_fill_result(task_id: str):
    task = TplFillTaskService.get_owned(task_id, current_user.id)
    if not task:
        return get_error_data_result("任务不存在")
    if not task.result_file_id:
        return get_error_data_result("生成稿尚未产出")
    # 生成稿与模板文件同 bucket（bucket=template_id）
    blob = settings.STORAGE_IMPL.get(task.template_id, task.result_file_id)
    if not blob:
        return get_error_data_result("生成稿文件缺失")
    ok, tpl = TplTemplateService.get_by_id(task.template_id)
    ext = tpl.file_type if ok and tpl else "docx"
    mime = DOCX_MIME if ext == "docx" else XLSX_MIME
    return Response(blob, mimetype=mime,
                    headers={"Content-Disposition": f"attachment; filename=fill_{task_id}.{ext}"})
