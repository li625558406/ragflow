# 范本填写取消链路补全 + 检索并发限流 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端停止真正取消服务端填写；取消检查点下沉到批次/检索粒度；画布检索并发限流（env 可调，默认 2）。

**Architecture:** 复用现有 Redis `{task_id}-cancel` 取消键与 `check_if_canceled` 轮询；`should_cancel` 回调穿透 executor 三函数；SSE envelope 已含 task_id，前端捕获后经新 REST 端点写取消键。

**Tech Stack:** Python asyncio (pytest), React/TS (jest 临时配置 `.scratch/jest.template-fill.config.js`)

**测试命令约定：** Python 一律 `uv run --no-sync pytest`；前端 jest 用 `.scratch/jest.template-fill.config.js`；tsc 全仓存量错误忽略，只看改动文件。

---

### Task 1: executor 取消原语 + 信号量注入

**Files:**
- Modify: `rag/svr/template_fill/executor.py`
- Test: `test/test_template_fill_executor.py`

- [ ] **Step 1: 写失败测试**（追加到 `test/test_template_fill_executor.py`）：

```python
import pytest

from rag.svr.template_fill import executor as tf_executor


class GenerateCancelled(Exception):
    pass


def test_generate_values_should_cancel_raises(test_tenant_id, make_placeholders, fake_chat_mdl):
    """should_cancel 命中：抛 GenerateCancelled，不在途批次结果不落返回。"""
    # make_placeholders/fake_chat_mdl 为该文件现有 fixture/工厂；按文件现状复用，
    # 若命名不同以现状为准（目标：让 generate_values 真正跑 LLM 批次）。
    calls = {"n": 0}

    def cancel_after_first():
        calls["n"] += 1
        return calls["n"] > 1

    phs = make_placeholders(6)  # 2 批（batch_size=3）
    with pytest.raises(tf_executor.GenerateCancelled):
        tf_executor.asyncio_run(
            tf_executor.generate_values(
                test_tenant_id, phs, {}, should_cancel=cancel_after_first))


def test_generate_values_no_cancel_regression(test_tenant_id, make_placeholders, fake_chat_mdl):
    """should_cancel=None：行为与现状一致。"""
    values, missing = tf_executor.asyncio_run(
        tf_executor.generate_values(test_tenant_id, make_placeholders(3), {}))
    assert isinstance(values, dict)


def test_retrieve_all_shared_should_cancel(test_tenant_id, monkeypatch):
    async def fake_retrieval(*a, **k):
        raise AssertionError("should not reach retrieval after cancel")

    monkeypatch.setattr(tf_executor, "retrieve_slot", fake_retrieval)
    placeholders = [[{"key": "k1", "name": "n1", "fill_mode": "llm"}]]
    import asyncio as _aio
    with pytest.raises(tf_executor.GenerateCancelled):
        _aio.run(tf_executor.retrieve_all_shared(
            "t", placeholders, ["kb1"], should_cancel=lambda: True))
```

（注：`tf_executor.asyncio_run` 不存在则直接 `asyncio.run`；fixture 以该测试文件现状为准，实现者负责让测试真实可跑，允许调整工厂写法但**不允许弱化断言语义**。）

- [ ] **Step 2: 跑测试确认失败**：`uv run --no-sync pytest test/test_template_fill_executor.py -k cancel -v` → FAIL（无 GenerateCancelled 属性）

- [ ] **Step 3: 实现**（executor.py）：

```python
class GenerateCancelled(Exception):
    """should_cancel 命中时抛出：调用方（组件/B端管道）捕获后各自收口。"""


async def generate_values(..., on_progress=None, should_cancel=None) -> tuple[dict, set]:
    ...
    async def _one(batch: list[dict]):
        user_msg = _build_msg(batch)
        async with sem:
            if should_cancel and should_cancel():
                raise GenerateCancelled()
            ans = await mdl.async_chat(...)
        return batch, _extract_json(ans)

    total = sum(len(b) for b in batches)
    done_slots = 0
    results = []
    try:
        for fut in asyncio.as_completed([_one(b) for b in batches]):
            results.append(await fut)
            if should_cancel and should_cancel():
                raise GenerateCancelled()
            if on_progress:
                done_slots += len(results[-1][0])
                try:
                    on_progress(done_slots, total)
                except Exception:
                    logger.exception("generate_values on_progress callback failed")
    except GenerateCancelled:
        logger.info("generate_values cancelled: %d/%d batches done", len(results), len(batches))
        raise
    ...
```

`retrieve_all_shared` 与 `_retrieve_all` 同法：签名加 `should_cancel=None, sem=None`；`_one` 获信号量后检查抛 GenerateCancelled；`sem = sem or asyncio.Semaphore(RETRIEVAL_CONCURRENCY)`；gather 外层 try/except GenerateCancelled 记日志后 raise。函数 docstring 补参数说明。

- [ ] **Step 4: 跑全部 executor 测试通过**：`uv run --no-sync pytest test/test_template_fill_executor.py -v`

- [ ] **Step 5: Commit**: `feat(template-fill): executor 支持外部取消回调与检索信号量注入`

### Task 2: 组件接线（should_cancel + 检索限流）

**Files:**
- Modify: `agent/component/template_fill.py`
- Test: `test/test_template_fill_events.py`、`test/test_agent_fill_template_component.py`

