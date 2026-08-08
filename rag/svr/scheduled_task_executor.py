#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import asyncio
import contextlib
import faulthandler
import io
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from common import settings
from common.config_utils import show_configs
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from common.versions import get_ragflow_version
from rag.utils.redis_conn import REDIS_CONN

POLL_INTERVAL = int(os.environ.get("SCHEDULED_TASK_POLL_INTERVAL", "15"))
stop_event = asyncio.Event()

# A2 旁路: detector 任务在本进程内执行, 不再入 Redis 主队列.
# 之前 detector-meta-system 每 60s 入队一条, 与真爬虫 / 文档解析抢同一
# task_executor 进程, 导致 19000+ 任务积压 + detector 自身被堵几小时.
# 旁路后, detector 严格 60s 触发 (PID 377 几乎空闲, 直接承担探测).
_DETECTOR_SCRIPT = "rag/svr/crawler_detector.py"

# 串行化: 上一轮 detector 没跑完前, 下一轮 dispatch 创建的任务会等待.
# 替代了原 Redis 队列 backlog 机制 —— 既然在本进程跑, asyncio.Lock 就够了.
_detector_lock = asyncio.Lock()

# 专用单 worker 线程池: detector 内部用 sync_playwright (browser_pool.py),
# 必须丢到线程里跑, 不阻塞主 asyncio 循环; max_workers=1 串行化 BrowserPool
# 访问, 避免多线程竞态.
_detector_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="detector")

# 通知旁路: notification 也在本进程内执行, 不入 Redis 主队列.
# 之前 notification-meta-system 每 120s 入队一条, 被 ~8457 条爬虫/解析任务
# 堵在主队列后面, 755 条 running 日志从未执行, 新采集数据迟迟无通知.
# 旁路后恢复 120s 实时生成. scan_once() 是纯 DB + Redis 操作 (无 Playwright /
# 无 LLM / 无 websocket 推送), 比 detector 还轻, 自带幂等 (batch_key 唯一索引)
# + Redis 锁 (notif:scan:lock TTL 110s) 防双跑.
_NOTIFIER_SCRIPT = "rag/svr/notification_generator.py"

# 与 detector 锁同理: 上一轮 scan 未完成时本轮 dispatch 直接 skip.
_notification_lock = asyncio.Lock()

# ★ 必须与 _detector_pool 分开: detector 单轮可能跑 5-15 分钟占住线程,
# 若共用同一池, 毫秒级的 notification scan 会被排在 detector 后面干等,
# 失去实时性. 独立单 worker 池保证两者互不阻塞.
_notification_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notification")


def signal_handler(sig, frame):
    logging.info("Received interrupt signal, shutting down...")
    stop_event.set()
    time.sleep(1)
    sys.exit(0)


async def compute_next_run(task: dict) -> int:
    """Compute the next run timestamp (in milliseconds) based on schedule_type."""
    now = datetime.now().astimezone()
    if task.get("schedule_type") == "cron" and task.get("cron_expression"):
        try:
            from croniter import croniter

            cron = croniter(task["cron_expression"], now)
            return int(cron.get_next() * 1000)
        except Exception as e:
            logging.warning(f"Failed to compute next cron run for task {task.get('id')}: {e}")
            return current_timestamp() + 3600000  # default 1h
    elif task.get("schedule_type") == "interval" and task.get("interval_seconds"):
        return current_timestamp() + int(task["interval_seconds"]) * 1000
    return current_timestamp() + 3600000


