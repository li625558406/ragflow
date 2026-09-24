# 范本填写检索增强（二档全文降级 + 实体分析）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 填写点第一档检索不到时降级「核心实体+填写点名称」二档全文宽检索；用户原话经 LLM 实体分析产出直填值（确认卡预填）与检索语境实体。

**Architecture:** 实体分析在画布节点确认阶段与 AI 预判并行（一次 LLM 调用/范本），直填值走既有 `direct_values` 通道，实体经新保留键 `_entities` 传入 executor；executor 检索阶段对一档空槽用实体组合词宽检索补证据，命中片段打 `source=fulltext`，产值 prompt 对 fulltext 字段放宽为归纳编写。B端/REST/dry_run 无 `_entities` 保留键，全部维持现状。

**Tech Stack:** Python（Quart 后端，pytest + monkeypatch，无真实 KB/LLM）、设计文档 `docs/superpowers/specs/2026-09-24-template-fill-retrieval-fallback-design.md`

**测试命令约定：** 后端一律 `uv run pytest <file>::<test> -v`（在仓库根 `D:\AI\ragflow2` 执行）。**禁止自动部署。**

---

### Task 1: executor.extract_entities —— 用户原话实体分析

**Files:**
- Modify: `rag/svr/template_fill/executor.py`（在 `predict_changed_fields` 函数之后、`_norm_fill_mode` 之前插入）
- Test: `test/test_template_fill_executor.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

追加到 `test/test_template_fill_executor.py` 末尾：

```python
# ---------- 实体分析 extract_entities ----------

def test_extract_entities_happy(monkeypatch):
    """直填值+实体一次抽出；direct key 限给定填写点；原话与字段清单进 prompt。"""
    from rag.svr.template_fill import executor
    captured = {}

    async def fake_chat(sys, msgs):
        captured["sys"], captured["msg"] = sys, msgs[0]["content"]
        return '{"direct": {"buyer": "某单位"}, "entities": {"项目名称": "A项目", "__context__": "市政房建文件"}}'

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "buyer", "name": "采购人", "fill_mode": "llm"}]
    out = executor._run_async(executor.extract_entities("t", "采购人：某单位，生成A项目文件", phs))
    assert out["direct"] == {"buyer": "某单位"}
    assert out["entities"] == {"项目名称": "A项目", "__context__": "市政房建文件"}
    assert "采购人" in captured["msg"] and "采购人：某单位" in captured["msg"]


def test_extract_entities_ghost_key_and_null_dropped(monkeypatch):
    """编造 key（不在填写点清单）与显式 null 值一律丢弃；非 str 实体值 str 归一。"""
    from rag.svr.template_fill import executor

    async def fake_chat(sys, msgs):
        return ('{"direct": {"ghost": "x", "buyer": null, "buyer": "某单位"},'
                ' "entities": {"金额": 123, "空": null}}')

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "buyer", "name": "采购人", "fill_mode": "llm"}]
    out = executor._run_async(executor.extract_entities("t", "原话", phs))
    assert out["direct"] == {"buyer": "某单位"}
    assert out["entities"] == {"金额": "123"}


def test_extract_entities_bad_json_returns_empty(monkeypatch):
    """LLM 输出不可解析 → 空结果兜底（不阻塞主流程）。"""
    from rag.svr.template_fill import executor

    async def fake_chat(sys, msgs):
        return "完全不是 JSON"

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "buyer", "name": "采购人", "fill_mode": "llm"}]
    out = executor._run_async(executor.extract_entities("t", "原话", phs))
    assert out == {"direct": {}, "entities": {}}


def test_extract_entities_llm_failure_returns_empty(monkeypatch):
    """LLM 调用异常 → 空结果兜底。"""
    from rag.svr.template_fill import executor

    async def fake_chat(sys, msgs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "buyer", "name": "采购人", "fill_mode": "llm"}]
    out = executor._run_async(executor.extract_entities("t", "原话", phs))
    assert out == {"direct": {}, "entities": {}}


def test_extract_entities_empty_query_or_slots_no_llm(monkeypatch):
    """空原话/空填写点清单 → 直接空结果，不发起 LLM 调用。"""
    from rag.svr.template_fill import executor
    called = {"n": 0}

    async def fake_chat(sys, msgs):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "buyer", "name": "采购人", "fill_mode": "llm"}]
    assert executor._run_async(executor.extract_entities("t", "   ", phs)) == {"direct": {}, "entities": {}}
    assert executor._run_async(executor.extract_entities("t", "原话", [])) == {"direct": {}, "entities": {}}
    assert called["n"] == 0


