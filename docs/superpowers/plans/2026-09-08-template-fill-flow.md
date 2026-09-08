# 模板填写进度流式 + 流程页签适配 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TemplateFill 画布节点推送 5 类进度事件（selected/filling/filled/failed/done|cancelled）经 SSE 实时渲染到 C端对话与流程 AI 面板；流程场景支持版本文本轻量注入 + 成稿一键存为流程版本。

**Architecture:** 复用 FanOut 已验证的事件管道（组件 `_event_queue` → canvas.run 心跳 drain → 同名 SSE → `use-send-message.ts` 消费）。canvas drain 从「仅 FanOut」泛化为「任意带 `_event_queue` 的组件」。前端进度状态归约为纯函数模块（可 jest 单测），渲染为 c-chat 与 flow 共用组件。设计文档：`docs/superpowers/specs/2026-09-08-template-fill-flow-design.md`。

**Tech Stack:** Python asyncio / Quart（后端）、React 18 + TS（前端）、pytest（`uv run --no-sync pytest`）、jest（`cd web && npx jest`）。

**约定（所有任务通用）：**
- Python 测试命令一律 `uv run --no-sync pytest <file> -v`（`--no-sync` 必带，否则 scrapling 依赖解析失败）。
- 前端文案只用中文，不加 i18n key（项目 CLAUDE.md 规则）。
- 每次 commit 带 `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`。
- 禁止部署服务器、禁止重启 Docker。

---

## 文件结构总览

| 层 | 文件 | 职责 | 任务 |
|---|---|---|---|
| 后端 | `rag/svr/template_fill/executor.py` | `generate_values` 增加可选 `on_progress(done,total)` 回调（进度数据源） | 1 |
| 后端 | `agent/component/template_fill.py` | `_event_queue` + 5 类事件推送 + 取消检查 | 2 |
| 后端 | `agent/canvas.py` | drain 泛化（新增 `_drain_component_events` 方法） | 3 |
| 后端 | `api/apps/restful_apis/flow_app.py` | 版本 content 端点 + upload_version source 扩展 | 8 |
| 前端 | `web/src/hooks/template-fill-stream.ts`（新建） | 事件归约纯函数 + 类型（hook 与渲染层共用） | 4 |
| 前端 | `web/src/hooks/use-send-message.ts` | SSE `template_fill_progress` 分支 + streamState 透出 | 5 |
| 前端 | `web/src/pages/c-chat/template-fill-progress.tsx`（新建） | 进度卡片渲染（c-chat 与 flow 共用） | 6 |
| 前端 | `web/src/pages/c-chat/index.tsx` | 流式气泡内渲染进度卡片 | 7 |
| 前端 | `web/src/pages/c-chat/flow/flow-types.ts` | `FlowVersionItem.source` 扩展 + `FlowLiveChat.templateFill` | 9 |
| 前端 | `web/src/services/flow-service.ts` | `getFlowVersionContent` | 9 |
| 前端 | `web/src/pages/c-chat/flow/flow-ai-panel.tsx` | 进度上报 + 轻量附带版本通道 | 9 |
| 前端 | `web/src/pages/c-chat/flow/flow-detail.tsx` | live 区渲染卡片 + 存为流程版本按钮 | 9 |
| 测试 | `test/test_template_fill_executor.py` / `test/test_template_fill_events.py`（新建）/ `test/test_canvas_event_drain.py`（新建）/ `test/test_flow_version_source.py`（新建）/ `web/src/hooks/__tests__/template-fill-stream.test.ts`（新建） | | 各任务内 |

---

### Task 1: executor.generate_values 进度回调

**Files:**
- Modify: `rag/svr/template_fill/executor.py:198-255`（generate_values）
- Test: `test/test_template_fill_executor.py`（追加）

- [ ] **Step 1: 写失败测试**（追加到 `test/test_template_fill_executor.py` 末尾，紧跟 `test_generate_values_batches_run_concurrently` 所在的「生成层」段落之后）

```python
def test_generate_values_on_progress_reports_batch_completion(monkeypatch):
    """on_progress 每批完成即回调 (done, total)；total 为全部 llm 槽数，末次回调 done=total。"""
    from rag.svr.template_fill import executor

    async def fake_chat(system, history, gen_conf=None, **kw):
        content = history[0]["content"]
        spec = json.loads(content.split("## 检索证据")[0].split("## 字段清单\n")[1])
        return json.dumps({s["key"]: "v" for s in spec}, ensure_ascii=False)

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    placeholders = [{"key": f"k{i}", "name": f"字段{i}", "description": "", "constraints": {}}
                    for i in range(5)]
    chunks_by_key = {f"k{i}": {"chunks": []} for i in range(5)}
    events = []
    vals, missing = executor._run_async(executor.generate_values(
        "t", placeholders, chunks_by_key, params={}, batch_size=2,
        on_progress=lambda done, total: events.append((done, total))))
    assert vals == {f"k{i}": "v" for i in range(5)} and missing == set()
    assert events[-1] == (5, 5)
    assert sorted(e[0] for e in events) == [2, 4, 5]   # 3 批 → 3 次回调
    assert all(e[1] == 5 for e in events)


def test_generate_values_on_progress_exception_swallowed(monkeypatch):
    """on_progress 抛异常不影响产值结果（进度是旁路，不能拖垮主流程）。"""
    from rag.svr.template_fill import executor

    async def fake_chat(system, history, gen_conf=None, **kw):
        content = history[0]["content"]
        spec = json.loads(content.split("## 检索证据")[0].split("## 字段清单\n")[1])
        return json.dumps({s["key"]: "v" for s in spec}, ensure_ascii=False)

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    placeholders = [{"key": "k1", "name": "字段一", "description": "", "constraints": {}}]
    chunks_by_key = {"k1": {"chunks": []}}

    def boom(done, total):
        raise RuntimeError("cb boom")

    vals, missing = executor._run_async(executor.generate_values(
        "t", placeholders, chunks_by_key, params={}, on_progress=boom))
    assert vals == {"k1": "v"} and missing == set()


def test_generate_values_without_on_progress_unchanged(monkeypatch):
    """不传 on_progress 行为与旧版完全一致（向后兼容回归）。"""
    from rag.svr.template_fill import executor

    async def fake_chat(system, history, gen_conf=None, **kw):
        return '{"k1": "v1", "k2": null}'

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    placeholders = [{"key": "k1", "name": "一", "description": "", "constraints": {}},
                    {"key": "k2", "name": "二", "description": "", "constraints": {}}]
    chunks_by_key = {"k1": {"chunks": []}, "k2": {"chunks": []}}
    vals, missing = executor._run_async(
        executor.generate_values("t", placeholders, chunks_by_key, params={}))
    assert vals == {"k1": "v1"} and missing == {"k2"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py::test_generate_values_on_progress_reports_batch_completion -v`
