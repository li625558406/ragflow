"""KB 聚合器：把已检索到的 KB chunk 拼成 references 文本块 + token 预算截断。

为什么是纯函数：执行层（T6 executor 守护线程）负责 I/O——embedding/ES 检索与
LLM 调用；本模块只做「chunk 列表 → 给 LLM 的 references 字符串」这一段纯变换，
不 import settings/Quart，可用假数据独立测试（设计文档 §执行层约束）。

chunk 契约（dict，形状见 rag/nlp/search.py retrieval()）：
    {"chunk_id", "content_with_weight", "doc_id", "docnm_kwd", "kb_id", ...}
真实检索结果是 dict 而非对象，文档名读 "docnm_kwd"。

截断语义（锁定，测试依赖）：
  - 头部三行（用户需求 / 审核模板 / 参考资料：）恒输出：它承载本次审核的意图与
    模板名，chunk 全放不下时也要让 LLM 知道「本次没有参考资料」，否则会凭空编。
  - chunk 整条进或整条不进，绝不切半条：半条标准条款截在句子中间，比没有更容易
    误导 LLM 产出错误批注。
  - 超预算即 break，不跳过靠前的大块去塞靠后的小块：保持检索给出的相关度排序。
  - 编号 [i] 按**已输出**顺序连续递增。
"""
from collections.abc import Iterable

from common.token_utils import num_tokens_from_string

# 正文键回退顺序：ES chunk → KG/其它分支 → 兜底
_CONTENT_KEYS = ("content_with_weight", "content", "text")
# 文档可读名回退顺序
_LABEL_KEYS = ("docnm_kwd", "doc_name", "doc_id", "chunk_id")


def _chunk_text(chunk) -> str:
    """取 chunk 正文，兼容三种来源（见模块 docstring）。"""
    if not isinstance(chunk, dict):
        return ""
    for key in _CONTENT_KEYS:
        val = chunk.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _chunk_label(chunk) -> str:
    """取 chunk 所属文档的可读名，逐级回退。"""
    if not isinstance(chunk, dict):
        return "?"
    for key in _LABEL_KEYS:
        val = chunk.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return "?"


def aggregate_references(*, kb_chunks: Iterable, user_query: str,
                         template_name: str, budget: int) -> str:
    """拼接 KB 检索结果为 references 字符串，超 budget 截断。

    budget 为**整块输出**（含头部）的 token 上限；头部恒输出，可能自身就接近
    或略超预算（用户需求+模板名通常很短），此时返回仅头部的块。
    非 dict / 无正文的 chunk 直接跳过（不占编号、不占预算）。
    """
    # 先物化：空 generator / None 都能正确落空，且入参只被消费一次
    chunks = list(kb_chunks or [])
    if not chunks:
        return ""

    out_parts = [f"用户需求：{user_query}", f"审核模板：{template_name}", "", "参考资料："]
    used = sum(num_tokens_from_string(p) for p in out_parts)
    n = 0
    for chunk in chunks:
        text = _chunk_text(chunk)
        if not text:
            continue
        entry = f"[{n + 1}] doc={_chunk_label(chunk)}\n{text}\n"
        tok = num_tokens_from_string(entry)
        if used + tok > budget:
            break
        out_parts.append(entry)
        used += tok
        n += 1
    return "\n".join(out_parts)
