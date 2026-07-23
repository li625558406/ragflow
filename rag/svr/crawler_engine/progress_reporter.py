"""ProgressReporter — 通过 Redis pubsub 上报任务进度/日志给 WebSocket 前端。

约束：
- 无 Redis 时静默降级（所有 publish 静默返回），绝不影响爬虫主流程
- 所有 publish 异常被捕获并降级为 warning 日志
- channel: crawler:task:{task_id}        (pubsub, 实时推送)
- list:    crawler:task:{task_id}:history (RPUSH+LTRIM 保留最近 500 条历史)

消息类型:
- {type: "progress", page, total_pages, new, scanned, ts}
- {type: "log", text, level, ts}     # level: info|warning|error
- {type: "done", status, summary, ts}  # status: success|fail|skipped
"""
import json
import logging
import time
from typing import Any, Dict, Optional

try:
    from rag.utils.redis_conn import REDIS_CONN
except ImportError:
    REDIS_CONN = None  # type: ignore


class ProgressReporter:
    """Publish crawl progress/log events to a Redis pubsub channel.

    Constructed per-task. If Redis is unavailable or the connection fails,
    every method silently no-ops so the crawler main loop is never affected.
    """

    CHANNEL_PREFIX = "crawler:task:"
    HISTORY_SUFFIX = ":history"
    HISTORY_MAX = 500  # keep last 500 messages per task

    def __init__(self, task_id: str):
        self._task_id = task_id or ""
        self._channel = f"{self.CHANNEL_PREFIX}{self._task_id}" if self._task_id else ""
        self._history_key = f"{self._channel}{self.HISTORY_SUFFIX}" if self._channel else ""
        self._redis = self._connect_redis()
        if self._redis is None:
            logging.info("ProgressReporter: Redis unavailable, progress events will be no-op (task=%s)",
                         self._task_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_progress(self, page: int, total_pages: int, new: int, scanned: int) -> None:
        """Report per-page progress. total_pages=0 means unknown."""
        self._publish({
            "type": "progress",
            "page": int(page),
            "total_pages": int(total_pages),
            "new": int(new),
            "scanned": int(scanned),
            "ts": time.time(),
        })

    def publish_log(self, text: str, level: str = "info") -> None:
        """Report a log line. level ∈ {info, warning, error}."""
        if not text:
            return
        self._publish({
            "type": "log",
            "text": str(text),
            "level": level if level in ("info", "warning", "error") else "info",
            "ts": time.time(),
        })

    def publish_done(self, status: str, summary: Optional[Dict[str, Any]] = None) -> None:
        """Report task completion. status ∈ {success, fail, skipped}."""
        self._publish({
            "type": "done",
            "status": status,
            "summary": summary or {},
            "ts": time.time(),
        })

    @property
    def enabled(self) -> bool:
        """True if Redis is connected and channel is configured."""
        return bool(self._redis and self._channel)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _connect_redis():
        """Return a redis client or None on any failure."""
        if REDIS_CONN is None:
            return None
        try:
            client = getattr(REDIS_CONN, "REDIS", None)
            if client is None:
                return None
            # sanity check
            client.ping()
            return client
        except Exception as e:
            logging.warning("ProgressReporter: connect redis failed: %s", e)
            return None

    def _publish(self, msg: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            payload = json.dumps(msg, ensure_ascii=False)
            # 1. Pub/Sub for live push
            self._redis.publish(self._channel, payload)
            # 2. History list for late-joining clients (RPUSH + LTRIM keeps last N)
            pipe = self._redis.pipeline()
            pipe.rpush(self._history_key, payload)
            pipe.ltrim(self._history_key, -self.HISTORY_MAX, -1)
            pipe.expire(self._history_key, 86400)  # 24h TTL
            pipe.execute()
        except Exception as e:
            logging.warning("ProgressReporter publish failed (task=%s): %s", self._task_id, e)

