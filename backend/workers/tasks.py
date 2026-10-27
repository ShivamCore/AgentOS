"""
AgentOS — Celery worker: agent task execution
==============================================
``run_agent_task(task_id)``: claim -> plan -> validate -> schedule/execute ->
persist the final result.

Guarantees and policies (see docs/architecture.md):

* **Duplicate delivery**: a task is claimed with a conditional UPDATE
  (``status='CREATED' AND cancel_requested=0``). A second delivery of the same
  message finds the task no longer CREATED and is a no-op.
* **No Celery-level retries**: LLM calls retry internally with bounded backoff,
  step retries are handled by the scheduler. A failed run is re-queued explicitly
  via ``POST /retry/{task_id}``, which resets it to CREATED.
* **Cancellation**: ``POST /tasks/{id}/cancel`` sets ``cancel_requested``; a
  watcher thread polls it and signals the scheduler.
* **Timeouts**: the run deadline is clamped below Celery's soft time limit so the
  worker can always record a terminal state.
* **Crash recovery**: tasks left RUNNING longer than the hard time limit are
  marked FAILED when a worker starts. Runs are not resumed automatically.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid

from datetime import UTC, datetime, timedelta
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready
from pydantic import BaseModel
from sqlalchemy import update

from agent.lifecycle import NodeState, RunState
from backend.config import settings
from backend.db.database import SessionLocal
from backend.models.sql_models import (
    FileEditRecord,
    LogRecord,
    TaskNodeRecord,
    TaskRecord,
)
from backend.utils.workspace_manager import WorkspaceManager
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_workspace_manager = WorkspaceManager()

# Per-task monotonic log sequence counter (in-process; one run per task at a time).
_seq_counters: dict[str, int] = {}
_seq_lock = threading.Lock()

CANCEL_POLL_SECONDS = 1.0


class TaskResult(BaseModel):
    """Structured output returned to the Celery result backend."""

    summary: str
    steps_executed: int
    files_modified: list[str]
    errors: list[str]
    next_steps: list[str]
    status: str = RunState.FAILED.value
    workspace: str | None = None


# ── Persistence helpers ────────────────────────────────────────────────────────


def _next_seq(task_id: str) -> int:
    with _seq_lock:
        _seq_counters[task_id] = _seq_counters.get(task_id, 0) + 1
        return _seq_counters[task_id]


def _publish(task_id: str, payload: dict[str, Any]) -> None:
    try:
        import redis  # lazy: optional at test time

        redis.from_url(settings.REDIS_URL).publish(  # type: ignore[no-untyped-call]
            f"task_stream:{task_id}", json.dumps(payload, default=str)
        )
    except Exception:  # broadcasting is best-effort; state is already in the DB
        logger.debug("Redis publish failed for task %s", task_id, exc_info=True)


def _publish_state(task_id: str) -> None:
    """Broadcast current task state to Redis pub/sub for WebSocket consumers."""
    try:
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
            status = record.status
    except Exception:
        logger.debug("could not load state for broadcast of %s", task_id, exc_info=True)
        return
    _publish(
        task_id, {"type": "state_update", "data": {"status": status, "nodes": nodes}}
    )


def _db_log(task_id: str, node_id: str | None, log_type: str, content: str) -> None:
    """Persist a log entry (correlated by task_id/node_id/seq_id) and stream it."""
    seq = _next_seq(task_id)
    content = content[:20_000]
    with SessionLocal() as db:
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
        created = record.created_at.isoformat() if record.created_at else None
        rec_id = record.id
    logger.info(
        "task=%s node=%s seq=%s type=%s %s",
        task_id,
        node_id,
        seq,
        log_type,
        content[:200],
    )
    _publish(
        task_id,
        {
            "id": rec_id,
            "seq_id": seq,
            "node_id": node_id,
            "type": log_type,
            "content": content,
            "time": created,
        },
    )


def _log_file(task_id: str, node_id: str, file_path: str, content: str) -> None:
    with SessionLocal() as db:
        db.add(
            FileEditRecord(
                task_id=task_id, node_id=node_id, file_path=file_path, content=content
            )
        )
        db.commit()


def _update_node(task_id: str, node_id: str, node_status: str, **fields: Any) -> None:
    values: dict[Any, Any] = {"status": node_status, **fields}
    with SessionLocal() as db:
        db.query(TaskNodeRecord).filter(
            TaskNodeRecord.task_id == task_id, TaskNodeRecord.node_id == node_id
        ).update(values)
        db.commit()
    _publish_state(task_id)


def _finish(
    task_id: str,
    status: RunState,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    with SessionLocal() as db:
        rec = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if rec is None:
            return
        rec.status = status.value
        rec.completed_at = datetime.now(UTC)
        rec.error = error
        if result is not None:
            rec.result_json = json.dumps(result, default=str)
        db.commit()
    if error:
        _db_log(task_id, None, "error", error)
    _publish_state(task_id)


def _claim(task_id: str) -> tuple[bool, TaskRecord | None]:
    """Atomically move CREATED -> RUNNING. Returns (claimed, record)."""
    with SessionLocal() as db:
        res = db.execute(
            update(TaskRecord)
            .where(
                TaskRecord.id == task_id,
                TaskRecord.status == RunState.CREATED.value,
                TaskRecord.cancel_requested.is_(False),
            )
            .values(
                status=RunState.RUNNING.value,
                started_at=datetime.now(UTC),
                completed_at=None,
                error=None,
                result_json=None,
            )
        )
        db.commit()
        rec = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if rec is not None:
            db.expunge(rec)
        return getattr(res, "rowcount", 0) == 1, rec


def _persist_plan(task_id: str, graph: Any) -> None:
    with SessionLocal() as db:
        db.query(TaskNodeRecord).filter(TaskNodeRecord.task_id == task_id).delete()
        for node in graph.nodes.values():
            db.add(
                TaskNodeRecord(
                    id=str(uuid.uuid4()),
                    task_id=task_id,
                    node_id=node.step_id,
                    description=node.description,
                    status=NodeState.PENDING.value,
                    dependencies_json=json.dumps(node.dependencies),
                )
            )
        db.commit()
    _publish_state(task_id)


def _sync_nodes(task_id: str, graph: Any) -> None:
    for node in graph.nodes.values():
        _update_node(
            task_id,
            node.step_id,
            node.status,
            attempts=node.attempts,
            error=node.error,
            files_modified=len(node.files_written),
        )


def _cancel_watcher(
    task_id: str, cancel: threading.Event, stop: threading.Event
) -> None:
    while not stop.wait(CANCEL_POLL_SECONDS):
        try:
            with SessionLocal() as db:
                flag = (
                    db.query(TaskRecord.cancel_requested)
                    .filter(TaskRecord.id == task_id)
                    .scalar()
                )
        except Exception:
            logger.warning(
                "cancel watcher could not poll task %s", task_id, exc_info=True
            )
            continue
        if flag:
            cancel.set()
            return


# ── Startup recovery ───────────────────────────────────────────────────────────


@worker_ready.connect  # type: ignore[untyped-decorator]
def _reset_stale_on_startup(sender: Any, **kwargs: Any) -> None:
    """Mark tasks stuck in RUNNING beyond the hard time limit as FAILED."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.TASK_TIMEOUT_SECONDS)
    try:
        with SessionLocal() as db:
            stale = (
                db.query(TaskRecord)
                .filter(TaskRecord.status == RunState.RUNNING.value)
                .all()
            )
            reset = 0
            for t in stale:
                started = t.started_at
                if started is not None and started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                if started is None or started < cutoff:
                    t.status = RunState.FAILED.value
                    t.error = (
                        "worker lost while task was running (not resumed; use retry)"
                    )
                    reset += 1
            db.commit()
            if reset:
                logger.warning(
                    "Startup: marked %d stale RUNNING task(s) FAILED.", reset
                )
    except Exception:
        logger.exception("Failed to reset stale tasks on worker startup.")


