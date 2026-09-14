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
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.db_models import DB
from api.db.services.template_fill_service import (
    TERMINAL_TASK_STATUSES,
    TplFillTaskService,
    TplTemplateService,
    TplTemplateVersionService,
    sanitize_filename,
)
from api.utils.api_utils import get_error_data_result, get_result
from common import settings
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from rag.svr.template_fill.detector import detect_fill_points, validate_placeholders
from rag.svr.template_fill.executor import read_progress_snapshot
from rag.svr.template_fill.spawn import is_running as _task_running
from rag.svr.template_fill.spawn import spawn_fill_task as _spawn_fill_task
from rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)

manager = Blueprint("rest_template_fill_app", __name__)

MAX_TEMPLATE_SIZE = 20 * 1024 * 1024
PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 可重试的终态：仅失败/部分完成的任务允许重试
_RETRYABLE_STATUSES = ("failed", "partial")

# 零候选专门文案：候选为空意味着特征与手写占位符全部未命中，最常见的真实原因
# 是旧 .doc / PDF 经 LibreOffice 转换后下划线/表格/留白结构丢失。区别于「有候选但识别 0 条」。
_ZERO_CANDIDATES_MSG = ("未从模板中识别到任何疑似填写位置（已扫描正文、表格、页眉、页脚、文本框）。"
                        "若模板由旧版 .doc 转换而来，可能存在格式丢失，建议用 Word 另存为 .docx 后重新上传；"
                        "若为 PDF 转换而来，可能因扫描件或版式原因无法识别，建议用 Word/WPS 重建为 .docx 后重试；"
                        "或在正文中手写 {{字段名}} 占位符后重试，也可到详情页手动添加填写点")


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


def _is_pdf(filename: str) -> bool:
    """是否为 PDF 文件。endswith(".pdf") 大小写不敏感；不会误伤 .pdfx 类伪
    扩展名；filename 为 None 时按空串兜底返回 False。"""
    return (filename or "").lower().endswith(".pdf")


# PDF 转换专用线程池：与默认 executor 隔离。pdf2docx 是进程内纯 Python 转换，
# 病态 PDF 可能长时间自旋且线程无法强杀——专用小池保证即使被占死也只影响
# PDF 上传路径，不会耗尽全局 to_thread 池（否则会卡死同进程所有异步请求）。
_PDF_CONVERT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tpl-pdf")
# 实测 237 页范本样张 26.5s，放宽到 5 分钟上界
_PDF_CONVERT_TIMEOUT = 300

# 留白横线回填的几何判定参数（237 页范本样张实测标定）：
# 横线画在文字基线上，线 y 与同行词垂直中点 y 之差 ≈ 6.5-7pt（字号 10.5-14），
# 取 (2, 10) 开区间判「同行」；词与横线区间的水平间隙小于 GAP_MAX 视为相邻。
_PDF_BLANK_Y_MIN, _PDF_BLANK_Y_MAX = 2.0, 10.0
_PDF_BLANK_GAP_MAX = 120.0
_PDF_BLANK_MIN_W = 15.0
# 横线端点接纵线（±2pt）→ 是表格网格边框而非留白，跳过防污染表格
_PDF_GRID_TOL = 2.0


