# 范本填写后台化与断连重连（方案A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 范本填写执行与 SSE 连接解耦：画布节点确认后委托 tpl_fill_task 后台线程执行，断连/刷新后任务跑完落库，前端轮询恢复实时进度并合成成稿卡。

**Architecture:** 画布 TemplateFill 节点确认环节保持内联不变；确认后为每个选中范本创建 `tpl_fill_task` 行（source='canvas'）并 spawn daemon 线程跑 `executor.execute_task`（与 B端 fill-task 同一条 pipeline）；节点降级为观察者（1.5s 轮询 Redis 快照+DB 状态 → 组装同形 template_fill_progress 事件，新增可选 task_id 字段）；断连后线程独立跑完；前端历史恢复态对带 task_id 的行轮询新端点 `GET /template/fill/fill-task/{id}/progress`，终态本地合成成稿卡（下载/预览走 {tenant}-downloads bucket 桥接）。

**Tech Stack:** Python Quart + Peewee + Redis（Valkey）+ MinIO storage；React 18 + TS + 既有 request 封装。

**设计文档:** `docs/superpowers/specs/2026-09-12-template-fill-detached-task-design.md`

**代码级事实（已核实，实现时以此为准）：**
- 状态机白名单 `_TASK_TRANSITS`（api/db/services/template_fill_service.py:36）目前无 `cancelled` 态，需扩展。
- 项目中**不存在** `tpl_fill:cancel:*` Redis 键契约（executor 的 `should_cancel` 只是回调参数，B端 pipeline 不传）——本计划新建该契约。
- executor 已有 `_merge_param_values`/`_merge_default_values`/`build_values`/`_render_result`，`generate_values` 第 4 参 `params` 即背景信息（节点传 `begin_fields`+「用户需求描述」同构）。
- 节点 `_fill_one`（agent/component/template_fill.py:318-425）的 D−C 收窄、user_file_text 证据插入、直填覆盖、沉淀 override 是 executor 缺口，本计划逐项对齐。
- bucket 桥接：canvas 成稿在 `{tenant_id}-downloads`（/agents/download 契约），fill-task 成稿在 `{template_id}` bucket——用确定性对象名 `tplfill-{task_id}` 拷贝桥接（幂等覆盖，不产生重复对象）。
- `TplFillTaskService.get_owned(task_id, tenant_id)`、`update_status(task_id, cur, nxt, **extra)`（CAS）已存在。
- `REDIS_CONN.set(key, val, exp=秒)`；`REDIS_CONN.get(k)` 返回 None/值。
- 节点已导入 `REDIS_CONN`、`get_uuid`、`settings`、`TplTemplateService/TplTemplateVersionService/_sanitize_filename`（template_fill_service 模块）。
- 前端进度卡 `template-fill-progress.tsx` 文案硬编码中文不走 i18n（文件头注释既有约定）。

**接受的取舍（设计 §8 已确认）：**
- 多范本跨任务共享检索去重（retrieve_all_shared）不再适用——每任务独立检索（B端行为），ES 压力可接受。
- 多范本并行不再受画布 `_FILL_CONCURRENCY` 总闸约束——每任务批次并发 3（executor 既有 GENERATE_CONCURRENCY），任务数为范本个数（个位数）。
- `flow_instance_id` 留空（画布 DSL 不携带流程上下文，字段留作后续）。

**测试环境约束：** 后端测试用 `uv run --no-sync pytest`；前端测试 `cd web && npx jest <file>`。

---

### Task 1: executor 委托能力对齐 + 进度快照 + 取消

**Files:**
- Modify: `api/db/services/template_fill_service.py`（状态机 + cancel_task + find_running）
- Modify: `rag/svr/template_fill/executor.py`（split_canvas_params / 快照 / 收窄 / 证据 / 取消探针）
- Test: `test/test_template_fill_executor.py`（追加用例）

- [ ] **Step 1.1: 写失败测试 —— split_canvas_params 与快照**

在 `test/test_template_fill_executor.py` 末尾追加：

```python
# ── 画布委托：params 保留键拆分 + Redis 进度快照 ─────────────────────


class TestSplitCanvasParams:
    """画布节点塞进 params 的保留键（下划线前缀）必须与背景信息隔离：
    干净 params 才能进 build_retrieval_query / _merge_param_values / LLM 背景。"""

    def test_split_extracts_reserved_keys(self):
        from rag.svr.template_fill.executor import split_canvas_params
        params = {
            "项目名称": "X 项目",
            "_direct_values": {"k1": "v1"},
            "_changed_keys": ["k2"],
            "_retrieve_skip_keys": ["k3"],
            "_user_file_text": "上传文件内容",
        }
        clean, opts = split_canvas_params(params)
        assert clean == {"项目名称": "X 项目"}
        assert opts["direct_values"] == {"k1": "v1"}
        assert opts["changed_keys"] == {"k2"}
        assert opts["retrieve_skip_keys"] == {"k3"}
        assert opts["user_file_text"] == "上传文件内容"

    def test_split_hostile_payloads(self):
        from rag.svr.template_fill.executor import split_canvas_params
        # 对抗：保留键缺失 / 类型乱塞 / params 为 None
        clean, opts = split_canvas_params(None)
        assert clean == {}
        assert opts == {"direct_values": {}, "changed_keys": set(),
                        "retrieve_skip_keys": set(), "user_file_text": ""}
        clean, opts = split_canvas_params({
            "_direct_values": "not-a-dict",       # 标量 → 按空处理
            "_changed_keys": {"k": 1},            # dict → 迭代键
            "_retrieve_skip_keys": [1, None],     # 非字符串项 → str 归一
            "_user_file_text": 42,                # 非字符串 → str()
        })
        assert clean == {}
        assert opts["direct_values"] == {}
        assert opts["changed_keys"] == {"k"}
        assert opts["retrieve_skip_keys"] == {"1", "None"}
        assert opts["user_file_text"] == "42"


class TestProgressSnapshot:
    """快照写失败只告警（Redis 不可用不拖垮填写）；读为纯容错。"""

    def test_write_snapshot_swallows_redis_error(self, monkeypatch):
        from rag.svr.template_fill import executor

        class _Boom:
            def set(self, *a, **kw):
                raise RuntimeError("redis down")

        monkeypatch.setattr(executor, "REDIS_CONN", _Boom())
        # 不抛异常即通过
        executor._write_snapshot("t1", status="generating", done=1, total=3)

    def test_read_snapshot_returns_none_on_error(self, monkeypatch):
        from rag.svr.template_fill import executor

        class _Boom:
            def get(self, k):
                raise RuntimeError("redis down")

        monkeypatch.setattr(executor, "REDIS_CONN", _Boom())
        assert executor.read_progress_snapshot("t1") is None
```

