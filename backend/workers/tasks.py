"""
AgentOS — Celery Worker: Agent Task Execution
==============================================
Single Celery task that orchestrates the full agent pipeline:
plan → DAG graph → constraint-bounded execution → result packaging.

Design principles:
  - Idempotent by task_id (re-entrancy guard on startup)
  - Structured logging throughout (no print())
  - All DB sessions are short-lived context managers
  - TaskResult follows the standard contract
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.database import SessionLocal
from backend.models.sql_models import FileEditRecord, LogRecord, TaskNodeRecord, TaskRecord
from backend.utils.workspace_manager import WorkspaceManager
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_workspace_manager = WorkspaceManager()

# Per-task monotonic log sequence counter (in-process only; each worker is isolated)
_seq_counters: dict[str, int] = {}
_seq_lock = threading.Lock()


# ── Result Schema ────────────────────────────────────────────────────────────────

class TaskResult(BaseModel):
    """Structured output returned to the Celery result backend."""

    summary: str
    steps_executed: int
    files_modified: list[str]
    errors: list[str]
    next_steps: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _next_seq(task_id: str) -> int:
    with _seq_lock:
        _seq_counters[task_id] = _seq_counters.get(task_id, 0) + 1
        return _seq_counters[task_id]


def _publish_state(task_id: str) -> None:
    """Broadcast current task state to Redis pub/sub for WebSocket consumers."""
    try:
        import redis  # lazy import — not available in test stubs

        redis_client = redis.from_url(settings.REDIS_URL)
        with SessionLocal() as db:
            record = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
            if not record:
                return
            nodes = [
                {
                    "node_id": n.node_id,
                    "description": n.description,
                    "status": n.status,
                    "files_modified": n.files_modified,
                }
                for n in record.nodes
            ]
            payload = {
                "type": "state_update",
                "data": {"status": record.status, "nodes": nodes},
            }
            redis_client.publish(f"task_stream:{task_id}", json.dumps(payload))
    except Exception:
        logger.debug("Redis broadcast failed for task %s", task_id, exc_info=True)


def _db_log(task_id: str, node_id: str, log_type: str, content: str) -> None:
    """Persist a log entry and stream it to Redis."""
    with SessionLocal() as db:
        seq = _next_seq(task_id)
        record = LogRecord(
            task_id=task_id,
            node_id=node_id,
            log_type=log_type,
            content=content,
            seq_id=seq,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # Best-effort stream to WebSocket consumers
        try:
            import redis

            redis_client = redis.from_url(settings.REDIS_URL)
            redis_client.publish(
                f"task_stream:{task_id}",
                json.dumps(
                    {
                        "id": record.id,
                        "seq_id": seq,
                        "node_id": node_id,
                        "type": log_type,
                        "content": content,
                        "time": record.created_at.isoformat() if record.created_at else None,
                    }
                ),
            )
        except Exception:
            logger.debug("Redis publish failed for task %s — non-critical", task_id)


def _log_file(task_id: str, node_id: str, file_path: str, content: str) -> None:
    with SessionLocal() as db:
        db.add(FileEditRecord(task_id=task_id, node_id=node_id, file_path=file_path, content=content))
        db.commit()


def _update_node(task_id: str, node_id: str, node_status: str, files_modified: int = 0) -> None:
    with SessionLocal() as db:
        db.query(TaskNodeRecord).filter(
            TaskNodeRecord.task_id == task_id,
            TaskNodeRecord.node_id == node_id,
        ).update({"status": node_status, "files_modified": files_modified})
        db.commit()
    _publish_state(task_id)


def _set_task_status(db: Session, record: TaskRecord, new_status: str) -> None:
    record.status = new_status
    db.commit()
    _publish_state(record.id)


# ── Reset stale tasks from crashed workers ────────────────────────────────────────

@worker_ready.connect  # type: ignore[misc]
def _reset_stale_on_startup(sender: Any, **kwargs: Any) -> None:
    try:
        with SessionLocal() as db:
            stale = db.query(TaskRecord).filter(TaskRecord.status == "RUNNING").all()
            for t in stale:
                t.status = "FAILED"
            db.commit()
            if stale:
                logger.warning("Startup: reset %d stale RUNNING task(s) to FAILED.", len(stale))
    except Exception:
        logger.exception("Failed to reset stale tasks on worker startup.")


# ── Main Task ─────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.run_agent_task",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_agent_task(self: Any, task_id: str, model: str = "Auto") -> dict[str, Any]:
    """
    Full agent execution pipeline for a single task.

    Idempotent: re-entering on a 'COMPLETED' task is a no-op.
    """
    start_ms = int(time.time() * 1000)
    task_workspace = _workspace_manager.create(task_id)
    final_summary = ""
    error_messages: list[str] = []
    orchestrator: Any = None

    db = SessionLocal()
    try:
        task_record = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if not task_record:
            logger.error("Task %s not found in DB — aborting.", task_id)
            return TaskResult(
                summary="Task not found",
                steps_executed=0,
                files_modified=[],
                errors=["Task record missing from database"],
                next_steps=["Check task_id is correct"],
            ).model_dump()

        # Idempotency guard — don't re-run completed tasks
        if task_record.status == "COMPLETED" and self.request.retries == 0:
            logger.info("Task %s already COMPLETED — skipping.", task_id)
            return TaskResult(
                summary="Already completed",
                steps_executed=0,
                files_modified=[],
                errors=[],
                next_steps=[],
            ).model_dump()

        task_record.status = "RUNNING"
        task_record.started_at = datetime.now(timezone.utc)
        db.commit()
        _publish_state(task_id)

        # ── Load constraints ────────────────────────────────────────────────────
        constraints: dict[str, Any] = {}
        if task_record.constraints_json:
            try:
                constraints = json.loads(task_record.constraints_json)
            except json.JSONDecodeError:
                logger.warning("Malformed constraints_json for task %s", task_id)

        user_task = task_record.description

        # ── Planning phase ──────────────────────────────────────────────────────
        _db_log(task_id, "planner", "action", f"[PLANNER] Planning: {user_task[:120]!r} …")

        from agent.selector import plan_markdown_task  # local import avoids circular dep at module load

        task_graph = plan_markdown_task(user_task, model=model, task_id=task_id)

        # Persist planned nodes
        with SessionLocal() as node_db:
            for node in task_graph.nodes.values():
                node_db.add(
                    TaskNodeRecord(
                        id=str(uuid.uuid4()),
                        task_id=task_id,
                        node_id=node.step_id,
                        description=node.description,
                        status="CREATED",
                    )
                )
            node_db.commit()

        _set_task_status(db, task_record, "PLANNED")

        # ── Execution phase ─────────────────────────────────────────────────────
        from agent.planner.executor import DAGOrchestrator

        orchestrator = DAGOrchestrator(
            workspace_dir=task_workspace,
            task_id=task_id,
            max_workers=settings.MAX_WORKERS,
            max_time=constraints.get("max_time", 300),
            max_steps=constraints.get("max_steps", 15),
            risk_level=constraints.get("risk_level", "balanced"),
            file_scope=constraints.get("file_scope", []),
            log_callback=lambda nid, lt, c: _db_log(task_id, nid, lt, c),
            node_callback=lambda nid, ns: _update_node(task_id, nid, ns),
        )

        _set_task_status(db, task_record, "RUNNING")
        steps_run = orchestrator.run_graph(task_graph)

        # Sync final node statuses
        for node in task_graph.nodes.values():
            _update_node(task_id, node.step_id, node.status)

        is_complete = task_graph.is_complete()
        final_summary = (
            f"Executed {steps_run} step(s) across {len(task_graph.nodes)} planned node(s)."
        )
        new_status = "COMPLETED" if is_complete else ("PARTIAL_SUCCESS" if steps_run > 0 else "FAILED")
        task_record.status = new_status
        task_record.completed_at = datetime.now(timezone.utc)
        db.commit()
        _publish_state(task_id)

    except (ConnectionError, TimeoutError, ValueError) as exc:
        attempt = self.request.retries + 1
        backoff = min(2**attempt, 60)
        error_messages.append(f"{type(exc).__name__}: {exc}")
        logger.warning("Task %s attempt %d/%d failed — retrying in %ds.", task_id, attempt, self.max_retries + 1, backoff)

        with SessionLocal() as retry_db:
            retry_db.add(
                LogRecord(
                    task_id=task_id,
                    log_type="error",
                    content=f"Attempt {attempt} failed: {exc}. Retrying in {backoff}s.",
                    seq_id=_next_seq(task_id),
                )
            )
            retry_db.commit()

        db.close()
        raise self.retry(exc=exc, countdown=backoff)

    except SoftTimeLimitExceeded:
        error_messages.append(f"Task exceeded time limit ({settings.TASK_TIMEOUT_SECONDS}s)")
        with SessionLocal() as tdb:
            rec = tdb.query(TaskRecord).filter(TaskRecord.id == task_id).first()
            if rec:
                rec.status = "FAILED"
                rec.completed_at = datetime.now(timezone.utc)
                tdb.add(LogRecord(task_id=task_id, log_type="error", content=error_messages[-1], seq_id=_next_seq(task_id)))
                tdb.commit()
        _workspace_manager.cleanup(task_id)

    except Exception:
        logger.exception("Unhandled error in task %s", task_id)
        error_messages.append("Internal orchestration error — see logs")
        with SessionLocal() as edb:
            rec = edb.query(TaskRecord).filter(TaskRecord.id == task_id).first()
            if rec:
                rec.status = "FAILED"
                rec.completed_at = datetime.now(timezone.utc)
                edb.add(LogRecord(task_id=task_id, log_type="error", content=error_messages[-1], seq_id=_next_seq(task_id)))
                edb.commit()
        _workspace_manager.cleanup(task_id)

    finally:
        db.close()
        with _seq_lock:
            _seq_counters.pop(task_id, None)
        _workspace_manager.archive(task_id)

    # ── Collect modified files from DB ──────────────────────────────────────────
    modified_paths: list[str] = []
    with SessionLocal() as fdb:
        edits = fdb.query(FileEditRecord).filter(FileEditRecord.task_id == task_id).all()
        modified_paths = list({e.file_path for e in edits})

    steps_executed = orchestrator.total_nodes_executed if orchestrator else 0

    return TaskResult(
        summary=final_summary or "Execution did not produce output.",
        steps_executed=steps_executed,
        files_modified=modified_paths,
        errors=error_messages,
        next_steps=(
            ["Review trace logs", "Verify sandboxed outputs"]
            if not error_messages
            else ["Fix root cause", "Retry via POST /retry/{task_id}"]
        ),
    ).model_dump()
