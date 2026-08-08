# crawl-dedup: 爬虫排队去重（crawl:queued 标记）

> **部署日期**: 2026-08-08
> **背景**: A2 detector 旁路上线后，主队列仍膨胀到 XLEN ~21000 / lag ~8400。排查发现 503 条 `detect:<site>` 重复爬虫任务（68 站点，最严重 cebpubservice×65），每条 ~8min 串行烧单 worker，解析任务被饿死。
> **方案核心**: `crawl:queued:{site}` Redis 标记 —— 入队前 SET NX 抢占，task_executor 跑完 DEL，保证**每站最多一条"排队中/运行中"爬虫**，扛任意队列积压。

---

## 1. 问题诊断（部署前数据）

| 指标 | 部署前 |
|------|-------|
| 主队列 `rag_flow_svr_queue` XLEN | 21188 |
| lag（未投递给 consumer） | ~8400 |
| `detect:<site>` 爬虫消息 | **503 条 / 68 站点** |
| 最严重重复 | cebpubservice×65, ccgp_bxsearch×41, ggzy_quanguo×36, ggzyjd_cases×32 |
| 单条爬虫耗时 | ~8min（单 worker 串行） |
| 重复任务总浪费 | ~435 条 × 8min ≈ **58 小时** worker 时间 |

**根因**: detector 探测到站点内容变化就无条件入队爬虫，无"该站已有任务在排队/运行"判断。高频发标站点每个 detect_interval 都命中变化，叠加队列积压（前一条还没轮到），滚雪球堆出几十条重复。爬虫幂等（DB upsert + processed_ids），数据不错乱，纯浪费吞吐。

---

## 2. 被否决的第一版：TTL 自愈（重要教训）

**设计**: NX key + 30~60min TTL，"过期自动放行"。

**为什么错**: 纯 TTL 只限制**入队频率**，不限制**队列内数量**。队列积压时任务排队时长 >> TTL：key 过期时任务还在排队，下一轮 detector 又能入队一条 → 重复照旧。

**判据**: 给"排队中任务"做防护，先问"key 过期/释放的那一刻，任务在哪？" —— 若任务还在排队，防护就是假的。

---

## 3. 正确设计：DEL-on-completion

### 生命周期

```
crawler_detector._enqueue_full_crawl          task_executor.handle_scheduled_script_task
─────────────────────────────────────         ──────────────────────────────────────────────
SET crawl:queued:{site} NX EX 21600           try: subprocess 跑 detect: 爬虫
  ├─ 抢到 → 继续组装入队                        ├─ 成功
  ├─ 没抢到 → skip 入队 (已有任务在排队/跑)      ├─ except TimeoutError (kill 子进程)
  └─ Redis 异常 → fail-open 放行走入队          ├─ except Exception
      (宁偶发重复, 不漏采整站)                   └─ finally: DEL crawl:queued:{site}
queue_product 失败/异常 → DEL 回收标记              (name 以 detect: 开头才 DEL, 其余 no-op)
```

### 改动文件（2 文件）

| 文件 | 改动 |
|------|------|
| `rag/svr/crawler_detector.py` | 新增 `CRAWL_QUEUED_KEY` / `CRAWL_QUEUED_SAFETY_TTL` 常量、`_release_crawl_queued()` 兜底函数；`_enqueue_full_crawl` 入队前 SET NX 抢占，入队失败/异常回收标记 |
| `rag/svr/task_executor.py` | 新增 `_CRAWL_QUEUED_KEY`（格式必须与 detector 端一致）、`_release_crawl_queued_marker(task)`；`handle_scheduled_script_task` 末尾加 `finally:` 释放标记 |

### 关键设计点

1. **为什么 DEL-on-completion 能扛积压**: 标记生命周期 = 排队中 + 运行中全程。只要消息还在 stream 里（或正在跑），标记就在，detector 无法重复入队。
2. **6h SAFETY_TTL 的角色**: 只是孤儿 key 最后保险 —— 容器崩溃导致消息丢失、或手动 XTRIM 掉消息时，6h 后站点自动解锁。正常路径永远由 task_executor 的 finally DEL 触发，TTL 不到期。
3. **为什么 fail-open**: Redis NX 检查异常时放行走入队。爬虫幂等，偶发重复无害；若 fail-close 则可能把整站漏采。
4. **释放端识别条件**: `task["name"].startswith("detect:")`（detector 入队消息 name 固定为 `detect:<site_id>`）。其它 scheduled_script 任务走 finally 时为 no-op。
5. **重启场景闭环**: task_executor 启动时 `get_unacked_iterator` 重新投递 pending（unACKed）消息，消息不会凭空消失 → 跑完 DEL 路径在容器重启后依然成立。
6. **两端 key 格式一致性**: 两个常量各自定义 + 注释互相引用（`★ 必须与 ... 完全一致`），冒烟测试 assert 相等。

---

## 4. 部署后清理（一次性，幂等）

旧重复消息不会被新逻辑自动清掉（它们入队时没有标记），必须手动清理，否则重启后 435 条旧重复仍会被逐条跑掉（~58 小时）。

