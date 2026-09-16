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
"""文件审核 REST API（4 端点）。

  GET  /file/review/templates                      可用审核模板（系统预置 + 本租户）
  GET  /file/review/file/<file_id>/state            以文件为中心的权威读模型
  POST /file/review/<task_id>/fix                   发起一轮修复
  POST /file/review/annotation/<aid>/status          人工闭环单条标注（兜底出口）

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
* 本模块**不返回**成稿的下载 URL：doc.object 是对象名（或原件 file_id），前端沿用既有
  GET /api/v1/files/<id> 取 blob —— 该路由对 `{tenant}-downloads` 桶有兜底、并按 zip 魔数
  识别 docx，审核面板的保真渲染本来就依赖它。
"""
import json
import logging

from quart import Blueprint, request

from api.apps import login_required
from api.db.services.file_review_service import (
    MAX_FIX_ROUNDS,
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
    compose_fix_query,
    fix_rounds_left,
)
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_argument_result,
    get_error_data_result,
    get_json_result,
)
from common.constants import RetCode
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)

manager = Blueprint("file_review_api", __name__)

# 轮次状态口径与 T6 executor 一致（取值见 db_models.FileReviewRound.status 注释）。
# 只列「线程还会回来写这一行」的两态：它们与 spawn 的 _running_tasks 是同一件事的两个视角，
# 是 fix 端点必须拒的两个前置条件。
_RUNNING_ROUND_STATUSES = ("reviewing", "fixing")

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
    return {
        "id": row.id,
        "round_no": row.round_no,
        "status": row.status,
        "file_version": row.file_version,
        "template_id": row.template_id or "",
        "user_query": row.user_query or "",
        "summary": row.summary or "",
        "error": row.error or "",
        "minio_path": row.minio_path or "",
        # 判据是「有对象名」而不是「状态是 done」：T6 的修复轮先落盘、后收口，收口那一步
        # 抛错时轮次被兜底写成 failed，而成稿**已经落盘**（交接契约第 2 条）。按状态判会把
        # 已修好的成稿藏起来。
        "produced": bool(row.minio_path),
    }


def _doc_payload(rounds: list, file_id: str) -> dict:
    """该展示的文档：最近一次落盘的成稿；从未落盘时退回原件。

    显式选 round_no 最大且带 minio_path 的轮次（不依赖入参顺序），与 T6
    _latest_version_name 的基线口径一致 —— 面板必须展示**最后一版**，否则用户看的是中间稿、
    批注却来自最终轮。
    """
    best = None
    for r in rounds:
        if not r.minio_path:
            continue
        if best is None or (r.round_no or 0) >= (best.round_no or 0):
            best = r
    if best is None:
        # 还没有成稿：用户看的就是原件。version 留空串而不是 "v1" ——
        # 「首轮版本号 = v1」是 T7/T8 的命名习惯，不是本层的契约，不该由这里替前端断言。
        return {"object": file_id, "version": ""}
    return {"object": best.minio_path, "version": best.file_version}


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


def _fix_base_query(rounds: list, extra: str = "") -> str:
    """修复轮的 user_query 基准 = **首轮**原始需求（可选追加本次补充说明）。

    基准只能取首轮：修复轮的 user_query 里已经带着上一轮写进去的级别指令，拿它当基准会把
    历轮指令叠起来（见 compose_fix_query 的 docstring，T8 实测踩过）。
    """
    base = (rounds[0].user_query or "").strip() if rounds else ""
    extra = (extra or "").strip()
    if not extra:
        return base
    return f"{base}\n本次补充要求：{extra}" if base else f"本次补充要求：{extra}"