def test_extract_entities_cancel_propagates(monkeypatch):
    """取消信号穿透（不被空结果兜底吞掉）；取消在 LLM 调用前触发，无需 LLM 桩。"""
    from rag.svr.template_fill import executor
    try:
        executor._run_async(executor.extract_entities(
            "t", "原话", [{"key": "a", "name": "甲", "fill_mode": "llm"}], should_cancel=lambda: True))
        raise AssertionError("GenerateCancelled 未穿透")
    except executor.GenerateCancelled:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k extract_entities -v`
Expected: FAIL，`AttributeError: ... has no attribute 'extract_entities'`

- [ ] **Step 3: 实现**

在 `rag/svr/template_fill/executor.py` 的 `predict_changed_fields` 函数结束（约 line 550，`_norm_fill_mode` 定义之前）插入：

```python
ENTITIES_SYSTEM = (
    "你是文档填写助手。分析用户需求原话，只输出一个 JSON 对象（不要输出任何其他文字）：\n"
    '{"direct": {"字段key": "字段值"}, "entities": {"实体名": "实体值"}}\n'
    "规则：\n"
    "1. direct：只收原话中明确给出了具体值、且能按名称/描述语义对应到给定字段清单的字段；"
    "原话没给值、或对应关系拿不准的字段一律不输出。\n"
    "2. entities：提取原话中的关键实体（如项目名称、采购人、招标代理、预算金额等），"
    "键为实体名、值为实体值；描述性/背景性内容（如「根据XX文件」）归入键 \"__context__\"。\n")


async def extract_entities(tenant_id: str, query: str, placeholders: list[dict],
                           should_cancel=None) -> dict:
    """用户原话实体分析（画布确认阶段调用，与 predict_changed_fields 并行）：
    一次 LLM 调用同时完成直填值抽取（direct，key 限给定填写点清单，高可信闸——
    拿不准的对应关系不输出）与关键实体/检索语境提取（entities，__context__ 固定键）。
    直填值过 _apply_constraints 约束闸；LLM 失败/不可解析/空输入 → 空结果兜底，
    不阻塞主流程（调用方按「无实体」走现状）。GenerateCancelled 穿透。"""
    items = [it for it in placeholders if isinstance(it, dict) and it.get("key")]
    if not query or not query.strip() or not items:
        return {"direct": {}, "entities": {}}
    valid = {it["key"]: it for it in items}
    spec = [{"key": _clean_for_prompt(it["key"], NAME_MAX),
             "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
             "description": _clean_for_prompt(it.get("description"), DESC_MAX)}
            for it in items]
    user_msg = ("## 字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                "\n\n## 用户需求原话\n" + _clean_for_prompt(query, QUERY_MAX))
    if _should_cancel(should_cancel):
        raise GenerateCancelled()
    try:
        mdl = _build_chat_mdl(tenant_id)
        ans = await mdl.async_chat(ENTITIES_SYSTEM, [{"role": "user", "content": user_msg}])
        raw = _extract_json(ans)
    except GenerateCancelled:
        raise
    except Exception:  # noqa: BLE001 — 实体分析失败不阻塞填写主流程
        logger.warning("extract_entities failed, tenant=%s", tenant_id, exc_info=True)
        return {"direct": {}, "entities": {}}
    direct: dict = {}
    raw_direct = raw.get("direct")
    if isinstance(raw_direct, dict):
        for k, v in raw_direct.items():
            it = valid.get(k)
            if it is None or v is None:
                continue            # 编造 key / 显式 null 一律丢弃
            val = _apply_constraints(str(v), it.get("constraints") or {})
            if val:
                direct[k] = val
    entities: dict = {}
    raw_ent = raw.get("entities")
    if isinstance(raw_ent, dict):
        for k, v in raw_ent.items():
            if v is None:
                continue
            val = _clean_for_prompt(str(v), PARAM_VAL_MAX)
            if val:
                entities[str(k)[:NAME_MAX]] = val
    return {"direct": direct, "entities": entities}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -k extract_entities -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): executor 新增 extract_entities 用户原话实体分析"
```

---

### Task 2: retrieve_slot 阈值参数 + _retrieve_all 二档全文降级

**Files:**
- Modify: `rag/svr/template_fill/executor.py`（常量区 line 38-45；`retrieve_slot` line 109-119；`_retrieve_all` line 717-782 末尾）
- Test: `test/test_template_fill_executor.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

