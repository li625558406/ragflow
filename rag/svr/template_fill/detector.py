"""LLM 填写点识别：prompt 构造、响应解析与占位符清单校验（纯函数部分可独立单测）。"""
import json
import re

FILL_MODES = ("llm", "param", "manual")
KEY_RE = re.compile(r"[^a-z0-9_]+")
MAX_ANCHOR_LEN = 500  # anchor 超长约束收口在 parse：识别阶段就拦住异常项，不让脏数据流入人工确认/apply 链路

DETECT_SYSTEM = """你是文档模板分析专家。用户给出固定模板中疑似需要填写的编号行（行号\\t文本）。
请识别其中所有"填写点"——模板留空、需要后续填写内容的位置。
输出 JSON 数组，每个元素：
{"line": 行号(int), "anchor": "该行原文中将被替换为占位符的精确子串", "key": "snake_case英文标识", "name": "中文字段名", "description": "给填写模型的说明", "retrieval_query": "适合去知识库检索的查询词", "fill_mode": "llm 或 manual", "required": true或false}
规则：
1. anchor 必须是该行原文的精确子串，禁止改写；一行可有多个填写点（拆成多个元素）。
2. 同一含义的填写点 key 全局唯一；日期类建议 key 如 sign_date。
3. 无法确定如何填写的位置用 fill_mode=manual。
4. 找不到任何填写点输出 []。只输出 JSON 数组，不要输出其它文字。"""


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
        key = normalize_key(it.get("key") or it.get("name") or "field")
        while key in used_keys:
            key = f"{key}_2"
        used_keys.add(key)
        mode = it.get("fill_mode") if it.get("fill_mode") in FILL_MODES else "llm"
        out.append({
            "key": key,
            "name": str(it.get("name") or key)[:100],
            "description": str(it.get("description") or ""),
            "retrieval_query": str(it.get("retrieval_query") or ""),
            "fill_mode": mode,
            "required": bool(it.get("required", True)),
            "addr": cand["addr"],
            "anchor": anchor,
            "line": line,
            "top_k": 6,
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


async def detect_fill_points(tenant_id: str, file_type: str, candidates: list) -> list:
    """调用租户默认 chat 模型识别填写点（失败抛异常，由 API 层转错误响应）。
    仅此处涉及 LLM/DB（延迟 import，保证纯函数部分无运行时依赖、可独立单测）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType

    if not candidates:
        return []
    model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
    chat_mdl = LLMBundle(tenant_id, model_config)
    numbered = "\n".join(f'{c["index"]}\t{c["text"]}' for c in candidates)
    user_msg = f"文件类型：{file_type}\n编号行：\n{numbered}"
    ans = await chat_mdl.async_chat(DETECT_SYSTEM, [{"role": "user", "content": user_msg}])
    return parse_detection_response(ans, candidates)
