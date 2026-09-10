# 范本默认值基线 + 变化字段确认填写 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给范本占位符引入默认值基线（识别提取/填写沉淀/B端编辑三来源），重填时 LLM 只处理变化字段，根治大范本（90页/几百填写点）漏填与重复 LLM 全量产值。

**Architecture:** 默认值存在 `TplTemplateVersion.placeholders` JSON item 内（不加表、零迁移）。P1：anchor 派生现值 + 产值 fallback 兜底 + 成稿后自动沉淀 + B端编辑端点。P2：画布 TemplateFill 节点 1 次 LLM 预判变化字段 → SSE `confirm_pending` 事件暂停 → 用户确认卡片提交 → Redis 唤醒 → 条件执行（默认字段免检索免 LLM）。

**Tech Stack:** Python Quart + Peewee（后端）、agent/canvas 组件、Redis 轮询、React + TanStack Query + SSE（前端）。

**设计文档:** `docs/superpowers/specs/2026-09-10-template-default-baseline-design.md`

**范围约定（对 spec 的两处细化）：**
1. 「识别提取现值」不需要额外 LLM 调用：识别器产出的 `anchor` 就是"将被替换为占位符的原文子串"——已填范本的 anchor 即现值，空范本的 anchor 是下划线/空格留空标记。用纯函数过滤留空标记即可，零成本（Task 1）。
2. spec §4 的 B端「默认值页签」实现为详情页既有 placeholder 表格新增「默认值」列（行内编辑 + 单 key PUT），不做独立页签——功能等价、代码更少。

**关键现状事实（实现者必读）：**
- `rag/svr/template_fill/detector.py`：识别纯函数 + `detect_fill_points`（LLM）。
- `api/db/services/template_fill_service.py`：`save_placeholders`（draft 原地改写 / published 升版）、`_save_as_new_version`、`replace_anchor_to_placeholder`（anchor→`{{key}}` 生成 render 副本）。
- `rag/svr/template_fill/executor.py`：`generate_values`（批量产值，batch=10 并发3）、`_merge_param_values`、`build_values`、`dry_run`（B端试跑）、`_execute_task_async`（B端 pipeline，⑥步状态机）、`retrieve_all_shared`（画布多范本共享检索）。
- `agent/component/template_fill.py`：画布节点。`_invoke_async`：选范本 → 共享检索 → 多范本并行 `_fill_one`（LLM产值→渲染→落 `{tenant_id}-downloads`）。`_push_progress` → SSE `template_fill_progress` 事件。
- 画布取消：`canvas.py:287` `REDIS_CONN.set(f"{task_id}-cancel", "x")`；节点可用 `self._canvas.task_id`；`self.check_if_canceled(reason)` 返回 bool。
- 前端 SSE 管道：`web/src/hooks/template-fill-stream.ts`（事件归约）→ `use-send-message.ts:524` 接线 → `web/src/pages/c-chat/template-fill-progress.tsx`（C端对话 + flow AI 面板共用渲染组件）。
- 前端请求层：`web/src/hooks/use-template-fill-request.ts`（TanStack Query hooks）+ `web/src/utils/api.ts:460-488`（端点表）。
- 测试：`uv run pytest test/test_template_fill_executor.py -v`（monkeypatch 风格，无真 DB/LLM）；前端 `cd web && npx jest template-fill-stream`。
- **禁止部署服务器、禁止重启 Docker**；全部文案中文不进 i18n。

---

## Task 1: anchor 派生默认值纯函数（detector.py）

**Files:**
- Modify: `rag/svr/template_fill/detector.py`
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_utils.py` 末尾追加：

```python
def test_derive_default_from_anchor_blank_markers():
    from rag.svr.template_fill.detector import derive_default_from_anchor
    assert derive_default_from_anchor("") == ""
    assert derive_default_from_anchor(None) == ""
    assert derive_default_from_anchor("______") == ""
    assert derive_default_from_anchor("＿＿＿＿") == ""   # 全角下划线
    assert derive_default_from_anchor("　　") == ""       # 全角空格
    assert derive_default_from_anchor("---") == ""
    assert derive_default_from_anchor("………") == ""
    assert derive_default_from_anchor("N/A") == ""


def test_derive_default_from_anchor_filled_values():
    from rag.svr.template_fill.detector import derive_default_from_anchor
    assert derive_default_from_anchor("XX建设工程有限公司") == "XX建设工程有限公司"
    assert derive_default_from_anchor("2026-09-10") == "2026-09-10"
    assert derive_default_from_anchor("  100万元  ") == "100万元"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k derive_default -v`
Expected: FAIL（ImportError: cannot import name 'derive_default_from_anchor'）

- [ ] **Step 3: 实现**

`rag/svr/template_fill/detector.py` 在 `MAX_ANCHOR_LEN` 之后追加：

```python
# 留空标记判定：仅由空白/下划线（含全角）/横线/点/顿号等组成的 anchor 视为"空范本留空位"，
# 不派生默认值。注意不含字母数字，日期（2026-09-10）、金额等含数字的现值不会误判。
_BLANK_ANCHOR_RE = re.compile(
    r"^[\s_＿\-—–~·*.*×﹏－﹣。．·.,，、;；:：/\\'\"”「」『』（）()【】\[\]……]+$")


def derive_default_from_anchor(anchor) -> str:
    """已填现值提取（纯函数）：anchor 是识别器选中的"将被替换为 {{key}} 的原文子串"。
    已填范本的 anchor 即现值 → 作为 detected 默认值；空范本的 anchor 是留空标记 → 无默认值。
    返回空串表示无默认值。截断对齐 MAX_ANCHOR_LEN（防御旧数据超长）。"""
    text = str(anchor or "").strip()
    if not text or _BLANK_ANCHOR_RE.fullmatch(text) or text.upper() in ("N/A", "NA", "NONE", "NULL"):
        return ""
    return text[:MAX_ANCHOR_LEN]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -k derive_default -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/detector.py test/test_template_fill_utils.py
git commit -m "feat(template): anchor 派生默认值纯函数（识别时零成本提取已填现值）"
```

---

## Task 2: save_placeholders 默认值合并（继承 + 派生）

**Files:**
- Modify: `api/db/services/template_fill_service.py`
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `test/test_template_fill_utils.py`：

```python
def test_merge_defaults_explicit_wins():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    items = [{"key": "a", "anchor": "旧值", "default_value": "人工改的", "default_source": "manual"},
             {"key": "b", "anchor": "甲公司"}]
    out = S._merge_defaults(items, [{"key": "a", "default_value": "上版默认", "default_source": "auto"}])
    assert out[0]["default_value"] == "人工改的" and out[0]["default_source"] == "manual"
    # b 无显式无旧版 → anchor 派生
    assert out[1]["default_value"] == "甲公司" and out[1]["default_source"] == "detected"


