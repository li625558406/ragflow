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
import time

from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from common import settings
from common.constants import LLMType
from rag.app.tag import label_question
from rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)

CONTENT_SNIPPET = 800
TOP_K_MIN = 1
TOP_K_MAX = 20
TOP_K_DEFAULT = 6
SIMILARITY_THRESHOLD = 0.2
VECTOR_SIMILARITY_WEIGHT = 0.5
# 逐槽检索并发路数（_retrieve_all 信号量）：大模板百级填写点并发 6 路，
# 单槽 ES 查询秒级耗时下把小时级串行压到分钟级，同时不把 ES/embd 打爆
RETRIEVAL_CONCURRENCY = 6

FULLTEXT_SIMILARITY_THRESHOLD = 0.1   # 二档全文宽检索阈值（低于一档 SIMILARITY_THRESHOLD）
_TIER2_ENTITY_MAX = 3                 # 二档组合词最多取实体值个数
_ENTITY_PRIO_WORDS = ("项目", "名称", "标题")


def _run_async(coro):
    """后台线程内执行 async pipeline（线程无事件循环，必须 asyncio.run）。"""
    return asyncio.run(coro)


def _should_cancel(cancel):
    """防御式取消探针（与 on_progress 的异常兜底对称）：探针回调自身抛异常时
    视为「未取消」并记一次 warning，异常不向上传播、也不吞掉真实取消信号。
    契约：cancel 为 None/假值 → False；回调异常 → False；其余返回 bool(cancel())。"""
    if not cancel:
        return False
    try:
        return bool(cancel())
    except Exception:
        logger.warning("should_cancel callback raised; treating as not cancelled", exc_info=True)
        return False


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


def load_retrieval_ctx(tenant_id: str, kb_ids: list[str]):
    """检索上下文一次性加载（知识库校验 + embedding 模型），供同批多槽复用，
    免去每槽重复 DB 查询与模型构建。"""
    kbs = _load_and_check_kbs(tenant_id, kb_ids)
    embd_mdl = _build_embd_mdl(tenant_id, kbs)
    return kbs, embd_mdl


