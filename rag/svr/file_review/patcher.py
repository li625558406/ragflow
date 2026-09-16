"""文件审核 patcher：唯一匹配定位 + docx 格式保真替换。

职责边界（与 T3 同构）：只做「(文件字节, patch 列表) → (新字节, applied 列表)」这一段
确定性变换——不调 LLM、不碰 DB、不读 settings，故能拿真实 docx 字节独立单测。

为什么 find 必须唯一才改：投标/合同文本里「1000元」「30天」这类片段天然多处出现，
猜第一个命中会把 A 处的报价改成 B 处的金额——改错比不改更糟。故 0 次（找不到）与
>1 次（歧义）一律跳过，由调用方把该条标注记为「未修复」。

为什么复用 template_fill 的 docx 原语：跨 run 区间替换（一句话被 Word 切进多个 run 是常态）
要求「只重写 anchor 覆盖的 run 区间、区间外 rPr 原样保留」，这段逻辑在
rag/svr/template_fill/docx_utils.py 已由真实范本验证（2026-09-15 段落定位链路，290 锚）。
本模块只读复用，不修改对方一个字节；耦合点收敛在下面两行 alias。
"""
import io

from docx import Document

# 与 template_fill 的**唯一**耦合点（只读复用，零修改）：
#   _build_addr_map(doc) -> ({addr: Paragraph}, items)
#     覆盖正文/表格/文本框/页眉页脚/内容控件的全部段落；
#     自写 doc.paragraphs 会漏掉页眉页脚与文本框里的问题句。
#   _replace_in_paragraph(p, anchor, repl, occ=1)
#     段内第 occ 次出现替换；跨 run 时只重写覆盖区间，区间外格式保留。
# 上游若改名/改签名，只需改这两行（测试用真实 docx 字节调用，符号消失即响亮失败）。
from rag.svr.template_fill.docx_utils import _build_addr_map as _docx_addr_map
from rag.svr.template_fill.docx_utils import _replace_in_paragraph as _docx_replace

__all__ = ["apply_patches", "apply_patches_to_docx", "find_unique"]


def find_unique(text: str, find_str: str) -> int:
    """find_str 在 text 中的**唯一**非重叠出现：唯一返回起始下标；0 次返回 -1；>1 次返回 -2。

    非重叠口径与 str.replace 一致（'aaa' 里找 'aa' 只算 1 次）——否则会出现
    「判定为歧义而跳过、替换本身却能完成」的口径打架。空 find 判 -1（缺失），
    不判歧义：空串会被当成"匹配任意位置"的锚，是畸形输入。
    """
    if not find_str:
        return -1
    count = text.count(find_str)
    if count == 0:
        return -1
    if count > 1:
        return -2
    return text.find(find_str)


def apply_patches(text: str, patches) -> tuple[str, list]:
    """纯文本层逐条应用 patch（顺序生效，后面的看得见前面的替换结果）。

    docx 走 apply_patches_to_docx（格式保真）；本函数用于已提取成纯文本的场景，
    以及非 docx 文件的降级修复路径。返回 (新文本, applied 列表)。
    """
    out = text
    applied = []
    for p in patches or []:
        if not isinstance(p, dict):
            # 非 dict 元素（如 LLM 直接吐了个字符串）跳过即可，不能让同批其余合规 patch 一起作废
            applied.append(False)
            continue
        find_str = p.get("find")
        replace_str = p.get("replace")
        # 非 str 一律按「缺失」跳过，绝不折成空串："" 表示显式删除该片段，
        # {"replace": null} 表示「LLM 没给出替换文本」——混同会让脏字段静默删掉正文。
        if not isinstance(find_str, str) or not isinstance(replace_str, str):
            applied.append(False)
            continue
        if find_unique(out, find_str) >= 0:
            out = out.replace(find_str, replace_str, 1)
            applied.append(True)
        else:
            applied.append(False)
    return out, applied


def apply_patches_to_docx(file_bytes: bytes, patches) -> tuple[bytes, list]:
    """在 docx 字节层应用 patch，返回 (新字节, applied 列表)。

    逐层唯一：先按 p.text 在全文档段落里找候选，候选必须恰好 1 个
    （覆盖正文/表格/文本框/页眉页脚），再要求该段落内 find 出现恰好 1 次。
    任一层不唯一即跳过该条（applied=False）、文件不动。

    已知保真局限（继承自复用原语，见 docx_utils._replace_in_paragraph docstring）：
    find 只出现在超链接/域内文本时，p.text 命中而 p.runs 不命中 → no-op 降级为
    applied=False（安全：不产脏数据）。
    """
    if not patches:
        # 无 patch 不做无意义的解析/重存（重存会重排 XML 字节）
        return file_bytes, []
    doc = Document(io.BytesIO(file_bytes))
    addr_map, _ = _docx_addr_map(doc)
    paragraphs = list(addr_map.values())
    applied = []
    for p in patches or []:
        if not isinstance(p, dict):
            # 非 dict 元素跳过，不抛异常打断整批
            applied.append(False)
            continue
        find_str = p.get("find")
        replace_str = p.get("replace")
        # 非 str 一律按「缺失」跳过，绝不折成空串（"" 是显式删除，语义不能丢）
        if not isinstance(find_str, str) or not isinstance(replace_str, str):
            applied.append(False)
            continue
        hits = [para for para in paragraphs if find_str and find_str in para.text]
        if len(hits) != 1 or find_unique(hits[0].text, find_str) < 0:
            applied.append(False)
            continue
        applied.append(bool(_docx_replace(hits[0], find_str, replace_str, occ=1)))
    if not any(applied):
        # 全部未生效 → 文档一字未改（_replace_in_paragraph 返回 False 不产生部分写入），
        # 必须返回原字节：重存会重排 XML 字节，让「零改动」被 T6 误存成「修复版新版本」。
        return file_bytes, applied
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), applied