def test_merge_defaults_inherit_by_key():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    items = [{"key": "a", "anchor": "＿＿＿"}]  # 本轮空范本，但旧版有默认值
    out = S._merge_defaults(items, [{"key": "a", "default_value": "历史沉淀", "default_source": "auto"}])
    assert out[0]["default_value"] == "历史沉淀" and out[0]["default_source"] == "auto"


def test_merge_defaults_echo_preserves_source():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    # 前端回显携带空 default_value（清空语义）→ 保留为空，不得重新从 anchor 派生
    items = [{"key": "a", "anchor": "现值", "default_value": "", "default_source": ""}]
    out = S._merge_defaults(items, [])
    assert out[0]["default_value"] == "" and out[0]["default_source"] == ""


def test_merge_defaults_new_key_from_blank_anchor():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    out = S._merge_defaults([{"key": "k", "anchor": "______"}], [])
    assert out[0]["default_value"] == "" and out[0]["default_source"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k merge_defaults -v`
Expected: FAIL（AttributeError: _merge_defaults）

- [ ] **Step 3: 实现**

`api/db/services/template_fill_service.py` 在 `TplTemplateVersionService` 类内（`replace_anchor_to_placeholder` 之后）加：

```python
    @staticmethod
    def _merge_defaults(items: list, prev_placeholders) -> list:
        """占位符默认值合并（纯函数，save_placeholders 落库前调用）。优先级：
        1. 条目显式携带 default_value 字段（前端回显/默认值编辑链路）→ 原样保留
           （含"显式清空"：value 空串且 source 空 = 用户清了默认值，不得再派生）；
        2. 同 key 旧版本已有默认值 → 继承（重新识别/改填写点不丢基线）；
        3. 否则从 anchor 派生（已填范本识别时零成本提取现值，source=detected）。
        就地修改并返回 items。"""
        from rag.svr.template_fill.detector import derive_default_from_anchor
        prev_map = {it.get("key"): it for it in (prev_placeholders or []) if isinstance(it, dict) and it.get("key")}
        for it in items:
            key = it.get("key")
            if not key:
                continue
            if "default_value" in it:
                val = str(it.get("default_value") or "")
                src = str(it.get("default_source") or "")
                it["default_value"], it["default_source"] = val, (src or ("manual" if val else ""))
                continue
            prev = prev_map.get(key) or {}
            if str(prev.get("default_value") or ""):
                it["default_value"] = prev["default_value"]
                it["default_source"] = prev.get("default_source") or "detected"
                continue
            derived = derive_default_from_anchor(it.get("anchor"))
            it["default_value"] = derived
            it["default_source"] = "detected" if derived else ""
        return items
```

然后改 `save_placeholders`，在 `ver = cls.latest(tpl_id)` 之后、`if template.get(...)` 分支之前插入一行：

```python
        placeholders = cls._merge_defaults(placeholders, ver.placeholders)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -k merge_defaults -v`
Expected: 4 PASS

- [ ] **Step 5: 回归既有测试**

Run: `uv run pytest test/test_template_api_routes.py test/test_template_fill_utils.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add api/db/services/template_fill_service.py test/test_template_fill_utils.py
git commit -m "feat(template): save_placeholders 默认值合并（显式>继承>anchor派生）"
```

---

## Task 3: 产值 fallback 默认值（executor，P1 兜底核心）

**Files:**
- Modify: `rag/svr/template_fill/executor.py`
- Test: `test/test_template_fill_executor.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `test/test_template_fill_executor.py`：

```python
def test_merge_default_values_fills_missing_only(monkeypatch):
    from rag.svr.template_fill import executor
    ph = [{"key": "a", "fill_mode": "llm", "default_value": "默认A"},
          {"key": "b", "fill_mode": "llm", "default_value": "默认B"},
          {"key": "c", "fill_mode": "llm", "default_value": ""},
          {"key": "d", "fill_mode": "param", "default_value": "不生效"}]
    generated, missing = {"a": "LLM值"}, {"b", "c", "d"}
    executor._merge_default_values(ph, generated, missing)
    assert generated["b"] == "默认B"
    assert "b" not in missing and "a" in missing  # a 已有值不动
    assert "c" in missing and "d" in missing      # 无默认值/param 不兜底


def test_build_msg_includes_default_value_hint():
    from rag.svr.template_fill import executor
    it = {"key": "k", "name": "字段", "description": "", "constraints": {},
          "default_value": "上次值"}
    ph_list = [it]
    chunks = {"k": {"chunks": [{"content": "证据", "doc_id": "", "doc_name": "", "similarity": 1.0}], "query": ""}}
    # _build_msg 是 generate_values 内部闭包，通过公共行为验证：直接构造同参调用
    spec_payload = executor._default_hint(it)
    assert spec_payload == "上次值"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k "merge_default or default_hint" -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`rag/svr/template_fill/executor.py`：

(a) `GENERATE_SYSTEM` 追加一条规则（字符串拼接处修改）：

```python
GENERATE_SYSTEM = (
    "你是文档填写引擎。根据每个字段的【检索证据】填写字段值。规则：\n"
    "1. 只准依据证据作答，禁止编造；证据中找不到的字段值输出 null。\n"
    "2. 遵守字段约束（类型/最大长度）。\n"
    "3. 字段带 default_value 时为该字段上次填写值，可作参考；证据与之冲突时以证据为准。\n"
    "4. 只输出一个 JSON 对象：{\"字段key\": \"字段值或null\", ...}，不要输出任何其他文字。")
```

(b) 模块级新增两个函数（放在 `_apply_constraints` 之后）：

```python
def _default_hint(it: dict) -> str:
    """产值 prompt 的默认值参考提示（无则空串）。"""
    return _clean_for_prompt(str(it.get("default_value") or ""), 100)


def _merge_default_values(placeholders: list[dict], generated: dict, missing: set):
    """P1 兜底：LLM 提取不到（missing）的 llm 字段直取默认值。只填 missing、
    不覆盖已有产值；param 模式不兜底（param 直取失败无默认语义）。就地修改。"""
    for it in placeholders:
        if _norm_fill_mode(it) != "llm":
            continue
        key = it.get("key")
        if key and key in missing and str(it.get("default_value") or ""):
            generated[key] = it["default_value"]
            missing.discard(key)
```

(c) `_build_msg`（`generate_values` 内部）的 spec.append 字典中 `"constraints"` 之后加一项：

```python
                         "default_value": _default_hint(it),
```

(d) 三处调用点在 `_merge_param_values(...)` 之后各插一行 `executor` 内为同模块直接调用：
- `dry_run`：`_merge_param_values(placeholders, generated, missing, params)` 之后 → `_merge_default_values(placeholders, generated, missing)`
- `_execute_task_async` ④步：`_merge_param_values(placeholders, generated, missing, params)` 之后 → `_merge_default_values(placeholders, generated, missing)`
- `agent/component/template_fill.py` `_fill_one` 中 `executor._merge_param_values(placeholders, generated, missing, begin_fields)` 之后 → `executor._merge_default_values(placeholders, generated, missing)`

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -v`
Expected: 全部 PASS（含既有）

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/executor.py agent/component/template_fill.py test/test_template_fill_executor.py
git commit -m "feat(template): LLM 提取不到时 fallback 默认值 + 产值 prompt 注入默认值参考"
```

---

## Task 4: 成稿后自动沉淀（sediment）

**Files:**
- Modify: `api/db/services/template_fill_service.py`
- Modify: `rag/svr/template_fill/executor.py`（`_execute_task_async` ⑥ 接入）
- Modify: `agent/component/template_fill.py`（`_fill_one` 接入）
- Test: `test/test_template_fill_utils.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_sediment_into_placeholders():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    ph = [{"key": "a", "default_value": "旧", "default_source": "auto"},
          {"key": "b", "default_value": "人工", "default_source": "manual"},
          {"key": "c", "default_value": "", "default_source": ""}]
    values = {"a": "新A", "b": "新B", "c": "新C", "empty": ""}
    changed = S._sediment_into_placeholders(ph, values)
    assert changed is True
    assert ph[0]["default_value"] == "新A" and ph[0]["default_source"] == "auto"
    assert ph[1]["default_value"] == "人工"    # manual 不被覆盖
    assert ph[2]["default_value"] == "新C"
    # 空值不沉淀（不会抹掉已有默认值）


def test_sediment_override_manual():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    ph = [{"key": "b", "default_value": "人工", "default_source": "manual"}]
    S._sediment_into_placeholders(ph, {"b": "用户直填"}, override_keys={"b"})
    assert ph[0]["default_value"] == "用户直填" and ph[0]["default_source"] == "auto"


def test_sediment_no_change_returns_false():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    ph = [{"key": "a", "default_value": "同值", "default_source": "auto"}]
    assert S._sediment_into_placeholders(ph, {"a": "同值"}) is False
    assert S._sediment_into_placeholders(ph, {}) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k sediment -v`
Expected: FAIL

- [ ] **Step 3: 实现纯函数 + DB 包装**

`template_fill_service.py` `TplTemplateVersionService` 类内追加：

```python
    @staticmethod
    def _sediment_into_placeholders(placeholders: list, values: dict,
                                    override_keys: set | None = None) -> bool:
        """产值沉淀纯逻辑：非空值写入 default_value（source=auto）。
        manual 不覆盖，除非 key ∈ override_keys（用户确认卡片显式给值）。
        空值（渲染留空）不沉淀——不得抹掉历史默认值。返回是否有变更。"""
        override = override_keys or set()
        changed = False
        for it in placeholders:
            key = it.get("key") if isinstance(it, dict) else None
            if not key:
                continue
            val = (values or {}).get(key)
            if val in (None, ""):
                continue
            if str(it.get("default_source") or "") == "manual" and key not in override:
                continue
            new_val = str(val)
            if it.get("default_value") == new_val and it.get("default_source") == "auto":
                continue
            it["default_value"] = new_val
            it["default_source"] = "auto"
            changed = True
        return changed

    @classmethod
    @DB.connection_context()
    def sediment_defaults(cls, template_id: str, version_id: str, values: dict,
                          override_keys: set | None = None) -> bool:
        """填写成功后把产值沉淀为该版本默认值。失败由调用方兜底（仅日志，
        不影响成稿交付）。返回是否有变更；版本行不存在返回 False。"""
        ver = cls.model.select().where(
            (cls.model.id == version_id) & (cls.model.template_id == template_id)).first()
        if ver is None:
            return False
        placeholders = ver.placeholders or []
        if not cls._sediment_into_placeholders(placeholders, values, override_keys):
            return False
        ver.placeholders = placeholders
        ver.save()
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_utils.py -k sediment -v`
Expected: 3 PASS

- [ ] **Step 5: 接入两条填写链路**

(a) `executor.py` `_execute_task_async` ⑥步把现有：

```python
    if not svc.update_status(task_id, "rendering", "done",
                             values={"cells": cell_status, "render": values},
                             evidence=evidence, result_file_id=result_obj):
        logger.warning("fill task final status CAS failed, task=%s result_obj=%s "
                       "(result object in storage without terminal status)", task_id, result_obj)
```

改为（沉淀只在 done 落库成功后执行）：

```python
    if not svc.update_status(task_id, "rendering", "done",
                             values={"cells": cell_status, "render": values},
                             evidence=evidence, result_file_id=result_obj):
        logger.warning("fill task final status CAS failed, task=%s result_obj=%s "
                       "(result object in storage without terminal status)", task_id, result_obj)
        return
    # ⑦ 产值沉淀为默认值（auto）：失败仅告警，不影响任务终态
    try:
        tpl_svc.TplTemplateVersionService.sediment_defaults(task.template_id, ver.id, values)
    except Exception:
        logger.warning("sediment_defaults failed, task=%s", task_id, exc_info=True)
```

(b) `agent/component/template_fill.py` `_fill_one` 中 `out = renderer.render(...)` 成功之后、`doc_id = get_uuid()` 之前追加：

```python
        # 产值沉淀为默认值（auto）：失败仅告警，不影响成稿交付
        try:
            from api.db.services.template_fill_service import TplTemplateVersionService
            TplTemplateVersionService.sediment_defaults(
                cand["template_id"], cand["_ver"].id, values)
        except Exception:
            logger.warning("sediment_defaults failed, template=%s", cand["template_id"], exc_info=True)
```

- [ ] **Step 6: 回归 + Commit**

Run: `uv run pytest test/test_template_fill_executor.py test/test_template_fill_utils.py -v`
Expected: 全部 PASS

```bash
git add api/db/services/template_fill_service.py rag/svr/template_fill/executor.py agent/component/template_fill.py test/test_template_fill_utils.py
git commit -m "feat(template): 填写成功后产值自动沉淀为默认值（auto，manual 保护）"
```

---

## Task 5: B端默认值编辑端点

**Files:**
- Modify: `api/db/services/template_fill_service.py`（`update_defaults`）
- Modify: `api/apps/restful_apis/template_api.py`
- Test: `test/test_template_fill_utils.py`（追加纯逻辑部分）

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_update_defaults_validation():
    from api.db.services.template_fill_service import TplTemplateVersionService as S
    ph = [{"key": "a", "default_value": "", "default_source": ""},
          {"key": "b", "default_value": "x", "default_source": "auto"}]
    ok, err = S._apply_defaults_edits(ph, {"a": "新值", "b": "", "ghost": "y"})
    assert ok is False and "ghost" in err
    ok, err = S._apply_defaults_edits(ph, {"a": "新值", "b": ""})
    assert ok is True
    assert ph[0]["default_value"] == "新值" and ph[0]["default_source"] == "manual"
    assert ph[1]["default_value"] == "" and ph[1]["default_source"] == ""  # 空串=清空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_utils.py -k update_defaults -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`template_fill_service.py` `TplTemplateVersionService` 类内追加：

```python
    @staticmethod
    def _apply_defaults_edits(placeholders: list, defaults: dict) -> tuple:
        """B端默认值编辑纯逻辑：{key: value}，空串=清空。未知 key 整体拒绝
        （防手改请求错别字静默丢编辑）。就地修改。返回 (ok, error_msg)。"""
        keys = {it.get("key") for it in placeholders if isinstance(it, dict)}
        unknown = [k for k in defaults if k not in keys]
        if unknown:
            return False, f"未知填写点 key: {', '.join(str(k) for k in unknown[:5])}"
        for it in placeholders:
            k = it.get("key")
            if k not in defaults:
                continue
            val = str(defaults[k] or "").strip()
            it["default_value"] = val
            it["default_source"] = "manual" if val else ""
        return True, ""

    @classmethod
    @DB.connection_context()
    def update_defaults(cls, template_id: str, defaults: dict) -> tuple:
        """B端默认值编辑：只改 latest 版本 placeholders 内的 default_value/default_source，
        不动 MinIO 文件、不升版本（默认值是元数据，render/original 无关）。
        返回 (ok, msg)。"""
        ver = cls.latest(template_id)
        if ver is None:
            return False, "模板版本不存在"
        placeholders = ver.placeholders or []
        ok, msg = cls._apply_defaults_edits(placeholders, defaults or {})
        if not ok:
            return False, msg
        ver.placeholders = placeholders
        ver.save()
        return True, ""
```

- [ ] **Step 4: 加 API 端点**

`api/apps/restful_apis/template_api.py`（`save_placeholders` 端点之后）：

```python
@manager.route("/template/fill/<template_id>/defaults", methods=["PUT"])
@login_required
async def update_template_defaults(template_id: str):
    """B端默认值编辑：{defaults: {key: value}}，空串=清空该字段默认值。"""
    body = await request.get_json(silent=True) or {}
    defaults = body.get("defaults")
    if not isinstance(defaults, dict) or not defaults:
        return get_error_data_result("defaults 不能为空")
    tpl, err = await _load_template(template_id)
    if err:
        return err
    ok, msg = TplTemplateVersionService.update_defaults(template_id, defaults)
    if not ok:
        return get_error_data_result(msg)
    return get_result(data={"id": template_id})
```

- [ ] **Step 5: 跑测试 + 回归**

Run: `uv run pytest test/test_template_fill_utils.py test/test_template_api_routes.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add api/db/services/template_fill_service.py api/apps/restful_apis/template_api.py test/test_template_fill_utils.py
git commit -m "feat(template): B端默认值编辑端点 PUT /template/fill/<id>/defaults"
```

---

## Task 6: 前端 B端默认值列编辑

**Files:**
- Modify: `web/src/hooks/use-template-fill-request.ts`
- Modify: `web/src/utils/api.ts`（~行 477 附近）
- Modify: `web/src/pages/template-fill/placeholder-table.tsx`
- Modify: `web/src/pages/template-fill/detail.tsx`（接线）

- [ ] **Step 1: 类型与端点**

`use-template-fill-request.ts` 的 `TplPlaceholder` 接口 `top_k: number;` 之后追加：

```ts
  /** 默认值基线：空串/undefined=无；source 标记来源 */
  default_value?: string;
  default_source?: 'detected' | 'auto' | 'manual' | '';
```

文件末尾追加 hook：

```ts
// 更新默认值（单 key 即时保存；空串=清空）
export function useUpdateTemplateFillDefaults() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      defaults,
    }: {
      id: string;
      defaults: Record<string, string>;
    }) => {
      const { data } = await request.post(
        `${api.updateTemplateFillDefaults(id)}`,
        { data: { defaults }, method: 'PUT' as const },
      );
      if (data.code !== 0) {
        throw new Error(data.message || '保存默认值失败');
      }
      return data.data as { id: string };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['templateFillDetail'] });
    },
  });
}
```

> 注意：若项目 request 封装不支持 `{ method: 'PUT' }` 内联写法，则按既有 `request.put(url, { data })` 风格改写（实现时以 `web/src/utils/request.ts` 实际导出为准，保持与项目一致）。

`utils/api.ts` 范本区追加：

```ts
  updateTemplateFillDefaults: (id: string) =>
    `${restAPIv1}/template/fill/${id}/defaults`,
