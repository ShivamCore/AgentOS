"""
AgentOS — SQLAlchemy Models
============================
Explicit status enums prevent silent state machine violations.
Indexes on hot query paths (status, created_at) for efficient list/filter queries.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLAlchemy resolves Mapped[] at runtime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from agent.lifecycle import NodeState, RunState
from backend.db.database import Base

# The lifecycle enums live in agent.lifecycle (single source of truth).
TaskStatus = RunState
NodeStatus = NodeState


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String, unique=True, index=True, nullable=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=RunState.CREATED.value, nullable=False, index=True
    )
    task_input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    nodes: Mapped[list[TaskNodeRecord]] = relationship(
        back_populates="task", lazy="select"
    )
    logs: Mapped[list[LogRecord]] = relationship(back_populates="task", lazy="select")
    file_edits: Mapped[list[FileEditRecord]] = relationship(
        back_populates="task", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<TaskRecord id={self.id!r} status={self.status!r}>"


class TaskNodeRecord(Base):
    __tablename__ = "task_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("tasks.id"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default=NodeState.PENDING.value, nullable=False
    )
    files_modified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[TaskRecord] = relationship(back_populates="nodes")

    def __repr__(self) -> str:
        return f"<TaskNodeRecord node_id={self.node_id!r} status={self.status!r}>"


class LogRecord(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("tasks.id"), nullable=False, index=True
    )
    node_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    seq_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # action | result | error | stream
    log_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped[TaskRecord] = relationship(back_populates="logs")

    def __repr__(self) -> str:
        return (
            f"<LogRecord task={self.task_id!r} seq={self.seq_id} "
            f"type={self.log_type!r}>"
        )


class FileEditRecord(Base):
    __tablename__ = "file_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("tasks.id"), nullable=False, index=True
    )
    node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped[TaskRecord] = relationship(back_populates="file_edits")

    def __repr__(self) -> str:
        return f"<FileEditRecord path={self.file_path!r}>"


class AgentSelectionLogRecord(Base):
    __tablename__ = "agent_selection_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    selected_agent: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AgentSelectionLogRecord task={self.task_id!r} "
            f"agent={self.selected_agent!r}>"
        )
