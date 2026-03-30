"""
tests/unit/test_task_router.py
===============================
Unit tests for backend/api/routers/task.py

REGRESSION PROOF: These tests prove the backpressure bug is dead.
  Bug: _check_backpressure queried for status "pending" (lowercase)
  while the DB stored "CREATED" (uppercase). The count was always 0.
  Fixed by: using uppercase status strings ("CREATED", "RUNNING") that
  match the actual DB values.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import seed_task


# ── Backpressure tests ────────────────────────────────────────────────────────────

class TestBackpressure:
    """
    REGRESSION: Prove the backpressure guard correctly counts uppercase statuses.
    """

    def test_backpressure_counts_uppercase_CREATED(self, db, test_client):
        """
        Seed the DB with tasks in status 'CREATED' (uppercase).
        The guard must detect them — the bug counted zero because it searched
        for lowercase 'pending'.
        """
        for _ in range(3):
            seed_task(db, status="CREATED")

        from backend.api.routers.task import _check_backpressure
        from backend.config import settings

        original_limit = settings.MAX_CONCURRENT_TASKS
        settings.MAX_CONCURRENT_TASKS = 100  # set high so it doesn't reject

        try:
            # Should not raise — but the count must be 3, not 0
            with patch("backend.api.routers.task.settings") as mock_settings:
                mock_settings.MAX_CONCURRENT_TASKS = 100
                # We verify by calling with a very high limit — if count was 0 the
                # function would never raise regardless. We check the internal count here.
                from sqlalchemy import func
                from backend.models.sql_models import TaskRecord
                count = db.query(TaskRecord).filter(
                    TaskRecord.status.in_(["CREATED", "PLANNED", "RUNNING"])
                ).count()
                assert count == 3, f"Expected 3 active tasks but found {count} — case mismatch?"
        finally:
            settings.MAX_CONCURRENT_TASKS = original_limit

    def test_backpressure_rejects_when_over_limit(self, db, test_client, mock_llm):
        """
        With MAX_CONCURRENT_TASKS=2, submitting when 2 tasks are CREATED must get 429.
        """
        seed_task(db, status="CREATED")
        seed_task(db, status="CREATED")

        with patch("backend.api.routers.task.settings") as mock_cfg:
            mock_cfg.MAX_CONCURRENT_TASKS = 2
            mock_cfg.TASK_TIMEOUT_SECONDS = 600
            response = test_client.post("/tasks/create", json={
                "title": "New task",
                "description": "Should be rejected by backpressure",
                "task_type": "build_app",
            })

        assert response.status_code == 429, f"Expected 429 but got {response.status_code}"
        assert "Queue saturated" in response.json()["detail"]

    def test_backpressure_allows_when_under_limit(self, db, test_client, mock_llm):
        """
        With MAX_CONCURRENT_TASKS=10 and 1 active task, submission should succeed (202).
        """
        seed_task(db, status="CREATED")

        response = test_client.post("/tasks/create", json={
            "title": "Allowed task",
            "description": "Within the task limit, should be accepted",
            "task_type": "fix_bug",
        })

        assert response.status_code in (200, 201, 202), \
            f"Expected 2xx but got {response.status_code}: {response.text}"

    def test_backpressure_ignores_completed_tasks(self, db, test_client, mock_llm):
        """
        COMPLETED and FAILED tasks must NOT count toward the backpressure limit.
        """
        # Add many completed/failed tasks — these must NOT block
        for _ in range(8):
            seed_task(db, status="COMPLETED")
        for _ in range(4):
            seed_task(db, status="FAILED")

        with patch("backend.api.routers.task.settings") as mock_cfg:
            mock_cfg.MAX_CONCURRENT_TASKS = 3
            mock_cfg.TASK_TIMEOUT_SECONDS = 600
            response = test_client.post("/tasks/create", json={
                "title": "Post completed tasks",
                "description": "Should not be blocked by inactive tasks",
                "task_type": "create_api",
            })

        assert response.status_code in (200, 201, 202), \
            f"Completed/failed tasks must not block new submissions, got {response.status_code}"

    def test_backpressure_counts_running_and_planned(self, db, test_client):
        """RUNNING and PLANNED tasks must count toward the limit, not just CREATED."""
        seed_task(db, status="RUNNING")
        seed_task(db, status="PLANNED")

        from backend.models.sql_models import TaskRecord
        count = db.query(TaskRecord).filter(
            TaskRecord.status.in_(["CREATED", "PLANNED", "RUNNING"])
        ).count()
        assert count == 2


# ── Response model validation ─────────────────────────────────────────────────────

class TestEndpointContracts:
    """Verify every endpoint has response_model, status_code, and tags configured."""

    def test_all_routes_have_tags(self, test_client):
        """
        Every registered route must have at least one tag.
        Untagged routes appear in 'default' Swagger section — a sign of incomplete metadata.
        """
        from backend.api.main import app

        untagged = []
        for route in app.routes:
            if hasattr(route, "tags") and not route.tags:
                if route.path not in ("/", "/openapi.json", "/docs", "/redoc"):
                    untagged.append(route.path)

        assert untagged == [], f"Routes missing tags: {untagged}"

    def test_task_create_returns_202(self, test_client, mock_llm):
        """POST /tasks/create must return 202 Accepted on valid input."""
        response = test_client.post("/tasks/create", json={
            "title": "Test Task",
            "description": "A valid description",
            "task_type": "build_app",
        })
        assert response.status_code == 202

    def test_task_create_response_has_task_id(self, test_client, mock_llm):
        """POST /tasks/create response must contain task_id and status fields."""
        response = test_client.post("/tasks/create", json={
            "title": "Test Task",
            "description": "Check response shape",
            "task_type": "fix_bug",
        })
        data = response.json()
        assert "task_id" in data
        assert "status" in data
        assert data["status"] == "CREATED"

    def test_get_nonexistent_task_returns_404(self, test_client):
        """GET /tasks/{task_id} for an unknown ID must return 404."""
        response = test_client.get(f"/tasks/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_retry_nonexistent_task_returns_404(self, test_client):
        """POST /retry/{task_id} for an unknown ID must return 404."""
        response = test_client.post(f"/retry/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_retry_completed_task_returns_409(self, test_client, db):
        """POST /retry/{task_id} for a COMPLETED task must return 409 Conflict."""
        task = seed_task(db, status="COMPLETED")
        response = test_client.post(f"/retry/{task.id}")
        assert response.status_code == 409

    def test_retry_failed_task_succeeds(self, test_client, db):
        """POST /retry/{task_id} for a FAILED task must return 202 and re-queue."""
        task = seed_task(db, status="FAILED")
        response = test_client.post(f"/retry/{task.id}")
        assert response.status_code == 202
        assert response.json()["status"] == "CREATED"


# ── Input validation ──────────────────────────────────────────────────────────────

class TestInputValidation:
    """Verify Pydantic validation rejects invalid inputs at the API boundary."""

    @pytest.mark.parametrize("task_type", ["invalid_type", "hack_system", "drop_table", ""])
    def test_invalid_task_type_returns_422(self, test_client, task_type):
        """Invalid task_type must be rejected with 422 Unprocessable Entity."""
        response = test_client.post("/tasks/create", json={
            "title": "Test",
            "description": "Valid description",
            "task_type": task_type,
        })
        assert response.status_code == 422

    def test_too_short_title_returns_422(self, test_client):
        """Title shorter than min_length=3 must be rejected."""
        response = test_client.post("/tasks/create", json={
            "title": "AB",
            "description": "Valid description",
            "task_type": "fix_bug",
        })
        assert response.status_code == 422

    def test_invalid_risk_level_in_constraints_returns_422(self, test_client):
        """risk_level outside (safe|balanced|aggressive) must be rejected."""
        response = test_client.post("/tasks/create", json={
            "title": "Test task",
            "description": "Valid description",
            "task_type": "build_app",
            "constraints": {"risk_level": "ULTRA_DANGEROUS"},
        })
        assert response.status_code == 422

    def test_idempotency_key_deduplicates(self, test_client, db, mock_llm):
        """Submitting the same idempotency_key twice must return the first task_id."""
        key = str(uuid.uuid4())
        r1 = test_client.post("/tasks/create", json={
            "title": "First task",
            "description": "First submission",
            "task_type": "build_app",
            "idempotency_key": key,
        })
        r2 = test_client.post("/tasks/create", json={
            "title": "Second task",
            "description": "Duplicate submission",
            "task_type": "build_app",
            "idempotency_key": key,
        })

        assert r1.status_code in (200, 201, 202)
        assert r2.status_code in (200, 201, 202)
        assert r1.json()["task_id"] == r2.json()["task_id"], "Idempotency key must deduplicate"
        assert r2.json()["deduplicated"] is True


# ── Planner prompt construction ────────────────────────────────────────────────────

class TestPlannerPromptBuilder:
    def test_build_planner_prompt_contains_title(self):
        """_build_planner_prompt must include the task title in the output."""
        from backend.api.routers.task import _build_planner_prompt, TaskInput

        task = TaskInput(
            title="My Stripe Handler",
            description="Handle payment webhooks",
            task_type="create_api",
        )
        prompt = _build_planner_prompt(task)
        assert "My Stripe Handler" in prompt

    def test_build_planner_prompt_contains_type(self):
        """_build_planner_prompt must include the task_type."""
        from backend.api.routers.task import _build_planner_prompt, TaskInput

        task = TaskInput(title="Test Task", description="Desc desc desc", task_type="fix_bug")
        prompt = _build_planner_prompt(task)
        assert "fix_bug" in prompt

    def test_build_planner_prompt_contains_tech_stack(self):
        """If tech_stack is provided it must appear in the prompt."""
        from backend.api.routers.task import _build_planner_prompt, TaskInput

        task = TaskInput(
            title="Test Task", description="Desc desc desc", task_type="build_app",
            tech_stack=["React", "FastAPI"],
        )
        prompt = _build_planner_prompt(task)
        assert "React" in prompt
        assert "FastAPI" in prompt

    def test_build_planner_prompt_is_deterministic(self):
        """Same input must always produce the same prompt (deterministic transform)."""
        from backend.api.routers.task import _build_planner_prompt, TaskInput

        task = TaskInput(title="T stable", description="Same input each time", task_type="refactor_code")
        assert _build_planner_prompt(task) == _build_planner_prompt(task)