Expected: FAIL — `generate_values() got an unexpected keyword argument 'on_progress'`

- [ ] **Step 3: 实现 on_progress**

`rag/svr/template_fill/executor.py` 中 `generate_values`：签名追加参数，并把 `results = await asyncio.gather(*[_one(b) for b in batches])`（约 :244）替换为 as_completed 增量回调：

```python
async def generate_values(tenant_id: str, placeholders: list[dict], chunks_by_key: dict,
                          params: dict | None = None,
                          batch_size: int = BATCH_SIZE,
                          sem: asyncio.Semaphore | None = None,
                          on_progress=None) -> tuple[dict, set]:
```

docstring 末尾追加一行说明：`on_progress 为可选回调 (done, total)：每批 LLM 返回后即回调一次（批次并发下完成顺序不定），进度上报用；回调异常被吞掉不影响产值。`

原 gather 行替换为：

```python
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
                logging.exception("generate_values on_progress callback failed")
```

文件头如无 `import logging` 则补上（检查后再加，已有则跳过）。

- [ ] **Step 4: 跑测试确认全绿**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -v`
Expected: 全部 PASS（含存量用例，as_completed 与 gather 对 results 的最终集合等价）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): generate_values 支持批次进度回调 on_progress"
```

---

### Task 2: TemplateFill 组件事件推送

**Files:**
- Modify: `agent/component/template_fill.py`
- Test: `test/test_template_fill_events.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `test/test_template_fill_events.py`）

```python
"""TemplateFill 画布节点进度事件单测：事件序列契约（selected→filling→filled/done、
单范本失败推 failed 不中断、取消推 cancelled）。全依赖 stub，不触 KB/LLM/存储。"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from agent.component.template_fill import TemplateFill, TemplateFillParam


def _cand(tid, name, placeholders):
    return {"template_id": tid, "name": name, "description": "", "file_type": "docx",
            "slot_names": [p["key"] for p in placeholders],
            "_ver": MagicMock(render_file_id="v1"), "_placeholders": placeholders}


def _dl(doc_id):
    return {"doc_id": doc_id, "filename": f"{doc_id}.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 10, "url": f"/api/v1/agents/download?id={doc_id}&created_by=t",
            "name": f"{doc_id}.docx"}


def _make_comp(candidates, fill_results, canceled=False):
    """构造 TemplateFill 实例并 stub 全部外部依赖。fill_results: {tid: dl 或 Exception}"""
    comp = TemplateFill(MagicMock(), "node1", TemplateFillParam())
    comp._canvas.get_tenant_id.return_value = "t"
    comp.check_if_canceled = MagicMock(return_value=canceled)
    comp._load_candidates = lambda tenant_id: candidates
    comp._select_templates = asyncio.coroutine(lambda *a, **k: list(candidates)) \
        if hasattr(asyncio, "coroutine") else None
    # Python 3.12 无 asyncio.coroutine，用普通 async 闭包：
    async def _sel(tenant_id, cands, query):
        return list(cands)
    comp._select_templates = _sel
    comp._begin_fields = lambda: {}
    comp._user_file_evidence = lambda: ""

    async def fake_retrieve(*a, **k):
        return [{c["template_id"]: {"chunks": [], "query": ""}} for c in a[1]] \
            if False else [dict() for _ in a[1]]
    # retrieve_all_shared(tenant, placeholders_list, kb_ids, task_id=...) → 与 chosen 等长的 list
    comp._canvas_retrieve = fake_retrieve

    async def _fill_one(tenant_id, cand, chunks, query, begin_fields, user_file_text, sem,
                        on_progress=None):
        res = fill_results[cand["template_id"]]
        if isinstance(res, Exception):
            raise res
        return res, {}, 3

    comp._fill_one = _fill_one

    outs = {}
    comp.set_output = lambda k, v: outs.update({k: v})
    comp._outs = outs
    return comp


def _drain(comp):
    evs = []
    while not comp._event_queue.empty():
        evs.append(comp._event_queue.get_nowait())
    return evs


def _run(comp):
    from rag.svr.template_fill import executor as _exc  # noqa: F401 确认可导入
    import agent.component.template_fill as tf_mod

    async def fake_retrieve_all(tenant_id, placeholders_list, kb_ids, task_id=""):
        return [{} for _ in placeholders_list]

    orig = tf_mod.executor.retrieve_all_shared
    tf_mod.executor.retrieve_all_shared = fake_retrieve_all
    try:
        comp._canvas.get_tenant_id.return_value = "t"
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(comp._invoke_async())
        finally:
            loop.close()
    finally:
        tf_mod.executor.retrieve_all_shared = orig
    return _drain(comp)


def test_event_sequence_happy_path():
    """两范本成功：selected → (filling → filled)×2 → done；download 输出含 url/name。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"},
                                   {"key": "k2", "fill_mode": "param"}])]
    comp = _make_comp(cands, {"t1": _dl("d1"), "t2": _dl("d2")})
    evs = [(e["event"], e["data"]["stage"],
            e["data"].get("template_id")) for e in _run(comp)]
    assert evs == [
        ("template_fill_progress", "selected", None),
        ("template_fill_progress", "filling", "t1"),
        ("template_fill_progress", "filled", "t1"),
        ("template_fill_progress", "filling", "t2"),
        ("template_fill_progress", "filled", "t2"),
        ("template_fill_progress", "done", None),
    ]
    # selected 携带槽位数
    q = comp._event_queue  # 已清空，重放断言走原始事件即可，见下
    # 重新构造并断言 data 细节
    comp2 = _make_comp(cands, {"t1": _dl("d1"), "t2": _dl("d2")})
    raw = _run(comp2)
    sel = raw[0]["data"]
    assert sel["templates"] == [
        {"template_id": "t1", "name": "范本A", "slot_count": 1},
        {"template_id": "t2", "name": "范本B", "slot_count": 2}]
    filled1 = raw[2]["data"]
    assert filled1["download"]["url"] == "/api/v1/agents/download?id=d1&created_by=t"
    assert filled1["download"]["name"] == "d1.docx"
    # filling 事件 total = llm 槽数（param 槽不计）
    assert raw[3]["data"]["total"] == 1 and raw[3]["data"]["done"] == 0
    # download 输出为两份成稿 JSON 列表
    outs = json.loads(comp2._outs["download"])
    assert [d["doc_id"] for d in outs] == ["d1", "d2"]


