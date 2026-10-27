"""
Scheduler correctness: ordering, concurrency, failure propagation, retries,
timeouts, cancellation and budgets. Synchronisation uses threading primitives
(Barrier/Event), not sleeps, so the assertions are deterministic.
"""

from __future__ import annotations

import threading
import time

import pytest

from agent.lifecycle import NodeState, RunState
from agent.planner.graph import StepNode, TaskGraph
from agent.planner.scheduler import DAGScheduler, SchedulerConfig, StepOutcome


def graph_of(edges: dict[str, list[str]]) -> TaskGraph:
    g = TaskGraph(task_id="t")
    for nid, deps in edges.items():
        g.nodes[nid] = StepNode(
            step_id=nid, description=f"step {nid}", dependencies=deps
        )
    return g


class Recorder:
    """Thread-safe record of start/finish order."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started: list[str] = []
        self.finished: list[str] = []

    def runner(self, fn=None):
        def run(node, ctx):
            with self.lock:
                self.started.append(node.step_id)
            out = fn(node, ctx) if fn else StepOutcome(True)
            with self.lock:
                self.finished.append(node.step_id)
            return out

        return run


def run(g, runner, **cfg) -> tuple:
    states: list[tuple[str, str]] = []
    sched = DAGScheduler(
        runner, SchedulerConfig(**cfg), on_state=lambda n, s: states.append((n, s))
    )
    report = sched.run(g)
    return report, states


class TestOrdering:
    def test_dependencies_complete_before_dependents_start(self):
        rec = Recorder()
        g = graph_of({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        report, _ = run(g, rec.runner(), max_workers=4)
        assert report.state is RunState.COMPLETED
        pos = {n: rec.started.index(n) for n in rec.started}
        fin = {n: rec.finished.index(n) for n in rec.finished}
        assert fin["a"] < pos["b"] and fin["a"] < pos["c"]
        assert fin["b"] < pos["d"] and fin["c"] < pos["d"]

    def test_long_chain_runs_strictly_in_order(self):
        n = 40
        edges = {f"n{i}": ([f"n{i - 1}"] if i else []) for i in range(n)}
        rec = Recorder()
        report, _ = run(graph_of(edges), rec.runner(), max_workers=8)
        assert report.state is RunState.COMPLETED
        assert rec.started == [f"n{i}" for i in range(n)]
        assert report.max_observed_concurrency == 1

    def test_every_node_follows_the_legal_lifecycle(self):
        g = graph_of({"a": [], "b": ["a"]})
        _, states = run(g, Recorder().runner(), max_workers=2)
        for nid in "ab":
            assert [s for n, s in states if n == nid] == [
                "READY",
                "RUNNING",
                "COMPLETED",
            ]


class TestConcurrency:
    def test_independent_nodes_run_concurrently(self):
        # Four roots must all be inside the runner at the same time to pass the barrier.
        barrier = threading.Barrier(4, timeout=5)

        def fn(node, ctx):
            barrier.wait()
            return StepOutcome(True)

        report, _ = run(graph_of({k: [] for k in "abcd"}), fn, max_workers=4)
        assert report.state is RunState.COMPLETED
        assert report.max_observed_concurrency == 4

    def test_concurrency_never_exceeds_max_workers(self):
        active, peak, lock = [0], [0], threading.Lock()
        release = threading.Event()

        def fn(node, ctx):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                if active[0] == 2:
                    release.set()
            release.wait(2)
            time.sleep(0.01)
            with lock:
                active[0] -= 1
            return StepOutcome(True)

        report, _ = run(graph_of({f"n{i}": [] for i in range(10)}), fn, max_workers=2)
        assert report.state is RunState.COMPLETED
        assert peak[0] == 2 and report.max_observed_concurrency == 2


class TestFailurePropagation:
    def test_dependents_blocked_independent_branch_continues(self):
        g = graph_of({"a": [], "b": ["a"], "c": ["b"], "x": [], "y": ["x"]})

        def fn(node, ctx):
            return StepOutcome(
                node.step_id != "a", error="a broke" if node.step_id == "a" else None
            )

        rec = Recorder()
        report, _ = run(g, rec.runner(fn), max_workers=2, max_retries=0)
        assert report.node_states == {
            "a": "FAILED",
            "b": "BLOCKED",
            "c": "BLOCKED",
            "x": "COMPLETED",
            "y": "COMPLETED",
        }
        assert "b" not in rec.started and "c" not in rec.started
        assert report.state is RunState.PARTIAL_SUCCESS
        assert "dependency 'a'" in report.errors["b"]

    def test_all_failed_is_failed(self):
        report, _ = run(
            graph_of({"a": []}),
            lambda n, c: StepOutcome(False, error="x"),
            max_retries=0,
        )
        assert report.state is RunState.FAILED

    def test_exception_in_runner_is_a_failed_node_not_a_crash(self):
        def boom(node, ctx):
            raise ValueError("bad")

        report, _ = run(graph_of({"a": [], "b": []}), boom, max_retries=0)
        assert set(report.node_states.values()) == {"FAILED"}
        assert "ValueError: bad" in report.errors["a"]

    def test_malformed_outcome_is_rejected(self):
        report, _ = run(
            graph_of({"a": []}), lambda n, c: {"success": True}, max_retries=0
        )
        assert report.node_states["a"] == "FAILED"
        assert "malformed" in report.errors["a"]


class TestRetries:
    def test_retry_then_success(self):
        seen = []

        def flaky(node, ctx):
            seen.append((ctx.attempt, ctx.previous_error))
            return StepOutcome(ctx.attempt >= 2, error="first try fails")

        g = graph_of({"a": []})
        report, states = run(g, flaky, max_retries=2)
        assert report.state is RunState.COMPLETED
        assert seen == [(1, None), (2, "first try fails")]
        assert [s for _, s in states] == [
            "READY",
            "RUNNING",
            "READY",
            "RUNNING",
            "COMPLETED",
        ]

    def test_non_retryable_failure_is_not_retried(self):
        calls = []

        def fn(node, ctx):
            calls.append(1)
            return StepOutcome(False, error="policy violation", retryable=False)

        run(graph_of({"a": []}), fn, max_retries=3)
        assert len(calls) == 1


class TestTimeoutsAndCancellation:
    def test_node_timeout_marks_timed_out_and_blocks_dependents(self):
        def hang(node, ctx):
            ctx.cancel_event.wait(10)
            return StepOutcome(True)

        t0 = time.monotonic()
        report, _ = run(
            graph_of({"a": [], "b": ["a"]}),
            hang,
            node_timeout_sec=0.3,
            run_timeout_sec=30,
        )
        assert time.monotonic() - t0 < 5
        assert report.node_states == {"a": "TIMED_OUT", "b": "BLOCKED"}

    def test_cancel_event_stops_run(self):
        started = threading.Event()
        cancel = threading.Event()

        def fn(node, ctx):
            started.set()
            ctx.cancel_event.wait(10)
            return StepOutcome(False, error="interrupted", retryable=False)

        g = graph_of({"a": [], "b": ["a"]})
        sched = DAGScheduler(fn, SchedulerConfig(max_workers=1))
        threading.Thread(
            target=lambda: (started.wait(5), cancel.set()), daemon=True
        ).start()
        report = sched.run(g, cancel_event=cancel)
        assert report.state is RunState.CANCELLED
        assert report.node_states["b"] == "CANCELLED"
        assert report.node_states["a"] in ("CANCELLED", "FAILED")

    def test_late_result_after_timeout_is_ignored(self):
        def slow(node, ctx):
            time.sleep(0.5)  # ignores the cancel event on purpose
            return StepOutcome(True)

        report, states = run(
            graph_of({"a": []}), slow, node_timeout_sec=0.1, run_timeout_sec=30
        )
        assert report.node_states["a"] == "TIMED_OUT"
        time.sleep(0.6)
        assert [s for _, s in states].count("COMPLETED") == 0

    def test_terminal_states_never_change(self):
        g = graph_of({"a": []})
        run(g, lambda n, c: StepOutcome(True))
        from agent.lifecycle import InvalidTransitionError

        with pytest.raises(InvalidTransitionError):
            g.nodes["a"].transition(NodeState.RUNNING)


class TestBudgets:
    def test_step_budget_cancels_unstarted_nodes(self):
        report, _ = run(
            graph_of({f"n{i}": [] for i in range(5)}),
            lambda n, c: StepOutcome(True),
            max_workers=1,
            max_steps=3,
        )
        assert report.nodes_started == 3
        assert sorted(report.node_states.values()).count("CANCELLED") == 2

    def test_retries_do_not_consume_step_budget(self):
        report, _ = run(
            graph_of({"a": [], "b": []}),
            lambda n, c: StepOutcome(c.attempt == 2, error="retry me"),
            max_workers=1,
            max_steps=2,
            max_retries=1,
        )
        assert report.state is RunState.COMPLETED
