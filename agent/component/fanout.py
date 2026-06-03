#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
import re
import time
from abc import ABC
from copy import deepcopy
from functools import partial

from agent.component.base import ComponentBase, ComponentParamBase
from agent.tools.base import LLMToolPluginCallSession
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.db.services.llm_service import LLMBundle
from api.db.services.mcp_server_service import MCPServerService
from api.db.services.tenant_llm_service import TenantLLMService
from common.mcp_tool_call_conn import MCPToolCallSession, mcp_tool_metadata_to_openai_tool


class FanOutParam(ComponentParamBase):
    def __init__(self):
        super().__init__()
        self.items_ref: str = ""
        self.llm_id: str = ""
        self.system_prompt: str = ""
        self.prompt_template: str = ""
        self.max_concurrency: int = 5
        self.error_strategy: str = "skip"
        self.tools: list = []
        self.mcp: list = []
        self.custom_header: dict = {}

    def get_input_form(self) -> dict[str, dict]:
        """Return the upstream data inputs for this component.

        FanOut only expects ONE input from the canvas:
            items   — {type: json} the array of items to process in parallel

        Everything else (llm_id, system_prompt, prompt_template,
        max_concurrency, error_strategy, tools, mcp) is *configuration*
        that the user sets in the properties panel — NOT data flowing
        through canvas edges.
        """
        return {
            "items": {
                "type": "line",
                "name": "Items",
                "description": "JSON array to process in parallel. "
                "Each item is injected into the prompt template as {item}.",
            }
        }

    def check(self):
        self.check_empty(self.items_ref, "Items reference")
        self.check_empty(self.llm_id, "LLM model")
        self.check_positive_integer(self.max_concurrency, "Max concurrency")
        return True