```python
# ---------- 二档全文降级 ----------

def test_fulltext_query_composition():
    """二档组合词：项目/名称/标题类实体优先，__context__ 次之，填写点名称兜底。"""
    from rag.svr.template_fill import executor
    q = executor._fulltext_query(
        {"项目名称": "A项目", "采购人": "B单位", "__context__": "市政房建"}, "工程概况")
    assert q.startswith("A项目")
    assert "B单位" in q and "市政房建" in q and "工程概况" in q
    q2 = executor._fulltext_query({}, "字段名")
    assert q2 == "字段名"          # 无实体退化为填写点名称
    assert executor._fulltext_query({"__context__": ""}, "") == ""   # 全空 → 空词


def test_retrieve_all_tier2_fills_empty_slots(monkeypatch):
    """一档空槽触发二档：组合词含实体+填写点名称、宽阈值、top_k 翻倍封顶；
    命中片段打 source=fulltext；一档有证据的槽不触发。"""
    from rag.svr.template_fill import executor
    calls = []

    async def fake_slot(tenant_id, kb_ids, query, top_k=6, ctx=None, similarity_threshold=0.2):
        calls.append({"query": query, "top_k": top_k, "thr": similarity_threshold})
        if query == "字段0":
            return []
        return [{"content": "一档证据", "doc_id": "d", "doc_name": "n", "similarity": 0.9}]

    monkeypatch.setattr(executor, "load_retrieval_ctx", lambda t, k: ("kbs", "embd"))
    monkeypatch.setattr(executor, "retrieve_slot", fake_slot)
    placeholders = [{"key": "k0", "name": "字段0", "fill_mode": "llm"},
                    {"key": "k1", "name": "字段1", "fill_mode": "llm"}]
    entities = {"项目名称": "莆美项目", "__context__": "市政房建"}
    chunks_by_key, evidence = executor._run_async(executor._retrieve_all(
        "t", placeholders, ["kb1"], {}, entities=entities))
    t2 = [c for c in calls if "莆美项目" in c["query"]]
    assert len(t2) == 1, "二档只对空槽 k0 发起一次"
    assert "字段0" in t2[0]["query"]
    assert t2[0]["thr"] == executor.FULLTEXT_SIMILARITY_THRESHOLD
    assert t2[0]["top_k"] == 12                 # 默认 6 翻倍
    assert chunks_by_key["k0"]["chunks"][0]["source"] == "fulltext"
    assert evidence["k0"]["chunks"][0]["source"] == "fulltext"
    assert chunks_by_key["k1"]["chunks"][0]["content"] == "一档证据"


def test_retrieve_all_tier2_skipped_without_entities(monkeypatch):
    """无 entities（B端/REST/dry_run）→ 只有第一档，行为纯现状。"""
    from rag.svr.template_fill import executor
    calls = []

    async def fake_slot(tenant_id, kb_ids, query, top_k=6, ctx=None, similarity_threshold=0.2):
        calls.append(query)
        return []

    monkeypatch.setattr(executor, "load_retrieval_ctx", lambda t, k: ("kbs", "embd"))
    monkeypatch.setattr(executor, "retrieve_slot", fake_slot)
    placeholders = [{"key": "k0", "name": "字段0", "fill_mode": "llm"}]
    executor._run_async(executor._retrieve_all("t", placeholders, ["kb1"], {}))
    assert len(calls) == 1


def test_retrieve_all_tier2_failure_degrades_empty(monkeypatch):
    """二档单槽失败降级空证据，不中断整单。"""
    from rag.svr.template_fill import executor

    async def fake_slot(tenant_id, kb_ids, query, top_k=6, ctx=None, similarity_threshold=0.2):
        if query == "字段0":
            return []
        raise RuntimeError("es down")          # 二档查询炸

    monkeypatch.setattr(executor, "load_retrieval_ctx", lambda t, k: ("kbs", "embd"))
    monkeypatch.setattr(executor, "retrieve_slot", fake_slot)
    placeholders = [{"key": "k0", "name": "字段0", "fill_mode": "llm"}]
    chunks_by_key, _ = executor._run_async(executor._retrieve_all(
        "t", placeholders, ["kb1"], {}, entities={"项目名称": "A"}))
    assert chunks_by_key["k0"]["chunks"] == []


def test_retrieve_all_tier2_cancel_propagates(monkeypatch):
    """二档取消信号穿透（不被降级吞掉）。"""
    from rag.svr.template_fill import executor

    async def fake_slot(tenant_id, kb_ids, query, top_k=6, ctx=None, similarity_threshold=0.2):
        if query == "字段0":
            return []
        raise executor.GenerateCancelled()      # 二档取消

    monkeypatch.setattr(executor, "load_retrieval_ctx", lambda t, k: ("kbs", "embd"))
    monkeypatch.setattr(executor, "retrieve_slot", fake_slot)
    placeholders = [{"key": "k0", "name": "字段0", "fill_mode": "llm"}]
    try:
        executor._run_async(executor._retrieve_all(
            "t", placeholders, ["kb1"], {}, entities={"项目名称": "A"},
            should_cancel=lambda: False))
        raise AssertionError("GenerateCancelled 未穿透")
    except executor.GenerateCancelled:
        pass


def test_retrieve_slot_similarity_threshold_passthrough(monkeypatch):
    """retrieve_slot 新增 similarity_threshold 参数并透传给检索器。"""
    from rag.svr.template_fill import executor
    captured = {}

    class FakeRetriever:
        async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids, page, page_size,
                            similarity_threshold, *a, **kw):
            captured["thr"] = similarity_threshold
            return {"chunks": []}

    monkeypatch.setattr(executor.settings, "retriever", FakeRetriever())
    monkeypatch.setattr(executor, "_load_and_check_kbs", lambda tenant, kbs: [
        types.SimpleNamespace(id="kb1", tenant_id=tenant, embd_id="bge")])
    monkeypatch.setattr(executor, "_build_embd_mdl", lambda tenant, kbs: object())
    monkeypatch.setattr(executor, "label_question", lambda q, kbs: None)
    executor._run_async(executor.retrieve_slot("t", ["kb1"], "q", similarity_threshold=0.1))
    assert captured["thr"] == 0.1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k "tier2 or fulltext or similarity_threshold" -v`
Expected: FAIL（`FULLTEXT_SIMILARITY_THRESHOLD` / `_fulltext_query` 不存在；`retrieve_slot` 无该参数）

- [ ] **Step 3: 实现**

3a. 常量区（`RETRIEVAL_CONCURRENCY = 6` 之后）追加：

```python
FULLTEXT_SIMILARITY_THRESHOLD = 0.1   # 二档全文宽检索阈值（低于一档 SIMILARITY_THRESHOLD）
_TIER2_ENTITY_MAX = 3                 # 二档组合词最多取实体值个数
_ENTITY_PRIO_WORDS = ("项目", "名称", "标题")
```

