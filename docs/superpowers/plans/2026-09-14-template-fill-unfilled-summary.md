# 范本填写未填充汇总与成稿内定位跳转 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 范本填写任务终态派生成稿留空填写点（unfilled），经 filled 事件与 progress 端点透传到前端，成稿行内联展示汇总（必填红标/选填灰），点击字段打开实时预览抽屉并滚动闪烁定位到文档中该占位符。

**Architecture:** 不新增持久化——executor 新增 `derive_unfilled` 纯函数，canvas 观察者（filled 事件）与 progress 端点（`build_progress_payload`）各自从最终 values 派生透传；前端归约器存 `t.unfilled`，`TemplateFillLivePreview` 新增 `focusKey` prop，经 `data-ph-key` 属性定位 span 做 scrollIntoView + 闪烁。c-chat 与 flow AI 面板共用组件，一处改动两端生效。

**Tech Stack:** Python (Quart/Peewee 后端) + pytest；React 18 + TypeScript + Jest + @testing-library/react。

**设计文档:** `docs/superpowers/specs/2026-09-14-template-fill-unfilled-summary-design.md`

---

## File Structure

| 操作 | 文件 | 职责 |
|---|---|---|
| Modify | `rag/svr/template_fill/executor.py` | 新增模块级纯函数 `derive_unfilled` |
| Modify | `api/apps/restful_apis/template_api.py` | `build_progress_payload` 增加 placeholders 入参与 `unfilled` 字段；端点层查询版本占位符 |
| Modify | `agent/component/template_fill.py` | 新增 `_unfilled_of` helper；done 分支 filled 事件携带 unfilled |
| Modify | `web/src/hooks/template-fill-stream.ts` | `ITemplateFillUnfilled` 类型；归约器 filled 分支写入 unfilled |
| Modify | `web/src/hooks/use-template-fill-task-poll.ts` | 终态 done 合并 unfilled |
| Modify | `web/src/pages/c-chat/template-fill-progress.tsx` | liveTarget 状态（id+focusKey）；成稿行内联汇总 |
| Modify | `web/src/pages/c-chat/template-fill-live-preview.tsx` | `focusKey` prop；定位滚动闪烁 |
| Modify | `web/src/pages/c-chat/docx-highlight.ts` | 占位符 span 写入 `data-ph-key` |
| Test | `test/test_template_fill_executor.py` | derive_unfilled 对抗用例 |
| Test | `test/test_template_fill_progress_api.py` | payload unfilled 用例 |
| Test | `test/test_template_fill_delegate.py` | _unfilled_of 用例 |
| Test | `web/src/hooks/__tests__/template-fill-stream.test.ts` | 归约 unfilled 用例 |
| Test | `web/src/hooks/__tests__/use-template-fill-task-poll.test.ts` | 轮询合并 unfilled 用例 |

---

### Task 1: 后端 `derive_unfilled` 纯函数

**Files:**
- Modify: `rag/svr/template_fill/executor.py`（`_merge_default_values` 函数之后，约 line 344）
- Test: `test/test_template_fill_executor.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `test/test_template_fill_executor.py` 末尾追加：

```python
def test_derive_unfilled_basic_and_order():
    """留空字段按占位符清单顺序输出；required 缺省兜底 True（detector 默认口径）。"""
    from rag.svr.template_fill import executor
    phs = [{"key": "a", "name": "甲", "required": True},
           {"key": "b", "name": "乙", "required": False},
           {"key": "c", "name": "丙"}]
    vals = {"a": "x", "c": ""}
    assert executor.derive_unfilled(phs, vals) == [
        {"key": "b", "name": "乙", "required": False},
        {"key": "c", "name": "丙", "required": True}]


def test_derive_unfilled_whitespace_is_unfilled():
    """纯空白值 = 未填充（str().strip() 判空口径）。"""
    from rag.svr.template_fill import executor
    phs = [{"key": "a", "name": "甲", "required": True}]
    assert executor.derive_unfilled(phs, {"a": "   "}) == [
        {"key": "a", "name": "甲", "required": True}]