async def dispatch_due_tasks():
    """Query and enqueue all due scheduled tasks to Redis."""
    from api.db.services.scheduled_task_service import (
        ScheduledTaskService,
        ScheduledTaskLogService,
    )

    try:
        due_tasks = ScheduledTaskService.get_due_tasks()
    except Exception as e:
        logging.warning(f"Failed to query due tasks: {e}")
        return

    if not due_tasks:
        return

    logging.info(f"Found {len(due_tasks)} due scheduled task(s)")

    for task in due_tasks:
        # Clear any stale cancel flag from a previous stop.
        REDIS_CONN.delete(f"{task['id']}-cancel")

        log_id = get_uuid()
        log_entry = {
            "id": log_id,
            "task_id": task["id"],
            "tenant_id": task["tenant_id"],
            "status": "running",
            "start_time": current_timestamp(),
        }
        try:
            ScheduledTaskLogService.save(**log_entry)
        except Exception as e:
            logging.error(f"Failed to create log entry for task {task['id']}: {e}")
            continue

        script_path = task.get("script_path", "") or ""
        # A2 旁路: detector 在本进程内执行, 不入主队列.
        if script_path.endswith(_DETECTOR_SCRIPT):
            # 防止 lock 积压: 若上一轮 detector 仍在跑 (如 SPA hang 触发 timeout
            # 后线程仍在收尾), 本轮直接放弃, 不再 create_task —— 保持 60s 节奏
            # 同时避免协程句柄无限增长. 上一轮结束后下一轮自然触发.
            if _detector_lock.locked():
                logging.info(
                    "[inproc] detector skipped: previous round still running (log=%s)",
                    log_id,
                )
                try:
                    ScheduledTaskLogService.update_by_id(log_id, {
                        "status": "skipped",
                        "end_time": current_timestamp(),
                        "duration": 0.0,
                        "error_msg": "Previous detector round still running",
                    })
                except Exception:
                    pass
                # 仍然推进 next_run_time, 保持调度节奏
                next_run = await compute_next_run(task)
                ScheduledTaskService.update_by_id(task["id"], {
                    "last_run_time": current_timestamp(),
                    "last_run_status": "running",
                    "next_run_time": next_run,
                    "retry_count": 0,
                })
                continue
            asyncio.create_task(_run_detector_inproc(task, log_id))
            # 立即写 next_run_time (与原入队行为对齐: 入队即排程下一轮)
            next_run = await compute_next_run(task)
            ScheduledTaskService.update_by_id(
                task["id"],
                {
                    "last_run_time": current_timestamp(),
                    "last_run_status": "running",
                    "next_run_time": next_run,
                    "retry_count": 0,
                },
            )
            logging.info(
                f"[inproc] detector dispatched (log={log_id}, tenant={task['tenant_id']})"
            )
            continue

        # 通知旁路: notification 在本进程内执行, 不入主队列 (结构归位, 同 detector).
        if script_path.endswith(_NOTIFIER_SCRIPT):
            if _notification_lock.locked():
                logging.info(
                    "[inproc] notification skipped: previous scan still running (log=%s)",
                    log_id,
                )
                try:
                    ScheduledTaskLogService.update_by_id(log_id, {
                        "status": "skipped",
                        "end_time": current_timestamp(),
                        "duration": 0.0,
                        "error_msg": "Previous notification scan still running",
                    })
                except Exception:
                    pass
                # 仍然推进 next_run_time, 保持调度节奏
                next_run = await compute_next_run(task)
                ScheduledTaskService.update_by_id(task["id"], {
                    "last_run_time": current_timestamp(),
                    "last_run_status": "running",
                    "next_run_time": next_run,
                    "retry_count": 0,
                })
                continue
            asyncio.create_task(_run_notification_inproc(task, log_id))
            # 立即写 next_run_time (与原入队行为对齐: 入队即排程下一轮)
            next_run = await compute_next_run(task)
            ScheduledTaskService.update_by_id(
                task["id"],
                {
                    "last_run_time": current_timestamp(),
                    "last_run_status": "running",
                    "next_run_time": next_run,
                    "retry_count": 0,
                },
            )
            logging.info(
                f"[inproc] notification dispatched (log={log_id}, tenant={task['tenant_id']})"
            )
            continue

        msg = {
            "id": log_id,
            "task_type": "scheduled_script",
            "tenant_id": task["tenant_id"],
            "name": task.get("name", ""),
            "script_path": task["script_path"],
            "script_args": task.get("script_args", "") or "",
            "timeout": task.get("timeout", 3600),
            "task_id_ref": task["id"],
            "target_url": task.get("target_url", "") or "",
            "llm_id": task.get("llm_id", "") or "",
            "llm_model_name": task.get("llm_model_name", "") or "",
            "kb_id": task.get("kb_id", "") or "",
            "access_token": task.get("access_token", "") or "",
        }

        # prio 1: scheduled_script(定时爬虫/用户脚本)优先于解析积压;
        # task_executor.collect 先消费 prio 1 再 prio 0, 解析类仍在 prio 0.
        if not REDIS_CONN.queue_product(settings.get_svr_queue_name(1), message=msg):
            logging.error(f"Failed to enqueue scheduled task {task['id']} to Redis")
            ScheduledTaskLogService.update_by_id(
                log_id, {"status": "fail", "error_msg": "Redis enqueue failed"}
            )
            continue

        # Compute and persist next run time
        next_run = await compute_next_run(task)
        ScheduledTaskService.update_by_id(
            task["id"],
            {
                "last_run_time": current_timestamp(),
                "last_run_status": "running",
                "next_run_time": next_run,
                "retry_count": 0,
            },
        )
        logging.info(
            f"Enqueued scheduled task {task['id']} ({task['name']}), next run at {next_run}"
        )


