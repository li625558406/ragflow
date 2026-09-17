"""LLM 填写点识别：prompt 构造、响应解析与占位符清单校验（纯函数部分可独立单测）。"""
import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

FILL_MODES = ("llm", "param", "manual")
KEY_RE = re.compile(r"[^a-z0-9_]+")
KEY_MAX_LEN = 64  # key 长度上限（与 validate_placeholders 的 [a-z][a-z0-9_]{0,63} 对齐）
MAX_ANCHOR_LEN = 500  # anchor 超长约束收口在 parse：识别阶段就拦住异常项，不让脏数据流入人工确认/apply 链路

# 手动占位符（与 docx_utils.PH_RE / renderer docxtpl / 前端预览同口径）
PH_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

# 分块识别：大范本（几百行候选）单次 LLM 调用会被输出上限截断且注意力稀释，
# 按块切分多次调用再合并；并发上限控制 LLM 压力。
DETECT_CHUNK_SIZE = 60
DETECT_CONCURRENCY = 3

# 留空标记判定：仅由空白/下划线（含全角）/横线/点/顿号等组成的 anchor 视为"空范本留空位"，
# 不派生默认值。注意不含字母数字，日期（2026-09-10）、金额等含数字的现值不会误判。
# 已用 fullmatch，无需 ^$ 首尾锚定。
_BLANK_ANCHOR_RE = re.compile(
    r"[\s_＿\-—–~·*.×﹏－﹣。．,，、;；:：/\\'\"”「」『』（）()【】\[\]……⋯‥▁＊〰]+")

# anchor 派生值回流渲染链路前的控制字符剥离（\x00-\x1f 中排除 \t\n\r 无必要——留空标记判定
# 已 strip，残留控制字符只会污染 docx XML）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# 标签/骨架型 anchor 判定：这类 anchor 是模板提示文字而非已填现值，派生成默认值
# 会被 D−C 条件执行原样回写成稿（用户视角「没填」），且经 sediment 固化污染基线。
# 三类形态（2026-09-12 福建通用本 523 槽实测 163+ 污染）：
# 1. 冒号结尾（"编号："、"申请人："）——字段标签
# 2. 日期骨架（"年 月 日"、"月"）——年月日字 + 空白标点、无数字（含数字是真实日期，保留）
# 3. 括号提示（"（投标人名称）"）——待填提示语
_LABEL_ANCHOR_RE = re.compile(r".+[：:]\s*$")
_DATE_SKELETON_RE = re.compile(r"[\s年月日度.．:：\-—_＿、]*[年月日][\s年月日度.．:：\-—_＿、]*")
_HINT_ANCHOR_RE = re.compile(r"[（(][^（）()]*[）)]\s*[\s元万元整人民币]*")

# 留白标记特征（收缩修正的目标形态）：下划线串（含全角＿）/ 连续空格（含全角　）/
# 括号提示（如"（投标人名称）"）。anchor 本身含留白标记 = 无需修正。
_BLANK_MARK_RE = re.compile(r"[_＿]{2,}|[ \u3000]{2,}|[（(][^（）()]*[）)]")
# 混合 anchor（标签前缀 + 纯留白后缀，如"编号：＿＿＿"）：结尾非冒号（漏过标签分支）、
# 含留白（漏过实心分支），不收缩会在渲染时整体替换丢掉标签，且污染默认值派生
# → 收缩为后缀留白部分（替换只动留白，标签保留；收缩后即纯留白，无歧义低置信）。
_MIXED_LABEL_BLANK_RE = re.compile(r"^(.+[：:]\s*)([_＿\u3000 ]+)$")
# 收缩目标优先级（不能合并成单条正则取 leftmost：同一行内多种留白共存时按类型择优）：
# 下划线填空线（最典型填写位）> 括号提示（比裸空格更具体，如"工程名称　　（填写完整名称）"
# 应收缩到括号提示而非中间空格）> 连续空格
_SHRINK_MARK_RES = (
    re.compile(r"[_＿]{2,}"),
    re.compile(r"[（(][^（）()]*[）)]"),
    re.compile(r"[ \u3000]{2,}"),
)


def _shrink_anchor_to_blank(anchor: str, line_text: str) -> str:
    """标签/实心 anchor 收缩修正：在 anchor 结束位置之后按优先级找留白标记串，
    返回该串作为新 anchor；找不到返回空串。治两类识别错误：
    ① LLM 把字段标签（"投标人名称："）选为 anchor → 收缩到标签后的留白；
    ③ LLM 把正文原文选为 anchor → 同行有留白时收缩到留白，避免原文被覆盖。"""
    pos = line_text.find(anchor)
    if pos < 0:
        return ""
    rest = line_text[pos + len(anchor):]
    for pat in _SHRINK_MARK_RES:
        m = pat.search(rest)
        if m:
            return m.group(0)
    return ""