- [ ] **Step 1.2: 运行测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -k "SplitCanvasParams or ProgressSnapshot" -v`
Expected: FAIL（`split_canvas_params` / `_write_snapshot` / `read_progress_snapshot` 不存在）

- [ ] **Step 1.3: executor 实现快照与参数拆分**

在 `rag/svr/template_fill/executor.py`：

① 顶部 import 区确认已有 `from rag.utils.redis_conn import REDIS_CONN`（若无则添加）。

② 在 `GenerateCancelled` 类定义之后添加：

```python
# ── 画布委托任务：params 保留键 + Redis 进度快照 ──────────────────────
# 画布节点把确认产物（直填值/预判变化键）、用户文件证据、检索跳过键以
# 下划线前缀保留键塞进 params 传给 execute_task；干净 params 继续充当
# 背景信息与 param 直取（与 B端表单字段同构）。
_CANVAS_RESERVED_KEYS = ("_direct_values", "_changed_keys",
                         "_retrieve_skip_keys", "_user_file_text")

# 进度快照键与 TTL（24h；终态也写一次，靠 TTL 过期，不主动删）
_PROGRESS_KEY = "tpl_fill_progress:{task_id}"
_PROGRESS_TTL = 24 * 3600
# 画布取消键：节点在画布被停止时为未终态任务写该键，executor 探针命中即中断
_CANCEL_KEY = "tpl_fill:cancel:{task_id}"
_CANCEL_TTL = 3600


def split_canvas_params(params: dict | None) -> tuple[dict, dict]:
    """拆出画布委托塞在 params 里的保留键，返回 (干净 params, opts)。
    前端/画布数据不可信：保留键载荷一律防御式清洗（非 dict/list 按空处理、
    非 str 项 str 归一），不炸 pipeline。"""
    params = params if isinstance(params, dict) else {}
    dv = params.get("_direct_values")
    opts = {
        "direct_values": ({str(k): str(v) for k, v in dv.items()}
                          if isinstance(dv, dict) else {}),
        "changed_keys": {str(k) for k in (params.get("_changed_keys") or [])},
        "retrieve_skip_keys": {str(k) for k in (params.get("_retrieve_skip_keys") or [])},
        "user_file_text": str(params.get("_user_file_text") or ""),
    }
    clean = {k: v for k, v in params.items() if k not in _CANVAS_RESERVED_KEYS}
    return clean, opts


def _write_snapshot(task_id: str, **fields) -> None:
    """写进度快照（累积 values 由调用方传全量）。Redis 故障只告警——
    快照是重连体验增强，不是执行的权威路径（权威在 DB 行）。"""
    import time as _time
    try:
        payload = {"updated_at": int(_time.time())}
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
```

注意：`json` 已在 executor 顶部 import（`_extract_json` 在用）；确认无重复 import。

- [ ] **Step 1.4: 运行测试确认通过**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -k "SplitCanvasParams or ProgressSnapshot" -v`
Expected: PASS（5 个用例全绿）

- [ ] **Step 1.5: 状态机扩展 + service 方法**

`api/db/services/template_fill_service.py`：

① `_TASK_TRANSITS`（36-42 行）扩展 `cancelled`：

```python
_TASK_TRANSITS = {
    "pending": {"retrieving", "failed", "cancelled"},
    "retrieving": {"generating", "failed", "cancelled"},
    "generating": {"rendering", "failed", "cancelled"},
    "rendering": {"done", "partial", "failed"},
    "done": set(), "partial": set(), "failed": set(), "cancelled": set(),
}
```

② `TplFillTaskService` 类内（`update_status` 之后）新增两个方法：

```python
    @classmethod
    @DB.connection_context()
    def cancel_running(cls, task_id: str) -> bool:
        """把未终态任务置 cancelled（画布停止/取消路径用）。where 不带 status==cur
        （取消可能发生在任一中间态），但用 in_ 白名单限定中间态，终态行不受影响。"""
        return cls.model.update(status="cancelled", error="画布已停止，任务被取消").where(
            cls.model.id == task_id,
            cls.model.status.in_(("pending", "retrieving", "generating", "rendering"))).execute() > 0

    @classmethod
    @DB.connection_context()
    def find_running(cls, template_id: str, tenant_id: str):
        """该范本在租户内是否已有执行中任务（画布重复发起时复用观察，不重复起线程）。
        取最新一条；无则 None。"""
        return cls.model.select().where(
            (cls.model.template_id == template_id)
            & (cls.model.tenant_id == tenant_id)
            & cls.model.status.in_(("pending", "retrieving", "generating", "rendering"))
        ).order_by(cls.model.create_time.desc()).first()
```

- [ ] **Step 1.6: 写失败测试 —— cancelled 状态转移**

`test/test_template_fill_executor.py` 追加：

```python
class TestCancelTransit:
    """cancelled 进入状态机白名单：中间态可转，终态不可被覆盖。"""

    def test_cancel_from_middle_states(self):
        from api.db.services.template_fill_service import TplFillTaskService
        for cur in ("pending", "retrieving", "generating"):
            assert TplFillTaskService.can_transit(cur, "cancelled") is True

    def test_cancel_not_from_terminal(self):
        from api.db.services.template_fill_service import TplFillTaskService
        for cur in ("done", "failed", "partial", "cancelled"):
            assert TplFillTaskService.can_transit(cur, "cancelled") is False
```

- [ ] **Step 1.7: 运行测试确认通过**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -k "TestCancelTransit" -v`
Expected: PASS

- [ ] **Step 1.8: `_execute_task_async` 能力对齐改造**

`rag/svr/template_fill/executor.py` `_execute_task_async`（621 行起）逐段修改：

① `params = task.params or {}` 之后插入拆分与证据准备：

```python
    params = task.params or {}
    clean_params, opts = split_canvas_params(params)
    # 画布委托门控：只有 params 携带保留键（画布节点必写，含空值）才启用
    # D−C 收窄/直填覆盖/证据注入/取消探针；B端普通任务保持全量进 LLM 的既有行为
    is_canvas = any(k in params for k in _CANVAS_RESERVED_KEYS)
    direct_values: dict = opts["direct_values"] if is_canvas else {}
    changed_keys: set = (opts["changed_keys"] if is_canvas
                         else {it.get("key") for it in placeholders if it.get("key")})
    skip_keys = opts["retrieve_skip_keys"] if is_canvas else None
    user_file_text = opts["user_file_text"] if is_canvas else ""
    cancel_probe = _make_cancel_probe(task_id) if is_canvas else None
