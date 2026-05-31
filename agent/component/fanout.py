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
import time
from abc import ABC
from agent.component.base import ComponentBase, ComponentParamBase
from api.db.services.llm_service import LLMBundle
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name


class FanOutParam(ComponentParamBase):
    def __init__(self):
        super().__init__()
        self.items_ref: str = ""
        self.llm_id: str = ""
        self.system_prompt: str = ""
        self.prompt_template: str = ""
        self.max_concurrency: int = 5
        self.error_strategy: str = "skip"

    def get_input_form(self) -> dict[str, dict]:
        return {
            "items": {"type": "json", "name": "Items"},
            "llm_id": {"type": "llm", "name": "LLM"},
            "system_prompt": {"type": "text", "name": "System Prompt"},
            "prompt_template": {"type": "text", "name": "Prompt Template"},
            "max_concurrency": {"type": "number", "name": "Max Concurrency"},
            "error_strategy": {
                "type": "options",
                "name": "Error Strategy",
                "options": ["skip", "stop"],
            },
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

    def get_start(self) -> str:
        for cid in self._canvas.components.keys():
            cpn = self._canvas.get_component(cid)
            if cpn.get("parent_id") == self._id:
                return cid
        return ""

    def _render_prompt(self, item_value, idx: int) -> str:
        tmpl = self._param.prompt_template
        item_str = item_value if isinstance(item_value, str) else json.dumps(item_value, ensure_ascii=False)
        rendered = tmpl.replace("{item}", item_str)
        rendered = rendered.replace("{index}", str(idx))
        return rendered

    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("FanOut processing"):
            return

        arr = self._canvas.get_variable_value(self._param.items_ref)
        if not isinstance(arr, (list, tuple)):
            self.set_output("_ERROR",
                            f"{self._param.items_ref} must be an array, but got {type(arr).__name__}")
            return

        arr = list(arr)
        n = len(arr)
        if n == 0:
            self.set_output("results", [])
            return

        tenant_id = self._canvas.get_tenant_id()
        llm_id = self._param.llm_id
        try:
            chat_model_config = get_model_config_by_type_and_name(
                tenant_id,
                TenantLLMService.llm_id2llm_type(llm_id),
                llm_id,
            )
        except Exception as e:
            self.set_output("_ERROR", f"Failed to get LLM config for {llm_id}: {e}")
            return

        chat_mdl = LLMBundle(tenant_id, chat_model_config)

        self._progress = {i: {"status": "pending"} for i in range(n)}
        self._cancel_event.clear()
        sem = asyncio.Semaphore(max(1, int(self._param.max_concurrency)))
        completed_count = [0]

        async def _process_one(idx: int, item_value):
            async with sem:
                if self._cancel_event.is_set():
                    return None

                started = time.perf_counter()
                self._progress[idx] = {"status": "running", "started_at": started}

                try:
                    rendered = self._render_prompt(item_value, idx)

                    chunks: list[str] = []
                    async for chunk in chat_mdl.async_chat_streamly_delta(
                        self._param.system_prompt,
                        [{"role": "user", "content": rendered}],
                        {},
                    ):
                        if self._cancel_event.is_set():
                            break
                        if isinstance(chunk, int):
                            continue
                        chunks.append(chunk)
                        await self._event_queue.put({
                            "event": "message",
                            "data": {"content": chunk},
                        })

                    result = "".join(chunks)
                    elapsed = time.perf_counter() - started
                    self._progress[idx] = {"status": "completed", "result": result, "elapsed": elapsed}

                    completed_count[0] += 1
                    await self._event_queue.put({
                        "event": "fanout_progress",
                        "data": {
                            "idx": idx,
                            "completed": completed_count[0],
                            "total": n,
                            "elapsed": elapsed,
                        },
                    })

                    return result

                except Exception as e:
                    logging.exception(f"FanOut item {idx} failed: {e}")
                    self._progress[idx] = {"status": "error", "error": str(e)}
                    completed_count[0] += 1
                    await self._event_queue.put({
                        "event": "fanout_progress",
                        "data": {
                            "idx": idx,
                            "completed": completed_count[0],
                            "total": n,
                            "error": str(e),
                        },
                    })

                    if self._param.error_strategy == "stop":
                        self._cancel_event.set()
                        raise
                    return None

        tasks = [asyncio.create_task(_process_one(i, v)) for i, v in enumerate(arr)]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for i, r in enumerate(gathered):
            if isinstance(r, Exception):
                logging.error(f"FanOut item {i} unhandled exception: {r}")
                results.append({"item_index": i, "content": None, "error": str(r)})
            else:
                results.append({"item_index": i, "content": r})

        self.set_output("results", results)

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
