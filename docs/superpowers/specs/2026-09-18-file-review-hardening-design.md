# 文件审核功能加固：R-1 中断轮次自愈 + R-6/R-7/R-8 + 前端测试基建（Vitest 迁移）

- 日期：2026-09-18
- 状态：设计定稿（待实施）
- 前置：`docs/superpowers/specs/2026-09-16-file-review-node-design.md`（全链路）+ CHANGE.md 2026-09-17 两条（收口审查遗留 R-1~R-8 与修复批次）

## 0. 背景与遗留清单

文件审核全链路 2026-09-17 已部署。收口审查与修复批次之后，仍记录在案的遗留：

| # | 级别 | 问题 |
|---|---|---|
| R-1 | Major | 中断轮次不自愈：服务重启后卡在 `reviewing/fixing` 的轮次只做「前端可识别 + 引导重新发起」，轮次行状态永不回落 |
| R-6 | Minor | REST fix 端点 `_ADMIT_LOCK.acquire(timeout=5s)` 同步阻塞事件循环 |
| R-7 | Minor | `admit_fix_round` 对 `levels=None` 会 TypeError（调用方已校验，防御性缺口） |
| R-8 | Minor | state 端点 `doc.object` 把 MinIO 内部对象名下发给前端 |
| 独立 | — | 仓库 `web/jest.config.ts` 依赖已移除的 `umi/test`，前端测试无法运行（现靠 `.scratch/` 本地脚手架） |
| 独立 | — | 3 个既有腐坏前端套件（chat.test / template-fill-confirm-card / useScrollToBottom，共 9 用例） |

用户决策：①本批全部处理；②R-1 **不改上游核心文件** `api/ragflow_server.py`，在 file_review 模块内解决；③前端测试基建**迁 Vitest**（不修 jest 配置）。

## 1. 第一性原理

R-1 的本质是「轮次行自称在跑、却没有任何活体会回来写它」——这是**可判定谓词**（`is_stale_running` 三判据，已上线验证）。既然谓词已存在，「不自愈」的唯一原因是只读不写：每次读取都重新派生一遍 `stale=true`，却从不把确定的事实写回行上。修法不是新增补偿机制（启动期扫描 / 后台线程），而是**在谓词成立的消费点上顺手把事实落库**——中断轮次从此与「线程内部崩溃」的 failed 轮（`_force_fail_round` 产物）**语义完全一致**：终态、error 有因、可继续发起修复轮（扣余额）。补偿式方案（janitor 线程 / 启动扫描）解决的是「没人读也要回落」，而回落唯一的消费场景就是读与受理——两者都接入 heal 后，及时性需求不存在，复杂度不该引入。

## 2. R-1：中断轮次惰性自愈

### 2.1 Service 层新增 `heal_stale_round(row) -> bool`

位置：`api/db/services/file_review_service.py`，紧邻 `is_stale_running`。

- 判据：直接复用 `is_stale_running(row)`（三判据不变：status ∈ RUNNING / spawn 未注册 / 行龄 > `STALE_GRACE_SECONDS=60`）。
- 动作：CAS 更新 `status='failed'` + `error='服务重启或异常退出，本轮审核已中断'`（经既有 `update_status(rid, status, **extra)` 通道）。
- 幂等：update 条件带 `status IN RUNNING_ROUND_STATUSES`（若 `update_status` 不支持条件，则在函数内先读后判、以「首个 CAS 赢家」为准；并发双 heal 最终行值相同，天然幂等——以实现时的最小改动为准，测试锁定幂等行为）。
- 返回：是否真的发生了回落。
- spawn 依赖保持函数内延迟 import（模块顶层不新增依赖边，同 `is_stale_running` 取向）。

### 2.2 消费点一：state 端点

`api/apps/restful_apis/file_review_api.py` 的 `review_state`：在 `_round_payload` 逐轮构建 payload 前，对每个 round 先 `heal_stale_round(r)`。效果：

- 前端第一次读取即看到 `status='failed'` + `error` 文案，走**既有的失败展示链路**（`current.error` 行），不再出现「已中断」红色僵尸态常驻；
- `rounds[].stale` 字段**保留**（`is_stale_running` 对已 heal 的 failed 行返回 False，故 heal 后 stale 恒为 false；字段仅覆盖「已中断但尚未被任何读取 heal」的瞬时窗口），前端既有 stale-aware 逻辑不动，向后兼容。

### 2.3 消费点二：`admit_fix_round` stale 闸门改为 heal 放行

现状：闸门 2 命中 stale 即抛 `FixAdmissionDenied("stale", "...请重新发起审核")`。

改为：命中 stale → `heal_stale_round(cur)` → 刷新 `cur` 的内存状态为 failed → **继续走后续闸门**（closing / no_quota / no_pending）。

语义变化：中断轮自愈后**若还有修复余额，用户可直接发起新修复轮**，不必重新发起审核——与崩溃 failed 轮行为一致。配套修改：

- docstring：闸门 2 描述从「唯一自助出口是重新发起」改为「heal 后与 failed 轮同权参与后续闸门」；
- `heal_stale_round` 返回 False（并发下别人已 heal / 状态已被别处回落）时同样刷新内存状态后继续——闸门从此不再产出 `stale` 拒绝码（保留 `FixAdmissionDenied` 类型本身，其他闸门仍在用）。

### 2.4 前端

**零逻辑改动**。failed 态展示、`canFix = !isRunning && fix_rounds_left > 0` 镜像闸门、`shouldPollFileReview` 终态停轮询，全部既有代码天然覆盖自愈后的状态。