```

② ②检索段（原 652-653 行）改为传 skip 与取消探针：

```python
    cancel_probe = _make_cancel_probe(task_id) if is_canvas else None
    try:
        chunks_by_key, evidence = await _retrieve_all(
            task.tenant_id, placeholders, kb_ids, clean_params, task_id=task_id,
            skip_keys=skip_keys, should_cancel=cancel_probe)
    except GenerateCancelled:
        svc.cancel_running(task_id)
        _write_snapshot(task_id, status="cancelled", error="画布已停止，任务被取消")
        return
```

`_retrieve_all` 签名加 `skip_keys: set | None = None`，todo 循环内（`if _norm_fill_mode(it) == "llm" and query and kb_ids:` 行）改为：

```python
        if key in (skip_keys or ()):
            continue
        if _norm_fill_mode(it) == "llm" and query and kb_ids:
            todo.append((key, query, it))
```

③ ③LLM 产值段（原 656-667 行）——收窄 + user_file_text 证据 + 累积快照 + 取消：

```python
    if not svc.update_status(task_id, "retrieving", "generating"):
        return
    _write_snapshot(task_id, status="generating", done=0, total=0)
    # 画布委托对齐节点 _fill_one：D−C 收窄（有默认值且预判未变化不进 LLM）+ 直填键排除
    llm_placeholders = [it for it in placeholders
                        if _norm_fill_mode(it) == "llm" and it.get("key")
                        and it["key"] not in direct_values
                        and not (str(it.get("default_value") or "")
                                 and it["key"] not in changed_keys)]
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
        svc.cancel_running(task_id)
        _write_snapshot(task_id, status="cancelled", values=dict(acc_values),
                        error="画布已停止，任务被取消")
        return
    except Exception as e:
        logger.exception("generate_values failed, task=%s", task_id)
        svc.update_status(task_id, "generating", "failed", error=f"LLM 生成失败: {e}")
        _write_snapshot(task_id, status="failed", values=dict(acc_values),
                        error=f"LLM 生成失败: {e}")
        return
```

④ ④合并段（原 669-673 行）——补齐 D−C missing、param 直取用 clean_params、直填覆盖：

```python
    # D−C 字段未进 LLM，显式纳入 missing 才能让 _merge_default_values 直取默认值
    for it in placeholders:
        k = it.get("key")
        if (k and k not in generated and k not in direct_values
                and _norm_fill_mode(it) == "llm"
                and str(it.get("default_value") or "")
                and k not in changed_keys):
            missing.add(k)
    _merge_param_values(placeholders, generated, missing, clean_params)
    # 用户直填值直取（空串=明确清空）；先摘出 missing 防默认值回填覆盖直填
    for k, v in direct_values.items():
        generated[k] = v
        missing.discard(k)
    _merge_default_values(placeholders, generated, missing)

    values, cell_status = build_values(placeholders, generated)
```

⑤ ⑤渲染段失败路径补快照（两处 `update_status(..., "failed", ...)` 后各加一行）：

```python
        _write_snapshot(task_id, status="failed", values=values, error=err)
```
（异常分支用 `error=f"渲染落稿失败: {e}"`）

⑥ ⑥终态段：done CAS 成功后、⑦沉淀之前写终态快照：

```python
    _write_snapshot(task_id, status="done", values=values)
```

- [ ] **Step 1.9: 全量回归 executor 测试**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -v`
Expected: 既有用例 + 新增用例全部 PASS（无回退）

- [ ] **Step 1.10: Commit**

```bash
git add api/db/services/template_fill_service.py rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): executor 支持画布委托——params保留键拆分/D-C收窄/用户文件证据/Redis进度快照/取消键"
```

---

### Task 2: spawn 复用抽取

**Files:**
- Create: `rag/svr/template_fill/spawn.py`
- Modify: `api/apps/restful_apis/template_api.py`（`_spawn_fill_task` 改为薄封装）
- Test: `test/test_template_fill_executor.py`（追加）

- [ ] **Step 2.1: 写失败测试**

```python
class TestSpawnReuse:
    """spawn 防重入：同 task_id 连续两次调用只起一个执行线程。"""

    def test_spawn_dedup(self, monkeypatch):
        import threading
        from rag.svr.template_fill import spawn as spawn_mod
        calls = []
        monkeypatch.setattr(spawn_mod, "execute_task",
                            lambda tid: calls.append(tid))
        # execute_task 在线程内延迟 import，这里 monkeypatch 模块属性即可
        monkeypatch.setattr(threading, "Thread",
                            _FakeThread)  # 见下方伪类：同步执行 target()
        spawn_mod.spawn_fill_task("t-spawn-1")
        spawn_mod.spawn_fill_task("t-spawn-1")
        assert calls == ["t-spawn-1"]


class _FakeThread:
    """同步跑 target 的假线程（测试免并发）：
    构造时记录 kwargs，start() 时直接调用 target(*args, **kwargs)。"""
    def __init__(self, target=None, daemon=None, name=None, **kw):
        self._target = target
    def start(self):
        self._target()
```

注意：`_FakeThread` 类定义放在 `TestSpawnReuse` 之前（模块级）。

- [ ] **Step 2.2: 运行测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -k TestSpawnReuse -v`
Expected: FAIL（`rag.svr.template_fill.spawn` 模块不存在）

- [ ] **Step 2.3: 实现 spawn 模块**

创建 `rag/svr/template_fill/spawn.py`：

```python
# -*- coding: utf-8 -*-
"""填写任务执行线程 spawn（B端 fill-task API 与画布节点共用）。

从 template_api._spawn_fill_task 抽取：防重入集合 + daemon 线程 +
线程启动失败兜底（集合 discard + 任务行 CAS 置 failed 供重试）。
executor 延迟 import——spawn 模块自身不拉起任何重依赖。
"""
import logging
import threading

logger = logging.getLogger(__name__)

# 填写任务线程防重入：同一任务同时最多一个执行线程（spawn 时 add、线程 finally discard）
_running_lock = threading.Lock()
_running_tasks: set = set()


def is_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def spawn_fill_task(task_id: str) -> None:
    """起 daemon 线程跑填写 pipeline。线程内异常自行兜底，
    executor.execute_task 内部已把崩溃任务置 failed。"""
    with _running_lock:
        if task_id in _running_tasks:
            return
        _running_tasks.add(task_id)

    def _run():
        try:
            from rag.svr.template_fill.executor import execute_task
            execute_task(task_id)
        except Exception:  # noqa: BLE001 — executor 内部已兜底，此处最后防线
            logger.exception("fill task thread crashed, task_id=%s", task_id)
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    try:
        threading.Thread(target=_run, daemon=True, name=f"tpl-fill-{task_id[:8]}").start()
    except Exception:
        # 线程启动失败：add 已执行而 finally 永不会跑，task_id 会永久滞留集合
        # 导致 retry 恒报「任务正在执行中」。锁内 discard + 任务行 CAS 置 failed 供重试。
        with _running_lock:
            _running_tasks.discard(task_id)
        logger.exception("fill task thread start failed, task_id=%s", task_id)
        try:
            from api.db.db_models import DB
            from api.db.services.template_fill_service import TplFillTaskService
            with DB.connection_context():
                TplFillTaskService.model.update(
                    status="failed", error="任务调度失败：后台线程启动异常，请重试").where(
                    TplFillTaskService.model.id == task_id,
                    TplFillTaskService.model.status == "pending").execute()
        except Exception:
            logger.exception("fill task spawn force-fail failed, task_id=%s", task_id)
