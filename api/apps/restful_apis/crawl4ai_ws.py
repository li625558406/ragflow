"""WebSocket handler for crawler task real-time progress/logs.

Listens on: /api/v1/crawl4ai/tasks/<task_id>/ws

Auth: same JWT token pattern as collaboration_ws.py (query param `?token=` or
Authorization: Bearer header). Token-only check (no per-task ACL — any
logged-in user can observe any task).

Protocol:
  Server → Client messages (JSON):
    {type: "progress", page, total_pages, new, scanned, ts}
    {type: "log", text, level, ts}
    {type: "done", status, summary, ts}
    {type: "history", messages: [...]}   # sent once on connect
    {type: "error", message: str}

  Client → Server messages: ignored (read-only push).

On connect:
  1. Authenticate token
  2. LRANGE crawler:task:{task_id}:history 0 -1  →  send as {type: "history", messages: [...]}
  3. SUBSCRIBE crawler:task:{task_id}  →  forward each message
  4. On disconnect/cancel: close pubsub and websocket
"""
import asyncio
import json
import logging
import threading
from typing import Any, Dict

from quart import websocket
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer

from common import settings
from api.db.services.user_token_service import UserTokenService
from api.db.services import UserService
from common.constants import StatusEnum


_HISTORY_MAX_SEND = 500  # cap history backfill (matches ProgressReporter HISTORY_MAX)


def _get_token_from_request(req) -> str:
    """Extract JWT token from WebSocket query params or Authorization header."""
    token = req.args.get("token")
    if token:
        return str(token)
    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return ""


async def _authenticate(token: str) -> Dict[str, Any]:
    """Verify token; return user dict or raise PermissionError."""
    jwt = Serializer(secret_key=settings.SECRET_KEY)
    try:
        access_token = str(jwt.loads(token))
    except Exception:
        access_token = token

    user_token = UserTokenService.find_by_token(access_token)
    if user_token:
        users = UserService.query(id=user_token.user_id, status=StatusEnum.VALID.value)
    else:
        users = UserService.query(access_token=access_token, status=StatusEnum.VALID.value)

    if not users:
        raise PermissionError("Invalid token")

    user = users[0]
    return {
        "id": user.id,
        "name": getattr(user, "nickname", None) or getattr(user, "email", "") or user.id,
    }


def _get_redis_client():
    """Get redis client; caller must handle None."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
        client = getattr(REDIS_CONN, "REDIS", None)
        if client is not None:
            client.ping()
        return client
    except Exception as e:
        logging.warning("crawl4ai_ws: redis unavailable: %s", e)
        return None


def _channel_for(task_id: str) -> str:
    return f"crawler:task:{task_id}"


def _history_key_for(task_id: str) -> str:
    return f"crawler:task:{task_id}:history"


async def handle_crawler_task_ws(task_id: str):
    """Quart WebSocket handler. Subscribes to Redis pubsub and pushes to client."""
    # ── Auth ─────────────────────────────────────────────────────────
    token = _get_token_from_request(websocket)
    if not token:
        await websocket.accept()
        await websocket.send(json.dumps({"type": "error", "message": "Missing token"}))
        await websocket.close(4001)
        return

    try:
        await _authenticate(token)
    except PermissionError as e:
        await websocket.accept()
        await websocket.send(json.dumps({"type": "error", "message": str(e)}))
        await websocket.close(4003)
        return

    await websocket.accept()

    # ── Redis setup ───────────────────────────────────────────────────
    redis_client = _get_redis_client()
    if redis_client is None:
        await websocket.send(json.dumps({
            "type": "error",
            "message": "Progress streaming unavailable (redis down)",
        }))
        await websocket.close(1011)
        return

    channel = _channel_for(task_id)
    history_key = _history_key_for(task_id)

    # ── Backfill history ──────────────────────────────────────────────
    try:
        raw_history = redis_client.lrange(history_key, 0, _HISTORY_MAX_SEND - 1)
        messages = []
        for raw in raw_history:
            try:
                messages.append(json.loads(raw))
            except Exception:
                continue
        await websocket.send(json.dumps({"type": "history", "messages": messages}))
    except Exception as e:
        logging.warning("crawl4ai_ws: backfill history failed (task=%s): %s", task_id, e)

    # ── Subscribe & forward ───────────────────────────────────────────
    # Run blocking redis pubsub in a thread; await websocket.send in main loop.
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    closed = False

    def _poll_pubsub():
        """Blocking thread: read messages from redis pubsub, push to asyncio queue."""
        nonlocal closed
        try:
            while not closed:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                try:
                    # Use put_nowait via call_soon_threadsafe — drop if queue full
                    # (prevents the helper thread from blocking on a slow client)
                    loop.call_soon_threadsafe(_safe_put, data)
                except Exception:
                    break
        except Exception as e:
            logging.warning("crawl4ai_ws: pubsub poll thread ended (task=%s): %s", task_id, e)
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    def _safe_put(item: str) -> None:
        if closed:
            return
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            logging.warning("crawl4ai_ws: WS queue full, dropping message (task=%s)", task_id)

    poller = threading.Thread(target=_poll_pubsub, daemon=True,
                              name=f"crawl4ai-ws-{task_id[:8]}")
    poller.start()

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Idle ping — detect disconnected client
                try:
                    await websocket.send(json.dumps({"type": "ping"}))
                except Exception:
                    break
                continue
            # If we see a "done" message, forward then close after a short delay
            try:
                await websocket.send(data)
            except Exception:
                break
            # Detect done → end stream
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "done":
                    # Allow client to receive final message; close shortly
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                pass
    finally:
        closed = True
        try:
            pubsub.unsubscribe(channel)
            pubsub.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def register_ws_routes(app):
    """Register WebSocket routes on the Quart app."""
    @app.websocket('/api/v1/crawl4ai/tasks/<task_id>/ws')
    async def crawler_ws_handler(task_id):
        await handle_crawler_task_ws(task_id)
