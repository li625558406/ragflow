"""LLM 填写点识别：prompt 构造、响应解析与占位符清单校验（纯函数部分可独立单测）。"""
import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

FILL_MODES = ("llm", "param", "manual")
KEY_RE = re.compile(r"[^a-z0-9_]+")
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
2. 同一含义的填写点 key 全局唯一；日期类建议 key 如 sign_date。
3. fill_mode 一律填 "llm"（所有填写点统一交给 AI 检索填写，检索不到的留空由人工后续加工）。
4. 找不到任何填写点输出 []。只输出 JSON 数组，不要输出其它文字。
5. 正文叙述中以冒号结尾、用于引出下文的句子（如"包括以下内容："、"下列情形之一："）不是填写点，不要输出；只有"标签：＋留白待填值"（如 申请人：/地址：/编号： 后跟空白）才是填写点。"""


def normalize_key(key: str) -> str:
    """归一化为 snake_case：小写 + 非法字符替换为下划线；全空兜底 "field"。
    注意：不截断长度——长度约束统一收口在 validate_placeholders（[a-z][a-z0-9_]{0,63}）。"""
    k = KEY_RE.sub("_", str(key).strip().lower())
    return k.strip("_") or "field"


def parse_detection_response(raw: str, candidates: list) -> list:
    """解析 LLM 输出 → 校验后的建议清单。行号/锚文本不合法的项直接丢弃。"""
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
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
        # 标签型 → 收缩到标签后的留白串，收缩失败丢弃（标签绝不能整体被替换）；
        # 实心（无留白特征）→ 同行有留白特征则收缩，失败保留并打低置信；
        # 整行无留白特征 → 已填范本现值是合法 anchor，保留并打低置信警示。
        low_confidence = False
        if _LABEL_ANCHOR_RE.fullmatch(anchor):
            shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
            if not shrunk:
                continue
            anchor = shrunk
        elif not _BLANK_MARK_RE.search(anchor):
            if _BLANK_MARK_RE.search(cand["text"]):
                shrunk = _shrink_anchor_to_blank(anchor, cand["text"])
                if shrunk:
                    anchor = shrunk
                else:
                    low_confidence = True
            else:
                low_confidence = True
        key = normalize_key(it.get("key") or it.get("name") or "field")
        while key in used_keys:
            key = f"{key}_2"
        used_keys.add(key)
        # fill_mode 代码层强制 llm：识别产物统一交给 AI 检索填写（prompt 只是引导，
        # LLM 不听话也拦得住）；manual/param 只能由人工在详情页显式配置
        out.append({
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
            it["addr"] = cand["addr"]  # 回填，保证落库的占位符都有有效 addr
        else:
            cand = cand_map.get(addr)
            if cand is None:
                return False, f"{key} 的定位 {addr!r} 不存在"
            if not anchor or anchor not in cand["text"]:
                return False, f"{key} 的 anchor 不在 {addr} 文本中"
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


def _merge_detection(explicit: list, llm_items: list) -> list:
    """合并手动直通项与 LLM 识别项：(addr, anchor) 去重（手动优先），跨源/跨块
    key 冲突按出现顺序加 _2/_3 后缀（与 parse_detection_response 单次调用内
    去重语义一致，但作用域为整次识别）。"""
    merged, seen_pos, used_keys = [], set(), set()
    for it in explicit + llm_items:
        pos = (it["addr"], it["anchor"])
        if pos in seen_pos:
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
    """识别填写点 = 手动占位符直通 + 分块 LLM 识别合并（失败抛异常，由 API 层转错误响应）。
    仅此处涉及 LLM/DB（延迟 import，保证纯函数部分无运行时依赖、可独立单测）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType

    if not candidates:
        return []
    explicit = extract_explicit_placeholders(candidates)
    model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    chat_mdl = LLMBundle(tenant_id, model_config)

    async def _chat(system, messages):
        return await chat_mdl.async_chat(system, messages)

    llm_items, failed = await _detect_chunked(_chat, file_type, candidates)
    merged = _merge_detection(explicit, llm_items)
    if failed:
        if not merged:
            raise RuntimeError(f"AI 识别失败：{failed} 个分块全部失败")
        logger.warning("detect: %d 个分块失败，返回部分合并结果（%d 项）", failed, len(merged))
    return merged