def test_derive_unfilled_falsy_valid_values_filled():
    """防 falsy 误杀："0"/"false" 是有效产值，不算留空。"""
    from rag.svr.template_fill import executor
    phs = [{"key": "a", "name": "甲", "required": True},
           {"key": "b", "name": "乙", "required": False}]
    assert executor.derive_unfilled(phs, {"a": "0", "b": "false"}) == []


def test_derive_unfilled_empty_inputs():
    """空 placeholders / None 入参 → 空列表（不炸）。"""
    from rag.svr.template_fill import executor
    assert executor.derive_unfilled([], {"a": "x"}) == []
    assert executor.derive_unfilled(None, None) == []


def test_derive_unfilled_no_key_skipped():
    """无 key 的占位符行跳过（与既有 placeholder missing key, skipped 口径一致）。"""
    from rag.svr.template_fill import executor
    phs = [{"name": "无key"}, {"key": "k", "name": "有"}]
    assert executor.derive_unfilled(phs, {}) == [
        {"key": "k", "name": "有", "required": True}]


def test_derive_unfilled_adversarial_key_passthrough():
    """key 含花括号残留/Unicode 控制字符 → 原样透传不炸（展示层转义是前端职责）。"""
    from rag.svr.template_fill import executor
    phs = [{"key": "a{{b}}", "name": "怪\x00名", "required": False}]
    got = executor.derive_unfilled(phs, {})
    assert got == [{"key": "a{{b}}", "name": "怪\x00名", "required": False}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_executor.py -k derive_unfilled -v`
Expected: FAIL，`AttributeError: ... has no attribute 'derive_unfilled'`

- [ ] **Step 3: 最小实现**

在 `rag/svr/template_fill/executor.py` 的 `_merge_default_values` 函数定义之后（`async def generate_values` 之前）插入：

```python
def derive_unfilled(placeholders: list[dict], values: dict) -> list[dict]:
    """终态派生成稿留空的填写点：values 中该 key 缺失/None/空串/纯空白 = 未填充。
    统一覆盖 LLM 无产值、直填空串清空、白名单外无默认值三条留空路径，与成稿
    实际内容一致（docxtpl 渲染时空值即留白）。用 str().strip() 判空而非 truthy，
    防 "0"/"false" 等 falsy 但有效值被误判。required 缺失兜底 True（detector 默认）。"""
    return [{"key": it["key"], "name": it.get("name") or it["key"],
             "required": bool(it.get("required", True))}
            for it in (placeholders or [])
            if it.get("key") and not str((values or {}).get(it["key"]) or "").strip()]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_executor.py -k derive_unfilled -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add rag/svr/template_fill/executor.py test/test_template_fill_executor.py
git commit -m "feat(template-fill): derive_unfilled 终态留空填写点派生纯函数"
```

---

### Task 2: progress 端点透传 unfilled

**Files:**
- Modify: `api/apps/restful_apis/template_api.py`（line 46 import；`build_progress_payload` 约 line 829-859；端点 `get_fill_task_progress` 约 line 862-874）
- Test: `test/test_template_fill_progress_api.py`（`TestBuildProgressPayload` 类末尾追加）

- [ ] **Step 1: 写失败测试**

在 `TestBuildProgressPayload` 类内追加（沿用文件既有 `self._task` helper）：

```python
    _PHS = [{"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]

    def test_done_with_placeholders_derives_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": ""}}),
            None, None, self._PHS)
        assert p["unfilled"] == [{"key": "b", "name": "乙", "required": False}]

    def test_non_terminal_never_derives(self):
        p = _template_api.build_progress_payload(
            self._task(status="generating",
                       values={"render": {"a": "", "b": ""}}),
            None, None, self._PHS)
        assert p["unfilled"] is None

    def test_no_placeholders_no_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": ""}}), None)
        assert p["unfilled"] is None

    def test_values_not_dict_no_unfilled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values=None), None, None, self._PHS)
        assert p["unfilled"] is None

    def test_falsy_valid_value_all_filled(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "0", "b": "false"}}),
            None, None, self._PHS)
        assert p["unfilled"] is None

    def test_snapshot_values_authoritative(self):
        p = _template_api.build_progress_payload(
            self._task(status="done", values={"render": {"a": "x", "b": ""}}),
            {"status": "done", "values": {"a": "", "b": "y"}},
            None, self._PHS)
        assert p["unfilled"] == [{"key": "a", "name": "甲", "required": True}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_progress_api.py -k unfilled -v`
Expected: FAIL，`TypeError: build_progress_payload() takes from 2 to 3 positional arguments but 4 were given`

- [ ] **Step 3: 实现**

`api/apps/restful_apis/template_api.py` 三处改动：

3a. line 46 import 扩展：

```python
from rag.svr.template_fill.executor import derive_unfilled, read_progress_snapshot
```

（保持原有行内其他导入不变，只加 `derive_unfilled`。）

3b. `build_progress_payload` 签名与返回值：

```python
def build_progress_payload(task, snapshot: dict | None, download: dict | None = None,
                           placeholders: list[dict] | None = None) -> dict:
```

`return {` 之前加：

```python
    # 终态派生成稿留空填写点（断连重连轮询恢复汇总用）：placeholders 由端点层
    # 查版本后传入；values 与上方字段同口径（快照优先，回退 DB render）。
    # 仅 done/partial 派生；派生为空（全填满）置 None，响应不下发空数组。
    unfilled = None
    if placeholders and status in ("done", "partial") and isinstance(values, dict):
        unfilled = derive_unfilled(placeholders, values) or None
```

`return` 字典增加一项：

```python
        "unfilled": unfilled,
```

3c. 端点 `get_fill_task_progress` 的 return 前插入版本查询、改传参：

```python
    placeholders = None
    if task.status in ("done", "partial"):
        ver = TplTemplateVersionService.get_by_id_checked(
            task.template_id, getattr(task, "template_version_id", ""))
        if ver is not None:
            placeholders = getattr(ver, "placeholders", None) or []
    return get_result(data=build_progress_payload(
        task, read_progress_snapshot(task_id), download, placeholders))
```

3d. 若文件顶部尚未导入 `TplTemplateVersionService`，在 `from api.db.services.template_fill_service import ...` 行中加入（先 grep 确认：`grep -n "TplTemplateVersionService" api/apps/restful_apis/template_api.py`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest test/test_template_fill_progress_api.py -v`
Expected: 全部 passed（含既有 stalled/bridge 用例不回归）

- [ ] **Step 5: 提交**

```bash
git add api/apps/restful_apis/template_api.py test/test_template_fill_progress_api.py
git commit -m "feat(template-fill): progress 端点终态透传 unfilled 列表"
```

---

### Task 3: canvas filled 事件携带 unfilled

**Files:**
- Modify: `agent/component/template_fill.py`（模块级 helper 加在 `_canvas_task_params` 之后；done 分支约 line 489-500）
- Test: `test/test_template_fill_delegate.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

`test/test_template_fill_delegate.py` 末尾追加（文件顶部若无 `from types import SimpleNamespace` 则补）：

```python
class TestUnfilledOf:
    _PHS = [{"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]

    def test_partial_filled_returns_unfilled_list(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": ""}})
        assert _unfilled_of(self._PHS, row) == [
            {"key": "b", "name": "乙", "required": False}]

    def test_all_filled_returns_none(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": "y"}})
        assert _unfilled_of(self._PHS, row) is None

    def test_values_not_dict_returns_none(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values=None)
        assert _unfilled_of(self._PHS, row) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest test/test_template_fill_delegate.py -v`
Expected: FAIL，`ImportError: cannot import name '_unfilled_of'`

- [ ] **Step 3: 实现**

`agent/component/template_fill.py`，`_canvas_task_params` 函数之后加模块级函数：

```python
def _unfilled_of(placeholders: list[dict], row) -> list[dict] | None:
    """终态行的成稿留空填写点（executor.derive_unfilled 包装）：值源 DB 行
    values.render（终态权威，不依赖 Redis 快照存活）。无留空或值结构异常
    返回 None——filled 事件不下发该字段，前端不渲染汇总条。"""
    values = row.values if isinstance(row.values, dict) else {}
    return executor.derive_unfilled(placeholders, values.get("render") or {}) or None
```

观察循环 `row.status == "done"` 且桥接成功的分支（`results[tid] = (dl, None)` 之后），把：

```python
                        self._push_progress({"stage": "filled", "template_id": tid,
                                             "name": cand["name"], "download": dl,
                                             "task_id": task_id})
```

改为：

```python
                        ev = {"stage": "filled", "template_id": tid,
                              "name": cand["name"], "download": dl,
                              "task_id": task_id}
                        unfilled = _unfilled_of(cand["_placeholders"], row)
                        if unfilled:
                            ev["unfilled"] = unfilled
                        self._push_progress(ev)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `uv run pytest test/test_template_fill_delegate.py test/test_agent_fill_template_component.py -v`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add agent/component/template_fill.py test/test_template_fill_delegate.py
git commit -m "feat(template-fill): canvas filled 事件携带 unfilled 留空字段"
```

---

### Task 4: 前端类型与归约器

**Files:**
- Modify: `web/src/hooks/template-fill-stream.ts`
- Test: `web/src/hooks/__tests__/template-fill-stream.test.ts`

- [ ] **Step 1: 写失败测试**

`describe('applyTemplateFillEvent', ...)` 内追加：

```ts
  it('filled 携带 unfilled 写入模板行；缺省不清旧值', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 2 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    });
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);

    // 事件缺省 unfilled（如旧后端/全部填满）→ 不清已有值
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd2', filename: 'b.docx', mime_type: 'x' },
    });
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);
  });

  it('unfilled 后到者胜（终态一次性数据整体替换，重放幂等）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A' }],
    });
    const ev = {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    };
    applyTemplateFillEvent(acc, ev as any);
    applyTemplateFillEvent(acc, JSON.parse(JSON.stringify(ev)) as any);
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts -t unfilled`
Expected: FAIL（unfilled 为 undefined）

- [ ] **Step 3: 实现**

`web/src/hooks/template-fill-stream.ts` 三处：

3a. `ITemplateFillDownload` 之后加类型：

```ts
/** 未填充字段（成稿留空的填写点，任务终态一次性下发） */
export interface ITemplateFillUnfilled {
  key: string;
  name: string;
  required: boolean;
}
```

3b. `ITemplateFillTemplate` 加字段（`task_id?` 之后）：

```ts
  /** 成稿留空的填写点（filled 事件/progress 终态派生；历史恢复经轮询端点合并） */
  unfilled?: ITemplateFillUnfilled[];
