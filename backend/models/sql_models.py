"""
AgentOS — SQLAlchemy Models
============================
Explicit status enums prevent silent state machine violations.
Indexes on hot query paths (status, created_at) for efficient list/filter queries.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from agent.lifecycle import NodeState, RunState
from backend.db.database import Base


# The lifecycle enums live in agent.lifecycle (single source of truth).
TaskStatus = RunState
NodeStatus = NodeState


class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True)
    idempotency_key = Column(String, unique=True, index=True, nullable=True)
    description = Column(Text, nullable=False)
    status = Column(String, default=RunState.CREATED.value, nullable=False, index=True)
    task_input_json = Column(Text, nullable=True)
    constraints_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested = Column(Boolean, default=False, nullable=False, server_default="0")
    error = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)

    nodes = relationship("TaskNodeRecord", back_populates="task", lazy="select")
    logs = relationship("LogRecord", back_populates="task", lazy="select")
    file_edits = relationship("FileEditRecord", back_populates="task", lazy="select")

    def __repr__(self) -> str:
        return f"<TaskRecord id={self.id!r} status={self.status!r}>"


class TaskNodeRecord(Base):
    __tablename__ = "task_nodes"

    id = Column(String, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    node_id = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, default=NodeState.PENDING.value, nullable=False)
    files_modified = Column(Integer, default=0, nullable=False)
    attempts = Column(Integer, default=0, nullable=False, server_default="0")
    error = Column(Text, nullable=True)
    dependencies_json = Column(Text, nullable=True)

    task = relationship("TaskRecord", back_populates="nodes")

    def __repr__(self) -> str:
        return f"<TaskNodeRecord node_id={self.node_id!r} status={self.status!r}>"


class LogRecord(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    node_id = Column(String, nullable=True, index=True)
    seq_id = Column(Integer, nullable=True, index=True)
    log_type = Column(String, nullable=False)   # action | result | error | stream
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("TaskRecord", back_populates="logs")

    def __repr__(self) -> str:
        return f"<LogRecord task={self.task_id!r} seq={self.seq_id} type={self.log_type!r}>"


class FileEditRecord(Base):
    __tablename__ = "file_edits"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    node_id = Column(String, nullable=True)
    file_path = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("TaskRecord", back_populates="file_edits")

    def __repr__(self) -> str:
        return f"<FileEditRecord path={self.file_path!r}>"


class AgentSelectionLogRecord(Base):
    __tablename__ = "agent_selection_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, index=True, nullable=True)
    input_hash = Column(String, nullable=False)
    selected_agent = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<AgentSelectionLogRecord task={self.task_id!r} agent={self.selected_agent!r}>"