```

- [ ] **Step 2: placeholder-table 加「默认值」列**

`placeholder-table.tsx`：
1. `emptyPlaceholder()` 返回对象追加 `default_value: '', default_source: '' as const,`
2. 表头 `TableHead` 行在「操作」列前加 `<TableHead className="w-[180px]">默认值</TableHead>`
3. 行内在对应位置加单元格（受控 Input + 失焦保存 + 来源徽标）：

```tsx
function DefaultValueCell({
  row,
  templateId,
  onSave,
  saving,
}: {
  row: TplPlaceholder;
  templateId: string;
  onSave: (key: string, value: string) => void;
  saving: boolean;
}) {
  const [val, setVal] = React.useState(row.default_value || '');
  React.useEffect(() => setVal(row.default_value || ''), [row.default_value]);
  const commit = () => {
    if ((row.default_value || '') !== val.trim()) onSave(row.key, val.trim());
  };
  return (
    <div className="flex items-center gap-1">
      <Input
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        placeholder="空=无默认值"
        className="h-7 text-xs"
        disabled={saving}
      />
      {row.default_source === 'manual' && (
        <span className="shrink-0 rounded bg-[#EFF4FF] px-1 text-[10px] text-[#1a66fb]">手动</span>
      )}
      {row.default_source === 'auto' && (
        <span className="shrink-0 rounded bg-[#F0F9EB] px-1 text-[10px] text-[#52c41a]">沉淀</span>
      )}
      {row.default_source === 'detected' && (
        <span className="shrink-0 rounded bg-[#FFF7E6] px-1 text-[10px] text-[#FA8C16]">识别</span>
      )}
    </div>
  );
}
```

> `placeholder-table` 同时被「识别建议确认」场景复用时，新增列仅在有 `templateId` prop 时渲染（上传向导场景还没有模板 id，无法保存）——给组件加可选 prop `templateId?: string`，为空则不渲染该列。

- [ ] **Step 3: detail.tsx 接线**

`detail.tsx` 中给 `PlaceholderTable` 传 `templateId={id}`，并组装保存回调：

```tsx
const saveDefaults = useUpdateTemplateFillDefaults();
const handleSaveDefault = (key: string, value: string) => {
  saveDefaults.mutate({ id, defaults: { [key]: value } }, {
    onSuccess: () => message.success('默认值已保存'),
    onError: (e) => message.error(e.message || '保存失败'),
  });
};
```

（`message` 用项目现有 toast 封装，与 detail.tsx 既有提示写法保持一致。）

- [ ] **Step 4: 构建验证**

Run: `cd web && npm run build`
Expected: 构建成功无 TS 报错

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/use-template-fill-request.ts web/src/utils/api.ts web/src/pages/template-fill/placeholder-table.tsx web/src/pages/template-fill/detail.tsx
git commit -m "feat(web): 范本详情默认值列（行内编辑/来源徽标/单key保存）"
```

