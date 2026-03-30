"""
tests/contract/test_api_contract.py
=====================================
API contract tests — verify the shape of every response matches what the frontend expects.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import seed_task


class TestResponseShapes:
    def test_task_create_response_has_exact_fields(self, test_client):
        """POST /tasks/create must return {task_id, status, deduplicated}."""
        response = test_client.post("/tasks/create", json={
            "title": "Contract test task",
            "description": "Verify shape of response",
            "task_type": "build_app",
        })
        assert response.status_code in (200, 201, 202)
        data = response.json()
        assert "task_id" in data
        assert "status" in data
        assert uuid.UUID(data["task_id"])  # Must be a valid UUID

    def test_task_detail_response_has_required_fields(self, test_client, db):
        """GET /tasks/{task_id} must include id, status, description, created_at, nodes."""
        task = seed_task(db, status="COMPLETED")
        response = test_client.get(f"/tasks/{task.id}")
        assert response.status_code == 200
        data = response.json()

        for field in ("id", "status", "description", "nodes"):
            assert field in data, f"Missing field {field!r} in task detail response"

    def test_task_list_items_have_required_fields(self, test_client, db):
        """GET /tasks items must each have id, status, description."""
        seed_task(db, status="COMPLETED")
        response = test_client.get("/tasks")
        assert response.status_code == 200
        items = response.json()
        assert len(items) > 0

        for item in items:
            for field in ("id", "status", "description"):
                assert field in item, f"List item missing field {field!r}"

    def test_explain_response_has_three_sections(self, test_client, db):
        """GET /tasks/{id}/explain must include planner_reasoning, agent_selection, tool_usage_decisions."""
        task = seed_task(db, status="COMPLETED")
        response = test_client.get(f"/tasks/{task.id}/explain")
        assert response.status_code == 200
        data = response.json()

        for key in ("planner_reasoning", "agent_selection", "tool_usage_decisions"):
            assert key in data, f"Explain response missing {key!r}"
            assert isinstance(data[key], list), f"{key!r} must be a list"


class TestStatusStringCasing:
    def test_status_always_uppercase_in_create_response(self, test_client):
        """POST /tasks/create status field must be uppercase."""
        response = test_client.post("/tasks/create", json={
            "title": "Case test",
            "description": "Verifying case contract",
            "task_type": "fix_bug",
        })
        assert response.status_code in (200, 201, 202)
        status = response.json()["status"]
        assert status == status.upper(), f"status {status!r} is not uppercase"

    def test_status_always_uppercase_in_task_detail(self, test_client, db):
        """GET /tasks/{id} status must be uppercase."""
        task = seed_task(db, status="CREATED")
        response = test_client.get(f"/tasks/{task.id}")
        status = response.json()["status"]
        assert status == status.upper(), f"status {status!r} is not uppercase"

    @pytest.mark.parametrize("status", ["CREATED", "RUNNING", "COMPLETED", "FAILED"])
    def test_all_valid_statuses_pass_through_unchanged(self, test_client, db, status):
        """Any valid TaskStatus stored in DB must be returned verbatim by the API."""
        task = seed_task(db, status=status)
        response = test_client.get(f"/tasks/{task.id}")
        assert response.json()["status"] == status


class TestErrorResponses:
    def test_404_has_detail_field(self, test_client):
        """404 responses must have a 'detail' key."""
        response = test_client.get(f"/tasks/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_422_on_invalid_input(self, test_client):
        """Invalid input must produce 422 with detail array."""
        response = test_client.post("/tasks/create", json={"title": "x"})
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_409_has_detail_field(self, test_client, db):
        """409 Conflict from retry must include a 'detail' field."""
        task = seed_task(db, status="COMPLETED")
        response = test_client.post(f"/retry/{task.id}")
        assert response.status_code == 409
        assert "detail" in response.json()

    def test_429_has_detail_field(self, test_client, db):
        """429 Too Many Requests from backpressure must include 'detail'."""
        from unittest.mock import patch
        with patch("backend.api.routers.task.settings") as mock_cfg:
            mock_cfg.MAX_CONCURRENT_TASKS = 0
            mock_cfg.TASK_TIMEOUT_SECONDS = 600
            response = test_client.post("/tasks/create", json={
                "title": "Overflow",
                "description": "Should get 429",
                "task_type": "fix_bug",
            })
        assert response.status_code == 429
        assert "detail" in response.json()


class TestHealthEndpoints:
    def test_health_endpoint_is_reachable(self, test_client):
        """GET /health must return 200."""
        response = test_client.get("/health")
        assert response.status_code == 200

    def test_root_endpoint_returns_json(self, test_client):
        """GET / must return a JSON body."""
        response = test_client.get("/")
        assert response.headers["content-type"].startswith("application/json")
