# agent/tools/file_review.py
"""C端对话 FileReviewTool：列审核模板 / 发起审核 / 查进度 / 按级别发起修复。

照 agent/tools/template_fill.py 的插件结构：FileReviewToolParam(ToolParamBase) 声明
meta，FileReviewTool(ToolBase) 提供 _invoke，被 component_class 按类名自动发现。
**延迟 import 只针对 rag.svr.file_review.executor**：它是唯一会拉起 python-docx /
settings 那串重依赖的模块，故只在真正要跑/解析级别别名时才 import。Service 与 DB
则是**顶层可达**的（本模块顶层 `from agent.component.file_review import ...`，而后者
顶层 `from api.db.services.file_review_service import ...`，连带 api.db.db_models），
所以「工具注册期不碰 DB」并不是这里的既成事实，别按它推理。

与 T7 画布节点 agent/component/file_review.py 是同一份执行真相的**两个入口**：两者都
只做「建轮次行 + spawn 后台线程」，随即返回；进度与结果一律按轮次行反查（本工具的
action=status 与 T9 的 REST 端点读同一批行）。故本模块不推 SSE、不观察线程 —— 把执行
绑死在某个具体连接上，刷新即丢，且第三个入口无连接可推。

类名必须与节点的 FileReview 不同：component_class 按类名解析且 agent.component 优先于
agent.tools，同名会互相遮蔽（T7 docstring 记录的同一条教训）。
"""

import json
import logging
import os
import time
from abc import ABC

from agent.component.file_review import FILE_ID_INPUT_KEY
from agent.tools.base import ToolBase, ToolMeta, ToolParamBase
from api.db.services.file_review_service import ROUND_STATUS_CN as _ROUND_STATUS_CN
from api.db.services.file_review_service import SEVERITY_CN as _SEVERITY_CN
from common.connection_utils import timeout
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)

# 执行超时与同步轮询上限：与 FillTemplate 同款（env 驱动；等待取 min(90, timeout-10)，
# 保证轮询不会吃满装饰器配额，超时后引导用户用 action=status 异步查询）。
_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))
_MAX_WAIT_SECONDS = min(90, max(_EXEC_TIMEOUT - 10, 10))
_POLL_INTERVAL = 3

# 轮次终态：annotated（首轮审核收口）/ done（修复轮收口）/ failed。
# 工具要能对用户如实说「这轮失败了」，故不能像 Service 的完成口径那样把 failed 当作
# 「没发生」—— 但**不许**回头去引 Service 的完成口径常量（它已随 max_completed_round_no
# 一并删除，见 T9），这里就是权威定义。
_TERMINAL_ROUND_STATUSES = ("annotated", "done", "failed")

# 轮次状态口径（_ROUND_STATUS_CN）与「进行中」两态（RUNNING_ROUND_STATUSES）的唯一实现
# 都在 Service 层：受理闸门 admit_fix_round 也要用它们。本层只 import 中文名映射（两个
# _format_status 渲染点用），不再重复定义，也不再用 RUNNING_ROUND_STATUSES（前置状态由
# 闸门在锁内判定，工具层再判一次就是又一个「检查通过但线程未注册」的窗口）。
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# 返回值会进 LLM 上下文：逐条 issue 全文（上限 1000 字）会刷爆 token，逐项裁剪。
_MAX_LIST_ITEMS = 30
_MAX_ISSUE_CHARS = 120
_MAX_MATCHED_CHARS = 60
_MAX_SUMMARY_CHARS = 400


