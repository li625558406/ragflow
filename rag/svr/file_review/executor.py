"""文件审核执行器：多轮状态机（审查轮 reviewing → annotated；修复轮 fixing → done）。

设计边界（为什么这样切）：
- 本模块**不认识**「此刻该审查还是该修复」：那是 T7 画布节点 / T8 对话工具 / T9 REST 的
  职责——它们先写好轮次行（`status='reviewing'` 或 `'fixing'`）再交给 spawn 拉起本模块。
  本模块只按轮次行推进状态机，故三个入口共用同一套逻辑，各自不必复制。
- 纯变换一律委托已测过的兄弟模块，不在这里重复实现：检索拼装 → `kb_aggregator`；
  锚点语义 → `template_fill.docx_utils`（与 B 端填写点直定位、前端 fnvHash32x2 同口径）；
  格式保真替换 → `file_review.patcher`。
- 不 import Quart / SSE：进度由 T9 的 progress 端点按轮次行反查（三入口共用同一份进度真相），
  在此推流会把「执行」与「某个具体连接」绑死。
- T5 契约：`execute_task` 必须保证返回（防重入集合没有超时/看门狗，永久阻塞 = 该 task 被
  永远判为「已在执行中」，只能重启进程恢复）。故每条外部调用都有明确的失败出口。
- 幂等：状态非 reviewing/fixing 的轮次（已完结 / 未知）安静返回，重复 spawn 无副作用；
  同一轮重跑会先删该轮的旧 **AI** 标注（否则进程被杀后重试会留下两套标注；用户手写的
  manual 批注不是本模块的产物，不在清理范围内）。
"""

import asyncio
import json
import logging
import re

from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
)
from common import settings
from rag.svr.file_review import kb_aggregator
from rag.svr.file_review.patcher import apply_patches_to_docx
from rag.svr.template_fill.docx_utils import iter_docx_paragraphs, norm_ws, para_hash32x2

logger = logging.getLogger(__name__)

__all__ = ["FileReviewError", "execute_task"]

DEFAULT_TEMPLATE_ID = "bid_doc_format"
KB_TOP_K = 6
KB_BUDGET_TOKENS = 3000
FILE_TEXT_MAX_CHARS = 40000
TRUNCATED_NOTE = "\n（注：文档较长，以上为可纳入本次审核的正文，其余部分未纳入）"
# 扫描件/图片文档提不出文字：把空/近空正文喂给「请审视全文」的提示词等于请模型编。
# 阈值取 50 是刻意的、且是保护而非妥协：只带页眉（「投标文件（正本）」）或几段 OCR 碎片的
# 扫描件轻松超过几个字，阈值再小就等于不设防，幻觉标注会当成审核结果落库；而真实投标/
# 招标文件正文不可能短于 50 字，生产上零误伤。
MIN_FILE_TEXT_CHARS = 50
QUERY_MAX = 300
LLM_RAW_MAX = 200_000
MAX_FIX_ITEMS = 20
MATCHED_TEXT_MAX = 500
ISSUE_MAX = 1000
SUGGESTION_MAX = 1000

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 各级别的同义写法归一：UI 的三色方案与用户的 levels 过滤都按 high/medium/low 匹配，
# 放任 LLM 自由发挥（"严重"/"Critical"/"warning"）会让这两处同时失配。
_SEVERITY_ALIASES = {
    "high": "high",
    "严重": "high",
    "高": "high",
    "critical": "high",
    "blocker": "high",
    "medium": "medium",
    "中": "medium",
    "中等": "medium",
    "moderate": "medium",
    "warning": "medium",
    "low": "low",
    "低": "low",
    "轻微": "low",
    "minor": "low",
    "info": "low",
}

_ARRAY_GREEDY = re.compile(r"\[[\s\S]*\]")
_ARRAY_LAZY = re.compile(r"\[[\s\S]*?\]")
_OBJECT_GREEDY = re.compile(r"\{[\s\S]*\}")

FIX_SYSTEM = (
    "你是文档修复助手。用户给你一份文档正文和一批待修复的问题标注，"
    "你要为每条标注给出**最小改动**的替换方案：find 必须逐字摘自文档、且全文只出现一次；"
    "replace 是替换后的文本。无法唯一定位或无需改动的条目直接省略，不要编造原文。"
    '只输出 JSON：{"patches": [{"idx": 1, "find": "原文片段", "replace": "新文本"}]}'
)


