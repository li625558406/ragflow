# 文件审核加固实施计划（R-1 自愈 + R-6/R-7/R-8 + Vitest 迁移）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实设计稿 `docs/superpowers/specs/2026-09-18-file-review-hardening-design.md`：中断轮次惰性自愈（读路径落库）、R-6 持锁段移出事件循环、R-7 levels 判型、R-8 摘除 MinIO 对象名、前端测试基建从 jest 迁 Vitest 并修腐坏用例。

**Architecture:** R-1 在既有 `is_stale_running` 谓词之上加 Service 层落库单点 `heal_stale_round`，接入 state 端点与 `admit_fix_round` 两个消费点；前端零逻辑改动。测试基建用 vitest（jsdom + globals）替换已损坏的 jest 配置。

**Tech Stack:** Python/Quart/Peewee（后端）、pytest（后端测试）、React/TS/Vitest/jsdom（前端）。

**实测基线（2026-09-18）：** 后端 337 passed；前端本地 jest 脚手架 10 套件 121 用例中 **2 套件 4 用例失败**（`chat.test.ts` 3 例断言过期、`useScrollToBottom` 1 例缺 `scrollTo`；`template-fill-confirm-card` 已被上一批修好，设计稿「3 套件 9 用例」为过期记录，以本基线为准）。

**测试命令约定：**
- 后端：`.venv/Scripts/python -m pytest test/<file> -v`（Windows，仓库根目录执行）
- 前端：迁移完成后 `cd web && npx vitest run`；迁移前用既有脚手架 `cd web && ./node_modules/.bin/jest --config ../.scratch/jest.local.cjs`

---

### Task 1: Service 层 `heal_stale_round`（R-1 落库单点）

**Files:**
- Modify: `api/db/services/file_review_service.py`（`is_stale_running` 之后新增）
- Test: `test/test_file_review_service.py`（末尾新增第 15 节）

- [ ] **Step 1: 写失败测试**

在 `test/test_file_review_service.py` 文件末尾追加（复用既有 `_mk_round` / `_round_row` / `_patch_is_running` 助手）：

```python
# ── 15. heal_stale_round：中断轮次惰性自愈（R-1 落库单点） ───────────────
# 判定复用 is_stale_running（纯谓词），落库走 update_status；DB 用例走真库，
# 时间轴用显式 now_ms 锁定（不给 flaky 留缝）。

def test_heal_stale_round_persists_failed_with_error(monkeypatch):
    """命中谓词 → 落库 failed + 中断文案，且**同步刷新传入行的内存字段**——
    调用方（state 端点 / 受理闸门）随后读的是回落后的值，不重查库。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_db"
    rid = _mk_round(tid, 1, "reviewing")
    row = _round_row(rid)
    _patch_is_running(monkeypatch, alive=False)

    assert heal_stale_round(row, now_ms=row.create_time + 61_000) is True
    assert row.status == "failed" and "已中断" in row.error
    after = _round_row(rid)
    assert after.status == "failed" and "已中断" in after.error


def test_heal_stale_round_skips_live_thread_terminal_and_grace(monkeypatch):
    """谓词不成立一律不动库：线程活着（单轮超 60s 是常态）、行龄在宽限期内、终态行。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_skip"
    rid = _mk_round(tid, 1, "reviewing")
    row = _round_row(rid)

    _patch_is_running(monkeypatch, alive=True)
    assert heal_stale_round(row, now_ms=row.create_time + 10 ** 9) is False

    _patch_is_running(monkeypatch, alive=False)
    assert heal_stale_round(row, now_ms=row.create_time) is False, "行龄 0 在宽限期内"

    done = _round_row(_mk_round(tid, 2, "done", file_version="v2"))
    assert heal_stale_round(done, now_ms=done.create_time + 10 ** 9) is False

    after = _round_row(rid)
    assert after.status == "reviewing" and not after.error


def test_heal_stale_round_is_idempotent(monkeypatch):
    """heal 后 status 离开 RUNNING 集合，谓词不再成立 ⇒ 第二次调用返回 False、
    不再触库。并发双 heal 最终行值相同（同一份文案），无害。"""
    from api.db.services.file_review_service import heal_stale_round

    tid = f"{PFX}t_heal_idem"
    rid = _mk_round(tid, 1, "fixing")
    row = _round_row(rid)
    _patch_is_running(monkeypatch, alive=False)
    when = row.create_time + 61_000

    assert heal_stale_round(row, now_ms=when) is True
    row.error = "SENTINEL"  # 若第二次误写库并刷新内存，哨兵会被覆盖而暴露
    assert heal_stale_round(row, now_ms=when) is False
    assert row.error == "SENTINEL"               # 内存未被第二次触碰
    assert _round_row(rid).error != "SENTINEL"   # 库里仍是首轮落下的文案
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py -k heal_stale -v`
Expected: FAIL，`ImportError: cannot import name 'heal_stale_round'`

- [ ] **Step 3: 实现**

`api/db/services/file_review_service.py`，紧跟 `is_stale_running` 函数之后（约 line 151）新增：