async def retrieve_slot(tenant_id: str, kb_ids: list[str], query: str, top_k: int = TOP_K_DEFAULT,
                        ctx=None, similarity_threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """单槽位检索。query 已由上游清洗；异常向上抛由编排层兜底为该槽位空结果。
    ctx 为 load_retrieval_ctx 产物（多槽并发时复用，省每槽重复加载）。
    similarity_threshold 供二档宽检索传更低阈值（默认一档现状值）。"""
    kbs, embd_mdl = ctx if ctx else load_retrieval_ctx(tenant_id, kb_ids)
    page_size = max(TOP_K_MIN, min(int(top_k or TOP_K_DEFAULT), TOP_K_MAX))
    kbinfos = await settings.retriever.retrieval(
        query, embd_mdl, [kb.tenant_id for kb in kbs], [kb.id for kb in kbs],
        1, page_size, similarity_threshold, VECTOR_SIMILARITY_WEIGHT,
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
# 产值批次并发上限：批次间字段独立无依赖，并发安全；大批量模板（百级填写点）
# 串行 20 批 × 单批 20~40s 会放大成 10 分钟级阻塞。共享 sem 时以外部闸为准。
GENERATE_CONCURRENCY = 3
MAX_EVIDENCE_CHUNKS = 6  # 每字段进 prompt 的证据片段上限（片段已在检索层截 800 字）
PARAMS_PROMPT_MAX = 2000       # 任务参数整体序列化进 prompt 的截断上限
CONSTRAINTS_PROMPT_MAX = 200   # 单字段 constraints 序列化进 prompt 的截断上限
DEFAULT_HINT_MAX = 100         # 默认值作为 prompt 参考提示的截断上限
# 增量抽取里「按已填旧值定位字段」的最小可定位长度：单字符值（是/无/男/女/0）
# 在中文语境里几乎必然作为子串出现在任意原话中（「但是」「是否」「是不是」），
# 按它定位必改错字段。1 字符一律禁用 current 定位（宁漏不误，漏了还有 name/key）
MIN_CURRENT_MATCH_LEN = 2

GENERATE_SYSTEM = (
    "你是文档填写引擎。根据每个字段的【检索证据】填写字段值。规则：\n"
    "1. 只准依据证据作答，禁止编造；证据中找不到的字段值输出 null。\n"
    "2. 遵守字段约束（类型/最大长度）。\n"
    "3. 字段带 default_value 时为该字段上次填写值，可作参考；证据与之冲突时以证据为准。\n"
    "4. 只输出一个 JSON 对象：{\"字段key\": \"字段值或null\", ...}，不要输出任何其他文字。")

PREDICT_CHUNK = 200  # 预判清单分块阈值：400字段×~150字符≈60K，超小窗口模型风险，分块串行
PREDICT_SYSTEM = (
    "你是文档填写助手。给出范本的默认值字段清单（key/名称/当前默认值）和本次填写需求。"
    "请判断哪些字段在本次填写中需要更新（与需求直接相关、或默认值明显是待改样例）。"
    "无关字段一律保持默认。只输出 JSON 对象：{\"changed\": [\"key\", ...]}，不要输出其他文字。")


class GenerateCancelled(Exception):
    """should_cancel 命中时抛出：调用方（组件/B端管道）捕获后各自收口。"""


# ── 画布委托任务：params 保留键 + Redis 进度快照 ──────────────────────
# 画布节点把确认产物（直填值/LLM 白名单键 _changed_keys）、用户文件证据、
# 检索跳过键以下划线前缀保留键塞进 params 传给 execute_task；干净 params 继续
# 充当背景信息与 param 直取（与 B端表单字段同构）。
# 跨模块契约（故不带下划线前缀）：template_api.create_fill_task 必须把这个集合
# 从 REST 入参里剥离，保证「params 带保留键 ⇔ 画布节点写入了用户确认决策」这一
# 等价关系成立——写回端点与下方 is_canvas 都依赖它做判定。
CANVAS_RESERVED_KEYS = ("_direct_values", "_changed_keys",
                        "_retrieve_skip_keys", "_user_file_text",
                        "_baseline_values")

# 进度快照键与 TTL（24h；终态也写一次，靠 TTL 过期，不主动删）
_PROGRESS_KEY = "tpl_fill_progress:{task_id}"
_PROGRESS_TTL = 24 * 3600
# 快照节流：非 force 写距上次 <0.5s 直接跳过（抑制 600 键模板 × 数十批的
# 全量序列化写放大）。跳过只丢中间进度——快照是幂等覆盖语义，终态写一律
# force=True 兜底，最终值不丢。多任务并发按 task_id 分桶；GIL 下 dict
# 读写原子，时间窗口误差无害，不加锁。
_PROGRESS_MIN_INTERVAL = 0.5
_last_snapshot_ts: dict[str, float] = {}
# 画布取消键：节点在画布被停止时为未终态任务写该键，executor 探针命中即中断
_CANCEL_KEY = "tpl_fill:cancel:{task_id}"
_CANCEL_TTL = 3600


def split_canvas_params(params: dict | None) -> tuple[dict, dict]:
    """拆出画布委托塞在 params 里的保留键，返回 (干净 params, opts)。
    前端/画布数据不可信：保留键载荷一律防御式清洗（非 dict/list 按空处理、
    非 str 项 str 归一），不炸 pipeline。"""
    params = params if isinstance(params, dict) else {}
    dv = params.get("_direct_values")
    bv = params.get("_baseline_values")
    opts = {
        "direct_values": ({str(k): (str(v) if v is not None else "")
                           for k, v in dv.items()}
                          if isinstance(dv, dict) else {}),
        "changed_keys": {str(k) for k in (params.get("_changed_keys") or [])},
        "retrieve_skip_keys": {str(k) for k in (params.get("_retrieve_skip_keys") or [])},
        "user_file_text": str(params.get("_user_file_text") or ""),
        # baseline：上一轮已落成稿的真实填写值（非 B 端 default_value 提示），
        # 仅对 missing 字段兜底，优先级 default_value 之前，让文档保留上次内容
        "baseline_values": ({str(k): str(v) for k, v in bv.items() if v is not None}
                            if isinstance(bv, dict) else {}),
    }
    clean = {k: v for k, v in params.items() if k not in CANVAS_RESERVED_KEYS}
    return clean, opts


def _write_snapshot(task_id: str, force: bool = False, **fields) -> None:
    """写进度快照（累积 values 由调用方传全量）。Redis 故障只告警——
    快照是重连体验增强，不是执行的权威路径（权威在 DB 行）。
    非 force 写按 task_id 节流（_PROGRESS_MIN_INTERVAL 秒内跳过）；终态写
    （done/failed/cancelled）一律 force=True，保证最终值必落。"""
    try:
        now = time.time()
        if not force and now - _last_snapshot_ts.get(task_id, 0.0) < _PROGRESS_MIN_INTERVAL:
            return
        _last_snapshot_ts[task_id] = now
        payload = {"updated_at": int(now * 1000)}  # 毫秒 epoch，与读侧 current_timestamp() 同量纲
        payload.update(fields)
        REDIS_CONN.set(_PROGRESS_KEY.format(task_id=task_id),
                       json.dumps(payload, ensure_ascii=False), exp=_PROGRESS_TTL)
    except Exception:
        logger.warning("write fill progress snapshot failed, task=%s", task_id, exc_info=True)


def read_progress_snapshot(task_id: str) -> dict | None:
    """读进度快照（progress 端点用）。失败/不存在返回 None，调用方退化读 DB。"""
    try:
        raw = REDIS_CONN.get(_PROGRESS_KEY.format(task_id=task_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("read fill progress snapshot failed, task=%s", task_id, exc_info=True)
        return None


def write_cancel_key(task_id: str) -> None:
    """写画布取消键（节点取消路径用）。失败只告警：最坏情况是任务跑完但
    画布已不在看（结果仍落 DB 可取），不会产生副作用错误。"""
    try:
        REDIS_CONN.set(_CANCEL_KEY.format(task_id=task_id), "1", exp=_CANCEL_TTL)
    except Exception:
        logger.warning("write fill cancel key failed, task=%s", task_id, exc_info=True)


def _make_cancel_probe(task_id: str):
    """execute_task 用的取消探针：命中画布取消键 → True；Redis 异常视为未取消。"""
    def _probe() -> bool:
        try:
            return bool(REDIS_CONN.get(_CANCEL_KEY.format(task_id=task_id)))
        except Exception:
            return False
    return _probe


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


def _default_hint(it: dict) -> str:
    """产值 prompt 的默认值参考提示（无则空串）。
    口径说明（有意设计，非遗漏）：prompt 提示只截 100 字（DEFAULT_HINT_MAX，
    仅为 LLM 提供参考）；fallback 兜底（_merge_default_values）用全量默认值
    （save 层入库时截 500）。两处长度口径不同：前者省 token，后者还原原值。"""
    return _clean_for_prompt(str(it.get("default_value") or ""), DEFAULT_HINT_MAX)


def _merge_default_values(placeholders: list[dict], generated: dict, missing: set):
    """P1 兜底：LLM 提取不到（missing）的 llm 字段直取默认值。只填 missing、
    不覆盖已有产值；param 模式不兜底（param 直取失败无默认语义）。就地修改。
    默认值来自 B 端存量数据，不可信：先 str 归一 + 控制字符剥离（防 dict/int/
    控制字符原样落渲染），再过 _apply_constraints 约束闸（与 LLM 产值同一道
    截断/类型校验）；约束判 None（如 number 字段默认值非数字）则留在 missing
    不写 generated，宁缺勿错。"""
    for it in placeholders:
        if _norm_fill_mode(it) != "llm":
            continue
        key = it.get("key")
        if not (key and key in missing):
            continue
        raw = _CTRL_RE.sub("", str(it.get("default_value") or "")).strip()
        if not raw:
            continue
        v = _apply_constraints(raw, it.get("constraints") or {})
        if v is not None:
            generated[key] = v
            missing.discard(key)


def derive_unfilled(placeholders: list[dict], values: dict) -> list[dict]:
    """终态派生成稿留空的填写点：values 中该 key 缺失/None/空串/纯空白 = 未填充。
    统一覆盖 LLM 无产值、直填空串清空、白名单外无默认值三条留空路径，与成稿
    实际内容一致（docxtpl 渲染时空值即留白）。判空须显式 None 判断而非 `or ""`
    折叠——数值 0/0.0/False 是有效产值（number 字段过 _apply_constraints 后
    为 int/float，渲染成 "0" 非留白）；"0"/"false" 字符串同理不算留空。
    required 缺失兜底 True（detector 默认）。"""
    return [{"key": it["key"], "name": it.get("name") or it["key"],
             "required": bool(it.get("required", True))}
            for it in (placeholders or [])
            if it.get("key")
            and (values is None or (v := (values or {}).get(it["key"])) is None
                 or not str(v).strip())]


def derive_filled(placeholders: list[dict], values: dict) -> list[dict]:
    """终态已填充的填写点（[{key, name}]，保 placeholders 文档序；**不下发值**——
    前端从已下发的 values 里 join，避免同一事件里重复传输几千字）。
    与 derive_unfilled 互补，且互补性由构造保证：取「有 key 的填写点 − 未填充集合」，
    判空真源只有 derive_unfilled 一处，杜绝两处谓词漂移导致 key→name 映射静默漏项
    （漏项时 LivePreview 会悄悄回落英文 key，用户看不出错但用不了）。
    第二段的过滤写法与 derive_unfilled 逐字一致，失败模式（非 dict 项、values 非
    dict 真值）随之完全同构。"""
    unfilled_keys = {it["key"] for it in derive_unfilled(placeholders, values)}
    return [{"key": it["key"], "name": it.get("name") or it["key"]}
            for it in (placeholders or [])
            if it.get("key") and it["key"] not in unfilled_keys]


async def generate_values(tenant_id: str, placeholders: list[dict], chunks_by_key: dict,
                          params: dict | None = None,
                          batch_size: int = BATCH_SIZE,
                          sem: asyncio.Semaphore | None = None,
                          on_progress=None,
                          should_cancel=None) -> tuple[dict, set]:
    """LLM 批量产值：一次调用产 ≤batch_size 个字段值（超出分批），批次间并发
    （GENERATE_CONCURRENCY 路；字段独立无依赖，并发安全）。
    batch_size 为 0/None 等假值时兜底为 BATCH_SIZE（step 与 slice 必须同值，
    否则 0 产生空批、None 导致 slice 取全量重复发送）。
    sem 为跨层共享并发闸（画布多范本并行时传入全局信号量，使多范本 × 批次
    总并发不超闸值）；不传则内部自建。
    on_progress 为可选回调 (done, total, new_values)：每批 LLM 返回后即回调一次（批次并发下
    完成顺序不定），进度上报用；new_values 为该批已过约束闸的产出值 dict（实时预览用）；
    回调异常被吞掉不影响产值。
    should_cancel 为可选取消探针（无参同步回调，返回 True 表示外部要求取消）：
    每批拿到信号量后、以及每批结果回收后各检查一次，命中即抛 GenerateCancelled，
    在途批次结果丢弃不落返回值；探针自身抛异常视为未取消（warning 不传播）；
    传 None 时行为与不取消完全一致。
    返回 (values, missing_keys)。每字段证据最多取 6 片（片段已截 800 字）。"""
    step = max(int(batch_size or BATCH_SIZE), 1)
    batches = [placeholders[i:i + step] for i in range(0, len(placeholders), step)]
    if not batches:
        return {}, set()
    sem = sem or asyncio.Semaphore(GENERATE_CONCURRENCY)
    mdl = _build_chat_mdl(tenant_id)

    def _build_msg(batch: list[dict]) -> str:
        spec = []
        for it in batch:
            key = _clean_for_prompt(it["key"], NAME_MAX)
            spec.append({"key": key,
                         "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
                         "description": _clean_for_prompt(it.get("description"), DESC_MAX),
                         "constraints": _clean_for_prompt(
                             json.dumps(it.get("constraints") or {}, ensure_ascii=False),
                             CONSTRAINTS_PROMPT_MAX),
                         "default_value": _default_hint(it)})
        evidence = []
        for it in batch:
            key = _clean_for_prompt(it["key"], NAME_MAX)
            chunks = (chunks_by_key.get(it["key"]) or {}).get("chunks", [])
            joined = "\n---\n".join(f"[片段{j + 1}] {c['content']}"
                                    for j, c in enumerate(chunks[:MAX_EVIDENCE_CHUNKS])) or "（无检索证据）"
            evidence.append(f"### 字段 {key}\n{joined}")
        return ("## 字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                "\n\n## 检索证据\n" + "\n\n".join(evidence) +
                "\n\n任务参数（背景信息）：" + _clean_for_prompt(
                    json.dumps(params or {}, ensure_ascii=False, default=str), PARAMS_PROMPT_MAX))

    async def _one(batch: list[dict]):
        user_msg = _build_msg(batch)
        async with sem:
            if _should_cancel(should_cancel):
                raise GenerateCancelled()
            ans = await mdl.async_chat(GENERATE_SYSTEM, [{"role": "user", "content": user_msg}])
        return batch, _extract_json(ans)

    total = sum(len(b) for b in batches)
    done_slots = 0
    results = []
    try:
        for fut in asyncio.as_completed([_one(b) for b in batches]):
            results.append(await fut)
            if _should_cancel(should_cancel):
                raise GenerateCancelled()
            if on_progress:
                done_slots += len(results[-1][0])
                try:
                    batch, raw = results[-1]
                    # 实时预览：把该批已产出（过约束闸）的字段值随回调带出，
                    # 与最终返回 values 同口径；约束处理异常只损本次实时事件
                    new_vals = {}
                    for it in batch:
                        val = _apply_constraints(raw.get(it["key"]), it.get("constraints") or {})
                        if val is not None:
                            new_vals[it["key"]] = val
                    on_progress(done_slots, total, new_vals)
                except Exception:
                    logger.exception("generate_values on_progress callback failed")
    except GenerateCancelled:
        logger.info("generate_values cancelled: %d/%d batches done", len(results), len(batches))
        raise
    missing: set = set()
    values: dict = {}
    for batch, raw in results:
        for it in batch:
            key = it["key"]
            val = _apply_constraints(raw.get(key), it.get("constraints") or {})
            if val is None:
                missing.add(key)
            else:
                values[key] = val
    return values, missing


async def predict_changed_fields(tenant_id: str, default_items: list[dict],
                                 background: dict | None = None,
                                 should_cancel=None) -> set:
    """1 次/块 LLM 调用预判需要更新的默认值字段集合。
    返回校验后 key 集合（编造/非法 key 过滤）；LLM 失败或输出不可解析 → 空集
    （调用方按"全部保持默认，用户确认时手动挑"兜底）。GenerateCancelled 穿透。"""
    # 缺 key / 非 dict 的脏 item 不参与预判（valid 集合与 spec 构建都会
    # KeyError/AttributeError，与同类脏数据防御口径一致）
    default_items = [it for it in default_items
                     if isinstance(it, dict) and it.get("key")]
    if not default_items:
        return set()
    mdl = _build_chat_mdl(tenant_id)
    valid = {it["key"] for it in default_items}
    found: set = set()
    for i in range(0, len(default_items), PREDICT_CHUNK):
        spec = [{"key": _clean_for_prompt(it["key"], NAME_MAX),
                 "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
                 "default_value": _default_hint(it)}
                for it in default_items[i:i + PREDICT_CHUNK]]
        user_msg = ("## 默认值字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                    "\n\n本次填写需求：" + _clean_for_prompt(
                        json.dumps(background or {}, ensure_ascii=False, default=str),
                        PARAMS_PROMPT_MAX))
        if _should_cancel(should_cancel):
            raise GenerateCancelled()
        try:
            ans = await mdl.async_chat(PREDICT_SYSTEM, [{"role": "user", "content": user_msg}])
            raw = _extract_json(ans)
            keys = raw.get("changed")
            if isinstance(keys, list):
                found |= {k for k in keys if isinstance(k, str) and k in valid}
        except GenerateCancelled:
            raise
        except Exception:
            logger.warning("predict_changed_fields chunk failed; keep defaults", exc_info=True)
    return found


# 增量填写场景：从用户 query + 上次填写值抽 patch（要改的字段+值）
# 节点在检测到同范本已有 done 成稿时调用，决定本次走「改字段」「补全留空」还是「全量重填」
PATCH_EXTRACT_SYSTEM = (
    "你是文档增量修改助手。给出一个范本的全部填写点清单"
    "（每项含 key / 中文名 name / 当前已填值 current / 该 current 能否用于定位 current_ok）"
    "和用户本轮原话。请判断用户本轮的意图并产出 JSON，**只能**输出 JSON 对象：\n"
    "{\"intent\": \"patch\"|\"refill\"|\"fill_unfilled\"|\"noop\","
    " \"direct\": {\"key\": \"新值\", ...}, \"changed\": [\"key\", ...]}\n"
    "规则：\n"
    "- intent=\"patch\"：用户在原话里明确指定了要改的字段及其新值（中文名、key 或该字段的当前值）。"
    "direct 放字段 key→新值（必填，值留空也算 patch 即明确清空），changed 放所有要改的 key（与 direct keys 一致即可）。"
    "用户说「xx 填成 yy」「xx 改成 yy」「xx 留空」都属于 patch。\n"
    "- intent=\"fill_unfilled\"：用户想补全/完善上次没填上的字段（如「完善」「补全」「继续填」「还有哪些没填的」）。"
    "把当前已填值仍为空（\"\" 或 null 或 缺失）的字段填进 changed（不需要 direct 值——走检索+LLM）。\n"
    "- intent=\"refill\"：用户明确要求全部重新填写（如「重新填」「全部重写」「从头填」）。"
    "直接返回 {\"intent\": \"refill\"} 即可。\n"
    "- intent=\"noop\"：用户没说修改/补全/重填任何字段，且当前已填值无留空。"
    "返回 {\"intent\": \"noop\"} 即可。\n"
    "定位字段严格按此优先级，命中即停："
    "① 原话中的中文名与 name 字面匹配；② 原话中的 key 与 key 字面匹配；"
    "③ 仅当 ①② 都匹配不上时，才可用 current 定位，且必须同时满足：该字段 current_ok 为 true，"
    "并且原话**逐字包含**该字段的 current 原文。\n"
    "current_ok=false 表示该值只有一个字、与别的字段当前值相同或互相包含、或已被截断，"
    "按它定位会改错字段，一律禁止使用。"
    "也不要用 current 去匹配原话未逐字包含的字段（如原话只说了近似值、或只描述了字段含义）。\n"
    "key 必须在清单中——匹配不上、含糊或与你意图判断不匹配的字段一律忽略，不要编造 key。"
    "未提及的字段不要塞进 direct/changed。")


async def extract_patch_values(tenant_id: str, placeholders: list[dict],
                                baseline_values: dict,
                                query: str,
                                should_cancel=None) -> dict:
    """增量填写意图+字段抽取（1 次 LLM 调用）：从用户 query + 上次填写值抽 patch。
    返回：{"intent": str, "direct": dict, "changed": list[str]}
      - intent ∈ {"patch", "refill", "fill_unfilled", "noop"}
      - direct: 用户在原话里明确给的值（key → str）
      - changed: 本次要走的字段 key 集合（包含 direct keys + 用户没给值但希望检索的）
    LLM 解析失败/输出非法 → 返回 noop（节点兜底走 baseline unfilled 提示）。"""
    items = [it for it in placeholders
             if isinstance(it, dict) and it.get("key")]
    if not items:
        return {"intent": "noop", "direct": {}, "changed": []}
    valid = {it["key"] for it in items}
    by_key = {it["key"]: it for it in items}
    base_norm = baseline_values if isinstance(baseline_values, dict) else {}
    spec = []
    for it in items:
        k = it["key"]
        cur = base_norm.get(k)
        if cur is None:
            cur_str = ""
        else:
            cur_str = str(cur)
        spec.append({"key": _clean_for_prompt(k, NAME_MAX),
                     "name": _clean_for_prompt(it.get("name") or k, NAME_MAX),
                     "current": _clean_for_prompt(cur_str, DEFAULT_HINT_MAX)})
    # current_ok：LLM 按「已填旧值」定位字段的前提是——该值在清单里唯一、足够具体、
    # 且 LLM 看到了完整原文。三种失效情形都置 false（宁漏不误：漏了还有 name/key 两条路，
    # 误了就直接改错文档字段）：
    #  ① 太短——单字符值（是/无/男/女/0）在中文里几乎必然作为子串出现在任意原话中，
    #     判据是「原话逐字包含该值」，单字符必然命中 → 按 MIN_CURRENT_MATCH_LEN 一律禁用；
    #  ② 值歧义——精确重复（「张三」同时是两个字段的当前值）或**子串包含**
    #     （「张三」/「张三丰」、「5」/「50」）：原话引用较长值时，较短值也满足
    #     「逐字包含」，prompt ③ 无并列消歧规则 → LLM 可能选中错字段。故包含关系
    #     双方一律置 false（长度阈值已挡掉 1 字符，此处处理 ≥2 字符的包含对）；
    #  ③ 截断——current 进 prompt 前被 _clean_for_prompt 截到 DEFAULT_HINT_MAX，
    #     LLM 只见前缀，原话里的完整值无法与之逐字比对。截断后长度等于上限即
    #     无法区分「原本正好 100 字符」与「被截断」，故一律按不可用处理。
    # 注意不把不可用的 current 清空：它是意图判断（noop / fill_unfilled）的输入，
    # 清空会让 LLM 误判「该字段当前为空」。
    counts: dict[str, int] = {}
    for r in spec:
        c = r["current"]
        if c:
            counts[c] = counts.get(c, 0) + 1
    # 子串包含：任一方向包含即双方不可用（空串是任意串的子串，故先 `if c` 过滤）
    ambiguous: set[str] = set()
    curs = [c for c in counts if c]
    for i, a in enumerate(curs):
        for b in curs[i + 1:]:
            if a in b or b in a:
                ambiguous.add(a)
                ambiguous.add(b)
    for r in spec:
        c = r["current"]
        r["current_ok"] = (len(c) >= MIN_CURRENT_MATCH_LEN
                           and len(c) < DEFAULT_HINT_MAX
                           and counts.get(c, 0) == 1
                           and c not in ambiguous)
    user_msg = ("## 填写点清单\n" + json.dumps(spec, ensure_ascii=False)
                + "\n\n用户原话：" + _clean_for_prompt(
                    (query or "").strip(), PARAMS_PROMPT_MAX))
    if _should_cancel(should_cancel):
        raise GenerateCancelled()
    try:
        mdl = _build_chat_mdl(tenant_id)
        ans = await mdl.async_chat(
            PATCH_EXTRACT_SYSTEM, [{"role": "user", "content": user_msg}])
        raw = _extract_json(ans)
        intent = str(raw.get("intent") or "").strip().lower()
        if intent not in ("patch", "refill", "fill_unfilled", "noop"):
            logger.warning("extract_patch_values: bad intent=%r, fallback noop", intent)
            return {"intent": "noop", "direct": {}, "changed": []}
        direct_raw = raw.get("direct") if isinstance(raw.get("direct"), dict) else {}
        direct = {str(k): str(v) for k, v in direct_raw.items()
                  if isinstance(k, str) and k in valid}
        # 防御：value 走 _apply_constraints 兜底（与产值同口径），通过才保留
        direct_validated: dict = {}
        for k, v in direct.items():
            v2 = _apply_constraints(v, by_key[k].get("constraints") or {})
            if v2 is not None:
                direct_validated[k] = str(v2)
        changed_raw = raw.get("changed") if isinstance(raw.get("changed"), list) else []
        changed = {str(k) for k in changed_raw
                   if isinstance(k, str) and k in valid}
        # patch 模式：changed 必须包含所有 direct keys（前端确认时这些要展示）
        if intent == "patch":
            changed |= set(direct_validated.keys())
        # fill_unfilled 模式：未提供 changed → 默认填所有留空字段（下方兜底补）
        if intent == "fill_unfilled" and not changed:
            for it in items:
                cur = base_norm.get(it["key"])
                cur_empty = (cur is None
                             or (isinstance(cur, str) and not cur.strip()))
                if cur_empty:
                    changed.add(it["key"])
        # noop 模式：清空
        if intent == "noop" or intent == "refill":
            direct_validated = {}
            changed = set()
        return {"intent": intent, "direct": direct_validated,
                "changed": sorted(changed)}
    except GenerateCancelled:
        raise
    except Exception:
        logger.warning("extract_patch_values failed; fallback noop", exc_info=True)
        return {"intent": "noop", "direct": {}, "changed": []}


ENTITIES_SYSTEM = (
    "你是文档填写助手。分析用户需求原话，只输出一个 JSON 对象（不要输出任何其他文字）：\n"
    '{"direct": {"字段key": "字段值"}, "entities": {"实体名": "实体值"}}\n'
    "规则：\n"
    "1. direct：只收原话中明确给出了具体值、且能按名称/描述语义对应到给定字段清单的字段；"
    "原话没给值、或对应关系拿不准的字段一律不输出。\n"
    "2. entities：提取原话中的关键实体（如项目名称、采购人、招标代理、预算金额等），"
    "键为实体名、值为实体值；描述性/背景性内容（如「根据XX文件」）归入键 \"__context__\"。\n")


async def extract_entities(tenant_id: str, query: str, placeholders: list[dict],
                           should_cancel=None) -> dict:
    """用户原话实体分析（画布确认阶段调用，与 predict_changed_fields 并行）：
    一次 LLM 调用同时完成直填值抽取（direct，key 限给定填写点清单，高可信闸——
    拿不准的对应关系不输出）与关键实体/检索语境提取（entities，__context__ 固定键）。
    直填值过 _apply_constraints 约束闸；LLM 失败/不可解析/空输入 → 空结果兜底，
    不阻塞主流程（调用方按「无实体」走现状）。GenerateCancelled 穿透。"""
    items = [it for it in placeholders if isinstance(it, dict) and it.get("key")]
    if not query or not query.strip() or not items:
        return {"direct": {}, "entities": {}}
    valid = {it["key"]: it for it in items}
    spec = [{"key": _clean_for_prompt(it["key"], NAME_MAX),
             "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
             "description": _clean_for_prompt(it.get("description") or "", DESC_MAX)}
            for it in items]
    user_msg = ("## 字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                "\n\n## 用户需求原话\n" + _clean_for_prompt(query, QUERY_MAX))
    if _should_cancel(should_cancel):
        raise GenerateCancelled()
    try:
        mdl = _build_chat_mdl(tenant_id)
        ans = await mdl.async_chat(ENTITIES_SYSTEM, [{"role": "user", "content": user_msg}])
        raw = _extract_json(ans)
    except GenerateCancelled:
        raise
    except Exception:  # noqa: BLE001 — 实体分析失败不阻塞填写主流程
        logger.warning("extract_entities failed, tenant=%s", tenant_id, exc_info=True)
        return {"direct": {}, "entities": {}}
    direct: dict = {}
    raw_direct = raw.get("direct")
    if isinstance(raw_direct, dict):
        for k, v in raw_direct.items():
            it = valid.get(k)
            if it is None or v is None:
                continue            # 编造 key / 显式 null 一律丢弃
            val = _apply_constraints(str(v), it.get("constraints") or {})
            if val:
                direct[k] = val
    entities: dict = {}
    raw_ent = raw.get("entities")
    if isinstance(raw_ent, dict):
        for k, v in raw_ent.items():
            if v is None:
                continue
            val = _clean_for_prompt(str(v), PARAM_VAL_MAX)
            if val:
                entities[str(k)[:NAME_MAX]] = val
    return {"direct": direct, "entities": entities}


# ---------- 编排层：产值合成 + 任务 pipeline（状态机乐观转移） ----------

_FILL_MODES = ("llm", "param", "manual")
# 中间态集合：崩溃兜底只把这些强置 failed（终态不可被兜底覆盖）
_TASK_MIDDLE_STATUSES = ("pending", "retrieving", "generating", "rendering")


def _norm_fill_mode(it: dict) -> str:
    """fill_mode 归一：manual 视同 llm（全部交给 AI 检索填写，填不出留空由人工
    二次加工，不再落【待人工】标记）；param 保留直取任务参数；缺省/白名单外
    脏值按 llm（对抗性输入不炸 pipeline）。"""
    mode = it.get("fill_mode")
    if mode == "param":
        return "param"
    return "llm"


def build_values(placeholders: list[dict], generated: dict) -> tuple[dict, dict]:
    """合成渲染产值二元组 (values, cell_status)（纯函数）。

    有产值落值，缺失一律落空串（输出文档留空，人工审核二次加工），
    不再插【待人工】标记、不再有 partial 终态。
    """
    values: dict = {}
    cell_status: dict = {}
    for it in placeholders:
        key = it.get("key")
        if not key:
            continue
        if generated.get(key) not in (None, ""):
            values[key] = generated[key]
            cell_status[key] = "filled"
        else:
            values[key] = ""
            cell_status[key] = "not_found"
    return values, cell_status


def _render_result(task, ver, placeholders: list[dict], values: dict, tpl_file_type: str):
    """渲染生成稿：读模板工作副本 → renderer 产值回填。返回 (blob, result_obj, err)，
    err 非空表示不可恢复失败（blob/result_obj 为 None）。模板 file_type 由调用方
    一次读出传入，本函数不做循环内 IO。"""
    from rag.svr.template_fill import renderer
    blob = settings.STORAGE_IMPL.get(task.template_id, ver.render_file_id)
    if not blob:
        return None, None, "模板工作副本缺失"
    addr_by_key = None
    if tpl_file_type == "xlsx":
        addr_by_key = {it["key"]: it.get("addr") for it in placeholders if it.get("key")}
    out = renderer.render(tpl_file_type, blob, values, addr_by_key)
    result_obj = f"v{ver.version}_result_{task.id}.{tpl_file_type}"
    return out, result_obj, ""


def _fulltext_query(entities: dict, name: str) -> str:
    """二档组合词：项目/名称/标题类实体值优先（最多 _TIER2_ENTITY_MAX 个），
    其余实体值随后，再拼 __context__ 检索语境与填写点名称；空段剔除后整体截断。"""
    prio, rest, ctx_val = [], [], ""
    for k, v in (entities or {}).items():
        v = str(v or "").strip()
        if not v:
            continue
        k = str(k)
        if k == "__context__":
            ctx_val = v
        elif any(w in k for w in _ENTITY_PRIO_WORDS):
            prio.append(v)
        else:
            rest.append(v)
    parts = (prio + rest)[:_TIER2_ENTITY_MAX]
    if ctx_val:
        parts.append(ctx_val)
    parts.append(str(name or "").strip())
    return _clean_for_prompt(" ".join(p for p in parts if p), QUERY_MAX)


async def _retrieve_all(tenant_id: str, placeholders: list[dict], kb_ids: list[str],
                        params: dict, task_id: str = "",
                        skip_keys: set | None = None,
                        should_cancel=None, sem: asyncio.Semaphore | None = None,
                        entities: dict | None = None) -> tuple[dict, dict]:
    """逐槽检索公共段（execute_task 与 dry_run 共用，纯抽取）：单槽失败降级为
    空证据（该字段留空待人工二次加工），不中断整单；kb_ids 为空时全槽直接空证据
    （不进 retrieve_slot，省 N 次无意义异常+warning）。
    llm 槽并发检索（RETRIEVAL_CONCURRENCY 路信号量），大模板（百级填写点）串行
    会被单次 ES 查询秒级~百秒级耗时放大成小时级阻塞；检索上下文只加载一次。
    should_cancel 为可选取消探针（无参同步回调，返回 True 表示外部要求取消）：
    每槽拿到信号量后检查，命中即抛 GenerateCancelled（穿透单槽降级路径向上抛，
    在途槽结果丢弃）；探针自身抛异常视为未取消（否则会被 return_exceptions 收进
    results 误记成槽失败，取消请求被静默吞掉）；传 None 时行为与不取消完全一致。
    sem 为可选注入的共享信号量（跨层共享并发闸）；不传则内部按
    RETRIEVAL_CONCURRENCY 自建，行为不变。
    返回 (chunks_by_key, evidence) 双结构。task_id 仅用于日志上下文（dry_run 无任务 id）。
    entities 为可选核心实体字典（仅画布链路传非空）：启用二档全文降级——一档证据为空
    的 llm 槽用「核心实体+填写点名称」组合词做一轮更宽的全文检索（FULLTEXT_SIMILARITY_
    THRESHOLD 宽阈值 + top_k 翻倍封顶）补证据，命中片段打 source="fulltext"；entities
    为 None/空（B端/REST/dry_run）时二档不启用，行为纯现状。"""
    chunks_by_key: dict = {}
    evidence: dict = {}
    todo: list[tuple[str, str, dict]] = []   # (key, query, placeholder)
    for it in placeholders:
        key = it.get("key")
        if not key:
            logger.warning("placeholder missing key, skipped, task=%s name=%r", task_id, it.get("name"))
            continue
        query = build_retrieval_query(it.get("retrieval_query") or it.get("name") or key, params)
        chunks_by_key[key] = {"chunks": [], "query": query}
        evidence[key] = {"query": query, "chunks": []}
        if key in (skip_keys or ()):
            continue
        if _norm_fill_mode(it) == "llm" and query and kb_ids:
            todo.append((key, query, it))
    ctx = None
    if todo:
        try:
            ctx = load_retrieval_ctx(tenant_id, kb_ids)
        except Exception as e:  # noqa: BLE001 — 上下文加载失败等价全部槽降级空证据
            logger.warning("load_retrieval_ctx failed, task=%s: %s", task_id, e)

    sem = sem if sem is not None else asyncio.Semaphore(RETRIEVAL_CONCURRENCY)

    async def _one(key: str, query: str, it: dict):
        async with sem:
            if _should_cancel(should_cancel):
                raise GenerateCancelled()
            chunks = await retrieve_slot(tenant_id, kb_ids, query,
                                         it.get("top_k") or TOP_K_DEFAULT, ctx=ctx)
        return key, chunks

    results = await asyncio.gather(
        *[_one(key, query, it) for key, query, it in todo], return_exceptions=True)
    # 取消必须穿透单槽降级路径（return_exceptions 会把异常当结果收进来）：
    # 命中即整体向上抛，在途/已完成槽结果丢弃，不落返回值
    for res in results:
        if isinstance(res, GenerateCancelled):
            logger.info("_retrieve_all cancelled: task=%s %d/%d slots done",
                        task_id, sum(1 for r in results if not isinstance(r, GenerateCancelled)),
                        len(todo))
            raise res
    for (key, _query, _it), res in zip(todo, results):
        if isinstance(res, BaseException):
            logger.warning("retrieve_slot failed, task=%s key=%s: %s", task_id, key, res)
            continue
        _key, chunks = res
        chunks_by_key[key]["chunks"] = chunks
        evidence[key]["chunks"] = chunks

    # 二档全文降级：仅画布链路（entities 非空）启用。一档证据为空的 llm 槽用
    # 「核心实体+填写点名称」组合词宽检索补一轮，命中片段打 source=fulltext 并入
    # 证据（该槽一档为空，槽内全部为二档片段）；失败/取消语义与一档一致。
    if entities:
        empty = [(key, it) for key, _q, it in todo if not chunks_by_key[key]["chunks"]]
        if empty:
            async def _one2(key: str, it: dict):
                q2 = _fulltext_query(entities, it.get("name") or key)
                if not q2:
                    return key, []
                async with sem:
                    if _should_cancel(should_cancel):
                        raise GenerateCancelled()
                    chunks = await retrieve_slot(
                        tenant_id, kb_ids, q2,
                        min(int(it.get("top_k") or TOP_K_DEFAULT) * 2, TOP_K_MAX),
                        ctx=ctx, similarity_threshold=FULLTEXT_SIMILARITY_THRESHOLD)
                return key, [dict(c, source="fulltext") for c in chunks]

            results2 = await asyncio.gather(
                *[_one2(key, it) for key, it in empty], return_exceptions=True)
            for res in results2:
                if isinstance(res, GenerateCancelled):
                    logger.info("_retrieve_all tier2 cancelled: task=%s", task_id)
                    raise res
            for (key, _it), res in zip(empty, results2):
                if isinstance(res, BaseException):
                    logger.warning("tier2 retrieve_slot failed, task=%s key=%s: %s",
                                   task_id, key, res)
                    continue
                _key, chunks2 = res
                if chunks2:
                    chunks_by_key[key]["chunks"] = chunks2
                    evidence[key]["chunks"] = chunks2
    return chunks_by_key, evidence


async def retrieve_all_shared(tenant_id: str, placeholders_list: list[list[dict]],
                              kb_ids: list[str], task_id: str = "",
                              should_cancel=None, sem: asyncio.Semaphore | None = None) -> list[dict]:
    """多范本共享检索（画布多范本节点专用）：各范本占位符平铺后按 (top_k, query)
    去重——不同范本的填写点检索词高度重合（同域政务范本尤其如此），同一检索词
    只查一次 ES，结果按 key 分发回各自范本。槽位归集/降级语义与 _retrieve_all
    一致：param/无 key 槽不进检索、单槽失败降级空证据、上下文加载失败全槽空。
    should_cancel 为可选取消探针（无参同步回调，返回 True 表示外部要求取消）：
    每槽拿到信号量后检查，命中即抛 GenerateCancelled（单槽降级不吞取消信号，
    在途槽结果丢弃）；探针自身抛异常视为未取消（warning 不传播）；
    传 None 时行为与不取消完全一致。
    sem 为可选注入的共享信号量（跨层共享并发闸，多范本 × 槽位总并发不超闸值）；
    不传则内部按 RETRIEVAL_CONCURRENCY 自建，行为不变。
    返回与入参等长的 [{key: {"chunks": [...], "query": ...}}, ...]。"""
    slots_list: list[dict] = []
    todo: dict[tuple[int, str], None] = {}   # (top_k, query) → 去重保序
    for placeholders in placeholders_list:
        slots: dict = {}
        for it in placeholders:
            key = it.get("key")
            if not key:
                logger.warning("placeholder missing key, skipped, task=%s name=%r",
                               task_id, it.get("name"))
                continue
            query = build_retrieval_query(it.get("retrieval_query") or it.get("name") or key, {})
            slots[key] = {"chunks": [], "query": query}
            if _norm_fill_mode(it) == "llm" and query and kb_ids:
                todo.setdefault((int(it.get("top_k") or TOP_K_DEFAULT), query))
        slots_list.append(slots)

    if not todo:
        return slots_list
    try:
        ctx = load_retrieval_ctx(tenant_id, kb_ids)
    except Exception as e:  # noqa: BLE001 — 上下文加载失败等价全部槽降级空证据
        logger.warning("load_retrieval_ctx failed, task=%s: %s", task_id, e)
        return slots_list

    sem = sem if sem is not None else asyncio.Semaphore(RETRIEVAL_CONCURRENCY)

    async def _one(qk: tuple[int, str]):
        top_k, query = qk
        async with sem:
            # 取消检查必须在单槽降级 try 之外：GenerateCancelled 是控制流信号，
            # 不是检索失败，不能被降级成空证据
            if _should_cancel(should_cancel):
                raise GenerateCancelled()
            try:
                return qk, await retrieve_slot(tenant_id, kb_ids, query, top_k, ctx=ctx)
            except Exception as e:  # noqa: BLE001 — 单槽失败降级空证据
                logger.warning("retrieve_slot failed, task=%s query=%r: %s", task_id, query, e)
                return qk, []

    try:
        results = await asyncio.gather(*[_one(qk) for qk in todo])
    except GenerateCancelled:
        logger.info("retrieve_all_shared cancelled: task=%s", task_id)
        raise
    chunks_by_qk = dict(results)
    for slots, placeholders in zip(slots_list, placeholders_list):
        for it in placeholders:
            key = it.get("key")
            if not key or _norm_fill_mode(it) != "llm":
                continue
            query = slots[key]["query"]
            slots[key]["chunks"] = chunks_by_qk.get(
                (int(it.get("top_k") or TOP_K_DEFAULT), query), [])
    return slots_list


def _merge_param_values(placeholders: list[dict], generated: dict, missing: set, params: dict):
    """param 模式直取任务参数（不经 LLM，同样过约束兜底），命中则覆盖/摘出 missing。"""
    for it in placeholders:
        if _norm_fill_mode(it) != "param":
            continue
        key = it.get("key")
        if not key:
            continue
        v = _apply_constraints((params or {}).get(key), it.get("constraints") or {})
        if v is not None:
            generated[key] = v
            missing.discard(key)


async def dry_run(tenant_id: str, template_id: str, kb_ids: list[str], params: dict) -> dict:
    """测试填写（B端试跑）：与 execute_task 同款前两步（检索+LLM 生成+param 直取+合成），
    但不建任务、不渲染、不落 MinIO，直接返回产值供用户预览。
    试跑永远针对当前最新版本（无 version 钉住需求）。
    异常向上抛由端点兜底；KB 越权（PermissionError）原样穿透——_retrieve_all 内
    单槽异常会降级吞掉，故 kb_ids 非空时先显式过一遍 _load_and_check_kbs。"""
    from api.db.services.template_fill_service import TplTemplateVersionService
    ver = TplTemplateVersionService.latest(template_id)
    if ver is None:
        raise ValueError("模板版本不存在")
    placeholders = ver.placeholders or []
    if not placeholders:
        raise ValueError("该范本未配置填写点")
    params = params or {}
    if kb_ids:
        _load_and_check_kbs(tenant_id, kb_ids)

    chunks_by_key, evidence = await _retrieve_all(tenant_id, placeholders, kb_ids or [], params)
    llm_placeholders = [it for it in placeholders
                        if _norm_fill_mode(it) == "llm" and it.get("key")]
    llm_chunks = {it["key"]: chunks_by_key.get(it["key"], {"chunks": [], "query": ""})
                  for it in llm_placeholders}
    generated, missing = await generate_values(tenant_id, llm_placeholders, llm_chunks, params)
    _merge_param_values(placeholders, generated, missing, params)
    _merge_default_values(placeholders, generated, missing)
    values, cell_status = build_values(placeholders, generated)
    # partial 字段为前端契约保留，恒 False——缺值留空待人工二次加工，不再有 partial 终态
    return {"values": values, "cells": cell_status, "evidence": evidence, "partial": False}


async def _execute_task_async(task_id: str):
    """pipeline 本体：每步经 update_status 乐观转移，转移失败即放弃
    （说明有并发执行器接管或终态已变）。"""
    from api.db.services import template_fill_service as tpl_svc
    svc = tpl_svc.TplFillTaskService

    # ① 接管任务：pending→retrieving（CAS 失败 = 已被接管/已终态，直接放弃）
    if not svc.update_status(task_id, "pending", "retrieving"):
        return
    task = svc.get_or_none(id=task_id)
    if task is None:
        return
    # 版本钉住：优先按任务创建时锁定的版本行 id（TplFillTask.template_version_id =
    # 版本行主键，见创建任务写入 ver.id）解析——历史任务必须按当时版本复现，
    # 防 pending 期间模板 published 升版后静默改用新版渲染；列为空（防御旧数据）
    # 才退 latest()，列非空但版本行解析不到 → failed，不做静默换版。
    if task.template_version_id:
        ver = tpl_svc.TplTemplateVersionService.get_by_id_checked(
            task.template_id, task.template_version_id)
    else:
        logger.warning("fill task has no pinned template_version_id, fallback to latest, task=%s", task_id)
        ver = tpl_svc.TplTemplateVersionService.latest(task.template_id)
    if ver is None:
        svc.update_status(task_id, "retrieving", "failed", error="模板版本不存在")
        return
    placeholders = ver.placeholders or []
    kb_ids = task.kb_ids or []
    params = task.params or {}
    clean_params, opts = split_canvas_params(params)
    # 画布委托门控：只有 params 携带保留键（画布节点必写，含空值）才启用
    # D−C 收窄/直填覆盖/证据注入/取消探针；B端普通任务保持全量进 LLM 的既有行为。
    # 该等价关系的成立依赖 create_fill_task 从 REST 入参剥离保留键（否则调用方
    # 可伪造画布语义，让 LLM 白名单/直填覆盖被误用）。
    is_canvas = any(k in params for k in CANVAS_RESERVED_KEYS)
    direct_values: dict = opts["direct_values"] if is_canvas else {}
    changed_keys: set = (opts["changed_keys"] if is_canvas
                         else {it.get("key") for it in placeholders if it.get("key")})
    skip_keys = opts["retrieve_skip_keys"] if is_canvas else None
    user_file_text = opts["user_file_text"] if is_canvas else ""
    cancel_probe = _make_cancel_probe(task_id) if is_canvas else None

    # ② 逐槽检索（公共段，dry_run 同款）：单槽失败降级为空证据（字段走 missing/待人工），
    # 不中断整单；kb_ids 为空时全槽直接空证据（不进 retrieve_slot，省 N 次无意义异常+warning）
    try:
        chunks_by_key, evidence = await _retrieve_all(
            task.tenant_id, placeholders, kb_ids, clean_params, task_id=task_id,
            skip_keys=skip_keys, should_cancel=cancel_probe)
    except GenerateCancelled:
        # cancel_running 返回 False 说明行已终态（如崩溃兜底已强置 failed）：
        # 不再写 cancelled 快照误导前端，按 failed 收口（error 沿用原文案）
        if svc.cancel_running(task_id):
            _write_snapshot(task_id, force=True, status="cancelled",
                            error="画布已停止，任务被取消")
        else:
            _write_snapshot(task_id, force=True, status="failed",
                            error="画布已停止，任务被取消")
        return

    # ③ LLM 批量产值（仅 llm 模式字段；整体失败 → 任务失败）
    if not svc.update_status(task_id, "retrieving", "generating"):
        return
    _write_snapshot(task_id, status="generating", done=0, total=0)
    # 画布委托对齐节点 _fill_one：白名单收窄（changed_keys=确认后要 LLM 填的 key
    # 集合，未勾选不进 LLM——有默认值走默认、无默认值留空）+ 直填键排除。
    # B 端 changed_keys=全部 key（split_canvas_params 兜底），条件恒真行为不变
    llm_placeholders = [it for it in placeholders
                        if _norm_fill_mode(it) == "llm" and it.get("key")
                        and it["key"] not in direct_values
                        and it["key"] in changed_keys]
    llm_chunks = {it["key"]: chunks_by_key.get(it["key"], {"chunks": [], "query": ""})
                  for it in llm_placeholders}
    # 用户上传文件作为填写证据：预置片段插到每槽证据首位（优先于 KB 片段）
    if user_file_text:
        for it in llm_placeholders:
            llm_chunks.setdefault(it["key"], {"chunks": [], "query": ""})
            llm_chunks[it["key"]]["chunks"].insert(0, {
                "content": f"[用户上传文件] {user_file_text}",
                "doc_id": "", "doc_name": "用户上传文件", "similarity": 1.0})
    acc_values: dict = {}

    def _on_progress(done: int, total: int, new_values: dict | None = None):
        if new_values:
            acc_values.update(new_values)
        _write_snapshot(task_id, status="generating", done=done, total=total,
                        values=dict(acc_values))

    try:
        generated, missing = await generate_values(
            task.tenant_id, llm_placeholders, llm_chunks, clean_params,
            on_progress=_on_progress, should_cancel=cancel_probe)
    except GenerateCancelled:
        # 同上：cancel_running False（行已被兜底置终态）→ 按 failed 快照收口
        if svc.cancel_running(task_id):
            _write_snapshot(task_id, force=True, status="cancelled",
                            values=dict(acc_values), error="画布已停止，任务被取消")
        else:
            _write_snapshot(task_id, force=True, status="failed",
                            values=dict(acc_values), error="画布已停止，任务被取消")
        return
    except Exception as e:
        logger.exception("generate_values failed, task=%s", task_id)
        svc.update_status(task_id, "generating", "failed", error=f"LLM 生成失败: {e}")
        _write_snapshot(task_id, force=True, status="failed", values=dict(acc_values),
                        error=f"LLM 生成失败: {e}")
        return

    # ④ param 模式直取任务参数（不经 LLM，同样过约束兜底），命中则覆盖/摘出 missing
    # 白名单外字段未进 LLM，显式纳入 missing：有默认值由 _merge_default_values 直取，
    # 无默认值留空交人工（与"缺值留空交人工二次加工"口径一致）
    for it in placeholders:
        k = it.get("key")
        if (k and k not in generated and k not in direct_values
                and _norm_fill_mode(it) == "llm"
                and k not in changed_keys):
            missing.add(k)
    _merge_param_values(placeholders, generated, missing, clean_params)
    # 用户直填值直取（空串=明确清空）；先摘出 missing 防默认值回填覆盖直填
    for k, v in direct_values.items():
        generated[k] = v
        missing.discard(k)
    # 增量填写场景：baseline 兜底（仅对 missing 字段；优先级 default_value 之前）。
    # baseline 是上次实际写入成稿的真实值，不是 B 端 default_value 提示——
    # 用户没动过的字段必须原样保留，否则「增量」会丢上次成果回退到默认值
    baseline_values = opts.get("baseline_values") if is_canvas else {}
    if baseline_values:
        for k, v in baseline_values.items():
            if k in missing and str(v).strip():
                generated[k] = v
                missing.discard(k)
    _merge_default_values(placeholders, generated, missing)

    values, cell_status = build_values(placeholders, generated)

    # ⑤ 渲染 + 落稿（storage put 失败会 raise，与渲染异常同路兜底）
    if not svc.update_status(task_id, "generating", "rendering"):
        return
    tpl = tpl_svc.TplTemplateService.get_or_none(id=task.template_id)
    tpl_file_type = (tpl.file_type if tpl and tpl.file_type else "docx")
    try:
        render_blob, result_obj, err = _render_result(task, ver, placeholders, values, tpl_file_type)
        if err:
            svc.update_status(task_id, "rendering", "failed", error=err)
            _write_snapshot(task_id, force=True, status="failed", values=values, error=err)
            return
        tpl_svc._storage_put(task.template_id, result_obj, render_blob)
    except Exception as e:
        logger.exception("render/store failed, task=%s", task_id)
        svc.update_status(task_id, "rendering", "failed", error=f"渲染落稿失败: {e}")
        _write_snapshot(task_id, force=True, status="failed", values=values,
                        error=f"渲染落稿失败: {e}")
        return

    # ⑥ 终态：渲染成功即 done（缺值留空交人工二次加工，不再有 partial）。
    # CAS 失败（返回 False）说明并发执行器接管或终态已变：稿件对象已入 MinIO
    # 但 DB 未落终态（孤儿对象），此处只告警不重试，交人工核对。
    if not svc.update_status(task_id, "rendering", "done",
                             values={"cells": cell_status, "render": values},
                             evidence=evidence, result_file_id=result_obj):
        logger.warning("fill task final status CAS failed, task=%s result_obj=%s "
                       "(result object in storage without terminal status)", task_id, result_obj)
        return
    _write_snapshot(task_id, force=True, status="done", values=values)


def execute_task(task_id: str):
    """线程入口（同步包装 asyncio.run）。最外层兜底：pipeline 崩溃时把仍处中间态
    的行强置 failed（终态不动），防任务永久卡在 retrieving/generating/rendering；
    兜底自身失败只记日志不外抛。"""
    try:
        _run_async(_execute_task_async(task_id))
    except Exception as e:
        logger.exception("fill task crashed, task_id=%s", task_id)
        try:
            from api.db.db_models import DB, TplFillTask
            # 兜底写入是 daemon 线程内的一次性 update，必须自备连接上下文，
            # 否则裸取的池连接随线程结束永久占用不归还。
            with DB.connection_context():
                TplFillTask.update(status="failed", error=f"引擎内部异常: {str(e)[:200]}").where(
                    TplFillTask.id == task_id,
                    TplFillTask.status.in_(_TASK_MIDDLE_STATUSES)).execute()
        except Exception:
            logger.exception("fill task force-fail failed, task_id=%s", task_id)