def _blank_line_targets(drawings, words):
    """纯几何判定：从一页 PDF 的矢量图形与词坐标中筛出「文字行内留白横线」。

    招标范本 PDF 的填写留白（福建省___市（区）___、招标编号：____）普遍是
    矢量绘制线条而非文字字符，pdf2docx 只转换文字/表格、矢量线直接丢弃——
    留白在转换件中消失导致 AI 识别不到。本函数找出需要回填 '_' 的横线：
    - 候选：水平线段/扁矩形（长 ≥ 15pt、厚 < 3pt）；
    - 排除1 表格边框：端点接纵线（网格）；
    - 排除2 装饰线（页眉分隔线等）：同行（基线 y 差 ∈ (2,10)）无相邻文字；
    drawings 为 page.get_drawings() 结构（{"items": [...]})，words 为
    get_text("words") 结构（x0,y0,x1,y1,word,...）。返回 [(x0, x1, y)]。"""
    hlines, vsegs = [], []
    for d in drawings:
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 1 and abs(p1.x - p2.x) >= _PDF_BLANK_MIN_W:
                    hlines.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y))
                elif abs(p1.x - p2.x) < 1 and abs(p1.y - p2.y) >= _PDF_BLANK_MIN_W:
                    vsegs.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                r = item[1]
                if r.width >= _PDF_BLANK_MIN_W and r.height < 3:
                    hlines.append((r.x0, r.x1, r.y0))
                elif r.height >= _PDF_BLANK_MIN_W and r.width < 3:
                    vsegs.append((r.x0, r.y0, r.y1))

    def _touches_grid(x, y):
        return any(abs(vx - x) <= _PDF_GRID_TOL and vy0 - _PDF_GRID_TOL <= y <= vy1 + _PDF_GRID_TOL
                   for vx, vy0, vy1 in vsegs)

    targets = []
    for x0, x1, y in hlines:
        if _touches_grid(x0, y) or _touches_grid(x1, y):
            continue  # 表格网格边框
        # 同行文字：词垂直中点位于横线上方 (2,10)pt 内（线画在基线上）
        near = [w for w in words if _PDF_BLANK_Y_MIN <= y - (w[1] + w[3]) / 2 <= _PDF_BLANK_Y_MAX]
        if not near:
            continue  # 装饰线（页眉线/分隔线），无同行文字
        # 相邻判定：词与横线区间的水平距离（允许 0.1pt 级微小重叠，gap=0）
        ok = any(((x0 - w[2]) if w[2] <= x0 else (w[0] - x1) if w[0] >= x1 else 0)
                 < _PDF_BLANK_GAP_MAX for w in near)
        if ok:
            targets.append((x0, x1, y))
    return targets


def _augment_pdf_blank_lines(src: str) -> str:
    """把 PDF 中矢量绘制的填写留白横线回填为 '_' 文字，另存增强副本返回其路径。

    回填只发生在副本上（同目录 input_aug.pdf），原 PDF 不动。无横线可回填时
    返回原路径零改动。尽力而为：fitz 打开/解析异常（畸形/加密 PDF 等）一律
    告警后回退原 PDF，不阻断转换主链路。以 stream 方式打开（不持源文件句柄——
    Windows 下失败构造的遗留句柄会锁住临时目录致清理失败）。仅 PDF 上传路径
    使用（惰性导入 fitz，pdf2docx 依赖链必带 PyMuPDF）。"""
    import fitz

    try:
        with open(src, "rb") as f:
            data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        logger.warning("pdf blank augment: open failed, use raw pdf", exc_info=True)
        return src
    try:
        total = 0
        for page in doc:
            words = page.get_text("words")
            targets = _blank_line_targets(page.get_drawings(), words)
            for x0, x1, y in targets:
                fs = 10.5
                uw = fitz.get_text_length("_", fontname="helv", fontsize=fs)
                n = max(3, int((x1 - x0) / uw))
                # 下划线文字基线取横线上方 1.5pt：字面压线，视觉与原留白一致
                page.insert_text((x0, y - 1.5), "_" * n, fontname="helv", fontsize=fs)
                total += 1
        if total == 0:
            return src
        aug = os.path.join(os.path.dirname(src), "input_aug.pdf")
        doc.save(aug)
        logger.info("pdf blank lines augmented: %d lines", total)
        return aug
    except Exception:
        logger.warning("pdf blank augment failed, use raw pdf", exc_info=True)
        return src
    finally:
        doc.close()


def _convert_pdf_to_docx(blob: bytes) -> bytes:
    """pdf2docx 转 PDF 为 .docx。LibreOffice writer_pdf_import 会把全部文字
    装进定位文本框（实测 237 页范本样张：34,050 个文本框、0 个原生表格，
    python-docx 按段落读取为 0 字符，下游识别/渲染链路不可见），2026-09-14
    起改用 pdf2docx 重建 Word 原生流式元素（同一样张：0 文本框、633 个
    原生表格含合并单元格）。转换前经 _augment_pdf_blank_lines 把矢量留白
    横线回填为 '_' 文字（同一样张候选 565→1351，含网格防护剔除表格边框垃圾）。
    惰性导入：仅 PDF 上传
    路径承担其加载开销。扫描件（无文字层的图片型 PDF）无法转换，由上层
    统一异常文案兜底。"""
    from pdf2docx import Converter

    with tempfile.TemporaryDirectory(prefix="tpl_pdf_") as tmp:
        src = os.path.join(tmp, "input.pdf")
        with open(src, "wb") as f:
            f.write(blob)
        out = os.path.join(tmp, "input.docx")
        cv = Converter(_augment_pdf_blank_lines(src))
        try:
            cv.convert(out)
        finally:
            try:
                cv.close()
            except Exception:
                # close 失败不顶替 convert 的真实异常（端点日志要记真凶）
                logger.debug("pdf2docx Converter.close failed", exc_info=True)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            logger.error("convert pdf to docx produced empty output")
            raise RuntimeError("convert pdf to docx failed")
        with open(out, "rb") as f:
            return f.read()