# ── Main task ──────────────────────────────────────────────────────────────────


def _execute(
    task_id: str, record: TaskRecord, model: str, workspace: str
) -> tuple[RunState, dict[str, Any], str | None]:
    from agent.planner.executor import RISK_TOOL_POLICY, DAGOrchestrator
    from agent.planner.graph import PlanPolicy
    from agent.selector import plan_markdown_task

    constraints: dict[str, Any] = {}
    if record.constraints_json:
        try:
            constraints = json.loads(record.constraints_json)
        except json.JSONDecodeError:
            logger.warning("Malformed constraints_json for task %s", task_id)
    max_steps = int(constraints.get("max_steps", 15))
    risk = constraints.get("risk_level", "balanced")
    policy = PlanPolicy(
        max_nodes=max(max_steps, 1),
        allowed_tools=RISK_TOOL_POLICY.get(risk, RISK_TOOL_POLICY["safe"]),
    )

    _db_log(
        task_id,
        "planner",
        "action",
        f"[PLANNER] Planning: {record.description[:200]!r}",
    )
    graph = plan_markdown_task(
        record.description, model=model, task_id=task_id, policy=policy
    )
    _persist_plan(task_id, graph)
    _db_log(
        task_id,
        "planner",
        "result",
        "plan accepted: "
        + ", ".join(f"{n.step_id}<-{n.dependencies}" for n in graph.nodes.values()),
    )

    # Leave headroom below Celery's soft limit so a terminal state is always recorded.
    run_budget = max(
        10,
        min(int(constraints.get("max_time", 300)), settings.TASK_TIMEOUT_SECONDS - 60),
    )
    orchestrator = DAGOrchestrator(
        workspace_dir=workspace,
        task_id=task_id,
        max_workers=settings.MAX_WORKERS,
        max_retries=settings.STEP_MAX_RETRIES,
        max_time=run_budget,
        max_steps=max_steps,
        node_timeout=min(run_budget, settings.STEP_TIMEOUT_SECONDS),
        risk_level=risk,
        file_scope=constraints.get("file_scope", []),
        model=model,
        user_task=record.description,
        log_callback=lambda nid, lt, c: _db_log(task_id, nid, lt, c),
        node_callback=lambda nid, ns: _update_node(task_id, nid, ns),
        file_callback=lambda nid, path, content: _log_file(task_id, nid, path, content),
    )
    cancel, stop = threading.Event(), threading.Event()
    watcher = threading.Thread(
        target=_cancel_watcher, args=(task_id, cancel, stop), daemon=True
    )
    watcher.start()
    try:
        report = orchestrator.run_graph(graph, cancel_event=cancel)
    finally:
        stop.set()
    _sync_nodes(task_id, graph)

    from agent.planner.scheduler import summarize_report

    summary = summarize_report(report)
    error = None
    if report.state is not RunState.COMPLETED:
        error = (
            "; ".join(f"{k}: {v}" for k, v in report.errors.items())[:4000]
            or report.state.value
        )
    return report.state, summary, error