3b. `retrieve_slot` 签名与调用改为：

```python
async def retrieve_slot(tenant_id: str, kb_ids: list[str], query: str, top_k: int = TOP_K_DEFAULT,
                        ctx=None, similarity_threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """单槽位检索。query 已由上游清洗；异常向上抛由编排层兜底为该槽位空结果。
    ctx 为 load_retrieval_ctx 产物（多槽并发时复用，省每槽重复加载）。
    similarity_threshold 供二档宽检索传更低阈值（默认一档现状值）。"""
    kbs, embd_mdl = ctx if ctx else load_retrieval_ctx(tenant_id, kb_ids)
    page_size = max(TOP_K_MIN, min(int(top_k or TOP_K_DEFAULT), TOP_K_MAX))
    kbinfos = await settings.retriever.retrieval(
        query, embd_mdl, [kb.tenant_id for kb in kbs], [kb.id for kb in kbs],
        1, page_size, similarity_threshold, VECTOR_SIMILARITY_WEIGHT,
        aggs=True, rank_feature=label_question(query, kbs))
    return [_clip_chunk(ck) for ck in kbinfos.get("chunks", [])]
```

3c. 模块级新函数（放 `_retrieve_all` 之前）：

```python
def _fulltext_query(entities: dict, name: str) -> str:
    """二档组合词：项目/名称/标题类实体值优先（最多 _TIER2_ENTITY_MAX 个），
    其余实体值随后，再拼 __context__ 检索语境与填写点名称；空段剔除后整体截断。"""
    prio, rest, ctx_val = [], [], ""
    for k, v in (entities or {}).items():
        v = str(v or "").strip()
        if not v:
            continue
        k = str(k)
        if k == "__context__":
            ctx_val = v
        elif any(w in k for w in _ENTITY_PRIO_WORDS):
            prio.append(v)
        else:
            rest.append(v)
    parts = (prio + rest)[:_TIER2_ENTITY_MAX]
    if ctx_val:
        parts.append(ctx_val)
    parts.append(str(name or "").strip())
    return _clean_for_prompt(" ".join(p for p in parts if p), QUERY_MAX)
```

3d. `_retrieve_all` 签名加 `entities: dict | None = None`，docstring 追加一段说明（二档仅画布链路 entities 非空时启用），并在函数末尾 `return chunks_by_key, evidence` 之前、一档结果回填循环之后插入：

```python
    # 二档全文降级：仅画布链路（entities 非空）启用。一档证据为空的 llm 槽用
    # 「核心实体+填写点名称」组合词宽检索补一轮，命中片段打 source=fulltext 并入
    # 证据（该槽一档为空，槽内全部为二档片段）；失败/取消语义与一档一致。
    if entities:
        empty = [(key, it) for key, _q, it in todo if not chunks_by_key[key]["chunks"]]
        if empty:
            async def _one2(key: str, it: dict):
                q2 = _fulltext_query(entities, it.get("name") or key)
                async with sem:
                    if _should_cancel(should_cancel):
                        raise GenerateCancelled()
                    chunks = await retrieve_slot(
                        tenant_id, kb_ids, q2,
                        min(int(it.get("top_k") or TOP_K_DEFAULT) * 2, TOP_K_MAX),
                        ctx=ctx, similarity_threshold=FULLTEXT_SIMILARITY_THRESHOLD)
                return key, [dict(c, source="fulltext") for c in chunks]

            results2 = await asyncio.gather(
                *[_one2(key, it) for key, it in empty], return_exceptions=True)
            for res in results2:
                if isinstance(res, GenerateCancelled):
                    logger.info("_retrieve_all tier2 cancelled: task=%s", task_id)
                    raise res
            for (key, _it), res in zip(empty, results2):
                if isinstance(res, BaseException):
                    logger.warning("tier2 retrieve_slot failed, task=%s key=%s: %s",
                                   task_id, key, res)
                    continue
                _key, chunks2 = res
                if chunks2:
                    chunks_by_key[key]["chunks"] = chunks2
                    evidence[key]["chunks"] = chunks2
    return chunks_by_key, evidence
```

注意：`_retrieve_all` 中 `q2` 可能为空串（实体与 name 全空）——`retrieve_slot` 空查询会白打 ES，在 `_one2` 里 `q2` 为空时直接 `return key, []` 跳过：

```python
            async def _one2(key: str, it: dict):
                q2 = _fulltext_query(entities, it.get("name") or key)
                if not q2:
                    return key, []
                async with sem:
                    ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -v`
Expected: 全部通过（含既有 `_retrieve_all` 用例——entities 缺省 None 行为不变）

- [ ] **Step 5: 提交**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): 一档空槽二档实体组合词宽检索降级"
```

---

### Task 3: fulltext 证据标签进产值 prompt

**Files:**
- Modify: `rag/svr/template_fill/executor.py`（`GENERATE_SYSTEM` line 147-152；`_build_msg` 内 evidence 拼接 line 429-432）
- Test: `test/test_template_fill_executor.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