def _is_template_skeleton(text: str) -> bool:
    if _LABEL_ANCHOR_RE.fullmatch(text):
        return True
    if _DATE_SKELETON_RE.fullmatch(text) and not any(c.isdigit() for c in text):
        return True
    return bool(_HINT_ANCHOR_RE.fullmatch(text))


def derive_default_from_anchor(anchor) -> str:
    """已填现值提取（纯函数）：anchor 是识别器选中的"将被替换为 {{key}} 的原文子串"。
    已填范本的 anchor 即现值 → 作为 detected 默认值；空范本的 anchor 是留空标记 → 无默认值。
    返回空串表示无默认值。截断对齐 MAX_ANCHOR_LEN（防御旧数据超长）。"""
    text = _CTRL_RE.sub("", str(anchor or "").strip())
    if not text or _BLANK_ANCHOR_RE.fullmatch(text) or text.upper() in ("N/A", "NA", "NONE", "NULL"):
        return ""
    # 标签/骨架型 anchor 是模板提示文字：派生默认值会把标签原样回写成稿并污染基线
    if _is_template_skeleton(text):
        return ""
    return text[:MAX_ANCHOR_LEN]

DETECT_SYSTEM = """你是文档模板分析专家。用户给出固定模板中疑似需要填写的编号行（行号\\t文本）。
请识别其中所有"填写点"——模板留空、需要后续填写内容的位置。
输出 JSON 数组，每个元素：
{"line": 行号(int), "anchor": "该行原文中将被替换为占位符的精确子串", "key": "snake_case英文标识", "name": "中文字段名", "description": "给填写模型的说明", "retrieval_query": "适合去知识库检索的查询词", "fill_mode": "llm", "required": true或false}
规则：
1. anchor 必须是该行原文的精确子串，禁止改写；一行可有多个填写点（拆成多个元素）。anchor 只能选留白标记（下划线串/连续空格/括号提示）或已填写的现值本身，禁止选字段标签（如"申请人："这类冒号结尾引导词）或正文叙述文字。
2. 同一含义的填写点 key 全局唯一，不超过 32 个字符（过长会被截断），日期类建议 key 如 sign_date。
3. fill_mode 一律填 "llm"（所有填写点统一交给 AI 检索填写，检索不到的留空由人工后续加工）。
4. 找不到任何填写点输出 []。只输出 JSON 数组，不要输出其它文字。
5. 正文叙述中以冒号结尾、用于引出下文的句子（如"包括以下内容："、"下列情形之一："）不是填写点，不要输出；只有"标签：＋留白待填值"（如 申请人：/地址：/编号： 后跟空白）才是填写点。"""

# V2「位编号→语义」契约：候选行的填写位由 blank_slots 在 run 层确定性切出，
# LLM 只标注字段语义、引用位编号，不再自选 anchor（根除拆碎片/漏识别/错位）。
DETECT_SYSTEM_V2 = """你是文档模板分析专家。用户给出固定模板中疑似需要填写的行（行号\\t文本），
每行下方列出该行已切分好的填写位：位[n]=「位文本」。填写位是模板留空待填的位置（下划线、空白、括号提示）。
请为每个填写位标注字段语义。输出 JSON 数组，每个元素：
{"line": 行号, "slot": 位序号, "key": "snake_case英文标识", "name": "中文字段名", "description": "给填写模型的说明", "retrieval_query": "适合检索的查询词", "required": true或false}
规则：
1. 每个填写位都必须标注，一行多位拆成多个元素；位序号从 1 开始，按行内列出顺序编号；括号里的提示语去掉括号就是中文字段名（如 位文本=「 （项目审批、核准或备案机关名称）」→ name=项目审批、核准或备案机关名称）
2. key 全局唯一、snake_case、字母开头、不超过32字符；同一含义出现多次（如多个日期）也分别标注，系统自动区分位置
3. required 按招标文件惯例判断（项目名称/招标人/金额/日期等核心信息为 true，次要信息为 false）
4. 找不到任何填写位输出 []。只输出 JSON 数组，不要输出其它文字。"""


def normalize_key(key: str) -> str:
    """归一化为 snake_case：小写 + 非法字符替换为下划线；全空兜底 "field"。
    长度截断在 parse_detection_response 内做（含去重后缀同步截断），validate_placeholders 仍保留终审。"""
    k = KEY_RE.sub("_", str(key).strip().lower())
    return k.strip("_") or "field"