def test_single_failure_pushes_failed_and_continues():
    """单范本异常：推 failed + 其余范本照常 filled，最终 done 不 cancelled。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}]),
             _cand("t2", "范本B", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": RuntimeError("检索炸了"), "t2": _dl("d2")})
    evs = _run(comp)
    stages = [(e["data"]["stage"], e["data"].get("template_id")) for e in evs]
    assert ("failed", "t1") in stages
    assert ("filled", "t2") in stages
    assert stages[-1] == ("done", None)
    assert "failed" not in [s for s, _ in stages[:1]]  # selected 仍是第一个
    failed_ev = next(e for e in evs if e["data"]["stage"] == "failed")
    assert "检索炸了" in failed_ev["data"]["error"]


def test_cancel_pushes_cancelled_and_no_done():
    """选中后取消：推 cancelled、不推 filled/done、不写输出。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": _dl("d1")})
    calls = {"n": 0}

    def check(*a, **k):
        calls["n"] += 1
        return calls["n"] > 1  # 第一次（入口）不取消，第二次（_fill_and_notify 内）取消

    comp.check_if_canceled = check
    evs = _run(comp)
    stages = [e["data"]["stage"] for e in evs]
    assert "selected" in stages
    assert stages[-1] == "cancelled"
    assert "filled" not in stages and "done" not in stages
    assert comp._outs.get("download") in (None, "")


def test_cancelled_before_start_no_output():
    """入口即取消：直接返回，无任何事件、无输出（不推 selected）。"""
    cands = [_cand("t1", "范本A", [{"key": "k1", "fill_mode": "llm"}])]
    comp = _make_comp(cands, {"t1": _dl("d1")}, canceled=True)
    evs = _run(comp)
    assert evs == []
    assert comp._outs.get("download") in (None, "")
```

注意：上面 `_make_comp` 中有一行历史残留式的 `asyncio.coroutine` 兼容写法会被紧跟其后的 `_sel` 覆盖——**实现时直接删掉 `comp._select_templates = asyncio.coroutine(...)` 与 `if hasattr(...)` 两行**，只保留 `_sel` 闭包版本；`_canvas_retrieve` 同理删除，仅保留 `fake_retrieve_all`（在 `_run` 中 monkeypatch 模块级 `executor.retrieve_all_shared`）。清理后的 `_make_comp` 以最终可运行为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_events.py -v`
Expected: FAIL — `TemplateFill` 无 `_event_queue` 属性（AttributeError）

- [ ] **Step 3: 实现事件推送**

`agent/component/template_fill.py`：

3a. 模块级加取消哨兵（放在 `_FILL_CONCURRENCY` 之后）：

```python
class _FillCancelled(Exception):
    """画布取消中断填写：不落 failed 事件，由 invoke 统一推 cancelled。"""
```

3b. `TemplateFill` 类加 `__init__`（`component_name` 定义之后）：

```python
    def __init__(self, canvas, id, param: ComponentParamBase):
        super().__init__(canvas, id, param)
        # 进度事件队列：canvas.run 心跳循环 drain 后经 decorate 变同名 SSE
        # （FanOut 同款机制；canvas drain 已泛化为任意带 _event_queue 的组件）
        self._event_queue: asyncio.Queue = asyncio.Queue()
```

3c. 加推送 helper（`_begin_fields` 方法前）：

```python
    def _push_progress(self, data: dict) -> None:
        self._event_queue.put_nowait({"event": "template_fill_progress", "data": data})
```

3d. `_fill_one` 签名加 `on_progress=None`，产值调用透传：

```python
    async def _fill_one(self, tenant_id: str, cand: dict, chunks_by_key: dict, query: str,
                        begin_fields: dict, user_file_text: str, sem: asyncio.Semaphore,
                        on_progress=None
                        ) -> tuple[dict, dict, int]:
```

```python
        generated, missing = await executor.generate_values(
            tenant_id, llm_placeholders, llm_chunks, background, sem=sem,
            on_progress=on_progress)
```

`_fill_one` 的 return 改为补齐 url/name（与 Message._with_download_url 同款规则）：

```python
        filled = sum(1 for s in cell_status.values() if s == "filled")
        return ({
            "doc_id": doc_id, "filename": filename,
            "mime_type": _MIME_BY_TYPE.get(ext, "application/octet-stream"),
            "size": len(out),
            "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}",
            "name": filename}, cell_status, filled)
```

3e. `_invoke_async` 改造——选型后推 selected：

```python
        chosen = await self._select_templates(tenant_id, candidates, query)
        self._push_progress({"stage": "selected", "templates": [
            {"template_id": c["template_id"], "name": c["name"],
             "slot_count": len(c["_placeholders"])} for c in chosen]})
```

原「③④ 多范本并行填写」gather 段替换为（`_fill_and_notify` 包装：filling/filled/failed 事件在范本完成时即时推送，不等其他范本）：

```python
        sem = asyncio.Semaphore(_FILL_CONCURRENCY)

        async def _fill_and_notify(cand: dict, chunks: dict):
            tid, name = cand["template_id"], cand["name"]
            if self.check_if_canceled("TemplateFill filling"):
                raise _FillCancelled()
            llm_total = sum(1 for it in cand["_placeholders"]
                            if executor._norm_fill_mode(it) == "llm" and it.get("key"))
            self._push_progress({"stage": "filling", "template_id": tid, "name": name,
                                 "done": 0, "total": llm_total})

            def _on_gen_progress(done: int, total: int):
                self._push_progress({"stage": "filling", "template_id": tid,
                                     "name": name, "done": done, "total": total})

            try:
                dl, cell_status, filled = await self._fill_one(
                    tenant_id, cand, chunks, query, begin_fields,
                    user_file_text, sem, on_progress=_on_gen_progress)
            except _FillCancelled:
                raise
            except Exception as e:
                logger.warning("TemplateFill %s fill failed: %s", tid, e)
                self._push_progress({"stage": "failed", "template_id": tid,
                                     "name": name, "error": str(e)})
                return (cand, None, {}, 0, e)
            self._push_progress({"stage": "filled", "template_id": tid,
                                 "name": name, "download": dl})
            return (cand, dl, cell_status, filled, None)

        results = await asyncio.gather(
            *[_fill_and_notify(cand, chunks)
              for cand, chunks in zip(chosen, chunks_list)],
            return_exceptions=True)

        if self.check_if_canceled("TemplateFill after gather") or any(
                isinstance(r, _FillCancelled) for r in results):
            self._push_progress({"stage": "cancelled"})
            return
```

原汇总循环适配新结果结构（`(cand, dl, cell_status, filled, err)` 五元组；`dl is None` 即 failed，failed 事件已推，汇总行简化）：

