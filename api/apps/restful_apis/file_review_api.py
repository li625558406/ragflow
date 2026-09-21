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
"""文件审核 REST API（5 端点）。

  GET  /file/review/templates                                可用审核模板（系统预置 + 本租户）
  GET  /file/review/file/<file_id>/state                      以文件为中心的权威读模型
  POST /file/review/<task_id>/fix                             发起一轮修复
  POST /file/review/annotation/<aid>/status                  人工闭环单条标注（兜底出口）
  GET  /file/review/<task_id>/<file_version>/download        下载指定轮次成稿 MinIO 对象
  GET  /file/review/<task_id>/<file_version>/content         读取指定轮次成稿段落结构（审核弹框展示成稿）

── 设计取舍（T9 前置侦察实测结论，改前请先读 Task 9 的「规格重写说明」）──────

* **不做进度 SSE。** executor 每轮只写一行最终状态、没有步进粒度，SSE 每 2s 推的还是
  同一个状态；本仓库现有 SSE 全部挂在画布/agent 会话上，没有「功能级 SSE」先例。最接近
  的同族功能（范本填写）也是踩过「执行与连接耦合」的坑之后才改成「后台线程 + 轮询 + 快照」。
  进度真相是 file_review_round 表，前端轮询。
* **没有 start / finish 端点。** 发起入口在 T7 画布节点与 T8 对话工具（都已在运行时里
  拿着 tenant_id + 文件 id），再开 HTTP 入口只是多一条能绕过它们的越权面。finish 更危险：
  把最后一轮写 done 会覆盖正在跑的 reviewing/fixing 轮次，线程回头还会写自己的终态。
* **读不限、写严格**（有意的不对称）：state 不按 tenant 过滤（对齐「文件所有人可见」口径，
  也让流程协作者看得到审核结果）；fix / annotation-status 必须 get_owned_task 严格校验 ——
  因为 execute_task / spawn_review_task / _force_fail_round 全链路只按 task_id 圈定、
  **不含任何 tenant 谓词**，本层是唯一防线（T6 → T9 交接契约第 5 条）。
* **成稿下载走本模块的专用端点，不走 GET /api/v1/files/<id>**：审核成稿的 MinIO 对象名
  是 `frv-{task_id}-{version}`（见 `file_review_service` 的落盘口径），**不是**上传系统的
  file_id —— 旧链路把 doc.object 当 file_id 拼 `/api/v1/files/<id>` 会 404（T9 → T14/T15
  复盘）。R-8 起 state 的 doc payload 已**摘除对象名**，只下发 `{has_result, version}`，
  预览/下载一律凭 task_id + version 走本模块的专用端点。该端点带 `@login_required`，
  而 `_load_user` 只从 `request.headers["Authorization"]` 取用户、**不从 cookie 兜底**，
  所以前端**不能**用 window.open / a[href] 直链（浏览器导航请求不带自定义头 ⇒ 必 401），
  必须 fetch 手挂 Authorization 取 Blob（`web/src/services/file-review-service.ts`）。
"""
import asyncio
import json
import logging
from urllib.parse import quote

from quart import Blueprint, Response, request

from api.apps import login_required
from api.db.services.file_review_service import (
    MAX_FIX_ROUNDS,
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
    FixAdmissionDenied,
    admit_fix_round,
    fix_rounds_left,
    heal_stale_round,
    is_stale_running,
)
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_argument_result,
    get_error_data_result,
    get_json_result,
)
from common import settings
from common.constants import RetCode
from common.misc_utils import thread_pool_exec
from rag.svr.file_review.executor import FileReviewError, perform_revert  # noqa: F401 (FileReviewError 供 except 子句引用)

# 本模块**不**顶层 import rag.svr.file_review.spawn：起线程那一步已随受理闸门一起下沉到
# Service 层的 admit_fix_round（由它函数内延迟 import），本层不再直接触 spawn。

logger = logging.getLogger(__name__)

manager = Blueprint("file_review_api", __name__)

