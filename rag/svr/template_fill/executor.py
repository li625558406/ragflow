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
# 逐槽检索并发路数（_retrieve_all 信号量）：大模板百级填写点并发 6 路，
# 单槽 ES 查询秒级耗时下把小时级串行压到分钟级，同时不把 ES/embd 打爆
RETRIEVAL_CONCURRENCY = 6


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


def load_retrieval_ctx(tenant_id: str, kb_ids: list[str]):
    """检索上下文一次性加载（知识库校验 + embedding 模型），供同批多槽复用，
    免去每槽重复 DB 查询与模型构建。"""
    kbs = _load_and_check_kbs(tenant_id, kb_ids)
    embd_mdl = _build_embd_mdl(tenant_id, kbs)
    return kbs, embd_mdl


async def retrieve_slot(tenant_id: str, kb_ids: list[str], query: str, top_k: int = TOP_K_DEFAULT,
                        ctx=None) -> list[dict]:
    """单槽位检索。query 已由上游清洗；异常向上抛由编排层兜底为该槽位空结果。
    ctx 为 load_retrieval_ctx 产物（多槽并发时复用，省每槽重复加载）。"""
    kbs, embd_mdl = ctx if ctx else load_retrieval_ctx(tenant_id, kb_ids)
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
# 产值批次并发上限：批次间字段独立无依赖，并发安全；大批量模板（百级填写点）
# 串行 20 批 × 单批 20~40s 会放大成 10 分钟级阻塞。共享 sem 时以外部闸为准。
GENERATE_CONCURRENCY = 3
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
                          batch_size: int = BATCH_SIZE,
                          sem: asyncio.Semaphore | None = None,
                          on_progress=None) -> tuple[dict, set]:
    """LLM 批量产值：一次调用产 ≤batch_size 个字段值（超出分批），批次间并发
    （GENERATE_CONCURRENCY 路；字段独立无依赖，并发安全）。
    batch_size 为 0/None 等假值时兜底为 BATCH_SIZE（step 与 slice 必须同值，
    否则 0 产生空批、None 导致 slice 取全量重复发送）。
    sem 为跨层共享并发闸（画布多范本并行时传入全局信号量，使多范本 × 批次
    总并发不超闸值）；不传则内部自建。
    on_progress 为可选回调 (done, total)：每批 LLM 返回后即回调一次（批次并发下
    完成顺序不定），进度上报用；回调异常被吞掉不影响产值。
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
                             CONSTRAINTS_PROMPT_MAX)})
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
            ans = await mdl.async_chat(GENERATE_SYSTEM, [{"role": "user", "content": user_msg}])
        return batch, _extract_json(ans)

    total = sum(len(b) for b in batches)
    done_slots = 0
    results = []
    for fut in asyncio.as_completed([_one(b) for b in batches]):
        results.append(await fut)
        if on_progress:
            done_slots += len(results[-1][0])
            try:
                on_progress(done_slots, total)
            except Exception:
                logger.exception("generate_values on_progress callback failed")
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


async def _retrieve_all(tenant_id: str, placeholders: list[dict], kb_ids: list[str],
                        params: dict, task_id: str = "") -> tuple[dict, dict]:
    """逐槽检索公共段（execute_task 与 dry_run 共用，纯抽取）：单槽失败降级为
    空证据（该字段留空待人工二次加工），不中断整单；kb_ids 为空时全槽直接空证据
    （不进 retrieve_slot，省 N 次无意义异常+warning）。
    llm 槽并发检索（RETRIEVAL_CONCURRENCY 路信号量），大模板（百级填写点）串行
    会被单次 ES 查询秒级~百秒级耗时放大成小时级阻塞；检索上下文只加载一次。
    返回 (chunks_by_key, evidence) 双结构。task_id 仅用于日志上下文（dry_run 无任务 id）。"""
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
        if _norm_fill_mode(it) == "llm" and query and kb_ids:
            todo.append((key, query, it))
    ctx = None
    if todo:
        try:
            ctx = load_retrieval_ctx(tenant_id, kb_ids)
        except Exception as e:  # noqa: BLE001 — 上下文加载失败等价全部槽降级空证据
            logger.warning("load_retrieval_ctx failed, task=%s: %s", task_id, e)

    sem = asyncio.Semaphore(RETRIEVAL_CONCURRENCY)

    async def _one(key: str, query: str, it: dict):
        async with sem:
            chunks = await retrieve_slot(tenant_id, kb_ids, query,
                                         it.get("top_k") or TOP_K_DEFAULT, ctx=ctx)
        return key, chunks

    results = await asyncio.gather(
        *[_one(key, query, it) for key, query, it in todo], return_exceptions=True)
    for (key, _query, _it), res in zip(todo, results):
        if isinstance(res, BaseException):
            logger.warning("retrieve_slot failed, task=%s key=%s: %s", task_id, key, res)
            continue
        _key, chunks = res
        chunks_by_key[key]["chunks"] = chunks
        evidence[key]["chunks"] = chunks
    return chunks_by_key, evidence


async def retrieve_all_shared(tenant_id: str, placeholders_list: list[list[dict]],
                              kb_ids: list[str], task_id: str = "") -> list[dict]:
    """多范本共享检索（画布多范本节点专用）：各范本占位符平铺后按 (top_k, query)
    去重——不同范本的填写点检索词高度重合（同域政务范本尤其如此），同一检索词
    只查一次 ES，结果按 key 分发回各自范本。槽位归集/降级语义与 _retrieve_all
    一致：param/无 key 槽不进检索、单槽失败降级空证据、上下文加载失败全槽空。
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

    sem = asyncio.Semaphore(RETRIEVAL_CONCURRENCY)

    async def _one(qk: tuple[int, str]):
        top_k, query = qk
        async with sem:
            try:
                return qk, await retrieve_slot(tenant_id, kb_ids, query, top_k, ctx=ctx)
            except Exception as e:  # noqa: BLE001 — 单槽失败降级空证据
                logger.warning("retrieve_slot failed, task=%s query=%r: %s", task_id, query, e)
                return qk, []

    results = await asyncio.gather(*[_one(qk) for qk in todo])
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

    # ② 逐槽检索（公共段，dry_run 同款）：单槽失败降级为空证据（字段走 missing/待人工），
    # 不中断整单；kb_ids 为空时全槽直接空证据（不进 retrieve_slot，省 N 次无意义异常+warning）
    chunks_by_key, evidence = await _retrieve_all(
        task.tenant_id, placeholders, kb_ids, params, task_id=task_id)

    # ③ LLM 批量产值（仅 llm 模式字段；整体失败 → 任务失败）
    if not svc.update_status(task_id, "retrieving", "generating"):
        return
    llm_placeholders = [it for it in placeholders
                        if _norm_fill_mode(it) == "llm" and it.get("key")]
    llm_chunks = {it["key"]: chunks_by_key.get(it["key"], {"chunks": [], "query": ""})
                  for it in llm_placeholders}
    try:
        generated, missing = await generate_values(task.tenant_id, llm_placeholders, llm_chunks, params)
    except Exception as e:
        logger.exception("generate_values failed, task=%s", task_id)
        svc.update_status(task_id, "generating", "failed", error=f"LLM 生成失败: {e}")
        return

    # ④ param 模式直取任务参数（不经 LLM，同样过约束兜底），命中则覆盖/摘出 missing
    _merge_param_values(placeholders, generated, missing, params)

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
            return
        tpl_svc._storage_put(task.template_id, result_obj, render_blob)
    except Exception as e:
        logger.exception("render/store failed, task=%s", task_id)
        svc.update_status(task_id, "rendering", "failed", error=f"渲染落稿失败: {e}")
        return

    # ⑥ 终态：渲染成功即 done（缺值留空交人工二次加工，不再有 partial）。
    # CAS 失败（返回 False）说明并发执行器接管或终态已变：稿件对象已入 MinIO
    # 但 DB 未落终态（孤儿对象），此处只告警不重试，交人工核对。
    if not svc.update_status(task_id, "rendering", "done",
                             values={"cells": cell_status, "render": values},
                             evidence=evidence, result_file_id=result_obj):
        logger.warning("fill task final status CAS failed, task=%s result_obj=%s "
                       "(result object in storage without terminal status)", task_id, result_obj)


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