class FanOut(ComponentBase, ABC):
    component_name = "FanOut"

    def __init__(self, canvas, id, param: ComponentParamBase):
        super().__init__(canvas, id, param)
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._progress: dict[int, dict] = {}
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._tools_loaded: bool = False
        self._tools_map: dict[str, object] = {}
        self._tool_meta: list[dict] = []

    def _invoke(self, **kwargs):
        """Sync entry point — used by the debug/run-single-component API.

        Accepts ``items`` as a JSON array (string or parsed list).
        Executes the async FanOut logic in a one-shot event loop on a
        worker thread so it works from both sync and async callers.
        """
        debug_items = kwargs.get("items")
        if debug_items is None:
            self.set_output("_ERROR",
                "FanOut is an async component.  Provide an 'items' array "
                "(e.g. [\"a\",\"b\",\"c\"]) in the debug panel to test directly, "
                "or run via the full canvas pipeline.")
            return

        if isinstance(debug_items, str):
            import json as _json
            try:
                debug_items = _json.loads(debug_items)
            except (TypeError, _json.JSONDecodeError):
                self.set_output("_ERROR", f"items is not valid JSON: {debug_items!r}")
                return

        if not isinstance(debug_items, (list, tuple)):
            self.set_output("_ERROR", f"items must be an array, got {type(debug_items).__name__}")
            return

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(
                asyncio.run, self._invoke_async_with_items(list(debug_items))
            ).result()

    async def _invoke_async_with_items(self, items: list):
        """Run FanOut with an explicit items list (used by both sync _invoke and canvas _invoke_async)."""
        tenant_id = self._canvas.get_tenant_id() if self._canvas else ""

        n = len(items)
        if n == 0:
            self.set_output("results", [])
            return

        try:
            chat_mdl = self._create_chat_mdl(tenant_id)
            logging.info(f"FanOut LLM model created: llm_id={self._param.llm_id}, tenant={tenant_id}")
        except Exception as e:
            logging.error(f"FanOut failed to get LLM config: {e}")
            self.set_output("_ERROR", f"Failed to get LLM config for {self._param.llm_id}: {e}")
            return

        self._progress = {i: {"status": "pending"} for i in range(n)}
        self._cancel_event.clear()
        sem = asyncio.Semaphore(max(1, int(self._param.max_concurrency)))
        completed_count = [0]
        gather_started = time.perf_counter()
        logging.info(f"FanOut START: {n} items, max_concurrency={self._param.max_concurrency}")

        async def _process_one(idx: int, item_value):
            async with sem:
                if self._cancel_event.is_set():
                    return None

                started = time.perf_counter()
                self._progress[idx] = {"status": "running", "started_at": started}
                label = self._item_label(item_value, idx)
                logging.info(f"FanOut lane {idx}/{n} START: {label}")

                try:
                    rendered = self._render_prompt(item_value, idx)
                    chunks: list[str] = []
                    in_think = False
                    async for chunk in chat_mdl.async_chat_streamly_delta(
                        self._param.system_prompt,
                        [{"role": "user", "content": rendered}],
                        {},
                    ):
                        if self._cancel_event.is_set():
                            break
                        if isinstance(chunk, int):
                            continue
                        if isinstance(chunk, Exception):
                            raise chunk
                        if not isinstance(chunk, str):
                            continue
                        clean = ""
                        pos = 0
                        while pos < len(chunk):
                            if in_think:
                                end = chunk.find("</think>", pos)
                                if end == -1:
                                    break
                                in_think = False
                                pos = end + len("</think>")
                            else:
                                start = chunk.find("<think>", pos)
                                if start == -1:
                                    end = chunk.find("</think>", pos)
                                    if end != -1:
                                        pos = end + len("</think>")
                                        continue
                                    clean += chunk[pos:]
                                    break
                                clean += chunk[pos:start]
                                in_think = True
                                pos = start + len("<think>")
                        if clean:
                            chunks.append(clean)

                    result = re.sub(r"<think>.*?</think>", "", "".join(chunks), flags=re.DOTALL)
                    result = re.sub(r"</?think>", "", result)
                    elapsed = time.perf_counter() - started
                    self._progress[idx] = {"status": "completed", "result": result, "elapsed": elapsed}
                    completed_count[0] += 1
                    logging.info(f"FanOut lane {idx}/{n} DONE: {label} ({elapsed:.1f}s) [{completed_count[0]}/{n}]")
                    # Send the complete chapter content as a single message event
                    await self._event_queue.put({
                        "event": "message",
                        "data": {"content": result},
                    })
                    return result

                except Exception as e:
                    logging.exception(f"FanOut item {idx} failed: {e}")
                    self._progress[idx] = {"status": "error", "error": str(e)}
                    completed_count[0] += 1
                    logging.error(f"FanOut lane {idx}/{n} ERROR: {self._item_label(item_value, idx)} — {e} [{completed_count[0]}/{n}]")
                    if self._param.error_strategy == "stop":
                        self._cancel_event.set()
                        raise
                    return None

        tasks = [asyncio.create_task(_process_one(i, v)) for i, v in enumerate(items)]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        gather_elapsed = time.perf_counter() - gather_started
        success_count = sum(1 for r in gathered if not isinstance(r, Exception))
        error_count = sum(1 for r in gathered if isinstance(r, Exception))
        logging.info(f"FanOut GATHER DONE: {n} items, {success_count} ok, {error_count} errors in {gather_elapsed:.1f}s")

        results = []
        for i, r in enumerate(gathered):
            if isinstance(r, Exception):
                results.append({"item_index": i, "content": None, "error": str(r)})
            else:
                results.append({"item_index": i, "content": r})
        self.set_output("results_raw", results)

        # Build flat text output for downstream components.
        parts: list[str] = []
        for entry in results:
            c = entry.get("content")
            if c:
                parts.append(c)
        self.set_output("results", "\n\n---\n\n".join(parts))

    def get_start(self) -> str:
        for cid in self._canvas.components.keys():
            cpn = self._canvas.get_component(cid)
            if cpn.get("parent_id") == self._id:
                return cid
        return ""

    def _load_tool_obj(self, cpn: dict):
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

    def _setup_tools(self) -> None:
        if self._tools_loaded:
            return
        self._tools_loaded = True

        if not self._param.tools and not self._param.mcp:
            return

        for idx, cpn in enumerate(self._param.tools):
            cpn_obj = self._load_tool_obj(cpn)
            original_name = cpn_obj.get_meta()["function"]["name"]
            indexed_name = f"{original_name}_{idx}"
            self._tools_map[indexed_name] = cpn_obj

        for indexed_name, tool_obj in self._tools_map.items():
            original_meta = tool_obj.get_meta()
            indexed_meta = deepcopy(original_meta)
            indexed_meta["function"]["name"] = indexed_name
            self._tool_meta.append(indexed_meta)

        for mcp in self._param.mcp:
            _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"])
            custom_header = self._param.custom_header
            tool_call_session = MCPToolCallSession(mcp_server, mcp_server.variables, custom_header)
            for tnm, meta in mcp["tools"].items():
                self._tool_meta.append(mcp_tool_metadata_to_openai_tool(meta))
                self._tools_map[tnm] = tool_call_session

        logging.info(f"FanOut tools registered: {list(self._tools_map.keys())}")

    def _create_chat_mdl(self, tenant_id: str) -> LLMBundle:
        self._setup_tools()
        chat_model_config = get_model_config_by_type_and_name(
            tenant_id,
            TenantLLMService.llm_id2llm_type(self._param.llm_id),
            self._param.llm_id,
        )
        chat_mdl = LLMBundle(tenant_id, chat_model_config)
        if self._tool_meta:
            callback = partial(self._canvas.tool_use_callback, self._id)
            toolcall_session = LLMToolPluginCallSession(self._tools_map, callback)
            chat_mdl.bind_tools(toolcall_session, self._tool_meta)
        return chat_mdl

    def _render_prompt(self, item_value, idx: int) -> str:
        tmpl = self._param.prompt_template
        item_str = item_value if isinstance(item_value, str) else json.dumps(item_value, ensure_ascii=False)
        rendered = tmpl.replace("{item}", item_str)
        rendered = rendered.replace("{index}", str(idx))
        return rendered

    def _item_label(self, item_value, idx: int) -> str:
        """Extract a human-readable label from an item for progress display."""
        if isinstance(item_value, dict):
            for key in ("chapter_title", "title", "name", "label", "章节标题", "标题", "名称"):
                if key in item_value and item_value[key]:
                    return str(item_value[key])
        if isinstance(item_value, str) and item_value:
            return item_value[:60] + "..." if len(item_value) > 60 else item_value
        return f"Item {idx}"

    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("FanOut processing"):
            return

        # Try explicit items from kwargs first (debug / single-run path),
        # otherwise resolve from the canvas variable referenced by items_ref.
        explicit = kwargs.get("items")
        if explicit is not None:
            if isinstance(explicit, str):
                import json as _json
                try:
                    explicit = _json.loads(explicit)
                except (TypeError, _json.JSONDecodeError):
                    self.set_output("_ERROR", f"items is not valid JSON: {explicit!r}")
                    return
            if not isinstance(explicit, (list, tuple)):
                self.set_output("_ERROR", f"items must be an array, got {type(explicit).__name__}")
                return
            arr = list(explicit)
        else:
            arr = self._canvas.get_variable_value(self._param.items_ref)
            logging.info(f"FanOut items_ref={self._param.items_ref!r}, resolved type={type(arr).__name__}, len={len(arr) if isinstance(arr, (list, tuple)) else '?'}")

            if not isinstance(arr, (list, tuple)):
                # Diagnostic: dump the upstream component's output to find the root cause
                parts = (self._param.items_ref or "").split("@")
                if len(parts) >= 1:
                    upstream_id = parts[0]
                    upstream = self._canvas.get_component(upstream_id)
                    if upstream:
                        upstream_outputs = upstream["obj"].output()
                        logging.error(
                            f"FanOut DIAGNOSTIC: upstream '{upstream_id}' "
                            f"output keys={list(upstream_outputs.keys()) if isinstance(upstream_outputs, dict) else type(upstream_outputs).__name__}, "
                            f"structured={upstream['obj'].output('structured')!r}"
                        )
                    else:
                        logging.error(f"FanOut DIAGNOSTIC: upstream '{upstream_id}' NOT FOUND in components")
                msg = f"{self._param.items_ref} must be an array, but got {type(arr).__name__}: {arr!r}"
                logging.error(f"FanOut: {msg}")
                self.set_output("_ERROR", msg)
                return
            arr = list(arr)

        logging.info(f"FanOut processing {len(arr)} items, max_concurrency={self._param.max_concurrency}")
        await self._invoke_async_with_items(arr)

    def thoughts(self) -> str:
        if not self._progress:
            val = self._canvas.get_variable_value(self._param.items_ref)
            if isinstance(val, (list, tuple)):
                return f"Will process {len(val)} items in parallel."
            return "Waiting for input items."

        total = len(self._progress)
        completed = sum(1 for p in self._progress.values() if p["status"] == "completed")
        running = sum(1 for p in self._progress.values() if p["status"] == "running")
        errors = sum(1 for p in self._progress.values() if p["status"] == "error")
        pending = total - completed - running - errors

        parts = [f"Processing {total} items in parallel"]
        if completed:
            parts.append(f"{completed} done")
        if running:
            parts.append(f"{running} running")
        if pending:
            parts.append(f"{pending} queued")
        if errors:
            parts.append(f"{errors} failed")
        return ", ".join(parts) + "."
