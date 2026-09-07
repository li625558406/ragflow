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


# ---------- 编排层：待人工合成 + 任务 pipeline（状态机乐观转移） ----------

_FILL_MODES = ("llm", "param", "manual")
# 中间态集合：崩溃兜底只把这些强置 failed（终态不可被兜底覆盖）
_TASK_MIDDLE_STATUSES = ("pending", "retrieving", "generating", "rendering")


def _norm_fill_mode(it: dict) -> str:
    """fill_mode 归一：缺省/白名单外脏值一律按 llm（对抗性输入不炸 pipeline）。"""
    mode = it.get("fill_mode")
    return mode if mode in _FILL_MODES else "llm"


def build_values(placeholders: list[dict], generated: dict, missing: set) -> tuple[dict, dict, bool]:
    """合成渲染产值三元组 (values, cell_status, is_partial)（纯函数）。

    每槽优先级：manual（恒待人工）> generated 有值 > required+missing（待人工）
    > 其余缺失落空串。is_partial=True 表示稿件含人工标记，任务终态落 partial 而非 done。
    """
    from rag.svr.template_fill.renderer import manual_mark
    values: dict = {}
    cell_status: dict = {}
    is_partial = False
    for it in placeholders:
        key = it.get("key")
        if not key:
            continue
        name = it.get("name") or key
        mode = _norm_fill_mode(it)
        if mode == "manual":
            values[key] = manual_mark(name)
            cell_status[key] = "manual"
            is_partial = True
        elif generated.get(key) not in (None, ""):
            values[key] = generated[key]
            cell_status[key] = "filled"
        elif key in missing and it.get("required"):
            values[key] = manual_mark(name)
            cell_status[key] = "not_found"
            is_partial = True
        else:
            values[key] = ""
            cell_status[key] = "not_found"
    return values, cell_status, is_partial


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

    # ② 逐槽检索：单槽失败降级为空证据（字段走 missing/待人工），不中断整单；
    # kb_ids 为空时全槽直接空证据（不进 retrieve_slot，省 N 次无意义异常+warning）
    chunks_by_key: dict = {}
    evidence: dict = {}
    for it in placeholders:
        key = it.get("key")
        if not key:
            logger.warning("placeholder missing key, skipped, task=%s name=%r", task_id, it.get("name"))
            continue
        query = build_retrieval_query(it.get("retrieval_query") or it.get("name") or key, params)
        chunks: list = []
        if _norm_fill_mode(it) == "llm" and query and kb_ids:
            try:
                chunks = await retrieve_slot(task.tenant_id, kb_ids, query,
                                             it.get("top_k") or TOP_K_DEFAULT)
            except Exception as e:  # noqa: BLE001 — 各存储/检索实现异常类型不一，降级空证据
                logger.warning("retrieve_slot failed, task=%s key=%s: %s", task_id, key, e)
        chunks_by_key[key] = {"chunks": chunks, "query": query}
        evidence[key] = {"query": query, "chunks": chunks}

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

    values, cell_status, is_partial = build_values(placeholders, generated, missing)

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

    # ⑥ 终态：稿件含人工标记 → partial，否则 done。CAS 失败（返回 False）说明
    # 并发执行器接管或终态已变：稿件对象已入 MinIO 但 DB 未落终态（孤儿对象），
    # 此处只告警不重试，交人工核对。
    if not svc.update_status(task_id, "rendering", "partial" if is_partial else "done",
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