# 轮次状态口径（RUNNING_ROUND_STATUSES）与受理闸门（admit_fix_round / FixAdmissionDenied）
# 都在 Service 层 —— 对话工具那条线程与 quart 事件循环是两个线程，只有进程级锁能互斥，
# 故互斥边界不能留在这个只跑在事件循环里的模块。故本模块不保留重复定义。

# 级别白名单：与 T1 预置模板 / T6 _norm_severity 的输出一致。
_SEVERITY_WHITELIST = ("high", "medium", "low")

# 人工可置的标注状态。**排除 fixed**：那是 executor 在补丁真正落地后写的派生结论，
# 手置会让「文档改了没改」与面板状态失去对应关系。排除 new（下一轮的中间态）。
_MANUAL_ANNOTATION_STATUSES = ("open", "resolved", "wontfix")


def _json_list(raw) -> list:
    """把 TEXT 列里的 JSON 数组还原成 list；非 list / 坏 JSON / 空一律降级 []。

    脏值只影响前端下拉候选项，不值得为它让整条端点 500。
    """
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def _json_dict(raw) -> dict:
    """把 anchor 这类 JSON 对象列还原成 dict；脏值降级 {}（=「无定位信息」）。

    前端对 anchor == {} 有既有降级路径（退到 matched_text 全文匹配），故这里降级是安全的；
    整条端点因为一行脏 anchor 挂掉则不是。
    """
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _template_payload(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "annotation_types": _json_list(row.annotation_types),
    }


def _round_payload(row) -> dict:
    _ERROR_CLIP = 200
    raw_err = row.error or ""
    error = (raw_err[:_ERROR_CLIP] + "…") if len(raw_err) > _ERROR_CLIP else raw_err
    return {
        "id": row.id,
        "round_no": row.round_no,
        "status": row.status,
        "file_version": row.file_version,
        "template_id": row.template_id or "",
        "user_query": row.user_query or "",
        "summary": row.summary or "",
        "error": error,
        "minio_path": row.minio_path or "",
        # 判据是「有对象名」而不是「状态是 done」：T6 的修复轮先落盘、后收口，收口那一步
        # 抛错时轮次被兜底写成 failed，而成稿**已经落盘**（交接契约第 2 条）。按状态判会把
        # 已修好的成稿藏起来。
        "produced": bool(row.minio_path),
        # 服务重启 / 崩溃后轮次会永久停在 reviewing|fixing（线程没了、状态不回落）。
        # 前端据此停止轮询、停止转圈、把状态显示成「已中断」并**隐藏**修复入口（服务端
        # stale 闸门会拒绝一切 fix，唯一出路是重新发起审核）—— 否则卡片会永远转下去
        # 且用户无任何出口。判据在 Service 层（is_stale_running），本层只透传，不自己
        # 发明更宽或更严的条件。R-1 自愈接入后，state / admit 两个消费点都会先 heal：
        # 本字段只剩「已中断但尚未被任何读取 heal」的**瞬时窗口**语义（heal 后谓词
        # 不成立，stale 恒 false），不再是常驻态。
        "stale": is_stale_running(row),
    }


def _doc_payload(rounds: list) -> dict:
    """该展示的文档版本与「有无成稿」。**不下发 MinIO 对象名**（R-8：内部对象名
    不该出 API；已核实两个前端调用点都只消费 version，预览/下载走 task_id+version
    的专用 download 端点）。

    显式选 round_no 最大且带 minio_path 的轮次（不依赖入参顺序），与 T6
    _latest_version_name 的基线口径一致 —— 面板必须展示**最后一版**。
    has_result 替代旧版「object != file_id 哨兵」的隐式约定。
    """
    best = None
    for r in rounds:
        if not r.minio_path:
            continue
        if best is None or (r.round_no or 0) >= (best.round_no or 0):
            best = r
    if best is None:
        # 还没有成稿：version 留空串——「首轮版本号 = v1」是 T7/T8 的命名习惯，
        # 不是本层的契约，不该由这里替前端断言。
        return {"has_result": False, "version": ""}
    return {"has_result": True, "version": best.file_version}


