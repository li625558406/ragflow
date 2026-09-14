"""docx 模板工具：段落遍历（含表格内段落）、填写点候选提取、锚文本→占位符替换。

addr 定位约定：正文段落 para:<idx>；表格内段落 cell:<tbl_no>:<row>:<col>:<para_idx>
（tbl_no 为正文第几个**顶层**表格，0 起——存量模板 addr 兼容依赖此规则不变）。
缺 tbl_no 时多表格文档的 cell(r,c,p) 会跨表撞号（不同表格同位置段落共享 addr，
validate/render 按 addr 查到的段落错位），故必须带表序号。嵌套表格在父单元格 addr
后追加 ":t<j>" 段并递归（cell:0:1:2:t0:0:1:0 = 顶层表 0 的 (1,2) 单元格内第 0 个
嵌套表的 (0,1) 单元格第 0 段）。合并单元格（gridSpan）同一 tc 只按首现坐标编址一次
（此前重复编址会让同一文本多处进候选，anchor 反查报「匹配到多处」）。
文本框段落 <父addr>:tx<k>:<pi>、内容控件段落 body 直系 sdt:<k>:<pi> / 嵌套
<父addr>:sdt<k>:<pi>——新区域只消耗扁平 index，不挤占存量 para:/cell: 序号
（否则含文本框/sdt 的存量模板升级后 addr 整体错位、同形 anchor 静默错填）。
index 为全文档扁平序号，与 addr 一一对应。

跨 run 替换取舍：普通段落跨 run 时只重写 anchor 覆盖的 run 区间，区间外格式保留
（`_replace_cross_run_in_place`）；但含超链接（w:hyperlink）/简单域（w:fldSimple）的段落**不可**整段重写——
python-docx 1.1+ 的 Paragraph.text 包含超链接内文本，而 Paragraph.runs 不包含，
整段重写会把超链接文本复制进首 run 且原节点仍在（内容重复）。这类段落改走
run 拼接替换路径（见 _replace_via_run_concat）。
"""
import io
import logging
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

# 常见填写点特征：下划线空位 / 中文括号空位 / 【】空位 / ×× 占位 / 括号内"填写"提示 / "：" 结尾（冒号后留白）。
# 注意："填写"仅在括号内（如"（请填写）"）才算特征，避免正文说明文字（"应如实填写"）误报污染候选集。
# 补充模式（标准范本实测漏报修复）：
# - ":[ \t\u3000]{3,}\S"：冒号+留白+后续文字（"编制日期：　　年　月　日"、"招标人：　　（盖单位电子公章）"等冒号不在行尾的填写点）
# - "[ \u3000]{2,}年[ \u3000]*月[ \u3000]*日"：「　年　月　日」空白日期占位（要求年前有留白，
#   真实日期"2026年9月11日"不含留白不误报）
# - "\S[ \u3000]{6,}\S"：行内长空白占位（"本招标项目　　（项目名称）　已由　　（审批机关）"跨栏留白）
FILL_HINT_RE = re.compile(
    r"(_{2,}|＿{2,}|（\s*）|\(\s*\)|【\s*】|×{2,}|XX{1,}|xx{1,}|□"
    r"|[（(][^（）()]*填写[^（）()]*[)）]|：\s*$|:\s*$"
    r"|[:：][ \t\u3000]{3,}\S"
    r"|[ \u3000]{2,}年[ \u3000]*月[ \u3000]*日"
    r"|\S[ \u3000]{6,}\S)"
)

# 手动占位符：用户在模板正文里手写的 {{snake_key}}（与 renderer/docxtpl、前端实时预览同口径）
PH_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

TXBX_DEPTH_LIMIT = 8  # 文本框嵌套深度上限：超限子树跳过（畸形 XML 防爆栈）

# python-docx nsmap 不含 mc 前缀（qn("mc:*") KeyError），markup-compatibility 命名空间自备常量
NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _iter_txbx_content(el) -> list:
    """深度优先收集 el 下全部 w:txbxContent（文本框内容根元素）。

    mc:AlternateContent 只下钻 mc:Choice、跳过 mc:Fallback——Word 对浮动文本框
    常存双份（Choice=wps、Fallback=VML），双份都收会让同一段落重复进候选
    （anchor 反查「匹配到多处」歧义 + LLM token 浪费）。"""
    out = []
    fallback_tag = f"{{{NS_MC}}}Fallback"

    def _walk(node):
        for child in node:
            tag = child.tag
            if tag == fallback_tag:
                continue
            if tag == qn("w:txbxContent"):
                out.append(child)
                continue  # 框内段落由 _walk_txbx 逐段处理（更深嵌套文本框随之发现）
            _walk(child)

    _walk(el)
    return out