```python
# ---------- fulltext 证据标签 ----------

def test_generate_values_fulltext_evidence_label(monkeypatch):
    """fulltext 片段标签 [全文匹配 片段N]，普通片段标签不变。"""
    from rag.svr.template_fill import executor
    captured = {}

    async def fake_chat(sys, msgs):
        captured["sys"] = sys
        captured["msg"] = msgs[0]["content"]
        return '{"k0": "v"}'

    monkeypatch.setattr(executor, "_build_chat_mdl",
                        lambda tenant: types.SimpleNamespace(async_chat=fake_chat))
    phs = [{"key": "k0", "name": "字段0", "fill_mode": "llm"}]
    chunks = {"k0": {"chunks": [{"content": "证据A", "doc_id": "d", "doc_name": "n",
                                 "similarity": 0.9, "source": "fulltext"},
                                {"content": "证据B", "doc_id": "d", "doc_name": "n",
                                 "similarity": 0.9}], "query": "q"}}
    values, missing = executor._run_async(executor.generate_values("t", phs, chunks))
    assert "[全文匹配 片段1] 证据A" in captured["msg"]
    assert "[片段2] 证据B" in captured["msg"]
    assert values == {"k0": "v"} and missing == set()
    assert "全文匹配" in captured["sys"], "system prompt 需含 fulltext 归纳编写规则"


def test_generate_system_has_fulltext_rule():
    from rag.svr.template_fill import executor
    assert "全文匹配" in executor.GENERATE_SYSTEM
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k fulltext -v`
Expected: FAIL（标签是 `[片段1]`；GENERATE_SYSTEM 无该规则）

- [ ] **Step 3: 实现**

3a. `GENERATE_SYSTEM` 追加第 5 条规则：

```python
GENERATE_SYSTEM = (
    "你是文档填写引擎。根据每个字段的【检索证据】填写字段值。规则：\n"
    "1. 只准依据证据作答，禁止编造；证据中找不到的字段值输出 null。\n"
    "2. 遵守字段约束（类型/最大长度）。\n"
    "3. 字段带 default_value 时为该字段上次填写值，可作参考；证据与之冲突时以证据为准。\n"
    "4. 只输出一个 JSON 对象：{\"字段key\": \"字段值或null\", ...}，不要输出任何其他文字。\n"
    "5. 片段标注[全文匹配]的字段，可基于片段内容的上下文归纳编写字段值；仍禁止无中生有。")
```

3b. `_build_msg` 的 evidence 拼接（原 line 430-431）改为：

```python
            joined = "\n---\n".join(
                (f"[全文匹配 片段{j + 1}] " if c.get("source") == "fulltext"
                 else f"[片段{j + 1}] ") + c["content"]
                for j, c in enumerate(chunks[:MAX_EVIDENCE_CHUNKS])) or "（无检索证据）"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -k "fulltext or generate_values" -v`
Expected: 全部 passed（含既有 generate_values 用例——普通片段标签路径不变）

- [ ] **Step 5: 提交**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): fulltext 片段标签与归纳编写 prompt 规则"
```

---

### Task 4: _entities 保留键 + execute_task 接线

**Files:**
- Modify: `rag/svr/template_fill/executor.py`（`CANVAS_RESERVED_KEYS` line 172-174；`split_canvas_params` line 190-210；`_execute_task_async` line 933-945）
- Test: `test/test_template_fill_executor.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

```python
# ---------- _entities 保留键 ----------

def test_split_canvas_params_entities_payload():
    from rag.svr.template_fill import executor
    clean, opts = executor.split_canvas_params(
        {"_entities": {"项目名称": "X", "__context__": "市政房建"}, "k": "v"})
    assert opts["entities"] == {"项目名称": "X", "__context__": "市政房建"}
    assert clean == {"k": "v"}


def test_split_canvas_params_entities_malformed():
    """非 dict 载荷一律按空处理，不炸 pipeline。"""
    from rag.svr.template_fill import executor
    for bad in (None, "x", 123, [1, 2]):
        _, opts = executor.split_canvas_params({"_entities": bad})
        assert opts["entities"] == {}


def test_canvas_reserved_keys_include_entities():
    """_entities 必须在保留键集合（is_canvas 判定与 REST 剥离都依赖它）。"""
    from rag.svr.template_fill import executor
    assert "_entities" in executor.CANVAS_RESERVED_KEYS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k "entities_payload or entities_malformed or reserved_keys_include" -v`
Expected: FAIL（`_entities` 不在保留键；opts 无 `entities` 键）

- [ ] **Step 3: 实现**

3a. `CANVAS_RESERVED_KEYS` 追加：

```python
CANVAS_RESERVED_KEYS = ("_direct_values", "_changed_keys",
                        "_retrieve_skip_keys", "_user_file_text",
                        "_baseline_values", "_entities")
```

3b. `split_canvas_params` 的 opts dict 追加（`baseline_values` 项之后）：

```python
    en = params.get("_entities")
    opts["entities"] = ({str(k): str(v) for k, v in en.items()}
                        if isinstance(en, dict) else {})
```

3c. `_execute_task_async`：`cancel_probe = ...` 行之后追加一行：

```python
    entities = opts["entities"] if is_canvas else None
```

并把 ② 步的 `_retrieve_all(...)` 调用改为：

```python
        chunks_by_key, evidence = await _retrieve_all(
            task.tenant_id, placeholders, kb_ids, clean_params, task_id=task_id,
            skip_keys=skip_keys, should_cancel=cancel_probe, entities=entities)
```

