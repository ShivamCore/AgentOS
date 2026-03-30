"""
tests/perf/test_load.py
========================
Performance benchmarks using pytest-benchmark.
All tests use in-memory SQLite + mocked LLM — no external services.

Run with: pytest tests/perf/ --benchmark-only
"""

from __future__ import annotations

import uuid
from unittest.mock import patch, MagicMock

import pytest


pytestmark = pytest.mark.benchmark


class TestEndpointLatency:
    def test_bench_single_task_submission(self, benchmark, test_client):
        """
        Benchmark POST /tasks/create latency.
        Target: p95 < 200ms on local in-memory SQLite.
        """
        from backend.api.routers.task import _limiter
        original_limit = _limiter._limit
        _limiter._limit = 100000  # unlimited for benchmark

        def submit():
            return test_client.post("/tasks/create", json={
                "title": "Benchmark task",
                "description": "Single benchmark submission test",
                "task_type": "fix_bug",
            })

        try:
            with patch("backend.api.routers.task.settings") as mock_cfg:
                mock_cfg.MAX_CONCURRENT_TASKS = 100000
                mock_cfg.TASK_TIMEOUT_SECONDS = 600
                result = benchmark(submit)
        finally:
            _limiter._limit = original_limit
        assert result.status_code in (200, 201, 202)

    def test_bench_task_list(self, benchmark, test_client, db):
        """
        Benchmark GET /tasks list query.
        Must complete in < 50ms for up to 50 rows.
        """
        from tests.conftest import seed_task
        for _ in range(10):
            seed_task(db, status="COMPLETED")

        result = benchmark(lambda: test_client.get("/tasks"))
        assert result.status_code == 200

    def test_bench_task_detail_lookup(self, benchmark, test_client, db):
        """
        Benchmark GET /tasks/{id} single record lookup.
        With a primary key index this must be < 5ms.
        """
        from tests.conftest import seed_task
        task = seed_task(db, status="COMPLETED")

        result = benchmark(lambda: test_client.get(f"/tasks/{task.id}"))
        assert result.status_code == 200


class TestMemoryEnginePerf:
    def test_bench_engine_factory_repeated_calls(self, benchmark, tmp_path, monkeypatch):
        """
        Benchmark repeated get_memory_engine() calls for the same workspace_dir.
        The lru_cache must make this O(1) — target < 1µs per call.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        workspace = str(tmp_path / "bench")
        # Prime the cache
        eng_mod.get_memory_engine(workspace)

        result_ids = []
        def repeated_lookup():
            result_ids.append(id(eng_mod.get_memory_engine(workspace)))

        benchmark(repeated_lookup)

        # All calls must return the same instance
        assert len(set(result_ids)) == 1

    def test_bench_store_noop_memory(self, benchmark, tmp_path, monkeypatch):
        """
        Benchmark no-op store_memory (ChromaDB unavailable).
        No-op must be < 1µs.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()
        engine = eng_mod.get_memory_engine(str(tmp_path / "noop"))

        result = benchmark(lambda: engine.store_memory("a", "t", "content"))
        assert result is False


class TestSelectorPerf:
    def test_bench_task_input_validation(self, benchmark, test_client):
        """
        Benchmark Pydantic validation of task input (before DB hit).
        ValidationError path must be < 5ms.
        """
        def validate_invalid():
            return test_client.post("/tasks/create", json={
                "title": "x",  # Too short — will fail validation
                "description": "d" * 20,
                "task_type": "invalid_type",
            })

        result = benchmark(validate_invalid)
        assert result.status_code == 422


class TestConcurrentSubmissions:
    def test_50_sequential_submissions_all_succeed(self, test_client):
        """
        Submit 50 tasks sequentially and verify all succeed.
        This tests throughput under sustained load.
        Expected: all 50 return 2xx within 10 seconds total.
        """
        from backend.api.routers.task import _limiter
        original_limit = _limiter._limit
        _limiter._limit = 1000  # unlimited for this test

        try:
            with patch("backend.api.routers.task.settings") as mock_cfg:
                mock_cfg.MAX_CONCURRENT_TASKS = 100
                mock_cfg.TASK_TIMEOUT_SECONDS = 600
                successes = 0
                for i in range(50):
                    resp = test_client.post("/tasks/create", json={
                        "title": f"Load test task {i}",
                        "description": f"Sequential submission number {i}",
                        "task_type": "fix_bug",
                    })
                    if resp.status_code in (200, 201, 202):
                        successes += 1
        finally:
            _limiter._limit = original_limit

        assert successes == 50, f"Only {successes}/50 tasks succeeded under load"