```

- [ ] **Step 2.4: template_api 改用共用 spawn**

`api/apps/restful_apis/template_api.py`：
① 删除模块级 `_running_lock`/`_running_tasks`（55-56 行）与 `_spawn_fill_task` 函数整体（62-93 行）。
② 顶部加 `from rag.svr.template_fill.spawn import is_running as _task_running, spawn_fill_task as _spawn_fill_task`。
③ 文件内所有 `task_id in _running_tasks` 判断（retry 端点 626 行）改为 `if _task_running(task_id):`。

- [ ] **Step 2.5: 运行测试与 import 冒烟**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py -v && uv run --no-sync python -c "import api.apps.restful_apis.template_api; print('api import OK')"`
Expected: PASS + `api import OK`

- [ ] **Step 2.6: Commit**

```bash
git add rag/svr/template_fill/spawn.py api/apps/restful_apis/template_api.py test/test_template_fill_executor.py
git commit -m "refactor(template-fill): spawn 抽取为共用模块——B端 API 与画布节点共一条调度路径"
```

---

### Task 3: progress 端点（重连轮询）

**Files:**
- Modify: `api/apps/restful_apis/template_api.py`（新路由 + 纯函数 payload 组装）
- Test: `test/test_template_fill_progress_api.py`（新建）

- [ ] **Step 3.1: 写失败测试 —— payload 组装纯函数**

创建 `test/test_template_fill_progress_api.py`：

```python
# -*- coding: utf-8 -*-
"""fill-task progress 端点 payload 组装的对抗性单测（纯函数，不触库不触 Redis）。"""
from datetime import datetime, timedelta


class TestBuildProgressPayload:
    def _task(self, status="generating", update_offset_s=0, values=None,
              result_file_id="", error=""):
        from types import SimpleNamespace
        return SimpleNamespace(
            id="t" * 32, template_id="tpl1", status=status,
            values=values, error=error, result_file_id=result_file_id,
            update_time=datetime.now() - timedelta(seconds=update_offset_s))

    def test_running_merges_snapshot(self):
        from api.apps.restful_apis.template_api import build_progress_payload
        snap = {"status": "generating", "done": 3, "total": 10,
                "values": {"a": "1"}, "error": ""}
        p = build_progress_payload(self._task(status="generating"), snap)
        assert p["status"] == "generating"
        assert p["done"] == 3 and p["total"] == 10
        assert p["values"] == {"a": "1"}
        assert p["stalled"] is False
        assert p["download"] is None

    def test_snapshot_none_falls_back_to_db(self):
        from api.apps.restful_apis.template_api import build_progress_payload
        # Redis 挂掉：DB 行权威（done 时 values.render 仍可回看）
        p = build_progress_payload(self._task(
            status="done", values={"cells": {}, "render": {"a": "1"}}), None)
        assert p["status"] == "done"
        assert p["values"] == {"a": "1"}

    def test_stalled_after_10min(self):
        from api.apps.restful_apis.template_api import build_progress_payload
        p = build_progress_payload(self._task(status="generating",
                                              update_offset_s=601), None)
        assert p["stalled"] is True

    def test_not_stalled_when_terminal(self):
        from api.apps.restful_apis.template_api import build_progress_payload
        p = build_progress_payload(self._task(status="failed",
                                              update_offset_s=99999), None)
        assert p["stalled"] is False

    def test_done_without_file_no_download(self):
        from api.apps.restful_apis.template_api import build_progress_payload
        p = build_progress_payload(self._task(status="done"), None)
        assert p["download"] is None
```

- [ ] **Step 3.2: 运行测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_progress_api.py -v`
Expected: FAIL（`build_progress_payload` 不存在）

- [ ] **Step 3.3: 实现 payload 纯函数 + 路由**

`api/apps/restful_apis/template_api.py`：

① import 区加：

```python
from rag.svr.template_fill.executor import read_progress_snapshot
```

② `retry_fill_task` 路由之后添加：

```python
# stalled 判定阈值：非终态任务 update_time 超过该秒数视为中断（服务器重启等）
_PROGRESS_STALLED_SECONDS = 600
# 终态任务行里 values 的结构：{"cells": {...}, "render": {...}}
_TERMINAL_STATUSES = ("done", "failed", "partial", "cancelled")


def build_progress_payload(task, snapshot: dict | None) -> dict:
    """合并 DB 行 + Redis 快照为 progress 响应（纯函数，便于对抗测试）。
    快照缺失（Redis 挂掉/过期）退化为 DB 行：进度数字停更但状态/终态值仍准确。
    stalled：非终态且 update_time 超阈值（服务器重启 daemon 线程死）→ 前端提示可重试。"""
    status = (snapshot or {}).get("status") or task.status
    values = (snapshot or {}).get("values")
    if values is None:
        values = (task.values or {}).get("render") if isinstance(task.values, dict) else None
    stalled = (task.status not in _TERMINAL_STATUSES
               and task.update_time is not None
               and (datetime.now() - task.update_time).total_seconds()
               > _PROGRESS_STALLED_SECONDS)
    download = None
    if task.status == "done" and task.result_file_id:
        # bucket 桥接：fill-task 稿件在 {template_id} bucket，拷入 {tenant}-downloads
        # （/agents/download 与 /files/{id}/content 的既有读取契约）。确定性对象名
        # tplfill-{task_id} 幂等覆盖，重复轮询不产生重复对象。
        doc_id = f"tplfill-{task.id}"
        filename = ""
        try:
            tpl = TplTemplateService.get_or_none(id=task.template_id)
            blob = settings.STORAGE_IMPL.get(task.template_id, task.result_file_id)
            if tpl and blob:
                filename = f"{_sanitize_filename(tpl.name)}.docx"
                settings.STORAGE_IMPL.put(f"{task.tenant_id}-downloads", doc_id, blob)
        except Exception:
            logger.exception("progress bucket bridge failed, task=%s", task.id)
        if filename:
            download = {"doc_id": doc_id, "filename": filename, "name": filename,
                        "url": f"/api/v1/agents/download?id={doc_id}&created_by={task.tenant_id}"}
    return {
        "status": status,
        "done": (snapshot or {}).get("done"),
        "total": (snapshot or {}).get("total"),
        "values": values,
        "download": download,
        "error": (snapshot or {}).get("error") or (task.error or ""),
        "stalled": stalled,
    }