```

3c. `ITemplateFillEvent` 加字段（`values?` 之后）：

```ts
  /** filled：成稿留空的填写点汇总（终态一次性整体替换） */
  unfilled?: ITemplateFillUnfilled[];
```

3d. 归约器 filled 分支：

```ts
  } else if (d.stage === 'filled') {
    t.status = 'filled';
    t.download = d.download;
    // 终态一次性数据整体替换（后到者胜幂等）；缺省不清旧值
    if (d.unfilled) t.unfilled = d.unfilled;
  }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx jest src/hooks/__tests__/template-fill-stream.test.ts`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add web/src/hooks/template-fill-stream.ts web/src/hooks/__tests__/template-fill-stream.test.ts
git commit -m "feat(template-fill-web): 归约器支持 filled 事件 unfilled 字段"
```

---

### Task 5: 轮询 hook 合并 unfilled

**Files:**
- Modify: `web/src/hooks/use-template-fill-task-poll.ts`（终态 done override，约 line 70-89）
- Test: `web/src/hooks/__tests__/use-template-fill-task-poll.test.ts`

- [ ] **Step 1: 写失败测试**

文件末尾 `describe` 内追加：

```ts
  it('终态 done 带 unfilled → 合并 override（历史恢复汇总不丢）', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
        values: { a: 'x' },
        unfilled: [{ key: 'b', name: '字段乙', required: false }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      unfilled: [{ key: 'b', name: '字段乙', required: false }],
    });
  });

  it('终态 done 缺 unfilled → 不下键，保留 SSE 已有汇总', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filling',
            unfilled: [{ key: 'k1', name: '字段一', required: true }],
          }),
        ],
        true,
      ),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    });
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx jest src/hooks/__tests__/use-template-fill-task-poll.test.ts -t unfilled`
Expected: 两条用例 FAIL（第一条 unfilled undefined；第二条被 override 整键覆盖丢 unfilled）