def _annotation_payload(row) -> dict:
    return {
        "id": row.id,
        "round_id": row.round_id,
        "task_id": row.task_id,
        "file_id": row.file_id,
        "file_version": row.file_version,
        "type": row.type,
        "severity": row.severity,
        "issue": row.issue,
        "suggestion": row.suggestion or "",
        "matched_text": row.matched_text or "",
        "source": row.source,
        "status": row.status,
        # 修复轮实际落地的 find/replace（脏值降级 {} = 无补丁，前端据此隐藏对比/回退）
        "patch": _json_dict(getattr(row, "patch_json", "")),
        "prev_annotation_id": row.prev_annotation_id or "",
        "anchor": _json_dict(row.anchor),
    }


def _count_annotations(rows) -> dict:
    """面板头部计数。pending 与 executor 的取数口径一致（open/new），fixed 单列。

    这里只做展示、不参与任何判定，故不 import Service 的 PENDING_ANNOTATION_STATUSES ——
    展示口径与「下一轮要修什么」的取数口径是两件事，绑在一起会让其中一方的调整被迫同步。
    """
    counts = {"total": len(rows), "high": 0, "medium": 0, "low": 0,
              "pending": 0, "fixed": 0}
    for a in rows:
        if a.severity in _SEVERITY_WHITELIST:
            counts[a.severity] += 1
        if a.status in ("open", "new"):
            counts["pending"] += 1
        elif a.status == "fixed":
            counts["fixed"] += 1
    return counts