- [ ] **Step 1: 失败测试**：`test_template_fill_events.py` 加用例——cancel 标志在 filling 中途翻真 → 组件推 `cancelled` 且 `generate_values` 收到 should_cancel（用 fake executor 断言 kwargs）。组件 `__init__` 后设 `cpn._event_queue`（该文件已有模式）。

- [ ] **Step 2: 实现**（template_fill.py）：

```python
import os
_RETRIEVAL_CONCURRENCY = int(os.getenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "2"))
```

`_invoke_async` 中：

```python
        retrieval_sem = asyncio.Semaphore(_RETRIEVAL_CONCURRENCY)
        cancelled = lambda: self.check_if_canceled("TemplateFill retrieval/filling")  # noqa: E731
        chunks_list = await executor.retrieve_all_shared(
            tenant_id, [c["_placeholders"] for c in chosen], kb_ids,
            task_id=f"canvas:{self._id}",
            should_cancel=cancelled, sem=retrieval_sem)
```

`_fill_one` 签名加 `should_cancel=None`，其内 `generate_values` 调用透传；`_fill_and_notify` 调 `_fill_one(..., should_cancel=cancelled)`；`except GenerateCancelled`（`executor.GenerateCancelled`）加入现有 `_FillCancelled` 透传分支（转抛 `_FillCancelled()`）。注释同步更新（删除「批次级取消未实现」类措辞）。

- [ ] **Step 3: 全部相关测试通过**：`uv run --no-sync pytest test/test_template_fill_events.py test/test_agent_fill_template_component.py test/test_template_fill_executor.py -v`

- [ ] **Step 4: Commit**: `feat(template-fill): 画布检索限流(默认2) + 批次级取消接线`

### Task 3: 取消端点

**Files:**
- Modify: `api/db/services/task_service.py`、`api/apps/restful_apis/agent_api.py`
- Test: `test/test_agent_task_cancel.py`（新建，stub+importlib 模式参考 `test/test_flow_version_source.py`）

- [ ] **Step 1: 失败测试**：断言 `cancel_task(task_id)` 调用 `REDIS_CONN.set(f"{task_id}-cancel", "x")`；端点路由存在且 @login_required。
- [ ] **Step 2: 实现**：

task_service.py（has_canceled 旁）：

```python
def cancel_task(task_id):
    try:
        REDIS_CONN.set(f"{task_id}-cancel", "x")
    except Exception as e:
        logging.exception(e)
        return False
    return True
```

agent_api.py（download 端点附近）：

```python
@manager.route("/agents/tasks/<task_id>/cancel", methods=["POST"])  # noqa: F821
@login_required
async def cancel_agent_task(task_id):
    cancel_task(task_id)
    return get_json_result(data=True)
```

（import 补 `from api.db.services.task_service import cancel_task`；`get_json_result` 该文件已有。）

- [ ] **Step 3: 测试通过** + `uv run --no-sync pytest test/test_agent_task_cancel.py -v`
- [ ] **Step 4: Commit**: `feat(agent-api): 新增 POST /agents/tasks/<id>/cancel 写取消键`

### Task 4: 前端停止接线

**Files:**
- Modify: `web/src/hooks/use-send-message.ts`

- [ ] **Step 1: 实现**：

a) 声明 ref（与 eventBufferRef 同区）：

```ts
  // 服务端取消：SSE envelope 每帧带 task_id（canvas.run decorate），停止时写取消键
  const taskIdRef = useRef<string | null>(null);
```

b) SSE 解析处（`const val = JSON.parse(value?.data || '')` 之后、事件分发前）：

```ts
                if (typeof val?.task_id === 'string' && val.task_id) {
                  taskIdRef.current = val.task_id;
                }
```

c) `send` 开头重置处（contentRef 清空附近）加 `taskIdRef.current = null;`

d) `stopOutputMessage`：

```ts
  const stopOutputMessage = useCallback(() => {
    const taskId = taskIdRef.current;
    if (taskId) {
      // fire-and-forget：服务端取消（幂等），失败不阻断本地停止
      fetch(`/api/v1/agents/tasks/${taskId}/cancel`, {
        method: 'POST',
        headers: { Authorization: localStorage.getItem('Authorization') || '' },
      }).catch((e) => console.warn('cancel task failed', e));
    }
    sseRef.current?.abort();
  }, []);
```

- [ ] **Step 2: 验证**：`cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep use-send-message`（零输出）+ `npx eslint src/hooks/use-send-message.ts`
- [ ] **Step 3: Commit**: `feat(c-chat): 停止输出时调用服务端任务取消接口`

### Task 5: 全量回归 + 文档

- [ ] 后端：`uv run --no-sync pytest test/test_template_fill_executor.py test/test_template_fill_events.py test/test_canvas_event_drain.py test/test_flow_version_source.py test/test_template_fill_tool.py test/test_template_fill_utils.py test/test_agent_task_cancel.py -v` 全绿
- [ ] 前端：jest 临时配置跑 `template-fill-stream.test.ts` + tsc/eslint 改动文件零新增
- [ ] CHANGE.md 置顶条目（含实测背景：09:07 卡死事件数据）
- [ ] CLAUDE.md 参考表登记设计文档行
- [ ] Commit: `docs: 取消链路补全迭代记录`

**部署（待用户指示后执行）：** 后端 4 文件 SCP + 重启 + 冒烟；前端 build + 部署；端到端：触发填写→点停止→服务器日志 "has been canceled" 且检索停止。