- [ ] **Step 3: 实现**

`use-template-fill-task-poll.ts` 终态 done override（`d.status === 'done' && d.download` 分支）在 values 展开之后加一行：

```ts
                  ? {
                      status: 'filled' as const,
                      download: d.download,
                      // values 缺省时不下该键：避免清掉 SSE/回放已累积的 t.values
                      //（「查看填写内容」入口依赖它）
                      ...(d.values && Object.keys(d.values).length
                        ? { values: d.values }
                        : {}),
                      // unfilled 同防御：缺省（旧后端/全填满）不清 SSE 已有汇总
                      ...(d.unfilled ? { unfilled: d.unfilled } : {}),
                    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx jest src/hooks/__tests__/use-template-fill-task-poll.test.ts`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add web/src/hooks/use-template-fill-task-poll.ts web/src/hooks/__tests__/use-template-fill-task-poll.test.ts
git commit -m "feat(template-fill-web): 断连重连轮询终态合并 unfilled"
```

---

### Task 6: 成稿行内联汇总 + liveTarget 状态

**Files:**
- Modify: `web/src/pages/c-chat/template-fill-progress.tsx`（全文件小改：state、3 个按钮 call site、filled 行 JSX、LivePreview 传参）

- [ ] **Step 1: state 改造**

`const [liveTplId, setLiveTplId] = useState<string>('');` 替换为：

```tsx
  // 打开预览的目标：范本 id + 可选定位字段 key（点击未填充汇总字段时携带）。
  // 存 id 不存对象快照的既有惯例不变（values 更新时从 templates 派生最新引用）
  const [liveTarget, setLiveTarget] = useState<{
    template_id: string;
    focusKey?: string;
  } | null>(null);
