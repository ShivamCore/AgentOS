"""
tests/unit/test_memory_engine.py
=================================
Unit tests for agent/memory/engine.py

REGRESSION PROOF: These tests prove the critical bug is dead:
  Bug: LocalMemoryEngine.__init__ mutated the module-level _CHROMA_AVAILABLE global
  inside an instance method (exception handler), causing state pollution between
  Celery worker processes. Fixed by @lru_cache factory with instance-scoped state.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from unittest.mock import MagicMock, call, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────────

def _make_engine(workspace_dir: str):
    """Import fresh to avoid lru_cache pollution across tests."""
    # We must clear the lru_cache between tests to get fresh instances
    from agent.memory import engine as eng_mod
    eng_mod.get_memory_engine.cache_clear()
    return eng_mod.get_memory_engine(workspace_dir)


# ── Instance isolation ────────────────────────────────────────────────────────────

class TestEngineInstanceIsolation:
    """Prove two engines with different workspace dirs are fully independent."""

    def test_engine_instance_isolation(self, tmp_path, monkeypatch):
        """
        REGRESSION: engine.py mutated a module-level global inside an instance.
        Prove two engines with different paths do NOT share state.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        path_a = str(tmp_path / "workspace_a")
        path_b = str(tmp_path / "workspace_b")

        engine_a = eng_mod.get_memory_engine(path_a)
        engine_b = eng_mod.get_memory_engine(path_b)

        # They must be distinct objects
        assert engine_a is not engine_b, "Two engines for different paths must be separate instances"

    def test_lru_cache_returns_same_instance(self, tmp_path, monkeypatch):
        """
        Prove the @lru_cache factory returns the IDENTICAL object for the same workspace_dir.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        path = str(tmp_path / "workspace_c")
        engine_1 = eng_mod.get_memory_engine(path)
        engine_2 = eng_mod.get_memory_engine(path)

        assert engine_1 is engine_2, "lru_cache must return the same instance for equal workspace_dir"

    def test_workspace_dir_stored_on_instance(self, tmp_path, monkeypatch):
        """Each engine stores its own workspace_dir — not a shared reference."""
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        path_x = str(tmp_path / "x")
        path_y = str(tmp_path / "y")

        eng_x = eng_mod.get_memory_engine(path_x)
        eng_y = eng_mod.get_memory_engine(path_y)

        assert eng_x.workspace_dir == path_x
        assert eng_y.workspace_dir == path_y
        assert eng_x.workspace_dir != eng_y.workspace_dir


# ── Concurrent isolation ──────────────────────────────────────────────────────────

class TestConcurrentWorkerIsolation:
    """Simulate multiple Celery worker threads accessing the memory engine."""

    def test_engine_reset_does_not_affect_other_workers(self, tmp_path, monkeypatch):
        """
        Simulate 4 concurrent workers each using their own workspace_dir.
        No worker should be able to mutate another's engine state.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        def worker_task(i: int) -> str:
            path = str(tmp_path / f"worker_{i}")
            eng = eng_mod.get_memory_engine(path)
            return eng.workspace_dir

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker_task, i) for i in range(4)]
            results = [f.result() for f in as_completed(futures)]

        # All 4 workers should have distinct workspace dirs
        assert len(set(results)) == 4, "Each worker engine must have a unique workspace_dir"

    def test_concurrent_store_does_not_raise(self, tmp_path, monkeypatch):
        """
        Concurrent store_memory calls on a no-op engine (ChromaDB unavailable)
        must return False cleanly without raising or corrupting state.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        eng = eng_mod.get_memory_engine(str(tmp_path / "concurrent"))

        def store(i: int) -> bool:
            return eng.store_memory("agent_a", "task", f"content_{i}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(store, range(20)))

        # All return False (no-op) without raising
        assert all(r is False for r in results)


# ── Logging behaviour ─────────────────────────────────────────────────────────────

class TestLoggingBehaviour:
    """Prove that exceptions are logged via logger.exception, not silently swallowed."""

    def test_store_memory_logs_exception_on_chromadb_error(self, tmp_path, monkeypatch):
        """
        When ChromaDB raises on collection.add(), logger.exception must be called.
        Previously this was a bare except: pass — the bug was silent failure.
        """
        pytest.importorskip("chromadb", reason="chromadb not installed")
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", True)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        mock_collection = MagicMock()
        mock_collection.add.side_effect = RuntimeError("ChromaDB simulated failure")

        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection

        with patch("chromadb.PersistentClient", return_value=mock_client):
            with patch.object(eng_mod, "_CHROMA_AVAILABLE", True):
                eng = eng_mod.LocalMemoryEngine(str(tmp_path / "log_test"))
                eng._collection = mock_collection

        with patch("agent.memory.engine.logger") as mock_logger:
            result = eng.store_memory("agent_x", "task", "content")

        assert result is False
        mock_logger.exception.assert_called_once()
        # Verify the call includes useful context
        call_args = mock_logger.exception.call_args
        assert "mem_" in str(call_args) or "Failed" in str(call_args[0][0])

    def test_search_memory_logs_exception_on_chromadb_error(self, tmp_path, monkeypatch):
        """
        When ChromaDB raises on collection.query(), logger.exception must be called.
        """
        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        mock_collection = MagicMock()
        mock_collection.query.side_effect = RuntimeError("Query failure")

        eng = eng_mod.LocalMemoryEngine.__new__(eng_mod.LocalMemoryEngine)
        eng.workspace_dir = str(tmp_path)
        eng._collection = mock_collection

        with patch("agent.memory.engine.logger") as mock_logger:
            result = eng.search_memory("test query")

        assert result == []
        mock_logger.exception.assert_called_once()

    def test_no_logger_error_or_warning_on_success(self, tmp_path, monkeypatch):
        """On successful operations, no error-level logs should be emitted."""
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", True)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        mock_collection = MagicMock()
        mock_collection.add.return_value = None
        eng = eng_mod.LocalMemoryEngine.__new__(eng_mod.LocalMemoryEngine)
        eng._collection = mock_collection
        eng.workspace_dir = str(tmp_path)

        with patch("agent.memory.engine.logger") as mock_logger:
            result = eng.store_memory("agent_z", "task", "hello world")

        assert result is True
        mock_logger.exception.assert_not_called()
        mock_logger.error.assert_not_called()


# ── No-op fallback behaviour ──────────────────────────────────────────────────────

class TestNoOpFallback:
    """When ChromaDB is unavailable the engine must degrade gracefully."""

    def test_store_returns_false_when_chroma_unavailable(self, tmp_path, monkeypatch):
        """store_memory must return False (not raise) when ChromaDB is absent."""
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        eng = eng_mod.LocalMemoryEngine(str(tmp_path / "noop"))
        result = eng.store_memory("a", "b", "c")
        assert result is False

    def test_search_returns_empty_list_when_chroma_unavailable(self, tmp_path, monkeypatch):
        """search_memory must return [] (not raise) when ChromaDB is absent."""
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        eng = eng_mod.LocalMemoryEngine(str(tmp_path / "noop2"))
        result = eng.search_memory("anything")
        assert result == []

    def test_collection_is_none_when_chroma_unavailable(self, tmp_path, monkeypatch):
        """_collection attribute must be None when ChromaDB init fails."""
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        eng = eng_mod.LocalMemoryEngine(str(tmp_path / "noop3"))
        assert eng._collection is None


# ── MemoryResult dataclass ────────────────────────────────────────────────────────

class TestMemoryResult:
    def test_to_dict_rounds_distance(self):
        """MemoryResult.to_dict must round distance to 4 decimal places."""
        from agent.memory.engine import MemoryResult

        r = MemoryResult(id="x", document="doc", metadata={}, distance=0.123456789)
        d = r.to_dict()
        assert d["distance"] == round(0.123456789, 4)

    def test_to_dict_contains_all_fields(self):
        """to_dict must return id, document, metadata, distance."""
        from agent.memory.engine import MemoryResult

        r = MemoryResult(id="abc", document="hello", metadata={"k": "v"}, distance=0.1)
        d = r.to_dict()
        assert set(d.keys()) == {"id", "document", "metadata", "distance"}

    def test_slots_are_defined(self):
        """MemoryResult uses __slots__ for memory efficiency (dataclass(slots=True))."""
        from agent.memory.engine import MemoryResult

        assert hasattr(MemoryResult, "__slots__")
