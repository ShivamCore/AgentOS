"""
tests/unit/test_executor.py
============================
Contract tests for agent/planner/executor.py (DAGOrchestrator).

The orchestrator takes an injectable ``step_runner`` so these tests exercise the
real scheduler, callbacks and state machine with deterministic steps instead of
patching internals. Contracts guarded here (originally regression tests for the
node_callback arity bug and silently-dropped failed futures):

* node_callback(node_id, status) is always called with exactly 2 args
* log_callback(node_id, log_type, content) is always called with exactly 3 args
* a node whose runner raises is logged at error level and ends FAILED
* start_ts is set when run_graph starts, not at construction
* max_steps / max_time are enforced
* file_scope defaults to an independent empty list
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from agent.lifecycle import NodeState, RunState
from agent.planner.executor import DAGOrchestrator
from agent.planner.graph import StepNode, TaskGraph
from agent.planner.scheduler import StepOutcome


def _graph(n: int = 1, deps: dict[str, list[str]] | None = None) -> TaskGraph:
    g = TaskGraph(task_id="t")
    for i in range(n):
        sid = f"step_{i}"
        g.nodes[sid] = StepNode(step_id=sid, description=f"Step {i}", dependencies=(deps or {}).get(sid, []))
    return g


def _ok(node, ctx):
    return StepOutcome(True, output=f"done {node.step_id}")


def _orch(tmp_path, runner=_ok, **kw) -> DAGOrchestrator:
    kw.setdefault("max_workers", 1)
    return DAGOrchestrator(workspace_dir=str(tmp_path), task_id="t", step_runner=runner, **kw)


class TestNodeCallbackSignature:
    def test_node_callback_called_with_exactly_two_args(self, tmp_path):
        received = []

        def strict_callback(node_id: str, status: str) -> None:
            received.append((node_id, status))

        _orch(tmp_path, node_callback=strict_callback).run_graph(_graph(1))
        assert [s for _, s in received] == ["READY", "RUNNING", "COMPLETED"]
        assert all(isinstance(a, str) and isinstance(b, str) for a, b in received)

    def test_node_callback_never_called_with_three_args(self, tmp_path):
        cb = MagicMock()
        _orch(tmp_path, node_callback=cb).run_graph(_graph(2))
        assert cb.call_count > 0
        assert all(len(c.args) == 2 and not c.kwargs for c in cb.call_args_list)

    def test_log_callback_called_with_three_args(self, tmp_path):
        logs = []

        def strict_log(node_id: str, log_type: str, content: str) -> None:
            logs.append((node_id, log_type, content))

        def failing(node, ctx):
            return StepOutcome(False, error="boom", retryable=False)

        _orch(tmp_path, runner=failing, log_callback=strict_log).run_graph(_graph(1))
        assert logs, "a failing node must produce log entries"
        assert all(len(e) == 3 for e in logs)


class TestFailedNodeHandling:
    def test_raising_runner_is_logged_at_error_level(self, tmp_path):
        def crash(node, ctx):
            raise RuntimeError("Simulated node crash")

        with patch("agent.planner.scheduler.logger") as mock_logger:
            report = _orch(tmp_path, runner=crash, max_retries=0).run_graph(_graph(1))
        assert mock_logger.error.called
        assert report.node_states == {"step_0": "FAILED"}
        assert "Simulated node crash" in report.errors["step_0"]

    def test_failed_node_ends_failed_after_retries(self, tmp_path):
        calls = []

        def failing(node, ctx):
            calls.append(ctx.attempt)
            return StepOutcome(False, error="error")

        g = _graph(1)
        report = _orch(tmp_path, runner=failing, max_retries=2).run_graph(g)
        assert calls == [1, 2, 3]
        assert g.nodes["step_0"].state is NodeState.FAILED
        assert report.state is RunState.FAILED


class TestTimingCorrectness:
    def test_start_ts_is_zero_before_run_graph(self, tmp_path):
        assert _orch(tmp_path).start_ts == 0.0

    def test_start_ts_is_set_during_run_graph(self, tmp_path):
        orch = _orch(tmp_path)
        before = time.time()
        orch.run_graph(_graph(1))
        assert before <= orch.start_ts <= time.time()


class TestConstraintEnforcement:
    def test_max_steps_halts_execution(self, tmp_path):
        g = _graph(5)
        report = _orch(tmp_path, max_steps=2).run_graph(g)
        assert report.nodes_started == 2
        states = sorted(report.node_states.values())
        assert states.count("COMPLETED") == 2 and states.count("CANCELLED") == 3
        assert report.state is RunState.PARTIAL_SUCCESS

    def test_max_time_halts_execution(self, tmp_path):
        def slow(node, ctx):
            ctx.cancel_event.wait(5)
            return StepOutcome(True)

        g = _graph(3, deps={"step_1": ["step_0"], "step_2": ["step_1"]})
        t0 = time.monotonic()
        report = _orch(tmp_path, runner=slow, max_time=1, node_timeout=30).run_graph(g)
        assert time.monotonic() - t0 < 4
        assert report.state is RunState.TIMED_OUT
        assert report.node_states["step_0"] == "TIMED_OUT"
        assert report.node_states["step_1"] == "CANCELLED"

    def test_null_callback_does_not_raise(self, tmp_path):
        _orch(tmp_path).run_graph(_graph(1))


class TestFileCallback:
    def test_file_callback_receives_written_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        seen = []

        def writer(node, ctx):
            return StepOutcome(True, files_written=["a.py"])

        _orch(tmp_path, runner=writer, file_callback=lambda n, p, c: seen.append((n, p, c))).run_graph(_graph(1))
        assert seen == [("step_0", "a.py", "x = 1\n")]


class TestFileScope:
    def test_file_scope_defaults_to_empty_list(self, tmp_path):
        orch = _orch(tmp_path)
        assert orch.file_scope == [] and isinstance(orch.file_scope, list)

    def test_file_scope_instances_are_independent(self, tmp_path):
        a = _orch(tmp_path / "a")
        b = _orch(tmp_path / "b")
        a.file_scope.append("src/")
        assert "src/" not in b.file_scope
