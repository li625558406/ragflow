# rag/svr/document_rewrite/rewriter.py
# -*- coding: utf-8 -*-
"""LLM 按节重写（设计 §5）。

投喂：用户指令 + 目标节全文 + 全文目录 + 前后节标题（不投喂全文正文）。
输出：JSON {"paragraphs": ["新段落1", ...]}，纯段落文本，LLM 不产格式标记。
代码端兜底：LLM 调用异常/非法 JSON/空段落重试 1 次；段数 >50、单段 >2000 字、
输出体量低于源文 _MIN_OUTPUT_RATIO 一律拒绝（返回 None 触发重试）——
2026-09-25 事故教训：超限静默截断曾把 92 段砍到 50 段仍照常落版本，整节尾部
内容被销毁且无人知晓，宁诚实失败不静默丢文；失败不产生版本（由工具层保证）。
投喂正文 >3 万字直接报错（不静默截断）。
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# 防御阈值：单节投喂上限（> 判断，整 3 万不报错）、段落数上限、单段字数上限、
# 输出体量闸（输出总字数 < 源文 × ratio 拒绝，防 LLM 漏段/截断静默丢内容）
_MAX_SECTION_CHARS = 30000
_MAX_PARAGRAPHS = 50
_MAX_PARA_CHARS = 2000
_MIN_OUTPUT_RATIO = 0.6
# 体量闸下限：源文不足该字数不启用闸（微小节/引导词场景防误拒）
_VOLUME_GATE_MIN_SRC = 200

# JSON 对象提取：先贪婪（应对正文中含 `}` 的合法对象），
# 贪婪解析失败再非贪婪（应对 LLM 输出尾部带花括号杂文：贪婪把杂文吞进去导致解析失败）。
_JSON_OBJ_RES = (
    re.compile(r"\{.*\}", flags=re.DOTALL),
    re.compile(r"\{.*?\}", flags=re.DOTALL),
)

_REWRITE_SYSTEM = (
    "你是公文文档改写专家。用户会给出一份文档的目录、某一节的当前内容，以及针对该节的重写要求。"
    "你的任务：只重写这一节，输出该节的新正文段落。要求：\n"
    "1. 保持公文体例与书面语，忠实结合用户要求改写；与目录中其他节衔接自然，不重复其他节内容；\n"
    "2. 输出是纯段落数组，不使用任何 Markdown 标记（不要 #、*、-、表格）；\n"
    "3. 只输出一个 JSON 对象，格式严格为：{\"paragraphs\": [\"新段落1\", \"新段落2\"]}\n"
    "4. 不要输出任何解释、前后缀或代码块标记之外的内容。"
)


def _parse_paragraphs(txt) -> list[str] | None:
    """解析 LLM 输出为段落列表；不合法/全空/超限返回 None（调用方据此重试）。

    容忍 ```json 围栏与前后杂文（先贪婪后非贪婪抽第一个合法 {...}，照 executor._extract_json
    已踩坑模式）；仅保留非空 str 并 strip。超限策略（2026-09-25 事故回归）：段数 >50、
    单段 >2000 字一律拒绝返回 None，绝不静默截断——截断曾致整节尾部内容被销毁仍照常落版本。"""
    if not isinstance(txt, str) or not txt.strip():
        return None
    data = None
    for obj_re in _JSON_OBJ_RES:
        m = obj_re.search(txt)
        if not m:
            continue
        try:
            parsed = json.loads(m.group(0))
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    if data is None:
        return None
    raw = data.get("paragraphs")
    if not isinstance(raw, list):
        return None
    paras = []
    overlong = False
    for item in raw:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        if len(item) > _MAX_PARA_CHARS:
            overlong = True
            continue
        paras.append(item)
    if overlong:
        logger.warning("[rewrite] paragraph(s) over %s chars, rejected", _MAX_PARA_CHARS)
        return None
    if not paras:
        return None
    if len(paras) > _MAX_PARAGRAPHS:
        logger.warning("[rewrite] paragraph count %s over limit %s, rejected",
                       len(paras), _MAX_PARAGRAPHS)
        return None
    return paras


def _build_chat_mdl(tenant_id: str):
    """照 executor.py 模式构造默认对话模型（延迟 import，纯函数部分可独立单测）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from common.constants import LLMType as _LT
    from api.db.services.llm_service import LLMBundle
    return LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, _LT.CHAT))


async def rewrite_section(tenant_id: str, instruction: str, section_title: str,
                          section_text: str, outline_text: str,
                          prev_title: str = "", next_title: str = "",
                          mdl=None) -> list[str]:
    """按节调 LLM 重写，返回新段落列表；两次尝试均失败抛 ValueError（不产生版本）。

    体量闸：输出总字数 < 源文 × _MIN_OUTPUT_RATIO（源文 ≥ _VOLUME_GATE_MIN_SRC 时启用）
    视为疑似截断/漏段，拒绝保存——宁可失败重试，不静默丢内容（2026-09-25 事故回归）。"""
    if len(section_text) > _MAX_SECTION_CHARS:
        raise ValueError("该节内容过长，超过3万字，建议拆分文档后重写。")

    min_chars = (int(len(section_text) * _MIN_OUTPUT_RATIO)
                 if len(section_text) >= _VOLUME_GATE_MIN_SRC else 0)

    prev_line = f"上一节标题：{prev_title}" if prev_title else "（本节是第一节）"
    next_line = f"下一节标题：{next_title}" if next_title else "（本节是最后一节）"
    user_msg = (
        f"【全文目录】\n{outline_text}\n\n"
        f"{prev_line}；{next_line}\n\n"
        f"【目标节《{section_title}》当前内容】\n{section_text}\n\n"
        f"【重写要求】\n{instruction}"
    )

    if mdl is None:
        mdl = _build_chat_mdl(tenant_id)

    last_exc = None
    volume_fail = False
    for attempt in (1, 2):
        try:
            # 仅捕 Exception：CancelledError 等继承 BaseException，不在此吞掉
            txt = await mdl.async_chat(_REWRITE_SYSTEM, [{"role": "user", "content": user_msg}])
        except Exception as e:  # noqa: BLE001 — 网络/限流等 LLM 侧异常纳入统一重试
            last_exc = e
            txt = None
            logger.warning("[rewrite] async_chat failed attempt=%s err=%s", attempt, e)
        paras = _parse_paragraphs(txt)
        if paras and min_chars and sum(len(p) for p in paras) < min_chars:
            volume_fail = True
            paras = None
            logger.warning("[rewrite] output volume below gate attempt=%s "
                           "chars<%s", attempt, min_chars)
        if paras:
            return paras
        logger.warning("[rewrite] bad LLM output attempt=%s preview=%s",
                       attempt, (txt or "")[:120])
    if volume_fail and last_exc is None:
        raise ValueError(
            "重写结果与原文体量差异过大，疑似内容缺失，为防止内容丢失已放弃保存。"
            "请重试一次，或把重写指令拆得更细（如只重写其中一小节）。")
    if last_exc is not None:
        raise ValueError("重写失败，请重试。") from last_exc
    raise ValueError("重写失败，请重试。")