class FileReviewError(Exception):
    """已知的、可直接展示给用户的中断原因（文案即面向用户的说明）。"""


# ── 入口 ─────────────────────────────────────────────────────────
def execute_task(task_id: str) -> None:
    """按该 task 最后一个轮次的状态推进状态机；无可推进项则安静返回。"""
    try:
        rounds = FileReviewRoundService.get_by_task(task_id)
    except Exception:
        # 连轮次都读不出来（DB 不可用）→ 交给 spawn 的兜底 CAS 再试一次
        logger.exception("file review: load rounds failed, task_id=%s", task_id)
        raise
    cur = rounds[-1] if rounds else None
    if cur is None:
        logger.warning("file review: no round row for task_id=%s", task_id)
        return
    handler = {"reviewing": _run_review_round, "fixing": _run_fix_round}.get(cur.status)
    if handler is None:
        # 已完结（annotated/done/failed）或未知状态：重复 spawn 的幂等出口
        logger.info("file review: round %s status=%s, nothing to do", cur.id, cur.status)
        return
    try:
        handler(cur, _latest_version_name(rounds, cur))
    except Exception as e:
        if isinstance(e, FileReviewError):
            # FileReviewError 的文案本身就是面向用户的说明，原样透出
            err = str(e)
        else:
            # 其余异常的原文（DB/MinIO 地址、内网路径）会经 error 列透给前端：既是内网
            # 拓扑泄露，对用户也毫无指导价值。原文只进日志。
            err = "服务端内部错误，请稍后重试（详见服务端日志）"
        logger.exception("file review round failed, round_id=%s", cur.id)
        try:
            FileReviewRoundService.update_status(cur.id, "failed", error=err[:2000])
        except Exception:
            # 收口都写不进去：绝不能让异常在这里被吞掉——那样 execute_task 正常返回，
            # spawn 的崩溃兜底 CAS 永不触发，该轮次就永久卡在中间态。重新抛出。
            logger.exception("file review: cannot record failure, round_id=%s", cur.id)
            raise


# ── 审查轮 ───────────────────────────────────────────────────────
def _run_review_round(round_row, version_name) -> None:
    tpl = _resolve_template(round_row.template_id)
    blob = _load_input_blob(round_row, version_name)
    items = _load_docx_items(blob)
    file_text = _compose_file_text(items)
    # 没配知识库时**不调**检索：空 kb_ids 的语义是「这次不用参考资料」，不是「检索失败」。
    # 交给 _retrieve_chunks 内部返回 [] 会掩盖调用事实（单测用 spy 锁住了「必须零调用」）。
    kb_ids = _parse_kb_ids(round_row.kb_ids)
    chunks = _retrieve_chunks(round_row.tenant_id, kb_ids, _retrieval_query(round_row, tpl)) if kb_ids else []
    references = kb_aggregator.aggregate_references(kb_chunks=chunks, budget=KB_BUDGET_TOKENS)
    try:
        user_prompt = tpl.user_prompt_template.format(user_query=round_row.user_query or "", file_text=file_text, references=references)
    except Exception as e:
        raise FileReviewError(f"审核模板 {tpl.id} 的提示词占位符不合法：{e}") from e
    raw = _call_llm(round_row.tenant_id, tpl.system_prompt, user_prompt)
    parsed = _parse_annotation_items(raw)
    if parsed is None:
        # None ≠ []：解析失败不能伪装成「审核通过」，否则用户看到的是一个虚假的干净结果
        FileReviewRoundService.update_status(round_row.id, "failed", llm_raw=_clip_raw(raw), error="LLM 输出无法解析为标注列表（原始响应见 llm_raw），请重试")
        return
    collected = _collect_annotations(parsed, tpl, items)
    if parsed and not collected:
        # 与修复轮同构的守卫（见 _run_fix_round 的 idx 判据）：有条目但全不可用（原文与
        # 问题描述皆空）是畸形响应，落成「未发现问题」等于伪造审核通过。收口必须在
        # delete_by_round **之前**：这种情况一条新标注都不该落，旧结果更不该被毁。
        FileReviewRoundService.update_status(round_row.id, "failed", llm_raw=_clip_raw(raw), error="LLM 返回了标注条目但均无可读的原文与问题描述，请重试")
        return
    FileReviewAnnotationService.delete_by_round(round_row.id)  # 重跑先清该轮 AI 标注，保幂等
    stats = _persist_annotations(round_row, collected)
    FileReviewRoundService.update_status(round_row.id, "annotated", llm_raw=_clip_raw(raw), summary=_summary_from_stats(stats))


