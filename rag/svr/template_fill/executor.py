#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""模板填写：执行引擎（确定性 pipeline，无 Quart 依赖）。

retrieve_slot → generate_values → validate → render，编排入口 execute_task（后续任务补齐）。
所有外部依赖（retriever / LLMBundle / STORAGE_IMPL）经模块属性注入，可独立单测。
"""
import asyncio
import json
import logging
import re

from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from common import settings
from common.constants import LLMType
from rag.app.tag import label_question

logger = logging.getLogger(__name__)

CONTENT_SNIPPET = 800
TOP_K_MIN = 1
TOP_K_MAX = 20
TOP_K_DEFAULT = 6
SIMILARITY_THRESHOLD = 0.2
VECTOR_SIMILARITY_WEIGHT = 0.5


def _run_async(coro):
    """后台线程内执行 async pipeline（线程无事件循环，必须 asyncio.run）。"""
    return asyncio.run(coro)


def _load_and_check_kbs(tenant_id: str, kb_ids: list[str]):
    """加载 KB 并做三道校验：请求的每个 id 都必须真实存在（部分命中视为
    传参错误，不静默丢弃——本函数是 settings.retriever 无权限校验下的唯一
    防线）；全部属于该租户；embd_id 一致（混用向量模型检索会错位）。
    返回校验后的 KB 列表（后续检索只用这份 id，不用原始入参）。"""
    req_ids = [k for k in (kb_ids or []) if k]
    kbs = [k for k in (KnowledgebaseService.get_by_ids(req_ids or [""]) or []) if k]
    if not req_ids or not kbs:
        raise ValueError("知识库不存在或已删除")
    if len(kbs) < len(set(req_ids)):
        raise ValueError("部分知识库不存在或已删除")
    for kb in kbs:
        if kb.tenant_id != tenant_id:
            raise PermissionError(f"知识库 {kb.id} 不属于当前租户")
    if len({kb.embd_id for kb in kbs}) > 1:
        raise ValueError("所选知识库使用了不同的 Embedding 模型，无法混合检索")
    return kbs


def _build_embd_mdl(tenant_id: str, kbs):
    """按 agent/tools/retrieval.py 同款写法构造 embedding 模型（embd_id 已校验一致）。"""
    embd_model_config = get_model_config_by_type_and_name(tenant_id, LLMType.EMBEDDING, kbs[0].embd_id)
    return LLMBundle(tenant_id, embd_model_config)


def _clip_chunk(ck: dict) -> dict:
    """裁剪为对下游生成层最小够用的字段，剥离 vector/content_ltks 等内部大字段。"""
    return {
        "content": (ck.get("content_with_weight") or "")[:CONTENT_SNIPPET],
        "doc_id": ck.get("doc_id", ""),
        "doc_name": ck.get("docnm_kwd", ""),
        "similarity": round(float(ck.get("similarity") or 0), 4),
    }


async def retrieve_slot(tenant_id: str, kb_ids: list[str], query: str, top_k: int = TOP_K_DEFAULT) -> list[dict]:
    """单槽位检索。query 已由上游清洗；异常向上抛由编排层兜底为该槽位空结果。"""
    kbs = _load_and_check_kbs(tenant_id, kb_ids)
    embd_mdl = _build_embd_mdl(tenant_id, kbs)
    page_size = max(TOP_K_MIN, min(int(top_k or TOP_K_DEFAULT), TOP_K_MAX))
    kbinfos = await settings.retriever.retrieval(
        query, embd_mdl, [kb.tenant_id for kb in kbs], [kb.id for kb in kbs],
        1, page_size, SIMILARITY_THRESHOLD, VECTOR_SIMILARITY_WEIGHT,
        aggs=True, rank_feature=label_question(query, kbs))
    return [_clip_chunk(ck) for ck in kbinfos.get("chunks", [])]


# ---------- 生成层：prompt 清洗 + LLM 批量产值 + 约束校验兜底 ----------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PARAM_RE = re.compile(r"\{params\.([a-zA-Z0-9_]+)\}")

DESC_MAX = 500       # description 清洗截断
NAME_MAX = 100
QUERY_MAX = 300
PARAM_VAL_MAX = 100
# 每批 LLM 产值的字段数上限（设计文档 §10：大模板分批）。
# 预算推导：BATCH_SIZE × MAX_EVIDENCE_CHUNKS × CONTENT_SNIPPET = 10 × 6 × 800 ≈ 48K 字符，
# 加字段清单/参数后仍在主流模型上下文窗口内（原 30 → 30×6×800≈144K 会顶爆小窗口模型）。
BATCH_SIZE = 10
MAX_EVIDENCE_CHUNKS = 6  # 每字段进 prompt 的证据片段上限（片段已在检索层截 800 字）
PARAMS_PROMPT_MAX = 2000       # 任务参数整体序列化进 prompt 的截断上限
CONSTRAINTS_PROMPT_MAX = 200   # 单字段 constraints 序列化进 prompt 的截断上限

GENERATE_SYSTEM = (
    "你是文档填写引擎。根据每个字段的【检索证据】填写字段值。规则：\n"
    "1. 只准依据证据作答，禁止编造；证据中找不到的字段值输出 null。\n"
    "2. 遵守字段约束（类型/最大长度）。\n"
    "3. 只输出一个 JSON 对象：{\"字段key\": \"字段值或null\", ...}，不要输出任何其他文字。")


def _clean_for_prompt(text: str, max_len: int) -> str:
    """用户可控的 description/name/retrieval_query 进 prompt 前清洗：
    去控制字符 + 截断。这些字段是 prompt 注入面（遗留债③），只能清洗+截断。"""
    if not isinstance(text, str):
        return ""
    return _CTRL_RE.sub("", text).strip()[:max_len]


def build_retrieval_query(query_tpl: str, params: dict) -> str:
    """retrieval_query 支持 {params.xxx} 引用任务参数：值清洗截断后替换；
    缺 key 原样保留（交由检索层当普通词处理）。"""
    def _sub(m):
        val = (params or {}).get(m.group(1))
        return _clean_for_prompt(str(val), PARAM_VAL_MAX) if val is not None else m.group(0)
    return _PARAM_RE.sub(_sub, _clean_for_prompt(query_tpl or "", QUERY_MAX))


def _build_chat_mdl(tenant_id: str):
    """照 detector.py 模式构造默认对话模型（延迟 import，纯函数部分可独立单测）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from common.constants import LLMType as _LT
    return LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, _LT.CHAT))