def _clip(text, limit: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _severity_cn(sev) -> str:
    """级别中文名。

    只把**空值**（None / 空串）折成「未知」；不认识的**字符串**原样透出——它表达的不是
    「没有级别」而是「有一条本工具不认识的级别」，抹成「未知」就把排障线索丢了
    （_severity_summary 的排序键 _SEVERITY_RANK 也恰好把未知串排在最后）。
    唯一硬约束：不许把字面 None 渲染进给用户与 LLM 的文案。
    """
    return _SEVERITY_CN.get(sev) or (sev if isinstance(sev, str) and sev else "未知")


class FileReviewToolParam(ToolParamBase):
    """FileReviewTool 参数声明。"""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "FileReviewTool",
            "description": """文件审核工具。四个 action：

1. list_templates：列出可用的审核模板（名称/用途/id）。用户没指定模板时先调用它。
2. review：对**用户刚上传的文件**发起一轮审核。本 action **不需要 file_id 参数** —— 待审核的文件由运行环境注入，工具自己取。可选 template_id（不传则用招标文件格式审核模板）、kb_ids（检索审核依据的知识库 ID 列表，JSON 数组字符串，如 '["kb1"]'，可选）、user_query（用户的审核要求原话，如「重点看资质和工期」）。提交后同步等待最多约 1 分钟，完成即返回批注摘要；超时返回 task_id，引导用户稍后用 action=status 查。
3. status：按 task_id 查审核进度与批注清单（按级别分组、列出未修复项），以及成稿预览/下载指引。
4. fix：按**用户选定的问题级别**发起一轮修复。task_id 与 levels 均必填（levels 取值 high/medium/low，逗号分隔）。用户说「把严重的问题改掉」「只修中等的」时用它。最多 3 轮；用尽后明确告知「未修复的保持原样」。

使用时机：用户上传文件并表达审核/检查/把关/看看有没有问题的意图时用 review；用户明确表示要针对某个级别的问题动手修改时用 fix。**先展示后修复**：review 之后要把批注读给用户听、由用户决定修哪个级别，不要自动接着调 fix。levels 只能取用户明确说出的级别，不得替用户扩大范围。kb_ids 必须由用户提供（可结合知识库列表工具），不要编造。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：list_templates（列审核模板）/ review（发起审核）/ status（查进度与批注）/ fix（按级别发起修复）。",
                    "enum": ["list_templates", "review", "status", "fix"],
                    "required": True,
                },
                "task_id": {
                    "type": "string",
                    "description": "审核任务 id。action=status / fix 时必填（review 返回的 task_id）。",
                    "default": "",
                    "required": False,
                },
                "template_id": {
                    "type": "string",
                    "description": "审核模板 id（list_templates 返回的 id）。action=review 时可选，不传则用默认的招标文件格式审核模板。",
                    "default": "",
                    "required": False,
                },
                "user_query": {
                    "type": "string",
                    "description": "用户的审核要求原话（如「重点看资质和工期是否满足」）。action=review 时可选。",
                    "default": "",
                    "required": False,
                },
                "kb_ids": {
                    "type": "string",
                    "description": "知识库 ID 列表，JSON 数组字符串，如 '[\"kb123\"]'。action=review 时可选，用于检索审核依据。",
                    "default": "",
                    "required": False,
                },
                "levels": {
                    "type": "string",
                    "description": "要修复的问题级别，逗号分隔，取值 high/medium/low。action=fix 时必填。",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()


class FileReviewTool(ToolBase, ABC):
    """C端对话文件审核工具。"""

    component_name = "FileReviewTool"

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("FileReviewTool"):
            return
        try:
            action = str(kwargs.get("action") or "").strip()
            if action == "list_templates":
                return self._list_templates()
            if action == "review":
                return self._review(kwargs)
            if action == "status":
                return self._status(kwargs)
            if action == "fix":
                return self._fix(kwargs)
            return (f"不支持的 action：{action or '(空)'}。可用 action："
                    "list_templates（列审核模板）/ review（发起审核）/ "
                    "status（查进度与批注）/ fix（按级别发起修复）。")
        except Exception as e:
            logger.exception("FileReviewTool invoke failed")
            self.set_output("_ERROR", str(e))
            return f"文件审核执行失败：{e}"

    # ---------- action: list_templates ----------

    def _list_templates(self):
        from api.db.services.file_review_service import FileReviewTemplateService

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        rows = FileReviewTemplateService.list_enabled(tenant_id)
        if not rows:
            return "当前没有可用的审核模板。"
        lines = [f"共 {len(rows)} 套可用审核模板："]
        for r in rows:
            desc = (r.description or "").strip()
            lines.append(f"- {r.name}（id={r.id}）" + (f"：{desc}" if desc else ""))
        lines.append("请告知用哪套模板、以及本次审核要重点看什么，即可发起审核。")
        return "\n".join(lines)

    # ---------- action: review ----------

    def _review(self, kwargs):
        from api.db.services.file_review_service import (
            FileReviewRoundService,
            FileReviewTemplateService,
        )
        from rag.svr.file_review.executor import DEFAULT_TEMPLATE_ID

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        file_id = self._resolve_file_id()
        if not file_id:
            return ("没有拿到待审核的文件。请先上传要审核的文件，再说出审核要求"
                    "（例如「帮我看看这份标书有没有问题」）。")

        enabled = FileReviewTemplateService.list_enabled(tenant_id)
        # 默认模板取自 executor 常量，不在此复刻字面量：默认值改了这里必须跟着变，
        # 复制一份就是等着漂移（T7 节点同款处理）。
        template_id = str(kwargs.get("template_id") or "").strip() or DEFAULT_TEMPLATE_ID
        if template_id not in [t.id for t in enabled]:
            # 不静默回落到默认值：LLM 编造的模板 id 若被吞掉，轮次行里就会留下一条
            # 「用户用了 X 模板」的假审计记录。把可用清单回给 LLM 让它自查重试。
            lines = [f"审核模板不可用：{template_id}。可用模板："]
            for t in enabled:
                lines.append(f"- {t.name}（id={t.id}）")
            return "\n".join(lines)

        task_id = get_uuid()
        round_no, file_version = FileReviewRoundService.next_round(task_id)
        FileReviewRoundService.create_round(
            task_id=task_id, file_id=file_id, round_no=round_no,
            template_id=template_id,
            user_query=str(kwargs.get("user_query") or "").strip(),
            file_version=file_version, status="reviewing",
            tenant_id=tenant_id, created_by=tenant_id,
            # 形态（list / JSON 文本 / 裸 id）归一是 Service 层 _normalize_kb_ids 的职责，
            # 工具只把解析出的空值折成 None（「这次不用参考资料」的显式表达）。
            kb_ids=self._parse_kb_ids(kwargs.get("kb_ids")) or None,
        )
        spawn_mod.spawn_review_task(task_id)
        return self._poll(task_id, tenant_id)

    # ---------- action: status ----------

    def _status(self, kwargs):
        from api.db.services.file_review_service import FileReviewRoundService

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        task_id = str(kwargs.get("task_id") or "").strip()
        if not task_id:
            return "请提供 task_id（发起审核时返回的任务 id）。"
        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
        if not rounds:
            return "没有找到该审核任务，或无权访问（请确认 task_id 是否正确）。"
        return self._format_status(rounds)

    # ---------- action: fix ----------

    def _fix(self, kwargs):
        from api.db.services.file_review_service import FixAdmissionDenied, admit_fix_round

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        task_id = str(kwargs.get("task_id") or "").strip()
        if not task_id:
            return "请提供 task_id（发起审核时返回的任务 id）。"

        # 级别解析留在工具层，且**刻意比 REST 端点宽松**（接受逗号串/别名，见 _parse_levels）：
        # 这是 LLM 参数解析的实际形态，与 REST「任一项非法即整批拒绝」是有意的既有差异。
        levels = self._parse_levels(kwargs.get("levels"))
        if not levels:
            return "请提供要修复的问题级别 levels（high / medium / low 中的一到多个，逗号分隔）。"

        # 「校验 — 定轮号 — 建轮次 — 起线程」整段交给 Service 层的 admit_fix_round。
        # 为什么必须这样：本函数被 @timeout 丢进**独立 daemon 线程**执行，与 quart 事件
        # 循环是两个线程 —— REST 端点赖以互斥的「临界区内无 await」在这里毫无意义。两入口
        # 并发打同一 task_id 的必死路径（双方都观测到 is_running=False → 各自 next_round()
        # 拿到同一轮号 → 后到者 spawn 静默 no-op → 留下永远没人消费的 fixing 轮次 → 该 task
        # 之后所有 fix/review 被前置闸门永久挡死，只能手工改库）只有 admit_fix_round 内部
        # 那把进程级 threading.Lock 能挡，详见它的 docstring。
        try:
            admit_fix_round(
                task_id=task_id, tenant_id=tenant_id, levels=levels,
                # 工具不提供「本次补充要求」入口：review 阶段的 user_query 已是首轮基准。
                user_query_override="")
        except FixAdmissionDenied as denied:
            # message 本就是面向用户的文案，原样回传；reason 供 API 层映射错误码。
            return denied.message
        return self._poll(task_id, tenant_id)

    # ---------- 轮询与摘要 ----------

    def _poll(self, task_id: str, tenant_id: str) -> str:
        """同步轮询至终态或超时（sleep 在测试中被替换）。

        每一跳都带 tenant 走 get_owned_task：轮询期间租户上下文不变，但重查时仍走
        同一条闸门，避免「入口校验了、产出路径没校验」的半吊子防线。
        """
        from api.db.services.file_review_service import FileReviewRoundService

        waited = 0.0
        while waited < _MAX_WAIT_SECONDS:
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
            if rounds and rounds[-1].status in _TERMINAL_ROUND_STATUSES:
                return self._format_status(rounds)
        return (f"审核任务已提交（task_id={task_id}），目前仍在进行中。"
                "请稍后用 action=status 携带该 task_id 查询进度与批注。")

    def _format_status(self, rounds) -> str:
        """轮次 + 批注统计 + 待修复清单 + 成稿指引。

        action=status 与 _poll 收尾**共用**本方法：两条路径（同步等到 / 稍后查）给出的
        信息必须一致，否则用户前后两次看到的对不上，会以为结果变了。
        """
        from api.db.services.file_review_service import (
            MAX_FIX_ROUNDS,
            FileReviewAnnotationService,
            fix_rounds_left,
        )

        task_id = rounds[0].task_id
        cur = rounds[-1]
        lines = [(f"审核任务 {task_id}：共 {len(rounds)} 轮，当前第 {cur.round_no} 轮"
                  f"（{_ROUND_STATUS_CN.get(cur.status, cur.status)}）。")]
        if cur.status == "failed":
            # error 列允许写满 2000 字（executor 的 err[:2000]），全量拼接会挤占 LLM
            # 上下文——同函数内 summary/issue/matched_text 一律过 _clip，它不能例外。
            lines.append("本轮失败：" + (_clip(cur.error, _MAX_SUMMARY_CHARS)
                                       or "（无详细信息，详见服务端日志）"))
        if cur.summary:
            lines.append("本轮结果：" + _clip(cur.summary, _MAX_SUMMARY_CHARS))

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        lines.append("待修复问题：" + self._severity_summary(pending))
        for a in pending[:_MAX_LIST_ITEMS]:
            line = (f"- [{_severity_cn(a.severity)}] "
                    f"{_clip(a.issue, _MAX_ISSUE_CHARS)}")
            if a.matched_text:
                line += f"（原文：{_clip(a.matched_text, _MAX_MATCHED_CHARS)}）"
            lines.append(line)
        if len(pending) > _MAX_LIST_ITEMS:
            lines.append(f"…（其余 {len(pending) - _MAX_LIST_ITEMS} 条略）")

        # 判据是「有 minio_path 就有可下载成稿」，与 status 无关：T6 交接契约第 2 条
        # 实测 failed 轮次也可能已经落盘（收口那步抛错前成稿已写进 MinIO）。
        produced = [r for r in rounds if r.minio_path]
        if produced:
            latest = produced[-1]
            lines.append(f"已产出第 {latest.round_no} 轮成稿，可在「文件审核」面板中预览/下载。")
        lines.append(f"修复轮次余额：还可发起 {fix_rounds_left(rounds)} 轮"
                     f"（上限 {MAX_FIX_ROUNDS} 轮）。")
        if pending:
            lines.append("如需修复，请告知要修复哪个级别（严重/一般/提示）；"
                         "不需要修复的可以先放着，未修复的问题会保持原样。")
        return "\n".join(lines)

    @staticmethod
    def _severity_summary(items) -> str:
        """按级别统计待修复条数（无则明说「无」，不留空让 LLM 猜）。"""
        if not items:
            return "无"
        counts = {}
        for a in items:
            counts[a.severity] = counts.get(a.severity, 0) + 1
        parts = [f"{_severity_cn(s)} {counts[s]} 条"
                 for s in sorted(counts, key=lambda x: _SEVERITY_RANK.get(x, 99))]
        return f"共 {len(items)} 条（{'、'.join(parts)}）"

    # ---------- 辅助 ----------

    def _get_tenant_id(self) -> str:
        canvas = getattr(self, "_canvas", None)
        if not canvas:
            return ""
        try:
            return canvas.get_tenant_id() or ""
        except Exception:  # noqa: BLE001 — canvas 实现异常类型不一，兜底空串
            return ""

    def _begin_output(self, key: str) -> str:
        """扫 Begin 输出取键值（与 T7 节点 FileReview._begin_output 同款）。

        不按 id 取：Begin 的 id 虽是硬编码的 "begin"，但节点参数可能被写成别的组件的
        引用、或干脆留空，而「本次传入的上传文件」只可能来自 Begin 输出 —— 扫一遍比
        断言 id 更耐用。取不到（含异常）都返回空串，由调用方给出用户可读的拒绝。
        """
        canvas = getattr(self, "_canvas", None)
        if not canvas:
            return ""
        try:
            for cpn in (canvas.components or {}).values():
                obj = cpn.get("obj") if isinstance(cpn, dict) else None
                if obj is not None and getattr(obj, "component_name", "").lower() == "begin":
                    val = (obj.output() or {}).get(key)
                    return val if isinstance(val, str) else ""
        except Exception:
            logger.warning("FileReviewTool._begin_output failed", exc_info=True)
        return ""

    def _resolve_file_id(self) -> str:
        """待审核文件 id（= /documents/upload 返回的 uuid）= 前端送入 Begin 的值。

        刻意不接受 LLM 传参：见模块 docstring —— 编造 uuid 会一路落进轮次行与 MinIO
        对象名，最后以一句「文件不存在」暴露，用户完全无从修正。
        """
        return self._begin_output(FILE_ID_INPUT_KEY)

    @staticmethod
    def _parse_kb_ids(raw) -> list:
        """kb_ids 容错解析：JSON 数组字符串 / 逗号分隔字符串 / list 均可。"""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            s = str(raw).strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
                items = parsed if isinstance(parsed, (list, tuple)) else []
            except Exception:  # noqa: BLE001 — LLM 输出容错：非 JSON 降级为逗号分隔
                items = [x for x in s.split(",") if x.strip()]
        return [str(k).strip() for k in items if str(k).strip()]

    @staticmethod
    def _parse_levels(raw) -> list:
        """levels → high/medium/low 去重且按严重度排序的列表。

        别名表复用 executor._SEVERITY_ALIASES（「级别语义」的唯一真相；不复刻一份，
        否则「警告」在一处算 medium、在另一处被拒）。
        与 executor._norm_severity 的区别是未知词**丢弃而非兜底成 medium**：用户可能
        只想要 low，静默升格会让「只修低级别」变成「修中等级别」，与用户明说的指令相反。
        """
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            items = [str(x) for x in raw]
        else:
            s = str(raw).strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
            except Exception:  # noqa: BLE001 — LLM 输出容错：非 JSON 降级为逗号分隔
                parsed = None
            items = [str(x) for x in parsed] if isinstance(parsed, list) else s.split(",")

        from rag.svr.file_review.executor import _SEVERITY_ALIASES

        out = []
        for it in items:
            sev = _SEVERITY_ALIASES.get(it.strip().lower())
            if sev and sev not in out:
                out.append(sev)
        return sorted(out, key=lambda s: _SEVERITY_RANK[s])