---

## Task 7: P1 联调验证 + CHANGE.md

- [ ] **Step 1: 本地冒烟（不起 Docker，静态确认）**

Run: `uv run pytest test/ -k "template" -v`
Expected: 全部 PASS

- [ ] **Step 2: 更新 CHANGE.md**

`CHANGE.md` 顶部追加条目（增量，不动已有记录）：

```markdown
## 2026-09-10 范本默认值基线 P1
- 主题：模板填写大范本（几百填写点）漏填根治与基线沉淀
- 核心变更：anchor 派生默认值（识别时零成本提取已填现值）→ save_placeholders 默认值合并（显式>按key继承>派生）→ LLM 提取不到 fallback 默认值 → 成稿后产值自动沉淀（manual 保护）→ B端详情默认值列编辑
- 遗留：P2（变化字段预判 + 对话中暂停确认 + 条件执行）另行实施；未部署
```

- [ ] **Step 3: Commit**

```bash
git add CHANGE.md
git commit -m "docs: CHANGE.md 记录范本默认值基线 P1"
```

---

## Task 8: 变化字段预判（executor.predict_changed_fields）

**Files:**
- Modify: `rag/svr/template_fill/executor.py`
- Test: `test/test_template_fill_executor.py`（追加）

- [ ] **Step 1: 写失败测试**

