#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
import asyncio
from functools import partial
import json
import os
import re
from abc import ABC
from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.constants import LLMType
from api.db.services.doc_metadata_service import DocMetadataService
from common.metadata_utils import apply_meta_data_filter
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from api.db.services.memory_service import MemoryService
from api.db.services.document_analysis_service import DocumentAnalysisService
from api.db.joint_services import memory_message_service
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name, get_tenant_default_model_by_type
from common import settings
from common.connection_utils import timeout
from rag.app.tag import label_question
from rag.prompts.generator import cross_languages, kb_prompt, memory_prompt
from rag.nlp import rag_tokenizer


class RetrievalParam(ToolParamBase):
    """
    Define the Retrieval component parameters.
    """

    def __init__(self):
        self.meta:ToolMeta = {
            "name": "search_my_dateset",
            "description": "This tool can be utilized for relevant content searching in the datasets.",
            "parameters": {
                "query": {
                    "type": "string",
                    "description": "The keywords to search the dataset. The keywords should be the most important words/terms(includes synonyms) from the original request.",
                    "default": "",
                    "required": True
                }
            }
        }
        super().__init__()
        self.function_name = "search_my_dateset"
        self.description = "This tool can be utilized for relevant content searching in the datasets."
        self.similarity_threshold = 0.2
        self.keywords_similarity_weight = 0.5
        self.top_n = 8
        self.top_k = 1024
        self.dataset_ids = []
        self.kb_ids = []  # Deprecated: keep for backward compatibility
        self.memory_ids = []
        self.kb_vars = []
        self.rerank_id = ""
        self.empty_response = ""
        self.use_kg = False
        self.cross_languages = []
        self.toc_enhance = False
        self.meta_data_filter={}

    def check(self):
        self.check_decimal_float(self.similarity_threshold, "[Retrieval] Similarity threshold")
        self.check_decimal_float(self.keywords_similarity_weight, "[Retrieval] Keyword similarity weight")
        self.check_positive_number(self.top_n, "[Retrieval] Top N")

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {
                "name": "Query",
                "type": "line"
            }
        }