@manager.route("/file/review/templates", methods=["GET"])
@login_required
@add_tenant_id_to_kwargs
async def list_review_templates(tenant_id: str | None = None):
    """可用审核模板：系统预置（tenant_id == ""）+ 本租户自有，且 enabled == 1。"""
    try:
        rows = FileReviewTemplateService.list_enabled(tenant_id or "")
        return get_json_result(data={"templates": [_template_payload(r) for r in rows]})
    except Exception:
        logger.exception("file review: list templates failed")
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/file/<file_id>/state", methods=["GET"])
@login_required
async def review_state(file_id: str):
    """以文件为中心的权威读模型：前端只凭 file_id 就能渲染进度与批注。

    doc.object = 该展示的文档对象名（最近一次落盘的成稿；无成稿时为原件 file_id），
    前端沿用既有 GET /api/v1/files/<id>。
    annotations = 该文件**全部**标注（跨轮次/版本/任务）—— 不能按成稿版本过滤：只有审查轮
    产标注且其 file_version 恒为首轮版本，成稿是 v2/v3/v4，按成稿版本过滤会一条都查不到。

    不按 tenant 过滤（读路径，见模块 docstring 的「读不限、写严格」）。
    """
    try:
        rounds = FileReviewRoundService.get_by_file(file_id)
        anns = FileReviewAnnotationService.list_by_file(file_id)
        return get_json_result(data={
            "file_id": file_id,
            "task_id": rounds[-1].task_id if rounds else None,
            "rounds": [_round_payload(r) for r in rounds],
            "current": _round_payload(rounds[-1]) if rounds else None,
            "doc": _doc_payload(rounds, file_id),
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
async def fix_review(task_id: str, tenant_id: str | None = None):
    """发起一轮修复。body: {"levels": ["high","medium"], "user_query": 可选补充说明}"""
    try:
        body = await request.get_json(silent=True) or {}
        levels = _parse_levels(body.get("levels"))
        if not levels:
            return get_error_argument_result(
                "levels 必须是非空数组，取值只能是 high / medium / low")

        # ══ 临界区开始：到 spawn_review_task 之前**不许出现任何 await** ══════════
        # 这段是「校验 — 定轮号 — 建轮次 — 起线程」的互斥边界。quart 是单进程单事件循环
        # （WS 默认 1，见 docker/launch_backend_service.sh），只要区内没有 await，另一个请求
        # 就插不进来。没有这道边界会怎样：两个并发 fix 各自观测到 is_running=False、
        # next_round() 取到**同一轮号**（(task_id, round_no) 无唯一约束），后到者的
        # spawn_review_task 命中 _running_tasks 后**静默 return**，于是留下一条永远没人消费的
        # fixing 轮次 —— 该 task 之后所有 fix/review 会被下面这些闸门永久挡死
        # （T6/T8 实测的必死路径）。区内要加 await，请先把 await 挪到临界区之外。
        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id or "")
        if not rounds:
            # 「不存在」与「不是你的」共用同一句文案：区分会把「他人 task 是否存在」
            # 这一信息透给攻击者（get_owned_task 的 docstring 同款口径）。
            return get_error_data_result("文件审核任务不存在或无权访问")
        cur = rounds[-1]
        if cur.status in _RUNNING_ROUND_STATUSES:
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="当前轮次仍在进行中，请等它结束后再发起修复")
        if spawn_mod.is_running(task_id):
            # 轮次行可能**已经**是终态而线程还在收尾：executor._run_fix_round 先置轮次终态、
            # 再逐条写最多 MAX_FIX_ITEMS(20) 条标注，中间隔着 20 次 DB 往返。此刻
            # spawn_review_task 会静默 no-op，新轮次永远等不到线程去消费它（T8 实测）。
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="上一轮审核正在收尾，请稍等片刻后重试")
        left = fix_rounds_left(rounds)
        if left <= 0:
            return get_json_result(
                code=RetCode.OPERATING_ERROR,
                message=f"已达到最大修复轮次（{MAX_FIX_ROUNDS} 轮），未修复的问题请按批注手动处理")

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        if not [a for a in pending if a.severity in levels]:
            # 所选级别没有待修项时必须拦下：executor 的 chosen 不带 severity 谓词，
            # 建出来的轮次会去修**别的**级别（把用户没选中的问题改掉），或者空转一轮
            # 白白烧掉一次重试机会。
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="所选级别没有待修复的问题，无需发起修复")

        no, version = FileReviewRoundService.next_round(task_id)
        rid = FileReviewRoundService.create_round(
            task_id=task_id,
            file_id=cur.file_id,
            round_no=no,
            template_id=cur.template_id,
            # 基准取首轮原始需求，不用上一轮（见 _fix_base_query 的说明）。
            user_query=compose_fix_query(
                _fix_base_query(rounds, body.get("user_query")), levels),
            file_version=version,
            status="fixing",
            # tenant 必须来自当前用户，不能留空：get_owned_task 要求「任一轮 tenant 不符
            # 即拒绝」，写空串的轮次连自己都过不了闸门；且 executor 用 `{tenant}-downloads`
            # 选桶，空串会让成稿落进 `-downloads` 桶、谁都取不到。
            tenant_id=tenant_id or "",
            created_by=tenant_id or "",
            # 继承本轮 KB 配置：修复轮不做检索，但 kb_ids 是「这一轮用了哪些知识库」的
            # 可追溯配置，留空会让它无从知晓。
            kb_ids=cur.kb_ids,
        )
        spawn_mod.spawn_review_task(task_id)
        # ══ 临界区结束 ══════════════════════════════════════════════════════
        return get_json_result(data={"task_id": task_id, "round_id": rid, "round_no": no,
                                     "status": "fixing", "fix_rounds_left": left - 1})
    except Exception:
        logger.exception("file review: start fix round failed, task_id=%s", task_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/annotation/<annotation_id>/status", methods=["POST"])
@login_required
@add_tenant_id_to_kwargs
async def update_annotation_status(annotation_id: str, tenant_id: str | None = None):
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
        if not FileReviewRoundService.get_owned_task(row.task_id, tenant_id or ""):
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewAnnotationService.update_status(annotation_id, status):
            # 上面刚查到行，这里再失败只可能是并发删除：同样按「不存在」回，不泄露时序差异。
            return get_error_data_result("批注不存在或无权访问")
        return get_json_result(data={"annotation_id": annotation_id, "status": status})
    except Exception:
        logger.exception("file review: update annotation status failed, aid=%s", annotation_id)
        return get_error_data_result(message="Internal server error")