追加：

```python
@pytest.mark.asyncio
async def test_predict_changed_fields_validates_keys(monkeypatch):
    from rag.svr.template_fill import executor

    class FakeMdl:
        async def async_chat(self, sys, msgs):
            return '{"changed": ["a", "ghost", 123]}'

    monkeypatch.setattr(executor, "_build_chat_mdl", lambda tid: FakeMdl())
    items = [{"key": "a", "name": "甲", "default_value": "1"},
             {"key": "b", "name": "乙", "default_value": "2"}]
    got = await executor.predict_changed_fields("t", items, {"需求": "x"})
    assert got == {"a"}


@pytest.mark.asyncio
async def test_predict_changed_fields_failure_returns_empty(monkeypatch):
    from rag.svr.template_fill import executor

    class BoomMdl:
        async def async_chat(self, sys, msgs):
            raise RuntimeError("llm down")

    monkeypatch.setattr(executor, "_build_chat_mdl", lambda tid: BoomMdl())
    items = [{"key": "a", "name": "甲", "default_value": "1"}]
    assert await executor.predict_changed_fields("t", items, {}) == set()


@pytest.mark.asyncio
async def test_predict_changed_fields_chunks_large_list(monkeypatch):
    """超大清单分块串行合并：400 字段 → 2 块，块间结果并集。"""
    from rag.svr.template_fill import executor
    calls = []

    class FakeMdl:
        async def async_chat(self, sys, msgs):
            calls.append(msgs)
            return '{"changed": ["k1"]}'

    monkeypatch.setattr(executor, "_build_chat_mdl", lambda tid: FakeMdl())
    items = [{"key": f"k{i}", "name": f"f{i}", "default_value": "v"} for i in range(400)]
    got = await executor.predict_changed_fields("t", items, {})
    assert got == {"k1"} and len(calls) == 2
```

（若测试文件未装 pytest-asyncio，按文件内既有 async 测试的标记方式对齐。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k predict -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`executor.py` 常量区追加：

```python
PREDICT_CHUNK = 200  # 预判清单分块阈值：400字段×~150字符≈60K，超小窗口模型风险，分块串行
PREDICT_SYSTEM = (
    "你是文档填写助手。给出范本的默认值字段清单（key/名称/当前默认值）和本次填写需求。"
    "请判断哪些字段在本次填写中需要更新（与需求直接相关、或默认值明显是待改样例）。"
    "无关字段一律保持默认。只输出 JSON 对象：{\"changed\": [\"key\", ...]}，不要输出其他文字。")
```

`generate_values` 之后新增：

```python
async def predict_changed_fields(tenant_id: str, default_items: list[dict],
                                 background: dict | None = None,
                                 should_cancel=None) -> set:
    """1 次/块 LLM 调用预判需要更新的默认值字段集合。
    返回校验后 key 集合（编造/非法 key 过滤）；LLM 失败或输出不可解析 → 空集
    （调用方按"全部保持默认，用户确认时手动挑"兜底）。GenerateCancelled 穿透。"""
    if not default_items:
        return set()
    mdl = _build_chat_mdl(tenant_id)
    valid = {it["key"] for it in default_items}
    found: set = set()
    for i in range(0, len(default_items), PREDICT_CHUNK):
        spec = [{"key": _clean_for_prompt(it["key"], NAME_MAX),
                 "name": _clean_for_prompt(it.get("name") or it["key"], NAME_MAX),
                 "default_value": _default_hint(it)}
                for it in default_items[i:i + PREDICT_CHUNK]]
        user_msg = ("## 默认值字段清单\n" + json.dumps(spec, ensure_ascii=False) +
                    "\n\n本次填写需求：" + _clean_for_prompt(
                        json.dumps(background or {}, ensure_ascii=False, default=str),
                        PARAMS_PROMPT_MAX))
        if _should_cancel(should_cancel):
            raise GenerateCancelled()
        try:
            ans = await mdl.async_chat(PREDICT_SYSTEM, [{"role": "user", "content": user_msg}])
            raw = _extract_json(ans)
            keys = raw.get("changed")
            if isinstance(keys, list):
                found |= {k for k in keys if isinstance(k, str) and k in valid}
        except GenerateCancelled:
            raise
        except Exception:
            logger.warning("predict_changed_fields chunk failed; keep defaults", exc_info=True)
    return found
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template): 变化字段 LLM 预判（分块/校验/失败回退空集）"
```

---

## Task 9: confirm 端点 + 画布节点暂停确认与条件执行

**Files:**
- Modify: `api/apps/restful_apis/template_api.py`（confirm 端点）
- Modify: `agent/component/template_fill.py`
- Test: `test/test_agent_fill_template_component.py`（追加，monkeypatch 风格对齐既有用例）

- [ ] **Step 1: confirm 端点**

`template_api.py` 追加（import 区确认有 `import json`、`from rag.utils.redis_conn import REDIS_CONN`——按文件既有 import 风格置于顶部）：