脚本: `.claude/scratch/cleanup_dup_detect_tasks.py`（容器内执行）

逻辑:
1. 分页 XRANGE 扫全 stream（游标翻页: `next_min = f"{seq}-{int(ms)+1}"`，同毫秒条目靠 seq 区分）
2. 解析 `message` 字段 JSON，找 `task_type=="scheduled_script"` 且 name 以 `detect:` 开头的消息
3. 每站保留**最早** 1 条（consumer 先进先出，留最早的最先跑完释放），XDEL 其余
4. 给每个保留站点 SET `crawl:queued:{site}` NX EX 21600 —— **重建去重状态**，防止清理窗口内 detector 又塞新任务

执行结果（2026-08-08）:

| 项 | 值 |
|----|----|
| 扫描条目 | 21188 |
| detect: 消息 | 503 条 / 68 站点 |
| XDEL 重复 | **435 条** |
| XLEN 清理后 | 20753 |
| 剩余 detect: | 68（每站 1 条） |
| 补设标记 | 65 新设 + 3 已存在 |

**脚本踩坑**: 导入必须 `from common import settings` 最先，否则 `rag.utils.redis_conn` ↔ `common.settings`（L36 反向 import REDIS_CONN）循环导入 ImportError。

---

## 5. 验证

### 部署冒烟

```bash
docker exec docker-ragflow-cpu-1 python -c "
from rag.svr.crawler_detector import run_detection, CRAWL_QUEUED_KEY, CRAWL_QUEUED_SAFETY_TTL, _release_crawl_queued
import rag.svr.task_executor as te
assert te._CRAWL_QUEUED_KEY == CRAWL_QUEUED_KEY, 'key format mismatch'
print('OK', CRAWL_QUEUED_KEY, CRAWL_QUEUED_SAFETY_TTL)
"
# 预期: OK crawl:queued:{site} 21600
```

### 线上生效证据（部署后日志）

```
detector: crawl already queued/running for site=cebpubservice; skip duplicate enqueue
[inproc] detector ok: triggered=1 unchanged=0 skipped=69 errors=0
```

detector 探测到 cebpubservice（原重复之王 ×65）又有变化，但标记已存在 → 正确拦下，不再入队。

### 队列状态

```bash
# XLEN / detect: 剩余 / 标记数 三者核对
docker exec docker-redis-1 redis-cli -a infini_rag_flow -n 1 --no-auth-warning KEYS 'crawl:queued:*' | wc -l
# 预期: 与队列中 detect: 消息数一致（每站 1 + 1）
```

### 观察标记释放

detect: 爬虫跑完后（~8min），日志 `scheduled_script task ... finished` 之后该站标记消失：

```bash
docker exec docker-redis-1 redis-cli -a infini_rag_flow -n 1 --no-auth-warning EXISTS crawl:queued:<site>
# 跑完前 1, 跑完后 0; 之后 detector 探测到变化可正常再次入队
```

---

## 6. SCP 部署清单（2 文件）

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no \
  rag/svr/crawler_detector.py \
  rag/svr/task_executor.py \
  root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/

ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 "docker restart docker-ragflow-cpu-1"
# 重启后执行 §4 清理脚本（容器内）
```

注意: `task_executor.py` 同时包含通知旁路的 skip guard（A2 后续改动），部署即一并生效。

---

## 7. 回滚

删除两处即可完全回滚（标记残留 6h 后自动过期，或手动 `KEYS crawl:queued:*` + DEL 清理）：

1. `crawler_detector.py::_enqueue_full_crawl` 中删除 SET NX 抢占段（`queued_key = ...` 到 `return False`）及两处 `_release_crawl_queued` 调用
2. `task_executor.py::handle_scheduled_script_task` 删除 `finally:` 子句

回滚后恢复"无条件入队"原行为（重复任务回归，但功能不受影响 —— 爬虫幂等）。

---

## 8. 已知限制 / 后续方向

| 项 | 说明 |
|----|------|
| 队列存量积压 | 去重只防新增重复；存量 ~20700 条（解析为主）仍需时间消化，或评估 XTRIM/优先级队列 |
| 单 worker 串行 | 爬虫与解析共用 task_executor，爬虫 ~8min/条；后续方向见 A2 文档 §8（crawl4ai 独立服务） |
| 用户手工命名冲突 | 若有人建名为 `detect:<site>` 的手工定时任务，会提前释放标记 —— 不现实边界，未防护 |

---

## 9. 相关文件

| 文件 | 作用 |
|------|------|
| `rag/svr/crawler_detector.py` | 入队前 SET NX 抢占 + 失败回收 |
| `rag/svr/task_executor.py` | finally DEL 释放 + skip guard（detector/notification 旁路） |
| `rag/svr/scheduled_task_executor.py` | A2 detector 旁路 + notification 旁路（in-process） |
| `.claude/scratch/cleanup_dup_detect_tasks.py` | 一次性队列清理脚本（幂等可重跑） |
| `踩坑问题清单.md` #30 | 本问题的踩坑条目 |
| `A2-detector-inprocess-旁路方案.md` | 前置方案：detector 进程内执行 |