def _build_addr_map(doc):
    """遍历文档全部可填写段落（正文/内容控件/表格/文本框，页眉页脚见 Task 2），
    返回 ({addr: Paragraph}, items)。

    编址（存量规则不变，新增前缀均为增量，旧 addr 永不复用）：
    - 正文段落 para:<idx>；表格内段落 cell:<tbl_no>:<row>:<col>:<para_idx>
      （tbl_no 只数顶层表格；嵌套表格在父单元格 addr 后追加 ":t<j>" 段递归）；
      合并单元格（gridSpan 横向合并）同一 tc 只按首现坐标编址一次。
    - 文本框段落 <父addr>:tx<k>:<pi>（k 为该段落内第 k 个文本框）；
      框内表格 <父addr>:tx<k>:cell:<t>:...；嵌套文本框继续追加 :tx<j> 段。
      mc:AlternateContent 只取 mc:Choice（见 _iter_txbx_content）。
    - w:sdt 内容控件独立前缀：body 直系 sdt:<k>:<pi>（内含表格 sdt:<k>:cell:...）；
      cell/文本框/嵌套内 sdt 追加 :sdt<k>: 段。新区域不消耗存量 para:/cell: 计数器
      （I-1 红线：含文本框/sdt 的存量模板升级后 para:/cell: 序列逐字节不变）。
    items: [{"index": 扁平序号, "text": 段落文本, "addr": 定位串}]，按文档顺序。
    """
    addr_map = {}
    items = []
    idx = 0        # 全文档扁平序号（items 排序用；新区域段落一并递增）
    para_seq = -1  # 存量正文段号：只随 body 直系 w:p 递增（I-1 红线：新区域不消耗）
    tbl_no = -1    # 存量顶层表格号：只随 body 直系 w:tbl 递增
    sdt_no = -1    # body 直系 sdt 序号（独立 sdt: 前缀）

    def _emit(p, addr):
        nonlocal idx
        addr_map[addr] = p
        items.append({"index": idx, "text": p.text, "addr": addr})
        idx += 1

    def _walk_sdt(sdt_el, prefix, depth):
        """内容控件展开：body 直系 → sdt:<k>:...；cell/文本框/嵌套内 →
        <父addr>:sdt<k>:...。内部段落/表格不消耗任何存量计数器；
        sdt 无 sdtContent（畸形）→ debug 日志跳过。"""
        if depth > TXBX_DEPTH_LIMIT:
            logger.warning("sdt nesting deeper than %d at %s, skipped",
                           TXBX_DEPTH_LIMIT, prefix)
            return
        content = sdt_el.find(qn("w:sdtContent"))
        if content is None:
            logger.debug("w:sdt without sdtContent at %s, skipped", prefix)
            return
        pi = 0
        tbl_k = 0
        sdt_k = 0
        for block in content.iterchildren():
            if block.tag == qn("w:p"):
                _walk_paragraph(Paragraph(block, doc), f"{prefix}:{pi}", depth)
                pi += 1
            elif block.tag == qn("w:tbl"):
                _walk_table(Table(block, doc), f"{prefix}:cell:{tbl_k}", depth)
                tbl_k += 1
            elif block.tag == qn("w:sdt"):
                _walk_sdt(block, f"{prefix}:sdt{sdt_k}", depth + 1)
                sdt_k += 1

    def _walk_table(tbl, prefix, depth=0):
        seen_tc = set()  # lxml 元素按底层 XML 节点判等：横向合并重复返回的 cell 去重
        for r, row in enumerate(tbl.rows):
            for c, cell in enumerate(row.cells):
                if cell._tc in seen_tc:
                    continue
                seen_tc.add(cell._tc)
                cell_addr = f"{prefix}:{r}:{c}"
                for pi, p in enumerate(cell.paragraphs):
                    _walk_paragraph(p, f"{cell_addr}:{pi}", depth)
                for j, sub in enumerate(cell.tables):
                    _walk_table(sub, f"{cell_addr}:t{j}", depth)
                # cell 直系内容控件：局部 sdt 序号从 0 起，不消耗任何存量计数器
                for k, sdt in enumerate(cell._tc.findall(qn("w:sdt"))):
                    _walk_sdt(sdt, f"{cell_addr}:sdt{k}", depth)

    def _walk_paragraph(p, base_addr, txbx_depth=0):
        """编址段落自身及其内嵌文本框（正文/表格 cell/页眉页脚/文本框内通用）。"""
        _emit(p, base_addr)
        for k, txbx in enumerate(_iter_txbx_content(p._p)):
            _walk_txbx(txbx, f"{base_addr}:tx{k}", txbx_depth + 1)

    def _walk_txbx(txbx_el, prefix, depth):
        if depth > TXBX_DEPTH_LIMIT:
            logger.warning("textbox nesting deeper than %d at %s, skipped",
                           TXBX_DEPTH_LIMIT, prefix)
            return
        pi = 0
        tbl_k = 0
        sdt_k = 0
        for block in txbx_el.iterchildren():
            if block.tag == qn("w:p"):
                _walk_paragraph(Paragraph(block, doc), f"{prefix}:{pi}", depth)
                pi += 1
            elif block.tag == qn("w:tbl"):
                _walk_table(Table(block, doc), f"{prefix}:cell:{tbl_k}", depth)
                tbl_k += 1
            elif block.tag == qn("w:sdt"):
                _walk_sdt(block, f"{prefix}:sdt{sdt_k}", depth + 1)
                sdt_k += 1

    def _walk_body_blocks(parent_el):
        """body 直系块编址（存量计数器唯一递增点）：
        w:p → para:<para_seq>；w:tbl → cell:<tbl_no>:...；w:sdt → sdt:<sdt_no>:..."""
        nonlocal para_seq, tbl_no, sdt_no
        for block in parent_el.iterchildren():
            if block.tag == qn("w:p"):
                para_seq += 1
                _walk_paragraph(Paragraph(block, doc), f"para:{para_seq}")
            elif block.tag == qn("w:tbl"):
                tbl_no += 1
                _walk_table(Table(block, doc), f"cell:{tbl_no}")
            elif block.tag == qn("w:sdt"):
                sdt_no += 1
                _walk_sdt(block, f"sdt:{sdt_no}", 0)

    _walk_body_blocks(doc.element.body)
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
    if i < 0 or j < 0:  # 理论不可达（anchor 必在拼接串覆盖范围内），兜底安全值防越界
        i, j, off_i, off_j = max(i, 0), max(j, 0), 0, 0
    return i, j, off_i, off_j


