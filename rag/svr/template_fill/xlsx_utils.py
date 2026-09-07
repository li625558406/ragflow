"""xlsx 模板工具：非空单元格遍历与占位符替换。

addr 约定 "<sheet>!<coordinate>"（如 封面!B1）。iter 产物结构与 docx_utils 一致
（index/text/addr + sheet/coord），供 detector 复用；apply 只改 cell.value，
openpyxl 对 value 赋值不触碰样式对象（字体/填充/边框等随 cell 保留）。

load_workbook 统一使用默认 data_only=False：公式按公式文本保留，重存后不丢公式。
"""
import io

from openpyxl import load_workbook


def iter_xlsx_cells(file_bytes: bytes) -> list:
    """遍历所有工作表的非空单元格（None 或纯空白文本跳过）。

    返回 [{"index": 全簿扁平序号, "text": str(值), "addr": "<sheet>!<coord>",
    "sheet": 表名, "coord": 坐标}]，按表顺序 + 行列顺序。
    """
    wb = load_workbook(io.BytesIO(file_bytes), data_only=False)
    items, idx = [], 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None or not str(cell.value).strip():
                    continue
                items.append({
                    "index": idx,
                    "text": str(cell.value),
                    "addr": f"{ws.title}!{cell.coordinate}",
                    "sheet": ws.title,
                    "coord": cell.coordinate,
                })
                idx += 1
    return items


def extract_xlsx_candidates(file_bytes: bytes) -> list:
    """提取疑似含填写点的单元格（供 LLM 识别，降低 token）。

    排除以 "=" 开头的公式单元格：公式文本（如 =SUM(A1:A2)）可能恰好命中
    FILL_HINT_RE，若作为填写点下发给 LLM，渲染时会用占位符文本覆盖公式本身，
    破坏模板计算逻辑——宁可漏候选，不可污染公式。
    """
    from rag.svr.template_fill.docx_utils import FILL_HINT_RE
    return [
        it for it in iter_xlsx_cells(file_bytes)
        if not it["text"].startswith("=") and FILL_HINT_RE.search(it["text"])
    ]


def _find_cell(wb, sheet: str, coord: str):
    """按 sheet/coord 定位单元格；定位失败时返回 None，交由调用方跳过该条。

    失败路径：
    - sheet 不存在 → openpyxl 抛 KeyError；
    - coord 非法（如 "不是坐标"）→ openpyxl 转坐标抛 ValueError；
    - coord 是 range 语法（如 "A1:B2"）→ openpyxl 合法接受并返回 cell 元组，
      而非单个 cell，后续 cell.value 会抛 AttributeError——同样视为非法 coord，
      返回 None 跳过，保证"脏输入不中断整批"契约；
    - coord 合法但超出工作表边界（行 > 1048576 或列 > XFD）→ ValueError；
    - coord 合法且在边界内但超出已用区域 → openpyxl 惰性创建空单元格返回，
      由调用方"值为 None 不替换"兜住。
    """
    try:
        cell = wb[sheet][coord]
    except (KeyError, ValueError):
        return None
    # range 语法（"A1:B2"）返回 tuple（或多层嵌套 tuple），不是单个 cell
    if isinstance(cell, tuple) or not hasattr(cell, "value"):
        return None
    return cell


def apply_xlsx_placeholders(file_bytes: bytes, replacements: list) -> bytes:
    """replacements 元素含 anchor/key + 定位信息（sheet/coord，或兜底 addr）；把 anchor 替换为 {{key}}。

    定位优先级：显式 sheet+coord 优先；二者任一缺失时，若 addr 为
    "<sheet>!<coord>" 格式（含 "!"），按最后一个 "!" 拆分兜底（rsplit，兼容
    sheet 名本身含 "!" 的罕见情况）。两者都不可用则跳过该条。

    只改 cell.value，样式保留。脏输入健壮性（与 docx_utils 语义一致）：
    定位/anchor/key 任一缺失或为空 → 预检查跳过；sheet 不存在或 coord 非法
    （含 range 语法）→ _find_cell 返回 None 跳过该条而不中断整批；anchor 不在
    单元格值中 → no-op（幂等）。
    """
    wb = load_workbook(io.BytesIO(file_bytes))
    for rep in replacements:
        sheet = rep.get("sheet")
        coord = rep.get("coord")
        addr = rep.get("addr")
        if (not sheet or not coord) and addr and "!" in addr:
            # addr 兜底：按最后一个 "!" 拆分为 sheet/coord
            sheet, coord = addr.rsplit("!", 1)
        anchor = rep.get("anchor")
        key = rep.get("key")
        if not sheet or not coord or not anchor or not key:
            continue
        cell = _find_cell(wb, sheet, coord)
        if cell is None:
            continue
        val = cell.value
        if val is not None and anchor in str(val):
            cell.value = str(val).replace(anchor, f"{{{{{key}}}}}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
