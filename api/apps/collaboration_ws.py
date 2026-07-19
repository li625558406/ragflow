"""
Real-time collaboration WebSocket relay server.

From first principles: the server is a dumb pipe.
It authenticates, relays messages between clients in the same room,
buffers updates for late joiners, and persists state when the room empties.

No y-py dependency — all Yjs logic lives on the client.
"""
import asyncio
import base64
import json
import logging
import uuid
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer

from common import settings
from api.apps.services.collaboration_api_service import (
    _get_user_role,
    ROLE_HIERARCHY,
    CollaborationDocumentService,
)
from api.db.services.user_token_service import UserTokenService
from api.db.services import UserService
from common.constants import StatusEnum


# ── Room state ──────────────────────────────────────────────────────────
# { doc_id: { "clients": { client_id: { "ws": WebSocket, "user_id": str, "user_name": str } },
#             "buffer": [bytes, ...],   # updates since last full save
#             "full_state": bytes|None } }
_rooms: dict[str, dict] = {}


async def _authenticate(token: str, doc_id: str) -> tuple:
    """Verify JWT and document access. Returns (user_dict, role, read_only) or raises."""
    jwt = Serializer(secret_key=settings.SECRET_KEY)
    try:
        access_token = str(jwt.loads(token))
    except Exception:
        # Try raw token fallback
        access_token = token

    # Look up user via UserToken table
    user_token = UserTokenService.find_by_token(access_token)
    if user_token:
        users = UserService.query(id=user_token.user_id, status=StatusEnum.VALID.value)
    else:
        # Fallback: legacy access_token
        users = UserService.query(access_token=access_token, status=StatusEnum.VALID.value)

    if not users:
        raise PermissionError("Invalid token")

    user = users[0]

    # Check document access
    role = _get_user_role(doc_id, user.id)
    if role is None:
        raise PermissionError("Access denied to this document")

    can_edit = ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY.get("editor", 3)

    return {
        "id": user.id,
        "name": getattr(user, "nickname", None) or getattr(user, "email", "") or user.id,
    }, role, not can_edit


def _make_client_id(user_id: str, ws_uid: str) -> str:
    """Build a room-unique client_id.

    Uses a per-connection UUID rather than id(websocket) — Python's id() is the
    object's memory address, which CPython can reuse immediately after GC. Under
    React StrictMode double-mount, ws1 is GC'd before ws2 is created, so both
    can map to the same client_id, causing the second to overwrite the first in
    the room map and corrupting presence tracking.
    """
    return f"{user_id}:{ws_uid}"


async def _load_full_state(doc_id: str) -> bytes | None:
    """Load the persisted Yjs binary state from MySQL."""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if e and doc.ydoc:
        return bytes(doc.ydoc)
    return None


async def _persist_full_state(doc_id: str, data: bytes):
    """Save the Yjs binary state to MySQL."""
    try:
        CollaborationDocumentService.update_by_id(doc_id, {"ydoc": data})
    except Exception as e:
        logging.error(f"Failed to persist ydoc for {doc_id}: {e}")


async def _broadcast(doc_id: str, message: str, exclude_client_id: str | None = None):
    """Send a message to all clients in a room except the excluded one."""
    room = _rooms.get(doc_id)
    if not room:
        return
    for cid, client in list(room["clients"].items()):
        if cid == exclude_client_id:
            continue
        try:
            await client["ws"].send(message)
            logging.info(f"[WS] broadcast msg_type={json.loads(message).get('t', '?')} to {cid}")
        except Exception:
            # Remove dead connection
            room["clients"].pop(cid, None)


async def _build_presence(doc_id: str) -> list[dict]:
    """Build the presence list for a room."""
    room = _rooms.get(doc_id)
    if not room:
        return []
    return [
        {"uid": c["user_id"], "name": c["user_name"]}
        for c in room["clients"].values()
    ]


async def _broadcast_presence(doc_id: str):
    """Notify all clients of the current member list."""
    presence = await _build_presence(doc_id)
    msg = json.dumps({"t": "presence", "d": presence})
    await _broadcast(doc_id, msg)


def _get_token_from_request(req) -> str | None:
    """Extract JWT token from WebSocket query params or headers."""
    token = req.args.get("token")
    if token:
        return token
    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return None