```python
_CONFIRM_TTL = 700  # > 画布节点 600s 等待超时


@manager.route("/template/fill/confirm", methods=["POST"])
@login_required
async def confirm_template_fill():
    """画布 TemplateFill 暂停确认唤醒：写 Redis 确认键，节点轮询读取后继续。
    decisions: {template_id: {changed: [key...], values: {key: value}}}"""
    body = await request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "").strip()
    decisions = body.get("decisions")
    if not task_id or not isinstance(decisions, dict):
        return get_error_data_result("task_id 与 decisions 不能为空")
    clean = {}
    for tid, d in decisions.items():
        if not isinstance(d, dict):
            continue
        clean[str(tid)] = {
            "changed": [str(k) for k in (d.get("changed") or []) if isinstance(k, str)],
            "values": {str(k): str(v) for k, v in (d.get("values") or {}).items()
                       if isinstance(k, str)},
        }
    try:
        REDIS_CONN.set(f"tpl_fill:confirm:{task_id}",
                       json.dumps(clean, ensure_ascii=False), exp=_CONFIRM_TTL)
    except Exception:
        logger.exception("write confirm key failed, task=%s", task_id)
        return get_error_data_result("确认提交失败，请重试")
    return get_result(data={"ok": True})
```

- [ ] **Step 2: 画布节点 — 确认编排方法**

`agent/component/template_fill.py`：模块常量区追加：

```python
_CONFIRM_TIMEOUT = 600          # 确认等待超时（秒），超时按预判自动继续
_CONFIRM_POLL_INTERVAL = 1.5    # Redis 轮询间隔（与 cancel 探针同量级）
```

import 区追加 `from rag.utils.redis_conn import REDIS_CONN`。

`TemplateFill` 类内追加方法：

```python
    async def _confirm_changed_fields(self, chosen: list[dict], query: str,
                                      begin_fields: dict) -> dict:
        """P2 暂停确认：预判各范本疑似变化字段 → 推 confirm_pending 事件挂起等待。
        返回 {template_id: {"changed": set, "values": dict}}；全部选中范本均无
        默认值字段时返回 {}（跳过确认，行为与现状一致）。
        超时/Redis 异常/预判失败 → 按预判结果（或空集）自动继续。"""
        d_map: dict[str, list[dict]] = {}
        for c in chosen:
            items = [it for it in c["_placeholders"]
                     if executor._norm_fill_mode(it) == "llm" and it.get("key")
                     and str(it.get("default_value") or "")]
            if items:
                d_map[c["template_id"]] = items
        if not d_map:
            return {}
        task_id = getattr(self._canvas, "task_id", "") or ""
        background = dict(begin_fields)
        if query:
            background["用户需求描述"] = query[:_BEGIN_FIELD_PROMPT_MAX]
        predicted: dict[str, set] = {}
        for tid, items in d_map.items():
            predicted[tid] = await executor.predict_changed_fields(
                self._canvas.get_tenant_id(), items, background,
                should_cancel=lambda: self.check_if_canceled("TemplateFill predict"))
        name_of = {c["template_id"]: c["name"] for c in chosen}
        # 字段名用 confirm_templates：selected 事件的 templates 已被前端归约占用
        self._push_progress({
            "stage": "confirm_pending", "task_id": task_id,
            "confirm_templates": [{"template_id": tid, "name": name_of.get(tid, ""),
                           "candidates": [{"key": it["key"],
                                           "name": it.get("name") or it["key"],
                                           "default_value": it.get("default_value")}
                                          for it in d_map[tid]],
                           "predicted": sorted(predicted.get(tid) or set())}
                          for tid in d_map]})
        decisions = {tid: {"changed": set(predicted.get(tid) or set()), "values": {}}
                     for tid in d_map}
        if not task_id:
            return decisions
        waited = 0.0
        while waited < _CONFIRM_TIMEOUT:
            if self.check_if_canceled("TemplateFill confirm wait"):
                raise _FillCancelled()
            try:
                raw = REDIS_CONN.get(f"tpl_fill:confirm:{task_id}")
            except Exception:
                logger.warning("confirm poll failed; fallback to predicted", exc_info=True)
                break
            if raw:
                try:
                    REDIS_CONN.delete(f"tpl_fill:confirm:{task_id}")
                    data = json.loads(raw)
                except Exception:
                    logger.warning("confirm payload unparsable; fallback to predicted")
                    break
                for tid, d in (data or {}).items():
                    if tid not in decisions or not isinstance(d, dict):
                        continue
                    valid = {it["key"] for it in d_map[tid]}
                    decisions[tid] = {
                        "changed": {k for k in d.get("changed", []) if k in valid},
                        "values": {k: v for k, v in d.get("values", {}).items()
                                   if k in valid}}
                return decisions
            await asyncio.sleep(_CONFIRM_POLL_INTERVAL)
            waited += _CONFIRM_POLL_INTERVAL
        self._push_progress({"stage": "confirm_timeout"})
        return decisions
```

- [ ] **Step 3: 画布节点 — 条件执行接线**

`_invoke_async` 中 `chosen = await self._select_templates(...)` 与 `self._push_progress({"stage": "selected" ...})` 之后、共享检索之前插入：

```python
        # P2 预判 + 暂停确认：有默认值字段才触发；返回 {} = 无基线，行为同现状
        decisions = await self._confirm_changed_fields(chosen, query, begin_fields)

        def _llm_fill_items(c: dict) -> list[dict]:
            """确认后该范本真正要走检索+LLM 的字段：N（无默认值）∪ C∩D（预判变化）
            ∪ 用户直填之外的兜底排除 D−C（直用默认值，免检索免 LLM）。"""
            d = decisions.get(c["template_id"]) or {}
            changed, direct = d.get("changed") or set(), set((d.get("values") or {}).keys())
            out = []
            for it in c["_placeholders"]:
                k = it.get("key")
                if not k or executor._norm_fill_mode(it) != "llm" or k in direct:
                    continue
                if str(it.get("default_value") or "") and k not in changed:
                    continue
                out.append(it)
            return out
```

共享检索调用改为传过滤后的清单（注意保持范本一一对应）：

```python
            chunks_list = await executor.retrieve_all_shared(
                tenant_id, [_llm_fill_items(c) for c in chosen], kb_ids,
                task_id=f"canvas:{self._id}",
                should_cancel=cancelled, sem=retrieval_sem)
```

`_fill_and_notify` 内 `llm_total` 改用过滤后清单：

```python
            llm_total = len(_llm_fill_items(cand))
```

`_fill_one` 签名追加 `decision: dict | None = None`，调用处传 `decision=decisions.get(cand["template_id"])`。`_fill_one` 内三处改动：

```python
        # ④ LLM 产值…… llm_placeholders 改为与检索一致的过滤集（默认值注入 prompt 作参考由
        #    generate_values 的 default_value hint 自动带上）
        decision = decision or {}
        direct_values = decision.get("values") or {}
        changed_keys = decision.get("changed") or set()
        llm_placeholders = [it for it in placeholders
                            if executor._norm_fill_mode(it) == "llm" and it.get("key")
                            and it["key"] not in direct_values
                            and not (str(it.get("default_value") or "") and it["key"] not in changed_keys)]
```