# ── 修复轮 ───────────────────────────────────────────────────────
def _run_fix_round(round_row, version_name) -> None:
    blob = _load_input_blob(round_row, version_name)
    pending = FileReviewAnnotationService.list_pending_by_task(round_row.task_id)
    if not pending:
        FileReviewRoundService.update_status(round_row.id, "done", summary="没有待修复的问题")
        return
    if blob[:2] != b"PK":
        # 非 docx 一律不自动改：把解码文本当新版本存回去会毁掉原件，还会成为下一轮
        # 审核的输入。诚实收口 done（不是 failed——没有可重试的东西），标注保持原样。
        FileReviewRoundService.update_status(round_row.id, "done", summary=(f"共 {len(pending)} 项待修复；该文件类型不支持自动修复（v1 仅支持 Word .docx），请按批注手动修改"))
        return
    chosen = pending[:MAX_FIX_ITEMS]
    items = _load_docx_items(blob)
    file_text = _compose_file_text(items)
    raw = _call_llm(round_row.tenant_id, FIX_SYSTEM, _build_fix_prompt(round_row, file_text, chosen))
    parsed = _parse_patch_items(raw)
    if parsed is None:
        FileReviewRoundService.update_status(round_row.id, "failed", llm_raw=_clip_raw(raw), error="LLM 输出无法解析为修复补丁列表（原始响应见 llm_raw），请重试")
        return
    patches, by_pos = _plan_patches(parsed, chosen)
    if parsed and not patches:
        # 有条目但 idx 全对不上：畸形响应，不能伪装成「无需改动」
        FileReviewRoundService.update_status(round_row.id, "failed", llm_raw=_clip_raw(raw), error="修复补丁的 idx 均无法与本轮标注对应，请重试")
        return
    if not patches:
        # 合法但空：LLM 判断无需改动。判 failed 会诱发无效重试
        summary = f"共 {len(pending)} 项待修复；本轮未产出可落地的修复补丁（保持原样）" + _capped_note(len(pending), len(chosen))
        FileReviewRoundService.update_status(round_row.id, "done", llm_raw=_clip_raw(raw), summary=summary)
        return
    new_blob, applied = apply_patches_to_docx(blob, patches)
    extra = {}
    if any(applied):
        # 先落盘再改任何状态：对象名由 task_id + file_version 决定，重试覆盖同名对象，天然幂等；
        # 反过来（先置 fixed 后落盘）一旦 Put 失败，标注说已修、文档没改、重试只看得到
        # 「没有待修复的问题」——没有任何自动路径能回到正确状态。
        extra["minio_path"] = _store_version_blob(round_row, new_blob)
    fixable = _fixable_positions(chosen, patches, by_pos, applied)
    open_n = len(chosen) - len(fixable)
    # open_n 有两种成因：补丁没落地（原文未能唯一定位）与补丁落地了但 find 与本条标注不符
    # （见 _fixable_positions）。文案必须同时覆盖，只写「未能唯一定位」会把后一种误报成定位问题。
    summary = f"本轮修复 {len(fixable)} 项" + (f"；{open_n} 项未能自动修复（原文未能唯一定位或补丁与本条不符，保持原样）" if open_n else "") + _capped_note(len(pending), len(chosen))
    # 收口先于置 fixed：标注状态是「本轮已完成」的派生结果，轮次行写不进去时标注必须留
    # 在 open（重试才有意义）。派生状态不得抢先于权威记录落地。
    FileReviewRoundService.update_status(round_row.id, "done", llm_raw=_clip_raw(raw), summary=summary, **extra)
    settled = _settle_annotations(chosen, patches, by_pos, applied)
    if settled != len(fixable):
        logger.warning("file review: marked %s/%s annotations fixed, round_id=%s", settled, len(fixable), round_row.id)


