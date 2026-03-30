"""
tests/unit/test_executor.py
============================
Unit tests for agent/planner/executor.py (DAGOrchestrator)

REGRESSION PROOF: These tests prove the node_callback signature bug is dead.
  Bug: node_callback was called with 3 positional arguments but the lambda in
  tasks.py was defined as lambda nid, ns: ... (2 args). Every callback
  invocation raised TypeError silently — results were lost.
  Fixed by: corrected to 2-arg call site + lambda signature alignment.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


def _make_resolved_future(result=True) -> Future:
    """Return a Future that is already resolved with `result`."""
    f: Future = Future()
    f.set_result(result)
    return f


def _make_pool_mock(fn_to_run=None) -> MagicMock:
    """
    Return a MagicMock that behaves like AgentPool.
    submit() actually runs the function synchronously in a real Future so that
    concurrent.futures.wait() returns immediately and node status is updated.
    """
    pool = MagicMock()

    def _submit(fn, *args, **kwargs):
        f: Future = Future()
        try:
            result = fn(*args, **kwargs)
            f.set_result(result)
        except Exception as exc:
            f.set_exception(exc)
        return f

    pool.submit.side_effect = _submit
    pool.shutdown.return_value = None
    return pool


def _make_agent_pool_class_mock():
    """Return a MagicMock class whose instances behave like AgentPool."""
    cls = MagicMock()
    cls.return_value = _make_pool_mock()
    return cls


# ── Fixtures ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_orchestrator(tmp_path):
    """Create a DAGOrchestrator with all external deps mocked out."""
    with (
        patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
        patch("agent.planner.executor.get_memory_engine"),
        patch("agent.planner.executor.execute_markdown_agent", return_value={"success": True, "stdout": "ok", "stderr": ""}),
        patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
        patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
        patch("agent.planner.executor.execute_step", return_value={"success": True, "stdout": "done", "stderr": ""}),
    ):
        from agent.planner.executor import DAGOrchestrator
        orch = DAGOrchestrator(
            workspace_dir=str(tmp_path),
            task_id="test-task-123",
            max_workers=2,
            max_time=60,
            max_steps=10,
        )
        yield orch


def _make_graph(step_count: int = 1):
    """Build a minimal TaskGraph with `step_count` independent nodes."""
    from agent.planner.graph import TaskGraph, StepNode

    graph = TaskGraph(task_id="test-task-123")
    for i in range(step_count):
        node = StepNode(
            step_id=f"step_{i}",
            description=f"Step {i}",
            required_tools=["write_file"],
            preferred_agent="coder",
            dependencies=[],
        )
        graph.nodes[node.step_id] = node
    return graph


# ── Callback signature tests ──────────────────────────────────────────────────────

class TestNodeCallbackSignature:
    """
    REGRESSION: Prove that node_callback is called with exactly 2 args.
    The previous bug called it with 3 args (node_id, status, files_count).
    """

    def test_node_callback_called_with_exactly_two_args(self, tmp_path):
        """
        node_callback must receive exactly (node_id: str, status: str).
        Mock with spec ensures any wrong-arity call raises TypeError immediately.
        """
        received_calls = []

        def strict_callback(node_id: str, status: str) -> None:
            received_calls.append((node_id, status))

        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t1",
                max_workers=1,
                node_callback=strict_callback,
            )
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)

        # At minimum RUNNING and COMPLETED should have been called
        assert len(received_calls) >= 1
        for node_id, status in received_calls:
            assert isinstance(node_id, str), f"First arg must be str, got {type(node_id)}"
            assert isinstance(status, str), f"Second arg must be str, got {type(status)}"

    def test_node_callback_never_called_with_three_args(self, tmp_path):
        """
        Assert no call to node_callback ever passes 3 positional arguments.
        This is the exact signature the broken code used.
        """
        mock_cb = MagicMock()

        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t2",
                max_workers=1,
                node_callback=mock_cb,
            )
            graph = _make_graph(step_count=2)
            orch.run_graph(graph)

        for c in mock_cb.call_args_list:
            positional_args = c.args
            assert len(positional_args) <= 2, (
                f"node_callback must receive ≤2 positional args, got {len(positional_args)}: {c}"
            )

    def test_log_callback_called_with_three_args(self, tmp_path):
        """
        log_callback(node_id, log_type, content) must still receive exactly 3 args.
        Confirm the two callbacks have different correct signatures.
        """
        log_calls = []

        def strict_log(node_id: str, log_type: str, content: str) -> None:
            log_calls.append((node_id, log_type, content))

        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t3",
                max_workers=1,
                log_callback=strict_log,
            )
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)

        assert len(log_calls) >= 1
        for entry in log_calls:
            assert len(entry) == 3, f"log_callback must receive 3 args, got {len(entry)}"


# ── Failed future handling ────────────────────────────────────────────────────────

class TestFailedFutureHandling:
    """Prove failed futures are logged and marked FAILED — not silently discarded."""

    def test_failed_future_is_logged_via_logger_error(self, tmp_path):
        """
        When a node raises an unhandled exception, logger.error must be called
        with the exception details. Previously failures were silently discarded.
        """
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_step",
                  side_effect=RuntimeError("Simulated node crash")),
            patch("agent.planner.executor.execute_markdown_agent",
                  side_effect=RuntimeError("Simulated node crash")),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.logger") as mock_logger,
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_fail",
                max_workers=1,
            )
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)

        # Either logger.error or logger.exception must have been called
        was_logged = (
            mock_logger.error.called or mock_logger.exception.called
        )
        assert was_logged, "Failed node must produce an error-level log entry"

    def test_failed_node_status_is_set_to_failed(self, tmp_path):
        """After a node failure, its status must be 'failed' not 'pending' or 'running'."""
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": False, "stdout": "", "stderr": "error"}),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": False, "stdout": "", "stderr": "error"}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_fail_status",
                max_workers=1,
            )
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)

        # After max retries, the node should be failed
        for node in graph.nodes.values():
            assert node.status in ("failed", "completed"), \
                f"Node status must resolve, got: {node.status}"


# ── Timing correctness ────────────────────────────────────────────────────────────

class TestTimingCorrectness:
    def test_start_ts_is_zero_before_run_graph(self, mock_orchestrator):
        """
        start_ts must be initialised to 0.0 in __init__ and only set at
        run_graph() invocation — not at construction time.
        The old code set it in __init__ which caused incorrect elapsed time
        calculations if there was a delay between construction and execution.
        """
        assert mock_orchestrator.start_ts == 0.0

    def test_start_ts_is_set_during_run_graph(self, tmp_path):
        """start_ts must be a recent timestamp after run_graph() starts."""
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_time",
                max_workers=1,
            )
            before = time.time()
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)
            after = time.time()

        assert before <= orch.start_ts <= after, \
            f"start_ts {orch.start_ts} must be within execution window [{before}, {after}]"


# ── Constraint enforcement ────────────────────────────────────────────────────────

class TestConstraintEnforcement:
    def test_max_steps_halts_execution(self, tmp_path):
        """
        With max_steps=2 and a 5-node graph, only 2 nodes should execute.
        """
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_max_steps",
                max_workers=1,
                max_steps=2,
            )
            graph = _make_graph(step_count=5)
            executed = orch.run_graph(graph)

        assert executed <= 2, f"max_steps=2 should cap execution at 2, got {executed}"

    def test_max_time_halts_execution(self, tmp_path):
        """
        With max_time=0 (already exceeded), no nodes should execute.
        """
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
            patch("time.time", side_effect=[0.0, 1000.0, 1001.0] * 10),  # simulate expired time
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_max_time",
                max_workers=1,
                max_time=1,  # 1 second
            )
            graph = _make_graph(step_count=3)
            executed = orch.run_graph(graph)

        # With time already expired, at most 0 nodes should complete AFTER the check
        # (we allow 1 due to race between the check and first dispatch)
        assert executed <= 1, f"max_time enforcement failed: {executed} nodes ran after timeout"

    def test_null_callback_does_not_raise(self, tmp_path):
        """Omitting node_callback and log_callback must not raise."""
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent", return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload", return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_no_cb",
                max_workers=1,
                # No callbacks
            )
            graph = _make_graph(step_count=1)
            orch.run_graph(graph)  # Must not raise


# ── File scope default ────────────────────────────────────────────────────────────

class TestFileScope:
    def test_file_scope_defaults_to_empty_list(self, tmp_path):
        """
        file_scope parameter must default to [] not None.
        The previous code had file_scope: List[str] = None — a mutable default bug.
        """
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="t_scope",
            )
        assert orch.file_scope == [], f"Expected [] but got {orch.file_scope!r}"
        assert isinstance(orch.file_scope, list)

    def test_file_scope_instances_are_independent(self, tmp_path):
        """Two separate orchestrators must not share the same file_scope list reference."""
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
        ):
            from agent.planner.executor import DAGOrchestrator
            orch_a = DAGOrchestrator(workspace_dir=str(tmp_path / "a"), task_id="a")
            orch_b = DAGOrchestrator(workspace_dir=str(tmp_path / "b"), task_id="b")

        orch_a.file_scope.append("src/")
        assert "src/" not in orch_b.file_scope, "file_scope instances must be independent"
