"""docx 模板工具：段落遍历（含表格内段落）、填写点候选提取、锚文本→占位符替换。

addr 定位约定：正文段落 para:<idx>；表格内段落 cell:<tbl_no>:<row>:<col>:<para_idx>
（tbl_no 为正文第几个表格，0 起）。缺 tbl_no 时多表格文档的 cell(r,c,p) 会跨表撞号
（不同表格同位置段落共享 addr，validate/render 按 addr 查到的段落错位），故必须带表序号。
index 为全文档扁平序号，与 addr 一一对应（合并单元格会在多处重复出现同一 addr，
替换按"锚文本存在才替换"幂等，重复 addr 无副作用）。

跨 run 替换取舍：普通段落跨 run 时只重写 anchor 覆盖的 run 区间，区间外格式保留
（`_replace_cross_run_in_place`）；但含超链接（w:hyperlink）/简单域（w:fldSimple）的段落**不可**整段重写——
python-docx 1.1+ 的 Paragraph.text 包含超链接内文本，而 Paragraph.runs 不包含，
整段重写会把超链接文本复制进首 run 且原节点仍在（内容重复）。这类段落改走
run 拼接替换路径（见 _replace_via_run_concat）。
"""
import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# 常见填写点特征：下划线空位 / 中文括号空位 / 【】空位 / ×× 占位 / 括号内"填写"提示 / "：" 结尾（冒号后留白）。
# 注意："填写"仅在括号内（如"（请填写）"）才算特征，避免正文说明文字（"应如实填写"）误报污染候选集。
# 补充模式（标准范本实测漏报修复）：
# - ":[ \t\u3000]{3,}\S"：冒号+留白+后续文字（"编制日期：　　年　月　日"、"招标人：　　（盖单位电子公章）"等冒号不在行尾的填写点）
# - "[ \u3000]{2,}年[ \u3000]*月[ \u3000]*日"：「　年　月　日」空白日期占位（要求年前有留白，
#   真实日期"2026年9月11日"不含留白不误报）
# - "\S[ \u3000]{6,}\S"：行内长空白占位（"本招标项目　　（项目名称）　已由　　（审批机关）"跨栏留白）
FILL_HINT_RE = re.compile(
    r"(_{2,}|（\s*）|\(\s*\)|【\s*】|×{2,}|XX{1,}|xx{1,}|[（(][^（）()]*填写[^（）()]*[)）]|：\s*$|:\s*$"
    r"|[:：][ \t\u3000]{3,}\S"
    r"|[ \u3000]{2,}年[ \u3000]*月[ \u3000]*日"
    r"|\S[ \u3000]{6,}\S)"
)

# 手动占位符：用户在模板正文里手写的 {{snake_key}}（与 renderer/docxtpl、前端实时预览同口径）
PH_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def _build_addr_map(doc):
    """遍历 body 直系子节点（w:p / w:tbl），返回 ({addr: Paragraph}, items)。

    局限：只遍历顶层表格（cell.paragraphs），嵌套表格（cell.tables）内的段落不在编址范围。
    items: [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。
    """
    addr_map = {}
    items = []
    idx = 0
    tbl_no = -1
    for block in doc.element.body.iterchildren():
        if block.tag == qn("w:p"):
            p = Paragraph(block, doc)
            addr_map[f"para:{idx}"] = p
            items.append({"index": idx, "text": p.text, "addr": f"para:{idx}"})
            idx += 1
        elif block.tag == qn("w:tbl"):
            tbl_no += 1
            tbl = Table(block, doc)
            for r, row in enumerate(tbl.rows):
                for c, cell in enumerate(row.cells):
                    for pi, p in enumerate(cell.paragraphs):
                        addr = f"cell:{tbl_no}:{r}:{c}:{pi}"
                        addr_map[addr] = p
                        items.append({"index": idx, "text": p.text, "addr": addr})
                        idx += 1
    return addr_map, items


def iter_docx_paragraphs(file_bytes: bytes) -> list:
    """返回 [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。"""
    _, items = _build_addr_map(Document(io.BytesIO(file_bytes)))
    return items


def extract_docx_candidates(file_bytes: bytes) -> list:
    """提取疑似含填写点的段落（供 LLM 识别，降低 token）。
    含手动占位符 {{key}} 的段落无条件纳入（用户显式标注，不经特征猜测）。"""
    return [
        it for it in iter_docx_paragraphs(file_bytes)
        if it["text"].strip() and (FILL_HINT_RE.search(it["text"]) or PH_RE.search(it["text"]))
    ]


def _has_link_or_field(p: Paragraph) -> bool:
    """段落是否含超链接（w:hyperlink）或简单域（w:fldSimple）直系子节点。

    这两类节点的文本会进入 Paragraph.text（python-docx 1.1+ 对 hyperlink 生效），
    但不在 Paragraph.runs 中，整段重写会导致文本被复制。
    """
    return bool(p._p.findall(qn("w:hyperlink")) or p._p.findall(qn("w:fldSimple")))