def _parse_levels(raw) -> list | None:
    """归一 levels 入参：小写化、去重、保序。**任一项非法即整批拒绝**（返回 None）。

    不静默丢弃非法项：level 会被 compose_fix_query 拼成给 LLM 的中文指令，悄悄吞掉一个
    "critical" 会让用户以为「严重级别已纳入本次修复」，而实际指令里根本没有它 ——
    这种失败没有任何回执。
    空输入同样返回 None（而非 []）：调用方拿 None 与 [] 走同一条「参数错误」分支，
    避免出现「修了 0 个级别但轮次照样建」的空转。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    out = []
    for item in raw:
        val = str(item or "").strip().lower()
        if val not in _SEVERITY_WHITELIST:
            return None
        if val not in out:
            out.append(val)
    return out or None


@manager.route("/file/review/templates", methods=["GET"])
@login_required
@add_tenant_id_to_kwargs
async def list_review_templates(tenant_id: str):
    """可用审核模板：系统预置（tenant_id == ""）+ 本租户自有，且 enabled == 1。"""
    try:
        rows = FileReviewTemplateService.list_enabled(tenant_id)
        return get_json_result(data={"templates": [_template_payload(r) for r in rows]})
    except Exception:
        logger.exception("file review: list templates failed")
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/file/<file_id>/state", methods=["GET"])
@login_required
async def review_state(file_id: str):
    """以文件为中心的权威读模型：前端只凭 file_id 就能渲染进度与批注。

    doc = {"has_result", "version"}：该展示的文档版本（最近一次落盘的成稿）与「有无成稿」。
    **不下发 MinIO 对象名**（R-8）：预览/下载一律走 task_id + file_version 的专用 download
    端点（见模块 docstring 末条）。
    annotations = 该文件**全部**标注（跨轮次/版本/任务）—— 不能按成稿版本过滤：只有审查轮
    产标注且其 file_version 恒为首轮版本，成稿是 v2/v3/v4，按成稿版本过滤会一条都查不到。
    rounds[].stale = 该轮自称在跑但已无线程会回来写它（服务重启 / 崩溃），前端据此停轮询。

    不按 tenant 过滤（读路径，见模块 docstring 的「读不限、写严格」）。
    """
    try:
        rounds = FileReviewRoundService.get_by_file(file_id)
        # R-1 自愈：中断轮次在第一次被读取时就地回落（幂等），随后 payload 构建读到的
        # 是回落后的 failed 行。stale 字段保留，仅覆盖「已中断但尚未被任何读取 heal」
        # 的瞬时窗口（heal 后谓词不成立，stale 恒 false）。
        for r in rounds:
            heal_stale_round(r)
        anns = FileReviewAnnotationService.list_by_file(file_id)
        return get_json_result(data={
            "file_id": file_id,
            "task_id": rounds[-1].task_id if rounds else None,
            "rounds": [_round_payload(r) for r in rounds],
            "current": _round_payload(rounds[-1]) if rounds else None,
            "doc": _doc_payload(rounds),
            "annotations": [_annotation_payload(a) for a in anns],
            "annotation_counts": _count_annotations(anns),
            "fix_rounds_left": fix_rounds_left(rounds),
            "max_fix_rounds": MAX_FIX_ROUNDS,
        })
    except Exception:
        logger.exception("file review: load state failed, file_id=%s", file_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/<task_id>/fix", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def fix_review(task_id: str, tenant_id: str):
    """发起一轮修复。body: {"levels": ["high","medium"], "user_query": 可选补充说明}"""
    try:
        body = await request.get_json(silent=True) or {}
        levels = _parse_levels(body.get("levels"))
        if not levels:
            return get_error_argument_result(
                "levels 必须是非空数组，取值只能是 high / medium / low")

        # 受理序列「校验 — 定轮号 — 建轮次 — 起线程」必须原子，互斥在 Service 层的
        # 进程级 threading.Lock（admit_fix_round），跨线程有效（对话工具被
        # common.connection_utils.timeout 丢进独立 daemon 线程）。
        # R-6：acquire 最坏要等 5s 超时、闸门里还有多次同步 DB 往返——整段经
        # asyncio.to_thread 移入线程池，事件循环不被单请求阻塞。锁是 threading.Lock，
        # 移入工作线程不改变互斥语义（事件循环、to_thread 工作线程、对话工具线程
        # 三者仍被同一把锁串行化）。
        try:
            result = await asyncio.to_thread(
                admit_fix_round,
                task_id=task_id, tenant_id=tenant_id, levels=levels,
                # 基准取首轮原始需求（见 _fix_base_query 的说明）；非法类型在 Service 层忽略。
                user_query_override=body.get("user_query"))
        except FixAdmissionDenied as denied:
            if denied.reason == "not_found":
                # 「不存在」与「不是你的」共用同一句文案（get_owned_task 的口径），走默认
                # 错误码路径 —— 与改造前的返回值完全一致。
                return get_error_data_result(denied.message)
            return get_json_result(code=RetCode.OPERATING_ERROR, message=denied.message)

        return get_json_result(data={"task_id": task_id, "round_id": result.round_id,
                                     "round_no": result.round_no, "status": "fixing",
                                     "fix_rounds_left": result.fix_rounds_left})
    except Exception:
        logger.exception("file review: start fix round failed, task_id=%s", task_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/annotation/<annotation_id>/status", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def update_annotation_status(annotation_id: str, tenant_id: str):
    """人工把单条标注置为 open / resolved / wontfix（交接契约第 4 条的人工兜底出口）。

    为什么必须有：修复轮可能「成稿落盘成功、轮次收口成功，但逐条置 fixed 时某条标注写入
    失败」—— 这条标注就永远停在 open，而文档已经改好、find 已被替换，再跑修复只会报
    「0 项未能自动修复」，**没有任何自动路径能自愈**。没有这个出口，面板上会永远挂着一个
    假未闭环项。
    """
    try:
        body = await request.get_json(silent=True) or {}
        status = (body.get("status") or "").strip().lower()
        if status not in _MANUAL_ANNOTATION_STATUSES:
            return get_error_argument_result(
                "status 只能是 open / resolved / wontfix（fixed 由服务端在修复落地后写入，不接受人工设置）")
        row = FileReviewAnnotationService.get_by_id(annotation_id)
        if not row:
            return get_error_data_result("批注不存在或无权访问")
        # 写路径严格校验：标注 → 所属 task → 轮次归属。用 get_owned_task 而不是直接比
        # row.tenant_id，是因为后者的可见范围与轮次不完全一致（标注行的 tenant 为空而轮次行
        # 有 tenant 的历史脏数据），而权限必须按「这条标注所属的审核任务」判。
        if not FileReviewRoundService.get_owned_task(row.task_id, tenant_id):
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewAnnotationService.update_status(annotation_id, status):
            # 上面刚查到行，这里再失败只可能是并发删除：同样按「不存在」回，不泄露时序差异。
            return get_error_data_result("批注不存在或无权访问")
        return get_json_result(data={"annotation_id": annotation_id, "status": status})
    except Exception:
        logger.exception("file review: update annotation status failed, aid=%s", annotation_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/annotation/<annotation_id>/revert", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def revert_annotation_fix(annotation_id: str, tenant_id: str):
    """回退一条已修复批注：逆补丁恢复原文 → 产新版本（kind='revert' 轮）→ 批注回 open。

    权限链照抄 delete 端点（登录 → 存在性 → get_owned_task 归属）。executor 的
    FileReviewError 携带用户可读文案（删除型不可回退/原文被后续轮改动/无修复记录等），
    逐字透传给前端——这些是「为什么不能自动回退」的业务结论，吞掉换成 Internal error
    等于让用户重新一个个对。
    """
    try:
        row = FileReviewAnnotationService.get_by_id(annotation_id)
        if not row:
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewRoundService.get_owned_task(row.task_id, tenant_id):
            return get_error_data_result("批注不存在或无权访问")
        # perform_revert 全程持 _ADMIT_LOCK（等锁 + MinIO blob 往返），必须丢线程池，
        # 否则阻塞 quart 事件循环——并发 admit_fix_round 会因锁被同步占住而误报 busy。
        result = await asyncio.to_thread(perform_revert, row)
        return get_json_result(data={"annotation_id": annotation_id, **result})
    except FileReviewError as e:
        return get_error_argument_result(str(e))
    except Exception:
        logger.exception("file review: revert annotation failed, aid=%s", annotation_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/annotation/<annotation_id>/delete", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def delete_annotation(annotation_id: str, tenant_id: str):
    """物理删除单条批注（AI / manual 均可），与状态修改同闸。

    权限链照抄 update_annotation_status：登录 → 标注存在性 → get_owned_task 按
    「标注所属审核任务」判归属（标注行 tenant 历史脏数据不可作依据）。硬删不软删：
    prev_annotation_id 只写不读，无链断裂风险；重复删除幂等（第二次按「不存在」回）。
    """
    try:
        row = FileReviewAnnotationService.get_by_id(annotation_id)
        if not row:
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewRoundService.get_owned_task(row.task_id, tenant_id):
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewAnnotationService.delete_annotation(annotation_id):
            # 存在性检查与删除之间的并发删除窗口：同样按「不存在」回，不泄露时序差异。
            return get_error_data_result("批注不存在或无权访问")
        return get_json_result(data={"annotation_id": annotation_id})
    except Exception:
        logger.exception("file review: delete annotation failed, aid=%s", annotation_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/<task_id>/<file_version>/download", methods=["GET"])
@login_required
async def download_review_version(task_id: str, file_version: str):
    """下载指定 (task_id, file_version) 轮次的成稿 MinIO 对象。

    与 flow_app.download_version 同形态：用 settings.STORAGE_IMPL.get(bucket, key)
    取 blob，返回 Content-Disposition 触发浏览器下载。对象名按 executor._store_version_blob
    的约定：bucket = `{tenant_id}-downloads`，key = `frv-{task_id}-{file_version}`。

    不入 owner-gate：T9 决策 reads ungated（文件所有人可见），下载属于读路径。
    """
    try:
        # 复用既有 get_by_task 聚合所有轮次 —— 不新增 list_by_task（与 service 层「按
        # 维度查一轮次」是同一种查询）。首轮 file_id 即后续轮次的 file_id（task 是一次
        # 审核会话，所有轮次归属同一文件）。
        rounds = FileReviewRoundService.get_by_task(task_id)
        if not rounds:
            return get_error_data_result("任务不存在")
        tenant_id = rounds[0].tenant_id or ""
        # 防御空 file_version：避免拼出 `frv-t1-` 这种「全 task 共用一份成稿」的脏路径。
        if not file_version:
            return get_error_data_result("该轮次无成稿")
        target = next(
            (r for r in rounds if r.file_version == file_version and r.minio_path),
            None,
        )
        if not target:
            return get_error_data_result("该轮次无成稿")
        object_name = f"frv-{task_id}-{file_version}"
        bucket = f"{tenant_id}-downloads" if tenant_id else ""
        if not bucket:
            return get_error_data_result("该任务归属缺失，无法下载")
        blob = await thread_pool_exec(settings.STORAGE_IMPL.get, bucket, object_name)
        if not blob:
            # STORAGE_IMPL.get 对丢失的对象返回 None；直接塞进 Response 会得到 200 + 0 字节，
            # 把「产物丢了」伪装成一次成功下载 —— 用户拿到空文件、无从判断是重试还是重跑，
            # 面板上「可下载」与实际打不开互相矛盾。这里如实报错。
            return get_error_data_result("成稿文件已丢失，请重新发起修复")
        # 文件名只用 file_version，**不带** file_id：那是上传系统的内部 uuid，透给用户既无
        # 信息量，还会被误当成可寻址的文件标识。编码方式不变（filename*=UTF-8''{quote}）。
        file_name = f"文件审核_{file_version}.docx"
        return Response(
            blob,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
        )
    except Exception:
        logger.exception("file review: download failed, task_id=%s version=%s", task_id, file_version)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/<task_id>/<file_version>/content", methods=["GET"])
@login_required
async def review_version_content(task_id: str, file_version: str):
    """读取指定 (task_id, file_version) 轮次成稿的段落结构，供审核弹框展示成稿版本。

    与 download 同一读路径（不入 owner-gate）、同一闸链（任务存在 → 轮次有产物 →
    bucket 可用 → 对象非空），取到 blob 后按魔数分发解析：PK 即 docx 直接
    Docx().to_paragraphs；OLE2（.doc 原件经 LibreOffice 修复链路产出前不会有，但
    防御历史脏对象）先 doc_to_docx_via_libreoffice 转换再解析 —— 与
    /files/<id>/content 对原文件的口径同构，保证弹框里成稿段落与原文件段落同源可比。
    """
    try:
        rounds = FileReviewRoundService.get_by_task(task_id)
        if not rounds:
            return get_error_data_result("任务不存在")
        tenant_id = rounds[0].tenant_id or ""
        if not file_version:
            return get_error_data_result("该轮次无成稿")
        target = next(
            (r for r in rounds if r.file_version == file_version and r.minio_path),
            None,
        )
        if not target:
            return get_error_data_result("该轮次无成稿")
        bucket = f"{tenant_id}-downloads" if tenant_id else ""
        if not bucket:
            return get_error_data_result("该任务归属缺失，无法读取")
        blob = await thread_pool_exec(
            settings.STORAGE_IMPL.get, bucket, f"frv-{task_id}-{file_version}"
        )
        if not blob:
            return get_error_data_result("成稿文件已丢失，请重新发起修复")

        from api.utils.doc_utils import doc_to_docx_via_libreoffice, is_doc_file

        # 防御：成稿落盘一律是 docx（executor._store_version_blob 口径），此处按魔数
        # 分发只为兼容历史脏对象；两头都解不出再报错，不静默降级纯文本（会让弹框
        # 把成稿当纯文本渲染，与保真视图互相矛盾）。
        if is_doc_file(blob):
            docx_blob = await thread_pool_exec(doc_to_docx_via_libreoffice, blob)
            if not docx_blob:
                return get_error_data_result("成稿文件解析失败，请重新发起修复")
            blob = docx_blob
        if blob[:2] != b"PK":
            return get_error_data_result("成稿文件格式异常，请重新发起修复")

        from rag.app.naive import Docx

        paragraphs = await thread_pool_exec(Docx().to_paragraphs, binary=blob)
        return get_json_result(
            data={"file_type": "docx", "file_version": file_version, "paragraphs": paragraphs}
        )
    except Exception:
        logger.exception("file review: version content failed, task_id=%s version=%s", task_id, file_version)
        return get_error_data_result(message="Internal server error")
