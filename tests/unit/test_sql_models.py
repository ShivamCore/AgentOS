"""
tests/unit/test_sql_models.py
==============================
Unit tests for backend/models/sql_models.py
"""

from __future__ import annotations

import pytest


class TestTaskStatusEnum:
    def test_all_expected_values_present(self):
        """Every documented lifecycle state must exist in TaskStatus."""
        from backend.models.sql_models import TaskStatus

        expected = {"CREATED", "PLANNED", "RUNNING", "PARTIAL_SUCCESS", "FAILED", "COMPLETED"}
        actual = {s.value for s in TaskStatus}
        missing = expected - actual
        assert not missing, f"Missing TaskStatus values: {missing}"

    def test_status_values_are_uppercase(self):
        """
        Every TaskStatus.value must be uppercase.
        The bug used lowercase 'pending' — this test proves it's gone.
        """
        from backend.models.sql_models import TaskStatus

        for status in TaskStatus:
            assert status.value == status.value.upper(), \
                f"TaskStatus.{status.name}.value={status.value!r} is not uppercase"

    def test_status_is_str_enum(self):
        """TaskStatus must be a str enum so it compares equal to raw strings."""
        from backend.models.sql_models import TaskStatus
        import enum

        assert issubclass(TaskStatus, str), "TaskStatus must be (str, enum.Enum)"
        assert isinstance(TaskStatus.CREATED, str)
        assert TaskStatus.CREATED == "CREATED"


class TestNodeStatusEnum:
    def test_all_expected_node_statuses_present(self):
        """Every node lifecycle state must exist in NodeStatus."""
        from backend.models.sql_models import NodeStatus

        expected = {"CREATED", "RUNNING", "COMPLETED", "FAILED"}
        actual = {s.value for s in NodeStatus}
        missing = expected - actual
        assert not missing, f"Missing NodeStatus values: {missing}"

    def test_node_statuses_are_uppercase(self):
        """NodeStatus values must all be uppercase."""
        from backend.models.sql_models import NodeStatus

        for status in NodeStatus:
            assert status.value == status.value.upper(), \
                f"NodeStatus.{status.name}.value must be uppercase"


class TestTaskRecordModel:
    def test_task_record_has_status_index(self):
        """
        TaskRecord.status must be indexed for efficient status-based filtering.
        Without this index, every backpressure check is a full table scan.
        """
        from backend.models.sql_models import TaskRecord

        indexed_cols = {
            col.name
            for idx in TaskRecord.__table__.indexes
            for col in idx.columns
        }
        assert "status" in indexed_cols or any(
            col.index for col in TaskRecord.__table__.columns if col.name == "status"
        ), "TaskRecord.status must be indexed"

    def test_task_record_has_created_at_index(self):
        """TaskRecord.created_at must be indexed for efficient pagination/ordering."""
        from backend.models.sql_models import TaskRecord

        # Check column-level index or table-level index
        col = TaskRecord.__table__.columns.get("created_at")
        assert col is not None
        table_indexed = any(
            "created_at" in {c.name for c in idx.columns}
            for idx in TaskRecord.__table__.indexes
        )
        col_indexed = col.index if col is not None else False
        assert table_indexed or col_indexed, "TaskRecord.created_at must be indexed"

    def test_task_record_has_repr(self):
        """TaskRecord.__repr__ must be defined for debuggability."""
        from backend.models.sql_models import TaskRecord

        record = TaskRecord(id="test-id", description="test", status="CREATED")
        repr_str = repr(record)
        assert "TaskRecord" in repr_str or "test-id" in repr_str

    def test_idempotency_key_is_nullable(self):
        """idempotency_key must be nullable — not all tasks use idempotency."""
        from backend.models.sql_models import TaskRecord

        col = TaskRecord.__table__.columns["idempotency_key"]
        assert col.nullable is True

    def test_id_is_primary_key(self):
        """TaskRecord.id must be the primary key."""
        from backend.models.sql_models import TaskRecord

        pk_cols = [c.name for c in TaskRecord.__table__.primary_key]
        assert "id" in pk_cols


class TestLogRecordModel:
    def test_log_record_has_seq_id(self):
        """LogRecord must have a seq_id column for ordered log retrieval."""
        from backend.models.sql_models import LogRecord

        assert "seq_id" in LogRecord.__table__.columns

    def test_log_record_has_log_type(self):
        """LogRecord must have a log_type column."""
        from backend.models.sql_models import LogRecord
        assert "log_type" in LogRecord.__table__.columns


class TestAllModelsHaveRepr:
    @pytest.mark.parametrize("model_name", [
        "TaskRecord", "TaskNodeRecord", "LogRecord", "FileEditRecord", "AgentSelectionLogRecord"
    ])
    def test_model_has_repr(self, model_name):
        """Every ORM model must define __repr__ for debuggability."""
        import backend.models.sql_models as models_mod

        model_cls = getattr(models_mod, model_name)
        assert "__repr__" in model_cls.__dict__, \
            f"{model_name} must define a custom __repr__"
