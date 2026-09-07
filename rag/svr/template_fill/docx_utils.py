"""docx 模板工具：段落遍历（含表格内段落）、填写点候选提取、锚文本→占位符替换。

addr 定位约定：正文段落 para:<idx>；表格内段落 cell:<row>:<col>:<para_idx>。
index 为全文档扁平序号，与 addr 一一对应（合并单元格会在多处重复出现同一 addr，
替换按"锚文本存在才替换"幂等，重复 addr 无副作用）。
"""
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# 常见填写点特征：下划线空位 / 中文括号空位 / 【】空位 / ×× 占位 / 括号内"填写"提示 / "：" 结尾（冒号后留白）。
# 注意："填写"仅在括号内（如"（请填写）"）才算特征，避免正文说明文字（"应如实填写"）误报污染候选集。
FILL_HINT_RE = re.compile(
    r"(_{2,}|（\s*）|\(\s*\)|【\s*】|×{2,}|XX{1,}|xx{1,}|[（(][^（）()]*填写[^（）()]*[)）]|：\s*$|:\s*$)"
)


def _build_addr_map(doc):
    """遍历 body 直系子节点（w:p / w:tbl），返回 ({addr: Paragraph}, items)。

    items: [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。
    """
    addr_map = {}
    items = []
    idx = 0
    for block in doc.element.body.iterchildren():
        if block.tag == qn("w:p"):
            p = Paragraph(block, doc)
            addr_map[f"para:{idx}"] = p
            items.append({"index": idx, "text": p.text, "addr": f"para:{idx}"})
            idx += 1
        elif block.tag == qn("w:tbl"):
            tbl = Table(block, doc)
            for r, row in enumerate(tbl.rows):
                for c, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        addr = f"cell:{r}:{c}:{pi}"
                        addr_map[addr] = p
                        items.append({"index": idx, "text": p.text, "addr": addr})
                        idx += 1
    return addr_map, items


def iter_docx_paragraphs(file_bytes: bytes) -> list:
    """返回 [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。"""
    _, items = _build_addr_map(Document(io.BytesIO(file_bytes)))
    return items


def extract_docx_candidates(file_bytes: bytes) -> list:
    """提取疑似含填写点的段落（供 LLM 识别，降低 token）。"""
    return [it for it in iter_docx_paragraphs(file_bytes) if it["text"].strip() and FILL_HINT_RE.search(it["text"])]


def _replace_in_paragraph(p: Paragraph, anchor: str, repl: str) -> bool:
    """段内替换锚文本为 repl。优先单 run 内完成；跨 run 时整段重写进首 run、
    清空其余（P1 取舍：牺牲段内混合格式，保证替换必然生效）。"""
    if anchor not in p.text:
        return False
    for run in p.runs:
        if anchor in run.text:
            # str.replace 语义：同段多次出现全部替换
            run.text = run.text.replace(anchor, repl)
            return True
    runs = p.runs
    if not runs:
        return False
    runs[0].text = p.text.replace(anchor, repl)
    for r in runs[1:]:
        r.text = ""
    return True


def apply_docx_placeholders(file_bytes: bytes, replacements: list) -> bytes:
    """replacements: [{"addr", "anchor", "key"}]，把 anchor 替换为 {{key}}。

    addr 不存在时静默跳过；anchor 不在该段落时为 no-op（幂等）。
    """
    doc = Document(io.BytesIO(file_bytes))
    addr_map, _ = _build_addr_map(doc)
    for rep in replacements:
        p = addr_map.get(rep.get("addr"))
        if p is not None:
            _replace_in_paragraph(p, rep["anchor"], f"{{{{{rep['key']}}}}}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