## 3. R-6 / R-7 / R-8

### 3.1 R-6：fix 端点持锁段移出事件循环

`fix_review` 端点：`result = await asyncio.to_thread(admit_fix_round, task_id=..., tenant_id=..., levels=...)`。

- 同步阻塞锁等待与全部闸门 DB 查询移入线程池，事件循环不再被单请求最长 5s 阻塞；
- `admit_fix_round` docstring 中「调用方不得在持锁期间 await（REST 侧必须在无 await 的临界区内同步调用）」的约束改写为「同步函数，REST 侧必须经 `asyncio.to_thread` 调用」；
- `_ADMIT_LOCK` 语义不变（进程级 `threading.Lock`，跨线程有效）。

### 3.2 R-7：levels 判型

`admit_fix_round` 入口（取锁之前）：`if not isinstance(levels, list): raise FixAdmissionDenied("invalid_levels", "修复级别参数不合法")`。

- 复用既有异常形态，不引入新异常类型；
- 两个真实调用方均已在入口校验，本闸门纯防御，不可达路径的拒绝文案无需打磨。

### 3.3 R-8：doc payload 摘除 MinIO 对象名

已核实：`onPreviewDoc` 的两个调用点（`c-chat/index.tsx`、`flow/flow-detail.tsx`）**均丢弃 minioPath 参数**，只消费 `fileVersion`（预览/下载走专用 download 端点）。

- 后端 `_doc_payload` 返回 `{version, has_result}`，**删除 `object` 键**（无成稿时返回 `{version: "", has_result: false}`，替代原先「object=file_id 哨兵」的隐式约定）；
- 前端 `file-review-progress.tsx`：预览/下载按钮显示条件 `data.doc.object && data.doc.object !== fileId && data.doc.version` → `data.doc.has_result && data.doc.version`；`onPreviewDoc` 签名收窄为 `(fileVersion: string) => void`；
- 两个调用点同步简化（去掉 `_minioPath` 参数与相关注释）；
- `file_review_api.py` 中 `doc.object` 相关注释（含 T9 复盘注释）同步更新。

## 4. 前端测试基建：迁 Vitest + 修 3 个腐坏套件

### 4.1 Vitest 接入

- 新增 `web/vitest.config.ts`：`environment: 'jsdom'`、`@/` → `src/` 别名、setupFiles；
- setup 文件：移植 `.scratch/` 验证过的桩（`TextEncoder/TextDecoder`、`Request/Response/Headers` 最小桩、`Element.prototype.scrollTo/scrollIntoView`）+ RTL `afterEach(cleanup)`；
- jest 的 esbuild+babel 四段 transformer 流水线**整体丢弃**——vitest 原生 esbuild 处理 TS/TSX，`vi.mock` 原生 hoisting；
- `package.json`：`"test": "vitest run"`；devDeps 增 `vitest` `jsdom`，移除 jest 相关依赖；
- 删除 `web/jest.config.ts`、`web/jest-setup.ts`；
- `web/CLAUDE.md` 测试命令说明同步更新（Jest → Vitest）。

### 4.2 测试文件迁移（6 套件）

`jest.*` → `vi.*`（jest.fn/jest.mock/jest.spyOn 对应替换），涉及：

- `src/pages/c-chat/__tests__/file-review-progress.test.tsx`
- `src/hooks/__tests__/file-review-poll.test.ts`
- `src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
- `src/utils/__tests__/chat.test.ts`
- `src/hooks/__tests__/logic-hooks.useScrollToBottom.test.tsx`
- `src/pages/c-chat/docx-highlight-direct.test.ts`

### 4.3 修 3 个腐坏套件（测试没跟上实现）

1. `chat.test.ts`：断言对齐 `preprocessLaTeX` 现行为（`$$${equation}$$` 保留捕获组内空格）；
2. `template-fill-confirm-card.test.tsx`：先点开折叠头（2026-09-15 起确认卡默认折叠）再找字段按钮；
3. `logic-hooks.useScrollToBottom.test.tsx`：mock container 补 `scrollTo`。

## 5. 测试与验收

### 5.1 后端新增对抗用例

- `heal_stale_round`：stale 行 heal 成 failed+error；活线程行（is_running=True）绝不 heal；终态行不参与；宽限期边界（≤60s 不 heal）；**并发双 heal 幂等**（第二个返回 False 或行值不变）；
- `admit_fix_round`：stale 行 → heal → 有余额时**放行建新轮**（不再产 stale 拒绝）；heal 后余额耗尽被 `no_quota` 拒；`levels=None` / `levels="high"`（字符串）→ `invalid_levels` 拒绝且不建轮次；
- `review_state`：含 stale 行的 state 读取后行已落 failed、payload `stale=false`；doc payload **无 `object` 键**、无成稿时 `has_result=false`；
- 既有 337 用例全绿（注意 `test_snapshot_values_authoritative` 类契约反转检查：现有 stale 闸门拒绝用例需按新语义改写而非保留）。

### 5.2 前端验收

- `npm run test`（vitest run）全量绿，含修复的 3 套件；
- 改动文件 `tsc --noEmit` / `eslint` 零新增错误。

## 6. 交付边界

- **不自动部署**：后端 2-3 文件（`file_review_service.py`、`file_review_api.py`，若工具侧文案受影响则 + `agent/tools/file_review.py`）成套 SCP + 重启；前端 `npm run build` + dist + nginx reload——均等用户指令；
- 部署后删除本设计稿表格中的遗留登记（CHANGE.md 增量更新）。