def _convert_to_docx(blob: bytes, src_ext: str = ".doc") -> bytes:
    """经 LibreOffice 转出 .docx（仅旧版 .doc 走此路径；PDF 分支见
    _convert_pdf_to_docx）。容器内有 LibreOffice；用独立
    UserInstallation 目录避免并发/首启 profile 锁冲突。容器 soffice 包装
    脚本未自设库路径，须显式注入 LD_LIBRARY_PATH（否则 soffice.bin 报
    libreglo.so cannot open shared object file, rc=127）。"""
    env = {**os.environ, "LD_LIBRARY_PATH": "/usr/lib/libreoffice/program"}
    with tempfile.TemporaryDirectory(prefix="tpl_doc_") as tmp:
        src = os.path.join(tmp, f"input{src_ext}")
        with open(src, "wb") as f:
            f.write(blob)
        profile = f"file://{tmp}/lo_profile_{uuid.uuid4().hex}"
        cmd = ["soffice", "--headless", "--norestore", f"-env:UserInstallation={profile}"]
        cmd += ["--convert-to", "docx", "--outdir", tmp, src]
        r = subprocess.run(
            cmd,
            capture_output=True, timeout=180, check=False, env=env)
        out = os.path.join(tmp, "input.docx")
        if not os.path.exists(out):
            logger.error("convert to docx failed (src_ext=%s): rc=%s stderr=%s",
                         src_ext, r.returncode, (r.stderr or b"")[:500])
            raise RuntimeError("convert to docx failed")
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
        return get_error_data_result("请上传 .docx / .doc / .pdf / .xlsx 模板文件")
    file_type = _file_type_of(file.filename)
    is_legacy_doc = False
    is_pdf = False
    if not file_type:
        if _is_legacy_doc(file.filename):
            # 旧版 .doc：后端转成 .docx 后以 docx 形态进入全链路（candidates/替换/预览/下载）
            is_legacy_doc = True
            file_type = "docx"
        elif _is_pdf(file.filename):
            # PDF：同款格式归一化——转 docx 后走既有识别/渲染/预览链路，原始 PDF 不留存
            is_pdf = True
            file_type = "docx"
        else:
            return get_error_data_result("仅支持 .docx / .doc / .pdf / .xlsx")
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
            # subprocess 同步等待会阻塞 Quart 事件循环（最长 timeout 秒，期间全部
            # 并发请求停摆），丢线程池执行
            blob = await asyncio.to_thread(_convert_to_docx, blob)
        except Exception:
            logger.exception("legacy doc convert failed, filename=%s", file.filename)
            return get_error_data_result("旧版 .doc 转换失败，请用 Word 另存为 .docx 后重新上传")
        # docx 体积可能大于源文件，转换后复查一次，防止存储超限 blob 入库
        if len(blob) > MAX_TEMPLATE_SIZE:
            return get_error_data_result("文件为空或超过 20MB")
    if is_pdf:
        try:
            # 专用池 + wait_for 超时：病态 PDF 自旋时请求可失败返回，
            # 线程留在专用池内不拖垮全局 to_thread 池
            blob = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _PDF_CONVERT_POOL, _convert_pdf_to_docx, blob),
                timeout=_PDF_CONVERT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("pdf convert timed out after %ss, filename=%s",
                         _PDF_CONVERT_TIMEOUT, file.filename)
            return get_error_data_result("PDF 转换超时，建议拆分或压缩后重试")
        except Exception:
            logger.exception("pdf convert failed, filename=%s", file.filename)
            return get_error_data_result("PDF 转换失败，建议用 Word/WPS 打开后另存为 .docx 再上传")
        # 转换后体积可能大于源文件，复查一次
        if len(blob) > MAX_TEMPLATE_SIZE:
            return get_error_data_result("文件为空或超过 20MB")
    # docx/xlsx 均为 zip 容器，轻量验证内容合法性，防后续解析抛 BadZipFile 500
    # （.doc/.pdf 转换产物也在此校验，转换失败产出非 zip 时被拦截）
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
    if not candidates:
        return get_error_data_result(_ZERO_CANDIDATES_MSG)
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
        if not candidates:
            TplTemplateService.set_detect_status(template_id, "failed", _ZERO_CANDIDATES_MSG)
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