```python
# R-1 自愈的落库文案——唯一实现，state 端点与受理闸门共用。
STALE_HEAL_ERROR = "服务重启或异常退出，本轮审核已中断"


def heal_stale_round(row, now_ms: int | None = None) -> bool:
    """「自称在跑却无人会回来写」的中断轮次就地回落为 failed（R-1 自愈落库单点）。

    判据完全复用 is_stale_running（三判据 + 60s 宽限；**部署契约同它**：要求 HTTP
    服务与 review 线程同进程，改多 worker 前必须先改判定）。命中即把轮次行落为
    failed + error 文案，并**同步刷新传入 row 的内存 status/error**——调用方随后
    构建 payload / 走后续闸门时读到的就是回落后的值，无需重查库。

    幂等由谓词天然保证：heal 后 status='failed' ∉ RUNNING_ROUND_STATUSES，谓词不再
    成立，第二次调用直接返回 False。并发双 heal 最终行值相同，无害。

    「读路径触发写」是设计核心而非副作用：中断轮次的唯一消费场景就是被读取
    （state 端点）与被受理（admit_fix_round），两个消费点接入即覆盖全部出口，
    无需启动期扫描或后台线程。写的是确定性事实（线程已不存在、行永远等不到结果），
    与用户输入无关，state 端点「读不限」策略不受影响。

    返回是否真的发生了回落（非中断行 / 幂等重入返回 False）。
    """
    if not is_stale_running(row, now_ms=now_ms):
        return False
    FileReviewRoundService.update_status(row.id, "failed", error=STALE_HEAL_ERROR)
    row.status = "failed"
    row.error = STALE_HEAL_ERROR
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py -v`
Expected: 全部 PASS（既有 stale 用例 + 新增 3 例）

- [ ] **Step 5: Commit**

```bash
git add api/db/services/file_review_service.py test/test_file_review_service.py
git commit -m "feat(file-review): heal_stale_round 中断轮次惰性自愈落库单点（R-1）"
```

---

### Task 2: `admit_fix_round` stale 闸门改为 heal 放行 + levels 判型（R-7）

**Files:**
- Modify: `api/db/services/file_review_service.py:654-758`（`admit_fix_round`）
- Test: `test/test_file_review_service.py`（改写 `test_admit_fix_round_denies_stale_before_running`，新增 2 例）

- [ ] **Step 1: 改写/新增测试**

**删除**既有 `test_admit_fix_round_denies_stale_before_running`（约 line 875-891，其断言「stale 必须拒绝」契约被本任务反转），**替换**为以下三例：

```python
def test_admit_fix_round_heals_stale_then_admits(monkeypatch):
    """R-1：stale 闸门从「拒绝」改为「heal → 刷新内存 → 放行走后续闸门」。
    中断轮与线程内部崩溃的 failed 轮同权：有余额即可直接发起下一轮修复，
    不再要求「重新发起审核」。"""
    stale_round = SimpleNamespace(id="r-old", round_no=1, status="reviewing",
                                  file_id="f1", template_id="t1", kb_ids=None,
                                  user_query="审核这份招标文件",
                                  task_id=f"{PFX}t_stale_heal", create_time=0)
    created, spawned, updates = [], [], []
    svc = _patch_admission(monkeypatch, state={"rounds": [stale_round]},
                           created=created, spawned=spawned)
    monkeypatch.setattr(svc.FileReviewRoundService, "update_status",
                        classmethod(lambda cls, rid, status, **extra:
                                    updates.append((rid, status, extra)) or True))

    result = svc.admit_fix_round(task_id=f"{PFX}t_stale_heal", tenant_id="u1",
                                 levels=["high"])
    assert updates and updates[0][0] == "r-old" and updates[0][1] == "failed"
    assert stale_round.status == "failed", "内存未刷新 ⇒ 后续闸门会读到旧 running 态"
    assert created and created[0]["round_no"] == 2
    assert spawned == [f"{PFX}t_stale_heal"]
    assert result.round_no == 2


def test_admit_fix_round_after_heal_respects_quota(monkeypatch):
    """自愈不是绕过闸门：heal 后继续走 no_quota——余额耗尽照样拒绝。"""
    rounds = [SimpleNamespace(id="r1", round_no=1, status="annotated", file_id="f1",
                              template_id="t1", kb_ids=None,
                              user_query="审核这份招标文件",
                              task_id=f"{PFX}t_stale_quota", create_time=0),
              SimpleNamespace(id="r4", round_no=4, status="fixing", file_id="f1",
                              template_id="t1", kb_ids=None, user_query="修复",
                              task_id=f"{PFX}t_stale_quota", create_time=0)]
    updates = []
    svc = _patch_admission(monkeypatch, state={"rounds": rounds},
                           created=[], spawned=[])
    monkeypatch.setattr(svc.FileReviewRoundService, "update_status",
                        classmethod(lambda cls, rid, status, **extra:
                                    updates.append((rid, status)) or True))

    with pytest.raises(svc.FixAdmissionDenied) as ei:
        svc.admit_fix_round(task_id=f"{PFX}t_stale_quota", tenant_id="u1",
                            levels=["high"])
    assert ei.value.reason == "no_quota"
    assert ("r4", "failed") in updates, "拒绝发生在 heal 之后：中断轮必须已被回落"


def test_admit_fix_round_rejects_non_list_levels(monkeypatch):
    """R-7 防御闸：levels 非 list（None / 字符串 / 元组 / 集合）在取锁与触库
    **之前**拒绝——不得 TypeError 冒烟成 500，也不得消耗锁等待窗口。"""
    from api.db.services import file_review_service as svc

    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: (_ for _ in ()).throw(
                            AssertionError("levels 判型必须发生在任何触库之前"))))
    for bad in (None, "high", ("high",), {"high"}):
        with pytest.raises(svc.FixAdmissionDenied) as ei:
            svc.admit_fix_round(task_id="t1", tenant_id="u1", levels=bad)
        assert ei.value.reason == "invalid_levels"
        assert not svc._ADMIT_LOCK.locked(), "拒绝路径不得持有受理锁"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py -k "stale or levels" -v`