`_merge_param_values(...)` 之后、`build_values` 之前插入（顺序：直填值最高优先 → 默认值兜底只填仍缺失的）：

```python
        # P2 用户直填值直取（空串=明确清空，渲染为空）；并作为沉淀 override
        for k, v in direct_values.items():
            generated[k] = v
            missing.discard(k)
        executor._merge_default_values(placeholders, generated, missing)
```

沉淀调用更新为带 override（Task 4 已加的块改为）：

```python
            TplTemplateVersionService.sediment_defaults(
                cand["template_id"], cand["_ver"].id, values,
                override_keys=set(direct_values.keys()))
```

- [ ] **Step 4: 单测（组件级，monkeypatch REDIS_CONN 与 predict）**

对齐 `test/test_agent_fill_template_component.py` 既有桩风格追加核心用例：

```python
def test_confirm_wait_timeout_uses_predicted(monkeypatch):
    """Redis 永远无键 → 超时按预判结果继续（本用例以缩短超时常量驱动）。"""
    pytest.skip("按既有组件测试桩补齐：monkeypatch REDIS_CONN.get 返回 None + "
                "template_fill._CONFIRM_TIMEOUT 调小 + asyncio.sleep 注入；"
                "断言 _confirm_changed_fields 返回 predicted 集合并推 confirm_timeout 事件")


def test_confirm_payload_filters_unknown_keys(monkeypatch):
    """confirm 载荷含编造 key / 越范本 key → 过滤；直填值进入 decisions['values']。"""
    pytest.skip("同上桩方式：REDIS_CONN.get 返回带 ghost key 的 JSON，断言 decisions 只含合法 key")
```

> 实现者注意：这两个用例必须落实（不允许保留 skip 提交）。桩要点：`monkeypatch.setattr(tf_module, "_CONFIRM_TIMEOUT", 0.05)`、`monkeypatch.setattr(tf_module, "REDIS_CONN", FakeRedis)`、`monkeypatch.setattr(tf_module.asyncio, "sleep", fake_sleep)`（fake_sleep 直接 return，避免真实等待）；事件断言用捕获 `_push_progress` 的假组件。组件测试桩的具体组装方式参照该文件内既有用例。

- [ ] **Step 5: 回归 + Commit**

Run: `uv run pytest test/test_agent_fill_template_component.py test/test_template_fill_executor.py test/test_template_api_routes.py -v`
Expected: 全部 PASS

```bash
git add api/apps/restful_apis/template_api.py agent/component/template_fill.py test/test_agent_fill_template_component.py
git commit -m "feat(template): 画布 TemplateFill 变化字段预判 + confirm 暂停唤醒 + 条件执行"
```

---

## Task 10: 前端 SSE 确认事件 + 确认卡片

**Files:**
- Modify: `web/src/hooks/template-fill-stream.ts`
- Modify: `web/src/utils/api.ts`
- Modify: `web/src/hooks/use-template-fill-request.ts`（confirm 提交函数）
- Create: `web/src/pages/c-chat/template-fill-confirm-card.tsx`
- Modify: `web/src/pages/c-chat/template-fill-progress.tsx`
- Test: `web/src/hooks/__tests__/template-fill-stream.test.ts`（追加）

- [ ] **Step 1: 类型与归约**

`template-fill-stream.ts`：

```ts
export interface ITemplateFillCandidate {
  key: string;
  name: string;
  default_value: string;
}

export interface ITemplateFillConfirmTemplate {
  template_id: string;
  name: string;
  candidates: ITemplateFillCandidate[];
  predicted: string[];
}

export interface ITemplateFillConfirmPending {
  task_id: string;
  templates: ITemplateFillConfirmTemplate[];
  expired?: boolean;
  submitted?: boolean;
}
```

`ITemplateFillState` 追加 `pendingConfirm?: ITemplateFillConfirmPending;`
`ITemplateFillEvent` stage 联合追加 `'confirm_pending' | 'confirm_timeout'`，并追加可选字段 `task_id?: string; templates_confirm?: ITemplateFillConfirmTemplate[]; predicted?: string[];`（或直接在事件上带 `pending` 对象——以最简契约为准：事件 `data` 直接携带 `{stage:'confirm_pending', task_id, templates:[...]}`，`templates` 复用现有字段名冲突，因此**确认范本清单字段名用 `confirm_templates`**，避免与 selected 事件的 `templates` 撞名。相应 `ITemplateFillEvent` 追加 `confirm_templates?: ITemplateFillConfirmTemplate[]`。）

归约函数在 `finished 终态防御` 行之前（confirm_pending 需穿透 finished 吗？不需要——确认发生在填写开始前，但防御顺序保持既有：confirm_pending 也允许重开新一轮之外穿透无意义，维持现有 guard 即可），`stage === 'selected'` 分支之后追加：

```ts
  if (d.stage === 'confirm_pending') {
    tf.pendingConfirm = {
      task_id: d.task_id || '',
      templates: d.confirm_templates || [],
    };
    return;
  }
  if (d.stage === 'confirm_timeout') {
    if (tf.pendingConfirm && !tf.pendingConfirm.submitted) {
      tf.pendingConfirm = { ...tf.pendingConfirm, expired: true };
    }
    return;
  }
```

- [ ] **Step 2: 提交函数**

`api.ts` 追加 `confirmTemplateFill: `${restAPIv1}/template/fill/confirm`,`。
`use-template-fill-request.ts` 末尾追加（非 hook 导出函数，照 `testTemplateFill` 模式）：

```ts
// 画布范本填写暂停确认提交（Redis 唤醒画布节点继续）
export async function confirmTemplateFill(
  taskId: string,
  decisions: Record<string, { changed: string[]; values: Record<string, string> }>,
): Promise<void> {
  const { data } = await request.post(api.confirmTemplateFill, {
    data: { task_id: taskId, decisions },
  });
  if (data.code !== 0) {
    throw new Error(data.message || '确认提交失败');
  }
}
```

- [ ] **Step 3: 确认卡片组件（新文件）**

`web/src/pages/c-chat/template-fill-confirm-card.tsx`：受控组件，props `{ pending: ITemplateFillConfirmPending }`。行为：
- 每范本一节：候选 checkbox 列表（初始勾选 = `predicted`），勾选项旁 Input 可直填值（留空=勾选表示"让 AI 重填"）；
- 底部「确认并继续填写」按钮 → 组装 `decisions: {tid: {changed: 勾选key数组, values: 直填非空项}}` → `confirmTemplateFill(task_id, decisions)` → 本地置 submitted 成功态文案「已确认，正在继续填写…」；
- `expired` 且未 submitted → 显示「等待超时，已按 AI 预判字段继续填写」灰字，按钮隐藏；
- 提交失败 → 按钮恢复可点 + 错误文案（中文）。
- 样式对齐 template-fill-progress.tsx 既有卡片风格（`rounded-lg border-[#E5E5E5] bg-[#F5F5F5] text-xs`，主色 `#1a66fb`）。