class Retrieval(ToolBase, ABC):
    component_name = "Retrieval"

    @property
    def _dataset_ids(self):
        """Get dataset IDs with backward compatibility for kb_ids."""
        return self._param.dataset_ids or getattr(self._param, "kb_ids", None) or []

    @property
    def _analysis_ids(self):
        """Get analysis result IDs (prefixed with 'analysis:')."""
        analysis_ids = []
        for id in self._dataset_ids:
            if isinstance(id, str) and id.startswith("analysis:"):
                # 提取分析结果 ID，格式可能是 analysis:result_id 或 analysis:doc_id
                analysis_id = id.replace("analysis:", "", 1)
                if analysis_id:
                    analysis_ids.append(analysis_id)
        return analysis_ids

    @property
    def _has_analysis_source(self):
        """Check if any dataset_id is an analysis result source."""
        return any(isinstance(id, str) and id.startswith("analysis:") for id in self._dataset_ids)

    async def _retrieve_analysis_results(self, query_text: str):
        """从分析结果中检索相关内容

        Args:
            query_text: 查询文本

        Returns:
            格式化后的内容
        """
        from api.db.db_models import DB, Document

        tenant_id = self._canvas.get_tenant_id()

        # 获取分析结果 chunks
        chunks = DocumentAnalysisService.get_analysis_results_as_kb_chunks(
            tenant_id=tenant_id,
            document_ids=self._analysis_ids if self._analysis_ids else None
        )

        if not chunks:
            self.set_output("formalized_content", self._param.empty_response)
            self.set_output("json", [])
            return

        # 简单的关键词匹配（由于分析结果没有向量，使用关键词匹配）
        vars = self.get_input_elements_from_text(query_text)
        vars = {k: o["value"] for k, o in vars.items()}
        query = self.string_format(query_text, vars)

        # 提取查询关键词
        query_lower = query.lower()

        # 计算每个 chunk 的相关性分数
        scored_chunks = []
        for chunk in chunks:
            score = 0
            content = chunk.get("content_with_weight", "")

            # 检查查询词是否出现在内容中
            if query_lower in content.lower():
                score += 10

            # 检查查询词的各个部分
            query_words = [w for w in query_lower.split() if len(w) > 1]
            for word in query_words:
                if word in content.lower():
                    score += 2

            if score > 0:
                scored_chunks.append((score, chunk))

        # 按分数排序，取 top_n
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = scored_chunks[:self._param.top_n] if hasattr(self._param, 'top_n') else scored_chunks[:10]

        if not top_chunks:
            self.set_output("formalized_content", self._param.empty_response)
            self.set_output("json", [])
            return

        # 提取 chunks
        retrieved_chunks = [chunk for score, chunk in top_chunks]

        # 移除向量字段（如果存在）
        for chunk in retrieved_chunks:
            chunk.pop("vector", None)
            chunk.pop("content_ltks", None)
            chunk.pop("content_sm_ltks", None)

        # 添加引用
        doc_aggs = []
        for chunk in retrieved_chunks:
            doc_aggs.append({
                "doc_id": chunk.get("doc_id", ""),
                "doc_name": chunk.get("doc_name", ""),
                "template_name": chunk.get("template_name", ""),
            })

        self._canvas.add_reference(retrieved_chunks, doc_aggs)

        # 格式化输出
        form_cnt = "\n\n".join([
            f"【{chunk.get('doc_name', '文档')} - {chunk.get('template_name', '分析')}】\n{chunk.get('content_with_weight', '')}"
            for chunk in retrieved_chunks
        ])

        self.set_output("formalized_content", form_cnt)
        self.set_output("json", retrieved_chunks)

        return form_cnt

    async def _retrieve_kb(self, query_text: str):
        kb_ids: list[str] = []
        for id in self._dataset_ids:
            if id.find("@") < 0:
                kb_ids.append(id)
                continue
            kb_nm = self._canvas.get_variable_value(id)
            # if kb_nm is a list
            kb_nm_list = kb_nm if isinstance(kb_nm, list) else [kb_nm]
            for nm_or_id in kb_nm_list:
                e, kb = KnowledgebaseService.get_by_name(nm_or_id,
                                                         self._canvas._tenant_id)
                if not e:
                    e, kb = KnowledgebaseService.get_by_id(nm_or_id)
                    if not e:
                        raise Exception(f"Dataset({nm_or_id}) does not exist.")
                kb_ids.append(kb.id)

        filtered_kb_ids: list[str] = list(set([kb_id for kb_id in kb_ids if kb_id]))

        kbs = KnowledgebaseService.get_by_ids(filtered_kb_ids)
        if not kbs:
            raise Exception("No dataset is selected.")

        embd_nms = list(set([kb.embd_id for kb in kbs]))
        assert len(embd_nms) == 1, "Knowledge bases use different embedding models."

        embd_mdl = None
        if embd_nms:
            tenant_id = self._canvas.get_tenant_id()
            embd_model_config = get_model_config_by_type_and_name(tenant_id, LLMType.EMBEDDING, embd_nms[0])
            embd_mdl = LLMBundle(tenant_id, embd_model_config)

        rerank_mdl = None
        if self._param.rerank_id:
            rerank_model_config = get_model_config_by_type_and_name(kbs[0].tenant_id, LLMType.RERANK, self._param.rerank_id)
            rerank_mdl = LLMBundle(kbs[0].tenant_id, rerank_model_config)

        vars = self.get_input_elements_from_text(query_text)
        vars = {k: o["value"] for k, o in vars.items()}
        query = self.string_format(query_text, vars)

        doc_ids = []
        if self._param.meta_data_filter != {}:
            metas = DocMetadataService.get_flatted_meta_by_kbs(kb_ids)

            def _resolve_manual_filter(flt: dict) -> dict:
                pat = re.compile(self.variable_ref_patt)
                s = flt.get("value", "")
                out_parts = []
                last = 0

                for m in pat.finditer(s):
                    out_parts.append(s[last:m.start()])
                    key = m.group(1)
                    v = self._canvas.get_variable_value(key)
                    if v is None:
                        rep = ""
                    elif isinstance(v, partial):
                        buf = []
                        for chunk in v():
                            buf.append(chunk)
                        rep = "".join(buf)
                    elif isinstance(v, str):
                        rep = v
                    else:
                        rep = json.dumps(v, ensure_ascii=False)

                    out_parts.append(rep)
                    last = m.end()

                out_parts.append(s[last:])
                flt["value"] = "".join(out_parts)
                return flt

            chat_mdl = None
            if self._param.meta_data_filter.get("method") in ["auto", "semi_auto"]:
                tenant_id = self._canvas.get_tenant_id()
                chat_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
                chat_mdl = LLMBundle(tenant_id, chat_model_config)

            doc_ids = await apply_meta_data_filter(
                self._param.meta_data_filter,
                metas,
                query,
                chat_mdl,
                doc_ids,
                _resolve_manual_filter if self._param.meta_data_filter.get("method") == "manual" else None,
            )

        if self._param.cross_languages:
            query = await cross_languages(kbs[0].tenant_id, None, query, self._param.cross_languages)

        if kbs:
            query = re.sub(r"^user[:：\s]*", "", query, flags=re.IGNORECASE)
            kbinfos = await settings.retriever.retrieval(
                query,
                embd_mdl,
                [kb.tenant_id for kb in kbs],
                filtered_kb_ids,
                1,
                self._param.top_n,
                self._param.similarity_threshold,
                1 - self._param.keywords_similarity_weight,
                doc_ids=doc_ids,
                aggs=True,
                rerank_mdl=rerank_mdl,
                rank_feature=label_question(query, kbs),
            )
            if self.check_if_canceled("Retrieval processing"):
                return

            if self._param.toc_enhance:
                tenant_id = self._canvas._tenant_id
                chat_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
                chat_mdl = LLMBundle(tenant_id, chat_model_config)
                cks = await settings.retriever.retrieval_by_toc(query, kbinfos["chunks"], [kb.tenant_id for kb in kbs],
                                                          chat_mdl, self._param.top_n)
                if self.check_if_canceled("Retrieval processing"):
                    return
                if cks:
                    kbinfos["chunks"] = cks
            kbinfos["chunks"] = settings.retriever.retrieval_by_children(kbinfos["chunks"],
                                                                         [kb.tenant_id for kb in kbs])
            if self._param.use_kg:
                tenant_id = self._canvas.get_tenant_id()
                chat_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
                ck = await settings.kg_retriever.retrieval(query,
                                                     [kb.tenant_id for kb in kbs],
                                                     kb_ids,
                                                     embd_mdl,
                                                     LLMBundle(tenant_id, chat_model_config))
                if self.check_if_canceled("Retrieval processing"):
                    return
                if ck["content_with_weight"]:
                    kbinfos["chunks"].insert(0, ck)
        else:
            kbinfos = {"chunks": [], "doc_aggs": []}

        if self._param.use_kg and kbs:
            chat_model_config = get_tenant_default_model_by_type(kbs[0].tenant_id, LLMType.CHAT)
            ck = await settings.kg_retriever.retrieval(query, [kb.tenant_id for kb in kbs], filtered_kb_ids, embd_mdl,
                                                 LLMBundle(kbs[0].tenant_id, chat_model_config))
            if self.check_if_canceled("Retrieval processing"):
                return
            if ck["content_with_weight"]:
                ck["content"] = ck["content_with_weight"]
                del ck["content_with_weight"]
                kbinfos["chunks"].insert(0, ck)

        for ck in kbinfos["chunks"]:
            if "vector" in ck:
                del ck["vector"]
            if "content_ltks" in ck:
                del ck["content_ltks"]

        if not kbinfos["chunks"]:
            self.set_output("formalized_content", self._param.empty_response)
            return

        # Format the chunks for JSON output (similar to how other tools do it)
        json_output = kbinfos["chunks"].copy()

        self._canvas.add_reference(kbinfos["chunks"], kbinfos["doc_aggs"])
        form_cnt = "\n".join(kb_prompt(kbinfos, 200000, True))

        # Set both formalized content and JSON output
        self.set_output("formalized_content", form_cnt)
        self.set_output("json", json_output)

        return form_cnt

    async def _retrieve_memory(self, query_text: str):
        memory_ids: list[str] = [memory_id for memory_id in self._param.memory_ids]
        user_id: str = self._param.user_id if hasattr(self._param, "user_id") else None
        memory_list = MemoryService.get_by_ids(memory_ids)
        if not memory_list:
            raise Exception("No memory is selected.")

        embd_names = list({memory.embd_id for memory in memory_list})
        assert len(embd_names) == 1, "Memory use different embedding models."

        vars = self.get_input_elements_from_text(query_text)
        vars = {k: o["value"] for k, o in vars.items()}
        query = self.string_format(query_text, vars)
        # query message
        filter_dict: dict = {"memory_id": memory_ids}
        if user_id:
            import re
            # is variable
            if re.match(r"^{.*}$", user_id):
                user_id = self._canvas.get_variable_value(user_id)
            filter_dict["user_id"] = user_id
        message_list = memory_message_service.query_message(filter_dict, {
            "query": query,
            "similarity_threshold": self._param.similarity_threshold,
            "keywords_similarity_weight": self._param.keywords_similarity_weight,
            "top_n": self._param.top_n
        })
        if not message_list:
            self.set_output("formalized_content", self._param.empty_response)
            return ""
        formated_content = "\n".join(memory_prompt(message_list, 200000))
        # set formalized_content output
        self.set_output("formalized_content", formated_content)

        return formated_content

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("Retrieval processing"):
            return
        if not kwargs.get("query"):
            self.set_output("formalized_content", self._param.empty_response)
            return

        # 检查是否需要从分析结果中检索
        if self._has_analysis_source:
            return await self._retrieve_analysis_results(kwargs["query"])

        if hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "dataset":
            return await self._retrieve_kb(kwargs["query"])
        elif hasattr(self._param, "retrieval_from") and self._param.retrieval_from == "memory":
            return await self._retrieve_memory(kwargs["query"])
        elif self._dataset_ids:
            return await self._retrieve_kb(kwargs["query"])
        elif hasattr(self._param, "memory_ids") and self._param.memory_ids:
            return await self._retrieve_memory(kwargs["query"])
        else:
            self.set_output("formalized_content", self._param.empty_response)
            return

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 12)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    def thoughts(self) -> str:
        return """
Keywords: {}
Looking for the most relevant articles.
        """.format(self.get_input().get("query", "-_-!"))