def _replace_via_run_concat(p: Paragraph, anchor: str, repl: str) -> bool:
    """含超链接/域段落的替换：把所有 run 的文本按序拼接，在拼接串上 replace，
    再按原 run 长度切分写回各 run。

    超链接/域内文本不参与（不在 runs 中），故不会产生复制。替换导致拼接串
    长度变化时，差值并入最后一个非空 run 的文本——简单可行即可，该路径只为
    避免超链接文本复制，不追求精确保持 run 边界。锚文本在拼接串中不存在
    （跨界没拼上）时返回 False（no-op）。
    """
    runs = p.runs
    joined = "".join(r.text for r in runs)
    if anchor not in joined:
        return False
    replaced = joined.replace(anchor, repl)
    # 按原 run 文本长度在替换后的拼接串上切分写回；总长度差（repl 与 anchor 不等长）
    # 并入最后一个非空 run——简单可行即可，不追求精确保持 run 边界。
    pos = 0
    last_nonempty = -1
    for i, r in enumerate(runs):
        n = len(r.text)
        r.text = replaced[pos:pos + n]
        pos += n
        if n > 0:
            last_nonempty = i
    tail = replaced[pos:]
    if tail:
        target = runs[max(last_nonempty, 0)]
        target.text = (target.text or "") + tail
    return True


def _locate_run_span(runs: list, start: int, end: int) -> tuple:
    """在 run 文本拼接坐标系里返回覆盖 [start, end) 的
    (首run下标 i, 尾run下标 j, anchor在首run内偏移, anchor在尾run内偏移)。"""
    pos = 0
    i = j = -1
    off_i = off_j = 0
    for idx, r in enumerate(runs):
        n = len(r.text)
        if i < 0 and pos + n > start:
            i, off_i = idx, start - pos
        if pos + n >= end:
            j, off_j = idx, end - pos
            break
        pos += n
    if i < 0 or j < 0:  # 理论不可达（anchor 必在拼接串覆盖范围内），兜底尾 run
        i, j, off_i, off_j = len(runs) - 1, len(runs) - 1, len(runs[-1].text), len(runs[-1].text)
    return i, j, off_i, off_j


def _replace_cross_run_in_place(p: Paragraph, anchor: str, repl: str) -> bool:
    """跨 run 区间替换（格式保真）：只重写 anchor 覆盖的 run 区间——
    首 run 保留 anchor 前文本并接替换值，尾 run 保留 anchor 后文本，中间 run 清空；
    区间外 run 原样不动（段内其他位置格式完整保留）。
    按替换前出现次数循环保持 str.replace「全部替换」语义，且不重扫替换产物
    （repl 含 anchor，如手动占位符 anchor==repl 场景，不会死循环）。"""
    runs = p.runs
    remaining = "".join(r.text for r in runs).count(anchor)
    replaced = False
    while remaining > 0:
        remaining -= 1
        joined = "".join(r.text for r in runs)
        start = joined.find(anchor)
        if start < 0:
            break
        end = start + len(anchor)
        i, j, off_i, off_j = _locate_run_span(runs, start, end)
        if i == j:
            runs[i].text = runs[i].text[:off_i] + repl + runs[i].text[off_j:]
        else:
            runs[i].text = runs[i].text[:off_i] + repl
            for mid in range(i + 1, j):
                runs[mid].text = ""
            runs[j].text = runs[j].text[off_j:]
        replaced = True
    return replaced


def _replace_in_paragraph(p: Paragraph, anchor: str, repl: str) -> bool:
    """段内替换锚文本为 repl。优先单 run 内完成；跨 run 时：普通段落只重写
    anchor 覆盖的 run 区间，区间外格式保留（见 _replace_cross_run_in_place）；
    含超链接/域的段落走 run 拼接替换（见 _replace_via_run_concat），
    避免超链接文本被复制进正文 run。"""
    if not anchor:
        return False
    if anchor not in p.text:
        return False
    for run in p.runs:
        if anchor in run.text:
            # str.replace 语义：同段多次出现全部替换
            run.text = run.text.replace(anchor, repl)
            return True
    if _has_link_or_field(p):
        return _replace_via_run_concat(p, anchor, repl)
    if not p.runs:
        return False
    return _replace_cross_run_in_place(p, anchor, repl)


def apply_docx_placeholders(file_bytes: bytes, replacements: list) -> bytes:
    """replacements: [{"addr", "anchor", "key"}]，把 anchor 替换为 {{key}}。

    addr 不存在时静默跳过；anchor 不在该段落时为 no-op（幂等）；
    addr/anchor/key 任一缺失或为空时跳过该条（LLM 脏输入健壮性，与 addr
    不存在静默跳过的语义一致）。
    """
    doc = Document(io.BytesIO(file_bytes))
    addr_map, _ = _build_addr_map(doc)
    for rep in replacements:
        addr = rep.get("addr")
        anchor = rep.get("anchor")
        key = rep.get("key")
        if not addr or not anchor or not key:
            continue
        p = addr_map.get(addr)
        if p is not None:
            _replace_in_paragraph(p, anchor, f"{{{{{key}}}}}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