（`create_fill_task` 的 REST 剥离基于 `CANVAS_RESERVED_KEYS` 集合，自动覆盖新键，`api/` 层零改动。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): _entities 画布保留键与执行器接线"
```

---

### Task 5: 画布节点 —— _canvas_task_params 带实体 + _merged_entities

**Files:**
- Modify: `agent/component/template_fill.py`（`_canvas_task_params` line 98-125；模块级新增 `_merged_entities`）
- Test: `test/test_template_fill_delegate.py`（`TestCanvasTaskParams` 类内追加）

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_delegate.py` 的 `TestCanvasTaskParams` 类内追加：

```python
    def test_entities_written_to_params(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="需求", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")],
            user_file_text="", entities={"项目名称": "A项目", "__context__": "市政房建"})
        assert params["_entities"] == {"项目名称": "A项目", "__context__": "市政房建"}

    def test_entities_default_empty(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")], user_file_text="")
        assert params["_entities"] == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_delegate.py::TestCanvasTaskParams -v`
Expected: FAIL（`entities` 参数不存在）

- [ ] **Step 3: 实现**

3a. `_canvas_task_params` 签名追加 kw-only 参数并在函数体写键：

```python
def _canvas_task_params(begin_fields: dict, query: str, decision: dict | None,
                        llm_item_keys: set, placeholders: list[dict],
                        user_file_text: str,
                        *, baseline_values: dict | None = None,
                        entities: dict | None = None) -> dict:
```

（docstring 追加一行：`entities` 为各范本实体分析 entities 并集，供 executor 二档检索拼词。）

函数体 `params["_baseline_values"] = dict(baseline_values or {})` 之后追加：

```python
    params["_entities"] = dict(entities or {})
```

3b. 模块级（`_canvas_task_params` 之前）新增：

```python
def _merged_entities(entity_map: dict) -> dict:
    """各范本实体分析结果（extract_entities 返回值）的 entities 并集（先到先得），
    供 executor 二档检索拼词；__context__ 取首个非空。"""
    merged: dict = {}
    for m in (entity_map or {}).values():
        for k, v in ((m or {}).get("entities") or {}).items():
            merged.setdefault(str(k), str(v))
    return merged
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_delegate.py -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add agent/component/template_fill.py test/test_template_fill_delegate.py
git commit -m "feat(template-fill): 画布任务参数携带 _entities 实体集"
```

---

### Task 6: 确认编排 —— 实体抽取并行 + direct_value 下发 + 弹卡条件 + 返回元组

**Files:**
- Modify: `agent/component/template_fill.py`（`_confirm_changed_fields` line 332-497；invoke 调用点 line 707-708、748-752）
- Test: `test/test_agent_fill_template_component.py`（确认段；**既有 8 处调用点需适配元组返回**）

**关键契约变化：`_confirm_changed_fields` 返回值从 `dict` 改为 `(decisions, entity_map)` 元组，唯一调用方与全部既有测试同步适配。**

- [ ] **Step 1: 适配既有测试（先改桩，保证红得有意义）**

1a. `_confirm_component` helper（`test/test_agent_fill_template_component.py`）内追加实体分析桩（防止既有用例真调 LLM）：

```python
def _confirm_component(monkeypatch, canvas_task_id="task-9"):
    """确认编排专用组件：画布带 task_id（真实 canvas 构造时注入，缺省回退空串即跳过等待）。"""
    canvas = FakeCanvas()
    canvas.task_id = canvas_task_id

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    return _make_component(TemplateFillParam(), canvas=canvas)
```

1b. 全文件 8 处 `cpn._confirm_changed_fields(...)` 调用改元组解包。前 7 处（line 623/662/702/748/781/812/1017）形如：

```python
    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
```

line 835 一处改为：

```python
    assert asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))[0] == {}
```

- [ ] **Step 2: 写新失败测试**

在确认段（`test_confirm_wait_timeout_uses_predicted` 附近）追加：

