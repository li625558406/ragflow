"""run 层确定性切位：从段落 runs 中切出填写位区间（纯函数，可独立单测）。

填写位 = 下划线格式（w:u）留白 run 或字符下划线串，边界在 run 层精确已知；
LLM 识别降级为「位编号→语义」标注，不再自选 anchor。坐标系为 runs 拼接文本
（与 docx_utils._replace_cross_run_in_place 同坐标系）。

第一性原理：识别 = 「哪里是填写位」（几何/格式，确定性）+「这个位是什么字段」
（语义，LLM 擅长）。本模块只负责前者。

2026-09-18 事故加固：Word 会把一个提示语拆成多 run（rsid/拼写检查/编辑历史，
如 (项目名称) → "(" + "项目名称" + ")   " 三个下划线 run），逐 run 全匹配会把
整簇切碎、丢掉提示语 → 改为连续下划线 run 成簇后按簇文本整体分类；
纯空白位扩展为段内最大空白 run，使 anchor 成为完整空白出现（compute_anchor_positions
raw 通道 / 前端 raw 通道可按完整空白定位，不再回退全文顺序匹配错位）。"""

import re

from docx.oxml.ns import qn

# 字符下划线：连续下划线字符 ≥2（不要求格式，兼容手打下划线）
_CHAR_UNDERLINE_RE = re.compile(r"[_＿]{2,}")
# 下划线格式 run 的文本须匹配留白特征（防实心文字+下划线格式误判为位）：
# 纯空白/下划线，或 空白+括号提示+空白
_BLANK_RUN_RE = re.compile(r"^[\s_＿]*$")
# 括号内不允许嵌套括号（[^（）()]*）：嵌套括号提示（如「（外层（内层））」）不识别为
# hint 位（保守取舍，宁可漏不误——嵌套形态在真实范本中未见，误标代价高于漏标）。
_HINT_RUN_RE = re.compile(r"^\s*[（(]([^（）()]*)[）)]\s*$")
# 簇级括号组扫描：簇文本中全部括号提示组
_GROUP_RE = re.compile(r"[（(][^（）()]*[）)]")
# 单个位长度上限：与 detector.MAX_ANCHOR_LEN 同量级，超长位下游 validate 必拒，源头不产
_MAX_SLOT_LEN = 500


def _classify_underline_text(text: str) -> tuple:
    """下划线格式 run 文本分类 → (kind, hint)。非留白特征返回 ("", "")。
    hint 命中但括号内 strip 后为空（如「（）」）同样返回 ("", "")——空括号提示
    没有语义，不成位（宁可漏不误）。仅用于逐 run 回退路径。"""
    if _BLANK_RUN_RE.fullmatch(text):
        return "blank", ""
    m = _HINT_RUN_RE.fullmatch(text)
    if m:
        hint = m.group(1).strip()
        if hint:
            return "hint", hint[:100]  # 沿用现有 name 100 上限
        return "", ""
    return "", ""


def _classify_cluster(ct: str, cstart: int) -> list | None:
    """下划线 run 簇整体分类 → segs 列表（坐标已偏移 cstart）或 None（回退逐 run）。

    簇文本按一个整体看：
    - 全空白/下划线 → 单 blank seg；
    - 存在括号组且组外全为空白/下划线 → hint segs：单组铺满整簇（混合形态
      ＿＿＿（项目名称）＿＿ 整体成 1 个 hint 位）；多组分区段铺满（首 slot 从
      簇首起、末 slot 延到簇尾，组间空白归入后一 slot 前缀）；
    - 其余（组外有实心文字 / 无组）→ None，调用方逐 run 回退。
    空括号组无语义不成位（宁可漏不误）；hint 沿用 name 100 上限。"""
    if _BLANK_RUN_RE.fullmatch(ct):
        return [[cstart, cstart + len(ct), "blank", ""]]
    groups = list(_GROUP_RE.finditer(ct))
    if not groups:
        return None
    outside = _GROUP_RE.sub("", ct)
    if not all(ch.isspace() or ch in "_＿" for ch in outside):
        return None
    segs = []
    n = len(groups)
    for i, g in enumerate(groups):
        hint = g.group(0)[1:-1].strip()
        if not hint:
            continue
        s = cstart if i == 0 else cstart + groups[i - 1].end()
        e = cstart + len(ct) if i == n - 1 else cstart + g.end()
        segs.append([s, e, "hint", hint[:100]])
    return segs or None


