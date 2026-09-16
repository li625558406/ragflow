"""KB 聚合器：把已检索到的 KB chunk 拼成给 LLM 的参考资料片段 + token 预算截断。

为什么是纯函数：执行层（T6 executor 守护线程）负责 I/O——embedding/ES 检索与
LLM 调用；本模块只做「chunk 列表 → references 字符串」这一段纯变换，
不 import settings/Quart，可用假数据独立测试（设计文档 §执行层约束）。

为什么只产片段、不产标题：返回值是填进审核模板 {references} 占位符的**槽位内容**。
5 套预置模板（db_models.py _PRESET_REVIEW_TEMPLATES）自己已写了
"用户需求：{user_query}" 与 "参考资料：\n{references}" 两行标题；槽位填充方再输出
一遍标题，成稿 prompt 里就会出现「参考资料：\n用户需求：…\n审核模板：…\n参考
资料：」的嵌套重复。故本模块只输出编号片段，无任何标题行。无可用片段时返回空串，
模板渲染出空的 "参考资料：" 段——这就是「本次没有参考资料」的诚实表达。

chunk 契约（dict，形状见 rag/nlp/search.py retrieval()）：
    {"chunk_id", "content_with_weight", "doc_id", "docnm_kwd", "kb_id", ...}
真实检索结果是 dict 而非对象，文档名读 "docnm_kwd"。
注意 retrieval() 返回的是 ranks 容器 {"chunks": [...], "doc_aggs": [...]}，
调用方要先取 ranks["chunks"]（传错形状会抛 TypeError，见下——刻意的响亮失败，
避免"零参考"静默跑完）。

截断语义（锁定，测试依赖）：
  - budget 是返回串的 token 上限；不变式 tokens(返回值) <= budget。
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


def aggregate_references(*, kb_chunks: Iterable, budget: int) -> str:
    """把 KB chunk 拼成编号参考资料片段，token 数不超过 budget。

    非 dict / 无正文的 chunk 直接跳过（不占编号、不占预算）；一条都放不下时返回 ""。
    """
    if isinstance(kb_chunks, dict):
        # retrieval() 返回的是 ranks 容器；误把整个容器传进来会退化成「零参考」却
        # 静默跑完。这里响亮失败，让调用方立刻发现。
        raise TypeError("kb_chunks 需为 chunk 列表（ranks['chunks']），不是 ranks 容器")
    entries = []
    n = 0
    for chunk in list(kb_chunks or []):
        text = _chunk_text(chunk)
        if not text:
            continue
        entry = f"[{n + 1}] doc={_chunk_label(chunk)}\n{text}\n"
        candidate = entries + [entry]
        # 预算按**真实输出串**计，不是各段 token 之和："\n".join() 会插入分隔符，且
        # BPE 在段边界可能合并出不同 token，两者并不相等。按 join 后整串计，
        # budget 才是真正的输出上限。
        if num_tokens_from_string("\n".join(candidate)) > budget:
            break
        entries = candidate
        n += 1
    return "\n".join(entries)