async def handle_ws(doc_id: str):
    """Quart WebSocket handler for /api/v1/collaboration/ws/<doc_id>"""
    from quart import websocket

    # ── Auth ─────────────────────────────────────────────────────────
    # In Quart, use websocket (not request) to access args/headers in WS context
    token = _get_token_from_request(websocket)
    if not token:
        await websocket.accept()
        await websocket.send(json.dumps({"t": "error", "d": "Missing token"}))
        await websocket.close(4001)
        return

    try:
        user, role, read_only = await _authenticate(token, doc_id)
    except PermissionError as e:
        await websocket.accept()
        await websocket.send(json.dumps({"t": "error", "d": str(e)}))
        await websocket.close(4003)
        return

    await websocket.accept()

    # Generate a fresh UUID per WebSocket connection — NEVER reuse id(websocket)
    # because CPython may recycle the memory address after GC, causing
    # StrictMode double-mount connections to collide on client_id.
    ws_uid = uuid.uuid4().hex
    client_id = _make_client_id(user["id"], ws_uid)
    logging.info(f"[WS] {user['name']} (role={role}, ro={read_only}) connected to doc {doc_id}")

    # ── Join room ────────────────────────────────────────────────────
    # IMPORTANT: websocket is a werkzeug LocalProxy that resolves to the
    # currently active websocket context. Storing the proxy directly would
    # cause _broadcast to send to the SENDER's websocket (since the proxy
    # resolves at call time, not at store time). We must store the
    # resolved actual WebSocket object.
    actual_ws = websocket._get_current_object()

    if doc_id not in _rooms:
        full_state = await _load_full_state(doc_id)
        _rooms[doc_id] = {"clients": {}, "buffer": [], "full_state": full_state}
    room = _rooms[doc_id]
    room["clients"][client_id] = {
        "ws": actual_ws,
        "user_id": user["id"],
        "user_name": user["name"],
        "aw_client_id": None,
        "last_aw": None,  # last raw aw message — replayed to future joiners
    }

    # ── Send initial state ───────────────────────────────────────────
    init_msg = {
        "t": "init",
        "d": base64.b64encode(room["full_state"]).decode("ascii") if room["full_state"] else None,
        "ro": read_only,
    }
    await websocket.send(json.dumps(init_msg))

    # Replay buffered updates for late joiners
    for update in room["buffer"]:
        await websocket.send(json.dumps({"t": "update", "d": base64.b64encode(update).decode("ascii")}))

    await _broadcast_presence(doc_id)

    # Replay existing clients' awareness so the newcomer sees them immediately.
    # Without this, B would only learn about A when A's next 5s heartbeat fires.
    for other_cid, other_client in list(room["clients"].items()):
        if other_cid == client_id:
            continue
        last_aw = other_client.get("last_aw")
        if last_aw:
            try:
                await websocket.send(last_aw)
                logging.info(f"[WS] replay aw from {other_cid} to newcomer {client_id}")
            except Exception:
                pass

    # ── Message loop ─────────────────────────────────────────────────
    try:
        while True:
            raw = await websocket.receive()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("t")
            msg_data = msg.get("d")

            if msg_type == "update":
                if read_only:
                    continue  # Drop edits from read-only users

                # Relay incremental deltas — do NOT overwrite full_state
                # full_state is only updated via 'save' messages (full snapshots)
                try:
                    update_bytes = base64.b64decode(msg_data)
                    room["buffer"].append(update_bytes)
                except Exception:
                    continue

                # Broadcast to all other clients
                await _broadcast(doc_id, raw, exclude_client_id=client_id)

            elif msg_type == "aw":
                # Awareness — track the Yjs clientID for cleanup on disconnect
                try:
                    aw_data = json.loads(msg_data) if isinstance(msg_data, str) else msg_data
                    if isinstance(aw_data, dict) and "clientID" in aw_data:
                        room["clients"][client_id]["aw_client_id"] = aw_data["clientID"]
                        logging.info(f"[WS] aw from {user['name']} clientID={aw_data['clientID']}, room has {len(room['clients'])} clients")
                except Exception:
                    pass
                # Cache raw message so future joiners can be replayed it
                if client_id in room["clients"]:
                    room["clients"][client_id]["last_aw"] = raw
                # Broadcast to all others
                await _broadcast(doc_id, raw, exclude_client_id=client_id)

            elif msg_type == "save":
                # Client confirms a full-state persistence — clear the buffer
                if msg_data:
                    try:
                        save_bytes = base64.b64decode(msg_data)
                        room["full_state"] = save_bytes
                        await _persist_full_state(doc_id, save_bytes)
                        room["buffer"].clear()
                        # Broadcast save notification to other clients
                        save_notify = json.dumps({"t": "saved", "d": {"name": user["name"]}})
                        await _broadcast(doc_id, save_notify, exclude_client_id=client_id)
                    except Exception:
                        pass

            elif msg_type == "comment-changed":
                # Comment mutation happened on another client — relay to others
                # so they reload via REST. No state to persist/buffer here.
                await _broadcast(doc_id, raw, exclude_client_id=client_id)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"[WS] Error for doc {doc_id}, user {user['name']}: {e}")
    finally:
        # ── Leave room ───────────────────────────────────────────
        client_info = room["clients"].pop(client_id, None)
        if not room["clients"]:
            # Last client left — persist and clean up
            if room["full_state"]:
                await _persist_full_state(doc_id, room["full_state"])
            _rooms.pop(doc_id, None)
            logging.info(f"[WS] Room {doc_id} closed (no clients)")
        else:
            await _broadcast_presence(doc_id)
            # Notify remaining clients to remove this user's awareness entry
            if client_info and client_info.get("aw_client_id") is not None:
                remove_msg = json.dumps({"t": "aw-remove", "d": client_info["aw_client_id"]})
                await _broadcast(doc_id, remove_msg)

        logging.info(f"[WS] {user['name']} disconnected from doc {doc_id}")


def register_ws_routes(app):
    """Register WebSocket route on the Quart app."""

    @app.websocket('/api/v1/collaboration/ws/<doc_id>')
    async def ws_handler(doc_id):
        await handle_ws(doc_id)
