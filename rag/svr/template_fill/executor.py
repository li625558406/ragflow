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
import logging

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
