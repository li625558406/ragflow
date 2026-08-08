# A2: Detector 走旁路（in-process 执行）

> **部署日期**: 2026-08-07
> **背景**: 智能采集系统卡死，11 万文档积压，排查发现 detector-meta-system 任务无脑入队，与真爬虫/文档解析抢同一 task_executor 进程，导致 Redis 主队列 19000+ 任务积压，detector 自身被堵几小时，完全失去实时性。
> **方案核心**: detector 不再入主队列，改为在 `scheduled_task_executor` 进程内直接执行（PID 377，几乎空闲），task_executor（PID 378）专注真爬虫 + 文档解析 + notification。

---

## 1. 问题诊断（部署前数据）

| 指标 | 部署前 |
|------|-------|
| 主队列 `rag_flow_svr_queue` 长度 | 19061（积压） |
| lag（未投递给 consumer） | 8737 |
| 文档 run='1' RUNNING 卡死 | 8035（progress≈0，"8774 tasks are ahead in the queue"） |
| detector-meta 入队频率 | 每 60s 一条（无脑） |
| detector 实际处理节奏 | 几小时一次（被堵在队列里） |
| 真实采集任务平均耗时 | 489s（8 分钟） |
| detector 偶发 SPA hang | 命中 300s timeout |

**根因**: scheduled_task_executor 不管队列深度，每 60s 入队一条 detector-meta；task_executor 单进程处理三类重活（detector / crawler / parse），detector 把吞吐吃光，parse 任务永远排队。

---

## 2. 方案：A2 detector in-process bypass

### 设计原则（第一性原理）

detector 本质是"轻量探针"（只取列表页 page 1，hash 对比，命中变化才入队真爬虫），本不该和重活抢同一队列。把它放回调度进程内是结构归位。

### 改动文件清单（4 文件）

| 文件 | 改动要点 |
|------|---------|
| `rag/svr/crawler_detector.py` | 抽出 `run_detection(tenant_id, config_path)` 可复用入口；`main()` CLI 转调；`finally` 调 `cleanup_browser_pool()` |
| `rag/svr/scheduled_task_executor.py` | `dispatch_due_tasks` 识别 detector 任务走旁路；新增 `_run_detector_inproc(task, log_id)` 协程；模块级 `_detector_lock` (asyncio.Lock) + `_detector_pool` (ThreadPoolExecutor max_workers=1) |
| `rag/svr/task_executor.py` | L1126 之后加 detector 防双跑护栏（主队列残留 detector 消息一律 skip） |
| `rag/svr/crawler_engine/browser_pool.py` | 新增 `is_healthy()` / `force_reset()`；`get_browser_pool()` 加健康检查自愈 |

### 关键设计点

1. **为什么用 `ThreadPoolExecutor(max_workers=1)`**: detector 内部用 sync_playwright，必须丢到线程里跑，不阻塞 asyncio 主循环；max_workers=1 串行化 BrowserPool 访问，避免多线程竞态。
2. **为什么用 `asyncio.Lock` 而不是 Redis 分布式锁**: detector 改造后只在 scheduled_task_executor 单进程内运行，进程内锁即可。
3. **lock.locked() 检查防积压**: 上一轮 detector 未完成时，dispatch 检查 lock 直接 skip 本轮，保持 60s 节奏同时避免协程句柄无限增长。
4. **每轮 cleanup_browser_pool()**: 释放 Chromium（~5% 冷启动开销换稳定性，避免内存膨胀/状态污染）。
5. **CLI 入口保留**: 紧急回滚只需改 scheduled_task_executor.py 一个文件。
6. **timeout 不彻底的取舍**: `asyncio.wait_for` 无法强杀线程里的 sync Playwright。timeout 触发后强制 `cleanup_browser_pool()`，下轮自动重建 Chromium。

---

## 3. 部署后效果（2026-08-07 11:00 观察）

| 指标 | 部署前 | 部署后 |
|------|-------|-------|
| 主队列 detector-meta 入队 | 每 60s 一条 | **不再入队** |
| task_executor 干扰 | 处理 detector backlog | 专注 parse + crawler |
| run='3' DONE | 100749 | **100785 (+36)** |
| run='1' RUNNING 卡死 | 8035 | **7936 (-99)** ← 在恢复 |
| detector 触发节奏 | 几小时一次 | 严格按 scheduled_task 表配置 |

### 日志特征

部署后正常工作流：
```
[inproc] detector dispatched (log=xxx, tenant=system)
detector: 127 YAML sites loaded, 71 active in crawler_task
BrowserPool: started Chromium at /opt/chrome/chrome
detector: enqueued collection crawl for site=xxx
[inproc] detector ok: triggered=N unchanged=N skipped=N errors=0
```

被 lock 跳过（上一轮未完）：
```
[inproc] detector skipped: previous round still running (log=xxx)
```

