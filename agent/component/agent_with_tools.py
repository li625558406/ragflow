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
import json
import logging
import os
import re
from copy import deepcopy
from functools import partial
from timeit import default_timer as timer
from typing import Any

import json_repair

from agent.component.llm import LLM, LLMParam
from agent.tools.base import LLMToolPluginCallSession, ToolBase, ToolMeta, ToolParamBase
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.llm_service import LLMBundle
from api.db.services.mcp_server_service import MCPServerService
from api.db.services.tenant_llm_service import TenantLLMService
from common.connection_utils import timeout
from common.mcp_tool_call_conn import MCPToolCallSession, mcp_tool_metadata_to_openai_tool
from rag.prompts.generator import citation_plus, citation_prompt, full_question, kb_prompt, message_fit_in, structured_output_prompt


class AgentParam(LLMParam, ToolParamBase):
    """
    Define the Agent component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "agent",
            "description": "This is an agent for a specific task.",
            "parameters": {
                "user_prompt": {"type": "string", "description": "This is the order you need to send to the agent.", "default": "", "required": True},
                "reasoning": {
                    "type": "string",
                    "description": ("Supervisor's reasoning for choosing the this agent. Explain why this agent is being invoked and what is expected of it."),
                    "required": True,
                },
                "context": {
                    "type": "string",
                    "description": (
                        "All relevant background information, prior facts, decisions, and state needed by the agent to solve the current query. Should be as detailed and self-contained as possible."
                    ),
                    "required": True,
                },
            },
        }
        super().__init__()
        self.function_name = "agent"
        self.tools = []
        self.mcp = []
        self.max_rounds = 5
        self.description = ""
        self.custom_header = {}


class Agent(LLM, ToolBase):
    component_name = "Agent"

    def __init__(self, canvas, id, param: LLMParam):
        LLM.__init__(self, canvas, id, param)
        self.tools = {}
        for idx, cpn in enumerate(self._param.tools):
            cpn = self._load_tool_obj(cpn)
            original_name = cpn.get_meta()["function"]["name"]
            indexed_name = f"{original_name}_{idx}"
            self.tools[indexed_name] = cpn
        chat_model_config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), TenantLLMService.llm_id2llm_type(self._param.llm_id), self._param.llm_id)
        self.chat_mdl = LLMBundle(
            self._canvas.get_tenant_id(),
            chat_model_config,
            max_retries=self._param.max_retries,
            retry_interval=self._param.delay_after_error,
            max_rounds=self._param.max_rounds,
            verbose_tool_use=False,
        )
        self.tool_meta = []
        for indexed_name, tool_obj in self.tools.items():
            original_meta = tool_obj.get_meta()
            indexed_meta = deepcopy(original_meta)
            indexed_meta["function"]["name"] = indexed_name
            self.tool_meta.append(indexed_meta)

        for mcp in self._param.mcp:
            _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"])
            custom_header = self._param.custom_header
            tool_call_session = MCPToolCallSession(mcp_server, mcp_server.variables, custom_header)
            for tnm, meta in mcp["tools"].items():
                self.tool_meta.append(mcp_tool_metadata_to_openai_tool(meta))
                self.tools[tnm] = tool_call_session
        self.callback = partial(self._canvas.tool_use_callback, id)
        self.toolcall_session = LLMToolPluginCallSession(self.tools, self.callback)
        if self.tool_meta:
            self.chat_mdl.bind_tools(self.toolcall_session, self.tool_meta)

    def _fit_messages(self, prompt: str, msg: list[dict]) -> list[dict]:
        _, fitted_messages = message_fit_in(
            [{"role": "system", "content": prompt}, *msg],
            int(self.chat_mdl.max_length * 0.97),
        )
        return fitted_messages

    @staticmethod
    def _append_system_prompt(msg: list[dict], extra_prompt: str) -> None:
        if extra_prompt and msg and msg[0]["role"] == "system":
            msg[0]["content"] += "\n" + extra_prompt

    @staticmethod
    def _clean_formatted_answer(ans: str) -> str:
        ans = re.sub(r"^.*</think>", "", ans, flags=re.DOTALL)
        ans = re.sub(r"^.*```json", "", ans, flags=re.DOTALL)
        return re.sub(r"```\n*$", "", ans, flags=re.DOTALL)

    def _load_tool_obj(self, cpn: dict) -> object:
        from agent.component import component_class

        tool_name = cpn["component_name"]
        param = component_class(tool_name + "Param")()
        param.update(cpn["params"])
        try:
            param.check()
        except Exception as e:
            self.set_output("_ERROR", cpn["component_name"] + f" configuration error: {e}")
            raise
        cpn_id = f"{self._id}-->" + cpn.get("name", "").replace(" ", "_")
        return component_class(cpn["component_name"])(self._canvas, cpn_id, param)

    def get_meta(self) -> dict[str, Any]:
        self._param.function_name = self._id.split("-->")[-1]
        m = super().get_meta()
        if hasattr(self._param, "user_prompt") and self._param.user_prompt:
            # Keep the JSON schema valid; user_prompt is a string field, not a schema node.
            m["function"]["parameters"]["properties"]["user_prompt"]["default"] = self._param.user_prompt
        return m

    def get_input_form(self) -> dict[str, dict]:
        res = {}
        for k, v in self.get_input_elements().items():
            res[k] = {"type": "line", "name": v["name"]}
        for cpn in self._param.tools:
            if not isinstance(cpn, LLM):
                continue
            res.update(cpn.get_input_form())
        return res

    def _get_output_schema(self):
        try:
            cand = self._param.outputs.get("structured")
        except Exception:
            return None

        if isinstance(cand, dict):
            if isinstance(cand.get("properties"), dict) and len(cand["properties"]) > 0:
                return cand
            for k in ("schema", "structured"):
                if isinstance(cand.get(k), dict) and isinstance(cand[k].get("properties"), dict) and len(cand[k]["properties"]) > 0:
                    return cand[k]

        return None

    async def _force_format_to_schema_async(self, text: str, schema_prompt: str) -> str:
        fmt_msgs = [
            {"role": "system", "content": schema_prompt + "\nIMPORTANT: Output ONLY valid JSON. No markdown, no extra text."},
            {"role": "user", "content": text},
        ]
        _, fmt_msgs = message_fit_in(fmt_msgs, int(self.chat_mdl.max_length * 0.97))
        return await self._generate_async(fmt_msgs)

    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 20 * 60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("Agent processing"):
            return

        if kwargs.get("user_prompt"):
            # Build supervisor context into system prompt, NOT role=user.
            # Upstream RAGFlow wraps reasoning+context+query into a single
            # role=user message, causing the LLM to confuse supervisor-provided
            # analysis with user-submitted content ("您已自行完成了分析").
            supervisor_ctx = ""
            if kwargs.get("reasoning"):
                supervisor_ctx += "\n\n【监督者指令】\n{}".format(kwargs["reasoning"])
            if kwargs.get("context"):
                supervisor_ctx += "\n\n【监督者提供的背景信息】\n{}".format(kwargs["context"])
            if supervisor_ctx:
                self._param.sys_prompt = (self._param.sys_prompt or "") + supervisor_ctx
            self._param.prompts = [{"role": "user", "content": str(kwargs["user_prompt"])}]

        if not self.tools:
            if self.check_if_canceled("Agent processing"):
                return
            return await LLM._invoke_async(self, **kwargs)

        prompt, msg, user_defined_prompt = self._prepare_prompt_variables()
        output_schema = self._get_output_schema()
        schema_prompt = ""
        if output_schema:
            schema = json.dumps(output_schema, ensure_ascii=False, indent=2)
            schema_prompt = structured_output_prompt(schema)

        component = self._canvas.get_component(self._id)
        downstreams = component["downstream"] if component else []
        ex = self.exception_handler()
        has_message_downstream = any(self._canvas.get_component_obj(cid).component_name.lower() == "message" for cid in downstreams)
        if has_message_downstream and not (ex and ex["goto"]) and not output_schema:
            self.set_output("content", partial(self.stream_output_with_tools_async, prompt, deepcopy(msg), user_defined_prompt))
            return

        msg = self._fit_messages(prompt, msg)
        self._append_system_prompt(msg, schema_prompt)
        ans = await self._generate_async(msg)

        if ans.find("**ERROR**") >= 0:
            logging.error(f"Agent._chat got error. response: {ans}")
            if self.get_exception_default_value():
                self.set_output("content", self.get_exception_default_value())
            else:
                self.set_output("_ERROR", ans)
            return

        if output_schema:
            logging.info("[STRUCTURED-OUTPUT] agent=%s has_schema=True max_retries=%d raw_ans_len=%d preview=%s",
                         self._id, self._param.max_retries, len(ans or ""), (ans or "")[:300].replace("\n", "\\n"))
            error = ""
            for attempt in range(self._param.max_retries + 1):
                try:
                    cleaned = self._clean_formatted_answer(ans)
                    logging.info("[STRUCTURED-OUTPUT] agent=%s attempt=%d cleaned_len=%d cleaned_preview=%s",
                                 self._id, attempt, len(cleaned), cleaned[:300].replace("\n", "\\n"))
                    obj = json_repair.loads(cleaned)
                    self.set_output("structured", obj)
                    logging.info("[STRUCTURED-OUTPUT] agent=%s SUCCESS attempt=%d keys=%s",
                                 self._id, attempt, list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__)
                    return obj
                except Exception as e:
                    logging.warning("[STRUCTURED-OUTPUT] agent=%s attempt=%d PARSE_FAILED: %s  raw_ans_tail=%s",
                                    self._id, attempt, str(e)[:200], (ans or "")[-200:].replace("\n", "\\n"))
                    error = f"The answer cannot be parsed as JSON: {e}"
                    ans = await self._force_format_to_schema_async(ans, schema_prompt)
                    if ans.find("**ERROR**") >= 0:
                        logging.warning("[STRUCTURED-OUTPUT] agent=%s force_format got ERROR on attempt=%d",
                                        self._id, attempt)
                        continue

            logging.error("[STRUCTURED-OUTPUT] agent=%s ALL_RETRIES_FAILED final_error=%s",
                          self._id, error)
            self.set_output("_ERROR", error)
            return

        artifact_md = self._collect_tool_artifact_markdown(existing_text=ans)
        if artifact_md:
            ans += "\n\n" + artifact_md
        self.set_output("content", ans)
        return ans

    async def stream_output_with_tools_async(self, prompt, msg, user_defined_prompt={}):
        if len(msg) > 3:
            st = timer()
            # Truncate assistant messages to prevent full_question LLM from
            # confusing previous agent outputs with user-provided content.
            truncated = []
            for m in msg:
                if m["role"] == "assistant" and len(m.get("content", "")) > 200:
                    truncated.append({**m, "content": m["content"][:200] + "..."})
                else:
                    truncated.append(m)
            user_request = await full_question(messages=truncated, chat_mdl=self.chat_mdl)
            # Safety: if full_question hallucinates a huge report instead of a
            # refined question, fall back to the original user message.
            original = msg[-1]["content"]
            if len(user_request) > max(len(original) * 5, 500):
                logging.warning(
                    "[FULL-QUESTION] output too long (%d chars vs original %d), falling back",
                    len(user_request), len(original)
                )
                user_request = original
            self.callback("Multi-turn conversation optimization", {}, user_request, elapsed_time=timer() - st)
            msg = [*msg[:-1], {"role": "user", "content": user_request}]

        msg = self._fit_messages(prompt, msg)

        # ── [DIAGNOSTIC] Log LLM context to detect self-referential content ──
        for i, m in enumerate(msg):
            content = str(m.get("content", ""))
            role = m.get("role", "?")
            # Look for signs of old agent output in the context
            has_citation_ids = "[ID:" in content or "[ID：" in content
            is_large = len(content) > 5000
            logging.info(
                "[LLM-CONTEXT msg#%d] role=%s len=%d has_citation_ids=%s is_large=%s preview=%s",
                i, role, len(content), has_citation_ids, is_large,
                content[:200].replace("\n", "\\n")
            )
        logging.info("[LLM-CONTEXT-TOTAL] msgs=%d total_chars=%d", len(msg), sum(len(str(m.get("content",""))) for m in msg))
        # ── END DIAGNOSTIC ──

        # need2cite checks cite param and that this is not a sub-agent (no "-->" in id).
        # We intentionally do NOT check get_reference()["chunks"] here because when the
        # LLM calls retrieval as a tool, chunks are populated mid-generation (after this
        # check). Post-generation we re-check and run citation post-processing if chunks
        # were added by tools during streaming.
        need2cite = self._param.cite and self._id.find("-->") < 0
        has_chunks_before = bool(self._canvas.get_reference()["chunks"])
        cited = False
        if need2cite and has_chunks_before and len(msg) < 7:
            self._append_system_prompt(msg, citation_prompt())
            cited = True

        logging.info("[STREAM-START] agent=%s need2cite=%s cited=%s has_chunks_before=%s msg_count=%d",
                     self._id, need2cite, cited, has_chunks_before, len(msg))

        answer = ""
        delta_count = 0
        first_delta_yielded = False
        think_content_len = 0
        in_think = False
        async for delta in self._generate_streamly(msg):
            if self.check_if_canceled("Agent streaming"):
                return
            if delta.find("**ERROR**") >= 0:
                if self.get_exception_default_value():
                    self.set_output("content", self.get_exception_default_value())
                    yield self.get_exception_default_value()
                else:
                    self.set_output("_ERROR", delta)
                return
            if not need2cite or cited:
                if not first_delta_yielded:
                    logging.info("[STREAM-FIRST-YIELD] agent=%s delta_count=%d preview=%s",
                                 self._id, delta_count, delta[:80].replace("\n", "\\n"))
                    first_delta_yielded = True
                yield delta
            answer += delta
            delta_count += 1
            # Track think block content length
            if delta == "<think>":
                in_think = True
                continue
            if delta == "</think>":
                in_think = False
                continue
            if in_think:
                think_content_len += len(delta)

        # ── [STREAM-END] diagnostic: what was actually streamed ──
        has_think_block = "<think>" in answer and "</think>" in answer
        visible_answer = answer
        if has_think_block:
            # Extract visible content after </think> for length calc
            end_think_pos = answer.rfind("</think>")
            visible_answer = answer[end_think_pos + len("</think>"):]
        logging.info("[STREAM-END] agent=%s delta_count=%d answer_total_len=%d think_len=%d visible_after_think_len=%d has_think_block=%s visible_preview=%s",
                     self._id, delta_count, len(answer), think_content_len, len(visible_answer.strip()),
                     has_think_block, visible_answer.strip()[:150].replace("\n", "\\n"))

        if not need2cite or cited:
            artifact_md = self._collect_tool_artifact_markdown(existing_text=answer)
            if artifact_md:
                yield "\n\n" + artifact_md
                answer += "\n\n" + artifact_md
            self.set_output("content", answer)
            logging.info("[STREAM-PATH] agent=%s path=direct_return answer_final_len=%d", self._id, len(answer))
            return

        # Re-check: chunks may have been populated by retrieval tools during streaming
        if not self._canvas.get_reference()["chunks"]:
            yield answer
            self.set_output("content", answer)
            logging.info("[STREAM-PATH] agent=%s path=no_chunks_recheck answer_len=%d", self._id, len(answer))
            return

        st = timer()
        cited_answer = ""
        async for delta in self._gen_citations_async(answer):
            if self.check_if_canceled("Agent streaming"):
                return
            yield delta
            cited_answer += delta
        artifact_md = self._collect_tool_artifact_markdown(existing_text=cited_answer)
        if artifact_md:
            yield "\n\n" + artifact_md
            cited_answer += "\n\n" + artifact_md
        self.callback("gen_citations", {}, cited_answer, elapsed_time=timer() - st)
        self.set_output("content", cited_answer)
        logging.info("[STREAM-PATH] agent=%s path=citations cited_answer_len=%d", self._id, len(cited_answer))

    @staticmethod
    def _strip_internal_tags(text: str) -> str:
        """Strip think/reasoning blocks and tool-call verbose output.

        Some LLMs wrap reasoning in `` tags; some don't.
        Tool-call sessions emit `` blocks for each invocation.
        Both are meaningless for citation generation and must be removed
        so the citation agent only sees the final answer text.
        """
        # 1. Strip thinking/reasoning blocks (safe no-op when absent)
        text = re.sub(r"<think[^>]*>.*?</think[^>]*>", "", text, flags=re.DOTALL)
        # 2. Strip tool-call verbose blocks (safe no-op when absent)
        text = re.sub(r"<tool_call.*?</tool_call\s*>", "", text, flags=re.DOTALL)
        # 3. Collapse excessive blank lines left after stripping
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    async def _gen_citations_async(self, text):
        text = self._strip_internal_tags(text)
        if not text:
            return
        retrievals = self._canvas.get_reference()
        retrievals = {"chunks": list(retrievals["chunks"].values()), "doc_aggs": list(retrievals["doc_aggs"].values())}
        formated_refer = kb_prompt(retrievals, self.chat_mdl.max_length, True)
        async for delta_ans in self._generate_streamly([{"role": "system", "content": citation_plus("\n\n".join(formated_refer))}, {"role": "user", "content": text}]):
            yield delta_ans

    def _collect_tool_artifact_markdown(self, existing_text: str = "") -> str:
        md_parts = []
        for tool_obj in self.tools.values():
            if not hasattr(tool_obj, "_param") or not hasattr(tool_obj._param, "outputs"):
                continue
            artifacts_meta = tool_obj._param.outputs.get("_ARTIFACTS", {})
            artifacts = artifacts_meta.get("value") if isinstance(artifacts_meta, dict) else None
            if not artifacts:
                continue
            for art in artifacts:
                if not isinstance(art, dict):
                    continue
                url = art.get("url", "")
                if url and (f"![]({url})" in existing_text or f"![{art.get('name', '')}]({url})" in existing_text):
                    continue
                if art.get("mime_type", "").startswith("image/"):
                    md_parts.append(f"![{art['name']}]({url})")
                else:
                    md_parts.append(f"[Download {art['name']}]({url})")
        return "\n\n".join(md_parts)

    def reset(self, only_output=False):
        """
        Reset all tools if they have a reset method. This avoids errors for tools like MCPToolCallSession.
        """
        for k in self._param.outputs.keys():
            self._param.outputs[k]["value"] = None

        for k, cpn in self.tools.items():
            if hasattr(cpn, "reset") and callable(cpn.reset):
                cpn.reset()
        if only_output:
            return
        for k in self._param.inputs.keys():
            self._param.inputs[k]["value"] = None
        self._param.debug_inputs = {}