@manager.route("/template/fill/fill-task/<task_id>/progress", methods=["GET"])
@login_required
async def get_fill_task_progress(task_id: str):
    """断连重连轮询端点（幂等只读）：owner 校验同 get_fill_task，
    合并 DB 行 + Redis 快照；前端 2s 轮询，status 终态即停。"""
    task = TplFillTaskService.get_owned(task_id, current_user.id)
    if not task:
        return get_error_data_result("任务不存在")
    return get_result(data=build_progress_payload(task, read_progress_snapshot(task_id)))
```

注意：`datetime` 若未导入需加 `from datetime import datetime`；`_sanitize_filename` 从 `api.db.services.template_fill_service` 导入（节点同款用法）。

- [ ] **Step 3.4: 运行测试确认通过**

Run: `uv run --no-sync pytest test/test_template_fill_progress_api.py -v`
Expected: PASS（5 用例）

- [ ] **Step 3.5: import 冒烟 + Commit**

Run: `uv run --no-sync python -c "import api.apps.restful_apis.template_api; print('OK')"`
Expected: `OK`

```bash
git add api/apps/restful_apis/template_api.py test/test_template_fill_progress_api.py
git commit -m "feat(template-fill): fill-task progress 重连端点——Redis快照+DB合并/stalled判定/downloads bucket桥接"
```

---

### Task 4: 画布节点委托改造

**Files:**
- Modify: `agent/component/template_fill.py`（`_invoke_async` ②③④段替换为委托+观察者；删除 `_fill_one`）
- Test: `test/test_template_fill_delegate.py`（新建）

- [ ] **Step 4.1: 写失败测试 —— 任务参数组装纯函数**

`agent/component/template_fill.py` 中将新增模块级纯函数 `_canvas_task_params`（Task 4.3 实装）。先写测试：

创建 `test/test_template_fill_delegate.py`：

```python
# -*- coding: utf-8 -*-
"""画布委托任务参数组装的对抗性单测（纯函数，不触库/LLM）。"""


class TestCanvasTaskParams:
    def _ph(self, key, mode="llm", default=""):
        return {"key": key, "fill_mode": mode, "default_value": default}

    def test_full_decision_payload(self):
        from agent.component.template_fill import _canvas_task_params
        placeholders = [self._ph("k1", default="d1"), self._ph("k2"), self._ph("k3")]
        llm_items = [{"key": "k2"}]   # 确认后仍要走检索+LLM 的字段
        params = _canvas_task_params(
            begin_fields={"项目名称": "X"}, query="需求描述",
            decision={"changed": {"k1"}, "values": {"k3": ""}},
            llm_item_keys={it["key"] for it in llm_items},
            placeholders=placeholders, user_file_text="文件内容")
        assert params["_direct_values"] == {"k3": ""}
        assert params["_changed_keys"] == ["k1"]
        # D−C（k1 有默认值且未变化→跳；k3 直填→跳）之外只有 k2 走检索
        assert params["_retrieve_skip_keys"] == ["k1", "k3"]
        assert params["_user_file_text"] == "文件内容"
        assert params["项目名称"] == "X"
        assert params["用户需求描述"] == "需求描述"

    def test_no_decision_keeps_defaults_path(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")],
            user_file_text="")
        assert params["_direct_values"] == {}
        assert params["_changed_keys"] == []
        assert params["_retrieve_skip_keys"] == []
        assert "用户需求描述" not in params

    def test_empty_query_not_inserted(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="   ", decision=None,
            llm_item_keys=set(), placeholders=[], user_file_text="x")
        assert "用户需求描述" not in params
```

- [ ] **Step 4.2: 运行测试确认失败**

Run: `uv run --no-sync pytest test/test_template_fill_delegate.py -v`
Expected: FAIL（`_canvas_task_params` 不存在）

- [ ] **Step 4.3: 实现纯函数**

`agent/component/template_fill.py` 模块级（`_MIME_BY_TYPE` 常量之后）添加：

```python
def _canvas_task_params(begin_fields: dict, query: str, decision: dict | None,
                        llm_item_keys: set, placeholders: list[dict],
                        user_file_text: str) -> dict:
    """组装委托给 executor.execute_task 的任务 params：
    背景（Begin 字段+需求描述，与节点内 background 同构）+ 下划线保留键
    （直填值/预判变化键/检索跳过键/用户文件证据），executor 侧 split_canvas_params 拆解。
    llm_item_keys 为确认后仍要走检索+LLM 的字段 key 集合；其余 llm 槽（D−C 与直填）
    跳过检索，由 executor 的 missing 显式纳入 → _merge_default_values 兜底。"""
    decision = decision or {}
    direct = decision.get("values") or {}
    changed = decision.get("changed") or set()
    skip = [it["key"] for it in placeholders
            if it.get("key") and executor._norm_fill_mode(it) == "llm"
            and it["key"] not in llm_item_keys]
    params: dict = dict(begin_fields)
    if query and query.strip():
        params["用户需求描述"] = query.strip()[:_BEGIN_FIELD_PROMPT_MAX]
    params["_direct_values"] = {str(k): str(v) for k, v in direct.items()}
    params["_changed_keys"] = sorted(str(k) for k in changed)
    params["_retrieve_skip_keys"] = skip
    params["_user_file_text"] = user_file_text or ""
    return params