timeout 自愈：
```
[inproc] detector timeout after 600s (log=xxx)
BrowserPool: cleaned up
[inproc] detector dispatched (log=yyy)  ← 下轮重启 Chromium
```

task_executor 处理主队列残留 detector 消息（双跑护栏）：
```
scheduled_script task xxx skipped: detector now runs in-process
```

---

## 4. 配置

`scheduled_task` 表中 `detector-meta-system` 任务的 `timeout` 字段控制单轮探测最长时间。

```sql
-- 查看当前配置
SELECT id, name, timeout, interval_seconds FROM scheduled_task WHERE id='detector-meta-system';

-- 调整 timeout（71 站点 SPA 串行，建议 600s）
UPDATE scheduled_task SET timeout=600 WHERE id='detector-meta-system';
```

无需重启容器，下次 dispatch 自动读取新值。

---

## 5. 验证方法

### 部署后冒烟

```bash
# 容器内 import 检查
docker exec docker-ragflow-cpu-1 python -c "
from rag.svr.crawler_detector import run_detection
from rag.svr.crawler_engine.browser_pool import cleanup_browser_pool
print('OK')
"

# 看旁路日志（应每 60s 出现 dispatched/skipped，不再有 Enqueued scheduled task detector-meta）
docker logs -f --since 5m docker-ragflow-cpu-1 | grep -E '\[inproc\]|Enqueued scheduled task detector-meta'
```

### 容器内手动跑一轮 detector

```bash
docker exec docker-ragflow-cpu-1 python -c "
from rag.svr.crawler_detector import run_detection
r = run_detection('system')
print(r)
"
```

预期：3-10s 完成（多数站点 not_due），返回 `{triggered: N, unchanged: N, skipped: 70+, errors: 0}`。

---

## 6. SCP 部署清单（4 文件成套）

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no \
  rag/svr/crawler_detector.py \
  rag/svr/scheduled_task_executor.py \
  rag/svr/task_executor.py \
  root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/

scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no \
  rag/svr/crawler_engine/browser_pool.py \
  root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/crawler_engine/

ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 "docker restart docker-ragflow-cpu-1"
```

---

## 7. 回滚

在 `scheduled_task_executor.py` 中删除 `if script_path.endswith(_DETECTOR_SCRIPT):` 整段分支，所有任务恢复走 `REDIS_CONN.queue_product` 主队列。CLI 入口完全保留，可单独 subprocess 跑 detector 调试。

---

## 8. 已知限制 / 后续优化方向

### 当前限制

- **detector 自身仍慢**：71 站点 SPA 串行，单轮 5-15 分钟。但已不影响主目标（task_executor 解析恢复）。
- **timeout 不彻底**：`asyncio.wait_for` 无法强杀 sync Playwright 线程，靠 BrowserPool cleanup 兜底。

### 后续优化方向

| 方案 | 描述 | 预期收益 |
|------|------|---------|
| **B. detector 并行化** | 多站点并发（asyncio + ThreadPoolExecutor max_workers=N），把 71 站点串行 → N=4 并行 | 单轮时长 ×1/N |
| **C. SPA 站点单独慢路** | 把 SPA 渲染站点分离，detector 只探 REST API 站点，SPA 走单独慢队列 | 主探测器回到秒级 |
| **D. crawl4ai 独立服务** | 爬虫完全脱离 ragflow worker，REST 调用回写（见 `crawl4ai-service-独立部署方案.md`） | 架构层面解耦 |

### 不要做的事

- ❌ 不要把真爬虫（unified_crawler）也走旁路 —— 它有 LD_PRELOAD 隔离需求 + 体积大，会拖死 scheduled_task_executor 主循环。
- ❌ 不要把 notification 也走旁路 —— 它写 Redis 消息队列给 websocket，需要与 task_executor 协调。
- ❌ 不要删 task_executor 的 `handle_scheduled_script_task` 函数 —— 其它 scheduled_script 仍走主队列。

---

## 9. 相关文件

| 文件 | 路径 | 作用 |
|------|------|------|
| 主调度器 | `rag/svr/scheduled_task_executor.py` | 入队 + A2 detector 旁路 |
| Worker | `rag/svr/task_executor.py` | 消费主队列 + 双跑护栏 |
| Detector 入口 | `rag/svr/crawler_detector.py` | 探测逻辑 + `run_detection()` |
| SiteDetector | `rag/svr/crawler_engine/detector.py` | 单站探测 + signature 算法 |
| BrowserPool | `rag/svr/crawler_engine/browser_pool.py` | Chromium 单例 + 健康检查 |
| 站点配置 | `rag/svr/crawler_sites.yaml` | 78 个站点 YAML |
| 调度表 | `scheduled_task` (MySQL) | detector-meta-system 配置 |
| 日志表 | `scheduled_task_log` (MySQL) | 每次执行 success/fail/skipped 记录 |

---

**Spec/Plan 归档**: `C:\Users\lg186\.claude\plans\joyful-marinating-cat.md`
