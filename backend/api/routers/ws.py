import asyncio
import hmac
import json
import logging
import os

from typing import Any

import redis.asyncio as redis

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import ConnectionError as RedisConnectionError

from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["Streaming"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Workspace the terminal starts in ─────────────────────────────────────────
TERMINAL_CWD = os.getenv("WORKSPACE_DIR", os.path.expanduser("~"))
TERMINAL_ENV_FLAG = "AGENTOS_ENABLE_WEB_TERMINAL"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# RFC 6455 close code 1008: policy violation.
_POLICY_VIOLATION = 1008


@router.websocket("/logs/{task_id}")
async def stream_logs(websocket: WebSocket, task_id: str, last_seq: int = -1) -> None:
    """
    Streams task logs in real-time via Redis Pub/Sub.

    last_seq: the highest seq_id the client already received.
    Any message with seq_id <= last_seq is silently dropped, preventing
    duplicate log display on WebSocket reconnect.
    """
    await websocket.accept()
    r: Any = redis.from_url(REDIS_URL, decode_responses=True)  # type: ignore[no-untyped-call]
    pubsub = r.pubsub()
    await pubsub.subscribe(f"task_stream:{task_id}")

    # Track the highest seq_id seen in THIS connection session
    seen_seq: int = last_seq

    async def redis_reader() -> None:
        nonlocal seen_seq
        while True:
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            # ── STREAM DEDUPLICATION ──────────────────
                            msg_seq = data.get("seq_id")
                            if msg_seq is not None and msg_seq <= seen_seq:
                                continue  # already delivered — skip
                            if msg_seq is not None:
                                seen_seq = msg_seq
                            # ─────────────────────────────────────────
                            await websocket.send_json(data)
                        except (ValueError, TypeError):
                            continue  # malformed pub/sub payload
            except RedisConnectionError:
                await asyncio.sleep(2)

    async def connection_checker() -> None:
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            pass

    reader_task = asyncio.create_task(redis_reader())
    checker_task = asyncio.create_task(connection_checker())

    _done, pending = await asyncio.wait(
        [reader_task, checker_task], return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()

    await pubsub.unsubscribe(f"task_stream:{task_id}")
    await r.close()


def _allowed_origins() -> set[str]:
    origins = settings.ALLOWED_ORIGINS
    if isinstance(origins, str):
        origins = origins.split(",")
    return {o.strip().rstrip("/") for o in origins if o.strip()}


def terminal_refusal(websocket: WebSocket) -> str | None:
    """
    The web terminal runs arbitrary shell commands on the host as the API's
    user. It is refused unless explicitly enabled, the client is on loopback,
    a browser client comes from an allowed origin, and (when API_KEY is
    configured) the key is presented in the X-API-Key header.
    Returns the refusal reason, or None if the connection is allowed.

    The loopback check alone does not stop a malicious web page: browsers do
    not apply CORS to WebSockets, so any page the user visits can open
    ws://127.0.0.1/ws/terminal and the connection arrives from loopback. A
    browser always sends Origin on a WebSocket handshake, so checking it
    against ALLOWED_ORIGINS closes that path; non-browser clients (CLI tools)
    send no Origin and are still bound by the other checks.

    The key is accepted only as a header. A query parameter would land in
    access logs and browser history.
    """
    if os.getenv(TERMINAL_ENV_FLAG) != "1":
        return f"web terminal is disabled (set {TERMINAL_ENV_FLAG}=1)"
    host = websocket.client.host if websocket.client else ""
    if host not in _LOOPBACK:
        return "web terminal only accepts loopback connections"
    origin = websocket.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in _allowed_origins():
        return f"origin {origin!r} is not in ALLOWED_ORIGINS"
    if settings.API_KEY:
        presented = websocket.headers.get("x-api-key", "")
        if not hmac.compare_digest(presented, settings.API_KEY):
            return "missing or invalid API key"
    return None


@router.websocket("/terminal")
async def terminal(websocket: WebSocket) -> None:
    """
    Interactive terminal WebSocket (development only, disabled by default).
    Client sends: {"cmd": "ls -la"}
    Server streams back lines: {"type": "stdout"|"stderr"|"exit", "data": "..."}
    """
    reason = terminal_refusal(websocket)
    if reason:
        logger.warning("refused /ws/terminal connection: %s", reason)
        await websocket.close(code=_POLICY_VIOLATION, reason=reason)
        return
    logger.warning("web terminal session opened: commands run on the host unsandboxed")
    await websocket.accept()
    cwd = TERMINAL_CWD
    os.makedirs(cwd, exist_ok=True)

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
                raw_cmd = payload.get("cmd", "").strip()
            except (ValueError, AttributeError):
                raw_cmd = msg.strip()

            if not raw_cmd:
                continue

            # Handle `cd` specially — change cwd for subsequent commands
            if raw_cmd.startswith("cd "):
                new_dir = raw_cmd[3:].strip().strip('"').strip("'")
                new_dir = os.path.expanduser(new_dir)
                if not os.path.isabs(new_dir):
                    new_dir = os.path.join(cwd, new_dir)
                new_dir = os.path.normpath(new_dir)
                if os.path.isdir(new_dir):
                    cwd = new_dir
                    await websocket.send_json(
                        {"type": "stdout", "data": f"[cwd] {cwd}\n"}
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "stderr",
                            "data": f"cd: no such directory: {new_dir}\n",
                        }
                    )
                await websocket.send_json({"type": "exit", "data": "0", "cwd": cwd})
                continue

            # Run the command, streaming output line by line
            try:
                proc = await asyncio.create_subprocess_shell(
                    raw_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )

                async def stream_pipe(
                    pipe: asyncio.StreamReader | None, kind: str
                ) -> None:
                    if pipe is None:
                        return
                    async for line in pipe:
                        await websocket.send_json(
                            {"type": kind, "data": line.decode(errors="replace")}
                        )

                await asyncio.gather(
                    stream_pipe(proc.stdout, "stdout"),
                    stream_pipe(proc.stderr, "stderr"),
                )
                await proc.wait()
                await websocket.send_json(
                    {"type": "exit", "data": str(proc.returncode), "cwd": cwd}
                )
            except OSError as e:
                await websocket.send_json({"type": "stderr", "data": f"Error: {e}\n"})
                await websocket.send_json({"type": "exit", "data": "1", "cwd": cwd})

    except WebSocketDisconnect:
        pass