def extract_paragraph_slots(p) -> list:
    """从段落切出填写位区间，返回 [{"start","end","text","kind","hint"}]。

    - 坐标系：p.runs 文本拼接（与替换层一致）；python-docx 1.2.0 的 p.text
      含超链接文本而 p.runs 不含，故段落含 w:hyperlink 时坐标系不可靠，
      整段不切位（返回 []，该行回退 LLM V1 路径）。
    - 位判定两种来源：下划线格式（bool(font.underline) 真值：None/False 无、
      True/枚举有）的连续 run 簇整体分类；或连续下划线字符 ≥2。
    - 簇分类失败回退逐 run 判定（旧行为）；相邻位段（中间只隔空白文本）合并为
      一个位，组内有 hint 则整体 kind=hint（触接的同为 hint 位不合并——多括号
      簇拆出的相邻提示位必须保持独立）。
    - 纯空白 blank 位扩展为段内最大空白 run：向前/后吸收相邻空白文本，止于
      非空白字符或相邻位边界（多位互不侵占）。
    """
    if p._p.findall(f".//{qn('w:hyperlink')}"):
        return []
    runs = p.runs
    segs = []  # [start, end, kind, hint]
    pos = 0
    i = 0
    n = len(runs)
    while i < n:
        t = runs[i].text or ""
        if not t:
            i += 1
            continue  # 空文本 run 无坐标贡献，不断簇
        if bool(runs[i].font.underline):
            j = i
            while j < n and bool(runs[j].font.underline):
                j += 1
            ct = "".join((runs[k].text or "") for k in range(i, j))
            cend = pos + len(ct)
            cls = _classify_cluster(ct, pos)
            if cls is None:
                # 逐 run 回退：簇内每个 run 单独分类 + 字符下划线扫描
                cpos = pos
                for k in range(i, j):
                    rt = runs[k].text or ""
                    if rt:
                        kind, hint = _classify_underline_text(rt)
                        if kind:
                            segs.append([cpos, cpos + len(rt), kind, hint])
                        else:
                            for m in _CHAR_UNDERLINE_RE.finditer(rt):
                                segs.append([cpos + m.start(), cpos + m.end(), "blank", ""])
                    cpos += len(rt)
            else:
                segs.extend(cls)
            pos = cend
            i = j
            continue
        for m in _CHAR_UNDERLINE_RE.finditer(t):
            segs.append([pos + m.start(), pos + m.end(), "blank", ""])
        pos += len(t)
        i += 1
    if not segs:
        return []
    runs_text = "".join(r.text for r in runs)
    segs.sort(key=lambda s: (s[0], s[1]))
    merged = [segs[0]]
    for s in segs[1:]:
        prev = merged[-1]
        if s[0] == prev[1] and prev[2] == "hint" and s[2] == "hint":
            merged.append(s)  # 多括号簇拆出的相邻 hint 位保持独立
            continue
        gap_blank = not runs_text[prev[1] : s[0]].strip()
        if s[0] <= prev[1] or (gap_blank and s[0] >= prev[1]):
            prev[1] = max(prev[1], s[1])
            if s[2] == "hint" and not prev[3]:
                prev[2], prev[3] = "hint", s[3]
        else:
            merged.append(s)
    # 纯空白 blank 位扩展为段内最大空白 run（完整空白出现才能被 raw 通道定位）
    out = []
    for idx, (start, end, kind, hint) in enumerate(merged):
        if kind == "blank" and not runs_text[start:end].strip():
            left = out[-1][1] if out else 0
            right = merged[idx + 1][0] if idx + 1 < len(merged) else len(runs_text)
            while start > left and runs_text[start - 1].isspace():
                start -= 1
            while end < right and runs_text[end].isspace():
                end += 1
        if end - start > _MAX_SLOT_LEN:
            continue
        out.append([start, end, kind, hint])
    return [
        {"start": s, "end": e, "text": runs_text[s:e], "kind": kind, "hint": hint}
        for s, e, kind, hint in out
    ]