def _extract_json(text: str) -> dict:
    """LLM 输出不可信：容忍 ```json 围栏/前后杂文，抽第一个 JSON 对象。
    贪婪匹配（应对正文中含 `}` 的合法对象）失败后，补一次非贪婪匹配
    （应对 LLM 输出尾部带花括号杂文：贪婪把杂文吞进去导致解析失败）。
    两次都失败 → 记 warning 返回空 dict（调用方据此整批 missing，不再静默）。"""
    s = text or ""
    for pattern in (r"\{.*\}", r"\{.*?\}"):
        m = re.search(pattern, s, re.DOTALL)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    logger.warning("generate_values: LLM 输出无法解析为 JSON 对象, raw[:200]=%r", s[:200])
    return {}


def _apply_constraints(value, constraints: dict):
    """类型/字数兜底（LLM 安全网）。返回规整后的值；不可修复 → None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a"):
        return None
    ctype = (constraints or {}).get("type")
    if ctype == "number":
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            return None
    try:
        max_len = int((constraints or {}).get("max_length") or 0)
    except (TypeError, ValueError):
        max_len = 0
    return text[:max_len] if max_len > 0 else text


async def generate_values(tenant_id: str, placeholders: list[dict], chunks_by_key: dict,
                          params: dict | None = None,
                          batch_size: int = BATCH_SIZE) -> tuple[dict, set]:
    """LLM 批量产值：一次调用产 ≤batch_size 个字段值（超出分批）。
    batch_size 为 0/None 等假值时兜底为 BATCH_SIZE（step 与 slice 必须同值，
    否则 0 产生空批、None 导致 slice 取全量重复发送）。
    返回 (values, missing_keys)。每字段证据最多取 6 片（片段已截 800 字）。"""
    missing: set = set()
    values: dict = {}
    mdl = None
    step = max(int(batch_size or BATCH_SIZE), 1)
    for i in range(0, len(placeholders), step):
        batch = placeholders[i:i + step]
        spec = []
        for it in batch:
            key = _clean_for_prompt(it["key"], NAME_MAX)
            spec.append({"key": key,
                         "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
                         "description": _clean_for_prompt(it.get("description"), DESC_MAX),
                         "constraints": _clean_for_prompt(
                             json.dumps(it.get("constraints") or {}, ensure_ascii=False),
                             CONSTRAINTS_PROMPT_MAX)})
        evidence = []
        for it in batch:
            key = _clean_for_prompt(it["key"], NAME_MAX)
            chunks = (chunks_by_key.get(it["key"]) or {}).get("chunks", [])
            joined = "\n---\n".join(f"[片段{j + 1}] {c['content']}"
                                    for j, c in enumerate(chunks[:MAX_EVIDENCE_CHUNKS])) or "（无检索证据）"
            evidence.append(f"### 字段 {key}\n{joined}")
        user_msg = ("## 字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                    "\n\n## 检索证据\n" + "\n\n".join(evidence) +
                    "\n\n任务参数（背景信息）：" + _clean_for_prompt(
                        json.dumps(params or {}, ensure_ascii=False, default=str), PARAMS_PROMPT_MAX))
        if mdl is None:
            mdl = _build_chat_mdl(tenant_id)
        ans = await mdl.async_chat(GENERATE_SYSTEM, [{"role": "user", "content": user_msg}])
        raw = _extract_json(ans)
        for it in batch:
            key = it["key"]
            val = _apply_constraints(raw.get(key), it.get("constraints") or {})
            if val is None:
                missing.add(key)
            else:
                values[key] = val
    return values, missing
