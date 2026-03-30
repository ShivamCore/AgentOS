from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
import asyncio
import redis.asyncio as redis
import os
import shlex

router = APIRouter(prefix="/ws", tags=["Streaming"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Workspace the terminal starts in ─────────────────────────────────────────
TERMINAL_CWD = os.getenv("WORKSPACE_DIR", os.path.expanduser("~"))

@router.websocket("/logs/{task_id}")
async def stream_logs(websocket: WebSocket, task_id: str, last_seq: int = -1):
    """
    Streams task logs in real-time via Redis Pub/Sub.

    last_seq: the highest seq_id the client already received.
    Any message with seq_id <= last_seq is silently dropped, preventing
    duplicate log display on WebSocket reconnect.
    """
    await websocket.accept()
    r = redis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(f"task_stream:{task_id}")

    # Track the highest seq_id seen in THIS connection session
    seen_seq: int = last_seq

    async def redis_reader():
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
                        except Exception:
                            pass
            except redis.exceptions.ConnectionError:
                await asyncio.sleep(2)

    async def connection_checker():
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            pass

    reader_task = asyncio.create_task(redis_reader())
    checker_task = asyncio.create_task(connection_checker())

    done, pending = await asyncio.wait(
        [reader_task, checker_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()

    await pubsub.unsubscribe(f"task_stream:{task_id}")
    await r.close()


@router.websocket("/terminal")
async def terminal(websocket: WebSocket):
    """
    Interactive terminal WebSocket.
    Client sends: {"cmd": "ls -la"}
    Server streams back lines: {"type": "stdout"|"stderr"|"exit", "data": "..."}
    """
    await websocket.accept()
    cwd = TERMINAL_CWD
    os.makedirs(cwd, exist_ok=True)

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
                raw_cmd = payload.get("cmd", "").strip()
            except Exception:
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
                    await websocket.send_json({"type": "stdout", "data": f"[cwd] {cwd}\n"})
                else:
                    await websocket.send_json({"type": "stderr", "data": f"cd: no such directory: {new_dir}\n"})
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

                async def stream_pipe(pipe, kind: str):
                    async for line in pipe:
                        await websocket.send_json({"type": kind, "data": line.decode(errors="replace")})

                await asyncio.gather(
                    stream_pipe(proc.stdout, "stdout"),
                    stream_pipe(proc.stderr, "stderr"),
                )
                await proc.wait()
                await websocket.send_json({"type": "exit", "data": str(proc.returncode), "cwd": cwd})
            except Exception as e:
                await websocket.send_json({"type": "stderr", "data": f"Error: {e}\n"})
                await websocket.send_json({"type": "exit", "data": "1", "cwd": cwd})

    except WebSocketDisconnect:
        pass