Expected: `heals_stale_then_admits` FAIL（stale 仍被拒绝）、`rejects_non_list_levels` FAIL（TypeError 而非 FixAdmissionDenied）

- [ ] **Step 3: 实现**

`admit_fix_round` 内，锁获取**之前**加 levels 判型：

```python
def admit_fix_round(*, task_id: str, tenant_id: str, levels, user_query_override="") -> AdmitResult:
    """受理一轮修复：校验 → 定轮号 → 建轮次 → spawn，**全程持进程级锁**。

    同步函数；REST 侧必须经 `asyncio.to_thread` 调用（R-6：持锁段的锁等待与 DB
    往返不得阻塞事件循环），对话工具线程直接同步调用。锁本身是跨线程的
    threading.Lock，工作线程与事件循环线程被同一把锁串行化。
    返回 AdmitResult 或抛 FixAdmissionDenied —— 不返回「拒绝码」，避免调用方漏判。

    前置条件：levels 必须是 list（R-7 判型闸在本函数第一行，取锁/触库之前）。

    闸门顺序不是随意的，每一条都在挡一条实测过的坏路径：
      1. not_found：task_id 不存在 / 不归 tenant（沿用 get_owned_task 的口径，同一句话，
         不泄露「他人 task 是否存在」）；
      2. stale→heal：本轮自称在跑但已无线程会回来写它 —— 受理点上就地 heal 为 failed
         （R-1 自愈），随后与崩溃 failed 轮**同权走后续闸门**：有余额即可直接发起下一轮
         修复，不再要求「重新发起审核」。heal 输给并发消费者（返回 False 但谓词刚成立）
         时行在库里已是 failed，同样按 failed 继续评估；
      3. running  ：本轮（rounds[-1]）还在 reviewing/fixing，线程还会回来写这一行；
      4. closing  ：轮次行已终态但 spawn 线程仍在收尾（理由同前版）；
      5. no_quota ：修复轮余额用尽（failed 轮同样计入，见 fix_rounds_left）；
      6. no_pending：所选级别没有待修项（理由同前版）。
    顺序约束：2 必须在 3 之前（原因同前版）；3 必须在 4 之前；4 在 6 之前是取舍；
      从 is_running 检查到 spawn_review_task 之间不得插入其它逻辑。
    """
    if not isinstance(levels, list):
        raise FixAdmissionDenied("invalid_levels", "修复级别参数不合法")
    if not _ADMIT_LOCK.acquire(timeout=ADMIT_LOCK_TIMEOUT):
```

stale 闸门（原 `raise FixAdmissionDenied("stale", ...)` 整段）替换为：

```python
        cur = rounds[-1]
        # stale 必须在 running 之前判：两者的 status 同样在 RUNNING_ROUND_STATUSES 里，
        # 顺序反了会把「永远不会好」错答成「等一会就好」。反过来不会误伤活轮次 ——
        # is_stale_running 的判据②要求 is_running 为 False。
        # R-1 自愈：中断轮就地回落（幂等）后放行走后续闸门；heal 内部已同步刷新
        # cur.status，这里再赋一次是防御并发下 heal 输给别的消费者的残影。
        if is_stale_running(cur):
            heal_stale_round(cur)
            cur.status = "failed"
```

（`FixAdmissionDenied` docstring 里 reason 契约集合同步：`stale` 移出、`invalid_levels` 加入：）

```python
    reason 取值集合是固定契约，调用方按它分发：
      busy / not_found / invalid_levels / running / closing / no_quota / no_pending
```

- [ ] **Step 4: 跑全量 service 测试**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/db/services/file_review_service.py test/test_file_review_service.py
git commit -m "feat(file-review): stale 闸门改 heal 放行 + levels 判型防御闸（R-1/R-7）"
```

---

### Task 3: API 层——review_state 接入 heal + `_doc_payload` 摘除对象名（R-8 后端）

**Files:**
- Modify: `api/apps/restful_apis/file_review_api.py`
- Test: `test/test_file_review_api.py`

- [ ] **Step 1: 更新 `_patch_services` + 改写 doc 断言测试**

`test/test_file_review_api.py` 的 `_patch_services`（约 line 97-128）在 annotation `update_status` patch 之后追加：

```python
    monkeypatch.setattr(_api.FileReviewRoundService, "update_status",
                        classmethod(lambda cls, rid, status, **extra:
                                    calls.setdefault("round_updates", []).append(
                                        (rid, status, extra)) or True))