```python
def test_confirm_candidates_carry_direct_value(monkeypatch):
    """实体直填值进 confirm_pending 事件 candidates.direct_value（前端预填输入框）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_predict(*a, **kw):
        return set()

    monkeypatch.setattr(fill_template.executor, "predict_changed_fields", fake_predict)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {"a": "直填甲"}, "entities": {"项目名称": "A项目"}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    decisions, emap = asyncio.run(
        cpn._confirm_changed_fields(_chosen_with_defaults(), "需求", {}))
    events = _drain_events(cpn)
    pending_ev = [e for e in events if e["stage"] == "confirm_pending"][0]
    cands = pending_ev["confirm_templates"][0]["candidates"]
    by_key = {c["key"]: c for c in cands}
    assert by_key["a"]["direct_value"] == "直填甲"
    assert by_key["b"]["direct_value"] == ""
    # 兜底 values 带直填值（超时未确认也不丢用户原话给出的值）
    assert decisions["t1"]["values"] == {"a": "直填甲"}
    assert emap["t1"]["entities"] == {"项目名称": "A项目"}


def test_confirm_pops_card_for_direct_without_defaults(monkeypatch):
    """无默认值字段但有实体直填值 → 也弹确认卡（现状是直接跳过）。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)

    class FakeRedis:
        def get(self, k):
            return None

        def delete(self, k):
            raise AssertionError

    monkeypatch.setattr(fill_template, "REDIS_CONN", FakeRedis())

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(fill_template.asyncio, "sleep", fake_sleep)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {"b": "直填乙"}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    chosen = [{"template_id": "t1", "name": "范本",
               "_placeholders": [{"key": "b", "name": "乙", "fill_mode": "llm"}]}]
    decisions, _emap = asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))
    events = _drain_events(cpn)
    assert any(e["stage"] == "confirm_pending" for e in events), "直填值必须弹卡供确认"
    assert decisions["t1"]["values"] == {"b": "直填乙"}


def test_confirm_skipped_when_no_defaults_no_direct(monkeypatch):
    """无默认值且无直填值 → 维持现状跳过确认（返回空 decisions），不推事件。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)

    async def fake_extract(tenant_id, query, placeholders, should_cancel=None):
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    chosen = [{"template_id": "t1", "name": "范本",
               "_placeholders": [{"key": "b", "name": "乙", "fill_mode": "llm"}]}]
    decisions, emap = asyncio.run(cpn._confirm_changed_fields(chosen, "需求", {}))
    assert decisions == {}
    assert emap["t1"] == {"direct": {}, "entities": {}}
    assert _drain_events(cpn) == []


def test_confirm_incremental_skips_entity_extraction(monkeypatch):
    """增量范本不做实体抽取（patch 流程有自己的 direct 抽取），extract 不被调用。"""
    import asyncio

    cpn = _confirm_component(monkeypatch)
    monkeypatch.setattr(fill_template, "_CONFIRM_TIMEOUT", 0.05)
    called = {"n": 0}

    async def fake_extract(*a, **kw):
        called["n"] += 1
        return {"direct": {}, "entities": {}}

    monkeypatch.setattr(fill_template.executor, "extract_entities", fake_extract)
    ov = {"t1": {"candidates": [{"key": "a", "name": "甲"}],
                 "predicted": ["a"], "fallback_changed": {"a"}, "fallback_values": {}}}
    asyncio.run(cpn._confirm_changed_fields(
        _chosen_with_defaults(), "需求", {}, incremental_overrides=ov))
    assert called["n"] == 0


def test_merged_entities_union():
    from agent.component.template_fill import _merged_entities
    emap = {"t1": {"direct": {}, "entities": {"项目名称": "A", "__context__": "C1"}},
            "t2": {"direct": {}, "entities": {"采购人": "B", "__context__": "C2"}}}
    assert _merged_entities(emap) == {"项目名称": "A", "__context__": "C1", "采购人": "B"}
    assert _merged_entities({}) == {}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest test/test_agent_fill_template_component.py -k "direct_value or pops_card or skipped_when or incremental_skips or merged_entities" -v`
Expected: FAIL（candidates 无 direct_value；无默认值+有直填不弹卡 等）

- [ ] **Step 4: 实现**

4a. `_confirm_changed_fields` 签名与返回改为元组，docstring 追加：
`返回 (decisions, entity_map)：entity_map 为各范本 extract_entities 结果
（{"direct":..., "entities":...}），调用方取 entities 并集写 params._entities。`

```python
    async def _confirm_changed_fields(self, chosen: list[dict], query: str,
                                      begin_fields: dict,
                                      *, incremental_overrides: dict | None = None
                                      ) -> tuple[dict, dict]:
```

4b. 把「early return」（原 line 360-361 `if not default_map and not incremental_overrides: return {}`）**移到实体抽取之后**，并把预判 try 块（原 line 366-375）替换为并行 gather：

```python
        task_id = getattr(self._canvas, "task_id", "") or ""
        tenant_id = self._canvas.get_tenant_id()
        background = dict(begin_fields)
        if query:
            background["用户需求描述"] = query[:_BEGIN_FIELD_PROMPT_MAX]
        predicted: dict[str, set] = {}
        entity_map: dict[str, dict] = {}
        inc_tids = set((incremental_overrides or {}).keys())

        async def _predict_all():
            out: dict[str, set] = {}
            for tid, items in default_map.items():
                out[tid] = await executor.predict_changed_fields(
                    tenant_id, items, background,
                    should_cancel=lambda: self.check_if_canceled("TemplateFill predict"))
            return out

        async def _extract_all():
            out: dict[str, dict] = {}
            for c in chosen:
                tid = c["template_id"]
                if tid in inc_tids or not d_map.get(tid):
                    continue
                out[tid] = await executor.extract_entities(
                    tenant_id, query, d_map[tid],
                    should_cancel=lambda: self.check_if_canceled("TemplateFill entities"))
            return out

        try:
            predicted, entity_map = await asyncio.gather(_predict_all(), _extract_all())
        except executor.GenerateCancelled:
            # 预判/抽取期取消信号不外逸：与检索/产值阶段同路转 _FillCancelled，
            # 由 invoke 统一收口为 cancelled 终态（不落 failed）
            raise _FillCancelled() from None
        direct_map = {tid: (m or {}).get("direct") or {} for tid, m in entity_map.items()}
        has_direct = any(direct_map.values())
        # 弹卡条件扩展：默认值字段 ∪ 实体直填值，任一非空即弹卡；全空维持现状跳过
        if not default_map and not incremental_overrides and not has_direct:
            return {}, entity_map
```

（原位置 `task_id`/`background` 两行删除，避免重复定义。）