```python
        downloads: list[dict] = []
        summary_lines: list[str] = []
        for cand, res in zip(chosen, results):
            if isinstance(res, BaseException):
                summary_lines.append(f"《{cand['name']}》：填写失败（{res}）。")
                continue
            _cand, dl, _cell_status, filled, _err = res
            if dl is None:
                summary_lines.append(f"《{cand['name']}》：填写失败。")
                continue
            downloads.append(dl)
            total = len(cand["_placeholders"])
            summary_lines.append(
                f"《{cand['name']}》：共 {total} 个填写点，AI 填充 {filled} 个，"
                f"{total - filled} 个未检索到值已留空。")
```

`set_output("content", ...)` 之后、`logger.info` 之前加：

```python
        self._push_progress({"stage": "done"})
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `uv run --no-sync pytest test/test_template_fill_events.py test/test_template_fill_executor.py test/test_template_fill_tool.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add agent/component/template_fill.py test/test_template_fill_events.py
git commit -m "feat(template-fill): 画布节点推送 5 类进度事件（selected/filling/filled/failed/done|cancelled）"
```

---

### Task 3: canvas drain 泛化

**Files:**
- Modify: `agent/canvas.py:624-634`（心跳 drain）与 `:656-664`（批后 drain）
- Test: `test/test_canvas_event_drain.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `test/test_canvas_event_drain.py`）

```python
"""canvas._drain_component_events 单测：drain 泛化对任意带 _event_queue 的组件生效，
FanOut 行为不变，无队列组件跳过，range 收敛。"""
from types import SimpleNamespace

from agent.canvas import Canvas


def _canvas_with(components):
    canvas = Canvas.__new__(Canvas)  # 跳过 __init__（DSL/LLM 依赖），仅测 drain 逻辑
    canvas.path = [f"n{i}" for i in range(len(components))]
    canvas.get_component_obj = lambda nid: components[int(nid[1:])]
    return canvas


def _fake_decorate(event, data):
    return {"event": event, "message_id": "m1", "task_id": "t1", "data": data}


def _make_cpn(name, events=None):
    import asyncio
    cpn = SimpleNamespace(component_name=name)
    if events is not None:
        q = asyncio.Queue()
        for e in events:
            q.put_nowait(e)
        cpn._event_queue = q
    return cpn


def test_drain_forwards_all_component_queues():
    canvas = _canvas_with([
        _make_cpn("Begin"),
        _make_cpn("TemplateFill",
                  [{"event": "template_fill_progress", "data": {"stage": "selected"}}]),
        _make_cpn("FanOut", [{"event": "fanout_meta", "data": {"lane_total": 1}},
                             {"event": "message", "data": {"content": "x"}}]),
    ])
    out = list(canvas._drain_component_events(0, 3, _fake_decorate))
    assert [e["event"] for e in out] == [
        "template_fill_progress", "fanout_meta", "message"]
    assert all(e["message_id"] == "m1" and e["task_id"] == "t1" for e in out)
    assert canvas.get_component_obj("n1")._event_queue.empty()   # 队列被清空
    assert canvas.get_component_obj("n2")._event_queue.empty()


def test_drain_skips_components_without_queue():
    canvas = _canvas_with([_make_cpn("LLM"), _make_cpn("Message")])
    assert list(canvas._drain_component_events(0, 2, _fake_decorate)) == []


def test_drain_respects_range():
    canvas = _canvas_with([
        _make_cpn("FanOut", [{"event": "message", "data": {}}]),
        _make_cpn("TemplateFill", [{"event": "template_fill_progress", "data": {}}]),
    ])
    out = list(canvas._drain_component_events(1, 2, _fake_decorate))
    assert [e["event"] for e in out] == ["template_fill_progress"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_canvas_event_drain.py -v`
Expected: FAIL — `Canvas` 无 `_drain_component_events` 属性

- [ ] **Step 3: 实现**。`agent/canvas.py` 类内新增方法（放 `get_files` 之后，约 :1032）：

```python
    def _drain_component_events(self, start: int, end: int, decorate):
        """Drain any component's _event_queue within path[start:end] (FanOut,
        TemplateFill, ...). Generalizes the former FanOut-only gate: any
        component owning an _event_queue gets its events forwarded as SSE."""
        for i in range(start, end):
            cpn_obj = self.get_component_obj(self.path[i])
            eq = getattr(cpn_obj, "_event_queue", None)
            if eq is None:
                continue
            while not eq.empty():
                ev = eq.get_nowait()
                yield decorate(ev["event"], ev["data"])
```

心跳循环 drain（原 :626-634）替换为：

```python
                    for ev in self._drain_component_events(idx, to, decorate):
                        yield ev
                        drained = True
```

批后 drain（原 :657-664）替换为：

```python
            for ev in self._drain_component_events(idx, to, decorate):
                yield ev
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `uv run --no-sync pytest test/test_canvas_event_drain.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add agent/canvas.py test/test_canvas_event_drain.py
git commit -m "refactor(canvas): 事件 drain 泛化到任意带 _event_queue 的组件（FanOut 行为不变）"
```

---

### Task 4: 前端进度状态归约模块（纯函数 + 单测）

**Files:**
- Create: `web/src/hooks/template-fill-stream.ts`
- Test: `web/src/hooks/__tests__/template-fill-stream.test.ts`（新建）

- [ ] **Step 1: 写失败测试**（新建 `web/src/hooks/__tests__/template-fill-stream.test.ts`）

```ts
import { applyTemplateFillEvent, IStreamAcc } from '../template-fill-stream';