# ── 外部依赖（模块级 seam，便于测试注入）───────────────────────────
def _load_input_blob(round_row, version_name) -> bytes:
    """修复轮的输入是**上一轮修复后的版本**（多轮叠加），无版本则回原件。"""
    if version_name:
        blob = settings.STORAGE_IMPL.get(f"{round_row.tenant_id}-downloads", version_name)
        if blob:
            return blob
        logger.warning("file review: version blob missing, round_id=%s name=%s", round_row.id, version_name)
    return _load_original_blob(round_row.tenant_id, round_row.file_id)


def _load_original_blob(tenant_id: str, file_id: str) -> bytes:
    """取文件原件字节；三级兜底与 file_api 的下载链路同口径，全部落空才报错。"""
    from api.db.services.file2document_service import File2DocumentService
    from api.db.services.file_service import FileService

    cands = []
    ok, file = FileService.get_by_id(file_id)
    if ok and file is not None:
        cands.append((file.parent_id, file.location))
    try:
        cands.append(File2DocumentService.get_storage_address(file_id=file_id))
    except Exception:
        logger.exception("file review: resolve storage address failed, file_id=%s", file_id)
    for bucket, name in cands:
        if not bucket or not name:
            continue
        blob = settings.STORAGE_IMPL.get(bucket, name)
        if blob:
            return blob
    blob = settings.STORAGE_IMPL.get(f"{tenant_id}-downloads", file_id)
    if blob:
        return blob
    raise FileReviewError(f"文件不存在或已删除（file_id={file_id}），无法审核")


def _store_version_blob(round_row, blob: bytes) -> str:
    """把修复后的文档存为版本对象，返回对象名。

    对象名只用 task_id + file_version（不含轮次号）是刻意的：同一版本号重复跑会**覆盖**，
    而不是不断堆孤儿对象；重试也天然幂等。
    """
    name = f"frv-{round_row.task_id}-{round_row.file_version}"
    settings.STORAGE_IMPL.put(f"{round_row.tenant_id}-downloads", name, blob)
    return name


def _retrieve_chunks(tenant_id: str, kb_ids: list, query: str) -> list:
    """检索参考资料。失败**不吞**：静默按「零参考」继续，等于告诉用户「标准就这些」。"""
    if not kb_ids:
        return []
    from rag.svr.template_fill.executor import retrieve_slot

    return asyncio.run(retrieve_slot(tenant_id, kb_ids, query, top_k=KB_TOP_K))


def _call_llm(tenant_id: str, system_prompt: str, user_prompt: str) -> str:
    """同步线程内跑一次 chat 补全（线程无事件循环，必须 asyncio.run）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType

    mdl = LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, LLMType.CHAT))
    return asyncio.run(mdl.async_chat(system_prompt, [{"role": "user", "content": user_prompt}]))


# ── 轮次元数据 ───────────────────────────────────────────────────
def _latest_version_name(rounds: list, upto) -> str | None:
    """取 round_no <= upto 的最近一个已落盘版本对象名（多轮叠加的输入基线）。

    显式选 round_no 最大者（同号时后者胜），**不依赖入参顺序**：get_by_task 目前保证
    升序，但把正确性寄托在调用方的排序上，换个调用路径就会静默退回到更旧的版本。
    排除当前轮自身，避免把「本轮的产物」当成「本轮的输入」。
    """
    best = None
    for r in rounds:
        if r.id == upto.id or r.round_no > upto.round_no or not r.minio_path:
            continue
        if best is None or r.round_no >= best.round_no:
            best = r
    return best.minio_path if best else None


def _parse_kb_ids(raw) -> list:
    """把轮次行里的 kb_ids（JSON 文本）还原成 id 列表；脏值降级为空列表并告警。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x) for x in raw if x]
    try:
        val = json.loads(raw)
    except Exception:  # noqa: BLE001 — 列无值域约束，脏 JSON 是预期输入而非缺陷，降级空列表并告警
        logger.warning("file review: kb_ids is not valid JSON, ignored: %r", raw)
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    logger.warning("file review: kb_ids JSON is not a list, ignored: %r", raw)
    return []


def _resolve_template(template_id):
    tpl = FileReviewTemplateService.get_by_id(template_id) if template_id else None
    if tpl is None:
        tpl = FileReviewTemplateService.get_by_id(DEFAULT_TEMPLATE_ID)
    if tpl is None:
        raise FileReviewError(f"审核模板不存在（template_id={template_id or DEFAULT_TEMPLATE_ID}），请检查预置模板是否已初始化")
    return tpl