4c. 全量模式 candidates（原 line 397-404）加 `direct_value`：

```python
            confirm_templates.append({
                "template_id": tid,
                "name": name_of.get(tid, ""),
                "candidates": [{"key": it["key"],
                                "name": it.get("name") or it["key"],
                                "default_value": it.get("default_value"),
                                "direct_value": str((direct_map.get(tid) or {}).get(it["key"]) or "")}
                               for it in d_map[tid]],
                "predicted": sorted(predicted.get(tid) or set())})
```

4d. 全量模式兜底 decisions（原 line 424-429）values 带直填值（valid 过滤）：

```python
            items = d_map.get(tid, [])
            valid = {it["key"] for it in items}
            decisions[tid] = {
                "changed": (set(predicted.get(tid) or set())
                            | {it["key"] for it in items
                               if not str(it.get("default_value") or "")}),
                "values": {k: v for k, v in (direct_map.get(tid) or {}).items()
                           if k in valid}}
```

4e. 所有 return 改元组：

- 兜底路径 `if not task_id:` → `return decisions, entity_map`
- 函数末尾 → `return decisions, entity_map`

4f. invoke 调用点（line 707-708）改解包：

```python
        decisions, entity_map = await self._confirm_changed_fields(
            chosen, query, begin_fields, incremental_overrides=incremental_overrides)
```

4g. invoke 的 `TplFillTaskService.insert(... params=_canvas_task_params(...))` 调用（line 748-752）追加实体：

```python
                params=_canvas_task_params(
                    begin_fields, query, decisions.get(tid),
                    {it["key"] for it in _llm_fill_items(c)},
                    c["_placeholders"], user_file_text,
                    baseline_values=(base_for_t["values"] if base_for_t else None),
                    entities=_merged_entities(entity_map)),
```

- [ ] **Step 5: 跑组件+事件全量测试**

Run: `uv run pytest test/test_agent_fill_template_component.py test/test_template_fill_events.py -v`
Expected: 若事件套件有用例因真调 `extract_entities` 报错（invoke 路径也会过确认编排），在该用例的 monkeypatch 区追加与 Step 1a 相同的 `fake_extract` 桩。Expected 最终：全部 passed。

- [ ] **Step 6: 提交**

```bash
git add agent/component/template_fill.py test/test_agent_fill_template_component.py test/test_template_fill_events.py
git commit -m "feat(template-fill): 确认编排实体抽取并行+直填预填+弹卡条件扩展"
```

---

### Task 7: 全量回归

**Files:** 无新改动

- [ ] **Step 1: 后端范本填写相关套件全量**

Run: `uv run pytest test/test_template_fill_executor.py test/test_template_fill_delegate.py test/test_agent_fill_template_component.py test/test_template_fill_events.py test/test_template_fill_tool.py test/test_template_fill_service.py test/test_template_fill_run_snapshot.py test/test_template_fill_progress_api.py test/test_template_fill_blank_slots.py -v`
Expected: 全部 passed。任何失败先判断是否为新契约元组返回/实体桩遗漏，修测试桩而非改实现。

- [ ] **Step 2: 前端确认卡契约回归（零改动验证）**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
Expected: 全部 passed（证明全量模式复用 direct_value 契约无需前端改动）。

- [ ] **Step 3: 提交（如有测试桩修复）**

```bash
git add -A test/
git commit -m "test(template-fill): 检索增强全量回归与桩适配"
```

（无改动则跳过。）

---

### Task 8: CHANGE.md + CLAUDE.md 登记

**Files:**
- Modify: `CHANGE.md`（最新条目插到最上方）
- Modify: `CLAUDE.md`（参考文档表追加一行）

- [ ] **Step 1: CHANGE.md 追加条目**（置顶，格式沿用既有条目：日期+主题+★要点+测试/部署状态），内容要点：

- 新增 `extract_entities`（一次 LLM 调用：直填值抽取 + 实体/语境提取，高可信闸 + 空结果兜底）
- `_retrieve_all` 一档空槽二档「实体组合词」宽检索（`FULLTEXT_SIMILARITY_THRESHOLD=0.1`、top_k 翻倍封顶 20、片段打 `source=fulltext`）
- 产值 prompt fulltext 归纳编写规则
- `_entities` 画布保留键（is_canvas/REST 剥离自动跟随）
- 确认卡弹卡条件扩展 + candidates.direct_value 预填（前端零改动）
- `_confirm_changed_fields` 返回值改元组（破坏性内部契约）
- B端/REST/dry_run 零行为变化；`retrieve_all_shared` 死代码不改

- [ ] **Step 2: CLAUDE.md 参考文档表追加一行**（含设计文档绝对路径 + 一句话简介 + 状态"已编码未部署"）

- [ ] **Step 3: 提交**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: CHANGE.md 登记 2026-09-24 范本填写检索增强 + CLAUDE.md 参考表"
```

---

## 部署清单（用户明确指示后才执行）

- 后端 2 文件成套 SCP + 容器重启：`rag/svr/template_fill/executor.py`、`agent/component/template_fill.py`（bind mount 热更新，仍需 restart 生效 import）
- 前端零改动，无需 build
- 冒烟：容器内 `python -c "from rag.svr.template_fill import executor; from agent.component.template_fill import _merged_entities; print('ok')"`