@celery_app.task(  # type: ignore[untyped-decorator]
    name="backend.workers.tasks.run_agent_task",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_agent_task(self: Any, task_id: str, model: str = "Auto") -> dict[str, Any]:
    """Execute one task end to end. Idempotent with respect to duplicate deliveries."""
    from agent.llm import LLMError
    from agent.planner.graph import PlanValidationError
    from agent.selector import PlannerError

    claimed, record = _claim(task_id)
    if record is None:
        logger.error("Task %s not found in DB — aborting.", task_id)
        return TaskResult(
            summary="Task not found",
            steps_executed=0,
            files_modified=[],
            errors=["Task record missing from database"],
            next_steps=[],
        ).model_dump()
    if not claimed:
        if record.status == RunState.CREATED.value and record.cancel_requested:
            _finish(task_id, RunState.CANCELLED, "cancelled before execution started")
            record.status = RunState.CANCELLED.value
        logger.info(
            "Task %s not claimed (status=%s) — duplicate or cancelled delivery.",
            task_id,
            record.status,
        )
        return TaskResult(
            summary=f"Not executed: task is {record.status}",
            steps_executed=0,
            files_modified=[],
            errors=[],
            next_steps=[],
            status=record.status,
        ).model_dump()

    _publish_state(task_id)
    workspace = _workspace_manager.create(task_id)
    state: RunState = RunState.FAILED
    summary: dict[str, Any] = {}
    error: str | None = None
    db = SessionLocal()
    try:
        state, summary, error = _execute(task_id, record, model, workspace)
    except (PlannerError, PlanValidationError, LLMError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except SoftTimeLimitExceeded:
        state, error = (
            RunState.TIMED_OUT,
            f"task exceeded time limit ({settings.TASK_TIMEOUT_SECONDS}s)",
        )
    except Exception as exc:
        logger.exception("Unhandled error in task %s", task_id)
        error = f"internal error: {type(exc).__name__}: {exc}"
    finally:
        db.close()
        _finish(task_id, state, error, summary or None)
        with _seq_lock:
            _seq_counters.pop(task_id, None)
        if settings.ARCHIVE_WORKSPACES:
            _workspace_manager.archive(task_id)

    with SessionLocal() as fdb:
        files = sorted(
            {
                e.file_path
                for e in fdb.query(FileEditRecord).filter(
                    FileEditRecord.task_id == task_id
                )
            }
        )
    return TaskResult(
        summary=(
            f"{state.value}: {summary.get('node_states', {})}"
            if summary
            else (error or state.value)
        ),
        steps_executed=int(summary.get("nodes_started", 0)) if summary else 0,
        files_modified=files,
        errors=[error] if error else [],
        next_steps=(
            []
            if state is RunState.COMPLETED
            else [f"Inspect logs, then POST /retry/{task_id}"]
        ),
        status=state.value,
        workspace=None if settings.ARCHIVE_WORKSPACES else workspace,
    ).model_dump()