def _extract_json_array(raw: str) -> list | None:
    """三级容错抽取 LLM 输出中的 JSON 数组：
    1. 裸数组直解（规整输出最快路径）；
    2. 剥 ``` 代码围栏后直解；
    3. 贪婪正则 [.*] 回退（存量语义，多数组等畸形输入在此保守失败）。
    全部失败返回 None（调用方折叠为 0 项；当前实现中解析失败与 LLM 合法空结果不可区分，
    均不计入 failed——历史语义，非静默告警路径）。"""
    raw = (raw or "").strip()
    candidates = [raw]
    fenced = re.match(r"^```[\w-]*\s*(.*?)\s*```$", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            arr = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(arr, list):
            return arr
    return None


def parse_detection_response(raw: str, candidates: list) -> list:
    """解析 LLM 输出 → 校验后的建议清单。行号/锚文本不合法的项直接丢弃。"""
    arr = _extract_json_array(raw)
    if arr is None:
        return []
    cand_map = {c["index"]: c for c in candidates}
    out, used_keys = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        line, anchor = it.get("line"), str(it.get("anchor") or "")
        # line 必须是 int（bool 除外）：字符串 "0"/None/不可哈希类型一律安全跳过
        if not isinstance(line, int) or isinstance(line, bool):
            continue
        if len(anchor) > MAX_ANCHOR_LEN:
            continue
        cand = cand_map.get(line)
        if cand is None or not anchor or anchor not in cand["text"]:
            continue
        # 标签/实心 anchor 后处理（治①③，prompt 只是引导，代码层兜底）：
        # 混合型（标签前缀+纯留白后缀）→ 收缩为后缀留白，替换只动留白标签保留；
        # 标签型 → 收缩到标签后的留白串，收缩失败丢弃（标签绝不能整体被替换）；
        # 实心（无留白特征）→ 同行有留白特征则收缩（收缩只是兜底猜测，可能收缩到
        # 同行其它字段的空位造成静默错位，成功也打低置信警示），失败保留并打低置信；
        # 整行无留白特征 → 已填范本现值是合法 anchor，保留并打低置信警示。
        low_confidence = False
        orig_anchor = anchor  # 收缩会改写 anchor：记录原值供合并撞车时回退（防字段静默丢失）
        mixed = _MIXED_LABEL_BLANK_RE.match(anchor)
        if mixed:
            anchor = mixed.group(2)
        elif _LABEL_ANCHOR_RE.fullmatch(anchor):
            shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
            if not shrunk:
                continue
            anchor = shrunk
        elif not _BLANK_MARK_RE.search(anchor):
            if _BLANK_MARK_RE.search(cand["text"]):
                shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
                if shrunk:
                    anchor = shrunk
                    low_confidence = True
                else:
                    low_confidence = True
            else:
                low_confidence = True
        key = normalize_key(it.get("key") or it.get("name") or "field")
        # 截断兜底：LLM 常照中文长字段名直译出 >64 字符的 key，若放行到
        # validate_placeholders 会判死整次识别（全部建议被丢弃），故在 parse
        # 阶段截断保住其余项；去重后缀拼接时同步保证总长不超限
        key = key[:KEY_MAX_LEN].rstrip("_") or "field"
        suffix = 2
        base = key
        while key in used_keys:
            tail = f"_{suffix}"
            key = base[: KEY_MAX_LEN - len(tail)].rstrip("_") + tail
            suffix += 1
        used_keys.add(key)
        # fill_mode 代码层强制 llm：识别产物统一交给 AI 检索填写（prompt 只是引导，
        # LLM 不听话也拦得住）；manual/param 只能由人工在详情页显式配置
        item = {
            "key": key,
            "name": str(it.get("name") or key)[:100],
            "description": str(it.get("description") or ""),
            "retrieval_query": str(it.get("retrieval_query") or ""),
            "fill_mode": "llm",
            "required": bool(it.get("required", True)),
            "addr": cand["addr"],
            "anchor": anchor,
            "line": line,
            "top_k": 6,
            "low_confidence": low_confidence,
        }
        if anchor != orig_anchor:
            item["_orig_anchor"] = orig_anchor  # 内部字段：合并撞车回退用，append 前必被删除
        # 内部字段：文本偏移（合并预分配 occ 的组内排序键）。基于最终（可能收缩后）
        # anchor 求位；成员校验已保证 anchor 在候选文本内，find 不会落空（-1 仅理论兜底）
        item["_anchor_pos"] = cand["text"].find(anchor)
        out.append(item)
    return out


def parse_slot_response(raw: str, candidates: list) -> tuple:
    """解析 V2 输出 → (items, covered)。covered = 已标注 (行号, 位序号) 集合，
    供兜底与跨块去重。anchor 由切位区间精确切片，LLM 无权决定 anchor；
    line/slot 引用非法（非整数、查无此位）直接丢弃该项；slot 文本不在候选
    原文中（runs 坐标系腐坏）同样丢弃，不静默产脏 anchor；
    key 与 V1 同口径做 parse 级截断感知去重（跨 slot 唯一），避免 merge 再加
    后缀溢出 KEY_MAX_LEN 被 validate_placeholders 判死整次识别。"""
    arr = _extract_json_array(raw)
    if arr is None:
        return [], set()
    slot_map = {}
    for c in candidates:
        for i, s in enumerate(c.get("slots") or [], start=1):
            slot_map[(c["index"], i)] = (c, s)
    out, covered, used_keys = [], set(), set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        line, slot_no = it.get("line"), it.get("slot")
        if not isinstance(line, int) or isinstance(line, bool):
            continue
        if not isinstance(slot_no, int) or isinstance(slot_no, bool):
            continue
        if (line, slot_no) in covered:
            continue  # 同一 slot 被标注两次取第一个
        hit = slot_map.get((line, slot_no))
        if hit is None:
            continue
        cand, slot = hit
        # 防御：slot 文本必须仍是候选原文的子串（runs 坐标系与候选 text 理论一致，
        # 不一致说明数据腐坏，丢弃优于静默产脏 anchor）
        if slot["text"] not in (cand.get("text") or ""):
            continue
        covered.add((line, slot_no))
        key = normalize_key(it.get("key") or it.get("name") or "field")
        key = key[:KEY_MAX_LEN].rstrip("_") or "field"
        # 截断感知去重（与 parse_detection_response 同算法）：重复 key 拼后缀时
        # 同步收缩基串，保证含后缀总长仍 ≤KEY_MAX_LEN
        suffix = 2
        base = key
        while key in used_keys:
            tail = f"_{suffix}"
            key = base[: KEY_MAX_LEN - len(tail)].rstrip("_") + tail
            suffix += 1
        used_keys.add(key)
        # required 显式判型：bool("false")==True 是经典陷阱，仅布尔 True /
        # 字符串 "true" 语义为 True，其余（"false"/False/0）为 False；缺失默认 True
        req = it.get("required", True)
        out.append({
            "key": key,
            "name": str(it.get("name") or key)[:100],
            "description": str(it.get("description") or ""),
            "retrieval_query": str(it.get("retrieval_query") or ""),
            "fill_mode": "llm",
            "required": req is True or (isinstance(req, str) and req.strip().lower() == "true"),
            "addr": cand["addr"],
            "anchor": slot["text"],
            "line": line,
            "top_k": 6,
            "low_confidence": False,
            "_anchor_pos": slot["start"],  # 切位精确偏移，供 occ 预分配排序
        })
    return out, covered


def slot_fallback_items(candidates: list, covered: set, seq_start: int = 0) -> list:
    """LLM 未标注的 slot 确定性兜底（结构性修漏识别）：每位恰好一条。
    hint 位 name=括号提示（不低置信——名字高可信，仅 key 机器生成）；
    blank 位 name=未命名填写位（低置信，B端确认时人工改名）。
    key=blank_{序号} 全局递增，确定性唯一；分块调用时必须传续接的 seq_start
    保证 blank_N 全局唯一（Task 4 分流层负责汇总）；不派生默认值由下游 derive
    链路天然保证（anchor 是纯留白/hint，derive_default_from_anchor 判空）。
    位区间互不重叠 → 兜底条目自身不溢出 occ 预分配上限；同段非位同形文本的
    occ 错位由分流层的确定性校验兜底（见 detect_fill_points）。"""
    out = []
    seq = seq_start
    for c in candidates:
        for i, s in enumerate(c.get("slots") or [], start=1):
            if (c["index"], i) in covered:
                continue
            seq += 1
            hint = (s.get("hint") or "").strip()
            if s.get("kind") == "hint" and hint:
                name, low = hint[:100], False
            else:
                name, low = "未命名填写位", True
            out.append({
                "key": f"blank_{seq}",
                "name": name,
                "description": "识别自括号提示的填写点" if not low else "识别兜底填写点，请确认字段名",
                "retrieval_query": hint if not low else "",
                "fill_mode": "llm",
                "required": True,
                "addr": c["addr"],
                "anchor": s["text"],
                "line": c["index"],
                "top_k": 6,
                "low_confidence": low,
                "_anchor_pos": s["start"],
            })
    return out


def validate_placeholders(items: list, candidates: list) -> tuple:
    """人工确认后的占位符清单校验。返回 (ok, error_message)，错误信息面向前端用户（中文）。
    手动添加行（addr 为空）允许仅凭 anchor 反查推导 addr：唯一命中则回填 item["addr"]，
    零命中/多处命中均拒绝（多处命中无法确定落位，回填会错位）。"""
    cand_map = {c["addr"]: c for c in candidates}
    seen = set()
    for row_no, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            return False, "条目格式非法（须为对象）"
        key = str(it.get("key") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            return False, f"非法 key: {key!r}（须为 snake_case，字母开头，不超过 64 字符）"
        if key in seen:
            return False, f"key 重复: {key}"
        seen.add(key)
        if not str(it.get("name") or "").strip():
            return False, f"{key} 缺少中文名称"
        addr, anchor = it.get("addr"), str(it.get("anchor") or "")
        # anchor 长度上限与 parse_detection_response（MAX_ANCHOR_LEN）对齐：parse 拦不住的
        # 场景（前端手改提交/旧数据回放）在此兜底
        if len(anchor) > MAX_ANCHOR_LEN:
            return False, f"{key} 的 anchor 超过{MAX_ANCHOR_LEN}字符"
        if not addr:
            # 手动添加行（前端 addr 恒为 ''）：用 anchor 在候选原文里反查定位。
            # 精确子串匹配、区分大小写，与下方 addr 非空分支的 membership 校验语义一致；
            # 空 anchor 时 `"" in text` 恒真会命中全部候选，需先行拦截。
            if not anchor:
                return False, f"第{row_no}个填写点：缺少锚文本"
            hits = [c for c in candidates if anchor in c["text"]]
            if not hits:
                return False, f"第{row_no}个填写点：锚文本在模板中未找到，请核对"
            if len(hits) > 1:
                return False, f"第{row_no}个填写点：锚文本匹配到{len(hits)}处，请使用更长的锚文本"
            cand = hits[0]
            # 手动添加行没有同形留白分组语义（反查已要求全模板唯一命中），
            # 只接受 occ 缺省或 occ=1；注意 True == 1 的 bool 陷阱须显式排除
            occ = it.get("occ")
            if occ is not None and (not isinstance(occ, int) or isinstance(occ, bool) or occ != 1):
                return False, f"{key}：手动添加行只支持单填写位"
            it["addr"] = cand["addr"]  # 回填，保证落库的占位符都有有效 addr
        else:
            cand = cand_map.get(addr)
            if cand is None:
                return False, f"{key} 的定位 {addr!r} 不存在"
            if not anchor or anchor not in cand["text"]:
                return False, f"{key} 的 anchor 不在 {addr} 文本中"
            # occ（第 N 次出现定位）终审：缺省放行（存量数据兼容）；存在时须为
            # 正整数（bool 是 int 子类，True==1 会漏过，显式拒绝）且不超过
            # anchor 在该候选文本中的实际出现次数（str.count 即非重叠计数，
            # 与渲染层逐次定位语义一致）
            occ = it.get("occ")
            if occ is not None:
                if not isinstance(occ, int) or isinstance(occ, bool) or occ < 1:
                    return False, f"{key}：填写位序号非法（须为正整数）"
                actual = cand["text"].count(anchor)
                if occ > actual:
                    return False, f"{key}：锚文本出现次数不足（需第 {occ} 处，实际仅 {actual} 处）"
        if it.get("fill_mode") not in FILL_MODES:
            return False, f"{key} 的 fill_mode 非法"
    return True, ""


def extract_explicit_placeholders(candidates: list) -> list:
    """手动占位符直通：用户在模板正文里手写的 {{snake_key}} 直接识别为填写点，
    不经 LLM（确定性、零成本、不漏不错）。anchor 即 {{key}} 整串——渲染链路
    anchor→{{key}} 替换对其幂等（替换后文本不变），docxtpl 直接渲染。
    key 冲突（同 key 多处出现）由 _merge_detection 统一加后缀。"""
    out = []
    for c in candidates:
        for m in PH_RE.finditer(c["text"]):
            key = normalize_key(m.group(1))
            out.append({
                "key": key,
                "name": key,
                "description": "手动占位符（模板中预先标注），确认时可修改中文名称",
                "retrieval_query": "",
                "fill_mode": "llm",
                "required": True,
                "addr": c["addr"],
                "anchor": m.group(0),
                "line": c["index"],
                "top_k": 6,
            })
    return out


async def _detect_chunked(chat, file_type: str, candidates: list) -> tuple:
    """分块并发识别。chat(system, messages) -> str（LLM 调用抽象，便于单测注入）。
    返回 (items, failed_chunks)：单块失败不拖垮整体（大范本部分结果好过全无），
    全部失败由调用方结合合并结果决定是否抛错。"""
    chunks = [candidates[i:i + DETECT_CHUNK_SIZE]
              for i in range(0, len(candidates), DETECT_CHUNK_SIZE)]
    sem = asyncio.Semaphore(DETECT_CONCURRENCY)

    async def run_one(chunk: list) -> list:
        numbered = "\n".join(f'{c["index"]}\t{c["text"]}' for c in chunk)
        user_msg = f"文件类型：{file_type}\n编号行：\n{numbered}"
        async with sem:
            ans = await chat(DETECT_SYSTEM, [{"role": "user", "content": user_msg}])
        return parse_detection_response(ans, chunk)

    results = await asyncio.gather(*(run_one(c) for c in chunks), return_exceptions=True)
    items, failed = [], 0
    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            logger.warning("detect chunk failed: %s", r)
            continue
        items.extend(r)
    return items, failed


async def _detect_slot_chunked(chat, candidates: list) -> tuple:
    """V2 分块并发识别（位编号→语义）。输入每行附位列表（extract_docx_candidates
    的 slots 键），输出 (items, covered, failed)；covered 用全局扁平 index 作行键，
    跨块无碰撞。单块失败不拖垮整体（未覆盖位由调用方 slot_fallback_items 兜底）。
    坐标系注意：提示词只放 s["text"] 整体，绝不在 c["text"] 上做 text[start:end]
    切片——slots 是 run 拼接文本坐标系，与候选行 text（p.text）在含 fldSimple 等
    段落上可不同（Task 2 审查 M-3），切片会产出错位脏 anchor。"""
    chunks = [candidates[i:i + DETECT_CHUNK_SIZE]
              for i in range(0, len(candidates), DETECT_CHUNK_SIZE)]
    sem = asyncio.Semaphore(DETECT_CONCURRENCY)

    async def run_one(chunk: list) -> tuple:
        lines = []
        for c in chunk:
            lines.append(f'{c["index"]}\t{c["text"]}')
            for i, s in enumerate(c.get("slots") or [], start=1):
                lines.append(f'位[{i}]=「{s["text"]}」')
        user_msg = "行与填写位：\n" + "\n".join(lines)
        async with sem:
            ans = await chat(DETECT_SYSTEM_V2, [{"role": "user", "content": user_msg}])
        return parse_slot_response(ans, chunk)

    results = await asyncio.gather(*(run_one(c) for c in chunks), return_exceptions=True)
    items, covered, failed = [], set(), 0
    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            logger.warning("slot detect chunk failed: %s", r)
            continue
        its, cov = r
        items.extend(its)
        covered |= cov
    return items, covered, failed


def _get_chat_model(tenant_id: str):
    """租户默认对话模型获取（模块级抽出，便于单测打桩绕开 DB/LLM）。
    延迟 import 语义与原 detect_fill_points 内联版本一致：保证本模块纯函数部分
    无运行时依赖、可独立单测。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType
    model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    return LLMBundle(tenant_id, model_config)


def _verify_slot_occ(merged: list, candidates: list) -> list:
    """V2 条目确定性 occ 校验闸（Task 3 审查 Major 3）：_merge_detection 的 occ
    预分配按 _anchor_pos 排序给同形 anchor 组分配 1..n，但渲染层按 anchor 在候选
    文本中的**非重叠出现序**取第 N 次（docx_utils._occurrence_intervals，步长
    len(anchor)）——同段存在非位的同形空白文本插在两位之间时 occ 会静默错位
    （值写进非位空白、真位落空）。此处据此重算「包含 _anchor_pos 的那次出现」的
    序号并回写 occ，与渲染层严格同一算法。
    找不到对应出现（数据腐坏/坐标系分歧，如含 fldSimple 段落上 runs 坐标与
    p.text 坐标不一致）→ 丢弃该条目，宁可漏不写错位；同组两 V2 条目算出同一
    出现序（真实切位互不重叠，发生即数据腐坏）→ 保留靠前者、丢弃靠后者。
    V1/explicit 条目不动（V1 的 _anchor_pos 本就来自同一文本序 find，零回归；
    explicit 无 _anchor_pos 天然透传）。"""
    cand_map = {c["addr"]: c for c in candidates}
    out = []
    claimed = {}  # (addr, anchor) -> 已占用的出现序号
    for it in merged:
        cand = cand_map.get(it.get("addr"))
        if cand is None or not cand.get("slots"):
            out.append(it)
            continue
        pos = it.get("_anchor_pos")
        anchor = it.get("anchor") or ""
        if not isinstance(pos, int) or isinstance(pos, bool) or not anchor:
            out.append(it)
            continue
        text = cand["text"]
        # 与渲染层 _occurrence_intervals 严格同一算法：非重叠消费，推进步长
        # len(anchor)（不是 +1，否则重叠出现会把序号数歪）
        idx, start = 1, text.find(anchor)
        hit = None
        while start != -1:
            if start <= pos < start + len(anchor):
                hit = idx
                break
            idx += 1
            start = text.find(anchor, start + len(anchor))
        gkey = (it.get("addr"), anchor)
        if hit is None or claimed.get(gkey) == hit:
            logger.warning(
                "verify_slot_occ: drop %s@%s pos=%s (%s)",
                it.get("key"), it.get("addr"), pos,
                "pos 不在任何出现区间" if hit is None else "同组出现序冲突")
            continue
        claimed[gkey] = hit
        it["occ"] = hit
        out.append(it)
    return out


def _merge_detection(explicit: list, llm_items: list, preassign_occ: bool = True,
                     candidates: list | None = None) -> list:
    """合并手动直通项与 LLM 识别项。
    - 同源同 (addr, anchor) 多份（同段同形留白 / 同一 {{key}} 出现多次）：
      预分配 occ（第 N 次出现定位，渲染层按次序落位）保留全部；LLM 组 occ≥2
      打低置信（同形留白歧义需人工核对），手动组确定性不打。
      xlsx 须传 preassign_occ=False 关闭预分配：xlsx 渲染层 replace-all 不识别
      occ，预分配会让同格重复占位符串值覆盖——关闭后重复项回到旧「去重丢弃、
      单 key replace-all」自洽语义。
    - 跨源同 (addr, anchor)：仍手动优先丢弃——不给 LLM 项分配 occ，防 LLM
      幻觉回显 {{key}} 产生 occ=2 超界项挤爆 validate。
    - 收缩撞车回退：带 _orig_anchor 的项回退到原 anchor 保留并打低置信，
      occ 作废（属于收缩后 anchor 的组）。注意：溢出封顶优先于收缩回退——
      项因溢出被丢弃时不再进入回退分支（丢弃方向保守，宁缺勿错）。
    - 组内溢出封顶（candidates 传入时）：同组项数超过 anchor 在候选文本中的
      实际出现次数（text.count 非重叠语义，与 validate 终审同口径）时，溢出项
      直接丢弃而非分配超界 occ——LLM 对同形留白重复建议/幻觉属输出随机性，
      不该让 validate 判死整次识别（真实事故：station_range occ=2 超界毁掉
      1351 候选大范本全部识别结果）。validate_placeholders 的严格终审保持
      不变，继续保护人工确认保存路径；不传 candidates 时行为同旧（兼容直调）。
    跨源/跨块 key 冲突按出现顺序加 _2/_3 后缀（作用域为整次识别）。"""

    # addr → 候选原文，供 occ 预分配封顶核对实际出现次数
    cand_text = {c["addr"]: c["text"] for c in candidates} if candidates else {}

    def _preassign_occ(items: list, mark_low_confidence: bool) -> set:
        """同源组内按 (addr, anchor) 计数：出现 >1 次的组按文本偏移升序（稳定）
        排序后分配 occ=1..n——防 LLM 乱序输出导致 occ 与文本位置颠倒、渲染值串位
        （_anchor_pos 由 parse 阶段记录；显式组/手动组天然文本序，排序为 no-op）。
        mark_low_confidence 时 occ≥2 强制低置信（同形留白歧义——即便 parse 阶段
        判定收缩后"无歧义"，多项撞进同一留白本身就是歧义证据）。
        candidates 提供时按 anchor 实际出现次数封顶：超界项不占序位、原样返回
        待丢弃（其 occ 若落库会被 validate 终审拒绝，此处前置拦截保住其余项）。"""
        totals, seq = {}, {}
        for it in items:
            p0 = (it["addr"], it["anchor"])
            totals[p0] = totals.get(p0, 0) + 1
        # list.sort 稳定：偏移相同（同 anchor 同候选时 find 结果恒同）保持原相对序
        items.sort(key=lambda _it: _it.get("_anchor_pos", -1))
        overflow = set()
        for it in items:
            p0 = (it["addr"], it["anchor"])
            if totals[p0] > 1:
                cap = cand_text.get(it["addr"], "").count(it["anchor"]) if cand_text else None
                n = seq.get(p0, 0) + 1
                if cap is not None and n > cap:
                    overflow.add(id(it))
                    continue
                seq[p0] = n
                it["occ"] = n
                if mark_low_confidence and n > 1:
                    it["low_confidence"] = True
        return overflow

    explicit_pos = {(it["addr"], it["anchor"]) for it in explicit}
    overflow_ids: set = set()
    if preassign_occ:
        overflow_ids |= _preassign_occ(explicit, mark_low_confidence=False)
        # 跨源撞位的 LLM 项不参与预分配（后续按手动优先丢弃，留 occ 会是超界脏值）
        overflow_ids |= _preassign_occ(
            [it for it in llm_items if (it["addr"], it["anchor"]) not in explicit_pos],
            mark_low_confidence=True)

    merged, seen_pos, used_keys = [], set(), set()
    for it in explicit + llm_items:
        if id(it) in overflow_ids:
            # 组内溢出项：LLM 对同一留白的重复建议/幻觉，丢弃而非让校验判死全部
            continue
        orig_anchor = it.pop("_orig_anchor", None)
        it.pop("_anchor_pos", None)  # 内部字段：仅预分配排序用，产物不外泄
        pos = (it["addr"], it["anchor"])
        if pos in seen_pos:
            if orig_anchor and (it["addr"], orig_anchor) not in seen_pos:
                it.pop("occ", None)  # occ 属于收缩后 anchor 的组，回退后作废
                it["anchor"] = orig_anchor
                pos = (it["addr"], orig_anchor)
                it["low_confidence"] = True
            elif "occ" in it:
                # 同源同位组内重复（已预分配 occ=1..n）：保留，渲染层按次序落位。
                # 不变式「带 occ 的重复项必属同源组」由预分配阶段保证：
                # 只有 explicit 组与未撞 explicit_pos 的 LLM 组参与预分配——
                # 跨源 LLM 项不带 occ（走下方丢弃），xlsx（preassign_occ=False）
                # 全组不带 occ（重复项同样走下方丢弃，旧 replace-all 语义）
                pass
            else:
                # 跨源撞位（LLM 回显手动占位符）：手动优先丢弃
                continue
        seen_pos.add(pos)
        base_key, key, n = it["key"], it["key"], 2
        while key in used_keys:
            key = f"{base_key}_{n}"
            n += 1
        used_keys.add(key)
        it["key"] = key
        merged.append(it)
    return merged


async def detect_fill_points(tenant_id: str, file_type: str, candidates: list) -> list:
    """识别填写点 = 手动占位符直通 + V1（无位行 LLM 选 anchor）+ V2（有位行
    位编号语义标注 + 兜底）合并（失败抛异常，由 API 层转错误响应）。
    仅此处涉及 LLM/DB（延迟 import 收口在 _get_chat_model，保证纯函数部分无
    运行时依赖、可独立单测）。"""
    if not candidates:
        return []
    explicit = extract_explicit_placeholders(candidates)
    chat_mdl = _get_chat_model(tenant_id)

    async def _chat(system, messages):
        return await chat_mdl.async_chat(system, messages)

    with_slots = [c for c in candidates if c.get("slots")]
    without_slots = [c for c in candidates if not c.get("slots")]
    llm_items, covered, failed = [], set(), 0
    v1_items, failed1 = await _detect_chunked(_chat, file_type, without_slots)
    llm_items.extend(v1_items)
    failed += failed1
    if with_slots:
        v2_items, covered, failed2 = await _detect_slot_chunked(_chat, with_slots)
        llm_items.extend(v2_items)
        llm_items.extend(slot_fallback_items(with_slots, covered))
        failed += failed2
    # V2 条目的切位偏移是确定性 occ 校验闸的判据，但 _merge_detection 会剥离
    # _anchor_pos（内部字段产物不外泄）——按对象身份暂存，合并后回挂给校验闸，
    # 闸后再剥，最终产物保持干净（llm_items 全程持引用，id 不会被复用）
    slot_pos = {id(it): it["_anchor_pos"] for it in llm_items
                if isinstance(it.get("_anchor_pos"), int)
                and not isinstance(it.get("_anchor_pos"), bool)}
    # xlsx 不做 occ 预分配：apply_xlsx_placeholders 是 replace-all 语义不识别 occ，
    # 同格重复占位符预分配会串值覆盖——回到旧「去重丢弃、单 key replace-all」语义
    # candidates 透传：occ 预分配按实际出现次数封顶，超界组内溢出项丢弃而非判死整次识别
    merged = _merge_detection(explicit, llm_items, preassign_occ=(file_type != "xlsx"),
                              candidates=candidates)
    for it in merged:
        pos = slot_pos.get(id(it))
        if pos is not None:
            it["_anchor_pos"] = pos
    merged = _verify_slot_occ(merged, candidates)
    for it in merged:
        it.pop("_anchor_pos", None)
    if failed:
        if not merged:
            raise RuntimeError(f"AI 识别失败：{failed} 个分块全部失败")
        logger.warning("detect: %d 个分块失败，返回部分合并结果（%d 项）", failed, len(merged))
    return merged