```

改写 3 处既有断言 + 新增 1 例：

```python
def test_doc_payload_picks_latest_produced_round():
    rounds = [_round(1, "done", minio_path="frv-t1-v2", file_version="v2"),
              _round(3, "done", minio_path="frv-t1-v3", file_version="v3")]
    assert _api._doc_payload(rounds) == {"has_result": True, "version": "v3"}
    assert _api._doc_payload(list(reversed(rounds))) == \
        {"has_result": True, "version": "v3"}
    assert _api._doc_payload([_round(1, "reviewing")]) == \
        {"has_result": False, "version": ""}
    assert _api._doc_payload([]) == {"has_result": False, "version": ""}
```

`test_state_endpoint_shape_when_never_reviewed`（约 line 328）：
`assert d["doc"] == {"object": "f1", "version": ""}` → `assert d["doc"] == {"has_result": False, "version": ""}`

`test_state_endpoint_assembles_rounds_annotations_and_doc`（约 line 344）：
`assert d["doc"] == {"object": "frv-t1-v2", "version": "v2"}` → `assert d["doc"] == {"has_result": True, "version": "v2"}`

新增（放在 stale 相关用例旁）：

```python
def test_state_endpoint_heals_interrupted_round(monkeypatch):
    """R-1 自愈：state 读取把中断轮就地落为 failed（update_status 恰好一次），
    payload 读到回落后的行——status=failed、stale=false、error 带中断文案。
    前端走既有失败展示链路（current.error），不再出现常驻「已中断」僵尸态。"""
    rounds = [_round(1, "reviewing", create_time=0)]
    calls = _patch_services(monkeypatch, rounds=rounds)

    d = _call(_api.review_state, method="GET", path="/x", file_id="f1")

    assert d["rounds"][0]["status"] == "failed"
    assert "已中断" in d["rounds"][0]["error"]
    assert d["current"]["status"] == "failed"
    assert d["rounds"][0]["stale"] is False, "heal 后谓词不再成立，stale 恒 false"
    assert len(calls["round_updates"]) == 1 and calls["round_updates"][0][1] == "failed"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest test/test_file_review_api.py -v`
Expected: 新用例与改写用例 FAIL（`heal_stale_round` 未接入 / `doc` 仍含 `object`）

- [ ] **Step 3: 实现**

`file_review_api.py`：

1. import 处补 `heal_stale_round`（与 `is_stale_running` 同一个 import 语句）。
2. `_doc_payload` 整体替换：

```python
def _doc_payload(rounds: list) -> dict:
    """该展示的文档版本与「有无成稿」。**不下发 MinIO 对象名**（R-8：内部对象名
    不该出 API；已核实两个前端调用点都只消费 version，预览/下载走 task_id+version
    的专用 download 端点）。

    显式选 round_no 最大且带 minio_path 的轮次（不依赖入参顺序），与 T6
    _latest_version_name 的基线口径一致 —— 面板必须展示**最后一版**。
    has_result 替代旧版「object != file_id 哨兵」的隐式约定。
    """
    best = None
    for r in rounds:
        if not r.minio_path:
            continue
        if best is None or (r.round_no or 0) >= (best.round_no or 0):
            best = r
    if best is None:
        # 还没有成稿：version 留空串——「首轮版本号 = v1」是 T7/T8 的命名习惯，
        # 不是本层的契约，不该由这里替前端断言。
        return {"has_result": False, "version": ""}
    return {"has_result": True, "version": best.file_version}
```

3. `review_state` 内 `rounds = FileReviewRoundService.get_by_file(file_id)` 之后插入：

```python
        # R-1 自愈：中断轮次在第一次被读取时就地回落（幂等），随后 payload 构建读到的
        # 是回落后的 failed 行。stale 字段保留，仅覆盖「已中断但尚未被任何读取 heal」
        # 的瞬时窗口（heal 后谓词不成立，stale 恒 false）。
        for r in rounds:
            heal_stale_round(r)
```

   并把 `"doc": _doc_payload(rounds, file_id)` 改为 `"doc": _doc_payload(rounds)`。
4. 注释同步：`review_state` docstring 中 `doc.object = …` 段改为说明 `{has_result, version}`；`_round_payload` 的 stale 字段注释补「仅瞬时窗口」；模块 docstring 第 37 行附近关于 `doc.object` 的说明改为 `doc.version`；`_round_payload` 中 `"stale": is_stale_running(row)` 不动（heal 后对 failed 行返回 False）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest test/test_file_review_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/apps/restful_apis/file_review_api.py test/test_file_review_api.py
git commit -m "feat(file-review): state 端点接入自愈 + doc payload 摘除 MinIO 对象名（R-1/R-8）"
```

---

### Task 4: fix 端点 `asyncio.to_thread`（R-6）+ 契约测试反转

**Files:**
- Modify: `api/apps/restful_apis/file_review_api.py`（`fix_review` 端点）
- Test: `test/test_file_review_api.py`

- [ ] **Step 1: 改写测试**

`test_fix_admission_is_synchronous_and_lock_guarded`（约 line 158-189）AST 部分反转（前半段 sync-fn + threading.Lock 断言**保留不动**）：