```

- [ ] **Step 4.4: 运行测试确认通过**

Run: `uv run --no-sync pytest test/test_template_fill_delegate.py -v`
Expected: PASS（3 用例）

- [ ] **Step 4.5: `_invoke_async` ②③④段替换为委托+观察者**

`agent/component/template_fill.py`：

① 顶部 import 区加：

```python
from api.db.services.template_fill_service import TplFillTaskService
from rag.svr.template_fill.spawn import spawn_fill_task
```

（`TplTemplateService, TplTemplateVersionService, _sanitize_filename` 已在既有 import 行，把 `TplFillTaskService` 并入该 import 块。）

② `_invoke_async` 中从「# ② 跨范本共享检索」（原 466 行 `retrieval_sem = ...` 起）到 gather 结果处理段（原 527-568 行）整体替换为：

```python
        # ② 委托后台执行器：每范本一个 tpl_fill_task 行 + daemon 线程（复用 B端
        # _spawn_fill_task 同一调度路径）。执行与连接解耦——断连/刷新后任务照常
        # 跑完落库；节点降级为观察者。同一范本已有执行中任务则复用观察（不重复起线程）。
        task_of: dict[str, str] = {}   # template_id -> task_id
        for c in chosen:
            tid = c["template_id"]
            row = TplFillTaskService.find_running(tid, tenant_id)
            if row is not None:
                task_of[tid] = row.id
                continue
            task_id = get_uuid()
            TplFillTaskService.insert(
                id=task_id, template_id=tid, template_version_id=c["_ver"].id,
                kb_ids=kb_ids,
                params=_canvas_task_params(
                    begin_fields, query, decisions.get(tid),
                    {it["key"] for it in _llm_fill_items(c)},
                    c["_placeholders"], user_file_text),
                status="pending", source="canvas", flow_instance_id="",
                tenant_id=tenant_id, created_by=tenant_id)
            spawn_fill_task(task_id)
            task_of[tid] = task_id

        # ③ 观察者轮询（1.5s，与确认轮询同量级）：读 Redis 快照 + DB 状态，组装
        # 与既有完全同形的 filling/filled/failed 事件（新增可选 task_id 字段）。
        # values 只在键数增长时推送（SSE 体积控制；前端合并幂等）。
        pending_tasks = {tid: t for tid, t in task_of.items()}
        results: dict[str, tuple[dict | None, str | None]] = {}  # tid -> (dl, err)
        pushed_values_len: dict[str, int] = {}
        while pending_tasks:
            if self.check_if_canceled("TemplateFill observing"):
                # 画布被停止：未终态任务写取消键（executor 批次/检索探针既有），
                # 线程自行收口置 cancelled——结果不丢，用户可重连查看
                for tid, t in list(pending_tasks.items()):
                    executor.write_cancel_key(t)
                self._push_progress({"stage": "cancelled"})
                return
            await asyncio.sleep(1.5)
            for cand in chosen:
                tid = cand["template_id"]
                if tid not in pending_tasks:
                    continue
                task_id = task_of[tid]
                row = TplFillTaskService.get_or_none(id=task_id)
                if row is None:
                    pending_tasks.pop(tid)
                    results[tid] = (None, "任务行不存在")
                    self._push_progress({"stage": "failed", "template_id": tid,
                                         "name": cand["name"],
                                         "error": "任务行不存在", "task_id": task_id})
                    continue
                snap = executor.read_progress_snapshot(task_id)
                if row.status in ("pending", "retrieving", "generating", "rendering"):
                    done = (snap or {}).get("done") or 0
                    total = (snap or {}).get("total")
                    if total is None:
                        total = len(_llm_fill_items(cand))
                    ev = {"stage": "filling", "template_id": tid, "name": cand["name"],
                          "done": done, "total": total, "task_id": task_id}
                    vals = (snap or {}).get("values")
                    if isinstance(vals, dict) and len(vals) > pushed_values_len.get(tid, 0):
                        ev["values"] = vals
                        pushed_values_len[tid] = len(vals)
                    self._push_progress(ev)
                    continue
                # 终态
                pending_tasks.pop(tid)
                if row.status == "done" and row.result_file_id:
                    dl = self._bridge_download(tenant_id, cand, row)
                    if dl is None:
                        results[tid] = (None, "成稿对象读取失败")
                        self._push_progress({"stage": "failed", "template_id": tid,
                                             "name": cand["name"],
                                             "error": "成稿对象读取失败", "task_id": task_id})
                        continue
                    results[tid] = (dl, None)
                    self._push_progress({"stage": "filled", "template_id": tid,
                                         "name": cand["name"], "download": dl,
                                         "task_id": task_id})
                elif row.status == "cancelled":
                    results[tid] = (None, "任务已取消")
                    self._push_progress({"stage": "failed", "template_id": tid,
                                         "name": cand["name"],
                                         "error": "任务已取消", "task_id": task_id})
                else:
                    err = row.error or (snap or {}).get("error") or "填写失败"
                    results[tid] = (None, err)
                    self._push_progress({"stage": "failed", "template_id": tid,
                                         "name": cand["name"], "error": err,
                                         "task_id": task_id})

        # ④ 汇总输出（契约与改造前一致：download 列表 + content 汇总）
        downloads: list[dict] = []
        summary_lines: list[str] = []
        for cand in chosen:
            tid = cand["template_id"]
            dl, err = results.get(tid, (None, "填写失败"))
            if dl is None:
                summary_lines.append(f"《{cand['name']}》：填写失败（{err}）。")
                continue
            downloads.append(dl)
            total = len(cand["_placeholders"])
            summary_lines.append(
                f"《{cand['name']}》：共 {total} 个填写点，AI 填充完成，"
                f"未检索到值的填写点已留空。")
        if not downloads:
            raise ValueError("所有范本填写均失败：" + "；".join(
                str(results.get(c["template_id"], ("", "未知"))[1]) for c in chosen))
        self.set_output("download", json.dumps(downloads, ensure_ascii=False))
        suffix = "，可在上方预览或下载成稿。" if len(downloads) == 1 else "，可在上方逐份预览或下载成稿。"
        head = (f"已选用 {len(downloads)} 份范本：\n" if len(downloads) > 1 else "已选用范本")
        self.set_output("content", head + "\n".join(
            ("- " + ln for ln in summary_lines) if len(downloads) > 1 else summary_lines
        ) + suffix)
        self._push_progress({"stage": "done"})
        logger.info("TemplateFill done, canvas=%s templates=%s",
                    self._id, [c["template_id"] for c in chosen])
```

③ 删除已无调用的 `_fill_one` 方法整体（原 318-425 行）。`_fill_and_notify` 闭包随替换段一并消失。

④ 类内新增桥接方法（放在 `_invoke_async` 之前）：

```python
    def _bridge_download(self, tenant_id: str, cand: dict, row) -> dict | None:
        """成稿 bucket 桥接：fill-task 稿件在 {template_id} bucket，拷入
        {tenant_id}-downloads（既有 /agents/download 与 /files/{id}/content 契约）。
        确定性对象名 tplfill-{task_id} 幂等覆盖。失败返回 None（降级为 failed 事件）。"""
        try:
            blob = settings.STORAGE_IMPL.get(cand["template_id"], row.result_file_id)
            if not blob:
                return None
            doc_id = f"tplfill-{row.id}"
            settings.STORAGE_IMPL.put(f"{tenant_id}-downloads", doc_id, blob)
            ext = cand["file_type"]
            filename = f"{_sanitize_filename(cand['name'])}.{ext}"
            return {"doc_id": doc_id, "filename": filename, "name": filename,
                    "mime_type": _MIME_BY_TYPE.get(ext, "application/octet-stream"),
                    "size": len(blob),
                    "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}"}
        except Exception:  # noqa: BLE001 — 桥接失败降级为 failed 事件，不炸观察循环
            logger.warning("bridge download failed, task=%s", row.id, exc_info=True)
            return None