@manager.route("/template/fill/<template_id>/defaults", methods=["PUT"])
@login_required
async def update_template_defaults(template_id: str):
    """B端默认值编辑：{defaults: {key: value}}，空串=清空该字段默认值。"""
    body = await request.get_json(silent=True) or {}
    defaults = body.get("defaults")
    if not isinstance(defaults, dict) or not defaults:
        return get_error_data_result("defaults 不能为空")
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ok, msg = TplTemplateVersionService.update_defaults(template_id, defaults)
    if not ok:
        return get_error_data_result(msg)
    return get_result(data={"id": template_id})


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
    if _task_running(task_id):
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


# stalled 判定阈值：非终态任务 update_time 超过该秒数视为中断（服务器重启等）
_PROGRESS_STALLED_SECONDS = 600

# done 任务生成稿 bucket 桥接的进程内记忆化：MinIO get+put 是每次轮询都不该重复付的
# IO 成本；确定性对象名 tplfill-{task_id} 本就幂等覆盖，记忆化不引入正确性风险。
# 进程重启集合清空 → 重跑一次 get+put，无害自愈。
_bridged_tasks: set[str] = set()


def _bridge_download(task) -> dict | None:
    """done 任务生成稿桥接：从 {template_id} bucket 拷入 {tenant}-downloads
    （/agents/download 与 /files/{id}/content 的既有读取契约），返回 download
    dict 或 None。模板行查询便宜（取真实 file_type 定扩展名），保留在记忆化前
    构造 filename；模板缺失/查询失败只是桥接细节，fallback 为 .docx（名退化
    为 doc_id），不让 done 任务丢下载入口；昂贵的 blob get+put 每任务进程内
    只做一次（确定性对象名 tplfill-{task_id} 幂等覆盖，记忆化复用不产生重复
    对象；生成稿缺失/IO 异常不记忆化，下次轮询自动重试）。"""
    doc_id = f"tplfill-{task.id}"
    try:
        ok, tpl = TplTemplateService.get_by_id(task.template_id)
        ext = tpl.file_type if ok and tpl and tpl.file_type in ("docx", "xlsx") else "docx"
        base = sanitize_filename(tpl.name) if ok and tpl else doc_id
        if task.id not in _bridged_tasks:
            blob = settings.STORAGE_IMPL.get(task.template_id, task.result_file_id)
            if not blob:
                return None
            settings.STORAGE_IMPL.put(f"{task.tenant_id}-downloads", doc_id, blob)
            _bridged_tasks.add(task.id)
        filename = f"{base}.{ext}"
    except Exception:
        logger.exception("progress bucket bridge failed, task=%s", task.id)
        return None
    return {"doc_id": doc_id, "filename": filename, "name": filename,
            "url": f"/api/v1/agents/download?id={doc_id}&created_by={task.tenant_id}"}