def _template_types(tpl) -> list:
    raw = getattr(tpl, "annotation_types", None)
    if not raw:
        return []
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001 — annotation_types 是人工/旧版写的 JSON 文本，坏了按「无声明」处理
        logger.warning("file review: template %s annotation_types unparsable: %r", tpl.id, raw)
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


def _retrieval_query(round_row, tpl) -> str:
    parts = [getattr(tpl, "name", "") or "", round_row.user_query or ""]
    q = " ".join(p.strip() for p in parts if p and p.strip())
    return q[:QUERY_MAX] or "招标文件要求"


# ── 字符串归一 ───────────────────────────────────────────────────
def _clean_str(value, max_len: int = 0) -> str:
    """只接受 str（其余一律当没填）；剥控制字符（防注入进 LLM 提示或前端渲染）。"""
    if not isinstance(value, str):
        return ""
    out = _CTRL_RE.sub("", value).strip()
    return out[:max_len] if max_len > 0 else out


def _norm_token(value, fallback: str = "other") -> str:
    """类型/来源类短标记归一：小写、去控制符、限长。

    刻意**不做白名单改写**——模板自带 annotation_types 声明了 scope，越界与否只用于
    告警（见 _collect_annotations），不在这里篡改 LLM 的判断。
    """
    return _clean_str(value, 32).lower() or fallback


def _norm_severity(value) -> str:
    token = _clean_str(value, 32).lower()
    sev = _SEVERITY_ALIASES.get(token)
    if sev is None:
        logger.warning("file review: unknown severity %r, fallback to medium", value)
        return "medium"
    return sev


def _clip_raw(raw) -> str:
    return (raw or "")[:LLM_RAW_MAX]


# ── 文档装配与锚点 ───────────────────────────────────────────────
def _load_docx_items(blob: bytes) -> list:
    if blob[:2] != b"PK":
        raise FileReviewError("暂不支持审核该文件类型（当前仅支持 Word .docx），请转换后重试")
    try:
        return iter_docx_paragraphs(blob)
    except Exception as e:
        raise FileReviewError(f"无法解析文档（可能已损坏或非 .docx 格式）：{e}") from e


def _compose_file_text(items: list) -> str:
    text = "\n".join(it["text"].strip() for it in items if (it.get("text") or "").strip())
    if len(norm_ws(text)) < MIN_FILE_TEXT_CHARS:
        # 扫描件/图片文档提不出文字：把空正文喂给「请审视全文」的提示词等于请模型编
        raise FileReviewError("文档正文为空或无法提取文字（可能是扫描件/图片文档），无法审核")
    if len(text) > FILE_TEXT_MAX_CHARS:
        # 静默截断会让模型断言「全文未提及某条款」——截断必须在正文里说明
        return text[:FILE_TEXT_MAX_CHARS] + TRUNCATED_NOTE
    return text


def _count_occurrences(norm: str, needle: str) -> int:
    """norm 通道的 indexOf step+1 计数（允许重叠），与 docx_utils 生成 a_occ 时同口径：
    下划线串「＿＿＿＿」含「＿＿＿」在 step+1 下算 2 次，str.count 非重叠只算 1 次。"""
    count, start = 0, 0
    while True:
        idx = norm.find(needle, start)
        if idx < 0:
            return count
        count += 1
        start = idx + 1


def _compute_anchor(addr_items: list, matched_text: str) -> dict:
    """按 matched_text 反查权威锚点 {p_idx,p_hash,a_occ,p_total}；不唯一/定位不到 → {}。

    要求**全文唯一命中**（唯一段落 + 段内唯一位置），与 patcher 修复侧的唯一定位口径
    一致。宁可没有跳转链接，也不能给出指向错误位置的链接。
    """
    needle = norm_ws(matched_text)
    if len(needle) < 2:
        return {}  # 单字匹配无法稳定定位
    hits = []
    for it in addr_items:
        norm = norm_ws(it.get("text") or "")
        if not norm or needle not in norm:
            continue
        occ = _count_occurrences(norm, needle)
        if occ:
            hits.append((it, occ))
    if len(hits) != 1:
        return {}
    it, occ = hits[0]
    if occ != 1:
        return {}
    return {
        "p_idx": it["index"],
        "p_hash": para_hash32x2(norm_ws(it["text"])),
        "a_occ": occ,
        "p_total": len(addr_items),
    }