```

⑤ 模块 docstring 第 ③ 步说明同步更新一句：「检索/产值/渲染经 tpl_fill_task 后台执行器执行（与 B端填写任务同 pipeline），节点只做任务创建与进度观察」。

- [ ] **Step 4.6: 清理死代码**

Run: `grep -n "_fill_one\|retrieve_all_shared\|_VALUES_PUSH_CHUNK\|_FILL_CONCURRENCY\|_RETRIEVAL_CONCURRENCY" agent/component/template_fill.py`
Expected: 无 `_fill_one`/`retrieve_all_shared` 调用残留；若 `_VALUES_PUSH_CHUNK`/`_FILL_CONCURRENCY`/`_RETRIEVAL_CONCURRENCY` 常量已无引用则一并删除（`_RETRIEVAL_CONCURRENCY` 若被 `_confirm_changed_fields` 引用则保留）。

- [ ] **Step 4.7: 全量回归**

Run: `uv run --no-sync pytest test/test_template_fill_delegate.py test/test_template_fill_executor.py test/test_template_fill_events.py test/test_template_fill_progress_api.py -v`
Expected: 全部 PASS

- [ ] **Step 4.8: Commit**

```bash
git add agent/component/template_fill.py test/test_template_fill_delegate.py
git commit -m "feat(template-fill): 画布节点委托后台执行器+观察者轮询——执行与连接解耦，断连可跑完落库"
```

---

### Task 5: 前端归约 + 轮询 hook + 进度卡适配

**Files:**
- Modify: `web/src/hooks/template-fill-stream.ts`（归约记 task_id）
- Modify: `web/src/utils/api.ts`（progress 端点）
- Create: `web/src/hooks/use-template-fill-task-poll.ts`
- Modify: `web/src/pages/c-chat/template-fill-progress.tsx`（接入轮询 override）
- Test: `web/src/hooks/__tests__/template-fill-stream.test.ts`（追加）

- [ ] **Step 5.1: 写失败测试 —— 归约记 task_id**

`web/src/hooks/__tests__/template-fill-stream.test.ts` 追加：

```ts
describe('applyTemplateFillEvent: task_id 归约（断连重连锚点）', () => {
  it('filling/filled/failed 事件携带 task_id 时记录到模板行', () => {
    const acc: any = {};
    applyTemplateFillEvent(acc, { stage: 'filling', template_id: 't1', name: 'A', done: 1, total: 5, task_id: 'task-1' } as any);
    expect(acc.templateFill.templates[0].task_id).toBe('task-1');
    applyTemplateFillEvent(acc, { stage: 'filled', template_id: 't1', name: 'A', download: { doc_id: 'd' }, task_id: 'task-1' } as any);
    expect(acc.templateFill.templates[0].task_id).toBe('task-1');
    const acc2: any = {};
    applyTemplateFillEvent(acc2, { stage: 'failed', template_id: 't2', name: 'B', error: 'x', task_id: 'task-2' } as any);
    expect(acc2.templateFill.templates[0].task_id).toBe('task-2');
  });

  it('无 task_id 的旧消息归约不变（兼容）', () => {
    const acc: any = {};
    applyTemplateFillEvent(acc, { stage: 'filling', template_id: 't1', name: 'A', done: 0, total: 3 });
    expect(acc.templateFill.templates[0].task_id).toBeUndefined();
  });

  it('同名无 task_id 的事件不抹掉已记录的 task_id', () => {
    const acc: any = {};
    applyTemplateFillEvent(acc, { stage: 'filling', template_id: 't1', name: 'A', done: 0, total: 3, task_id: 'task-1' } as any);
    applyTemplateFillEvent(acc, { stage: 'filling', template_id: 't1', name: 'A', done: 1, total: 3 });
    expect(acc.templateFill.templates[0].task_id).toBe('task-1');
  });
});
```

- [ ] **Step 5.2: 运行测试确认失败**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts`
Expected: FAIL（task_id 未归约）

- [ ] **Step 5.3: 归约实现**

`web/src/hooks/template-fill-stream.ts`：
① `ITemplateFillTemplate` 增加字段：

```ts
  /** 后台任务锚点（断连重连轮询用）；旧消息无此字段 → 轮询不触发 */
  task_id?: string;
```

② reducer 中 `if (d.name) t.name = d.name;`（144 行）之后加一行：

```ts
  if (d.task_id) t.task_id = d.task_id;
```

- [ ] **Step 5.4: 运行测试确认通过**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts`
Expected: PASS

- [ ] **Step 5.5: api.ts 加端点**

`web/src/utils/api.ts` api 对象内（`downloadTemplateFillTask` 之后）加：

```ts
  templateFillTaskProgress: (taskId: string) =>
    `${restAPIv1}/template/fill/fill-task/${taskId}/progress`,
```

- [ ] **Step 5.6: 实现轮询 hook**

创建 `web/src/hooks/use-template-fill-task-poll.ts`：

```ts
// 范本填写断连重连轮询（方案A）：历史恢复态（!streaming，SSE 已停）下，对带
// task_id 且仍在 filling 的模板行轮询 progress 端点；running → 本地 override
// 更新进度+实时预览值；终态 → 停轮询，filled 本地合成成稿卡（下载/预览走
// 桥接后的 /agents/download 契约）。实时流式期间事件也带 task_id，但 SSE 为准、
// override 与 SSE 幂等合并（后到者胜），无状态冲突（设计 §5）。
import { useEffect, useRef, useState } from 'react';
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import api from '@/utils/api';
import { request } from '@/services/http-client'; // 与既有 c-chat 请求封装一致；如实际导出名不同，按同文件其他 hook 的 import 写法对齐

const POLL_INTERVAL_MS = 2000;