```python
    with open(_API_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "fix_review")
    body = next(s for s in fn.body if isinstance(s, ast.Try)).body
    stmts = [s for s in body if "admit_fix_round" in ast.dump(s)]
    assert stmts, "fix_review 必须把受理交给 admit_fix_round"
    for stmt in stmts:
        dump = ast.dump(stmt)
        assert "to_thread" in dump, \
            "受理必须经 asyncio.to_thread 移入线程池（R-6：持锁段不得阻塞事件循环）"
        assert any(isinstance(n, ast.Await) for n in ast.walk(stmt)), \
            "to_thread 调用必须被 await（结果要回传给响应）"
```

`test_fix_rejects_interrupted_round_with_actionable_message`（约 line 385）**改名改写**为：

```python
def test_fix_heals_interrupted_round_then_admits(monkeypatch):
    """R-1：中断轮不再被 stale 拒绝——受理点先 heal 再走后续闸门，有余额直接建新轮。"""
    rounds = [_round(1, "reviewing", create_time=0)]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds)

    resp = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})

    assert resp["code"] == 0
    assert calls["round_updates"] and calls["round_updates"][0][1] == "failed"
    assert calls["created"]["round_no"] == 2
    assert calls["spawned"] == ["t1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest test/test_file_review_api.py -k "synchronous or interrupted" -v`
Expected: 2 例 FAIL

- [ ] **Step 3: 实现**

`file_review_api.py` 顶部补 `import asyncio`。`fix_review` 内原同步调用段（约 line 304-308）替换：

```python
        # 受理序列「校验 — 定轮号 — 建轮次 — 起线程」必须原子，互斥在 Service 层的
        # 进程级 threading.Lock（admit_fix_round），跨线程有效（对话工具被
        # common.connection_utils.timeout 丢进独立 daemon 线程）。
        # R-6：acquire 最坏要等 5s 超时、闸门里还有多次同步 DB 往返——整段经
        # asyncio.to_thread 移入线程池，事件循环不被单请求阻塞。锁是 threading.Lock，
        # 移入工作线程不改变互斥语义（事件循环、to_thread 工作线程、对话工具线程
        # 三者仍被同一把锁串行化）。
        try:
            result = await asyncio.to_thread(
                admit_fix_round,
                task_id=task_id, tenant_id=tenant_id, levels=levels,
                # 基准取首轮原始需求（见 _fix_base_query 的说明）；非法类型在 Service 层忽略。
                user_query_override=body.get("user_query"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest test/test_file_review_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/apps/restful_apis/file_review_api.py test/test_file_review_api.py
git commit -m "perf(file-review): fix 受理经 asyncio.to_thread 移出事件循环（R-6）"
```

---

### Task 5: 后端全量回归

- [ ] **Step 1: 全量 pytest**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py test/test_file_review_api.py test/test_file_review_tool.py test/test_file_review_node.py test/test_file_review_e2e.py test/test_file_review_spawn.py test/test_file_review_db.py test/test_file_review_executor.py test/test_file_review_patcher.py test/test_file_review_kb_aggregator.py -v`
Expected: 全部 PASS（基线 337 + 本批新增约 8 例；若某 test 文件名与实际不符，以 `ls test/test_file_review*` 为准）

- [ ] **Step 2: ruff**

Run: `.venv/Scripts/python -m ruff check api/db/services/file_review_service.py api/apps/restful_apis/file_review_api.py`
Expected: 0 error

---

### Task 6: 前端 R-8 配合（类型 + 组件 + 两个调用点 + 测试）

**Files:**
- Modify: `web/src/hooks/file-review-stream.ts`（doc 类型 + stale 注释）
- Modify: `web/src/pages/c-chat/file-review-progress.tsx`（onPreviewDoc 签名 + 按钮条件）
- Modify: `web/src/pages/c-chat/index.tsx:2592` 与 `web/src/pages/c-chat/flow/flow-detail.tsx:1048`（调用点）
- Test: `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx`

- [ ] **Step 1: 类型**（`file-review-stream.ts`）

```ts
  /** 该展示的文档：最近一次落盘的成稿版本。R-8：MinIO 内部对象名不再下发，
   *  预览/下载一律走 task_id + version 的专用端点。从未落盘时 has_result=false、
   *  version=''（替代旧版「object !== fileId 哨兵」约定）。 */
  doc: { has_result: boolean; version: string };
```

`IFileReviewRound.stale` 注释末尾追加一句：

```ts
   *  加固后（R-1 自愈）state 端点读取时会把中断轮就地回落为 failed，本字段仅覆盖
   *  「已中断但尚未被任何读取 heal」的瞬时窗口，heal 后 stale 恒为 false。
```

- [ ] **Step 2: 组件**（`file-review-progress.tsx`）

`onPreviewDoc` prop 签名与注释替换为：

```tsx
  /** 点击「下载成稿」时回调（参数：成稿版本号）。
   *  调用方**必须**调 `downloadFileReviewVersion(taskId, fileVersion)`
   *  （`@/services/file-review-service`）—— 它用 fetch 手挂 Authorization 取 Blob
   *  再 createObjectURL 下载。**禁止**改成 window.open / a[href] 直链：下载端点带
   *  `@login_required` 且只从请求头取用户，浏览器导航类请求不带自定义头 ⇒ 必 401。
   *  R-8：服务端不再下发 MinIO 对象名，这里只收版本号。 */
  onPreviewDoc?: (fileVersion: string) => void;