async def _run_detector_inproc(task: dict, log_id: str) -> None:
    """A2: 在 scheduled_task_executor 进程内执行 detector.

    替代了原 ``REDIS_CONN.queue_product → task_executor.handle_scheduled_script_task``
    链路. detector 内部用 sync_playwright, 必须丢到 ``_detector_pool`` 单 worker
    线程池里跑, 否则会阻塞 asyncio 主循环.

    成功 / 失败 / 超时 三路都回写 ScheduledTaskLogService 和 ScheduledTaskService,
    与 task_executor.handle_scheduled_script_task 的字段保持一致 —— 前端监控面板
    看不出差别.

    ``_detector_lock`` 保证上一轮未完成时下一轮 dispatch 创建的协程会等待,
    不会并发触发 BrowserPool 竞态.
    """
    from api.db.services.scheduled_task_service import (
        ScheduledTaskLogService,
        ScheduledTaskService,
    )
    from rag.svr.crawler_detector import run_detection

    timeout_sec = task.get("timeout", 300)
    async with _detector_lock:
        start_ts = time.time()
        loop = asyncio.get_running_loop()
        try:
            def _sync_run():
                # run_detection 内部 _safe_print 用 print(), 重定向捕获,
                # 写到 ScheduledTaskLogService.output 供前端日志面板查看.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    return run_detection(task["tenant_id"]), buf.getvalue()

            result, captured = await asyncio.wait_for(
                loop.run_in_executor(_detector_pool, _sync_run),
                timeout=timeout_sec,
            )
            duration = time.time() - start_ts
            summary = (
                f"[inproc] detector ok: triggered={result['triggered']} "
                f"unchanged={result['unchanged']} skipped={result['skipped']} "
                f"errors={result['errors']}"
            )
            logging.info(summary)
            output_text = summary + "\n" + "\n".join(result["summary_lines"])
            if captured:
                output_text += "\n--- stdout ---\n" + captured
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "success",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "output": output_text,
                "error_msg": "",
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "success",
                "retry_count": 0,
            })
        except asyncio.TimeoutError:
            duration = time.time() - start_ts
            logging.error(
                "[inproc] detector timeout after %ss (log=%s)",
                timeout_sec, log_id,
            )
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "fail",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "error_msg": f"Timeout after {timeout_sec}s",
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "fail",
            })
            # 强制重置 BrowserPool, 防 Chromium 卡死后所有后续探测失败.
            # 注意: sync_playwright 在线程里跑, asyncio.wait_for 取消后线程
            # 未必立即停, 这里 cleanup 可能 race —— 但 BrowserPool 设计为
            # 幂等, 多调一次 cleanup 无害.
            try:
                from rag.svr.crawler_engine.browser_pool import cleanup_browser_pool
                cleanup_browser_pool()
            except Exception as e:
                logging.warning("[inproc] cleanup_browser_pool after timeout failed: %s", e)
        except Exception as e:
            duration = time.time() - start_ts
            logging.exception("[inproc] detector crashed (log=%s)", log_id)
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "fail",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "error_msg": str(e)[:5000],
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "fail",
            })