def _replace_cross_run_in_place(p: Paragraph, anchor: str, repl: str) -> bool:
    """跨 run 区间替换（格式保真）：只重写 anchor 覆盖的 run 区间——
    首 run 保留 anchor 前文本并接替换值，尾 run 保留 anchor 后文本，中间 run 清空；
    区间外 run 原样不动（段内其他位置格式完整保留）。

    扫描策略：按替换前出现次数循环，每轮从上一轮替换终点之后继续 find
    （scan_from = start + len(repl)），严格复刻 str.replace「不重扫替换产物」
    的语义——anchor 是 repl 子串时（如 anchor="name"、repl="{{name}}"），
    刚写入的 repl 不会被再次命中，避免文本腐坏与后续真实出现漏替。
    中段 run 仅在有文本时置空：空文本 run 的置空唯一效果是经 Run.text setter
    销毁 rPr 外的结构子节点（w:fldChar/w:drawing 等），跳过即保住结构。"""
    runs = p.runs
    remaining = "".join(r.text for r in runs).count(anchor)
    replaced = False
    scan_from = 0
    while remaining > 0:
        remaining -= 1
        joined = "".join(r.text for r in runs)
        start = joined.find(anchor, scan_from)
        if start < 0:
            break
        end = start + len(anchor)
        i, j, off_i, off_j = _locate_run_span(runs, start, end)
        if i == j:
            runs[i].text = runs[i].text[:off_i] + repl + runs[i].text[off_j:]
        else:
            runs[i].text = runs[i].text[:off_i] + repl
            for mid in range(i + 1, j):
                if runs[mid].text:
                    runs[mid].text = ""
            runs[j].text = runs[j].text[off_j:]
        scan_from = start + len(repl)
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
        else:
            # 静默跳过不变（脏输入健壮性），但留告警痕迹：存量模板的落库 addr
            # 因编址规则演进（如合并单元格去重）悬空时，可凭此定位「填写点没生效」
            logger.warning("apply_docx_placeholders: addr %r not found, skip key=%s", addr, key)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
