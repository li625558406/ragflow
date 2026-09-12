# rag/svr/document_rewrite/rewriter.py
# -*- coding: utf-8 -*-
"""LLM 按节重写（设计 §5）。

投喂：用户指令 + 目标节全文 + 全文目录 + 前后节标题（不投喂全文正文）。
输出：JSON {"paragraphs": ["新段落1", ...]}，纯段落文本，LLM 不产格式标记。
代码端兜底：非法 JSON/空段落重试 1 次；段数 1-50、单段 ≤2000 字（超出截断）；
投喂正文 >3 万字直接报错（不静默截断）。失败不产生版本（由工具层保证）。
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# 防御阈值：单节投喂上限（> 判断，整 3 万不报错）、段落数上限、单段字数上限
_MAX_SECTION_CHARS = 30000
_MAX_PARAGRAPHS = 50
_MAX_PARA_CHARS = 2000

_JSON_OBJ_RE = re.compile(r"\{.*\}", flags=re.DOTALL)

_REWRITE_SYSTEM = (
    "你是公文文档改写专家。用户会给出一份文档的目录、某一节的当前内容，以及针对该节的重写要求。"
    "你的任务：只重写这一节，输出该节的新正文段落。要求：\n"
    "1. 保持公文体例与书面语，忠实结合用户要求改写；与目录中其他节衔接自然，不重复其他节内容；\n"
    "2. 输出是纯段落数组，不使用任何 Markdown 标记（不要 #、*、-、表格）；\n"
    "3. 只输出一个 JSON 对象，格式严格为：{\"paragraphs\": [\"新段落1\", \"新段落2\"]}\n"
    "4. 不要输出任何解释、前后缀或代码块标记之外的内容。"
)


def _parse_paragraphs(txt) -> list[str] | None:
    """解析 LLM 输出为段落列表；不合法/全空返回 None（调用方据此重试）。

    容忍 ```json 围栏与前后杂文（贪婪抽第一个 {...}）；仅保留非空 str 并 strip；
    代码端兜底截断：段数 ≤50、单段 ≤2000 字（超出静默截断并告警）。"""
    if not isinstance(txt, str) or not txt.strip():
        return None
    m = _JSON_OBJ_RE.search(txt)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("paragraphs")
    if not isinstance(raw, list):
        return None
    paras = []
    truncated = False
    for item in raw:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        if len(item) > _MAX_PARA_CHARS:
            item = item[:_MAX_PARA_CHARS]
            truncated = True
        paras.append(item)
    if truncated:
        logger.warning("[rewrite] paragraph(s) truncated to %s chars", _MAX_PARA_CHARS)
    if not paras:
        return None
    return paras[:_MAX_PARAGRAPHS]


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
    """按节调 LLM 重写，返回新段落列表；两次尝试均失败抛 ValueError（不产生版本）。"""
    if len(section_text) > _MAX_SECTION_CHARS:
        raise ValueError("该节内容过长，超过3万字，建议拆分文档后重写。")

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

    for attempt in (1, 2):
        txt = await mdl.async_chat(_REWRITE_SYSTEM, [{"role": "user", "content": user_msg}])
        paras = _parse_paragraphs(txt)
        if paras:
            return paras
        logger.warning("[rewrite] bad LLM output attempt=%s preview=%s",
                       attempt, (txt or "")[:120])
    raise ValueError("重写失败，请重试。")