组件骨架（核心 state 与提交逻辑完整给出，渲染 JSX 按行为契约+样式对齐既有卡片补齐）：

```tsx
import { useMemo, useState } from 'react';
import type { ITemplateFillConfirmPending } from '@/hooks/template-fill-stream';
import { confirmTemplateFill } from '@/hooks/use-template-fill-request';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';

export default function TemplateFillConfirmCard({
  pending,
}: {
  pending: ITemplateFillConfirmPending;
}) {
  const [checked, setChecked] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(
      pending.templates.map((t) => [t.template_id, new Set(t.predicted)]),
    ),
  );
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const toggle = (tid: string, key: string) => {
    setChecked((prev) => {
      const next = new Set(prev[tid]);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return { ...prev, [tid]: next };
    });
  };

  const submit = async () => {
    const decisions = Object.fromEntries(
      pending.templates.map((t) => [
        t.template_id,
        {
          changed: [...(checked[t.template_id] || [])],
          values: Object.fromEntries(
            t.candidates
              .filter((c) => (inputs[`${t.template_id}_${c.key}`] || '').trim() !== '')
              .map((c) => [c.key, inputs[`${t.template_id}_${c.key}`].trim()]),
          ),
        },
      ]),
    );
    try {
      await confirmTemplateFill(pending.task_id, decisions);
      setSubmitted(true);
    } catch (e) {
      setError((e as Error).message || '确认提交失败，请重试');
    }
  };

  if (pending.expired && !submitted) {
    return (
      <div className="mt-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#525252]">
        等待超时，已按 AI 预判字段继续填写。
      </div>
    );
  }
  if (submitted) {
    return (
      <div className="mt-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#1a66fb]">
        已确认，正在继续填写…
      </div>
    );
  }
  return (
    /* 按范本分节渲染：pending.templates.map → 标题《name》+ 候选行（Checkbox 初始勾选
       = predicted、字段名、默认值灰字、直填 Input 可选）；底部 Button「确认并继续填写」
       onClick=submit，disabled=!pending.task_id；error 时红字提示。样式对齐
       template-fill-progress.tsx（rounded-lg border-[#E5E5E5] bg-[#F5F5F5] text-xs，主色 #1a66fb） */
  );
}
```

`template-fill-progress.tsx` 顶部（`state?.templates?.length` 判空之前）插入：

```tsx
  if (state?.pendingConfirm) {
    return (
      <div className="mt-2">
        <TemplateFillConfirmCard pending={state.pendingConfirm} />
        {/* 既有范本行继续渲染在其下 */}
      </div>
    );
  }
```

（保持既有范本行渲染不被吞掉——将 confirm 卡片作为附加块而非替代渲染。）

- [ ] **Step 4: 归约单测**

`web/src/hooks/__tests__/template-fill-stream.test.ts` 追加：

```ts
describe('confirm_pending / confirm_timeout', () => {
  it('confirm_pending 写入 pendingConfirm 状态', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'confirm_pending',
      task_id: 't1',
      confirm_templates: [{ template_id: 'tp1', name: '范本', candidates: [{ key: 'a', name: '甲', default_value: 'v' }], predicted: ['a'] }],
    } as any);
    expect(acc.templateFill?.pendingConfirm?.task_id).toBe('t1');
    expect(acc.templateFill?.pendingConfirm?.templates[0].candidates[0].key).toBe('a');
  });

  it('confirm_timeout 置 expired；submitted 后不覆盖', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, { stage: 'confirm_pending', task_id: 't1', confirm_templates: [] } as any);
    acc.templateFill!.pendingConfirm!.submitted = true;
    applyTemplateFillEvent(acc, { stage: 'confirm_timeout' } as any);
    expect(acc.templateFill!.pendingConfirm!.expired).toBeUndefined();
  });
});
```

- [ ] **Step 5: 验证 + Commit**

Run: `cd web && npx jest template-fill-stream && npm run build`
Expected: 测试通过、构建成功

```bash
git add web/src/hooks/template-fill-stream.ts web/src/hooks/use-template-fill-request.ts web/src/hooks/__tests__/template-fill-stream.test.ts web/src/utils/api.ts web/src/pages/c-chat/template-fill-confirm-card.tsx web/src/pages/c-chat/template-fill-progress.tsx
git commit -m "feat(web): 范本填写变化字段确认卡片（SSE confirm_pending + Redis 唤醒提交）"
```

---

## Task 11: flow AI 面板验证 + P2 收尾

- [ ] **Step 1: 验证 flow 面板复用**

确认 `web/src/pages/c-chat/flow/flow-ai-panel.tsx` 渲染的是共用 `TemplateFillProgress` 组件（是则确认卡片自动生效，零改动）；若 flow 面板有独立的渲染分支，则同样插入 `pendingConfirm` 卡片。

- [ ] **Step 2: 全量回归**

Run: `uv run pytest test/ -k "template" -v && cd web && npx jest && npm run build`
Expected: 全部通过

- [ ] **Step 3: 更新 CHANGE.md 与 CLAUDE.md**

CHANGE.md 顶部追加：

```markdown
## 2026-09-10 范本默认值基线 P2（变化字段确认填写）
- 主题：重填场景 LLM 负担从几百字段降到 ~100 变化字段
- 核心变更：executor.predict_changed_fields（分块预判/失败回退空集）→ TemplateFill confirm_pending SSE 暂停 → 前端确认卡片（勾选变化字段+直填值）→ POST /template/fill/confirm 写 Redis 唤醒 → 条件执行（D−C 免检索免LLM，直填值最高优先+沉淀override）
- 遗留：B端异步填写任务（无人在场）不接入暂停确认，保持 P1 fallback 行为；未部署
```

CLAUDE.md 参考表「范本默认值基线设计」行尾的「未实施」改为「P1+P2 已完成编码，未部署，实施计划 docs/superpowers/plans/2026-09-10-template-default-baseline.md」。

- [ ] **Step 4: Commit**

```bash
git add CHANGE.md CLAUDE.md web/src/pages/c-chat/flow/flow-ai-panel.tsx
git commit -m "docs: CHANGE.md/CLAUDE.md 记录范本默认值基线 P2 完成"
```

---

## 部署提醒（用户手动执行，禁止自动部署）

后端成套 SCP（模板填写链路任一文件改动需整组同步）：
`api/db/services/template_fill_service.py`、`api/apps/restful_apis/template_api.py`、`rag/svr/template_fill/detector.py`、`rag/svr/template_fill/executor.py`、`agent/component/template_fill.py`，随后容器内 import 冒烟 + 前端 build 走既有前端部署流程。