```

按钮段（原 line 161-169）替换：

```tsx
        {data.doc.has_result && data.doc.version && (
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
            onClick={() => onPreviewDoc?.(data.doc.version)}
          >
            <Download className="h-3 w-3" /> 下载成稿
          </button>
        )}
```

- [ ] **Step 3: 两个调用点**

`c-chat/index.tsx`（约 2592）：`onPreviewDoc={(_minioPath, fileVersion) => {` → `onPreviewDoc={(fileVersion) => {`；其上方「doc.object 是 MinIO 对象名…」注释块首句改为「R-8：服务端不下发对象名，预览/下载只凭 taskId + fileVersion 走专用 download 端点。」其余 tid 逻辑不动。
`flow/flow-detail.tsx`（约 1048）：同样把参数收窄为 `(fileVersion)`。

- [ ] **Step 4: 测试**（`file-review-progress.test.tsx`）

fixture（line 65）：`doc: { object: 'frv-t1-v2.docx', version: 'v2' }` → `doc: { has_result: true, version: 'v2' }`。
新增 2 例：

```tsx
  it('has_result 时显示「下载成稿」，onPreviewDoc 只收 fileVersion（R-8 不再透传对象名）', () => {
    const onPreviewDoc = vi.fn();
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" onPreviewDoc={onPreviewDoc} />);
    fireEvent.click(screen.getByRole('button', { name: /下载成稿/ }));
    expect(onPreviewDoc).toHaveBeenCalledTimes(1);
    expect(onPreviewDoc).toHaveBeenCalledWith('v2');
  });

  it('has_result=false（从未落盘）不显示「下载成稿」', () => {
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            doc: { has_result: false, version: '' },
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: /下载成稿/ })).toBeNull();
  });
```

（本任务随 Task 9 的 vitest 迁移一起跑；此时尚无 vitest，先完成代码改动，测试留到 Task 9 验证。）

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/file-review-stream.ts web/src/pages/c-chat/file-review-progress.tsx web/src/pages/c-chat/index.tsx web/src/pages/c-chat/flow/flow-detail.tsx web/src/pages/c-chat/__tests__/file-review-progress.test.tsx
git commit -m "feat(file-review): 前端摘除 doc.object 依赖，下载只凭版本号（R-8 配合）"
```

---

### Task 7: Vitest 接入

**Files:**
- Create: `web/vitest.config.ts`、`web/src/test/setup.ts`
- Modify: `web/package.json`、`web/tsconfig.json`
- Delete: `web/jest.config.ts`、`web/jest-setup.ts`

- [ ] **Step 1: 安装依赖**

Run: `cd web && npm i -D vitest jsdom && npm uninstall jest jest-environment-jsdom @types/jest @types/testing-library__jest-dom`
（需外网时先 `export http_proxy=socks5://127.0.0.1:10808 https_proxy=socks5://127.0.0.1:10808`）

- [ ] **Step 2: `web/vitest.config.ts`**

```ts
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

// 单测配置独立于产物构建（vite.config.ts 是 build 配置）。环境/别名与已删除的
// 本地 jest 脚手架（.scratch/jest.local.cjs）等价；CSS 按默认策略返回空模块。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@parent': path.resolve(__dirname, '..'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/__tests__/**/*.test.{ts,tsx}'],
  },
});
```

- [ ] **Step 3: `web/src/test/setup.ts`**（从 `.scratch/jest-setup.local.ts` 移植，去掉绝对路径与 jest 专属部分）

```ts
// 测试环境兜底：jsdom 缺失的浏览器/Node API 桩（缺口在 harness，不在产品代码）。
// 全部用「存在即不覆盖」的守卫式注入，与 Node/jsdom 版本演进兼容。
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import * as nodeUtil from 'node:util';

// RTL cleanup：显式挂一道，防 transform/环境差异导致自动 cleanup 失效。
afterEach(() => cleanup());

// react-router 的 development 构建在模块顶层 new TextEncoder()——只要 import 到
// routes.tsx 的链路（logic-hooks → route-hook）就会在模块求值期用到。
if (!(globalThis as any).TextEncoder) {
  (globalThis as any).TextEncoder = nodeUtil.TextEncoder;
}
if (!(globalThis as any).TextDecoder) {
  (globalThis as any).TextDecoder = nodeUtil.TextDecoder;
}
if (!(globalThis as any).structuredClone) {
  (globalThis as any).structuredClone = (nodeUtil as any).structuredClone;
}

// routes.tsx 在模块作用域 createBrowserRouter() → new Request(...)。以下是最小桩
// （只保证构造不抛错）：若有用例真发请求，必须换真实现，否则是「静默假成功」。
class StubHeaders {
  private map = new Map<string, string>();
  constructor(init?: any) {
    if (init instanceof StubHeaders) {
      init.forEach((v: string, k: string) => this.map.set(k, v));
    } else if (Array.isArray(init)) {
      for (const [k, v] of init) this.map.set(String(k).toLowerCase(), String(v));
    } else if (init && typeof init === 'object') {
      for (const [k, v] of Object.entries(init)) {
        this.map.set(String(k).toLowerCase(), String(v));
      }
    }
  }
  get(k: string) { return this.map.get(String(k).toLowerCase()) ?? null; }
  set(k: string, v: string) { this.map.set(String(k).toLowerCase(), String(v)); }
  has(k: string) { return this.map.has(String(k).toLowerCase()); }
  forEach(cb: (v: string, k: string) => void) { this.map.forEach((v, k) => cb(v, k)); }
  entries() { return this.map.entries(); }
}
class StubRequest {
  url: string;
  method: string;
  headers: any;
  body: any;
  signal: any;
  constructor(input: any, init: any = {}) {
    const base = typeof input === 'string' ? input : (input?.url ?? String(input));
    this.url = base;
    this.method = (init.method ?? input?.method ?? 'GET').toUpperCase();
    this.headers = new StubHeaders(init.headers);
    this.body = init.body ?? null;
    this.signal = init.signal ?? null;
  }
  clone() {
    return new StubRequest(this.url, {
      method: this.method, headers: this.headers, body: this.body, signal: this.signal,
    });
  }
}
class StubResponse {
  status: number;
  statusText: string;
  headers: any;
  body: any;
  ok: boolean;
  constructor(body: any = null, init: any = {}) {
    this.body = body;
    this.status = init.status ?? 200;
    this.statusText = init.statusText ?? '';
    this.headers = new StubHeaders(init.headers);
    this.ok = this.status >= 200 && this.status < 300;
  }
  async json() { return JSON.parse(this.body ?? 'null'); }
  async text() { return String(this.body ?? ''); }
}
for (const [key, val] of [
  ['Request', StubRequest],
  ['Response', StubResponse],
  ['Headers', StubHeaders],
] as const) {
  if (!(globalThis as any)[key]) (globalThis as any)[key] = val;
}

// jsdom 未实现滚动 API（useScrollToBottom 等直接调 container.scrollTo）。
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {} as any;
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {} as any;
}
```

- [ ] **Step 4: `web/package.json`**

- `"test": "jest --no-cache --coverage"` → `"test": "vitest run"`
- devDependencies 确认已无 `jest` / `jest-environment-jsdom` / `@types/jest` / `@types/testing-library__jest-dom`（Step 1 已卸载）

- [ ] **Step 5: `web/tsconfig.json`**

`"types": ["vite/client", "node", "@testing-library/jest-dom"]` → `"types": ["vite/client", "node", "vitest/globals"]`
（jest-dom 的 matcher 类型改由 setup 里 `import '@testing-library/jest-dom/vitest'` 自增强提供。）

- [ ] **Step 6: 删除 jest 残留**

```bash
git rm web/jest.config.ts web/jest-setup.ts
```

- [ ] **Step 7: Commit**

```bash
git add web/vitest.config.ts web/src/test/setup.ts web/package.json web/tsconfig.json package-lock.json 2>/dev/null; git add -u web/
git commit -m "test(web): 测试基建迁 Vitest，替换依赖已移除 umi/test 的损坏 jest 配置"
```

---

### Task 8: 迁移 10 个测试文件（jest.* → vi.*）

**Files（全部既有测试文件）：**
- `web/src/utils/__tests__/chat.test.ts`
- `web/src/hooks/__tests__/{file-review-poll.test.ts, template-fill-stream.test.ts, use-template-fill-task-poll.test.ts, logic-hooks.useScrollToBottom.test.tsx}`
- `web/src/pages/c-chat/__tests__/{docx-highlight-describe.test.ts, file-review-progress.test.tsx, template-fill-confirm-card.test.tsx, template-fill-live-preview.test.tsx, template-fill-sediment-button.test.tsx}`

- [ ] **Step 1: 逐文件机械替换**

每个文件：
1. 顶部加 `import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';`（**只 import 该文件实际用到的名字**，globals:true 下运行时本可省，显式 import 是为了让 `tsc --noEmit` 在移除 @types/jest 后仍有类型）。
2. 按映射表替换（先 `grep -n "jest\." <file>` 列出命中点）：
   - `jest.mock(` → `vi.mock(`
   - `jest.fn(` → `vi.fn(`
   - `jest.spyOn(` → `vi.spyOn(`
   - `jest.useFakeTimers()` → `vi.useFakeTimers()`；`jest.useRealTimers()` → `vi.useRealTimers()`；`jest.runAllTimers()` → `vi.runAllTimers()`；`jest.advanceTimersByTime(` → `vi.advanceTimersByTime(`
   - `as jest.MockedFunction<typeof X>` → 用 `vi.mocked(X)`（见 Step 2 示例）
   - `jest.requireMock('...')` → 改用 `vi.mocked(导入符号)`（见 Step 3 示例）

- [ ] **Step 2: `file-review-progress.test.tsx` 的类型 cast 改写**

```ts
// 原：
const mockUseFileReviewState = useFileReviewState as jest.MockedFunction<
  typeof useFileReviewState
>;
// 改为：
const mockUseFileReviewState = vi.mocked(useFileReviewState);
```
（`mockUseFixFileReview` / `mockUseUpdateAnnotationStatus` 同样处理。）

- [ ] **Step 3: `template-fill-confirm-card.test.tsx` 的 requireMock 改写**

```ts
// 原：const { confirmTemplateFill } = jest.requireMock('@/hooks/use-template-fill-request');
// 改为直接导入 mock 模块的符号，再 vi.mocked：
import { confirmTemplateFill } from '@/hooks/use-template-fill-request';
// ...测试体内：
const confirmMock = vi.mocked(confirmTemplateFill);
confirmMock.mockClear();
// 断言处：confirmMock.mock.calls[0]
```
（`vi.mock('@/hooks/use-template-fill-request', ...)` 工厂本身保持 `vi.fn().mockResolvedValue(undefined)`。）

- [ ] **Step 4: 跑全量**

Run: `cd web && npx vitest run`
Expected: 10 套件全绿（此时 chat.test 3 例与 useScrollToBottom 1 例仍红，属预期，Task 9 修）

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "test(web): 全部测试文件 jest→vi API 迁移"
```

---

### Task 9: 修 2 个腐坏套件（4 用例）

- [ ] **Step 1: `chat.test.ts` 断言对齐现实现**

实现（`web/src/utils/chat.ts:55-67`）的捕获组**保留分隔符内侧空格**（`$$${equation}$$`），3 处断言改为：

```ts
  it('converts block \\[ \\] to $$ $$', () => {
    expect(preprocessLaTeX('\\[ x + y \\]')).toBe('$$ x + y $$');
  });

  it('converts inline \\( \\) to $ $', () => {
    expect(preprocessLaTeX('\\( a \\)')).toBe('$ a $');
  });
```

```ts
  it('handles multiple block equations', () => {
    const content = 'First \\[ a \\] then \\[ b \\right] c \\]';
    const result = preprocessLaTeX(content);
    expect(result).toBe('First $$ a $$ then $$ b \\right] c $$');
  });
```

- [ ] **Step 2: `useScrollToBottom` mock container 补 `scrollTo`**

`logic-hooks.useScrollToBottom.test.tsx` 的 `createMockContainer` 返回对象补一行（hook 实现会在 messages 变化时调 `container.scrollTo`，mock 是纯对象缺该方法 → TypeError）：

```ts
  return {
    current: {
      scrollTop,
      clientHeight,
      scrollHeight,
      scrollTo: jest.fn(),   // ← 迁移后即 vi.fn()（Task 8 已替换）
      addEventListener: jest.fn((event, cb) => {
        listeners[event] = cb;
      }),
      removeEventListener: jest.fn(),
    },
    listeners,
  } as any;
```

- [ ] **Step 3: 跑全量验证**

Run: `cd web && npx vitest run`
Expected: **10 套件全绿**（121 + 新增 2 例 ≈ 123 用例）

- [ ] **Step 4: Commit**

```bash
git add web/src/utils/__tests__/chat.test.ts web/src/hooks/__tests__/logic-hooks.useScrollToBottom.test.tsx
git commit -m "test(web): 修 2 个既有腐坏套件——LaTeX 断言对齐现实现 + mock 补 scrollTo"
```

---

### Task 10: 全量验证 + 文档登记

- [ ] **Step 1: 后端全量**

Run: `.venv/Scripts/python -m pytest test/test_file_review_service.py test/test_file_review_api.py -v && .venv/Scripts/python -m ruff check api/db/services/file_review_service.py api/apps/restful_apis/file_review_api.py`
Expected: 全 PASS + 0 error

- [ ] **Step 2: 前端全量**

Run: `cd web && npx vitest run && npx tsc --noEmit 2>&1 | grep -c "error TS" ; npx eslint src --ext .ts,.tsx`
Expected: vitest 全绿；tsc 相对基线（改动文件）零新增错误；eslint 0 error（改动文件）

- [ ] **Step 3: CHANGE.md 增量更新**（文件顶部追加条目）

新增 `## 2026-09-18 文件审核加固（R-1 中断轮次自愈 + R-6/R-7/R-8 + 前端迁 Vitest，未部署）`，内容要点：四个修复的根因/修法、语义变化（中断轮 heal 后可直接发起修复轮；API `doc.object` 字段删除）、实测基线修正（腐坏套件实为 2 套件 4 用例，confirm-card 已修）、部署清单。

- [ ] **Step 4: 项目 CLAUDE.md 参考表登记**

「文件审核全链路」行末尾追加：`；2026-09-18 加固批次（R-1 自愈/R-6/R-7/R-8 + Vitest 迁移）设计 docs/superpowers/specs/2026-09-18-file-review-hardening-design.md`。

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 文件审核加固批次迭代记录与参考表登记"
```

---

## 部署（不在本计划内自动执行）

等用户指令，届时：
- 后端 2 文件成套 SCP：`api/db/services/file_review_service.py` + `api/apps/restful_apis/file_review_api.py` → 容器重启
- 前端：`npm run build` → dist 上传（`rm -rf dist/*` 保 inode）→ `nginx -s reload`
- 部署后冒烟：`GET /api/v1/file/review/templates` 无 Authorization 头回 401；state 端点响应中 `doc` 无 `object` 键