def build_progress_payload(task, snapshot: dict | None, download: dict | None = None) -> dict:
    """合并 DB 行 + Redis 快照组装 progress 响应（纯函数，便于对抗测试）；
    download 由端点层桥接后传入。快照缺失（Redis 挂掉/过期）退化为 DB 行：
    进度数字停更但状态/终态值仍准确。
    stalled：非终态且 DB update_time 超阈值（服务器重启 daemon 线程死）→ 前端
    提示可重试。双信号防误报：DB update_time 只在状态跃迁刷新，长 generating
    任务会被超阈值——但快照 updated_at（executor 每次写快照都刷新，毫秒 epoch，
    旧秒级值读侧防御归一）仍新鲜说明任务活着，不判 stalled；两个活性信号都停跳才判中断。"""
    status = (snapshot or {}).get("status") or task.status
    values = (snapshot or {}).get("values")
    if values is None:
        values = (task.values or {}).get("render") if isinstance(task.values, dict) else None
    snap_alive = False
    if snapshot and snapshot.get("updated_at"):
        snap_ts = snapshot["updated_at"]
        if snap_ts < 10**12:  # 兼容旧秒级快照：归一到毫秒
            snap_ts *= 1000
        snap_alive = (current_timestamp() - snap_ts) <= _PROGRESS_STALLED_SECONDS * 1000
    stalled = (task.status not in TERMINAL_TASK_STATUSES
               and bool(task.update_time)
               and (current_timestamp() - task.update_time) > _PROGRESS_STALLED_SECONDS * 1000
               and not snap_alive)
    return {
        "status": status,
        "done": (snapshot or {}).get("done"),
        "total": (snapshot or {}).get("total"),
        "values": values,
        "download": download,
        "error": (snapshot or {}).get("error") or (task.error or ""),
        "stalled": stalled,
    }


@manager.route("/template/fill/fill-task/<task_id>/progress", methods=["GET"])
@login_required
async def get_fill_task_progress(task_id: str):
    """断连重连轮询端点（幂等只读）：owner 校验同 get_fill_task，
    合并 DB 行 + Redis 快照；前端 2s 轮询，status 终态即停。
    done 任务的 download 桥接（MinIO 拷贝）在端点层做，payload 保持纯函数。"""
    task = TplFillTaskService.get_owned(task_id, current_user.id)
    if not task:
        return get_error_data_result("任务不存在")
    download = None
    if task.status == "done" and task.result_file_id:
        download = _bridge_download(task)
    return get_result(data=build_progress_payload(task, read_progress_snapshot(task_id), download))


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


# ---------- 画布 TemplateFill 暂停确认（P2 默认值基线） ----------

# > 画布节点 _CONFIRM_TIMEOUT(600s)：确认提交晚于节点超时也没意义，键先过期自愈
_CONFIRM_TTL = 700

# nonce 卫生：限长 64 + 字母数字与连字符白名单，防键注入/超长键
# （组件侧 get_uuid() 产出为 32 位 hex，天然满足）
_CONFIRM_NONCE_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


@manager.route("/template/fill/confirm", methods=["POST"])
@login_required
async def confirm_template_fill():
    """画布 TemplateFill 暂停确认唤醒：写 Redis 确认键，节点轮询读取后继续。
    decisions: {template_id: {changed: [key...], values: {key: value}}}
    nonce 为本次运行的运行级随机数（confirm_pending 事件下发）：task_id 即
    agent_id 跨运行不变，键含 nonce 后孤儿键（重复点击/超时后确认/取消残留）
    永不被新运行误读，由 700s TTL 自然过期。"""
    body = await request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "").strip()
    nonce = str(body.get("nonce") or "").strip()
    decisions = body.get("decisions")
    if not task_id or not isinstance(decisions, dict):
        return get_error_data_result("task_id 与 decisions 不能为空")
    if not nonce:
        return get_error_data_result("nonce 不能为空")
    if not _CONFIRM_NONCE_RE.fullmatch(nonce):
        return get_error_data_result("nonce 非法")
    # 入参清洗（前端数据不可信）：只留字符串 key，值统一 str 归一
    clean = {}
    for tid, d in decisions.items():
        if not isinstance(d, dict):
            continue
        clean[str(tid)] = {
            "changed": [str(k) for k in (d.get("changed") or []) if isinstance(k, str)],
            "values": {str(k): str(v) for k, v in (d.get("values") or {}).items()
                       if isinstance(k, str)},
        }
    # RedisDB.set 内部吞异常只返回 False，必须查返回值而非只捕获异常，
    # 否则真实 Redis 故障会误报 ok:true、确认静默丢失
    try:
        ok = REDIS_CONN.set(f"tpl_fill:confirm:{task_id}:{nonce}",
                            json.dumps(clean, ensure_ascii=False), exp=_CONFIRM_TTL)
    except Exception:
        logger.exception("write confirm key failed, task=%s nonce=%s", task_id, nonce)
        ok = False
    if not ok:
        return get_error_data_result("确认提交失败，请重试")
    return get_result(data={"ok": True})
