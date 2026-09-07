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
import io
import logging
import re
import zipfile

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.services.template_fill_service import TplTemplateService, TplTemplateVersionService
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


def _file_type_of(filename: str):
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".xlsx"):
        return "xlsx"
    return None


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
        return get_error_data_result("请上传 .docx 或 .xlsx 模板文件")
    file_type = _file_type_of(file.filename)
    if not file_type:
        return get_error_data_result("仅支持 .docx / .xlsx")
    # read 前先 seek 到尾部探实际大小，避免超大文件先整份读进内存（app 级 MAX_CONTENT_LENGTH 默认 1GB）
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size <= 0 or size > MAX_TEMPLATE_SIZE:
        return get_error_data_result("文件为空或超过 20MB")
    blob = file.read()
    if not blob or len(blob) > MAX_TEMPLATE_SIZE:
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
    TplTemplateVersionService.save_placeholders(tpl.to_dict(), items)
    return get_result(data={"id": template_id, "placeholder_count": len(items)})


@manager.route("/template/fill/<template_id>/publish", methods=["POST"])
@login_required
async def publish_template(template_id: str):
    _tpl, err = await _load_template(template_id)
    if err:
        return err
    ver = TplTemplateVersionService.latest(template_id)
    if not ver or not ver.render_file_id:
        return get_error_data_result("请先保存填写点配置再发布")
    TplTemplateService.set_status(template_id, current_user.id, "published")
    return get_result()


@manager.route("/template/fill/<template_id>/disable", methods=["POST"])
@login_required
async def disable_template(template_id: str):
    _tpl, err = await _load_template(template_id)
    if err:
        return err
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
