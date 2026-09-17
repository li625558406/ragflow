"""run 层确定性切位：从段落 runs 中切出填写位区间（纯函数，可独立单测）。

填写位 = 下划线格式（w:u）留白 run 或字符下划线串，边界在 run 层精确已知；
LLM 识别降级为「位编号→语义」标注，不再自选 anchor。坐标系为 runs 拼接文本
（与 docx_utils._replace_cross_run_in_place 同坐标系）。

第一性原理：识别 = 「哪里是填写位」（几何/格式，确定性）+「这个位是什么字段」
（语义，LLM 擅长）。本模块只负责前者。"""

import re

from docx.oxml.ns import qn

# 字符下划线：连续下划线字符 ≥2（不要求格式，兼容手打下划线）
_CHAR_UNDERLINE_RE = re.compile(r"[_＿]{2,}")
# 下划线格式 run 的文本须匹配留白特征（防实心文字+下划线格式误判为位）：
# 纯空白/下划线，或 空白+括号提示+空白
_BLANK_RUN_RE = re.compile(r"^[\s_＿]*$")
_HINT_RUN_RE = re.compile(r"^\s*[（(]([^（）()]*)[））]\s*$")
# 单个位长度上限：与 detector.MAX_ANCHOR_LEN 同量级，超长位下游 validate 必拒，源头不产
_MAX_SLOT_LEN = 500


def _classify_underline_text(text: str) -> tuple:
    """下划线格式 run 文本分类 → (kind, hint)。非留白特征返回 ("", "")。"""
    if _BLANK_RUN_RE.fullmatch(text):
        return "blank", ""
    m = _HINT_RUN_RE.fullmatch(text)
    if m:
        return "hint", m.group(1).strip()[:100]  # 沿用现有 name 100 上限
    return "", ""


def extract_paragraph_slots(p) -> list:
    """从段落切出填写位区间，返回 [{"start","end","text","kind","hint"}]。

    - 坐标系：p.runs 文本拼接（与替换层一致）；python-docx 1.2.0 的 p.text
      含超链接文本而 p.runs 不含，故段落含 w:hyperlink 时坐标系不可靠，
      整段不切位（返回 []，该行回退 LLM V1 路径）。
    - 位判定两种来源：下划线格式（bool(font.underline) 真值：None/False 无、
      True/枚举有）且文本匹配留白特征；或连续下划线字符 ≥2。
    - 相邻位段（中间只隔空白文本）合并为一个位；组内有 hint 则整体 kind=hint。
    """
    if p._p.findall(f".//{qn('w:hyperlink')}"):
        return []
    runs = p.runs
    segs = []  # [start, end, kind, hint]
    pos = 0
    for r in runs:
        t = r.text or ""
        if not t:
            continue
        if bool(r.font.underline):
            kind, hint = _classify_underline_text(t)
            if kind:
                segs.append([pos, pos + len(t), kind, hint])
                pos += len(t)
                continue
        for m in _CHAR_UNDERLINE_RE.finditer(t):
            segs.append([pos + m.start(), pos + m.end(), "blank", ""])
        pos += len(t)
    if not segs:
        return []
    runs_text = "".join(r.text for r in runs)
    segs.sort(key=lambda s: (s[0], s[1]))
    merged = [segs[0]]
    for s in segs[1:]:
        prev = merged[-1]
        gap_blank = not runs_text[prev[1] : s[0]].strip()
        if s[0] <= prev[1] or (gap_blank and s[0] >= prev[1]):
            prev[1] = max(prev[1], s[1])
            if s[2] == "hint" and not prev[3]:
                prev[2], prev[3] = "hint", s[3]
        else:
            merged.append(s)
    out = []
    for start, end, kind, hint in merged:
        if end - start > _MAX_SLOT_LEN:
            continue
        out.append({"start": start, "end": end, "text": runs_text[start:end], "kind": kind, "hint": hint})
    return out