export function useTemplateFillTaskPoll(
  templates: ITemplateFillTemplate[] | undefined,
  enabled: boolean,
) {
  const [overrides, setOverrides] = useState<Record<string, Partial<ITemplateFillTemplate>>>({});
  const stopped = useRef<Set<string>>(new Set()); // 已到终态的 task_id，停轮询

  useEffect(() => {
    if (!enabled || !templates?.length) return;
    const targets = templates.filter(
      (t) => t.task_id && t.status === 'filling' && !stopped.current.has(t.task_id),
    );
    if (!targets.length) return;
    let cancelled = false;

    const tick = async () => {
      for (const t of targets) {
        const taskId = t.task_id!;
        try {
          const { data } = await request.get(api.templateFillTaskProgress(taskId));
          if (cancelled) return;
          const d = data?.data || data;
          if (!d?.status) continue;
          if (['done', 'failed', 'partial', 'cancelled'].includes(d.status)) {
            stopped.current.add(taskId);
            setOverrides((prev) => ({
              ...prev,
              [taskId]:
                d.status === 'done' && d.download
                  ? { status: 'filled' as const, download: d.download, values: d.values || undefined }
                  : { status: 'failed' as const, error: d.stalled ? '任务中断，可重试' : d.error || '填写失败' },
            }));
          } else {
            setOverrides((prev) => ({
              ...prev,
              [taskId]: {
                done: d.done ?? 0,
                total: d.total ?? 0,
                ...(d.values && Object.keys(d.values).length ? { values: d.values } : {}),
              },
            }));
          }
        } catch {
          // 网络抖动/端点异常：下一轮继续，不中断其他任务
        }
      }
    };

    const timer = setInterval(tick, POLL_INTERVAL_MS);
    tick();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, templates]);

  // 合并 override：SSE 已到 filled 的行以 SSE 为准（丢弃 override）；
  // filling 行叠加轮询产物（done/total/values），终态 override 换 status。
  const merged = templates?.map((t) => {
    const ov = t.task_id ? overrides[t.task_id] : undefined;
    if (!ov) return t;
    if (t.status === 'filled') return t;
    return { ...t, ...ov } as ITemplateFillTemplate;
  });
  return merged;
}
```

注意：`request` 的实际导入路径/导出名以 `use-template-fill-request.ts` 顶部既有写法为准（实现时先读该文件对齐，不改封装本身）。

- [ ] **Step 5.7: 进度卡接入**

`web/src/pages/c-chat/template-fill-progress.tsx`：
① 顶部加 `import { useTemplateFillTaskPoll } from '@/hooks/use-template-fill-task-poll';`
② 组件内 `liveTpl` 派生之前加：

```tsx
  // 断连重连：带 task_id 且 SSE 已停（历史恢复态）的行走轮询 override；
  // 实时流式期间本组件也会挂载，override 与 SSE 幂等合并无冲突
  const mergedTemplates = useTemplateFillTaskPoll(state?.templates, true);
  const mergedState = mergedTemplates
    ? ({ ...(state || {}), templates: mergedTemplates } as ITemplateFillState)
    : state;
```

③ 组件内后续所有 `state` 引用（`state?.pendingConfirm`、`state?.templates.map`、`liveTpl` 查找）改为 `mergedState`。

- [ ] **Step 5.8: 类型检查 + 测试 + 构建**

Run: `cd web && npx tsc --noEmit && npx jest src/hooks/__tests__/template-fill-stream.test.ts`
Expected: 无类型错误，测试 PASS

Run: `cd web && npm run build`
Expected: 构建成功

- [ ] **Step 5.9: Commit**

```bash
git add web/src/hooks/template-fill-stream.ts web/src/hooks/use-template-fill-task-poll.ts web/src/hooks/__tests__/template-fill-stream.test.ts web/src/utils/api.ts web/src/pages/c-chat/template-fill-progress.tsx
git commit -m "feat(web): 范本填写断连重连——归约task_id+轮询hook本地override+终态合成成稿卡"
```

---

### Task 6: 收尾（文档 + 全量验证 + 审查）

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）
- Modify: `CLAUDE.md`（参考表该行状态更新）
- Modify: `docs/superpowers/specs/2026-09-12-template-fill-detached-task-design.md`（状态改「已实施」）

- [ ] **Step 6.1: CHANGE.md 追加条目**

`CHANGE.md` 顶部追加（格式照既有条目）：

```markdown
## 2026-09-12 范本填写后台化与断连重连（方案A）

**主题**：填写执行与 SSE 连接解耦——断连/刷新后任务在服务器跑完，成稿落库可取。

**核心变更**：
- 画布 TemplateFill 节点确认后委托 tpl_fill_task 后台线程（source='canvas'，spawn 与 B端共用），节点降级为观察者轮询（1.5s），事件新增可选 task_id
- executor 对齐节点能力：params 保留键拆分（直填/D−C 变化键/检索跳过/用户文件证据）、Redis 进度快照（tpl_fill_progress:{id} TTL 24h）、取消键契约（tpl_fill:cancel:{id}）
- 新端点 GET /template/fill/fill-task/{id}/progress（owner 校验+stalled 判定+downloads bucket 桥接 tplfill-{task_id}）
- 状态机扩展 cancelled 态；TplFillTaskService 新增 cancel_running/find_running
- 前端：归约记 task_id + useTemplateFillTaskPoll 轮询 hook（终态本地合成成稿卡）

**遗留**：未部署；flow_instance_id 暂留空；多范本共享检索去重随委托化不再适用（B端行为）
```

- [ ] **Step 6.2: CLAUDE.md 参考表登记**

`CLAUDE.md` 参考文档表追加一行（紧随范本识别准确性行之后）：

```markdown
| 范本填写后台化与断连重连 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-12-template-fill-detached-task-design.md` | ★ 填写执行与连接解耦（方案A）：节点委托 tpl_fill_task 后台线程+观察者轮询+progress 重连端点+前端轮询 hook；实施计划 docs/superpowers/plans/2026-09-12-template-fill-detached-task.md（已完成编码，未部署） |
```

- [ ] **Step 6.3: 全量验证**

Run: `uv run --no-sync pytest test/test_template_fill_executor.py test/test_template_fill_delegate.py test/test_template_fill_events.py test/test_template_fill_progress_api.py test/test_template_fill_utils.py test/test_template_fill_tool.py -v`
Expected: 全部 PASS（设计 §7：既有用例不回退）

- [ ] **Step 6.4: 代码审查**

调用 `konus-code-review` 技能审查全部改动（本次开发完成后的强制动作）。

- [ ] **Step 6.5: Commit 收尾**

```bash
git add CHANGE.md CLAUDE.md docs/superpowers/specs/2026-09-12-template-fill-detached-task-design.md
git commit -m "docs: 范本填写后台化与断连重连（方案A）迭代记录与设计状态更新"
```

---

## 自审记录

- **Spec 覆盖**：设计 §4.1（T1+T4）、§4.2 快照（T1）、§4.3 端点（T3）、§4.4 成稿持久化+桥接（T3/T4）、§4.5 事件 task_id（T4+T5）、§5 前端（T5）、§6 八个边界场景（stalled=T3、重复发起复用=T4 find_running、Redis 降级=T1/T3、旧消息零变化=T5 兼容用例、取消=T4、越权=T3 get_owned）——全覆盖。
- **类型一致性**：`split_canvas_params` 返回 `(clean, opts)`、opts 四键名（direct_values/changed_keys/retrieve_skip_keys/user_file_text）在 T1/T4 一致；快照字段（status/done/total/values/error/updated_at）在 T1 写与 T3 读一致；`tplfill-{task_id}` 桥接对象名在 T3/T4 一致；前端 `task_id` 字段名前后端一致。
- **占位符扫描**：唯一开放点是 T5.6 的 `request` 导入路径——已显式指示实现者以 `use-template-fill-request.ts` 既有写法对齐（非 TBD，是「以现有代码为准」的确定性指令）。
