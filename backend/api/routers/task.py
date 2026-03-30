"""
AgentOS — Task API Router
==========================
All task lifecycle endpoints: create, read, list, retry, steps, explain.
Business logic is intentionally kept inside service functions — route handlers
are thin dispatchers only.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.rate_limiter import RateLimiter
from backend.config import settings
from backend.db.database import get_db
from backend.models.sql_models import (
    AgentSelectionLogRecord,
    FileEditRecord,
    LogRecord,
    TaskNodeRecord,
    TaskRecord,
)
from backend.workers.tasks import run_agent_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Tasks"])
_limiter = RateLimiter()



class ConstraintInput(BaseModel):
    max_time: int = Field(default=300, ge=10, le=3600, description="Maximum wall-clock seconds for this task.")
    max_steps: int = Field(default=15, ge=1, le=50, description="Maximum DAG nodes to execute.")
    risk_level: str = Field(default="balanced", pattern="^(safe|balanced|aggressive)$")
    file_scope: list[str] = Field(default_factory=list, description="Restrict file writes to these directories.")


class TaskInput(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=3, max_length=8_000)
    task_type: str = Field(..., pattern="^(build_app|fix_bug|refactor_code|create_api)$")
    tech_stack: list[str] = Field(default_factory=list, max_length=20)
    features: list[str] = Field(default_factory=list, max_length=30)
    constraints: ConstraintInput = Field(default_factory=ConstraintInput)
    model: str = Field(default="Auto")
    idempotency_key: str | None = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    deduplicated: bool = False


class TaskResult(BaseModel):
    task_id: str
    status: str
    summary: str
    files_modified: list[str]
    errors: list[str]
    next_steps: list[str]


class TaskDetail(BaseModel):
    id: str
    title: str | None
    description: str
    status: str
    created_at: Any
    nodes: list[dict[str, Any]]


class StepDetail(BaseModel):
    step_id: str
    description: str
    status: str
    execution_logs: list[dict[str, Any]]


class ExplainResponse(BaseModel):
    planner_reasoning: list[str]
    agent_selection: list[dict[str, Any]]
    tool_usage_decisions: list[dict[str, Any]]


# ── Service Helpers ─────────────────────────────────────────────────────────────

def _build_planner_prompt(task: TaskInput) -> str:
    """Deterministic, structured prompt from a TaskInput — no raw user string passed."""
    parts = [
        f"TITLE: {task.title}",
        f"TYPE: {task.task_type}",
    ]
    if task.tech_stack:
        parts.append(f"TECH STACK: {', '.join(task.tech_stack)}")
    if task.features:
        parts.append(f"FEATURES: {', '.join(task.features)}")
    parts.append(f"\nGOAL:\n{task.description}")
    if task.constraints.file_scope:
        parts.append(f"FILE SCOPE: {', '.join(task.constraints.file_scope)}")
    return "\n".join(parts)


def _check_backpressure(db: Session) -> None:
    active = (
        db.query(TaskRecord)
        .filter(TaskRecord.status.in_(["CREATED", "PLANNED", "RUNNING"]))
        .count()
    )
    if active >= settings.MAX_CONCURRENT_TASKS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Queue saturated: {active} active tasks. Retry shortly.",
        )


# ── Endpoints ───────────────────────────────────────────────────────────────────

@router.post(
    "/tasks/create",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create and dispatch a new agent task",
)
def create_task(
    request: Request,
    task: TaskInput,
    db: Session = Depends(get_db),
) -> TaskResponse:
    _limiter.check(request)

    # Idempotency: return existing task if key already used
    if task.idempotency_key:
        existing = (
            db.query(TaskRecord)
            .filter(TaskRecord.idempotency_key == task.idempotency_key)
            .first()
        )
        if existing:
            return TaskResponse(task_id=existing.id, status=existing.status, deduplicated=True)

    _check_backpressure(db)

    task_id = str(uuid.uuid4())
    planner_prompt = _build_planner_prompt(task)

    # Extract a clean title from the structured input
    task_title = task.title

    record = TaskRecord(
        id=task_id,
        description=planner_prompt,
        status="CREATED",
        idempotency_key=task.idempotency_key,
        task_input_json=task.model_dump_json(),
        constraints_json=task.constraints.model_dump_json(),
    )
    db.add(record)
    db.commit()

    run_agent_task.apply_async(
        args=[task_id, task.model],
        expires=settings.TASK_TIMEOUT_SECONDS * 2,
    )

    logger.info("Task %s created (type=%s)", task_id, task.task_type)
    return TaskResponse(task_id=task_id, status="CREATED")


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetail,
    summary="Get full task details including node statuses",
)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskDetail:
    task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    nodes = db.query(TaskNodeRecord).filter(TaskNodeRecord.task_id == task_id).all()

    # Attempt to recover the original title from stored JSON
    title: str | None = None
    if task.task_input_json:
        try:
            title = json.loads(task.task_input_json).get("title")
        except (json.JSONDecodeError, AttributeError):
            pass

    return TaskDetail(
        id=task.id,
        title=title,
        description=task.description,
        status=task.status,
        created_at=task.created_at,
        nodes=[
            {
                "node_id": n.node_id,
                "description": n.description,
                "status": n.status,
                "files_modified": n.files_modified,
            }
            for n in nodes
        ],
    )


@router.get(
    "/tasks/{task_id}/steps",
    response_model=list[StepDetail],
    summary="Get per-step execution trace for a task",
)
def get_task_steps(task_id: str, db: Session = Depends(get_db)) -> list[StepDetail]:
    nodes = db.query(TaskNodeRecord).filter(TaskNodeRecord.task_id == task_id).all()
    if not nodes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No steps found for task")

    results: list[StepDetail] = []
    for node in nodes:
        logs = (
            db.query(LogRecord)
            .filter(LogRecord.node_id == node.node_id, LogRecord.task_id == task_id)
            .order_by(LogRecord.seq_id.asc())
            .all()
        )
        results.append(
            StepDetail(
                step_id=node.node_id,
                description=node.description,
                status=node.status,
                execution_logs=[
                    {"type": l.log_type, "content": l.content, "time": l.created_at}
                    for l in logs
                ],
            )
        )
    return results


@router.get(
    "/tasks/{task_id}/explain",
    response_model=ExplainResponse,
    summary="Explain planner, agent, and tool decisions for a task",
)
def explain_task(task_id: str, db: Session = Depends(get_db)) -> ExplainResponse:
    planner_logs = (
        db.query(LogRecord)
        .filter(LogRecord.task_id == task_id, LogRecord.log_type.in_(["action", "result"]))
        .order_by(LogRecord.seq_id.asc())
        .limit(5)
        .all()
    )
    selection_logs = (
        db.query(AgentSelectionLogRecord)
        .filter(AgentSelectionLogRecord.task_id == task_id)
        .all()
    )
    nodes = db.query(TaskNodeRecord).filter(TaskNodeRecord.task_id == task_id).all()

    return ExplainResponse(
        planner_reasoning=[l.content for l in planner_logs],
        agent_selection=[
            {"agent": s.selected_agent, "confidence": s.confidence, "reason": s.reason}
            for s in selection_logs
        ],
        tool_usage_decisions=[{"node": n.node_id, "goal": n.description} for n in nodes],
    )


@router.get(
    "/tasks",
    response_model=list[TaskDetail],
    summary="List the 50 most recent tasks",
)
def list_tasks(db: Session = Depends(get_db)) -> list[TaskDetail]:
    tasks = db.query(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(50).all()
    return [
        TaskDetail(
            id=t.id,
            title=None,
            description=t.description,
            status=t.status,
            created_at=t.created_at,
            nodes=[],
        )
        for t in tasks
    ]


@router.get(
    "/task/{task_id}/files",
    summary="List files generated or modified by a task",
)
def get_task_files(task_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    edits = db.query(FileEditRecord).filter(FileEditRecord.task_id == task_id).all()
    return [
        {
            "id": e.id,
            "node_id": e.node_id,
            "file_path": e.file_path,
            "content": e.content,
            "created_at": e.created_at,
        }
        for e in edits
    ]


@router.get(
    "/logs/{task_id}",
    summary="Stream all ordered logs for a task",
)
def get_logs(task_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    logs = (
        db.query(LogRecord)
        .filter(LogRecord.task_id == task_id)
        .order_by(LogRecord.seq_id.asc().nullslast(), LogRecord.id.asc())
        .all()
    )
    return [
        {
            "id": l.id,
            "seq_id": l.seq_id,
            "node_id": l.node_id,
            "type": l.log_type,
            "content": l.content,
            "time": l.created_at,
        }
        for l in logs
    ]


@router.post(
    "/retry/{task_id}",
    response_model=TaskResponse,
    status_code=202,
    summary="Re-queue a failed task",
)
def retry_task(task_id: str, db: Session = Depends(get_db)) -> TaskResponse:
    task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status not in ("FAILED",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task is '{task.status}'. Only FAILED tasks can be retried.",
        )

    task.status = "CREATED"
    task.started_at = None
    task.completed_at = None
    db.commit()

    run_agent_task.apply_async(
        args=[task_id, "Auto"],
        expires=settings.TASK_TIMEOUT_SECONDS * 2,
    )
    return TaskResponse(task_id=task_id, status="CREATED")


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResult,
    summary="Structured result summary for a completed task",
)
def get_task_result(task_id: str, db: Session = Depends(get_db)) -> TaskResult:
    """
    Returns a structured, CLI-friendly summary derived from task records.

    - summary: human-readable outcome sentence
    - files_modified: all file paths written during execution
    - errors: error log entries (if any)
    - next_steps: suggested follow-up actions based on outcome
    """
    task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # ── Files modified ────────────────────────────────────────────────────────
    edits = db.query(FileEditRecord).filter(FileEditRecord.task_id == task_id).all()
    files_modified = sorted({e.file_path for e in edits if e.file_path})

    # ── Errors ────────────────────────────────────────────────────────────────
    error_logs = (
        db.query(LogRecord)
        .filter(LogRecord.task_id == task_id, LogRecord.log_type == "error")
        .order_by(LogRecord.seq_id.asc().nullslast())
        .limit(20)
        .all()
    )
    errors = [l.content for l in error_logs if l.content]

    # ── Node summary ──────────────────────────────────────────────────────────
    nodes = db.query(TaskNodeRecord).filter(TaskNodeRecord.task_id == task_id).all()
    total = len(nodes)
    completed = sum(1 for n in nodes if n.status == "COMPLETED")
    failed = sum(1 for n in nodes if n.status == "FAILED")

    # ── Human-readable summary ────────────────────────────────────────────────
    if task.status == "COMPLETED":
        summary = (
            f"Task completed successfully. "
            f"{completed}/{total} steps done, "
            f"{len(files_modified)} file(s) modified."
        )
        next_steps = [
            "Review generated files in your workspace directory.",
            "Run tests to validate the output.",
            f"Use `agentos status {task_id} --logs` for the full execution trace.",
        ]
    elif task.status == "PARTIAL_SUCCESS":
        summary = (
            f"Task partially completed. "
            f"{completed}/{total} steps succeeded, {failed} failed."
        )
        next_steps = [
            f"Retry the task: `agentos retry {task_id}`",
            "Check errors above for what went wrong.",
        ]
    elif task.status == "FAILED":
        summary = f"Task failed after {completed}/{total} steps."
        next_steps = [
            f"Retry: `agentos retry {task_id}`",
            "Check errors above for root cause.",
            "Try a different model: `agentos run ... --model deepseek-coder:6.7b`",
        ]
    elif task.status == "RUNNING":
        summary = f"Task is still running ({completed}/{total} steps done so far)."
        next_steps = [f"Poll with: `agentos status {task_id}`"]
    else:
        summary = f"Task status: {task.status}"
        next_steps = []

    return TaskResult(
        task_id=task_id,
        status=task.status,
        summary=summary,
        files_modified=files_modified,
        errors=errors[:10],  # cap at 10 to keep response lean
        next_steps=next_steps,
    )