# ── LLM 输出解析 ─────────────────────────────────────────────────
def _json_candidates(raw: str) -> list:
    """可能的 JSON 片段，按「越可能整体成立」的顺序排列。

    顺序是硬要求：贪心 `{...}` 排在数组之前，会在裸数组 `[{...},{...}]` 里先吃掉第一个
    内层对象，得到 1 条而不是 N 条（或直接解析失败 → 0 条 → 伪造「审核通过」）。
    """
    cands = [raw]
    for rx in (_ARRAY_GREEDY, _ARRAY_LAZY, _OBJECT_GREEDY):
        m = rx.search(raw)
        if m:
            cands.append(m.group(0))
    return cands


def _items_from(raw, key: str):
    """从候选片段里取出目标数组；取不到返回 None（**不返回 []**）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    for cand in _json_candidates(raw):
        try:
            val = json.loads(cand)
        except Exception:  # noqa: BLE001, S112 — 候选片段本就是自由文本，非 JSON 是常态，继续试下一个
            continue
        if isinstance(val, dict):
            val = val.get(key)
        if not isinstance(val, list):
            continue
        if not all(isinstance(x, dict) for x in val):
            # ["无问题"] / [1,2] 是散文噪声，不是「零标注」——折成 [] 会伪造「审核通过」
            continue
        return val
    return None


def _parse_annotation_items(raw):
    """审查轮输出 → 标注 dict 列表；无法解析返回 None（与「空数组」严格区分）。"""
    return _items_from(raw, "annotations")


def _parse_patch_items(raw):
    """修复轮输出 → 补丁 dict 列表；无法解析返回 None。"""
    return _items_from(raw, "patches")


# ── 标注落库 ─────────────────────────────────────────────────────
def _collect_annotations(parsed: list, tpl, addr_items: list) -> list:
    """把 LLM 的原始条目整理成可落库的形态。

    type 越出模板声明的 annotation_types 时**记录但不丢弃**：静默丢问题比多一个标签
    更糟；越界本身是「提示词需要修」的信号，故打 warning 供排查。
    """
    allowed = _template_types(tpl)
    out = []
    for item in parsed:
        matched = _clean_str(item.get("matched_text"), MATCHED_TEXT_MAX)
        issue = _clean_str(item.get("issue"), ISSUE_MAX)
        if not matched and not issue:
            continue  # 空壳：既无原文也无可读问题，落库只是噪声
        ann_type = _norm_token(item.get("type"), "other")
        if allowed and ann_type not in allowed:
            logger.warning("file review: annotation type %r outside template scope %r", ann_type, allowed)
        out.append(
            {
                "matched_text": matched,
                "type": ann_type,
                "severity": _norm_severity(item.get("severity")),
                "issue": issue,
                "suggestion": _clean_str(item.get("suggestion"), SUGGESTION_MAX),
                "anchor": json.dumps(_compute_anchor(addr_items, matched), ensure_ascii=False),
            }
        )
    return out


def _persist_annotations(round_row, collected: list) -> dict:
    stats = {"total": 0, "high": 0, "medium": 0, "low": 0}
    for ann in collected:
        FileReviewAnnotationService.create(
            round_id=round_row.id,
            task_id=round_row.task_id,
            file_id=round_row.file_id,
            file_version=round_row.file_version,
            anchor=ann["anchor"],
            matched_text=ann["matched_text"],
            ann_type=ann["type"],
            severity=ann["severity"],
            issue=ann["issue"],
            suggestion=ann["suggestion"],
            source="ai",
            status="open",
            tenant_id=round_row.tenant_id,
            created_by=round_row.created_by or "",
        )
        stats["total"] += 1
        stats[ann["severity"]] += 1
    return stats


def _capped_note(total: int, chosen_n: int) -> str:
    """待修数超过单轮上限时补一句真实总量，否则用户会把「本轮处理数」当成「问题总数」，
    剩下那些既不进摘要也不进任何计数。"""
    if total <= chosen_n:
        return ""
    return f"；本轮仅处理前 {chosen_n} 项，其余 {total - chosen_n} 项留待下一轮"


def _summary_from_stats(stats: dict) -> str:
    """摘要由**实际落库**的标注派生，不用 LLM 自述的 summary——预置模板输出的是裸数组，
    没有 summary 槽位；自述一旦与实际条数不符，面板就会自相矛盾。"""
    if not stats.get("total"):
        return "未发现问题"
    return f"共 {stats['total']} 个问题（高 {stats['high']} / 中 {stats['medium']} / 低 {stats['low']}）"


# ── 修复轮辅助 ───────────────────────────────────────────────────
def _build_fix_prompt(round_row, file_text: str, chosen: list) -> str:
    lines = []
    for i, a in enumerate(chosen, 1):
        lines.append(f"[{i}] 类型={a.type} 级别={a.severity} 原文：{a.matched_text or '（未给出原文）'}")
        lines.append(f"    问题：{a.issue}")
        if a.suggestion:
            lines.append(f"    建议：{a.suggestion}")
    return (
        "用户需求：" + (round_row.user_query or "（无）") + "\n\n"
        "待修复问题：\n" + "\n".join(lines) + "\n\n"
        "文档正文：\n" + file_text + "\n\n"
        "请为每条问题给出最小改动的替换方案，输出 JSON：\n"
        '{"patches": [{"idx": 1, "find": "原文片段", "replace": "新文本"}]}\n'
        "find 必须是文档中逐字出现且全文唯一的片段，否则该条会被跳过。"
    )


def _plan_patches(parsed: list, chosen: list) -> tuple:
    """补丁数组 + {补丁下标: 标注序号(1-based)} 映射。

    find/replace **原样透传**（哪怕是 None）：patcher 对 None / 非 str 的跳过规则已被
    对抗测试锁死，在这里预处理会把那些保护绕过去。
    """
    patches, by_pos, seen = [], {}, set()
    for item in parsed:
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx in seen or not 1 <= idx <= len(chosen):
            continue
        seen.add(idx)
        patches.append({"find": item.get("find"), "replace": item.get("replace")})
        by_pos[len(patches) - 1] = idx
    return patches, by_pos


def _patch_matches_annotation(ann, patch) -> bool:
    """补丁的 find 与该标注的 matched_text 是否指向同一段话。

    判据取**双向**子串：find 合法地会比 matched_text 更长（向外扩上下文以保证全文唯一，
    这正是 FIX_SYSTEM 要求的）或更短（取更短的唯一子串），相等只是其中一种特例。
    matched_text 为空（标注允许只给问题描述不给原文）时无从校验，**按序号认**——
    不因噎废食地把这类条目挡在修复之外。find 可能是 None / 非 str（_plan_patches 刻意
    原样透传），非 str 一律当没给。
    """
    raw_find = patch.get("find") if isinstance(patch, dict) else None
    needle = norm_ws(ann.matched_text or "")
    find = norm_ws(raw_find) if isinstance(raw_find, str) else ""
    if not needle or not find:
        return True
    return needle in find or find in needle


def _fixable_positions(chosen: list, patches: list, by_pos: dict, applied: list) -> list:
    """真正该标 fixed 的补丁下标：补丁落地 + 序号对得上 + find 确实在改这条标注指的那段话。

    LLM 偶发把 idx 串位时 find 仍可能唯一定位（改的是文档里另一处），只按序号盖
    「已修复」就是假修复：错的标注变 fixed，原问题分毫未动。
    """
    out = []
    for pos, ok in enumerate(applied):
        if not ok:
            continue
        idx = by_pos.get(pos)
        if idx is None:
            continue
        if _patch_matches_annotation(chosen[idx - 1], patches[pos]):
            out.append(pos)
    return out


def _settle_annotations(chosen: list, patches: list, by_pos: dict, applied: list) -> int:
    """把真正落地的补丁对应标注标 fixed，返回条数；未生效的保持 open（未修复好保持原样）。

    只负责写标注状态：调用方须先算出 _fixable_positions（摘要要用）并完成轮次收口
    （见 _run_fix_round 的顺序约定）。
    """
    fixable = set(_fixable_positions(chosen, patches, by_pos, applied))
    for pos, ok in enumerate(applied):
        idx = by_pos.get(pos)
        if ok and idx is not None and pos not in fixable:
            logger.warning("file review: patch find does not match annotation %s, left open", chosen[idx - 1].id)
    fixed = 0
    for pos in fixable:
        if FileReviewAnnotationService.update_status(chosen[by_pos[pos] - 1].id, "fixed"):
            fixed += 1
    return fixed