```

`const liveTpl = mergedState?.templates.find((t) => t.template_id === liveTplId);` 改为：

```tsx
  const liveTpl = mergedState?.templates.find(
    (t) => t.template_id === liveTarget?.template_id,
  );
```

- [ ] **Step 2: 三处按钮 call site**

- selected 行「查看范本」：`onClick={() => setLiveTplId(t.template_id)}` → `onClick={() => setLiveTarget({ template_id: t.template_id })}`
- filling 行「实时预览」：同上改法
- filled 行「查看填写内容」：同上改法

- [ ] **Step 3: filled 行内联汇总**

filled 分支（`const dl = t.download; if (!dl) return null;` 之后的 return）整体替换为：

```tsx
          const dl = t.download;
          if (!dl) return null;
          return (
            <div key={t.template_id} className="space-y-1">
              <div className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]">
                {/* ……此 div 内部内容与原 filled 行完全一致，原样保留：文件按钮/名称、
                    extraAction、查看填写内容按钮、下载链接…… */}
              </div>
              {t.unfilled && t.unfilled.length > 0 && (
                <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5 px-3 py-1 text-xs">
                  <span className="text-[#FAAD14]">
                    ⚠ {t.unfilled.length} 个填写点未填充：
                  </span>
                  {t.unfilled.map((f, i) => (
                    <span key={f.key} className="flex items-center gap-1">
                      <button
                        className={
                          f.required
                            ? 'text-[#F5222D] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#CF1322]'
                            : 'text-[#8C8C8C] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#525252]'
                        }
                        title={`定位到文档中的「${f.name}」`}
                        onClick={() =>
                          setLiveTarget({
                            template_id: t.template_id,
                            focusKey: f.key,
                          })
                        }
                      >
                        {f.name}
                      </button>
                      {f.required && (
                        <span className="rounded bg-[#FFF1F0] px-1 text-[10px] text-[#F5222D]">
                          必填
                        </span>
                      )}
                      {i < (t.unfilled?.length ?? 0) - 1 && (
                        <span className="text-[#8C8C8C]">·</span>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
```

**注意**：执行时原 filled 行内层 div 的全部子元素（`onPreview ?` 文件按钮三元、`extraAction?.(dl)`、`t.values` 条件的查看填写内容按钮、下载 `<a>`）必须原样搬入，不得删改。

- [ ] **Step 4: LivePreview 传参**

文件末尾：

```tsx
      {liveTpl && (
        <TemplateFillLivePreview
          tpl={liveTpl}
          focusKey={liveTarget?.focusKey}
          onClose={() => setLiveTarget(null)}
        />
      )}
```

- [ ] **Step 5: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -20`
Expected: `TemplateFillLivePreview` 报缺少 `focusKey` prop 之外的错误为 0（focusKey 报错在 Task 7 实现后消除；若严格模式阻断，可先给组件加可选 prop 空实现过渡）

- [ ] **Step 6: 提交**

```bash
git add web/src/pages/c-chat/template-fill-progress.tsx
git commit -m "feat(template-fill-web): 成稿行内联未填充汇总（必填红标/选填灰/点击定位）"
```

---

### Task 7: LivePreview focusKey 定位滚动闪烁

**Files:**
- Modify: `web/src/pages/c-chat/docx-highlight.ts`（`stylePlaceholderSpan` 加一行）
- Modify: `web/src/pages/c-chat/template-fill-live-preview.tsx`

- [ ] **Step 1: docx span 写入 data-ph-key**

`docx-highlight.ts` 的 `stylePlaceholderSpan` 函数体开头（`const v = values[key];` 之前）加：

```ts
  span.dataset.phKey = key;
```

- [ ] **Step 2: LivePreview 定位 helper**

`template-fill-live-preview.tsx` 顶部 `splitPlaceholders` 函数之后加：

```tsx
// 点击未填充汇总字段后的定位闪烁时长（毫秒）
const FLASH_MS = 2000;

/** 定位到容器内 data-ph-key 匹配的占位符：滚动居中 + 临时 outline 闪烁。
 * 找不到返回 false（调用方据此不标记已定位，留待渲染完成后重试）。 */
function focusPlaceholder(container: HTMLElement, key: string): boolean {
  const el = container.querySelector<HTMLElement>(
    `[data-ph-key="${CSS.escape(key)}"]`,
  );
  if (!el) return false;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  const prev = el.style.outline;
  el.style.outline = '2px solid #1a66fb';
  window.setTimeout(() => {
    el.style.outline = prev;
  }, FLASH_MS);
  return true;
}
```

- [ ] **Step 3: 组件 props 与状态**

组件签名改：

```tsx
export default function TemplateFillLivePreview({
  tpl,
  focusKey,
  onClose,
}: {
  tpl: ITemplateFillTemplate;
  /** 点击未填充汇总字段带来的定位目标（抽屉打开后定位一次） */
  focusKey?: string;
  onClose: () => void;
}) {
```

`const [renderFailed, setRenderFailed] = useState(false);` 之后加：

```tsx
  // docx 保真渲染完成标记：定位 effect 依赖它区分「渲染未完不能定位」与「文档无该 key 静默放弃」
  const [renderedOk, setRenderedOk] = useState(false);
```

- [ ] **Step 4: 渲染 effect 埋点**

docx 渲染 effect：开头 `setRenderFailed(false);` 之后加 `setRenderedOk(false);`；`.then()` 回调末尾（`placeholderSpansRef.current = applyDocxHighlight(...)` 之后）加 `setRenderedOk(true);`。

范本切换重置 effect（`useEffect(... , [tpl.template_id])`）内加 `setRenderedOk(false);`。

- [ ] **Step 5: 定位 effect**

values 增量更新 effect 之后加：

```tsx
  // 点击汇总字段后的定位：渲染完成后滚动到该占位符并闪烁；只执行一次
  //（focusDoneRef 记录已定位 key）。docx 保真渲染未完成（renderedOk=false 且
  // 未降级）时容器还没有占位符 span，不标记已定位，等依赖翻转后重试；
  // 文本降级/xlsx 路径随 items 到达触发。文档中无该 key → 静默跳过。
  const focusDoneRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focusKey || docxLoading || !containerRef.current) return;
    if (focusDoneRef.current === focusKey) return;
    if (docxEnabled && !renderedOk && !renderFailed) return;
    if (focusPlaceholder(containerRef.current, focusKey)) {
      focusDoneRef.current = focusKey;
    }
  }, [focusKey, docxLoading, renderedOk, items, docxEnabled, renderFailed]);
```

- [ ] **Step 6: 文本模式 span 补 data-ph-key**

`renderText` 两个占位符 span（已填高亮 span 与未填虚线 span）分别加 `data-ph-key={seg.key}` 属性（xlsx 行与降级文本段落共用 renderText，一并覆盖）。

- [ ] **Step 7: 手工冒烟（本地 dev）**

Run: `cd web && npm run dev`，C端对话发起一次含留空字段的范本填写（或用历史消息回放），验证：
1. filled 行下方出现「⚠ N 个填写点未填充」行，必填红标、选填灰；
2. 点击字段名 → 抽屉打开并滚动到对应虚线槽位、闪烁 2s；
3. 刷新页面（走轮询恢复）→ 汇总仍在、点击仍可定位；
4. 全部填满的范本行不出现汇总行。

- [ ] **Step 8: 提交**

```bash
git add web/src/pages/c-chat/template-fill-live-preview.tsx web/src/pages/c-chat/docx-highlight.ts
git commit -m "feat(template-fill-web): 实时预览支持 focusKey 定位滚动闪烁"
```

---

### Task 8: 构建验证、文档与收尾

**Files:**
- Modify: `CHANGE.md`（增量条目）
- Modify: `CLAUDE.md`（参考表条目状态「设计完成，未实施」→「已完成编码+测试，未部署」）

- [ ] **Step 1: 前端生产构建验证**

Run: `cd web && npm run build`
Expected: 构建成功无 TS 错误（本地构建仅为验证；部署构建由用户指示时执行）

- [ ] **Step 2: 后端测试全量回归**

Run: `uv run pytest test/test_template_fill_executor.py test/test_template_fill_progress_api.py test/test_template_fill_delegate.py test/test_agent_fill_template_component.py test/test_template_fill_events.py -v`
Expected: 全部 passed

- [ ] **Step 3: CHANGE.md 增量条目**

在 `CHANGE.md` 顶部追加（日期 2026-09-14，主题「范本填写未填充汇总与成稿内定位跳转」），核心变更点：executor `derive_unfilled` 派生 + filled 事件/progress 端点透传 + 前端成稿行内联汇总（必填红标/选填灰）+ LivePreview focusKey 定位闪烁；遗留：未部署（后端 3 文件成套 SCP：`rag/svr/template_fill/executor.py`、`api/apps/restful_apis/template_api.py`、`agent/component/template_fill.py`；前端 build）。

- [ ] **Step 4: CLAUDE.md 参考表状态更新 + 提交**

参考表「范本填写未填充汇总与定位跳转」条目尾注改为「已完成编码+测试，未部署」。

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 范本填写未填充汇总与定位跳转实施完成记录"
```

- [ ] **Step 5: 部署提醒（不执行）**

部署需用户明确指示：后端 3 文件成套 SCP + `docker restart docker-ragflow-cpu-1`；前端 `npm run build` + tar + nginx reload。前后端可独立部署（增量字段，无互锁）。

---

## Self-Review 记录

- **Spec 覆盖**：§4.1→Task 1；§4.2→Task 3；§4.3→Task 2；§5.1→Task 4；§5.2→Task 5；§5.3→Task 6；§5.4→Task 7；§6 边界→各 Task 用例 + Task 7 effect 守卫；§7 测试→Task 1/2/4/5 用例；§8 部署→Task 8。无缺口。
- **占位符扫描**：Task 6 Step 3 的「原样保留」注释是对执行者的搬移指令（附明确清单），非实现占位符；其余步骤均含完整代码。
- **类型一致性**：`derive_unfilled`（Task 1 定义，Task 2/3 调用）、`_unfilled_of`（Task 3 定义+测试同名）、`ITemplateFillUnfilled`（Task 4 定义，Task 5 测试引用）、`focusKey`/`liveTarget`（Task 6 传递，Task 7 消费）、`data-ph-key`（Task 7 Step 1/6 写入，helper 查询）均一致。