async def _run_notification_inproc(task: dict, log_id: str) -> None:
    """在 scheduled_task_executor 进程内执行 notification scan.

    替代原 ``REDIS_CONN.queue_product → task_executor.handle_scheduled_script_task``
    链路. ``notification_generator.scan_once()`` 是纯 DB + Redis 操作 (无 Playwright /
    无 LLM / 无 websocket 推送), 毫秒~秒级, 丢到 ``_notification_pool`` 线程池避免
    阻塞 asyncio 主循环.

    通知任务之前留在主队列, 被 ~8457 条爬虫/解析任务堵死 (755 条 running 日志从未
    执行), 新采集数据迟迟无通知. 旁路后恢复 120s 实时生成. scan_once() 自带幂等
    (batch_key 唯一索引) + Redis 锁 (notif:scan:lock TTL 110s) 防双跑, 因此与
    task_executor 无耦合, 安全旁路.

    成功 / 失败 / 超时 三路都回写 ScheduledTaskLogService 和 ScheduledTaskService,
    字段与 task_executor.handle_scheduled_script_task 保持一致, 前端监控面板无差异.

    注意: scan_once() 无 Chromium, 超时后无需 cleanup_browser_pool (区别于 detector).
    """
    from api.db.services.scheduled_task_service import (
        ScheduledTaskLogService,
        ScheduledTaskService,
    )
    from rag.svr.notification_generator import scan_once

    timeout_sec = task.get("timeout", 180)
    async with _notification_lock:
        start_ts = time.time()
        loop = asyncio.get_running_loop()
        try:
            def _sync_run():
                # scan_once 内部用 logging, 但捕获 stdout/stderr 以收进 output 字段,
                # 供前端日志面板查看 (与 detector 一致).
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    return scan_once(), buf.getvalue()

            stats, captured = await asyncio.wait_for(
                loop.run_in_executor(_notification_pool, _sync_run),
                timeout=timeout_sec,
            )
            duration = time.time() - start_ts
            summary = (
                f"[inproc] notification ok: created={stats['notifications_created']} "
                f"sites={stats['sites_scanned']} errors={stats['errors']} "
                f"skipped_lock={stats['skipped_lock']} old_deleted={stats['old_deleted']}"
            )
            logging.info(summary)
            output_text = summary
            if captured:
                output_text += "\n--- stdout ---\n" + captured
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "success",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "output": output_text,
                "error_msg": "",
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "success",
                "retry_count": 0,
            })
        except asyncio.TimeoutError:
            duration = time.time() - start_ts
            logging.error(
                "[inproc] notification timeout after %ss (log=%s)",
                timeout_sec, log_id,
            )
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "fail",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "error_msg": f"Timeout after {timeout_sec}s",
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "fail",
            })
        except Exception as e:
            duration = time.time() - start_ts
            logging.exception("[inproc] notification crashed (log=%s)", log_id)
            ScheduledTaskLogService.update_by_id(log_id, {
                "status": "fail",
                "end_time": int(time.time() * 1000),
                "duration": duration,
                "error_msg": str(e)[:5000],
            })
            ScheduledTaskService.update_by_id(task["id"], {
                "last_run_time": int(time.time() * 1000),
                "last_run_status": "fail",
            })


async def main():
    logging.info(r"""
   ____       _           _         _            _____     _        _                   _
  / ___|  ___| | ___  ___| |__   __| | ___  ___ |_   _|__ | |_  ___| |_ _ __ ___  _   _| |_ ___
 \___ \ / _ \ |/ _ \/ __| '_ \ / _` |/ _ \/ __|  | |/ _ \| \ \/ / | __| '__/ _ \| | | | __/ _ \
  ___) |  __/ |  __/ (__| | | | (_| |  __/\__ \  | | (_) | |\  /| | |_| | | (_) | |_| | ||  __/
 |____/ \___|_|\___|\___|_| |_|\__,_|\___||___/  |_|\___/|_| \/ |_|\__|_|  \___/ \__,_|\__\___|
    """)
    logging.info(f"RAGFlow scheduled task executor version: {get_ragflow_version()}")
    show_configs()
    settings.init_settings()
    if sys.platform != "win32":
        from common.signal_utils import start_tracemalloc_and_snapshot, stop_tracemalloc

        signal.signal(signal.SIGUSR1, start_tracemalloc_and_snapshot)
        signal.signal(signal.SIGUSR2, stop_tracemalloc)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logging.info(
        f"Scheduled task executor ready. Poll interval: {POLL_INTERVAL}s"
    )
    while not stop_event.is_set():
        await dispatch_due_tasks()
        await asyncio.sleep(POLL_INTERVAL)
    logging.error("BUG!!! You should not reach here!!!")


if __name__ == "__main__":
    CONSUMER_NAME = "scheduled_task_executor"
    faulthandler.enable()
    init_root_logger(CONSUMER_NAME)
    asyncio.run(main())