describe('applyTemplateFillEvent', () => {
  it('selected 重置范本卡片列表', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [
        { template_id: 't1', name: '范本A', slot_count: 3 },
        { template_id: 't2', name: '范本B', slot_count: 5 },
      ],
    });
    expect(acc.templateFill?.templates).toEqual([
      { template_id: 't1', name: '范本A', slot_count: 3, status: 'selected' },
      { template_id: 't2', name: '范本B', slot_count: 5, status: 'selected' },
    ]);
    expect(acc.templateFill?.finished).toBeUndefined();
  });

  it('filling 更新进度 done/total', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: '范本A', slot_count: 4 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling', template_id: 't1', name: '范本A', done: 2, total: 4,
    });
    expect(acc.templateFill?.templates[0]).toMatchObject({
      status: 'filling', done: 2, total: 4,
    });
  });

  it('未知 template_id 自动补建卡片（容错）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'filling', template_id: 'tx', name: '范本X', done: 1, total: 2,
    });
    expect(acc.templateFill?.templates).toHaveLength(1);
    expect(acc.templateFill?.templates[0].name).toBe('范本X');
  });

  it('filled 落 download、failed 落 error、done/cancelled 置 finished', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 1 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x', url: '/u', name: 'a.docx' },
    });
    expect(acc.templateFill?.templates[0].status).toBe('filled');
    expect(acc.templateFill?.templates[0].download?.doc_id).toBe('d1');
    applyTemplateFillEvent(acc, { stage: 'done' });
    expect(acc.templateFill?.finished).toBe(true);
  });

  it('selected 在中途重复到达时重置（新一轮运行容错）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, { stage: 'selected', templates: [{ template_id: 't1', name: 'A' }] });
    applyTemplateFillEvent(acc, { stage: 'done' });
    applyTemplateFillEvent(acc, { stage: 'selected', templates: [{ template_id: 't9', name: 'B' }] });
    expect(acc.templateFill?.templates).toHaveLength(1);
    expect(acc.templateFill?.templates[0].template_id).toBe('t9');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts --no-cache`
Expected: FAIL — Cannot find module '../template-fill-stream'

- [ ] **Step 3: 实现模块**（新建 `web/src/hooks/template-fill-stream.ts`）

```ts
// TemplateFill 范本填写进度：SSE 事件（template_fill_progress）→ 渲染状态的纯归约。
// 类型与归约独立成模块，供 use-send-message.ts（累积）与渲染组件（展示）共用。
export interface ITemplateFillDownload {
  doc_id: string;
  filename: string;
  mime_type: string;
  size?: number;
  url?: string;
  name?: string;
}

export interface ITemplateFillTemplate {
  template_id: string;
  name: string;
  slot_count?: number;
  done?: number;
  total?: number;
  status: 'selected' | 'filling' | 'filled' | 'failed';
  download?: ITemplateFillDownload;
  error?: string;
}

export interface ITemplateFillState {
  templates: ITemplateFillTemplate[];
  finished?: boolean;
}

/** use-send-message 的 streamAccRef 与本模块解耦的最小结构约束 */
export interface IStreamAcc {
  templateFill?: ITemplateFillState;
}

export interface ITemplateFillEvent {
  stage: 'selected' | 'filling' | 'filled' | 'failed' | 'done' | 'cancelled';
  template_id?: string;
  name?: string;
  slot_count?: number;
  done?: number;
  total?: number;
  download?: ITemplateFillDownload;
  error?: string;
  templates?: Array<{ template_id: string; name: string; slot_count?: number }>;
}

/** O(1) 事件归约：就地更新 acc.templateFill（与 hook 的增量累积模式一致） */
export function applyTemplateFillEvent(acc: IStreamAcc, d: ITemplateFillEvent): void {
  if (!acc.templateFill) {
    acc.templateFill = { templates: [] };
  }
  const tf = acc.templateFill;
  if (d.stage === 'selected') {
    tf.templates = (d.templates || []).map((t) => ({
      template_id: t.template_id,
      name: t.name,
      slot_count: t.slot_count,
      status: 'selected' as const,
    }));
    tf.finished = undefined;
    return;
  }
  if (d.stage === 'done' || d.stage === 'cancelled') {
    tf.finished = true;
    return;
  }
  if (!d.template_id) return;
  let t = tf.templates.find((x) => x.template_id === d.template_id);
  if (!t) {
    t = { template_id: d.template_id, name: d.name || '', status: 'filling' };
    tf.templates.push(t);
  }
  if (d.name) t.name = d.name;
  if (d.stage === 'filling') {
    t.status = 'filling';
    t.done = d.done;
    t.total = d.total;
  } else if (d.stage === 'filled') {
    t.status = 'filled';
    t.download = d.download;
  } else if (d.stage === 'failed') {
    t.status = 'failed';
    t.error = d.error;
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts --no-cache`
Expected: 5 个用例全 PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/template-fill-stream.ts web/src/hooks/__tests__/template-fill-stream.test.ts
git commit -m "feat(c-chat): 范本填写进度事件归约模块（纯函数 + 单测）"
```

---

### Task 5: use-send-message.ts 接入事件分支

**Files:**
- Modify: `web/src/hooks/use-send-message.ts`

- [ ] **Step 1: 导入与类型**。文件顶部加：

```ts
import {
  applyTemplateFillEvent,
  ITemplateFillState,
} from './template-fill-stream';
```

`IStreamState` 接口（:109-132）末尾（`fanOutLanes` 之后）加：

```ts
  /** TemplateFill 范本填写进度（template_fill_progress 事件累积） */
  templateFill?: ITemplateFillState;
```

- [ ] **Step 2: 三处 streamAccRef 初始化补 `templateFill: undefined`**：
- `streamAccRef` 初始值（:159-165）
- `resetAnswerList`（:298-306）
- `send` 开头（:341-349）

三处的对象字面量各加一行 `templateFill: undefined,`。

- [ ] **Step 3: SSE 事件分支**。在 `if (val?.event === 'fanout_meta') {...}` 块（:504 结束）之后插入：

```ts
                if (val?.event === 'template_fill_progress') {
                  applyTemplateFillEvent(streamAccRef.current, val.data);
                }
```

（归约内部 O(1)、就地修改 acc，rAF flush 的 `setStreamState({ ...acc })` 浅拷贝会带出新引用 —— `templateFill` 对象引用不变但 React 以外层 streamState 新对象触发重渲，渲染层直接读字段即可。）

- [ ] **Step 4: 类型检查 + 手动冒烟**

Run: `cd web && npx tsc --noEmit -p tsconfig.json`
Expected: 无新增错误（存量错误忽略，只确认本次改动文件无错）

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/use-send-message.ts
git commit -m "feat(c-chat): use-send-message 消费 template_fill_progress SSE 事件"
```

---

### Task 6: 共享渲染组件 TemplateFillProgress

**Files:**
- Create: `web/src/pages/c-chat/template-fill-progress.tsx`

- [ ] **Step 1: 新建组件**（无独立单测——纯展示，逻辑已在 Task 4 归约层覆盖；由 Task 7/9 集成验证）

```tsx
// 范本填写进度卡片：c-chat 对话与 flow AI 对话区共用（设计 3.2「逻辑同构」的落地）。
// selected → 范本卡片行（填写点徽标）；filling → 进度行；filled → 即时下载条（不等其他范本）；
// failed → 降级文案行。全部文案中文，不走 i18n。
import { Download, FileText, Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';
import type {
  ITemplateFillDownload,
  ITemplateFillState,
} from '@/hooks/template-fill-stream';

export default function TemplateFillProgress({
  state,
  onPreview,
  extraAction,
}: {
  state?: ITemplateFillState;
  /** 成稿点击预览（c-chat 传 setPreviewDoc；不传则文件名为纯文本） */
  onPreview?: (dl: ITemplateFillDownload) => void;
  /** 条目右侧附加动作（flow 传「存为流程版本」按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
}) {
  if (!state?.templates?.length) return null;
  return (
    <div className="mt-2 space-y-1.5">
      {state.templates.map((t) => {
        if (t.status === 'selected') {
          return (
            <div
              key={t.template_id}
              className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]"
            >
              <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
              <span className="truncate">《{t.name}》</span>
              <span className="ml-auto shrink-0 rounded bg-[#EFF4FF] px-1.5 text-[#1a66fb]">
                {t.slot_count ?? 0} 个填写点
              </span>
            </div>
          );
        }
        if (t.status === 'filling') {
          return (
            <div
              key={t.template_id}
              className="flex items-center gap-2 px-3 py-2 text-xs text-[#525252]"
            >
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[#1a66fb]" />
              《{t.name}》填写中 {t.done ?? 0}/{t.total ?? 0}
            </div>
          );
        }
        if (t.status === 'failed') {
          return (
            <div key={t.template_id} className="px-3 py-2 text-xs text-[#FAAD14]">
              《{t.name}》填写失败（{t.error || '未知原因'}），其余范本不受影响。
            </div>
          );
        }
        const dl = t.download;
        if (!dl) return null;
        return (
          <div
            key={t.template_id}
            className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]"
          >
            {onPreview ? (
              <button
                className="flex min-w-0 items-center gap-2 text-left transition-colors hover:text-[#1a66fb]"
                onClick={() => onPreview(dl)}
              >
                <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                <span className="max-w-[280px] truncate">
                  {dl.filename || dl.name || '成稿'}
                </span>
              </button>
            ) : (
              <span className="flex min-w-0 items-center gap-2">
                <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                <span className="max-w-[280px] truncate">
                  {dl.filename || dl.name || '成稿'}
                </span>
              </span>
            )}
            {extraAction?.(dl)}
            <a
              href={dl.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex shrink-0 items-center gap-1 text-[#525252] transition-colors hover:text-[#000000]"
            >
              <Download className="h-3.5 w-3.5" strokeWidth={2} />
              下载
            </a>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json`
Expected: 新文件无错误

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/template-fill-progress.tsx
git commit -m "feat(c-chat): 范本填写进度卡片共用组件"
```

---

### Task 7: c-chat index.tsx 流式渲染接入

**Files:**
- Modify: `web/src/pages/c-chat/index.tsx`

- [ ] **Step 1: 导入组件**（`import ReviewPanel ...` 附近，:55）：

```ts
import TemplateFillProgress from './template-fill-progress';
```

- [ ] **Step 2: 流式气泡内渲染**。定位 assistant 消息渲染块内的骨架屏注释（约 :2539）：

```
{/* Skeleton: streaming indicator */}
```

在该注释所在 JSX 元素**之后、同一父容器内**插入：

```tsx
                                {streaming && (
                                  <TemplateFillProgress
                                    state={streamState.templateFill}
                                    onPreview={(dl) =>
                                      setPreviewDoc({
                                        fileId: dl.doc_id || '',
                                        fileName:
                                          dl.filename || dl.name || '成稿',
                                      })
                                    }
                                  />
                                )}
```

说明：`streaming`（= `isLast && sendLoading`，:2141）为 true 期间展示实时进度卡片；流结束后卡片卸载，成稿由既有 `msg.downloads` 渲染（:2404）接管，内容一致无重复。

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json`
Expected: 改动文件无错误

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/index.tsx
git commit -m "feat(c-chat): 对话流式气泡实时渲染范本填写进度与成稿"
```

---

### Task 8: flow 后端——版本 content 端点 + upload source 扩展

**Files:**
- Modify: `api/apps/restful_apis/flow_app.py`
- Test: `test/test_flow_version_source.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `test/test_flow_version_source.py`）

```python
"""flow 版本来源白名单纯逻辑单测（upload_version 的 source 表单字段）。"""


def test_normalize_version_source():
    from api.apps.restful_apis.flow_app import _normalize_version_source
    assert _normalize_version_source(None) == "manual_upload"
    assert _normalize_version_source("") == "manual_upload"
    assert _normalize_version_source("manual_upload") == "manual_upload"
    assert _normalize_version_source("ai_template_fill") == "ai_template_fill"
    assert _normalize_version_source("hacker") == "manual_upload"  # 白名单外回退


def test_version_content_max_constant():
    from api.apps.restful_apis.flow_app import _VERSION_CONTENT_MAX
    assert _VERSION_CONTENT_MAX == 20000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_flow_version_source.py -v`
Expected: FAIL — ImportError: cannot import name `_normalize_version_source`

- [ ] **Step 3: 实现**。`api/apps/restful_apis/flow_app.py`：

3a. import 区（`from api.utils.doc_utils import ...` 附近）加：

```python
from api.db.services.file_service import FileService
```

3b. `_safe_filename` 函数之后加白名单与归一 helper：

```python
# 版本来源白名单：manual_upload=发起/负责人手动上传；ai_template_fill=AI 范本填写成稿落版本
_VERSION_SOURCES = ("manual_upload", "ai_template_fill")


def _normalize_version_source(raw) -> str:
    return raw if raw in _VERSION_SOURCES else "manual_upload"
```

3c. `upload_version`（:277）中 `file_name = _safe_filename(file.filename)` 之后插入：

```python
        # AI 面板「存为流程版本」与手动上传共用本端点；source 白名单防脏标注
        form = await request.form
        source = _normalize_version_source(form.get("source"))
```

并把 `add_version(...)` 调用的第 6 参 `"manual_upload"` 改为 `source`：

```python
        version = FlowVersionService.add_version(
            flow, object_name, file_name, file.mimetype or "", len(blob),
            source, current_user.id,
        )
```

3d. 新增 content 端点（放在 `download_version` 之后、批注段之前）：

```python
# ── 5.1 版本内容纯文本（三角色可读；模板填写证据注入等轻量用途） ──
_VERSION_CONTENT_MAX = 20000


@manager.route("/flow/<flow_id>/version/<version_id>/content", methods=["GET"])  # noqa: F821
@login_required
async def version_content(flow_id: str, version_id: str):
    """版本文本提取（服务端化）：供流程 AI 对话附带当前版本作填写证据。
    解析失败返回空文本 + warning，不阻断（前端回退纯 KB 检索）。"""
    try:
        flow = _require_participant(_flow_dict(flow_id))
        version = next(
            (v for v in FlowVersionService.list_by_flow(flow_id) if v["id"] == version_id),
            None)
        if not version:
            return _err("版本不存在", 404)
        blob = await thread_pool_exec(
            settings.STORAGE_IMPL.get, _bucket_of(flow), version["file_path"])
        if not blob:
            return get_json_result(data={"content": ""})

        def _extract_text() -> str:
            try:
                text = FileService.parse(version["file_name"], blob, True, current_user.id)
            except Exception:
                logger.warning("flow version content parse failed: %s",
                               version["file_name"], exc_info=True)
                return ""
            return (text or "")[:_VERSION_CONTENT_MAX]

        text = await thread_pool_exec(_extract_text)
        return get_json_result(data={"content": text})
    except LookupError as e:
        return _err(str(e), 404)
    except PermissionError as e:
        return _err(str(e), 403)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `uv run --no-sync pytest test/test_flow_version_source.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/apps/restful_apis/flow_app.py test/test_flow_version_source.py
git commit -m "feat(flow): 版本内容文本端点 + 版本上传 source 扩展（ai_template_fill）"
```

---

### Task 9: flow 前端——进度上报、轻量附带版本、存为流程版本

**Files:**
- Modify: `web/src/pages/c-chat/flow/flow-types.ts`
- Modify: `web/src/services/flow-service.ts`
- Modify: `web/src/pages/c-chat/flow/flow-ai-panel.tsx`
- Modify: `web/src/pages/c-chat/flow/flow-detail.tsx`

- [ ] **Step 1: flow-types.ts** 两处：
`FlowVersionItem.source`（:29）扩展：

```ts
  source: 'manual_upload' | 'ai_output' | 'ai_template_fill';
```

`FlowLiveChat`（:68-72）加字段：

```ts
export interface FlowLiveChat {
  instruction: string;
  response: string;
  busy: boolean;
  /** 范本填写进度（template_fill_progress 事件累积，随流式上报） */
  templateFill?: ITemplateFillState;
}
```

顶部加 `import type { ITemplateFillState } from '@/hooks/template-fill-stream';`

- [ ] **Step 2: flow-service.ts** 加 API（`downloadVersionBlob` 之后）：

```ts
/** 版本文件纯文本（服务端提取，20000 字截断；解析失败返回空串）。
 * 供流程 AI 对话附带当前版本作范本填写证据，替代每次整份 blob 上传。 */
export async function getFlowVersionContent(
  flowId: string,
  versionId: string,
): Promise<string> {
  const res = await apiFetch<{ content: string }>(
    `/flow/${flowId}/version/${versionId}/content`,
  );
  return res?.content || '';
}
```

- [ ] **Step 3: FlowAiPanel（flow-ai-panel.tsx）** 四处：

3a. import 区加：

```ts
import type { ITemplateFillState } from '@/hooks/template-fill-stream';
import { getFlowVersionContent } from '@/services/flow-service';
```

3b. `contentRef` 声明（:110）之后加进度快照 ref：

```ts
  // 范本填写进度快照：流式期间随 onLiveChatChange 上报；send() 结束 hook 会清空
  // streamState，用 ref 兜住供完成后（completed 态）继续展示成稿条
  const templateFillRef = useRef<ITemplateFillState | undefined>(undefined);
  useEffect(() => {
    if (streamState.templateFill) {
      templateFillRef.current = streamState.templateFill;
    }
  }, [streamState.templateFill]);
```

3c. 「对话状态上报」useEffect（:173-194）：busy 分支 payload 加 `templateFill: streamState.templateFill`；completed 的 `next` 对象加 `templateFill: templateFillRef.current`。

3d. `handleSend` 两处改动：

- 开头清空（`contentRef.current = '';` 旁）加 `templateFillRef.current = undefined;`
- 附带版本段（`if (files.length === 0 && attachFile && version) {...}`，:368-376）整体替换为轻量通道 + 回退：

```ts
      const docs = uploadedDocsRef.current;
      let files: unknown[] = docs;
      if (files.length === 0 && attachFile && version) {
        // 轻量通道：服务端提取版本纯文本 → 小 txt 文件上传（免每次整份 docx
        // blob 上传 + 画布重复解析）；失败静默回退原 uploadVersionAsDocument
        try {
          const text = await getFlowVersionContent(flowId, version.id);
          if (text) {
            const fd = new FormData();
            fd.append(
              'file',
              new File([text], `${version.file_name}.txt`, {
                type: 'text/plain',
              }),
            );
            const resp = await fetch('/api/v1/documents/upload', {
              method: 'POST',
              headers: {
                Authorization: localStorage.getItem('Authorization') || '',
              },
              body: fd,
            });
            const result = await resp.json();
            if (result.code === 0 && result.data) {
              const d = Array.isArray(result.data)
                ? result.data[0]
                : result.data;
              // 传完整上传响应对象（含 mime_type）：canvas.get_files_async 依赖
              if (d?.id) files = [d];
            }
          }
        } catch {
          // 轻通道失败 → 走下方回退
        }
        if (files.length === 0) {
          try {
            const doc = await uploadVersionAsDocument();
            if (doc) files = [doc];
          } catch {
            // 附件上传失败不阻断发送，降级为无文件提问
            files = [];
          }
        }
      }
```

- [ ] **Step 4: flow-detail.tsx 存为流程版本**：

4a. import 区加：

```ts
import { uploadFlowVersion } from '@/services/flow-service';
import TemplateFillProgress from '../template-fill-progress';
import type { ITemplateFillDownload } from '@/hooks/template-fill-stream';
```

（若 `queryClient` 未引入则补 `useQueryClient` 的 react-query import。）

4b. 组件内（`liveChat` state 声明 :90 附近）加：

```ts
  // AI 范本填写成稿「存为流程版本」：成稿 blob（agents/download）→ flow 版本
  const [savingDocIds, setSavingDocIds] = useState<
    Record<string, 'saving' | 'saved' | 'error'>
  >({});
  const saveDownloadAsVersion = useCallback(
    async (dl: ITemplateFillDownload) => {
      const docId = dl.doc_id || '';
      if (!docId || savingDocIds[docId] === 'saving') return;
      setSavingDocIds((p) => ({ ...p, [docId]: 'saving' }));
      try {
        const resp = await fetch(dl.url || '', {
          headers: { Authorization: localStorage.getItem('Authorization') || '' },
        });
        if (!resp.ok) throw new Error(`download failed ${resp.status}`);
        const blob = await resp.blob();
        const fd = new FormData();
        fd.append(
          'file',
          new File([blob], dl.filename || '成稿.docx', {
            type: dl.mime_type || 'application/octet-stream',
          }),
        );
        fd.append('source', 'ai_template_fill');
        await uploadFlowVersion(flowId, fd);
        setSavingDocIds((p) => ({ ...p, [docId]: 'saved' }));
        await queryClient.invalidateQueries({
          queryKey: ['flow-detail', flowId],
        });
      } catch {
        setSavingDocIds((p) => ({ ...p, [docId]: 'error' }));
      }
    },
    [flowId, queryClient, savingDocIds],
  );
```

4c. `ConversationView`（:730）签名与 live 渲染扩展：

```tsx
function ConversationView({
  chats,
  live,
  extraAction,
}: {
  chats: FlowAiChatItem[];
  live: FlowLiveChat | null;
  /** 成稿条目附加动作（存为流程版本按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
}) {
```

（顶部补 `import type { ReactNode } from 'react';`）

`{live && (...)}` 块内、流式回复气泡 `</div>` 之后追加：

```tsx
          {live.templateFill?.templates?.length ? (
            <div className="max-w-[90%]">
              <TemplateFillProgress
                state={live.templateFill}
                extraAction={(dl) => extraAction?.(dl)}
              />
            </div>
          ) : null}
```

4d. 调用处（:416 `<ConversationView ... />`）传 prop：

```tsx
            <ConversationView
              chats={data.ai_chats ?? []}
              live={liveChat}
              extraAction={(dl) => {
                const st = savingDocIds[dl.doc_id || ''];
                return (
                  <button
                    disabled={st === 'saving' || st === 'saved'}
                    onClick={() => saveDownloadAsVersion(dl)}
                    className={`ml-2 shrink-0 rounded px-2 py-0.5 transition-colors ${
                      st === 'saved'
                        ? 'bg-[#F0F9EB] text-[#67C23A]'
                        : st === 'error'
                          ? 'bg-[#FDE9E9] text-red-500'
                          : 'border border-[#BFD3F5] bg-[#F0F5FF] text-[#1a66fb] hover:bg-[#E3EDFF]'
                    }`}
                  >
                    {st === 'saving'
                      ? '保存中…'
                      : st === 'saved'
                        ? '已存版本'
                        : st === 'error'
                          ? '失败重试'
                          : '存为流程版本'}
                  </button>
                );
              }}
            />
```

- [ ] **Step 5: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json`
Expected: 改动文件无错误

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-types.ts web/src/services/flow-service.ts web/src/pages/c-chat/flow/flow-ai-panel.tsx web/src/pages/c-chat/flow/flow-detail.tsx
git commit -m "feat(flow): AI 面板范本填写进度上报、版本轻量注入、成稿存为流程版本"
```

---

### Task 10: 全量回归 + 文档登记

**Files:**
- Modify: `CHANGE.md`、`CLAUDE.md`（参考文档表）

- [ ] **Step 1: 后端全量回归**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py test/test_template_fill_events.py test/test_canvas_event_drain.py test/test_flow_version_source.py test/test_template_fill_tool.py test/test_template_fill_utils.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 前端类型 + 单测**

Run: `cd web && npx tsc --noEmit -p tsconfig.json && npx jest src/hooks/__tests__/template-fill-stream.test.ts --no-cache`
Expected: 无错误、5 用例 PASS

- [ ] **Step 3: CHANGE.md 增量条目**（置顶追加）：

```markdown
## 2026-09-08 模板填写进度流式 + 流程页签适配

**主题**：TemplateFill 画布节点全程零反馈 → 5 类进度事件实时渲染（C端对话 + 流程 AI 面板共用）；流程场景版本文本轻量注入 + 成稿一键落流程版本时间线。

**核心变更**：
- 后端：`generate_values` 批次进度回调 `on_progress(done,total)`；TemplateFill 组件 `_event_queue` 推 selected/filling/filled/failed/done|cancelled（filled 即时下载条含 url/name，单范本失败不中断）；canvas drain 泛化（任意带 `_event_queue` 的组件，FanOut 行为不变）；flow 新端点 `GET /flow/<id>/version/<vid>/content`（三角色可读、20000 字截断、解析失败回空串）+ `POST /flow/<id>/version` 支持 `source=ai_template_fill`
- 前端：`template-fill-stream.ts` 事件归约纯函数 + `use-send-message.ts` SSE 分支；`template-fill-progress.tsx` 共用进度卡片（c-chat 流式气泡 / flow 对话区）；FlowAiPanel 附带版本改走轻量文本通道（失败回退整份上传）；flow 成稿条「存为流程版本」按钮（blob→uploadFlowVersion→时间线刷新）
- 设计文档：docs/superpowers/specs/2026-09-08-template-fill-flow-design.md；实施计划：docs/superpowers/plans/2026-09-08-template-fill-flow.md

**遗留**：容器内端到端冒烟（SSE 事件到达 + 成稿落版本）待部署后验证
```

- [ ] **Step 4: CLAUDE.md 参考文档表**加一行（「模板填写系统设计」行之后）：

```markdown
| 模板填写进度流式设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-08-template-fill-flow-design.md` | ★ TemplateFill 进度流式（5 类事件经 FanOut 同款管道）+ 流程页签适配（版本文本轻量注入 + 成稿存为流程版本）；实施计划 docs/superpowers/plans/2026-09-08-template-fill-flow.md |
```

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 模板填写进度流式 + 流程适配迭代记录与参考表登记"
```

---

## 自审记录（写计划时已核对）

1. **Spec 覆盖**：设计 3.1（Task 1/2/3 + 取消检查）、3.2（Task 4/5/6/7/9）、3.3（Task 8 content 端点 + Task 9 轻量通道）、3.4（Task 8 source 扩展 + Task 9 存版本按钮）、§5 错误处理（failed 降级/cancelled/解析回空串/保存失败按钮可重试）、§6 测试（各任务内 TDD）、§7 文件清单全覆盖 —— 无缺口。
2. **占位符扫描**：无 TBD/TODO；Task 2 Step 1 中两处冗余 stub 写法已显式标注「实现时删除」，以最终可运行为准。
3. **类型一致性**：`ITemplateFillState/ITemplateFillTemplate/ITemplateFillDownload` 定义于 `template-fill-stream.ts`（Task 4），被 hook（Task 5）、渲染组件（Task 6）、flow-types（Task 9）按同名引用；事件 data 键与设计 §3.1 契约表逐字对齐（`template_fill_progress`/`stage`/`template_id`/`slot_count`/`done`/`total`/`download`/`error`）。
4. **已知取舍**（设计批准范围内）：取消为检查点式（批次 LLM 在途调用完成后丢弃结果，不中断在途请求）——`generate_values` 批次经共享信号量并发，强行 cancel 需重构 gather 语义，收益不成比例；成稿预览仅在 c-chat 提供（flow 对话区无 ReviewPanel 挂载点，设计 §4 已注明 flow 场景 ④ 为核心）。
